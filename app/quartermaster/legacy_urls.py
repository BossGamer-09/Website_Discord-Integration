"""Redirect legacy /inventory/* URLs to /loot/stock/*"""
from django.urls import path, re_path
from django.views.generic import RedirectView

urlpatterns = [
    path("",                           RedirectView.as_view(pattern_name="quartermaster:stock_overview", permanent=True)),
    re_path(r"^item/(?P<item_pk>\d+)/", RedirectView.as_view(pattern_name="quartermaster:item_detail",   permanent=True)),
    re_path(r"^log/(?P<item_pk>\d+)/",  RedirectView.as_view(pattern_name="quartermaster:log_item",      permanent=True)),
    path("log/",                        RedirectView.as_view(pattern_name="quartermaster:log",            permanent=True)),
]
