"""
The workflows namespace: housekeeping the host does, not the day-to-day work.

Day to day, a workspace syncs itself with `invoke n8n.sync` from inside its own home. These three
tasks are for the other times: seeing what the database holds across every workspace, refreshing that
mirror, and rebuilding n8n out of it after n8n has lost its own.

Claiming and releasing tags is deliberately not here. A workspace claims its own, and transferring
one hands over another workspace's workflows, which belongs in the admin where a human is looking.
"""
from __future__ import annotations

import json
from pathlib import Path

from invoke.context import Context
from invoke.tasks import task

from workspaces.cli.workflows import client


def print_workflows(workflows: list[client.WorkflowRecord]) -> None:
    if not workflows:
        print("No workflows stored. Run 'invoke workflows.pull --workspace-module=<module>' first.")
        return

    header = f"{'WORKSPACE':<24}{'NAME':<40}{'N8N ID':<24}TAGS"
    print(header)
    print("-" * len(header))
    for workflow in workflows:
        tags = ", ".join(workflow.tags) or "-"
        print(f"{workflow.workspace_module:<24}{workflow.name:<40}{workflow.n8n_id or '-':<24}{tags}")


def print_tags(tags: list[client.WorkflowTagRecord]) -> None:
    if not tags:
        print("No tags reserved yet. A workspace claims its own with 'invoke n8n.tags --add=<name>'.")
        return

    header = f"{'TAG':<32}{'WORKSPACE':<24}N8N ID"
    print(header)
    print("-" * len(header))
    for tag in tags:
        print(f"{tag.name:<32}{tag.workspace_module:<24}{tag.n8n_id or '-'}")


def write_workflow_files(workflows: list[client.WorkflowRecord], output: str) -> None:
    """
    Dump definitions in the shape a workspace tree uses, for reading rather than for editing.

    Grouped by workspace, because unlike the workspace side this covers all of them at once.
    """
    root = Path(output)
    for workflow in workflows:
        directory = root / workflow.workspace_module
        directory.mkdir(parents=True, exist_ok=True)
        document = {"id": workflow.n8n_id, "name": workflow.name, "tags": workflow.tags,
                    **workflow.definition}
        path = directory / f"{workflow.slug}.json"
        path.write_text(json.dumps(document, indent=4) + "\n", encoding="utf-8")
    print(f"Wrote {len(workflows)} workflow(s) below {root}")


@task(
    name="list",
    help={
        "workspace_module": "Limit the listing to one workspace",
        "tags": "List the tag reservations and their owners instead of the workflows",
    },
)
def list_workflows(ctx: Context, workspace_module: str | None = None, tags: bool = False):
    """List stored workflows, or the tag reservations that keep workspaces apart."""
    if tags:
        print_tags(client.list_tags(workspace_module))
    else:
        print_workflows(client.list_workflows(workspace_module))


@task(
    help={
        "workspace_module": "Workspace to refresh",
        "prune": "Also delete rows for workflows n8n no longer has",
        "output": "Additionally write the pulled definitions below this directory, for inspection",
    },
)
def pull(ctx: Context, workspace_module: str, prune: bool = False, output: str | None = None):
    """Refresh the stored mirror from n8n. Writes no files unless asked with --output."""
    workflows = client.pull_workflows(workspace_module, prune=prune)
    print_workflows(workflows)
    if output:
        write_workflow_files(workflows, output)


@task(
    help={"workspace_module": "Limit the rebuild to one workspace, otherwise every workspace is covered"},
)
def repush(ctx: Context, workspace_module: str | None = None):
    """
    Rebuild n8n from the stored mirror. The recovery path after n8n has lost its data.

    Workflows whose stored id no longer resolves are recreated, and the new ids are written back onto
    the stored rows, so a following pull finds the same workflows rather than a second copy.
    """
    results = client.repush_workflows(workspace_module)
    if not results:
        print("Nothing stored to repush.")
        return

    for module, pushed in sorted(results.items()):
        print("")
        print(f"{module}:")
        for result in pushed:
            print(f"  {result.action:<8}{result.name:<40}{result.n8n_id}")
