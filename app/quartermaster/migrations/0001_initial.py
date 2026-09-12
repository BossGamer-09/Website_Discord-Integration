from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import simple_history.models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="LootItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True)),
                ("name", models.CharField(max_length=150, unique=True)),
                ("category", models.CharField(
                    choices=[
                        ("CURRENCY", "Currency (aUEC/Merits)"),
                        ("WIKELO_CURRENCY", "Wikelo Currencies"),
                        ("WIKELO_MATERIAL", "Wikelo Materials"),
                        ("MONSTER_PART", "Monster Parts"),
                        ("MILITARY", "Military Items"),
                        ("COMPONENT", "Wikelo Components"),
                        ("WEAPON", "Weapons"),
                        ("LEGACY", "Legacy Loot"),
                        ("OTHER", "Other"),
                    ],
                    max_length=30, db_index=True,
                )),
                ("location", models.CharField(
                    choices=[
                        ("STANTON", "Stanton"),
                        ("PYRO", "Pyro"),
                        ("NYX", "Nyx"),
                        ("UNIVERSAL", "Universal (no location)"),
                    ],
                    default="STANTON", max_length=20, db_index=True,
                )),
                ("location_agnostic", models.BooleanField(default=False)),
                ("target_qty", models.PositiveIntegerField(default=0)),
                ("low_threshold", models.PositiveIntegerField(default=0)),
                ("excess_threshold", models.PositiveIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True, db_index=True)),
                ("notes", models.TextField(blank=True)),
            ],
            options={"default_permissions": (), "app_label": "loot_tracker", "ordering": ["category", "name"]},
        ),
        migrations.CreateModel(
            name="LootStock",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True)),
                ("item", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="stock", to="loot_tracker.lootitem",
                )),
                ("quantity", models.BigIntegerField(default=0)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("updated_by", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="loot_stock_updates",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={"default_permissions": (), "app_label": "loot_tracker", "verbose_name": "Loot Stock Level"},
        ),
        migrations.CreateModel(
            name="LootRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True)),
                ("item", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="requests", to="loot_tracker.lootitem",
                )),
                ("requester", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="loot_requests", to=settings.AUTH_USER_MODEL,
                )),
                ("direction", models.CharField(
                    choices=[("INPUT", "Input (deposit)"), ("WITHDRAW", "Withdraw")],
                    max_length=10, db_index=True,
                )),
                ("quantity", models.PositiveIntegerField()),
                ("location", models.CharField(blank=True, max_length=20)),
                ("notes", models.TextField(blank=True)),
                ("status", models.CharField(
                    choices=[("PENDING", "Pending"), ("APPROVED", "Approved"), ("DENIED", "Denied")],
                    default="PENDING", max_length=10, db_index=True,
                )),
                ("reviewed_by", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="reviewed_loot_requests",
                    to=settings.AUTH_USER_MODEL,
                )),
                ("reviewer_note", models.TextField(blank=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("thread_id", models.BigIntegerField(blank=True, null=True)),
                ("embed_message_id", models.BigIntegerField(blank=True, null=True)),
                ("submitted_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"default_permissions": (), "app_label": "loot_tracker", "ordering": ["-submitted_at"]},
        ),
        migrations.CreateModel(
            name="LootStatusMessage",
            fields=[
                ("id", models.IntegerField(default=1, primary_key=True)),
                ("message_id", models.BigIntegerField(blank=True, null=True)),
                ("last_updated", models.DateTimeField(auto_now=True)),
            ],
            options={"default_permissions": (), "app_label": "loot_tracker"},
        ),
        migrations.AddConstraint(
            model_name="lootstatusmessage",
            constraint=models.CheckConstraint(
                name="loot_tracker_single_status_record",
                condition=models.Q(id=1),
            ),
        ),
        # History tables
        migrations.CreateModel(
            name="HistoricalLootItem",
            fields=[
                ("id", models.BigIntegerField(blank=True, db_index=True)),
                ("name", models.CharField(max_length=150)),
                ("category", models.CharField(max_length=30, db_index=True)),
                ("location", models.CharField(default="STANTON", max_length=20, db_index=True)),
                ("location_agnostic", models.BooleanField(default=False)),
                ("target_qty", models.PositiveIntegerField(default=0)),
                ("low_threshold", models.PositiveIntegerField(default=0)),
                ("excess_threshold", models.PositiveIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True, db_index=True)),
                ("notes", models.TextField(blank=True)),
                ("history_id", models.AutoField(primary_key=True)),
                ("history_date", models.DateTimeField(db_index=True)),
                ("history_change_reason", models.CharField(max_length=100, null=True)),
                ("history_type", models.CharField(
                    choices=[("+", "Created"), ("~", "Changed"), ("-", "Deleted")], max_length=1,
                )),
                ("history_user", models.ForeignKey(
                    null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+", to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={"verbose_name": "historical loot item", "ordering": ("-history_date", "-history_id"), "get_latest_by": ("history_date", "history_id")},
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
        migrations.CreateModel(
            name="HistoricalLootStock",
            fields=[
                ("id", models.BigIntegerField(blank=True, db_index=True)),
                ("quantity", models.BigIntegerField(default=0)),
                ("updated_at", models.DateTimeField(blank=True, editable=False)),
                ("history_id", models.AutoField(primary_key=True)),
                ("history_date", models.DateTimeField(db_index=True)),
                ("history_change_reason", models.CharField(max_length=100, null=True)),
                ("history_type", models.CharField(
                    choices=[("+", "Created"), ("~", "Changed"), ("-", "Deleted")], max_length=1,
                )),
                ("history_user", models.ForeignKey(
                    null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+", to=settings.AUTH_USER_MODEL,
                )),
                ("item", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.DO_NOTHING,
                    related_name="+", to="loot_tracker.lootitem",
                )),
            ],
            options={"verbose_name": "historical loot stock", "ordering": ("-history_date", "-history_id"), "get_latest_by": ("history_date", "history_id")},
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
        migrations.CreateModel(
            name="HistoricalLootRequest",
            fields=[
                ("id", models.BigIntegerField(blank=True, db_index=True)),
                ("direction", models.CharField(max_length=10, db_index=True)),
                ("quantity", models.PositiveIntegerField()),
                ("location", models.CharField(blank=True, max_length=20)),
                ("notes", models.TextField(blank=True)),
                ("status", models.CharField(default="PENDING", max_length=10, db_index=True)),
                ("reviewer_note", models.TextField(blank=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("thread_id", models.BigIntegerField(blank=True, null=True)),
                ("embed_message_id", models.BigIntegerField(blank=True, null=True)),
                ("submitted_at", models.DateTimeField(blank=True, editable=False)),
                ("updated_at", models.DateTimeField(blank=True, editable=False)),
                ("history_id", models.AutoField(primary_key=True)),
                ("history_date", models.DateTimeField(db_index=True)),
                ("history_change_reason", models.CharField(max_length=100, null=True)),
                ("history_type", models.CharField(
                    choices=[("+", "Created"), ("~", "Changed"), ("-", "Deleted")], max_length=1,
                )),
                ("history_user", models.ForeignKey(
                    null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+", to=settings.AUTH_USER_MODEL,
                )),
                ("item", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.DO_NOTHING,
                    related_name="+", to="loot_tracker.lootitem",
                )),
                ("requester", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.DO_NOTHING,
                    related_name="+", to=settings.AUTH_USER_MODEL,
                )),
                ("reviewed_by", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.DO_NOTHING,
                    related_name="+", to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={"verbose_name": "historical loot request", "ordering": ("-history_date", "-history_id"), "get_latest_by": ("history_date", "history_id")},
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
    ]
