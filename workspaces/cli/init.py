from shlex import quote

from invoke import task

from workspaces.cli.client import get_project
from workspaces.cli.common import build_ssh_connection, log_setup_step, require_setup_steps


def ensure_git_repo(conn, repo_dir: str, project_name: str, project_slug: str) -> None:
    result = conn.run(f"test -d {quote(repo_dir)}/.git", hide=True, warn=True)
    if not result.ok:
        conn.run(f"git init {quote(repo_dir)}", echo=True)

    conn.run(f"git -C {quote(repo_dir)} config user.name {quote(project_name)}", echo=True)
    conn.run(f"git -C {quote(repo_dir)} config user.email {quote(project_slug)}@workspace.local", echo=True)


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


@task(help={"project_slug": "Existing project slug created by workspaces.create"})
def init(ctx, project_slug: str):
    """Initialize git and Django over SSH as the project user."""
    project = get_project(project_slug)
    require_setup_steps(project, ("project_created", "home_created", "ssh_access"))

    repo_dir = f"/home/{project.slug}"
    conn = build_ssh_connection(project)

    ensure_git_repo(conn, repo_dir, project.name, project.slug)
    log_setup_step(project.slug, "git_initialized")

    django_initialized = ensure_django_project(conn, repo_dir, project.django_module)
    if django_initialized:
        log_setup_step(project.slug, "django_initialized")

    initial_commit = ensure_initial_commit(conn, repo_dir)
    if initial_commit:
        log_setup_step(project.slug, "initial_commit")

    print("")
    print(f"Initialized project {project.name} ({project.slug}) over SSH.")
    print("Next step:")
    print(f"  invoke workspaces.enable --project-slug={project.slug}")
