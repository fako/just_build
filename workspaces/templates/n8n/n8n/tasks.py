"""
Moving workflows between this workspace and the shared n8n.

`n8n.sync` is the one to reach for. It pushes what is in the workflows directory, then writes back
what n8n actually holds, so the files, management's records and n8n end up saying the same thing. The
separate push and pull halves are for when only one direction is wanted.
"""
from __future__ import annotations

from pathlib import Path

from invoke.collection import Collection
from invoke.context import Context
from invoke.exceptions import Exit
from invoke.tasks import task

from n8n import client
from n8n.files import WORKFLOWS_DIR, read_workflows, write_workflows


def report(pushed: list[dict]) -> None:
    for result in pushed:
        tags = ", ".join(result["tags"])
        print(f"  {result['action']:<8}{result['name']:<40}{result['n8n_id']:<24}{tags}")


def require_workflows(name: str | None) -> list[dict]:
    definitions = read_workflows(name=name)
    if not definitions:
        described = f"named '{name}'" if name else f"in {WORKFLOWS_DIR}"
        raise Exit(f"No workflows {described}.")
    return definitions


@task(help={"name": "Only sync the workflow with this name"})
def sync(ctx: Context, name: str | None = None):
    """Push the workflows directory to n8n, then write back what n8n holds. Start here."""
    result = client.sync(require_workflows(name))

    print("Pushed:")
    report(result["pushed"])
    written = write_workflows(result["workflows"])
    print("")
    print(f"Wrote {len(written)} workflow(s) to {WORKFLOWS_DIR}, ids included.")


@task(help={"name": "Only push the workflow with this name"})
def push(ctx: Context, name: str | None = None):
    """Push to n8n without writing anything back. Use sync unless you know you want only this half."""
    print("Pushed:")
    report(client.push(require_workflows(name)))


@task(help={"output": "Write to this directory instead of the workflows directory"})
def pull(ctx: Context, output: str | None = None):
    """Fetch what n8n holds. Overwrites the workflows directory unless --output says otherwise."""
    workflows = client.pull()
    if not workflows:
        print("n8n holds no workflows under this workspace's tags.")
        return

    written = write_workflows(workflows, Path(output) if output else None)
    for path in written:
        print(f"  {path}")


@task(help={"add": "Claim this tag name for the workspace"})
def tags(ctx: Context, add: str | None = None):
    """
    List the tags this workspace owns, or claim another.

    A tag name belongs to one workspace across the whole installation, so a name another workspace
    already took is refused. Every workflow needs at least one before it can be pushed.
    """
    if add:
        claimed = client.add_tag(add)
        print(f"Claimed '{claimed['name']}'.")
        return

    owned = client.list_tags()
    if not owned:
        print("No tags yet. Claim one with 'invoke n8n.tags --add=<name>'.")
        return
    for tag in owned:
        print(f"  {tag['name']}")


namespace = Collection("n8n", sync, push, pull, tags)
