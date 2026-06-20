from django.db import migrations, models


def populate_modules(apps, schema_editor):
    Workspace = apps.get_model("access_control", "Workspace")
    for workspace in Workspace.objects.all():
        workspace.module = workspace.slug.replace("-", "_")
        workspace.save(update_fields=["module"])


class Migration(migrations.Migration):

    dependencies = [
        ("access_control", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="workspace",
            name="module",
            field=models.CharField(max_length=255, null=True),
        ),
        migrations.RunPython(populate_modules, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="workspace",
            name="module",
            field=models.CharField(max_length=255, unique=True),
        ),
        migrations.AlterField(
            model_name="workspace",
            name="slug",
            field=models.SlugField(editable=False, max_length=255, unique=True),
        ),
    ]
