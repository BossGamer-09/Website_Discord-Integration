from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("schedevents", "0007_rsvp_emojis"),
    ]

    operations = [
        migrations.AddField(
            model_name="eventplan",
            name="location",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Where the event takes place (shown in Discord scheduled event).",
                max_length=200,
            ),
        ),
    ]
