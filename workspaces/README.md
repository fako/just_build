# Multi-Runtime Workspaces for Agents

This container runs the processes of multiple workspaces under a single nginx reverse proxy managed
by supervisord.

A **workspace** is a Linux user with a home directory, a database and SSH access. A **runtime** is
one process that workspace runs. A workspace owns as many runtimes as it needs: `magic_match` owns a
`DjangoRuntime` called `web` and a `CeleryRuntime` called `worker`.

Runtimes live in the management service, which renders their supervisord and nginx configuration and
controls their processes over supervisord's XML-RPC interface. Management never writes to the host
filesystem; it returns a manifest and `invoke runtimes.apply` materialises it. A workspace can
restart its own runtimes through the management API with the key in its `.env`, and gets a 404 for
anybody else's.

## Architecture

```
                  ┌──────────────────────────────────────────┐
 invoke CLI ─────►│ management (control:<uuid>)              │
 (host)     ◄─────│ renders config, controls processes       │
   │  manifest    └──────────────┬───────────────────────────┘
   │ writes                      │ XML-RPC :9001
   ▼                             ▼
 workspaces/src/{supervisor,nginx}/<module>/<runtime>.conf
   │ bind mount                  │
   ▼                             ▼
┌─────────────────────────────────────────────────────────────┐
│                    Nginx (port 7000)                        │
│         Routes requests based on hostname/subdomain         │
└──────────────┬──────────────────────────────┬───────────────┘
               │                              │
               ▼                              ▼
        ┌──────────────┐               ┌──────────────┐
        │ magic_match  │               │ other        │
        │  web  :8001  │  worker       │  web  :8002  │
        │  (Django)    │  (Celery)     │  (Django)    │
        └──────┬───────┘               └──────┬───────┘
               ▼                              ▼
        /home/magic_match               /home/other
        /var/log/workspaces/magic_match/{web,worker}.log

┌─────────────────────────────────────────────────────────────┐
│                    SSHD (port 2222)                         │
│         Remote development access for AI agents             │
│         Each workspace user → /home/{module}                │
└─────────────────────────────────────────────────────────────┘
```

Each workspace can restart its own runtimes by calling the management API with the
`WORKSPACE_API_KEY` in its `.env`, and cannot see or touch any other workspace's.

## Quick Start

```bash
# Generate SSH host keys (first time only)
invoke workspaces.setup

# Start the container
docker compose --profile workspaces up --build

# Create the workspace, its Linux account, secrets, SSH access and its own git key
invoke workspaces.create --name="My Workspace" --module=my_workspace

# Scaffold a new Django project and the workspace templates over SSH as the workspace user
invoke workspaces.scaffold --workspace-module=my_workspace

# Add the web runtime, install what it needs, and start it
invoke runtimes.add --workspace-module=my_workspace --type=django --name=web
invoke runtimes.install --workspace-module=my_workspace --name=web
invoke runtimes.enable --workspace-module=my_workspace --name=web
```

Starting from an existing repository instead of a scaffold replaces one step. Add the workspace's
public key to the repository as a deploy key first; `workspaces.create` prints it, and the
management admin keeps it:

```bash
invoke workspaces.clone-repo --workspace-module=my_workspace \
    --repository=git@github.com:owner/repository.git
```

To add a Celery worker as well, layer the celery template and add a second runtime:

```bash
invoke workspaces.scaffold --workspace-module=my_workspace --templates=default,celery
invoke runtimes.add --workspace-module=my_workspace --type=celery --name=worker
invoke runtimes.install --workspace-module=my_workspace --name=worker
invoke runtimes.enable --workspace-module=my_workspace --name=worker
```

## Namespaces

| Namespace | Addressed by | What it does |
| --- | --- | --- |
| `workspaces.*` | workspace module | The privileged lifecycle a workspace needs once. Rarely used. |
| `runtimes.*` | workspace module and runtime name | Day-to-day process and configuration work, through the management API. |
| `remote.*` | workspace module | Utility work inside a workspace over SSH: login, run, copy, fetch, tail. |

The module is the canonical workspace identifier and is used for Linux users, home directories, databases, SSH, and
supervisor. Its URL-safe slug is generated automatically with underscores converted to hyphens, so `my_workspace`
uses `my-workspace.localhost` by default.

During creation, the workspace home and secrets directory receive an ACL granting the host user that ran the
command read/write access. The default ACL is inherited by new files and directories while container ownership
remains with the workspace user.

## Two keypairs

A workspace is at both ends of an SSH connection, with a different key for each direction.

| Key | Private half | Public half | What it is for |
| --- | --- | --- | --- |
| Inbound | `workspaces/src/ssh/<module>/id_ed25519` on the host | container `authorized_keys` | The CLI, Fabric and Cursor Remote SSH signing in to the workspace |
| Outbound | `/home/<module>/.ssh/id_ed25519` inside the workspace | `git_public_key` on the workspace in management | The workspace signing in to its git remotes |

The outbound key is generated by `workspaces.create`, inside the workspace, protected with a password it asks for.
Nothing stores that password: every command that reaches a git remote loads the key into an ssh-agent that lives
only for that command, and `ssh-add` asks for it again. Add its public half to a repository as a deploy key to let
the workspace clone and push; `workspaces.create` prints it and the management admin keeps it.

## Directory Structure

```
workspaces/
├── Dockerfile
├── README.md
├── requirements.txt         # Python dependencies
├── nginx/
│   └── nginx.conf           # Main nginx config (port 7000)
├── supervisor/
│   └── supervisord.conf.template  # Main supervisor config, rendered at container start
├── ssh/
│   ├── sshd_config          # SSH daemon config (port 22 → 2222)
│   ├── config               # Generated SSH config include file
│   └── keys/                # Container host keys (private gitignored)
├── templates/
│   ├── default/             # Django project scaffolding
│   └── celery/              # Adds a Celery app and dependency
└── src/
    ├── nginx/<module>/      # Rendered nginx configs (gitignored)
    ├── ssh/                 # Workspace SSH keypairs (gitignored)
    ├── supervisor/<module>/ # Rendered supervisor configs (gitignored)
    └── repos/               # Workspace git repositories → /home/
```

Everything under `src/nginx` and `src/supervisor` is generated. `invoke runtimes.apply` rewrites it
from what management renders and deletes whatever management no longer lists, so edits there do not
survive.

## Project Onboarding

### Automated flow

For the common SSH-first workflow, use:

```bash
invoke workspaces.create --name="My Workspace" --module=my_workspace
invoke workspaces.scaffold --workspace-module=my_workspace
invoke runtimes.add --workspace-module=my_workspace --type=django --name=web
invoke runtimes.install --workspace-module=my_workspace --name=web
invoke runtimes.enable --workspace-module=my_workspace --name=web
```

The commands do the following:
- `workspaces.create` creates the management workspace and its API key, the Linux user, home and log
  directories, secrets, SSH access, the workspace's own git keypair, and refreshes `workspaces/ssh/config`
- `workspaces.scaffold` connects over SSH as the workspace user, runs `django-admin startproject <django_module> .`,
  resolves workspace templates, and by default initializes git and creates the initial commit
- `workspaces.clone-repo` is the alternative to scaffolding: it brings an existing repository into the same
  home directory, using the workspace's own key
- `runtimes.add` records the runtime in management and allocates its port
- `runtimes.install` runs what the runtime type needs inside the workspace, which is what makes it enableable
- `runtimes.enable` writes its configuration and starts it under supervisord
- `workspaces.remove` removes the workspace repository, secrets, SSH keys and authorization, runtimes and
  their configs, logs, Linux account, PostgreSQL databases and role, and management record

To remove a workspace completely from the host and containers:

```bash
invoke workspaces.remove --workspace-module=my_workspace
```

The command displays the resources that will be permanently deleted and requires an explicit `y` or `yes` before
starting cleanup.

By default, `workspaces.scaffold` applies the `default` template from `workspaces/templates/default`. Templates are
layered in the comma-separated order provided, so later templates overwrite files from earlier templates:

```bash
invoke workspaces.scaffold --workspace-module=my_workspace --templates=default,custom
```

To create the Django workspace without initializing a git repository, use:

```bash
invoke workspaces.scaffold --workspace-module=my_workspace --no-git
```

Templates overwrite what they cover, so scaffolding is for new projects only, and it refuses to run when the
workspace home already holds a git repository. A repository that already exists comes in through
`workspaces.clone-repo`, which applies no templates at all.

Interactive SSH shells automatically activate `/home/<workspace-module>/venv` once it exists. Workspace creation
installs this behavior in both `.profile` and `.bashrc`, covering Bash login and non-login interactive shells.

### 1. Create Database

Each project needs its own PostgreSQL database. Run inside the postgres container:

```bash
docker exec -it postgres /scripts/setup_database.sh
```

With environment variables:

```bash
docker exec -e DATABASE_NAME=myproject_db \
            -e DATABASE_USER=myproject_user \
            -e DATABASE_PASSWORD=secure_password \
            postgres /scripts/setup_database.sh
```

This creates:
- A new database with the given name
- A new role/user with full access to that database
- Proper schema permissions

### 2. Configure Redis

Each project should use a unique Redis key prefix to isolate its data:

```python
# Django settings.py
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": "redis://redis:6379/0",
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
        },
        "KEY_PREFIX": "myproject",  # Unique per project
    }
}

# Celery
CELERY_BROKER_URL = "redis://redis:6379/1"
CELERY_RESULT_BACKEND = "redis://redis:6379/1"
CELERY_TASK_DEFAULT_QUEUE = "myproject"  # Unique queue name
```

### 3. Create System User

`workspaces.create` creates the Linux user automatically. If you need to create one manually, write it into the
persistent account database and sync it into the running container:

```bash
docker exec workspaces groupadd -P /workspaces/state myproject
docker exec workspaces useradd -P /workspaces/state -M -s /bin/bash -g myproject myproject
docker exec workspaces sh -c 'P=/workspaces/state/etc; cp -a "$P/passwd" "$P/group" "$P/shadow" "$P/gshadow" /etc/'
docker exec workspaces sh -c 'mkdir -p /home/myproject && chown -R myproject:myproject /home/myproject'
```

Workspace Linux users are stored in the persistent Docker volume `workspaces_state` (mounted at `/workspaces/state`).
The container entrypoint syncs `/workspaces/state/etc/{passwd,group,shadow,gshadow}` into `/etc` before supervisord
starts, so users survive container rebuilds and recreates.

#### SSH Access for AI Agents (Cursor Remote SSH)

The workspaces container exposes SSH on port 2222 for remote development with Cursor or other editors. To enable SSH access for a project user:

Run `invoke workspaces.create --name="My Workspace" --module=my_workspace` to generate the inbound workspace SSH
keypair and publish the public key into the container-managed `authorized_keys` volume automatically. The same
command generates the workspace's outbound git keypair; see [Two keypairs](#two-keypairs) for which is which.

The container uses an internal Docker volume for `/etc/ssh/authorized_keys/`, and sshd is configured to look for
`/etc/ssh/authorized_keys/%u` (where `%u` is the username). The private key remains on the host under
`workspaces/src/ssh/<workspace-module>/` so Cursor, Fabric, and local SSH tooling can use it.

The generated public key is published inside the container by `workspaces.create`, and the Python CLI expects the
`/etc/ssh/authorized_keys` directory permissions to come from the image/container environment rather than fixing
them at runtime.

You can test access directly with:

```bash
ssh -i workspaces/src/ssh/myproject/id_ed25519 myproject@localhost -p 2222
```

**Connecting with Cursor Remote SSH**

1. Open Cursor
2. Press `Ctrl+Shift+P` (or `Cmd+Shift+P` on macOS)
3. Type "Remote-SSH: Connect to Host..."
4. Enter: `myproject-workspace`
5. Cursor opens with `/home/myproject` as the workspace root

All terminal commands and AI agent actions will run as the `myproject` user:
- `whoami` → `myproject`
- `pwd` → `/home/myproject`
- `hostname` → container ID

**SSH Config Include**

Add this once to `~/.ssh/config` so generated project entries are available to both OpenSSH and Cursor:

```ssh-config
Include /absolute/path/to/just_build/workspaces/ssh/config
```

The generated include file contains entries like:

```ssh-config
Host myproject-workspace
    HostName localhost
    Port 2222
    User myproject
    IdentityFile /absolute/path/to/just_build/workspaces/src/ssh/myproject/id_ed25519
```

### 4. Scaffold or Clone the Project Repository

The project home lives under `workspaces/src/repos/<workspace-module>/` and is mounted to `/home/<workspace-module>/`
inside the container.

For a new project, scaffold it over SSH as the project user:

```bash
invoke workspaces.scaffold --workspace-module=my_workspace
```

This command runs `django-admin startproject <django_module> .` and applies workspace templates. By default it also
initializes git, sets local commit identity, and creates the initial commit; pass `--no-git` to skip all git actions.

For an existing project, clone it instead:

```bash
invoke workspaces.clone-repo --workspace-module=my_workspace \
    --repository=git@github.com:owner/repository.git --branch=main
```

Only SSH URLs are accepted, because the workspace authenticates as itself rather than as whoever ran the command.
Leave `--branch` out to take the default branch of the remote. The home directory is never empty, so this creates
the repository in place and fetches into it rather than running `git clone`; git refuses to overwrite the untracked
files that give the workspace its shell environment, which is the safety net if a repository carries its own
`.profile` or `.bashrc`.

The two commands are not interchangeable. Scaffolding writes templates over whatever it finds, so it refuses a home
directory that already holds a git repository and points at `clone-repo` instead.

When the remote does not accept the key, the command says so and prints the public key to add as a deploy key.
That is the same key as the one on the workspace in the management admin.

### 5. Add Runtimes

A workspace runs nothing until it has runtimes. Management renders their configuration; the CLI
writes it.

```bash
invoke runtimes.add --workspace-module=my_workspace --type=django --name=web
invoke runtimes.add --workspace-module=my_workspace --type=celery --name=worker \
    --configuration='{"concurrency": 4}'
invoke runtimes.install --workspace-module=my_workspace --name=web
invoke runtimes.install --workspace-module=my_workspace --name=worker
invoke runtimes.enable --workspace-module=my_workspace --name=web
invoke runtimes.enable --workspace-module=my_workspace --name=worker
```

`runtimes.add` allocates the port for HTTP runtimes, so nothing has to be tracked by hand.
`runtimes.install` builds the workspace virtualenv and runs whatever else the type needs; management
answers 409 to enabling a runtime that has not been installed. `runtimes.enable` writes the config,
creates the log directory, and starts the process.

The virtualenv belongs to the workspace rather than to one runtime, so a second `runtimes.install`
installs into the one that is already there. To throw it away and build it again, which stops and
restarts everything in the workspace:

```bash
invoke runtimes.install --workspace-module=my_workspace --name=web --rebuild
```

Each type renders its own supervisord program and, if it serves HTTP, its own nginx server block:

| Type | Command | Port | nginx block |
| --- | --- | --- | --- |
| `django` | uvicorn against `<django_module>.asgi` | allocated | yes |
| `celery` | `celery --app <module> worker` | none | no |

Configuration is validated against a schema per type, so a typo is rejected rather than written into
a broken ini file:

```bash
invoke runtimes.configure --workspace-module=my_workspace --name=web \
    --configuration='{"workers": 4, "domain": "my-workspace.localhost"}'
```

### 6. Adopting a workspace from before runtimes

Workspaces created before runtimes existed have a flat config naming their supervisord program after
the workspace. Adoption reads the port and domain out of it, creates the runtime that describes it,
and lets the reconciler replace the flat file:

```bash
invoke runtimes.adopt --workspace-module=my_workspace
```

### 7. Local DNS Setup

Add entries to `/etc/hosts` for local development:

```
127.0.0.1 myproject.localhost
127.0.0.1 another-project.localhost
```

Or use dnsmasq to route all `*.localhost` to 127.0.0.1.

## Managing Runtimes

Everything below goes through management, which is what a workspace's own agent can do for itself
too. No `docker exec` required.

### Status

```bash
invoke runtimes.status
invoke runtimes.status --workspace-module=my_workspace
```

### Logs

```bash
# Through supervisord, from the host
invoke runtimes.logs --workspace-module=my_workspace --name=web

# Or from inside the workspace, following
invoke remote.tail --workspace-module=my_workspace --runtime=web --follow
```

Logs live at `/var/log/workspaces/<module>/<runtime>.log`. The directory is readable by the
workspace group and nobody else, so a workspace only ever sees its own.

### Restart, start and stop

```bash
invoke runtimes.restart --workspace-module=my_workspace --name=web
invoke runtimes.stop --workspace-module=my_workspace --name=worker
```

An agent inside a workspace does the same over HTTP, with the key in its `.env`:

```bash
curl -X POST -H "Authorization: Bearer $WORKSPACE_API_KEY" \
    "$MANAGEMENT_URL/api/v1/runtimes/$RUNTIME_ID/restart/"
```

It can list its own runtimes to find the id, and gets a 404 for anything belonging to another
workspace.

### Sync after a dependency or static file change

```bash
invoke runtimes.sync --workspace-module=my_workspace --name=web
```

Management decides what syncing means for each type: a Django runtime installs dependencies and runs
`collectstatic`, a Celery runtime only installs dependencies. The CLI runs those commands over SSH as
the workspace user and restarts the runtime afterwards.

### Repair the configuration tree

```bash
invoke runtimes.apply
```

Rewrites every enabled runtime's config from management and prunes what is no longer listed. Safe to
run at any time; that is the point of it.

### Enter the container

```bash
docker exec -it workspaces bash
invoke remote.login --workspace-module=my_workspace
```

## Project Requirements

Each ASGI project should:

1. Have an ASGI application (e.g., `config/asgi.py` for Django)
2. Configure `ALLOWED_HOSTS` to accept the project domain
3. Use environment variables for database/redis connections
4. Have a `requirements.txt` for dependencies (install manually or in Dockerfile)

Example Django `config/settings.py`:

```python
import os

ALLOWED_HOSTS = [os.environ.get('PROJECT_DOMAIN', 'localhost'), 'localhost']
CSRF_TRUSTED_ORIGINS = [f"http://{os.environ.get('PROJECT_DOMAIN', 'localhost')}:7000"]
USE_X_FORWARDED_HOST = True
USE_X_FORWARDED_PORT = True

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('DATABASE_NAME', 'myproject_db'),
        'USER': os.environ.get('DATABASE_USER', 'myproject_user'),
        'PASSWORD': os.environ.get('DATABASE_PASSWORD', ''),
        'HOST': os.environ.get('DATABASE_HOST', 'postgres'),
        'PORT': os.environ.get('DATABASE_PORT', '5432'),
    }
}
```

## Port Allocation

**External Ports (exposed to host):**

| Service | Host Port | Container Port | Purpose |
|---------|-----------|----------------|---------|
| Nginx   | 7000      | 7000           | HTTP reverse proxy |
| SSHD    | 2222      | 22             | Remote development |

| Supervisord RPC | 9001 | 9001 | Management process control, loopback only |

**Internal ports (per HTTP runtime):**

Management allocates the lowest free port at or above 8001 when a runtime is added, and stores it, so
nothing needs to be tracked by hand. `invoke runtimes.list` shows which runtime holds which port.

## Troubleshooting

### 502 Bad Gateway

- Check the runtime is running: `invoke runtimes.status --workspace-module=myproject`
- Check its log: `invoke runtimes.logs --workspace-module=myproject --name=web`
- Rewrite the configuration from management: `invoke runtimes.apply`

### Static Files Not Loading

- Ensure `collectstatic` has been run
- Verify `STATIC_ROOT` is set correctly in Django settings
- Check nginx config points to the correct static directory

### A git remote rejects the workspace

- `workspaces.clone-repo` prints the public key to add as a deploy key when a fetch is rejected
- The same key is on the workspace in the management admin, under Git public key
- The key is loaded through an ssh-agent rather than read by ssh from disk, so the ACL on the workspace home
  cannot make it look unreadable; a rejection is about the remote, not about permissions

### Runtime Not Discovered

- Check the runtime is enabled: `invoke runtimes.list`
- Run `invoke runtimes.apply`, which rewrites the config and makes supervisord reread it
- A runtime supervisord has not been told about yet answers 409 on restart, which says exactly that

### Management rejects the CLI with a 401

`INVOKE_MANAGEMENT_SECURITY_API_KEY` in `.env` has to match what the management service was started
with. After changing it, recreate the container: `docker compose up -d --force-recreate management`.
