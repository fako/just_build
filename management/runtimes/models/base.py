"""
The Runtime table and the shared supervisord interface every runtime type implements.

There is one concrete table with a `type` discriminator and a JSON `configuration`. The class
hierarchy above it is made of proxy models, so a runtime type is a real Python class with real
behaviour but needs no table of its own. Adding a type is a code-only change: the field carries no
`choices`, deliberately, so that registering a new type does not produce a metadata migration. The
registry validates the value instead, and the admin builds its dropdown from the same registry.
"""
from __future__ import annotations

import copy
import uuid

from django.core.exceptions import ValidationError
from django.db import models
from pydantic import ValidationError as PydanticValidationError

from runtimes.schemas import RuntimeConfiguration, SupervisordConfiguration


WORKSPACE_LOG_ROOT = "/var/log/workspaces"
WORKSPACE_HOME_ROOT = "/home"
FIRST_RUNTIME_PORT = 8001

RUNTIME_TYPES: dict[str, type["Runtime"]] = {}


class UnknownRuntimeType(LookupError):
    pass


def register_runtime(runtime_type: str):
    """Register a proxy model under the `type` value that selects it."""
    def register(runtime_class: type["Runtime"]) -> type["Runtime"]:
        RUNTIME_TYPES[runtime_type] = runtime_class
        runtime_class.runtime_type = runtime_type
        return runtime_class

    return register


def runtime_class_for(runtime_type: str) -> type["Runtime"]:
    try:
        return RUNTIME_TYPES[runtime_type]
    except KeyError as exc:
        known = ", ".join(sorted(RUNTIME_TYPES)) or "none"
        raise UnknownRuntimeType(f"Unknown runtime type '{runtime_type}'. Known types: {known}.") from exc


class RuntimeQuerySet(models.QuerySet):

    def specialized(self) -> list["Runtime"]:
        return [runtime.specialize() for runtime in self]

    def enabled(self) -> "RuntimeQuerySet":
        return self.filter(is_enabled=True)


class Runtime(models.Model):
    """A process belonging to a workspace. Never used directly; specialize() returns the real type."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        "access_control.Workspace", related_name="runtimes", on_delete=models.CASCADE,
    )
    type = models.CharField(max_length=50)
    name = models.SlugField(max_length=64)
    configuration = models.JSONField(default=dict, blank=True)
    # Only runtimes that serve HTTP hold a port, and it is unique across the whole container.
    port = models.PositiveIntegerField(null=True, blank=True, unique=True)
    is_enabled = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    modified_at = models.DateTimeField(auto_now=True)

    objects = RuntimeQuerySet.as_manager()

    # Set by register_runtime on each proxy model.
    runtime_type: str = ""
    configuration_schema: type[RuntimeConfiguration] = RuntimeConfiguration

    class Meta:
        unique_together = ("workspace", "name")
        ordering = ("workspace__module", "name")

    def __str__(self) -> str:
        return self.program_name

    def specialize(self) -> "Runtime":
        """Return this row as an instance of the class its type selects."""
        runtime_class = runtime_class_for(self.type)
        if type(self) is runtime_class:
            return self
        specialized = copy.copy(self)
        specialized.__class__ = runtime_class
        return specialized

    @property
    def settings(self) -> RuntimeConfiguration:
        """The configuration JSON, parsed and defaulted by this type's schema."""
        return self.configuration_schema.model_validate(self.configuration or {})

    @property
    def program_name(self) -> str:
        """Unique across the whole supervisord instance, not just within the workspace."""
        return f"{self.workspace.module}_{self.name}"

    @property
    def home_directory(self) -> str:
        return f"{WORKSPACE_HOME_ROOT}/{self.workspace.module}"

    @property
    def log_directory(self) -> str:
        return f"{WORKSPACE_LOG_ROOT}/{self.workspace.module}"

    @property
    def log_path(self) -> str:
        return f"{self.log_directory}/{self.name}.log"

    @property
    def python_path(self) -> str:
        return f"{self.home_directory}/venv/bin/python"

    @classmethod
    def allocate_port(cls) -> int:
        """Lowest free port at or above the first runtime port, across every workspace."""
        taken = set(Runtime.objects.exclude(port=None).values_list("port", flat=True))
        port = FIRST_RUNTIME_PORT
        while port in taken:
            port += 1
        return port

    def clean(self) -> None:
        super().clean()
        try:
            runtime_class_for(self.type)
        except UnknownRuntimeType as exc:
            raise ValidationError({"type": str(exc)}) from exc

        try:
            self.specialize().settings
        except PydanticValidationError as exc:
            raise ValidationError({"configuration": str(exc)}) from exc


class SupervisordRuntime(Runtime):
    """
    A runtime supervisord runs as a program.

    Everything below this class shares one contract. Two of its members return data for somebody else
    to act on, because they need privileges management does not have: config_files() is written by
    the CLI on the host, and sync_commands() is executed by the CLI over SSH as the workspace user.
    """

    configuration_schema: type[SupervisordConfiguration] = SupervisordConfiguration

    class Meta:
        proxy = True

    @property
    def command(self) -> str:
        """The supervisord command= line."""
        raise NotImplementedError

    def environment(self) -> dict[str, str]:
        """Variables for the supervisord environment= line."""
        settings = self.settings
        environment = {
            "DJANGO_SETTINGS_MODULE": f"{self.workspace.django_module}.settings",
            "PYTHONPATH": self.home_directory,
        }
        environment.update(settings.environment)
        return environment

    def sync_commands(self) -> list[str]:
        """
        Shell commands that bring the workspace up to date for this runtime.

        Run by the CLI over SSH as the workspace user, from the workspace home directory.
        """
        return ["venv/bin/python -m pip install -e ."]
