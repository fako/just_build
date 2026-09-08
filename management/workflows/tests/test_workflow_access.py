"""
Who may reach what.

Two questions are asked of every route. Does a workspace stay inside its own rows, and is the small
set of operations that cross workspaces closed to it? The second matters more than it looks: repush
writes ids back across every workspace at once, and releasing a tag decides who inherits workflows.
"""
import json

import pytest

from workflows.models import Workflow, WorkflowTag


pytestmark = pytest.mark.django_db


@pytest.fixture
def their_workflow(other_workspace):
    tag = WorkflowTag.objects.create(workspace=other_workspace, name="theirs")
    workflow = Workflow.objects.create(
        workspace=other_workspace, name="Theirs", n8n_id="wfTheirs",
    )
    workflow.tags.set([tag])
    return workflow


def test_a_workspace_sees_only_its_own(workspace, workspace_client, stored_workflow, their_workflow):
    response = workspace_client(workspace).get("/api/v1/workflows/")

    assert [entry["name"] for entry in response.json()] == ["Github invoices"]


def test_control_sees_every_workspace(control_client, stored_workflow, their_workflow):
    response = control_client.get("/api/v1/workflows/")

    assert sorted(entry["name"] for entry in response.json()) == ["Github invoices", "Theirs"]


def test_another_workspace_workflow_is_a_404_not_a_403(workspace, workspace_client, their_workflow):
    """A 403 would confirm it exists, which is a fact this caller has no business learning."""
    response = workspace_client(workspace).get(f"/api/v1/workflows/{their_workflow.id}/")

    assert response.status_code == 404


def test_another_workspace_workflow_cannot_be_deleted(workspace, workspace_client, n8n,
                                                      their_workflow):
    response = workspace_client(workspace).delete(f"/api/v1/workflows/{their_workflow.id}/")

    assert response.status_code == 404
    assert Workflow.objects.filter(pk=their_workflow.pk).exists()


def test_a_workspace_cannot_act_on_another_by_naming_it(workspace, workspace_client, n8n,
                                                        other_workspace, definition):
    response = workspace_client(workspace).post(
        "/api/v1/workflows/push/",
        data=json.dumps({"workspace_module": "just_automate", "workflows": [definition()]}),
        content_type="application/json",
    )

    assert response.status_code == 404


def test_a_workspace_sees_only_its_own_tags(workspace, workspace_client, business_tag, their_workflow):
    response = workspace_client(workspace).get("/api/v1/workflows/tags/")

    assert [entry["name"] for entry in response.json()] == ["business"]


def test_repush_is_control_only(workspace, workspace_client, n8n):
    response = workspace_client(workspace).post(
        "/api/v1/workflows/repush/", data=json.dumps({}), content_type="application/json",
    )

    assert response.status_code == 401


def test_releasing_a_tag_is_control_only(workspace, workspace_client, business_tag):
    response = workspace_client(workspace).delete(f"/api/v1/workflows/tags/{business_tag.id}/")

    assert response.status_code == 401
    assert WorkflowTag.objects.filter(pk=business_tag.pk).exists()


@pytest.mark.parametrize("method,path", [
    ("get", "/api/v1/workflows/"),
    ("post", "/api/v1/workflows/push/"),
    ("post", "/api/v1/workflows/pull/"),
    ("post", "/api/v1/workflows/sync/"),
    ("get", "/api/v1/workflows/tags/"),
])
def test_every_route_needs_a_key(client, method, path):
    if method == "post":
        response = client.post(path, data="{}", content_type="application/json")
    else:
        response = client.get(path)

    assert response.status_code == 401


def test_repush_covers_every_workspace_for_control(control_client, n8n, workspace, other_workspace,
                                                   definition):
    from workflows.services import sync

    sync(workspace, [definition(name="Mine", tags=["business"])])
    sync(other_workspace, [definition(name="Theirs", tags=["theirs"])])

    response = control_client.post(
        "/api/v1/workflows/repush/", data=json.dumps({}), content_type="application/json",
    )

    assert response.status_code == 200
    assert sorted(response.json()) == ["just_automate", "magic_match"]
