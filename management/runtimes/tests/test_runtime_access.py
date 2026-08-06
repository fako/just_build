"""
Scoping tests for the runtime API.

The point of the whole design is that magic_match may restart its own runtimes and nothing else, so
every route is checked rather than a representative sample.
"""
import pytest
from django.test import Client

from access_control.models import Workspace, generate_api_key
from runtimes.models import Runtime


# method, path template, whether the owning workspace may use it.
# Control-only routes are the ones that assume a host filesystem behind the caller.
RUNTIME_ROUTES = [
    ("get", "/api/v1/runtimes/", True),
    ("post", "/api/v1/runtimes/", False),
    ("get", "/api/v1/runtimes/configs/", False),
    ("post", "/api/v1/runtimes/reload/", False),
    ("get", "/api/v1/runtimes/{id}/", True),
    ("patch", "/api/v1/runtimes/{id}/", False),
    ("delete", "/api/v1/runtimes/{id}/", False),
    ("post", "/api/v1/runtimes/{id}/enable/", False),
    ("post", "/api/v1/runtimes/{id}/disable/", False),
    ("get", "/api/v1/runtimes/{id}/configs/", False),
    ("get", "/api/v1/runtimes/{id}/sync-commands/", False),
    ("post", "/api/v1/runtimes/{id}/restart/", True),
    ("post", "/api/v1/runtimes/{id}/start/", True),
    ("post", "/api/v1/runtimes/{id}/stop/", True),
    ("get", "/api/v1/runtimes/{id}/status/", True),
    ("get", "/api/v1/runtimes/{id}/logs/", True),
]
RUNTIME_ROUTES_WITH_ID = [route for route in RUNTIME_ROUTES if "{id}" in route[1]]


@pytest.fixture
def magic_match(db) -> Workspace:
    return Workspace.objects.create(name="Magic Match", module="magic_match")


@pytest.fixture
def other_workspace(db) -> Workspace:
    return Workspace.objects.create(name="Other", module="other")


@pytest.fixture
def own_runtime(magic_match, supervisor) -> Runtime:
    supervisor.add_program("magic_match_web")
    supervisor.add_program("nginx")
    return Runtime.objects.create(workspace=magic_match, type="django", name="web", port=8001, is_enabled=True)


@pytest.fixture
def foreign_runtime(other_workspace, supervisor) -> Runtime:
    supervisor.add_program("other_web")
    return Runtime.objects.create(workspace=other_workspace, type="django", name="web", port=8002, is_enabled=True)


def request_route(client: Client, method: str, path: str):
    return getattr(client, method)(path, data={}, content_type="application/json")


@pytest.mark.django_db
@pytest.mark.parametrize("method,path,_allowed", RUNTIME_ROUTES)
def test_every_route_rejects_anonymous_callers(client, own_runtime, method, path, _allowed):
    response = request_route(client, method, path.format(id=own_runtime.id))

    assert response.status_code == 401


@pytest.mark.django_db
@pytest.mark.parametrize("method,path,_allowed", RUNTIME_ROUTES)
def test_every_route_rejects_unknown_keys(client, own_runtime, method, path, _allowed):
    unknown = Client(headers={"authorization": f"Bearer {generate_api_key('workspace')}"})

    response = request_route(unknown, method, path.format(id=own_runtime.id))

    assert response.status_code == 401


@pytest.mark.django_db
@pytest.mark.parametrize("method,path,allowed", RUNTIME_ROUTES)
def test_control_only_routes_reject_workspace_keys(workspace_client, magic_match, own_runtime, method, path, allowed):
    client = workspace_client(magic_match)

    response = request_route(client, method, path.format(id=own_runtime.id))

    if allowed:
        assert response.status_code != 401, f"{method} {path} should be open to the owning workspace"
    else:
        assert response.status_code == 401, f"{method} {path} should be control only"


@pytest.mark.django_db
@pytest.mark.parametrize("method,path,_allowed", RUNTIME_ROUTES_WITH_ID)
def test_a_workspace_cannot_reach_another_workspaces_runtime(
    workspace_client, magic_match, own_runtime, foreign_runtime, method, path, _allowed,
):
    client = workspace_client(magic_match)

    response = request_route(client, method, path.format(id=foreign_runtime.id))

    # 404 rather than 403, so asking is not a way to learn the runtime exists. Control-only routes
    # answer 401 first, which tells the caller even less.
    assert response.status_code in (401, 404), f"{method} {path} leaked a foreign runtime"


@pytest.mark.django_db
def test_a_workspace_restarts_only_its_own_runtime(workspace_client, magic_match, own_runtime, foreign_runtime,
                                                   supervisor):
    client = workspace_client(magic_match)

    assert client.post(f"/api/v1/runtimes/{own_runtime.id}/restart/").status_code == 200
    assert client.post(f"/api/v1/runtimes/{foreign_runtime.id}/restart/").status_code == 404
    assert ("restart", "other_web") not in supervisor.calls


@pytest.mark.django_db
def test_listing_runtimes_shows_only_your_own(workspace_client, magic_match, own_runtime, foreign_runtime):
    client = workspace_client(magic_match)

    response = client.get("/api/v1/runtimes/")

    assert response.status_code == 200
    assert [runtime["program_name"] for runtime in response.json()] == ["magic_match_web"]


@pytest.mark.django_db
def test_filtering_by_another_workspace_returns_nothing(workspace_client, magic_match, own_runtime, foreign_runtime):
    client = workspace_client(magic_match)

    response = client.get("/api/v1/runtimes/?workspace_module=other")

    assert response.json() == []
