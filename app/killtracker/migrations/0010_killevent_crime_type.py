from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("killtracker", "0009_parserrelease_historicalparserrelease"),
    ]

    operations = [
        migrations.AddField(
            model_name="killevent",
            name="crime_type",
            field=models.CharField(blank=True, db_index=True, default="", max_length=64),
        ),
    ]
