import pytest

from workflows.definitions import InvalidDefinition
from workflows.models import Workflow, WorkflowTag
from workflows.n8n import FakeN8nClient
from workflows.services import TagOwnedElsewhere, UnknownWorkflowId, push, workspace_index


pytestmark = pytest.mark.django_db


def test_a_first_push_creates_and_then_tags(workspace, n8n, definition):
    """Neither create nor update accepts tags in its body, so the second call is not optional."""
    results = push(workspace, [definition()], client=FakeN8nClient())

    assert [(result.name, result.action) for result in results] == [("Github invoices", "created")]
    actions = [call[0] for call in n8n.calls]
    assert actions.index("create_workflow") < actions.index("set_workflow_tags")
    workflow_id = results[0].n8n_id
    assert n8n.workflows[workflow_id].tag_names == {"business"}


def test_push_stores_no_workflows(workspace, n8n, definition):
    """The mirror records what n8n has, and only a pull can know that."""
    push(workspace, [definition()], client=FakeN8nClient())

    assert not Workflow.objects.exists()
    # Tags are registry state rather than a mirror, so those it does write.
    assert WorkflowTag.objects.filter(workspace=workspace, name="business").exists()


def test_a_second_push_updates_by_name(workspace, business_tag, n8n, definition):
    business_tag.n8n_id = n8n.add_tag("business").id
    business_tag.save(update_fields=["n8n_id", "modified_at"])
    existing = n8n.add_workflow("Github invoices", tags=["business"])

    results = push(workspace, [definition()], client=FakeN8nClient())

    assert [(result.n8n_id, result.action) for result in results] == [(existing.id, "updated")]


def test_a_push_by_id_updates_that_workflow(workspace, business_tag, n8n, definition):
    business_tag.n8n_id = n8n.add_tag("business").id
    business_tag.save(update_fields=["n8n_id", "modified_at"])
    existing = n8n.add_workflow("Old name", tags=["business"])

    results = push(workspace, [definition(name="New name", id=existing.id)], client=FakeN8nClient())

    assert results[0].n8n_id == existing.id
    assert n8n.workflows[existing.id].name == "New name"


def test_retagging_replaces_the_tags_in_n8n(workspace, business_tag, n8n, definition):
    business_tag.n8n_id = n8n.add_tag("business").id
    business_tag.save(update_fields=["n8n_id", "modified_at"])
    existing = n8n.add_workflow("Github invoices", tags=["business"])

    push(workspace, [definition(tags=["invoices"])], client=FakeN8nClient())

    assert n8n.workflows[existing.id].tag_names == {"invoices"}


def test_another_workspace_id_is_refused_and_writes_nothing(workspace, other_workspace, n8n, definition):
    """The whole cross-workspace defence: an id is only usable once the tag index has resolved it."""
    WorkflowTag.objects.create(workspace=other_workspace, name="theirs", n8n_id=n8n.add_tag("theirs").id)
    theirs = n8n.add_workflow("Theirs", tags=["theirs"])

    with pytest.raises(UnknownWorkflowId, match=theirs.id):
        push(workspace, [definition(id=theirs.id)], client=FakeN8nClient())

    assert n8n.workflows[theirs.id].name == "Theirs"
    assert "update_workflow" not in [call[0] for call in n8n.calls]


def test_an_unknown_id_is_refused(workspace, n8n, definition):
    with pytest.raises(UnknownWorkflowId, match="wfGone"):
        push(workspace, [definition(id="wfGone")], client=FakeN8nClient())


def test_a_bad_definition_halfway_down_stops_the_whole_batch(workspace, n8n, definition):
    batch = [definition(name="Good"), definition(name="Bad", tags=[])]

    with pytest.raises(InvalidDefinition, match="carries no tags"):
        push(workspace, batch, client=FakeN8nClient())

    assert n8n.workflows == {}


def test_a_foreign_tag_stops_the_whole_batch(workspace, other_workspace, n8n, definition):
    WorkflowTag.objects.create(workspace=other_workspace, name="theirs")

    with pytest.raises(TagOwnedElsewhere):
        push(workspace, [definition(name="Mine"), definition(name="Theirs", tags=["theirs"])],
             client=FakeN8nClient())

    assert n8n.workflows == {}


def test_an_export_from_another_instance_is_refused(workspace, n8n, exported_workflow):
    """
    A just_automate export cannot be pushed as it stands.

    It carries the id it had on the instance it came from, which is not reachable here, so it is
    refused rather than silently written over whatever happens to hold that id.
    """
    exported_workflow["tags"] = ["business"]

    with pytest.raises(UnknownWorkflowId, match=exported_workflow["id"]):
        push(workspace, [exported_workflow], client=FakeN8nClient())


def test_credential_references_reach_n8n_untouched(workspace, n8n, exported_workflow):
    """Management holds no credentials, so the references in the nodes are all that keeps them working."""
    exported_workflow["tags"] = ["business"]
    exported_workflow.pop("id")
    results = push(workspace, [exported_workflow], client=FakeN8nClient())

    stored = n8n.workflows[results[0].n8n_id].definition
    assert stored["nodes"][0]["credentials"] == {
        "gmailOAuth2": {"id": "598Iuek3tXAaPv98", "name": "Gmail account"},
    }


def test_the_index_is_built_from_the_tags_n8n_reports(workspace, other_workspace, n8n, definition):
    """
    The workspace boundary is whatever n8n says carries one of this workspace's tags.

    Built here rather than taken from a filter n8n was asked to apply, because it does not reliably
    apply one, and built from n8n rather than from the mirror, because the mirror is stale by design.
    """
    WorkflowTag.objects.create(workspace=workspace, name="business", n8n_id=n8n.add_tag("business").id)
    WorkflowTag.objects.create(workspace=other_workspace, name="theirs", n8n_id=n8n.add_tag("theirs").id)
    mine = n8n.add_workflow("Mine", tags=["business"])
    n8n.add_workflow("Theirs", tags=["theirs"])
    n8n.add_workflow("Nobody's")

    index = workspace_index(workspace, FakeN8nClient())

    assert list(index) == [mine.id]
