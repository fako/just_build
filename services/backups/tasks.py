from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from shlex import quote
import shutil
import tempfile

from invoke.collection import Collection
from invoke.context import Context
from invoke.exceptions import Exit, UnexpectedExit
from invoke.tasks import task

from services.backups.access import Access, authorized_line, line_tag, parse_public_key, write_authorized_keys
from services.backups.postgres import Postgres, PostgresUnavailable
from services.backups.remotes import Direction, Remote, read_remotes, register_remote, unregister_remote
from services.backups.retention import Policy, backups_to_delete, clean_candidates
from services.backups.schedule import backup_due, day_start, parse_daily_at
from services.backups.status import (
    format_size,
    push_status,
    render_overview,
    render_system,
    system_order,
    system_status,
)
from services.backups.storage import (
    BACKUP_ID_PATTERN,
    GLOBALS_FILE,
    MANIFEST_FILE,
    MANIFEST_FORMAT,
    PARTIAL_SUFFIX,
    REPOSITORY_ROOT,
    Backup,
    BackupError,
    backup_root,
    delete_directory,
    directory_size,
    ensure_root,
    file_entry,
    find_backup,
    list_backups,
    list_suffixed,
    mark_corrupt,
    new_backup_id,
    read_manifest,
    root_lock,
    system_directory,
    system_names,
    validate_system_name,
    verify_backup,
    write_manifest,
    write_push_state,
)
from services.backups.transport import (
    JOB_KEY_PATH,
    UNREACHABLE_EXIT_CODE,
    fetch_command,
    identity_files,
    list_command,
    listed_names,
    pull_command,
    push_command,
    uses_job_key,
)


AUTHORIZED_KEYS_PATH = Path.home() / ".ssh" / "authorized_keys"
# A partial directory this old belongs to a run that died, not to one still writing.
STALE_PARTIAL_AGE = timedelta(days=1)


@dataclass(frozen=True)
class Settings:
    system: str
    root: Path
    policy: Policy
    stale_after: timedelta
    clean_after_days: int
    daily_at: time
    superuser: str


def _settings(ctx: Context) -> Settings:
    config = ctx.config.backups
    try:
        system = validate_system_name(config.system)
    except BackupError as exc:
        raise Exit(f"[backups] INVOKE_BACKUPS_SYSTEM is not set to a usable name. {exc}") from exc
    try:
        daily_at = parse_daily_at(config.daily_at)
    except BackupError as exc:
        raise Exit(f"[backups] {exc}") from exc
    return Settings(
        system=system,
        root=backup_root(config.root),
        policy=Policy(days=int(config.retention.days), remote_months=int(config.retention.remote_months)),
        stale_after=timedelta(hours=int(config.stale_after_hours)),
        clean_after_days=int(config.retention.clean_after_days),
        daily_at=daily_at,
        superuser=ctx.config.postgres.user,
    )


@contextmanager
def _reported() -> Iterator[None]:
    """Turn the package's own errors into invoke's, which print a message instead of a traceback."""
    try:
        yield
    except BackupError as exc:
        raise Exit(f"[backups] {exc}") from exc


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _confirm(question: str) -> bool:
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


def _postgres(ctx: Context, settings: Settings) -> Postgres:
    return Postgres(ctx, REPOSITORY_ROOT, settings.superuser)


def _commit(ctx: Context) -> str | None:
    result = ctx.run(f"git -C {quote(str(REPOSITORY_ROOT))} rev-parse --short HEAD", hide=True, warn=True)
    return result.stdout.strip() if result.ok else None


def _create(ctx: Context, settings: Settings, label: str | None = None) -> Backup:
    """
    Dump globals and every database into a new backup of the own system.

    It is written under <id>.partial and only renamed into place once the manifest is there, so a
    complete looking directory is always a complete backup.
    """
    postgres = _postgres(ctx, settings)
    postgres.ensure_running()

    started = _now().replace(microsecond=0)
    backup_id = new_backup_id(started)
    directory = system_directory(settings.root, settings.system)
    directory.mkdir(parents=True, exist_ok=True)
    final = directory / backup_id
    partial = directory / f"{backup_id}{PARTIAL_SUFFIX}"
    if final.exists() or partial.exists():
        raise BackupError(f"A backup {backup_id} already exists. Wait a second and try again.")

    partial.mkdir()
    try:
        version = postgres.server_version()
        databases = postgres.databases()
        postgres.dump_globals(partial / GLOBALS_FILE)
        files = {GLOBALS_FILE: file_entry(partial / GLOBALS_FILE)}
        for database, owner in databases.items():
            name = f"{database}.dump"
            print(f"[backups] Dumping {database}")
            postgres.dump_database(database, partial / name)
            files[name] = file_entry(partial / name, database=database, owner=owner)
        write_manifest(partial, {
            "format": MANIFEST_FORMAT,
            "system": settings.system,
            "id": backup_id,
            "created_at": started.isoformat().replace("+00:00", "Z"),
            "label": label,
            "postgres_version": version,
            "commit": _commit(ctx),
            "files": files,
        })
        partial.rename(final)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise

    backup = Backup(system=settings.system, id=backup_id, path=final)
    print(f"[backups] Created {settings.system}/{backup_id} ({len(databases)} databases, "
          f"{format_size(directory_size(final))})")
    return backup


def _prune(settings: Settings, systems: list[str], dry_run: bool = False) -> None:
    now = _now()
    for system in systems:
        backups = {backup.id: backup for backup in list_backups(settings.root, system)}
        doomed = backups_to_delete(list(backups), now, system == settings.system, settings.policy)
        for backup_id in doomed:
            print(f"[backups] {'Would prune' if dry_run else 'Pruning'} {system}/{backup_id}")
            if not dry_run:
                delete_directory(backups[backup_id].path)
        for partial in list_suffixed(settings.root, system, PARTIAL_SUFFIX):
            modified = datetime.fromtimestamp(partial.stat().st_mtime, timezone.utc)
            if now - modified > STALE_PARTIAL_AGE:
                print(f"[backups] {'Would remove' if dry_run else 'Removing'} abandoned {system}/{partial.name}")
                if not dry_run:
                    delete_directory(partial)


def _pull_remotes(settings: Settings, remote_name: str | None) -> list[Remote]:
    remotes = read_remotes(settings.root)
    if remote_name:
        if remote_name not in remotes:
            raise BackupError(f"'{remote_name}' is not registered. Add it with 'invoke backups.register'.")
        return [remotes[remote_name]]
    return [remote for remote in remotes.values() if remote.direction is Direction.PULL]


def _arrived(rsync_output: str) -> set[str]:
    """The backups an rsync run wrote to, from its --out-format=%n lines."""
    arrived = set()
    for line in rsync_output.splitlines():
        top = line.strip().split("/", maxsplit=1)[0]
        if BACKUP_ID_PATTERN.match(top):
            arrived.add(top)
    return arrived


def _pull(ctx: Context, settings: Settings, remote: Remote, system: str) -> list[str]:
    """
    Fetch a remote's backups into <root>/<system>/ and verify what arrived.

    Returns the backups that failed verification, which are moved aside as .corrupt.
    """
    destination = system_directory(settings.root, system)
    destination.mkdir(parents=True, exist_ok=True)
    print(f"[backups] Pulling {system} from {remote.host}")
    result = ctx.run(f"{pull_command(remote.host, destination)} --out-format='%n'", hide=True)

    corrupt = []
    arrived = sorted(_arrived(result.stdout))
    for backup_id in arrived:
        problems = verify_backup(destination / backup_id, system)
        if problems:
            mark_corrupt(destination / backup_id)
            corrupt.append(backup_id)
            print(f"[backups] {system}/{backup_id} failed verification: {'; '.join(problems)}")
    received = len(arrived) - len(corrupt)
    print(f"[backups] Pulled {received} new backup(s) of {system}" + (f", {len(corrupt)} corrupt" if corrupt else ""))
    return corrupt


def _push(ctx: Context, settings: Settings, remote: Remote) -> None:
    ssh_config = ctx.run(f"ssh -G {quote(remote.host)}", hide=True).stdout
    if uses_job_key(ssh_config):
        raise BackupError(
            f"{remote.host} would connect with the passphrase-less job key {JOB_KEY_PATH}, which may only "
            "ever read. Give the push target an SSH alias of its own, with a key you made for writing."
        )
    for identity in identity_files(ssh_config):
        if identity.is_file():
            unprotected = ctx.run(f"ssh-keygen -y -P '' -f {quote(str(identity))}", hide=True, warn=True).ok
            if unprotected:
                print(f"[backups] WARNING: {identity} has no passphrase. Keys that write to other machines should.")

    backups = list_backups(settings.root, settings.system)
    if not backups:
        raise BackupError(f"There are no backups of '{settings.system}' to push yet.")
    print(f"[backups] Pushing {settings.system} to {remote.host}")
    ctx.run(push_command(remote.host, system_directory(settings.root, settings.system)))
    write_push_state(settings.root, remote.name, backups[-1].id)
    print(f"[backups] Pushed {settings.system} up to {backups[-1].id} to {remote.name}")


@task(help={"label": "A note stored in the manifest, shown by backups.status --system"})
def create(ctx: Context, label: str | None = None) -> None:
    """Back up every database of this installation, then prune this system's old backups."""
    settings = _settings(ctx)
    with _reported(), root_lock(settings.root):
        _create(ctx, settings, label=label)
        _prune(settings, [settings.system])


@task(help={
    "backup": "The backup id to restore, or 'latest' (default)",
    "database": "Restore only this database instead of all of them",
    "yes": "Skip the confirmation prompt",
})
def restore(ctx: Context, backup: str = "latest", database: str | None = None, yes: bool = False) -> None:
    """
    Restore one of this machine's own backups, replacing the databases it holds.

    A backup of the current state, labelled pre-restore, is made first, so a restore can be undone.
    """
    settings = _settings(ctx)
    with _reported(), root_lock(settings.root):
        chosen = find_backup(settings.root, settings.system, backup)
        problems = verify_backup(chosen.path, settings.system)
        if problems:
            raise BackupError(f"{settings.system}/{chosen.id} cannot be restored: {'; '.join(problems)}")

        manifest = read_manifest(chosen.path)
        targets = {
            entry["database"]: (name, entry["owner"])
            for name, entry in manifest["files"].items() if "database" in entry
        }
        if database:
            if database not in targets:
                raise BackupError(f"{chosen.id} has no database '{database}'. It has: {', '.join(sorted(targets))}")
            targets = {database: targets[database]}

        postgres = _postgres(ctx, settings)
        postgres.ensure_running()
        existing = postgres.databases()
        print(f"[backups] Restoring {settings.system}/{chosen.id}, made {chosen.created_at:%Y-%m-%d %H:%M} UTC")
        for name in sorted(targets):
            print(f"  {'replace' if name in existing else 'create '}  {name}")
        if not database:
            for name in sorted(set(existing) - set(targets)):
                print(f"  keep     {name} (not in this backup)")
        if not yes and not _confirm("Replace these databases?"):
            raise Exit("[backups] Nothing restored.")

        safety = _create(ctx, settings, label="pre-restore")
        print(f"[backups] Current state saved as {safety.id}; restore that to undo this.")

        consumers = postgres.running_consumers()
        postgres.stop_services(consumers)
        try:
            for error in postgres.restore_globals(chosen.path / GLOBALS_FILE):
                print(f"[backups] roles: {error}")
            for name, (file_name, owner) in sorted(targets.items()):
                print(f"[backups] Restoring {name}")
                postgres.recreate_database(name, owner)
                postgres.restore_database(name, chosen.path / file_name)
        finally:
            postgres.start_services(consumers)
        print(f"[backups] Restored {len(targets)} database(s) from {chosen.id}")


def _check_pull_remote(ctx: Context, remote: Remote) -> None:
    """
    See whether the job key can read the remote, and whether what it reads is the system expected.

    A warning rather than an error, so a remote can be registered before the other side has run
    backups.authorize.
    """
    result = ctx.run(list_command(remote.host), hide=True, warn=True)
    if not result.ok:
        print(f"[backups] WARNING: could not list {remote.host} yet: {result.stderr.strip()}")
        print(f"[backups] On {remote.name}, run: invoke backups.authorize --name=<this system> --key=<job key>")
        return
    backup_ids = sorted(name for name in listed_names(result.stdout) if BACKUP_ID_PATTERN.match(name))
    if not backup_ids:
        print(f"[backups] {remote.host} is reachable but holds no backups yet.")
        return
    with tempfile.TemporaryDirectory() as directory:
        fetched = ctx.run(
            fetch_command(remote.host, f"{backup_ids[-1]}/{MANIFEST_FILE}", Path(directory)), hide=True, warn=True
        )
        if not fetched.ok:
            print(f"[backups] WARNING: {remote.host} lists backups but its newest manifest could not be read.")
            return
        manifest = read_manifest(Path(directory))
    if manifest.get("system") != remote.name:
        print(f"[backups] WARNING: {remote.host} serves backups of '{manifest.get('system')}', not '{remote.name}'.")
        return
    print(f"[backups] {remote.host} serves {len(backup_ids)} backup(s) of {remote.name}, newest {backup_ids[-1]}")


@task(help={
    "name": "The other machine's system name",
    "host": "The SSH alias to reach it through, from ~/.ssh/config",
    "push": "Register a push target instead of a system to pull from",
})
def register(ctx: Context, name: str, host: str, push: bool = False) -> None:
    """Register a system to pull backups from, or with --push a machine to push this system's backups to."""
    settings = _settings(ctx)
    with _reported(), root_lock(settings.root):
        direction = Direction.PUSH if push else Direction.PULL
        remote = register_remote(settings.root, settings.system, name, host, direction)
    print(f"[backups] Registered {remote.name} ({remote.direction}) through {remote.host}")
    if remote.direction is Direction.PULL:
        with _reported():
            _check_pull_remote(ctx, remote)


@task(help={"name": "The system name to forget"})
def unregister(ctx: Context, name: str) -> None:
    """Forget a registered system. Backups already held for it stay until pruned."""
    settings = _settings(ctx)
    with _reported(), root_lock(settings.root):
        remote = unregister_remote(settings.root, name)
    print(f"[backups] Unregistered {remote.name}")


@task(help={
    "name": "The system name of the machine being given access",
    "key": "Its public key, or the path to a .pub file. Not needed with --revoke",
    "system": "Expose this system's directory instead of the own one, for disaster recovery",
    "push": "Let it write into its own directory here instead of reading",
    "source": "Only accept the key from this address (an sshd from= pattern)",
    "revoke": "Remove the access instead of granting it",
})
def authorize(ctx: Context, name: str, key: str | None = None, system: str | None = None, push: bool = False,
              source: str | None = None, revoke: bool = False) -> None:
    """
    Let another machine's key reach this machine's backups: read-only by default, write-only with --push.

    Writes a restricted, rrsync-forced line to ~/.ssh/authorized_keys. The key can do nothing but
    rsync, inside one directory, in one direction.
    """
    settings = _settings(ctx)
    with _reported():
        validate_system_name(name)
        if push and system:
            raise BackupError("A push always lands in the sender's own directory, so --system does not apply.")
        access = Access.PUSH if push else Access.READ
        exposed = name if push else validate_system_name(system or settings.system)
        tag = line_tag(name, access, exposed)

        if revoke:
            found = write_authorized_keys(AUTHORIZED_KEYS_PATH, tag, None)
            print(f"[backups] {'Revoked' if found else 'There was no'} {access} access for {name} to {exposed}")
            return

        if not key:
            raise BackupError("Pass the other machine's public key with --key.")
        rrsync = shutil.which("rrsync")
        if not rrsync:
            raise BackupError("rrsync is not installed. It ships with rsync; install or upgrade that first.")
        directory = system_directory(settings.root, exposed)
        ensure_root(settings.root)
        directory.mkdir(exist_ok=True)
        line = authorized_line(parse_public_key(key), name, access, exposed, directory, rrsync=rrsync, source=source)
        found = write_authorized_keys(AUTHORIZED_KEYS_PATH, tag, line)
        verb = "write into" if push else "read"
        print(f"[backups] {'Updated' if found else 'Granted'}: {name} may {verb} {directory} and nothing else")


@task(help={
    "remote": "Pull only this registered system",
    "system": "Store and verify the pull as this system, for disaster recovery. Needs --remote",
})
def pull(ctx: Context, remote: str | None = None, system: str | None = None) -> None:
    """Fetch the backups of registered systems over SSH with the read-only job key, then prune them."""
    settings = _settings(ctx)
    with _reported(), root_lock(settings.root):
        if system and not remote:
            raise BackupError("--system names what one remote serves, so it needs --remote as well.")
        remotes = _pull_remotes(settings, remote)
        if not remotes:
            print("[backups] No systems registered to pull from.")
            return
        pulled = []
        for chosen in remotes:
            target = validate_system_name(system) if system else chosen.name
            _pull(ctx, settings, chosen, target)
            pulled.append(target)
        _prune(settings, pulled)


@task(help={"remote": "Push only to this registered push target"})
def push(ctx: Context, remote: str | None = None) -> None:
    """
    Send this system's backups to registered push targets. Manual only, never run by the timer.

    Connects through each target's own SSH alias, with a key you made and protected with a passphrase.
    """
    settings = _settings(ctx)
    with _reported(), root_lock(settings.root):
        remotes = read_remotes(settings.root)
        targets = [item for item in remotes.values() if item.direction is Direction.PUSH]
        if remote:
            targets = [item for item in targets if item.name == remote]
            if not targets:
                raise BackupError(f"'{remote}' is not registered as a push target.")
        if not targets:
            print("[backups] No push targets registered.")
            return
        for target in targets:
            _push(ctx, settings, target)


@task(help={"system": "List every backup of this system and why retention keeps it"})
def status(ctx: Context, system: str | None = None) -> None:
    """Show the latest known backup of every system, from local files only."""
    settings = _settings(ctx)
    with _reported():
        now = _now()
        remotes = read_remotes(settings.root)
        order = system_order(settings.root, settings.system, remotes)
        if system:
            source = dict(order).get(system, "unregistered")
            print(render_system(
                system_status(settings.root, system, source, now, settings.policy, settings.stale_after),
                now, settings.policy,
            ))
            return
        systems = [
            system_status(settings.root, name, source, now, settings.policy, settings.stale_after)
            for name, source in order
        ]
        print(f"Backups in {settings.root}\n")
        print(render_overview(systems, push_status(settings.root, settings.system, remotes), now))


@task(help={
    "system": "Prune only this system",
    "dry_run": "Print what would be deleted without deleting it",
})
def prune(ctx: Context, system: str | None = None, dry_run: bool = False) -> None:
    """Apply retention: the last 14 days, the first backup of each month, and the newest own backup."""
    settings = _settings(ctx)
    with _reported(), root_lock(settings.root):
        _prune(settings, [system] if system else system_names(settings.root), dry_run=dry_run)


@task(help={
    "before": "Delete backups made before this date (YYYY-MM-DD) instead of the default age cutoff",
    "system": "Clean only this system",
    "dry_run": "Print what would be deleted without deleting it",
    "yes": "Skip the confirmation prompt",
})
def clean(ctx: Context, before: str | None = None, system: str | None = None, dry_run: bool = False,
          yes: bool = False) -> None:
    """Delete very old backups, by default those older than a year. Never run by the timer."""
    settings = _settings(ctx)
    with _reported(), root_lock(settings.root):
        if before:
            try:
                cutoff = datetime.strptime(before, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError as exc:
                raise BackupError(f"--before expects a date as YYYY-MM-DD, not '{before}'.") from exc
        else:
            cutoff = _now() - timedelta(days=settings.clean_after_days)

        doomed: list[Backup] = []
        for name in [system] if system else system_names(settings.root):
            backups = {backup.id: backup for backup in list_backups(settings.root, name)}
            doomed.extend(backups[backup_id] for backup_id in clean_candidates(
                list(backups), cutoff, name == settings.system
            ))

        if not doomed:
            print(f"[backups] Nothing made before {cutoff:%Y-%m-%d}.")
            return
        total = 0
        for backup in doomed:
            size = directory_size(backup.path)
            total += size
            print(f"  {backup.system}/{backup.id}  {format_size(size)}")
        print(f"[backups] {len(doomed)} backup(s) made before {cutoff:%Y-%m-%d}, {format_size(total)} in all")
        if dry_run:
            return
        if not yes and not _confirm("Delete them?"):
            raise Exit("[backups] Nothing deleted.")
        for backup in doomed:
            delete_directory(backup.path)
        print(f"[backups] Deleted {len(doomed)} backup(s)")


def _failure(exc: Exception) -> str:
    """One line about a failed step, instead of invoke's full report with the command and both streams."""
    if isinstance(exc, UnexpectedExit):
        lines = [line for line in exc.result.stderr.splitlines() if line.strip()]
        return f"exit code {exc.result.exited}" + (f": {lines[0]}" if lines else "")
    return str(exc)


@task
def run(ctx: Context) -> None:
    """
    What the hourly timer runs: back up if today's backup is due, pull every registered system, prune.

    A backup day starts at backups.daily_at (03:00), so a machine that is on then backs up then, and
    one that is not backs up at its first check after. A stack that is not up yet, or a remote that is
    off, is skipped until the next check. Anything else that fails is reported, the rest carries on,
    and the task fails at the end so systemd records it. Never pushes.
    """
    settings = _settings(ctx)
    failures: list[str] = []
    with _reported(), root_lock(settings.root):
        now = _now()
        own_backups = [backup.id for backup in list_backups(settings.root, settings.system)]
        if not backup_due(own_backups, now, settings.daily_at):
            since = day_start(now, settings.daily_at).astimezone()
            print(f"[backups] Already backed up since {since:%Y-%m-%d %H:%M}, nothing to create")
        else:
            try:
                _create(ctx, settings)
            except PostgresUnavailable:
                print("[backups] The postgres service is not running yet, will try again at the next check")
            except (BackupError, UnexpectedExit) as exc:
                failures.append(f"create: {_failure(exc)}")
        try:
            remotes = _pull_remotes(settings, None)
        except BackupError as exc:
            failures.append(f"remotes: {exc}")
            remotes = []
        for remote in remotes:
            try:
                if _pull(ctx, settings, remote, remote.name):
                    failures.append(f"pull {remote.name}: corrupt backups arrived")
            except UnexpectedExit as exc:
                if exc.result.exited == UNREACHABLE_EXIT_CODE:
                    print(f"[backups] {remote.name} is not reachable, will try again at the next check")
                else:
                    failures.append(f"pull {remote.name}: {_failure(exc)}")
            except BackupError as exc:
                failures.append(f"pull {remote.name}: {_failure(exc)}")
        _prune(settings, system_names(settings.root))
    if failures:
        for failure in failures:
            print(f"[backups] FAILED {failure}")
        raise Exit(f"[backups] {len(failures)} step(s) failed", code=1)
    print("[backups] Done")


@task(name="test", help={
    "test_file": "A path to a file containing a subset of tests to run",
    "test_method": "An expression of which test methods to run",
    "fail_fast": "Fails at first failing test when enabled",
})
def backups_test(ctx: Context, test_file: str | None = None, test_method: str | None = None,
                 fail_fast: bool = False) -> None:
    """Runs the tests for the backups tooling, which need neither Django nor a database."""
    test_file = test_file or "services/backups/tests"
    test_method_flag = f"-x -k {test_method}" if test_method else ""
    fail_fast_flag = "-x" if fail_fast else ""

    assert not test_method or test_file, "Can't specify a test method without specifying the test file"

    ctx.run(
        f"PYTHONPATH=. pytest -p no:django {test_file} {test_method_flag} {fail_fast_flag} --disable-warnings",
        echo=True, pty=True,
    )


collection = Collection(
    "backups",
    create, restore, register, unregister, authorize, pull, push, status, prune, clean, run, backups_test,
)
