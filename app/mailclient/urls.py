from django.urls import path
from app.mailclient import views

app_name = "mailclient"

urlpatterns = [
    path("", views.InboxView.as_view(), name="inbox"),
    path("sync/", views.SyncView.as_view(), name="sync"),
    path("thread/<str:thread_root>/", views.ThreadView.as_view(), name="thread"),
    path("thread/<str:thread_root>/reply/", views.ReplyView.as_view(), name="reply"),
]
