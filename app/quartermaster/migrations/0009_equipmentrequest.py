import uuid
import django.db.models.deletion
import django.utils.timezone
import simple_history.models
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('loot_tracker', '0008_craftmaterial_yield_max_digits'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='EquipmentRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uid', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True)),
                ('notes', models.TextField(blank=True)),
                ('status', models.CharField(
                    choices=[('PENDING', 'Pending'), ('APPROVED', 'Approved'), ('DENIED', 'Denied')],
                    db_index=True, default='PENDING', max_length=10,
                )),
                ('reviewer_note', models.TextField(blank=True)),
                ('reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('thread_id', models.BigIntegerField(blank=True, null=True)),
                ('embed_message_id', models.BigIntegerField(blank=True, null=True)),
                ('submitted_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('stock_item', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='equipment_requests', to='loot_tracker.stockitem',
                )),
                ('requester', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='equipment_requests', to=settings.AUTH_USER_MODEL,
                )),
                ('assigned_unit', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='assigned_requests', to='loot_tracker.stockunit',
                )),
                ('reviewed_by', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='reviewed_equipment_requests', to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'default_permissions': (),
                'app_label': 'loot_tracker',
                'ordering': ['-submitted_at'],
            },
        ),
        migrations.CreateModel(
            name='HistoricalEquipmentRequest',
            fields=[
                ('id', models.BigIntegerField(auto_created=True, blank=True, db_index=True, verbose_name='ID')),
                ('uid', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False)),
                ('notes', models.TextField(blank=True)),
                ('status', models.CharField(
                    choices=[('PENDING', 'Pending'), ('APPROVED', 'Approved'), ('DENIED', 'Denied')],
                    db_index=True, default='PENDING', max_length=10,
                )),
                ('reviewer_note', models.TextField(blank=True)),
                ('reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('thread_id', models.BigIntegerField(blank=True, null=True)),
                ('embed_message_id', models.BigIntegerField(blank=True, null=True)),
                ('submitted_at', models.DateTimeField(blank=True, editable=False)),
                ('updated_at', models.DateTimeField(blank=True, editable=False)),
                ('history_id', models.AutoField(primary_key=True, serialize=False)),
                ('history_date', models.DateTimeField(db_index=True)),
                ('history_change_reason', models.CharField(max_length=100, null=True)),
                ('history_type', models.CharField(choices=[('+', 'Created'), ('~', 'Changed'), ('-', 'Deleted')], max_length=1)),
                ('stock_item', models.ForeignKey(
                    blank=True, db_constraint=False, null=True,
                    on_delete=django.db.models.deletion.DO_NOTHING,
                    related_name='+', to='loot_tracker.stockitem',
                )),
                ('requester', models.ForeignKey(
                    blank=True, db_constraint=False, null=True,
                    on_delete=django.db.models.deletion.DO_NOTHING,
                    related_name='+', to=settings.AUTH_USER_MODEL,
                )),
                ('assigned_unit', models.ForeignKey(
                    blank=True, db_constraint=False, null=True,
                    on_delete=django.db.models.deletion.DO_NOTHING,
                    related_name='+', to='loot_tracker.stockunit',
                )),
                ('reviewed_by', models.ForeignKey(
                    blank=True, db_constraint=False, null=True,
                    on_delete=django.db.models.deletion.DO_NOTHING,
                    related_name='+', to=settings.AUTH_USER_MODEL,
                )),
                ('history_user', models.ForeignKey(
                    null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='+', to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'historical equipment request',
                'verbose_name_plural': 'historical equipment requests',
                'ordering': ('-history_date', '-history_id'),
                'get_latest_by': ('history_date', 'history_id'),
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
    ]
