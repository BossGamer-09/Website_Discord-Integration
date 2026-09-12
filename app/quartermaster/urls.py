from django.urls import path
from django.views.generic import RedirectView
from . import views

app_name = "quartermaster"

urlpatterns = [
    # ── Loot stockpile ────────────────────────────────────────────────────────
    # /loot/
    path("",                                        views.loot_overview,      name="overview"),
    # /loot/requests/
    path("requests/",                               views.loot_requests,      name="loot_requests"),

    # ── Physical equipment stock ──────────────────────────────────────────────
    # /loot/stock/ → redirects to unified overview
    path("stock/",                                  RedirectView.as_view(pattern_name="quartermaster:overview", permanent=False), name="stock_overview"),
    # /loot/stock/log/  ← must be before <slug> to avoid being swallowed
    path("stock/log/",                              views.assignment_log,     name="log"),
    # /loot/stock/p8-ar-rifle/
    path("stock/<slug:item_slug>/",                 views.item_detail,        name="item_detail"),
    # /loot/stock/p8-ar-rifle/unit/3f2a1b4c-…/action/
    path("stock/<slug:item_slug>/unit/<uuid:unit_uid>/action/", views.assign_unit, name="assign_unit"),
    # /loot/stock/p8-ar-rifle/log/
    path("stock/<slug:item_slug>/log/",             views.assignment_log,     name="log_item"),

    # ── Craft guide ───────────────────────────────────────────────────────────
    # /loot/adjust/
    path("adjust/",                                  views.adjust_stock,       name="adjust_stock"),
    # /loot/craft/
    path("craft/",                                   views.craft_guide,        name="craft_guide"),
    # /loot/equipment/requests/
    path("equipment/requests/",                      views.equipment_requests, name="equipment_requests"),

    # ── Merit / money requests ────────────────────────────────────────────────
    # /loot/merits/
    path("merits/",                                 views.merit_list,         name="merit_list"),
    # /loot/merits/mine/
    path("merits/mine/",                            views.my_merit_requests,  name="my_merit_requests"),
    # /loot/merits/new/
    path("merits/new/",                             views.merit_friends_gate, name="merit_new"),
    # /loot/merits/submit/
    path("merits/submit/",                          views.merit_submit,       name="merit_submit"),
    # /loot/merits/3f2a1b4c-…/
    path("merits/<uuid:uid>/",                      views.merit_detail,       name="merit_detail"),
    # /loot/merits/3f2a1b4c-…/process/
    path("merits/<uuid:uid>/process/",              views.merit_process,      name="merit_process"),
]
