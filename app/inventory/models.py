from django.db import models, transaction
from django.conf import settings
from django.utils.timezone import now

from simple_history.models import HistoricalRecords
from app.main.util.db import SignalEmittingManager, OriginalStateMixin


class TrackedCraftedWeapon(OriginalStateMixin, models.Model):
    submitter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    weapon_name = models.CharField(max_length=255)
    stats = models.JSONField(default=dict)
    submitted_at = models.DateTimeField(auto_now_add=True)
    gone_at = models.DateTimeField(null=True, blank=True)

    history = HistoricalRecords()

    objects = SignalEmittingManager()

    def set_editing_user(self, user):
        self._history_user = user

    def __str__(self):
        return f"{self.weapon_name} (Scanned by: {self.submitter.display_name})"

    class Meta:
        default_permissions = ()
        ordering = ['-submitted_at']
        permissions = [
            ("can_submit_new_weapon", "Can submit new weapon"),
            ("can_see_weapon_listing", "Can see weapon listing"),
            ("can_request_held_limited", "Can request weapon held for Knights"),
            ("can_request_held_orgwide", "Can request weapon held for Org"),
            ("can_approve_requests", "Can approve weapon requests"),
            ("can_discord_manage_state", "Can manage weapon state in discord")
        ]


class TrackedCraftedWeaponState(OriginalStateMixin, models.Model):
    class Status(models.TextChoices):
        IN_USE = 'USED', 'Actively in Use'
        HELD_AVAILIBLE_LIMITED = 'HELD_K', 'Availible for Knights'
        HELD_AVAILIBLE_ORGWIDE = 'HELD_O', 'Availible for Org'
        LOST_CIG = 'LOST_CIG', 'Lost Other'
        LOST_COMBAT = 'LOST_COMBAT', 'Lost in Combat'
        HELD_EXTERNALLY = 'EXTERNAL', 'Not under Org control'
        AWAITING_RETURN = 'AWAIT_RET', 'Awaiting Return'

    status = models.CharField(max_length=14, choices=Status.choices, default=Status.HELD_EXTERNALLY, db_index=True)
    for_weapon = models.OneToOneField(TrackedCraftedWeapon, related_name='state', on_delete=models.CASCADE)
    current_owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True)
    purpose = models.CharField(max_length=255)
    changed_at = models.DateTimeField(auto_now=True)

    history = HistoricalRecords()

    objects = SignalEmittingManager()

    def set_editing_user(self, user):
        self._history_user = user

    def save(self, *args, **kwargs):
        if self.has_value_changed_from_db('status') and self.status in (self.Status.LOST_CIG, self.Status.LOST_COMBAT):
            with transaction.atomic():
                self.changed_at = now()
                self.current_owner = None

                user = getattr(self, '_history_user', None)
                if user:
                    self.for_weapon.set_editing_user(user)
                    self.for_weapon.gone_at = self.changed_at
                    self.for_weapon.save()

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.status}"


class TrackedCraftedRequests(models.Model):
    submitter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    for_weapon = models.ForeignKey(TrackedCraftedWeapon, related_name='requests', on_delete=models.CASCADE)
    note = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    accepted = models.BooleanField(default=None, null=True, blank=True)
    closed_at = models.DateTimeField(blank=True, null=True, default=None)

    objects = SignalEmittingManager()

    def __str__(self):
        return f"{self.submitter} Request"

    class Meta:
        ordering = ['-created_at']


# ---------------------------------------------------------------------------
# Quartermaster stock system
# ---------------------------------------------------------------------------

class StockItem(models.Model):
    """Catalogue entry — the base item type (e.g. 'P8-AR Rifle', 'Ballistic Shield')."""

    name        = models.CharField(max_length=150, unique=True)
    category    = models.CharField(max_length=100, blank=True, default="", db_index=True,
                                   help_text="e.g. Weapons, Armour, Medical, Ammo")
    description = models.TextField(blank=True, default="")
    notes       = models.TextField(blank=True, default="")
    low_stock_threshold = models.PositiveIntegerField(
        default=2, help_text="Post a warning to the log channel when available units fall to this level"
    )

    created_by  = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="created_stock_items",
    )
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    history = HistoricalRecords()

    class Meta:
        default_permissions = ()
        ordering = ["category", "name"]
        permissions = [
            ("manage_stock",  "Can add, edit, and remove stock items and units"),
            ("assign_stock",  "Can assign stock units to knights"),
            ("view_stock",    "Can view the stock inventory"),
        ]

    def __str__(self):
        return self.name

    @property
    def available_count(self) -> int:
        return self.units.filter(status=StockUnit.Status.AVAILABLE).count()

    @property
    def total_count(self) -> int:
        return self.units.exclude(status=StockUnit.Status.RETIRED).count()

    @property
    def is_low_stock(self) -> bool:
        return self.available_count <= self.low_stock_threshold


class StockUnit(OriginalStateMixin, models.Model):
    """One physical unit of a StockItem with its own rolled stats."""

    class Status(models.TextChoices):
        AVAILABLE = "AVAILABLE", "Available"
        ASSIGNED  = "ASSIGNED",  "Assigned"
        RETURNED  = "RETURNED",  "Returned"
        LOST      = "LOST",      "Lost"
        RETIRED   = "RETIRED",   "Retired"

    item        = models.ForeignKey(StockItem, on_delete=models.CASCADE, related_name="units")
    # Stat roll — same JSON pattern as TrackedCraftedWeapon.stats
    # e.g. {"Impact Force": "+24.09%", "Recoil Kick": "-38.72%"}
    stats       = models.JSONField(default=dict, blank=True)
    status      = models.CharField(max_length=12, choices=Status.choices,
                                   default=Status.AVAILABLE, db_index=True)
    condition_notes = models.TextField(blank=True, default="")

    # Who currently holds this unit
    current_holder = models.ForeignKey(
        "unifieduser.OrgPlayer", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="held_stock_units",
    )

    added_by    = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="added_stock_units",
    )
    added_at    = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    history = HistoricalRecords()
    objects = SignalEmittingManager()

    class Meta:
        default_permissions = ()
        ordering = ["item__name", "-added_at"]

    def __str__(self):
        stats_summary = ", ".join(f"{k}: {v}" for k, v in list(self.stats.items())[:2])
        return f"{self.item.name} #{self.pk} [{self.get_status_display()}] {stats_summary}"


class StockAssignment(models.Model):
    """Immutable log entry — one assignment or return event."""

    class Action(models.TextChoices):
        ASSIGNED = "ASSIGNED", "Assigned"
        RETURNED = "RETURNED", "Returned"
        LOST     = "LOST",     "Lost"

    unit        = models.ForeignKey(StockUnit, on_delete=models.CASCADE,
                                    related_name="assignment_log")
    action      = models.CharField(max_length=10, choices=Action.choices, db_index=True)

    recipient   = models.ForeignKey(
        "unifieduser.OrgPlayer", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="stock_assignment_log",
    )
    # Free-text fallback for recipients not yet in the backend
    recipient_name = models.CharField(max_length=150, blank=True, default="")

    notes       = models.TextField(blank=True, default="")
    actioned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="stock_actions_made",
    )
    actioned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        ordering = ["-actioned_at"]

    def __str__(self):
        who = self.recipient.display_name if self.recipient_id else self.recipient_name or "Unknown"
        return f"{self.action} {self.unit.item.name} #{self.unit_id} → {who}"

