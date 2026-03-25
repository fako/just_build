from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from shlex import quote

from fabric import Connection
from invoke import Context

from workspaces.cli.client import ProjectRecord, get_ssh_config, patch_project
from workspaces.cli.constants import (
    ACTIVE_NGINX_DIR,
    ACTIVE_SUPERVISOR_DIR,
    NGINX_TEMPLATE_PATH,
    PROJECT_REPOS_DIR,
    PROJECT_SSH_KEYS_DIR,
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


def extract_project_port(config_path: Path) -> int | None:
    if not config_path.exists():
        return None

    for line in config_path.read_text().splitlines():
        if "--port " not in line:
            continue
        port_fragment = line.split("--port ", 1)[1].split()[0]
        if port_fragment.isdigit():
            return int(port_fragment)

    return None


def next_project_port(project_slug: str) -> int:
    config_paths = [
        ACTIVE_SUPERVISOR_DIR / f"{project_slug}.conf",
        STAGED_SUPERVISOR_DIR / f"{project_slug}.conf",
    ]
    for config_path in config_paths:
        existing_port = extract_project_port(config_path)
        if existing_port is not None:
            return existing_port

    ports: set[int] = set()
    for directory in (ACTIVE_SUPERVISOR_DIR, STAGED_SUPERVISOR_DIR):
        for config_path in directory.glob("*.conf"):
            port = extract_project_port(config_path)
            if port is not None:
                ports.add(port)

    port = 8001
    while port in ports:
        port += 1
    return port


def parse_project_domain(config_path: Path) -> str:
    for line in config_path.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("server_name "):
            return stripped.removeprefix("server_name ").rstrip(";")
    raise RuntimeError(f"Could not determine project domain from {config_path}")


def project_repo_dir(project_slug: str) -> Path:
    return PROJECT_REPOS_DIR / project_slug


def project_key_dir(project_slug: str) -> Path:
    return PROJECT_SSH_KEYS_DIR / project_slug


def project_private_key_path(project_slug: str) -> Path:
    return project_key_dir(project_slug) / "id_ed25519"


def project_public_key_path(project_slug: str) -> Path:
    return project_key_dir(project_slug) / "id_ed25519.pub"


def staged_supervisor_config_path(project_slug: str) -> Path:
    return STAGED_SUPERVISOR_DIR / f"{project_slug}.conf"


def staged_nginx_config_path(project_slug: str) -> Path:
    return STAGED_NGINX_DIR / f"{project_slug}.conf"


def active_supervisor_config_path(project_slug: str) -> Path:
    return ACTIVE_SUPERVISOR_DIR / f"{project_slug}.conf"


def active_nginx_config_path(project_slug: str) -> Path:
    return ACTIVE_NGINX_DIR / f"{project_slug}.conf"


def assert_project_workspace_clean(project_slug: str) -> None:
    paths_to_check = [
        project_repo_dir(project_slug),
        project_key_dir(project_slug),
        staged_supervisor_config_path(project_slug),
        staged_nginx_config_path(project_slug),
        active_supervisor_config_path(project_slug),
        active_nginx_config_path(project_slug),
    ]

    dirty_paths = [path for path in paths_to_check if path.exists()]
    if dirty_paths:
        raise RuntimeError(
            "Refusing to create project because workspace state is not clean:\n"
            + "\n".join(f"- {path}" for path in dirty_paths)
        )


def ensure_project_keypair(ctx: Context, project_slug: str) -> tuple[Path, Path]:
    key_dir = project_key_dir(project_slug)
    key_dir.mkdir(parents=True, exist_ok=True)

    private_key = project_private_key_path(project_slug)
    public_key = project_public_key_path(project_slug)

    if private_key.exists() or public_key.exists():
        raise RuntimeError(f"Refusing to overwrite existing SSH keypair in {key_dir}")

    ctx.run(
        f'ssh-keygen -t ed25519 -f "{private_key}" -N "" -C "{project_slug}@workspace.local"',
        echo=True,
    )
    return private_key, public_key


def ensure_workspaces_container(ctx: Context) -> None:
    ensure_ssh_host_keys(ctx)
    ctx.run("docker compose --profile workspaces up -d workspaces", echo=True)


def docker_exec(ctx: Context, script: str, *, user: str | None = None, hide: bool = False, warn: bool = False):
    user_flag = f"--user {quote(user)} " if user else ""
    command = f"docker compose exec -T {user_flag}workspaces sh -lc {quote(script)}"
    return ctx.run(command, echo=not hide, hide=hide, warn=warn)


def assert_container_project_absent(ctx: Context, project_slug: str) -> None:
    result = docker_exec(ctx, f"id -u {quote(project_slug)}", hide=True, warn=True)
    if result.ok:
        raise RuntimeError(f"Refusing to create project because user '{project_slug}' already exists in workspaces")

    ssh_key_result = docker_exec(ctx, f"test ! -e /etc/ssh/authorized_keys/{quote(project_slug)}", hide=True, warn=True)
    if not ssh_key_result.ok:
        raise RuntimeError(
            f"Refusing to create project because /etc/ssh/authorized_keys/{project_slug} already exists in workspaces"
        )


def create_container_user_and_home(ctx: Context, project_slug: str) -> None:
    docker_exec(ctx, f"useradd -m -s /bin/bash {quote(project_slug)}")
    docker_exec(ctx, f"mkdir -p /home/{quote(project_slug)} && chown -R {quote(project_slug)}:{quote(project_slug)} /home/{quote(project_slug)}")


def publish_authorized_key(ctx: Context, project_slug: str, public_key: str) -> None:
    quoted_slug = quote(project_slug)
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


def stage_project_configs(project: ProjectRecord, domain: str) -> tuple[Path, Path]:
    project_port = next_project_port(project.slug)
    asgi_module = f"{project.django_module}.asgi"

    supervisor_config = render_template(
        SUPERVISOR_TEMPLATE_PATH,
        {
            "PROJECT_NAME": project.slug,
            "PROJECT_PORT": str(project_port),
            "ASGI_MODULE": asgi_module,
            "DJANGO_MODULE": project.django_module,
        },
    )
    supervisor_config = "; Automatically generated by invoke workspaces.create.\n" + supervisor_config

    nginx_config = render_template(
        NGINX_TEMPLATE_PATH,
        {
            "PROJECT_NAME": project.slug,
            "PROJECT_PORT": str(project_port),
            "PROJECT_DOMAIN": domain,
        },
    )
    nginx_config = "# Automatically generated by invoke workspaces.create.\n" + nginx_config

    supervisor_path = staged_supervisor_config_path(project.slug)
    nginx_path = staged_nginx_config_path(project.slug)
    write_text_file(supervisor_path, supervisor_config)
    write_text_file(nginx_path, nginx_config)
    return supervisor_path, nginx_path


def log_setup_step(project_slug: str, step: str) -> None:
    patch_project(project_slug, setup={step: timestamp()})


def refresh_generated_ssh_config() -> None:
    generated_config = get_ssh_config()
    if "# Automatically generated" not in generated_config:
        raise RuntimeError("Management returned SSH config content without the generated-file warning header.")
    write_text_file(SSH_CONFIG_PATH, generated_config)


def build_ssh_connection(project: ProjectRecord) -> Connection:
    identity_file = project.ssh.identity_file
    if not identity_file:
        raise RuntimeError(f"Project '{project.slug}' does not have SSH identity metadata yet.")

    private_key = Path(identity_file)
    if not private_key.is_absolute():
        private_key = WORKSPACES_DIR.parent / private_key

    return Connection(
        host=project.ssh.host or DEFAULT_HOST,
        user=project.ssh.user or project.slug,
        port=project.ssh.port or DEFAULT_SSH_PORT,
        connect_kwargs={"key_filename": [str(private_key)]},
    )


def require_setup_steps(project: ProjectRecord, steps: tuple[str, ...]) -> None:
    missing_steps = [step for step in steps if step not in project.setup]
    if missing_steps:
        raise RuntimeError(
            f"Project '{project.slug}' is missing required setup steps: {', '.join(sorted(missing_steps))}"
        )
