"""Thin client over the management workflow API, for the housekeeping the host does."""
from __future__ import annotations

from datetime import datetime

import requests
from pydantic import BaseModel

from workspaces.cli.client import DEFAULT_MANAGEMENT_URL, ManagementClientError, control_headers


class WorkflowRecord(BaseModel):
    id: str
    workspace_module: str
    name: str
    slug: str
    n8n_id: str
    definition: dict
    tags: list[str]
    pulled_at: datetime | None = None


class WorkflowTagRecord(BaseModel):
    id: str
    workspace_module: str
    name: str
    n8n_id: str


class PushResultRecord(BaseModel):
    name: str
    n8n_id: str
    action: str
    tags: list[str]


def _workflows_url(path: str = "") -> str:
    return f"{DEFAULT_MANAGEMENT_URL.rstrip('/')}/api/v1/workflows/{path}"


def _request(method: str, path: str, description: str, **kwargs):
    try:
        response = requests.request(
            method, _workflows_url(path), headers=control_headers(), timeout=60, **kwargs,
        )
    except requests.RequestException as exc:
        raise ManagementClientError(f"Could not {description}: {exc}") from exc

    if not response.ok:
        detail = response.text
        try:
            detail = response.json().get("detail", detail)
        except ValueError:
            pass
        raise ManagementClientError(f"Could not {description}: {response.status_code} {detail}")

    return response


def list_workflows(workspace_module: str | None = None, tag: str | None = None) -> list[WorkflowRecord]:
    params = {key: value for key, value in {"workspace_module": workspace_module, "tag": tag}.items() if value}
    response = _request("get", "", "list workflows", params=params or None)
    return [WorkflowRecord.model_validate(record) for record in response.json()]


def list_tags(workspace_module: str | None = None) -> list[WorkflowTagRecord]:
    params = {"workspace_module": workspace_module} if workspace_module else None
    response = _request("get", "tags/", "list workflow tags", params=params)
    return [WorkflowTagRecord.model_validate(record) for record in response.json()]


def pull_workflows(workspace_module: str, prune: bool = False) -> list[WorkflowRecord]:
    response = _request(
        "post", "pull/", f"pull workflows for workspace '{workspace_module}'",
        json={"workspace_module": workspace_module, "prune": prune},
    )
    return [WorkflowRecord.model_validate(record) for record in response.json()]


def repush_workflows(workspace_module: str | None = None) -> dict[str, list[PushResultRecord]]:
    payload = {"workspace_module": workspace_module} if workspace_module else {}
    described = f"workspace '{workspace_module}'" if workspace_module else "every workspace"
    response = _request("post", "repush/", f"repush workflows for {described}", json=payload)
    return {
        module: [PushResultRecord.model_validate(record) for record in records]
        for module, records in response.json().items()
    }
