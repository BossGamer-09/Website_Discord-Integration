from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import simple_history.models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("inventory", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="MeritRequest",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("kind",          models.CharField(choices=[("MONEY", "aUEC Payout"), ("MERIT", "Merit Award")], db_index=True, max_length=10)),
                ("title",         models.CharField(max_length=120)),
                ("reason",        models.TextField()),
                ("amount",        models.PositiveIntegerField(default=0)),
                ("status",        models.CharField(choices=[("PENDING", "Pending"), ("APPROVED", "Approved"), ("FULFILLED", "Fulfilled"), ("DENIED", "Denied")], db_index=True, default="PENDING", max_length=12)),
                ("reviewer_note", models.TextField(blank=True)),
                ("reminder_sent", models.BooleanField(default=False)),
                ("submitted_at",  models.DateTimeField(auto_now_add=True)),
                ("updated_at",    models.DateTimeField(auto_now=True)),
                ("reviewed_at",   models.DateTimeField(blank=True, null=True)),
                ("fulfilled_at",  models.DateTimeField(blank=True, null=True)),
                ("requester",     models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="merit_requests", to=settings.AUTH_USER_MODEL)),
                ("reviewed_by",   models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reviewed_merit_requests", to=settings.AUTH_USER_MODEL)),
                ("linked_item",   models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="merit_requests", to="inventory.stockitem")),
            ],
            options={
                "ordering": ["-submitted_at"],
                "default_permissions": (),
                "permissions": [
                    ("submit_merit_request",  "Can submit merit/money requests"),
                    ("review_merit_request",  "Can approve or deny merit/money requests"),
                    ("fulfill_merit_request", "Can mark merit/money requests as fulfilled"),
                    ("view_merit_requests",   "Can view all merit/money requests"),
                ],
            },
        ),
        migrations.CreateModel(
            name="HistoricalMeritRequest",
            fields=[
                ("id",            models.IntegerField(blank=True, db_index=True)),
                ("kind",          models.CharField(choices=[("MONEY", "aUEC Payout"), ("MERIT", "Merit Award")], db_index=True, max_length=10)),
                ("title",         models.CharField(max_length=120)),
                ("reason",        models.TextField()),
                ("amount",        models.PositiveIntegerField(default=0)),
                ("status",        models.CharField(choices=[("PENDING", "Pending"), ("APPROVED", "Approved"), ("FULFILLED", "Fulfilled"), ("DENIED", "Denied")], db_index=True, default="PENDING", max_length=12)),
                ("reviewer_note", models.TextField(blank=True)),
                ("reminder_sent", models.BooleanField(default=False)),
                ("submitted_at",  models.DateTimeField(blank=True, editable=False)),
                ("updated_at",    models.DateTimeField(blank=True, editable=False)),
                ("reviewed_at",   models.DateTimeField(blank=True, null=True)),
                ("fulfilled_at",  models.DateTimeField(blank=True, null=True)),
                ("history_id",    models.AutoField(primary_key=True, serialize=False)),
                ("history_date",  models.DateTimeField(db_index=True)),
                ("history_change_reason", models.CharField(max_length=100, null=True)),
                ("history_type",  models.CharField(choices=[("+", "Created"), ("~", "Changed"), ("-", "Deleted")], max_length=1)),
                ("history_user",  models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("requester",     models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("reviewed_by",   models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("linked_item",   models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name="+", to="inventory.stockitem")),
            ],
            options={
                "verbose_name": "historical merit request",
                "verbose_name_plural": "historical merit requests",
                "ordering": ["-history_date", "-history_id"],
                "get_latest_by": ("history_date", "history_id"),
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
    ]
