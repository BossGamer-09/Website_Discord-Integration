# Device-flow login records for the KillTracker desktop client.
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("killtracker", "0006_killevent_use_tracker_perm"),
    ]

    operations = [
        migrations.CreateModel(
            name="DeviceAuthRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("device_code_hash", models.CharField(db_index=True, max_length=64, unique=True)),
                ("user_code", models.CharField(db_index=True, max_length=12, unique=True)),
                ("status", models.CharField(default="pending", max_length=12)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("issued_key", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="killtracker.apikey")),
                ("user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="killtracker_device_auths", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "db_table": "killtracker_device_auth",
                "ordering": ["-created_at"],
                "default_permissions": (),
            },
        ),
    ]
