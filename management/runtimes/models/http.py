from __future__ import annotations

from django.core.exceptions import ValidationError

from runtimes.configs import ConfigFile, nginx_config_path, render_config
from runtimes.models.base import SupervisordRuntime, register_runtime
from runtimes.models.django_project import DjangoProjectRuntime
from runtimes.schemas import DjangoConfiguration, HttpConfiguration


class HttpRuntime(SupervisordRuntime):
    """
    A runtime that serves HTTP, and therefore owns a port and an nginx server block.

    Kept as its own class rather than a mixin so that "does this runtime need a port" is an
    isinstance question, and so that non-Python types such as Node or Laravel can join it later.
    """

    configuration_schema: type[HttpConfiguration] = HttpConfiguration
    nginx_template = "runtimes/nginx/http.conf"
    serves_http = True

    class Meta:
        proxy = True

    def config_files(self) -> list[ConfigFile]:
        return super().config_files() + [
            ConfigFile(
                path=nginx_config_path(self.workspace.module, self.name),
                content=render_config(self.nginx_template, {"runtime": self}),
            ),
        ]

    @property
    def internal_domain(self) -> str:
        """
        The name container-to-container traffic arrives under, via the /r/ router in the workspaces
        container's nginx.

        Nothing ever resolves this: the router builds it as a string and nginx matches it against
        server_name as one. That is what keeps it independent of whatever the installation is called
        outside, so it stays on .localhost on a laptop, the home network box and a VPS alike.
        """
        return f"{self.workspace.slug}.{self.name}.localhost"

    @property
    def server_names(self) -> list[str]:
        """
        Every name this runtime answers to, in the order they were earned.

        The internal name is always present. The workspace's bare name belongs to whichever runtime
        fronts it, so a workspace with two web runtimes reaches the second one by its internal name
        or by a real domain, never by accident.
        """
        names = [self.internal_domain]
        if self.is_primary:
            names.append(f"{self.settings.subdomain or self.workspace.slug}.localhost")
        return names + list(self.settings.domains)

    @property
    def upstream_name(self) -> str:
        return f"{self.program_name}_upstream"

    def clean(self) -> None:
        super().clean()
        if self.port is None:
            raise ValidationError({"port": "HTTP runtimes need a port."})


@register_runtime("django")
class DjangoRuntime(DjangoProjectRuntime, HttpRuntime):

    configuration_schema: type[DjangoConfiguration] = DjangoConfiguration

    class Meta:
        proxy = True

    @property
    def asgi_module(self) -> str:
        return self.settings.asgi_module or f"{self.module}.asgi"

    @property
    def command(self) -> str:
        settings = self.settings
        return (
            f"{self.python_path} -m uvicorn {self.asgi_module}:application"
            f" --host 127.0.0.1 --port {self.port} --workers {settings.workers}"
            " --loop uvloop --http httptools"
        )

    def sync_commands(self) -> list[str]:
        # Static files are served by nginx straight from staticfiles/, so they have to be collected
        # before the process comes back up.
        return super().sync_commands() + ["venv/bin/python manage.py collectstatic --noinput"]
