"""
Per-type configuration schemas.

The Runtime table stores configuration as JSON, so these schemas are what keep a malformed payload
from becoming a broken supervisord file. What every runtime has regardless of type is a field on
Runtime instead; this is only what differs per type. Values derived from something the schema cannot
see stay optional here and resolve on the runtime class.
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
    # Defaults to "<runtime module>.asgi".
    asgi_module: str | None = None


class CeleryConfiguration(SupervisordConfiguration):
    queues: list[str] = Field(default_factory=list)
    concurrency: int = Field(default=2, ge=1, le=64)
    loglevel: str = "INFO"
    # Runs the beat scheduler inside the worker, which is what a development box wants.
    beat: bool = False
