"""Weapon / ship / game-mode display mappings (ported from old gameData.js)."""

from django.core.cache import caches

from app.killtracker.models import GameDataMapping, GameDataList, ExclusionRule, GAME_DATA_CACHE_KEY

GAME_MODE_DISPLAY = {
    "SC_Frontend": "Main Menu",
    "SC_Default": "Persistent Universe",
    "EA_SquadronBattle": "Squadron Battle",
    "EA_FreeFlight": "Free Flight",
    "EA_FPSGunGame": "Gun Game",
    "EA_FPSKillConfirmed": "Kill Confirmed",
    "EA_Elimination": "Elimination",
    "EA_TeamElimination": "Team Elimination",
    "EA_TonkRoyale_FreeForAll": "Tonk Battles",
    "EA_Elimination_XOnly": "Single Weapon Elim",
    "Bot_Testing": "Bot Testing",
}

# Weapons, ships, ignored-victim rules, exclusion rules, user.cfg lines, the body-part-ID zone
# map, OS process names, contract-log regexes, and RSI-scraper CSS selectors all live in the DB
# now (GameDataMapping / GameDataList / ExclusionRule, editable in /admin) instead of as Python
# constants here. That means an admin edit takes effect immediately for every client and for
# this Discord bot process alike — no code deploy, no server restart. Cached for
# _GAME_DATA_CACHE_TTL seconds and invalidated immediately by a signal on any write (see
# app.killtracker.models.invalidate_game_data_cache), so a fix is visible within one request
# either way.
_GAME_DATA_CACHE_TTL = 300


def get_game_data() -> dict:
    """Return (and cache) every DB-backed game-data table as one dict. Shared by the Discord
    bot's display_weapon()/display_ship()/is_ignored_victim() below and by
    views.ServerDataAllView, which is the single source of truth for what the desktop client
    reads out of this dict — keep field names in sync with that view."""
    cache = caches["default"]
    data = cache.get(GAME_DATA_CACHE_KEY)
    if data is not None:
        return data

    def _mapping(category):
        return dict(GameDataMapping.objects.filter(category=category).values_list("key", "value"))

    def _list(category):
        return tuple(GameDataList.objects.filter(category=category).values_list("value", flat=True))

    data = {
        "weapons": _mapping(GameDataMapping.CATEGORY_WEAPON),
        "ships": _mapping(GameDataMapping.CATEGORY_SHIP),
        "part_map": {int(k): v for k, v in _mapping(GameDataMapping.CATEGORY_PART_MAP).items()},
        "process_names": _mapping(GameDataMapping.CATEGORY_PROCESS_NAME),
        "contract_patterns": _mapping(GameDataMapping.CATEGORY_CONTRACT_PATTERN),
        "rsi_selectors": _mapping(GameDataMapping.CATEGORY_RSI_SELECTOR),
        "ignored_victim_substrings": _list(GameDataList.CATEGORY_IGNORED_VICTIM_SUBSTRING),
        "ignored_victim_prefixes": _list(GameDataList.CATEGORY_IGNORED_VICTIM_PREFIX),
        "ship_manufacturer_prefixes": _list(GameDataList.CATEGORY_SHIP_MANUFACTURER_PREFIX),
        "user_cfg_lines": _list(GameDataList.CATEGORY_USER_CFG_LINE),
        "free_flight_game_modes": _list(GameDataList.CATEGORY_FREE_FLIGHT_GAME_MODE),
        "exclusion_rules": list(ExclusionRule.objects.values("game_mode", "substring", "message")),
    }
    cache.set(GAME_DATA_CACHE_KEY, data, _GAME_DATA_CACHE_TTL)
    return data


CRIME_TYPES = [
    ("homicide",              "Homicide"),
    ("aggravated_assault",    "Aggravated Assault"),
    ("incapacitated",         "Incapacitated"),
    ("armistice_violation",   "Armistice Violation"),
    ("destruction_of_vehicle","Destruction of Vehicle"),
    ("ramming",               "Ramming"),
    ("trespassing",           "Trespassing (First Degree)"),
]

# Points per confirmed crime — update here when the point code arrives, no client release needed.
CRIME_POINTS = {
    "homicide":               10,
    "aggravated_assault":      5,
    "incapacitated":           3,
    "armistice_violation":     8,
    "destruction_of_vehicle":  7,
    "ramming":                 4,
    "trespassing":             2,
}

CRIME_COLORS = {
    "homicide":               "#ff3b3b",
    "aggravated_assault":     "#ff6b35",
    "incapacitated":          "#ffb830",
    "armistice_violation":    "#4a9eff",
    "destruction_of_vehicle": "#ff6b00",
    "ramming":                "#f5a623",
    "trespassing":            "#a855f7",
}

CRIME_ICONS = {
    "homicide":               "☠",
    "aggravated_assault":     "⚡",
    "incapacitated":          "⚡",
    "armistice_violation":    "⚠",
    "destruction_of_vehicle": "💥",
    "ramming":                "⚡",
    "trespassing":            "⚠",
}


def display_game_mode(key: str) -> str:
    return GAME_MODE_DISPLAY.get(key, key or "NA")


def cleanup_name(name: str | None) -> str:
    if not name:
        return "Unknown"
    import re
    return re.sub(r"_\d+$", "", name)


def display_weapon(w: str | None) -> str:
    if not w:
        return "Unknown Weapon"
    return get_game_data()["weapons"].get(w) or cleanup_name(w)


def display_ship(s: str | None) -> str:
    if not s:
        return "Unknown Ship"
    return get_game_data()["ships"].get(s) or cleanup_name(s)


def is_ignored_victim(victim: str | None) -> bool:
    if not victim:
        return False
    v = victim.lower()
    data = get_game_data()
    if any(s in v for s in data["ignored_victim_substrings"]):
        return True
    if any(v.startswith(p) for p in data["ignored_victim_prefixes"]):
        return True
    if v.startswith("vlk") and not v.startswith("vlk_apex_"):
        return True
    return False


def preprocess_name(name: str) -> str:
    import re
    return re.sub(r"[^a-zA-Z0-9]", "", re.sub(r"\[.*?\]", "", name or "")).strip().lower()
