import django.db.models.deletion
import django.utils.timezone
import simple_history.models
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = False

    dependencies = [
        ("candidacy", "0001_initial"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("unifieduser", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [

        # ------------------------------------------------------------------
        # ApplicationType
        # ------------------------------------------------------------------
        migrations.CreateModel(
            name="ApplicationType",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("kind", models.CharField(choices=[("STAFF", "Staff Position"), ("TRNG", "Training Program")], db_index=True, max_length=10)),
                ("name", models.CharField(max_length=100, unique=True)),
                ("description", models.TextField(blank=True)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("on_accept_disclaimer", models.TextField(blank=True)),
                ("question_1", models.CharField(blank=True, max_length=45, null=True)),
                ("question_2", models.CharField(blank=True, max_length=45, null=True)),
                ("question_3", models.CharField(blank=True, max_length=45, null=True)),
                ("question_4", models.CharField(blank=True, max_length=45, null=True)),
                ("question_5", models.CharField(blank=True, max_length=45, null=True)),
                ("on_accept_gives_permission_groups", models.ManyToManyField(blank=True, related_name="given_by_application_type", to="auth.group")),
            ],
            options={
                "permissions": [
                    ("can_review_application_type", "Can review applications by type"),
                    ("can_submit_application_type", "Can submit applications by type"),
                ],
            },
        ),

        # ------------------------------------------------------------------
        # ApplicationRecord
        # ------------------------------------------------------------------
        migrations.CreateModel(
            name="ApplicationRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("status", models.CharField(choices=[("PENDING", "Pending Review"), ("APPROVED", "Approved"), ("DENIED", "Denied")], db_index=True, default="PENDING", max_length=20)),
                ("thread_id", models.BigIntegerField(help_text="Discord Thread ID for this application", unique=True)),
                ("answer_1", models.TextField(blank=True, null=True)),
                ("answer_2", models.TextField(blank=True, null=True)),
                ("answer_3", models.TextField(blank=True, null=True)),
                ("answer_4", models.TextField(blank=True, null=True)),
                ("answer_5", models.TextField(blank=True, null=True)),
                ("submitted_at", models.DateTimeField(auto_now_add=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("applicant", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="applications", to=settings.AUTH_USER_MODEL)),
                ("app_type", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="records", to="candidacy.applicationtype")),
                ("reviewer", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reviewed_apps", to="unifieduser.orgplayer")),
            ],
            options={
                "ordering": ["-submitted_at"],
                "permissions": [
                    ("can_review_application", "Can review applications"),
                    ("can_submit_application", "Can submit applications"),
                ],
            },
        ),

        # ------------------------------------------------------------------
        # MembershipApplicationType
        # ------------------------------------------------------------------
        migrations.CreateModel(
            name="MembershipApplicationType",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=100, unique=True)),
                ("description", models.TextField(blank=True)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("on_accept_trial_rank_pk", models.PositiveIntegerField(blank=True, null=True, help_text="PK of the OrgRank granted on approval. Find on the OrgRank admin list.")),
                ("on_accept_disclaimer", models.TextField(blank=True, default="## ✅ Application Approved\n\nWelcome, {applicant}!\n\nYou are now a **{rank_prefix} {rank_name}**.\n\nHead to {discipline_channel} and choose your discipline(s).\n\n*This thread will be archived in 24 hours.*")),
                ("welcome_message", models.CharField(blank=True, default="{applicant}, welcome as **{rank_prefix} {rank_name}**!", max_length=400)),
                ("question_1", models.CharField(blank=True, max_length=45, null=True)),
                ("question_2", models.CharField(blank=True, max_length=45, null=True)),
                ("question_3", models.CharField(blank=True, max_length=45, null=True)),
                ("question_4", models.CharField(blank=True, max_length=45, null=True)),
                ("question_5", models.CharField(blank=True, max_length=45, null=True)),
                ("on_accept_gives_permission_groups", models.ManyToManyField(blank=True, related_name="given_by_membership_type", to="auth.group")),
            ],
            options={
                "permissions": [
                    ("can_review_membershipapplication", "Can review membership applications"),
                ],
            },
        ),

        # ------------------------------------------------------------------
        # RSIVerification
        # ------------------------------------------------------------------
        migrations.CreateModel(
            name="RSIVerification",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("discord_id", models.BigIntegerField(db_index=True, help_text="Discord User Snowflake")),
                ("rsi_handle", models.CharField(max_length=60)),
                ("rsi_profile_url", models.URLField(max_length=200)),
                ("verification_code", models.CharField(max_length=20)),
                ("verification_status", models.CharField(choices=[("PENDING", "Pending"), ("VERIFIED", "Verified"), ("EXPIRED", "Expired"), ("FAILED", "Failed")], db_index=True, default="PENDING", max_length=8)),
                ("expires_at", models.DateTimeField()),
                ("verified_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="HistoricalRSIVerification",
            fields=[
                ("id", models.BigIntegerField(blank=True, db_index=True)),
                ("discord_id", models.BigIntegerField(db_index=True)),
                ("rsi_handle", models.CharField(max_length=60)),
                ("rsi_profile_url", models.URLField(max_length=200)),
                ("verification_code", models.CharField(max_length=20)),
                ("verification_status", models.CharField(max_length=8)),
                ("expires_at", models.DateTimeField()),
                ("verified_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(blank=True, editable=False)),
                ("history_id", models.AutoField(primary_key=True, serialize=False)),
                ("history_date", models.DateTimeField(db_index=True)),
                ("history_change_reason", models.CharField(max_length=100, null=True)),
                ("history_type", models.CharField(choices=[("+", "Created"), ("~", "Changed"), ("-", "Deleted")], max_length=1)),
            ],
            options={"verbose_name": "historical RSI verification", "ordering": ("-history_date", "-history_id")},
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),

        # ------------------------------------------------------------------
        # MembershipApplicationRecord
        # ------------------------------------------------------------------
        migrations.CreateModel(
            name="MembershipApplicationRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("status", models.CharField(choices=[("PENDING", "Pending Review"), ("APPROVED", "Approved"), ("DENIED", "Denied")], db_index=True, default="PENDING", max_length=20)),
                ("thread_id", models.BigIntegerField(help_text="Discord Thread Snowflake. Locked/archived on close, never deleted.", unique=True)),
                ("rsi_handle", models.CharField(blank=True, max_length=60)),
                ("rsi_profile_url", models.URLField(blank=True, max_length=200)),
                ("rsi_verified", models.BooleanField(default=False)),
                ("answer_1", models.TextField(blank=True, null=True)),
                ("answer_2", models.TextField(blank=True, null=True)),
                ("answer_3", models.TextField(blank=True, null=True)),
                ("answer_4", models.TextField(blank=True, null=True)),
                ("answer_5", models.TextField(blank=True, null=True)),
                ("submitted_at", models.DateTimeField(auto_now_add=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("reviewer_notes", models.TextField(blank=True)),
                ("applicant", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="membership_applications", to=settings.AUTH_USER_MODEL)),
                ("membership_type", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="records", to="candidacy.membershipapplicationtype")),
                ("reviewer", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reviewed_membership_apps", to="unifieduser.orgplayer")),
            ],
            options={"ordering": ["-submitted_at"]},
        ),
        migrations.CreateModel(
            name="HistoricalMembershipApplicationRecord",
            fields=[
                ("id", models.BigIntegerField(blank=True, db_index=True)),
                ("status", models.CharField(db_index=True, max_length=20)),
                ("thread_id", models.BigIntegerField()),
                ("rsi_handle", models.CharField(blank=True, max_length=60)),
                ("rsi_profile_url", models.URLField(blank=True, max_length=200)),
                ("rsi_verified", models.BooleanField(default=False)),
                ("answer_1", models.TextField(blank=True, null=True)),
                ("answer_2", models.TextField(blank=True, null=True)),
                ("answer_3", models.TextField(blank=True, null=True)),
                ("answer_4", models.TextField(blank=True, null=True)),
                ("answer_5", models.TextField(blank=True, null=True)),
                ("submitted_at", models.DateTimeField(blank=True, editable=False)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("reviewer_notes", models.TextField(blank=True)),
                ("history_id", models.AutoField(primary_key=True, serialize=False)),
                ("history_date", models.DateTimeField(db_index=True)),
                ("history_change_reason", models.CharField(max_length=100, null=True)),
                ("history_type", models.CharField(choices=[("+", "Created"), ("~", "Changed"), ("-", "Deleted")], max_length=1)),
            ],
            options={"verbose_name": "historical membership application record", "ordering": ("-history_date", "-history_id")},
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
    ]