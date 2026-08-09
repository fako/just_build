import importlib
from types import SimpleNamespace

import pytest
from workspaces.cli.client import WorkspaceRecord


clone_cli = importlib.import_module("workspaces.cli.clone")


COMPLETE_SETUP = {
    "workspace_created": "now",
    "home_created": "now",
    "secrets_created": "now",
    "ssh_access": "now",
    "git_key_created": "now",
    "database_created": "now",
}


class RecordingContext:
    def __init__(self) -> None:
        self.config = SimpleNamespace(postgres=SimpleNamespace(user="postgres", password="postgres-password"))

    def run(self, command: str, **kwargs) -> None:
        raise AssertionError(f"unexpected host command: {command}")


def workspace_record(*, setup: dict[str, str] | None = None,
                     git_public_key: str = "ssh-ed25519 AAAA") -> WorkspaceRecord:
    return WorkspaceRecord(
        id="11111111-1111-1111-1111-111111111111",
        name="Demo Workspace",
        module="demo",
        slug="demo",
        setup=COMPLETE_SETUP if setup is None else setup,
        git_public_key=git_public_key,
        ssh=WorkspaceRecord.SSHConfig(identity_file="workspaces/ssh/keys/demo/id_ed25519"),
    )


def patch_clone(monkeypatch, workspace: WorkspaceRecord, calls: list, *, remote_key: str | None = None):
    """Replace everything that leaves the process, recording the order the task did it in."""
    monkeypatch.setattr(clone_cli, "get_workspace", lambda workspace_module: workspace)
    monkeypatch.setattr(clone_cli, "build_ssh_connection", lambda received: object())
    monkeypatch.setattr(clone_cli, "ensure_workspace_database", lambda ctx, received: calls.append("database"))
    monkeypatch.setattr(clone_cli, "log_setup_step", lambda module, step: calls.append(("setup", step)))
    monkeypatch.setattr(clone_cli, "ensure_git_repo", lambda *args: calls.append("git_repo"))
    monkeypatch.setattr(
        clone_cli, "read_workspace_git_key",
        lambda conn, module: remote_key if remote_key is not None else workspace.git_public_key,
    )
    monkeypatch.setattr(
        clone_cli, "patch_workspace",
        lambda module, **kwargs: calls.append(("patched", kwargs)),
    )
    monkeypatch.setattr(clone_cli, "set_origin", lambda conn, repo_dir, url: calls.append(("origin", url)))
    monkeypatch.setattr(clone_cli, "fetch_origin", lambda *args: calls.append("fetch"))
    monkeypatch.setattr(clone_cli, "default_remote_branch", lambda conn, repo_dir: "main")
    monkeypatch.setattr(clone_cli, "checkout_branch", lambda conn, repo_dir, branch: calls.append(("checkout", branch)))


def test_clone_repo_fetches_and_checks_out_the_default_branch(monkeypatch) -> None:
    calls: list = []
    patch_clone(monkeypatch, workspace_record(), calls)

    clone_cli.clone_repo.body(RecordingContext(), "demo", "git@github.com:owner/repo.git")

    assert calls == [
        "git_repo",
        ("setup", "git_initialized"),
        ("origin", "git@github.com:owner/repo.git"),
        "fetch",
        ("checkout", "main"),
        ("setup", "repository_cloned"),
    ]


def test_clone_repo_checks_out_the_requested_branch(monkeypatch) -> None:
    calls: list = []
    patch_clone(monkeypatch, workspace_record(), calls)
    monkeypatch.setattr(
        clone_cli, "default_remote_branch", lambda *args: pytest.fail("resolved a branch that was given"),
    )

    clone_cli.clone_repo.body(RecordingContext(), "demo", "git@github.com:owner/repo.git", branch="develop")

    assert ("checkout", "develop") in calls


def test_clone_repo_creates_the_database_when_it_is_missing(monkeypatch) -> None:
    calls: list = []
    setup = {step: "now" for step in COMPLETE_SETUP if step != "database_created"}
    patch_clone(monkeypatch, workspace_record(setup=setup), calls)

    clone_cli.clone_repo.body(RecordingContext(), "demo", "git@github.com:owner/repo.git")

    assert calls[:2] == ["database", ("setup", "database_created")]


def test_clone_repo_requires_the_workspace_key(monkeypatch) -> None:
    setup = {step: "now" for step in COMPLETE_SETUP if step != "git_key_created"}
    monkeypatch.setattr(clone_cli, "get_workspace", lambda workspace_module: workspace_record(setup=setup))

    with pytest.raises(RuntimeError, match="git_key_created"):
        clone_cli.clone_repo.body(RecordingContext(), "demo", "git@github.com:owner/repo.git")


def test_clone_repo_refuses_an_https_url(monkeypatch) -> None:
    monkeypatch.setattr(
        clone_cli, "get_workspace", lambda *args: pytest.fail("management called for an unusable URL"),
    )

    with pytest.raises(RuntimeError, match="not an SSH git URL"):
        clone_cli.clone_repo.body(RecordingContext(), "demo", "https://github.com/owner/repo.git")


def test_clone_repo_stores_a_key_management_does_not_have_yet(monkeypatch) -> None:
    calls: list = []
    patch_clone(
        monkeypatch, workspace_record(git_public_key=""), calls, remote_key="ssh-ed25519 BBBB demo@workspace.local",
    )

    clone_cli.clone_repo.body(RecordingContext(), "demo", "git@github.com:owner/repo.git")

    assert ("patched", {"git_public_key": "ssh-ed25519 BBBB demo@workspace.local"}) in calls


def test_clone_repo_leaves_a_matching_key_alone(monkeypatch) -> None:
    calls: list = []
    patch_clone(monkeypatch, workspace_record(git_public_key="ssh-ed25519 AAAA"), calls)

    clone_cli.clone_repo.body(RecordingContext(), "demo", "git@github.com:owner/repo.git")

    assert not any(call[0] == "patched" for call in calls if isinstance(call, tuple))


def test_workspace_admin_url_points_at_the_workspace(monkeypatch) -> None:
    monkeypatch.setattr(clone_cli, "DEFAULT_MANAGEMENT_URL", "http://127.0.0.1:8000/")

    url = clone_cli.workspace_admin_url(workspace_record())

    assert url == (
        "http://127.0.0.1:8000/admin/access_control/workspace/11111111-1111-1111-1111-111111111111/change/"
    )
