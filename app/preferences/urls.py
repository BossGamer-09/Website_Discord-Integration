from django.urls import path

from . import views
from .apps import PreferencesConfig


app_name = PreferencesConfig.name


urlpatterns = [
    path('usersettings/', views.UserSettingUserView.as_view(), name='usersettings'),
]
