"""
Scaffolding: the greenfield half of getting a project into a workspace.

Its counterpart is workspaces.clone_repo, which brings an existing repository in instead. Both end
in the same place, so both are followed by runtimes.add and runtimes.install.
"""
from pathlib import Path
from shlex import quote
from tempfile import NamedTemporaryFile

from invoke.tasks import task
from jinja2 import Environment, StrictUndefined

from workspaces.cli.client import WorkspaceRecord, get_workspace
from workspaces.cli.common import (
    build_ssh_connection,
    ensure_workspace_database,
    log_setup_step,
    require_setup_steps,
)
from workspaces.cli.constants import TEMPLATES_DIR
from workspaces.cli.repository import assert_no_git_repo, ensure_git_repo, ensure_initial_commit


DEFAULT_TEMPLATES = ("default",)
TEMPLATE_ENV = Environment(autoescape=False, keep_trailing_newline=True, undefined=StrictUndefined)


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


def ensure_remote_template_dir(conn, repo_dir: str, relative_dir: Path) -> None:
    if relative_dir == Path("."):
        return

    remote_dir = f"{repo_dir}/{relative_dir.as_posix()}"
    conn.run(f"test -d {quote(remote_dir)} || mkdir -p {quote(remote_dir)}", echo=True)


def template_output_path(relative_path: Path) -> Path:
    suffixes = relative_path.suffixes
    if len(suffixes) < 2 or suffixes[-2] != ".tpl":
        return relative_path

    template_suffix = "".join(suffixes[-2:])
    output_name = f"{relative_path.name[:-len(template_suffix)]}{suffixes[-1]}"
    return relative_path.with_name(output_name)


def template_context(workspace: WorkspaceRecord) -> dict[str, object]:
    context: dict[str, object] = workspace.model_dump()
    context["workspace"] = workspace
    return context


def render_template_file(local_path: Path, workspace: WorkspaceRecord) -> str:
    template = TEMPLATE_ENV.from_string(local_path.read_text(encoding="utf-8"))
    return template.render(template_context(workspace))


def put_rendered_template_file(conn, repo_dir: str, local_path: Path, remote_path: Path,
                               workspace: WorkspaceRecord) -> None:
    rendered = render_template_file(local_path, workspace)
    temp_path: Path | None = None
    try:
        with NamedTemporaryFile("w", encoding="utf-8", delete=False) as temp_file:
            temp_file.write(rendered)
            rendered_path = Path(temp_file.name)

        temp_path = rendered_path
        rendered_path.chmod(local_path.stat().st_mode & 0o777)
        conn.put(str(rendered_path), remote=remote_template_path(repo_dir, remote_path))
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def copy_template_files(conn, repo_dir: str, template_name: str, workspace: WorkspaceRecord) -> None:
    source_dir = template_dir(template_name)

    for local_path in sorted(source_dir.rglob("*")):
        relative_path = local_path.relative_to(source_dir)
        if local_path.is_dir():
            ensure_remote_template_dir(conn, repo_dir, relative_path)
            continue

        if not local_path.is_file():
            continue

        remote_path = template_output_path(relative_path)
        ensure_remote_template_dir(conn, repo_dir, remote_path.parent)
        if remote_path == relative_path:
            conn.put(str(local_path), remote=remote_template_path(repo_dir, remote_path))
        else:
            put_rendered_template_file(conn, repo_dir, local_path, remote_path, workspace)


def copy_workspace_templates(conn, repo_dir: str, templates: list[str] | tuple[str, ...] | str | None,
                             workspace: WorkspaceRecord) -> tuple[str, ...]:
    template_names = normalize_template_names(templates)
    for template_name in template_names:
        print(f"Applying workspace template: {template_name}")
        copy_template_files(conn, repo_dir, template_name, workspace)

    return template_names


@task(
    help={
        "workspace_module": "Existing workspace module created by workspaces.create",
        "templates": "Comma-separated template names to layer in order, defaults to default.",
        "git": "Initialize a git repository and create the initial commit (default: enabled).",
    },
)
def scaffold(ctx, workspace_module: str, templates: str = "default", git: bool = True):
    """Scaffold a new Django project and the workspace templates over SSH as the workspace user."""
    workspace = get_workspace(workspace_module)
    require_setup_steps(workspace, ("workspace_created", "home_created", "secrets_created", "ssh_access"))

    repo_dir = f"/home/{workspace.module}"
    conn = build_ssh_connection(workspace)
    # Checked whether or not git is asked for: a repository in the home directory means there is
    # already a project there, and templates would be written over it either way.
    assert_no_git_repo(conn, repo_dir, workspace.module)

    if "database_created" not in workspace.setup:
        ensure_workspace_database(ctx, workspace)
        log_setup_step(workspace.module, "database_created")

    if git:
        ensure_git_repo(conn, repo_dir, workspace.name, workspace.module)
        log_setup_step(workspace.module, "git_initialized")

    django_initialized = ensure_django_project(conn, repo_dir, workspace.django_module)
    if django_initialized:
        log_setup_step(workspace.module, "django_initialized")

    template_names = copy_workspace_templates(conn, repo_dir, templates, workspace)
    log_setup_step(workspace.module, "templates_resolved")

    if git:
        initial_commit = ensure_initial_commit(conn, repo_dir)
        if initial_commit:
            log_setup_step(workspace.module, "initial_commit")

    print("")
    print(f"Scaffolded workspace {workspace.name} ({workspace.module}) over SSH.")
    print(f"Templates resolved: {', '.join(template_names)}")
    print("Next step:")
    print(f"  invoke runtimes.add --workspace-module={workspace.module} --type=django --name=web")
