"""Redirect legacy /merits/* URLs to /loot/merits/*"""
from django.urls import path, re_path
from django.views.generic import RedirectView

urlpatterns = [
    path("",                        RedirectView.as_view(pattern_name="quartermaster:merit_list",         permanent=True)),
    path("mine/",                   RedirectView.as_view(pattern_name="quartermaster:my_merit_requests",  permanent=True)),
    path("new/",                    RedirectView.as_view(pattern_name="quartermaster:merit_new",          permanent=True)),
    path("submit/",                 RedirectView.as_view(pattern_name="quartermaster:merit_submit",       permanent=True)),
    re_path(r"^(?P<pk>\d+)/$",      RedirectView.as_view(pattern_name="quartermaster:merit_detail",      permanent=True)),
    re_path(r"^(?P<pk>\d+)/process/$", RedirectView.as_view(pattern_name="quartermaster:merit_process",  permanent=True)),
]
