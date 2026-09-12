from django.db import migrations, models


class Migration(migrations.Migration):
    """
    State-only migration: registers UserJoinRecord in the 'org' app.
    The table already exists as orm_models_userjoinrecord (kept via db_table),
    so no DDL is executed against the database.
    """

    dependencies = [
        ("org", "0004_historicalorgplayernote_orgplayernote"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.CreateModel(
                    name='UserJoinRecord',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('discord_id', models.BigIntegerField(db_index=True, help_text='Unique Discord snowflake ID for the user', verbose_name='Discord ID')),
                        ('username', models.CharField(help_text='Discord username (without discriminator in new system)', max_length=100, verbose_name='Username')),
                        ('global_name', models.CharField(blank=True, help_text="User's global display name (new Discord system)", max_length=100, null=True, verbose_name='Global Name')),
                        ('discriminator', models.CharField(blank=True, help_text='Legacy discriminator (e.g., #0001) - deprecated but stored for historical data', max_length=4, null=True, verbose_name='Discriminator')),
                        ('display_name', models.CharField(blank=True, help_text='Server-specific nickname if set, otherwise null', max_length=100, null=True, verbose_name='Server Display Name')),
                        ('avatar_url', models.URLField(blank=True, help_text="URL to the user's avatar at time of join/update", max_length=500, null=True, verbose_name='Avatar URL')),
                        ('avatar_hash', models.CharField(blank=True, help_text='Hash of the avatar for change detection', max_length=100, null=True, verbose_name='Avatar Hash')),
                        ('is_active', models.BooleanField(db_index=True, default=True, help_text='True if the user is currently in the server', verbose_name='Active Member')),
                        ('membership_status', models.CharField(choices=[('ACTIVE', 'Active Member'), ('LEFT', 'Voluntarily Left'), ('KICKED', 'Kicked from Server'), ('BANNED', 'Banned from Server'), ('INACTIVE', 'Inactive (Auto-removed)')], default='ACTIVE', help_text='Current membership status of the user', max_length=20, verbose_name='Membership Status')),
                        ('first_seen', models.DateTimeField(blank=True, help_text='First time this user was observed in the server', null=True, verbose_name='First Seen')),
                        ('joined_at', models.DateTimeField(auto_now_add=True, help_text='When this join record was created', verbose_name='Join Timestamp')),
                        ('left_at', models.DateTimeField(blank=True, help_text='When the user left/was removed from the server', null=True, verbose_name='Left At')),
                        ('last_seen', models.DateTimeField(auto_now=True, help_text='Last time the user was active (auto-updates on activity)', verbose_name='Last Seen')),
                        ('last_updated', models.DateTimeField(auto_now=True, help_text='When this record was last modified', verbose_name='Last Updated')),
                        ('join_count', models.PositiveIntegerField(default=1, help_text='Number of times this user has joined (for returning members)', verbose_name='Join Count')),
                        ('total_duration', models.DurationField(blank=True, help_text='Total time spent in server across all joins', null=True, verbose_name='Total Duration')),
                        ('current_streak_start', models.DateTimeField(blank=True, help_text='When the current continuous membership period started', null=True, verbose_name='Current Streak Start')),
                        ('longest_streak', models.DurationField(blank=True, help_text='Longest continuous membership duration', null=True, verbose_name='Longest Streak')),
                        ('message_count', models.PositiveIntegerField(default=0, help_text='Total messages sent in the server', verbose_name='Message Count')),
                        ('last_message_at', models.DateTimeField(blank=True, help_text='When the user last sent a message', null=True, verbose_name='Last Message')),
                        ('welcome_message_id', models.BigIntegerField(blank=True, help_text='Message ID of the welcome post for tracking/cleanup', null=True, verbose_name='Welcome Message ID')),
                        ('join_log_message_id', models.BigIntegerField(blank=True, help_text='Message ID in the join log channel for tracking', null=True, verbose_name='Join Log Message ID')),
                        ('welcome_image_generated', models.BooleanField(default=False, help_text='Whether a custom welcome image was created', verbose_name='Welcome Image Generated')),
                        ('welcome_image_path', models.CharField(blank=True, help_text='File path to the generated welcome image', max_length=500, null=True, verbose_name='Welcome Image Path')),
                        ('flags', models.JSONField(default=dict, help_text='JSON field for storing arbitrary flags and metadata', verbose_name='User Flags')),
                        ('notes', models.TextField(blank=True, help_text='Administrative notes about the user', null=True, verbose_name='Admin Notes')),
                    ],
                    options={
                        'verbose_name': 'User Join Record',
                        'verbose_name_plural': 'User Join Records',
                        'db_table': 'orm_models_userjoinrecord',
                        'ordering': ['-joined_at'],
                    },
                ),
            ],
        ),
    ]
