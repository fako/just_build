"""
Remote work inside workspaces: the utility layer for reaching in over SSH.

These would have been fabric tasks in a fabfile, but fabric's task runner requires invoke<3.0 and
this repository pins invoke 3.0.3, so `fab` cannot run here at all. Fabric's Connection is
unaffected, and is what these use, exactly as the rest of the CLI already does.

The split the plan called for still holds, it just lives in one tool: the runtimes namespace
orchestrates through the management API, and these execute inside a workspace as its Linux user.
Management never gets an SSH identity for a workspace; this is where that privilege stays.
"""
from __future__ import annotations

from pathlib import Path
from shlex import quote

from invoke.collection import Collection
from invoke.context import Context
from invoke.tasks import task

from workspaces.cli.client import get_workspace
from workspaces.cli.common import build_ssh_connection


WORKSPACE_LOG_ROOT = "/var/log/workspaces"


def workspace_connection(workspace_module: str):
    return build_ssh_connection(get_workspace(workspace_module))


def workspace_home(workspace_module: str) -> str:
    return f"/home/{workspace_module}"


@task(help={"workspace_module": "Workspace to connect to"})
def login(ctx: Context, workspace_module: str):
    """Open an interactive shell in the workspace, as the workspace user."""
    workspace = get_workspace(workspace_module)
    alias = workspace.ssh.alias
    if alias:
        # Going through the alias keeps ~/.ssh/config authoritative for interactive sessions, which
        # is the same path Cursor Remote SSH takes.
        ctx.run(f"ssh {quote(alias)}", pty=True, echo=True)
        return

    identity = workspace.ssh.identity_file
    if not identity:
        raise RuntimeError(f"Workspace '{workspace_module}' has no SSH metadata yet.")
    ctx.run(
        f"ssh -i {quote(identity)} -p {workspace.ssh.port or 2222} "
        f"{quote(workspace.ssh.user or workspace_module)}@{quote(workspace.ssh.host or 'localhost')}",
        pty=True, echo=True,
    )


@task(help={
    "workspace_module": "Workspace to run in",
    "command": "Shell command to run",
    "directory": "Directory to run it in, defaults to the workspace home",
})
def run(ctx: Context, workspace_module: str, command: str, directory: str | None = None):
    """Run a command in the workspace as the workspace user."""
    target = directory or workspace_home(workspace_module)
    workspace_connection(workspace_module).run(f"cd {quote(target)} && {command}", echo=True, pty=True)


@task(help={
    "workspace_module": "Workspace to copy into",
    "source": "Local path to copy",
    "target": "Remote path, relative to the workspace home unless absolute",
})
def copy(ctx: Context, workspace_module: str, source: str, target: str = "."):
    """Copy a local file into the workspace."""
    local_path = Path(source).expanduser().resolve()
    if not local_path.is_file():
        raise RuntimeError(f"'{source}' is not a file.")

    remote = target if target.startswith("/") else f"{workspace_home(workspace_module)}/{target}"
    workspace_connection(workspace_module).put(str(local_path), remote=remote)
    print(f"Copied {local_path} to {workspace_module}:{remote}")


@task(help={
    "workspace_module": "Workspace to copy from",
    "source": "Remote path, relative to the workspace home unless absolute",
    "target": "Local directory to copy into, defaults to the working directory",
})
def fetch(ctx: Context, workspace_module: str, source: str, target: str = "."):
    """Copy a file out of the workspace."""
    remote = source if source.startswith("/") else f"{workspace_home(workspace_module)}/{source}"
    local_path = Path(target).expanduser().resolve()
    workspace_connection(workspace_module).get(remote, local=f"{local_path}/")
    print(f"Fetched {workspace_module}:{remote} into {local_path}")


@task(help={
    "workspace_module": "Workspace that owns the runtime",
    "runtime": "Runtime name, for example web or worker",
    "lines": "Number of lines to show",
    "follow": "Keep following the log",
})
def tail(ctx: Context, workspace_module: str, runtime: str = "web", lines: int = 50, follow: bool = False):
    """
    Tail a runtime's log from inside the workspace.

    The log directory is readable by the workspace group and nobody else, so a workspace only ever
    sees its own. `invoke runtimes.logs` reads the same file through supervisord instead.
    """
    log_path = f"{WORKSPACE_LOG_ROOT}/{workspace_module}/{runtime}.log"
    flags = "-f" if follow else ""
    workspace_connection(workspace_module).run(f"tail -n {lines} {flags} {quote(log_path)}", pty=True)


namespace = Collection("remote")
namespace.add_task(login)
namespace.add_task(run)
namespace.add_task(copy)
namespace.add_task(fetch)
namespace.add_task(tail)
