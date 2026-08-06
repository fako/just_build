"""
Adoption for workspaces created before runtimes existed.

Those workspaces have a flat `workspaces/src/supervisor/<module>.conf` naming its supervisord
program after the workspace itself, with its log at `/var/log/projects/<module>.log`. Adoption reads
the port out of that file one last time, creates the Django runtime that describes it, and lets the
reconciler replace the flat file with the nested one.
"""
from __future__ import annotations

from pathlib import Path

from invoke.context import Context
from invoke.tasks import task

from workspaces.cli.client import ManagementClientError, get_workspace
from workspaces.cli.constants import NGINX_DIR, SUPERVISOR_DIR
from workspaces.cli.runtimes import client
from workspaces.cli.runtimes.common import apply_configs


DEFAULT_RUNTIME_NAME = "web"


def legacy_supervisor_config(workspace_module: str) -> Path:
    return SUPERVISOR_DIR / f"{workspace_module}.conf"


def legacy_nginx_config(workspace_module: str) -> Path:
    return NGINX_DIR / f"{workspace_module}.conf"


def read_legacy_port(config_path: Path) -> int | None:
    """The last time anything scrapes a port out of a generated file."""
    if not config_path.exists():
        return None

    for line in config_path.read_text().splitlines():
        if "--port " not in line:
            continue
        fragment = line.split("--port ", 1)[1].split()[0]
        if fragment.isdigit():
            return int(fragment)
    return None


def read_legacy_domain(config_path: Path) -> str | None:
    if not config_path.exists():
        return None

    for line in config_path.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("server_name ") and not stripped.startswith("server_name _"):
            return stripped.removeprefix("server_name ").rstrip(";")
    return None


@task(
    help={
        "workspace_module": "Workspace to adopt",
        "name": "Name for the adopted runtime, defaults to web",
        "port": "Override the port instead of reading it from the old config",
    },
)
def adopt(ctx: Context, workspace_module: str, name: str = DEFAULT_RUNTIME_NAME, port: int | None = None):
    """Create a Django runtime for a workspace that predates runtimes, keeping its port and domain."""
    workspace = get_workspace(workspace_module)

    for runtime in client.list_runtimes(workspace_module):
        if runtime.name == name:
            print(f"Workspace '{workspace_module}' already has a runtime called '{name}'. Nothing to adopt.")
            return

    supervisor_path = legacy_supervisor_config(workspace_module)
    resolved_port = port or read_legacy_port(supervisor_path)
    if resolved_port is None:
        print(f"No port found in {supervisor_path}, letting management allocate one.")

    domain = read_legacy_domain(legacy_nginx_config(workspace_module))
    configuration = {"domain": domain} if domain else {}

    try:
        runtime = client.create_runtime(
            workspace_module, "django", name, configuration=configuration, port=resolved_port,
        )
    except ManagementClientError as exc:
        raise RuntimeError(f"Could not adopt workspace '{workspace_module}': {exc}") from exc

    client.set_runtime_enabled(runtime.id, True)
    # The flat config belongs to this workspace, so the reconciler prunes it as it writes the nested
    # one. Adoption is what makes management authoritative for the workspace in the first place.
    apply_configs(ctx)

    print("")
    print(f"Adopted {workspace.name} ({workspace_module}) as runtime '{runtime.name}'.")
    print(f"Supervisord program: {runtime.program_name} (was {workspace_module})")
    print(f"Port: {runtime.port}")
    print(f"Log file: {runtime.log_path} (was /var/log/projects/{workspace_module}.log)")
