"""
Migration 0003: add slug fields to LootItem + StockItem,
                add uid (UUID) fields to StockUnit, LootRequest, MeritRequest.
"""
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("loot_tracker", "0002_stock_crafted_merits"),
    ]

    operations = [
        # LootItem slug
        migrations.AddField(
            model_name="lootitem",
            name="slug",
            field=models.SlugField(blank=True, max_length=180, unique=True, default=""),
            preserve_default=False,
        ),
        # StockItem slug
        migrations.AddField(
            model_name="stockitem",
            name="slug",
            field=models.SlugField(blank=True, max_length=180, unique=True, default=""),
            preserve_default=False,
        ),
        # StockUnit uid
        migrations.AddField(
            model_name="stockunit",
            name="uid",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True, db_index=True),
        ),
        # LootRequest uid
        migrations.AddField(
            model_name="lootrequest",
            name="uid",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True, db_index=True),
        ),
        # MeritRequest uid
        migrations.AddField(
            model_name="meritrequest",
            name="uid",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True, db_index=True),
        ),
    ]
