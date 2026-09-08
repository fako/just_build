import pytest
import requests

from workflows.n8n import (
    FAKE_N8N,
    FakeN8nClient,
    HttpN8nClient,
    N8nConflict,
    N8nNotFound,
    N8nRejected,
    N8nUnauthorized,
    N8nUnavailable,
    get_n8n_client,
)


class StubResponse:

    def __init__(self, status_code: int = 200, payload=None, text: str = "", raw: bool = False) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.content = b"" if payload is None and not text else b"body"
        self._raw = raw

    def json(self):
        if self._raw:
            raise ValueError("not json")
        return self._payload


class StubSession:
    """Stands in for the requests.Session an HttpN8nClient talks through."""

    def __init__(self, responses: list[StubResponse] | None = None, error: Exception | None = None) -> None:
        self.responses = responses or []
        self.error = error
        self.headers: dict[str, str] = {}
        self.calls: list[tuple] = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs.get("params"), kwargs.get("json")))
        if self.error is not None:
            raise self.error
        return self.responses.pop(0)


def build_client(session: StubSession) -> HttpN8nClient:
    client = HttpN8nClient(url="http://n8n:5678/api/v1", api_key="secret", project_id="project-1")
    client.session = session
    return client


def test_missing_configuration_fails_at_construction():
    """A missing install step should read as one, not as a 401 from a service nobody addressed."""
    with pytest.raises(N8nUnauthorized, match="invoke install.n8n"):
        HttpN8nClient(url="http://n8n:5678/api/v1", api_key="", project_id="project-1")
    with pytest.raises(N8nUnauthorized, match="project"):
        HttpN8nClient(url="http://n8n:5678/api/v1", api_key="secret", project_id="")


def test_api_key_header_and_url():
    client = HttpN8nClient(url="http://n8n:5678/api/v1/", api_key="secret", project_id="project-1")
    assert client.url == "http://n8n:5678/api/v1"
    assert client.session.headers["X-N8N-API-KEY"] == "secret"


def test_list_workflows_scopes_to_the_project():
    session = StubSession([StubResponse(payload={"data": [{"id": "wf1", "name": "One"}]})])
    workflows = build_client(session).list_workflows()

    method, url, params, _ = session.calls[0]
    assert (method, url) == ("GET", "http://n8n:5678/api/v1/workflows")
    assert params == {"projectId": "project-1", "limit": 250}
    assert [workflow.id for workflow in workflows] == ["wf1"]


def test_the_tag_filter_is_applied_here_not_by_n8n():
    """
    Regression. n8n drops its tags filter when a projectId is also given, and says nothing about it,
    so asking for one tag came back with the whole project. Tags are the only thing separating one
    workspace's workflows from another's, so that silently removed the boundary entirely.
    """
    session = StubSession([StubResponse(payload={"data": [
        {"id": "wf1", "name": "Ours", "tags": [{"id": "t1", "name": "business"}]},
        {"id": "wf2", "name": "Theirs", "tags": [{"id": "t2", "name": "someone-else"}]},
        {"id": "wf3", "name": "Untagged"},
    ]})])

    workflows = build_client(session).list_workflows(tag="business")

    assert [workflow.id for workflow in workflows] == ["wf1"]
    # The parameter is never sent, so n8n has no opportunity to widen the answer.
    assert "tags" not in session.calls[0][2]


def test_list_workflows_follows_the_cursor():
    session = StubSession([
        StubResponse(payload={"data": [{"id": "wf1", "name": "One"}], "nextCursor": "abc"}),
        StubResponse(payload={"data": [{"id": "wf2", "name": "Two"}], "nextCursor": None}),
    ])
    workflows = build_client(session).list_workflows()

    assert [workflow.id for workflow in workflows] == ["wf1", "wf2"]
    assert session.calls[1][2]["cursor"] == "abc"


def test_workflows_arrive_sanitized():
    """Nothing n8n owns should travel above this module, whatever the response carries."""
    payload = {
        "id": "wf1", "name": "One", "active": True, "versionId": "v1", "shared": [{"projectId": "p"}],
        "tags": [{"id": "tag1", "name": "business"}], "nodes": [], "connections": {},
    }
    session = StubSession([StubResponse(payload=payload)])
    workflow = build_client(session).get_workflow("wf1")

    assert workflow.id == "wf1"
    assert workflow.tag_names == {"business"}
    assert set(workflow.definition) == {"name", "nodes", "connections"}


def test_set_workflow_tags_sends_ids():
    session = StubSession([StubResponse(payload={"data": [{"id": "tag1", "name": "business"}]})])
    tags = build_client(session).set_workflow_tags("wf1", ["tag1"])

    _, url, _, body = session.calls[0]
    assert url.endswith("/workflows/wf1/tags")
    assert body == [{"id": "tag1"}]
    assert [tag.name for tag in tags] == ["business"]


def test_create_tag_accepts_201():
    session = StubSession([StubResponse(status_code=201, payload={"id": "tag1", "name": "business"})])
    assert build_client(session).create_tag("business").id == "tag1"


@pytest.mark.parametrize("status,expected", [
    (401, N8nUnauthorized),
    (403, N8nUnauthorized),
    (404, N8nNotFound),
    (409, N8nConflict),
    (400, N8nRejected),
    (500, N8nRejected),
])
def test_status_codes_become_typed_errors(status, expected):
    session = StubSession([StubResponse(status_code=status, payload={"message": "no"})])
    with pytest.raises(expected):
        build_client(session).list_tags()


def test_rejection_carries_the_message_from_n8n():
    """n8n names the field it disliked, which nothing written here could replace."""
    payload = {"message": "request/body must NOT have additional properties: active"}
    session = StubSession([StubResponse(status_code=400, payload=payload)])
    with pytest.raises(N8nRejected, match="additional properties: active"):
        build_client(session).create_workflow({"name": "One"})


def test_transport_failures_become_unavailable():
    session = StubSession(error=requests.ConnectionError("refused"))
    with pytest.raises(N8nUnavailable, match="Could not reach n8n at http://n8n:5678"):
        build_client(session).list_tags()


def test_non_json_answers_become_unavailable():
    session = StubSession([StubResponse(text="<html>", raw=True)])
    with pytest.raises(N8nUnavailable, match="not JSON"):
        build_client(session).list_tags()


def test_get_client_follows_the_setting(settings):
    settings.N8N_CLIENT = "workflows.n8n.FakeN8nClient"
    assert isinstance(get_n8n_client(), FakeN8nClient)


def test_fake_rejects_fields_the_real_schema_forbids():
    """The fake has to enforce the closed schema, or a leaked field passes here and fails live."""
    FAKE_N8N.reset()
    client = FakeN8nClient()
    with pytest.raises(N8nRejected, match="active"):
        client.create_workflow({"name": "One", "nodes": [], "connections": {}, "active": True})
    with pytest.raises(N8nRejected, match="projectId"):
        client.update_workflow("wf1", {"name": "One", "projectId": "p"})


def test_fake_conflicts_on_a_duplicate_tag():
    FAKE_N8N.reset()
    client = FakeN8nClient()
    client.create_tag("business")
    with pytest.raises(N8nConflict):
        client.create_tag("business")
