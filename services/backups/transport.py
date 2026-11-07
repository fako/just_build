"""
The rsync and ssh commands that move backups between machines.

The other side decides which directory a key sees, through the rrsync line backups.authorize wrote
there, so every remote path here is "./": the client asks for whatever it is allowed, and the
manifests it gets back say whose backups they are. Hosts are the just-build-backup-* aliases from
<root>/config, which every ssh call reads with -F instead of ~/.ssh/config.
"""
from __future__ import annotations

from pathlib import Path
from shlex import quote


# Never transfer a backup that is still being written, or one that failed verification.
EXCLUDES = "--exclude '*.partial' --exclude '*.corrupt'"
# A backup only appears once the whole transfer is done, so one cut short leaves directories without a
# manifest, which nothing counts, instead of complete looking backups with files missing.
ATOMIC = "--ignore-existing --delay-updates"
# Fail instead of prompting, so a missing key cannot hang the timer, and give up on a machine that is
# off before the next hourly check comes round.
BATCH_OPTIONS = "-o BatchMode=yes -o ConnectTimeout=20"
# What rsync exits with when ssh could not connect at all: the remote is off or out of reach. Anything
# else, such as rrsync refusing the key, is a real error.
UNREACHABLE_EXIT_CODE = 255


def ssh_command(ssh_config: Path) -> str:
    return f"ssh -F {quote(str(ssh_config))} {BATCH_OPTIONS}"


def pull_command(host: str, destination: Path, ssh_config: Path) -> str:
    """
    Fetch what the source exposes to the job key.

    Without --delete, so pruning on the source never prunes the copy, and with --ignore-existing, so a
    backup already held is only ever completed, never rewritten.
    """
    return (
        f"rsync -a {ATOMIC} {EXCLUDES} -e {quote(ssh_command(ssh_config))} "
        f"{quote(host + ':./')} {quote(str(destination) + '/')}"
    )


def list_command(host: str, ssh_config: Path) -> str:
    """The one check a read-only key allows: listing what it can read."""
    return f"rsync --list-only -e {quote(ssh_command(ssh_config))} {quote(host + ':./')}"


def fetch_command(host: str, remote_path: str, destination: Path, ssh_config: Path) -> str:
    """Fetch a single file the job key can read, such as a manifest."""
    return (
        f"rsync -e {quote(ssh_command(ssh_config))} {quote(f'{host}:./{remote_path}')} "
        f"{quote(str(destination) + '/')}"
    )


def listed_names(listing: str) -> list[str]:
    """The top level entries of an rsync --list-only listing, without '.'."""
    names = []
    for line in listing.splitlines():
        fields = line.split(maxsplit=4)
        if len(fields) == 5 and fields[4] != ".":
            names.append(fields[4])
    return names
