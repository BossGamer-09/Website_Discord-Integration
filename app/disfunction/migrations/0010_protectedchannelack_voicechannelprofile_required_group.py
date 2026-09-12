from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
        ('disfunction', '0009_remove_voicechannelprofile_permissions_source_channel_id'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='voicechannelprofile',
            name='required_discord_role_id',
        ),
        migrations.AddField(
            model_name='voicechannelprofile',
            name='required_group',
            field=models.ForeignKey(
                blank=True,
                help_text='Django Group required to create/switch to this profile. Leave blank = everyone.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='vc_profiles',
                to='auth.group',
            ),
        ),
        migrations.CreateModel(
            name='ProtectedChannelAck',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('user_id', models.BigIntegerField(unique=True, help_text='Discord user ID.')),
                ('last_acknowledged_at', models.DateTimeField(help_text="When the user last clicked 'I Understand'.")),
            ],
            options={
                'verbose_name': 'Protected Channel Acknowledgement',
                'verbose_name_plural': 'Protected Channel Acknowledgements',
            },
        ),
    ]
