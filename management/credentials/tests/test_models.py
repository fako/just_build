from datetime import datetime, timezone
from unittest import mock

from django.test import TestCase

from credentials.models import Credential, CredentialStoreError
from credentials.models import CredentialStore
from credentials.tests.base import PassTestCase


class CredentialStoreModelTests(PassTestCase, TestCase):
    fixtures = ["test_credential_store.json"]

    def setUp(self) -> None:
        super().setUp()
        self.store = CredentialStore.objects.get(slug="test-store")

    def test_credential_paths_scans_the_store(self) -> None:
        self.assertEqual(
            self.store.credential_paths(),
            [
                "ai/new/latest/openai",
                "ai/new/openai",
                "ai/openai",
                "bad/empty",
                "bad/malformed",
                "ops/anthropic",
            ],
        )

    def test_get_credential_parses_value_headers_and_notes(self) -> None:
        parsed = self.store.get_credential("ai/openai")

        self.assertEqual(parsed.value, "openai-secret")
        self.assertEqual(parsed.headers["Location"], "https://platform.openai.com/")
        self.assertEqual(parsed.headers["Account"], "api")
        self.assertEqual(parsed.notes, "Production account\nRotate monthly.")

    def test_command_environment_uses_explicit_store_settings(self) -> None:
        environment = self.store.pass_command_environment()

        self.assertEqual(environment["PASSWORD_STORE_DIR"], str(self.password_store_dir))
        self.assertEqual(environment["PASSWORD_STORE_KEY"], self.store.store_key)
        self.assertEqual(environment["PASSWORD_STORE_GPG_OPTS"], "--trust-model always")
        self.assertEqual(environment["GNUPGHOME"], self._test_env()["GNUPGHOME"])

    def test_get_credential_raises_for_malformed_headers(self) -> None:
        with self.assertRaisesRegex(CredentialStoreError, "Malformed header line"):
            self.store.get_credential("bad/malformed")

    def test_get_credential_raises_for_empty_entries(self) -> None:
        with self.assertRaisesRegex(CredentialStoreError, "Credential entry is empty"):
            self.store.get_credential("bad/empty")

    def test_credential_caches_secret_per_instance(self) -> None:
        credential = Credential.objects.get(path="ai/openai")

        with mock.patch.object(credential.store, "get_credential", wraps=credential.store.get_credential) as mocked_get:
            self.assertEqual(credential.value, "openai-secret")
            self.assertEqual(credential.headers["Account"], "api")
            self.assertEqual(credential.notes, "Production account\nRotate monthly.")

        self.assertEqual(mocked_get.call_count, 1)

    def test_credential_raises_when_store_entry_is_missing(self) -> None:
        credential = Credential.objects.get(path="missing/credential")

        with self.assertRaises(CredentialStoreError):
            _ = credential.value

    def test_last_updated_reads_git_history(self) -> None:
        self.assertEqual(self.store.last_updated_for("ai/openai"), self.git_dates["ai/openai"])

    def test_sync_adds_removes_and_updates_credentials(self) -> None:
        credential = Credential.objects.get(path="ai/openai")
        credential.last_updated_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
        credential.save(update_fields=("last_updated_at", "modified_at"))

        result = self.store.sync()

        self.assertEqual(result, {"created": 5, "updated": 1, "deleted": 1})
        self.assertEqual(
            list(self.store.credentials.order_by("path").values_list("path", "folder")),
            [
                ("ai/new/latest/openai", "ai"),
                ("ai/new/openai", "ai"),
                ("ai/openai", "ai"),
                ("bad/empty", "bad"),
                ("bad/malformed", "bad"),
                ("ops/anthropic", "ops"),
            ],
        )

        credential.refresh_from_db()
        self.assertEqual(credential.last_updated_at, self.git_dates["ai/openai"])

    def test_sync_keeps_database_timestamp_when_git_is_unavailable(self) -> None:
        credential = Credential.objects.get(path="ai/openai")
        original_timestamp = datetime(2022, 2, 2, tzinfo=timezone.utc)
        credential.last_updated_at = original_timestamp
        credential.save(update_fields=("last_updated_at", "modified_at"))

        with mock.patch.object(self.store, "last_updated_for", return_value=None):
            result = self.store.sync()

        credential.refresh_from_db()
        self.assertEqual(result["updated"], 0)
        self.assertEqual(credential.last_updated_at, original_timestamp)

    def test_sync_excludes_requested_paths_and_subtrees(self) -> None:
        excluded_direct = Credential(store=self.store, path="bad/empty", last_updated_at=None)
        excluded_direct.clean()
        excluded_direct.save()

        excluded_nested = Credential(store=self.store, path="ai/new/latest/openai", last_updated_at=None)
        excluded_nested.clean()
        excluded_nested.save()

        result = self.store.sync(exclude=["bad", "ai/new/latest/openai"])

        self.assertEqual(result, {"created": 2, "updated": 0, "deleted": 3})
        self.assertEqual(
            list(self.store.credentials.order_by("path").values_list("path", flat=True)),
            [
                "ai/new/openai",
                "ai/openai",
                "ops/anthropic",
            ],
        )
