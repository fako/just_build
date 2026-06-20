import uuid

from django.db import models
from django.utils.text import slugify


class Workspace(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    module = models.CharField(max_length=255, unique=True)
    slug = models.SlugField(max_length=255, unique=True, editable=False)
    django_module = models.CharField(max_length=255, default="web")
    setup = models.JSONField(default=dict, blank=True)
    ssh = models.JSONField(default=dict, blank=True)

    def save(self, *args, **kwargs) -> None:
        self.slug = slugify(self.module.replace("_", "-"))
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "module" in update_fields:
            kwargs["update_fields"] = {*update_fields, "slug"}
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return str(self.name)
