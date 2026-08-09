"""
Push, pull and sync: the three things a workspace does to the shared n8n.

The split between them is deliberate and worth stating plainly, because it is the part that surprises.
Push writes to n8n and stores nothing, so this table never records an intention, only an observation.
Pull reads from n8n and stores what it finds, so the table is a mirror and can be trusted as one.
Sync is push then pull, which is the only order that leaves the workspace's files, this database and
n8n all saying the same thing, and is therefore what an agent should call.

Ownership of a workflow is derived rather than stored. Management records which workspace owns a tag;
only n8n knows which workflows carry it right now. That is why every operation here begins by asking
n8n for the workspace's tagged workflows, and why the Workflow table is never consulted to decide
whether a caller may touch something: it is a mirror, it is stale by design, and a workflow tagged by
hand in the n8n UI has no row in it at all.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.text import slugify

from access_control.models import Workspace
from workflows.definitions import (
    InvalidDefinition,
    build_create_body,
    build_update_body,
    definition_tags,
    sanitize_definition,
    validate_definition,
)
from workflows.models import Workflow, WorkflowTag
from workflows.n8n import N8nClient, N8nConflict, N8nWorkflow, get_n8n_client


class WorkflowServiceError(RuntimeError):
    pass


class TagOwnedElsewhere(WorkflowServiceError):
    """A tag name this workspace asked for belongs to another workspace."""


class UnknownWorkflowId(WorkflowServiceError):
    """An id that is not among the workflows this workspace's tags cover."""


@dataclass(frozen=True, slots=True)
class PushResult:
    name: str
    n8n_id: str
    action: str
    tags: list[str] = field(default_factory=list)


def ensure_tags(workspace: Workspace, names: Iterable[str], client: N8nClient) -> dict[str, WorkflowTag]:
    """
    Claim the named tags for this workspace and make sure n8n holds each one.

    Ownership is settled first, for every name, before n8n is touched at all: a batch that mentions
    one foreign tag must not leave the others half created.
    """
    wanted = list(dict.fromkeys(names))
    existing = {tag.name: tag for tag in WorkflowTag.objects.filter(name__in=wanted)}

    foreign = [
        (name, existing[name].workspace)
        for name in wanted
        if name in existing and existing[name].workspace_id != workspace.pk
    ]
    if foreign:
        described = "; ".join(f"'{name}' belongs to workspace '{owner.module}'" for name, owner in foreign)
        raise TagOwnedElsewhere(
            f"{described}. Tags are unique across workspaces because they are what keeps one "
            "workspace's workflows out of another's."
        )

    tags: dict[str, WorkflowTag] = {}
    for name in wanted:
        tag = existing.get(name)
        if tag is None:
            try:
                tag = WorkflowTag.objects.create(workspace=workspace, name=name)
            except IntegrityError as exc:
                # Someone claimed it between the query above and here. Whoever they are, the name is
                # theirs now, and re-reading is what tells us so.
                raise TagOwnedElsewhere(f"Tag '{name}' was claimed by another workspace.") from exc
        tags[name] = _reconcile_tag(tag, client)
    return tags


def _reconcile_tag(tag: WorkflowTag, client: N8nClient) -> WorkflowTag:
    """Give a tag its n8n id, adopting one that is already there rather than making a duplicate."""
    if tag.n8n_id:
        return tag

    by_name = {n8n_tag.name: n8n_tag for n8n_tag in client.list_tags()}
    n8n_tag = by_name.get(tag.name)
    if n8n_tag is None:
        try:
            n8n_tag = client.create_tag(tag.name)
        except N8nConflict:
            # Created in the UI while this ran. Adopt it, the same as if it had been there all along.
            n8n_tag = {n8n_tag.name: n8n_tag for n8n_tag in client.list_tags()}[tag.name]

    tag.n8n_id = n8n_tag.id
    tag.save(update_fields=["n8n_id", "modified_at"])
    return tag


def workspace_index(workspace: Workspace, client: N8nClient) -> dict[str, N8nWorkflow]:
    """
    Every n8n workflow this workspace's tags reach, keyed by n8n id.

    This is the workspace boundary. Anything in here may be read and written by the workspace, and
    anything outside it may not, so it is built from the tags n8n reports on each workflow rather than
    from anything management remembers or from a filter n8n was asked to apply.
    """
    owned = {tag.name for tag in workspace.workflow_tags.all()}
    if not owned:
        return {}

    return {
        workflow.id: workflow
        for workflow in client.list_workflows()
        if workflow.tag_names & owned
    }


def push(workspace: Workspace, definitions: list[dict[str, Any]], *, client: N8nClient | None = None,
         allow_recreate: bool = False) -> list[PushResult]:
    """
    Write definitions to n8n. Stores no workflows, only the tags they claimed.

    Everything that can be refused is refused before the first write, so a batch either lands or does
    not. Half a batch would leave the workspace's files describing a state that no longer exists in
    either direction.
    """
    client = client or get_n8n_client()

    for index, definition in enumerate(definitions):
        validate_definition(definition, index=index)

    tag_names = [name for definition in definitions for name in definition_tags(definition)]
    tags = ensure_tags(workspace, tag_names, client)

    index_by_id = workspace_index(workspace, client)
    index_by_name = {workflow.name: workflow for workflow in index_by_id.values()}

    if not allow_recreate:
        unknown = [
            definition["id"] for definition in definitions
            if definition.get("id") and definition["id"] not in index_by_id
        ]
        if unknown:
            # The id either does not exist or is another workspace's. Both are refused, and refused
            # identically: distinguishing them would confirm that someone else's workflow is there.
            raise UnknownWorkflowId(
                f"No workflow with id {', '.join(sorted(unknown))} is reachable through this "
                "workspace's tags. Run 'invoke n8n.pull' to see what is actually there, or remove the "
                "'id' field to create a new workflow."
            )

    results: list[PushResult] = []
    for definition in definitions:
        names = definition_tags(definition)
        target = None
        if definition.get("id"):
            target = index_by_id.get(definition["id"])
        if target is None:
            target = index_by_name.get(definition["name"])

        if target is None:
            workflow = client.create_workflow(build_create_body(definition, client.project_id))
            action = "created"
        else:
            workflow = client.update_workflow(target.id, build_update_body(definition))
            action = "updated"

        # Always, on both branches: neither create nor update accepts tags in its body, so a change
        # to a definition's tag list would otherwise never reach n8n.
        client.set_workflow_tags(workflow.id, [tags[name].n8n_id for name in names])
        results.append(PushResult(name=workflow.name, n8n_id=workflow.id, action=action, tags=names))

    return results


def pull(workspace: Workspace, *, client: N8nClient | None = None, prune: bool = False) -> list[Workflow]:
    """
    Read every workflow this workspace's tags reach and mirror it into the database.

    Tags n8n reports that management does not own are ignored rather than adopted. A tag added to a
    workflow in the UI must not be able to hand a workspace a name that belongs to someone else.
    """
    client = client or get_n8n_client()

    owned_tags = {tag.name: tag for tag in workspace.workflow_tags.all()}
    if not owned_tags:
        return []

    index = workspace_index(workspace, client)

    workflows: list[Workflow] = []
    with transaction.atomic():
        for n8n_workflow in index.values():
            # Archived is n8n's recycle bin, not a deletion. Mirroring it would resurrect workflows a
            # human deliberately put away.
            if n8n_workflow.is_archived:
                continue
            workflows.append(_store(workspace, n8n_workflow, owned_tags))

        if prune:
            _prune(workspace, set(index))

    return workflows


def _store(workspace: Workspace, n8n_workflow: N8nWorkflow, owned_tags: dict[str, WorkflowTag]) -> Workflow:
    workflow = Workflow.objects.filter(workspace=workspace, n8n_id=n8n_workflow.id).first()
    if workflow is None:
        # Adoption: a row that has never been to n8n, matched by the slug its name produces. This is
        # what attaches an id to a workflow the workspace pushed a moment ago, or entered by hand.
        workflow = Workflow.objects.filter(
            workspace=workspace, n8n_id="", slug=slugify(n8n_workflow.name),
        ).first()
    if workflow is None:
        workflow = Workflow(workspace=workspace)

    workflow.name = n8n_workflow.name
    workflow.n8n_id = n8n_workflow.id
    workflow.definition = sanitize_definition(n8n_workflow.definition)
    workflow.pulled_at = timezone.now()
    workflow.save()
    workflow.tags.set([owned_tags[name] for name in n8n_workflow.tag_names if name in owned_tags])
    return workflow


def _prune(workspace: Workspace, present: set[str]) -> None:
    """Delete rows for workflows n8n no longer has. Only ever reached with a complete listing."""
    Workflow.objects.filter(workspace=workspace).exclude(n8n_id="").exclude(n8n_id__in=present).delete()


def sync(workspace: Workspace, definitions: list[dict[str, Any]],
         *, client: N8nClient | None = None) -> tuple[list[PushResult], list[Workflow]]:
    """Push, then pull. The only operation that leaves files, database and n8n agreeing."""
    client = client or get_n8n_client()
    pushed = push(workspace, definitions, client=client)
    return pushed, pull(workspace, client=client)


def stored_definitions(workspace: Workspace) -> list[dict[str, Any]]:
    """Rebuild push input from the mirror, which is what makes the mirror worth keeping."""
    definitions: list[dict[str, Any]] = []
    for workflow in workspace.workflows.prefetch_related("tags"):
        definition = dict(workflow.definition)
        definition["name"] = workflow.name
        definition["tags"] = [tag.name for tag in workflow.tags.all()]
        if workflow.n8n_id:
            definition["id"] = workflow.n8n_id
        definitions.append(definition)
    return definitions


def repush(workspaces: Iterable[Workspace], *, client: N8nClient | None = None) -> dict[str, list[PushResult]]:
    """
    Rebuild n8n from the mirror, for as many workspaces as asked.

    The recovery path after n8n has lost its data: stored ids no longer resolve, so recreation is
    allowed here and the fresh ids are written back onto the rows. That write-back is the one thing
    push otherwise never does, which is why this is a separate control-only operation rather than a
    flag on push that a workspace could reach.
    """
    client = client or get_n8n_client()

    results: dict[str, list[PushResult]] = {}
    for workspace in workspaces:
        definitions = stored_definitions(workspace)
        if not definitions:
            continue
        pushed = push(workspace, definitions, client=client, allow_recreate=True)
        _write_back_ids(workspace, pushed)
        results[workspace.module] = pushed
    return results


def _write_back_ids(workspace: Workspace, results: list[PushResult]) -> None:
    by_name = {workflow.name: workflow for workflow in workspace.workflows.all()}
    for result in results:
        workflow = by_name.get(result.name)
        if workflow is not None and workflow.n8n_id != result.n8n_id:
            workflow.n8n_id = result.n8n_id
            workflow.save(update_fields=["n8n_id", "modified_at"])


__all__ = [
    "InvalidDefinition",
    "PushResult",
    "TagOwnedElsewhere",
    "UnknownWorkflowId",
    "WorkflowServiceError",
    "ensure_tags",
    "pull",
    "push",
    "repush",
    "stored_definitions",
    "sync",
    "workspace_index",
]
