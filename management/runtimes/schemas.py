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
    # One label, not a domain, which is why it is not called one: it is composed with a zone to make
    # the name a browser on the host uses. Defaults to the workspace slug, and only the primary
    # runtime has one at all.
    subdomain: str | None = None
    # Fully qualified names added by a human or an agent: the home network box, a VPS, a customer
    # domain. Kept apart from `subdomain` because they are a different kind of thing rather than a
    # longer version of the same one. Management routes them; it does not own or verify them, and
    # nothing here makes them resolve.
    domains: list[str] = Field(default_factory=list)
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
