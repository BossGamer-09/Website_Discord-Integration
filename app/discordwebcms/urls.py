from django.urls import path
from . import views

app_name = 'discordwebcms'

urlpatterns = [
    path('kb/',                  views.post_index,   name='post_index'),
    path('kb/new/',              views.post_create,  name='post_create'),
    path('kb/<int:pk>/',         views.post_detail,  name='post_detail'),
    path('kb/<int:pk>/edit/',    views.post_edit,    name='post_edit'),
    path('kb/<int:pk>/history/', views.post_history, name='post_history'),
]
