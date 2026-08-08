from __future__ import annotations

from django.core.exceptions import ValidationError

from runtimes.models.base import SupervisordRuntime, register_runtime
from runtimes.models.django_project import DjangoProjectRuntime
from runtimes.schemas import CeleryConfiguration


@register_runtime("celery")
class CeleryRuntime(DjangoProjectRuntime, SupervisordRuntime):
    """A Celery worker. Serves no HTTP, so it holds no port and renders no nginx config."""

    configuration_schema: type[CeleryConfiguration] = CeleryConfiguration

    class Meta:
        proxy = True

    @property
    def command(self) -> str:
        settings = self.settings
        command = (
            f"{self.python_path} -m celery --app {self.module} worker"
            f" --loglevel {settings.loglevel} --concurrency {settings.concurrency}"
        )
        if settings.queues:
            command += f" --queues {','.join(settings.queues)}"
        if settings.beat:
            # Embedded beat. Fine for a development box, where there is one worker per queue anyway.
            command += " --beat"
        return command

    def clean(self) -> None:
        super().clean()
        if self.port is not None:
            raise ValidationError({"port": "Celery runtimes do not serve HTTP and take no port."})
