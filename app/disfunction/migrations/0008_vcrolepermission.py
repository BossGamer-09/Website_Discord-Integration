from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('disfunction', '0007_voicechannelprofile_permissions_source_channel_id'),
    ]

    operations = [
        migrations.CreateModel(
            name='VCRolePermission',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('discord_role_id', models.BigIntegerField(help_text='Discord Role ID. Use 0 to target @everyone.')),
                ('role_label', models.CharField(blank=True, help_text="Human-readable label for this row (e.g. 'BVK Members'). Not used by the bot.", max_length=100)),
                ('allow_view_channel', models.BooleanField(blank=True, default=True, help_text='Can see the channel in the sidebar.', null=True)),
                ('allow_connect', models.BooleanField(blank=True, default=True, help_text='Can join the voice channel.', null=True)),
                ('allow_speak', models.BooleanField(blank=True, default=True, help_text='Can speak (unmute themselves).', null=True)),
                ('allow_stream', models.BooleanField(blank=True, default=None, help_text='Can stream video.', null=True)),
                ('allow_use_soundboard', models.BooleanField(blank=True, default=None, help_text='Can use soundboard.', null=True)),
                ('allow_mute_members', models.BooleanField(blank=True, default=None, help_text='Can server-mute others.', null=True)),
                ('allow_deafen_members', models.BooleanField(blank=True, default=None, help_text='Can server-deafen others.', null=True)),
                ('allow_move_members', models.BooleanField(blank=True, default=None, help_text='Can move members between VCs.', null=True)),
                ('profile', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='role_permissions', to='disfunction.voicechannelprofile')),
            ],
            options={
                'verbose_name': 'VC Role Permission',
                'verbose_name_plural': 'VC Role Permissions',
                'ordering': ['discord_role_id'],
                'unique_together': {('profile', 'discord_role_id')},
            },
        ),
    ]
