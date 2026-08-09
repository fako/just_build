"""Shared helpers for the runtimes namespace. Every one of them defers to the management API."""
from __future__ import annotations

from invoke.context import Context

from workspaces.cli.client import ManagementClientError
from workspaces.cli.common import ensure_workspace_log_dir
from workspaces.cli.configs import reconcile_configs
from workspaces.cli.runtimes import client


def resolve_runtime(workspace_module: str, name: str) -> client.RuntimeRecord:
    """Find a runtime by the pair a human types, rather than by the id the API addresses it with."""
    runtimes = client.list_runtimes(workspace_module)
    for runtime in runtimes:
        if runtime.name == name:
            return runtime

    known = ", ".join(sorted(runtime.name for runtime in runtimes)) or "none"
    raise ManagementClientError(
        f"Workspace '{workspace_module}' has no runtime called '{name}'. Known runtimes: {known}."
    )


def apply_configs(ctx: Context | None = None, workspace_module: str | None = None,
                  prune: bool = True) -> tuple[list, list]:
    """
    Fetch what management renders, write it, and let supervisord and nginx pick it up.

    Write first, reload second. Every flow that changes configuration ends here.
    """
    manifest = client.get_manifest(workspace_module)
    written, removed = reconcile_configs(manifest, prune=prune)

    if ctx is not None:
        # Supervisord refuses to load a program whose log directory is missing, and will not create
        # one itself, so the directories have to exist before it rereads.
        for module in manifest.workspaces:
            ensure_workspace_log_dir(ctx, module)

    changes = client.reload_runtimes()

    for path in written:
        print(f"[configs] wrote {path}")
    for path in removed:
        print(f"[configs] removed {path}")
    for label, groups in (("added", changes.added), ("changed", changes.changed), ("removed", changes.removed)):
        for group in groups:
            print(f"[supervisord] {label} {group}")

    return written, removed


def control_workspace_runtimes(workspace_module: str, action: str) -> None:
    """
    Apply a process action to every enabled runtime of a workspace.

    A runtime supervisord has not been told about yet answers 409, which is a normal state during
    setup rather than a failure, so it is reported and skipped.
    """
    for runtime in client.list_runtimes(workspace_module):
        if not runtime.is_enabled:
            continue
        try:
            status = client.control_runtime(runtime.id, action)
            print(f"[{action}] {status.name}: {status.state}")
        except ManagementClientError as exc:
            print(f"[{action}] {runtime.program_name}: skipped ({exc})")


def stop_workspace_runtimes(workspace_module: str) -> None:
    control_workspace_runtimes(workspace_module, "stop")


def restart_workspace_runtimes(workspace_module: str) -> None:
    control_workspace_runtimes(workspace_module, "restart")


def print_runtimes(runtimes: list[client.RuntimeRecord]) -> None:
    if not runtimes:
        print("No runtimes.")
        return

    width = max(len(runtime.program_name) for runtime in runtimes)
    for runtime in sorted(runtimes, key=lambda runtime: runtime.program_name):
        port = f":{runtime.port}" if runtime.port else ""
        state = runtime_state(runtime)
        print(f"{runtime.program_name:<{width}}  {runtime.type:<8} {state:<11} {port}")


def runtime_state(runtime: client.RuntimeRecord) -> str:
    """Where a runtime is in its life: added, installed, and only then enabled."""
    if runtime.is_enabled:
        return "enabled"
    return "installed" if runtime.installed_at else "uninstalled"
