import pytest
from django.test import Client

from access_control.models import Workspace


CONTROL_API_KEY = "control:11111111-1111-1111-1111-111111111111"


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
