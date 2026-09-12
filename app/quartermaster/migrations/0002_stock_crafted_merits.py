"""
Migration 0002: absorb app.inventory and app.merits models into loot_tracker.

Creates:
  - StockItem, StockUnit, StockAssignment
  - TrackedCraftedWeapon, TrackedCraftedWeaponState, TrackedCraftedRequests
  - MeritRequest
  + their historical tables
  + updated permissions on LootItem
"""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import simple_history.models


class Migration(migrations.Migration):

    dependencies = [
        ("loot_tracker", "0001_initial"),
        ("unifieduser", "__first__"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [

        # ── Update LootItem permissions ───────────────────────────────────────
        migrations.AlterModelOptions(
            name="lootitem",
            options={
                "default_permissions": (),
                "app_label": "loot_tracker",
                "ordering": ["category", "name"],
                "permissions": [
                    ("manage_loot", "QM: Can manage loot items, stock levels, and requests"),
                    ("submit_loot_request", "Can submit loot input/withdraw requests"),
                    ("view_loot", "Can view loot inventory"),
                    ("manage_stock", "Can add, edit, and remove stock items and units"),
                    ("assign_stock", "Can assign stock units to knights"),
                    ("view_stock", "Can view the stock inventory"),
                    ("submit_merit_request", "Can submit merit/money requests"),
                    ("review_merit_request", "Can approve or deny merit/money requests"),
                    ("fulfill_merit_request", "Can mark merit/money requests as fulfilled"),
                    ("view_merit_requests", "Can view all merit/money requests"),
                    ("can_submit_new_weapon", "Can submit new crafted weapon"),
                    ("can_see_weapon_listing", "Can see weapon listing"),
                    ("can_request_held_limited", "Can request weapon held for Knights"),
                    ("can_request_held_orgwide", "Can request weapon held for Org"),
                    ("can_approve_requests", "Can approve weapon requests"),
                    ("can_discord_manage_state", "Can manage weapon state in Discord"),
                ],
            },
        ),

        # ── StockItem ─────────────────────────────────────────────────────────
        migrations.CreateModel(
            name="StockItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True)),
                ("name", models.CharField(max_length=150, unique=True)),
                ("category", models.CharField(blank=True, db_index=True, default="", max_length=100)),
                ("description", models.TextField(blank=True, default="")),
                ("notes", models.TextField(blank=True, default="")),
                ("low_stock_threshold", models.PositiveIntegerField(default=2)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_stock_items", to=settings.AUTH_USER_MODEL)),
            ],
            options={"default_permissions": (), "app_label": "loot_tracker", "ordering": ["category", "name"]},
        ),
        migrations.CreateModel(
            name="HistoricalStockItem",
            fields=[
                ("id", models.BigIntegerField(blank=True, db_index=True)),
                ("name", models.CharField(max_length=150)),
                ("category", models.CharField(blank=True, db_index=True, default="", max_length=100)),
                ("description", models.TextField(blank=True, default="")),
                ("notes", models.TextField(blank=True, default="")),
                ("low_stock_threshold", models.PositiveIntegerField(default=2)),
                ("created_at", models.DateTimeField(blank=True, editable=False)),
                ("updated_at", models.DateTimeField(blank=True, editable=False)),
                ("history_id", models.AutoField(primary_key=True)),
                ("history_date", models.DateTimeField(db_index=True)),
                ("history_change_reason", models.CharField(max_length=100, null=True)),
                ("history_type", models.CharField(choices=[("+", "Created"), ("~", "Changed"), ("-", "Deleted")], max_length=1)),
                ("created_by", models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("history_user", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={"verbose_name": "historical stock item", "ordering": ("-history_date", "-history_id"), "get_latest_by": ("history_date", "history_id")},
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),

        # ── StockUnit ─────────────────────────────────────────────────────────
        migrations.CreateModel(
            name="StockUnit",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True)),
                ("stats", models.JSONField(blank=True, default=dict)),
                ("status", models.CharField(choices=[("AVAILABLE","Available"),("ASSIGNED","Assigned"),("RETURNED","Returned"),("LOST","Lost"),("RETIRED","Retired")], db_index=True, default="AVAILABLE", max_length=12)),
                ("condition_notes", models.TextField(blank=True, default="")),
                ("added_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("item", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="units", to="loot_tracker.stockitem")),
                ("current_holder", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="held_stock_units", to="unifieduser.orgplayer")),
                ("added_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="added_stock_units", to=settings.AUTH_USER_MODEL)),
            ],
            options={"default_permissions": (), "app_label": "loot_tracker", "ordering": ["item__name", "-added_at"]},
        ),
        migrations.CreateModel(
            name="HistoricalStockUnit",
            fields=[
                ("id", models.BigIntegerField(blank=True, db_index=True)),
                ("stats", models.JSONField(blank=True, default=dict)),
                ("status", models.CharField(db_index=True, default="AVAILABLE", max_length=12)),
                ("condition_notes", models.TextField(blank=True, default="")),
                ("added_at", models.DateTimeField(blank=True, editable=False)),
                ("updated_at", models.DateTimeField(blank=True, editable=False)),
                ("history_id", models.AutoField(primary_key=True)),
                ("history_date", models.DateTimeField(db_index=True)),
                ("history_change_reason", models.CharField(max_length=100, null=True)),
                ("history_type", models.CharField(choices=[("+", "Created"), ("~", "Changed"), ("-", "Deleted")], max_length=1)),
                ("item", models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name="+", to="loot_tracker.stockitem")),
                ("current_holder", models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name="+", to="unifieduser.orgplayer")),
                ("added_by", models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("history_user", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={"verbose_name": "historical stock unit", "ordering": ("-history_date", "-history_id"), "get_latest_by": ("history_date", "history_id")},
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),

        # ── StockAssignment ───────────────────────────────────────────────────
        migrations.CreateModel(
            name="StockAssignment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True)),
                ("action", models.CharField(choices=[("ASSIGNED","Assigned"),("RETURNED","Returned"),("LOST","Lost")], db_index=True, max_length=10)),
                ("recipient_name", models.CharField(blank=True, default="", max_length=150)),
                ("notes", models.TextField(blank=True, default="")),
                ("actioned_at", models.DateTimeField(auto_now_add=True)),
                ("unit", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="assignment_log", to="loot_tracker.stockunit")),
                ("recipient", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="stock_assignment_log", to="unifieduser.orgplayer")),
                ("actioned_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="stock_actions_made", to=settings.AUTH_USER_MODEL)),
            ],
            options={"default_permissions": (), "app_label": "loot_tracker", "ordering": ["-actioned_at"]},
        ),

        # ── TrackedCraftedWeapon ──────────────────────────────────────────────
        migrations.CreateModel(
            name="TrackedCraftedWeapon",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True)),
                ("weapon_name", models.CharField(max_length=255)),
                ("stats", models.JSONField(default=dict)),
                ("submitted_at", models.DateTimeField(auto_now_add=True)),
                ("gone_at", models.DateTimeField(blank=True, null=True)),
                ("submitter", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={"default_permissions": (), "app_label": "loot_tracker", "ordering": ["-submitted_at"]},
        ),
        migrations.CreateModel(
            name="HistoricalTrackedCraftedWeapon",
            fields=[
                ("id", models.BigIntegerField(blank=True, db_index=True)),
                ("weapon_name", models.CharField(max_length=255)),
                ("stats", models.JSONField(default=dict)),
                ("submitted_at", models.DateTimeField(blank=True, editable=False)),
                ("gone_at", models.DateTimeField(blank=True, null=True)),
                ("history_id", models.AutoField(primary_key=True)),
                ("history_date", models.DateTimeField(db_index=True)),
                ("history_change_reason", models.CharField(max_length=100, null=True)),
                ("history_type", models.CharField(choices=[("+", "Created"), ("~", "Changed"), ("-", "Deleted")], max_length=1)),
                ("submitter", models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("history_user", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={"verbose_name": "historical tracked crafted weapon", "ordering": ("-history_date", "-history_id"), "get_latest_by": ("history_date", "history_id")},
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),

        # ── TrackedCraftedWeaponState ─────────────────────────────────────────
        migrations.CreateModel(
            name="TrackedCraftedWeaponState",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True)),
                ("status", models.CharField(choices=[("USED","Actively in Use"),("HELD_K","Available for Knights"),("HELD_O","Available for Org"),("LOST_CIG","Lost Other"),("LOST_COMBAT","Lost in Combat"),("EXTERNAL","Not under Org control"),("AWAIT_RET","Awaiting Return")], db_index=True, default="EXTERNAL", max_length=14)),
                ("purpose", models.CharField(blank=True, default="", max_length=255)),
                ("changed_at", models.DateTimeField(auto_now=True)),
                ("for_weapon", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="state", to="loot_tracker.trackedcraftedweapon")),
                ("current_owner", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={"default_permissions": (), "app_label": "loot_tracker"},
        ),
        migrations.CreateModel(
            name="HistoricalTrackedCraftedWeaponState",
            fields=[
                ("id", models.BigIntegerField(blank=True, db_index=True)),
                ("status", models.CharField(db_index=True, default="EXTERNAL", max_length=14)),
                ("purpose", models.CharField(blank=True, default="", max_length=255)),
                ("changed_at", models.DateTimeField(blank=True, editable=False)),
                ("history_id", models.AutoField(primary_key=True)),
                ("history_date", models.DateTimeField(db_index=True)),
                ("history_change_reason", models.CharField(max_length=100, null=True)),
                ("history_type", models.CharField(choices=[("+", "Created"), ("~", "Changed"), ("-", "Deleted")], max_length=1)),
                ("for_weapon", models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name="+", to="loot_tracker.trackedcraftedweapon")),
                ("current_owner", models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("history_user", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={"verbose_name": "historical tracked crafted weapon state", "ordering": ("-history_date", "-history_id"), "get_latest_by": ("history_date", "history_id")},
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),

        # ── TrackedCraftedRequests ────────────────────────────────────────────
        migrations.CreateModel(
            name="TrackedCraftedRequests",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True)),
                ("note", models.TextField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("accepted", models.BooleanField(blank=True, default=None, null=True)),
                ("closed_at", models.DateTimeField(blank=True, default=None, null=True)),
                ("submitter", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ("for_weapon", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="requests", to="loot_tracker.trackedcraftedweapon")),
            ],
            options={"default_permissions": (), "app_label": "loot_tracker", "ordering": ["-created_at"]},
        ),

        # ── MeritRequest ──────────────────────────────────────────────────────
        migrations.CreateModel(
            name="MeritRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True)),
                ("kind", models.CharField(choices=[("MONEY","aUEC Payout"),("MERIT","Merit Award")], db_index=True, max_length=10)),
                ("title", models.CharField(blank=True, max_length=120)),
                ("reason", models.TextField(blank=True)),
                ("rsi_handle", models.CharField(blank=True, max_length=60)),
                ("during_event", models.BooleanField(blank=True, null=True)),
                ("purchase_for", models.CharField(blank=True, max_length=200)),
                ("amount", models.PositiveIntegerField(default=0)),
                ("status", models.CharField(choices=[("PENDING","Pending"),("FULFILLED","Fulfilled"),("DENIED","Denied")], db_index=True, default="PENDING", max_length=12)),
                ("reviewer_note", models.TextField(blank=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("fulfilled_at", models.DateTimeField(blank=True, null=True)),
                ("submitted_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("requester", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="merit_requests", to=settings.AUTH_USER_MODEL)),
                ("linked_item", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="merit_requests", to="loot_tracker.stockitem")),
                ("reviewed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reviewed_merit_requests", to=settings.AUTH_USER_MODEL)),
            ],
            options={"default_permissions": (), "app_label": "loot_tracker", "ordering": ["-submitted_at"]},
        ),
        migrations.CreateModel(
            name="HistoricalMeritRequest",
            fields=[
                ("id", models.BigIntegerField(blank=True, db_index=True)),
                ("kind", models.CharField(db_index=True, max_length=10)),
                ("title", models.CharField(blank=True, max_length=120)),
                ("reason", models.TextField(blank=True)),
                ("rsi_handle", models.CharField(blank=True, max_length=60)),
                ("during_event", models.BooleanField(blank=True, null=True)),
                ("purchase_for", models.CharField(blank=True, max_length=200)),
                ("amount", models.PositiveIntegerField(default=0)),
                ("status", models.CharField(db_index=True, default="PENDING", max_length=12)),
                ("reviewer_note", models.TextField(blank=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("fulfilled_at", models.DateTimeField(blank=True, null=True)),
                ("submitted_at", models.DateTimeField(blank=True, editable=False)),
                ("updated_at", models.DateTimeField(blank=True, editable=False)),
                ("history_id", models.AutoField(primary_key=True)),
                ("history_date", models.DateTimeField(db_index=True)),
                ("history_change_reason", models.CharField(max_length=100, null=True)),
                ("history_type", models.CharField(choices=[("+", "Created"), ("~", "Changed"), ("-", "Deleted")], max_length=1)),
                ("requester", models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("linked_item", models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name="+", to="loot_tracker.stockitem")),
                ("reviewed_by", models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("history_user", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={"verbose_name": "historical merit request", "ordering": ("-history_date", "-history_id"), "get_latest_by": ("history_date", "history_id")},
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
    ]
