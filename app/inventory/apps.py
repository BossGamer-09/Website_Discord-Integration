from app.main.util.cog_loader import CogAwareAppConfig


class InventoryConfig(CogAwareAppConfig):
    name = 'app.inventory'
    label = "inventory"
    verbose_name = "Inventory — Weapons & Stock"
    default = False
