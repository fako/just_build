import importlib
from types import SimpleNamespace

from workspaces.cli.client import WorkspaceRecord


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


def test_grant_host_workspace_access_sets_access_and_inherited_acls(monkeypatch) -> None:
    calls: list[dict[str, object]] = []
    ctx = object()

    monkeypatch.setattr(
        common,
        "docker_exec",
        lambda received_ctx, script, **kwargs: calls.append({
            "ctx": received_ctx,
            "script": script,
            **kwargs,
        }),
    )

    common.grant_host_workspace_access(ctx, "datagrowth_django", host_uid=1000)

    assert calls == [{
        "ctx": ctx,
        "script": (
            "setfacl -R -m u:1000:rwX /home/datagrowth_django"
            " && find /home/datagrowth_django -type d -exec setfacl -m d:u:1000:rwX {} +"
        ),
        "user": "root",
    }]


def test_stage_workspace_configs_uses_module_for_runtime_and_slug_for_domain(tmp_path, monkeypatch) -> None:
    supervisor_template = tmp_path / "supervisor.template"
    supervisor_template.write_text("[program:PROJECT_NAME]\ndirectory=/home/PROJECT_NAME\n")
    nginx_template = tmp_path / "nginx.template"
    nginx_template.write_text("server_name PROJECT_DOMAIN;\nalias /home/PROJECT_NAME/staticfiles/;\n")
    staged_supervisor = tmp_path / "supervisor"
    staged_nginx = tmp_path / "nginx"

    monkeypatch.setattr(common, "SUPERVISOR_TEMPLATE_PATH", supervisor_template)
    monkeypatch.setattr(common, "NGINX_TEMPLATE_PATH", nginx_template)
    monkeypatch.setattr(common, "STAGED_SUPERVISOR_DIR", staged_supervisor)
    monkeypatch.setattr(common, "STAGED_NGINX_DIR", staged_nginx)
    monkeypatch.setattr(common, "next_workspace_port", lambda workspace_module: 8001)

    workspace = WorkspaceRecord(
        id="workspace-id",
        name="Data Growth Django",
        module="datagrowth_django",
        slug="datagrowth-django",
        django_module="web",
        setup={},
        ssh=WorkspaceRecord.SSHConfig(),
    )

    supervisor_path, nginx_path = common.stage_workspace_configs(
        workspace, f"{workspace.slug}.localhost"
    )

    assert supervisor_path == staged_supervisor / "datagrowth_django.conf"
    assert "[program:datagrowth_django]" in supervisor_path.read_text()
    assert "directory=/home/datagrowth_django" in supervisor_path.read_text()
    assert nginx_path == staged_nginx / "datagrowth_django.conf"
    assert "server_name datagrowth-django.localhost;" in nginx_path.read_text()
    assert "alias /home/datagrowth_django/staticfiles/;" in nginx_path.read_text()
