from __future__ import annotations

from datetime import datetime, timezone
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


def next_workspace_port(workspace_slug: str) -> int:
    config_paths = [
        ACTIVE_SUPERVISOR_DIR / f"{workspace_slug}.conf",
        STAGED_SUPERVISOR_DIR / f"{workspace_slug}.conf",
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


def workspace_repo_dir(workspace_slug: str) -> Path:
    return REPOS_DIR / workspace_slug


def workspace_secret_dir(workspace_slug: str) -> Path:
    return SECRETS_DIR / workspace_slug


def workspace_secret_env_path(workspace_slug: str) -> Path:
    return workspace_secret_dir(workspace_slug) / ".env"


def workspace_pgpass_path(workspace_slug: str) -> Path:
    return workspace_secret_dir(workspace_slug) / ".pgpass"


def container_workspace_secret_env_path(workspace_slug: str) -> str:
    return f"{WORKSPACES_SECRETS_DIR}/{workspace_slug}/.env"


def container_workspace_pgpass_path(workspace_slug: str) -> str:
    return f"{WORKSPACES_SECRETS_DIR}/{workspace_slug}/.pgpass"


def workspace_key_dir(workspace_slug: str) -> Path:
    return WORKSPACE_SSH_KEYS_DIR / workspace_slug


def workspace_private_key_path(workspace_slug: str) -> Path:
    return workspace_key_dir(workspace_slug) / "id_ed25519"


def workspace_public_key_path(workspace_slug: str) -> Path:
    return workspace_key_dir(workspace_slug) / "id_ed25519.pub"


def staged_supervisor_config_path(workspace_slug: str) -> Path:
    return STAGED_SUPERVISOR_DIR / f"{workspace_slug}.conf"


def staged_nginx_config_path(workspace_slug: str) -> Path:
    return STAGED_NGINX_DIR / f"{workspace_slug}.conf"


def active_supervisor_config_path(workspace_slug: str) -> Path:
    return ACTIVE_SUPERVISOR_DIR / f"{workspace_slug}.conf"


def active_nginx_config_path(workspace_slug: str) -> Path:
    return ACTIVE_NGINX_DIR / f"{workspace_slug}.conf"


def assert_workspace_state_clean(workspace_slug: str) -> None:
    paths_to_check = [
        workspace_repo_dir(workspace_slug),
        workspace_secret_dir(workspace_slug),
        workspace_key_dir(workspace_slug),
        staged_supervisor_config_path(workspace_slug),
        staged_nginx_config_path(workspace_slug),
        active_supervisor_config_path(workspace_slug),
        active_nginx_config_path(workspace_slug),
    ]

    dirty_paths = [path for path in paths_to_check if path.exists()]
    if dirty_paths:
        raise RuntimeError(
            "Refusing to create workspace because workspace state is not clean:\n"
            + "\n".join(f"- {path}" for path in dirty_paths)
        )


def ensure_workspace_keypair(ctx: Context, workspace_slug: str) -> tuple[Path, Path]:
    key_dir = workspace_key_dir(workspace_slug)
    key_dir.mkdir(parents=True, exist_ok=True)

    private_key = workspace_private_key_path(workspace_slug)
    public_key = workspace_public_key_path(workspace_slug)

    if private_key.exists() or public_key.exists():
        raise RuntimeError(f"Refusing to overwrite existing SSH keypair in {key_dir}")

    ctx.run(
        f'ssh-keygen -t ed25519 -f "{private_key}" -N "" -C "{workspace_slug}@workspace.local"',
        echo=True,
    )
    return private_key, public_key


def ensure_workspace_secret_root() -> None:
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    SECRETS_DIR.chmod(0o711)


def render_workspace_secret_env(workspace_slug: str, postgres_password: str) -> str:
    pgpass_path = container_workspace_pgpass_path(workspace_slug)
    return "\n".join([
        f"POSTGRES_DB={workspace_slug}",
        f"POSTGRES_USER={workspace_slug}",
        f"POSTGRES_PASSWORD={postgres_password}",
        "POSTGRES_HOST=postgres",
        "POSTGRES_PORT=5432",
        f"PGDATABASE={workspace_slug}",
        f"PGUSER={workspace_slug}",
        "PGHOST=postgres",
        "PGPORT=5432",
        f"PGPASSFILE={pgpass_path}",
        "",
    ])


def render_workspace_pgpass(workspace_slug: str, postgres_password: str) -> str:
    return f"postgres:5432:{workspace_slug}:{workspace_slug}:{postgres_password}\n"


def render_workspace_shell_environment(workspace_slug: str) -> str:
    secret_path = container_workspace_secret_env_path(workspace_slug)
    return "\n".join([
        "# >>> just-build workspace secrets >>>",
        f'if [ -f "{secret_path}" ]; then',
        "    set -a",
        f'    . "{secret_path}"',
        "    set +a",
        "fi",
        "# <<< just-build workspace secrets <<<",
        "",
    ])


def read_workspace_secret_environment(workspace_slug: str) -> dict[str, str]:
    secret_path = workspace_secret_env_path(workspace_slug)
    if not secret_path.exists():
        raise RuntimeError(f"Workspace '{workspace_slug}' is missing its secret file at {secret_path}")

    environment: dict[str, str] = {}
    for line in secret_path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = stripped.partition("=")
        if not separator:
            raise RuntimeError(f"Malformed workspace secret line in {secret_path}: {line}")
        environment[key] = value

    return environment


def ensure_workspace_secret_file(ctx: Context, workspace_slug: str) -> Path:
    ensure_workspace_secret_root()

    secret_dir = workspace_secret_dir(workspace_slug)
    secret_path = workspace_secret_env_path(workspace_slug)
    pgpass_path = workspace_pgpass_path(workspace_slug)
    if secret_path.exists():
        raise RuntimeError(f"Refusing to overwrite existing workspace secret file at {secret_path}")
    if pgpass_path.exists():
        raise RuntimeError(f"Refusing to overwrite existing workspace pgpass file at {pgpass_path}")

    secret_dir.mkdir(parents=True, exist_ok=False)
    secret_dir.chmod(0o700)
    postgres_password = token_urlsafe(32)
    write_text_file(secret_path, render_workspace_secret_env(workspace_slug, postgres_password))
    write_text_file(pgpass_path, render_workspace_pgpass(workspace_slug, postgres_password))
    secret_path.chmod(0o600)
    pgpass_path.chmod(0o600)

    quoted_dir = quote(f"{WORKSPACES_SECRETS_DIR}/{workspace_slug}")
    quoted_owner = f"{quote(workspace_slug)}:{quote(workspace_slug)}"
    quoted_pgpass = quote(container_workspace_pgpass_path(workspace_slug))
    docker_exec(
        ctx,
        f"test -f {quote(container_workspace_secret_env_path(workspace_slug))}"
        f" && chown -R root:{quote(workspace_slug)} {quoted_dir}"
        f" && chmod 750 {quoted_dir}"
        f" && chmod 640 {quote(container_workspace_secret_env_path(workspace_slug))}"
        f" && chown {quoted_owner} {quoted_pgpass}"
        f" && chmod 600 {quoted_pgpass}",
    )
    return secret_path


def install_workspace_shell_environment(ctx: Context, workspace_slug: str) -> None:
    quoted_slug = quote(workspace_slug)
    quoted_content = quote(render_workspace_shell_environment(workspace_slug))
    for name in (".profile", ".bashrc"):
        path = quote(f"/home/{workspace_slug}/{name}")
        docker_exec(
            ctx,
            f"printf '%s' {quoted_content} > {path}"
            f" && chown {quoted_slug}:{quoted_slug} {path}"
            f" && chmod 644 {path}",
        )


def ensure_workspaces_container(ctx: Context) -> None:
    ensure_ssh_host_keys(ctx)
    ctx.run("docker compose --profile workspaces up -d workspaces", echo=True)


def docker_exec(ctx: Context, script: str, *, user: str | None = None, hide: bool = False, warn: bool = False):
    user_flag = f"--user {quote(user)} " if user else ""
    command = f"docker compose exec -T {user_flag}workspaces sh -lc {quote(script)}"
    return ctx.run(command, echo=not hide, hide=hide, warn=warn)


def assert_container_workspace_absent(ctx: Context, workspace_slug: str) -> None:
    result = docker_exec(ctx, f"id -u {quote(workspace_slug)}", hide=True, warn=True)
    if result.ok:
        raise RuntimeError(
            f"Refusing to create workspace because user '{workspace_slug}' already exists in workspaces"
        )

    ssh_key_result = docker_exec(
        ctx,
        f"test ! -e /etc/ssh/authorized_keys/{quote(workspace_slug)}",
        hide=True,
        warn=True,
    )
    if not ssh_key_result.ok:
        raise RuntimeError(
            "Refusing to create workspace because "
            f"/etc/ssh/authorized_keys/{workspace_slug} already exists in workspaces"
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


def create_container_user_and_home(ctx: Context, workspace_slug: str) -> None:
    quoted_slug = quote(workspace_slug)
    quoted_state_dir = quote(WORKSPACES_STATE_DIR)

    ensure_workspace_state_account_files(ctx)
    docker_exec(ctx, f"groupadd -P {quoted_state_dir} {quoted_slug}")
    docker_exec(ctx, f"useradd -P {quoted_state_dir} -M -s /bin/bash -g {quoted_slug} {quoted_slug}")
    sync_workspace_state_account_files(ctx)
    docker_exec(ctx, f"mkdir -p /home/{quoted_slug} && chown -R {quoted_slug}:{quoted_slug} /home/{quoted_slug}")


def publish_authorized_key(ctx: Context, workspace_slug: str, public_key: str) -> None:
    quoted_slug = quote(workspace_slug)
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
        f"printf '%s\\n' {quoted_key} > /etc/ssh/authorized_keys/{quoted_slug}"
        f" && test \"$(stat -c '%U:%G %a' /etc/ssh/authorized_keys/{quoted_slug})\" = 'root:root 644'",
    )


def stage_workspace_configs(workspace: WorkspaceRecord, domain: str) -> tuple[Path, Path]:
    workspace_port = next_workspace_port(workspace.slug)
    asgi_module = f"{workspace.django_module}.asgi"

    supervisor_config = render_template(
        SUPERVISOR_TEMPLATE_PATH,
        {
            "PROJECT_NAME": workspace.slug,
            "PROJECT_PORT": str(workspace_port),
            "ASGI_MODULE": asgi_module,
            "DJANGO_MODULE": workspace.django_module,
        },
    )
    supervisor_config = "; Automatically generated by invoke workspaces.create.\n" + supervisor_config

    nginx_config = render_template(
        NGINX_TEMPLATE_PATH,
        {
            "PROJECT_NAME": workspace.slug,
            "PROJECT_PORT": str(workspace_port),
            "PROJECT_DOMAIN": domain,
        },
    )
    nginx_config = "# Automatically generated by invoke workspaces.create.\n" + nginx_config

    supervisor_path = staged_supervisor_config_path(workspace.slug)
    nginx_path = staged_nginx_config_path(workspace.slug)
    write_text_file(supervisor_path, supervisor_config)
    write_text_file(nginx_path, nginx_config)
    return supervisor_path, nginx_path


def log_setup_step(workspace_slug: str, step: str) -> None:
    patch_workspace(workspace_slug, setup={step: timestamp()})


def refresh_generated_ssh_config() -> None:
    generated_config = get_ssh_config()
    if "# Automatically generated" not in generated_config:
        raise RuntimeError("Management returned SSH config content without the generated-file warning header.")
    write_text_file(SSH_CONFIG_PATH, generated_config)


def build_ssh_connection(workspace: WorkspaceRecord) -> Connection:
    identity_file = workspace.ssh.identity_file
    if not identity_file:
        raise RuntimeError(f"Workspace '{workspace.slug}' does not have SSH identity metadata yet.")

    private_key = Path(identity_file)
    if not private_key.is_absolute():
        private_key = WORKSPACES_DIR.parent / private_key

    return Connection(
        host=workspace.ssh.host or DEFAULT_HOST,
        user=workspace.ssh.user or workspace.slug,
        port=workspace.ssh.port or DEFAULT_SSH_PORT,
        connect_kwargs={"key_filename": [str(private_key)]},
    )


def require_setup_steps(workspace: WorkspaceRecord, steps: tuple[str, ...]) -> None:
    missing_steps = [step for step in steps if step not in workspace.setup]
    if missing_steps:
        raise RuntimeError(
            f"Workspace '{workspace.slug}' is missing required setup steps: {', '.join(sorted(missing_steps))}"
        )
