from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("killtracker", "0017_clientrelease_file_nosound"),
    ]

    operations = [
        migrations.AddField(
            model_name="clientrelease",
            name="sha256",
            field=models.CharField(blank=True, default="", editable=False, max_length=64,
                                   help_text="Auto-computed SHA-256 of the with-sounds exe; the client verifies its download against this."),
        ),
        migrations.AddField(
            model_name="clientrelease",
            name="sha256_nosound",
            field=models.CharField(blank=True, default="", editable=False, max_length=64,
                                   help_text="Auto-computed SHA-256 of the no-sounds exe."),
        ),
        migrations.AddField(
            model_name="historicalclientrelease",
            name="sha256",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="historicalclientrelease",
            name="sha256_nosound",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
    ]
