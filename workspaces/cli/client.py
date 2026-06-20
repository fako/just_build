import os

import requests
from pydantic import BaseModel


DEFAULT_MANAGEMENT_URL = os.environ.get("WORKSPACES_MANAGEMENT_URL", "http://127.0.0.1:8000")


class ManagementClientError(RuntimeError):
    pass


class WorkspaceRecord(BaseModel):
    id: str
    name: str
    module: str
    slug: str
    django_module: str
    setup: dict[str, str]

    class SSHConfig(BaseModel):
        alias: str | None = None
        user: str | None = None
        host: str | None = None
        port: int | None = None
        identity_file: str | None = None

    ssh: SSHConfig


def _workspace_url(workspace_module: str) -> str:
    return f"{DEFAULT_MANAGEMENT_URL.rstrip('/')}/api/v1/workspaces/{workspace_module}/"


def create_workspace(name: str, module: str, django_module: str = "web") -> WorkspaceRecord:
    try:
        response = requests.post(
            f"{DEFAULT_MANAGEMENT_URL.rstrip('/')}/api/v1/workspaces/",
            json={"name": name, "module": module, "django_module": django_module},
            timeout=10,
        )
    except requests.RequestException as exc:
        raise ManagementClientError(f"Could not create workspace '{module}': {exc}") from exc

    if response.status_code == 409:
        raise ManagementClientError(f"Workspace '{module}' already exists.")

    try:
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ManagementClientError(f"Could not create workspace '{module}': {exc}") from exc

    return WorkspaceRecord.model_validate(response.json())


def get_workspace(workspace_module: str) -> WorkspaceRecord:
    try:
        response = requests.get(_workspace_url(workspace_module), timeout=10)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ManagementClientError(f"Could not fetch workspace '{workspace_module}': {exc}") from exc
    return WorkspaceRecord.model_validate(response.json())


def patch_workspace(workspace_module: str, *, setup: dict[str, str] | None = None,
                    ssh: dict[str, str | int] | None = None) -> WorkspaceRecord:
    payload: dict[str, object] = {}
    if setup:
        payload["setup"] = setup
    if ssh:
        payload["ssh"] = ssh

    try:
        response = requests.patch(_workspace_url(workspace_module), json=payload, timeout=10)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ManagementClientError(f"Could not update workspace '{workspace_module}': {exc}") from exc
    return WorkspaceRecord.model_validate(response.json())


def get_ssh_config() -> str:
    url = f"{DEFAULT_MANAGEMENT_URL.rstrip('/')}/api/v1/workspaces/ssh-config/"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ManagementClientError(f"Could not fetch generated SSH config: {exc}") from exc
    return response.text
