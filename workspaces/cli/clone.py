"""
Bringing an existing repository into a workspace.

The counterpart of workspaces.scaffold: same preconditions, same end state, different origin for
the code. It authenticates as the workspace rather than as the human running the command, so the
repository has to trust the workspace key before this can work, and saying so when it does not is
half of what this module is for.
"""
from __future__ import annotations

from invoke.context import Context
from invoke.tasks import task

from workspaces.cli.client import DEFAULT_MANAGEMENT_URL, WorkspaceRecord, get_workspace, patch_workspace
from workspaces.cli.common import (
    build_ssh_connection,
    ensure_workspace_database,
    log_setup_step,
    require_setup_steps,
)
from workspaces.cli.repository import (
    assert_ssh_repository_url,
    checkout_branch,
    default_remote_branch,
    ensure_git_repo,
    fetch_origin,
    read_workspace_git_key,
    set_origin,
)


CLONE_REQUIRED_SETUP_STEPS = (
    "workspace_created",
    "home_created",
    "secrets_created",
    "ssh_access",
    "git_key_created",
)


def workspace_admin_url(workspace: WorkspaceRecord) -> str:
    return f"{DEFAULT_MANAGEMENT_URL.rstrip('/')}/admin/access_control/workspace/{workspace.id}/change/"


def resolve_public_key(conn, workspace: WorkspaceRecord) -> str:
    """
    The key a remote has to trust, taken from management and confirmed against the workspace.

    Reading it back matters because the failure this feeds is the one where they disagree: a key
    regenerated inside the workspace is exactly the case where the deploy key on the remote is the
    old one.
    """
    public_key = read_workspace_git_key(conn, workspace.module)
    if public_key != workspace.git_public_key:
        patch_workspace(workspace.module, git_public_key=public_key)
    return public_key


@task(
    help={
        "workspace_module": "Existing workspace module created by workspaces.create",
        "repository": "SSH git URL, for example git@github.com:owner/repository.git",
        "branch": "Branch to check out, defaults to the default branch of the remote",
    },
)
def clone_repo(ctx: Context, workspace_module: str, repository: str, branch: str | None = None):
    """Clone an existing git repository into the workspace home, over SSH as the workspace user."""
    assert_ssh_repository_url(repository)
    workspace = get_workspace(workspace_module)
    require_setup_steps(workspace, CLONE_REQUIRED_SETUP_STEPS)

    repo_dir = f"/home/{workspace.module}"
    conn = build_ssh_connection(workspace)

    if "database_created" not in workspace.setup:
        ensure_workspace_database(ctx, workspace)
        log_setup_step(workspace.module, "database_created")

    # The home directory already holds .profile, .bashrc and .ssh, so `git clone` has nowhere empty
    # to write into. Fetching into a repository created in place is the same result without asking
    # anyone to move their shell environment out of the way first.
    ensure_git_repo(conn, repo_dir, workspace.name, workspace.module)
    log_setup_step(workspace.module, "git_initialized")

    public_key = resolve_public_key(conn, workspace)
    set_origin(conn, repo_dir, repository)
    fetch_origin(conn, repo_dir, workspace, repository, public_key, workspace_admin_url(workspace))

    checkout = branch or default_remote_branch(conn, repo_dir)
    checkout_branch(conn, repo_dir, checkout)
    log_setup_step(workspace.module, "repository_cloned")

    print("")
    print(f"Cloned {repository} into {workspace.name} ({workspace.module}) on branch {checkout}.")
    print("Next step:")
    print(f"  invoke runtimes.add --workspace-module={workspace.module} --type=django --name=web")
