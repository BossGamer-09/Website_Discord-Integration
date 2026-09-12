from django import forms
from django.conf import settings
from django.apps import apps

from app.preferences.utils import global_preference
from app.preferences.types import BoolChoicePreferenceDefinition, IntChoicePreferenceDefinition, CharPreferenceDefinition, IntPreferenceDefinition, StrChoicePreferenceDefinition, DiscordChannelSelectPreferenceDefinition


@global_preference
class CMSDiscordForumChanID(DiscordChannelSelectPreferenceDefinition):
    initial_name = "CMSDiscordChanID"
    initial_description = "Will auto sync to this >forum<"
    default_value = 0
    channel_types_filter = ["text"]
