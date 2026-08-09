import importlib


create_cli = importlib.import_module("workspaces.cli.create")


def test_prompt_key_password_does_not_ask_for_a_password_it_was_given(monkeypatch) -> None:
    monkeypatch.setattr(create_cli, "getpass", lambda prompt: "should not be asked")

    assert create_cli.prompt_key_password("given-password") == "given-password"


def test_prompt_key_password_asks_again_until_both_answers_match(monkeypatch) -> None:
    answers = iter(["first", "second", "same", "same"])
    monkeypatch.setattr(create_cli, "getpass", lambda prompt: next(answers))

    assert create_cli.prompt_key_password(None) == "same"


def test_prompt_key_password_allows_an_unprotected_key(monkeypatch, capsys) -> None:
    monkeypatch.setattr(create_cli, "getpass", lambda prompt: "")

    assert create_cli.prompt_key_password(None) == ""
    assert "unprotected" in capsys.readouterr().out
