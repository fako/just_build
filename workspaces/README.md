# Multi-Project ASGI Workspaces for Agents

This container runs multiple ASGI projects (Django, FastAPI, etc.) under a single nginx reverse proxy managed by supervisord.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Nginx (port 7000)                        │
│         Routes requests based on hostname/subdomain         │
└──────────────┬──────────────┬──────────────┬───────────────┘
               │              │              │
               ▼              ▼              ▼
        ┌──────────┐   ┌──────────┐   ┌──────────┐
        │ Uvicorn  │   │ Uvicorn  │   │ Uvicorn  │
        │ :8001    │   │ :8002    │   │ :8003    │
        └────┬─────┘   └────┬─────┘   └────┬─────┘
             │              │              │
             ▼              ▼              ▼
      ┌────────────┐ ┌────────────┐ ┌────────────┐
      │ Project A  │ │ Project B  │ │ Project C  │
      │ /home/a    │ │ /home/b    │ │ /home/c    │
      └────────────┘ └────────────┘ └────────────┘

┌─────────────────────────────────────────────────────────────┐
│                    SSHD (port 2222)                         │
│         Remote development access for AI agents             │
│         Each project user → /home/{project}                 │
└─────────────────────────────────────────────────────────────┘
```

## Quick Start

```bash
# Generate SSH host keys (first time only)
invoke workspaces.setup

# Start the container
docker compose --profile workspaces up --build

# Create the workspace, SSH access, and staged configs
invoke workspaces.create --name="My Workspace" --module=my_workspace

# Initialize git, Django, and workspace templates over SSH as the workspace user
invoke workspaces.init --workspace-module=my_workspace

# Enable the staged configs after initialization
invoke workspaces.enable --workspace-module=my_workspace
```

The module is the canonical workspace identifier and is used for Linux users, home directories, databases, SSH, and
supervisor. Its URL-safe slug is generated automatically with underscores converted to hyphens, so `my_workspace`
uses `my-workspace.localhost` by default.

## Directory Structure

```
workspaces/
├── Dockerfile
├── README.md
├── requirements.txt         # Python dependencies
├── nginx/
│   └── nginx.conf           # Main nginx config (port 7000)
├── supervisor/
│   └── supervisord.conf     # Main supervisor config
├── ssh/
│   ├── sshd_config          # SSH daemon config (port 22 → 2222)
│   ├── config               # Generated SSH config include file
│   └── keys/                # Container host keys (private gitignored)
└── src/
    ├── nginx/               # Workspace nginx configs (gitignored)
    ├── staged/              # Staged configs waiting to be enabled
    ├── supervisor/          # Workspace supervisor configs (gitignored)
    └── repos/               # Workspace git repositories → /home/
```

## Project Onboarding

### Automated flow

For the common SSH-first workflow, use:

```bash
invoke workspaces.create --name="My Workspace" --module=my_workspace
invoke workspaces.init --workspace-module=my_workspace
invoke workspaces.enable --workspace-module=my_workspace
```

The commands do the following:
- `workspaces.create` creates the management workspace, creates the Linux user and home directory, generates SSH access, stages nginx and supervisor configs, and refreshes `workspaces/ssh/config`
- `workspaces.init` connects over SSH as the project user, initializes git, runs `django-admin startproject <django_module> .`, resolves workspace templates, and creates the initial commit
- `workspaces.enable` activates the staged configs and reloads supervisor and nginx

By default, `workspaces.init` applies the `default` template from `workspaces/templates/default`. Templates are layered in
the comma-separated order provided, so later templates overwrite files from earlier templates:

```bash
invoke workspaces.init --workspace-module=my_workspace --templates=default,custom
```

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

Run `invoke workspaces.create --name="My Workspace" --module=my_workspace` to generate a workspace SSH keypair and publish
the public key into the container-managed `authorized_keys` volume automatically.

The container uses an internal Docker volume for `/etc/ssh/authorized_keys/`, and sshd is configured to look for
`/etc/ssh/authorized_keys/%u` (where `%u` is the username). The private key remains on the host under
`workspaces/ssh/keys/src/<workspace-module>/` so Cursor, Fabric, and local SSH tooling can use it.

The generated public key is published inside the container by `workspaces.create`, and the Python CLI expects the
`/etc/ssh/authorized_keys` directory permissions to come from the image/container environment rather than fixing
them at runtime.

You can test access directly with:

```bash
ssh -i workspaces/ssh/keys/src/myproject/id_ed25519 myproject@localhost -p 2222
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
    IdentityFile /absolute/path/to/just_build/workspaces/ssh/keys/src/myproject/id_ed25519
```

### 4. Initialize Project Repository

The project home lives under `workspaces/src/repos/<workspace-module>/` and is mounted to `/home/<workspace-module>/`
inside the container.

Initialize it over SSH as the project user with:

```bash
invoke workspaces.init --workspace-module=my_workspace
```

This command initializes git, sets local commit identity, runs `django-admin startproject <django_module> .`,
and creates the initial commit.

### 5. Stage Supervisor Config

`workspaces.create` writes a staged supervisor config for the project. `workspaces.enable` is the step that
publishes it into the active supervisor directory and reloads services.

Example:

```ini
[program:myproject]
command=uvicorn web.asgi:application --host 127.0.0.1 --port 8001 --workers 2 --loop uvloop --http httptools
directory=/home/myproject
user=www-data
autostart=true
autorestart=true
stopasgroup=true
killasgroup=true
redirect_stderr=true
stdout_logfile=/var/log/projects/myproject.log
stdout_logfile_maxbytes=50MB
stdout_logfile_backups=5
environment=
    DJANGO_SETTINGS_MODULE="web.settings",
    PYTHONPATH="/home/myproject",
    DATABASE_URL="postgres://myproject_user:secure_password@postgres:5432/myproject_db",
    REDIS_URL="redis://redis:6379/0"
```

### 6. Stage Nginx Config

`workspaces.create` also writes the staged nginx config. `workspaces.enable` is responsible for activating it.

Example:

```nginx
upstream myproject_upstream {
    server 127.0.0.1:8001 fail_timeout=0;
}

server {
    listen 7000;
    server_name myproject.localhost;

    client_max_body_size 100M;

    location /static/ {
        alias /home/myproject/staticfiles/;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    location /media/ {
        alias /home/myproject/media/;
        expires 7d;
    }

    location / {
        proxy_pass http://myproject_upstream;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }
}
```

### 7. Enable Configs

After `workspaces.init` has finished successfully, activate the staged configs with:

```bash
invoke workspaces.enable --workspace-module=my_workspace
```

### 8. Local DNS Setup

Add entries to `/etc/hosts` for local development:

```
127.0.0.1 myproject.localhost
127.0.0.1 another-project.localhost
```

Or use dnsmasq to route all `*.localhost` to 127.0.0.1.

## Managing Projects

### View Status

```bash
docker exec workspaces supervisorctl status
```

### View Logs

```bash
# All logs
docker compose --profile workspaces logs -f workspaces

# Specific project
docker exec workspaces tail -f /var/log/projects/myproject.log
```

### Restart a Project

```bash
docker exec workspaces supervisorctl restart myproject
```

### Stop a Project

```bash
docker exec workspaces supervisorctl stop myproject
```

### Enter the Container

```bash
docker exec -it workspaces bash
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

**Internal Ports (per project):**

| Project | Port |
|---------|------|
| project-a | 8001 |
| project-b | 8002 |
| project-c | 8003 |
| ... | ... |

## Troubleshooting

### 502 Bad Gateway

- Check if the uvicorn process is running: `supervisorctl status`
- Check project logs: `tail -f /var/log/projects/myproject.log`
- Verify port matches between supervisor and nginx configs

### Static Files Not Loading

- Ensure `collectstatic` has been run
- Verify `STATIC_ROOT` is set correctly in Django settings
- Check nginx config points to the correct static directory

### Project Not Discovered

- Ensure supervisor config is in `src/supervisor/` with `.conf` extension
- Run `supervisorctl reread && supervisorctl update`
- Check for syntax errors: `supervisorctl reread` will report them
