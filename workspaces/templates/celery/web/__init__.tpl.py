"""Exports the Celery app so `celery --app {{ workspace.django_module }}` finds it."""
from {{ workspace.django_module }}.celery import app as celery_app


__all__ = ["celery_app"]
