"""
The n8n public REST API, as a narrow client with a fake beside it.

Management runs in its own container with no docker socket, so `docker compose exec n8n n8n
import:workflow` is not available to it and would not be reached for if it were. n8n publishes an
HTTP API on the compose network, which is the same shape of channel supervisord offers the runtimes
app: no privileged mounts, no shelling out, and something a test can replace with an object.

Two things about the API are worth knowing before changing anything here.

The tags filter is an intersection. `GET /workflows?tags=a,b` returns workflows carrying a *and* b,
not either, which is why list_workflows takes one tag rather than a list: a caller that wanted "any
of these" and wrote a comma separated string would get an empty result and no error to explain it.

Write schemas are closed. Both create and update are declared `additionalProperties: false`, and the
two do not accept the same fields, so bodies are assembled by allowlist in definitions.py rather than
by stripping keys off a pulled workflow.

Absent from the protocol by design: anything that activates, publishes or unpublishes, because
workflow state belongs to n8n and must not gain a second home in management, and anything touching
credentials or executions, because neither is in scope. Breaking either rule means widening this
interface first, which is a visible act rather than an accident.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import requests
from django.conf import settings
from django.utils.module_loading import import_string

from workflows.definitions import CREATE_FIELDS, UPDATE_FIELDS, sanitize_definition


PAGE_SIZE = 250
REQUEST_TIMEOUT = 30


class N8nError(RuntimeError):
    pass


class N8nUnavailable(N8nError):
    """n8n could not be reached at all, or answered something that is not a response."""


class N8nUnauthorized(N8nError):
    """No usable API key, or n8n refused the one it was given."""


class N8nNotFound(N8nError):
    pass


class N8nConflict(N8nError):
    """n8n already holds something with this identity, most often a tag name."""


class N8nRejected(N8nError):
    """n8n refused the request body. Carries n8n's own message, which is the useful part."""


@dataclass(frozen=True, slots=True)
class N8nTag:
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class N8nWorkflow:
    id: str
    name: str
    # Already sanitized, so nothing n8n owns is ever carried around above this module.
    definition: dict[str, Any] = field(default_factory=dict)
    tags: tuple[N8nTag, ...] = ()
    is_archived: bool = False
    updated_at: str = ""

    @property
    def tag_names(self) -> set[str]:
        return {tag.name for tag in self.tags}


class N8nClient(Protocol):

    # The one project every workspace writes into. Part of the contract because create bodies carry
    # it, and because a client pointed at a different project is a different world.
    project_id: str

    def list_tags(self) -> list[N8nTag]: ...

    def create_tag(self, name: str) -> N8nTag: ...

    def list_workflows(self, tag: str | None = None) -> list[N8nWorkflow]: ...

    def get_workflow(self, workflow_id: str) -> N8nWorkflow: ...

    def create_workflow(self, body: dict[str, Any]) -> N8nWorkflow: ...

    def update_workflow(self, workflow_id: str, body: dict[str, Any]) -> N8nWorkflow: ...

    def delete_workflow(self, workflow_id: str) -> None: ...

    def set_workflow_tags(self, workflow_id: str, tag_ids: list[str]) -> list[N8nTag]: ...


def build_tag(payload: dict[str, Any]) -> N8nTag:
    return N8nTag(id=str(payload.get("id", "")), name=str(payload.get("name", "")))


def build_workflow(payload: dict[str, Any]) -> N8nWorkflow:
    return N8nWorkflow(
        id=str(payload.get("id", "")),
        name=str(payload.get("name", "")),
        definition=sanitize_definition(payload),
        tags=tuple(build_tag(tag) for tag in payload.get("tags") or []),
        is_archived=bool(payload.get("isArchived", False)),
        updated_at=str(payload.get("updatedAt", "")),
    )


class HttpN8nClient:
    """Talks to the n8n public API over the compose network."""

    def __init__(self, url: str | None = None, api_key: str | None = None,
                 project_id: str | None = None) -> None:
        self.url = (url if url is not None else settings.N8N_API_URL).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.N8N_API_KEY
        self.project_id = project_id if project_id is not None else settings.N8N_PROJECT_ID
        # Checked here rather than on the first call, so a missing install step reads as a missing
        # install step instead of a 401 relayed from a service the caller never addressed.
        if not self.api_key:
            raise N8nUnauthorized(
                "No n8n API key is configured. Run 'invoke install.n8n' and follow the instructions "
                "it prints, then restart management."
            )
        if not self.project_id:
            raise N8nUnauthorized(
                "No n8n project is configured. Run 'invoke install.n8n' and follow the instructions "
                "it prints, then restart management."
            )
        self.session = requests.Session()
        self.session.headers.update({"X-N8N-API-KEY": self.api_key, "Accept": "application/json"})

    def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        url = f"{self.url}{path}"
        try:
            response = self.session.request(method, url, timeout=REQUEST_TIMEOUT, **kwargs)
        except requests.RequestException as exc:
            raise N8nUnavailable(
                f"Could not reach n8n at {url}: {exc}. Check that it is running in the current "
                "compose profile."
            ) from exc

        if response.status_code in (401, 403):
            raise N8nUnauthorized(f"n8n refused the API key with HTTP {response.status_code}.")
        if response.status_code == 404:
            raise N8nNotFound(f"n8n has nothing at {path}.")
        if response.status_code == 409:
            raise N8nConflict(self._message(response, "n8n reports a conflict"))
        if response.status_code >= 400:
            raise N8nRejected(self._message(response, f"n8n refused the request with HTTP {response.status_code}"))

        if not response.content:
            return {}
        try:
            payload = response.json()
        except ValueError as exc:
            raise N8nUnavailable(f"n8n answered {url} with something that is not JSON.") from exc
        return payload if isinstance(payload, dict) else {"data": payload}

    def _message(self, response: requests.Response, fallback: str) -> str:
        """n8n's own message says which field it disliked, which no wording here could replace."""
        try:
            payload = response.json()
        except ValueError:
            return f"{fallback}: {response.text[:200]}"
        message = payload.get("message") if isinstance(payload, dict) else None
        return f"{fallback}: {message}" if message else fallback

    def _paginate(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Follow nextCursor to the end. n8n caps a page at 250 whatever limit asks for."""
        results: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            page_params = {**params, "limit": PAGE_SIZE}
            if cursor:
                page_params["cursor"] = cursor
            payload = self._request("GET", path, params=page_params)
            results.extend(payload.get("data") or [])
            cursor = payload.get("nextCursor")
            if not cursor:
                return results

    def list_tags(self) -> list[N8nTag]:
        return [build_tag(payload) for payload in self._paginate("/tags", {})]

    def create_tag(self, name: str) -> N8nTag:
        return build_tag(self._request("POST", "/tags", json={"name": name}))

    def list_workflows(self, tag: str | None = None) -> list[N8nWorkflow]:
        """
        Every workflow in the configured project, or only those carrying one tag.

        The tag is applied here rather than by n8n, which cannot be trusted to do it. Sending
        `?projectId=…&tags=…` returns the whole project: n8n keeps the project filter and drops the
        tag one, with no error to say so. Since tags are the only thing separating one workspace from
        another, a filter that silently widens is the difference between isolation and none at all,
        so it is not asked for.

        Filtering here also sidesteps the other trap in that parameter, which is that n8n intersects
        several tags rather than uniting them.
        """
        workflows = [
            build_workflow(payload)
            for payload in self._paginate("/workflows", {"projectId": self.project_id})
        ]
        if not tag:
            return workflows
        return [workflow for workflow in workflows if tag in workflow.tag_names]

    def get_workflow(self, workflow_id: str) -> N8nWorkflow:
        return build_workflow(self._request("GET", f"/workflows/{workflow_id}"))

    def create_workflow(self, body: dict[str, Any]) -> N8nWorkflow:
        return build_workflow(self._request("POST", "/workflows", json=body))

    def update_workflow(self, workflow_id: str, body: dict[str, Any]) -> N8nWorkflow:
        return build_workflow(self._request("PUT", f"/workflows/{workflow_id}", json=body))

    def delete_workflow(self, workflow_id: str) -> None:
        self._request("DELETE", f"/workflows/{workflow_id}")

    def set_workflow_tags(self, workflow_id: str, tag_ids: list[str]) -> list[N8nTag]:
        """
        Replace a workflow's tags outright, which is what n8n does with this call.

        Needed after every write: neither create nor update accepts tags in its body, so a workflow
        whose tags changed in a file would otherwise keep the ones it already had.
        """
        payload = self._request(
            "PUT", f"/workflows/{workflow_id}/tags", json=[{"id": tag_id} for tag_id in tag_ids],
        )
        return [build_tag(tag) for tag in payload.get("data") or []]


class FakeN8nState:
    """Shared in-memory state, so a fake built by the factory is the one a test inspects."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.tags: dict[str, N8nTag] = {}
        self.workflows: dict[str, N8nWorkflow] = {}
        self.workflow_tags: dict[str, list[str]] = {}
        self.calls: list[tuple[str, ...]] = []
        self.failures: dict[str, N8nError] = {}
        self._sequence = 0

    def next_id(self, prefix: str) -> str:
        self._sequence += 1
        return f"{prefix}{self._sequence:04d}"

    def add_tag(self, name: str, tag_id: str | None = None) -> N8nTag:
        tag = N8nTag(id=tag_id or self.next_id("tag"), name=name)
        self.tags[tag.id] = tag
        return tag

    def add_workflow(self, name: str, tags: list[str] | None = None, workflow_id: str | None = None,
                     definition: dict[str, Any] | None = None, is_archived: bool = False) -> N8nWorkflow:
        identifier = workflow_id or self.next_id("wf")
        tag_ids = [self.tag_by_name(tag).id for tag in tags or []]
        self.workflow_tags[identifier] = tag_ids
        workflow = N8nWorkflow(
            id=identifier,
            name=name,
            definition=definition or {"name": name, "nodes": [], "connections": {}, "settings": {}},
            tags=tuple(self.tags[tag_id] for tag_id in tag_ids),
            is_archived=is_archived,
        )
        self.workflows[identifier] = workflow
        return workflow

    def tag_by_name(self, name: str) -> N8nTag:
        for tag in self.tags.values():
            if tag.name == name:
                return tag
        return self.add_tag(name)

    def fail(self, method: str, error: N8nError) -> None:
        """Make one client method raise, for the paths that only happen when n8n misbehaves."""
        self.failures[method] = error


FAKE_N8N = FakeN8nState()


class FakeN8nClient:
    """
    Test double. Selected by pointing the N8N_CLIENT setting at this class.

    It enforces the two rules that actually bite in production: a write body may only carry the keys
    n8n's closed schema accepts, and a duplicate tag name is a conflict. Without those a leaked `id`
    or `active` would sail through every test here and fail on the first real push.
    """

    def __init__(self, state: FakeN8nState | None = None) -> None:
        self.state = state or FAKE_N8N
        self.project_id = "project-test"

    def _check(self, method: str) -> None:
        error = self.state.failures.get(method)
        if error is not None:
            raise error

    def _assert_allowed(self, body: dict[str, Any], allowed: tuple[str, ...]) -> None:
        rejected = sorted(set(body) - set(allowed))
        if rejected:
            raise N8nRejected(
                f"n8n refused the request with HTTP 400: request/body must NOT have additional "
                f"properties: {', '.join(rejected)}"
            )

    def list_tags(self) -> list[N8nTag]:
        self._check("list_tags")
        self.state.calls.append(("list_tags",))
        return list(self.state.tags.values())

    def create_tag(self, name: str) -> N8nTag:
        self._check("create_tag")
        self.state.calls.append(("create_tag", name))
        if any(tag.name == name for tag in self.state.tags.values()):
            raise N8nConflict(f"n8n reports a conflict: a tag named '{name}' already exists")
        return self.state.add_tag(name)

    def list_workflows(self, tag: str | None = None) -> list[N8nWorkflow]:
        self._check("list_workflows")
        self.state.calls.append(("list_workflows", tag or ""))
        if tag is None:
            return list(self.state.workflows.values())
        return [workflow for workflow in self.state.workflows.values() if tag in workflow.tag_names]

    def get_workflow(self, workflow_id: str) -> N8nWorkflow:
        self._check("get_workflow")
        self.state.calls.append(("get_workflow", workflow_id))
        if workflow_id not in self.state.workflows:
            raise N8nNotFound(f"n8n has nothing at /workflows/{workflow_id}.")
        return self.state.workflows[workflow_id]

    def create_workflow(self, body: dict[str, Any]) -> N8nWorkflow:
        self._check("create_workflow")
        self._assert_allowed(body, CREATE_FIELDS)
        self.state.calls.append(("create_workflow", body.get("name", "")))
        identifier = self.state.next_id("wf")
        workflow = N8nWorkflow(
            id=identifier,
            name=body.get("name", ""),
            definition=sanitize_definition(body),
        )
        self.state.workflows[identifier] = workflow
        self.state.workflow_tags[identifier] = []
        return workflow

    def update_workflow(self, workflow_id: str, body: dict[str, Any]) -> N8nWorkflow:
        self._check("update_workflow")
        self._assert_allowed(body, UPDATE_FIELDS)
        self.state.calls.append(("update_workflow", workflow_id))
        existing = self.get_workflow(workflow_id)
        workflow = N8nWorkflow(
            id=workflow_id,
            name=body.get("name", existing.name),
            definition=sanitize_definition(body),
            tags=existing.tags,
            is_archived=existing.is_archived,
        )
        self.state.workflows[workflow_id] = workflow
        return workflow

    def delete_workflow(self, workflow_id: str) -> None:
        self._check("delete_workflow")
        self.state.calls.append(("delete_workflow", workflow_id))
        self.get_workflow(workflow_id)
        del self.state.workflows[workflow_id]
        self.state.workflow_tags.pop(workflow_id, None)

    def set_workflow_tags(self, workflow_id: str, tag_ids: list[str]) -> list[N8nTag]:
        self._check("set_workflow_tags")
        self.state.calls.append(("set_workflow_tags", workflow_id, ",".join(tag_ids)))
        workflow = self.get_workflow(workflow_id)
        tags = tuple(self.state.tags[tag_id] for tag_id in tag_ids if tag_id in self.state.tags)
        self.state.workflow_tags[workflow_id] = list(tag_ids)
        self.state.workflows[workflow_id] = N8nWorkflow(
            id=workflow.id,
            name=workflow.name,
            definition=workflow.definition,
            tags=tags,
            is_archived=workflow.is_archived,
        )
        return list(tags)


def get_n8n_client() -> N8nClient:
    """Build the client the settings select. Tests point N8N_CLIENT at the fake."""
    return import_string(settings.N8N_CLIENT)()
