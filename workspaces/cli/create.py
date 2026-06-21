from invoke.tasks import task

from workspaces.cli.client import create_workspace, patch_workspace
from workspaces.cli.common import (
    DEFAULT_HOST,
    DEFAULT_SSH_PORT,
    assert_container_workspace_absent,
    assert_workspace_state_clean,
    create_container_user_and_home,
    ensure_workspace_secret_file,
    ensure_workspace_secret_root,
    ensure_workspace_keypair,
    ensure_workspaces_container,
    grant_host_workspace_access,
    install_workspace_shell_environment,
    log_setup_step,
    publish_authorized_key,
    refresh_generated_ssh_config,
    stage_workspace_configs,
    workspace_private_key_path,
)
from workspaces.cli.constants import WORKSPACES_DIR


@task(
    help={
        "name": "Human-friendly workspace name",
        "module": "Unique workspace module and Linux username",
        "domain": "Domain for the staged nginx config, defaults to <slug>.localhost",
        "django_module": "Django project module name, defaults to web",
    }
)
def create(ctx, name: str, module: str, domain: str | None = None, django_module: str = "web"):
    """Create a workspace, SSH access, and staged configs."""
    assert_workspace_state_clean(module)
    ensure_workspace_secret_root()
    ensure_workspaces_container(ctx)
    assert_container_workspace_absent(ctx, module)

    workspace = create_workspace(name=name, module=module, django_module=django_module)
    domain = domain or f"{workspace.slug}.localhost"
    log_setup_step(workspace.module, "workspace_created")

    create_container_user_and_home(ctx, workspace.module)
    log_setup_step(workspace.module, "home_created")

    secret_path = ensure_workspace_secret_file(ctx, workspace.module)
    grant_host_workspace_access(ctx, workspace.module)
    install_workspace_shell_environment(ctx, workspace.module)
    log_setup_step(workspace.module, "secrets_created")

    private_key, public_key = ensure_workspace_keypair(ctx, workspace.module)
    publish_authorized_key(ctx, workspace.module, public_key.read_text().strip())

    patch_workspace(
        workspace.module,
        ssh={
            "alias": f"{workspace.slug}-workspace",
            "user": workspace.module,
            "host": DEFAULT_HOST,
            "port": DEFAULT_SSH_PORT,
            "identity_file": str(workspace_private_key_path(workspace.module).relative_to(WORKSPACES_DIR.parent)),
        },
    )
    log_setup_step(workspace.module, "ssh_access")

    stage_workspace_configs(workspace, domain)
    log_setup_step(workspace.module, "config_staged")

    refresh_generated_ssh_config()
    log_setup_step(workspace.module, "ssh_config")

    print("")
    print(f"Created workspace {workspace.name} ({workspace.module}; {workspace.slug}).")
    print(f"Workspace secrets: {secret_path}")
    print(f"SSH private key: {private_key}")
    print("Next step:")
    print(f"  invoke workspaces.init --workspace-module={workspace.module}")
