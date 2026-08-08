from __future__ import annotations


class DjangoProjectRuntime:
    """
    Shared by every runtime that boots a Django project: the web server and the workers behind it.

    A plain mixin rather than a proxy model in the hierarchy, because "boots Django" cuts across the
    HTTP split: the web runtime serves requests and the worker does not, but both need the settings
    module. Keeping it here is what stops DJANGO_SETTINGS_MODULE from being something every runtime
    type inherits, including the Node and Laravel ones that have no Django in them at all.

    The settings module comes from the runtime's own module, so one workspace can run two projects.
    """

    def default_environment(self) -> dict[str, str]:
        return {
            **super().default_environment(),
            "DJANGO_SETTINGS_MODULE": f"{self.module}.settings",
        }
