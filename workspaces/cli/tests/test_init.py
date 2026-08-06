import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from workspaces.cli.client import WorkspaceRecord


init_cli = importlib.import_module("workspaces.cli.init")
common_cli = importlib.import_module("workspaces.cli.common")


class RecordingConnection:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.uploads: dict[str, str] = {}

    def run(self, command: str, echo: bool = False, hide: bool = False, warn: bool = False):
        self.commands.append(command)
        return SimpleNamespace(ok=False)

    def put(self, local: str, remote: str) -> None:
        self.uploads[remote] = Path(local).read_text(encoding="utf-8")


class RecordingContext:
    def __init__(self) -> None:
        self.runs: list[dict[str, object]] = []
        self.config = SimpleNamespace(postgres=SimpleNamespace(user="postgres", password="postgres-password"))

    def run(self, command: str, *, env: dict[str, str], pty: bool, echo: bool) -> None:
        self.runs.append({"command": command, "env": env, "pty": pty, "echo": echo})


def workspace_record(module: str = "demo", *, slug: str | None = None,
                     setup: dict[str, str] | None = None) -> WorkspaceRecord:
    return WorkspaceRecord(
        id="workspace-id",
        name="Demo Workspace",
        module=module,
        slug=slug or module.replace("_", "-"),
        django_module="web",
        setup=setup or {},
        ssh=WorkspaceRecord.SSHConfig(),
    )


def test_template_output_path_strips_tpl_before_final_suffix() -> None:
    assert init_cli.template_output_path(Path("web/settings.tpl.py")) == Path("web/settings.py")
    assert init_cli.template_output_path(Path("web/settings.py")) == Path("web/settings.py")


def test_ensure_git_repo_initializes_main_branch() -> None:
    conn = RecordingConnection()

    init_cli.ensure_git_repo(conn, "/home/demo", "Demo Workspace", "demo")

    assert conn.commands == [
        "test -d /home/demo/.git",
        "git init -b main /home/demo",
        "git -C /home/demo config user.name 'Demo Workspace'",
        "git -C /home/demo config user.email demo@workspace.local",
    ]


def test_init_can_skip_git_setup(monkeypatch) -> None:
    workspace = workspace_record(
        setup={
            "workspace_created": "now",
            "home_created": "now",
            "secrets_created": "now",
            "ssh_access": "now",
            "database_created": "now",
        }
    )
    conn = RecordingConnection()
    setup_steps: list[str] = []

    monkeypatch.setattr(init_cli, "get_workspace", lambda workspace_module: workspace)
    monkeypatch.setattr(init_cli, "build_ssh_connection", lambda received_workspace: conn)
    monkeypatch.setattr(
        init_cli,
        "ensure_git_repo",
        lambda *args: pytest.fail("git repository initialized with git=False"),
    )
    monkeypatch.setattr(
        init_cli,
        "ensure_initial_commit",
        lambda *args: pytest.fail("initial commit attempted with git=False"),
    )
    monkeypatch.setattr(init_cli, "ensure_django_project", lambda *args: False)
    monkeypatch.setattr(init_cli, "copy_workspace_templates", lambda *args: ("default",))
    monkeypatch.setattr(
        init_cli,
        "log_setup_step",
        lambda workspace_module, step: setup_steps.append(step),
    )

    init_cli.init.body(RecordingContext(), "demo", git=False)

    assert setup_steps == ["templates_resolved"]


def test_copy_template_files_traverses_directories_and_renders_templates(tmp_path, monkeypatch) -> None:
    source_dir = tmp_path / "default"
    (source_dir / "web" / "empty").mkdir(parents=True)
    (source_dir / "web" / "settings.tpl.py").write_text(
        'HOST = "{{ workspace.slug }}.localhost"\nNAME = "{{ module }}"\n',
        encoding="utf-8",
    )
    (source_dir / "README.md").write_text("raw\n", encoding="utf-8")

    monkeypatch.setattr(init_cli, "template_dir", lambda template_name: source_dir)
    workspace = workspace_record("demo_module")
    conn = RecordingConnection()

    init_cli.copy_template_files(conn, "/home/demo_module", "default", workspace)

    assert conn.uploads["/home/demo_module/web/settings.py"] == (
        'HOST = "demo-module.localhost"\nNAME = "demo_module"\n'
    )
    assert conn.uploads["/home/demo_module/README.md"] == "raw\n"
    assert "test -d /home/demo_module/web || mkdir -p /home/demo_module/web" in conn.commands
    assert "test -d /home/demo_module/web/empty || mkdir -p /home/demo_module/web/empty" in conn.commands


def test_default_opencode_template_uses_workspace_reference_without_server_credentials() -> None:
    template_path = init_cli.TEMPLATES_DIR / "default" / "opencode.tpl.jsonc"

    rendered = init_cli.render_template_file(template_path, workspace_record())

    assert '"demo"' in rendered
    assert '"path": "/home/demo"' in rendered
    assert '"server"' not in rendered
    assert "password" not in rendered.lower()


def test_ensure_workspace_database_uses_generated_workspace_secrets(monkeypatch) -> None:
    monkeypatch.setattr(
        common_cli,
        "read_workspace_secret_environment",
        lambda ctx, workspace_module: {
            "POSTGRES_DB": workspace_module,
            "POSTGRES_USER": workspace_module,
            "POSTGRES_PASSWORD": "workspace-password",
            "POSTGRES_HOST": "postgres",
            "POSTGRES_PORT": "5432",
        },
    )
    ctx = RecordingContext()

    common_cli.ensure_workspace_database(ctx, workspace_record())

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
        common_cli,
        "read_workspace_secret_environment",
        lambda ctx, workspace_module: {"POSTGRES_DB": workspace_module, "POSTGRES_USER": workspace_module},
    )

    with pytest.raises(RuntimeError, match="POSTGRES_PASSWORD"):
        common_cli.ensure_workspace_database(RecordingContext(), workspace_record())
