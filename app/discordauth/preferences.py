from django import forms
from django.conf import settings
from django.apps import apps

from app.preferences.utils import global_preference
from app.preferences.types import BoolChoicePreferenceDefinition, IntChoicePreferenceDefinition, CharPreferenceDefinition, IntPreferenceDefinition, StrChoicePreferenceDefinition, PermissionGroupSelectPreferenceDefinition


@global_preference
class BotToken(CharPreferenceDefinition):
    initial_name = "Discord Bot Token" # can be changed in ACP
    initial_description = "Discord Bot Token."  # can be changed in ACP
    default_value = ""

@global_preference
class BotGuildID(IntPreferenceDefinition):
    initial_name = "Discord Bot Guild ID" # can be changed in ACP
    initial_description = "Discord Bot Guild ID"  # can be changed in ACP
    default_value = 0

@global_preference
class DiscordSiteBaseURL(CharPreferenceDefinition):
    initial_name = "Discord Base URL"
    initial_description = "Discord (OAuth Reply) Base URL"
    default_value = "https://{0}".format(getattr(settings, 'ABSOLUTE_URL', settings.ALLOWED_HOSTS[0]))

@global_preference
class DiscordUserGroup(PermissionGroupSelectPreferenceDefinition):
    initial_name = "Discord Group Name"
    initial_description = "The group Discord users are put in by default"
    default_value = None

@global_preference
class DiscordClientID(IntPreferenceDefinition):
    initial_name = "Discord Client ID"
    initial_description = ""
    default_value = ""

@global_preference
class DiscordClientSecret(CharPreferenceDefinition):
    initial_name = "Discord Client Secret"
    initial_description = ""
    default_value = ""

@global_preference
class DiscordOAuthURL(CharPreferenceDefinition):
    initial_name = "Discord OAuth Auth URL"
    initial_description = ""
    default_value = 'https://discord.com/api/oauth2/authorize'

@global_preference
class DiscordOAuthTokenURL(CharPreferenceDefinition):
    initial_name = "Discord OAuth Token Exchange URL"
    initial_description = ""
    default_value = 'https://discord.com/api/oauth2/token'

@global_preference
class DiscordOAuthUserInfoURL(CharPreferenceDefinition):
    initial_name = "Discord OAuth UserInfo URL"
    initial_description = ""
    default_value = 'https://discord.com/api/users/@me'

@global_preference
class DiscordOAuthGuildMemberInfoURL(CharPreferenceDefinition):  # scope: guilds.members.read
    initial_name = "Discord OAuth Guild Member Info URL"
    initial_description = ""
    default_value = 'https://discord.com/api/users/@me/guilds/{guild_id}/member'

@global_preference
class DiscordOAuthGuildsListURL(CharPreferenceDefinition):  # scope: guilds
    initial_name = "Discord OAuth Guild List URL"
    initial_description = ""
    default_value = 'https://discord.com/api/users/@me/guilds'

@global_preference
class DiscordOAuthRequestedScopes(CharPreferenceDefinition):  # scope: guilds
    initial_name = "Discord OAuth Requested Scopes"
    initial_description = ""
    default_value = 'identify guilds guilds.members.read'


@global_preference
class DiscordOAuthGuildRolesURL(CharPreferenceDefinition):  # bot token used
    initial_name = "Discord Guild Roles List URL"
    initial_description = ""
    default_value = 'https://discord.com/api/guilds/{guild_id}/roles'
