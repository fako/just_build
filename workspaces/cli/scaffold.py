"""
Scaffolding: the greenfield half of getting a project into a workspace.

Its counterpart is workspaces.clone_repo, which brings an existing repository in instead. Both end
in the same place, so both are followed by runtimes.add and runtimes.install.

The two also meet: --over-existing layers the same templates onto a project that is already in the
workspace, cloned or scaffolded earlier, and leaves the result as changes to review rather than as a
commit.
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
from workspaces.cli.repository import (
    assert_clean_worktree,
    assert_git_repo,
    assert_no_git_repo,
    ensure_git_repo,
    ensure_initial_commit,
    list_tracked_files,
)


DEFAULT_TEMPLATES = ("default",)
TEMPLATE_ENV = Environment(autoescape=False, keep_trailing_newline=True, undefined=StrictUndefined)

# Python writes these next to any template module it imports, and they are bytecode for whichever
# interpreter happened to do it. Copying them into a workspace uploads stale binaries as if they were
# source, so they are skipped rather than left to whoever remembers to clean the directory.
IGNORED_TEMPLATE_DIRS = ("__pycache__",)
IGNORED_TEMPLATE_SUFFIXES = (".pyc", ".pyo")


def ensure_django_project(conn, repo_dir: str, runtime_module: str) -> bool:
    result = conn.run(
        f"test -f {quote(repo_dir)}/manage.py -o -d {quote(repo_dir)}/{quote(runtime_module)}",
        hide=True,
        warn=True,
    )
    if result.ok:
        return False

    conn.run(f"cd {quote(repo_dir)} && django-admin startproject {quote(runtime_module)} .", echo=True)
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


def is_ignored_template_path(relative_path: Path) -> bool:
    """Whether a path inside a template is build output rather than something to scaffold."""
    if any(part in IGNORED_TEMPLATE_DIRS for part in relative_path.parts):
        return True
    return relative_path.suffix in IGNORED_TEMPLATE_SUFFIXES


def template_output_path(relative_path: Path) -> Path:
    suffixes = relative_path.suffixes
    if len(suffixes) < 2 or suffixes[-2] != ".tpl":
        return relative_path

    template_suffix = "".join(suffixes[-2:])
    output_name = f"{relative_path.name[:-len(template_suffix)]}{suffixes[-1]}"
    return relative_path.with_name(output_name)


def template_context(workspace: WorkspaceRecord, runtime_module: str) -> dict[str, object]:
    """
    What a template can reach.

    `runtime_module` is the Python package this scaffold creates, and the same value a runtime is
    later added with. It is passed as a plain variable rather than through a runtime object, because
    scaffolding happens before any runtime exists.
    """
    context: dict[str, object] = workspace.model_dump()
    context["workspace"] = workspace
    context["runtime_module"] = runtime_module
    return context


def render_template_file(local_path: Path, workspace: WorkspaceRecord, runtime_module: str) -> str:
    template = TEMPLATE_ENV.from_string(local_path.read_text(encoding="utf-8"))
    return template.render(template_context(workspace, runtime_module))


def put_rendered_template_file(conn, repo_dir: str, local_path: Path, remote_path: Path,
                               workspace: WorkspaceRecord, runtime_module: str) -> None:
    rendered = render_template_file(local_path, workspace, runtime_module)
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


def iter_template_paths(source_dir: Path):
    """Every path a template holds, in the order it is applied, minus whatever is build output."""
    for local_path in sorted(source_dir.rglob("*")):
        relative_path = local_path.relative_to(source_dir)
        if is_ignored_template_path(relative_path):
            continue
        yield local_path, relative_path


def template_file_paths(template_names: tuple[str, ...]) -> tuple[str, ...]:
    """
    The repository-relative paths a run of these templates writes, without duplicates.

    Layering means later templates land on files earlier ones wrote, and those are not overwrites of
    anything that was in the project, so each path is counted once.
    """
    written_paths: dict[str, None] = {}
    for template_name in template_names:
        for local_path, relative_path in iter_template_paths(template_dir(template_name)):
            if not local_path.is_file():
                continue
            written_paths[template_output_path(relative_path).as_posix()] = None

    return tuple(written_paths)


def existing_repository_files(conn, repo_dir: str, relative_paths: tuple[str, ...]) -> tuple[str, ...]:
    """Which of these paths are already in the workspace, asked in one command rather than one each."""
    if not relative_paths:
        return ()

    prefix = f"{repo_dir}/"
    quoted_paths = " ".join(quote(f"{prefix}{relative_path}") for relative_path in relative_paths)
    result = conn.run(
        f'for path in {quoted_paths}; do test -e "$path" && printf "%s\\n" "$path" || true; done',
        hide=True, warn=True,
    )
    return tuple(line.strip().removeprefix(prefix) for line in result.stdout.splitlines() if line.strip())


def report_template_overwrites(conn, repo_dir: str, template_names: tuple[str, ...],
                               workspace_module: str) -> tuple[str, ...]:
    """
    Say which files the templates are about to replace, and refuse the ones git could not give back.

    A tracked file that a template lands on is a diff to read and revert. An untracked one is not:
    there is no earlier copy of it anywhere, so overwriting it is the one thing this command cannot
    offer to undo, and it stops instead.
    """
    overwritten = existing_repository_files(conn, repo_dir, template_file_paths(template_names))
    tracked = set(list_tracked_files(conn, repo_dir, overwritten))

    untracked = tuple(path for path in overwritten if path not in tracked)
    if untracked:
        raise RuntimeError(
            f"Workspace '{workspace_module}' holds files these templates write, which git does not "
            f"track and could not restore:\n\n"
            + "\n".join(f"    {path}" for path in untracked)
            + "\n\nCommit them or move them out of the way, then run this command again."
        )

    if overwritten:
        counted = f"{len(overwritten)} tracked file{'' if len(overwritten) == 1 else 's'}"
        print(f"Writing over {counted}, which the templates replace in full:")
        for path in overwritten:
            print(f"    {path}")
        print("")

    return overwritten


def copy_template_files(conn, repo_dir: str, template_name: str, workspace: WorkspaceRecord,
                        runtime_module: str) -> None:
    for local_path, relative_path in iter_template_paths(template_dir(template_name)):
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
            put_rendered_template_file(conn, repo_dir, local_path, remote_path, workspace, runtime_module)


def copy_workspace_templates(conn, repo_dir: str, templates: list[str] | tuple[str, ...] | str | None,
                             workspace: WorkspaceRecord, runtime_module: str) -> tuple[str, ...]:
    template_names = normalize_template_names(templates)
    for template_name in template_names:
        print(f"Applying workspace template: {template_name}")
        copy_template_files(conn, repo_dir, template_name, workspace, runtime_module)

    return template_names


@task(
    help={
        "workspace_module": "Existing workspace module created by workspaces.create",
        "runtime_module": "Python package the project lives in, and the module runtimes will run. Defaults to web.",
        "templates": "Comma-separated template names to layer in order, defaults to default.",
        "git": "Initialize a git repository and create the initial commit (default: enabled).",
        "over_existing": "Layer templates onto the project already in the workspace, leaving the result uncommitted.",
    },
)
def scaffold(ctx, workspace_module: str, runtime_module: str = "web", templates: str = "default",
             git: bool = True, over_existing: bool = False):
    """Scaffold a new Django project and the workspace templates over SSH as the workspace user."""
    workspace = get_workspace(workspace_module)
    require_setup_steps(workspace, ("workspace_created", "home_created", "secrets_created", "ssh_access"))

    repo_dir = f"/home/{workspace.module}"
    conn = build_ssh_connection(workspace)
    if over_existing:
        # The project there is somebody's work rather than an empty slate, so what this mode offers
        # instead of an untouched home directory is a diff: a repository to revert through, and
        # nothing already pending for the templates to hide among.
        assert_git_repo(conn, repo_dir, workspace.module)
        assert_clean_worktree(conn, repo_dir, workspace.module)
    else:
        # Checked whether or not git is asked for: a repository in the home directory means there is
        # already a project there, and templates would be written over it either way.
        assert_no_git_repo(conn, repo_dir, workspace.module)

    if "database_created" not in workspace.setup:
        ensure_workspace_database(ctx, workspace)
        log_setup_step(workspace.module, "database_created")

    # Both of these belong to starting a project rather than to templating one. Over an existing
    # project the repository is already there, django-admin has nothing to add to a tree that holds
    # its own layout, and a commit is the reviewer's to make once they have read what changed.
    manage_git = git and not over_existing

    if manage_git:
        ensure_git_repo(conn, repo_dir, workspace.name, workspace.module)
        log_setup_step(workspace.module, "git_initialized")

    if not over_existing:
        django_initialized = ensure_django_project(conn, repo_dir, runtime_module)
        if django_initialized:
            log_setup_step(workspace.module, "django_initialized")

    template_names = normalize_template_names(templates)
    if over_existing:
        report_template_overwrites(conn, repo_dir, template_names, workspace.module)

    copy_workspace_templates(conn, repo_dir, template_names, workspace, runtime_module)
    log_setup_step(workspace.module, "templates_resolved")

    if manage_git:
        initial_commit = ensure_initial_commit(conn, repo_dir)
        if initial_commit:
            log_setup_step(workspace.module, "initial_commit")

    print("")
    if over_existing:
        print(f"Applied workspace templates over the project in {workspace.name} ({workspace.module}).")
        print(f"Templates resolved: {', '.join(template_names)}")
        print("Nothing was committed. Next step:")
        print(f"  invoke remote.run --workspace-module={workspace.module} --command='git status'")
        return

    print(f"Scaffolded workspace {workspace.name} ({workspace.module}) over SSH.")
    print(f"Templates resolved: {', '.join(template_names)}")
    print("Next step:")
    print(f"  invoke runtimes.add --workspace-module={workspace.module} --type=django --name=web"
          f" --module={runtime_module}")
