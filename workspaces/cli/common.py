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
    ACTIVE_NGINX_DIR,
    ACTIVE_SUPERVISOR_DIR,
    NGINX_TEMPLATE_PATH,
    REPOS_DIR,
    SECRETS_DIR,
    WORKSPACE_SSH_KEYS_DIR,
    SSH_CONFIG_PATH,
    STAGED_NGINX_DIR,
    STAGED_SUPERVISOR_DIR,
    SUPERVISOR_TEMPLATE_PATH,
    WORKSPACES_DIR,
)
from workspaces.cli.setup import ensure_ssh_host_keys


DEFAULT_HOST = "localhost"
DEFAULT_SSH_PORT = 2222
DEFAULT_PROXY_PORT = 7000
OPENCODE_ATTACH_URL = "http://127.0.0.1:4097"
WORKSPACES_STATE_DIR = "/workspaces/state"
WORKSPACES_SECRETS_DIR = "/workspaces/secrets"
ACCOUNT_FILES = ("passwd", "group", "shadow", "gshadow")


def timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def render_template(template_path: Path, replacements: dict[str, str]) -> str:
    content = template_path.read_text()
    for key, value in replacements.items():
        content = content.replace(key, value)
    return content


def write_text_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def publish_staged_file(staged_path: Path, active_path: Path) -> None:
    if not staged_path.exists():
        raise RuntimeError(f"Missing staged file {staged_path}")
    write_text_file(active_path, staged_path.read_text())


def extract_workspace_port(config_path: Path) -> int | None:
    if not config_path.exists():
        return None

    for line in config_path.read_text().splitlines():
        if "--port " not in line:
            continue
        port_fragment = line.split("--port ", 1)[1].split()[0]
        if port_fragment.isdigit():
            return int(port_fragment)

    return None


def next_workspace_port(workspace_module: str) -> int:
    config_paths = [
        ACTIVE_SUPERVISOR_DIR / f"{workspace_module}.conf",
        STAGED_SUPERVISOR_DIR / f"{workspace_module}.conf",
    ]
    for config_path in config_paths:
        existing_port = extract_workspace_port(config_path)
        if existing_port is not None:
            return existing_port

    ports: set[int] = set()
    for directory in (ACTIVE_SUPERVISOR_DIR, STAGED_SUPERVISOR_DIR):
        for config_path in directory.glob("*.conf"):
            port = extract_workspace_port(config_path)
            if port is not None:
                ports.add(port)

    port = 8001
    while port in ports:
        port += 1
    return port


def parse_workspace_domain(config_path: Path) -> str:
    for line in config_path.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("server_name "):
            return stripped.removeprefix("server_name ").rstrip(";")
    raise RuntimeError(f"Could not determine workspace domain from {config_path}")


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


def staged_supervisor_config_path(workspace_module: str) -> Path:
    return STAGED_SUPERVISOR_DIR / f"{workspace_module}.conf"


def staged_nginx_config_path(workspace_module: str) -> Path:
    return STAGED_NGINX_DIR / f"{workspace_module}.conf"


def active_supervisor_config_path(workspace_module: str) -> Path:
    return ACTIVE_SUPERVISOR_DIR / f"{workspace_module}.conf"


def active_nginx_config_path(workspace_module: str) -> Path:
    return ACTIVE_NGINX_DIR / f"{workspace_module}.conf"


def assert_workspace_state_clean(workspace_module: str) -> None:
    paths_to_check = [
        workspace_repo_dir(workspace_module),
        workspace_secret_dir(workspace_module),
        workspace_key_dir(workspace_module),
        staged_supervisor_config_path(workspace_module),
        staged_nginx_config_path(workspace_module),
        active_supervisor_config_path(workspace_module),
        active_nginx_config_path(workspace_module),
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


def render_workspace_secret_env(workspace_module: str, postgres_password: str) -> str:
    pgpass_path = container_workspace_pgpass_path(workspace_module)
    return "\n".join([
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


def ensure_workspace_secret_file(ctx: Context, workspace_module: str) -> Path:
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
    write_text_file(secret_path, render_workspace_secret_env(workspace_module, postgres_password))
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


def stop_workspace_program(ctx: Context, workspace_module: str, *, warn: bool = False) -> None:
    ensure_workspaces_container(ctx)
    docker_exec(ctx, f"supervisorctl stop {quote(workspace_module)}", warn=warn)


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
    docker_exec(
        ctx,
        f"setfacl -R -m u:{uid}:rwX {quoted_home}"
        f" && find {quoted_home} -type d -exec setfacl -m d:u:{uid}:rwX {{}} +",
        user="root",
    )


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


def stage_workspace_configs(workspace: WorkspaceRecord, domain: str) -> tuple[Path, Path]:
    workspace_port = next_workspace_port(workspace.module)
    asgi_module = f"{workspace.django_module}.asgi"

    supervisor_config = render_template(
        SUPERVISOR_TEMPLATE_PATH,
        {
            "PROJECT_NAME": workspace.module,
            "PROJECT_PORT": str(workspace_port),
            "ASGI_MODULE": asgi_module,
            "DJANGO_MODULE": workspace.django_module,
        },
    )
    supervisor_config = "; Automatically generated by invoke workspaces.create.\n" + supervisor_config

    nginx_config = render_template(
        NGINX_TEMPLATE_PATH,
        {
            "PROJECT_NAME": workspace.module,
            "PROJECT_PORT": str(workspace_port),
            "PROJECT_DOMAIN": domain,
        },
    )
    nginx_config = "# Automatically generated by invoke workspaces.create.\n" + nginx_config

    supervisor_path = staged_supervisor_config_path(workspace.module)
    nginx_path = staged_nginx_config_path(workspace.module)
    write_text_file(supervisor_path, supervisor_config)
    write_text_file(nginx_path, nginx_config)
    return supervisor_path, nginx_path


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
