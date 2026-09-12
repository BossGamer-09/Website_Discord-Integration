from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin
from app.main.admin import GranularObjPermAdminMixin
from .models import MeritRequest


@admin.register(MeritRequest)
class MeritRequestAdmin(GranularObjPermAdminMixin, SimpleHistoryAdmin):
    list_display  = ("pk", "kind", "title", "requester", "amount", "status", "submitted_at", "fulfilled_at")
    list_filter   = ("status", "kind")
    search_fields = ("title", "requester__username", "reason")
    readonly_fields = ("submitted_at", "updated_at", "reviewed_at", "fulfilled_at")
    raw_id_fields = ("requester", "reviewed_by", "linked_item")
