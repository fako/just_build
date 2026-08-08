import os

import requests
from pydantic import BaseModel


DEFAULT_MANAGEMENT_URL = os.environ.get("WORKSPACES_MANAGEMENT_URL", "http://127.0.0.1:8000")
CONTROL_API_KEY = os.environ.get("INVOKE_MANAGEMENT_SECURITY_API_KEY", "")


class ManagementClientError(RuntimeError):
    pass


def control_headers() -> dict[str, str]:
    """Authenticate as the control principal, which every CLI call does."""
    if not CONTROL_API_KEY:
        raise ManagementClientError(
            "No control API key available. Set INVOKE_MANAGEMENT_SECURITY_API_KEY in .env "
            "and run 'source activate.sh'."
        )
    return {"Authorization": f"Bearer {CONTROL_API_KEY}"}


class WorkspaceRecord(BaseModel):
    id: str
    name: str
    module: str
    slug: str
    django_module: str
    setup: dict[str, str]
    git_public_key: str = ""
    # Only present on the response that creates the workspace.
    api_key: str | None = None

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
            headers=control_headers(),
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
        response = requests.get(_workspace_url(workspace_module), headers=control_headers(), timeout=10)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ManagementClientError(f"Could not fetch workspace '{workspace_module}': {exc}") from exc
    return WorkspaceRecord.model_validate(response.json())


def patch_workspace(workspace_module: str, *, setup: dict[str, str] | None = None,
                    ssh: dict[str, str | int] | None = None,
                    git_public_key: str | None = None) -> WorkspaceRecord:
    payload: dict[str, object] = {}
    if setup:
        payload["setup"] = setup
    if ssh:
        payload["ssh"] = ssh
    if git_public_key is not None:
        payload["git_public_key"] = git_public_key

    try:
        response = requests.patch(
            _workspace_url(workspace_module), json=payload, headers=control_headers(), timeout=10,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ManagementClientError(f"Could not update workspace '{workspace_module}': {exc}") from exc
    return WorkspaceRecord.model_validate(response.json())


def delete_workspace(workspace_module: str) -> bool:
    """Delete a management workspace, returning false when it was already absent."""
    try:
        response = requests.delete(_workspace_url(workspace_module), headers=control_headers(), timeout=10)
    except requests.RequestException as exc:
        raise ManagementClientError(f"Could not delete workspace '{workspace_module}': {exc}") from exc

    if response.status_code == 404:
        return False

    try:
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ManagementClientError(f"Could not delete workspace '{workspace_module}': {exc}") from exc
    return True


def get_ssh_config(repository_root: str) -> str:
    url = f"{DEFAULT_MANAGEMENT_URL.rstrip('/')}/api/v1/workspaces/ssh-config/"
    try:
        response = requests.get(
            url, params={"repository_root": repository_root}, headers=control_headers(), timeout=10,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ManagementClientError(f"Could not fetch generated SSH config: {exc}") from exc
    return response.text
