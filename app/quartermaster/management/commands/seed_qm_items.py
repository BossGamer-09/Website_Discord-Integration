"""
python main.py seed_qm_items

Seeds initial StockItems + CraftBlueprints for the QM system.
Safe to re-run: items are get_or_created; blueprints/materials are wiped and rebuilt each run.
"""
from django.core.management.base import BaseCommand
from app.quartermaster.models import StockItem, CraftBlueprint, CraftMaterial, CraftConstraint

# ── Data ─────────────────────────────────────────────────────────────────────
# fmt: {"name", "category", "subcategory", "tracking_mode", "description", "notes",
#        "blueprint": {"item_type", "notes", "materials": [...], "constraints": [...]}}
# material fmt: {"material_name", "min_temp", "temp_is_floor", "yield_min", "yield_max", "sort_order", "constraint_group"}

_SHIELD_MATS_SM = [
    {"material_name": "Stileron",   "min_temp": 950, "temp_is_floor": True,  "yield_min": "0.15", "yield_max": "1.2",   "sort_order": 1},
    {"material_name": "Feynmaline", "min_temp": 950, "temp_is_floor": True,  "yield_min": "20",   "yield_max": "178",   "sort_order": 2},
]
_SHIELD_MATS_LG = [
    {"material_name": "Stileron",   "min_temp": 950, "temp_is_floor": True,  "yield_min": "0.24", "yield_max": "1.9",   "sort_order": 1},
    {"material_name": "Feynmaline", "min_temp": 950, "temp_is_floor": True,  "yield_min": "20",   "yield_max": "178",   "sort_order": 2},
]
_ARMOR_7SA_MATS = [
    {"material_name": "Borase",  "min_temp": 950, "temp_is_floor": True,  "yield_min": "0.15", "yield_max": "0.42", "sort_order": 1},
    {"material_name": "Gold",    "min_temp": 0,   "temp_is_floor": True,  "yield_min": "0.24", "yield_max": "0.68", "sort_order": 2},
    {"material_name": "Beradom", "min_temp": 950, "temp_is_floor": True,  "yield_min": "20",   "yield_max": "58",   "sort_order": 3},
]
_POWERPLANT_MATS = [
    {"material_name": "Stileron",  "min_temp": 0, "temp_is_floor": True, "yield_min": "0.35", "yield_max": "4.3", "sort_order": 1},
    {"material_name": "Beryl",     "min_temp": 0, "temp_is_floor": True, "yield_min": "0.14", "yield_max": "1.7", "sort_order": 2},
    {"material_name": "Savrilium", "min_temp": 0, "temp_is_floor": True, "yield_min": "0.24", "yield_max": "3",   "sort_order": 3},
]
_HEADHUNTER_WEAPON_MATS = [
    {"material_name": "Boaras",    "min_temp": 0, "temp_is_floor": True, "yield_min": "0.36", "yield_max": "1.16", "sort_order": 1},
    {"material_name": "Riccite",   "min_temp": 0, "temp_is_floor": True, "yield_min": "0.05", "yield_max": "0.17", "sort_order": 2},
    {"material_name": "Titanium",  "min_temp": 0, "temp_is_floor": True, "yield_min": "0.18", "yield_max": "0.58", "sort_order": 3},
]
_COOLER_MATS = [
    {"material_name": "Savrilium",       "min_temp": 0, "temp_is_floor": True, "yield_min": "0.16", "yield_max": "0.5",  "sort_order": 1},
    {"material_name": "Pressurized Ice", "min_temp": 0, "temp_is_floor": True, "yield_min": "0.16", "yield_max": "0.5",  "sort_order": 2},
    {"material_name": "Borase",          "min_temp": 0, "temp_is_floor": True, "yield_min": "0.1",  "yield_max": "0.3",  "sort_order": 3},
]
_QT_MATS = [
    {"material_name": "Borase",    "min_temp": 0, "temp_is_floor": True, "yield_min": "0.35", "yield_max": "1.24", "sort_order": 1},
    {"material_name": "Tungsten",  "min_temp": 0, "temp_is_floor": True, "yield_min": "0.14", "yield_max": "0.5",  "sort_order": 2},
    {"material_name": "Savrilium", "min_temp": 0, "temp_is_floor": True, "yield_min": "0.14", "yield_max": "0.5",  "sort_order": 3},
]

ITEMS = [
    # ── Ship Shields ──────────────────────────────────────────────────────────
    {
        "name": "FR-66", "category": "Ship Components", "subcategory": "Shields",
        "description": "Xenothreat FR-66 shield. Delayed until patch 4.8.3.",
        "blueprint": {"item_type": "COMPONENT", "notes": "Foxwell, Stanton Rank 3. Xenothreat variant — delayed until 4.8.3.", "materials": _SHIELD_MATS_SM},
    },
    {
        "name": "FR-76", "category": "Ship Components", "subcategory": "Shields",
        "description": "Xenothreat FR-76 shield. Delayed until patch 4.8.3.",
        "blueprint": {"item_type": "COMPONENT", "notes": "Foxwell, Stanton Rank 3. Xenothreat variant — delayed until 4.8.3.", "materials": _SHIELD_MATS_SM},
    },
    {
        "name": "FR-86", "category": "Ship Components", "subcategory": "Shields",
        "description": "Foxwell FR-86 shield, Stanton Rank 3.",
        "blueprint": {"item_type": "COMPONENT", "notes": "Foxwell, Stanton Rank 3.", "materials": _SHIELD_MATS_LG},
    },

    # ── FPS Armor ─────────────────────────────────────────────────────────────
    {
        "name": "7SA Concord", "category": "FPS Armor", "subcategory": "",
        "description": "Foxwell FPS armor, Stanton Rank 2.",
        "blueprint": {"item_type": "FPS_ARMOR", "notes": "Foxwell, Stanton Rank 2.", "materials": _ARMOR_7SA_MATS},
    },
    {
        "name": "7MA Lorica", "category": "FPS Armor", "subcategory": "",
        "description": "Foxwell FPS armor, Stanton Rank 2.",
        "blueprint": {"item_type": "FPS_ARMOR", "notes": "Foxwell, Stanton Rank 2.", "materials": _ARMOR_7SA_MATS},
    },
    {
        "name": "Morozov-SH", "category": "FPS Armor", "subcategory": "",
        "description": "FPS armor suit.",
        "blueprint": {
            "item_type": "FPS_ARMOR",
            "materials": [
                {"material_name": "Tungsten",  "min_temp": 900, "temp_is_floor": True,  "yield_min": "0.05", "yield_max": "0.07", "sort_order": 1},
                {"material_name": "Aslarite",  "min_temp": 600, "temp_is_floor": True,  "yield_min": "0.01", "yield_max": "0.02", "sort_order": 2},
                {"material_name": "Laranite",  "min_temp": 900, "temp_is_floor": True,  "yield_min": "0.05", "yield_max": "0.07", "sort_order": 3},
            ],
        },
    },
    {
        "name": "Geist", "category": "FPS Armor", "subcategory": "",
        "description": "FPS armor suit.",
        "blueprint": {
            "item_type": "FPS_ARMOR",
            "materials": [
                {"material_name": "Taranite",  "min_temp": 900, "temp_is_floor": True,  "yield_min": "0.03", "yield_max": None,   "sort_order": 1},
                {"material_name": "Aslarite",  "min_temp": 600, "temp_is_floor": True,  "yield_min": "0.02", "yield_max": None,   "sort_order": 2},
                {"material_name": "Stileron",  "min_temp": 900, "temp_is_floor": True,  "yield_min": "0.03", "yield_max": None,   "sort_order": 3},
            ],
        },
    },

    # ── FPS Weapons ───────────────────────────────────────────────────────────
    {
        "name": "SW16BRX", "category": "FPS Weapons", "subcategory": "",
        "description": "Headhunters Rank 4, Stanton.",
        "blueprint": {"item_type": "FPS_WEAPON", "notes": "Headhunters Rank 4, Stanton.", "materials": _HEADHUNTER_WEAPON_MATS},
    },
    {
        "name": "Buzzsaw", "category": "FPS Weapons", "subcategory": "",
        "description": "Headhunters Rank 4, Stanton.",
        "blueprint": {"item_type": "FPS_WEAPON", "notes": "Headhunters Rank 4, Stanton.", "materials": _HEADHUNTER_WEAPON_MATS},
    },
    {
        "name": "Sawbuck", "category": "FPS Weapons", "subcategory": "",
        "description": "Headhunters Rank 4, Stanton.",
        "blueprint": {"item_type": "FPS_WEAPON", "notes": "Headhunters Rank 4, Stanton.", "materials": _HEADHUNTER_WEAPON_MATS},
    },
    {
        "name": "Shredder", "category": "FPS Weapons", "subcategory": "",
        "description": "Headhunters Rank 4, Stanton.",
        "blueprint": {"item_type": "FPS_WEAPON", "notes": "Headhunters Rank 4, Stanton.", "materials": _HEADHUNTER_WEAPON_MATS},
    },
    {
        "name": "P6-LR", "category": "FPS Weapons", "subcategory": "",
        "description": "FPS precision rifle.",
        "blueprint": {
            "item_type": "FPS_WEAPON",
            "materials": [
                {"material_name": "Taranite",      "min_temp": 750, "temp_is_floor": True,  "yield_min": "0.06", "yield_max": None, "sort_order": 1, "constraint_group": ""},
                {"material_name": "Hephaestanite", "min_temp": 750, "temp_is_floor": True,  "yield_min": "0.02", "yield_max": None, "sort_order": 2, "constraint_group": ""},
                {"material_name": "Iron",          "min_temp": 950, "temp_is_floor": True,  "yield_min": "0.03", "yield_max": None, "sort_order": 3, "constraint_group": "*"},
                {"material_name": "Hadanite",      "min_temp": 950, "temp_is_floor": True,  "yield_min": "1",    "yield_max": None, "sort_order": 4, "constraint_group": "*"},
            ],
            "constraints": [
                {"group_label": "*", "combined_total": 1800, "note": "Iron + Hadanite combined refining temperature must equal 1800"},
            ],
        },
    },
    {
        "name": "P8-AR", "category": "FPS Weapons", "subcategory": "",
        "description": "FPS assault rifle.",
        "blueprint": {
            "item_type": "FPS_WEAPON",
            "materials": [
                {"material_name": "Stileron",      "min_temp": 750, "temp_is_floor": True,  "yield_min": "0.04", "yield_max": None, "sort_order": 1},
                {"material_name": "Hephaestanite", "min_temp": 750, "temp_is_floor": True,  "yield_min": "0.02", "yield_max": None, "sort_order": 2},
                {"material_name": "Iron",          "min_temp": 900, "temp_is_floor": True,  "yield_min": "0.02", "yield_max": None, "sort_order": 3},
            ],
        },
    },
    {
        "name": "P8-SC", "category": "FPS Weapons", "subcategory": "",
        "description": "FPS submachine carbine.",
        "blueprint": {
            "item_type": "FPS_WEAPON",
            "materials": [
                {"material_name": "Savrilium",     "min_temp": 750, "temp_is_floor": True,  "yield_min": "0.03", "yield_max": None, "sort_order": 1},
                {"material_name": "Hephaestanite", "min_temp": 750, "temp_is_floor": True,  "yield_min": "0.01", "yield_max": None, "sort_order": 2},
                {"material_name": "Iron",          "min_temp": 900, "temp_is_floor": True,  "yield_min": "0.01", "yield_max": None, "sort_order": 3},
            ],
        },
    },
    {
        "name": "Parallax", "category": "FPS Weapons", "subcategory": "",
        "description": "FPS sniper rifle.",
        "blueprint": {
            "item_type": "FPS_WEAPON",
            "materials": [
                {"material_name": "Tungsten", "min_temp": 750, "temp_is_floor": True,  "yield_min": "0.04", "yield_max": None, "sort_order": 1},
                {"material_name": "Gold",     "min_temp": 850, "temp_is_floor": True,  "yield_min": "0.02", "yield_max": None, "sort_order": 2},
                {"material_name": "Carinite", "min_temp": 900, "temp_is_floor": True,  "yield_min": "1",    "yield_max": None, "sort_order": 3},
            ],
        },
    },

    # ── Ship Components ───────────────────────────────────────────────────────
    {
        "name": "JS-300", "category": "Ship Components", "subcategory": "Powerplants",
        "description": "Tactical Strike Groups powerplant.",
        "blueprint": {"item_type": "COMPONENT", "notes": "Tactical Strike Groups.", "materials": _POWERPLANT_MATS},
    },
    {
        "name": "JS-400", "category": "Ship Components", "subcategory": "Powerplants",
        "description": "Tactical Strike Groups powerplant.",
        "blueprint": {"item_type": "COMPONENT", "notes": "Tactical Strike Groups.", "materials": _POWERPLANT_MATS},
    },
    {
        "name": "JS-500", "category": "Ship Components", "subcategory": "Powerplants",
        "description": "Tactical Strike Groups powerplant.",
        "blueprint": {"item_type": "COMPONENT", "notes": "Tactical Strike Groups.", "materials": _POWERPLANT_MATS},
    },
    {
        "name": "Snowblind", "category": "Ship Components", "subcategory": "Coolers",
        "description": "HeadHunters Rank 1 cooler, Pyro. (Terminus variant)",
        "blueprint": {"item_type": "COMPONENT", "notes": "HeadHunters Rank 1, Pyro.", "materials": _COOLER_MATS},
    },
    {
        "name": "Nightfall", "category": "Ship Components", "subcategory": "Coolers",
        "description": "HeadHunters Rank 1 cooler, Pyro. (Bloom variant)",
        "blueprint": {"item_type": "COMPONENT", "notes": "HeadHunters Rank 1, Pyro.", "materials": _COOLER_MATS},
    },
    {
        "name": "Spectre", "category": "Ship Components", "subcategory": "Quantum Drives",
        "description": "Ling Hauling Rank 0 quantum drive, anywhere.",
        "blueprint": {"item_type": "COMPONENT", "notes": "Ling Hauling Rank 0, anywhere.", "materials": _QT_MATS},
    },
    {
        "name": "Spicule", "category": "Ship Components", "subcategory": "Quantum Drives",
        "description": "Ling Hauling Rank 0 quantum drive, anywhere.",
        "blueprint": {"item_type": "COMPONENT", "notes": "Ling Hauling Rank 0, anywhere.", "materials": _QT_MATS},
    },
    {
        "name": "Fullspec", "category": "Ship Components", "subcategory": "Radars",
        "description": "Shubin Interstellar Rank 4 ship radar, Pyro/Nyx.",
        "blueprint": {
            "item_type": "COMPONENT",
            "notes": "Shubin Interstellar Rank 4 Ship Mining, Pyro/Nyx.",
            "materials": [
                {"material_name": "Titanium", "min_temp": 0,   "temp_is_floor": True,  "yield_min": "0.1",  "yield_max": "2",   "sort_order": 1},
                {"material_name": "Laranite", "min_temp": 853, "temp_is_floor": True,  "yield_min": "0.04", "yield_max": "0.8", "sort_order": 2},
                {"material_name": "Riccite",  "min_temp": 853, "temp_is_floor": True,  "yield_min": "0.04", "yield_max": "0.8", "sort_order": 3},
            ],
        },
    },
]


class Command(BaseCommand):
    help = "Seeds initial QM StockItems and CraftBlueprints. Safe to re-run."

    def handle(self, *args, **options):
        created_items = 0
        updated_bps   = 0

        for data in ITEMS:
            bp_data = data.pop("blueprint", None)
            defaults = {k: v for k, v in data.items() if k != "name"}
            defaults.setdefault("tracking_mode", StockItem.TrackingMode.UNIT)

            item, created = StockItem.objects.get_or_create(
                name=data["name"],
                defaults=defaults,
            )
            if created:
                created_items += 1
                self.stdout.write(f"  + Created: {item.name}")
            else:
                self.stdout.write(f"  ~ Exists:  {item.name}")

            if bp_data:
                materials  = bp_data.pop("materials",    [])
                constraints = bp_data.pop("constraints", [])
                bp, _ = CraftBlueprint.objects.update_or_create(
                    stock_item=item,
                    defaults=bp_data,
                )
                bp.materials.all().delete()
                bp.constraints.all().delete()
                for m in materials:
                    m.setdefault("constraint_group", "")
                    CraftMaterial.objects.create(blueprint=bp, **m)
                for c in constraints:
                    CraftConstraint.objects.create(blueprint=bp, **c)
                updated_bps += 1

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. {created_items} items created, {updated_bps} blueprints seeded."
        ))
