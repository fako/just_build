from pathlib import Path
from shlex import quote

from invoke.tasks import task

from workspaces.cli.client import get_workspace
from workspaces.cli.common import build_ssh_connection, log_setup_step, require_setup_steps
from workspaces.cli.constants import TEMPLATES_DIR


DEFAULT_TEMPLATES = ("default",)


def ensure_git_repo(conn, repo_dir: str, workspace_name: str, workspace_slug: str) -> None:
    result = conn.run(f"test -d {quote(repo_dir)}/.git", hide=True, warn=True)
    if not result.ok:
        conn.run(f"git init {quote(repo_dir)}", echo=True)

    conn.run(f"git -C {quote(repo_dir)} config user.name {quote(workspace_name)}", echo=True)
    conn.run(f"git -C {quote(repo_dir)} config user.email {quote(workspace_slug)}@workspace.local", echo=True)


def ensure_django_project(conn, repo_dir: str, django_module: str) -> bool:
    result = conn.run(
        f"test -f {quote(repo_dir)}/manage.py -o -d {quote(repo_dir)}/{quote(django_module)}",
        hide=True,
        warn=True,
    )
    if result.ok:
        return False

    conn.run(f"cd {quote(repo_dir)} && django-admin startproject {quote(django_module)} .", echo=True)
    return True


def normalize_template_names(templates: list[str] | tuple[str, ...] | str | None) -> tuple[str, ...]:
    if templates is None:
        return DEFAULT_TEMPLATES

    raw_templates = [templates] if isinstance(templates, str) else templates
    template_names = []
    for raw_template in raw_templates:
        template_names.extend(template.strip() for template in raw_template.split(",") if template.strip())

    return tuple(template_names) or DEFAULT_TEMPLATES


def template_dir(template_name: str) -> Path:
    if template_name in {".", ".."} or Path(template_name).name != template_name:
        raise RuntimeError(f"Template names must be direct children of {TEMPLATES_DIR}: {template_name}")

    path = TEMPLATES_DIR / template_name
    if not path.is_dir():
        raise RuntimeError(f"Workspace template '{template_name}' does not exist at {path}")

    return path


def remote_template_path(repo_dir: str, relative_path: Path) -> str:
    if relative_path.parent == Path("."):
        return f"{repo_dir}/{relative_path.name}"

    return f"{repo_dir}/{relative_path.as_posix()}"


def copy_template_files(conn, repo_dir: str, template_name: str) -> None:
    source_dir = template_dir(template_name)
    files = sorted(path for path in source_dir.rglob("*") if path.is_file())
    remote_dirs = sorted({path.relative_to(source_dir).parent for path in files})

    for relative_dir in remote_dirs:
        remote_dir = repo_dir if relative_dir == Path(".") else f"{repo_dir}/{relative_dir.as_posix()}"
        conn.run(f"mkdir -p {quote(remote_dir)}", echo=True)

    for local_path in files:
        relative_path = local_path.relative_to(source_dir)
        conn.put(str(local_path), remote=remote_template_path(repo_dir, relative_path))


def copy_workspace_templates(conn, repo_dir: str, templates: list[str] | tuple[str, ...] | str | None) -> tuple[str, ...]:
    template_names = normalize_template_names(templates)
    for template_name in template_names:
        print(f"Applying workspace template: {template_name}")
        copy_template_files(conn, repo_dir, template_name)

    return template_names


def ensure_initial_commit(conn, repo_dir: str) -> bool:
    result = conn.run(f"git -C {quote(repo_dir)} rev-parse --verify HEAD", hide=True, warn=True)
    if result.ok:
        return False

    status = conn.run(f"git -C {quote(repo_dir)} status --porcelain", hide=True, warn=True)
    if not status.stdout.strip():
        return False

    conn.run(f"git -C {quote(repo_dir)} add .", echo=True)
    conn.run(f'git -C {quote(repo_dir)} commit -m "Initialize Django project."', echo=True)
    return True


@task(
    help={
        "workspace_slug": "Existing workspace slug created by workspaces.create",
        "templates": "Comma-separated template names to layer in order, defaults to default.",
    },
)
def init(ctx, workspace_slug: str, templates: str = "default"):
    """Initialize git and Django over SSH as the workspace user."""
    workspace = get_workspace(workspace_slug)
    require_setup_steps(workspace, ("workspace_created", "home_created", "ssh_access"))

    repo_dir = f"/home/{workspace.slug}"
    conn = build_ssh_connection(workspace)

    ensure_git_repo(conn, repo_dir, workspace.name, workspace.slug)
    log_setup_step(workspace.slug, "git_initialized")

    django_initialized = ensure_django_project(conn, repo_dir, workspace.django_module)
    if django_initialized:
        log_setup_step(workspace.slug, "django_initialized")

    template_names = copy_workspace_templates(conn, repo_dir, templates)
    log_setup_step(workspace.slug, "templates_resolved")

    initial_commit = ensure_initial_commit(conn, repo_dir)
    if initial_commit:
        log_setup_step(workspace.slug, "initial_commit")

    print("")
    print(f"Initialized workspace {workspace.name} ({workspace.slug}) over SSH.")
    print(f"Templates resolved: {', '.join(template_names)}")
    print("Next step:")
    print(f"  invoke workspaces.enable --workspace-slug={workspace.slug}")
