import importlib


sync_cli = importlib.import_module("workspaces.cli.sync")


class RecordingConnection:
    def __init__(self) -> None:
        self.commands: list[dict[str, object]] = []

    def run(self, command: str, *, echo: bool = False) -> None:
        self.commands.append({"command": command, "echo": echo})


def test_collect_static_files_runs_collectstatic_through_workspace_venv() -> None:
    conn = RecordingConnection()

    sync_cli.collect_static_files(conn, "/home/demo")

    assert conn.commands == [
        {"command": "cd /home/demo && venv/bin/python manage.py collectstatic --noinput", "echo": True}
    ]


def test_reset_workspace_ownership_runs_recursively_as_root(monkeypatch) -> None:
    calls: list[tuple[object, str, dict[str, object]]] = []
    ctx = object()

    monkeypatch.setattr(
        sync_cli,
        "ensure_workspaces_container",
        lambda received_ctx: calls.append((received_ctx, "up", {})),
    )
    monkeypatch.setattr(
        sync_cli,
        "docker_exec",
        lambda received_ctx, command, **kwargs: calls.append((received_ctx, command, kwargs)),
    )

    sync_cli.reset_workspace_ownership(ctx, "datagrowth_django")

    assert calls == [
        (ctx, "up", {}),
        (
            ctx,
            "chown -R datagrowth_django:datagrowth_django /home/datagrowth_django",
            {"user": "root"},
        ),
    ]


def test_restart_workspace_program_restarts_supervisor_program(monkeypatch) -> None:
    calls: list[tuple[object, str]] = []
    ctx = object()

    monkeypatch.setattr(
        sync_cli,
        "ensure_workspaces_container",
        lambda received_ctx: calls.append((received_ctx, "up")),
    )
    monkeypatch.setattr(
        sync_cli,
        "docker_exec",
        lambda received_ctx, command: calls.append((received_ctx, command)),
    )

    sync_cli.restart_workspace_program(ctx, "demo")

    assert calls == [
        (ctx, "up"),
        (ctx, "supervisorctl restart demo"),
        (ctx, "nginx -s reload"),
    ]


def test_stop_workspace_program_stops_supervisor_program(monkeypatch) -> None:
    calls: list[tuple[object, str, dict[str, object]]] = []
    ctx = object()

    monkeypatch.setattr(
        sync_cli,
        "ensure_workspaces_container",
        lambda received_ctx: calls.append((received_ctx, "up", {})),
    )
    monkeypatch.setattr(
        sync_cli,
        "docker_exec",
        lambda received_ctx, command, **kwargs: calls.append((received_ctx, command, kwargs)),
    )

    sync_cli.stop_workspace_program(ctx, "demo")

    assert calls == [
        (ctx, "up", {}),
        (ctx, "supervisorctl stop demo", {}),
    ]
