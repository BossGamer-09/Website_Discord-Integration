import requests

import secrets

from django.http import HttpResponse

try:
    from urllib.parse import urlencode
except ImportError:
    from urllib import urlencode

from .preferences import DiscordGuildChannelsURL
from app.preferences.utils import get_global_preference


def get_guild_channels(bot_token, guild_id):
    data = {}
    headers = {"Authorization": "Bot {}".format(bot_token)}
    r = requests.get(get_global_preference(DiscordGuildChannelsURL.import_path).format(guild_id=guild_id), data=data, headers=headers, timeout=10)
    r.raise_for_status()
    a = r.json()
    return a
