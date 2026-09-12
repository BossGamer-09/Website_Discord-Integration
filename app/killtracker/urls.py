from django.urls import path

from . import views
from .apps import KillTrackerConfig

app_name = "killtracker"

urlpatterns = [
    path("",                      views.DashboardView.as_view(),       name="dashboard"),
    path("keys/generate/",        views.generate_key_web,              name="generate_key"),
    path("keys/<int:key_id>/revoke/", views.revoke_key_web,            name="revoke_key"),
    path("keys/<int:key_id>/toggle-renew/", views.toggle_auto_renew,   name="toggle_renew"),
    path("leaderboard/",          views.LeaderboardView.as_view(),     name="leaderboard"),
    path("api/leaderboard/",      views.LeaderboardDataView.as_view(), name="leaderboard_data"),
    # KillTracker desktop-client endpoints (paths match client's api_client.py)
    path("api/report/",           views.ReportKillAPIView.as_view(),   name="report"),
    path("reportKill",            views.ReportKillAPIView.as_view(),   name="report_kill"),
    path("reportACKill",          views.ReportKillAPIView.as_view(),   name="report_ac_kill"),
    path("validateKey",           views.ValidateKeyAPIView.as_view(),  name="validate_key"),
    path("download/latest",                 views.ClientDownloadView.as_view(), name="client_download"),
    path("api/rsi/<str:handle>",            views.RSIProfileView.as_view(),     name="rsi_profile"),
    path("api/server/data/all",             views.ServerDataAllView.as_view(),  name="server_data_all"),
    path("api/server/data/<str:data_type>", views.ServerDataMapView.as_view(), name="server_data"),
    # Desktop device-login flow (RFC 8628): start -> browser approve -> poll -> token
    path("device/start",          views.DeviceStartView.as_view(),     name="device_start"),
    path("device/poll",           views.DevicePollView.as_view(),      name="device_poll"),
    path("device",                views.DeviceAuthorizeView.as_view(), name="device_authorize"),
]
