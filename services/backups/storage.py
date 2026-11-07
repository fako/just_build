"""
The on-disk layout of backups, and the manifest that marks one as complete.

Everything here is plain filesystem work, without invoke or docker, so the rest of the package can be
tested against a temporary directory.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import fcntl
import hashlib
import json
import re
import shutil


SYSTEM_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
BACKUP_ID_PATTERN = re.compile(r"^\d{8}T\d{6}Z$")
BACKUP_ID_FORMAT = "%Y%m%dT%H%M%SZ"

MANIFEST_FILE = "manifest.json"
MANIFEST_FORMAT = 1
GLOBALS_FILE = "globals.sql"
PARTIAL_SUFFIX = ".partial"
CORRUPT_SUFFIX = ".corrupt"
LOCK_FILE = ".lock"
SSH_CONFIG_FILE = "config"
KEYS_DIR = "keys"
JOB_KEY_NAME = "job"
# Directories in the root that are not systems, and so can never be a system's name.
RESERVED_NAMES = frozenset({KEYS_DIR})

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
# Inside the repository, wherever it was cloned, and ignored by git. Other machines never need to know
# where that is: the rrsync line backups.authorize writes here carries the path.
DEFAULT_ROOT = REPOSITORY_ROOT / "backups"


class BackupError(Exception):
    pass


class LockBusy(BackupError):
    """Another task holds the root. The hourly timer treats this as not now, rather than as a failure."""


@dataclass(frozen=True)
class Backup:
    system: str
    id: str
    path: Path

    @property
    def created_at(self) -> datetime:
        return parse_backup_id(self.id)

    def manifest(self) -> dict:
        return read_manifest(self.path)


def validate_system_name(name: str | None) -> str:
    if not name or not SYSTEM_NAME_PATTERN.match(name):
        raise BackupError(
            f"'{name or ''}' is not a valid system name. Use lowercase letters, digits and dashes, "
            "starting with a letter or digit."
        )
    if name in RESERVED_NAMES:
        raise BackupError(f"'{name}' is taken by a directory the backups use themselves. Pick another name.")
    return name


def backup_root(configured: str | None) -> Path:
    """The configured root, or the default when the setting is empty, as it is in a fresh .env."""
    return Path(configured).expanduser() if configured else DEFAULT_ROOT


def new_backup_id(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.astimezone(timezone.utc).strftime(BACKUP_ID_FORMAT)


def parse_backup_id(backup_id: str) -> datetime:
    return datetime.strptime(backup_id, BACKUP_ID_FORMAT).replace(tzinfo=timezone.utc)


def system_directory(root: Path, system: str) -> Path:
    return root / system


def system_names(root: Path) -> list[str]:
    """Every system this root holds a directory for, own and held alike."""
    if not root.is_dir():
        return []
    return sorted(
        path.name for path in root.iterdir()
        if path.is_dir() and SYSTEM_NAME_PATTERN.match(path.name) and path.name not in RESERVED_NAMES
    )


def list_backups(root: Path, system: str) -> list[Backup]:
    """
    The complete backups of a system, oldest first.

    Complete means named after a backup id and holding a manifest. A partial one is still being
    written and a corrupt one failed verification, and neither should be counted, restored or pruned
    by retention.
    """
    directory = system_directory(root, system)
    if not directory.is_dir():
        return []
    backups = [
        Backup(system=system, id=path.name, path=path)
        for path in directory.iterdir()
        if path.is_dir() and BACKUP_ID_PATTERN.match(path.name) and (path / MANIFEST_FILE).is_file()
    ]
    return sorted(backups, key=lambda backup: backup.id)


def list_suffixed(root: Path, system: str, suffix: str) -> list[Path]:
    directory = system_directory(root, system)
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.iterdir() if path.is_dir() and path.name.endswith(suffix))


def find_backup(root: Path, system: str, backup_id: str) -> Backup:
    backups = list_backups(root, system)
    if not backups:
        raise BackupError(f"There are no backups of '{system}' under {root}.")
    if backup_id == "latest":
        return backups[-1]
    for backup in backups:
        if backup.id == backup_id:
            return backup
    raise BackupError(f"No backup '{backup_id}' of '{system}'. 'invoke backups.status --system={system}' lists them.")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_entry(path: Path, **details: str) -> dict:
    return {**details, "bytes": path.stat().st_size, "sha256": file_sha256(path)}


def write_manifest(directory: Path, manifest: dict) -> None:
    (directory / MANIFEST_FILE).write_text(json.dumps(manifest, indent=2) + "\n")


def read_manifest(directory: Path) -> dict:
    try:
        return json.loads((directory / MANIFEST_FILE).read_text())
    except (OSError, ValueError) as exc:
        raise BackupError(f"Unreadable manifest in {directory}: {exc}") from exc


def verify_backup(directory: Path, expected_system: str) -> list[str]:
    """Return what is wrong with a backup, which is nothing for one that can be restored."""
    try:
        manifest = read_manifest(directory)
    except BackupError as exc:
        return [str(exc)]

    problems: list[str] = []
    if manifest.get("format") != MANIFEST_FORMAT:
        problems.append(f"unknown manifest format {manifest.get('format')!r}")
    if manifest.get("system") != expected_system:
        problems.append(f"made by system '{manifest.get('system')}', expected '{expected_system}'")
    if manifest.get("id") != directory.name.removesuffix(CORRUPT_SUFFIX):
        problems.append(f"manifest is for backup '{manifest.get('id')}'")
    for name, entry in manifest.get("files", {}).items():
        path = directory / name
        if not path.is_file():
            problems.append(f"{name} is missing")
        elif path.stat().st_size != entry.get("bytes"):
            problems.append(f"{name} has {path.stat().st_size} bytes, expected {entry.get('bytes')}")
        elif file_sha256(path) != entry.get("sha256"):
            problems.append(f"{name} does not match its checksum")
    return problems


def mark_corrupt(directory: Path) -> Path:
    """Move a backup out of the way so it is not counted, and so the next pull fetches it again."""
    target = directory.with_name(directory.name + CORRUPT_SUFFIX)
    if target.exists():
        shutil.rmtree(target)
    directory.rename(target)
    return target


def directory_size(path: Path) -> int:
    return sum(file.stat().st_size for file in path.rglob("*") if file.is_file())


def delete_directory(path: Path) -> None:
    shutil.rmtree(path)


def ensure_root(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    # Backups hold every row and every role's password hash. Nobody else on the host needs them.
    root.chmod(0o700)


def keys_directory(root: Path) -> Path:
    return root / KEYS_DIR


def job_key_path(root: Path) -> Path:
    """The passphrase-less key the timer pulls with. Other machines only ever authorize it to read."""
    return keys_directory(root) / JOB_KEY_NAME


@contextmanager
def root_lock(root: Path) -> Iterator[None]:
    """
    Hold the root for one task at a time.

    A timer run pruning while a manual pull is still writing would delete what is arriving, so every
    task that writes or deletes takes this. It fails instead of waiting, because a task queued behind
    a hung transfer is worse than one that says so.

    The .lock file itself stays. What is held is a lock on it, which the kernel releases when the task
    exits, however it exits, so a crashed task never leaves the root locked.
    """
    ensure_root(root)
    with (root / LOCK_FILE).open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LockBusy(f"Another backup task is using {root}. Try again when it has finished.") from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def lock_held(root: Path) -> bool:
    """Whether a backup task is running right now, without taking the lock from it."""
    path = root / LOCK_FILE
    if not path.exists():
        return False
    with path.open("r") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(lock, fcntl.LOCK_UN)
        return False
