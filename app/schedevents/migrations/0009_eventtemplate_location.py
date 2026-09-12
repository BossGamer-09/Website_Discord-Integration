from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("schedevents", "0008_eventplan_location"),
    ]

    operations = [
        migrations.AddField(
            model_name="eventtemplate",
            name="location",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
    ]
