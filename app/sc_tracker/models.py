from django.db import models
from django.utils import timezone


# ── RSI Status Models ─────────────────────────────────────────────────────────

class RSIIssue(models.Model):
    """
    Cached RSI status issue, fetched by Celery and read by both bot and web.

    The Celery task (tasks/rsi_status.py) is the single writer.
    The bot cog and Django view both read from here — no redundant API calls.
    """
    filename     = models.CharField(max_length=255, unique=True, db_index=True)
    discord_id   = models.BigIntegerField(null=True, blank=True)  # Discord message ID
    title        = models.TextField()
    affected     = models.JSONField(default=list)
    severity     = models.CharField(max_length=50)
    permalink    = models.TextField()
    kind         = models.CharField(max_length=50)

    # Content
    markdown_content = models.TextField(blank=True, default="")

    # Status
    resolved     = models.BooleanField(default=False, db_index=True)
    informational = models.BooleanField(default=False)

    # Timestamps (from RSI API)
    rsi_created_at  = models.DateTimeField(null=True, blank=True)
    rsi_resolved_at = models.DateTimeField(null=True, blank=True)
    rsi_lastmod_at  = models.DateTimeField(null=True, blank=True)

    # Our timestamps
    first_seen  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()
        app_label = "sc_tracker"
        db_table  = "sc_tracker_rsi_issue"
        verbose_name = "RSI Issue"
        verbose_name_plural = "RSI Issues"
        indexes = [
            models.Index(fields=["resolved", "updated_at"]),
            models.Index(fields=["severity"]),
        ]

    def __str__(self):
        return f"{self.title} ({'Resolved' if self.resolved else 'Active'})"


class RSIStatusSnapshot(models.Model):
    """
    Latest full JSON snapshot from status.robertsspaceindustries.com.

    Celery writes one row (upsert on id=1).  Both the Django view and the bot
    cog read from here — eliminates duplicate RSI API calls.
    """
    id          = models.IntegerField(primary_key=True, default=1)
    raw_json    = models.JSONField(default=dict)
    fetched_at  = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()
        app_label = "sc_tracker"
        db_table  = "sc_tracker_rsi_snapshot"
        verbose_name = "RSI Status Snapshot"

    def __str__(self):
        return f"RSI Snapshot @ {self.fetched_at}"

    @classmethod
    async def get_latest(cls):
        return await cls.objects.filter(id=1).afirst()

    @classmethod
    async def upsert(cls, data: dict):
        obj, _ = await cls.objects.aupdate_or_create(
            id=1,
            defaults={"raw_json": data},
        )
        return obj


# ── Status Subscription / DM Models ──────────────────────────────────────────

class StatusSubscription(models.Model):
    """
    A user's subscription to RSI status DM alerts.

    discord_user_id is stored as BigIntegerField (project standard for snowflakes).
    Permission to subscribe is gated by Django Permission: sc_tracker.can_subscribe_status
    """
    discord_user_id = models.BigIntegerField(unique=True, db_index=True)
    is_active       = models.BooleanField(default=True, db_index=True)
    created_at      = models.DateTimeField(auto_now_add=True)
    last_notified   = models.DateTimeField(null=True, blank=True)

    class Meta:
        default_permissions = ()
        app_label = "sc_tracker"
        db_table  = "sc_tracker_status_subscription"
        verbose_name = "Status Subscription"
        verbose_name_plural = "Status Subscriptions"
        permissions = [
            ("can_subscribe_status", "Can subscribe to RSI status DM alerts"),
        ]

    def __str__(self):
        return f"Subscription {self.discord_user_id} ({'Active' if self.is_active else 'Inactive'})"


class StatusDMMessage(models.Model):
    """Tracks every DM we've sent so we can deduplicate and clean up."""
    MESSAGE_TYPES = [
        ("confirmation", "Confirmation"),
        ("update",       "Incident Update"),
        ("unsubscribe",  "Unsubscribe"),
        ("test",         "Test"),
    ]

    discord_user_id = models.BigIntegerField(db_index=True)
    dm_message_id   = models.BigIntegerField()          # Discord message snowflake
    incident_guid   = models.CharField(max_length=255, null=True, blank=True, db_index=True)
    incident_title  = models.TextField(null=True, blank=True)
    message_type    = models.CharField(max_length=20, choices=MESSAGE_TYPES)
    created_at      = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        default_permissions = ()
        app_label = "sc_tracker"
        db_table  = "sc_tracker_status_dm_message"
        verbose_name = "Status DM Message"
        verbose_name_plural = "Status DM Messages"
        indexes = [
            models.Index(fields=["discord_user_id", "message_type"]),
            models.Index(fields=["incident_guid", "created_at"]),
        ]

    def __str__(self):
        return f"DM {self.dm_message_id} → {self.discord_user_id} ({self.message_type})"


class StatusChannelMessage(models.Model):
    """
    Tracks the single live status-embed Discord message per channel.
    Replaces the old StatusMessage model with BigIntegerField snowflakes.
    """
    channel_id      = models.BigIntegerField(unique=True, db_index=True)
    message_id      = models.BigIntegerField()
    message_type    = models.CharField(max_length=50, default="status_embed")
    last_updated    = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()
        app_label = "sc_tracker"
        db_table  = "sc_tracker_status_channel_message"
        verbose_name = "Status Channel Message"

    def __str__(self):
        return f"Status embed {self.message_id} in channel {self.channel_id}"


# ── Configuration ─────────────────────────────────────────────────────────────

class SCTrackerConfig(models.Model):
    """
    Singleton configuration — edit via Django admin SC_TRACKER section.
    No env vars needed for channel/role IDs.
    """
    id = models.IntegerField(primary_key=True, default=1)

    # ── Channel IDs ───────────────────────────────────────────────────────────
    hangar_channel_id = models.BigIntegerField(
        default=0,
        help_text="Discord channel ID where the Executive Hangar timer embed is posted."
    )
    status_channel_id = models.BigIntegerField(
        default=0,
        help_text="Discord channel ID where the RSI Status embed is posted."
    )
    updates_channel_id = models.BigIntegerField(
        default=0,
        help_text="Discord channel ID where active incident embeds are posted (Star Citizen Updates)."
    )

    # ── Role IDs ──────────────────────────────────────────────────────────────
    hangar_phase_role_id = models.BigIntegerField(
        default=0,
        help_text="Role pinged on GREEN ↔ RED phase transitions. 0 = no ping."
    )
    hangar_light_role_id = models.BigIntegerField(
        default=0,
        help_text="Role pinged on significant light changes. 0 = no ping."
    )

    # ── Hangar Timing (updated after each SC patch) ───────────────────────────
    hangar_open_ms = models.BigIntegerField(
        default=3_900_338,
        help_text="OPEN_DURATION in ms from exec.xyxyll.com/app.js"
    )
    hangar_close_ms = models.BigIntegerField(
        default=7_200_623,
        help_text="CLOSE_DURATION in ms from exec.xyxyll.com/app.js"
    )
    hangar_t0_ms = models.BigIntegerField(
        default=1_774_501_916_500,
        help_text="INITIAL_OPEN_TIME as Unix ms from exec.xyxyll.com/app.js. Update after every SC patch."
    )
    hangar_patch_info = models.CharField(
        max_length=255,
        default="Updated Mar 27, 2026 for Star Citizen Patch 4.7.0-LIVE (Server Version 11518367)",
        help_text="Human-readable patch label shown in embeds."
    )

    # ── Feature Flags ─────────────────────────────────────────────────────────
    hangar_enabled = models.BooleanField(default=True)
    status_enabled = models.BooleanField(default=True)

    class Meta:
        default_permissions = ()
        app_label = "sc_tracker"
        db_table  = "sc_tracker_config"
        verbose_name = "SC Tracker Configuration"
        verbose_name_plural = "SC Tracker Configuration"
        constraints = [
            models.CheckConstraint(
                name="sc_tracker_single_config_record",
                condition=models.Q(id=1),
            )
        ]

    def __str__(self):
        return "SC Tracker Configuration"

    @classmethod
    def get(cls):
        """Always returns the singleton, creating it with defaults if missing."""
        obj, _ = cls.objects.get_or_create(id=1)
        return obj


# ── Hangar Models ─────────────────────────────────────────────────────────────

class HangarStatusMessage(models.Model):
    """
    Single-row table (id=1) tracking the live hangar-timer Discord embed.
    """
    id                    = models.IntegerField(primary_key=True, default=1)
    message_id                   = models.BigIntegerField(null=True, blank=True)
    phase_notification_message_id = models.BigIntegerField(null=True, blank=True)
    last_phase_change            = models.DateTimeField(null=True, blank=True)
    last_light_change     = models.DateTimeField(null=True, blank=True)
    notification_count    = models.IntegerField(default=0)
    last_website_sync     = models.DateTimeField(null=True, blank=True)
    website_sync_attempts = models.IntegerField(default=0)
    last_updated          = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()
        app_label = "sc_tracker"
        db_table  = "sc_tracker_hangar_status_message"
        verbose_name = "Hangar Status Message"
        constraints = [
            models.CheckConstraint(
                name="sc_tracker_single_hangar_status_record",
                condition=models.Q(id=1),
            )
        ]

    def __str__(self):
        return f"Hangar embed: {self.message_id or 'none'}"

    @classmethod
    async def get_singleton(cls):
        obj, _ = await cls.objects.aget_or_create(id=1)
        return obj