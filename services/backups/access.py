"""
The authorized_keys lines that let other machines reach this one's backups, and nothing else.

Each line forces rrsync, so the key can only run rsync, inside one directory, in one direction, and
`restrict` takes away the shell, the pty and every kind of forwarding. Lines are tagged in their
comment so rerunning replaces them and --revoke finds them, without touching lines that are not ours.
"""
from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from shlex import quote

from services.backups.storage import BackupError


TAG = "just-build-backups"
KEY_TYPE_PREFIXES = ("ssh-", "ecdsa-", "sk-")


class Access(StrEnum):
    READ = "read"
    PUSH = "push"


def parse_public_key(value: str) -> str:
    """Accept a public key or a path to one, and return its type and body without the comment."""
    candidate = Path(value).expanduser()
    if not value.startswith(KEY_TYPE_PREFIXES) and candidate.is_file():
        value = candidate.read_text()
    fields = value.strip().split()
    if len(fields) < 2 or not fields[0].startswith(KEY_TYPE_PREFIXES):
        raise BackupError("Expected a public key such as 'ssh-ed25519 AAAA...', or the path to a .pub file.")
    return f"{fields[0]} {fields[1]}"


def line_tag(name: str, access: Access, system: str) -> str:
    return f"{TAG}:{name}:{access}:{system}"


def authorized_line(public_key: str, name: str, access: Access, system: str, directory: Path,
                    rrsync: str = "rrsync", source: str | None = None) -> str:
    """
    One authorized_keys line.

    READ gives `rrsync -ro`, which refuses every attempt to send files. PUSH gives `rrsync -wo`, which
    refuses every attempt to read, so a pusher cannot see what the receiver holds, its own directory
    included. -no-del stops a pusher deleting what it sent before, and -munge stops the symlinks it
    sends from pointing anywhere on the receiver.
    """
    mode = "-ro" if access is Access.READ else "-wo -no-del -munge"
    command = f"{rrsync} {mode} {quote(str(directory))}".replace('"', '\\"')
    options = ["restrict"]
    if source:
        options.append(f'from="{source}"')
    options.append(f'command="{command}"')
    return f"{','.join(options)} {public_key} {line_tag(name, access, system)}"


def update_authorized_keys(text: str, tag: str, line: str | None) -> tuple[str, bool]:
    """
    Replace the line carrying this tag, or remove it when line is None.

    Returns the new text and whether a tagged line was there before. Every other line, ours with a
    different tag included, is left exactly as it was.
    """
    kept: list[str] = []
    found = False
    for existing in text.splitlines():
        fields = existing.split()
        if fields and fields[-1] == tag:
            found = True
            continue
        kept.append(existing)
    if line is not None:
        kept.append(line)
    return ("\n".join(kept) + "\n" if kept else ""), found


def write_authorized_keys(path: Path, tag: str, line: str | None) -> bool:
    path.parent.mkdir(mode=0o700, exist_ok=True)
    text = path.read_text() if path.exists() else ""
    updated, found = update_authorized_keys(text, tag, line)
    path.write_text(updated)
    # sshd ignores an authorized_keys others can write to.
    path.chmod(0o600)
    return found
