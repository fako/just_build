"""
Per-type configuration schemas.

The Runtime table stores configuration as JSON, so these schemas are what keep a malformed payload
from becoming a broken supervisord file. Fields that default to something derived from the workspace
are optional here and resolved on the runtime class, which is the only place that knows the
workspace.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RuntimeConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SupervisordConfiguration(RuntimeConfiguration):
    autostart: bool = True
    autorestart: bool = True
    # Extra variables for the supervisord environment= line, on top of what the runtime derives.
    environment: dict[str, str] = Field(default_factory=dict)


class HttpConfiguration(SupervisordConfiguration):
    # Defaults to "<workspace slug>.localhost".
    domain: str | None = None
    workers: int = Field(default=2, ge=1, le=16)


class DjangoConfiguration(HttpConfiguration):
    # Defaults to the workspace's own django_module.
    django_module: str | None = None
    # Defaults to "<django_module>.asgi".
    asgi_module: str | None = None


class CeleryConfiguration(SupervisordConfiguration):
    # Defaults to the workspace's own django_module.
    app: str | None = None
    queues: list[str] = Field(default_factory=list)
    concurrency: int = Field(default=2, ge=1, le=64)
    loglevel: str = "INFO"
    # Runs the beat scheduler inside the worker, which is what a development box wants.
    beat: bool = False
