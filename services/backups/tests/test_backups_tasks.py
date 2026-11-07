"""
The tasks against a recording context that plays docker, rsync and ssh.

Tasks are called through their body, because invoke's own call insists on a real Context and these
only need something with run() and config.
"""
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import re

import pytest
from invoke.exceptions import Exit, UnexpectedExit
from invoke.runners import Result

from services.backups import postgres, storage, tasks
from services.backups.remotes import register_remote


GLOBALS = """\
CREATE ROLE postgres;
ALTER ROLE postgres WITH SUPERUSER LOGIN PASSWORD 'SCRAM-SHA-256$old';
CREATE ROLE management;
ALTER ROLE management WITH LOGIN PASSWORD 'SCRAM-SHA-256$management';
"""


def call(task: Any, *args: Any, **kwargs: Any) -> None:
    task.body(*args, **kwargs)


def result(stdout: str = "", ok: bool = True, stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(stdout=stdout, stderr=stderr, ok=ok)


class RecordingContext:
    """Answers the commands the tasks run, writing dump files where a command redirects into one."""

    def __init__(self, root: Path, system: str = "a") -> None:
        self.config = SimpleNamespace(
            backups=SimpleNamespace(
                system=system,
                root=str(root),
                daily_at="03:00",
                retention=SimpleNamespace(days=14, remote_months=12, clean_after_days=365),
                stale_after_hours=36,
            ),
            postgres=SimpleNamespace(user="postgres"),
        )
        self.commands: list[str] = []
        self.inputs: list[str] = []
        self.running = ["postgres", "management", "n8n", "workspaces"]
        self.responders: list[tuple[str, Callable[[str], Any]]] = []

    def respond(self, pattern: str, handler: Callable[[str], Any]) -> None:
        self.responders.append((pattern, handler))

    def run(self, command: str, **kwargs):
        self.commands.append(command)
        if "in_stream" in kwargs:
            self.inputs.append(kwargs["in_stream"].read())
        for pattern, handler in self.responders:
            if re.search(pattern, command):
                return handler(command)
        if "ps --services --status running" in command:
            return result("\n".join(self.running) + "\n")
        if "SHOW server_version" in command:
            return result("16.4\n")
        if "FROM pg_database" in command:
            return result("management|management\nn8n|n8n\n")
        if "rev-parse" in command:
            return result("abc1234\n")
        redirect = re.search(r" > (\S+)$", command)
        if redirect:
            Path(redirect.group(1).strip("'")).write_text(GLOBALS if "pg_dumpall" in command else "dump of " + command)
        return result()


@pytest.fixture
def root(tmp_path):
    return tmp_path / "backups"


def test_create_writes_a_complete_backup_through_the_container(root):
    ctx = RecordingContext(root)

    call(tasks.create, ctx, label="before upgrade")

    [backup] = storage.list_backups(root, "a")
    manifest = backup.manifest()
    assert manifest["system"] == "a"
    assert manifest["label"] == "before upgrade"
    assert manifest["commit"] == "abc1234"
    assert manifest["postgres_version"] == "16.4"
    assert set(manifest["files"]) == {"globals.sql", "management.dump", "n8n.dump"}
    assert manifest["files"]["n8n.dump"]["owner"] == "n8n"
    assert storage.verify_backup(backup.path, "a") == []
    assert not storage.list_suffixed(root, "a", storage.PARTIAL_SUFFIX)
    dumps = [command for command in ctx.commands if "pg_dump " in command]
    assert all("exec -T postgres pg_dump -U postgres -Fc" in command for command in dumps)


def test_a_failed_create_leaves_nothing_behind(root):
    ctx = RecordingContext(root)

    def fail(command):
        raise UnexpectedExit(Result(command=command, exited=1, stderr="boom"))

    ctx.respond(r"pg_dump .* n8n", fail)

    with pytest.raises(UnexpectedExit):
        call(tasks.create, ctx)

    assert list((root / "a").iterdir()) == []


def test_create_refuses_when_postgres_is_down(root):
    ctx = RecordingContext(root)
    ctx.running = ["management"]

    with pytest.raises(Exit, match="postgres service is not running"):
        call(tasks.create, ctx)


def test_restore_saves_the_current_state_and_restores_with_consumers_stopped(root):
    ctx = RecordingContext(root)
    call(tasks.create, ctx)
    [chosen] = storage.list_backups(root, "a")
    ctx.commands.clear()
    ctx.running = ["postgres", "management", "n8n"]

    with pytest.MonkeyPatch.context() as patch:
        # Two backups in one second would collide, so give the safety backup an id of its own.
        patch.setattr(tasks, "new_backup_id", lambda started=None: "20991231T000000Z")
        call(tasks.restore, ctx, backup=chosen.id, yes=True)

    labels = {backup.id: backup.manifest()["label"] for backup in storage.list_backups(root, "a")}
    assert labels["20991231T000000Z"] == "pre-restore"

    def position(fragment: str) -> int:
        return next(index for index, command in enumerate(ctx.commands) if fragment in command)

    assert position("pg_dumpall") < position("stop management n8n")
    assert position("stop management n8n") < position("psql -U postgres -d postgres -q")
    assert position('DROP DATABASE IF EXISTS "management"') < position("pg_restore -U postgres -d management")
    assert position("pg_restore -U postgres -d n8n") < position("start management n8n")
    assert 'CREATE DATABASE "n8n" OWNER "n8n" TEMPLATE template0' in "\n".join(ctx.commands)
    # The superuser keeps the password .env gives it.
    assert "ROLE postgres" not in ctx.inputs[0]
    assert "CREATE ROLE management;" in ctx.inputs[0]


def test_restore_can_take_a_single_database(root):
    ctx = RecordingContext(root)
    call(tasks.create, ctx)
    ctx.commands.clear()

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(tasks, "new_backup_id", lambda started=None: "20991231T000000Z")
        call(tasks.restore, ctx, backup=storage.list_backups(root, "a")[0].id, database="n8n", yes=True)

    restored = [command for command in ctx.commands if "pg_restore" in command]
    assert len(restored) == 1 and "-d n8n" in restored[0]


def test_restore_refuses_a_backup_that_does_not_verify(root):
    ctx = RecordingContext(root)
    call(tasks.create, ctx)
    [chosen] = storage.list_backups(root, "a")
    (chosen.path / "n8n.dump").write_text("tampered")

    with pytest.raises(Exit, match="cannot be restored"):
        call(tasks.restore, ctx, yes=True)


def make_remote_backup(source: Path, system: str, backup_id: str) -> None:
    directory = source / backup_id
    directory.mkdir(parents=True)
    (directory / "globals.sql").write_text(GLOBALS)
    storage.write_manifest(directory, {
        "format": storage.MANIFEST_FORMAT, "system": system, "id": backup_id,
        "files": {"globals.sql": storage.file_entry(directory / "globals.sql")},
    })


def rsync_from(source: Path, corrupt: str | None = None):
    """Play rsync: copy what the source serves and report it the way --out-format=%n does."""
    def handler(command):
        destination = Path(command.split()[-2].strip("'"))
        names = []
        for backup in sorted(source.iterdir()):
            target = destination / backup.name
            if target.exists():
                continue
            target.mkdir(parents=True)
            names.append(backup.name + "/")
            for file in backup.iterdir():
                (target / file.name).write_bytes(file.read_bytes())
                names.append(f"{backup.name}/{file.name}")
            if backup.name == corrupt:
                (target / "globals.sql").write_text("truncated")
        return result("\n".join(names) + "\n")
    return handler


def test_pull_keeps_what_verifies_and_sets_aside_what_does_not(root, tmp_path):
    source = tmp_path / "remote-b"
    make_remote_backup(source, "b", "20261101T030000Z")
    make_remote_backup(source, "b", "20261102T030000Z")
    ctx = RecordingContext(root)
    register_remote(root, "a", "b", "b.example.com")
    ctx.respond(r"^rsync -a ", rsync_from(source, corrupt="20261102T030000Z"))

    call(tasks.pull, ctx)

    assert [backup.id for backup in storage.list_backups(root, "b")] == ["20261101T030000Z"]
    assert (root / "b" / "20261102T030000Z.corrupt").is_dir()
    [command] = [command for command in ctx.commands if command.startswith("rsync")]
    assert "just-build-backup-b:./" in command and "--delete" not in command
    assert f"ssh -F {root / 'config'} -o BatchMode=yes" in command


def test_pull_with_system_recovers_backups_served_under_another_name(root, tmp_path):
    source = tmp_path / "remote-a-serving-b"
    make_remote_backup(source, "b", "20261101T030000Z")
    ctx = RecordingContext(root, system="b")
    register_remote(root, "b", "a", "a.example.com")
    ctx.respond(r"^rsync -a ", rsync_from(source))

    call(tasks.pull, ctx, remote="a", system="b")

    assert [backup.id for backup in storage.list_backups(root, "b")] == ["20261101T030000Z"]


def test_run_carries_on_past_a_failing_remote_and_still_fails(root, tmp_path):
    source = tmp_path / "remote-c"
    make_remote_backup(source, "c", "20261101T030000Z")
    ctx = RecordingContext(root)
    register_remote(root, "a", "b", "b.example.com")
    register_remote(root, "a", "c", "c.example.com")

    def refused(command):
        raise UnexpectedExit(Result(command=command, exited=12, stderr="rrsync error: not allowed"))

    ctx.respond(r"just-build-backup-b:", refused)
    ctx.respond(r"^rsync -a .*just-build-backup-c:", rsync_from(source))

    with pytest.raises(Exit, match="1 step"):
        call(tasks.run, ctx)

    assert len(storage.list_backups(root, "a")) == 1
    assert len(storage.list_backups(root, "c")) == 1


def test_run_skips_a_remote_that_is_off_without_failing(root, capsys):
    ctx = RecordingContext(root)
    register_remote(root, "a", "b", "b.example.com")

    def unreachable(command):
        raise UnexpectedExit(Result(command=command, exited=255, stderr="ssh: connect to host b: No route to host"))

    ctx.respond(r"just-build-backup-b:", unreachable)

    call(tasks.run, ctx)

    assert "b is not reachable, will try again" in capsys.readouterr().out


def test_run_waits_for_the_stack_instead_of_failing(root, capsys):
    ctx = RecordingContext(root)
    ctx.running = []

    call(tasks.run, ctx)

    assert "not running yet" in capsys.readouterr().out
    assert storage.list_backups(root, "a") == []


def test_run_backs_up_once_per_day(root, monkeypatch, capsys):
    ctx = RecordingContext(root)
    monkeypatch.setattr(tasks, "backup_due", lambda backup_ids, now, daily_at: not backup_ids)

    call(tasks.run, ctx)
    call(tasks.run, ctx)

    assert len(storage.list_backups(root, "a")) == 1
    assert "Already backed up since" in capsys.readouterr().out


def test_authorize_writes_a_restricted_line_and_revoke_removes_it(root, tmp_path, monkeypatch):
    authorized_keys = tmp_path / "ssh" / "authorized_keys"
    monkeypatch.setattr(tasks, "AUTHORIZED_KEYS_PATH", authorized_keys)
    monkeypatch.setattr(tasks.shutil, "which", lambda name: "/usr/bin/rrsync")
    ctx = RecordingContext(root, system="b")

    call(tasks.authorize, ctx, name="a", key="ssh-ed25519 AAAAKEY a@host")
    call(tasks.authorize, ctx, name="b", key="ssh-ed25519 AAAARECOVER b@host", system="c")

    lines = authorized_keys.read_text().splitlines()
    assert lines[0] == (
        f'restrict,command="/usr/bin/rrsync -ro {root / "b"}" ssh-ed25519 AAAAKEY just-build-backups:a:read:b'
    )
    assert lines[1] == (
        f'restrict,command="/usr/bin/rrsync -ro {root / "c"}" ssh-ed25519 AAAARECOVER just-build-backups:b:read:c'
    )
    assert (root / "c").is_dir()

    call(tasks.authorize, ctx, name="a", revoke=True)
    assert authorized_keys.read_text().splitlines() == lines[1:]


def test_clean_deletes_old_backups_before_a_date(root):
    ctx = RecordingContext(root)
    for backup_id in ("20240101T030000Z", "20250101T030000Z", "20260101T030000Z"):
        make_remote_backup(root / "a", "a", backup_id)
    make_remote_backup(root / "b", "b", "20250101T030000Z")

    call(tasks.clean, ctx, before="2026-01-01", system="a", yes=True)

    assert [backup.id for backup in storage.list_backups(root, "a")] == ["20260101T030000Z"]
    assert len(storage.list_backups(root, "b")) == 1


def test_filter_superuser_leaves_other_roles_alone():
    filtered = postgres.filter_superuser(GLOBALS, "postgres")

    assert filtered.splitlines() == [
        "CREATE ROLE management;",
        "ALTER ROLE management WITH LOGIN PASSWORD 'SCRAM-SHA-256$management';",
    ]


def test_run_skips_the_check_while_another_task_holds_the_root(root, capsys):
    ctx = RecordingContext(root)

    with storage.root_lock(root):
        call(tasks.run, ctx)

    assert "Another backup task is running, will try again" in capsys.readouterr().out
    assert ctx.commands == []


def test_a_manual_task_still_refuses_a_held_root(root):
    ctx = RecordingContext(root)

    with storage.root_lock(root):
        with pytest.raises(Exit, match="Another backup task"):
            call(tasks.create, ctx)


def test_status_says_when_a_task_is_running(root, capsys):
    ctx = RecordingContext(root)
    storage.ensure_root(root)

    call(tasks.status, ctx)
    assert "running right now" not in capsys.readouterr().out

    with storage.root_lock(root):
        call(tasks.status, ctx)
    assert "A backup task is running right now." in capsys.readouterr().out


def test_connect_prints_the_authorize_command_with_this_machines_key(root, capsys):
    job_key = storage.job_key_path(root)
    job_key.parent.mkdir(parents=True)
    Path(f"{job_key}.pub").write_text("ssh-ed25519 AAAAJOB just-build-backups:a@host-a\n")
    ctx = RecordingContext(root)

    call(tasks.connect, ctx, name="b", address="b.example.com", user="fako")

    output = capsys.readouterr().out
    assert "invoke backups.authorize --name=a --key='ssh-ed25519 AAAAJOB just-build-backups:a@host-a'" in output
    assert "invoke backups.register --name=b --address=b.example.com --user=fako\n" in output


def test_connect_without_a_job_key_says_how_to_get_one(root):
    with pytest.raises(Exit, match="install.backups"):
        call(tasks.connect, RecordingContext(root))


def test_register_writes_the_block_and_checks_what_the_job_key_can_read(root, capsys):
    ctx = RecordingContext(root)
    ctx.respond(r"^rsync --list-only", lambda command: result(ok=False, stderr="Permission denied (publickey)."))

    call(tasks.register, ctx, name="b", address="b.example.com", user="fako", port="2200")

    output = capsys.readouterr().out
    assert "Host just-build-backup-b\n    HostName b.example.com\n    User fako\n    Port 2200\n" in output
    assert "could not list just-build-backup-b yet: Permission denied" in output
    assert "just-build-backup-b" in (root / "config").read_text()


def test_check_lists_what_the_job_key_can_read_on_every_system(root, capsys):
    ctx = RecordingContext(root)
    register_remote(root, "a", "b", "b.example.com")
    register_remote(root, "a", "c", "c.example.com")
    ctx.respond(r"^rsync --list-only .*just-build-backup-b:", lambda command: result(
        "drwx------          4,096 2026/11/01 03:00:12 .\n"
    ))
    ctx.respond(r"^rsync --list-only .*just-build-backup-c:", lambda command: result(
        ok=False, stderr="Connection refused"
    ))

    call(tasks.check, ctx)

    output = capsys.readouterr().out
    assert "just-build-backup-b is reachable but holds no backups yet." in output
    assert "could not list just-build-backup-c yet: Connection refused" in output
