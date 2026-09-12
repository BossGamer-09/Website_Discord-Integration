"""
Static specialty/role configuration.

roles         — all Discord role IDs assigned when the specialty is granted
                (first entry is always the category parent role)
specific_role — the specialty-specific role ID (used for duplicate-check)
label         — human-readable name shown in embeds
category      — SpecialtyCategory value
training_link — optional Discord channel ID for training material
"""

SPECIALTY_DATA = {
    # ── Pilot ──────────────────────────────────────────────────────────────
    "pilot_brawler": {
        "roles":         ["1314624882746593371", "1314636682120925295"],
        "specific_role": "1314636682120925295",
        "label":         "Brawler",
        "category":      "pilot",
    },
    "pilot_striker": {
        "roles":         ["1314624882746593371", "1314636684918526033"],
        "specific_role": "1314636684918526033",
        "label":         "Striker",
        "category":      "pilot",
    },
    "pilot_scout": {
        "roles":         ["1314624882746593371", "1314636688038826075"],
        "specific_role": "1314636688038826075",
        "label":         "Scout",
        "category":      "pilot",
    },

    # ── Infantry ───────────────────────────────────────────────────────────
    "infantry_lrs": {
        "roles":         ["1314624886647427184", "1314636695383179354"],
        "specific_role": "1314636695383179354",
        "label":         "LRS",
        "category":      "infantry",
        "training_link": "1250636644797907015",
    },
    "infantry_cqb": {
        "roles":         ["1314624886647427184", "1314636698176585778"],
        "specific_role": "1314636698176585778",
        "label":         "CQB",
        "category":      "infantry",
        "training_link": "1314841392035397682",
    },
    "infantry_advanced": {
        "roles":         ["1314624886647427184", "1426132300298584115"],
        "specific_role": "1426132300298584115",
        "label":         "Advanced Infantry",
        "category":      "infantry",
    },
    "infantry_cracked": {
        "roles":         ["1314624886647427184", "1317200975097761833"],
        "specific_role": "1317200975097761833",
        "label":         "Cracked Infantry",
        "category":      "infantry",
    },
    "infantry_main": {
        "roles":         ["1314624886647427184", "1168285299416248422"],
        "specific_role": "1168285299416248422",
        "label":         "Main Infantry",
        "category":      "infantry",
    },
    "infantry_secondary": {
        "roles":         ["1314624886647427184", "1285585932417761320"],
        "specific_role": "1285585932417761320",
        "label":         "Secondary Infantry",
        "category":      "infantry",
    },

    # ── Crewman ────────────────────────────────────────────────────────────
    "crewman_atg": {
        "roles":         ["1314624890648657996", "1314636710235340880"],
        "specific_role": "1314636710235340880",
        "label":         "ATG",
        "category":      "crewman",
    },
    "crewman_hm": {
        "roles":         ["1314624890648657996", "1314636662151843952"],
        "specific_role": "1314636662151843952",
        "label":         "Helmsman",
        "category":      "crewman",
    },
    "crewman_engineer": {
        "roles":         ["1314624890648657996", "1314636712819032155"],
        "specific_role": "1314636712819032155",
        "label":         "Engineer",
        "category":      "crewman",
    },
    "crewman_bombardier": {
        "roles":         ["1314624890648657996", "1314636715683745822"],
        "specific_role": "1314636715683745822",
        "label":         "Bombardier",
        "category":      "crewman",
    },

    # ── Tradesman/Support ──────────────────────────────────────────────────
    "tradesman_salvager": {
        "roles":         ["1314624893731602544", "1314636728660787340"],
        "specific_role": "1314636728660787340",
        "label":         "Salvager",
        "category":      "tradesman",
    },
    "tradesman_interdictor": {
        "roles":         ["1314624882746593371", "1314636690589220925"],
        "specific_role": "1314636690589220925",
        "label":         "Interdictor",
        "category":      "tradesman",
    },
    "tradesman_acquisitor": {
        "roles":         ["1314624882746593371", "1314636690589220925"],
        "specific_role": "1314636690589220925",
        "label":         "Acquisitor",
        "category":      "tradesman",
    },
    "tradesman_miner": {
        "roles":         ["1314624893731602544", "1314636723052875917"],
        "specific_role": "1314636723052875917",
        "label":         "Miner",
        "category":      "tradesman",
    },
}

# Category parent role IDs (one per category)
CATEGORY_PARENT_ROLES = [
    "1314624882746593371",  # Pilot
    "1314624886647427184",  # Infantry
    "1314624890648657996",  # Crewman
    "1314624893731602544",  # Tradesman/Support
]

# Granted automatically when member holds all requiredRoles
COMBINATION_MAPPINGS = [
    {
        "required_roles": ["1314636690589220925", "1314636720498675782"],
        "resulting_role":  "1314636665179869276",
        "label":           "Pirate",
    },
]

MULTI_SPECIALIST_ROLE = "1314625098078097428"

# Discord role IDs allowed to use the specialty granting panel
GRANTER_ALLOWED_ROLES = [
    "1173822659071578182",
    "1173822697956982794",
    "1173822842442367026",
    "1168327555917557780",
    "1168327592269598760",
    "1168327699203375114",
    "1168327804874661931",
]

# Select-menu options shown to granters
SPECIALTY_MENU_OPTIONS = [
    ("pilot_specialties",     "Pilot Specialties"),
    ("infantry_specialties",  "Infantry Specialties"),
    ("crewman_specialties",   "Crewman Specialties"),
    ("tradesman_specialties", "Support Specialties"),
]

# Buttons shown per category menu
CATEGORY_BUTTONS = {
    "pilot_specialties": [
        ("pilot_brawler", "Brawler"),
        ("pilot_striker", "Striker"),
        ("pilot_scout",   "Scout"),
    ],
    "infantry_specialties": [
        ("infantry_lrs",       "LRS"),
        ("infantry_cqb",       "CQB"),
        ("infantry_cracked",   "Cracked Infantry"),
        ("infantry_advanced",  "Advanced Infantry"),
        ("infantry_main",      "Main Infantry"),
        ("infantry_secondary", "Secondary Infantry"),
    ],
    "crewman_specialties": [
        ("crewman_atg",        "ATG"),
        ("crewman_hm",         "Helmsman"),
        ("crewman_engineer",   "Engineer"),
        ("crewman_bombardier", "Bombardier"),
    ],
    "tradesman_specialties": [
        ("tradesman_salvager",    "Salvager"),
        ("tradesman_interdictor", "Interdictor"),
        ("tradesman_acquisitor",  "Acquisitor"),
        ("tradesman_miner",       "Miner"),
    ],
}
