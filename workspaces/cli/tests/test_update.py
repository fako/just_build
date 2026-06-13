import importlib
from types import SimpleNamespace

import pytest


update_cli = importlib.import_module("workspaces.cli.update")


class RecordingConnection:
    def __init__(self, *, has_pyproject: bool = True) -> None:
        self.has_pyproject = has_pyproject
        self.commands: list[dict[str, object]] = []

    def run(self, command: str, *, echo: bool = False, hide: bool = False, warn: bool = False):
        self.commands.append({"command": command, "echo": echo, "hide": hide, "warn": warn})
        if command.endswith("/pyproject.toml"):
            return SimpleNamespace(ok=self.has_pyproject)
        return SimpleNamespace(ok=True)


def test_install_pyproject_dependencies_creates_venv_and_installs_editable_project() -> None:
    conn = RecordingConnection()

    update_cli.install_pyproject_dependencies(conn, "/home/demo", "demo")

    assert conn.commands == [
        {"command": "test -f /home/demo/pyproject.toml", "echo": False, "hide": True, "warn": True},
        {
            "command": "cd /home/demo && python3 -m venv venv --copies --upgrade-deps",
            "echo": True,
            "hide": False,
            "warn": False,
        },
        {
            "command": "cd /home/demo && venv/bin/python -m pip install -e .",
            "echo": True,
            "hide": False,
            "warn": False,
        },
    ]


def test_install_pyproject_dependencies_requires_pyproject() -> None:
    conn = RecordingConnection(has_pyproject=False)

    with pytest.raises(RuntimeError, match="pyproject.toml"):
        update_cli.install_pyproject_dependencies(conn, "/home/demo", "demo")
