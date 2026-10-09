# v1.12.0: field rules for the extra fields of any screen (form designer)
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("Chatter", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="fielddef",
            name="rules",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
