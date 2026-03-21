import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


TEST_PASS_ROOT = Path(__file__).resolve().parent
TEST_PASS_CREDENTIALS_DIR = TEST_PASS_ROOT / "credentials"
TEST_PASS_KEYS_DIR = TEST_PASS_ROOT / "keys"
TEST_PASS_STORE_DIR = TEST_PASS_ROOT / "store"
TEST_PASS_GIT_DATES = {
    "ai/new/latest/openai": datetime(2024, 1, 2, 12, 0, tzinfo=timezone.utc),
    "ai/new/openai": datetime(2024, 1, 3, 12, 0, tzinfo=timezone.utc),
    "ai/openai": datetime(2024, 1, 4, 12, 0, tzinfo=timezone.utc),
    "bad/empty": datetime(2024, 1, 5, 12, 0, tzinfo=timezone.utc),
    "bad/malformed": datetime(2024, 1, 6, 12, 0, tzinfo=timezone.utc),
    "ops/anthropic": datetime(2024, 1, 7, 12, 0, tzinfo=timezone.utc),
}


def _test_store_environment(gpghome: Path, store_dir: Path, **extra: str) -> dict[str, str]:
    return {
        **os.environ,
        "GNUPGHOME": str(gpghome),
        "PASSWORD_STORE_DIR": str(store_dir),
        "PASSWORD_STORE_GPG_OPTS": "--trust-model always",
        **extra,
    }


def _test_store_git_environment(gpghome: Path, store_dir: Path, when: datetime) -> dict[str, str]:
    return _test_store_environment(
        gpghome,
        store_dir,
        GIT_AUTHOR_NAME="Credential Tests",
        GIT_AUTHOR_EMAIL="credential-tests@example.com",
        GIT_COMMITTER_NAME="Credential Tests",
        GIT_COMMITTER_EMAIL="credential-tests@example.com",
        GIT_AUTHOR_DATE=when.isoformat(),
        GIT_COMMITTER_DATE=when.isoformat(),
    )


def _test_store_key_files() -> list[Path]:
    return sorted(path for path in TEST_PASS_KEYS_DIR.glob("*.asc") if path.is_file())


def _cleanup_test_store_directory(store_dir: Path) -> None:
    store_dir.mkdir(parents=True, exist_ok=True)
    for child in store_dir.iterdir():
        if child.name == ".gitkeep":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def _test_store_fingerprint(gpghome: Path, store_dir: Path) -> str:
    result = subprocess.run(
        ["gpg", "--list-secret-keys", "--with-colons"],
        check=True,
        capture_output=True,
        text=True,
        env=_test_store_environment(gpghome, store_dir),
    )
    for line in result.stdout.splitlines():
        if line.startswith("fpr:"):
            return line.split(":")[9]
    raise RuntimeError("No secret test key was imported for pass test store setup.")


def setup_test_store(gpghome: Path, store_dir: Path = TEST_PASS_STORE_DIR) -> Path:
    gpghome.mkdir(parents=True, exist_ok=True, mode=0o700)
    gpghome.chmod(0o700)
    _cleanup_test_store_directory(store_dir)

    for key_file in _test_store_key_files():
        subprocess.run(
            ["gpg", "--batch", "--import", str(key_file)],
            check=True,
            capture_output=True,
            text=True,
            env=_test_store_environment(gpghome, store_dir),
        )

    subprocess.run(
        ["pass", "init", _test_store_fingerprint(gpghome, store_dir)],
        check=True,
        capture_output=True,
        text=True,
        env=_test_store_environment(gpghome, store_dir),
    )
    subprocess.run(
        ["git", "init"],
        check=True,
        capture_output=True,
        text=True,
        cwd=store_dir,
        env=_test_store_environment(gpghome, store_dir),
    )

    for credential_file in sorted(path for path in TEST_PASS_CREDENTIALS_DIR.rglob("*") if path.is_file()):
        relative_path = credential_file.relative_to(TEST_PASS_CREDENTIALS_DIR).as_posix()
        subprocess.run(
            ["pass", "insert", "-m", relative_path],
            input=credential_file.read_text(encoding="utf-8"),
            check=True,
            capture_output=True,
            text=True,
            env=_test_store_git_environment(gpghome, store_dir, TEST_PASS_GIT_DATES[relative_path]),
        )

    return store_dir
