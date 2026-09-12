import simple_history.models
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contenttypes', '0002_remove_content_type_name'),
        ('discordwebcms', '0004_alter_entry_options_alter_post_options_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # discord_channel_id, discord_message_id (on Post), and version already
        # exist in the DB from a prior unrecorded run — state-only.
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AddField(
                    model_name='post',
                    name='discord_channel_id',
                    field=models.BigIntegerField(blank=True, help_text='Channel containing the linked live message', null=True),
                ),
                migrations.AddField(
                    model_name='post',
                    name='discord_message_id',
                    field=models.BigIntegerField(blank=True, help_text='Linked live Discord message ID', null=True),
                ),
                migrations.AddField(
                    model_name='post',
                    name='version',
                    field=models.PositiveIntegerField(default=0, editable=False),
                ),
            ],
        ),
        # HistoricalPost is a brand-new table — real CreateModel.
        migrations.CreateModel(
            name='HistoricalPost',
            fields=[
                ('id', models.BigIntegerField(auto_created=True, blank=True, db_index=True, verbose_name='ID')),
                ('order', models.PositiveIntegerField(db_index=True, editable=False, verbose_name='order')),
                ('title', models.CharField(db_index=True, max_length=200)),
                ('created_at', models.DateTimeField(blank=True, editable=False)),
                ('discord_thread_id', models.BigIntegerField(blank=True, help_text='ID of the Discord Forum Thread', null=True)),
                ('discord_message_id', models.BigIntegerField(blank=True, help_text='Linked live Discord message ID', null=True)),
                ('discord_channel_id', models.BigIntegerField(blank=True, help_text='Channel containing the linked live message', null=True)),
                ('visibility', models.CharField(
                    choices=[('PUBLIC', 'Public (all members)'), ('STAFF', 'Staff only')],
                    db_index=True,
                    default='PUBLIC',
                    help_text='PUBLIC = all members via /kbsearch; STAFF = only users with discordwebcms.view_staff_post',
                    max_length=10,
                )),
                ('version', models.PositiveIntegerField(default=0, editable=False)),
                ('history_id', models.AutoField(primary_key=True, serialize=False)),
                ('history_date', models.DateTimeField(db_index=True)),
                ('history_change_reason', models.CharField(max_length=100, null=True)),
                ('history_type', models.CharField(
                    choices=[('+', 'Created'), ('~', 'Changed'), ('-', 'Deleted')],
                    max_length=1,
                )),
                ('author', models.ForeignKey(
                    blank=True,
                    db_constraint=False,
                    null=True,
                    on_delete=django.db.models.deletion.DO_NOTHING,
                    related_name='+',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('history_user', models.ForeignKey(
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='+',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'historical post',
                'verbose_name_plural': 'historical posts',
                'ordering': ('-history_date', '-history_id'),
                'get_latest_by': ('history_date', 'history_id'),
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
        migrations.AlterModelOptions(
            name='post',
            options={
                'default_permissions': (),
                'ordering': ('order',),
                'permissions': [
                    ('view_staff_post', 'Can view staff-only knowledge base posts'),
                    ('edit_post', 'Can create and edit knowledge base posts'),
                ],
            },
        ),
    ]
