"""
Turning n8n workflow JSON into something storable, and back into something postable.

Both directions are allowlists rather than blocklists. n8n's request schemas are declared
`additionalProperties: false`, so a single key it did not ask for fails the whole call, and the set it
asks for is not the same between create and update: `projectId` is accepted only on create, and
`description` only on update. Building a body by deleting known-bad keys from a pulled definition
would work until n8n adds a field, and then break on a version bump for reasons nobody would connect
to this file.

What is stripped on the way in is everything n8n owns. `staticData` is in that list even though it
looks like content: it changes as executions run, so storing it would make every pull report a diff.
Node level credential references are inside `nodes` and are never touched, so a workflow keeps
pointing at the same credentials it always did without management ever holding one.
"""
from __future__ import annotations

from typing import Any


# Everything n8n decides for itself. None of it is stored, and none of it is ever sent back.
N8N_OWNED_FIELDS = (
    "id",
    "active",
    "isArchived",
    "createdAt",
    "updatedAt",
    "versionId",
    "activeVersionId",
    "activeVersion",
    "versionCounter",
    "versionMetadata",
    "triggerCount",
    "meta",
    "shared",
    "tags",
    "staticData",
    # Set once, when the workflow is created, and never part of what a workspace authored. Storing it
    # would leave the mirror quoting a project that configuration may since have moved away from.
    "projectId",
)

# Exactly what POST /workflows accepts. projectId is create-only.
CREATE_FIELDS = ("name", "nodes", "connections", "settings", "pinData", "projectId")

# Exactly what PUT /workflows/{id} accepts. description is update-only.
UPDATE_FIELDS = ("name", "nodes", "connections", "settings", "pinData", "description")

# Without these a definition is not a workflow, whichever direction it came from.
REQUIRED_FIELDS = ("name", "nodes", "connections")


class InvalidDefinition(ValueError):
    """A definition that cannot be sent to n8n, reported before anything is written."""


def sanitize_definition(definition: dict[str, Any]) -> dict[str, Any]:
    """Drop everything n8n owns, leaving what a workspace actually authored."""
    return {key: value for key, value in definition.items() if key not in N8N_OWNED_FIELDS}


def validate_definition(definition: Any, *, index: int) -> None:
    """
    Check one incoming definition, naming the position so a failure points at a file.

    Runs over the whole batch before anything is pushed, so a bad definition halfway down cannot
    leave the first half applied.
    """
    position = f"workflow {index + 1}"
    if not isinstance(definition, dict):
        raise InvalidDefinition(f"{position} is not an object")

    name = definition.get("name")
    if not isinstance(name, str) or not name.strip():
        raise InvalidDefinition(f"{position} has no name")

    position = f"Workflow '{name}'"
    for field in REQUIRED_FIELDS:
        if field not in definition:
            raise InvalidDefinition(f"{position} is missing '{field}'")
    if not isinstance(definition["nodes"], list):
        raise InvalidDefinition(f"{position} has a 'nodes' value that is not a list")
    if not isinstance(definition["connections"], dict):
        raise InvalidDefinition(f"{position} has a 'connections' value that is not an object")

    tags = definition.get("tags") or []
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        raise InvalidDefinition(f"{position} has a 'tags' value that is not a list of names")
    if not tags:
        # The n8n project is shared by every workspace, so a tag is the only thing that lets a later
        # pull find this workflow again. An untagged workflow is not merely untidy, it is unreachable.
        raise InvalidDefinition(
            f"{position} carries no tags, so nothing could find it again. Add one to the workflow's "
            "'tags' list, and claim it first with 'invoke n8n.tags --add=<name>' if it is new."
        )

    workflow_id = definition.get("id")
    if workflow_id is not None and not isinstance(workflow_id, str):
        raise InvalidDefinition(f"{position} has an 'id' that is not a string")


def definition_tags(definition: dict[str, Any]) -> list[str]:
    """The tag names a definition asks for, deduplicated and in the order they were written."""
    seen: dict[str, None] = {}
    for tag in definition.get("tags") or []:
        seen.setdefault(tag, None)
    return list(seen)


def _build_body(definition: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    body = {field: definition[field] for field in fields if field in definition}
    # settings is required by both schemas and n8n rejects a workflow without one, but a hand written
    # file routinely leaves it out. Defaulting is friendlier than a 400 relayed from n8n.
    body.setdefault("settings", {})
    return body


def build_create_body(definition: dict[str, Any], project_id: str) -> dict[str, Any]:
    body = _build_body(definition, CREATE_FIELDS)
    body["projectId"] = project_id
    return body


def build_update_body(definition: dict[str, Any]) -> dict[str, Any]:
    return _build_body(definition, UPDATE_FIELDS)
