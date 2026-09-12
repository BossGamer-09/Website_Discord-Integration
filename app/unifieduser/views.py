from datetime import datetime, timezone

from django.shortcuts import render
from django.urls import reverse, NoReverseMatch
from django.conf import settings
from django.shortcuts import redirect, resolve_url
from django.urls import reverse_lazy
from django.utils.http import urlencode
from django.utils.decorators import method_decorator
from django.http import JsonResponse, HttpResponseRedirect
from django.conf import settings

from django.views.generic.base import TemplateResponseMixin, View
from django.views.generic.detail import BaseDetailView
from django.views.generic.edit import DeletionMixin
from django.views.generic.list import ListView
from django.contrib.auth.mixins import LoginRequiredMixin

from django.contrib import messages
from django.contrib.auth import logout as auth_logout

from django_otp import devices_for_user
from django_otp.views import LoginView as OTPLoginView
from django_otp.forms import OTPTokenForm

from django_ratelimit.decorators import ratelimit

from app.main.util.misc import utcnow_aware

from .models import DisplayNameSearchCache
from .forms import OptionalOTPAuthenticationForm


# /logout
def logout(request):
    auth_logout(request)
    messages.warning(request, 'Logout Successful')

    # `next` may be a URL name (legacy callers) OR a same-site relative path — the latter is
    # needed when the return page has to keep its query string (e.g. the KillTracker device
    # authorize page's ?code=...). url_has_allowed_host_and_scheme guards against open-redirect.
    from django.utils.http import url_has_allowed_host_and_scheme
    raw_next = request.GET.get('next', '')
    try:
        next_page = reverse(raw_next)
    except NoReverseMatch:
        if raw_next and url_has_allowed_host_and_scheme(raw_next, allowed_hosts=None):
            next_page = raw_next
        else:
            next_page = reverse('unifieduser:index')

    return HttpResponseRedirect(next_page,)


class DisplayNameSearchView(View):
    @method_decorator(ratelimit(key='ip', rate='28/m', method='GET', block=True))
    @method_decorator(ratelimit(key='ip', rate='1/s', method='GET', block=True))
    def get(self, request, *args, **kwargs):
        term = self.request.GET.get('t', "")
        return JsonResponse(self.get_data(term))

    def get_data(self, term):
        if 3 <= len(term) <= 31:
            result = {"status": "OK", "data": DisplayNameSearchCache.partial_search(term)}
        else:
            result = {"status": "ERROR", "status_reason": "param t is not valid len."}
        return result


class IndexView(TemplateResponseMixin, View):
    template_name = 'unifieduser/index.html'
    page_title = "User Index"

    def get_context_data(self, **kwargs):
#		context = super().get_context_data(**kwargs)
        context = {}
        context["title"] = self.page_title

        param_next = self.request.GET.get('next', '')
        if param_next:
            login_url_params = urlencode({"next": param_next})
            context["site_login_url"] = "{}?{}".format(reverse('unifieduser:otplogin'), login_url_params)
        else:
            context["site_login_url"] =  "{}".format(reverse('unifieduser:otplogin'))

        context["otp_verify_url"] =  "{}".format(settings.OTP_LOGIN_URL)

        if param_next:
            login_url_params = urlencode({"next": param_next})
            context["discord_login_url"] = "{}?{}".format(reverse('discordauth:login'), login_url_params)
        else:
            context["discord_login_url"] =  "{}".format(reverse('discordauth:login'))

        # context["logout_url"] = settings.LOGOUT_URL
        context["next_url"] = param_next

        u = self.request.user

        from app.schedevents.permissions import user_can_view_events
        context["show_events_app"] = user_can_view_events(u)

        from app.pilotboard.permissions import user_can_view_roster
        context["show_pilotboard_app"] = user_can_view_roster(u)

        from app.killtracker.permissions import user_can_view_killtracker
        context["show_killtracker_app"] = user_can_view_killtracker(u)

        context["show_mailclient_app"] = u.is_authenticated and u.has_perm("mailclient.view_inbox")

        context["show_infantryboard_app"] = u.is_authenticated and (
            u.has_perm("infantryboard.view_infantry_roster") or u.has_perm("infantryboard.manage_infantry_roster")
        )

        context["show_inventory_app"] = False  # retired — merged into quartermaster
        context["show_merits_app"] = False     # retired — merged into quartermaster

        context["show_loot_app"] = u.is_authenticated and (
            u.has_perm("loot_tracker.view_loot")
            or u.has_perm("loot_tracker.submit_loot_request")
            or u.has_perm("loot_tracker.view_stock")
            or u.has_perm("loot_tracker.submit_merit_request")
            or u.has_perm("loot_tracker.view_merit_requests")
        )

        context["show_roster_app"] = u.is_authenticated and u.has_perm("unifieduser.view_org_roster")

        context["show_analytics_app"] = u.is_authenticated and (
            u.is_staff or u.is_superuser or u.has_perm("analytics.view_analytics")
        )

        context["show_kb_app"] = u.is_authenticated and (
            u.has_perm("discordwebcms.edit_post") or u.has_perm("discordwebcms.view_staff_post")
        )

        context["show_staff_panel"] = u.is_authenticated and any([
            context["show_roster_app"],
            context["show_mailclient_app"],
            context["show_analytics_app"],
            context["show_kb_app"],
        ])

        return context

    def get(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)
        return self.render_to_response(context)


class LoginView(OTPLoginView):
    """
    If user is not logged in will provide an optional token field that takes an MFA challange, which will be matched against all user's devices
    If user is logged in will provide a required token field.
    """
    template_name = "auth/login.html"

    otp_authentication_form = OptionalOTPAuthenticationForm
    otp_token_form = OTPTokenForm


class SessionMixin(object):
    def get_queryset(self):
        return self.request.user.session_set.filter(expire_date__gt=utcnow_aware()).order_by('-updated_at')


class SessionListView(LoginRequiredMixin, SessionMixin, ListView):
    template_name = "unifieduser/session_list.html"

    def get_context_data(self, **kwargs):
        kwargs['session_key'] = self.request.session.session_key
        return super().get_context_data(**kwargs)


class SessionDeleteView(LoginRequiredMixin, SessionMixin, DeletionMixin, BaseDetailView):
    def delete(self, request, *args, **kwargs):
        if kwargs['pk'] == request.session.session_key:
            auth_logout(request)
            next_page = getattr(settings, 'LOGOUT_REDIRECT_URL', '/')
            return redirect(resolve_url(next_page))
        return super(SessionDeleteView, self).delete(request, *args, **kwargs)

    def get_success_url(self):
        return str(reverse_lazy('app.unifieduser:session_list'))


class SessionDeleteOtherView(LoginRequiredMixin, SessionMixin, DeletionMixin, View):
    def get_object(self):
        return super(SessionDeleteOtherView, self).get_queryset().exclude(session_key=self.request.session.session_key)

    def get_success_url(self):
        return str(reverse_lazy('app.unifieduser:session_list'))


"""
class SetupMFA(TemplateResponseMixin, View):
    def _pre_add_mfa(self, user):
        devices = devices_for_user(user, confirmed=None)
        for device in devices:
            if device.confirmed:
                raise PermissionDenied
            else:
                device.delete()
    
    def 

    device = TOTPDevice.objects.get(user=obj)
    if device:
        import qrcode
        import qrcode.image.svg
        img = qrcode.make(device.config_url, image_factory=qrcode.image.svg.SvgImage)
"""
