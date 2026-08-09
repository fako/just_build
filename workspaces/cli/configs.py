"""
Materialises the configuration manifest management renders.

This is the only writer of `workspaces/src/supervisor` and `workspaces/src/nginx`. Management renders
but never writes, so the files stay owned by the host user who ran the command.

The manifest is the complete desired state, so reconciling also deletes: any `.conf` under the root
that the manifest does not list is stale by definition. That is what makes disabling a runtime,
renaming one, or removing a workspace prune themselves without either side tracking deletes.
"""
from __future__ import annotations

from pathlib import Path

from workspaces.cli.constants import REPOSITORY_DIR
from workspaces.cli.runtimes.client import ManifestRecord


CONFIG_SUFFIX = ".conf"


class ConfigPathError(RuntimeError):
    pass


def manifest_root(manifest: ManifestRecord) -> Path:
    root = (REPOSITORY_DIR / manifest.root).resolve()
    if REPOSITORY_DIR not in root.parents and root != REPOSITORY_DIR:
        raise ConfigPathError(f"Manifest root '{manifest.root}' resolves outside the repository.")
    return root


def resolve_config_path(root: Path, relative_path: str) -> Path:
    """
    Resolve a manifest path, refusing anything that escapes the root.

    Management is trusted, but a client that writes server-supplied paths straight to disk is the
    wrong shape regardless of who is on the other end.
    """
    if Path(relative_path).is_absolute():
        raise ConfigPathError(f"Manifest path '{relative_path}' must be relative.")

    resolved = (root / relative_path).resolve()
    if root not in resolved.parents:
        raise ConfigPathError(f"Manifest path '{relative_path}' resolves outside {root}.")
    if resolved.suffix != CONFIG_SUFFIX:
        raise ConfigPathError(f"Manifest path '{relative_path}' is not a {CONFIG_SUFFIX} file.")
    return resolved


def owned_config_paths(root: Path, workspace_module: str) -> set[Path]:
    """
    Config files that belong to one workspace.

    Both layouts count: the nested `<kind>/<module>/<runtime>.conf` that runtimes use, and the flat
    `<kind>/<module>.conf` from before runtimes existed, so adopting a workspace clears its old file.
    """
    paths: set[Path] = set()
    for kind in root.iterdir() if root.is_dir() else []:
        if not kind.is_dir():
            continue
        nested = kind / workspace_module
        if nested.is_dir():
            paths.update(path.resolve() for path in nested.rglob(f"*{CONFIG_SUFFIX}") if path.is_file())
        legacy = kind / f"{workspace_module}{CONFIG_SUFFIX}"
        if legacy.is_file():
            paths.add(legacy.resolve())
    return paths


def reconcile_configs(manifest: ManifestRecord, prune: bool = True) -> tuple[list[Path], list[Path]]:
    """
    Write every file the manifest lists and remove the stale ones.

    Returns the paths written and the paths removed. Safe to run repeatedly; that is the point.
    """
    root = manifest_root(manifest)
    desired: dict[Path, str] = {}
    for config_file in manifest.files:
        desired[resolve_config_path(root, config_file.path)] = config_file.content

    written: list[Path] = []
    for path, content in sorted(desired.items()):
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_text() != content:
            path.write_text(content)
            written.append(path)

    removed: list[Path] = []
    if prune:
        # Prune only within the workspaces the manifest speaks for. A workspace management has no
        # record of is none of this manifest's business, and must keep whatever config it has.
        owned: set[Path] = set()
        for workspace_module in manifest.workspaces:
            owned |= owned_config_paths(root, workspace_module)
        for path in sorted(owned - set(desired)):
            path.unlink()
            removed.append(path)
        remove_empty_directories(root, manifest.workspaces)

    return written, removed


def remove_empty_directories(root: Path, workspace_modules: list[str]) -> None:
    """Leave nothing behind for a workspace whose runtimes are all gone."""
    for kind in root.iterdir() if root.is_dir() else []:
        for workspace_module in workspace_modules:
            directory = kind / workspace_module
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
