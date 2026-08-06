import getpass
from shlex import quote

from invoke.context import Context
from invoke.tasks import task

from workspaces.cli.client import get_workspace
from workspaces.cli.common import (
    build_ssh_connection,
    ensure_workspace_database,
    log_setup_step,
    require_setup_steps,
)
from workspaces.cli.runtimes.common import restart_workspace_runtimes, stop_workspace_runtimes

CLEAN_REQUIRED_SETUP_STEPS = (
    "workspace_created",
    "home_created",
    "secrets_created",
    "ssh_access",
    "django_initialized",
    "dependencies_updated",
)


def run_workspace_migrations(conn, repo_dir: str) -> None:
    conn.run(f"cd {quote(repo_dir)} && venv/bin/python manage.py migrate", echo=True, pty=True)


def ensure_workspace_superusers(conn, repo_dir: str, superusers, force_password: str | None = None) -> None:
    for user in superusers:
        password = user["password"]
        if password is None and force_password:
            password = force_password
        elif password is None:
            password = getpass.getpass(f"Password for '{user['username']}': ")
        conn.run(
            f"cd {quote(repo_dir)} && venv/bin/python manage.py createsuperuser --noinput",
            env={
                "DJANGO_SUPERUSER_USERNAME": user["username"],
                "DJANGO_SUPERUSER_EMAIL": user["email"],
                "DJANGO_SUPERUSER_PASSWORD": password,
            },
            echo=True,
            warn=True,
        )


@task(
    help={
        "workspace_module": "Existing workspace module to reset",
        "force_password": "Sets this password for all superusers when their configured password is null",
    },
)
def clean(ctx: Context, workspace_module: str, force_password: str | None = None):
    """Recreate the workspace database, run migrations, and create configured superusers."""
    workspace = get_workspace(workspace_module)
    require_setup_steps(workspace, CLEAN_REQUIRED_SETUP_STEPS)

    stop_workspace_runtimes(workspace.module)
    ensure_workspace_database(ctx, workspace)
    log_setup_step(workspace.module, "database_created")

    repo_dir = f"/home/{workspace.module}"
    conn = build_ssh_connection(workspace)
    run_workspace_migrations(conn, repo_dir)
    ensure_workspace_superusers(conn, repo_dir, ctx.config.management.superusers, force_password)

    restart_workspace_runtimes(workspace.module)

    print("")
    print(f"Cleaned workspace {workspace.name} ({workspace.module}).")
