import hashlib
import uuid

from django.db import models
from django.utils import timezone
from django.utils.text import slugify


WORKSPACE_KEY_SCHEME = "workspace"
CONTROL_KEY_SCHEME = "control"


def generate_api_key(scheme: str) -> str:
    """Build a scheme-prefixed API key such as 'workspace:<uuid>'."""
    return f"{scheme}:{uuid.uuid4()}"


def hash_api_key(token: str) -> str:
    """Hash a complete API key, including its scheme prefix."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class Workspace(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    module = models.CharField(max_length=255, unique=True)
    slug = models.SlugField(max_length=255, unique=True, editable=False)
    django_module = models.CharField(max_length=255, default="web")
    setup = models.JSONField(default=dict, blank=True)
    ssh = models.JSONField(default=dict, blank=True)
    # The public half of the keypair the workspace itself owns, generated inside its home by
    # workspaces.create. It is what a workspace reaches git remotes with, so this is the key a human
    # copies out of the admin and adds to a repository as a deploy key. The private half never
    # leaves the workspace, and the inbound keypair in workspace.ssh is a different key entirely.
    git_public_key = models.TextField(blank=True, default="")
    # Only the hash is stored. The plaintext key is returned once, when it is created or rotated, and
    # lives on from there in the workspace's own .env file.
    api_key_hash = models.CharField(max_length=64, unique=True, null=True, blank=True, editable=False)
    api_key_created_at = models.DateTimeField(null=True, blank=True, editable=False)

    def issue_api_key(self) -> str:
        """Generate a fresh API key, store its hash, and return the plaintext once."""
        api_key = generate_api_key(WORKSPACE_KEY_SCHEME)
        self.api_key_hash = hash_api_key(api_key)
        self.api_key_created_at = timezone.now()
        # The primary key has a default, so pk is set well before the row exists. Only _state.adding
        # distinguishes a workspace that is being created from one that is being rotated.
        if not self._state.adding:
            self.save(update_fields=["api_key_hash", "api_key_created_at"])
        return api_key

    def save(self, *args, **kwargs) -> None:
        self.slug = slugify(self.module.replace("_", "-"))
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "module" in update_fields:
            kwargs["update_fields"] = {*update_fields, "slug"}
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return str(self.name)
