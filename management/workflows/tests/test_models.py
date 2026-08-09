import pytest
from django.db import IntegrityError

from workflows.definitions import (
    CREATE_FIELDS,
    InvalidDefinition,
    build_create_body,
    build_update_body,
    definition_tags,
    sanitize_definition,
    validate_definition,
)
from workflows.models import Workflow, WorkflowTag


pytestmark = pytest.mark.django_db


def test_a_tag_name_belongs_to_one_workspace(workspace, other_workspace):
    """The constraint is the lock. n8n's tag namespace is global, so management's has to be too."""
    WorkflowTag.objects.create(workspace=workspace, name="business")
    with pytest.raises(IntegrityError):
        WorkflowTag.objects.create(workspace=other_workspace, name="business")


def test_slugs_are_derived_and_deduplicated(workspace):
    """n8n puts no uniqueness on names, so two that slugify alike must both be storable."""
    first = Workflow.objects.create(workspace=workspace, name="Report!")
    second = Workflow.objects.create(workspace=workspace, name="Report?")

    assert first.slug == "report"
    assert second.slug == "report-2"


def test_the_same_slug_is_free_in_another_workspace(workspace, other_workspace):
    Workflow.objects.create(workspace=workspace, name="Report")
    assert Workflow.objects.create(workspace=other_workspace, name="Report").slug == "report"


def test_renaming_keeps_the_slug_in_step(workspace):
    workflow = Workflow.objects.create(workspace=workspace, name="Report")
    workflow.name = "Invoices"
    workflow.save(update_fields=["name", "modified_at"])

    workflow.refresh_from_db()
    assert workflow.slug == "invoices"


def test_sanitize_drops_everything_n8n_owns(exported_workflow):
    sanitized = sanitize_definition(exported_workflow)

    assert set(sanitized) == {"name", "nodes", "connections", "settings", "pinData", "description"}
    for owned in ("id", "active", "isArchived", "createdAt", "updatedAt", "meta", "shared", "tags",
                  "staticData"):
        assert owned not in sanitized


def test_create_body_is_exactly_the_allowlist(exported_workflow):
    """A single key n8n did not ask for fails the whole call, so assert the set, not its absences."""
    body = build_create_body(exported_workflow, "project-1")

    assert set(body) <= set(CREATE_FIELDS)
    assert body["projectId"] == "project-1"
    assert "id" not in body and "active" not in body and "tags" not in body


def test_update_body_carries_no_project(exported_workflow):
    """PUT drops projectId from its schema, so sending it is a 400 rather than a no-op."""
    body = build_update_body(exported_workflow)

    assert "projectId" not in body
    assert "description" in body


def test_credential_references_survive_a_round_trip(exported_workflow):
    """Management holds no credentials, but a workflow keeps pointing at the ones it always used."""
    original = [node.get("credentials") for node in exported_workflow["nodes"]]
    body = build_create_body(sanitize_definition(exported_workflow), "project-1")

    assert [node.get("credentials") for node in body["nodes"]] == original
    assert original[0] == {"gmailOAuth2": {"id": "598Iuek3tXAaPv98", "name": "Gmail account"}}


def test_missing_settings_is_defaulted(definition):
    body = build_create_body({k: v for k, v in definition().items() if k != "settings"}, "project-1")
    assert body["settings"] == {}


def test_tags_are_deduplicated_in_order(definition):
    assert definition_tags(definition(tags=["b", "a", "b"])) == ["b", "a"]


@pytest.mark.parametrize("broken,message", [
    ({"nodes": [], "connections": {}, "tags": ["a"]}, "no name"),
    ({"name": "One", "connections": {}, "tags": ["a"]}, "missing 'nodes'"),
    ({"name": "One", "nodes": {}, "connections": {}, "tags": ["a"]}, "not a list"),
    ({"name": "One", "nodes": [], "connections": {}, "tags": "a"}, "not a list of names"),
    ({"name": "One", "nodes": [], "connections": {}, "id": 7, "tags": ["a"]}, "not a string"),
])
def test_validation_names_the_problem(broken, message):
    with pytest.raises(InvalidDefinition, match=message):
        validate_definition(broken, index=0)


def test_an_untagged_workflow_is_refused(definition):
    """Nothing could find it again: the project is shared and the tag is the only handle."""
    with pytest.raises(InvalidDefinition, match="carries no tags"):
        validate_definition(definition(tags=[]), index=0)
