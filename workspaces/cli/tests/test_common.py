import importlib


common = importlib.import_module("workspaces.cli.common")


def test_render_workspace_secret_env_includes_django_and_libpq_settings() -> None:
    content = common.render_workspace_secret_env("demo", "workspace-password")

    assert "POSTGRES_DB=demo\n" in content
    assert "POSTGRES_USER=demo\n" in content
    assert "POSTGRES_PASSWORD=workspace-password\n" in content
    assert "PGDATABASE=demo\n" in content
    assert "PGUSER=demo\n" in content
    assert "PGPASSFILE=/workspaces/secrets/demo/.pgpass\n" in content
    assert "OPENCODE_ATTACH_URL=http://127.0.0.1:4097\n" in content


def test_render_workspace_pgpass_uses_generated_postgres_credentials() -> None:
    assert common.render_workspace_pgpass("demo", "workspace-password") == (
        "postgres:5432:demo:demo:workspace-password\n"
    )
