import importlib
from types import SimpleNamespace

import pytest
from workspaces.cli.client import WorkspaceRecord


repository = importlib.import_module("workspaces.cli.repository")


class RecordingConnection:
    """Answers commands by the first fragment that matches, and records everything it was asked."""

    def __init__(self, results: dict[str, object] | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self.results = results or {}

    def run(self, command: str, echo: bool = False, hide: bool = False, warn: bool = False,
            pty: bool = False, env: dict[str, str] | None = None, watchers: list | None = None):
        self.calls.append({
            "command": command, "env": env, "pty": pty, "warn": warn, "watchers": watchers or [],
        })
        for fragment, result in self.results.items():
            if fragment in command:
                return result
        return SimpleNamespace(ok=True, stdout="", stderr="")

    @property
    def commands(self) -> list[str]:
        return [str(call["command"]) for call in self.calls]


def workspace_record() -> WorkspaceRecord:
    return WorkspaceRecord(
        id="workspace-id",
        name="Demo Workspace",
        module="demo",
        slug="demo",
        setup={},
        ssh=WorkspaceRecord.SSHConfig(),
    )


def result(ok: bool = True, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(ok=ok, stdout=stdout, stderr=stderr)


@pytest.mark.parametrize("url", [
    "git@github.com:owner/repository.git",
    "ssh://git@github.com/owner/repository.git",
])
def test_assert_ssh_repository_url_accepts_ssh_forms(url: str) -> None:
    repository.assert_ssh_repository_url(url)


@pytest.mark.parametrize("url", [
    "https://github.com/owner/repository.git",
    "github.com/owner/repository",
    "git@github.com",
])
def test_assert_ssh_repository_url_rejects_everything_else(url: str) -> None:
    with pytest.raises(RuntimeError, match="not an SSH git URL"):
        repository.assert_ssh_repository_url(url)


def test_ensure_git_repo_initializes_main_branch() -> None:
    conn = RecordingConnection({"test -d /home/demo/.git": result(ok=False)})

    repository.ensure_git_repo(conn, "/home/demo", "Demo Workspace", "demo")

    assert conn.commands == [
        "test -d /home/demo/.git",
        "git init -b main /home/demo",
        "git -C /home/demo config user.name 'Demo Workspace'",
        "git -C /home/demo config user.email demo@workspace.local",
    ]


def test_ensure_git_repo_leaves_an_existing_repository_alone() -> None:
    conn = RecordingConnection()

    repository.ensure_git_repo(conn, "/home/demo", "Demo Workspace", "demo")

    assert "git init -b main /home/demo" not in conn.commands


def keygen_connection() -> RecordingConnection:
    return RecordingConnection({
        "test -f /home/demo/.ssh/id_ed25519": result(ok=False),
        "cat /home/demo/.ssh/id_ed25519.pub": result(stdout="ssh-ed25519 AAAA demo@workspace.local\n"),
        # A protected key: ssh-keygen refuses to read it with an empty passphrase.
        'ssh-keygen -y -P ""': result(ok=False),
    })


def test_ensure_workspace_git_key_generates_the_key_with_the_password_out_of_the_command() -> None:
    conn = keygen_connection()

    public_key = repository.ensure_workspace_git_key(conn, "demo", "a-password")

    assert public_key == "ssh-ed25519 AAAA demo@workspace.local"
    keygen_call = next(call for call in conn.calls if "ssh-keygen -t" in str(call["command"]))
    # The password is typed at the prompt, so it reaches neither the command line nor the
    # environment. Fabric copies env values into the remote command line without escaping them.
    assert "a-password" not in str(keygen_call["command"])
    assert keygen_call["env"] is None
    assert keygen_call["pty"] is True
    assert "chmod 600 /home/demo/.ssh/id_ed25519" in conn.commands


@pytest.mark.parametrize("password", [
    "corr&ct horse",
    "a;b`whoami`c",
    "p$ss\"wo'rd",
    "pipe|and $(subshell)",
])
def test_ensure_workspace_git_key_accepts_shell_metacharacters(password) -> None:
    """
    A password is a password, not a fragment of shell.

    Every one of these used to break the command it was interpolated into: '&' ended the export and
    stopped ssh-keygen ever running, and ';' ran whatever came after it.
    """
    conn = keygen_connection()

    repository.ensure_workspace_git_key(conn, "demo", password)

    keygen_call = next(call for call in conn.calls if "ssh-keygen -t" in str(call["command"]))
    assert password not in str(keygen_call["command"])
    responses = [watcher.response for watcher in keygen_call["watchers"]]
    # Answered verbatim at both prompts, with nothing quoted, escaped or stripped.
    assert responses == [f"{password}\n", f"{password}\n"]


def test_ensure_workspace_git_key_refuses_a_key_that_came_out_unprotected() -> None:
    """A prompt that stopped matching would otherwise hand back an unprotected key silently."""
    conn = RecordingConnection({
        "test -f /home/demo/.ssh/id_ed25519": result(ok=False),
        "cat /home/demo/.ssh/id_ed25519.pub": result(stdout="ssh-ed25519 AAAA demo@workspace.local\n"),
        'ssh-keygen -y -P ""': result(ok=True),
    })

    with pytest.raises(RuntimeError, match="without the password that was asked for"):
        repository.ensure_workspace_git_key(conn, "demo", "a-password")


def test_ensure_workspace_git_key_does_not_check_protection_when_no_password_was_asked_for() -> None:
    conn = keygen_connection()

    repository.ensure_workspace_git_key(conn, "demo", "")

    assert not any('ssh-keygen -y -P ""' in command for command in conn.commands)


def test_ensure_workspace_git_key_keeps_an_existing_key() -> None:
    conn = RecordingConnection({
        "cat /home/demo/.ssh/id_ed25519.pub": result(stdout="ssh-ed25519 AAAA demo@workspace.local\n"),
    })

    public_key = repository.ensure_workspace_git_key(conn, "demo", "a-password")

    assert public_key == "ssh-ed25519 AAAA demo@workspace.local"
    assert not any("ssh-keygen" in command for command in conn.commands)


def test_read_workspace_git_key_reports_a_workspace_without_one() -> None:
    conn = RecordingConnection({"cat /home/demo/.ssh/id_ed25519.pub": result(ok=False)})

    with pytest.raises(RuntimeError, match="has no SSH key"):
        repository.read_workspace_git_key(conn, "demo")


def test_assert_no_git_repo_refuses_a_repository_that_is_already_there() -> None:
    conn = RecordingConnection()

    with pytest.raises(RuntimeError) as error:
        repository.assert_no_git_repo(conn, "/home/demo", "demo")

    message = str(error.value)
    assert "already has a git repository at /home/demo" in message
    assert "workspaces.clone-repo" in message


def test_assert_no_git_repo_passes_on_an_empty_home() -> None:
    conn = RecordingConnection({"test -d /home/demo/.git": result(ok=False)})

    repository.assert_no_git_repo(conn, "/home/demo", "demo")


def test_fetch_origin_runs_one_agent_for_the_whole_fetch() -> None:
    conn = RecordingConnection()

    repository.fetch_origin(
        conn, "/home/demo", workspace_record(), "git@github.com:owner/repo.git", "ssh-ed25519 AAAA", "http://admin/",
    )

    fetch_call = conn.calls[0]
    command = str(fetch_call["command"])
    assert "ssh-agent -s" in command
    assert "ssh-add /home/demo/.ssh/id_ed25519" in command
    assert "git fetch --tags --prune origin" in command
    assert "git remote set-head origin --auto" in command
    # The agent dies with the command whether the fetch worked or not.
    assert command.endswith("; status=$?; ssh-agent -k > /dev/null 2>&1; exit $status")
    # A pty is what lets ssh-add ask for the key password.
    assert fetch_call["pty"] is True
    assert fetch_call["env"] == {"GIT_SSH_COMMAND": repository.GIT_SSH_COMMAND}


def test_fetch_origin_reports_a_rejected_key_with_the_key_to_install() -> None:
    conn = RecordingConnection({
        "git fetch": result(ok=False, stdout="git@github.com: Permission denied (publickey)."),
    })

    with pytest.raises(RuntimeError) as error:
        repository.fetch_origin(
            conn, "/home/demo", workspace_record(), "git@github.com:owner/repo.git",
            "ssh-ed25519 AAAA demo@workspace.local", "http://management/admin/workspace/1/",
        )

    message = str(error.value)
    assert "did not accept the SSH key" in message
    assert "ssh-ed25519 AAAA demo@workspace.local" in message
    assert "deploy key" in message
    assert "http://management/admin/workspace/1/" in message


def test_fetch_origin_passes_other_failures_through() -> None:
    conn = RecordingConnection({
        "git fetch": result(ok=False, stdout="fatal: the remote end hung up unexpectedly"),
    })

    with pytest.raises(RuntimeError) as error:
        repository.fetch_origin(
            conn, "/home/demo", workspace_record(), "git@github.com:owner/repo.git", "ssh-ed25519 AAAA", "http://a/",
        )

    message = str(error.value)
    assert "hung up unexpectedly" in message
    assert "deploy key" not in message


def test_default_remote_branch_reads_what_the_fetch_resolved() -> None:
    conn = RecordingConnection({"symbolic-ref": result(stdout="origin/develop\n")})

    assert repository.default_remote_branch(conn, "/home/demo") == "develop"


def test_default_remote_branch_asks_for_a_branch_when_origin_has_no_head() -> None:
    conn = RecordingConnection({"symbolic-ref": result(ok=False)})

    with pytest.raises(RuntimeError, match="--branch"):
        repository.default_remote_branch(conn, "/home/demo")


def test_checkout_branch_tracks_the_remote_branch() -> None:
    conn = RecordingConnection()

    repository.checkout_branch(conn, "/home/demo", "main")

    assert conn.commands == [
        "git -C /home/demo checkout -B main origin/main",
        "git -C /home/demo branch --set-upstream-to=origin/main main",
    ]


def test_checkout_branch_explains_a_collision_with_the_workspace_home() -> None:
    conn = RecordingConnection({
        "checkout": result(
            ok=False,
            stderr="error: The following untracked working tree files would be overwritten: .profile",
        ),
    })

    with pytest.raises(RuntimeError) as error:
        repository.checkout_branch(conn, "/home/demo", "main")

    message = str(error.value)
    assert ".profile" in message
    assert "will not overwrite them" in message


def test_set_origin_adds_or_updates_the_remote() -> None:
    without_origin = RecordingConnection({"remote get-url origin": result(ok=False)})
    with_origin = RecordingConnection({"remote get-url origin": result(stdout="git@github.com:owner/old.git")})

    repository.set_origin(without_origin, "/home/demo", "git@github.com:owner/repo.git")
    repository.set_origin(with_origin, "/home/demo", "git@github.com:owner/repo.git")

    assert without_origin.commands[-1] == "git -C /home/demo remote add origin git@github.com:owner/repo.git"
    assert with_origin.commands[-1] == "git -C /home/demo remote set-url origin git@github.com:owner/repo.git"
