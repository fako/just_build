"""
The workflows directory: reading it, and writing back what n8n returned.

One file per workflow, flat, named after the workflow. The file is a single JSON object rather than
the one element array `n8n export:workflow` produces, because everything here goes through the API a
workflow at a time.

Two keys in a file are not part of the workflow itself. `id` is n8n's, written here by a pull so that
a later push updates rather than duplicates, and `tags` is the list of tag names the workflow should
carry. Tags are what keeps this workspace's work apart from every other workspace using the same n8n,
so a workflow without at least one is refused.
"""
from __future__ import annotations

import json
import re
from pathlib import Path


WORKFLOWS_DIR = Path(__file__).resolve().parent / "workflows"


def workflow_file_name(name: str) -> str:
    return re.sub(r"[^\w]+", "-", name.strip().lower()).strip("-") + ".json"


def read_workflows(directory: Path | None = None, name: str | None = None) -> list[dict]:
    """Every workflow file, or the one whose name matches."""
    directory = directory or WORKFLOWS_DIR
    if not directory.is_dir():
        return []

    definitions = []
    for path in sorted(directory.glob("*.json")):
        with open(path, encoding="utf-8") as workflow_file:
            definition = json.load(workflow_file)
        # An export straight out of n8n is a one element array. Accept it, so a file copied from the
        # n8n UI works without being reshaped by hand first.
        if isinstance(definition, list):
            definition = definition[0]
        if name and definition.get("name") != name:
            continue
        definitions.append(definition)
    return definitions


def write_workflows(workflows: list[dict], directory: Path | None = None) -> list[Path]:
    """
    Write what n8n returned, ids included.

    The ids are the point: without them the next push matches on name alone, and a rename would
    create a second workflow rather than renaming the one that is there.
    """
    directory = directory or WORKFLOWS_DIR
    directory.mkdir(parents=True, exist_ok=True)

    written = []
    for workflow in workflows:
        document = {
            "id": workflow["n8n_id"],
            "name": workflow["name"],
            "tags": workflow["tags"],
            **workflow["definition"],
        }
        path = directory / workflow_file_name(workflow["name"])
        path.write_text(json.dumps(document, indent=4) + "\n", encoding="utf-8")
        written.append(path)
    return written
