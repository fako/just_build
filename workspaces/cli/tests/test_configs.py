import importlib

import pytest


configs_cli = importlib.import_module("workspaces.cli.configs")
runtimes_client = importlib.import_module("workspaces.cli.runtimes.client")


def build_manifest(root: str, files: dict[str, str],
                   workspaces: list[str] | None = None) -> runtimes_client.ManifestRecord:
    if workspaces is None:
        # Default to whichever workspaces the paths mention, which is what management sends.
        workspaces = sorted({path.split("/")[1] for path in files if len(path.split("/")) > 2})
    return runtimes_client.ManifestRecord(
        root=root,
        workspaces=workspaces,
        files=[{"path": path, "content": content} for path, content in files.items()],
    )


@pytest.fixture
def repository(tmp_path, monkeypatch):
    monkeypatch.setattr(configs_cli, "REPOSITORY_DIR", tmp_path)
    return tmp_path


def test_reconcile_writes_the_manifest(repository):
    manifest = build_manifest("src", {
        "supervisor/magic_match/web.conf": "[program:magic_match_web]\n",
        "nginx/magic_match/web.conf": "server {}\n",
    })

    written, removed = configs_cli.reconcile_configs(manifest)

    assert (repository / "src/supervisor/magic_match/web.conf").read_text() == "[program:magic_match_web]\n"
    assert (repository / "src/nginx/magic_match/web.conf").read_text() == "server {}\n"
    assert len(written) == 2
    assert removed == []


def test_reconcile_is_idempotent(repository):
    manifest = build_manifest("src", {"supervisor/magic_match/web.conf": "[program:magic_match_web]\n"})
    configs_cli.reconcile_configs(manifest)

    written, removed = configs_cli.reconcile_configs(manifest)

    # Nothing changed, so nothing is rewritten. Re-running has to stay cheap and quiet.
    assert written == []
    assert removed == []


def test_reconcile_rewrites_changed_content(repository):
    configs_cli.reconcile_configs(build_manifest("src", {"supervisor/magic_match/web.conf": "old\n"}))

    written, _ = configs_cli.reconcile_configs(build_manifest("src", {"supervisor/magic_match/web.conf": "new\n"}))

    assert (repository / "src/supervisor/magic_match/web.conf").read_text() == "new\n"
    assert len(written) == 1


def test_reconcile_prunes_what_the_manifest_no_longer_lists(repository):
    configs_cli.reconcile_configs(build_manifest("src", {
        "supervisor/magic_match/web.conf": "web\n",
        "supervisor/magic_match/worker.conf": "worker\n",
    }))

    # Disabling the worker is exactly this: it drops out of the manifest.
    written, removed = configs_cli.reconcile_configs(build_manifest("src", {
        "supervisor/magic_match/web.conf": "web\n",
    }))

    assert removed == [repository / "src/supervisor/magic_match/worker.conf"]
    assert not (repository / "src/supervisor/magic_match/worker.conf").exists()
    assert (repository / "src/supervisor/magic_match/web.conf").exists()


def test_reconcile_prunes_a_whole_workspace(repository):
    configs_cli.reconcile_configs(build_manifest("src", {
        "supervisor/magic_match/web.conf": "web\n",
        "supervisor/other/web.conf": "other\n",
    }))

    # "other" still exists as a workspace, it just has no enabled runtimes left.
    _, removed = configs_cli.reconcile_configs(build_manifest(
        "src", {"supervisor/magic_match/web.conf": "web\n"}, workspaces=["magic_match", "other"],
    ))

    assert removed == [repository / "src/supervisor/other/web.conf"]
    # The empty directory goes too, so a removed workspace leaves nothing behind.
    assert not (repository / "src/supervisor/other").exists()


def test_reconcile_leaves_unknown_workspaces_alone(repository):
    # A workspace management has no record of yet, for example one not adopted into runtimes.
    stranger = repository / "src/supervisor/legacy_workspace.conf"
    stranger.parent.mkdir(parents=True)
    stranger.write_text("; not managed here\n")

    _, removed = configs_cli.reconcile_configs(build_manifest(
        "src", {"supervisor/magic_match/web.conf": "web\n"}, workspaces=["magic_match"],
    ))

    assert removed == []
    assert stranger.exists()


def test_reconcile_prunes_the_flat_legacy_config_of_an_adopted_workspace(repository):
    legacy = repository / "src/supervisor/magic_match.conf"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("; the pre-runtimes layout\n")

    _, removed = configs_cli.reconcile_configs(build_manifest("src", {
        "supervisor/magic_match/web.conf": "web\n",
    }))

    assert removed == [legacy]


def test_reconcile_prunes_across_every_config_kind_of_a_known_workspace(repository):
    # A Django runtime turning into a Celery one loses its nginx block, which has to go.
    nginx_config = repository / "src/nginx/magic_match/web.conf"
    nginx_config.parent.mkdir(parents=True)
    nginx_config.write_text("server {}\n")

    _, removed = configs_cli.reconcile_configs(build_manifest("src", {"supervisor/magic_match/web.conf": "web\n"}))

    assert removed == [nginx_config]


def test_reconcile_can_skip_pruning(repository):
    configs_cli.reconcile_configs(build_manifest("src", {"supervisor/magic_match/worker.conf": "worker\n"}))

    _, removed = configs_cli.reconcile_configs(
        build_manifest("src", {"supervisor/magic_match/web.conf": "web\n"}), prune=False,
    )

    assert removed == []
    assert (repository / "src/supervisor/magic_match/worker.conf").exists()


def test_reconcile_ignores_files_that_are_not_configs(repository):
    note = repository / "src/supervisor/magic_match/README.md"
    note.parent.mkdir(parents=True)
    note.write_text("not a config\n")

    configs_cli.reconcile_configs(build_manifest("src", {"supervisor/magic_match/web.conf": "web\n"}))

    assert note.exists()


@pytest.mark.parametrize("path", [
    "../../../etc/passwd.conf",
    "/etc/passwd.conf",
    "supervisor/../../escape.conf",
])
def test_reconcile_refuses_paths_that_escape_the_root(repository, path):
    # Management is trusted, but a client that writes server-supplied paths straight to disk is the
    # wrong shape regardless of who is on the other end.
    with pytest.raises(configs_cli.ConfigPathError):
        configs_cli.reconcile_configs(build_manifest("src", {path: "malicious\n"}))


def test_reconcile_refuses_non_config_suffixes(repository):
    with pytest.raises(configs_cli.ConfigPathError):
        configs_cli.reconcile_configs(build_manifest("src", {"supervisor/magic_match/web.sh": "#!/bin/sh\n"}))


def test_reconcile_refuses_a_root_outside_the_repository(repository):
    with pytest.raises(configs_cli.ConfigPathError):
        configs_cli.reconcile_configs(build_manifest("../../etc", {"supervisor/web.conf": "x\n"}))
