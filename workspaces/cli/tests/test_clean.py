import importlib
from types import SimpleNamespace

import pytest
from workspaces.cli.client import WorkspaceRecord


clean_cli = importlib.import_module("workspaces.cli.clean")


class RecordingConnection:
    def __init__(self) -> None:
        self.commands: list[dict[str, object]] = []

    def run(self, command: str, *, echo: bool = False, pty: bool = False, env=None, warn: bool = False):
        self.commands.append({"command": command, "echo": echo, "pty": pty, "env": env, "warn": warn})


class RecordingContext:
    def __init__(self) -> None:
        self.runs: list[dict[str, object]] = []
        self.config = SimpleNamespace(
            postgres=SimpleNamespace(user="postgres", password="postgres-password"),
            management=SimpleNamespace(
                superusers=[{"username": "admin", "email": "admin@example.com", "password": "secret"}]
            ),
        )

    def run(self, command: str, *, env: dict[str, str], pty: bool, echo: bool) -> None:
        self.runs.append({"command": command, "env": env, "pty": pty, "echo": echo})


def workspace_record(*, setup: dict[str, str] | None = None, enabled: bool = False) -> WorkspaceRecord:
    base_setup = {
        "workspace_created": "now",
        "home_created": "now",
        "secrets_created": "now",
        "ssh_access": "now",
        "django_initialized": "now",
        "dependencies_updated": "now",
    }
    if enabled:
        base_setup["enabled"] = "now"
    if setup:
        base_setup.update(setup)
    return WorkspaceRecord(
        id="workspace-id",
        name="Demo Workspace",
        module="demo",
        slug="demo",
        django_module="web",
        setup=base_setup,
        ssh=WorkspaceRecord.SSHConfig(identity_file="workspaces/src/ssh/demo/id_ed25519"),
    )


def test_run_workspace_migrations_uses_workspace_venv() -> None:
    conn = RecordingConnection()

    clean_cli.run_workspace_migrations(conn, "/home/demo")

    assert conn.commands == [
        {"command": "cd /home/demo && venv/bin/python manage.py migrate", "echo": True, "pty": True, "env": None, "warn": False}
    ]


def test_ensure_workspace_superusers_runs_createsuperuser_with_env_vars() -> None:
    conn = RecordingConnection()
    superusers = [{"username": "admin", "email": "admin@example.com", "password": "secret"}]

    clean_cli.ensure_workspace_superusers(conn, "/home/demo", superusers)

    assert len(conn.commands) == 1
    assert conn.commands[0]["command"] == "cd /home/demo && venv/bin/python manage.py createsuperuser --noinput"
    assert conn.commands[0]["env"] == {
        "DJANGO_SUPERUSER_USERNAME": "admin",
        "DJANGO_SUPERUSER_EMAIL": "admin@example.com",
        "DJANGO_SUPERUSER_PASSWORD": "secret",
    }
    assert conn.commands[0]["warn"] is True


def test_clean_recreates_database_migrates_and_creates_users(monkeypatch) -> None:
    workspace = workspace_record()
    conn = RecordingConnection()
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(clean_cli, "get_workspace", lambda workspace_module: workspace)
    monkeypatch.setattr(clean_cli, "stop_workspace_program", lambda ctx, module, **kwargs: calls.append(("stop", module)))
    monkeypatch.setattr(
        clean_cli,
        "ensure_workspace_database",
        lambda ctx, received_workspace: calls.append(("database", received_workspace.module)),
    )
    monkeypatch.setattr(
        clean_cli,
        "log_setup_step",
        lambda workspace_module, step: calls.append(("setup", (workspace_module, step))),
    )
    monkeypatch.setattr(clean_cli, "build_ssh_connection", lambda received_workspace: conn)
    monkeypatch.setattr(clean_cli, "restart_workspace_program", lambda ctx, module: calls.append(("restart", module)))

    clean_cli.clean.body(RecordingContext(), "demo", force_password=None)

    assert calls == [
        ("stop", "demo"),
        ("database", "demo"),
        ("setup", ("demo", "database_created")),
    ]
    assert conn.commands[0]["command"].endswith("manage.py migrate")
    assert conn.commands[1]["command"].endswith("createsuperuser --noinput")
    assert conn.commands[1]["env"]["DJANGO_SUPERUSER_EMAIL"] == "admin@example.com"


def test_clean_restarts_enabled_workspace(monkeypatch) -> None:
    workspace = workspace_record(enabled=True)
    calls: list[str] = []

    monkeypatch.setattr(clean_cli, "get_workspace", lambda workspace_module: workspace)
    monkeypatch.setattr(clean_cli, "stop_workspace_program", lambda *args, **kwargs: None)
    monkeypatch.setattr(clean_cli, "ensure_workspace_database", lambda *args, **kwargs: None)
    monkeypatch.setattr(clean_cli, "log_setup_step", lambda *args, **kwargs: None)
    monkeypatch.setattr(clean_cli, "build_ssh_connection", lambda workspace: RecordingConnection())
    monkeypatch.setattr(
        clean_cli,
        "restart_workspace_program",
        lambda ctx, module: calls.append(module),
    )

    clean_cli.clean.body(RecordingContext(), "demo")

    assert calls == ["demo"]


def test_clean_requires_dependencies_updated(monkeypatch) -> None:
    workspace = WorkspaceRecord(
        id="workspace-id",
        name="Demo Workspace",
        module="demo",
        slug="demo",
        django_module="web",
        setup={
            "workspace_created": "now",
            "home_created": "now",
            "secrets_created": "now",
            "ssh_access": "now",
            "django_initialized": "now",
        },
        ssh=WorkspaceRecord.SSHConfig(identity_file="workspaces/src/ssh/demo/id_ed25519"),
    )

    monkeypatch.setattr(clean_cli, "get_workspace", lambda workspace_module: workspace)

    with pytest.raises(RuntimeError, match="dependencies_updated"):
        clean_cli.clean.body(RecordingContext(), "demo")
