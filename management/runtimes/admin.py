from django import forms
from django.contrib import admin
from django.db import models

from web.fields import PrettyJSONFormField
from runtimes.models import RUNTIME_TYPES, Runtime


class RuntimeForm(forms.ModelForm):
    """
    Builds the type dropdown from the registry.

    The model field carries no choices on purpose, so that registering a runtime type stays a
    code-only change instead of producing a metadata migration.
    """

    class Meta:
        model = Runtime
        fields = "__all__"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["type"] = forms.ChoiceField(
            choices=[(runtime_type, runtime_type.title()) for runtime_type in sorted(RUNTIME_TYPES)],
        )


@admin.register(Runtime)
class RuntimeAdmin(admin.ModelAdmin):
    form = RuntimeForm
    list_display = ("program_name", "workspace", "type", "port", "installed_at", "is_enabled")
    list_filter = ("type", "is_enabled", "workspace")
    search_fields = ("name", "workspace__name", "workspace__module")
    readonly_fields = ("id", "created_at", "modified_at", "program_name", "log_path")
    formfield_overrides = {
        models.JSONField: {"form_class": PrettyJSONFormField},
    }

    @admin.display(description="Program")
    def program_name(self, obj: Runtime) -> str:
        return obj.program_name

    @admin.display(description="Log file")
    def log_path(self, obj: Runtime) -> str:
        return obj.log_path
