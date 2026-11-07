"""
The systems this machine pulls backups from, kept as an SSH config in <root>/config.

Each system is one `Host just-build-backup-<name>` block, written by backups.register. Every ssh and
rsync call the tooling makes passes this file with -F, so nothing depends on ~/.ssh/config, and the
file doubles as the registry: what is in it is what gets pulled. Every block connects with the job key,
which the other machines only ever authorize to read.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from services.backups.storage import SSH_CONFIG_FILE, BackupError, ensure_root, job_key_path, validate_system_name


HOST_PREFIX = "just-build-backup-"
HOST_LINE = re.compile(r"^\s*Host\s+(\S+)\s*$", re.IGNORECASE)
OPTION_LINE = re.compile(r"^\s*(\w+)\s*(?:=\s*|\s+)(.+?)\s*$")
# Written into every block from the root's job key, so they are not read back as options.
MANAGED_OPTIONS = ("identityfile", "identitiesonly")
HEADER = """\
# Systems this machine pulls backups from, one block per system. Written by invoke backups.register;
# HostName, User, Port and any options you add are kept when it rewrites the file, anything outside a
# block is not. Every block connects with the job key in keys/. The backup tasks read this file with
# ssh -F; ~/.ssh/config plays no part.
"""


@dataclass(frozen=True)
class Remote:
    name: str
    address: str | None
    user: str | None = None
    port: int | None = None
    options: tuple[str, ...] = ()

    @property
    def host(self) -> str:
        return host_alias(self.name)


def host_alias(name: str) -> str:
    return f"{HOST_PREFIX}{name}"


def ssh_config_path(root: Path) -> Path:
    return root / SSH_CONFIG_FILE


def _parse_block(name: str, lines: list[str]) -> Remote:
    values: dict[str, str] = {}
    options: list[str] = []
    for line in lines:
        match = OPTION_LINE.match(line)
        if not match or line.lstrip().startswith("#"):
            continue
        key, value = match.group(1), match.group(2)
        if key.lower() in ("hostname", "user", "port"):
            values.setdefault(key.lower(), value)
        elif key.lower() not in MANAGED_OPTIONS:
            options.append(f"{key} {value}")
    try:
        port = int(values["port"]) if "port" in values else None
    except ValueError as exc:
        raise BackupError(f"The block for {host_alias(name)} has a port that is not a number.") from exc
    return Remote(name=name, address=values.get("hostname"), user=values.get("user"), port=port,
                  options=tuple(options))


def read_remotes(root: Path) -> dict[str, Remote]:
    """Every just-build-backup-* block in the file. Other blocks are not ours and are ignored."""
    path = ssh_config_path(root)
    if not path.is_file():
        return {}
    blocks: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in path.read_text().splitlines():
        host = HOST_LINE.match(line)
        if host:
            alias = host.group(1)
            name = alias.removeprefix(HOST_PREFIX)
            current = blocks.setdefault(name, []) if alias.startswith(HOST_PREFIX) and name else None
        elif current is not None:
            current.append(line)
    return {name: _parse_block(name, lines) for name, lines in sorted(blocks.items())}


def render_block(root: Path, remote: Remote) -> str:
    lines = [f"Host {remote.host}"]
    if remote.address:
        lines.append(f"    HostName {remote.address}")
    if remote.user:
        lines.append(f"    User {remote.user}")
    if remote.port:
        lines.append(f"    Port {remote.port}")
    lines.append(f"    IdentityFile {job_key_path(root)}")
    # Offer only the job key, never others from an agent, so it is the key the other side's rrsync
    # line judges.
    lines.append("    IdentitiesOnly yes")
    lines.extend(f"    {option}" for option in remote.options)
    return "\n".join(lines)


def write_remotes(root: Path, remotes: dict[str, Remote]) -> None:
    ensure_root(root)
    path = ssh_config_path(root)
    blocks = [render_block(root, remote) for _, remote in sorted(remotes.items())]
    path.write_text(HEADER + "".join(f"\n{block}\n" for block in blocks))
    # ssh refuses a config that others can write to.
    path.chmod(0o600)


def register_remote(root: Path, own_system: str, name: str, address: str, user: str | None = None,
                    port: int | None = None) -> Remote:
    """Add or replace a system's block, keeping options added to an existing one by hand."""
    validate_system_name(name)
    if name == own_system:
        raise BackupError(f"'{name}' is this machine's own system name. Register the other machine's name.")
    if not address:
        raise BackupError("A remote needs an address: a host name or IP address to connect to.")
    remotes = read_remotes(root)
    existing = remotes.get(name)
    remote = Remote(name=name, address=address, user=user, port=port,
                    options=existing.options if existing else ())
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
