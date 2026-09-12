import requests

import secrets

from django.http import HttpResponse

try:
    from urllib.parse import urlencode
except ImportError:
    from urllib import urlencode

from .preferences import DiscordSiteBaseURL, DiscordClientID, DiscordClientSecret, DiscordOAuthURL, DiscordOAuthTokenURL, DiscordOAuthUserInfoURL, DiscordOAuthGuildsListURL, DiscordOAuthGuildMemberInfoURL, DiscordOAuthRequestedScopes, DiscordOAuthGuildRolesURL
from app.preferences.utils import get_global_preference


def redirect_to_discord_signin(request, response_url):
    response_url = "{0}{1}".format(get_global_preference(DiscordSiteBaseURL.import_path), response_url)
    scopes = get_global_preference(DiscordOAuthRequestedScopes.import_path)
    state = secrets.token_urlsafe(16)
    request.session['discord_oauth_state'] = state

    request_params = {
        "client_id": get_global_preference(DiscordClientID.import_path),
        "redirect_uri": response_url,
        "response_type": "code",
        "scope": scopes, # guilds.join gdm.join
        "state": state,
    }

    response = HttpResponse()
    response['Location'] = "{0}?{1}".format(get_global_preference(DiscordOAuthURL.import_path), urlencode(request_params))
    response['Content-Type'] = 'application/x-www-form-urlencoded'
    response.status_code = 302
    return response


def validate_discord_oauth_state(request, response_state):
    state = request.session.get('discord_oauth_state')
    if state != response_state:
        return False
    else:
        del request.session['discord_oauth_state']
        return True


def exchange_reply_code(response_url, code):
    response_url = "{0}{1}".format(get_global_preference(DiscordSiteBaseURL.import_path), response_url)
    scopes = get_global_preference(DiscordOAuthRequestedScopes.import_path)

    data = {
        'client_id': get_global_preference(DiscordClientID.import_path),
        'client_secret': get_global_preference(DiscordClientSecret.import_path),
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': response_url,
        'scope': scopes,  # guilds.join gdm.join
    }
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}

    r = requests.post(get_global_preference(DiscordOAuthTokenURL.import_path), data=data, headers=headers, timeout=4)
    if r.status_code >= 400:
        response_body = r.text
        print("Resp Body:", response_body)
    r.raise_for_status()
    return r.json()


def refresh_token(response_url, refresh_token):
    response_url = "{0}{1}".format(get_global_preference(DiscordSiteBaseURL.import_path), response_url)
    scopes = get_global_preference(DiscordOAuthRequestedScopes.import_path)

    data = {
        'client_id': get_global_preference(DiscordClientID.import_path),
        'client_secret': get_global_preference(DiscordClientSecret.import_path),
        'grant_type': 'refresh_token',
        'refresh_token': refresh_token,
        'redirect_uri': response_url,
        'scope': scopes,  # guilds.join gdm.join
    }
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded'
    }
    r = requests.post(get_global_preference(DiscordOAuthTokenURL.import_path), data=data, headers=headers, timeout=4)
    r.raise_for_status()
    return r.json()


def get_user_info(access_token):  # scope: identify
    data = {}
    headers = {"Authorization": "Bearer {}".format(access_token)}
    r = requests.get(get_global_preference(DiscordOAuthUserInfoURL.import_path), data=data, headers=headers, timeout=4)
    r.raise_for_status()
    a = r.json()
    return a


def get_user_guilds(access_token):  # scope: guilds
    data = {}
    headers = {"Authorization": "Bearer {}".format(access_token)}
    r = requests.get(get_global_preference(DiscordOAuthGuildsListURL.import_path), data=data, headers=headers, timeout=4)
    r.raise_for_status()
    a = r.json()
    return a


def get_user_guild_info(access_token, guild_id):  # scope: guilds.members.read
    data = {}
    headers = {"Authorization": "Bearer {}".format(access_token)}
    r = requests.get(get_global_preference(DiscordOAuthGuildMemberInfoURL.import_path).format(guild_id=guild_id), data=data, headers=headers, timeout=4)
    r.raise_for_status()
    a = r.json()
    return a


def get_guild_roles(bot_token, guild_id):  # scope: guilds.members.read
    data = {}
    headers = {"Authorization": "Bot {}".format(bot_token)}
    r = requests.get(get_global_preference(DiscordOAuthGuildRolesURL.import_path).format(guild_id=guild_id), data=data, headers=headers, timeout=4)
    try:
        r.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        try:
            b = get_guild_roles_bv_api(guild_id)
        except Exception:
            raise exc
        else:
            return b
    a = r.json()
    return a
