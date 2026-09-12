from django.urls import include, path

from . import views
from .apps import DiscordauthConfig


app_name = DiscordauthConfig.name


urlpatterns = [
    path('login/', views.login, name='login'),
    path('loginprocess/', views.loginprocess, name='loginprocess'),
    path('add/', views.add, name='add'),
    path('addprocess/', views.addprocess, name='addprocess'),
    path('link-token/', views.generate_link_token, name='link_token'),
]
