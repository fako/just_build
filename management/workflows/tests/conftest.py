import json
from pathlib import Path

import pytest

from access_control.models import Workspace
from workflows.models import Workflow, WorkflowTag


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def workspace(db) -> Workspace:
    return Workspace.objects.create(name="Magic Match", module="magic_match")


@pytest.fixture
def other_workspace(db) -> Workspace:
    return Workspace.objects.create(name="Just Automate", module="just_automate")


@pytest.fixture
def business_tag(workspace) -> WorkflowTag:
    return WorkflowTag.objects.create(workspace=workspace, name="business")


@pytest.fixture
def definition():
    """The shape a workspace file holds: a definition plus the tags it asks for."""
    def build(name: str = "Github invoices", tags: list[str] | None = None, **extra) -> dict:
        return {
            "name": name,
            "nodes": [],
            "connections": {},
            "settings": {"executionOrder": "v1"},
            "tags": tags if tags is not None else ["business"],
            **extra,
        }

    return build


@pytest.fixture
def exported_workflow() -> dict:
    """A real n8n export, trimmed. The only fixture here that management did not invent."""
    with open(FIXTURES / "github_invoices.json") as workflow_file:
        return json.load(workflow_file)


@pytest.fixture
def stored_workflow(workspace, business_tag, definition) -> Workflow:
    workflow = Workflow.objects.create(
        workspace=workspace, name="Github invoices", n8n_id="wfExisting",
        definition=definition(),
    )
    workflow.tags.set([business_tag])
    return workflow
