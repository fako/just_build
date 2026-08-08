## Workspace Management

The Workspace Management is an API to manage workspaces. Workspace creation still happens on the host, but once setup the API gives control over workspace processes. Possibly using the Supervisor XML-RPC to control runtimes. A workspace can only access processes and secrets that it owns and Workspace Management enforces this rule.

You can run tests for Workspace Management on the host with:

```bash
invoke management.test
```

To edit the underlying Django project you can run Docker Compose with --watch or you can run the development service without anything else through:

```bash
COMPOSE_PROFILES=services docker compose up -d
source activate.sh
cd management && python manage.py runserver
```

Make sure that you have run `invoke install.hosts-file` when running the Workspace Management outside of its container or the server won't be able to find other services.
