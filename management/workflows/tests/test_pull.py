import pytest

from workflows.models import Workflow, WorkflowTag
from workflows.n8n import FakeN8nClient, N8nUnavailable
from workflows.services import pull


pytestmark = pytest.mark.django_db


@pytest.fixture
def reconciled_tag(workspace, n8n):
    return WorkflowTag.objects.create(
        workspace=workspace, name="business", n8n_id=n8n.add_tag("business").id,
    )


def test_pull_mirrors_what_n8n_holds(workspace, reconciled_tag, n8n):
    n8n.add_workflow("Github invoices", tags=["business"])

    workflows = pull(workspace, client=FakeN8nClient())

    assert [workflow.name for workflow in workflows] == ["Github invoices"]
    stored = Workflow.objects.get()
    assert stored.n8n_id and stored.pulled_at
    assert [tag.name for tag in stored.tags.all()] == ["business"]


def test_a_workspace_without_tags_pulls_nothing(workspace, n8n):
    n8n.add_workflow("Somebody else's", tags=["theirs"])

    assert pull(workspace, client=FakeN8nClient()) == []
    assert not Workflow.objects.exists()


def test_a_second_pull_updates_the_same_row(workspace, reconciled_tag, n8n):
    n8n.add_workflow("Github invoices", tags=["business"])
    client = FakeN8nClient()

    pull(workspace, client=client)
    pull(workspace, client=client)

    assert Workflow.objects.count() == 1


def test_a_rename_in_n8n_updates_rather_than_duplicates(workspace, reconciled_tag, n8n):
    """n8n_id is the identity, which is the whole reason a rename does not fork the row."""
    workflow = n8n.add_workflow("Old name", tags=["business"])
    client = FakeN8nClient()
    pull(workspace, client=client)

    n8n.workflows[workflow.id] = n8n.workflows[workflow.id].__class__(
        id=workflow.id, name="New name", definition=workflow.definition, tags=workflow.tags,
    )
    pull(workspace, client=client)

    stored = Workflow.objects.get()
    assert (stored.name, stored.slug) == ("New name", "new-name")


def test_a_row_that_never_reached_n8n_is_adopted_by_slug(workspace, reconciled_tag, n8n):
    """What attaches an id to a workflow a push just created, without pushing having stored it."""
    orphan = Workflow.objects.create(workspace=workspace, name="Github invoices")
    created = n8n.add_workflow("Github invoices", tags=["business"])

    pull(workspace, client=FakeN8nClient())

    orphan.refresh_from_db()
    assert orphan.n8n_id == created.id
    assert Workflow.objects.count() == 1


def test_tags_management_does_not_own_are_ignored(workspace, reconciled_tag, n8n):
    """A tag added in the UI must not hand this workspace a name that belongs to someone else."""
    n8n.add_workflow("Github invoices", tags=["business", "someone-elses"])

    pull(workspace, client=FakeN8nClient())

    assert [tag.name for tag in Workflow.objects.get().tags.all()] == ["business"]
    assert not WorkflowTag.objects.filter(name="someone-elses").exists()


def test_archived_workflows_are_skipped(workspace, reconciled_tag, n8n):
    """Archived is n8n's recycle bin. Mirroring it would resurrect what a human put away."""
    n8n.add_workflow("Archived", tags=["business"], is_archived=True)

    assert pull(workspace, client=FakeN8nClient()) == []


def test_workflow_state_never_lands_in_the_definition(workspace, reconciled_tag, n8n):
    n8n.add_workflow("Github invoices", tags=["business"], definition={
        "name": "Github invoices", "nodes": [], "connections": {}, "active": True, "versionId": "v9",
    })

    pull(workspace, client=FakeN8nClient())

    definition = Workflow.objects.get().definition
    assert "active" not in definition and "versionId" not in definition


def test_prune_removes_what_n8n_no_longer_has(workspace, reconciled_tag, n8n):
    gone = Workflow.objects.create(workspace=workspace, name="Gone", n8n_id="wfGone")
    n8n.add_workflow("Kept", tags=["business"])

    pull(workspace, client=FakeN8nClient(), prune=True)

    assert not Workflow.objects.filter(pk=gone.pk).exists()
    assert Workflow.objects.filter(name="Kept").exists()


def test_prune_leaves_rows_that_never_reached_n8n(workspace, reconciled_tag, n8n):
    """An empty n8n_id means "not pushed yet", not "deleted in n8n"."""
    pending = Workflow.objects.create(workspace=workspace, name="Pending")

    pull(workspace, client=FakeN8nClient(), prune=True)

    assert Workflow.objects.filter(pk=pending.pk).exists()


def test_a_failed_listing_prunes_nothing(workspace, reconciled_tag, n8n):
    """Half a listing plus a prune would delete rows for workflows that are still there."""
    survivor = Workflow.objects.create(workspace=workspace, name="Survivor", n8n_id="wfSurvivor")
    n8n.fail("list_workflows", N8nUnavailable("n8n is down"))

    with pytest.raises(N8nUnavailable):
        pull(workspace, client=FakeN8nClient(), prune=True)

    assert Workflow.objects.filter(pk=survivor.pk).exists()
