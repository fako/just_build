import pytest

from workflows.models import Workflow, WorkflowTag
from workflows.n8n import FakeN8nClient
from workflows.services import pull, repush, stored_definitions, sync


pytestmark = pytest.mark.django_db


def test_sync_pushes_then_stores_what_came_back(workspace, n8n, definition):
    pushed, workflows = sync(workspace, [definition()], client=FakeN8nClient())

    assert [result.action for result in pushed] == ["created"]
    # The id the workspace needs in its file is n8n's, and only the pull half knows it.
    assert workflows[0].n8n_id == pushed[0].n8n_id


def test_syncing_twice_updates_rather_than_duplicates(workspace, n8n, definition):
    client = FakeN8nClient()
    _, first = sync(workspace, [definition()], client=client)

    # Second time round the workspace has the id, exactly as a synced file would.
    pushed, second = sync(workspace, [definition(id=first[0].n8n_id)], client=client)

    assert [result.action for result in pushed] == ["updated"]
    assert Workflow.objects.count() == 1
    assert second[0].definition == first[0].definition


def test_syncing_without_the_id_still_matches_by_name(workspace, n8n, definition):
    """A file that lost its id is not a new workflow, as long as the name is unchanged."""
    client = FakeN8nClient()
    sync(workspace, [definition()], client=client)

    pushed, _ = sync(workspace, [definition()], client=client)

    assert [result.action for result in pushed] == ["updated"]
    assert len(n8n.workflows) == 1


def test_removing_a_file_deletes_nothing(workspace, n8n, definition):
    """Pushing a subset is the normal case, so push is never allowed to be destructive."""
    client = FakeN8nClient()
    sync(workspace, [definition(name="One"), definition(name="Two")], client=client)

    sync(workspace, [definition(name="One")], client=client)

    assert Workflow.objects.count() == 2
    assert len(n8n.workflows) == 2


def test_stored_definitions_round_trip_back_into_push_input(workspace, n8n, definition):
    sync(workspace, [definition()], client=FakeN8nClient())

    rebuilt = stored_definitions(workspace)

    assert rebuilt[0]["name"] == "Github invoices"
    assert rebuilt[0]["tags"] == ["business"]
    assert rebuilt[0]["id"]


def test_repush_recreates_after_n8n_loses_everything(workspace, n8n, definition):
    """The recovery path: stored ids no longer resolve, so they are recreated and written back."""
    client = FakeN8nClient()
    sync(workspace, [definition()], client=client)
    original_id = Workflow.objects.get().n8n_id

    # n8n comes back empty, the way it does after its volume is removed. Tags go with it.
    n8n.reset()
    n8n._sequence = 100  # so a recreated workflow cannot coincidentally reuse the old id
    WorkflowTag.objects.filter(workspace=workspace).update(n8n_id="")

    results = repush([workspace], client=FakeN8nClient())

    assert [result.action for result in results["magic_match"]] == ["created"]
    stored = Workflow.objects.get()
    assert stored.n8n_id != original_id
    assert stored.n8n_id == results["magic_match"][0].n8n_id
    assert n8n.workflows[stored.n8n_id].tag_names == {"business"}


def test_repush_covers_every_workspace_at_once(workspace, other_workspace, n8n, definition):
    client = FakeN8nClient()
    sync(workspace, [definition(name="Mine", tags=["business"])], client=client)
    sync(other_workspace, [definition(name="Theirs", tags=["theirs"])], client=client)

    n8n.reset()
    WorkflowTag.objects.update(n8n_id="")

    results = repush([workspace, other_workspace], client=FakeN8nClient())

    assert sorted(results) == ["just_automate", "magic_match"]


def test_repush_skips_a_workspace_with_nothing_stored(workspace, n8n):
    assert repush([workspace], client=FakeN8nClient()) == {}


def test_a_pull_after_repush_finds_the_same_rows(workspace, n8n, definition):
    client = FakeN8nClient()
    sync(workspace, [definition()], client=client)
    n8n.reset()
    WorkflowTag.objects.filter(workspace=workspace).update(n8n_id="")
    repush([workspace], client=FakeN8nClient())

    pull(workspace, client=FakeN8nClient())

    assert Workflow.objects.count() == 1
