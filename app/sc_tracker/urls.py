from django.urls import path
from . import views
from .apps import ScTrackerConfig

app_name = ScTrackerConfig.name  # 'app.sc_tracker'

urlpatterns = [
    path('',                     views.SCTrackerView.as_view(),        name='dashboard'),
    path('api/sc-status/',       views.RSIStatusProxyView.as_view(),   name='sc_status'),
    path('api/sc-hangar/',        views.HangarTimerAPIView.as_view(),        name='sc_hangar'),
    path('api/sc-recent/',        views.RecentActivityAPIView.as_view(),     name='sc_recent'),
    path('api/sc-hangar-debug/', views.HangarSyncDebugView.as_view(),  name='sc_hangar_debug'),
]