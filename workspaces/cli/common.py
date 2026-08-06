from __future__ import annotations

from datetime import datetime, timezone
from os import getuid
from pathlib import Path
from secrets import token_urlsafe
from shlex import quote

from fabric import Connection
from invoke.context import Context

from workspaces.cli.client import WorkspaceRecord, get_ssh_config, patch_workspace
from workspaces.cli.constants import (
    NGINX_DIR,
    REPOS_DIR,
    SECRETS_DIR,
    WORKSPACE_SSH_KEYS_DIR,
    SSH_CONFIG_PATH,
    SUPERVISOR_DIR,
    WORKSPACES_DIR,
)
from workspaces.cli.setup import ensure_ssh_host_keys


DEFAULT_HOST = "localhost"
DEFAULT_SSH_PORT = 2222
DEFAULT_PROXY_PORT = 7000
OPENCODE_ATTACH_URL = "http://127.0.0.1:4096"
REDIS_URL = "redis://redis:6379/0"
# Workspaces reach management by compose service name, not through the host's published port.
WORKSPACE_MANAGEMENT_URL = "http://management:8000"
WORKSPACES_STATE_DIR = "/workspaces/state"
WORKSPACES_SECRETS_DIR = "/workspaces/secrets"
ACCOUNT_FILES = ("passwd", "group", "shadow", "gshadow")


def timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_text_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def workspace_repo_dir(workspace_module: str) -> Path:
    return REPOS_DIR / workspace_module


def workspace_secret_dir(workspace_module: str) -> Path:
    return SECRETS_DIR / workspace_module


def workspace_secret_env_path(workspace_module: str) -> Path:
    return workspace_secret_dir(workspace_module) / ".env"


def workspace_pgpass_path(workspace_module: str) -> Path:
    return workspace_secret_dir(workspace_module) / ".pgpass"


def container_workspace_secret_env_path(workspace_module: str) -> str:
    return f"{WORKSPACES_SECRETS_DIR}/{workspace_module}/.env"


def container_workspace_pgpass_path(workspace_module: str) -> str:
    return f"{WORKSPACES_SECRETS_DIR}/{workspace_module}/.pgpass"


def workspace_key_dir(workspace_module: str) -> Path:
    return WORKSPACE_SSH_KEYS_DIR / workspace_module


def workspace_private_key_path(workspace_module: str) -> Path:
    return workspace_key_dir(workspace_module) / "id_ed25519"


def workspace_public_key_path(workspace_module: str) -> Path:
    return workspace_key_dir(workspace_module) / "id_ed25519.pub"


def assert_workspace_state_clean(workspace_module: str) -> None:
    paths_to_check = [
        workspace_repo_dir(workspace_module),
        workspace_secret_dir(workspace_module),
        workspace_key_dir(workspace_module),
        SUPERVISOR_DIR / workspace_module,
        NGINX_DIR / workspace_module,
    ]

    dirty_paths = [path for path in paths_to_check if path.exists()]
    if dirty_paths:
        raise RuntimeError(
            "Refusing to create workspace because workspace state is not clean:\n"
            + "\n".join(f"- {path}" for path in dirty_paths)
        )


def ensure_workspace_keypair(ctx: Context, workspace_module: str) -> tuple[Path, Path]:
    key_dir = workspace_key_dir(workspace_module)
    key_dir.mkdir(parents=True, exist_ok=True)

    private_key = workspace_private_key_path(workspace_module)
    public_key = workspace_public_key_path(workspace_module)

    if private_key.exists() or public_key.exists():
        raise RuntimeError(f"Refusing to overwrite existing SSH keypair in {key_dir}")

    ctx.run(
        f'ssh-keygen -t ed25519 -f "{private_key}" -N "" -C "{workspace_module}@workspace.local"',
        echo=True,
    )
    return private_key, public_key


def ensure_workspace_secret_root() -> None:
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    SECRETS_DIR.chmod(0o711)


def render_workspace_secret_env(workspace_module: str, postgres_password: str, api_key: str) -> str:
    pgpass_path = container_workspace_pgpass_path(workspace_module)
    return "\n".join([
        f"MANAGEMENT_URL={WORKSPACE_MANAGEMENT_URL}",
        f"WORKSPACE_API_KEY={api_key}",
        f"POSTGRES_DB={workspace_module}",
        f"POSTGRES_USER={workspace_module}",
        f"POSTGRES_PASSWORD={postgres_password}",
        "POSTGRES_HOST=postgres",
        "POSTGRES_PORT=5432",
        f"PGDATABASE={workspace_module}",
        f"PGUSER={workspace_module}",
        "PGHOST=postgres",
        "PGPORT=5432",
        f"PGPASSFILE={pgpass_path}",
        # One Redis for every workspace, so queues and cache keys are namespaced by module rather
        # than by database index, which nothing allocates.
        f"REDIS_URL={REDIS_URL}",
        f"CELERY_BROKER_URL={REDIS_URL}",
        f"CELERY_RESULT_BACKEND={REDIS_URL}",
        f"OPENCODE_ATTACH_URL={OPENCODE_ATTACH_URL}",
        "",
    ])


def render_workspace_pgpass(workspace_module: str, postgres_password: str) -> str:
    return f"postgres:5432:{workspace_module}:{workspace_module}:{postgres_password}\n"


def render_workspace_shell_environment(workspace_module: str) -> str:
    secret_path = container_workspace_secret_env_path(workspace_module)
    return "\n".join([
        "# >>> just-build workspace secrets >>>",
        f'if [ -f "{secret_path}" ]; then',
        "    set -a",
        f'    . "{secret_path}"',
        "    set +a",
        "fi",
        "# <<< just-build workspace secrets <<<",
        "",
        "# >>> just-build workspace virtualenv >>>",
        'if [ -z "${VIRTUAL_ENV:-}" ] && [ -f "$HOME/venv/bin/activate" ]; then',
        '    . "$HOME/venv/bin/activate"',
        "fi",
        "# <<< just-build workspace virtualenv <<<",
        "",
    ])


def read_workspace_secret_environment(ctx: Context, workspace_module: str) -> dict[str, str]:
    secret_path = container_workspace_secret_env_path(workspace_module)
    result = docker_exec(ctx, f"cat {quote(secret_path)}", user="root", hide=True, warn=True)
    if not result.ok:
        raise RuntimeError(f"Workspace '{workspace_module}' is missing or cannot read its secret file at {secret_path}")

    environment: dict[str, str] = {}
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = stripped.partition("=")
        if not separator:
            raise RuntimeError(f"Malformed workspace secret line in {secret_path}: {line}")
        environment[key] = value

    return environment


def ensure_workspace_database(ctx: Context, workspace: WorkspaceRecord) -> None:
    secret_environment = read_workspace_secret_environment(ctx, workspace.module)
    required_keys = ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD")
    missing_keys = [key for key in required_keys if key not in secret_environment]
    if missing_keys:
        raise RuntimeError(f"Workspace '{workspace.module}' is missing secret values: {', '.join(missing_keys)}")

    ctx.run(
        "./services/postgres/scripts/setup_database.sh",
        env={
            "DATABASE_NAME": secret_environment["POSTGRES_DB"],
            "DATABASE_USER": secret_environment["POSTGRES_USER"],
            "DATABASE_PASSWORD": secret_environment["POSTGRES_PASSWORD"],
            "POSTGRES_USER": ctx.config.postgres.user,
            "PGPASSWORD": ctx.config.postgres.password,
            "POSTGRES_DB": getattr(ctx.config.postgres, "database", "postgres"),
            "PGHOST": secret_environment.get("POSTGRES_HOST", "postgres"),
            "PGPORT": secret_environment.get("POSTGRES_PORT", "5432"),
        },
        pty=True,
        echo=True,
    )


def ensure_workspace_secret_file(ctx: Context, workspace_module: str, api_key: str) -> Path:
    ensure_workspace_secret_root()

    secret_dir = workspace_secret_dir(workspace_module)
    secret_path = workspace_secret_env_path(workspace_module)
    pgpass_path = workspace_pgpass_path(workspace_module)
    if secret_path.exists():
        raise RuntimeError(f"Refusing to overwrite existing workspace secret file at {secret_path}")
    if pgpass_path.exists():
        raise RuntimeError(f"Refusing to overwrite existing workspace pgpass file at {pgpass_path}")

    secret_dir.mkdir(parents=True, exist_ok=False)
    secret_dir.chmod(0o700)
    postgres_password = token_urlsafe(32)
    write_text_file(secret_path, render_workspace_secret_env(workspace_module, postgres_password, api_key))
    write_text_file(pgpass_path, render_workspace_pgpass(workspace_module, postgres_password))
    secret_path.chmod(0o600)
    pgpass_path.chmod(0o600)

    quoted_dir = quote(f"{WORKSPACES_SECRETS_DIR}/{workspace_module}")
    quoted_owner = f"{quote(workspace_module)}:{quote(workspace_module)}"
    quoted_pgpass = quote(container_workspace_pgpass_path(workspace_module))
    docker_exec(
        ctx,
        f"test -f {quote(container_workspace_secret_env_path(workspace_module))}"
        f" && chown -R root:{quote(workspace_module)} {quoted_dir}"
        f" && chmod 750 {quoted_dir}"
        f" && chmod 640 {quote(container_workspace_secret_env_path(workspace_module))}"
        f" && chown {quoted_owner} {quoted_pgpass}"
        f" && chmod 600 {quoted_pgpass}",
    )
    return secret_path


def install_workspace_shell_environment(ctx: Context, workspace_module: str) -> None:
    quoted_module = quote(workspace_module)
    quoted_content = quote(render_workspace_shell_environment(workspace_module))
    for name in (".profile", ".bashrc"):
        path = quote(f"/home/{workspace_module}/{name}")
        docker_exec(
            ctx,
            f"printf '%s' {quoted_content} > {path}"
            f" && chown {quoted_module}:{quoted_module} {path}"
            f" && chmod 644 {path}",
        )


def ensure_workspace_static_dir(ctx: Context, workspace_module: str) -> None:
    quoted_module = quote(workspace_module)
    quoted_home_dir = quote(f"/home/{workspace_module}")
    quoted_static_dir = quote(f"/home/{workspace_module}/staticfiles")
    quoted_state_dir = quote(WORKSPACES_STATE_DIR)
    docker_exec(
        ctx,
        f"groupmod -P {quoted_state_dir} -a -U www-data {quoted_module}"
        f" && cp -a {quoted_state_dir}/etc/group /etc/group"
        f" && mkdir -p {quoted_static_dir}"
        f" && chown {quoted_module}:{quoted_module} {quoted_home_dir} {quoted_static_dir}"
        f" && chmod 750 {quoted_home_dir} {quoted_static_dir}",
    )


def ensure_workspaces_container(ctx: Context) -> None:
    ensure_ssh_host_keys(ctx)
    ctx.run("docker compose --profile workspaces up -d workspaces", echo=True)


def docker_exec(ctx: Context, script: str, *, user: str | None = None, hide: bool = False, warn: bool = False):
    user_flag = f"--user {quote(user)} " if user else ""
    command = f"docker compose exec -T {user_flag}workspaces sh -lc {quote(script)}"
    return ctx.run(command, echo=not hide, hide=hide, warn=warn)


def ensure_workspace_log_dir(ctx: Context, workspace_module: str) -> None:
    """
    Create the log directory supervisord writes into. It will not create parents itself.

    Owned by root and readable by the workspace group, so a workspace can tail its own logs and
    nobody else's.
    """
    quoted_module = quote(workspace_module)
    quoted_log_dir = quote(f"/var/log/workspaces/{workspace_module}")
    docker_exec(
        ctx,
        f"mkdir -p {quoted_log_dir}"
        f" && chown root:{quoted_module} {quoted_log_dir}"
        f" && chmod 750 {quoted_log_dir}",
        user="root",
    )


def assert_container_workspace_absent(ctx: Context, workspace_module: str) -> None:
    result = docker_exec(ctx, f"id -u {quote(workspace_module)}", hide=True, warn=True)
    if result.ok:
        raise RuntimeError(
            f"Refusing to create workspace because user '{workspace_module}' already exists in workspaces"
        )

    ssh_key_result = docker_exec(
        ctx,
        f"test ! -e /etc/ssh/authorized_keys/{quote(workspace_module)}",
        hide=True,
        warn=True,
    )
    if not ssh_key_result.ok:
        raise RuntimeError(
            "Refusing to create workspace because "
            f"/etc/ssh/authorized_keys/{workspace_module} already exists in workspaces"
        )


def ensure_workspace_state_account_files(ctx: Context) -> None:
    state_etc = f"{WORKSPACES_STATE_DIR}/etc"
    docker_exec(ctx, f"mkdir -p {quote(state_etc)}")
    for account_file in ACCOUNT_FILES:
        source = quote(f"/etc/{account_file}")
        target = quote(f"{state_etc}/{account_file}")
        docker_exec(ctx, f"test -f {target} || cp -a {source} {target}")


def sync_workspace_state_account_files(ctx: Context) -> None:
    account_files = " ".join(quote(f"{WORKSPACES_STATE_DIR}/etc/{account_file}") for account_file in ACCOUNT_FILES)
    _ = docker_exec(ctx, f"cp -a {account_files} /etc/")


def create_container_user_and_home(ctx: Context, workspace_module: str) -> None:
    quoted_module = quote(workspace_module)
    quoted_state_dir = quote(WORKSPACES_STATE_DIR)

    ensure_workspace_state_account_files(ctx)
    docker_exec(ctx, f"groupadd -P {quoted_state_dir} {quoted_module}")
    docker_exec(ctx, f"useradd -P {quoted_state_dir} -M -s /bin/bash -g {quoted_module} {quoted_module}")
    sync_workspace_state_account_files(ctx)
    docker_exec(
        ctx,
        f"mkdir -p /home/{quoted_module} && chown -R {quoted_module}:{quoted_module} /home/{quoted_module}",
    )


def grant_host_workspace_access(ctx: Context, workspace_module: str, host_uid: int | None = None) -> None:
    uid = getuid() if host_uid is None else host_uid
    quoted_home = quote(f"/home/{workspace_module}")
    script = (
        f"setfacl -R -m u:{uid}:rwX {quoted_home}"
        f" && find {quoted_home} -type d -exec setfacl -m d:u:{uid}:rwX {{}} +"
    )
    if workspace_secret_dir(workspace_module).exists():
        quoted_secrets = quote(f"{WORKSPACES_SECRETS_DIR}/{workspace_module}")
        script += (
            f" && setfacl -R -m u:{uid}:rwX {quoted_secrets}"
            f" && find {quoted_secrets} -type d -exec setfacl -m d:u:{uid}:rwX {{}} +"
        )
    docker_exec(ctx, script, user="root")


def reset_workspace_ownership(ctx: Context, workspace_module: str) -> None:
    ensure_workspaces_container(ctx)
    quoted_module = quote(workspace_module)
    quoted_home = quote(f"/home/{workspace_module}")
    quoted_secrets_dir = quote(f"{WORKSPACES_SECRETS_DIR}/{workspace_module}")
    quoted_pgpass = quote(container_workspace_pgpass_path(workspace_module))
    docker_exec(
        ctx,
        f"chown -R {quoted_module}:{quoted_module} {quoted_home}"
        f" && if [ -d {quoted_secrets_dir} ]; then"
        f" chown -R root:{quoted_module} {quoted_secrets_dir}"
        f" && if [ -f {quoted_pgpass} ]; then chown {quoted_module}:{quoted_module} {quoted_pgpass}; fi;"
        f" fi",
        user="root",
    )
    grant_host_workspace_access(ctx, workspace_module)


def publish_authorized_key(ctx: Context, workspace_module: str, public_key: str) -> None:
    quoted_module = quote(workspace_module)
    quoted_key = quote(public_key)

    directory_state = docker_exec(
        ctx,
        "test -d /etc/ssh/authorized_keys"
        " && test \"$(stat -c '%U:%G %a' /etc/ssh/authorized_keys)\" = 'root:root 755'",
        hide=True,
        warn=True,
    )
    if not directory_state.ok:
        raise RuntimeError(
            "Expected /etc/ssh/authorized_keys to exist as root:root with mode 755. "
            "Rebuild or recreate the workspaces container environment."
        )

    docker_exec(
        ctx,
        f"printf '%s\\n' {quoted_key} > /etc/ssh/authorized_keys/{quoted_module}"
        f" && test \"$(stat -c '%U:%G %a' /etc/ssh/authorized_keys/{quoted_module})\" = 'root:root 644'",
    )


def log_setup_step(workspace_module: str, step: str) -> None:
    patch_workspace(workspace_module, setup={step: timestamp()})


def refresh_generated_ssh_config() -> None:
    generated_config = get_ssh_config()
    if "# Automatically generated" not in generated_config:
        raise RuntimeError("Management returned SSH config content without the generated-file warning header.")
    write_text_file(SSH_CONFIG_PATH, generated_config)


def build_ssh_connection(workspace: WorkspaceRecord) -> Connection:
    identity_file = workspace.ssh.identity_file
    if not identity_file:
        raise RuntimeError(f"Workspace '{workspace.module}' does not have SSH identity metadata yet.")

    private_key = Path(identity_file)
    if not private_key.is_absolute():
        private_key = WORKSPACES_DIR.parent / private_key

    return Connection(
        host=workspace.ssh.host or DEFAULT_HOST,
        user=workspace.ssh.user or workspace.module,
        port=workspace.ssh.port or DEFAULT_SSH_PORT,
        connect_kwargs={"key_filename": [str(private_key)]},
    )


def require_setup_steps(workspace: WorkspaceRecord, steps: tuple[str, ...]) -> None:
    missing_steps = [step for step in steps if step not in workspace.setup]
    if missing_steps:
        raise RuntimeError(
            f"Workspace '{workspace.module}' is missing required setup steps: {', '.join(sorted(missing_steps))}"
        )
