from shlex import quote

from invoke.tasks import task

from workspaces.cli.client import get_workspace
from workspaces.cli.common import (
    build_ssh_connection,
    docker_exec,
    ensure_workspace_static_dir,
    ensure_workspaces_container,
    log_setup_step,
    require_setup_steps,
    reset_workspace_ownership,
    stop_workspace_program,
)
from workspaces.cli.update import install_pyproject_dependencies


def collect_static_files(conn, repo_dir: str) -> None:
    quoted_repo_dir = quote(repo_dir)
    conn.run(f"cd {quoted_repo_dir} && venv/bin/python manage.py collectstatic --noinput", echo=True)


def restart_workspace_program(ctx, workspace_module: str) -> None:
    ensure_workspaces_container(ctx)
    docker_exec(ctx, f"supervisorctl restart {quote(workspace_module)}")
    docker_exec(ctx, "nginx -s reload")


@task(help={"workspace_module": "Existing enabled workspace module"})
def sync(ctx, workspace_module: str):
    """Install dependencies, collect static files, and restart a workspace."""
    workspace = get_workspace(workspace_module)
    require_setup_steps(
        workspace,
        (
            "workspace_created",
            "home_created",
            "ssh_access",
            "git_initialized",
            "django_initialized",
            "dependencies_updated",
            "enabled",
        ),
    )

    repo_dir = f"/home/{workspace.module}"
    reset_workspace_ownership(ctx, workspace.module)
    stop_workspace_program(ctx, workspace.module)
    conn = build_ssh_connection(workspace)
    install_pyproject_dependencies(conn, repo_dir, workspace.module)
    log_setup_step(workspace.module, "dependencies_updated")
    ensure_workspace_static_dir(ctx, workspace.module)
    collect_static_files(conn, repo_dir)
    restart_workspace_program(ctx, workspace.module)
    log_setup_step(workspace.module, "synced")

    print("")
    print(f"Synced workspace {workspace.name} ({workspace.module}).")
