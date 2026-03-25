import os

import requests
from pydantic import BaseModel


DEFAULT_MANAGEMENT_URL = os.environ.get("WORKSPACES_MANAGEMENT_URL", "http://127.0.0.1:8000")


class ManagementClientError(RuntimeError):
    pass


class ProjectRecord(BaseModel):
    id: str
    name: str
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


def _project_url(project_slug: str) -> str:
    return f"{DEFAULT_MANAGEMENT_URL.rstrip('/')}/api/v1/projects/{project_slug}/"


def create_project(name: str, slug: str, django_module: str = "web") -> ProjectRecord:
    try:
        response = requests.post(
            f"{DEFAULT_MANAGEMENT_URL.rstrip('/')}/api/v1/projects/",
            json={"name": name, "slug": slug, "django_module": django_module},
            timeout=10,
        )
    except requests.RequestException as exc:
        raise ManagementClientError(f"Could not create project '{slug}': {exc}") from exc

    if response.status_code == 409:
        raise ManagementClientError(f"Project '{slug}' already exists.")

    try:
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ManagementClientError(f"Could not create project '{slug}': {exc}") from exc

    return ProjectRecord.model_validate(response.json())


def get_project(project_slug: str) -> ProjectRecord:
    try:
        response = requests.get(_project_url(project_slug), timeout=10)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ManagementClientError(f"Could not fetch project '{project_slug}': {exc}") from exc
    return ProjectRecord.model_validate(response.json())


def patch_project(project_slug: str, *, setup: dict[str, str] | None = None,
                  ssh: dict[str, str | int] | None = None) -> ProjectRecord:
    payload: dict[str, object] = {}
    if setup:
        payload["setup"] = setup
    if ssh:
        payload["ssh"] = ssh

    try:
        response = requests.patch(_project_url(project_slug), json=payload, timeout=10)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ManagementClientError(f"Could not update project '{project_slug}': {exc}") from exc
    return ProjectRecord.model_validate(response.json())


def get_ssh_config() -> str:
    url = f"{DEFAULT_MANAGEMENT_URL.rstrip('/')}/api/v1/projects/ssh-config/"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ManagementClientError(f"Could not fetch generated SSH config: {exc}") from exc
    return response.text
