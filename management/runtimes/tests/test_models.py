import pytest
from django.core.exceptions import ValidationError
from django.db.utils import IntegrityError

from access_control.models import Workspace
from runtimes.models import CeleryRuntime, DjangoRuntime, HttpRuntime, Runtime, SupervisordRuntime
from runtimes.models.base import UnknownRuntimeType, runtime_class_for


@pytest.fixture
def workspace(db) -> Workspace:
    return Workspace.objects.create(name="Magic Match", module="magic_match")


@pytest.fixture
def django_runtime(workspace) -> Runtime:
    return Runtime.objects.create(workspace=workspace, type="django", name="web", port=8001)


@pytest.fixture
def celery_runtime(workspace) -> Runtime:
    return Runtime.objects.create(workspace=workspace, type="celery", name="worker")


@pytest.mark.django_db
def test_specialize_returns_the_registered_class(django_runtime, celery_runtime):
    assert isinstance(django_runtime.specialize(), DjangoRuntime)
    assert isinstance(celery_runtime.specialize(), CeleryRuntime)


@pytest.mark.django_db
def test_specialize_keeps_the_row_it_came_from(django_runtime):
    specialized = django_runtime.specialize()

    assert specialized.pk == django_runtime.pk
    assert specialized.configuration == django_runtime.configuration
    assert specialized._state.db == django_runtime._state.db


@pytest.mark.django_db
def test_specialize_is_idempotent(django_runtime):
    specialized = django_runtime.specialize()

    assert specialized.specialize() is specialized


@pytest.mark.django_db
def test_the_hierarchy_places_each_type(django_runtime, celery_runtime):
    django = django_runtime.specialize()
    celery = celery_runtime.specialize()

    assert isinstance(django, HttpRuntime)
    assert isinstance(django, SupervisordRuntime)
    assert isinstance(celery, SupervisordRuntime)
    # Celery serves no HTTP, which is what keeps it from rendering an nginx block or holding a port.
    assert not isinstance(celery, HttpRuntime)


def test_unknown_runtime_type_names_the_known_ones():
    with pytest.raises(UnknownRuntimeType) as error:
        runtime_class_for("laravel")

    assert "celery" in str(error.value)
    assert "django" in str(error.value)


@pytest.mark.django_db
def test_program_name_is_unique_across_workspaces(workspace, django_runtime):
    other = Workspace.objects.create(name="Other", module="other")
    other_runtime = Runtime.objects.create(workspace=other, type="django", name="web", port=8002)

    assert django_runtime.program_name == "magic_match_web"
    assert other_runtime.program_name == "other_web"


@pytest.mark.django_db
def test_log_path_is_nested_per_workspace(django_runtime, celery_runtime):
    assert django_runtime.log_path == "/var/log/workspaces/magic_match/web.log"
    assert celery_runtime.log_path == "/var/log/workspaces/magic_match/worker.log"


@pytest.mark.django_db
def test_runtime_names_are_unique_within_a_workspace(workspace, django_runtime):
    from django.db import IntegrityError

    with pytest.raises(IntegrityError):
        Runtime.objects.create(workspace=workspace, type="celery", name="web")


@pytest.mark.django_db
def test_allocate_port_skips_taken_ports(django_runtime, workspace):
    Runtime.objects.create(workspace=workspace, type="django", name="admin", port=8002)

    assert Runtime.allocate_port() == 8003


@pytest.mark.django_db
def test_allocate_port_ignores_runtimes_without_one(celery_runtime):
    assert Runtime.allocate_port() == 8001


@pytest.mark.django_db
def test_django_command_renders_uvicorn(django_runtime):
    runtime = django_runtime.specialize()

    assert runtime.command == (
        "/home/magic_match/venv/bin/python -m uvicorn web.asgi:application"
        " --host 127.0.0.1 --port 8001 --workers 2 --loop uvloop --http httptools"
    )


@pytest.mark.django_db
def test_module_drives_the_django_settings_and_asgi_modules(workspace):
    runtime = Runtime.objects.create(
        workspace=workspace, type="django", name="web", port=8001, module="portal",
        configuration={"workers": 4},
    ).specialize()

    assert runtime.asgi_module == "portal.asgi"
    assert "--workers 4" in runtime.command
    assert runtime.environment()["DJANGO_SETTINGS_MODULE"] == "portal.settings"


@pytest.mark.django_db
def test_module_drives_the_celery_app(workspace):
    runtime = Runtime.objects.create(
        workspace=workspace, type="celery", name="worker", module="portal",
    ).specialize()

    assert "--app portal worker" in runtime.command
    assert runtime.environment()["DJANGO_SETTINGS_MODULE"] == "portal.settings"


@pytest.mark.django_db
def test_module_defaults_to_web(workspace, django_runtime):
    assert django_runtime.module == "web"


@pytest.mark.django_db
def test_two_runtimes_in_one_workspace_can_run_different_modules(workspace):
    web = Runtime.objects.create(
        workspace=workspace, type="django", name="web", port=8001, module="portal",
    ).specialize()
    worker = Runtime.objects.create(
        workspace=workspace, type="celery", name="worker", module="tasks",
    ).specialize()

    # The module belongs to the runtime, so one workspace is not limited to one project.
    assert web.environment()["DJANGO_SETTINGS_MODULE"] == "portal.settings"
    assert worker.environment()["DJANGO_SETTINGS_MODULE"] == "tasks.settings"


@pytest.mark.django_db
def test_asgi_module_can_still_be_overridden(workspace):
    runtime = Runtime.objects.create(
        workspace=workspace, type="django", name="web", port=8001, module="portal",
        configuration={"asgi_module": "portal.custom_asgi"},
    ).specialize()

    assert runtime.asgi_module == "portal.custom_asgi"


@pytest.mark.django_db
def test_internal_domain_is_derived_from_workspace_and_runtime(django_runtime):
    assert django_runtime.specialize().internal_domain == "magic-match.web.localhost"


@pytest.mark.django_db
def test_server_names_hold_the_internal_name_until_a_runtime_is_primary(django_runtime):
    runtime = django_runtime.specialize()

    assert runtime.server_names == ["magic-match.web.localhost"]

    runtime.is_primary = True

    assert runtime.server_names == ["magic-match.web.localhost", "magic-match.localhost"]


@pytest.mark.django_db
def test_a_workspace_cannot_have_two_primary_runtimes(workspace, django_runtime):
    django_runtime.is_primary = True
    django_runtime.save()

    with pytest.raises(IntegrityError):
        Runtime.objects.create(
            workspace=workspace, type="django", name="admin", port=8002, is_primary=True,
        )


@pytest.mark.django_db
def test_a_runtime_without_http_cannot_be_primary(celery_runtime):
    celery_runtime.is_primary = True

    with pytest.raises(ValidationError, match="serves no HTTP"):
        celery_runtime.clean()


@pytest.mark.django_db
def test_celery_command_renders_a_worker(celery_runtime):
    runtime = celery_runtime.specialize()

    assert runtime.command == (
        "/home/magic_match/venv/bin/python -m celery --app web worker"
        " --loglevel INFO --concurrency 2"
    )


@pytest.mark.django_db
def test_celery_command_takes_queues_and_beat(workspace):
    runtime = Runtime.objects.create(
        workspace=workspace, type="celery", name="worker",
        configuration={"queues": ["default", "slow"], "concurrency": 8, "beat": True, "loglevel": "DEBUG"},
    ).specialize()

    assert runtime.command == (
        "/home/magic_match/venv/bin/python -m celery --app web worker"
        " --loglevel DEBUG --concurrency 8 --queues default,slow --beat"
    )


@pytest.mark.django_db
def test_environment_carries_the_workspace_python_path(django_runtime):
    assert django_runtime.specialize().environment() == {
        "DJANGO_SETTINGS_MODULE": "web.settings",
        "PYTHONPATH": "/home/magic_match",
    }


@pytest.mark.django_db
def test_django_settings_module_is_not_something_every_runtime_type_inherits(workspace):
    """
    The base type stays out of Django.

    Both current types boot a Django project so both get the variable, but they get it from a mixin
    rather than from SupervisordRuntime, which is what keeps a future Node or Laravel runtime from
    inheriting a setting it has no use for.
    """
    runtime = Runtime.objects.create(workspace=workspace, type="celery", name="worker")
    supervisord = SupervisordRuntime.objects.get(pk=runtime.pk)

    assert "DJANGO_SETTINGS_MODULE" not in supervisord.default_environment()
    assert "DJANGO_SETTINGS_MODULE" in runtime.specialize().default_environment()


@pytest.mark.django_db
def test_configured_environment_wins_over_what_the_type_derived(workspace):
    runtime = Runtime.objects.create(
        workspace=workspace, type="celery", name="worker",
        configuration={"environment": {"DJANGO_SETTINGS_MODULE": "web.settings_local"}},
    ).specialize()

    assert runtime.environment()["DJANGO_SETTINGS_MODULE"] == "web.settings_local"


@pytest.mark.django_db
def test_environment_accepts_extra_variables(workspace):
    runtime = Runtime.objects.create(
        workspace=workspace, type="celery", name="worker",
        configuration={"environment": {"CELERY_QUEUE": "magic_match"}},
    ).specialize()

    assert runtime.environment()["CELERY_QUEUE"] == "magic_match"
    assert runtime.environment()["PYTHONPATH"] == "/home/magic_match"


@pytest.mark.django_db
def test_sync_commands_are_type_specific(django_runtime, celery_runtime):
    # collectstatic is a Django concern and must not leak into every runtime type.
    assert django_runtime.specialize().sync_commands() == [
        "venv/bin/python -m pip install -e .",
        "venv/bin/python manage.py collectstatic --noinput",
    ]
    assert celery_runtime.specialize().sync_commands() == [
        "venv/bin/python -m pip install -e .",
    ]


@pytest.mark.django_db
def test_clean_rejects_an_unknown_type(workspace):
    runtime = Runtime(workspace=workspace, type="laravel", name="web")

    with pytest.raises(ValidationError) as error:
        runtime.clean()

    assert "type" in error.value.message_dict


@pytest.mark.django_db
def test_clean_rejects_unknown_configuration_keys(workspace):
    runtime = Runtime(workspace=workspace, type="celery", name="worker", configuration={"concurrncy": 4})

    with pytest.raises(ValidationError) as error:
        runtime.clean()

    assert "configuration" in error.value.message_dict


@pytest.mark.django_db
def test_clean_requires_a_port_for_http_runtimes(workspace):
    runtime = Runtime(workspace=workspace, type="django", name="web")

    with pytest.raises(ValidationError) as error:
        runtime.specialize().clean()

    assert "port" in error.value.message_dict


@pytest.mark.django_db
def test_clean_refuses_a_port_on_celery_runtimes(workspace):
    runtime = Runtime(workspace=workspace, type="celery", name="worker", port=8005)

    with pytest.raises(ValidationError) as error:
        runtime.specialize().clean()

    assert "port" in error.value.message_dict


@pytest.mark.django_db
def test_queryset_helpers(workspace, django_runtime, celery_runtime):
    django_runtime.is_enabled = True
    django_runtime.save(update_fields=["is_enabled"])

    assert [runtime.name for runtime in Runtime.objects.enabled()] == ["web"]
    assert all(type(runtime) is not Runtime for runtime in Runtime.objects.all().specialized())


@pytest.mark.django_db
def test_runtimes_are_reachable_from_their_workspace(workspace, django_runtime, celery_runtime):
    assert set(workspace.runtimes.values_list("name", flat=True)) == {"web", "worker"}


@pytest.mark.django_db
def test_deleting_a_workspace_deletes_its_runtimes(workspace, django_runtime):
    workspace.delete()

    assert not Runtime.objects.exists()
