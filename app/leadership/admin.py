from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from app.main.admin import GranularObjPermAdmin

from .models import DisciplinaryRecord, DecisionLog


@admin.register(DisciplinaryRecord)
class DisciplinaryRecordAdmin(SimpleHistoryAdmin):
    list_display = ('subject', 'action_type', 'status', 'issued_by', 'issued_at', 'expires_at', 'requires_approval')
    list_filter = ('action_type', 'status', 'requires_approval')
    search_fields = ('subject__username', 'reason', 'internal_notes')
    readonly_fields = ('issued_at', 'approved_at', 'pardoned_at')
    raw_id_fields = ('subject', 'issued_by', 'approved_by', 'pardoned_by')


@admin.register(DecisionLog)
class DecisionLogAdmin(admin.ModelAdmin):
    list_display = ('subject', 'event_type', 'actor', 'summary', 'created_at')
    list_filter = ('event_type',)
    search_fields = ('subject__username', 'summary', 'detail')
    readonly_fields = ('created_at', 'subject', 'actor', 'event_type', 'summary',
                       'detail', 'disciplinary_record', 'rank_before_id', 'rank_after_id')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
