"""
The workflow mirror and the tag registry that keeps workspaces out of each other's n8n.

Every workspace writes into the same n8n project, so something else has to separate them. That
something is the tag. A tag name is unique across the whole installation and belongs to exactly one
workspace, which is enforced here by a database constraint rather than by application code, because
n8n's own tag namespace is flat and global and management's has to match it exactly. Two workspaces
allowed to own `invoices` would be two workspaces reading each other's workflows.

Workflow rows are a mirror of n8n, not a second source of truth. Nothing about workflow state lives
here: whether a workflow is active, published, or on which version is n8n's business, and copying any
of it into this table would create a second answer to a question that already has one.
"""
from __future__ import annotations

import uuid

from django.db import models
from django.db.models import Q
from django.utils.text import slugify


class WorkflowTag(models.Model):
    """A tag name reserved by one workspace, in management and in n8n."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        "access_control.Workspace", related_name="workflow_tags", on_delete=models.CASCADE,
    )
    # Unique on its own rather than per workspace: this is the reservation. A SlugField rather than a
    # CharField because n8n splits its ?tags= filter on commas, so a name holding one could never be
    # queried back out again.
    name = models.SlugField(max_length=64, unique=True)
    # Empty until the tag has been reconciled with n8n, which is the first time it is pushed through.
    n8n_id = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    modified_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(
                fields=["n8n_id"], condition=~Q(n8n_id=""), name="workflow_tag_unique_n8n_id",
            ),
        ]

    def __str__(self) -> str:
        return str(self.name)


class WorkflowQuerySet(models.QuerySet):

    def for_tag(self, name: str) -> "WorkflowQuerySet":
        return self.filter(tags__name=name)


class Workflow(models.Model):
    """One n8n workflow, as management last saw it."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        "access_control.Workspace", related_name="workflows", on_delete=models.CASCADE,
    )
    # n8n owns the name. A pull writes whatever it finds there over whatever is here.
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, editable=False)
    # Empty only for a row created by hand in the admin before it has ever reached n8n.
    n8n_id = models.CharField(max_length=64, blank=True, default="")
    definition = models.JSONField(default=dict, blank=True)
    tags = models.ManyToManyField(WorkflowTag, related_name="workflows", blank=True)
    pulled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    modified_at = models.DateTimeField(auto_now=True)

    objects = WorkflowQuerySet.as_manager()

    class Meta:
        ordering = ("workspace__module", "name")
        constraints = [
            models.UniqueConstraint(
                fields=["n8n_id"], condition=~Q(n8n_id=""), name="workflow_unique_n8n_id",
            ),
            models.UniqueConstraint(
                fields=["workspace", "slug"], name="workflow_unique_workspace_slug",
            ),
        ]

    def __str__(self) -> str:
        return str(self.name)

    def build_slug(self) -> str:
        """
        A slug unique within the workspace, suffixed when n8n holds two names that slugify alike.

        n8n puts no uniqueness on workflow names at all, so this cannot be left to the constraint:
        a second workflow called 'Report!' would otherwise fail a pull rather than land beside the
        first one.
        """
        base = slugify(self.name) or "workflow"
        taken = Workflow.objects.filter(workspace=self.workspace).exclude(pk=self.pk)
        candidate = base
        suffix = 2
        while taken.filter(slug=candidate).exists():
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    def save(self, *args, **kwargs) -> None:
        self.slug = self.build_slug()
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "name" in update_fields:
            kwargs["update_fields"] = {*update_fields, "slug"}
        super().save(*args, **kwargs)
