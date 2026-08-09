"""
Tests for tasks/install.py.

They live here rather than beside the task because this is the repository's only pytest run without
Django behind it, which is what these need: install runs before there is a database to talk to.
"""
import importlib

import pytest
from invoke.context import Context
from invoke.exceptions import Exit


install = importlib.import_module("tasks.install")


EXAMPLE = """\
# Comment kept as is
COMPOSE_PROFILES=workspaces
INVOKE_POSTGRES_PASSWORD=qwerty
INVOKE_WORKSPACES_SUPERVISOR_PASSWORD=qwerty
INVOKE_N8N_DATABASE_PASSWORD=qwerty
INVOKE_N8N_API_KEY=
INVOKE_MANAGEMENT_SECURITY_SECRET_KEY=
INVOKE_MANAGEMENT_SECURITY_API_KEY=control:00000000-0000-0000-0000-000000000000
INVOKE_MANAGEMENT_DATABASE_PASSWORD=qwerty
"""


@pytest.fixture
def repository(tmp_path, monkeypatch):
    (tmp_path / ".env.example").write_text(EXAMPLE, encoding="utf-8")
    monkeypatch.setattr(install, "_repository_root", lambda: tmp_path)
    return tmp_path


def environment_values(repository) -> dict[str, str]:
    return install._environment_values(repository / ".env")


def test_every_secret_gets_a_generated_value(repository):
    install.environment(Context())

    values = environment_values(repository)
    for name in install.ENVIRONMENT_GENERATORS:
        assert values[name] and values[name] != "qwerty", name
    # Non secrets and comments survive untouched.
    assert values["COMPOSE_PROFILES"] == "workspaces"
    assert "# Comment kept as is" in (repository / ".env").read_text()


def test_the_api_key_keeps_its_scheme(repository):
    install.environment(Context())

    assert environment_values(repository)["INVOKE_MANAGEMENT_SECURITY_API_KEY"].startswith("control:")


def test_an_existing_file_is_not_overwritten_without_a_flag(repository):
    (repository / ".env").write_text("INVOKE_POSTGRES_PASSWORD=keepme\n", encoding="utf-8")

    with pytest.raises(Exit, match="Refusing to overwrite"):
        install.environment(Context())


def test_force_rotates_every_secret(repository):
    install.environment(Context())
    original = environment_values(repository)

    install.environment(Context(), force=True)
    regenerated = environment_values(repository)

    for name in install.ENVIRONMENT_GENERATORS:
        assert regenerated[name] != original[name], name


def test_missing_fills_only_what_is_absent(repository, capsys):
    """
    The reason this flag exists: .env.example grows variables, and existing installations need them.

    Rotating the rest to get one new value would lock the file out of the postgres roles and
    containers that were built with the old ones.
    """
    install.environment(Context())
    original = environment_values(repository)
    # An installation whose .env predates a variable being added to the example.
    (repository / ".env").write_text(
        "\n".join(f"{name}={value}" for name, value in original.items()
                  if name != "INVOKE_N8N_DATABASE_PASSWORD") + "\n",
        encoding="utf-8",
    )

    install.environment(Context(), missing=True)

    filled = environment_values(repository)
    assert filled["INVOKE_N8N_DATABASE_PASSWORD"] not in ("", "qwerty")
    for name in install.ENVIRONMENT_GENERATORS:
        if name != "INVOKE_N8N_DATABASE_PASSWORD":
            assert filled[name] == original[name], name
    assert "Generated INVOKE_N8N_DATABASE_PASSWORD" in capsys.readouterr().out


def test_missing_keeps_a_value_that_was_chosen_by_hand(repository):
    """A value in the file is somebody's decision, secret or not, so it is never second-guessed."""
    install.environment(Context())
    (repository / ".env").write_text(
        "COMPOSE_PROFILES=control\nINVOKE_N8N_API_KEY=minted-by-hand\n", encoding="utf-8",
    )

    install.environment(Context(), missing=True)

    filled = environment_values(repository)
    assert filled["COMPOSE_PROFILES"] == "control"
    assert filled["INVOKE_N8N_API_KEY"] == "minted-by-hand"


def test_missing_on_a_fresh_installation_behaves_like_a_first_run(repository):
    install.environment(Context(), missing=True)

    assert environment_values(repository)["INVOKE_POSTGRES_PASSWORD"] != "qwerty"


def test_force_and_missing_contradict_each_other(repository):
    with pytest.raises(Exit, match="opposite things"):
        install.environment(Context(), force=True, missing=True)


def test_a_secret_with_no_generator_is_reported(repository, capsys):
    """The n8n API key cannot be generated, so the warning is the only thing that flags it as unset."""
    install.environment(Context())

    assert "INVOKE_N8N_API_KEY has no generator and no value" in capsys.readouterr().out


def test_a_secret_that_is_already_filled_is_not_warned_about(repository, capsys):
    install.environment(Context())
    (repository / ".env").write_text("INVOKE_N8N_API_KEY=minted-by-hand\n", encoding="utf-8")
    capsys.readouterr()

    install.environment(Context(), missing=True)

    assert "INVOKE_N8N_API_KEY" not in capsys.readouterr().out


@pytest.mark.parametrize("value,expected", [
    ("n8n", "'n8n'"),
    ("it's", "'it''s'"),
])
def test_sql_literals_are_quoted_for_sql_not_for_a_shell(value, expected):
    """shlex.quote leaves a bare word bare, which turns a database name into a column reference."""
    assert install._sql_literal(value) == expected
