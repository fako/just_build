from django.contrib import admin
from django.db import models

from web.fields import PrettyJSONFormField
from access_control.models import Workspace


@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = ("name", "module", "slug")
    search_fields = ("name", "module", "slug")
    readonly_fields = ("id",)
    formfield_overrides = {
        models.JSONField: {"form_class": PrettyJSONFormField},
    }
