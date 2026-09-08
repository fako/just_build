"""
Git work inside a workspace, over SSH, as the workspace user.

Two commands need this: workspaces.scaffold, which starts an empty repository, and
workspaces.clone_repo, which brings an existing one in. Both run against the workspace home, which
is never an empty directory, and both authenticate to git remotes with the keypair the workspace
generated for itself at creation.
"""
from __future__ import annotations

from shlex import quote

from invoke.watchers import Responder

from workspaces.cli.client import WorkspaceRecord


WORKSPACE_KEY_COMMENT_DOMAIN = "workspace.local"
# Accepting a remote host key on first contact. The alternative is an interactive prompt in the
# middle of a fetch, which is no more of a verification than this is.
#
# Nothing in this module passes env= to conn.run, and nothing should. Fabric cannot set an
# environment variable over SSH, so it prefixes the command with `export NAME=value` built by string
# formatting, with no escaping: a value holding a space ends the assignment, and one holding ';'
# runs what follows it. Quote values into the command instead, or answer a prompt for them.
GIT_SSH_COMMAND = "ssh -o StrictHostKeyChecking=accept-new"
# What a git remote says when it does not accept the workspace key. Anything matching means the key
# is the thing to fix, so the failure is reported with the key to install rather than as raw output.
AUTHENTICATION_MARKERS = (
    "permission denied",
    "publickey",
    "could not read from remote repository",
    "repository not found",
    "authentication failed",
)


def workspace_home(workspace_module: str) -> str:
    return f"/home/{workspace_module}"


def workspace_key_path(workspace_module: str) -> str:
    return f"{workspace_home(workspace_module)}/.ssh/id_ed25519"


def workspace_public_key_path(workspace_module: str) -> str:
    return f"{workspace_key_path(workspace_module)}.pub"


def command_output(result) -> str:
    """Whatever the command said. A pty merges stderr into stdout, so only one of the two is set."""
    return (result.stderr or result.stdout or "").strip()


def assert_ssh_repository_url(repository: str) -> None:
    """
    Accept only the SSH forms of a git URL.

    An HTTPS remote would ask for a password nobody can type into a supervised process, and would
    not use the workspace key at all, so it is refused where it is typed rather than where it fails.
    """
    if repository.startswith("ssh://") or (repository.startswith("git@") and ":" in repository):
        return

    raise RuntimeError(
        f"'{repository}' is not an SSH git URL. Use git@host:owner/repository.git or "
        "ssh://git@host/owner/repository.git, so the workspace can authenticate with its own key."
    )


def ensure_workspace_git_key(conn, workspace_module: str, key_password: str) -> str:
    """
    Generate the workspace's own SSH keypair inside its home, and return the public half.

    The private key never leaves the workspace and is protected by the password its human chose, so
    every git command that reaches a remote loads it into a short-lived ssh-agent instead of
    handing ssh a file to read.
    """
    private_key = workspace_key_path(workspace_module)
    key_dir = f"{workspace_home(workspace_module)}/.ssh"

    existing = conn.run(f"test -f {quote(private_key)}", hide=True, warn=True)
    if not existing.ok:
        conn.run(f"mkdir -p {quote(key_dir)} && chmod 700 {quote(key_dir)}", echo=True)
        generate_workspace_git_key(conn, workspace_module, private_key, key_password)
        conn.run(f"chmod 600 {quote(private_key)}", echo=True)

    return read_workspace_git_key(conn, workspace_module)


def generate_workspace_git_key(conn, workspace_module: str, private_key: str, key_password: str) -> None:
    """
    Run ssh-keygen, answering its passphrase prompts rather than passing the password to it.

    The password reaches ssh-keygen through the terminal and nothing else. Neither -N nor an
    environment variable would do: Fabric puts env vars into the remote command line verbatim, with
    no escaping at all, so a password holding '&' would end the export and stop the key ever being
    generated, and one holding ';' would run whatever followed it. A command line is also the wrong
    place for it regardless of escaping, because /proc/<pid>/cmdline is readable by every other
    workspace in the container.
    """
    passphrase_prompts = [
        Responder(pattern=r"Enter passphrase", response=f"{key_password}\n"),
        Responder(pattern=r"Enter same passphrase", response=f"{key_password}\n"),
    ]
    conn.run(
        f"ssh-keygen -t ed25519 -f {quote(private_key)}"
        f" -C {quote(f'{workspace_module}@{WORKSPACE_KEY_COMMENT_DOMAIN}')}",
        pty=True,
        hide=True,
        watchers=passphrase_prompts,
        echo=True,
    )
    assert_key_password_applied(conn, private_key, key_password)


def assert_key_password_applied(conn, private_key: str, key_password: str) -> None:
    """
    Refuse to hand back a key that is not protected when a password was asked for.

    The prompts are answered by pattern, so a change in what ssh-keygen prints would leave a key
    with no passphrase on it. Failing loudly is the difference between that and handing someone a
    key they believe is protected.
    """
    if not key_password:
        return

    unprotected = conn.run(f'ssh-keygen -y -P "" -f {quote(private_key)}', hide=True, warn=True)
    if unprotected.ok:
        raise RuntimeError(
            f"Generated {private_key} without the password that was asked for. "
            "Remove the key and try again; do not use it as a deploy key."
        )


def read_workspace_git_key(conn, workspace_module: str) -> str:
    result = conn.run(f"cat {quote(workspace_public_key_path(workspace_module))}", hide=True, warn=True)
    if not result.ok:
        raise RuntimeError(
            f"Workspace '{workspace_module}' has no SSH key at {workspace_key_path(workspace_module)}."
        )
    return result.stdout.strip()


def assert_no_git_repo(conn, repo_dir: str, workspace_module: str) -> None:
    """
    Refuse to scaffold onto a repository that is already there.

    Scaffolding writes templates over whatever it finds, so a repository in the home directory is
    somebody's work rather than the empty slate the command assumes.
    """
    result = conn.run(f"test -d {quote(repo_dir)}/.git", hide=True, warn=True)
    if result.ok:
        raise RuntimeError(
            f"Workspace '{workspace_module}' already has a git repository at {repo_dir}, and "
            "scaffolding would write workspace templates over it. Use workspaces.clone-repo to "
            "bring an existing repository in, workspaces.scaffold --over-existing to layer templates "
            "onto the project that is there, or workspaces.remove to start the workspace over."
        )


def assert_git_repo(conn, repo_dir: str, workspace_module: str) -> None:
    """
    Refuse to layer templates onto a home directory that is not a repository.

    Writing over files that are already there is only reasonable because git can undo it, so a
    project with no repository behind it has nothing to review or restore the result against.
    """
    result = conn.run(f"test -d {quote(repo_dir)}/.git", hide=True, warn=True)
    if not result.ok:
        raise RuntimeError(
            f"Workspace '{workspace_module}' has no git repository at {repo_dir}, so there would be "
            "no way back from writing templates over it. Scaffold without --over-existing to start a "
            "project there, or bring one in with workspaces.clone-repo first."
        )


def assert_clean_worktree(conn, repo_dir: str, workspace_module: str) -> None:
    """
    Refuse to write templates over uncommitted work.

    Untracked files are ignored on purpose: the workspace home is the repository root, so it always
    holds .bashrc, .ssh and the rest of a home directory that the cloned repository knows nothing
    about. What matters is tracked changes, because those are what the templates would overwrite,
    and their absence is what makes everything this command writes readable as one diff.
    """
    result = conn.run(f"git -C {quote(repo_dir)} status --porcelain --untracked-files=no", hide=True, warn=True)
    if not result.ok:
        raise RuntimeError(
            f"Could not read the git status of {repo_dir} in workspace '{workspace_module}':\n"
            f"{command_output(result)}"
        )

    changes = result.stdout.strip()
    if changes:
        raise RuntimeError(
            f"Workspace '{workspace_module}' has uncommitted changes in {repo_dir}, and workspace "
            f"templates are written over whatever they cover:\n\n{changes}\n\n"
            "Commit or stash them first, so that what this command writes is the whole diff."
        )


def list_tracked_files(conn, repo_dir: str, relative_paths: tuple[str, ...]) -> tuple[str, ...]:
    """Which of these paths, relative to the repository root, git already has under version control."""
    if not relative_paths:
        return ()

    paths = " ".join(quote(relative_path) for relative_path in relative_paths)
    result = conn.run(f"git -C {quote(repo_dir)} ls-files -- {paths}", hide=True, warn=True)
    if not result.ok:
        raise RuntimeError(
            f"Could not list the tracked files of {repo_dir}:\n{command_output(result)}"
        )

    return tuple(line.strip() for line in result.stdout.splitlines() if line.strip())


def ensure_git_repo(conn, repo_dir: str, workspace_name: str, workspace_module: str) -> None:
    result = conn.run(f"test -d {quote(repo_dir)}/.git", hide=True, warn=True)
    if not result.ok:
        conn.run(f"git init -b main {quote(repo_dir)}", echo=True)

    conn.run(f"git -C {quote(repo_dir)} config user.name {quote(workspace_name)}", echo=True)
    conn.run(f"git -C {quote(repo_dir)} config user.email {quote(workspace_module)}@workspace.local", echo=True)


def ensure_initial_commit(conn, repo_dir: str) -> bool:
    result = conn.run(f"git -C {quote(repo_dir)} rev-parse --verify HEAD", hide=True, warn=True)
    if result.ok:
        return False

    status = conn.run(f"git -C {quote(repo_dir)} status --porcelain", hide=True, warn=True)
    if not status.stdout.strip():
        return False

    conn.run(f"git -C {quote(repo_dir)} add .", echo=True)
    conn.run(f'git -C {quote(repo_dir)} commit -m "Initialize Django project."', echo=True)
    return True


def set_origin(conn, repo_dir: str, repository: str) -> None:
    result = conn.run(f"git -C {quote(repo_dir)} remote get-url origin", hide=True, warn=True)
    subcommand = "set-url" if result.ok else "add"
    conn.run(f"git -C {quote(repo_dir)} remote {subcommand} origin {quote(repository)}", echo=True)


def agent_command(repo_dir: str, workspace_module: str, git_command: str) -> str:
    """
    Wrap a git command that reaches the network in an ssh-agent that dies with it.

    The agent is what makes one password prompt do for the whole command: ssh-add asks once over
    the pty, and everything after it authenticates from memory. It is killed on the way out whether
    the command succeeded or not, so no agent outlives the task that started it.
    """
    private_key = workspace_key_path(workspace_module)
    return (
        f"cd {quote(repo_dir)}"
        # Exported here rather than handed to conn.run(env=...): Fabric has no way to set an
        # environment variable over SSH, so it prefixes the command with an unescaped `export`, and
        # a value holding spaces ends the assignment early. Quoted, it is just a shell assignment.
        f" && export GIT_SSH_COMMAND={quote(GIT_SSH_COMMAND)}"
        ' && eval "$(ssh-agent -s)" > /dev/null'
        f" && ssh-add {quote(private_key)}"
        f" && {git_command}"
        "; status=$?; ssh-agent -k > /dev/null 2>&1; exit $status"
    )


def describe_authentication_failure(workspace: WorkspaceRecord, repository: str, public_key: str,
                                    admin_url: str, output: str) -> str:
    if not any(marker in output.lower() for marker in AUTHENTICATION_MARKERS):
        return f"Could not fetch {repository} into workspace '{workspace.module}':\n{output.strip()}"

    return "\n".join([
        f"{repository} did not accept the SSH key of workspace '{workspace.module}'.",
        "",
        "Add this public key to the repository as a deploy key with write access",
        "(GitHub: Settings > Deploy keys > Add deploy key), then run this command again:",
        "",
        f"    {public_key}",
        "",
        f"The same key is on the workspace in the management admin: {admin_url}",
    ])


def fetch_origin(conn, repo_dir: str, workspace: WorkspaceRecord, repository: str, public_key: str,
                 admin_url: str) -> None:
    """
    Fetch everything the remote has, and point origin/HEAD at its default branch.

    Both halves reach the network, so they share one agent and one password prompt. This is also
    the only step that authenticates, which is why the deploy key advice hangs off its failure.
    """
    command = agent_command(
        repo_dir, workspace.module, "git fetch --tags --prune origin && git remote set-head origin --auto",
    )
    result = conn.run(command, echo=True, pty=True, warn=True)
    if not result.ok:
        raise RuntimeError(
            describe_authentication_failure(workspace, repository, public_key, admin_url, command_output(result))
        )


def default_remote_branch(conn, repo_dir: str) -> str:
    """The branch origin/HEAD points at, which the fetch just resolved."""
    result = conn.run(
        f"git -C {quote(repo_dir)} symbolic-ref --short refs/remotes/origin/HEAD", hide=True, warn=True,
    )
    if not result.ok or not result.stdout.strip():
        raise RuntimeError(
            "Could not determine the default branch of origin. Pass --branch to choose one explicitly."
        )
    return result.stdout.strip().removeprefix("origin/")


def checkout_branch(conn, repo_dir: str, branch: str) -> None:
    """
    Check the branch out over the home directory, and track it.

    Git refuses to overwrite untracked files it does not know about, which is the safety net here:
    a repository carrying its own .profile or .bashrc stops the checkout instead of replacing the
    files that give the workspace its shell environment.
    """
    result = conn.run(
        f"git -C {quote(repo_dir)} checkout -B {quote(branch)} {quote(f'origin/{branch}')}",
        echo=True, warn=True,
    )
    if not result.ok:
        raise RuntimeError(
            f"Could not check out '{branch}' in {repo_dir}:\n{command_output(result)}\n\n"
            "The workspace home already holds the files a workspace needs, and git will not "
            "overwrite them. Move whatever conflicts out of the way and run this command again."
        )

    conn.run(
        f"git -C {quote(repo_dir)} branch --set-upstream-to={quote(f'origin/{branch}')} {quote(branch)}",
        echo=True,
    )
