# Registers the killtracker.use_tracker permission (gate for the desktop client).
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("killtracker", "0005_apikey_hash"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="killevent",
            options={
                "default_permissions": (),
                "ordering": ["-time"],
                "permissions": [("use_tracker", "Can use the KillTracker desktop client")],
            },
        ),
    ]
