from __future__ import annotations

from getpass import getpass
from pathlib import Path
from shlex import quote
import secrets
import string
import tempfile
import uuid

import requests
from invoke.collection import Collection
from invoke.context import Context
from invoke.exceptions import Exit
from invoke.tasks import task
from invoke.watchers import Responder, FailingResponder

from workspaces.cli.common import ensure_ssh_host_keys
from workspaces.cli.constants import SSH_CONFIG_PATH


HOSTS_FILE = Path("/etc/hosts")
HOSTS_IP = "127.0.0.1"
HOSTS_MARKER = "# just_build docker compose services"

ENVIRONMENT_FILE = ".env"
ENVIRONMENT_EXAMPLE_FILE = ".env.example"

# Alphanumeric only. activate.sh sources .env unquoted and docker compose interpolates ${...}, so a
# value holding $, (, ), ` or quotes breaks one of the two. Length makes up for the smaller alphabet.
SECRET_ALPHABET = string.ascii_letters + string.digits
PASSWORD_LENGTH = 32
SECRET_KEY_LENGTH = 50

# Variables without a generator are copied over unchanged, so anything that looks like a secret but
# is missing from GENERATORS is worth a warning rather than a silent placeholder in .env.
SECRET_NAME_HINTS = ("PASSWORD", "SECRET", "TOKEN", "API_KEY")

USER_SSH_CONFIG = Path.home() / ".ssh" / "config"


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _docker_compose_service_names(ctx: Context, compose_path: Path) -> list[str]:
    result = ctx.run(f"docker compose --file {quote(str(compose_path))} config --services", hide=True)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _random_secret(length: int) -> str:
    return "".join(secrets.choice(SECRET_ALPHABET) for _ in range(length))


def _generate_password(example_value: str) -> str:
    return _random_secret(PASSWORD_LENGTH)


def _generate_secret_key(example_value: str) -> str:
    return _random_secret(SECRET_KEY_LENGTH)


def _generate_api_key(example_value: str) -> str:
    """
    Replace the placeholder UUID while keeping the scheme prefix.

    Management partitions the key on ':' and rejects anything whose scheme it does not know, so the
    prefix has to survive regeneration.
    """
    scheme, separator, _ = example_value.partition(":")
    prefix = f"{scheme}{separator}" if separator else ""
    return f"{prefix}{uuid.uuid4()}"


ENVIRONMENT_GENERATORS = {
    "INVOKE_POSTGRES_PASSWORD": _generate_password,
    "INVOKE_WORKSPACES_SUPERVISOR_PASSWORD": _generate_password,
    "INVOKE_MANAGEMENT_SECURITY_SECRET_KEY": _generate_secret_key,
    "INVOKE_MANAGEMENT_SECURITY_API_KEY": _generate_api_key,
    "INVOKE_MANAGEMENT_DATABASE_PASSWORD": _generate_password,
    "INVOKE_N8N_DATABASE_PASSWORD": _generate_password,
}


def _environment_variable_name(line: str) -> str | None:
    """Return the variable a .env line assigns, or None for comments and blanks."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    return stripped.split("=", maxsplit=1)[0].strip()


def _environment_values(environment_path: Path) -> dict[str, str]:
    """Read the values an existing .env assigns, so --force can carry the preserved ones over."""
    if not environment_path.exists():
        return {}

    values: dict[str, str] = {}
    for line in environment_path.read_text().splitlines():
        name = _environment_variable_name(line)
        if name is not None:
            values[name] = line.split("=", maxsplit=1)[1].strip()
    return values


def _hosts_hostnames(hosts_path: Path) -> set[str]:
    hostnames: set[str] = set()
    for line in hosts_path.read_text().splitlines():
        tokens = line.split("#", maxsplit=1)[0].split()
        if len(tokens) < 2:
            continue
        hostnames.update(tokens[1:])
    return hostnames


@task(name="environment", help={
    "force": "Overwrites an existing .env, replacing every secret in it with a freshly generated one",
    "missing": "Keeps every value the existing .env already has, and only fills in what is absent",
})
def environment(ctx: Context, force: bool = False, missing: bool = False) -> None:
    """
    Write .env from .env.example, generating a secret for every value that needs one.

    Comments, ordering and non-secret defaults are copied over untouched, so .env stays readable and
    .env.example remains the place to document new variables.

    On an installation that is already running, --missing is the one to reach for: .env.example grows
    variables over time, and it adds those without touching the secrets postgres, its roles and the
    workspaces were built with. --force is for starting over, and rotates all of them.
    """
    root = _repository_root()
    example_path = root / ENVIRONMENT_EXAMPLE_FILE
    environment_path = root / ENVIRONMENT_FILE

    if not example_path.exists():
        raise Exit(f"Missing {ENVIRONMENT_EXAMPLE_FILE}: {example_path}")
    if force and missing:
        raise Exit("--force and --missing ask for opposite things. Pick one.")
    if environment_path.exists() and not (force or missing):
        raise Exit(
            f"Refusing to overwrite the existing {ENVIRONMENT_FILE}. Pass --missing to fill in only "
            "what it does not have yet, or --force to replace it, which regenerates every secret in "
            "it and will lock you out of a running stack."
        )

    existing_values = _environment_values(environment_path)

    lines: list[str] = []
    generated: list[str] = []
    kept: list[str] = []
    unfilled_secrets: list[str] = []

    for line in example_path.read_text().splitlines():
        name = _environment_variable_name(line)
        if name is None:
            lines.append(line)
            continue

        # Under --missing anything already answered stays answered, secret or not: a value in the
        # file is a decision somebody made, and a fresh one would undo it.
        if missing and existing_values.get(name):
            lines.append(f"{name}={existing_values[name]}")
            kept.append(name)
            continue

        example_value = line.split("=", maxsplit=1)[1].strip()
        generator = ENVIRONMENT_GENERATORS.get(name)
        if generator is None:
            if any(hint in name for hint in SECRET_NAME_HINTS) and not example_value:
                unfilled_secrets.append(name)
            lines.append(line)
            continue

        lines.append(f"{name}={generator(example_value)}")
        generated.append(name)

    written = {*generated, *kept}
    absent_from_example = [name for name in ENVIRONMENT_GENERATORS if name not in written]
    if absent_from_example:
        raise Exit(
            f"{ENVIRONMENT_EXAMPLE_FILE} is missing expected variables: {', '.join(absent_from_example)}"
        )

    environment_path.write_text("\n".join(lines) + "\n")
    # The file holds every secret the stack runs on, so keep it off other accounts on this host.
    environment_path.chmod(0o600)

    for name in generated:
        print(f"[environment] Generated {name}")
    if kept:
        print(f"[environment] Kept {len(kept)} existing value(s) untouched")
    for name in unfilled_secrets:
        print(f"[environment] WARNING: {name} has no generator and no value, set it by hand")
    print(f"[install] Wrote {environment_path}")


@task
def hosts_file(ctx: Context) -> None:
    """
    Ensure docker compose service hostnames resolve via /etc/hosts.

    Appends any missing service names from docker-compose.yml so host-side
    processes can reach published container ports on localhost.
    """
    compose_path = _repository_root() / "docker-compose.yml"
    services = _docker_compose_service_names(ctx, compose_path)
    # n8n hands out webhook and editor URLs under this name and nginx serves it, so it has to resolve
    # here too. It is not a compose service name, which is why it is added separately.
    hostnames = [*services, ctx.config.n8n.host]

    if not HOSTS_FILE.exists():
        raise Exit(f"Missing hosts file: {HOSTS_FILE}")

    existing = _hosts_hostnames(HOSTS_FILE)
    missing_hostnames = [name for name in hostnames if name not in existing]
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


def _check_ssh_config_include() -> bool:
    """
    Report whether the user's SSH config includes the generated workspace aliases.

    The fabfile addresses workspaces by alias and fails to resolve them without this, which looks
    like a connection problem rather than a missing line.
    """
    include_line = f"Include {SSH_CONFIG_PATH}"
    if USER_SSH_CONFIG.exists() and str(SSH_CONFIG_PATH) in USER_SSH_CONFIG.read_text():
        return True

    print("")
    print(f"Generated workspace SSH aliases are not included from {USER_SSH_CONFIG}.")
    print("Add this line at the top of that file to use 'fab -H <alias>' and Cursor Remote SSH:")
    print("")
    print(f"    {include_line}")
    return False


@task
def ssh(ctx: Context) -> None:
    """
    Generate SSH host keys for the workspaces container and check the SSH config include.

    Run this before building, because the workspaces image copies the host keys in at build time.
    """
    generated = ensure_ssh_host_keys(ctx)
    if not generated:
        print("SSH host keys already exist. Delete them first to regenerate.")
    else:
        print("\nSSH host keys generated. Rebuild the container to use them:")
        print("  docker compose --profile workspaces up --build")

    _check_ssh_config_include()


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


def _postgres_environment(ctx: Context) -> dict[str, str]:
    """The connection environment the postgres scripts and psql calls below share."""
    return {
        "POSTGRES_USER": ctx.config.postgres.user,
        "PGPASSWORD": ctx.config.postgres.password,
        "POSTGRES_DB": getattr(ctx.config.postgres, "database", "postgres"),
        "PGHOST": "postgres",
        "PGPORT": "5432",
    }


def _sql_literal(value: str) -> str:
    """Quote a value for SQL. Not shlex.quote, which quotes for a shell and leaves a bare word bare."""
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def _database_exists(ctx: Context, database_name: str) -> bool:
    environment_variables = _postgres_environment(ctx)
    query = f"SELECT 1 FROM pg_database WHERE datname = {_sql_literal(database_name)}"
    result = ctx.run(
        f"psql -U {quote(ctx.config.postgres.user)} -d {quote(environment_variables['POSTGRES_DB'])} "
        f"-tAc {quote(query)}",
        env=environment_variables,
        hide=True,
        warn=True,
    )
    if not result.ok:
        raise Exit(
            "Could not check for the n8n database: "
            f"{result.stderr.strip() or 'psql exited without a message'}\n"
            "If PostgreSQL is not up, start it with 'docker compose --profile control up -d postgres', "
            "and check that 'invoke install.hosts-file' has run so the 'postgres' hostname resolves."
        )
    return result.stdout.strip() == "1"


def _n8n_api_request(ctx: Context, path: str, api_key: str) -> tuple[int, dict]:
    """One authenticated call to the n8n public API, from the host rather than from management."""
    url = f"{ctx.config.n8n.api.url.rstrip('/')}{path}"
    try:
        response = requests.get(url, headers={"X-N8N-API-KEY": api_key}, timeout=10)
    except requests.RequestException as exc:
        raise Exit(
            f"Could not reach the n8n API at {url}: {exc}\n"
            "Start it with 'docker compose --profile control up -d n8n' and make sure "
            "'invoke install.hosts-file' has run so the 'n8n' hostname resolves."
        ) from exc

    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, {}


def _print_api_key_instructions(ctx: Context) -> None:
    print("")
    print("No n8n API key is configured yet. n8n has no environment variable or CLI command that")
    print("mints one, so this step is manual and only has to happen once:")
    print("")
    print(f"  1. Open http://{ctx.config.n8n.host}/ and complete the owner setup if you have not already.")
    print("  2. Go to Settings > n8n API and create an API key.")
    print("  3. Give it the workflow and workflowTags scopes, or leave it unrestricted.")
    print(f"  4. Put it in {ENVIRONMENT_FILE} as INVOKE_N8N_API_KEY=<key>")
    print("  5. Run 'source activate.sh', then 'invoke install.n8n' again.")


def _print_project_instructions(ctx: Context) -> None:
    print("")
    print("No n8n project is configured yet. Every workspace writes into one shared project, and")
    print("workspaces are kept apart by tags rather than by projects. Listing projects over the API is")
    print("a licensed feature, so the id is copied by hand:")
    print("")
    print(f"  1. Open http://{ctx.config.n8n.host}/ and pick the project workflows should live in.")
    print("  2. Take the id out of the browser URL: /projects/<id>/workflows")
    print(f"  3. Put it in {ENVIRONMENT_FILE} as INVOKE_N8N_API_PROJECT_ID=<id>")
    print("  4. Run 'source activate.sh', then 'invoke install.n8n' again.")


@task(name="n8n")
def n8n(ctx: Context) -> None:
    """
    Prepare n8n for management: create its database, then check the API key and project.

    Safe to run repeatedly. The database step is skipped when the database is already there, because
    the setup script it calls drops before it creates, and dropping this one takes every workflow and
    credential in n8n with it. Recreating the n8n database is a deliberate manual act, not a flag.
    """
    database_name = ctx.config.n8n.database.name
    if _database_exists(ctx, database_name):
        print(f"[n8n] Database '{database_name}' already exists, leaving it alone")
    else:
        if not ctx.config.n8n.database.password:
            # --missing rather than --force: the latter would rotate the postgres and management
            # passwords too, which the running containers and database roles were built with.
            raise Exit(
                f"INVOKE_N8N_DATABASE_PASSWORD is empty in {ENVIRONMENT_FILE}. Fill it in with "
                "'invoke install.environment --missing', which leaves every value you already have "
                "alone, then run 'source activate.sh'."
            )
        ctx.run(
            "./services/postgres/scripts/setup_database.sh",
            env={
                "DATABASE_NAME": database_name,
                "DATABASE_USER": ctx.config.n8n.database.user,
                "DATABASE_PASSWORD": ctx.config.n8n.database.password,
                **_postgres_environment(ctx),
            },
            pty=True,
            echo=True,
        )
        print("")
        print("[n8n] Database created. Start n8n before continuing:")
        print("  docker compose --profile control up -d n8n")

    api_key = ctx.config.n8n.api.key
    if not api_key:
        _print_api_key_instructions(ctx)
        return

    project_id = ctx.config.n8n.api.project_id
    if not project_id:
        _print_project_instructions(ctx)
        return

    status, body = _n8n_api_request(ctx, f"/workflows?projectId={project_id}&limit=1", api_key)
    if status in (401, 403):
        raise Exit(
            f"n8n rejected the configured API key with HTTP {status}. Mint a new one under "
            f"Settings > n8n API and update INVOKE_N8N_API_KEY in {ENVIRONMENT_FILE}."
        )
    if status != 200:
        raise Exit(f"n8n answered HTTP {status} for the verification call: {body}")

    workflows = body.get("data", [])
    print("")
    print(f"[n8n] API key accepted at {ctx.config.n8n.api.url}")
    print(f"[n8n] Project {project_id} currently holds {'at least one' if workflows else 'no'} workflow")
    if not workflows:
        # A project id that does not exist filters to an empty list rather than erroring, so an empty
        # result cannot distinguish "new project" from "wrong id". Say so instead of implying success.
        print("[n8n] An empty result also looks like this when the project id is wrong. Check the id in")
        print(f"[n8n] the editor URL at http://{ctx.config.n8n.host}/ if you expected workflows here.")
    print("")
    print("Management can now reach n8n. Next step:")
    print("  docker compose --profile control up -d management")


namespace = Collection("install", environment, hosts_file, ssh, management_database, n8n)
