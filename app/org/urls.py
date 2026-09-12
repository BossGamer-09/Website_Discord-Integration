from django.urls import path
from . import views

app_name = "org"

urlpatterns = [
    path("roster/",          views.roster_view,   name="roster"),
    path("roster/<pk>/",     views.roster_member, name="roster_member"),
]
