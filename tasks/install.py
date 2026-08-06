from __future__ import annotations

from getpass import getpass
from pathlib import Path
from shlex import quote
import tempfile

from invoke.collection import Collection
from invoke.context import Context
from invoke.exceptions import Exit
from invoke.tasks import task
from invoke.watchers import Responder, FailingResponder


HOSTS_FILE = Path("/etc/hosts")
HOSTS_IP = "127.0.0.1"
HOSTS_MARKER = "# just_build docker compose services"


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _docker_compose_service_names(ctx: Context, compose_path: Path) -> list[str]:
    result = ctx.run(f"docker compose --file {quote(str(compose_path))} config --services", hide=True)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _hosts_hostnames(hosts_path: Path) -> set[str]:
    hostnames: set[str] = set()
    for line in hosts_path.read_text().splitlines():
        tokens = line.split("#", maxsplit=1)[0].split()
        if len(tokens) < 2:
            continue
        hostnames.update(tokens[1:])
    return hostnames


@task
def hosts_file(ctx: Context) -> None:
    """
    Ensure docker compose service hostnames resolve via /etc/hosts.

    Appends any missing service names from docker-compose.yml so host-side
    processes can reach published container ports on localhost.
    """
    compose_path = _repository_root() / "docker-compose.yml"
    services = _docker_compose_service_names(ctx, compose_path)

    if not HOSTS_FILE.exists():
        raise Exit(f"Missing hosts file: {HOSTS_FILE}")

    existing = _hosts_hostnames(HOSTS_FILE)
    missing_hostnames = [name for name in services if name not in existing]
    if not missing_hostnames:
        print("[hosts] All docker compose services already present in /etc/hosts")
        return

    for name in missing_hostnames:
        print(f"[hosts] Adding {HOSTS_IP} {name}")

    lines: list[str] = []
    hosts_text = HOSTS_FILE.read_text()
    if HOSTS_MARKER not in hosts_text:
        lines.append("")
        lines.append(HOSTS_MARKER)

    lines.extend(f"{HOSTS_IP}\t{hostname}" for hostname in missing_hostnames)
    block = "\n".join(lines) + "\n"

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as tmp:
        tmp.write(block)
        tmp_path = Path(tmp.name)

    try:
        append_cmd = f"cat {quote(str(tmp_path))} >> {quote(str(HOSTS_FILE))}"
        ctx.sudo(
            f"sh -c {quote(append_cmd)}",
            password=getpass("[sudo] Password: "),
            hide=True,
        )
    finally:
        tmp_path.unlink(missing_ok=True)

    print(f"[install] Updated {HOSTS_FILE}")


@task(name="management_database", help={
    "recreate": "Recreates the database and role before migrating",
    "force_password": "Sets this password for all superusers whose configured password is null",
})
def management_database(ctx: Context, recreate: bool = True, force_password: str | None = None) -> None:
    """
    Create the management database, migrate it, and create pre-configured superusers.

    Runs on the host against the published PostgreSQL port, so it works whether management itself
    runs in its container or as a development server on the host.
    """
    if recreate:
        ctx.run(
            "./services/postgres/scripts/setup_database.sh",
            env={
                "DATABASE_NAME": ctx.config.management.database.name,
                "DATABASE_USER": ctx.config.management.database.user,
                "DATABASE_PASSWORD": ctx.config.management.database.password,
                "POSTGRES_USER": ctx.config.postgres.user,
                "PGPASSWORD": ctx.config.postgres.password,
                "POSTGRES_DB": getattr(ctx.config.postgres, "database", "postgres"),
                "PGHOST": "postgres",
                "PGPORT": "5432",
            },
            pty=True,
            echo=True,
        )
    with ctx.cd("management"):
        ctx.run("python manage.py migrate", pty=True, echo=True)
        for user in ctx.config.management.superusers:
            if user["password"] is None and force_password:
                user["password"] = force_password
            elif user["password"] is None:
                user["password"] = getpass(f"Password for '{user['username']}': ")
            watchers = [
                FailingResponder(
                    pattern=r"Error: That username is already taken\.",
                    response="\x03",  # Ctrl+C
                    sentinel="Username already exists, skipping user creation"
                ),
                Responder(pattern=r"Password:", response=f"{user['password']}\n"),
                Responder(pattern=r"Password \(again\):", response=f"{user['password']}\n"),
                Responder(pattern=r"Bypass password validation", response="y\n"),
            ]
            ctx.run(
                f"python manage.py createsuperuser --username {user['username']} --email {user['email']}",
                pty=True, echo=True, watchers=watchers, warn=True
            )


namespace = Collection("install", hosts_file, management_database)
