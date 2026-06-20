from pathlib import Path
from shutil import rmtree
from shlex import quote

from invoke.context import Context
from invoke.tasks import task

from workspaces.cli.client import delete_workspace
from workspaces.cli.common import (
    active_nginx_config_path,
    active_supervisor_config_path,
    docker_exec,
    ensure_workspace_state_account_files,
    ensure_workspaces_container,
    refresh_generated_ssh_config,
    staged_nginx_config_path,
    staged_supervisor_config_path,
    sync_workspace_state_account_files,
    workspace_key_dir,
    workspace_repo_dir,
    workspace_secret_dir,
)


def validate_workspace_module(workspace_module: str) -> None:
    if not workspace_module.isascii() or not workspace_module.isidentifier():
        raise RuntimeError(
            "Workspace module must be an ASCII Python identifier, for example 'my_workspace'."
        )


def confirm_workspace_removal(workspace_module: str) -> bool:
    answer = input(
        f"Remove workspace '{workspace_module}' completely? "
        "This permanently deletes its files, databases, credentials, and account. [y/N] "
    )
    return answer.strip().lower() in {"y", "yes"}


def remove_runtime_config(ctx: Context, workspace_module: str) -> None:
    docker_exec(ctx, f"supervisorctl stop {quote(workspace_module)}", warn=True)

    for path in (
        active_supervisor_config_path(workspace_module),
        active_nginx_config_path(workspace_module),
        staged_supervisor_config_path(workspace_module),
        staged_nginx_config_path(workspace_module),
    ):
        path.unlink(missing_ok=True)

    docker_exec(ctx, "supervisorctl reread && supervisorctl update")
    docker_exec(ctx, "nginx -t && nginx -s reload")


def remove_workspace_database(ctx: Context, workspace_module: str) -> None:
    ctx.run(
        "./services/postgres/scripts/remove_database.sh",
        env={
            "DATABASE_NAME": workspace_module,
            "DATABASE_USER": workspace_module,
            "POSTGRES_USER": ctx.config.postgres.user,
            "PGPASSWORD": ctx.config.postgres.password,
            "POSTGRES_DB": getattr(ctx.config.postgres, "database", "postgres"),
            "PGHOST": "postgres",
            "PGPORT": "5432",
        },
        pty=True,
        echo=True,
    )


def remove_container_workspace(ctx: Context, workspace_module: str) -> None:
    quoted_module = quote(workspace_module)
    quoted_state = quote("/workspaces/state")
    quoted_paths = " ".join(
        quote(str(path))
        for path in (
            Path("/home") / workspace_module,
            Path("/workspaces/secrets") / workspace_module,
            Path("/etc/ssh/authorized_keys") / workspace_module,
        )
    )
    docker_exec(ctx, f"rm -rf -- {quoted_paths}", user="root")

    ensure_workspace_state_account_files(ctx)
    docker_exec(
        ctx,
        f"if id -u {quoted_module} >/dev/null 2>&1; then"
        f" pkill -KILL -u {quoted_module} 2>/dev/null || true;"
        f" userdel -P {quoted_state} {quoted_module};"
        " fi",
        user="root",
    )
    docker_exec(
        ctx,
        f"if getent group {quoted_module} >/dev/null 2>&1; then"
        f" groupdel -P {quoted_state} {quoted_module};"
        " fi",
        user="root",
    )
    sync_workspace_state_account_files(ctx)


def remove_host_workspace(workspace_module: str) -> None:
    # The repo and secrets are normally removed through their container mounts.
    # Removing them here as well makes this helper complete and independently testable.
    for path in (
        workspace_repo_dir(workspace_module),
        workspace_secret_dir(workspace_module),
        workspace_key_dir(workspace_module),
    ):
        if path.exists():
            rmtree(path)


@task(help={"workspace_module": "Workspace module to remove completely"})
def remove(ctx: Context, workspace_module: str):
    """Remove all host, container, database, SSH, config, and management state."""
    validate_workspace_module(workspace_module)
    if not confirm_workspace_removal(workspace_module):
        print(f"Removal of workspace {workspace_module} cancelled.")
        return

    ensure_workspaces_container(ctx)

    remove_runtime_config(ctx, workspace_module)
    remove_workspace_database(ctx, workspace_module)
    remove_container_workspace(ctx, workspace_module)
    remove_host_workspace(workspace_module)

    workspace_deleted = delete_workspace(workspace_module)
    refresh_generated_ssh_config()

    suffix = "" if workspace_deleted else " (management record was already absent)"
    print("")
    print(f"Removed workspace {workspace_module} completely{suffix}.")
