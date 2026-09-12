from django.urls import path
from . import views

app_name = "inventory"

urlpatterns = [
    path("",                          views.stock_overview,  name="overview"),
    path("item/<int:item_pk>/",       views.item_detail,     name="item_detail"),
    path("unit/<int:unit_pk>/action/",views.assign_unit,     name="assign_unit"),
    path("log/",                      views.assignment_log,  name="log"),
    path("log/<int:item_pk>/",        views.assignment_log,  name="log_item"),
]
