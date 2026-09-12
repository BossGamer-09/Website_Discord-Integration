from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("killtracker", "0010_killevent_crime_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="killevent",
            name="victim_location",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
    ]
