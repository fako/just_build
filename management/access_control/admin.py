from django.contrib import admin
from django.db import models

from web.fields import PrettyJSONFormField
from access_control.models import Workspace


@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = ("name", "module", "slug", "has_api_key", "has_git_public_key")
    search_fields = ("name", "module", "slug")
    readonly_fields = ("id", "api_key_created_at", "git_public_key")
    exclude = ("api_key_hash",)
    formfield_overrides = {
        models.JSONField: {"form_class": PrettyJSONFormField},
    }

    @admin.display(description="API key", boolean=True)
    def has_api_key(self, obj: Workspace) -> bool:
        return bool(obj.api_key_hash)

    @admin.display(description="Git key", boolean=True)
    def has_git_public_key(self, obj: Workspace) -> bool:
        return bool(obj.git_public_key)
