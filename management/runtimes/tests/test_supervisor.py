import pytest
from xmlrpc.client import Fault, ProtocolError

from runtimes.supervisor import (
    ALREADY_ADDED,
    ALREADY_STARTED,
    BAD_NAME,
    NOT_RUNNING,
    FakeSupervisorClient,
    FakeSupervisorState,
    SupervisorError,
    TransportErrorProxy,
    UnknownProgram,
    XmlRpcSupervisorClient,
    get_supervisor_client,
)


class StubProxy:
    """Stands in for the supervisor namespace of an XML-RPC ServerProxy."""

    def __init__(self, faults: dict[str, Fault] | None = None, config: list | None = None) -> None:
        self.faults = faults or {}
        self.config = config or [[[], [], []]]
        self.calls: list[tuple] = []
        self.info = {"name": "magic_match_web", "statename": "RUNNING", "description": "pid 1", "pid": 1}

    def _record(self, name: str, *args):
        self.calls.append((name, *args))
        if name in self.faults:
            raise self.faults[name]

    def getProcessInfo(self, program):
        self._record("getProcessInfo", program)
        return self.info

    def startProcess(self, program, wait):
        self._record("startProcess", program)

    def stopProcess(self, program, wait):
        self._record("stopProcess", program)

    def signalProcess(self, program, signal_name):
        self._record("signalProcess", program, signal_name)

    def reloadConfig(self):
        self._record("reloadConfig")
        return self.config

    def addProcessGroup(self, group):
        self._record("addProcessGroup", group)

    def stopProcessGroup(self, group):
        self._record("stopProcessGroup", group)

    def removeProcessGroup(self, group):
        self._record("removeProcessGroup", group)

    def readProcessStdoutLog(self, program, offset, length):
        self._record("readProcessStdoutLog", program, offset, length)
        return "log output"


def build_client(proxy: StubProxy) -> XmlRpcSupervisorClient:
    client = XmlRpcSupervisorClient(url="http://workspaces:9001/RPC2", username="management", password="secret")
    client._proxy = lambda: proxy
    return client


def test_transport_failures_become_supervisor_errors():
    # A wrong password or a container that is not up arrives as a ProtocolError, not a Fault.
    class RefusingProxy:
        def getProcessInfo(self, program):
            raise ProtocolError("workspaces:9001/RPC2", 401, "Unauthorized", {})

    client = XmlRpcSupervisorClient(url="http://workspaces:9001/RPC2", username="management", password="wrong")
    client._proxy = lambda: TransportErrorProxy(RefusingProxy(), client.url)

    with pytest.raises(SupervisorError) as error:
        client.status("magic_match_web")

    assert "401" in str(error.value)
    assert "supervisor password" in str(error.value)


def test_unreachable_supervisord_becomes_a_supervisor_error():
    class UnreachableProxy:
        def reloadConfig(self):
            raise ConnectionRefusedError("Connection refused")

    client = XmlRpcSupervisorClient(url="http://workspaces:9001/RPC2", username="management", password="secret")
    client._proxy = lambda: TransportErrorProxy(UnreachableProxy(), client.url)

    with pytest.raises(SupervisorError) as error:
        client.reread()

    assert "Could not reach supervisord" in str(error.value)


def test_faults_pass_through_the_transport_wrapper():
    # Faults are the protocol working, so each caller still interprets them itself.
    class FaultingProxy:
        def getProcessInfo(self, program):
            raise Fault(BAD_NAME, "BAD_NAME")

    client = XmlRpcSupervisorClient(url="http://workspaces:9001/RPC2", username="management", password="secret")
    client._proxy = lambda: TransportErrorProxy(FaultingProxy(), client.url)

    with pytest.raises(UnknownProgram):
        client.status("nope")


def test_credentials_are_placed_in_the_url():
    client = XmlRpcSupervisorClient(url="http://workspaces:9001/RPC2", username="management", password="secret")
    parts = []

    class Recorder:
        def __init__(self, url):
            parts.append(url)
            self.supervisor = object()

    import runtimes.supervisor as supervisor_module
    original = supervisor_module.ServerProxy
    supervisor_module.ServerProxy = Recorder
    try:
        client._proxy()
    finally:
        supervisor_module.ServerProxy = original

    assert parts == ["http://management:secret@workspaces:9001/RPC2"]


def test_restart_is_a_stop_then_a_start():
    # Supervisord has no restart call, so the order here is the behaviour.
    proxy = StubProxy()

    build_client(proxy).restart("magic_match_web")

    called = [call[0] for call in proxy.calls]
    assert called.index("stopProcess") < called.index("startProcess")


def test_starting_an_already_started_program_is_not_an_error():
    proxy = StubProxy(faults={"startProcess": Fault(ALREADY_STARTED, "ALREADY_STARTED")})

    status = build_client(proxy).start("magic_match_web")

    assert status.state == "RUNNING"


def test_stopping_a_stopped_program_is_not_an_error():
    proxy = StubProxy(faults={"stopProcess": Fault(NOT_RUNNING, "NOT_RUNNING")})

    assert build_client(proxy).stop("magic_match_web").name == "magic_match_web"


def test_unknown_programs_raise_a_named_error():
    proxy = StubProxy(faults={"getProcessInfo": Fault(BAD_NAME, "BAD_NAME")})

    with pytest.raises(UnknownProgram) as error:
        build_client(proxy).status("nope")

    assert "nope" in str(error.value)


def test_other_faults_surface_as_supervisor_errors():
    proxy = StubProxy(faults={"startProcess": Fault(30, "SPAWN_ERROR")})

    with pytest.raises(SupervisorError) as error:
        build_client(proxy).start("magic_match_web")

    assert "SPAWN_ERROR" in str(error.value)


def test_update_adds_changes_and_removes_groups():
    proxy = StubProxy(config=[[["added_one"], ["changed_one"], ["removed_one"]]])

    changes = build_client(proxy).update()

    assert changes.added == ["added_one"]
    assert changes.changed == ["changed_one"]
    assert changes.removed == ["removed_one"]
    assert proxy.calls == [
        ("reloadConfig",),
        ("stopProcessGroup", "removed_one"),
        ("removeProcessGroup", "removed_one"),
        # A changed group has to go before it can come back with its new configuration.
        ("stopProcessGroup", "changed_one"),
        ("removeProcessGroup", "changed_one"),
        ("addProcessGroup", "changed_one"),
        ("addProcessGroup", "added_one"),
    ]


def test_update_tolerates_a_group_that_is_already_added():
    proxy = StubProxy(config=[[["added_one"], [], []]], faults={"addProcessGroup": Fault(ALREADY_ADDED, "ALREADY")})

    assert build_client(proxy).update().added == ["added_one"]


def test_reread_reports_without_changing_anything():
    proxy = StubProxy(config=[[["added_one"], [], []]])

    changes = build_client(proxy).reread()

    assert changes.added == ["added_one"]
    assert proxy.calls == [("reloadConfig",)]


def test_signal_passes_the_signal_name():
    proxy = StubProxy()

    build_client(proxy).signal("nginx", "HUP")

    assert ("signalProcess", "nginx", "HUP") in proxy.calls


def test_signalling_a_stopped_program_is_not_an_error():
    proxy = StubProxy(faults={"signalProcess": Fault(NOT_RUNNING, "NOT_RUNNING")})

    build_client(proxy).signal("nginx", "HUP")


def test_read_log_passes_the_window():
    proxy = StubProxy()

    assert build_client(proxy).read_log("magic_match_web", offset=10, length=100) == "log output"
    assert ("readProcessStdoutLog", "magic_match_web", 10, 100) in proxy.calls


def test_fake_client_tracks_state_and_calls():
    state = FakeSupervisorState()
    state.add_program("magic_match_web", state="STOPPED", log="hello world")
    client = FakeSupervisorClient(state)

    assert client.status("magic_match_web").state == "STOPPED"
    assert client.start("magic_match_web").state == "RUNNING"
    assert client.stop("magic_match_web").state == "STOPPED"
    assert client.read_log("magic_match_web", offset=6) == "world"
    assert ("start", "magic_match_web") in state.calls


def test_fake_client_rejects_unknown_programs():
    with pytest.raises(UnknownProgram):
        FakeSupervisorClient(FakeSupervisorState()).restart("nope")


def test_factory_builds_the_configured_client(settings):
    settings.SUPERVISOR_CLIENT = "runtimes.supervisor.FakeSupervisorClient"

    assert isinstance(get_supervisor_client(), FakeSupervisorClient)
