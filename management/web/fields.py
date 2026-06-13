import json

from django import forms
from django.contrib.admin.widgets import AdminTextareaWidget
from django.forms.fields import InvalidJSONInput


class PrettyJSONFormField(forms.JSONField):

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", AdminTextareaWidget(attrs={"rows": 12}))
        super().__init__(*args, **kwargs)

    def prepare_value(self, value):
        if isinstance(value, InvalidJSONInput):
            return value
        return json.dumps(value, indent=4, sort_keys=True, cls=self.encoder)
