from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('org', '0013_delete_orgposttemplate_orgpostrequest'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.CreateModel(
                    name='PrivateNotifyThread',
                    fields=[
                        ('user_id', models.BigIntegerField(primary_key=True, serialize=False, unique=True)),
                        ('thread_id', models.BigIntegerField(unique=True)),
                    ],
                    options={
                        'default_permissions': (),
                        'db_table': 'orm_models_privatenotifythread',
                    },
                ),
            ],
        ),
    ]
