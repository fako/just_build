import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from workspaces.cli.client import WorkspaceRecord


init_cli = importlib.import_module("workspaces.cli.init")


class RecordingConnection:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.uploads: dict[str, str] = {}

    def run(self, command: str, echo: bool = False) -> None:
        self.commands.append(command)

    def put(self, local: str, remote: str) -> None:
        self.uploads[remote] = Path(local).read_text(encoding="utf-8")


class RecordingContext:
    def __init__(self) -> None:
        self.runs: list[dict[str, object]] = []
        self.config = SimpleNamespace(postgres=SimpleNamespace(user="postgres", password="postgres-password"))

    def run(self, command: str, *, env: dict[str, str], pty: bool, echo: bool) -> None:
        self.runs.append({"command": command, "env": env, "pty": pty, "echo": echo})


def workspace_record(slug: str = "demo", *, setup: dict[str, str] | None = None) -> WorkspaceRecord:
    return WorkspaceRecord(
        id="workspace-id",
        name="Demo Workspace",
        slug=slug,
        django_module="web",
        setup=setup or {},
        ssh=WorkspaceRecord.SSHConfig(),
    )


def test_template_output_path_strips_tpl_before_final_suffix() -> None:
    assert init_cli.template_output_path(Path("web/settings.tpl.py")) == Path("web/settings.py")
    assert init_cli.template_output_path(Path("web/settings.py")) == Path("web/settings.py")


def test_copy_template_files_traverses_directories_and_renders_templates(tmp_path, monkeypatch) -> None:
    source_dir = tmp_path / "default"
    (source_dir / "web" / "empty").mkdir(parents=True)
    (source_dir / "web" / "settings.tpl.py").write_text(
        'HOST = "{{ workspace.slug }}.localhost"\nNAME = "{{ slug }}"\n',
        encoding="utf-8",
    )
    (source_dir / "README.md").write_text("raw\n", encoding="utf-8")

    monkeypatch.setattr(init_cli, "template_dir", lambda template_name: source_dir)
    workspace = workspace_record()
    conn = RecordingConnection()

    init_cli.copy_template_files(conn, "/home/demo", "default", workspace)

    assert conn.uploads["/home/demo/web/settings.py"] == 'HOST = "demo.localhost"\nNAME = "demo"\n'
    assert conn.uploads["/home/demo/README.md"] == "raw\n"
    assert "test -d /home/demo/web || mkdir -p /home/demo/web" in conn.commands
    assert "test -d /home/demo/web/empty || mkdir -p /home/demo/web/empty" in conn.commands


def test_default_opencode_template_uses_workspace_reference_without_server_credentials() -> None:
    template_path = init_cli.TEMPLATES_DIR / "default" / "opencode.jsonc.tpl"

    rendered = init_cli.render_template_file(template_path, workspace_record())

    assert '"demo"' in rendered
    assert '"path": "/home/demo"' in rendered
    assert '"server"' not in rendered
    assert "password" not in rendered.lower()


def test_ensure_workspace_database_uses_generated_workspace_secrets(monkeypatch) -> None:
    monkeypatch.setattr(
        init_cli,
        "read_workspace_secret_environment",
        lambda workspace_slug: {
            "POSTGRES_DB": workspace_slug,
            "POSTGRES_USER": workspace_slug,
            "POSTGRES_PASSWORD": "workspace-password",
            "POSTGRES_HOST": "postgres",
            "POSTGRES_PORT": "5432",
        },
    )
    ctx = RecordingContext()

    init_cli.ensure_workspace_database(ctx, workspace_record())

    assert ctx.runs == [
        {
            "command": "./services/postgres/scripts/setup_database.sh",
            "env": {
                "DATABASE_NAME": "demo",
                "DATABASE_USER": "demo",
                "DATABASE_PASSWORD": "workspace-password",
                "POSTGRES_USER": "postgres",
                "PGPASSWORD": "postgres-password",
                "POSTGRES_DB": "postgres",
                "PGHOST": "postgres",
                "PGPORT": "5432",
            },
            "pty": True,
            "echo": True,
        }
    ]


def test_ensure_workspace_database_requires_password_secret(monkeypatch) -> None:
    monkeypatch.setattr(
        init_cli,
        "read_workspace_secret_environment",
        lambda workspace_slug: {"POSTGRES_DB": workspace_slug, "POSTGRES_USER": workspace_slug},
    )

    with pytest.raises(RuntimeError, match="POSTGRES_PASSWORD"):
        init_cli.ensure_workspace_database(RecordingContext(), workspace_record())
