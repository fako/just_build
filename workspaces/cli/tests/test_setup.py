import importlib
from pathlib import Path


setup_cli = importlib.import_module("workspaces.cli.setup")


def test_include_check_passes_when_the_generated_config_is_included(tmp_path, monkeypatch, capsys) -> None:
    generated = tmp_path / "workspaces" / "ssh" / "config"
    user_config = tmp_path / "config"
    user_config.write_text(f"Include {generated}\n\nHost example\n    HostName example.com\n")
    monkeypatch.setattr(setup_cli, "SSH_CONFIG_PATH", generated)
    monkeypatch.setattr(setup_cli, "USER_SSH_CONFIG", user_config)

    assert setup_cli.check_ssh_config_include() is True
    assert capsys.readouterr().out == ""


def test_include_check_explains_what_to_add_when_it_is_missing(tmp_path, monkeypatch, capsys) -> None:
    generated = tmp_path / "workspaces" / "ssh" / "config"
    user_config = tmp_path / "config"
    user_config.write_text("Host example\n    HostName example.com\n")
    monkeypatch.setattr(setup_cli, "SSH_CONFIG_PATH", generated)
    monkeypatch.setattr(setup_cli, "USER_SSH_CONFIG", user_config)

    assert setup_cli.check_ssh_config_include() is False
    # Without the line, alias based tooling looks like a connection problem instead of a missing line.
    assert f"Include {generated}" in capsys.readouterr().out


def test_include_check_handles_a_missing_user_config(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(setup_cli, "SSH_CONFIG_PATH", tmp_path / "generated")
    monkeypatch.setattr(setup_cli, "USER_SSH_CONFIG", Path(tmp_path / "does-not-exist"))

    assert setup_cli.check_ssh_config_include() is False
