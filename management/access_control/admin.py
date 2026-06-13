from django.contrib import admin
from django.db import models

from web.fields import PrettyJSONFormField
from access_control.models import Workspace


@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = ("name", "slug")
    search_fields = ("name", "slug")
    readonly_fields = ("id",)
    prepopulated_fields = {"slug": ("name",)}
    formfield_overrides = {
        models.JSONField: {"form_class": PrettyJSONFormField},
    }
