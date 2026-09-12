from django.urls import path

from . import views
from .apps import UnifieduserConfig


app_name = UnifieduserConfig.name


urlpatterns = [
    path('index/', view=views.IndexView.as_view(), name='index'),
    path('logout/', views.logout, name='logout'),
    path('display_name_search/', views.DisplayNameSearchView.as_view(), name='display_name_search'),
    path('otp/login/', view=views.LoginView.as_view(), name='otplogin'),
    path('sessions/', view=views.SessionListView.as_view(), name='session_list'),
    path('sessions/other/delete/', view=views.SessionDeleteOtherView.as_view(), name='session_delete_other'),
    path('sessions/<str:pk>/delete/', view=views.SessionDeleteView.as_view(), name='session_delete'),
]
