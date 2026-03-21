from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from credentials.models import Credential, CredentialStore
from credentials.tests.base import PassTestCase


class CredentialAdminTests(PassTestCase, TestCase):

    fixtures = ["test_credential_store.json"]

    @classmethod
    def setUpTestData(cls) -> None:
        super().setUpTestData()
        cls.admin_user = get_user_model().objects.create_superuser(
            username="credentials-admin",
            email="credentials-admin@example.com",
            password="qwerty",
        )

    def setUp(self) -> None:
        super().setUp()
        self.store = CredentialStore.objects.get(slug="test-store")
        self.client.force_login(self.admin_user)

    def test_credential_change_page_renders_decrypted_fields(self) -> None:
        credential = Credential.objects.get(path="ai/openai")

        response = self.client.get(reverse("admin:credentials_credential_change", args=(credential.pk,)))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "openai-secret")
        self.assertContains(response, "https://platform.openai.com/")
        self.assertContains(response, "Production account")
