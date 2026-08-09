from pathlib import Path

from invoke.collection import Collection
from invoke.context import Context
from invoke.tasks import task

from management.credentials.tests.pass_helpers.helpers import setup_test_store


@task(name="test", help={
    "test_file": "A path to a file containing a subset of tests to run",
    "test_method": "An expression of which test methods to run",
    "warnings": "Whether to print warnings in the test report",
    "fail_fast": "Fails at first failing test when enabled",
    "debug": "Whether to allow and stop at debugger statements"
})
def management_test(ctx: Context, test_file: str | None = None, test_method: str | None = None,
                    warnings: bool = False, fail_fast: bool = False, debug: bool = False) -> None:
    """
    Runs the tests for web module
    """
    # Specify some flags we'll be passing on to pytest based on command line arguments
    test_file = test_file if test_file else ""
    test_method_flag = f"-x -k {test_method}" if test_method else ""
    warnings_flag = "--disable-warnings" if not warnings else ""
    fail_fast_flag = "" if not fail_fast else "-x"
    debug_flag = "" if not debug else "-s"

    # Assert that inputs make sense
    assert not test_method or test_file, "Can't specify a test method without specifying the test file"

    # Run pytest command
    with ctx.cd("management"):
        ctx.run(
            # "INVOKE_DATABASE_USER=postgres INVOKE_DATABASE_PASSWORD=qwerty "
            f"pytest {test_file} {test_method_flag} {warnings_flag} {fail_fast_flag} {debug_flag} "
            f"--ds=web.settings --import-mode=importlib",
            echo=True, pty=True
        )


@task(name="setup_test_store")
def setup_test_store_task(ctx: Context) -> None:
    """
    Recreates the fixed pass test store for debugging and tests.
    """
    configured_gpghome = ctx.config.management.credentials.gpghome
    assert configured_gpghome, "Set management.credentials.gpghome via activate.sh or invoke config first"

    gpghome = Path(str(configured_gpghome)).expanduser()

    store_dir = setup_test_store(gpghome)
    print(f"Test pass store ready at {store_dir}")
    print(f"Test GPG home ready at {gpghome}")


namespace = Collection(
    "management",
    setup_test_store_task,
    management_test,
)
