"""
The systems this machine exchanges backups with, kept in <root>/remotes.yml.

It lives beside the backups rather than in invoke.yml, because remotes are per machine and the
repository's config is shared, and rather than in .env, because each entry has structure.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path

import yaml

from services.backups.storage import REMOTES_FILE, BackupError, ensure_root, validate_system_name


class Direction(StrEnum):
    PULL = "pull"
    PUSH = "push"


@dataclass(frozen=True)
class Remote:
    name: str
    host: str
    direction: Direction


def remotes_path(root: Path) -> Path:
    return root / REMOTES_FILE


def read_remotes(root: Path) -> dict[str, Remote]:
    path = remotes_path(root)
    if not path.is_file():
        return {}
    entries = yaml.safe_load(path.read_text()) or {}
    if not isinstance(entries, dict):
        raise BackupError(f"{path} should map system names to remotes.")
    try:
        return {
            name: Remote(name=name, host=entry["host"], direction=Direction(entry["direction"]))
            for name, entry in sorted(entries.items())
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise BackupError(f"{path} has an invalid entry: {exc}") from exc


def write_remotes(root: Path, remotes: dict[str, Remote]) -> None:
    entries = {
        name: {key: str(value) for key, value in asdict(remote).items() if key != "name"}
        for name, remote in sorted(remotes.items())
    }
    ensure_root(root)
    remotes_path(root).write_text(yaml.safe_dump(entries, sort_keys=True) if entries else "")


def register_remote(root: Path, own_system: str, name: str, host: str, direction: Direction) -> Remote:
    validate_system_name(name)
    if name == own_system:
        raise BackupError(f"'{name}' is this machine's own system name. Register the other machine's name.")
    if not host:
        raise BackupError("A remote needs an SSH alias to connect through.")
    remotes = read_remotes(root)
    remote = Remote(name=name, host=host, direction=direction)
    remotes[name] = remote
    write_remotes(root, remotes)
    return remote


def unregister_remote(root: Path, name: str) -> Remote:
    remotes = read_remotes(root)
    if name not in remotes:
        raise BackupError(f"'{name}' is not registered.")
    remote = remotes.pop(name)
    write_remotes(root, remotes)
    return remote
