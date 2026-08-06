"""
The runtimes namespace: the day-to-day commands.

These are thin clients over the management API. The only work they do themselves is what management
cannot: writing config files onto the host, and running commands inside a workspace over SSH.
"""
from __future__ import annotations

import json

from invoke.context import Context
from invoke.tasks import task

from workspaces.cli.client import get_workspace
from workspaces.cli.common import build_ssh_connection, ensure_workspace_log_dir, ensure_workspaces_container
from workspaces.cli.runtimes import client
from workspaces.cli.runtimes.common import apply_configs, print_runtimes, resolve_runtime


@task(
    name="list",
    help={"workspace_module": "Limit the listing to one workspace"},
)
def list_runtimes(ctx: Context, workspace_module: str | None = None):
    """List runtimes and whether they are enabled."""
    print_runtimes(client.list_runtimes(workspace_module))


@task(
    help={
        "workspace_module": "Workspace that will own the runtime",
        "type": "Runtime type, for example django or celery",
        "name": "Runtime name, unique within the workspace, for example web or worker",
        "port": "Fixed port for HTTP runtimes. Left out, management allocates the next free one.",
        "configuration": 'Type specific configuration as JSON, for example \'{"concurrency": 4}\'',
    },
)
def add(ctx: Context, workspace_module: str, type: str, name: str, port: int | None = None,  # noqa: A002
        configuration: str | None = None):
    """Add a runtime to a workspace. Enable it to put it on disk."""
    parsed = json.loads(configuration) if configuration else None
    runtime = client.create_runtime(workspace_module, type, name, configuration=parsed, port=port)

    print("")
    print(f"Added {runtime.type} runtime '{runtime.name}' to workspace {workspace_module}.")
    print(f"Supervisord program: {runtime.program_name}")
    if runtime.port:
        print(f"Port: {runtime.port}")
    print(f"Log file: {runtime.log_path}")
    print("Next step:")
    print(f"  invoke runtimes.enable --workspace-module={workspace_module} --name={runtime.name}")


@task(help={
    "workspace_module": "Workspace that owns the runtime",
    "name": "Runtime name",
    "configuration": 'Type specific configuration as JSON, for example \'{"concurrency": 4}\'',
    "port": "Change the port of an HTTP runtime",
})
def configure(ctx: Context, workspace_module: str, name: str, configuration: str | None = None,
              port: int | None = None):
    """Replace a runtime's configuration. Apply afterwards to put the change on disk."""
    runtime = resolve_runtime(workspace_module, name)

    parsed: dict | None = None
    if configuration is not None:
        try:
            parsed = json.loads(configuration)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Configuration is not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("Configuration must be a JSON object.")

    updated = client.patch_runtime(runtime.id, configuration=parsed, port=port)
    if updated.is_enabled:
        apply_configs(ctx)

    print("")
    print(f"Configured {updated.program_name}: {json.dumps(updated.configuration)}")


@task(help={"workspace_module": "Workspace that owns the runtime", "name": "Runtime name"})
def enable(ctx: Context, workspace_module: str, name: str):
    """Enable a runtime, write its config, and start it under supervisord."""
    runtime = resolve_runtime(workspace_module, name)
    ensure_workspaces_container(ctx)
    ensure_workspace_log_dir(ctx, workspace_module)
    client.set_runtime_enabled(runtime.id, True)
    apply_configs(ctx)

    print("")
    print(f"Enabled {runtime.program_name}.")


@task(help={"workspace_module": "Workspace that owns the runtime", "name": "Runtime name"})
def disable(ctx: Context, workspace_module: str, name: str):
    """Disable a runtime, remove its config, and stop it."""
    runtime = resolve_runtime(workspace_module, name)
    client.set_runtime_enabled(runtime.id, False)
    # The runtime drops out of the manifest, so reconciling deletes its files.
    apply_configs(ctx)

    print("")
    print(f"Disabled {runtime.program_name}.")


@task(help={"workspace_module": "Limit the reconcile to one workspace"})
def apply(ctx: Context, workspace_module: str | None = None):
    """Rewrite every enabled runtime's config from management and reload. Idempotent."""
    ensure_workspaces_container(ctx)
    written, removed = apply_configs(ctx, workspace_module)

    print("")
    if not written and not removed:
        print("Configuration was already up to date.")
    else:
        print(f"Applied configuration: {len(written)} written, {len(removed)} removed.")


@task(help={"workspace_module": "Workspace that owns the runtime", "name": "Runtime name"})
def restart(ctx: Context, workspace_module: str, name: str):
    """Restart a runtime."""
    runtime = resolve_runtime(workspace_module, name)
    status = client.control_runtime(runtime.id, "restart")
    print(f"{status.name}: {status.state}")


@task(help={"workspace_module": "Workspace that owns the runtime", "name": "Runtime name"})
def start(ctx: Context, workspace_module: str, name: str):
    """Start a runtime."""
    runtime = resolve_runtime(workspace_module, name)
    status = client.control_runtime(runtime.id, "start")
    print(f"{status.name}: {status.state}")


@task(help={"workspace_module": "Workspace that owns the runtime", "name": "Runtime name"})
def stop(ctx: Context, workspace_module: str, name: str):
    """Stop a runtime."""
    runtime = resolve_runtime(workspace_module, name)
    status = client.control_runtime(runtime.id, "stop")
    print(f"{status.name}: {status.state}")


@task(help={"workspace_module": "Limit the status to one workspace"})
def status(ctx: Context, workspace_module: str | None = None):
    """Show the supervisord state of every runtime."""
    runtimes = client.list_runtimes(workspace_module)
    if not runtimes:
        print("No runtimes.")
        return

    width = max(len(runtime.program_name) for runtime in runtimes)
    for runtime in sorted(runtimes, key=lambda runtime: runtime.program_name):
        if not runtime.is_enabled:
            print(f"{runtime.program_name:<{width}}  DISABLED")
            continue
        try:
            process = client.get_runtime_status(runtime.id)
            print(f"{runtime.program_name:<{width}}  {process.state:<10} {process.description}")
        except Exception as exc:  # noqa: BLE001 - one unreachable runtime must not hide the rest
            print(f"{runtime.program_name:<{width}}  UNKNOWN    {exc}")


@task(
    help={
        "workspace_module": "Workspace that owns the runtime",
        "name": "Runtime name",
        "offset": "Byte offset to start reading from. Negative reads the last N bytes.",
    },
)
def logs(ctx: Context, workspace_module: str, name: str, offset: int = -4000):
    """Print a runtime's log, read through supervisord."""
    runtime = resolve_runtime(workspace_module, name)
    record = client.get_runtime_logs(runtime.id, offset=offset)
    print(record.content, end="")


@task(help={"workspace_module": "Workspace that owns the runtime", "name": "Runtime name"})
def sync(ctx: Context, workspace_module: str, name: str):
    """Run a runtime's sync commands inside the workspace over SSH, then restart it."""
    workspace = get_workspace(workspace_module)
    runtime = resolve_runtime(workspace_module, name)
    sync_commands = client.get_sync_commands(runtime.id)

    # Management decides what to run; the CLI is what has an SSH identity for the workspace user.
    connection = build_ssh_connection(workspace)
    for command in sync_commands.commands:
        connection.run(f"cd {sync_commands.directory} && {command}", echo=True, pty=True)

    status = client.control_runtime(runtime.id, "restart")

    print("")
    print(f"Synced {runtime.program_name}: {status.state}")


@task(help={"workspace_module": "Workspace that owns the runtime", "name": "Runtime name"})
def remove(ctx: Context, workspace_module: str, name: str):
    """Remove a runtime and its configuration."""
    runtime = resolve_runtime(workspace_module, name)
    client.delete_runtime(runtime.id)
    apply_configs(ctx)

    print("")
    print(f"Removed {runtime.program_name}.")
