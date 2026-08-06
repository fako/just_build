"""
The rendered-configuration manifest.

Management renders workspace configuration but never writes it. It hands back a manifest of
repository-relative paths and contents, and the invoke CLI on the host materialises it. That keeps
`workspaces/src` owned by the host user and keeps management out of the filesystem entirely.

The manifest for a set of runtimes is always the *complete* desired state for those runtimes, never
a diff, so the reconciler on the other side can delete anything it finds that is not listed. That is
what makes disabling, renaming and removing prune themselves without either side tracking deletes.
"""
from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.template.loader import render_to_string


# Relative to the repository root. The workspaces container bind-mounts the two directories below it.
CONFIG_ROOT = "workspaces/src"
SUPERVISOR_DIRECTORY = "supervisor"
NGINX_DIRECTORY = "nginx"
CONFIG_SUFFIX = ".conf"


@dataclass(frozen=True, slots=True)
class ConfigFile:
    """One rendered file, at a path relative to CONFIG_ROOT."""
    path: str
    content: str


def render_config(template_name: str, context: dict[str, object]) -> str:
    return render_to_string(template_name, context, using=settings.CONFIG_TEMPLATE_ENGINE)


def render_supervisord_environment(environment: dict[str, str]) -> str:
    """
    Render the supervisord environment= line.

    Values are quoted and percent signs doubled, because supervisord expands %(...)s itself and
    would otherwise choke on a literal one.
    """
    if not environment:
        return ""

    entries = [
        '{key}="{value}"'.format(key=key, value=value.replace("%", "%%"))
        for key, value in environment.items()
    ]
    return "\n    " + ",\n    ".join(entries)


def supervisor_config_path(workspace_module: str, runtime_name: str) -> str:
    return f"{SUPERVISOR_DIRECTORY}/{workspace_module}/{runtime_name}{CONFIG_SUFFIX}"


def nginx_config_path(workspace_module: str, runtime_name: str) -> str:
    return f"{NGINX_DIRECTORY}/{workspace_module}/{runtime_name}{CONFIG_SUFFIX}"


def build_manifest(runtimes) -> list[ConfigFile]:
    """Collect the config files of every given runtime, ordered by path for a stable response."""
    files: list[ConfigFile] = []
    for runtime in runtimes:
        files.extend(runtime.specialize().config_files())
    return sorted(files, key=lambda config_file: config_file.path)
