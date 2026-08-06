import importlib
from types import SimpleNamespace

import pytest


remove_cli = importlib.import_module("workspaces.cli.remove")


class RecordingContext:
    def __init__(self) -> None:
        self.config = SimpleNamespace(
            postgres=SimpleNamespace(user="postgres", password="postgres-password")
        )
        self.runs: list[dict[str, object]] = []

    def run(self, command: str, **kwargs) -> None:
        self.runs.append({"command": command, **kwargs})


def test_validate_workspace_module_rejects_unsafe_paths() -> None:
    for module in ("../demo", "demo-name", "demo/name", ""):
        with pytest.raises(RuntimeError, match="ASCII Python identifier"):
            remove_cli.validate_workspace_module(module)


@pytest.mark.parametrize("answer", ["y", "yes", "Y", " YES "])
def test_confirm_workspace_removal_accepts_explicit_yes(monkeypatch, answer) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt: answer)

    assert remove_cli.confirm_workspace_removal("demo") is True


@pytest.mark.parametrize("answer", ["", "n", "no", "anything else"])
def test_confirm_workspace_removal_rejects_everything_else(monkeypatch, answer) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt: answer)

    assert remove_cli.confirm_workspace_removal("demo") is False


def test_remove_task_cancellation_does_not_start_cleanup(monkeypatch, capsys) -> None:
    monkeypatch.setattr(remove_cli, "confirm_workspace_removal", lambda module: False)
    monkeypatch.setattr(
        remove_cli,
        "ensure_workspaces_container",
        lambda ctx: pytest.fail("cleanup started after cancellation"),
    )

    remove_cli.remove.body(object(), "demo")

    assert capsys.readouterr().out == "Removal of workspace demo cancelled.\n"


def test_remove_workspace_runtimes_deletes_each_and_reconciles(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []
    runtimes = [
        SimpleNamespace(id="runtime-web", program_name="demo_web"),
        SimpleNamespace(id="runtime-worker", program_name="demo_worker"),
    ]

    monkeypatch.setattr(remove_cli.runtimes_client, "list_runtimes", lambda module: runtimes)
    monkeypatch.setattr(
        remove_cli.runtimes_client, "delete_runtime", lambda runtime_id: calls.append(("delete", runtime_id)),
    )
    monkeypatch.setattr(remove_cli, "apply_configs", lambda ctx: calls.append(("apply", None)))

    remove_cli.remove_workspace_runtimes(RecordingContext(), "demo")

    # Deleting drops each runtime out of the manifest, and the reconcile is what removes its files.
    assert calls == [
        ("delete", "runtime-web"),
        ("delete", "runtime-worker"),
        ("apply", None),
    ]


def test_remove_workspace_runtimes_continues_when_management_is_unreachable(monkeypatch, capsys) -> None:
    def explode(module):
        raise remove_cli.ManagementClientError("management is down")

    monkeypatch.setattr(remove_cli.runtimes_client, "list_runtimes", explode)

    # Removal must still be able to finish tearing down the account and files.
    remove_cli.remove_workspace_runtimes(RecordingContext(), "demo")

    assert "continuing anyway" in capsys.readouterr().out


def test_remove_workspace_database_uses_workspace_database_and_role() -> None:
    ctx = RecordingContext()

    remove_cli.remove_workspace_database(ctx, "demo")

    assert ctx.runs == [{
        "command": "./services/postgres/scripts/remove_database.sh",
        "env": {
            "DATABASE_NAME": "demo",
            "DATABASE_USER": "demo",
            "POSTGRES_USER": "postgres",
            "PGPASSWORD": "postgres-password",
            "POSTGRES_DB": "postgres",
            "PGHOST": "postgres",
            "PGPORT": "5432",
        },
        "pty": True,
        "echo": True,
    }]


def test_remove_container_workspace_removes_mounts_key_and_persistent_account(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        remove_cli,
        "docker_exec",
        lambda ctx, command, **kwargs: calls.append((command, kwargs)),
    )
    monkeypatch.setattr(
        remove_cli,
        "ensure_workspace_state_account_files",
        lambda ctx: calls.append(("ensure account files", {})),
    )
    monkeypatch.setattr(
        remove_cli,
        "sync_workspace_state_account_files",
        lambda ctx: calls.append(("sync account files", {})),
    )

    remove_cli.remove_container_workspace(object(), "demo")

    assert calls == [
        (
            "rm -rf -- /home/demo /workspaces/secrets/demo /etc/ssh/authorized_keys/demo"
            " /var/log/workspaces/demo",
            {"user": "root"},
        ),
        ("ensure account files", {}),
        (
            "if id -u demo >/dev/null 2>&1; then pkill -KILL -u demo 2>/dev/null || true;"
            " userdel -P /workspaces/state demo; fi",
            {"user": "root"},
        ),
        (
            "if getent group demo >/dev/null 2>&1; then groupdel -P /workspaces/state demo; fi",
            {"user": "root"},
        ),
        ("sync account files", {}),
    ]


def test_remove_host_workspace_removes_all_workspace_directories(tmp_path, monkeypatch) -> None:
    paths = [tmp_path / name for name in ("repo", "secret", "key")]
    for path in paths:
        path.mkdir()
        (path / "file").write_text("data")

    monkeypatch.setattr(remove_cli, "workspace_repo_dir", lambda module: paths[0])
    monkeypatch.setattr(remove_cli, "workspace_secret_dir", lambda module: paths[1])
    monkeypatch.setattr(remove_cli, "workspace_key_dir", lambda module: paths[2])

    remove_cli.remove_host_workspace("demo")

    assert not any(path.exists() for path in paths)
