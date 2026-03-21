from django.contrib import admin
from django.utils.html import format_html

from credentials.models import Credential, CredentialStore, CredentialStoreError


@admin.register(CredentialStore)
class CredentialStoreAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "repository_path", "store_key", "credential_count")
    search_fields = ("name", "slug", "repository_path", "store_key")
    readonly_fields = ("id", "created_at", "modified_at",)
    prepopulated_fields = {"slug": ("name",)}
    fields = ("id", "name", "slug", "repository_path", "store_key", "created_at", "modified_at",)

    @admin.display(description="Credentials")
    def credential_count(self, obj: CredentialStore) -> int:
        return obj.credentials.count()


@admin.register(Credential)
class CredentialAdmin(admin.ModelAdmin):
    list_display = ("path", "store", "folder_name", "section_name", "last_updated_at")
    list_filter = ("store", "folder")
    search_fields = ("path", "store__name", "store__slug")
    readonly_fields = (
        "id",
        "created_at",
        "modified_at",
        "last_updated_at",
        "folder_name",
        "section_name",
        "label_name",
        "value_display",
        "headers_display",
        "notes_display",
    )
    fields = (
        "id",
        "store",
        "path",
        "created_at",
        "modified_at",
        "last_updated_at",
        "folder_name",
        "section_name",
        "label_name",
        "value_display",
        "headers_display",
        "notes_display",
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("store")

    @admin.display(description="Folder")
    def folder_name(self, obj: Credential) -> str:
        return obj.folder if obj.folder != "root" else "Root"

    @admin.display(description="Section")
    def section_name(self, obj: Credential) -> str:
        return obj.section or "(none)"

    @admin.display(description="Credential")
    def label_name(self, obj: Credential) -> str:
        return obj.label

    @admin.display(description="Value")
    def value_display(self, obj: Credential) -> str:
        try:
            return format_html("<pre>{}</pre>", obj.value)
        except CredentialStoreError as exc:
            return f"Unavailable: {exc}"

    @admin.display(description="Headers")
    def headers_display(self, obj: Credential) -> str:
        try:
            return format_html("<pre>{}</pre>", obj.headers_json)
        except CredentialStoreError as exc:
            return f"Unavailable: {exc}"

    @admin.display(description="Notes")
    def notes_display(self, obj: Credential) -> str:
        try:
            return format_html("<pre>{}</pre>", obj.notes or "(empty)")
        except CredentialStoreError as exc:
            return f"Unavailable: {exc}"
