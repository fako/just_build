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

This workspace uses `venv` as the project virtual environment. Keep Python dependencies in `pyproject.toml`, then ask your human to `invoke workspaces.update --workspace-slug={{ workspace.slug }}` from the control repository or run the equivalent inside this workspace:

```bash
python3 -m venv venv --copies --upgrade-deps
venv/bin/python -m pip install -e .
```

The supervisor-managed web process runs through `venv/bin/python`, so install dependencies there before expecting the service to start.
Static files are gathered through collectstatic and end up in staticfiles/
