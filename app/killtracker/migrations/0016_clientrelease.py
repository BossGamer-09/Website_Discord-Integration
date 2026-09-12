import django.db.models.deletion
import simple_history.models
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("killtracker", "0015_killevent_incap"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ClientRelease",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("version", models.CharField(help_text="Must match the exe's local_version (e.g. '1.7'). Clients update when this is greater than theirs.", max_length=64, unique=True)),
                ("file", models.FileField(help_text="The built 'BlightVeil KillTracker.exe'.", upload_to="killtracker/releases/")),
                ("is_active", models.BooleanField(db_index=True, default=False)),
                ("notes", models.CharField(blank=True, default="", max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "killtracker_client_release",
                "ordering": ["-created_at"],
                "default_permissions": (),
            },
        ),
        migrations.CreateModel(
            name="HistoricalClientRelease",
            fields=[
                ("id", models.BigIntegerField(auto_created=True, blank=True, db_index=True, verbose_name="ID")),
                ("version", models.CharField(db_index=True, help_text="Must match the exe's local_version (e.g. '1.7'). Clients update when this is greater than theirs.", max_length=64)),
                ("file", models.TextField(help_text="The built 'BlightVeil KillTracker.exe'.", max_length=100)),
                ("is_active", models.BooleanField(db_index=True, default=False)),
                ("notes", models.CharField(blank=True, default="", max_length=255)),
                ("created_at", models.DateTimeField(blank=True, editable=False)),
                ("history_id", models.AutoField(primary_key=True, serialize=False)),
                ("history_date", models.DateTimeField(db_index=True)),
                ("history_change_reason", models.CharField(max_length=100, null=True)),
                ("history_type", models.CharField(choices=[("+", "Created"), ("~", "Changed"), ("-", "Deleted")], max_length=1)),
                (
                    "history_user",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "historical client release",
                "verbose_name_plural": "historical client releases",
                "ordering": ("-history_date", "-history_id"),
                "get_latest_by": ("history_date", "history_id"),
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
    ]
