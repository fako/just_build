from invoke import task

from workspaces.cli.client import create_project, patch_project
from workspaces.cli.common import (
    DEFAULT_HOST,
    DEFAULT_SSH_PORT,
    assert_container_project_absent,
    assert_project_workspace_clean,
    create_container_user_and_home,
    ensure_project_keypair,
    ensure_workspaces_container,
    log_setup_step,
    project_private_key_path,
    publish_authorized_key,
    refresh_generated_ssh_config,
    stage_project_configs,
)
from workspaces.cli.constants import WORKSPACES_DIR


@task(
    help={
        "name": "Human-friendly project name",
        "slug": "Unique project slug and Linux username",
        "domain": "Domain for the staged nginx config, defaults to <slug>.localhost",
        "django_module": "Django project module name, defaults to web",
    }
)
def create(ctx, name: str, slug: str, domain: str | None = None, django_module: str = "web"):
    """Create a workspace project, SSH access, and staged configs."""
    domain = domain or f"{slug}.localhost"

    assert_project_workspace_clean(slug)
    ensure_workspaces_container(ctx)
    assert_container_project_absent(ctx, slug)

    project = create_project(name=name, slug=slug, django_module=django_module)
    log_setup_step(project.slug, "project_created")

    create_container_user_and_home(ctx, project.slug)
    log_setup_step(project.slug, "home_created")

    private_key, public_key = ensure_project_keypair(ctx, project.slug)
    publish_authorized_key(ctx, project.slug, public_key.read_text().strip())

    patch_project(
        project.slug,
        ssh={
            "alias": f"{project.slug}-workspace",
            "user": project.slug,
            "host": DEFAULT_HOST,
            "port": DEFAULT_SSH_PORT,
            "identity_file": str(project_private_key_path(project.slug).relative_to(WORKSPACES_DIR.parent)),
        },
    )
    log_setup_step(project.slug, "ssh_access")

    stage_project_configs(project, domain)
    log_setup_step(project.slug, "config_staged")

    refresh_generated_ssh_config()
    log_setup_step(project.slug, "ssh_config")

    print("")
    print(f"Created workspace project {project.name} ({project.slug}).")
    print(f"SSH private key: {private_key}")
    print("Next step:")
    print(f"  invoke workspaces.init --project-slug={project.slug}")
