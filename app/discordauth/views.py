from django.contrib import messages
from django.contrib.auth import authenticate
from django.contrib.auth import login as auth_login, logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseRedirect, JsonResponse
from django.urls import reverse, NoReverseMatch, resolve, Resolver404
from urllib.parse import unquote

from .extra import redirect_to_discord_signin, validate_discord_oauth_state


# /login
def login(request):
    try:
        next_page = request.GET['next']
    except KeyError:
        next_page = reverse('unifieduser:index')
    else:
        next_page = unquote(request.GET['next'])
        try:
            next_page = reverse(next_page)
        except NoReverseMatch:
            try:
                # next_page may carry a query string (e.g. /killtracker/device?code=XYZ).
                # resolve() matches only the path, so validate the path part and keep the full
                # target (with query) for the redirect. Open-redirects still fail to resolve.
                resolve(next_page.split('?', 1)[0])
            except Resolver404:
                next_page = reverse('unifieduser:index')

    if request.user.is_authenticated:
        messages.warning(request, "Already logged in.")
        return HttpResponseRedirect(next_page,)

    request.session['login-redirect'] = next_page

    return redirect_to_discord_signin(request, reverse("discordauth:loginprocess"))


# /process
def loginprocess(request):
    try:
        next_page = request.session['login-redirect']
    except KeyError:
        next_page = reverse('unifieduser:index')
    else:
        del request.session['login-redirect']

    if not validate_discord_oauth_state(request, request.GET.get('state')):
        messages.error(request, "Invalid state parameter.")
        return HttpResponseRedirect(next_page,)

    if request.user.is_authenticated:
        messages.warning(request, "Already logged in.")
        return HttpResponseRedirect(next_page,)

    user = authenticate(discord_resp_param=request.GET)
    if user is None:
        messages.error(request, "Could not log into Discord account.")
    else:
        auth_login(request, user)
        messages.success(request, "Logged into Discord account {0}!".format(user.discorduser.full_username))

    return HttpResponseRedirect(next_page,)


# /link-token  — retained as a redirect stub. Self-service Discord linking was removed:
# linking is automatic at login via Discord OAuth; pre-existing accounts are bound by staff
# (/link-account or Django admin). Kept so any lingering "Link Discord" nav link still resolves.
@login_required
def generate_link_token(request):
    if hasattr(request.user, "discorduser"):
        messages.info(request, "Your account is already linked to Discord.")
    else:
        messages.info(
            request,
            "Discord linking is automatic — just sign in with Discord. "
            "If you have an existing account that needs attaching, contact an administrator.",
        )
    return HttpResponseRedirect(reverse("unifieduser:index"))


# /add
def add(request):
    try:
        next_page = request.GET['next']
    except KeyError:
        next_page = reverse('unifieduser:index')
    else:
        next_page = unquote(request.GET['next'])
        try:
            next_page = reverse(next_page)
        except NoReverseMatch:
            try:
                resolve(next_page.split('?', 1)[0])  # validate path; keep query for redirect
            except Resolver404:
                next_page = reverse('unifieduser:index')

    if not request.user.is_authenticated:
        messages.warning(request, "Need to login 1st.")
        return HttpResponseRedirect(next_page,)

    request.session['login-redirect'] = next_page

    return redirect_to_discord_signin(request, reverse("discordauth:addprocess"))


# /addprocess
def addprocess(request):
    from .models import DiscordAuthBackend

    try:
        discorduser = DiscordAuthBackend.add_as_secondary(request, discord_resp_param=request.GET)
    except Exception as exc:
        messages.error(request, "Could not log into Discord account: {}.".format(exc))
    else:
        messages.success(request, "Logged into Discord account {0}!".format(discorduser.full_username))

    try:
        next_page = request.session['login-redirect']
    except KeyError:
        next_page = reverse('unifieduser:index')
    else:
        del request.session['login-redirect']

    return HttpResponseRedirect(next_page,)
