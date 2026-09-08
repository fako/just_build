## n8n workflows

Workflow definitions live in `n8n/workflows/`, one JSON file each, flat. They are moved between this
workspace and the shared n8n through the management API:

```bash
invoke n8n.sync              # push the directory, then write back what n8n holds. Use this one.
invoke n8n.push              # push only
invoke n8n.pull              # fetch only, overwriting the directory
invoke n8n.pull --output=/tmp/n8n   # fetch somewhere else, to look without overwriting anything
invoke n8n.tags              # tags this workspace owns
invoke n8n.tags --add=<name> # claim another
```

### Tags are not decoration

Every other workspace on this machine shares the same n8n. Tags are the only thing separating them: a
tag name belongs to exactly one workspace across the whole installation, and this workspace can only
see and change workflows carrying one of its own.

So a workflow file must list at least one tag, or the push is refused:

```json
{
    "id": "QZLAkiLCTmr6SZCllVtov",
    "name": "Github invoices",
    "tags": ["business"],
    "nodes": [],
    "connections": {},
    "settings": {"executionOrder": "v1"}
}
```

Pick whatever names suit the work. Claim a new one with `invoke n8n.tags --add=<name>`; a name another
workspace already holds comes back as an error naming the owner, and there is no way around it.

### Leave `id` alone

`id` is n8n's, written into the file by a pull or a sync. It is what makes an edit an edit: with it, a
renamed workflow is renamed in n8n; without it, a renamed workflow becomes a second workflow. Do not
invent one, and do not copy a file's id onto another file.

A workflow exported from a different n8n carries that instance's id, which will not resolve here.
Delete the `id` field before the first push and let sync write the real one back.

### State and credentials are not yours to keep

Whether a workflow is active or published is n8n's business, and nothing here records it. **Syncing a
workflow that is already published deploys it**, so treat `invoke n8n.sync` on a live workflow as a
release rather than a save.

Credentials are never stored in this workspace, and there is no task that would. The `credentials`
blocks inside nodes are references by id and are left exactly as they are, so a synced workflow keeps
using credentials someone created in the n8n UI. A workflow brought in from another n8n will point at
credential ids that do not exist here; that has to be fixed in the UI, not in these files.
