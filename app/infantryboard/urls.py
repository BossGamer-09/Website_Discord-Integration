from django.urls import path

from . import views

app_name = "infantryboard"

urlpatterns = [
    # Dashboard
    path("", views.dashboard, name="dashboard"),

    # Soldier detail
    path("<slug:slug>/", views.soldier_detail, name="detail"),

    # Soldier mutations (AJAX)
    path("<slug:slug>/rename/",    views.soldier_rename,        name="rename"),
    path("<slug:slug>/skill/",     views.soldier_update_skill,  name="update_skill"),
    path("<slug:slug>/tags/",      views.soldier_update_tags,   name="update_tags"),
    path("<slug:slug>/notes/",     views.soldier_save_notes,    name="save_notes"),
    path("<slug:slug>/active/",    views.soldier_toggle_active, name="toggle_active"),
    path("<slug:slug>/group/<int:gid>/", views.soldier_toggle_group, name="toggle_group"),

    # Goal mutations (AJAX)
    path("<slug:slug>/goals/",                                  views.goal_add,      name="goal_add"),
    path("<slug:slug>/goals/<int:gid>/complete/",               views.goal_complete, name="goal_complete"),
    path("<slug:slug>/goals/<int:gid>/delete/",                 views.goal_delete,   name="goal_delete"),

    # Goal note mutations (AJAX)
    path("<slug:slug>/goals/<int:gid>/notes/",                  views.goal_note_add,    name="goal_note_add"),
    path("<slug:slug>/goals/<int:gid>/notes/<int:nid>/delete/", views.goal_note_delete, name="goal_note_delete"),
]
