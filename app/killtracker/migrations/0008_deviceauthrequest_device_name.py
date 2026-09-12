# Adds the client-reported hostname to the device-login record, used as the API key label.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("killtracker", "0007_deviceauthrequest"),
    ]

    operations = [
        migrations.AddField(
            model_name="deviceauthrequest",
            name="device_name",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
    ]
