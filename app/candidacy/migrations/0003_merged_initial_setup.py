from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('candidacy', '0002_membership_application_system'),
    ]

    operations = [
        migrations.AddField(
            model_name='historicalrsiverification',
            name='history_user_id',
            field=models.IntegerField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name='historicalmembershipapplicationrecord',
            name='history_user_id',
            field=models.IntegerField(blank=True, db_index=True, null=True),
        ),
    ]