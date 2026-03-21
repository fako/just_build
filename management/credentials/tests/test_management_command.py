from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from credentials.models import Credential, CredentialStore
from credentials.tests.base import PassTestCase


class SyncCredentialStoreCommandTests(PassTestCase, TestCase):
    fixtures = ["test_credential_store.json"]

    def setUp(self) -> None:
        super().setUp()
        self.store = CredentialStore.objects.get(slug="test-store")

    def test_command_syncs_store_by_slug(self) -> None:
        output = StringIO()

        call_command("sync_credential_store", "test-store", stdout=output)

        self.assertIn("Synchronized 'test-store': 5 created, 0 updated, 1 deleted.", output.getvalue())

    def test_command_raises_for_unknown_slug(self) -> None:
        with self.assertRaises(CommandError):
            call_command("sync_credential_store", "missing-store")

    def test_command_accepts_excluded_paths(self) -> None:
        output = StringIO()
        Credential.objects.create(
            store=self.store,
            path="bad/empty",
            folder="bad",
            last_updated_at=None,
        )
        Credential.objects.create(
            store=self.store,
            path="ai/new/latest/openai",
            folder="ai",
            last_updated_at=None,
        )

        call_command(
            "sync_credential_store",
            "test-store",
            "--exclude=bad",
            "--exclude=ai/new/latest/openai",
            stdout=output,
        )

        self.assertIn("Synchronized 'test-store': 2 created, 0 updated, 3 deleted.", output.getvalue())
        self.assertIn("Excluded: bad, ai/new/latest/openai.", output.getvalue())
        self.assertEqual(
            list(Credential.objects.order_by("path").values_list("path", flat=True)),
            [
                "ai/new/openai",
                "ai/openai",
                "ops/anthropic",
            ],
        )
