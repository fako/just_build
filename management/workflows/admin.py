from django.contrib import admin
from django.db import models

from web.fields import PrettyJSONFormField
from workflows.models import Workflow, WorkflowTag


@admin.register(WorkflowTag)
class WorkflowTagAdmin(admin.ModelAdmin):
    """
    Where a tag is transferred between workspaces or released.

    Deliberately the only place: transferring a tag hands one workspace another's workflows, so it is
    a human decision rather than something an API route offers.
    """

    list_display = ("name", "workspace", "n8n_id", "workflow_count", "modified_at")
    list_filter = ("workspace",)
    search_fields = ("name", "workspace__name", "workspace__module")
    readonly_fields = ("id", "created_at", "modified_at")

    @admin.display(description="Workflows")
    def workflow_count(self, obj: WorkflowTag) -> int:
        return obj.workflows.count()


@admin.register(Workflow)
class WorkflowAdmin(admin.ModelAdmin):
    list_display = ("name", "workspace", "n8n_id", "pulled_at", "modified_at")
    list_filter = ("workspace", "tags")
    search_fields = ("name", "slug", "n8n_id", "workspace__name", "workspace__module")
    readonly_fields = ("id", "slug", "created_at", "modified_at", "pulled_at")
    filter_horizontal = ("tags",)
    formfield_overrides = {
        models.JSONField: {"form_class": PrettyJSONFormField},
    }
