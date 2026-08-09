from invoke.context import Context
from invoke.tasks import task


@task(name="test", help={
    "test_file": "A path to a file containing a subset of tests to run",
    "test_method": "An expression of which test methods to run",
    "fail_fast": "Fails at first failing test when enabled",
})
def workspaces_test(ctx: Context, test_file: str | None = None, test_method: str | None = None,
                    fail_fast: bool = False) -> None:
    """
    Runs the tests for the workspaces CLI.

    These are plain pytest tests with no database behind them, so the Django plugin that the
    management suite needs is switched off and the repository root goes on the path instead.
    """
    test_file = test_file or "workspaces/cli/tests"
    test_method_flag = f"-x -k {test_method}" if test_method else ""
    fail_fast_flag = "-x" if fail_fast else ""

    assert not test_method or test_file, "Can't specify a test method without specifying the test file"

    ctx.run(
        f"PYTHONPATH=. pytest -p no:django {test_file} {test_method_flag} {fail_fast_flag} --disable-warnings",
        echo=True, pty=True,
    )
