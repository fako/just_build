"""
The runtime API.

Every route resolves its object through `request.auth.runtimes()`, the queryset the principal is
scoped to. That is what keeps one workspace out of another's runtimes: an out-of-scope id is a 404,
and there is no unscoped manager access in this module for anyone to reach for by accident.

Routes split along one line. Anything that changes the host filesystem is control-only, because it
assumes the invoke CLI is there to write what management renders. Anything that only changes process
state is open to the workspace that owns the runtime.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import HttpRequest
from django.utils import timezone
from ninja import Router, Schema
from ninja.errors import HttpError

from access_control.authentication import control_api_key_auth
from access_control.models import Workspace
from runtimes.configs import CONFIG_ROOT, build_manifest
from runtimes.models import HttpRuntime, Runtime
from runtimes.models.base import UnknownRuntimeType, runtime_class_for
from runtimes.supervisor import SupervisorError, UnknownProgram, get_supervisor_client


controller = Router()

NGINX_PROGRAM = "nginx"


class RuntimeSchema(Schema):
    id: UUID
    workspace_module: str
    type: str
    name: str
    module: str
    program_name: str
    configuration: dict
    port: int | None
    is_enabled: bool
    installed_at: datetime | None
    log_path: str

    @staticmethod
    def resolve_workspace_module(obj: Runtime) -> str:
        return obj.workspace.module


class RuntimeCreateSchema(Schema):
    workspace_module: str
    type: str
    name: str
    # The code the runtime runs, which is whatever workspaces.scaffold was told to create.
    module: str = "web"
    configuration: dict = {}
    # Left out for HTTP runtimes so management allocates the next free one.
    port: int | None = None


class RuntimePatchSchema(Schema):
    module: str | None = None
    configuration: dict | None = None
    port: int | None = None


class ConfigFileSchema(Schema):
    path: str
    content: str


class ManifestSchema(Schema):
    """The complete desired state for the runtimes it covers, never a diff."""
    root: str
    # The workspaces this manifest speaks for. The reconciler prunes only inside these, so a
    # workspace management has no runtimes for keeps whatever config it has rather than losing it.
    workspaces: list[str]
    files: list[ConfigFileSchema]


class CommandsSchema(Schema):
    """Shell commands for the CLI to run inside a workspace, used by both install and sync."""
    program_name: str
    directory: str
    commands: list[str]


class ProcessStatusSchema(Schema):
    name: str
    state: str
    description: str
    pid: int
    uptime_seconds: int


class ConfigUpdateSchema(Schema):
    added: list[str]
    changed: list[str]
    removed: list[str]


class LogSchema(Schema):
    program_name: str
    content: str


def get_runtime_or_404(request: HttpRequest, runtime_id: UUID) -> Runtime:
    """Resolve a runtime within the caller's scope, so out-of-scope ids are indistinguishable."""
    runtime = request.auth.runtimes().filter(pk=runtime_id).first()
    if runtime is None:
        raise HttpError(404, "Runtime not found")
    return runtime.specialize()


def supervisor_action(runtime: Runtime, action: str):
    client = get_supervisor_client()
    try:
        return getattr(client, action)(runtime.program_name)
    except UnknownProgram as exc:
        raise HttpError(
            409,
            f"Supervisord does not know '{runtime.program_name}' yet. "
            "Enable the runtime and apply its configuration first.",
        ) from exc
    except SupervisorError as exc:
        raise HttpError(502, str(exc)) from exc


@controller.get("/", response=list[RuntimeSchema], tags=["Runtimes"])
def list_runtimes(request: HttpRequest, workspace_module: str | None = None) -> list[Runtime]:
    runtimes = request.auth.runtimes().select_related("workspace")
    if workspace_module:
        runtimes = runtimes.filter(workspace__module=workspace_module)
    return list(runtimes)


@controller.post("/", response={201: RuntimeSchema}, auth=control_api_key_auth, tags=["Runtimes"])
def create_runtime(request: HttpRequest, data: RuntimeCreateSchema) -> tuple[int, Runtime]:
    try:
        workspace = Workspace.objects.get(module=data.workspace_module)
    except Workspace.DoesNotExist as exc:
        raise HttpError(404, f"Workspace '{data.workspace_module}' not found") from exc

    try:
        runtime_class = runtime_class_for(data.type)
    except UnknownRuntimeType as exc:
        raise HttpError(422, str(exc)) from exc

    port = data.port
    if port is None and issubclass(runtime_class, HttpRuntime):
        port = Runtime.allocate_port()

    runtime = runtime_class(
        workspace=workspace, type=data.type, name=data.name, module=data.module,
        configuration=data.configuration, port=port,
    )
    validate_runtime(runtime)
    try:
        runtime.save()
    except IntegrityError as exc:
        raise HttpError(409, f"Workspace '{workspace.module}' already has a runtime called '{data.name}'") from exc
    return 201, runtime


def describe_validation_error(error: ValidationError) -> str:
    if hasattr(error, "message_dict"):
        return "; ".join(f"{field}: {' '.join(messages)}" for field, messages in error.message_dict.items())
    return "; ".join(error.messages)


def validate_runtime(runtime: Runtime) -> None:
    """Validate through the specialized class, so each type's own rules and schema apply."""
    try:
        # Uniqueness is left to the database, which reports it as a 409 rather than a 422.
        runtime.full_clean(validate_unique=False)
    except ValidationError as exc:
        raise HttpError(422, describe_validation_error(exc)) from exc


@controller.get("/configs/", response=ManifestSchema, auth=control_api_key_auth, tags=["Runtimes"])
def get_configs(request: HttpRequest, workspace_module: str | None = None) -> ManifestSchema:
    """
    The complete desired configuration tree for every enabled runtime.

    Complete rather than incremental on purpose: the reconciler on the host deletes whatever it finds
    under the root that is not listed here, so disabling, renaming and removing all prune themselves.
    """
    workspaces = request.auth.workspaces()
    runtimes = request.auth.runtimes().enabled().select_related("workspace")
    if workspace_module:
        workspaces = workspaces.filter(module=workspace_module)
        runtimes = runtimes.filter(workspace__module=workspace_module)
    return ManifestSchema(
        root=CONFIG_ROOT,
        workspaces=sorted(workspaces.values_list("module", flat=True)),
        files=build_manifest(runtimes),
    )


@controller.post("/reload/", response=ConfigUpdateSchema, auth=control_api_key_auth, tags=["Runtimes"])
def reload_runtimes(request: HttpRequest) -> ConfigUpdateSchema:
    """Pick up whatever the host just wrote, then reload nginx. Always follows a reconcile."""
    client = get_supervisor_client()
    try:
        changes = client.update()
        client.signal(NGINX_PROGRAM, "HUP")
    except SupervisorError as exc:
        raise HttpError(502, str(exc)) from exc
    return ConfigUpdateSchema(added=changes.added, changed=changes.changed, removed=changes.removed)


@controller.get("/{runtime_id}/", response=RuntimeSchema, tags=["Runtimes"])
def get_runtime(request: HttpRequest, runtime_id: UUID) -> Runtime:
    return get_runtime_or_404(request, runtime_id)


@controller.patch("/{runtime_id}/", response=RuntimeSchema, auth=control_api_key_auth, tags=["Runtimes"])
def patch_runtime(request: HttpRequest, runtime_id: UUID, data: RuntimePatchSchema) -> Runtime:
    runtime = get_runtime_or_404(request, runtime_id)

    if data.module is not None:
        runtime.module = data.module
    if data.configuration is not None:
        runtime.configuration = data.configuration
    if data.port is not None:
        runtime.port = data.port

    validate_runtime(runtime)
    runtime.save(update_fields=["module", "configuration", "port", "modified_at"])
    return runtime


@controller.delete("/{runtime_id}/", response={204: None}, auth=control_api_key_auth, tags=["Runtimes"])
def delete_runtime(request: HttpRequest, runtime_id: UUID) -> tuple[int, None]:
    get_runtime_or_404(request, runtime_id).delete()
    return 204, None


@controller.post("/{runtime_id}/installed/", response=RuntimeSchema, auth=control_api_key_auth, tags=["Runtimes"])
def mark_runtime_installed(request: HttpRequest, runtime_id: UUID) -> Runtime:
    """Record that install_commands() have run. Only the CLI can say so, because only it can run them."""
    runtime = get_runtime_or_404(request, runtime_id)
    runtime.installed_at = timezone.now()
    runtime.save(update_fields=["installed_at", "modified_at"])
    return runtime


@controller.post("/{runtime_id}/enable/", response=RuntimeSchema, auth=control_api_key_auth, tags=["Runtimes"])
def enable_runtime(request: HttpRequest, runtime_id: UUID) -> Runtime:
    runtime = get_runtime_or_404(request, runtime_id)
    if runtime.installed_at is None:
        # Enabling starts a process. Without an install there is no virtualenv for it to start from,
        # which surfaces as a supervisord backoff loop rather than as the missing step it is.
        raise HttpError(
            409,
            f"Runtime '{runtime.program_name}' is not installed. Run "
            f"'invoke runtimes.install --workspace-module={runtime.workspace.module} "
            f"--name={runtime.name}' first.",
        )
    runtime.is_enabled = True
    runtime.save(update_fields=["is_enabled", "modified_at"])
    return runtime


@controller.post("/{runtime_id}/disable/", response=RuntimeSchema, auth=control_api_key_auth, tags=["Runtimes"])
def disable_runtime(request: HttpRequest, runtime_id: UUID) -> Runtime:
    runtime = get_runtime_or_404(request, runtime_id)
    runtime.is_enabled = False
    runtime.save(update_fields=["is_enabled", "modified_at"])
    return runtime


@controller.get("/{runtime_id}/configs/", response=ManifestSchema, auth=control_api_key_auth, tags=["Runtimes"])
def get_runtime_configs(request: HttpRequest, runtime_id: UUID) -> ManifestSchema:
    runtime = get_runtime_or_404(request, runtime_id)
    return ManifestSchema(
        root=CONFIG_ROOT, workspaces=[runtime.workspace.module], files=build_manifest([runtime]),
    )


@controller.get("/{runtime_id}/install-commands/", response=CommandsSchema, auth=control_api_key_auth,
                tags=["Runtimes"])
def get_install_commands(request: HttpRequest, runtime_id: UUID) -> CommandsSchema:
    """What this runtime needs run inside its workspace once, before it can be enabled."""
    runtime = get_runtime_or_404(request, runtime_id)
    return CommandsSchema(
        program_name=runtime.program_name,
        directory=runtime.home_directory,
        commands=runtime.install_commands(),
    )


@controller.get("/{runtime_id}/sync-commands/", response=CommandsSchema, auth=control_api_key_auth,
                tags=["Runtimes"])
def get_sync_commands(request: HttpRequest, runtime_id: UUID) -> CommandsSchema:
    """
    What this runtime needs run inside its workspace to be up to date.

    Returned as data because management has no way into a workspace; the CLI runs them over SSH as
    the workspace user, which is where that privilege already lives.
    """
    runtime = get_runtime_or_404(request, runtime_id)
    return CommandsSchema(
        program_name=runtime.program_name,
        directory=runtime.home_directory,
        commands=runtime.sync_commands(),
    )


@controller.post("/{runtime_id}/restart/", response=ProcessStatusSchema, tags=["Runtimes"])
def restart_runtime(request: HttpRequest, runtime_id: UUID) -> ProcessStatusSchema:
    """The endpoint a workspace calls for its own runtimes, and only its own."""
    return supervisor_action(get_runtime_or_404(request, runtime_id), "restart")


@controller.post("/{runtime_id}/start/", response=ProcessStatusSchema, tags=["Runtimes"])
def start_runtime(request: HttpRequest, runtime_id: UUID) -> ProcessStatusSchema:
    return supervisor_action(get_runtime_or_404(request, runtime_id), "start")


@controller.post("/{runtime_id}/stop/", response=ProcessStatusSchema, tags=["Runtimes"])
def stop_runtime(request: HttpRequest, runtime_id: UUID) -> ProcessStatusSchema:
    return supervisor_action(get_runtime_or_404(request, runtime_id), "stop")


@controller.get("/{runtime_id}/status/", response=ProcessStatusSchema, tags=["Runtimes"])
def get_runtime_status(request: HttpRequest, runtime_id: UUID) -> ProcessStatusSchema:
    return supervisor_action(get_runtime_or_404(request, runtime_id), "status")


@controller.get("/{runtime_id}/logs/", response=LogSchema, tags=["Runtimes"])
def get_runtime_logs(request: HttpRequest, runtime_id: UUID, offset: int = 0, length: int = 0) -> LogSchema:
    runtime = get_runtime_or_404(request, runtime_id)
    client = get_supervisor_client()
    try:
        content = client.read_log(runtime.program_name, offset=offset, length=length)
    except UnknownProgram as exc:
        raise HttpError(409, f"Supervisord does not know '{runtime.program_name}' yet.") from exc
    except SupervisorError as exc:
        raise HttpError(502, str(exc)) from exc
    return LogSchema(program_name=runtime.program_name, content=content)
