import importlib
from types import SimpleNamespace

import pytest


common = importlib.import_module("workspaces.cli.common")


def test_render_workspace_secret_env_includes_django_and_libpq_settings() -> None:
    content = common.render_workspace_secret_env("demo", "workspace-password", "workspace:a-key")

    assert "POSTGRES_DB=demo\n" in content
    assert "POSTGRES_USER=demo\n" in content
    assert "POSTGRES_PASSWORD=workspace-password\n" in content
    assert "PGDATABASE=demo\n" in content
    assert "PGUSER=demo\n" in content
    assert "PGPASSFILE=/workspaces/secrets/demo/.pgpass\n" in content
    # The workspace calls the management API with these, from inside the container.
    assert "MANAGEMENT_URL=http://management:8000\n" in content
    assert "WORKSPACE_API_KEY=workspace:a-key\n" in content


def test_render_workspace_pgpass_uses_generated_postgres_credentials() -> None:
    assert common.render_workspace_pgpass("demo", "workspace-password") == (
        "postgres:5432:demo:demo:workspace-password\n"
    )


def test_render_workspace_shell_environment_loads_secrets_and_activates_venv() -> None:
    content = common.render_workspace_shell_environment("demo")

    assert '. "/workspaces/secrets/demo/.env"' in content
    assert '[ -z "${VIRTUAL_ENV:-}" ]' in content
    assert '[ -f "$HOME/venv/bin/activate" ]' in content
    assert '. "$HOME/venv/bin/activate"' in content


def test_read_workspace_secret_environment_reads_as_container_root(monkeypatch) -> None:
    calls: list[dict[str, object]] = []
    ctx = object()

    def fake_docker_exec(received_ctx, script, **kwargs):
        calls.append({"ctx": received_ctx, "script": script, **kwargs})
        return SimpleNamespace(
            ok=True,
            stdout="POSTGRES_DB=datagrowth_django\nPOSTGRES_PASSWORD=secret\n",
        )

    monkeypatch.setattr(common, "docker_exec", fake_docker_exec)

    environment = common.read_workspace_secret_environment(ctx, "datagrowth_django")

    assert environment == {
        "POSTGRES_DB": "datagrowth_django",
        "POSTGRES_PASSWORD": "secret",
    }
    assert calls == [{
        "ctx": ctx,
        "script": "cat /workspaces/secrets/datagrowth_django/.env",
        "user": "root",
        "hide": True,
        "warn": True,
    }]


def test_reset_workspace_ownership_chowns_home_and_secrets(monkeypatch) -> None:
    calls: list[tuple[object, str, dict[str, object]]] = []
    ctx = object()

    monkeypatch.setattr(
        common,
        "ensure_workspaces_container",
        lambda received_ctx: calls.append((received_ctx, "up", {})),
    )
    monkeypatch.setattr(
        common,
        "docker_exec",
        lambda received_ctx, command, **kwargs: calls.append((received_ctx, command, kwargs)),
    )

    common.reset_workspace_ownership(ctx, "datagrowth_django")

    assert calls == [
        (ctx, "up", {}),
        (
            ctx,
            "chown -R datagrowth_django:datagrowth_django /home/datagrowth_django"
            " && if [ -d /workspaces/secrets/datagrowth_django ]; then"
            " chown -R root:datagrowth_django /workspaces/secrets/datagrowth_django"
            " && if [ -f /workspaces/secrets/datagrowth_django/.pgpass ]; then"
            " chown datagrowth_django:datagrowth_django /workspaces/secrets/datagrowth_django/.pgpass;"
            " fi; fi",
            {"user": "root"},
        ),
    ]


class RecordingContext:
    """Captures what ctx.run is handed, including the stdin stream write_container_file uses."""

    def __init__(self) -> None:
        self.runs: list[dict[str, object]] = []

    def run(self, command: str, **kwargs):
        in_stream = kwargs.pop("in_stream", None)
        self.runs.append({
            "command": command,
            "stdin": in_stream.getvalue() if in_stream is not None else None,
            **kwargs,
        })
        return SimpleNamespace(ok=True, stdout="")


def test_write_container_file_keeps_the_content_out_of_the_command() -> None:
    ctx = RecordingContext()

    common.write_container_file(ctx, "/workspaces/secrets/demo/.env", "SECRET=hunter2\n", owner="root:demo", mode="640")

    run = ctx.runs[0]
    assert run["stdin"] == "SECRET=hunter2\n"
    # The whole point: what gets echoed to the terminal and the shell's argument list holds no secret.
    assert "hunter2" not in str(run["command"])
    assert run["command"] == (
        "docker compose exec -T --user root workspaces sh -lc "
        "'umask 077 && cat > /workspaces/secrets/demo/.env"
        " && chown root:demo /workspaces/secrets/demo/.env"
        " && chmod 640 /workspaces/secrets/demo/.env'"
    )


def test_ensure_workspace_secret_root_is_traversable_but_not_listable(monkeypatch) -> None:
    scripts: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        common, "docker_exec", lambda ctx, script, **kwargs: scripts.append((script, kwargs)),
    )

    common.ensure_workspace_secret_root(object())

    assert scripts == [(
        "mkdir -p /workspaces/secrets && chown root:root /workspaces/secrets && chmod 711 /workspaces/secrets",
        {"user": "root"},
    )]


def test_ensure_workspace_secret_file_writes_into_the_container(monkeypatch) -> None:
    scripts: list[str] = []
    written: list[dict[str, object]] = []

    monkeypatch.setattr(common, "ensure_workspace_secret_root", lambda ctx: None)
    monkeypatch.setattr(
        common,
        "docker_exec",
        lambda ctx, script, **kwargs: scripts.append(script) or SimpleNamespace(ok=False, stdout=""),
    )
    monkeypatch.setattr(
        common,
        "write_container_file",
        lambda ctx, path, content, **kwargs: written.append({"path": path, "content": content, **kwargs}),
    )

    secret_path = common.ensure_workspace_secret_file(object(), "demo", "workspace:a-key")

    assert secret_path == "/workspaces/secrets/demo/.env"
    assert scripts == [
        "test -e /workspaces/secrets/demo/.env -o -e /workspaces/secrets/demo/.pgpass",
        "mkdir -p /workspaces/secrets/demo && chown root:demo /workspaces/secrets/demo"
        " && chmod 750 /workspaces/secrets/demo",
    ]
    assert [entry["path"] for entry in written] == [
        "/workspaces/secrets/demo/.env",
        "/workspaces/secrets/demo/.pgpass",
    ]
    assert written[0]["owner"] == "root:demo" and written[0]["mode"] == "640"
    # Readable by the workspace user alone, because libpq refuses a group-readable pgpass file.
    assert written[1]["owner"] == "demo:demo" and written[1]["mode"] == "600"
    assert "WORKSPACE_API_KEY=workspace:a-key" in written[0]["content"]


def test_ensure_workspace_secret_file_refuses_to_overwrite(monkeypatch) -> None:
    monkeypatch.setattr(common, "ensure_workspace_secret_root", lambda ctx: None)
    monkeypatch.setattr(
        common, "docker_exec", lambda ctx, script, **kwargs: SimpleNamespace(ok=True, stdout=""),
    )
    monkeypatch.setattr(
        common,
        "write_container_file",
        lambda *args, **kwargs: pytest.fail("wrote over existing secrets"),
    )

    with pytest.raises(RuntimeError, match="Refusing to overwrite existing workspace secrets"):
        common.ensure_workspace_secret_file(object(), "demo", "workspace:a-key")


def test_assert_container_workspace_absent_checks_the_volume_backed_paths(monkeypatch) -> None:
    checked: list[str] = []

    def fake_docker_exec(ctx, script, **kwargs):
        checked.append(script)
        # No such user, and none of the directories exist.
        return SimpleNamespace(ok=script.startswith("test !"), stdout="")

    monkeypatch.setattr(common, "docker_exec", fake_docker_exec)

    common.assert_container_workspace_absent(object(), "demo")

    assert checked == [
        "id -u demo",
        "test ! -e /home/demo",
        "test ! -e /workspaces/secrets/demo",
        "test ! -e /etc/ssh/authorized_keys/demo",
    ]


@pytest.mark.parametrize("occupied", ["/home/demo", "/workspaces/secrets/demo"])
def test_assert_container_workspace_absent_refuses_leftovers_in_the_volumes(monkeypatch, occupied) -> None:
    def fake_docker_exec(ctx, script, **kwargs):
        if script == "id -u demo":
            return SimpleNamespace(ok=False, stdout="")
        return SimpleNamespace(ok=script != f"test ! -e {occupied}", stdout="")

    monkeypatch.setattr(common, "docker_exec", fake_docker_exec)

    with pytest.raises(RuntimeError, match=f"{occupied} already exists"):
        common.assert_container_workspace_absent(object(), "demo")
