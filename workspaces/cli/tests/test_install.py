import importlib
from types import SimpleNamespace

import pytest
from workspaces.cli.client import WorkspaceRecord


tasks_cli = importlib.import_module("workspaces.cli.runtimes.tasks")


class RecordingConnection:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def run(self, command: str, echo: bool = False, pty: bool = False):
        self.commands.append(command)
        return SimpleNamespace(ok=True, stdout="", stderr="")


def workspace_record(setup: dict[str, str] | None = None) -> WorkspaceRecord:
    return WorkspaceRecord(
        id="workspace-id",
        name="Demo Workspace",
        module="demo",
        slug="demo",
        django_module="web",
        setup=setup if setup is not None else {
            "workspace_created": "now",
            "home_created": "now",
            "secrets_created": "now",
            "ssh_access": "now",
        },
        ssh=WorkspaceRecord.SSHConfig(identity_file="workspaces/ssh/keys/demo/id_ed25519"),
    )


def runtime_record() -> SimpleNamespace:
    return SimpleNamespace(id="runtime-id", name="web", program_name="demo_web")


def patch_install(monkeypatch, workspace: WorkspaceRecord, conn: RecordingConnection, calls: list):
    monkeypatch.setattr(tasks_cli, "get_workspace", lambda workspace_module: workspace)
    monkeypatch.setattr(tasks_cli, "resolve_runtime", lambda module, name: runtime_record())
    monkeypatch.setattr(tasks_cli, "build_ssh_connection", lambda received: conn)
    monkeypatch.setattr(
        tasks_cli.client,
        "get_install_commands",
        lambda runtime_id: SimpleNamespace(
            program_name="demo_web",
            directory="/home/demo",
            commands=["test -f pyproject.toml", "venv/bin/python -m pip install -e ."],
        ),
    )
    monkeypatch.setattr(
        tasks_cli.client, "mark_runtime_installed", lambda runtime_id: calls.append(("installed", runtime_id)),
    )
    monkeypatch.setattr(tasks_cli, "log_setup_step", lambda module, step: calls.append(("setup", step)))
    monkeypatch.setattr(tasks_cli, "stop_workspace_runtimes", lambda module: calls.append("stop"))
    monkeypatch.setattr(tasks_cli, "restart_workspace_runtimes", lambda module: calls.append("restart"))


def test_install_runs_what_management_returns_and_records_it(monkeypatch) -> None:
    conn = RecordingConnection()
    calls: list = []
    patch_install(monkeypatch, workspace_record(), conn, calls)

    tasks_cli.install.body(object(), "demo", "web")

    assert conn.commands == [
        "cd /home/demo && test -f pyproject.toml",
        "cd /home/demo && venv/bin/python -m pip install -e .",
    ]
    assert calls == [("installed", "runtime-id"), ("setup", "dependencies_installed")]


def test_install_leaves_the_virtualenv_alone_without_rebuild(monkeypatch) -> None:
    conn = RecordingConnection()
    calls: list = []
    patch_install(monkeypatch, workspace_record(), conn, calls)

    tasks_cli.install.body(object(), "demo", "web")

    assert not any("rm -rf venv" in command for command in conn.commands)
    assert "stop" not in calls


def test_install_rebuild_stops_the_workspace_before_removing_its_virtualenv(monkeypatch) -> None:
    conn = RecordingConnection()
    calls: list = []
    patch_install(monkeypatch, workspace_record(), conn, calls)

    tasks_cli.install.body(object(), "demo", "web", rebuild=True)

    assert conn.commands[0] == "cd /home/demo && rm -rf venv"
    # The virtualenv belongs to the workspace, so everything in it goes down and comes back up.
    assert calls[0] == "stop"
    assert calls[-1] == "restart"


def test_install_requires_ssh_access(monkeypatch) -> None:
    workspace = workspace_record({"workspace_created": "now"})
    monkeypatch.setattr(tasks_cli, "get_workspace", lambda workspace_module: workspace)

    with pytest.raises(RuntimeError, match="ssh_access"):
        tasks_cli.install.body(object(), "demo", "web")
