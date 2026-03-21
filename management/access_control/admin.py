from django.contrib import admin

from access_control.models import Project


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "slug")
    search_fields = ("name", "slug")
    readonly_fields = ("id",)
    prepopulated_fields = {"slug": ("name",)}
