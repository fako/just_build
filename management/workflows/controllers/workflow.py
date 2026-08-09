"""
The workflow API.

The auth split follows the one runtimes uses, applied to a different privilege. There the line is the
host filesystem; here it is whether an operation can reach past the calling workspace. Push, pull and
sync stay open to a workspace, because they are exactly what an agent inside one is meant to run and
because every one of them is scoped by the workspace's own tags. Repush and releasing a tag are
control only: the first writes ids back across every workspace at once, the second hands one
workspace's tag to nobody or to someone else.

Route order matters. The literal paths are declared before `{workflow_id}`, or a request for
`/workflows/push/` resolves as a workflow whose id happens to be "push".
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from django.http import HttpRequest
from ninja import Router, Schema
from ninja.errors import HttpError

from access_control.authentication import control_api_key_auth
from access_control.models import Workspace
from workflows.definitions import InvalidDefinition
from workflows.models import Workflow, WorkflowTag
from workflows.n8n import (
    N8nConflict,
    N8nError,
    N8nNotFound,
    N8nRejected,
    get_n8n_client,
)
from workflows.services import (
    TagOwnedElsewhere,
    UnknownWorkflowId,
    ensure_tags,
    pull,
    push,
    repush,
    sync,
)


controller = Router()


class WorkflowSchema(Schema):
    id: UUID
    workspace_module: str
    name: str
    slug: str
    n8n_id: str
    definition: dict
    tags: list[str]
    pulled_at: datetime | None

    @staticmethod
    def resolve_workspace_module(obj: Workflow) -> str:
        return obj.workspace.module

    @staticmethod
    def resolve_tags(obj: Workflow) -> list[str]:
        return [tag.name for tag in obj.tags.all()]


class WorkflowTagSchema(Schema):
    id: UUID
    workspace_module: str
    name: str
    n8n_id: str

    @staticmethod
    def resolve_workspace_module(obj: WorkflowTag) -> str:
        return obj.workspace.module


class TagCreateSchema(Schema):
    workspace_module: str | None = None
    name: str


class PushSchema(Schema):
    workspace_module: str | None = None
    # Raw n8n definitions. Not modelled as a schema on purpose: n8n already declares this shape, and
    # restating it here would leave two versions of it to drift apart.
    workflows: list[dict]


class PullSchema(Schema):
    workspace_module: str | None = None
    prune: bool = False


class RepushSchema(Schema):
    workspace_module: str | None = None


class PushResultSchema(Schema):
    name: str
    n8n_id: str
    action: str
    tags: list[str]


class SyncResultSchema(Schema):
    pushed: list[PushResultSchema]
    workflows: list[WorkflowSchema]


def resolve_workspace(request: HttpRequest, workspace_module: str | None) -> Workspace:
    """
    The workspace a call acts on, always through the caller's own scope.

    A workspace key needs no module and cannot name another's; a control key must name one, since it
    has no single workspace of its own. Out of scope is a 404 rather than a 403, so a caller learns
    nothing about workspaces it cannot see.
    """
    workspaces = request.auth.workspaces()
    if workspace_module:
        workspace = workspaces.filter(module=workspace_module).first()
    else:
        workspace = workspaces.first() if not request.auth.is_control else None
        if workspace is None and request.auth.is_control:
            raise HttpError(422, "A control key has to name a workspace_module.")

    if workspace is None:
        raise HttpError(404, f"Workspace '{workspace_module}' not found")
    return workspace


def translate_errors(exc: Exception) -> HttpError:
    """One mapping from the failures below this layer to status codes, used by every route."""
    if isinstance(exc, InvalidDefinition):
        return HttpError(422, str(exc))
    if isinstance(exc, TagOwnedElsewhere):
        return HttpError(409, str(exc))
    if isinstance(exc, UnknownWorkflowId):
        return HttpError(409, str(exc))
    if isinstance(exc, N8nRejected):
        return HttpError(422, str(exc))
    if isinstance(exc, N8nConflict):
        return HttpError(409, str(exc))
    if isinstance(exc, N8nNotFound):
        return HttpError(404, str(exc))
    # Unavailable and unauthorized both mean management could not do its job, not that the caller
    # asked for something wrong, so they are a bad gateway rather than a client error.
    return HttpError(502, str(exc))


@controller.get("/", response=list[WorkflowSchema], tags=["Workflows"])
def list_workflows(request: HttpRequest, workspace_module: str | None = None,
                   tag: str | None = None) -> list[Workflow]:
    workflows = request.auth.workflows().select_related("workspace").prefetch_related("tags")
    if workspace_module:
        workflows = workflows.filter(workspace__module=workspace_module)
    if tag:
        workflows = workflows.filter(tags__name=tag)
    return list(workflows)


@controller.post("/push/", response=list[PushResultSchema], tags=["Workflows"])
def push_workflows(request: HttpRequest, data: PushSchema) -> list:
    """Write definitions to n8n without storing them. A pull is what records the result."""
    workspace = resolve_workspace(request, data.workspace_module)
    try:
        return push(workspace, data.workflows)
    except (InvalidDefinition, TagOwnedElsewhere, UnknownWorkflowId, N8nError) as exc:
        raise translate_errors(exc) from exc


@controller.post("/pull/", response=list[WorkflowSchema], tags=["Workflows"])
def pull_workflows(request: HttpRequest, data: PullSchema) -> list[Workflow]:
    workspace = resolve_workspace(request, data.workspace_module)
    try:
        return pull(workspace, prune=data.prune)
    except N8nError as exc:
        raise translate_errors(exc) from exc


@controller.post("/sync/", response=SyncResultSchema, tags=["Workflows"])
def sync_workflows(request: HttpRequest, data: PushSchema) -> dict:
    """Push then pull, so the caller gets back what n8n actually holds, ids included."""
    workspace = resolve_workspace(request, data.workspace_module)
    try:
        pushed, workflows = sync(workspace, data.workflows)
    except (InvalidDefinition, TagOwnedElsewhere, UnknownWorkflowId, N8nError) as exc:
        raise translate_errors(exc) from exc
    return {"pushed": pushed, "workflows": workflows}


@controller.post("/repush/", response=dict[str, list[PushResultSchema]], auth=control_api_key_auth,
                 tags=["Workflows"])
def repush_workflows(request: HttpRequest, data: RepushSchema) -> dict:
    """
    Rebuild n8n from the mirror. The recovery path after n8n has lost its data.

    Control only because it recreates workflows whose stored ids no longer resolve and writes the new
    ids back, which is the one case where a push changes this database.
    """
    workspaces = request.auth.workspaces()
    if data.workspace_module:
        workspaces = workspaces.filter(module=data.workspace_module)
    try:
        return repush(workspaces)
    except (InvalidDefinition, TagOwnedElsewhere, N8nError) as exc:
        raise translate_errors(exc) from exc


@controller.get("/tags/", response=list[WorkflowTagSchema], tags=["Workflows"])
def list_tags(request: HttpRequest, workspace_module: str | None = None) -> list[WorkflowTag]:
    tags = request.auth.workflow_tags().select_related("workspace")
    if workspace_module:
        tags = tags.filter(workspace__module=workspace_module)
    return list(tags)


@controller.post("/tags/", response={201: WorkflowTagSchema}, tags=["Workflows"])
def create_tag(request: HttpRequest, data: TagCreateSchema) -> tuple[int, WorkflowTag]:
    """Reserve a tag name for a workspace, in management and in n8n at the same time."""
    workspace = resolve_workspace(request, data.workspace_module)
    try:
        tags = ensure_tags(workspace, [data.name], get_n8n_client())
    except (TagOwnedElsewhere, N8nError) as exc:
        raise translate_errors(exc) from exc
    return 201, tags[data.name]


@controller.delete("/tags/{tag_id}/", response={204: None}, auth=control_api_key_auth, tags=["Workflows"])
def delete_tag(request: HttpRequest, tag_id: UUID) -> tuple[int, None]:
    """
    Release a tag reservation.

    Refused while workflows still carry it: releasing it then would let another workspace claim the
    name and inherit those workflows on its next pull.
    """
    tag = request.auth.workflow_tags().filter(pk=tag_id).first()
    if tag is None:
        raise HttpError(404, "Tag not found")
    if tag.workflows.exists():
        raise HttpError(
            409,
            f"Tag '{tag.name}' still covers {tag.workflows.count()} workflow(s). Retag or delete them "
            "first, or another workspace could claim the name and inherit them.",
        )
    tag.delete()
    return 204, None


@controller.get("/{workflow_id}/", response=WorkflowSchema, tags=["Workflows"])
def get_workflow(request: HttpRequest, workflow_id: UUID) -> Workflow:
    return get_workflow_or_404(request, workflow_id)


@controller.delete("/{workflow_id}/", response={204: None}, tags=["Workflows"])
def delete_workflow(request: HttpRequest, workflow_id: UUID) -> tuple[int, None]:
    """Delete in n8n first, then here. The other order would orphan the workflow on a failure."""
    workflow = get_workflow_or_404(request, workflow_id)
    if workflow.n8n_id:
        try:
            get_n8n_client().delete_workflow(workflow.n8n_id)
        except N8nNotFound:
            # Already gone in n8n. The row is the only thing left to remove.
            pass
        except N8nError as exc:
            raise translate_errors(exc) from exc
    workflow.delete()
    return 204, None


def get_workflow_or_404(request: HttpRequest, workflow_id: UUID) -> Workflow:
    workflow = request.auth.workflows().filter(pk=workflow_id).select_related("workspace").first()
    if workflow is None:
        raise HttpError(404, "Workflow not found")
    return workflow
