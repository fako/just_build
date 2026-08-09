---
name: runtimes app
overview: Introduce a `runtimes` Django app in management that owns a polymorphic `SupervisordRuntime` hierarchy (Django and Celery to start), moves supervisord/nginx config *rendering* into management while file writes stay on the host through a thin CLI client, controls processes over supervisord XML-RPC, authenticates workspaces with a `workspace:<uuid>` API key that scopes every runtime action to the owning workspace, and reorganises the invoke namespaces into a rarely-used `workspaces.*` lifecycle and a commonly-used `runtimes.*` set of thin API clients.
todos:
  - id: management-container
    content: Add a management Dockerfile, a watch-enabled compose service on the control profile, and move setup_database into tasks/install.py so management stays runnable on the host.
    status: pending
  - id: api-key-auth
    content: Add workspace and control API keys to management, a django-ninja auth backend, and queryset-level principal scoping.
    status: pending
  - id: runtime-models
    content: Build the `runtimes` app with the Runtime table, SupervisordRuntime proxy hierarchy, DjangoRuntime, CeleryRuntime, and a type registry.
    status: pending
  - id: config-rendering
    content: Move the supervisord and nginx templates into management, expose rendered configs as a path/content manifest, and switch to nested log/config paths.
    status: pending
  - id: supervisor-rpc
    content: Expose supervisord over authenticated XML-RPC and add a management supervisor client with a testable fake.
    status: pending
  - id: runtime-api
    content: Add CRUD plus configs/reload/start/stop/restart/status/logs endpoints under `/api/v1/runtimes/`, scoped to the calling principal.
    status: pending
  - id: cli-reorganisation
    content: Split the invoke namespaces into `workspaces.*` (lifecycle) and `runtimes.*` (day-to-day, API-backed), add the config-tree reconciler, and delete the staged-config flow.
    status: pending
  - id: fabfile
    content: Add a fabfile for in-workspace remote work over the generated SSH aliases and route runtime sync commands through it.
    status: pending
  - id: celery-template-and-migration
    content: Add a Celery workspace template, an adoption path for existing workspaces, and update the docs.
    status: pending
isProject: true
---

# Runtimes Plan

## Problem And Shape Of The Solution

Today a "workspace" and "the one uvicorn process it runs" are the same thing. The identity leaks
everywhere: the supervisord program is named after the workspace module, the port is recovered by
grepping `--port` out of `.conf` files in
[workspaces/cli/common.py](workspaces/cli/common.py), the nginx domain is recovered by grepping
`server_name` back out of the generated file, `workspaces.sync` hard-codes `collectstatic` (a
Django-only concept), and the log path is `/var/log/projects/${module}.log`.

The change is to make **runtime** a first-class management-owned entity, one-to-many under
`Workspace`. Management becomes the authority on *what* every runtime's config says and the only
component that can control a process — but it never touches the host filesystem. Rendering is an API
response; materialising it is the CLI's job, so `workspaces/src/` stays owned by the host user.

```
                 ┌──────────────────────────────────────────────┐
 host CLI ──────►│ management  (control:<uuid> key)              │
 (invoke)  ◄─────│  runtimes app: models, rendering, RPC client  │
   │  config      └──────────────────────┬───────────────────────┘
   │  manifest                           │
   │                 workspace ──────────┤ XML-RPC (restart only)
   │ writes          (workspace:<uuid>)  │
   ▼                                     ▼
 workspaces/src/{supervisor,nginx}/  ┌──────────────────────────────┐
 <module>/<runtime>.conf ──bind mnt──► workspaces: supervisord+nginx│
                                     └──────────────────────────────┘
```

Three roles, cleanly separated:

| | renders config | writes files | controls processes | runs code inside a workspace |
| --- | --- | --- | --- | --- |
| management | ✅ | ❌ | ✅ (XML-RPC) | ❌ |
| invoke CLI (host) | ❌ | ✅ | ❌ | ✅ (SSH/fabric) |
| workspace | ❌ | ❌ | via API, own runtimes only | ✅ (it is the workspace) |

The invariant that falls out: **anything that changes the host filesystem goes through the CLI;
anything that only changes process state can go through the API.** A workspace can restart itself; it
cannot enable, disable, or reconfigure itself.

## Data Model

A single concrete table with a `type` discriminator and a JSON `configuration`, plus **proxy models**
for the class hierarchy. This matches the existing idiom in
[management/access_control/models.py](management/access_control/models.py) (`setup` and `ssh` are
already JSON), gives real Python subclassing for the shared rendering interface, and — importantly
for the Laravel/Node/FastAPI types you sketched — makes adding a runtime type a code-only change
with no migration.

```python
# management/runtimes/models/base.py
class Runtime(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey("access_control.Workspace", related_name="runtimes", on_delete=models.CASCADE)
    type = models.CharField(max_length=50, choices=runtime_type_choices)
    name = models.SlugField(max_length=64)          # "web", "worker"
    configuration = models.JSONField(default=dict, blank=True)
    port = models.PositiveIntegerField(null=True, blank=True, unique=True)
    is_enabled = models.BooleanField(default=False)
    created_at / modified_at

    class Meta:
        unique_together = ("workspace", "name")
        ordering = ("workspace__module", "name")

    def specialize(self) -> "Runtime":
        return RUNTIME_TYPES[self.type](**{f.name: getattr(self, f.name) for f in self._meta.fields})
```

```
Runtime                                  concrete, one table
└── SupervisordRuntime      (proxy)      renders a [program:] block, RPC-controllable
    ├── HttpRuntime         (proxy)      + owns a port and an nginx server block
    │   ├── DjangoRuntime   (proxy)      uvicorn ASGI, collectstatic on sync
    │   └── FastAPIRuntime  (proxy)      later
    ├── CeleryRuntime       (proxy)      celery worker, no port, no nginx
    └── NodeRuntime / LaravelRuntime     later
```

`HttpRuntime` is an intermediate proxy rather than a mixin so the "does this runtime own a port and
an nginx block" question is answered by `isinstance`, and so Node/Laravel can join it later without
being Python. Proxy-of-proxy is legal as long as the concrete base is shared.

### The runtime contract

Every subclass implements the same interface. This is the "common actions during development"
contract:

| Member | Returns | Who acts on it |
| --- | --- | --- |
| `program_name` | `f"{workspace.module}_{name}"` — globally unique in supervisord | — |
| `log_path` | `/var/log/workspaces/{module}/{name}.log` | — |
| `config_files()` | `[ConfigFile(path, content)]` — supervisord block, and nginx block for http runtimes | **CLI writes them** |
| `environment()` | dict merged into the `environment=` line | management (into the render) |
| `start()` / `stop()` / `restart()` / `status()` | supervisor RPC result | management |
| `tail_log(offset, length)` | log bytes via RPC `readProcessStdoutLog` | management |
| `sync_commands()` | ordered list of shell commands | **CLI, over SSH as the workspace user** |

Two of the seven members return *data for someone else to act on*, and that is deliberate — they are
exactly the two that need privileges management does not have (host filesystem, workspace user
identity). `DjangoRuntime.sync_commands()` returns `pip install -e .` plus `manage.py collectstatic
--noinput`; `CeleryRuntime`'s returns just the dependency install. The definition of every action
stays in one class hierarchy; the execution lands wherever the privilege already lives.

### Type configurations

Validated by a per-type pydantic schema in `management/runtimes/schemas.py`, so a bad
`configuration` payload is a 422 at the API edge rather than a broken ini file.

- `DjangoRuntime`: `django_module` (defaults to `workspace.django_module`), `asgi_module`, `workers`,
  `domain` (defaults to `{workspace.slug}.localhost`). Port is allocated by management, not scraped.
- `CeleryRuntime`: `app` (e.g. `web`), `queues`, `concurrency`, `loglevel`, `beat` (bool).

`domain` moves onto the http runtime because the runtime owns the nginx block; that deletes
`parse_workspace_domain()` and `extract_workspace_port()` / `next_workspace_port()` from
[workspaces/cli/common.py](workspaces/cli/common.py).

## Rendering Without Writing

Management renders; it returns a **manifest** of repo-relative paths and contents; the CLI
materialises it.

```json
GET /api/v1/runtimes/configs/            → control only, the full desired tree
{
  "root": "workspaces/src",
  "files": [
    {"path": "supervisor/magic_match/web.conf",    "content": "[program:magic_match_web]\n..."},
    {"path": "supervisor/magic_match/worker.conf", "content": "[program:magic_match_worker]\n..."},
    {"path": "nginx/magic_match/web.conf",         "content": "upstream magic_match_web_upstream ..."}
  ]
}
```

The manifest is the **complete** desired state for every enabled runtime, not a diff. The CLI's
reconciler writes each file and deletes any `.conf` under `root` that is not listed — so disabling a
runtime, renaming one, or removing a workspace all prune themselves with no delete-tracking logic on
either side. `GET /api/v1/runtimes/{id}/configs/` returns the same shape scoped to one runtime for
targeted operations.

Since the client writes files from an HTTP response, the reconciler validates that every resolved
path stays inside `workspaces/src/` and ends in `.conf` before touching disk. Management is trusted,
but a thin client that writes arbitrary server-supplied paths is the wrong shape regardless.

Ordering, because it matters: **write first, reload second.** Every mutating flow is

```
PATCH/POST the runtime  →  GET the manifest  →  reconcile the tree  →  POST /runtimes/reload/
```

and `reload` is the only step that talks to supervisord (`reread`, `update`, `signal HUP nginx`).
`is_enabled` is management's record of intent; the manifest plus reconciler is what makes disk match
it, and re-running the reconcile is always safe.

Templates move out of `workspaces/` into management, rendered by Django's template engine
(`APP_DIRS` is already on):

```
management/runtimes/templates/runtimes/supervisord/{django,celery}.conf
management/runtimes/templates/runtimes/nginx/http.conf
```

They replace [workspaces/supervisor/example.conf.template](workspaces/supervisor/example.conf.template)
and [workspaces/nginx/example.conf.template](workspaces/nginx/example.conf.template) and their
`PROJECT_NAME`-style string replacement.

## Authentication And Enforcement

Two principals, one header, one code path.

```
Authorization: Bearer workspace:<uuid>    →  WorkspacePrincipal(workspace)
Authorization: Bearer control:<uuid>      →  ControlPrincipal()   # the invoke CLI on the host
```

- `Workspace` gains `api_key_hash` (sha256 of the full token, unique, indexed) and
  `api_key_created_at`. The plaintext is returned **once** from `POST /api/v1/workspaces/` and from a
  `POST /api/v1/workspaces/{module}/rotate-key/` endpoint; the CLI writes it into the workspace's
  `.env`. Storing only the hash means the management database is not a key vault, and lookup is a
  single indexed equality on the hash — the `workspace:` prefix only selects which principal table to
  consult, so `control:` keys slot in without ambiguity.
- The scheme prefix is required. A token without a recognised prefix is a 401, never a fallback.
- New file `management/access_control/authentication.py` holds the ninja `HttpBearer` subclass and
  the principal objects. `request.auth` is the principal.

Enforcement is **queryset scoping, not per-view checks**:

```python
class WorkspacePrincipal:
    def runtimes(self) -> QuerySet[Runtime]:
        return Runtime.objects.filter(workspace=self.workspace)

class ControlPrincipal:
    def runtimes(self) -> QuerySet[Runtime]:
        return Runtime.objects.all()
```

Every runtime endpoint resolves its object through `request.auth.runtimes()`. A workspace asking for
another workspace's runtime gets a 404, not a 403 — no existence leak, and there is no endpoint where
someone can forget the check, because there is no unscoped manager access in the controller module.
A test parametrised over every runtime route asserts cross-workspace access is 404 and missing auth
is 401.

`NinjaAPI` is constructed with a default `auth=`, so any future controller is authenticated unless it
explicitly opts out. `/api/v1/workspaces/` becomes control-only except for a workspace reading its
own record.

## Process Control: Supervisord Over XML-RPC

Management controls processes through supervisord's own XML-RPC interface rather than
`docker compose exec`, which it could not reach from its own container anyway.

In [workspaces/supervisor/supervisord.conf](workspaces/supervisor/supervisord.conf):

```ini
[inet_http_server]
port=0.0.0.0:9001
username=management
password=%(ENV_WORKSPACES_SUPERVISOR_PASSWORD)s

[include]
files = /etc/supervisor/conf.d/*.conf /etc/supervisor/conf.d/*/*.conf
```

Published as `127.0.0.1:9001:9001` — loopback only. Publishing is required so that management running
*on the host* (see below) can still drive supervisord, and it gives up nothing: the port is already
reachable from inside the container by anyone there, and on the host side it is exposed only to the
user who already owns the whole repo. It is password-protected either way.

Two details matter:

1. Supervisord passes its own environment to every child process, so workspace users would inherit
   `WORKSPACES_SUPERVISOR_PASSWORD` and could then drive supervisord for other workspaces. The fix
   follows the pattern already used for the opencode secret in
   [workspaces/container/](workspaces/container/): ship `supervisor/supervisord.conf.template`, have
   a `load_supervisor_secret.sh` render it into `/etc/supervisor/supervisord.conf`, then `unset` the
   variable in [docker-entrypoint.sh](workspaces/container/docker-entrypoint.sh) before `exec "$@"`.
2. nginx reloads also stop being a `docker exec`. nginx already runs as `[program:nginx]` under
   supervisord, so management calls `signalProcess("nginx", "HUP")` over the same RPC connection.

`management/runtimes/supervisor.py` defines a narrow `SupervisorClient` protocol
(`start/stop/restart/status/signal/reread/update/read_log`) with an XML-RPC implementation and an
in-memory fake selected by settings, so the whole API layer is testable without a container.

## Config And Log Layout

Both trees become nested per workspace, mirroring each other and the new log path:

```
workspaces/src/supervisor/<module>/<runtime>.conf   → /etc/supervisor/conf.d/<module>/<runtime>.conf
workspaces/src/nginx/<module>/<runtime>.conf        → /etc/nginx/conf.d/<module>/<runtime>.conf
                                                      /var/log/workspaces/<module>/<runtime>.log
```

- `[include] files` gains the `*/*.conf` glob (above); nginx's `include /etc/nginx/conf.d/*.conf` in
  [workspaces/nginx/nginx.conf](workspaces/nginx/nginx.conf) gains `/etc/nginx/conf.d/*/*.conf`.
- The [Dockerfile](workspaces/Dockerfile) creates `/var/log/workspaces` instead of
  `/var/log/projects`. Supervisord will not create parent directories, so `workspaces.create` creates
  `/var/log/workspaces/<module>` as `root:<module>` mode `750` via the existing `docker_exec` helper
  — which also means a workspace can read its own logs and nobody else's.
- **The `staged/` directory disappears.** Staging exists today only because the filesystem was the
  source of truth; now the management database is, and `is_enabled` plus the manifest reconciler
  replace it exactly. `runtimes.enable` flips the flag and reconciles, same `create` → `enable` UX,
  one less concept and one less directory in
  [workspaces/cli/constants.py](workspaces/cli/constants.py).

## API Surface

```
GET    /api/v1/runtimes/                        list (scoped)
POST   /api/v1/runtimes/                        create              control only
GET    /api/v1/runtimes/{id}/                   read
PATCH  /api/v1/runtimes/{id}/                   update configuration control only
DELETE /api/v1/runtimes/{id}/                   delete              control only
POST   /api/v1/runtimes/{id}/enable/            set is_enabled      control only
POST   /api/v1/runtimes/{id}/disable/           clear is_enabled    control only
GET    /api/v1/runtimes/configs/                full desired file manifest       control only
GET    /api/v1/runtimes/{id}/configs/           manifest for one runtime         control only
GET    /api/v1/runtimes/{id}/sync-commands/     commands to run over SSH         control only
POST   /api/v1/runtimes/reload/                 supervisord reread/update + nginx HUP
POST   /api/v1/runtimes/{id}/restart/           ← the endpoint a workspace calls
POST   /api/v1/runtimes/{id}/start/
POST   /api/v1/runtimes/{id}/stop/
GET    /api/v1/runtimes/{id}/status/
GET    /api/v1/runtimes/{id}/logs/              tail via RPC
```

The control-only block is precisely the set that assumes a host filesystem behind the caller. What a
workspace keeps is read, restart/start/stop, status, and logs — for its own runtimes only.

Runtimes are addressed by UUID rather than `{module}/{name}` so a workspace cannot construct another
workspace's URL by guessing, and so the scoped-queryset lookup is the only resolution path.

## Invoke Namespace Reorganisation

```
workspaces.*   rare, lifecycle, privileged (SSH + docker exec + postgres)
  setup      unchanged — SSH host keys
  create     + creates the api key, writes it to .env, creates the log dir; no longer stages configs
  init       unchanged
  update     venv rebuild from pyproject
  clean      database reset + migrate + superusers
  remove     unchanged in spirit, deletes runtimes by cascade
  list       new, thin

runtimes.*   common, thin clients over the management API
  list       [--workspace-module=]
  add        --workspace-module --type=django|celery --name
  enable / disable    flip the flag, reconcile the tree, reload
  apply      reconcile the whole tree from the manifest and reload; idempotent, the repair command
  restart / start / stop / status
  logs       --follow
  sync       fetch sync-commands, run them over SSH, then restart
  remove
```

`workspaces.enable` and `workspaces.sync` are the two that split. `enable` becomes `runtimes.enable`
because config is now per runtime. `sync` becomes `runtimes.sync`, and the Django-specific
`collectstatic` in [workspaces/cli/sync.py](workspaces/cli/sync.py) stops being hard-coded — it comes
back from `sync-commands/` for a `DjangoRuntime` and simply is not there for a `CeleryRuntime`.

New CLI modules: `workspaces/cli/runtimes/` (client + tasks) and `workspaces/cli/configs.py` (the
manifest reconciler, the only writer of `workspaces/src/{supervisor,nginx}`).
[workspaces/cli/client.py](workspaces/cli/client.py) grows an auth header; the control key comes from
`INVOKE_MANAGEMENT_API_KEY` via [activate.sh](activate.sh) and [.env.example](.env.example).

## Fabric For In-Workspace Work

Everything the CLI does *inside* a workspace already goes through fabric — `build_ssh_connection()`
in [workspaces/cli/common.py](workspaces/cli/common.py) hands back a `fabric.Connection`, built by
hand from the workspace's SSH metadata even though
[workspaces/ssh/config](workspaces/ssh/config) already contains a generated `Host <slug>-workspace`
alias for exactly this.

Add a root `fabfile.py` whose tasks take fabric's native `-H <alias>` and let fabric's own
`~/.ssh/config` handling resolve them, covering the utility layer from
[TODO.md](TODO.md) — `login`, `copy`, `tail`, `run` — plus the in-workspace half of the runtime
contract:

```bash
fab -H magic-match-workspace login
fab -H magic-match-workspace tail --runtime=worker
fab -H magic-match-workspace runtime.sync --runtime=web    # executes sync_commands()
```

`runtimes.sync` stays the friendly entry point (it resolves the module, fetches `sync-commands/`,
and restarts afterwards) but delegates the actual remote execution to a shared helper the fabfile
also exposes. The split is: **invoke tasks are addressed by workspace module and orchestrate;
fabric tasks are addressed by host alias and execute.** That also removes `build_ssh_connection()`'s
hand-rolled key resolution in favour of the generated config that is already being maintained.

The one prerequisite is the `Include` line in `~/.ssh/config` that
[workspaces/README.md](workspaces/README.md) already documents; `workspaces.setup` should check for
it and say so, since the fabfile stops working silently without it.

## Management Service

Management stays a normal Django project that runs fine on the host. The container is a convenience,
not a requirement, and nothing in the CLI shells into it.

```yaml
management:
  profiles: [control, workspaces]          # deliberately NOT in `services`
  build: ./management
  ports: ["127.0.0.1:8000:8000"]
  volumes:
    - ./management:/management             # code only — no workspaces/src mounts
  environment:
    - INVOKE_MANAGEMENT_*
    - WORKSPACES_SUPERVISOR_URL=http://workspaces:9001/RPC2
    - WORKSPACES_SUPERVISOR_PASSWORD=${INVOKE_WORKSPACES_SUPERVISOR_PASSWORD}
  depends_on: { postgres: { condition: service_healthy } }
  develop:
    watch:
      - action: sync
        path: ./management
        target: /management
      - action: rebuild
        path: ./management/requirements.txt
```

- **`COMPOSE_PROFILES=services`** brings up postgres, redis and tika only — then
  `python manage.py runserver` on the host for debugging, with a debugger attached, against the same
  database. `postgres` and `workspaces` already resolve from the host thanks to
  `invoke install.hosts-file`, so the same `WORKSPACES_SUPERVISOR_URL` works in both modes and no
  settings branch is needed.
- **`COMPOSE_PROFILES=control`** or `workspaces` brings the container up with `docker compose watch`
  syncing code on save.
- **No `workspaces/src` mounts**, because management no longer writes files. That also disposes of
  the file-ownership problem entirely — nothing root-owned appears in the repo.

`setup_database` moves out of [management/cli.py](management/cli.py) into
[tasks/install.py](tasks/install.py) and becomes part of installation, next to `hosts_file`. It runs
on the host against the published postgres port exactly as it does now — no `docker compose exec`
anywhere. `management.test` and `setup_test_store` stay in `management/cli.py` and stay host-side,
which also means the `pass`/gpg dependency of the `credentials` app never has to be solved inside a
container. The management image still installs `gnupg` and `pass` so the containerised mode is not
crippled, but nothing in the workflow depends on it.

## Sequenced Work

Each phase leaves the repo working.

1. **`management-container`** — Dockerfile, watch-enabled compose service on the `control` profile,
   `setup_database` moved into `tasks/install.py`, docs for both run modes. No behaviour change.
2. **`api-key-auth`** — `api_key_hash` migration, `authentication.py`, principals, default `auth=` on
   `NinjaAPI`, control key in `.env`/`activate.sh`, CLI sends the header. Workspace `.env` gains
   `MANAGEMENT_URL` and `WORKSPACE_API_KEY` in `render_workspace_secret_env()`. Tests: 401, wrong
   scheme, cross-workspace 404 on the existing workspace routes.
3. **`runtime-models`** — the app, `Runtime`, the proxy hierarchy, registry, pydantic config schemas,
   admin, migration. Pure model layer; golden-output tests for `program_name`, `log_path`, and
   `environment()`.
4. **`config-rendering`** — Django templates for the supervisord and nginx blocks, `config_files()`
   and the manifest schema, nested log/config paths, supervisord and nginx include globs,
   `/var/log/workspaces` in the Dockerfile. Golden-file tests for both runtime types.
5. **`supervisor-rpc`** — `inet_http_server`, the entrypoint template/unset dance, `SupervisorClient`
   plus fake, nginx reload via `signalProcess`.
6. **`runtime-api`** — the controllers above, all resolving through `request.auth.runtimes()`, plus
   `management/access_control/tests/manual/runtimes.http` alongside the existing
   [workspaces.http](management/access_control/tests/manual/workspaces.http). Cross-workspace denial
   test parametrised over every route, and a test that the control-only routes reject a workspace key.
7. **`cli-reorganisation`** — `workspaces/cli/runtimes/`, the `configs.py` reconciler with its
   path-containment check and prune-stale behaviour, `workspaces.create` drops staging and gains key
   + log dir, `sync`/`enable` move, `staged/` and the port/domain scrapers deleted,
   `workspaces/cli/tests/` updated.
8. **`fabfile`** — root `fabfile.py` with `login`/`copy`/`tail`/`run` plus the sync executor,
   `runtimes.sync` delegating to it, `build_ssh_connection()` retired in favour of the generated SSH
   config, `workspaces.setup` checking for the `Include` line.
9. **`celery-template-and-migration`** — a `workspaces/templates/celery/` template (celery + redis in
   `pyproject.tpl.toml`, `web/celery.py`, settings snippet) so `CeleryRuntime` has something real to
   run; a `runtimes.adopt` task that creates a `DjangoRuntime` for each existing workspace from its
   current `.conf` (port included), then `runtimes.apply` materialises the new tree and the flat
   files prune themselves; README, INSTALLATION and TODO updates.

## Out Of Scope

FastAPI, Node and Laravel runtimes are designed for but not implemented — the registry and templates
are the only places they will need to touch. Key rotation gets an endpoint but no scheduled rotation.
`/var/log/projects` is left in place on existing containers rather than migrated; step 9 recreates
the logs under the new path and the old directory can be dropped on the next rebuild.
