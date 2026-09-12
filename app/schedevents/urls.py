from django.urls import path
from . import views

app_name = "schedevents"

urlpatterns = [
    path("",                               views.dashboard,          name="dashboard"),
    path("calendar/",                      views.event_calendar,     name="calendar"),
    path("manage/",                        views.event_manage,       name="manage"),
    path("create/",                        views.event_create,       name="event_create"),
    path("import-template/",              views.event_import_template, name="import_template"),
    path("<str:codename_id>/",             views.event_detail,       name="event_detail"),
    path("<str:codename_id>/edit/",        views.event_edit,         name="event_edit"),
    path("<str:codename_id>/roster/",      views.event_roster,       name="event_roster"),
    path("<str:codename_id>/rsvp/",        views.event_rsvp_post,    name="event_rsvp"),
    path("<str:codename_id>/cancel/",      views.event_cancel,       name="event_cancel"),
    path("<str:codename_id>/publish/",     views.event_publish,      name="event_publish"),
    path("<str:codename_id>/checkin/",     views.event_force_checkin,      name="event_force_checkin"),
]
