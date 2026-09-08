"""
Thin client over the management workflow API, authenticated as this workspace.

The workspace never talks to n8n directly. Management holds the n8n API key and the tag registry, and
it is the registry that stops one workspace writing over another's workflows, so going around it
would be going around the only thing keeping this workspace's work its own.
"""
from __future__ import annotations

import os
from pathlib import Path

import requests
from dotenv import load_dotenv


# Also loaded here rather than relying on the shell: a task run over a non-login SSH exec never
# sources the profile that exports these, and would otherwise fail with an empty key.
load_dotenv(Path("/workspaces/secrets/{{ workspace.module }}/.env"))

MANAGEMENT_URL = os.environ.get("MANAGEMENT_URL", "http://management:8000")
WORKSPACE_API_KEY = os.environ.get("WORKSPACE_API_KEY", "")

TIMEOUT = 60


class ManagementError(RuntimeError):
    pass


def headers() -> dict[str, str]:
    if not WORKSPACE_API_KEY:
        raise ManagementError(
            "No WORKSPACE_API_KEY available. It lives in "
            "/workspaces/secrets/{{ workspace.module }}/.env, which your shell profile sources."
        )
    return {"Authorization": f"Bearer {WORKSPACE_API_KEY}"}


def request(method: str, path: str, description: str, **kwargs) -> dict | list:
    url = f"{MANAGEMENT_URL.rstrip('/')}/api/v1/workflows/{path}"
    try:
        response = requests.request(method, url, headers=headers(), timeout=TIMEOUT, **kwargs)
    except requests.RequestException as exc:
        raise ManagementError(f"Could not {description}: {exc}") from exc

    if not response.ok:
        detail = response.text
        try:
            detail = response.json().get("detail", detail)
        except ValueError:
            pass
        raise ManagementError(f"Could not {description}: {response.status_code} {detail}")

    return response.json()


def list_workflows() -> list[dict]:
    return request("get", "", "list workflows")


def list_tags() -> list[dict]:
    return request("get", "tags/", "list tags")


def add_tag(name: str) -> dict:
    return request("post", "tags/", f"claim tag '{name}'", json={"name": name})


def push(workflows: list[dict]) -> list[dict]:
    return request("post", "push/", "push workflows", json={"workflows": workflows})


def pull() -> list[dict]:
    return request("post", "pull/", "pull workflows", json={})


def sync(workflows: list[dict]) -> dict:
    return request("post", "sync/", "sync workflows", json={"workflows": workflows})
