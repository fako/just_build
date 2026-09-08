## Workspace

You are working in a shielded workspace where your user has its own home on a Debian box with Python 3.12 as its main program. You are completely whitelisted for this environment and can do anything you want, except for leaving this sandbox and the home folder. Prefer the usage of Python when writing code or scripts, but the alternative is to ask your human to install more software for you.

## Project stack

The prefered stack for projects is:
* Python+Django+Ninja
* Postgres
* Redis
* Tika
* Invoke (CLI library in Python)
* Jinja2+HTMX+Tailwind

You are not required to use the full stack, but when picking a tool make sure the choice aligns with the prefered choices or consult your human.

## Python environment

This workspace uses `venv` as the project virtual environment. Keep Python dependencies in `pyproject.toml`, then ask your human to `invoke runtimes.install --workspace-module={{ workspace.module }} --name=<runtime>` from the control repository or run the equivalent inside this workspace:

```bash
python3 -m venv venv --copies --upgrade-deps
venv/bin/python -m pip install -e .
```

The supervisor-managed web process runs through `venv/bin/python`, so install dependencies there before expecting the service to start.
Static files are gathered through collectstatic and end up in staticfiles/

## How domains resolve

This runtime answers to more than one name, and which one to use depends on where the caller is.

* **A browser on the host** reaches the workspace's primary runtime at `http://{{ workspace.slug }}.localhost:7000/`. Every resolver treats anything under `.localhost` as loopback, and that is where the port is published.
* **Another container** — n8n, or another workspace's runtime — uses `http://runtimes.workspaces:7000/r/{{ workspace.slug }}/<runtime>/`. The workspace and runtime travel in the path, not the hostname, so only that one name has to resolve.
* **Never call `{{ workspace.slug }}.localhost` from inside a container.** It does resolve, to the *calling* container's own loopback, so the mistake surfaces as a refused connection rather than an unknown host. This is the confusing one.
* **A real domain** for a home network machine, a VPS or a customer is added to the runtime's `domains` through management. Do not hand-write nginx server blocks: they are generated and will be overwritten on the next `invoke runtimes.apply`.
