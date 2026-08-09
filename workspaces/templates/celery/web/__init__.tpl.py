"""Exports the Celery app so `celery --app {{ runtime_module }}` finds it."""
from {{ runtime_module }}.celery import app as celery_app


__all__ = ["celery_app"]
