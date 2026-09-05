import pytest
from django.utils import timezone

from access_control.models import Workspace
from runtimes.models import Runtime


@pytest.fixture
def workspace(db) -> Workspace:
    return Workspace.objects.create(name="Magic Match", module="magic_match")


@pytest.fixture
def django_runtime(workspace) -> Runtime:
    return Runtime.objects.create(workspace=workspace, type="django", name="web", port=8001, is_enabled=True)


@pytest.fixture
def celery_runtime(workspace) -> Runtime:
    return Runtime.objects.create(workspace=workspace, type="celery", name="worker", is_enabled=True)


@pytest.mark.django_db
def test_create_django_runtime_allocates_a_port(control_client, workspace):
    response = control_client.post(
        "/api/v1/runtimes/",
        data={"workspace_module": "magic_match", "type": "django", "name": "web"},
        content_type="application/json",
    )

    assert response.status_code == 201
    assert response.json()["port"] == 8001
    assert response.json()["program_name"] == "magic_match_web"
    assert response.json()["log_path"] == "/var/log/workspaces/magic_match/web.log"
    assert response.json()["is_enabled"] is False


@pytest.mark.django_db
def test_create_celery_runtime_takes_no_port(control_client, workspace):
    response = control_client.post(
        "/api/v1/runtimes/",
        data={"workspace_module": "magic_match", "type": "celery", "name": "worker"},
        content_type="application/json",
    )

    assert response.status_code == 201
    assert response.json()["port"] is None


@pytest.mark.django_db
def test_create_runtime_rejects_an_unknown_type(control_client, workspace):
    response = control_client.post(
        "/api/v1/runtimes/",
        data={"workspace_module": "magic_match", "type": "laravel", "name": "web"},
        content_type="application/json",
    )

    assert response.status_code == 422


@pytest.mark.django_db
def test_create_runtime_rejects_an_unknown_configuration_key(control_client, workspace):
    response = control_client.post(
        "/api/v1/runtimes/",
        data={
            "workspace_module": "magic_match", "type": "celery", "name": "worker",
            "configuration": {"concurrncy": 4},
        },
        content_type="application/json",
    )

    assert response.status_code == 422


@pytest.mark.django_db
def test_create_runtime_rejects_a_duplicate_name(control_client, django_runtime):
    response = control_client.post(
        "/api/v1/runtimes/",
        data={"workspace_module": "magic_match", "type": "celery", "name": "web"},
        content_type="application/json",
    )

    assert response.status_code == 409


@pytest.mark.django_db
def test_create_runtime_needs_an_existing_workspace(control_client, db):
    response = control_client.post(
        "/api/v1/runtimes/",
        data={"workspace_module": "nope", "type": "django", "name": "web"},
        content_type="application/json",
    )

    assert response.status_code == 404


@pytest.mark.django_db
def test_list_runtimes_filters_by_workspace(control_client, django_runtime, celery_runtime):
    other = Workspace.objects.create(name="Other", module="other")
    Runtime.objects.create(workspace=other, type="django", name="web", port=8002)

    response = control_client.get("/api/v1/runtimes/?workspace_module=magic_match")

    assert response.status_code == 200
    assert sorted(runtime["name"] for runtime in response.json()) == ["web", "worker"]


@pytest.mark.django_db
def test_patch_runtime_updates_configuration(control_client, django_runtime):
    response = control_client.patch(
        f"/api/v1/runtimes/{django_runtime.id}/",
        data={"configuration": {"workers": 4}},
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json()["configuration"] == {"workers": 4}


@pytest.mark.django_db
def test_patch_runtime_rejects_invalid_configuration(control_client, django_runtime):
    response = control_client.patch(
        f"/api/v1/runtimes/{django_runtime.id}/",
        data={"configuration": {"workers": 999}},
        content_type="application/json",
    )

    assert response.status_code == 422
    django_runtime.refresh_from_db()
    assert django_runtime.configuration == {}


@pytest.mark.django_db
def test_enable_and_disable_flip_the_flag(control_client, workspace):
    runtime = Runtime.objects.create(workspace=workspace, type="celery", name="worker", installed_at=timezone.now())

    assert control_client.post(f"/api/v1/runtimes/{runtime.id}/enable/").json()["is_enabled"] is True
    assert control_client.post(f"/api/v1/runtimes/{runtime.id}/disable/").json()["is_enabled"] is False


@pytest.mark.django_db
def test_enable_refuses_a_runtime_that_was_never_installed(control_client, workspace):
    runtime = Runtime.objects.create(workspace=workspace, type="celery", name="worker")

    response = control_client.post(f"/api/v1/runtimes/{runtime.id}/enable/")

    assert response.status_code == 409
    # The message is the command that fixes it, because that is the whole content of the failure.
    assert "runtimes.install" in response.json()["detail"]
    assert "--workspace-module=magic_match --name=worker" in response.json()["detail"]
    runtime.refresh_from_db()
    assert runtime.is_enabled is False


@pytest.mark.django_db
def test_marking_a_runtime_installed_lets_it_be_enabled(control_client, workspace):
    runtime = Runtime.objects.create(workspace=workspace, type="celery", name="worker")

    response = control_client.post(f"/api/v1/runtimes/{runtime.id}/installed/")

    assert response.status_code == 200
    assert response.json()["installed_at"] is not None
    assert control_client.post(f"/api/v1/runtimes/{runtime.id}/enable/").json()["is_enabled"] is True


@pytest.mark.django_db
def test_install_commands_build_the_virtualenv_before_syncing(control_client, django_runtime, celery_runtime):
    django_commands = control_client.get(f"/api/v1/runtimes/{django_runtime.id}/install-commands/").json()
    celery_commands = control_client.get(f"/api/v1/runtimes/{celery_runtime.id}/install-commands/").json()

    assert django_commands["directory"] == "/home/magic_match"
    assert django_commands["commands"] == [
        "test -f pyproject.toml || { echo 'No pyproject.toml to install from.' >&2; exit 1; }",
        "test -d venv || python3 -m venv venv --copies --upgrade-deps",
        "venv/bin/python -m pip install -e .",
        "venv/bin/python manage.py collectstatic --noinput",
    ]
    # A Celery worker serves no static files, so installing it stops at its dependencies.
    assert celery_commands["commands"][-1] == "venv/bin/python -m pip install -e ."


@pytest.mark.django_db
def test_delete_runtime(control_client, django_runtime):
    response = control_client.delete(f"/api/v1/runtimes/{django_runtime.id}/")

    assert response.status_code == 204
    assert not Runtime.objects.filter(pk=django_runtime.pk).exists()


@pytest.mark.django_db
def test_configs_manifest_covers_enabled_runtimes(control_client, django_runtime, celery_runtime):
    response = control_client.get("/api/v1/runtimes/configs/")

    assert response.status_code == 200
    assert response.json()["root"] == "workspaces/src"
    assert [config["path"] for config in response.json()["files"]] == [
        "nginx/magic_match/web.conf",
        "supervisor/magic_match/web.conf",
        "supervisor/magic_match/worker.conf",
    ]


@pytest.mark.django_db
def test_disabled_runtimes_drop_out_of_the_manifest(control_client, django_runtime, celery_runtime):
    control_client.post(f"/api/v1/runtimes/{celery_runtime.id}/disable/")

    response = control_client.get("/api/v1/runtimes/configs/")

    # The manifest is the complete desired state, so dropping out of it is how a file gets deleted.
    assert [config["path"] for config in response.json()["files"]] == [
        "nginx/magic_match/web.conf",
        "supervisor/magic_match/web.conf",
    ]


@pytest.mark.django_db
def test_single_runtime_manifest(control_client, django_runtime, celery_runtime):
    response = control_client.get(f"/api/v1/runtimes/{celery_runtime.id}/configs/")

    assert [config["path"] for config in response.json()["files"]] == ["supervisor/magic_match/worker.conf"]


@pytest.mark.django_db
def test_sync_commands_are_type_specific(control_client, django_runtime, celery_runtime):
    django_response = control_client.get(f"/api/v1/runtimes/{django_runtime.id}/sync-commands/")
    celery_response = control_client.get(f"/api/v1/runtimes/{celery_runtime.id}/sync-commands/")

    assert django_response.json()["directory"] == "/home/magic_match"
    assert "venv/bin/python manage.py collectstatic --noinput" in django_response.json()["commands"]
    assert "collectstatic" not in " ".join(celery_response.json()["commands"])


@pytest.mark.django_db
def test_restart_drives_supervisord(control_client, supervisor, django_runtime):
    supervisor.add_program("magic_match_web", state="STOPPED")

    response = control_client.post(f"/api/v1/runtimes/{django_runtime.id}/restart/")

    assert response.status_code == 200
    assert response.json()["state"] == "RUNNING"
    assert ("restart", "magic_match_web") in supervisor.calls


@pytest.mark.django_db
def test_start_stop_and_status(control_client, supervisor, django_runtime):
    supervisor.add_program("magic_match_web", state="STOPPED")

    assert control_client.post(f"/api/v1/runtimes/{django_runtime.id}/start/").json()["state"] == "RUNNING"
    assert control_client.post(f"/api/v1/runtimes/{django_runtime.id}/stop/").json()["state"] == "STOPPED"
    assert control_client.get(f"/api/v1/runtimes/{django_runtime.id}/status/").json()["state"] == "STOPPED"


@pytest.mark.django_db
def test_controlling_a_runtime_supervisord_has_not_seen_yet(control_client, supervisor, django_runtime):
    response = control_client.post(f"/api/v1/runtimes/{django_runtime.id}/restart/")

    assert response.status_code == 409
    assert "Enable the runtime" in response.json()["detail"]


@pytest.mark.django_db
def test_logs_read_through_supervisord(control_client, supervisor, django_runtime):
    supervisor.add_program("magic_match_web", log="hello from the runtime")

    response = control_client.get(f"/api/v1/runtimes/{django_runtime.id}/logs/?offset=6")

    assert response.json() == {"program_name": "magic_match_web", "content": "from the runtime"}


@pytest.mark.django_db
def test_reload_updates_supervisord_and_reloads_nginx(control_client, supervisor):
    supervisor.add_program("nginx")

    response = control_client.post("/api/v1/runtimes/reload/")

    assert response.status_code == 200
    assert ("update",) in supervisor.calls
    # nginx runs under supervisord, so reloading it needs no docker exec either.
    assert ("signal", "nginx", "HUP") in supervisor.calls


@pytest.mark.django_db
def test_create_runtime_can_claim_the_workspace_name(control_client, workspace):
    response = control_client.post(
        "/api/v1/runtimes/",
        data={"workspace_module": "magic_match", "type": "django", "name": "web", "is_primary": True},
        content_type="application/json",
    )

    assert response.status_code == 201
    assert response.json()["is_primary"] is True


@pytest.mark.django_db
def test_create_runtime_rejects_a_second_primary(control_client, workspace, django_runtime):
    django_runtime.is_primary = True
    django_runtime.save()

    response = control_client.post(
        "/api/v1/runtimes/",
        data={"workspace_module": "magic_match", "type": "django", "name": "admin", "is_primary": True},
        content_type="application/json",
    )

    # A 409 naming the real collision, rather than the "already has a runtime called" message that
    # the other unique constraint on this table produces.
    assert response.status_code == 409
    assert "already has a primary runtime" in response.json()["detail"]


@pytest.mark.django_db
def test_create_runtime_refuses_a_primary_that_serves_no_http(control_client, workspace):
    response = control_client.post(
        "/api/v1/runtimes/",
        data={"workspace_module": "magic_match", "type": "celery", "name": "worker", "is_primary": True},
        content_type="application/json",
    )

    assert response.status_code == 422
    assert "serves no HTTP" in response.json()["detail"]


@pytest.mark.django_db
def test_patch_leaves_primary_alone_unless_it_is_named(control_client, django_runtime):
    django_runtime.is_primary = True
    django_runtime.save()

    response = control_client.patch(
        f"/api/v1/runtimes/{django_runtime.pk}/",
        data={"module": "portal"},
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json()["is_primary"] is True


@pytest.mark.django_db
def test_patch_can_hand_the_workspace_name_over(control_client, workspace, django_runtime):
    django_runtime.is_primary = True
    django_runtime.save()
    admin = Runtime.objects.create(workspace=workspace, type="django", name="admin", port=8002)

    cleared = control_client.patch(
        f"/api/v1/runtimes/{django_runtime.pk}/",
        data={"is_primary": False},
        content_type="application/json",
    )
    claimed = control_client.patch(
        f"/api/v1/runtimes/{admin.pk}/",
        data={"is_primary": True},
        content_type="application/json",
    )

    assert cleared.json()["is_primary"] is False
    assert claimed.json()["is_primary"] is True
