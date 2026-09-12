from django.db import migrations, models


_HELP = (
    "The 'BlightVeil KillTracker (no sounds).exe'. Clients running the no-sounds build get "
    "this; falls back to the with-sounds file if left empty."
)


class Migration(migrations.Migration):

    dependencies = [
        ("killtracker", "0016_clientrelease"),
    ]

    operations = [
        migrations.AddField(
            model_name="clientrelease",
            name="file_nosound",
            field=models.FileField(blank=True, help_text=_HELP, upload_to="killtracker/releases/"),
        ),
        migrations.AddField(
            model_name="historicalclientrelease",
            name="file_nosound",
            field=models.TextField(blank=True, help_text=_HELP, max_length=100),
        ),
        # The with-sounds help text was also refined on the model; keep DB state in sync.
        migrations.AlterField(
            model_name="clientrelease",
            name="file",
            field=models.FileField(
                help_text="The 'BlightVeil KillTracker.exe' (WITH sounds). Clients running the with-sounds build get this.",
                upload_to="killtracker/releases/",
            ),
        ),
        migrations.AlterField(
            model_name="historicalclientrelease",
            name="file",
            field=models.TextField(
                help_text="The 'BlightVeil KillTracker.exe' (WITH sounds). Clients running the with-sounds build get this.",
                max_length=100,
            ),
        ),
    ]
