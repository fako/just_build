import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from workspaces.cli.client import WorkspaceRecord


scaffold_cli = importlib.import_module("workspaces.cli.scaffold")
common_cli = importlib.import_module("workspaces.cli.common")


class RecordingConnection:
    def __init__(self, ok: bool = False) -> None:
        self.ok = ok
        self.commands: list[str] = []
        self.uploads: dict[str, str] = {}

    def run(self, command: str, echo: bool = False, hide: bool = False, warn: bool = False):
        self.commands.append(command)
        return SimpleNamespace(ok=self.ok)

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
        setup=setup or {},
        ssh=WorkspaceRecord.SSHConfig(),
    )


def test_template_output_path_strips_tpl_before_final_suffix() -> None:
    assert scaffold_cli.template_output_path(Path("web/settings.tpl.py")) == Path("web/settings.py")
    assert scaffold_cli.template_output_path(Path("web/settings.py")) == Path("web/settings.py")


def test_scaffold_can_skip_git_setup(monkeypatch) -> None:
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

    monkeypatch.setattr(scaffold_cli, "get_workspace", lambda workspace_module: workspace)
    monkeypatch.setattr(scaffold_cli, "build_ssh_connection", lambda received_workspace: conn)
    monkeypatch.setattr(
        scaffold_cli,
        "ensure_git_repo",
        lambda *args: pytest.fail("git repository initialized with git=False"),
    )
    monkeypatch.setattr(
        scaffold_cli,
        "ensure_initial_commit",
        lambda *args: pytest.fail("initial commit attempted with git=False"),
    )
    monkeypatch.setattr(scaffold_cli, "ensure_django_project", lambda *args: False)
    monkeypatch.setattr(scaffold_cli, "copy_workspace_templates", lambda *args: ("default",))
    monkeypatch.setattr(
        scaffold_cli,
        "log_setup_step",
        lambda workspace_module, step: setup_steps.append(step),
    )

    scaffold_cli.scaffold.body(RecordingContext(), "demo", git=False)

    assert setup_steps == ["templates_resolved"]


def scaffold_workspace() -> WorkspaceRecord:
    return workspace_record(
        setup={
            "workspace_created": "now",
            "home_created": "now",
            "secrets_created": "now",
            "ssh_access": "now",
            "database_created": "now",
        }
    )


def test_scaffold_refuses_a_home_that_already_holds_a_repository(monkeypatch) -> None:
    # An ok connection answers `test -d /home/demo/.git` with a repository that is already there.
    monkeypatch.setattr(scaffold_cli, "get_workspace", lambda workspace_module: scaffold_workspace())
    monkeypatch.setattr(scaffold_cli, "build_ssh_connection", lambda received_workspace: RecordingConnection(ok=True))
    monkeypatch.setattr(
        scaffold_cli, "copy_workspace_templates",
        lambda *args: pytest.fail("templates written over an existing repository"),
    )

    with pytest.raises(RuntimeError, match="already has a git repository"):
        scaffold_cli.scaffold.body(RecordingContext(), "demo")


def test_scaffold_checks_for_a_repository_even_without_git(monkeypatch) -> None:
    """A repository means there is a project there, whether or not this run would touch git."""
    monkeypatch.setattr(scaffold_cli, "get_workspace", lambda workspace_module: scaffold_workspace())
    monkeypatch.setattr(scaffold_cli, "build_ssh_connection", lambda received_workspace: RecordingConnection(ok=True))

    with pytest.raises(RuntimeError, match="already has a git repository"):
        scaffold_cli.scaffold.body(RecordingContext(), "demo", git=False)


def test_copy_template_files_traverses_directories_and_renders_templates(tmp_path, monkeypatch) -> None:
    source_dir = tmp_path / "default"
    (source_dir / "web" / "empty").mkdir(parents=True)
    (source_dir / "web" / "settings.tpl.py").write_text(
        'HOST = "{{ workspace.slug }}.localhost"\nNAME = "{{ module }}"\nPACKAGE = "{{ runtime_module }}"\n',
        encoding="utf-8",
    )
    (source_dir / "README.md").write_text("raw\n", encoding="utf-8")

    monkeypatch.setattr(scaffold_cli, "template_dir", lambda template_name: source_dir)
    workspace = workspace_record("demo_module")
    conn = RecordingConnection()

    scaffold_cli.copy_template_files(conn, "/home/demo_module", "default", workspace, "portal")

    # runtime_module comes from the scaffold argument rather than from the workspace, because no
    # runtime exists yet to ask.
    assert conn.uploads["/home/demo_module/web/settings.py"] == (
        'HOST = "demo-module.localhost"\nNAME = "demo_module"\nPACKAGE = "portal"\n'
    )
    assert conn.uploads["/home/demo_module/README.md"] == "raw\n"
    assert "test -d /home/demo_module/web || mkdir -p /home/demo_module/web" in conn.commands
    assert "test -d /home/demo_module/web/empty || mkdir -p /home/demo_module/web/empty" in conn.commands


def test_default_opencode_template_uses_workspace_reference_without_server_credentials() -> None:
    template_path = scaffold_cli.TEMPLATES_DIR / "default" / "opencode.tpl.jsonc"

    rendered = scaffold_cli.render_template_file(template_path, workspace_record(), "web")

    assert '"demo"' in rendered
    assert '"path": "/home/demo"' in rendered
    assert '"server"' not in rendered
    assert "password" not in rendered.lower()


def test_compiled_python_is_never_scaffolded(tmp_path, monkeypatch) -> None:
    """
    Templates hold real modules, and importing one leaves bytecode beside it.

    Uploading that into a workspace ships binaries as if they were source, and the read fails on the
    first byte that is not UTF-8, so it is skipped at the source.
    """
    source_dir = tmp_path / "n8n"
    (source_dir / "__pycache__").mkdir(parents=True)
    (source_dir / "__pycache__" / "tasks.cpython-312.pyc").write_bytes(b"\xcb\x0d\x0d\x0a")
    (source_dir / "tasks.py").write_text("namespace = None\n", encoding="utf-8")

    monkeypatch.setattr(scaffold_cli, "template_dir", lambda template_name: source_dir)
    conn = RecordingConnection()

    scaffold_cli.copy_template_files(conn, "/home/demo", "n8n", workspace_record(), "web")

    assert list(conn.uploads) == ["/home/demo/tasks.py"]
    assert not any("__pycache__" in command for command in conn.commands)


def test_n8n_template_lays_out_a_flat_workflows_directory() -> None:
    conn = RecordingConnection()

    scaffold_cli.copy_template_files(conn, "/home/demo", "n8n", workspace_record(), "web")

    assert "/home/demo/n8n/workflows/.gitkeep" in conn.uploads
    assert "/home/demo/n8n/tasks.py" in conn.uploads
    assert "/home/demo/n8n/AGENT.md" in conn.uploads
    # Credentials stay out of the workspace entirely, which is easier to keep true when there is
    # nowhere obvious to put them.
    assert not any("credential" in path for path in conn.uploads)


def test_n8n_template_replaces_the_root_task_namespace() -> None:
    """The default template writes an empty one, so this overwrites rather than introduces."""
    conn = RecordingConnection()

    scaffold_cli.copy_template_files(conn, "/home/demo", "default", workspace_record(), "web")
    scaffold_cli.copy_template_files(conn, "/home/demo", "n8n", workspace_record(), "web")

    assert "n8n.tasks" in conn.uploads["/home/demo/tasks.py"]


def test_n8n_client_template_reaches_management_as_the_workspace() -> None:
    template_path = scaffold_cli.TEMPLATES_DIR / "n8n" / "n8n" / "client.tpl.py"

    rendered = scaffold_cli.render_template_file(template_path, workspace_record(), "web")

    assert "/workspaces/secrets/demo/.env" in rendered
    assert "WORKSPACE_API_KEY" in rendered
    # The workspace goes through management, never straight at n8n: the tag registry management holds
    # is the only thing keeping one workspace out of another's workflows.
    assert "5678" not in rendered
    assert "X-N8N-API-KEY" not in rendered


def test_default_template_carries_what_the_task_templates_need() -> None:
    """Layering replaces pyproject.toml wholesale, so the shared dependencies belong in every copy."""
    for template_name in ("default", "celery"):
        template_path = scaffold_cli.TEMPLATES_DIR / template_name / "pyproject.tpl.toml"
        rendered = scaffold_cli.render_template_file(template_path, workspace_record(), "web")

        assert "invoke==" in rendered, template_name
        assert "requests==" in rendered, template_name


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
