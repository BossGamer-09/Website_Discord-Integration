"""
Migration 0005 — Sesh feature parity

Adds:
  EventPlan:      color_hex, hide_attendees, mentions_on_create, mentions_on_start,
                  allow_maintain_rsvp, thread_title_template, thread_auto_archive_duration,
                  thread_join_on_rsvp, thread_start_message_type
  EventReminder:  title_template, mentions
  EventRSVP:      rsvp_option FK
  EventTemplate:  color_hex, hide_attendees, mentions_on_create, mentions_on_start,
                  allow_maintain_rsvp, thread_title_template, thread_auto_archive_duration,
                  thread_join_on_rsvp, thread_start_message_type
  New models:     RSVPOption, EventAttendeeRole, EventRoleRestriction
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('schedevents', '0004_templatecategory_eventtemplate_category'),
    ]

    operations = [

        # ── EventPlan new fields ──────────────────────────────────────────
        migrations.AddField(
            model_name='eventplan',
            name='color_hex',
            field=models.CharField(blank=True, help_text='Per-event embed color override (#RRGGBB). Overrides the indicator color.', max_length=7, null=True),
        ),
        migrations.AddField(
            model_name='eventplan',
            name='hide_attendees',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='eventplan',
            name='mentions_on_create',
            field=models.JSONField(blank=True, default=list, help_text='Discord role IDs (ints) to ping when event is published.'),
        ),
        migrations.AddField(
            model_name='eventplan',
            name='mentions_on_start',
            field=models.JSONField(blank=True, default=list, help_text='Discord role IDs (ints) to ping when event goes ACTIVE.'),
        ),
        migrations.AddField(
            model_name='eventplan',
            name='allow_maintain_rsvp',
            field=models.BooleanField(default=False, help_text='Carry RSVPs forward when a recurring event repeats.'),
        ),
        migrations.AddField(
            model_name='eventplan',
            name='thread_title_template',
            field=models.CharField(blank=True, default='$EventName', help_text='Thread name template. Supports $EventName.', max_length=200),
        ),
        migrations.AddField(
            model_name='eventplan',
            name='thread_auto_archive_duration',
            field=models.CharField(
                choices=[('OneHour', '1 Hour'), ('OneDay', '1 Day'), ('ThreeDays', '3 Days'), ('OneWeek', '1 Week')],
                default='OneDay', max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='eventplan',
            name='thread_join_on_rsvp',
            field=models.BooleanField(default=True, help_text='Auto-add members to attendee thread when they RSVP GOING.'),
        ),
        migrations.AddField(
            model_name='eventplan',
            name='thread_start_message_type',
            field=models.CharField(
                choices=[('Thread', 'Post in thread'), ('Channel', 'Post in channel'), ('None', 'No start message')],
                default='Thread', max_length=10,
            ),
        ),

        # ── EventReminder new fields ──────────────────────────────────────
        migrations.AddField(
            model_name='eventreminder',
            name='title_template',
            field=models.CharField(blank=True, help_text='Notification title. Supports $EventName, $DT_TimeRelative.', max_length=500, null=True),
        ),
        migrations.AddField(
            model_name='eventreminder',
            name='mentions',
            field=models.JSONField(blank=True, default=list, help_text='Discord role IDs (ints) to mention in this notification.'),
        ),

        # ── EventTemplate new fields ──────────────────────────────────────
        migrations.AddField(
            model_name='eventtemplate',
            name='color_hex',
            field=models.CharField(blank=True, max_length=7, null=True),
        ),
        migrations.AddField(
            model_name='eventtemplate',
            name='hide_attendees',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='eventtemplate',
            name='mentions_on_create',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='eventtemplate',
            name='mentions_on_start',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='eventtemplate',
            name='allow_maintain_rsvp',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='eventtemplate',
            name='thread_title_template',
            field=models.CharField(blank=True, default='$EventName', max_length=200),
        ),
        migrations.AddField(
            model_name='eventtemplate',
            name='thread_auto_archive_duration',
            field=models.CharField(blank=True, default='OneDay', max_length=20),
        ),
        migrations.AddField(
            model_name='eventtemplate',
            name='thread_join_on_rsvp',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='eventtemplate',
            name='thread_start_message_type',
            field=models.CharField(blank=True, default='Thread', max_length=10),
        ),

        # ── RSVPOption (new model) ────────────────────────────────────────
        migrations.CreateModel(
            name='RSVPOption',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('guild_id', models.BigIntegerField(db_index=True)),
                ('event', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='rsvp_options',
                    to='schedevents.eventplan',
                    help_text='Leave blank for a server-wide default.',
                )),
                ('emoji', models.CharField(max_length=64)),
                ('label', models.CharField(max_length=100)),
                ('maps_to_status', models.CharField(
                    choices=[('GOING', 'Going'), ('MAYBE', 'Maybe'), ('NOT_GOING', 'Not Going')],
                    default='GOING', max_length=20,
                    help_text='Which system attendance bucket this option counts toward.',
                )),
                ('capacity_limit', models.PositiveIntegerField(blank=True, null=True)),
                ('reminders_enabled', models.BooleanField(default=True)),
                ('sort_order', models.PositiveSmallIntegerField(default=0)),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={
                'ordering': ['sort_order', 'id'],
            },
        ),

        # ── EventRSVP.rsvp_option FK ──────────────────────────────────────
        migrations.AddField(
            model_name='eventrsvp',
            name='rsvp_option',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='rsvps',
                to='schedevents.rsvpoption',
            ),
        ),

        # ── EventAttendeeRole (new model) ─────────────────────────────────
        migrations.CreateModel(
            name='EventAttendeeRole',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('event', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='attendee_roles',
                    to='schedevents.eventplan',
                )),
                ('role_id', models.BigIntegerField(help_text='Discord role snowflake ID')),
                ('add_time', models.CharField(
                    choices=[('RSVP', 'On RSVP'), ('EVENT_START', 'On Event Start')],
                    default='RSVP', max_length=15,
                )),
                ('remove_on_end', models.BooleanField(default=True)),
            ],
            options={
                'unique_together': {('event', 'role_id')},
            },
        ),

        # ── EventRoleRestriction (new model) ──────────────────────────────
        migrations.CreateModel(
            name='EventRoleRestriction',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('event', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='role_restrictions',
                    to='schedevents.eventplan',
                )),
                ('role_id', models.BigIntegerField(help_text='Discord role snowflake ID allowed to RSVP')),
                ('rsvp_option', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    help_text='Restrict to a specific RSVP option. Null = applies to all RSVP.',
                    to='schedevents.rsvpoption',
                )),
            ],
            options={
                'unique_together': {('event', 'role_id', 'rsvp_option')},
            },
        ),
    ]
