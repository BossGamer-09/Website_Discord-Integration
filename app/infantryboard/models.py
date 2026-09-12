"""
app/infantryboard/models.py

Infantry roster management system.
Tracks soldiers' skill ratings (aim, teamplay, comms, tactics, leadership),
categorical tags, general notes, and goal tracking with per-goal notes.
"""
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.text import slugify
from simple_history.models import HistoricalRecords

from app.main.util.db import SignalEmittingManager


# ---------------------------------------------------------------------------
# Tag constants
# ---------------------------------------------------------------------------

class InfantryTag(models.TextChoices):
    ACTIVE_DUTY  = "active_duty",    "Active Duty"
    RESERVE      = "reserve",        "Reserve"
    TRIAL        = "trial",          "Trial"
    INACTIVE     = "inactive",       "Inactive"
    LEADERSHIP   = "leadership_tag", "Leadership"
    VETERAN      = "veteran",        "Veteran"


# ---------------------------------------------------------------------------
# Soldier
# ---------------------------------------------------------------------------

class Soldier(models.Model):
    """A tracked infantry member with skill ratings, tags, notes, and goals."""

    name      = models.CharField(max_length=255, unique=True)
    slug      = models.SlugField(max_length=255, unique=True, blank=True)
    is_active = models.BooleanField(default=True)

    # Skills 0–5
    aim        = models.PositiveSmallIntegerField(
        default=0, validators=[MinValueValidator(0), MaxValueValidator(5)],
    )
    teamplay   = models.PositiveSmallIntegerField(
        default=0, validators=[MinValueValidator(0), MaxValueValidator(5)],
    )
    comms      = models.PositiveSmallIntegerField(
        default=0, validators=[MinValueValidator(0), MaxValueValidator(5)],
    )
    tactics    = models.PositiveSmallIntegerField(
        default=0, validators=[MinValueValidator(0), MaxValueValidator(5)],
    )
    leadership = models.PositiveSmallIntegerField(
        default=0, validators=[MinValueValidator(0), MaxValueValidator(5)],
    )

    # Stored as a JSON list of InfantryTag keys, e.g. ["active_duty", "trial"]
    tags = models.JSONField(default=list, blank=True)

    org_player = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="infantry_profile",
    )
    notes            = models.TextField(blank=True, default="")
    notes_updated_at = models.DateTimeField(null=True, blank=True)
    updated_by       = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(default=timezone.now)

    objects = SignalEmittingManager()
    history = HistoricalRecords()

    class Meta:
        default_permissions = ()
        ordering = ["name"]
        permissions = [
            ("view_infantry_roster",   "Can view the infantry roster"),
            ("manage_infantry_roster", "Can add/edit/delete soldiers and goals"),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug or (kwargs.get("update_fields") and "name" in kwargs["update_fields"]):
            base = slugify(self.name) or "soldier"
            slug, n = base, 2
            while Soldier.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base}-{n}"
                n += 1
            self.slug = slug
            if "update_fields" in kwargs and "slug" not in kwargs["update_fields"]:
                kwargs["update_fields"] = list(kwargs["update_fields"]) + ["slug"]
        super().save(*args, **kwargs)

    @property
    def open_goal(self):
        return self.goals.filter(completed=False).order_by("created_at").first()

    @property
    def skill_total(self):
        return self.aim + self.teamplay + self.comms + self.tactics + self.leadership


# ---------------------------------------------------------------------------
# SoldierGoal
# ---------------------------------------------------------------------------

class SoldierGoal(models.Model):
    """A training / improvement goal attached to a soldier. One open at a time."""

    soldier    = models.ForeignKey(Soldier, on_delete=models.CASCADE, related_name="goals")
    title      = models.CharField(max_length=500)
    completed  = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    history = HistoricalRecords()

    class Meta:
        default_permissions = ()
        ordering = ["created_at"]

    def __str__(self):
        status = "✓" if self.completed else "○"
        return f"[{status}] {self.soldier.name}: {self.title}"


# ---------------------------------------------------------------------------
# GoalNote
# ---------------------------------------------------------------------------

class GoalNote(models.Model):
    """A threaded note on a specific goal, authored by a staff member."""

    goal      = models.ForeignKey(SoldierGoal, on_delete=models.CASCADE, related_name="notes")
    content   = models.TextField()
    author    = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        default_permissions = ()
        ordering = ["created_at"]

    def __str__(self):
        author = self.author.display_name if self.author else "unknown"
        return f"{author} on goal #{self.goal_id}: {self.content[:40]}"


# ---------------------------------------------------------------------------
# InfantryGroupFilter
# ---------------------------------------------------------------------------

class InfantryGroupFilter(models.Model):
    """Admin-configurable list of Django Groups shown as filter buttons on the roster."""

    group = models.OneToOneField(
        "auth.Group",
        on_delete=models.CASCADE,
        related_name="+",
    )
    label = models.CharField(
        max_length=100,
        blank=True,
        help_text="Override display label (leave blank to use group name)",
    )
    order = models.PositiveSmallIntegerField(default=0, help_text="Display order (lower = first)")

    class Meta:
        default_permissions = ()
        ordering = ["order", "group__name"]
        verbose_name = "Roster Group Filter"
        verbose_name_plural = "Roster Group Filters"

    def __str__(self):
        return self.label or self.group.name

    @property
    def display_label(self):
        return self.label or self.group.name
