import json

import pytest

from workflows.models import Workflow, WorkflowTag
from workflows.n8n import N8nUnavailable


pytestmark = pytest.mark.django_db


def post(client, path: str, payload: dict):
    return client.post(f"/api/v1/workflows/{path}", data=json.dumps(payload),
                       content_type="application/json")


def test_list_returns_the_mirror(workspace, workspace_client, stored_workflow):
    response = workspace_client(workspace).get("/api/v1/workflows/")

    assert response.status_code == 200
    assert response.json() == [{
        "id": str(stored_workflow.id),
        "workspace_module": "magic_match",
        "name": "Github invoices",
        "slug": "github-invoices",
        "n8n_id": "wfExisting",
        "definition": stored_workflow.definition,
        "tags": ["business"],
        "pulled_at": None,
    }]


def test_list_filters_by_tag(workspace, workspace_client, stored_workflow):
    client = workspace_client(workspace)

    assert len(client.get("/api/v1/workflows/?tag=business").json()) == 1
    assert client.get("/api/v1/workflows/?tag=other").json() == []


def test_push_returns_what_it_did(workspace, workspace_client, n8n, definition):
    response = post(workspace_client(workspace), "push/", {"workflows": [definition()]})

    assert response.status_code == 200
    assert [(entry["name"], entry["action"]) for entry in response.json()] == \
        [("Github invoices", "created")]
    assert not Workflow.objects.exists()


def test_sync_returns_both_halves(workspace, workspace_client, n8n, definition):
    response = post(workspace_client(workspace), "sync/", {"workflows": [definition()]})

    assert response.status_code == 200
    body = response.json()
    assert body["pushed"][0]["action"] == "created"
    assert body["workflows"][0]["n8n_id"] == body["pushed"][0]["n8n_id"]


def test_an_untagged_workflow_is_a_422(workspace, workspace_client, n8n, definition):
    response = post(workspace_client(workspace), "push/", {"workflows": [definition(tags=[])]})

    assert response.status_code == 422
    assert "carries no tags" in response.json()["detail"]


def test_a_foreign_tag_is_a_409(workspace, other_workspace, workspace_client, n8n, definition):
    WorkflowTag.objects.create(workspace=other_workspace, name="theirs")

    response = post(workspace_client(workspace), "push/", {"workflows": [definition(tags=["theirs"])]})

    assert response.status_code == 409
    assert "just_automate" in response.json()["detail"]


def test_an_unreachable_id_is_a_409(workspace, workspace_client, n8n, definition):
    response = post(workspace_client(workspace), "push/", {"workflows": [definition(id="wfGone")]})

    assert response.status_code == 409


def test_n8n_being_down_is_a_502(workspace, workspace_client, n8n, definition):
    """The caller asked for something reasonable. It is management that could not deliver."""
    n8n.fail("list_tags", N8nUnavailable("n8n is not running in this profile"))

    response = post(workspace_client(workspace), "push/", {"workflows": [definition()]})

    assert response.status_code == 502
    assert "profile" in response.json()["detail"]


def test_claiming_a_tag(workspace, workspace_client, n8n):
    response = post(workspace_client(workspace), "tags/", {"name": "invoices"})

    assert response.status_code == 201
    assert response.json()["name"] == "invoices"
    assert WorkflowTag.objects.get(name="invoices").workspace == workspace


def test_claiming_a_taken_tag_names_the_owner(workspace, other_workspace, workspace_client, n8n):
    WorkflowTag.objects.create(workspace=other_workspace, name="invoices")

    response = post(workspace_client(workspace), "tags/", {"name": "invoices"})

    assert response.status_code == 409
    assert "just_automate" in response.json()["detail"]


def test_a_tag_in_use_cannot_be_released(workspace, control_client, business_tag, stored_workflow):
    """Releasing it would let another workspace claim the name and inherit these workflows."""
    response = control_client.delete(f"/api/v1/workflows/tags/{business_tag.id}/")

    assert response.status_code == 409
    assert WorkflowTag.objects.filter(pk=business_tag.pk).exists()


def test_an_unused_tag_can_be_released(workspace, control_client, business_tag):
    response = control_client.delete(f"/api/v1/workflows/tags/{business_tag.id}/")

    assert response.status_code == 204
    assert not WorkflowTag.objects.exists()


def test_deleting_a_workflow_removes_it_from_n8n_first(workspace, workspace_client, n8n,
                                                       business_tag, stored_workflow):
    n8n.add_workflow("Github invoices", workflow_id="wfExisting")

    response = workspace_client(workspace).delete(f"/api/v1/workflows/{stored_workflow.id}/")

    assert response.status_code == 204
    assert "wfExisting" not in n8n.workflows
    assert not Workflow.objects.exists()


def test_deleting_a_workflow_n8n_already_lost_still_clears_the_row(
        workspace, workspace_client, n8n, stored_workflow):
    response = workspace_client(workspace).delete(f"/api/v1/workflows/{stored_workflow.id}/")

    assert response.status_code == 204
    assert not Workflow.objects.exists()


def test_control_has_to_name_a_workspace(control_client, n8n, definition):
    """A control key belongs to no workspace, so there is nothing to default to."""
    response = post(control_client, "push/", {"workflows": [definition()]})

    assert response.status_code == 422
    assert "workspace_module" in response.json()["detail"]
