import importlib
from pathlib import Path

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
    workspace = WorkspaceRecord(
        id="workspace-id",
        name="Demo Workspace",
        slug="demo",
        django_module="web",
        setup={},
        ssh=WorkspaceRecord.SSHConfig(),
    )
    conn = RecordingConnection()

    init_cli.copy_template_files(conn, "/home/demo", "default", workspace)

    assert conn.uploads["/home/demo/web/settings.py"] == 'HOST = "demo.localhost"\nNAME = "demo"\n'
    assert conn.uploads["/home/demo/README.md"] == "raw\n"
    assert "test -d /home/demo/web || mkdir -p /home/demo/web" in conn.commands
    assert "test -d /home/demo/web/empty || mkdir -p /home/demo/web/empty" in conn.commands
