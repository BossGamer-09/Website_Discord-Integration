from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin
from .models import (
    TrackedCraftedWeapon,
    StockItem, StockUnit, StockAssignment,
)


@admin.register(TrackedCraftedWeapon)
class WeaponStatAdmin(admin.ModelAdmin):
    list_display    = ('weapon_name', 'submitter', 'submitted_at')
    list_filter     = ('submitted_at',)
    search_fields   = ('weapon_name', 'submitter')
    readonly_fields = ('submitted_at',)


class StockUnitInline(admin.TabularInline):
    model           = StockUnit
    extra           = 0
    fields          = ('pk', 'status', 'current_holder', 'stats', 'condition_notes', 'added_at')
    readonly_fields = ('pk', 'added_at')
    show_change_link = True


@admin.register(StockItem)
class StockItemAdmin(SimpleHistoryAdmin):
    list_display    = ('name', 'category', 'available_count', 'total_count', 'low_stock_threshold', 'created_at')
    list_filter     = ('category',)
    search_fields   = ('name',)
    inlines         = [StockUnitInline]


@admin.register(StockUnit)
class StockUnitAdmin(SimpleHistoryAdmin):
    list_display    = ('__str__', 'item', 'status', 'current_holder', 'added_at')
    list_filter     = ('status', 'item')
    search_fields   = ('item__name',)
    readonly_fields = ('added_at', 'updated_at')


@admin.register(StockAssignment)
class StockAssignmentAdmin(admin.ModelAdmin):
    list_display    = ('__str__', 'action', 'unit', 'recipient', 'actioned_by', 'actioned_at')
    list_filter     = ('action',)
    readonly_fields = ('actioned_at',)
