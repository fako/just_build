"""
The rsync and ssh commands that move backups between machines.

The other side decides which directory a key sees, through the rrsync line backups.authorize wrote
there, so every remote path here is "./": the client asks for whatever it is allowed, and the
manifests it gets back say whose backups they are.
"""
from __future__ import annotations

from pathlib import Path
from shlex import quote


JOB_KEY_PATH = Path.home() / ".ssh" / "just_build_backups"

# Never transfer a backup that is still being written, or one that failed verification.
EXCLUDES = "--exclude '*.partial' --exclude '*.corrupt'"
# A backup only appears once the whole transfer is done, so one cut short leaves directories without a
# manifest, which nothing counts, instead of complete looking backups with files missing.
ATOMIC = "--ignore-existing --delay-updates"
# Fail instead of prompting, so a missing key cannot hang the timer, and give up on a machine that is
# off before the next hourly check comes round.
BATCH_SSH = "ssh -o BatchMode=yes -o ConnectTimeout=20"
# What rsync exits with when ssh could not connect at all: the remote is off or out of reach. Anything
# else, such as rrsync refusing the key, is a real error.
UNREACHABLE_EXIT_CODE = 255


def pull_command(host: str, destination: Path) -> str:
    """
    Fetch what the source exposes to the job key.

    Without --delete, so pruning on the source never prunes the copy, and with --ignore-existing, so a
    backup already held is only ever completed, never rewritten.
    """
    return (
        f"rsync -a {ATOMIC} {EXCLUDES} -e {quote(BATCH_SSH)} "
        f"{quote(host + ':./')} {quote(str(destination) + '/')}"
    )


def push_command(host: str, source: Path) -> str:
    """
    Send the own system's backups to a receiver.

    Plain ssh rather than batch mode: the push alias points at a key the user made, with a passphrase,
    so ssh has to be able to ask for it.
    """
    return f"rsync -a {ATOMIC} {EXCLUDES} {quote(str(source) + '/')} {quote(host + ':./')}"


def list_command(host: str) -> str:
    """The one check a read-only key allows: listing what it can read."""
    return f"rsync --list-only -e {quote(BATCH_SSH)} {quote(host + ':./')}"


def fetch_command(host: str, remote_path: str, destination: Path) -> str:
    """Fetch a single file the job key can read, such as a manifest."""
    return f"rsync -e {quote(BATCH_SSH)} {quote(f'{host}:./{remote_path}')} {quote(str(destination) + '/')}"


def listed_names(listing: str) -> list[str]:
    """The top level entries of an rsync --list-only listing, without '.'."""
    names = []
    for line in listing.splitlines():
        fields = line.split(maxsplit=4)
        if len(fields) == 5 and fields[4] != ".":
            names.append(fields[4])
    return names


def identity_files(ssh_config_dump: str) -> list[Path]:
    """The identity files `ssh -G <host>` says a connection would offer, expanded."""
    files = []
    for line in ssh_config_dump.splitlines():
        key, _, value = line.partition(" ")
        if key.lower() == "identityfile" and value:
            files.append(Path(value.strip()).expanduser())
    return files


def uses_job_key(ssh_config_dump: str, job_key: Path = JOB_KEY_PATH) -> bool:
    job_key = job_key.expanduser().resolve()
    return any(path.resolve() == job_key for path in identity_files(ssh_config_dump))
