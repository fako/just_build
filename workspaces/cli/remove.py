from shutil import rmtree
from shlex import quote

from invoke.context import Context
from invoke.tasks import task

from workspaces.cli.client import ManagementClientError, delete_workspace
from workspaces.cli.common import (
    container_workspace_home_dir,
    container_workspace_secret_dir,
    docker_exec,
    ensure_workspace_state_account_files,
    ensure_workspaces_container,
    refresh_generated_ssh_config,
    sync_workspace_state_account_files,
    workspace_key_dir,
)
from workspaces.cli.runtimes import client as runtimes_client
from workspaces.cli.runtimes.common import apply_configs


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


def remove_workspace_runtimes(ctx: Context, workspace_module: str) -> None:
    """
    Delete the workspace's runtimes, then reconcile.

    Deleting a runtime drops it out of the manifest, which is what removes its files: the reconciler
    prunes whatever management no longer lists.
    """
    try:
        for runtime in runtimes_client.list_runtimes(workspace_module):
            runtimes_client.delete_runtime(runtime.id)
        apply_configs(ctx)
    except ManagementClientError as exc:
        print(f"Could not remove runtimes through management, continuing anyway: {exc}")


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
        quote(path)
        for path in (
            container_workspace_home_dir(workspace_module),
            container_workspace_secret_dir(workspace_module),
            f"/etc/ssh/authorized_keys/{workspace_module}",
            f"/var/log/workspaces/{workspace_module}",
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
    # All that is left on the host is the keypair the control side signs in with. The home and the
    # secrets live in volumes, and remove_container_workspace is what deletes those.
    key_dir = workspace_key_dir(workspace_module)
    if key_dir.exists():
        rmtree(key_dir)


@task(help={"workspace_module": "Workspace module to remove completely"})
def remove(ctx: Context, workspace_module: str):
    """Remove all host, container, database, SSH, config, and management state."""
    validate_workspace_module(workspace_module)
    if not confirm_workspace_removal(workspace_module):
        print(f"Removal of workspace {workspace_module} cancelled.")
        return

    ensure_workspaces_container(ctx)

    remove_workspace_runtimes(ctx, workspace_module)
    remove_workspace_database(ctx, workspace_module)
    remove_container_workspace(ctx, workspace_module)
    remove_host_workspace(workspace_module)

    workspace_deleted = delete_workspace(workspace_module)
    refresh_generated_ssh_config()

    suffix = "" if workspace_deleted else " (management record was already absent)"
    print("")
    print(f"Removed workspace {workspace_module} completely{suffix}.")
