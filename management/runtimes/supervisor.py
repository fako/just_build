"""
Process control over supervisord's XML-RPC interface.

Management runs in its own container, so `docker compose exec workspaces supervisorctl ...` is not
available to it. Supervisord's own RPC interface is the clean channel: no docker socket, no SSH, and
no shelling out. nginx reloads travel the same way, because nginx is itself a supervisord program.

The client is a narrow protocol with a fake alongside it, so everything above this module can be
tested without a container.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
from xmlrpc.client import Fault, ProtocolError, ServerProxy
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from django.utils.module_loading import import_string


# Supervisord fault codes worth naming. See supervisor.xmlrpc.Faults.
BAD_NAME = 10
ALREADY_STARTED = 60
NOT_RUNNING = 70
ALREADY_ADDED = 90


class SupervisorError(RuntimeError):
    pass


class UnknownProgram(SupervisorError):
    pass


@dataclass(frozen=True, slots=True)
class ProcessStatus:
    name: str
    state: str
    description: str
    pid: int = 0
    uptime_seconds: int = 0


@dataclass(frozen=True, slots=True)
class ConfigUpdate:
    """What a reread told us, and what an update did about it."""
    added: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


class SupervisorClient(Protocol):

    def status(self, program: str) -> ProcessStatus: ...

    def start(self, program: str) -> ProcessStatus: ...

    def stop(self, program: str) -> ProcessStatus: ...

    def restart(self, program: str) -> ProcessStatus: ...

    def signal(self, program: str, signal_name: str) -> None: ...

    def reread(self) -> ConfigUpdate: ...

    def update(self) -> ConfigUpdate: ...

    def read_log(self, program: str, offset: int = 0, length: int = 0) -> str: ...


class TransportErrorProxy:
    """
    Turns transport failures into SupervisorError.

    A wrong password, a container that is not up, or a refused connection arrives as a ProtocolError
    or an OSError rather than an XML-RPC Fault, and would otherwise escape as a bare traceback.
    Faults pass straight through, because the callers below interpret those individually.
    """

    def __init__(self, proxy, url: str) -> None:
        self._proxy = proxy
        self._url = url

    def __getattr__(self, name: str):
        method = getattr(self._proxy, name)

        def call(*args):
            try:
                return method(*args)
            except ProtocolError as exc:
                raise SupervisorError(
                    f"Supervisord at {self._url} refused the call with HTTP {exc.errcode} {exc.errmsg}. "
                    "Check that the workspaces container is running with a matching supervisor password."
                ) from exc
            except OSError as exc:
                raise SupervisorError(f"Could not reach supervisord at {self._url}: {exc}") from exc

        return call


class XmlRpcSupervisorClient:
    """Talks to the supervisord inet_http_server in the workspaces container."""

    def __init__(self, url: str | None = None, username: str | None = None, password: str | None = None) -> None:
        self.url = url or settings.SUPERVISOR_URL
        self.username = username if username is not None else settings.SUPERVISOR_USERNAME
        self.password = password if password is not None else settings.SUPERVISOR_PASSWORD

    def _proxy(self):
        parts = urlsplit(self.url)
        netloc = f"{self.username}:{self.password}@{parts.netloc}" if self.username else parts.netloc
        authenticated = urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
        return TransportErrorProxy(ServerProxy(authenticated).supervisor, self.url)

    def _status_from_info(self, info: dict) -> ProcessStatus:
        return ProcessStatus(
            name=info["name"],
            state=info["statename"],
            description=info.get("description", ""),
            pid=info.get("pid", 0) or 0,
            uptime_seconds=max(int(info.get("now", 0)) - int(info.get("start", 0)), 0) if info.get("start") else 0,
        )

    def status(self, program: str) -> ProcessStatus:
        try:
            return self._status_from_info(self._proxy().getProcessInfo(program))
        except Fault as fault:
            if fault.faultCode == BAD_NAME:
                raise UnknownProgram(f"Supervisord does not know a program called '{program}'.") from fault
            raise SupervisorError(f"Could not read the status of '{program}': {fault.faultString}") from fault

    def start(self, program: str) -> ProcessStatus:
        try:
            self._proxy().startProcess(program, True)
        except Fault as fault:
            if fault.faultCode == BAD_NAME:
                raise UnknownProgram(f"Supervisord does not know a program called '{program}'.") from fault
            # Already running is the state the caller asked for, so it is not a failure.
            if fault.faultCode != ALREADY_STARTED:
                raise SupervisorError(f"Could not start '{program}': {fault.faultString}") from fault
        return self.status(program)

    def stop(self, program: str) -> ProcessStatus:
        try:
            self._proxy().stopProcess(program, True)
        except Fault as fault:
            if fault.faultCode == BAD_NAME:
                raise UnknownProgram(f"Supervisord does not know a program called '{program}'.") from fault
            if fault.faultCode != NOT_RUNNING:
                raise SupervisorError(f"Could not stop '{program}': {fault.faultString}") from fault
        return self.status(program)

    def restart(self, program: str) -> ProcessStatus:
        # Supervisord has no restart call. This is what supervisorctl does.
        self.stop(program)
        return self.start(program)

    def signal(self, program: str, signal_name: str) -> None:
        try:
            self._proxy().signalProcess(program, signal_name)
        except Fault as fault:
            if fault.faultCode == BAD_NAME:
                raise UnknownProgram(f"Supervisord does not know a program called '{program}'.") from fault
            if fault.faultCode != NOT_RUNNING:
                raise SupervisorError(f"Could not signal '{program}': {fault.faultString}") from fault

    def _reload_config(self) -> ConfigUpdate:
        try:
            result = self._proxy().reloadConfig()
        except Fault as fault:
            raise SupervisorError(f"Could not reread the supervisord configuration: {fault.faultString}") from fault
        added, changed, removed = result[0]
        return ConfigUpdate(added=list(added), changed=list(changed), removed=list(removed))

    def reread(self) -> ConfigUpdate:
        return self._reload_config()

    def update(self) -> ConfigUpdate:
        """Apply what a reread found, the way supervisorctl update does."""
        changes = self._reload_config()
        proxy = self._proxy()

        for group in changes.removed:
            self._remove_group(proxy, group)
        for group in changes.changed:
            self._remove_group(proxy, group)
            self._add_group(proxy, group)
        for group in changes.added:
            self._add_group(proxy, group)

        return changes

    def _add_group(self, proxy, group: str) -> None:
        try:
            proxy.addProcessGroup(group)
        except Fault as fault:
            if fault.faultCode != ALREADY_ADDED:
                raise SupervisorError(f"Could not add process group '{group}': {fault.faultString}") from fault

    def _remove_group(self, proxy, group: str) -> None:
        try:
            proxy.stopProcessGroup(group)
        except Fault:
            # Already stopped, or gone. Either way there is nothing to stop before removing it.
            pass
        try:
            proxy.removeProcessGroup(group)
        except Fault as fault:
            if fault.faultCode != BAD_NAME:
                raise SupervisorError(f"Could not remove process group '{group}': {fault.faultString}") from fault

    def read_log(self, program: str, offset: int = 0, length: int = 0) -> str:
        try:
            return self._proxy().readProcessStdoutLog(program, offset, length)
        except Fault as fault:
            if fault.faultCode == BAD_NAME:
                raise UnknownProgram(f"Supervisord does not know a program called '{program}'.") from fault
            raise SupervisorError(f"Could not read the log of '{program}': {fault.faultString}") from fault


class FakeSupervisorState:
    """Shared in-memory state, so a fake built by the factory is the one a test inspects."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.programs: dict[str, str] = {}
        self.logs: dict[str, str] = {}
        self.calls: list[tuple[str, ...]] = []
        self.pending: ConfigUpdate = ConfigUpdate()

    def add_program(self, program: str, state: str = "RUNNING", log: str = "") -> None:
        self.programs[program] = state
        self.logs[program] = log


FAKE_SUPERVISOR = FakeSupervisorState()


class FakeSupervisorClient:
    """Test double. Selected by pointing the SUPERVISOR_CLIENT setting at this class."""

    def __init__(self, state: FakeSupervisorState | None = None) -> None:
        self.state = state or FAKE_SUPERVISOR

    def _require(self, program: str) -> None:
        if program not in self.state.programs:
            raise UnknownProgram(f"Supervisord does not know a program called '{program}'.")

    def status(self, program: str) -> ProcessStatus:
        self._require(program)
        self.state.calls.append(("status", program))
        return ProcessStatus(name=program, state=self.state.programs[program], description="fake")

    def start(self, program: str) -> ProcessStatus:
        self._require(program)
        self.state.calls.append(("start", program))
        self.state.programs[program] = "RUNNING"
        return self.status(program)

    def stop(self, program: str) -> ProcessStatus:
        self._require(program)
        self.state.calls.append(("stop", program))
        self.state.programs[program] = "STOPPED"
        return self.status(program)

    def restart(self, program: str) -> ProcessStatus:
        self._require(program)
        self.state.calls.append(("restart", program))
        self.state.programs[program] = "RUNNING"
        return self.status(program)

    def signal(self, program: str, signal_name: str) -> None:
        self._require(program)
        self.state.calls.append(("signal", program, signal_name))

    def reread(self) -> ConfigUpdate:
        self.state.calls.append(("reread",))
        return self.state.pending

    def update(self) -> ConfigUpdate:
        self.state.calls.append(("update",))
        return self.state.pending

    def read_log(self, program: str, offset: int = 0, length: int = 0) -> str:
        self._require(program)
        self.state.calls.append(("read_log", program))
        log = self.state.logs.get(program, "")
        return log[offset:offset + length] if length else log[offset:]


def get_supervisor_client() -> SupervisorClient:
    """Build the client the settings select. Tests point SUPERVISOR_CLIENT at the fake."""
    return import_string(settings.SUPERVISOR_CLIENT)()
