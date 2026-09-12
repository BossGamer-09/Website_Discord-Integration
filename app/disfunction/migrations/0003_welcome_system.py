"""
disfunction/migrations/0003_welcome_system.py

State-only migration: registers UserJoinRecord under the disfunction app.
Table already exists as orm_models_userjoinrecord — no DDL executed.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("disfunction", "0002_nomination_system"),
        ("org",         "0005_userjoinrecord"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.CreateModel(
                    name="UserJoinRecord",
                    fields=[
                        ("id",                      models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("discord_id",              models.BigIntegerField(db_index=True, verbose_name="Discord ID")),
                        ("username",                models.CharField(max_length=100, verbose_name="Username")),
                        ("global_name",             models.CharField(blank=True, max_length=100, null=True, verbose_name="Global Name")),
                        ("discriminator",           models.CharField(blank=True, max_length=4, null=True, verbose_name="Discriminator")),
                        ("display_name",            models.CharField(blank=True, max_length=100, null=True, verbose_name="Server Display Name")),
                        ("avatar_url",              models.URLField(blank=True, max_length=500, null=True, verbose_name="Avatar URL")),
                        ("avatar_hash",             models.CharField(blank=True, max_length=100, null=True, verbose_name="Avatar Hash")),
                        ("is_active",               models.BooleanField(db_index=True, default=True, verbose_name="Active Member")),
                        ("membership_status",       models.CharField(
                            choices=[
                                ("ACTIVE",   "Active Member"),
                                ("LEFT",     "Voluntarily Left"),
                                ("KICKED",   "Kicked from Server"),
                                ("BANNED",   "Banned from Server"),
                                ("INACTIVE", "Inactive (Auto-removed)"),
                            ],
                            default="ACTIVE", max_length=20, verbose_name="Membership Status",
                        )),
                        ("first_seen",              models.DateTimeField(blank=True, null=True, verbose_name="First Seen")),
                        ("joined_at",               models.DateTimeField(auto_now_add=True, verbose_name="Join Timestamp")),
                        ("left_at",                 models.DateTimeField(blank=True, null=True, verbose_name="Left At")),
                        ("last_seen",               models.DateTimeField(auto_now=True, verbose_name="Last Seen")),
                        ("last_updated",            models.DateTimeField(auto_now=True, verbose_name="Last Updated")),
                        ("join_count",              models.PositiveIntegerField(default=1, verbose_name="Join Count")),
                        ("total_duration",          models.DurationField(blank=True, null=True, verbose_name="Total Duration")),
                        ("current_streak_start",    models.DateTimeField(blank=True, null=True, verbose_name="Current Streak Start")),
                        ("longest_streak",          models.DurationField(blank=True, null=True, verbose_name="Longest Streak")),
                        ("message_count",           models.PositiveIntegerField(default=0, verbose_name="Message Count")),
                        ("last_message_at",         models.DateTimeField(blank=True, null=True, verbose_name="Last Message")),
                        ("welcome_message_id",      models.BigIntegerField(blank=True, null=True, verbose_name="Welcome Message ID")),
                        ("join_log_message_id",     models.BigIntegerField(blank=True, null=True, verbose_name="Join Log Message ID")),
                        ("welcome_image_generated", models.BooleanField(default=False, verbose_name="Welcome Image Generated")),
                        ("welcome_image_path",      models.CharField(blank=True, max_length=500, null=True, verbose_name="Welcome Image Path")),
                        ("flags",                   models.JSONField(default=dict, verbose_name="User Flags")),
                        ("notes",                   models.TextField(blank=True, null=True, verbose_name="Admin Notes")),
                    ],
                    options={
                        "verbose_name": "User Join Record",
                        "verbose_name_plural": "User Join Records",
                        "db_table": "orm_models_userjoinrecord",
                        "ordering": ["-joined_at"],
                    },
                ),
            ],
        ),
    ]
