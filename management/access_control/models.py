import uuid

from django.db import models


class Workspace(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    django_module = models.CharField(max_length=255, default="web")
    setup = models.JSONField(default=dict, blank=True)
    ssh = models.JSONField(default=dict, blank=True)

    def __str__(self) -> str:
        return str(self.name)
