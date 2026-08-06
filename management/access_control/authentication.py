"""
API key authentication for the management API.

Every key carries a scheme prefix that decides which principal it becomes:

    Authorization: Bearer workspace:<uuid>   ->  WorkspacePrincipal
    Authorization: Bearer control:<uuid>     ->  ControlPrincipal

A principal is not a permission flag but a set of scoped querysets. Controllers resolve objects
through those querysets only, so an out-of-scope object is a 404 rather than a leaked 403 and there
is no endpoint where the check can be forgotten.
"""
from __future__ import annotations

from secrets import compare_digest

from django.apps import apps
from django.conf import settings
from django.db.models import Model, QuerySet
from django.http import HttpRequest
from ninja.security import HttpBearer

from access_control.models import CONTROL_KEY_SCHEME, WORKSPACE_KEY_SCHEME, Workspace, hash_api_key


def runtime_model() -> type[Model]:
    """Resolved lazily: runtimes depends on access_control, so importing it here would be circular."""
    return apps.get_model("runtimes", "Runtime")


class Principal:
    """The authenticated caller, expressed as the data it is allowed to see."""

    is_control = False

    def workspaces(self) -> QuerySet[Workspace]:
        raise NotImplementedError

    def runtimes(self) -> QuerySet:
        raise NotImplementedError


class ControlPrincipal(Principal):
    """The invoke CLI on the host. Owns the repository, so it owns everything."""

    is_control = True

    def workspaces(self) -> QuerySet[Workspace]:
        return Workspace.objects.all()

    def runtimes(self) -> QuerySet:
        return runtime_model().objects.all()

    def __str__(self) -> str:
        return "control"


class WorkspacePrincipal(Principal):
    """A single workspace, calling from inside the workspaces container."""

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace

    def workspaces(self) -> QuerySet[Workspace]:
        return Workspace.objects.filter(pk=self.workspace.pk)

    def runtimes(self) -> QuerySet:
        return runtime_model().objects.filter(workspace=self.workspace)

    def __str__(self) -> str:
        return f"workspace:{self.workspace.module}"


def authenticate_api_key(token: str) -> Principal | None:
    scheme, separator, secret = token.partition(":")
    if not separator or not secret:
        return None

    if scheme == CONTROL_KEY_SCHEME:
        control_key = settings.CONTROL_API_KEY
        if not control_key or not compare_digest(token, control_key):
            return None
        return ControlPrincipal()

    if scheme == WORKSPACE_KEY_SCHEME:
        workspace = Workspace.objects.filter(api_key_hash=hash_api_key(token)).first()
        if workspace is None:
            return None
        return WorkspacePrincipal(workspace)

    return None


class ApiKeyAuth(HttpBearer):
    """Accepts any recognised key. The principal decides what the caller can reach."""

    def authenticate(self, request: HttpRequest, token: str) -> Principal | None:
        return authenticate_api_key(token)


class ControlApiKeyAuth(ApiKeyAuth):
    """
    Accepts control keys only.

    Used for operations that assume a host filesystem behind the caller: creating and configuring
    workspaces and runtimes, and reading rendered config. A workspace key is rejected as
    unauthenticated rather than forbidden, which keeps those routes from confirming anything.
    """

    def authenticate(self, request: HttpRequest, token: str) -> Principal | None:
        principal = super().authenticate(request, token)
        if principal is None or not principal.is_control:
            return None
        return principal


api_key_auth = ApiKeyAuth()
control_api_key_auth = ControlApiKeyAuth()
