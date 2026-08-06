import pytest
from django.test import Client

from access_control.models import Workspace, generate_api_key, hash_api_key


# Every route, with the method used to reach it and whether a workspace key should be allowed there.
# Control-only routes are the ones that assume a host filesystem behind the caller.
WORKSPACE_ROUTES = [
    ("get", "/api/v1/workspaces/", True),
    ("post", "/api/v1/workspaces/", False),
    ("get", "/api/v1/workspaces/ssh-config/", False),
    ("get", "/api/v1/workspaces/{module}/", True),
    ("patch", "/api/v1/workspaces/{module}/", False),
    ("delete", "/api/v1/workspaces/{module}/", False),
    ("post", "/api/v1/workspaces/{module}/rotate-key/", False),
]


@pytest.fixture
def acme(db) -> Workspace:
    return Workspace.objects.create(name="Acme", module="acme")


def request_route(client: Client, method: str, path: str):
    return getattr(client, method)(path, data={}, content_type="application/json")


@pytest.mark.django_db
@pytest.mark.parametrize("method,path,_workspace_allowed", WORKSPACE_ROUTES)
def test_routes_reject_anonymous_callers(client, acme, method, path, _workspace_allowed):
    response = request_route(client, method, path.format(module=acme.module))

    assert response.status_code == 401


@pytest.mark.django_db
@pytest.mark.parametrize("method,path,_workspace_allowed", WORKSPACE_ROUTES)
def test_routes_reject_unknown_keys(client, acme, method, path, _workspace_allowed):
    unknown = Client(headers={"authorization": f"Bearer {generate_api_key('workspace')}"})

    response = request_route(unknown, method, path.format(module=acme.module))

    assert response.status_code == 401


@pytest.mark.django_db
@pytest.mark.parametrize("scheme", ["", "bogus", "workspace"])
def test_control_key_is_not_accepted_under_another_scheme(client, settings, control_api_key, scheme):
    secret = control_api_key.partition(":")[2]
    token = f"{scheme}:{secret}" if scheme else secret
    disguised = Client(headers={"authorization": f"Bearer {token}"})

    response = disguised.get("/api/v1/workspaces/")

    assert response.status_code == 401


@pytest.mark.django_db
@pytest.mark.parametrize("method,path,workspace_allowed", WORKSPACE_ROUTES)
def test_control_only_routes_reject_workspace_keys(workspace_client, acme, method, path, workspace_allowed):
    client = workspace_client(acme)

    response = request_route(client, method, path.format(module=acme.module))

    if workspace_allowed:
        assert response.status_code != 401
    else:
        assert response.status_code == 401


@pytest.mark.django_db
def test_workspace_only_sees_itself(workspace_client, acme):
    Workspace.objects.create(name="Other", module="other")
    client = workspace_client(acme)

    response = client.get("/api/v1/workspaces/")

    assert response.status_code == 200
    assert [workspace["module"] for workspace in response.json()] == ["acme"]


@pytest.mark.django_db
def test_workspace_cannot_read_another_workspace(workspace_client, acme):
    other = Workspace.objects.create(name="Other", module="other")
    client = workspace_client(acme)

    response = client.get(f"/api/v1/workspaces/{other.module}/")

    # A 404 rather than a 403, so asking is not a way to learn that the workspace exists.
    assert response.status_code == 404
    assert response.json() == {"detail": "Workspace not found"}


@pytest.mark.django_db
def test_create_workspace_stores_only_the_key_hash(control_client):
    response = control_client.post(
        "/api/v1/workspaces/",
        data={"name": "Acme", "module": "acme"},
        content_type="application/json",
    )

    api_key = response.json()["api_key"]
    workspace = Workspace.objects.get(module="acme")
    assert workspace.api_key_hash == hash_api_key(api_key)
    assert api_key not in workspace.api_key_hash
    assert workspace.api_key_created_at is not None


@pytest.mark.django_db
def test_issued_key_authenticates_its_own_workspace(control_client, acme):
    response = control_client.post(f"/api/v1/workspaces/{acme.module}/rotate-key/")

    assert response.status_code == 200
    api_key = response.json()["api_key"]
    client = Client(headers={"authorization": f"Bearer {api_key}"})
    assert client.get(f"/api/v1/workspaces/{acme.module}/").status_code == 200


@pytest.mark.django_db
def test_rotating_a_key_retires_the_previous_one(control_client, workspace_client, acme):
    stale_client = workspace_client(acme)
    assert stale_client.get(f"/api/v1/workspaces/{acme.module}/").status_code == 200

    control_client.post(f"/api/v1/workspaces/{acme.module}/rotate-key/")

    assert stale_client.get(f"/api/v1/workspaces/{acme.module}/").status_code == 401


@pytest.mark.django_db
def test_control_authentication_is_denied_when_no_key_is_configured(settings, acme):
    settings.CONTROL_API_KEY = None
    client = Client(headers={"authorization": "Bearer control:11111111-1111-1111-1111-111111111111"})

    response = client.get("/api/v1/workspaces/")

    assert response.status_code == 401
