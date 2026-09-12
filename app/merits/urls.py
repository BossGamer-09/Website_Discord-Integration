from django.urls import path
from . import views

app_name = "merits"

urlpatterns = [
    path("",                          views.request_list,   name="list"),
    path("mine/",                     views.my_requests,    name="my_requests"),
    path("submit/",                   views.submit_request, name="submit"),
    path("new/",                      views.friends_gate,   name="new"),
    path("<int:pk>/",                 views.request_detail, name="detail"),
    path("<int:pk>/process/",         views.process_request, name="process"),
]
