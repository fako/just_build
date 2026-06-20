from invoke.tasks import task

from workspaces.cli.client import get_workspace
from workspaces.cli.common import (
    DEFAULT_HOST,
    DEFAULT_PROXY_PORT,
    active_nginx_config_path,
    active_supervisor_config_path,
    docker_exec,
    ensure_workspaces_container,
    log_setup_step,
    parse_workspace_domain,
    publish_staged_file,
    require_setup_steps,
    staged_nginx_config_path,
    staged_supervisor_config_path,
)


@task(help={"workspace_module": "Existing workspace module created and initialized earlier"})
def enable(ctx, workspace_module: str):
    """Enable staged nginx and supervisor configs for a workspace."""
    workspace = get_workspace(workspace_module)
    require_setup_steps(
        workspace,
        (
            "workspace_created",
            "home_created",
            "ssh_access",
            "config_staged",
            "git_initialized",
            "django_initialized",
            "initial_commit",
            "dependencies_updated",
        ),
    )

    staged_supervisor = staged_supervisor_config_path(workspace.module)
    staged_nginx = staged_nginx_config_path(workspace.module)
    if not staged_supervisor.exists() or not staged_nginx.exists():
        raise RuntimeError(f"Staged configs are missing for workspace '{workspace.module}'")

    publish_staged_file(staged_supervisor, active_supervisor_config_path(workspace.module))
    publish_staged_file(staged_nginx, active_nginx_config_path(workspace.module))

    ensure_workspaces_container(ctx)
    docker_exec(ctx, "supervisorctl reread")
    docker_exec(ctx, "supervisorctl update")
    docker_exec(ctx, "nginx -s reload")
    log_setup_step(workspace.module, "enabled")

    domain = parse_workspace_domain(staged_nginx)
    print("")
    print(f"Enabled staged configs for {workspace.name} ({workspace.module}).")
    print(f'Verify with a host header: curl -H "Host: {domain}" http://{DEFAULT_HOST}:{DEFAULT_PROXY_PORT}/')
    print(f"Or open in a browser after hosts setup: http://{domain}:{DEFAULT_PROXY_PORT}/")
