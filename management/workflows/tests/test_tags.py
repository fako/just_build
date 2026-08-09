import pytest

from workflows.models import WorkflowTag
from workflows.n8n import FakeN8nClient, N8nConflict
from workflows.services import TagOwnedElsewhere, ensure_tags


pytestmark = pytest.mark.django_db


def test_claiming_a_free_name_creates_it_on_both_sides(workspace, n8n):
    tags = ensure_tags(workspace, ["business"], FakeN8nClient())

    assert tags["business"].workspace == workspace
    assert tags["business"].n8n_id
    assert [tag.name for tag in n8n.tags.values()] == ["business"]


def test_an_existing_n8n_tag_is_adopted_rather_than_duplicated(workspace, n8n):
    """A name already in n8n is the same tag, not a second one that happens to look alike."""
    existing = n8n.add_tag("business")

    tags = ensure_tags(workspace, ["business"], FakeN8nClient())

    assert tags["business"].n8n_id == existing.id
    assert len(n8n.tags) == 1


def test_a_tag_created_mid_flight_is_adopted(workspace, n8n):
    """Someone made it in the UI between the listing and the create. It is still the same tag."""
    client = FakeN8nClient()
    created = []

    def create_tag(name: str):
        # Behave like n8n does when the name appeared a moment ago.
        tag = n8n.add_tag(name)
        created.append(tag)
        raise N8nConflict(f"a tag named '{name}' already exists")

    client.create_tag = create_tag

    tags = ensure_tags(workspace, ["business"], client)

    assert tags["business"].n8n_id == created[0].id


def test_a_foreign_name_is_refused_before_n8n_is_touched(workspace, other_workspace, n8n):
    """Ownership is settled first, so a batch mentioning one foreign tag creates none of the others."""
    WorkflowTag.objects.create(workspace=other_workspace, name="business")

    with pytest.raises(TagOwnedElsewhere, match="belongs to workspace 'just_automate'"):
        ensure_tags(workspace, ["invoices", "business"], FakeN8nClient())

    assert n8n.calls == []
    assert not WorkflowTag.objects.filter(name="invoices").exists()


def test_an_already_reconciled_tag_costs_no_calls(workspace, business_tag, n8n):
    business_tag.n8n_id = "tag-existing"
    business_tag.save(update_fields=["n8n_id", "modified_at"])

    ensure_tags(workspace, ["business"], FakeN8nClient())

    assert n8n.calls == []
