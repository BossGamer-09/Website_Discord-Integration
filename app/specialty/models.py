"""
app/specialty/models.py

Tracks member specialty grants and pending approval requests.
Specialty data (role IDs, labels, categories) lives in preferences/config;
these models only record history and pending work.
"""
from django.db import models
from django.conf import settings
from django.utils import timezone
from simple_history.models import HistoricalRecords


class SpecialtyCategory(models.TextChoices):
    PILOT     = "pilot",     "Pilot"
    INFANTRY  = "infantry",  "Infantry"
    CREWMAN   = "crewman",   "Crewman"
    TRADESMAN = "tradesman", "Support"


class SpecialtyGrant(models.Model):
    """A record of a specialty grant request, its approval/denial, and resulting roles."""

    class Status(models.TextChoices):
        PENDING  = "PENDING",  "Pending"
        APPROVED = "APPROVED", "Approved"
        DENIED   = "DENIED",   "Denied"

    # Who is receiving the specialty
    recipient_discord_id = models.BigIntegerField(db_index=True)

    # Who submitted the grant request
    requested_by_discord_id = models.BigIntegerField()

    # Who approved/denied (null until resolved)
    resolved_by_discord_id = models.BigIntegerField(null=True, blank=True)

    # Specialty key (e.g. "pilot_brawler", "infantry_lrs")
    specialty_key   = models.CharField(max_length=60, db_index=True)
    specialty_label = models.CharField(max_length=80)
    category        = models.CharField(max_length=20, choices=SpecialtyCategory.choices, db_index=True)

    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True
    )

    # Discord message IDs for the approval embed — used to update the embed on resolution
    approval_channel_message_id = models.BigIntegerField(null=True, blank=True)

    requested_at = models.DateTimeField(default=timezone.now)
    resolved_at  = models.DateTimeField(null=True, blank=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["-requested_at"]

    def __str__(self):
        return f"SpecialtyGrant({self.specialty_key}, recipient={self.recipient_discord_id}, {self.status})"
