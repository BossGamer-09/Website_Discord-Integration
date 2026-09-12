from django import forms
from django.conf import settings
from django.apps import apps

from app.preferences.utils import global_preference
from app.preferences.types import BoolChoicePreferenceDefinition, IntChoicePreferenceDefinition, CharPreferenceDefinition, IntPreferenceDefinition, StrChoicePreferenceDefinition


@global_preference
class MSGLoggerAttachUploadURL(CharPreferenceDefinition):
    initial_name = "Discord MSG Logger Attachments Upload URL"
    initial_description = "Upload URL for Discord MSG Logger Temp Attachments Storage (Catbox Litterbox)"
    default_value = 'https://litterbox.catbox.moe/resources/internals/api.php'


@global_preference
class MSGLoggerAttachUploadDuration(StrChoicePreferenceDefinition):
    initial_name = "Discord MSG Logger Attachments Upload Duration"
    initial_description = "(Catbox Litterbox)"
    default_value = "72h"
    choices = [["1h", "1 Hour"], ["12h","12 Hours"], ["24h","1 Day"], ["72h","3 Days"],]


@global_preference
class DiscordGuildChannelsURL(CharPreferenceDefinition):  # bot token used
    initial_name = "Discord Guild Channels List URL"
    initial_description = ""
    default_value = 'https://discord.com/api/v10/guilds/{guild_id}/channels'

@global_preference
class DiscordVoiceStateSaveInterval(IntPreferenceDefinition):  # bot token used
    initial_name = "Discord Logger Voice State Snapshot Interval"
    initial_description = "in seconds"
    default_value = 5


@global_preference
class MSGLoggerExcludedChannelIDs(CharPreferenceDefinition):
    initial_name = "MSG Logger Excluded Channel/Category IDs"
    initial_description = "Comma-separated Discord channel or category IDs to skip logging (e.g. '123456,789012')"
    default_value = ""
