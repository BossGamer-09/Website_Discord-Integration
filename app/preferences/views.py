from django.shortcuts import render
from django.urls import reverse
from django.utils.http import urlencode
from django.db.models import Prefetch, Q

from django.views.generic.base import TemplateResponseMixin, View
from django.views.generic.list import ListView
from django.views.generic.list import UpdateView
from django.contrib.auth.mixins import LoginRequiredMixin

from .models import UserSetting, UserSettingData


class UserSettingUserView(LoginRequiredMixin, ListView):
    template_name = 'preferences/userview.html'
    context_object_name = 'user_settings_list'
    paginate_by = 100
    page_title = "My Preferences"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = self.page_title
        return context

    def get_queryset(self):
        user = self.request.user
        r = []
        for item in UserSetting.objects.all().prefetch_related(Prefetch("data", queryset=UserSettingData.objects.filter(for_user=user), to_attr="_user_data")):
            can_usersetting_view_own = user.has_perm("preferences.can_usersetting_view_own", item)
            if can_usersetting_view_own:
                item.can_usersetting_view_own = can_usersetting_view_own
                item.can_usersetting_edit_own = user.has_perm("preferences.can_usersetting_edit_own", item)
                try:
                    item.user_data = item._user_data[0]
                except IndexError:
                    pass
                r.append(item)
        return r
