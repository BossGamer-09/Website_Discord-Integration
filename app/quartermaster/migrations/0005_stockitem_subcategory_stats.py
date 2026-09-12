from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("loot_tracker", "0004_alter_historicallootitem_options_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="stockitem",
            name="subcategory",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                help_text="Optional sub-category (e.g. ore quality, item tier)",
                max_length=100,
            ),
        ),
        migrations.AddField(
            model_name="historicalstockitem",
            name="subcategory",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                help_text="Optional sub-category (e.g. ore quality, item tier)",
                max_length=100,
            ),
        ),
        migrations.AddField(
            model_name="stockitem",
            name="stats",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Free-form key/value stats (e.g. Quality, Yield, Damage)",
            ),
        ),
        migrations.AddField(
            model_name="historicalstockitem",
            name="stats",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Free-form key/value stats (e.g. Quality, Yield, Damage)",
            ),
        ),
    ]
