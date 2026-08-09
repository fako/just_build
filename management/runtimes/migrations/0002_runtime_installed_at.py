from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('runtimes', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='runtime',
            name='installed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
