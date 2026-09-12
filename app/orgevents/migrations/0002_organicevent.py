from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orgevents', '0001_initial'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.CreateModel(
                    name='OrganicEvent',
                    fields=[
                        ('thread_id', models.BigIntegerField(primary_key=True, serialize=False)),
                        ('embed_message_id', models.BigIntegerField()),
                        ('event_type', models.CharField(max_length=50)),
                        ('starter_id', models.BigIntegerField()),
                        ('voice_channel_id', models.BigIntegerField(null=True, blank=True)),
                        ('is_active', models.BooleanField(default=True, db_index=True)),
                        ('started_at', models.DateTimeField(auto_now_add=True)),
                        ('summary', models.TextField(null=True, blank=True)),
                        ('select_vc_msg_id', models.BigIntegerField(null=True, blank=True)),
                        ('latest_summary_source_hash', models.CharField(max_length=64, null=True, blank=True)),
                    ],
                    options={
                        'default_permissions': (),
                        'ordering': ['-started_at'],
                        'db_table': 'orm_models_organicevent',
                    },
                ),
            ],
        ),
    ]
