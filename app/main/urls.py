from django.urls import path, include, re_path
from django.conf import settings
from django.contrib import admin

from app.unifieduser.views import IndexView


def custom_404(request, exception=None):
    from django.shortcuts import render
    return render(request, "404.html", status=404)


def custom_403(request, exception=None):
    from django.shortcuts import render
    return render(request, "403.html", status=403)


handler404 = custom_404
handler403 = custom_403

urlpatterns = [
    path("user/discord/", include("app.discordauth.urls", namespace="discordauth")),
    path("user/", include("app.unifieduser.urls", namespace="unifieduser")),

    path('admin/doc/', include('django.contrib.admindocs.urls')),
    path('admin/', admin.site.urls),
    path("select2/", include("django_select2.urls")),
    path("admin_user/", include("django.contrib.auth.urls")),

    path('', IndexView.as_view()),
    path('sc/', include('app.sc_tracker.urls')),
    path('infantry/', include('app.infantryboard.urls', namespace='infantryboard')),
    path('events/', include('app.schedevents.urls', namespace='schedevents')),
    path('pilots/', include('app.pilotboard.urls', namespace='pilotboard')),
    path('mail/', include('app.mailclient.urls', namespace='mailclient')),
    path('killtracker/', include('app.killtracker.urls', namespace='killtracker')),
    path('loot/',      include('app.quartermaster.urls', namespace='quartermaster')),
    # Legacy redirects — old bookmarks keep working
    path('inventory/', include('app.quartermaster.legacy_urls')),
    path('merits/',    include('app.quartermaster.legacy_urls_merits')),
    path('org/', include('app.org.urls', namespace='org')),
    path('analytics/', include('app.analytics.urls', namespace='analytics')),
    path('voice/', include('app.disfunction.urls', namespace='disfunction')),
    path('', include('app.discordwebcms.urls', namespace='discordwebcms')),
]


# Serve static files TODO bleh

import re

from django.core.exceptions import ImproperlyConfigured
from django.views.static import serve


# version of from django.conf.urls.static import static for prod
def static(prefix, view=serve, **kwargs):
    """
    Return a URL pattern for serving files in debug mode.

    from django.conf import settings
    from django.conf.urls.static import static

    urlpatterns = [
        # ... the rest of your URLconf goes here ...
    ] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    """
    if not prefix:
        raise ImproperlyConfigured("Empty static prefix not permitted")
    return [
        re_path(
            r"^%s(?P<path>.*)$" % re.escape(prefix.lstrip("/")), view, kwargs=kwargs
        ),
    ]


urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
