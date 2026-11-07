from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from services.backups import access, remotes, status, storage, transport
from services.backups.retention import Policy


NOW = datetime(2026, 11, 20, 3, 0, tzinfo=timezone.utc)


def make_backup(root: Path, system: str, backup_id: str, label: str | None = None, manifest_system: str | None = None,
                content: bytes = b"dump") -> Path:
    directory = root / system / backup_id
    directory.mkdir(parents=True)
    (directory / "management.dump").write_bytes(content)
    storage.write_manifest(directory, {
        "format": storage.MANIFEST_FORMAT,
        "system": manifest_system or system,
        "id": backup_id,
        "label": label,
        "files": {
            "management.dump": storage.file_entry(
                directory / "management.dump", database="management", owner="management"
            ),
        },
    })
    return directory


# Storage

def test_the_keys_directory_is_not_a_system(tmp_path):
    (tmp_path / "keys").mkdir()
    (tmp_path / "a").mkdir()
    (tmp_path / "README.md").write_text("docs")

    assert storage.system_names(tmp_path) == ["a"]


def test_only_complete_backups_are_listed(tmp_path):
    make_backup(tmp_path, "a", "20261102T030000Z")
    make_backup(tmp_path, "a", "20261101T030000Z")
    (tmp_path / "a" / "20261103T030000Z.partial").mkdir()
    (tmp_path / "a" / "20261031T030000Z.corrupt").mkdir()
    (tmp_path / "a" / "20261030T030000Z").mkdir()  # no manifest

    assert [backup.id for backup in storage.list_backups(tmp_path, "a")] == ["20261101T030000Z", "20261102T030000Z"]


def test_a_sound_backup_verifies(tmp_path):
    directory = make_backup(tmp_path, "b", "20261101T030000Z")

    assert storage.verify_backup(directory, "b") == []


def test_verification_catches_a_changed_file(tmp_path):
    directory = make_backup(tmp_path, "b", "20261101T030000Z")
    (directory / "management.dump").write_bytes(b"dumq")

    assert storage.verify_backup(directory, "b") == ["management.dump does not match its checksum"]


def test_verification_catches_a_missing_file(tmp_path):
    directory = make_backup(tmp_path, "b", "20261101T030000Z")
    (directory / "management.dump").unlink()

    assert storage.verify_backup(directory, "b") == ["management.dump is missing"]


def test_verification_catches_backups_of_another_system(tmp_path):
    directory = make_backup(tmp_path, "b", "20261101T030000Z", manifest_system="c")

    assert storage.verify_backup(directory, "b") == ["made by system 'c', expected 'b'"]


def test_a_corrupt_backup_is_moved_aside_and_replaces_an_older_corrupt_copy(tmp_path):
    directory = make_backup(tmp_path, "b", "20261101T030000Z")
    (tmp_path / "b" / "20261101T030000Z.corrupt").mkdir()

    moved = storage.mark_corrupt(directory)

    assert moved.name == "20261101T030000Z.corrupt"
    assert (moved / storage.MANIFEST_FILE).exists()
    assert storage.list_backups(tmp_path, "b") == []


def test_find_backup_resolves_latest_and_ids(tmp_path):
    make_backup(tmp_path, "a", "20261101T030000Z")
    make_backup(tmp_path, "a", "20261102T030000Z")

    assert storage.find_backup(tmp_path, "a", "latest").id == "20261102T030000Z"
    assert storage.find_backup(tmp_path, "a", "20261101T030000Z").id == "20261101T030000Z"
    with pytest.raises(storage.BackupError, match="No backup"):
        storage.find_backup(tmp_path, "a", "20261103T030000Z")


def test_the_root_lock_refuses_a_second_holder(tmp_path):
    with storage.root_lock(tmp_path):
        with pytest.raises(storage.BackupError, match="Another backup task"):
            with storage.root_lock(tmp_path):
                pass
    assert oct(tmp_path.stat().st_mode & 0o777) == "0o700"


def test_the_lock_is_released_but_its_file_stays(tmp_path):
    with storage.root_lock(tmp_path):
        assert storage.lock_held(tmp_path)

    assert not storage.lock_held(tmp_path)
    assert (tmp_path / storage.LOCK_FILE).exists()


@pytest.mark.parametrize("name", ["", None, "A", "-a", "a_b", "a/b", "x" * 64, "keys"])
def test_unusable_system_names_are_refused(name):
    with pytest.raises(storage.BackupError):
        storage.validate_system_name(name)


def test_an_empty_root_setting_means_the_default():
    assert storage.backup_root("") == storage.DEFAULT_ROOT
    assert storage.backup_root(None) == storage.DEFAULT_ROOT
    assert storage.backup_root("/srv/backups") == Path("/srv/backups")


# Remotes

def test_registering_writes_an_ssh_config_block_per_system(tmp_path):
    remotes.register_remote(tmp_path, "a", "c", "c.example.com", user="fako", port=2200)
    remotes.register_remote(tmp_path, "a", "b", "b.example.com")

    text = (tmp_path / "config").read_text()
    assert f"""
Host just-build-backup-c
    HostName c.example.com
    User fako
    Port 2200
    IdentityFile {tmp_path / "keys" / "job"}
    IdentitiesOnly yes
""" in text
    assert oct((tmp_path / "config").stat().st_mode & 0o777) == "0o600"

    registered = remotes.read_remotes(tmp_path)
    assert list(registered) == ["b", "c"]
    assert registered["c"].host == "just-build-backup-c"
    assert (registered["c"].address, registered["c"].user, registered["c"].port) == ("c.example.com", "fako", 2200)
    assert registered["c"].options == ()

    remotes.unregister_remote(tmp_path, "b")
    assert list(remotes.read_remotes(tmp_path)) == ["c"]


def test_registering_again_replaces_the_block_and_keeps_options_added_by_hand(tmp_path):
    remotes.register_remote(tmp_path, "a", "c", "old.example.com")
    config = tmp_path / "config"
    with config.open("a") as stream:
        stream.write("    ProxyJump gate\n")

    remotes.register_remote(tmp_path, "a", "c", "new.example.com")

    [remote] = remotes.read_remotes(tmp_path).values()
    assert remote.address == "new.example.com"
    assert remote.options == ("ProxyJump gate",)
    assert config.read_text().count("Host just-build-backup-c") == 1


def test_blocks_that_are_not_ours_are_ignored(tmp_path):
    (tmp_path / "config").write_text("Host elsewhere\n    HostName x\n\nHost just-build-backup-c\n    HostName c\n")

    registered = remotes.read_remotes(tmp_path)

    assert list(registered) == ["c"]


def test_the_own_system_cannot_be_registered(tmp_path):
    with pytest.raises(storage.BackupError, match="own system name"):
        remotes.register_remote(tmp_path, "a", "a", "a.example.com")


# Authorized keys

PUBLIC_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample just-build-backups:a@laptop"


def test_a_read_line_forces_read_only_rrsync_on_one_directory():
    line = access.authorized_line(access.parse_public_key(PUBLIC_KEY), "a", "b",
                                  Path("/home/me/just_build/backups/b"), rrsync="/usr/bin/rrsync")

    assert line == (
        'restrict,command="/usr/bin/rrsync -ro /home/me/just_build/backups/b" '
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample just-build-backups:a:read:b"
    )


def test_a_line_can_be_tied_to_an_address():
    line = access.authorized_line("ssh-ed25519 AAAA", "c", "c", Path("/backups/c"), source="203.0.113.7")

    assert line == (
        'restrict,from="203.0.113.7",command="rrsync -ro /backups/c" ssh-ed25519 AAAA just-build-backups:c:read:c'
    )


def test_public_keys_are_read_from_files(tmp_path):
    key_file = tmp_path / "id.pub"
    key_file.write_text(PUBLIC_KEY + "\n")

    assert access.parse_public_key(str(key_file)) == "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample"
    with pytest.raises(storage.BackupError):
        access.parse_public_key("not a key")


def test_authorizing_again_replaces_only_its_own_line(tmp_path):
    path = tmp_path / ".ssh" / "authorized_keys"
    path.parent.mkdir()
    path.write_text("ssh-ed25519 PERSONAL me@laptop\n")
    tag = access.line_tag("a", "b")

    recovery_tag = access.line_tag("a", "a")

    assert access.write_authorized_keys(path, tag, f"restrict ssh-ed25519 OLD {tag}") is False
    access.write_authorized_keys(path, recovery_tag, f"restrict ssh-ed25519 RECOVERY {recovery_tag}")
    assert access.write_authorized_keys(path, tag, f"restrict ssh-ed25519 NEW {tag}") is True

    lines = path.read_text().splitlines()
    assert lines[0] == "ssh-ed25519 PERSONAL me@laptop"
    assert sum("ssh-ed25519 NEW" in line for line in lines) == 1
    assert not any("OLD" in line for line in lines)
    assert any("just-build-backups:a:read:a" in line for line in lines)
    assert oct(path.stat().st_mode & 0o777) == "0o600"

    assert access.write_authorized_keys(path, tag, None) is True
    assert not any(line.endswith(tag) for line in path.read_text().splitlines())
    assert path.read_text().startswith("ssh-ed25519 PERSONAL me@laptop\n")


# Transport

def test_pulls_never_delete_and_never_rewrite():
    command = transport.pull_command("just-build-backup-b", Path("/backups/b"), Path("/backups/config"))

    assert "--delete" not in command
    assert "--ignore-existing" in command
    assert "--delay-updates" in command
    assert "-e 'ssh -F /backups/config -o BatchMode=yes" in command
    assert command.endswith("just-build-backup-b:./ /backups/b/")


def test_listed_names_skip_the_directory_itself():
    listing = (
        "drwxr-xr-x          4,096 2026/11/01 03:00:12 .\n"
        "drwxr-xr-x          4,096 2026/11/01 03:00:12 20261101T030000Z\n"
    )

    assert transport.listed_names(listing) == ["20261101T030000Z"]


# Status

def test_status_orders_own_then_registered_then_the_rest(tmp_path):
    for system in ("x", "c", "a", "b"):
        (tmp_path / system).mkdir()
    remotes.register_remote(tmp_path, "a", "c", "c.example.com")
    remotes.register_remote(tmp_path, "a", "b", "b.example.com")

    order = status.system_order(tmp_path, "a", remotes.read_remotes(tmp_path))

    assert order == [("a", "own"), ("b", "pull"), ("c", "pull"), ("x", "unregistered")]


def test_status_flags_stale_systems_and_corrupt_backups(tmp_path):
    make_backup(tmp_path, "c", "20261101T030000Z")
    (tmp_path / "c" / "20261102T030000Z.corrupt").mkdir()

    result = status.system_status(tmp_path, "c", "pull", NOW, Policy(), timedelta(hours=36))

    assert result.latest is not None and result.latest.id == "20261101T030000Z"
    assert result.monthly == 1
    assert result.notes == ["stale", "1 corrupt"]


def test_the_overview_renders_every_system(tmp_path):
    make_backup(tmp_path, "a", "20261120T010000Z")
    systems = [
        status.system_status(tmp_path, "a", "own", NOW, Policy(), timedelta(hours=36)),
        status.system_status(tmp_path, "b", "pull", NOW, Policy(), timedelta(hours=36)),
    ]

    output = status.render_overview(systems, NOW).splitlines()

    assert output[0].split() == ["SYSTEM", "SOURCE", "LATEST", "AGE", "BACKUPS", "MONTHLY", "SIZE", "NOTE"]
    assert output[1].split()[:5] == ["a", "own", "2026-11-20", "01:00", "UTC"]
    assert output[2].endswith("no backups yet")
