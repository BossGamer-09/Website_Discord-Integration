"""
app/quartermaster/models.py

Unified tracker for:
  - Org SC loot stockpile (bulk materials, currencies) by location
  - Physical org equipment (StockItem / StockUnit / StockAssignment)
  - Crafted weapons (TrackedCraftedWeapon)
  - aUEC / merit payout requests (MeritRequest)
"""
import uuid
from django.db import models, transaction
from django.conf import settings
from django.utils import timezone
from django.utils.timezone import now
from django.utils.text import slugify
from simple_history.models import HistoricalRecords
from app.main.util.db import SignalEmittingManager, OriginalStateMixin


# ─────────────────────────────────────────────────────────────────────────────
# LOOT STOCKPILE  (bulk materials / currencies by location)
# ─────────────────────────────────────────────────────────────────────────────

class LootItem(models.Model):
    class Location(models.TextChoices):
        STANTON   = "STANTON",   "Stanton"
        PYRO      = "PYRO",      "Pyro"
        NYX       = "NYX",       "Nyx"
        UNIVERSAL = "UNIVERSAL", "Universal (no location)"

    class Category(models.TextChoices):
        CURRENCY        = "CURRENCY",        "Currency (aUEC/Merits)"
        WIKELO_CURRENCY = "WIKELO_CURRENCY", "Wikelo Currencies"
        WIKELO_MATERIAL = "WIKELO_MATERIAL", "Wikelo Materials"
        MONSTER_PART    = "MONSTER_PART",    "Monster Parts"
        MILITARY        = "MILITARY",        "Military Items"
        COMPONENT       = "COMPONENT",       "Wikelo Components"
        WEAPON          = "WEAPON",          "Weapons"
        LEGACY          = "LEGACY",          "Legacy Loot"
        OTHER           = "OTHER",           "Other"

    name              = models.CharField(max_length=150, unique=True)
    slug              = models.SlugField(max_length=180, unique=True, blank=True, db_index=True)
    category          = models.CharField(max_length=30, choices=Category.choices, db_index=True)
    location          = models.CharField(max_length=20, choices=Location.choices, default=Location.STANTON, db_index=True)
    location_agnostic = models.BooleanField(default=False, help_text="No location asked on requests (e.g. aUEC, Merits).")
    target_qty        = models.PositiveIntegerField(default=0)
    low_threshold     = models.PositiveIntegerField(default=0)
    excess_threshold  = models.PositiveIntegerField(default=0)
    is_active         = models.BooleanField(default=True, db_index=True)
    notes             = models.TextField(blank=True)
    history           = HistoricalRecords()

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name)[:170]
            slug, n = base, 1
            while LootItem.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base}-{n}"
                n += 1
            self.slug = slug
        super().save(*args, **kwargs)

    class Meta:
        default_permissions = ()
        app_label = "loot_tracker"
        ordering = ["category", "name"]
        permissions = [
            ("manage_loot",         "QM: Can manage loot items, stock levels, and requests"),
            ("submit_loot_request", "Can submit loot input/withdraw requests"),
            ("view_loot",           "Can view loot inventory"),
            # carried over from inventory
            ("manage_stock",        "Can add, edit, and remove stock items and units"),
            ("assign_stock",        "Can assign stock units to members"),
            ("view_stock",          "Can view the stock inventory"),
            # carried over from merits
            ("submit_merit_request",  "Can submit merit/money requests"),
            ("review_merit_request",  "Can approve or deny merit/money requests"),
            ("fulfill_merit_request", "Can mark merit/money requests as fulfilled"),
            ("view_merit_requests",   "Can view all merit/money requests"),
            # carried over from inventory (crafted weapons)
            ("can_submit_new_weapon",       "Can submit new crafted weapon"),
            ("can_see_weapon_listing",      "Can see weapon listing"),
            ("can_request_held_limited",    "Can request weapon held for Knights"),
            ("can_request_held_orgwide",    "Can request weapon held for Org"),
            ("can_approve_requests",        "Can approve weapon requests"),
            ("can_discord_manage_state",    "Can manage weapon state in Discord"),
        ]

    def __str__(self):
        return self.name

    @property
    def display_location(self):
        return "Universal" if self.location_agnostic else self.get_location_display()


class LootStock(models.Model):
    item       = models.OneToOneField(LootItem, on_delete=models.CASCADE, related_name="stock")
    quantity   = models.BigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="loot_stock_updates",
    )
    history = HistoricalRecords()

    class Meta:
        default_permissions = ()
        app_label = "loot_tracker"
        verbose_name = "Loot Stock Level"

    def __str__(self):
        return f"{self.item.name}: {self.quantity}"

    @property
    def status_emoji(self):
        q, item = self.quantity, self.item
        if item.excess_threshold and q >= item.excess_threshold:
            return "⬆️"
        if q >= item.target_qty:
            return "🟢"
        if item.low_threshold and q <= item.low_threshold:
            return "🔴"
        return "🟡"


class LootRequest(models.Model):
    class Direction(models.TextChoices):
        INPUT    = "INPUT",    "Input (deposit)"
        WITHDRAW = "WITHDRAW", "Withdraw"

    class Status(models.TextChoices):
        PENDING  = "PENDING",  "Pending"
        APPROVED = "APPROVED", "Approved"
        DENIED   = "DENIED",   "Denied"

    uid           = models.UUIDField(default=uuid.uuid4, editable=False, unique=True, db_index=True)
    item          = models.ForeignKey(LootItem, on_delete=models.CASCADE, related_name="requests")
    requester     = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="loot_requests")
    direction     = models.CharField(max_length=10, choices=Direction.choices, db_index=True)
    quantity      = models.PositiveIntegerField()
    location      = models.CharField(max_length=20, choices=LootItem.Location.choices, blank=True)
    notes         = models.TextField(blank=True)
    status        = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    reviewed_by   = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="reviewed_loot_requests",
    )
    reviewer_note  = models.TextField(blank=True)
    reviewed_at    = models.DateTimeField(null=True, blank=True)
    thread_id      = models.BigIntegerField(null=True, blank=True)
    embed_message_id = models.BigIntegerField(null=True, blank=True)
    submitted_at   = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)
    history        = HistoricalRecords()

    class Meta:
        default_permissions = ()
        app_label = "loot_tracker"
        ordering = ["-submitted_at"]

    def __str__(self):
        return f"[{self.direction}] {self.item.name} ×{self.quantity} — {self.requester}"

    @property
    def location_display(self):
        if self.item.location_agnostic or not self.location:
            return "Universal"
        return LootItem.Location(self.location).label


class LootStatusMessage(models.Model):
    id           = models.IntegerField(primary_key=True, default=1)
    message_id   = models.BigIntegerField(null=True, blank=True)
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()
        app_label = "loot_tracker"
        constraints = [models.CheckConstraint(name="loot_tracker_single_status_record", condition=models.Q(id=1))]

    @classmethod
    async def get_singleton(cls):
        obj, _ = await cls.objects.aget_or_create(id=1)
        return obj


# ─────────────────────────────────────────────────────────────────────────────
# PHYSICAL EQUIPMENT STOCK  (moved from app.inventory)
# ─────────────────────────────────────────────────────────────────────────────

class StockItem(models.Model):
    class TrackingMode(models.TextChoices):
        UNIT     = "UNIT",     "Individual Unit"    # one physical item per StockUnit
        BULK_SCU = "BULK_SCU", "Bulk / SCU Volume"  # ore/materials; scu_volume on each StockUnit
        CRAFTED  = "CRAFTED",  "Crafted Item"       # rolled-stat item with state machine

    name                = models.CharField(max_length=150, unique=True)
    slug                = models.SlugField(max_length=180, unique=True, blank=True, db_index=True)
    category            = models.CharField(max_length=100, blank=True, default="", db_index=True)
    subcategory         = models.CharField(max_length=100, blank=True, default="", db_index=True, help_text="Optional sub-category (e.g. ore quality, item tier)")
    tracking_mode       = models.CharField(max_length=10, choices=TrackingMode.choices, default=TrackingMode.UNIT, db_index=True)
    # Item-level SC metadata (manufacturer, item_type, class, attachments, etc.)
    sc_metadata         = models.JSONField(default=dict, blank=True, help_text="Star Citizen item metadata (manufacturer, class, item_type, attachments, …)")
    stats               = models.JSONField(default=dict, blank=True, help_text="Free-form key/value stats (e.g. Quality, Yield, Damage)")
    description         = models.TextField(blank=True, default="")
    notes               = models.TextField(blank=True, default="")
    low_stock_threshold = models.PositiveIntegerField(default=2)
    created_by          = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="created_stock_items",
    )
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)
    history     = HistoricalRecords()

    class Meta:
        default_permissions = ()
        app_label = "loot_tracker"
        ordering = ["category", "name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name)[:170]
            slug, n = base, 1
            while StockItem.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base}-{n}"
                n += 1
            self.slug = slug
        super().save(*args, **kwargs)

    @property
    def available_count(self):
        return self.units.filter(status=StockUnit.Status.AVAILABLE).count()

    @property
    def total_count(self):
        return self.units.exclude(status=StockUnit.Status.RETIRED).count()

    @property
    def is_low_stock(self):
        return self.available_count <= self.low_stock_threshold


class StockUnit(OriginalStateMixin, models.Model):
    class Status(models.TextChoices):
        AVAILABLE = "AVAILABLE", "Available"
        ASSIGNED  = "ASSIGNED",  "Assigned"
        RETURNED  = "RETURNED",  "Returned"
        LOST      = "LOST",      "Lost"
        RETIRED   = "RETIRED",   "Retired"

    class CraftState(models.TextChoices):
        HELD_K         = "HELD_K",    "Available for Knights"
        HELD_O         = "HELD_O",    "Available for Org"
        IN_USE         = "IN_USE",    "Actively in Use"
        AWAITING_RETURN= "AWAIT_RET", "Awaiting Return"
        EXTERNAL       = "EXTERNAL",  "Not under Org control"
        LOST_COMBAT    = "LOST_COMBAT","Lost in Combat"
        LOST_OTHER     = "LOST_OTHER", "Lost / Other"

    uid             = models.UUIDField(default=uuid.uuid4, editable=False, unique=True, db_index=True)
    item            = models.ForeignKey(StockItem, on_delete=models.CASCADE, related_name="units")
    # Per-unit rolled/crafted stats (CRAFTED items) or generic stats (UNIT items)
    stats           = models.JSONField(default=dict, blank=True)
    # Per-unit SC metadata: serial, scan_date, source_location, etc.
    sc_metadata     = models.JSONField(default=dict, blank=True, help_text="Per-unit SC metadata (serial, source location, scan date, …)")
    # SCU volume — populated for BULK_SCU items (ore chunks, material stacks)
    scu_volume      = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text="SCU volume for bulk/ore items")
    # State machine for CRAFTED items (mirrors TrackedCraftedWeaponState)
    craft_state     = models.CharField(max_length=12, choices=CraftState.choices, null=True, blank=True, db_index=True)
    status          = models.CharField(max_length=12, choices=Status.choices, default=Status.AVAILABLE, db_index=True)
    condition_notes = models.TextField(blank=True, default="")
    current_holder  = models.ForeignKey(
        "unifieduser.OrgPlayer", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="held_stock_units",
    )
    added_by   = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="added_stock_units",
    )
    added_at   = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    history    = HistoricalRecords()
    objects    = SignalEmittingManager()

    class Meta:
        default_permissions = ()
        app_label = "loot_tracker"
        ordering = ["item__name", "-added_at"]

    def __str__(self):
        stats_summary = ", ".join(f"{k}: {v}" for k, v in list(self.stats.items())[:2])
        return f"{self.item.name} #{self.pk} [{self.get_status_display()}] {stats_summary}"


class StockAssignment(models.Model):
    class Action(models.TextChoices):
        ASSIGNED = "ASSIGNED", "Assigned"
        RETURNED = "RETURNED", "Returned"
        LOST     = "LOST",     "Lost"

    unit           = models.ForeignKey(StockUnit, on_delete=models.CASCADE, related_name="assignment_log")
    action         = models.CharField(max_length=10, choices=Action.choices, db_index=True)
    recipient      = models.ForeignKey(
        "unifieduser.OrgPlayer", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="stock_assignment_log",
    )
    recipient_name = models.CharField(max_length=150, blank=True, default="")
    notes          = models.TextField(blank=True, default="")
    actioned_by    = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="stock_actions_made",
    )
    actioned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        app_label = "loot_tracker"
        ordering = ["-actioned_at"]

    def __str__(self):
        who = self.recipient.display_name if self.recipient_id else self.recipient_name or "Unknown"
        return f"{self.action} {self.unit.item.name} #{self.unit_id} → {who}"


# ─────────────────────────────────────────────────────────────────────────────
# CRAFT BLUEPRINTS  (QM-managed crafting requirements per StockItem)
# ─────────────────────────────────────────────────────────────────────────────

class CraftBlueprint(models.Model):
    class ItemType(models.TextChoices):
        FPS_ARMOR   = "FPS_ARMOR",   "FPS Armor"
        FPS_WEAPON  = "FPS_WEAPON",  "FPS Weapon"
        SHIP_WEAPON = "SHIP_WEAPON", "Ship Weapon"
        SHIP        = "SHIP",        "Ship"
        COMPONENT   = "COMPONENT",   "Ship Component"
        OTHER       = "OTHER",       "Other"

    stock_item = models.OneToOneField(
        StockItem, on_delete=models.CASCADE, related_name="blueprint",
        help_text="The StockItem this blueprint produces.",
    )
    item_type  = models.CharField(max_length=20, choices=ItemType.choices, db_index=True)
    notes      = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="created_blueprints",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    history    = HistoricalRecords()

    class Meta:
        default_permissions = ()
        app_label = "loot_tracker"
        ordering = ["item_type", "stock_item__name"]

    def __str__(self):
        return f"{self.stock_item.name} Blueprint"


class CraftMaterial(models.Model):
    blueprint      = models.ForeignKey(CraftBlueprint, on_delete=models.CASCADE, related_name="materials")
    material_name  = models.CharField(max_length=100)
    min_temp       = models.PositiveIntegerField(help_text="Minimum refining temperature")
    temp_is_floor  = models.BooleanField(
        default=True,
        help_text="Checked = minimum threshold (900+). Unchecked = exact temperature.",
    )
    yield_min      = models.DecimalField(
        max_digits=10, decimal_places=4,
        help_text="Minimum required quantity (e.g. 0.05 SCU, or 20 gems)",
    )
    yield_max      = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True,
        help_text="Maximum required quantity. Leave blank if same as minimum.",
    )
    constraint_group = models.CharField(
        max_length=10, blank=True,
        help_text="Label linking materials sharing a combined constraint (e.g. '*'). Must match a CraftConstraint group_label.",
    )
    sort_order     = models.PositiveSmallIntegerField(default=0)

    class Meta:
        default_permissions = ()
        app_label = "loot_tracker"
        ordering = ["sort_order", "pk"]

    def __str__(self):
        return f"{self.material_name} ({self.blueprint})"

    @property
    def temp_display(self):
        if self.min_temp == 0:
            return "Any"
        return f"{self.min_temp}+" if self.temp_is_floor else str(self.min_temp)

    @property
    def yield_display(self):
        def fmt(v):
            return '{:f}'.format(v.normalize())
        if self.yield_max is not None:
            return f"{fmt(self.yield_min)}–{fmt(self.yield_max)}"
        return fmt(self.yield_min)


class CraftConstraint(models.Model):
    blueprint      = models.ForeignKey(CraftBlueprint, on_delete=models.CASCADE, related_name="constraints")
    group_label    = models.CharField(max_length=10, help_text="Must match constraint_group on linked CraftMaterial rows")
    combined_total = models.PositiveIntegerField(help_text="Combined temperature total required across grouped materials")
    note           = models.TextField(blank=True, help_text="Human-readable explanation of the constraint")

    class Meta:
        default_permissions = ()
        app_label = "loot_tracker"

    def __str__(self):
        return f"[{self.group_label}] combined={self.combined_total} ({self.blueprint})"


# ─────────────────────────────────────────────────────────────────────────────
# EQUIPMENT REQUESTS  (members request crafted items from stock)
# ─────────────────────────────────────────────────────────────────────────────

class EquipmentRequest(models.Model):
    class Status(models.TextChoices):
        PENDING  = "PENDING",  "Pending"
        APPROVED = "APPROVED", "Approved"
        DENIED   = "DENIED",   "Denied"

    uid            = models.UUIDField(default=uuid.uuid4, editable=False, unique=True, db_index=True)
    stock_item     = models.ForeignKey(StockItem, on_delete=models.CASCADE, related_name="equipment_requests")
    requester      = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="equipment_requests"
    )
    notes          = models.TextField(blank=True)
    status         = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    assigned_unit  = models.ForeignKey(
        StockUnit, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="assigned_requests",
    )
    reviewed_by    = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="reviewed_equipment_requests",
    )
    reviewer_note  = models.TextField(blank=True)
    reviewed_at    = models.DateTimeField(null=True, blank=True)
    thread_id      = models.BigIntegerField(null=True, blank=True)
    embed_message_id = models.BigIntegerField(null=True, blank=True)
    submitted_at   = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)
    history        = HistoricalRecords()

    class Meta:
        default_permissions = ()
        app_label = "loot_tracker"
        ordering = ["-submitted_at"]

    def __str__(self):
        return f"[{self.status}] {self.stock_item.name} ← {self.requester}"


# ─────────────────────────────────────────────────────────────────────────────
# CRAFTED WEAPONS  (moved from app.inventory)
# ─────────────────────────────────────────────────────────────────────────────

class TrackedCraftedWeapon(OriginalStateMixin, models.Model):
    submitter    = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    weapon_name  = models.CharField(max_length=255)
    stats        = models.JSONField(default=dict)
    submitted_at = models.DateTimeField(auto_now_add=True)
    gone_at      = models.DateTimeField(null=True, blank=True)
    history      = HistoricalRecords()
    objects      = SignalEmittingManager()

    def set_editing_user(self, user):
        self._history_user = user

    def __str__(self):
        name = self.submitter.display_name if self.submitter_id else "Unknown"
        return f"{self.weapon_name} (Scanned by: {name})"

    class Meta:
        default_permissions = ()
        app_label = "loot_tracker"
        ordering = ["-submitted_at"]


class TrackedCraftedWeaponState(OriginalStateMixin, models.Model):
    class Status(models.TextChoices):
        IN_USE                 = "USED",      "Actively in Use"
        HELD_AVAILIBLE_LIMITED = "HELD_K",    "Available for Knights"
        HELD_AVAILIBLE_ORGWIDE = "HELD_O",    "Available for Org"
        LOST_CIG               = "LOST_CIG",  "Lost Other"
        LOST_COMBAT            = "LOST_COMBAT","Lost in Combat"
        HELD_EXTERNALLY        = "EXTERNAL",  "Not under Org control"
        AWAITING_RETURN        = "AWAIT_RET", "Awaiting Return"

    status       = models.CharField(max_length=14, choices=Status.choices, default=Status.HELD_EXTERNALLY, db_index=True)
    for_weapon   = models.OneToOneField(TrackedCraftedWeapon, related_name="state", on_delete=models.CASCADE)
    current_owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True)
    purpose      = models.CharField(max_length=255, blank=True, default="")
    changed_at   = models.DateTimeField(auto_now=True)
    history      = HistoricalRecords()
    objects      = SignalEmittingManager()

    def set_editing_user(self, user):
        self._history_user = user

    def save(self, *args, **kwargs):
        if self.has_value_changed_from_db("status") and self.status in (self.Status.LOST_CIG, self.Status.LOST_COMBAT):
            with transaction.atomic():
                self.changed_at   = now()
                self.current_owner = None
                user = getattr(self, "_history_user", None)
                if user:
                    self.for_weapon.set_editing_user(user)
                    self.for_weapon.gone_at = self.changed_at
                    self.for_weapon.save()
        super().save(*args, **kwargs)

    def __str__(self):
        return str(self.status)

    class Meta:
        default_permissions = ()
        app_label = "loot_tracker"


class TrackedCraftedRequests(models.Model):
    submitter  = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    for_weapon = models.ForeignKey(TrackedCraftedWeapon, related_name="requests", on_delete=models.CASCADE)
    note       = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    accepted   = models.BooleanField(default=None, null=True, blank=True)
    closed_at  = models.DateTimeField(blank=True, null=True, default=None)
    objects    = SignalEmittingManager()

    def __str__(self):
        return f"{self.submitter} Request"

    class Meta:
        default_permissions = ()
        app_label = "loot_tracker"
        ordering = ["-created_at"]
        verbose_name = "Tracked crafted request"
        verbose_name_plural = "Tracked crafted requests"


# ─────────────────────────────────────────────────────────────────────────────
# MERIT / MONEY REQUESTS  (moved from app.merits)
# ─────────────────────────────────────────────────────────────────────────────

class MeritRequest(models.Model):
    class Kind(models.TextChoices):
        MONEY = "MONEY", "aUEC Payout"
        MERIT = "MERIT", "Merit Award"

    class Status(models.TextChoices):
        PENDING   = "PENDING",   "Pending"
        FULFILLED = "FULFILLED", "Fulfilled"
        DENIED    = "DENIED",    "Denied"

    uid           = models.UUIDField(default=uuid.uuid4, editable=False, unique=True, db_index=True)
    requester     = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="merit_requests")
    kind          = models.CharField(max_length=10, choices=Kind.choices, db_index=True)
    title         = models.CharField(max_length=120, blank=True)
    reason        = models.TextField(blank=True)
    linked_item   = models.ForeignKey(
        "loot_tracker.StockItem", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="merit_requests",
    )
    rsi_handle    = models.CharField(max_length=60, blank=True)
    during_event  = models.BooleanField(null=True, blank=True)
    purchase_for  = models.CharField(max_length=200, blank=True)
    amount        = models.PositiveIntegerField(default=0)
    status        = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)
    reviewed_by   = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="reviewed_merit_requests",
    )
    reviewer_note = models.TextField(blank=True)
    reviewed_at   = models.DateTimeField(null=True, blank=True)
    fulfilled_at  = models.DateTimeField(null=True, blank=True)
    submitted_at  = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)
    history       = HistoricalRecords()

    class Meta:
        default_permissions = ()
        app_label = "loot_tracker"
        ordering = ["-submitted_at"]

    def __str__(self):
        return f"[{self.get_kind_display()}] {self.title} — {self.requester}"

    @property
    def status_color(self):
        return {
            self.Status.PENDING:   "amber",
            self.Status.FULFILLED: "green",
            self.Status.DENIED:    "red",
        }.get(self.status, "muted")
