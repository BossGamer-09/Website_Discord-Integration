from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pilotboard", "0003_pilot_is_active_pilotgroupfilter"),
    ]

    operations = [
        # org_player_id missing from historicalpilot (0002 only patched the live table)
        migrations.AddField(
            model_name="historicalpilot",
            name="org_player_id",
            field=models.IntegerField(blank=True, db_index=True, null=True),
        ),
        # is_active missing from historicalpilot (0003 only patched the live table)
        migrations.AddField(
            model_name="historicalpilot",
            name="is_active",
            field=models.BooleanField(default=True),
        ),
    ]
