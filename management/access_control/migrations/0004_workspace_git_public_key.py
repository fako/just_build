from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('access_control', '0003_workspace_api_key_created_at_workspace_api_key_hash'),
    ]

    operations = [
        migrations.AddField(
            model_name='workspace',
            name='git_public_key',
            field=models.TextField(blank=True, default=''),
        ),
    ]
