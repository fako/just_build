from getpass import getpass

from invoke.tasks import task

from workspaces.cli.client import create_workspace, patch_workspace
from workspaces.cli.common import (
    DEFAULT_HOST,
    DEFAULT_SSH_PORT,
    assert_container_workspace_absent,
    assert_workspace_state_clean,
    build_ssh_connection,
    create_container_user_and_home,
    ensure_workspace_secret_file,
    ensure_workspace_keypair,
    ensure_workspace_log_dir,
    ensure_workspaces_container,
    install_workspace_shell_environment,
    log_setup_step,
    publish_authorized_key,
    refresh_generated_ssh_config,
    workspace_private_key_path,
)
from workspaces.cli.constants import REPOSITORY_DIR
from workspaces.cli.repository import ensure_workspace_git_key


def prompt_key_password(key_password: str | None) -> str:
    """
    Ask for the password that protects the workspace's own SSH key.

    Nothing stores the answer. Every later command that reaches a git remote loads the key into an
    ssh-agent and lets ssh-add ask for the password again, which is why an empty one is allowed but
    said out loud: it leaves the key usable by anything that can read the workspace home.
    """
    if key_password is not None:
        return key_password

    while True:
        password = getpass("Password for the workspace SSH key (empty for an unprotected key): ")
        confirmation = getpass("Repeat the password: ")
        if password == confirmation:
            if not password:
                print("Creating an unprotected SSH key.")
            return password
        print("Passwords did not match. Try again.")


@task(
    help={
        "name": "Human-friendly workspace name",
        "module": "Unique workspace module and Linux username",
        "key_password": "Password for the workspace SSH key. Prompted for when left out.",
    }
)
def create(ctx, name: str, module: str, key_password: str | None = None):
    """Create a workspace, its Linux account, secrets, SSH access and its own git SSH key."""
    assert_workspace_state_clean(module)
    ensure_workspaces_container(ctx)
    assert_container_workspace_absent(ctx, module)
    # Asked for after the checks that refuse to create anything, so nobody types a password for a
    # workspace that was never going to exist.
    key_password = prompt_key_password(key_password)

    workspace = create_workspace(name=name, module=module)
    if not workspace.api_key:
        raise RuntimeError(f"Management did not return an API key for workspace '{workspace.module}'.")
    log_setup_step(workspace.module, "workspace_created")

    create_container_user_and_home(ctx, workspace.module)
    ensure_workspace_log_dir(ctx, workspace.module)
    log_setup_step(workspace.module, "home_created")

    # The plaintext API key exists only in this response, so it has to reach the workspace .env now.
    secret_path = ensure_workspace_secret_file(ctx, workspace.module, workspace.api_key)
    install_workspace_shell_environment(ctx, workspace.module)
    log_setup_step(workspace.module, "secrets_created")

    private_key, public_key = ensure_workspace_keypair(ctx, workspace.module)
    publish_authorized_key(ctx, workspace.module, public_key.read_text().strip())

    workspace = patch_workspace(
        workspace.module,
        ssh={
            "alias": f"{workspace.slug}-workspace",
            "user": workspace.module,
            "host": DEFAULT_HOST,
            "port": DEFAULT_SSH_PORT,
            "identity_file": str(workspace_private_key_path(workspace.module).relative_to(REPOSITORY_DIR)),
        },
    )
    log_setup_step(workspace.module, "ssh_access")

    refresh_generated_ssh_config()
    log_setup_step(workspace.module, "ssh_config")

    # The second keypair, and the one pointing the other way: the workspace's own identity towards
    # git remotes. It is generated inside the workspace over the SSH access just established, so the
    # private half is born where it stays and management only ever learns the public half.
    git_public_key = ensure_workspace_git_key(build_ssh_connection(workspace), workspace.module, key_password)
    patch_workspace(workspace.module, git_public_key=git_public_key)
    log_setup_step(workspace.module, "git_key_created")

    print("")
    print(f"Created workspace {workspace.name} ({workspace.module}; {workspace.slug}).")
    print(f"Workspace secrets: {secret_path}")
    print(f"SSH private key: {private_key}")
    print("")
    print("The workspace signs in to git remotes with its own key. Add it as a deploy key on any")
    print("repository this workspace should clone or push to:")
    print("")
    print(f"    {git_public_key}")
    print("")
    print("Next step, for a new project:")
    print(f"  invoke workspaces.scaffold --workspace-module={workspace.module}")
    print("Or, for an existing repository:")
    print(f"  invoke workspaces.clone-repo --workspace-module={workspace.module} "
          "--repository=git@github.com:owner/repository.git")
