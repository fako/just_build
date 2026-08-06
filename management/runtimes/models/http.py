from __future__ import annotations

from django.core.exceptions import ValidationError

from runtimes.models.base import SupervisordRuntime, register_runtime
from runtimes.schemas import DjangoConfiguration, HttpConfiguration


class HttpRuntime(SupervisordRuntime):
    """
    A runtime that serves HTTP, and therefore owns a port and an nginx server block.

    Kept as its own class rather than a mixin so that "does this runtime need a port" is an
    isinstance question, and so that non-Python types such as Node or Laravel can join it later.
    """

    configuration_schema: type[HttpConfiguration] = HttpConfiguration

    class Meta:
        proxy = True

    @property
    def domain(self) -> str:
        return self.settings.domain or f"{self.workspace.slug}.localhost"

    @property
    def upstream_name(self) -> str:
        return f"{self.program_name}_upstream"

    def clean(self) -> None:
        super().clean()
        if self.port is None:
            raise ValidationError({"port": "HTTP runtimes need a port."})


@register_runtime("django")
class DjangoRuntime(HttpRuntime):

    configuration_schema: type[DjangoConfiguration] = DjangoConfiguration

    class Meta:
        proxy = True

    @property
    def django_module(self) -> str:
        return self.settings.django_module or self.workspace.django_module

    @property
    def asgi_module(self) -> str:
        return self.settings.asgi_module or f"{self.django_module}.asgi"

    @property
    def command(self) -> str:
        settings = self.settings
        return (
            f"{self.python_path} -m uvicorn {self.asgi_module}:application"
            f" --host 127.0.0.1 --port {self.port} --workers {settings.workers}"
            " --loop uvloop --http httptools"
        )

    def environment(self) -> dict[str, str]:
        environment = super().environment()
        environment["DJANGO_SETTINGS_MODULE"] = f"{self.django_module}.settings"
        return environment

    def sync_commands(self) -> list[str]:
        # Static files are served by nginx straight from staticfiles/, so they have to be collected
        # before the process comes back up.
        return super().sync_commands() + ["venv/bin/python manage.py collectstatic --noinput"]
