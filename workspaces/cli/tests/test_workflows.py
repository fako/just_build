import importlib.util
import json
import sys
from pathlib import Path

import pytest
from invoke.context import Context

from workspaces.cli.constants import TEMPLATES_DIR
from workspaces.cli.workflows import client, tasks


def load_template_module(relative_path: str, name: str):
    """
    Import a module out of a workspace template.

    Templates are written for a workspace home rather than for this repository, so the only way to
    exercise one is to load it by path. Worth doing for this file: it is what every agent's sync runs
    through, and its two behaviours below are easy to break and invisible until a workspace breaks.
    """
    spec = importlib.util.spec_from_file_location(name, TEMPLATES_DIR / relative_path)
    module = importlib.util.module_from_spec(spec)
    # Without this the import leaves a __pycache__ inside the template directory, which is then
    # something the scaffolder has to know to skip. It does, but there is no reason to make it.
    dont_write_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = dont_write_bytecode
    return module


workflow_files = load_template_module("n8n/n8n/files.py", "template_n8n_files")


def test_workflow_records_parse_the_api_response():
    record = client.WorkflowRecord.model_validate({
        "id": "0f1b", "workspace_module": "magic_match", "name": "Github invoices",
        "slug": "github-invoices", "n8n_id": "wf1", "definition": {"nodes": []},
        "tags": ["business"], "pulled_at": None,
    })

    assert record.name == "Github invoices"
    assert record.tags == ["business"]


def test_reading_a_workflow_directory(tmp_path):
    (tmp_path / "one.json").write_text(json.dumps({"name": "One", "tags": ["business"]}))
    (tmp_path / "notes.txt").write_text("ignored")

    definitions = workflow_files.read_workflows(tmp_path)

    assert [definition["name"] for definition in definitions] == ["One"]


def test_an_export_straight_from_n8n_is_unwrapped(tmp_path):
    """`n8n export:workflow` emits a one element array, and so do just_automate's committed files."""
    (tmp_path / "one.json").write_text(json.dumps([{"name": "One", "tags": ["business"]}]))

    assert workflow_files.read_workflows(tmp_path)[0]["name"] == "One"


def test_reading_can_pick_one_workflow_by_name(tmp_path):
    (tmp_path / "one.json").write_text(json.dumps({"name": "One"}))
    (tmp_path / "two.json").write_text(json.dumps({"name": "Two"}))

    assert [entry["name"] for entry in workflow_files.read_workflows(tmp_path, name="Two")] == ["Two"]


def test_reading_a_directory_that_is_not_there_is_empty(tmp_path):
    assert workflow_files.read_workflows(tmp_path / "absent") == []


def test_writing_puts_the_n8n_id_back_into_the_file(tmp_path):
    """Without the id a later push matches on name alone, so a rename would fork the workflow."""
    written = workflow_files.write_workflows([{
        "n8n_id": "wf1", "name": "Github invoices", "tags": ["business"],
        "definition": {"nodes": [], "connections": {}},
    }], tmp_path)

    document = json.loads(written[0].read_text())
    assert written[0].name == "github-invoices.json"
    assert document["id"] == "wf1"
    assert document["tags"] == ["business"]
    assert document["nodes"] == []


def test_writing_round_trips_back_into_reading(tmp_path):
    workflow_files.write_workflows([{
        "n8n_id": "wf1", "name": "Github invoices", "tags": ["business"],
        "definition": {"nodes": [], "connections": {}, "settings": {}},
    }], tmp_path)

    definition = workflow_files.read_workflows(tmp_path)[0]

    assert (definition["id"], definition["name"], definition["tags"]) == \
        ("wf1", "Github invoices", ["business"])


@pytest.mark.parametrize("name,expected", [
    ("Github invoices", "github-invoices.json"),
    ("  Process Invoices!  ", "process-invoices.json"),
    ("2FA / human in loop", "2fa-human-in-loop.json"),
])
def test_file_names_are_slugified(name, expected):
    assert workflow_files.workflow_file_name(name) == expected


def test_host_pull_writes_nothing_without_output(monkeypatch, tmp_path, capsys):
    """The host command refreshes the database mirror; files are the exception, not the default."""
    records = [client.WorkflowRecord(
        id="0f1b", workspace_module="magic_match", name="Github invoices", slug="github-invoices",
        n8n_id="wf1", definition={"nodes": []}, tags=["business"],
    )]
    monkeypatch.setattr(client, "pull_workflows", lambda module, prune=False: records)

    tasks.pull(Context(), workspace_module="magic_match")

    assert list(tmp_path.iterdir()) == []
    assert "Github invoices" in capsys.readouterr().out


def test_host_pull_with_output_groups_by_workspace(monkeypatch, tmp_path):
    records = [client.WorkflowRecord(
        id="0f1b", workspace_module="magic_match", name="Github invoices", slug="github-invoices",
        n8n_id="wf1", definition={"nodes": []}, tags=["business"],
    )]
    monkeypatch.setattr(client, "pull_workflows", lambda module, prune=False: records)

    tasks.pull(Context(), workspace_module="magic_match", output=str(tmp_path))

    written = Path(tmp_path, "magic_match", "github-invoices.json")
    assert json.loads(written.read_text())["id"] == "wf1"
