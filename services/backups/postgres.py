"""
Dumping and restoring through the postgres container.

The tools run inside the container, so they always match the server's major version; a host pg_dump
older than the server refuses to dump. Data streams through `docker compose exec -T` to and from host
files. Inside the container psql connects over the local socket, which the official image trusts, so
no password is involved.
"""
from __future__ import annotations

from io import StringIO
from pathlib import Path
from shlex import quote
import re

from invoke.context import Context

from services.backups.storage import BackupError


# Services that hold connections to the databases and would fight a restore for them.
CONSUMER_SERVICES = ("management", "n8n", "n8n-worker", "workspaces")

DATABASES_QUERY = (
    "SELECT datname, pg_get_userbyid(datdba) FROM pg_database "
    "WHERE NOT datistemplate AND datname <> 'postgres' AND datname NOT LIKE 'test\\_%' ORDER BY datname"
)


class PostgresUnavailable(BackupError):
    """The stack is not up. The hourly timer treats this as not yet, rather than as a failure."""


def compose_command(repository_root: Path) -> str:
    """
    The compose invocation for this installation, independent of the caller's working directory.

    Built the way services/daemon/install.sh builds it, so it behaves the same from a shell and from
    the systemd unit.
    """
    root = quote(str(repository_root))
    return (
        f"docker compose --project-directory {root} --env-file {root}/.env --file {root}/docker-compose.yml"
    )


def sql_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class Postgres:

    def __init__(self, ctx: Context, repository_root: Path, superuser: str) -> None:
        self.ctx = ctx
        self.compose = compose_command(repository_root)
        self.superuser = superuser

    def exec(self, command: str) -> str:
        return f"{self.compose} exec -T postgres {command}"

    def psql(self, sql: str, database: str = "postgres") -> str:
        """Run one statement and return its unaligned, tuples-only output."""
        command = self.exec(
            f"psql -v ON_ERROR_STOP=1 -U {quote(self.superuser)} -d {quote(database)} -tA -F '|' -c {quote(sql)}"
        )
        return self.ctx.run(command, hide=True).stdout

    def ensure_running(self) -> None:
        result = self.ctx.run(f"{self.compose} ps --services --status running", hide=True, warn=True)
        if not result.ok or "postgres" not in result.stdout.split():
            raise PostgresUnavailable(
                "The postgres service is not running. Start the stack, for instance with "
                "'docker compose up -d postgres', and try again."
            )

    def server_version(self) -> str:
        return self.psql("SHOW server_version").strip()

    def databases(self) -> dict[str, str]:
        """Every database worth keeping, mapped to its owner. Test databases are recreated by test runs."""
        databases: dict[str, str] = {}
        for line in self.psql(DATABASES_QUERY).splitlines():
            if line.strip():
                name, owner = line.split("|", maxsplit=1)
                databases[name] = owner
        return databases

    def dump_globals(self, target: Path) -> None:
        command = self.exec(f"pg_dumpall -U {quote(self.superuser)} --globals-only")
        self.ctx.run(f"{command} > {quote(str(target))}", hide=True)

    def dump_database(self, database: str, target: Path) -> None:
        command = self.exec(f"pg_dump -U {quote(self.superuser)} -Fc {quote(database)}")
        self.ctx.run(f"{command} > {quote(str(target))}", hide=True)

    def restore_globals(self, source: Path) -> list[str]:
        """
        Apply the roles of a backup, and return the errors worth showing.

        Without ON_ERROR_STOP: an existing role fails its CREATE ROLE and still gets the ALTER ROLE
        that follows, which is what carries its attributes and password. The superuser's own lines are
        left out, because its old password would no longer match INVOKE_POSTGRES_PASSWORD.
        """
        sql = filter_superuser(source.read_text(), self.superuser)
        command = self.exec(f"psql -U {quote(self.superuser)} -d postgres -q")
        result = self.ctx.run(command, in_stream=StringIO(sql), hide=True, warn=True)
        return [
            line for line in result.stderr.splitlines()
            if line.strip() and "already exists" not in line
        ]

    def recreate_database(self, database: str, owner: str) -> None:
        self.psql(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname = {sql_literal(database)} AND pid <> pg_backend_pid()"
        )
        self.psql(f"DROP DATABASE IF EXISTS {sql_identifier(database)}")
        # template0, so the database is exactly what the dump describes. template1 carries the vector
        # extension setup_database.sh installs there, which the dump brings along itself.
        self.psql(f"CREATE DATABASE {sql_identifier(database)} OWNER {sql_identifier(owner)} TEMPLATE template0")

    def restore_database(self, database: str, source: Path) -> None:
        command = self.exec(f"pg_restore -U {quote(self.superuser)} -d {quote(database)} --exit-on-error")
        self.ctx.run(f"{command} < {quote(str(source))}", hide=True)

    def running_consumers(self) -> list[str]:
        result = self.ctx.run(f"{self.compose} ps --services --status running", hide=True)
        running = set(result.stdout.split())
        return [service for service in CONSUMER_SERVICES if service in running]

    def stop_services(self, services: list[str]) -> None:
        if services:
            print(f"[backups] Stopping {', '.join(services)}")
            self.ctx.run(f"{self.compose} stop {' '.join(services)}")

    def start_services(self, services: list[str]) -> None:
        if services:
            print(f"[backups] Starting {', '.join(services)}")
            self.ctx.run(f"{self.compose} start {' '.join(services)}")


def filter_superuser(globals_sql: str, superuser: str) -> str:
    pattern = re.compile(rf"^(CREATE|ALTER) ROLE (\"{re.escape(superuser)}\"|{re.escape(superuser)})( |;)")
    return "\n".join(line for line in globals_sql.splitlines() if not pattern.match(line)) + "\n"
