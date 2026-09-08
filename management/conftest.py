import pytest
from django.test import Client

from access_control.models import Workspace
from runtimes.supervisor import FAKE_SUPERVISOR, FakeSupervisorState
from workflows.n8n import FAKE_N8N, FakeN8nState


CONTROL_API_KEY = "control:11111111-1111-1111-1111-111111111111"


@pytest.fixture
def supervisor(settings) -> FakeSupervisorState:
    """Swap supervisord for the in-memory fake, so process control is testable without a container."""
    settings.SUPERVISOR_CLIENT = "runtimes.supervisor.FakeSupervisorClient"
    FAKE_SUPERVISOR.reset()
    return FAKE_SUPERVISOR


@pytest.fixture
def n8n(settings) -> FakeN8nState:
    """Swap n8n for the in-memory fake, so workflows are testable without the service."""
    settings.N8N_CLIENT = "workflows.n8n.FakeN8nClient"
    settings.N8N_API_KEY = "test-key"
    settings.N8N_PROJECT_ID = "project-test"
    FAKE_N8N.reset()
    return FAKE_N8N


@pytest.fixture
def control_api_key(settings) -> str:
    settings.CONTROL_API_KEY = CONTROL_API_KEY
    return CONTROL_API_KEY


@pytest.fixture
def control_client(control_api_key: str) -> Client:
    """A client authenticated as the invoke CLI on the host."""
    return Client(headers={"authorization": f"Bearer {control_api_key}"})


@pytest.fixture
def workspace_client():
    """Build a client authenticated as a given workspace, issuing it a key in passing."""
    def build_client(workspace: Workspace) -> Client:
        api_key = workspace.issue_api_key()
        return Client(headers={"authorization": f"Bearer {api_key}"})

    return build_client
