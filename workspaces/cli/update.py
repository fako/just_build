from shlex import quote

from invoke.tasks import task

from workspaces.cli.client import get_workspace
from workspaces.cli.common import build_ssh_connection, log_setup_step, require_setup_steps
from workspaces.cli.runtimes.common import stop_workspace_runtimes


def ensure_pyproject_exists(conn, repo_dir: str, workspace_module: str) -> None:
    result = conn.run(f"test -f {quote(repo_dir)}/pyproject.toml", hide=True, warn=True)
    if not result.ok:
        raise RuntimeError(f"Workspace '{workspace_module}' does not have a pyproject.toml at {repo_dir}")


def install_pyproject_dependencies(conn, repo_dir: str, workspace_module: str) -> None:
    ensure_pyproject_exists(conn, repo_dir, workspace_module)
    quoted_repo_dir = quote(repo_dir)
    conn.run(
        f"cd {quoted_repo_dir} && rm -rf venv && python3 -m venv venv --copies --upgrade-deps",
        echo=True,
    )
    conn.run(f"cd {quoted_repo_dir} && venv/bin/python -m pip install -e .", echo=True)


@task(help={"workspace_module": "Existing workspace module created and initialized earlier"})
def update(ctx, workspace_module: str):
    """Create/update the workspace venv from pyproject.toml."""
    workspace = get_workspace(workspace_module)
    require_setup_steps(workspace, ("workspace_created", "home_created", "ssh_access"))

    repo_dir = f"/home/{workspace.module}"
    stop_workspace_runtimes(workspace.module)
    conn = build_ssh_connection(workspace)
    install_pyproject_dependencies(conn, repo_dir, workspace.module)
    log_setup_step(workspace.module, "dependencies_updated")

    print("")
    print(f"Updated dependencies for {workspace.name} ({workspace.module}).")
    print("Next step:")
    print(f"  invoke runtimes.add --workspace-module={workspace.module} --type=django --name=web")
