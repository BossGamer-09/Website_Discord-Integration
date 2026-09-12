"""
app/merits/models.py

Merit & Money Request system.

Members submit requests for aUEC payouts or merit rewards linked to
inventory items or free-form reasons. Staff fulfill requests on the
website; the bot sends reminders and fulfillment notifications.
"""
from django.db import models
from django.conf import settings
from django.utils import timezone
from simple_history.models import HistoricalRecords


class MeritRequest(models.Model):
    class Kind(models.TextChoices):
        MONEY  = "MONEY",  "aUEC Payout"
        MERIT  = "MERIT",  "Merit Award"

    class Status(models.TextChoices):
        PENDING   = "PENDING",   "Pending"
        FULFILLED = "FULFILLED", "Fulfilled"
        DENIED    = "DENIED",    "Denied"

    # Who & what
    requester   = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="merit_requests",
    )
    kind        = models.CharField(max_length=10, choices=Kind.choices, db_index=True)
    title       = models.CharField(max_length=120, blank=True, help_text="Auto-generated from submission fields")
    reason      = models.TextField(blank=True, help_text="Auto-generated from submission fields")

    # Optional link to an inventory stock item
    linked_item = models.ForeignKey(
        "inventory.StockItem",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="merit_requests",
        help_text="Optionally link to an inventory item this relates to",
    )

    # Submitter's RSI handle
    rsi_handle    = models.CharField(max_length=60, blank=True, help_text="RSI handle of the requester")

    # MERIT-specific: was this earned during an org event?
    during_event  = models.BooleanField(null=True, blank=True, help_text="True=Yes, False=No, None=not applicable")

    # MONEY-specific: what are they buying?
    purchase_for  = models.CharField(max_length=200, blank=True, help_text="Ship or item the aUEC is being used for")

    # Amount — aUEC for MONEY, points for MERIT
    amount      = models.PositiveIntegerField(
        default=0,
        help_text="aUEC amount (MONEY) or merit points (MERIT). 0 = TBD by staff.",
    )

    status      = models.CharField(
        max_length=12, choices=Status.choices,
        default=Status.PENDING, db_index=True,
    )

    # Staff fields
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_merit_requests",
    )
    reviewer_note = models.TextField(blank=True)
    reviewed_at   = models.DateTimeField(null=True, blank=True)
    fulfilled_at  = models.DateTimeField(null=True, blank=True)

    submitted_at  = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)

    history = HistoricalRecords()

    class Meta:
        default_permissions = ()
        ordering = ["-submitted_at"]
        permissions = [
            ("submit_merit_request",  "Can submit merit/money requests"),
            ("review_merit_request",  "Can approve or deny merit/money requests"),
            ("fulfill_merit_request", "Can mark merit/money requests as fulfilled"),
            ("view_merit_requests",   "Can view all merit/money requests"),
        ]

    def __str__(self):
        return f"[{self.get_kind_display()}] {self.title} — {self.requester}"

    @property
    def status_color(self):
        return {
            self.Status.PENDING:   "amber",
            self.Status.FULFILLED: "green",
            self.Status.DENIED:    "red",
        }.get(self.status, "muted")
