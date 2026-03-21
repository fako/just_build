import os
from unittest import SkipTest

from django.conf import settings
from credentials.tests.pass_helpers.helpers import TEST_PASS_GIT_DATES, TEST_PASS_ROOT, TEST_PASS_STORE_DIR


class PassTestCase:

    pass_fixture_root = TEST_PASS_ROOT
    password_store_dir = TEST_PASS_STORE_DIR
    git_dates = TEST_PASS_GIT_DATES

    @classmethod
    def setUpClass(cls) -> None:
        if not settings.GPGHOME:
            raise SkipTest("management.credentials.gpghome is not configured.")
        if not cls.password_store_dir.exists():
            raise SkipTest("Pass test store has not been built yet.")
        if not any(cls.password_store_dir.rglob("*.gpg")):
            raise SkipTest("Pass test store is empty. Run management.setup_test_store first.")
        super().setUpClass()

    @classmethod
    def tearDownClass(cls) -> None:
        super().tearDownClass()

    @classmethod
    def _test_env(cls, **extra: str) -> dict[str, str]:
        return {
            **os.environ,
            "GNUPGHOME": settings.GPGHOME,
            "PASSWORD_STORE_DIR": str(cls.password_store_dir),
            "PASSWORD_STORE_GPG_OPTS": "--trust-model always",
            **extra,
        }
