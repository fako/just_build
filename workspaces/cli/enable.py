from invoke import task

from workspaces.cli.client import get_project
from workspaces.cli.common import (
    DEFAULT_HOST,
    DEFAULT_PROXY_PORT,
    active_nginx_config_path,
    active_supervisor_config_path,
    docker_exec,
    ensure_workspaces_container,
    log_setup_step,
    parse_project_domain,
    publish_staged_file,
    require_setup_steps,
    staged_nginx_config_path,
    staged_supervisor_config_path,
)


@task(help={"project_slug": "Existing project slug created and initialized earlier"})
def enable(ctx, project_slug: str):
    """Enable staged nginx and supervisor configs for a project."""
    project = get_project(project_slug)
    require_setup_steps(
        project,
        ("project_created", "home_created", "ssh_access", "config_staged", "git_initialized", "django_initialized", "initial_commit"),
    )

    staged_supervisor = staged_supervisor_config_path(project.slug)
    staged_nginx = staged_nginx_config_path(project.slug)
    if not staged_supervisor.exists() or not staged_nginx.exists():
        raise RuntimeError(f"Staged configs are missing for project '{project.slug}'")

    publish_staged_file(staged_supervisor, active_supervisor_config_path(project.slug))
    publish_staged_file(staged_nginx, active_nginx_config_path(project.slug))

    ensure_workspaces_container(ctx)
    docker_exec(ctx, "supervisorctl reread")
    docker_exec(ctx, "supervisorctl update")
    docker_exec(ctx, "nginx -s reload")
    log_setup_step(project.slug, "enabled")

    domain = parse_project_domain(staged_nginx)
    print("")
    print(f"Enabled staged configs for {project.name} ({project.slug}).")
    print(f'Verify with a host header: curl -H "Host: {domain}" http://{DEFAULT_HOST}:{DEFAULT_PROXY_PORT}/')
    print(f"Or open in a browser after hosts setup: http://{domain}:{DEFAULT_PROXY_PORT}/")
