from django.urls import path
from . import views

app_name = "disfunction"

urlpatterns = [
    path('transcripts/',          views.vc_transcript_list,   name='vc_transcript_list'),
    path('transcripts/<int:pk>/', views.vc_transcript_detail, name='vc_transcript_detail'),
    path('message-log/',          views.message_log,          name='message_log'),
]
