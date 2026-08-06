"""
The Celery application for this workspace.

A CeleryRuntime runs this with `celery --app {{ workspace.django_module }} worker`, which imports
{{ workspace.django_module }}/__init__.py and picks up the app exported there.
"""
import os

from celery import Celery


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "{{ workspace.django_module }}.settings")

app = Celery("{{ workspace.module }}")

# Defaults first, so any CELERY_ prefixed Django setting below can still override them. The queue is
# named after the workspace because every workspace shares one Redis.
app.conf.update(
    broker_url=os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0"),
    result_backend=os.environ.get("CELERY_RESULT_BACKEND", "redis://redis:6379/0"),
    task_default_queue="{{ workspace.module }}",
    task_track_started=True,
    timezone="UTC",
)
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self) -> str:
    """A task to prove the worker is alive. Queue it with debug_task.delay() from a Django shell."""
    return f"celery is running as {self.request.id}"
