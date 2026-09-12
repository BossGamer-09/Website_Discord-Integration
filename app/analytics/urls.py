from django.urls import path
from . import views

app_name = "analytics"

urlpatterns = [
    path("",                views.overview,            name="overview"),
    path("members/",        views.member_activity,     name="member_activity"),
    path("events/",         views.event_health,        name="event_health"),
    path("kills/",          views.kill_stats,          name="kill_stats"),
    path("leadership/",     views.leadership_activity, name="leadership_activity"),
    path("performance/",    views.performance_scores,  name="performance_scores"),
]
