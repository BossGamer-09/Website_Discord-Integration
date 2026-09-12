from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("killtracker", "0014_parserrelease_signature"),
    ]

    operations = [
        migrations.AddField(
            model_name="killevent",
            name="incap",
            field=models.BooleanField(db_index=True, default=False),
        ),
    ]
