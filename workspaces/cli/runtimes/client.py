"""Thin client over the management runtime API. The CLI does no rendering of its own."""
from __future__ import annotations

import requests
from pydantic import BaseModel

from workspaces.cli.client import DEFAULT_MANAGEMENT_URL, ManagementClientError, control_headers


class RuntimeRecord(BaseModel):
    id: str
    workspace_module: str
    type: str
    name: str
    program_name: str
    configuration: dict
    port: int | None
    is_enabled: bool
    log_path: str


class ConfigFileRecord(BaseModel):
    path: str
    content: str


class ManifestRecord(BaseModel):
    """The complete desired configuration tree, which the reconciler writes and prunes against."""
    root: str
    # The workspaces this manifest speaks for. Pruning stays inside these.
    workspaces: list[str]
    files: list[ConfigFileRecord]


class SyncCommandsRecord(BaseModel):
    program_name: str
    directory: str
    commands: list[str]


class ProcessStatusRecord(BaseModel):
    name: str
    state: str
    description: str
    pid: int
    uptime_seconds: int


class ConfigUpdateRecord(BaseModel):
    added: list[str]
    changed: list[str]
    removed: list[str]


class LogRecord(BaseModel):
    program_name: str
    content: str


def _runtimes_url(path: str = "") -> str:
    return f"{DEFAULT_MANAGEMENT_URL.rstrip('/')}/api/v1/runtimes/{path}"


def _request(method: str, path: str, description: str, **kwargs):
    try:
        response = requests.request(method, _runtimes_url(path), headers=control_headers(), timeout=30, **kwargs)
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


def list_runtimes(workspace_module: str | None = None) -> list[RuntimeRecord]:
    params = {"workspace_module": workspace_module} if workspace_module else None
    response = _request("get", "", "list runtimes", params=params)
    return [RuntimeRecord.model_validate(record) for record in response.json()]


def get_runtime(runtime_id: str) -> RuntimeRecord:
    response = _request("get", f"{runtime_id}/", f"fetch runtime '{runtime_id}'")
    return RuntimeRecord.model_validate(response.json())


def create_runtime(workspace_module: str, runtime_type: str, name: str,
                   configuration: dict | None = None, port: int | None = None) -> RuntimeRecord:
    payload = {
        "workspace_module": workspace_module,
        "type": runtime_type,
        "name": name,
        "configuration": configuration or {},
    }
    if port is not None:
        payload["port"] = port
    response = _request("post", "", f"add runtime '{name}' to workspace '{workspace_module}'", json=payload)
    return RuntimeRecord.model_validate(response.json())


def patch_runtime(runtime_id: str, configuration: dict | None = None, port: int | None = None) -> RuntimeRecord:
    payload: dict[str, object] = {}
    if configuration is not None:
        payload["configuration"] = configuration
    if port is not None:
        payload["port"] = port
    response = _request("patch", f"{runtime_id}/", f"update runtime '{runtime_id}'", json=payload)
    return RuntimeRecord.model_validate(response.json())


def delete_runtime(runtime_id: str) -> None:
    _request("delete", f"{runtime_id}/", f"delete runtime '{runtime_id}'")


def set_runtime_enabled(runtime_id: str, enabled: bool) -> RuntimeRecord:
    action = "enable" if enabled else "disable"
    response = _request("post", f"{runtime_id}/{action}/", f"{action} runtime '{runtime_id}'")
    return RuntimeRecord.model_validate(response.json())


def get_manifest(workspace_module: str | None = None) -> ManifestRecord:
    params = {"workspace_module": workspace_module} if workspace_module else None
    response = _request("get", "configs/", "fetch the runtime configuration manifest", params=params)
    return ManifestRecord.model_validate(response.json())


def get_sync_commands(runtime_id: str) -> SyncCommandsRecord:
    response = _request("get", f"{runtime_id}/sync-commands/", f"fetch sync commands for '{runtime_id}'")
    return SyncCommandsRecord.model_validate(response.json())


def reload_runtimes() -> ConfigUpdateRecord:
    response = _request("post", "reload/", "reload supervisord and nginx")
    return ConfigUpdateRecord.model_validate(response.json())


def control_runtime(runtime_id: str, action: str) -> ProcessStatusRecord:
    response = _request("post", f"{runtime_id}/{action}/", f"{action} runtime '{runtime_id}'")
    return ProcessStatusRecord.model_validate(response.json())


def get_runtime_status(runtime_id: str) -> ProcessStatusRecord:
    response = _request("get", f"{runtime_id}/status/", f"read the status of runtime '{runtime_id}'")
    return ProcessStatusRecord.model_validate(response.json())


def get_runtime_logs(runtime_id: str, offset: int = 0, length: int = 0) -> LogRecord:
    response = _request(
        "get", f"{runtime_id}/logs/", f"read the log of runtime '{runtime_id}'",
        params={"offset": offset, "length": length},
    )
    return LogRecord.model_validate(response.json())
