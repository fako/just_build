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
```

## Quick Start

```bash
docker compose --profile workspaces up --build
```

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
└── projects/
    ├── nginx/               # Project nginx configs (gitignored)
    ├── supervisor/          # Project supervisor configs (gitignored)
    └── repos/               # Project git repositories → /home/
```

## Project Onboarding

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

Create a Linux user for the project inside the container:

```bash
docker exec workspaces useradd -m -s /bin/bash myproject
```

### 4. Add Project Repository

Clone or symlink your project into `workspaces/projects/repos/`:

```bash
cd workspaces/projects/repos/
git clone https://github.com/yourorg/myproject.git
```

The `repos/` directory is mounted to `/home/` in the container, so `/home/myproject/` will contain your code.

### 5. Create Supervisor Config

Copy the template and customize:

```bash
cp workspaces/projects/supervisor/example.conf.template \
   workspaces/projects/supervisor/myproject.conf
```

Edit `myproject.conf`:
- Replace `PROJECT_NAME` with `myproject`
- Replace `PROJECT_PORT` with a unique port (e.g., `8001`)
- Replace `ASGI_MODULE` with your ASGI module (e.g., `config.asgi` for Django)

Example:

```ini
[program:myproject]
command=uvicorn config.asgi:application --host 127.0.0.1 --port 8001 --workers 2 --loop uvloop --http httptools
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
    DJANGO_SETTINGS_MODULE="config.settings",
    PYTHONPATH="/home/myproject",
    DATABASE_URL="postgres://myproject_user:secure_password@postgres:5432/myproject_db",
    REDIS_URL="redis://redis:6379/0"
```

### 6. Create Nginx Config

Copy the template and customize:

```bash
cp workspaces/projects/nginx/example.conf.template \
   workspaces/projects/nginx/myproject.conf
```

Edit `myproject.conf`:
- Replace `PROJECT_NAME` with `myproject`
- Replace `PROJECT_DOMAIN` with your domain (e.g., `myproject.localhost`)
- Replace `PROJECT_PORT` with the same port from supervisor config

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

### 7. Reload Services

After adding or modifying configs:

```bash
# Reload supervisor to pick up new/changed configs
docker exec workspaces supervisorctl reread
docker exec workspaces supervisorctl update

# Reload nginx to pick up new/changed configs
docker exec workspaces nginx -s reload
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

Assign unique ports to each project:

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

- Ensure supervisor config is in `projects/supervisor/` with `.conf` extension
- Run `supervisorctl reread && supervisorctl update`
- Check for syntax errors: `supervisorctl reread` will report them
