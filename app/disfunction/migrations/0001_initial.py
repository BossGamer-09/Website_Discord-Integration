"""
disfunction/migrations/0001_initial.py

Steps:
  1. Real DDL — CREATE TABLE for VoiceChannelProfile (brand-new).
  2. State-only — register all models moved from orm_models so Django
     tracks them under the disfunction app without touching the DB tables.
  3. Real DDL — ADD COLUMN profile_slug on orm_models_temporaryvoicechannel
     (new field not present in the legacy table).
"""
import django.db.models.deletion
import simple_history.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = []

    operations = [
        # ------------------------------------------------------------------ #
        # 1. Real DDL — VoiceChannelProfile is a brand-new table.            #
        # ------------------------------------------------------------------ #
        migrations.CreateModel(
            name="VoiceChannelProfile",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "name",
                    models.CharField(
                        help_text="Display name, e.g. 'Knights VC'",
                        max_length=100,
                    ),
                ),
                (
                    "slug",
                    models.SlugField(
                        help_text="Internal key, e.g. 'knights'",
                        unique=True,
                    ),
                ),
                (
                    "channel_class",
                    models.CharField(
                        choices=[
                            ("PUBLIC",    "Public"),
                            ("PROTECTED", "Protected (Legion/Aux)"),
                            ("STAFF",     "Staff"),
                            ("LEADER",    "Leader"),
                            ("CUSTOM",    "Custom"),
                        ],
                        default="PUBLIC",
                        max_length=20,
                    ),
                ),
                (
                    "prefix",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="Channel name prefix, e.g. '🔴BVK VC '",
                        max_length=50,
                    ),
                ),
                (
                    "required_permission",
                    models.CharField(
                        help_text=(
                            "Django permission codename checked on the member, "
                            "e.g. 'disfunction.create_knights_vc'"
                        ),
                        max_length=200,
                    ),
                ),
                (
                    "apply_voice_ban",
                    models.BooleanField(
                        default=False,
                        help_text=(
                            "Whether active RED voice bans are applied to "
                            "channels of this type"
                        ),
                    ),
                ),
                (
                    "show_red_warning",
                    models.BooleanField(
                        default=False,
                        help_text=(
                            "Whether joining members receive a RED-channel DM warning"
                        ),
                    ),
                ),
                (
                    "allow_type_switch",
                    models.BooleanField(
                        default=True,
                        help_text=(
                            "Whether the channel owner can switch this channel "
                            "to a different profile type"
                        ),
                    ),
                ),
                (
                    "allow_knights_controls",
                    models.BooleanField(
                        default=False,
                        help_text=(
                            "Whether to post the Knights ban/unban control panel in the VC"
                        ),
                    ),
                ),
                (
                    "sort_order",
                    models.PositiveSmallIntegerField(
                        default=50,
                        help_text=(
                            "Lower = higher priority. Evaluated ascending when picking a profile."
                        ),
                    ),
                ),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={
                "verbose_name": "Voice Channel Profile",
                "verbose_name_plural": "Voice Channel Profiles",
                "ordering": ["sort_order"],
            },
        ),

        # ------------------------------------------------------------------ #
        # 2. State-only — register models that already live in the DB under   #
        #    orm_models_* table names.  No database_operations means Django    #
        #    will not touch any tables; it only updates its own state graph.   #
        # ------------------------------------------------------------------ #
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[

                # ---- TemporaryVoiceChannel -------------------------------- #
                migrations.CreateModel(
                    name="TemporaryVoiceChannel",
                    fields=[
                        (
                            "channel_id",
                            models.BigIntegerField(primary_key=True, serialize=False),
                        ),
                        ("guild_id",          models.BigIntegerField(db_index=True)),
                        ("parent_channel_id", models.BigIntegerField(db_index=True)),
                        ("creator_id",        models.BigIntegerField()),
                        ("name",              models.CharField(max_length=100)),
                        (
                            "channel_type",
                            models.CharField(
                                choices=[
                                    ("PUBLIC",    "Public Channel"),
                                    ("PROTECTED", "Protected Channel"),
                                    ("STAFF",     "Staff Channel"),
                                    ("LEADER",    "Leader Channel"),
                                    ("BLUE",      "Blue Team Channel"),
                                    ("RED",       "Red Team Channel"),
                                ],
                                db_index=True,
                                default="PUBLIC",
                                max_length=10,
                            ),
                        ),
                        ("user_limit",          models.IntegerField(default=0)),
                        ("bitrate",             models.IntegerField(default=64000)),
                        ("region",              models.CharField(default="automatic", max_length=50)),
                        ("control_message_id",  models.BigIntegerField(blank=True, null=True)),
                        ("is_paused",           models.BooleanField(default=False)),
                        ("pause_until",         models.DateTimeField(blank=True, null=True)),
                        ("is_active",           models.BooleanField(db_index=True, default=True)),
                        ("created_at",          models.DateTimeField(auto_now_add=True)),
                        ("last_updated",        models.DateTimeField(auto_now=True)),
                    ],
                    options={
                        "verbose_name": "Temporary Voice Channel",
                        "verbose_name_plural": "Temporary Voice Channels",
                        "ordering": ["-created_at"],
                        "db_table": "orm_models_temporaryvoicechannel",
                        "indexes": [
                            models.Index(fields=["guild_id", "is_active"],      name="orm_models__guild_i_683e79_idx"),
                            models.Index(fields=["creator_id"],                 name="orm_models__creator_6c88ec_idx"),
                            models.Index(fields=["parent_channel_id"],          name="orm_models__parent__3bd712_idx"),
                            models.Index(fields=["channel_type", "is_active"],  name="orm_models__channel_0eaa06_idx"),
                        ],
                    },
                ),

                # ---- VoiceBan --------------------------------------------- #
                migrations.CreateModel(
                    name="VoiceBan",
                    fields=[
                        (
                            "id",
                            models.BigAutoField(
                                auto_created=True,
                                primary_key=True,
                                serialize=False,
                                verbose_name="ID",
                            ),
                        ),
                        ("user_id",                 models.BigIntegerField(db_index=True)),
                        ("invoked_at",              models.DateTimeField(auto_now_add=True)),
                        ("expires_at",              models.DateTimeField(blank=True, null=True)),
                        ("reason",                  models.TextField(blank=True, null=True)),
                        ("comment",                 models.TextField(blank=True, null=True)),
                        (
                            "kind",
                            models.CharField(
                                choices=[
                                    ("REDC", "All Red / Protected Channels"),
                                    ("ONEC", "A Specific Channel"),
                                ],
                                db_index=True,
                                default="REDC",
                                max_length=4,
                            ),
                        ),
                        ("kind_specific_metadata",  models.JSONField(default=dict)),
                    ],
                    options={
                        "db_table": "orm_models_voiceban",
                    },
                ),

                # ---- HistoricalVoiceBan ------------------------------------ #
                migrations.CreateModel(
                    name="HistoricalVoiceBan",
                    fields=[
                        (
                            "id",
                            models.BigIntegerField(
                                auto_created=True,
                                blank=True,
                                db_index=True,
                                verbose_name="ID",
                            ),
                        ),
                        ("user_id",                models.BigIntegerField(db_index=True)),
                        ("invoked_at",             models.DateTimeField(blank=True, editable=False)),
                        ("expires_at",             models.DateTimeField(blank=True, null=True)),
                        ("reason",                 models.TextField(blank=True, null=True)),
                        ("comment",                models.TextField(blank=True, null=True)),
                        (
                            "kind",
                            models.CharField(
                                choices=[
                                    ("REDC", "All Red / Protected Channels"),
                                    ("ONEC", "A Specific Channel"),
                                ],
                                db_index=True,
                                default="REDC",
                                max_length=4,
                            ),
                        ),
                        ("kind_specific_metadata", models.JSONField(default=dict)),
                        ("history_user_id",        models.BigIntegerField(null=True)),
                        ("history_id",             models.AutoField(primary_key=True, serialize=False)),
                        ("history_date",           models.DateTimeField(db_index=True)),
                        ("history_change_reason",  models.CharField(max_length=100, null=True)),
                        (
                            "history_type",
                            models.CharField(
                                choices=[("+", "Created"), ("~", "Changed"), ("-", "Deleted")],
                                max_length=1,
                            ),
                        ),
                    ],
                    options={
                        "verbose_name": "historical voice ban",
                        "verbose_name_plural": "historical voice bans",
                        "ordering": ("-history_date", "-history_id"),
                        "get_latest_by": ("history_date", "history_id"),
                        "db_table": "orm_models_historicalvoiceban",
                    },
                    bases=(simple_history.models.HistoricalChanges, models.Model),
                ),

                # ---- VoiceSession ----------------------------------------- #
                migrations.CreateModel(
                    name="VoiceSession",
                    fields=[
                        (
                            "id",
                            models.BigAutoField(
                                auto_created=True,
                                primary_key=True,
                                serialize=False,
                                verbose_name="ID",
                            ),
                        ),
                        ("user_id",              models.BigIntegerField(db_index=True)),
                        ("guild_id",             models.BigIntegerField(db_index=True)),
                        ("channel_id",           models.BigIntegerField(db_index=True)),
                        ("channel_name",         models.CharField(blank=True, max_length=200, null=True)),
                        ("channel_type",         models.CharField(blank=True, max_length=50, null=True)),
                        ("join_time",            models.DateTimeField()),
                        ("leave_time",           models.DateTimeField(blank=True, null=True)),
                        ("was_muted",            models.BooleanField(default=False)),
                        ("was_deafened",         models.BooleanField(default=False)),
                        ("was_streaming",        models.BooleanField(default=False)),
                        ("was_video",            models.BooleanField(default=False)),
                        ("was_afk",              models.BooleanField(default=False)),
                        ("duration_seconds",     models.IntegerField(blank=True, null=True)),
                        ("talking_time_seconds", models.IntegerField(default=0)),
                        ("talking_percentage",   models.FloatField(default=0.0)),
                        (
                            "session_quality",
                            models.CharField(
                                choices=[
                                    ("EXCELLENT", "Excellent (>80% talking)"),
                                    ("GOOD",      "Good (50-80% talking)"),
                                    ("FAIR",      "Fair (20-50% talking)"),
                                    ("POOR",      "Poor (<20% talking)"),
                                    ("INACTIVE",  "Inactive (muted/deafened/afk)"),
                                ],
                                default="FAIR",
                                max_length=20,
                            ),
                        ),
                        ("ended_normally",    models.BooleanField(default=True)),
                        ("disconnect_reason", models.CharField(blank=True, max_length=100, null=True)),
                        ("device_info",       models.JSONField(blank=True, default=dict, null=True)),
                        ("session_restored",  models.BooleanField(default=False)),
                        ("checkpoint_id",     models.CharField(blank=True, max_length=100, null=True)),
                    ],
                    options={
                        "ordering": ["-join_time"],
                        "db_table": "orm_models_voicesession",
                        "indexes": [
                            models.Index(fields=["user_id", "join_time"],   name="orm_models__user_id_0b56bb_idx"),
                            models.Index(fields=["guild_id", "channel_id"], name="orm_models__guild_i_255968_idx"),
                            models.Index(fields=["join_time", "leave_time"],name="orm_models__join_ti_3afbcc_idx"),
                            models.Index(fields=["checkpoint_id"],          name="orm_models__checkpo_e86b9b_idx"),
                        ],
                        "constraints": [
                            models.CheckConstraint(name="positive_duration",        condition=models.Q(duration_seconds__gte=0)),
                            models.CheckConstraint(name="positive_talking_time",    condition=models.Q(talking_time_seconds__gte=0)),
                            models.CheckConstraint(name="valid_talking_percentage", condition=models.Q(talking_percentage__gte=0) & models.Q(talking_percentage__lte=100)),
                        ],
                    },
                ),

                # ---- VoiceSessionCheckpoint -------------------------------- #
                migrations.CreateModel(
                    name="VoiceSessionCheckpoint",
                    fields=[
                        (
                            "id",
                            models.BigAutoField(
                                auto_created=True,
                                primary_key=True,
                                serialize=False,
                                verbose_name="ID",
                            ),
                        ),
                        ("checkpoint_id",               models.CharField(db_index=True, max_length=100, unique=True)),
                        ("user_id",                     models.BigIntegerField(db_index=True)),
                        ("guild_id",                    models.BigIntegerField()),
                        ("channel_id",                  models.BigIntegerField()),
                        ("channel_name",                models.CharField(max_length=200)),
                        ("join_time",                   models.DateTimeField()),
                        ("last_update",                 models.DateTimeField(auto_now=True)),
                        ("is_muted",                    models.BooleanField(default=False)),
                        ("is_deafened",                 models.BooleanField(default=False)),
                        ("is_afk",                      models.BooleanField(default=False)),
                        ("is_streaming",                models.BooleanField(default=False)),
                        ("talking_start_time",          models.DateTimeField(blank=True, null=True)),
                        ("accumulated_talking_seconds", models.IntegerField(default=0)),
                        ("device_info",                 models.JSONField(default=dict)),
                        ("is_active",                   models.BooleanField(default=True)),
                        ("created_at",                  models.DateTimeField(auto_now_add=True)),
                    ],
                    options={
                        "ordering": ["-created_at"],
                        "db_table": "orm_models_voicesessioncheckpoint",
                        "indexes": [
                            models.Index(fields=["checkpoint_id", "is_active"], name="orm_models__checkpo_b722cb_idx"),
                            models.Index(fields=["user_id", "is_active"],       name="orm_models__user_id_8c0ca1_idx"),
                        ],
                    },
                ),

                # ---- VoiceActivitySummary ---------------------------------- #
                migrations.CreateModel(
                    name="VoiceActivitySummary",
                    fields=[
                        (
                            "id",
                            models.BigAutoField(
                                auto_created=True,
                                primary_key=True,
                                serialize=False,
                                verbose_name="ID",
                            ),
                        ),
                        ("user_id",     models.BigIntegerField(db_index=True)),
                        ("guild_id",    models.BigIntegerField(db_index=True)),
                        (
                            "period_type",
                            models.CharField(
                                choices=[
                                    ("DAILY",   "Daily"),
                                    ("WEEKLY",  "Weekly"),
                                    ("MONTHLY", "Monthly"),
                                    ("TOTAL",   "All Time"),
                                ],
                                default="DAILY",
                                max_length=10,
                            ),
                        ),
                        ("period_start",            models.DateTimeField()),
                        ("period_end",              models.DateTimeField()),
                        ("total_sessions",          models.IntegerField(default=0)),
                        ("total_duration_seconds",  models.IntegerField(default=0)),
                        ("total_talking_seconds",   models.IntegerField(default=0)),
                        ("unique_channels",         models.IntegerField(default=0)),
                        ("excellent_sessions",      models.IntegerField(default=0)),
                        ("good_sessions",           models.IntegerField(default=0)),
                        ("fair_sessions",           models.IntegerField(default=0)),
                        ("poor_sessions",           models.IntegerField(default=0)),
                        ("inactive_sessions",       models.IntegerField(default=0)),
                        ("avg_session_duration",    models.IntegerField(default=0)),
                        ("avg_talking_percentage",  models.FloatField(default=0.0)),
                        ("talking_efficiency",      models.FloatField(default=0.0)),
                        ("consistency_score",       models.FloatField(default=0.0)),
                        ("streak_days",             models.IntegerField(default=0)),
                    ],
                    options={
                        "db_table": "orm_models_voiceactivitysummary",
                        "unique_together": {("user_id", "guild_id", "period_type", "period_start")},
                        "indexes": [
                            models.Index(fields=["user_id", "period_type", "period_start"], name="orm_models__user_id_64068c_idx"),
                            models.Index(fields=["guild_id", "period_type", "period_end"],  name="orm_models__guild_i_1c9c57_idx"),
                        ],
                    },
                ),

                # ---- EventAttendance --------------------------------------- #
                migrations.CreateModel(
                    name="EventAttendance",
                    fields=[
                        (
                            "id",
                            models.BigAutoField(
                                auto_created=True,
                                primary_key=True,
                                serialize=False,
                                verbose_name="ID",
                            ),
                        ),
                        ("event_id",         models.CharField(db_index=True, max_length=100)),
                        ("event_name",       models.CharField(max_length=200)),
                        (
                            "event_type",
                            models.CharField(
                                choices=[
                                    ("ORGANIC",   "Organic Event"),
                                    ("SCHEDULED", "Scheduled Event"),
                                    ("TRAINING",  "Training Session"),
                                    ("MEETING",   "Organisation Meeting"),
                                    ("SOCIAL",    "Social Gathering"),
                                    ("OPERATION", "Military Operation"),
                                ],
                                max_length=20,
                            ),
                        ),
                        ("organizer_id",                  models.BigIntegerField()),
                        ("voice_channel_id",              models.BigIntegerField(blank=True, null=True)),
                        ("thread_id",                     models.BigIntegerField(blank=True, null=True)),
                        ("actual_start",                  models.DateTimeField()),
                        ("actual_end",                    models.DateTimeField(blank=True, null=True)),
                        ("scheduled_start",               models.DateTimeField(blank=True, null=True)),
                        ("total_participants",            models.IntegerField(default=0)),
                        ("average_attendance_duration",   models.IntegerField(default=0)),
                        ("max_concurrent_participants",   models.IntegerField(default=0)),
                        ("tags",                          models.JSONField(default=list)),
                        ("description",                   models.TextField(blank=True, null=True)),
                        ("event_checkpoint",              models.JSONField(default=dict)),
                        ("is_recovered",                  models.BooleanField(default=False)),
                    ],
                    options={
                        "ordering": ["-actual_start"],
                        "db_table": "orm_models_eventattendance",
                        "indexes": [
                            models.Index(fields=["event_id", "event_type"],     name="orm_models__event_i_2de412_idx"),
                            models.Index(fields=["organizer_id", "actual_start"],name="orm_models__organiz_4fadfd_idx"),
                            models.Index(fields=["voice_channel_id"],           name="orm_models__voice_c_e4e05a_idx"),
                        ],
                    },
                ),

                # ---- EventAttendanceRecord --------------------------------- #
                migrations.CreateModel(
                    name="EventAttendanceRecord",
                    fields=[
                        (
                            "id",
                            models.BigAutoField(
                                auto_created=True,
                                primary_key=True,
                                serialize=False,
                                verbose_name="ID",
                            ),
                        ),
                        (
                            "event",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="attendees",
                                to="disfunction.eventattendance",
                            ),
                        ),
                        ("user_id", models.BigIntegerField(db_index=True)),
                        (
                            "status",
                            models.CharField(
                                choices=[
                                    ("ACTIVE",      "Currently Active"),
                                    ("PRESENT",     "Present"),
                                    ("LATE",        "Arrived Late"),
                                    ("LEFT_EARLY",  "Left Early"),
                                    ("EXCUSED",     "Excused Absence"),
                                    ("ABSENT",      "Absent"),
                                ],
                                default="ACTIVE",
                                max_length=20,
                            ),
                        ),
                        ("join_time",  models.DateTimeField()),
                        ("leave_time", models.DateTimeField(blank=True, null=True)),
                        (
                            "voice_session",
                            models.ForeignKey(
                                blank=True,
                                null=True,
                                on_delete=django.db.models.deletion.SET_NULL,
                                to="disfunction.voicesession",
                            ),
                        ),
                        ("total_talking_seconds",  models.IntegerField(default=0)),
                        ("was_active_participant",  models.BooleanField(default=False)),
                        ("participation_score",     models.FloatField(default=0.0)),
                        ("was_muted",               models.BooleanField(default=False)),
                        ("was_deafened",            models.BooleanField(default=False)),
                        ("was_afk",                 models.BooleanField(default=False)),
                        ("verified_by",             models.BigIntegerField(blank=True, null=True)),
                        ("verification_notes",      models.TextField(blank=True, null=True)),
                        ("checkpoint_id",           models.CharField(blank=True, max_length=100, null=True)),
                        ("is_restored",             models.BooleanField(default=False)),
                    ],
                    options={
                        "db_table": "orm_models_eventattendancerecord",
                        "unique_together": {("event", "user_id")},
                        "indexes": [
                            models.Index(fields=["event", "user_id"],   name="orm_models__event_i_95240a_idx"),
                            models.Index(fields=["user_id", "join_time"],name="orm_models__user_id_bf7339_idx"),
                            models.Index(fields=["checkpoint_id"],       name="orm_models__checkpo_81f4f6_idx"),
                        ],
                        "constraints": [
                            models.CheckConstraint(
                                name="attendance_positive_talking",
                                condition=models.Q(total_talking_seconds__gte=0),
                            ),
                            models.CheckConstraint(
                                name="valid_participation_score",
                                condition=models.Q(participation_score__gte=0) & models.Q(participation_score__lte=100),
                            ),
                        ],
                    },
                ),
            ],
        ),

        # ------------------------------------------------------------------ #
        # 3. Real DDL — new profile_slug column on TemporaryVoiceChannel.    #
        # ------------------------------------------------------------------ #
        migrations.AddField(
            model_name="temporaryvoicechannel",
            name="profile_slug",
            field=models.CharField(
                blank=True,
                help_text="Slug of the VoiceChannelProfile that created this VC",
                max_length=50,
                null=True,
            ),
        ),
    ]
