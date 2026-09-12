from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin
from .models import (
    LootItem, LootStock, LootRequest, LootStatusMessage,
    StockItem, StockUnit, StockAssignment,
    CraftBlueprint, CraftMaterial, CraftConstraint,
    TrackedCraftedWeapon, TrackedCraftedWeaponState, TrackedCraftedRequests,
    MeritRequest,
)


# ─────────────────────────────────────────────────────────────────────────────
# LOOT STOCKPILE
# ─────────────────────────────────────────────────────────────────────────────

class LootStockInline(admin.StackedInline):
    model          = LootStock
    can_delete     = False
    extra          = 0
    fields         = ["quantity", "updated_by", "updated_at"]
    readonly_fields = ["updated_at"]
    verbose_name   = "Current Stock Level"
    verbose_name_plural = "Stock Level"


@admin.register(LootItem)
class LootItemAdmin(SimpleHistoryAdmin):
    list_display  = ["name", "category", "location", "location_agnostic", "target_qty", "low_threshold", "excess_threshold", "is_active"]
    list_filter   = ["category", "location", "is_active"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ("name",)}
    inlines       = [LootStockInline]


@admin.register(LootRequest)
class LootRequestAdmin(SimpleHistoryAdmin):
    list_display    = ["uid", "item", "direction", "quantity", "location", "status", "requester", "submitted_at"]
    list_filter     = ["direction", "status"]
    search_fields   = ["item__name", "requester__username"]
    readonly_fields = ["uid", "thread_id", "embed_message_id", "submitted_at"]


# ─────────────────────────────────────────────────────────────────────────────
# PHYSICAL STOCK
# ─────────────────────────────────────────────────────────────────────────────

class StockUnitInline(admin.TabularInline):
    model           = StockUnit
    extra           = 0
    show_change_link = True
    fields          = ["uid", "status", "craft_state", "current_holder", "condition_notes", "added_at"]
    readonly_fields = ["uid", "added_at"]
    ordering        = ["status", "-added_at"]
    can_delete      = False


@admin.register(StockItem)
class StockItemAdmin(SimpleHistoryAdmin):
    list_display  = ["name", "category", "subcategory", "tracking_mode", "available_count", "total_count", "low_stock_threshold"]
    list_filter   = ["category", "tracking_mode"]
    search_fields = ["name", "slug", "category"]
    prepopulated_fields = {"slug": ("name",)}
    inlines       = [StockUnitInline]


@admin.register(StockAssignment)
class StockAssignmentAdmin(admin.ModelAdmin):
    list_display    = ["action", "unit", "recipient_name", "actioned_by", "actioned_at"]
    list_filter     = ["action"]
    search_fields   = ["unit__item__name", "recipient_name"]
    readonly_fields = ["unit", "action", "recipient", "recipient_name", "actioned_by", "actioned_at"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


# ─────────────────────────────────────────────────────────────────────────────
# CRAFT BLUEPRINTS
# ─────────────────────────────────────────────────────────────────────────────

class CraftMaterialInline(admin.TabularInline):
    model    = CraftMaterial
    extra    = 3
    fields   = ["sort_order", "material_name", "min_temp", "temp_is_floor", "yield_min", "yield_max", "constraint_group"]
    ordering = ["sort_order", "pk"]


class CraftConstraintInline(admin.TabularInline):
    model  = CraftConstraint
    extra  = 1
    fields = ["group_label", "combined_total", "note"]


@admin.register(CraftBlueprint)
class CraftBlueprintAdmin(SimpleHistoryAdmin):
    list_display        = ["stock_item", "item_type", "updated_at", "created_by"]
    list_filter         = ["item_type"]
    search_fields       = ["stock_item__name"]
    autocomplete_fields = ["stock_item"]
    readonly_fields     = ["created_at", "updated_at", "created_by"]
    inlines             = [CraftMaterialInline, CraftConstraintInline]

    def save_model(self, request, obj, form, change):
        if not obj.pk:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


# ─────────────────────────────────────────────────────────────────────────────
# TRACKED CRAFTED WEAPONS
# ─────────────────────────────────────────────────────────────────────────────

class TrackedCraftedWeaponStateInline(admin.StackedInline):
    model       = TrackedCraftedWeaponState
    can_delete  = False
    extra       = 0
    fields      = ["status", "current_owner", "purpose", "changed_at"]
    readonly_fields = ["changed_at"]
    verbose_name = "Weapon State"
    verbose_name_plural = "Weapon State"


class TrackedCraftedRequestsInline(admin.TabularInline):
    model           = TrackedCraftedRequests
    extra           = 0
    show_change_link = False
    fields          = ["submitter", "note", "accepted", "created_at", "closed_at"]
    readonly_fields = ["submitter", "created_at"]
    verbose_name    = "Member request"


@admin.register(TrackedCraftedWeapon)
class TrackedCraftedWeaponAdmin(SimpleHistoryAdmin):
    list_display  = ["weapon_name", "submitter", "submitted_at", "gone_at"]
    search_fields = ["weapon_name"]
    inlines       = [TrackedCraftedWeaponStateInline, TrackedCraftedRequestsInline]


# ─────────────────────────────────────────────────────────────────────────────
# MERIT REQUESTS
# ─────────────────────────────────────────────────────────────────────────────

@admin.register(MeritRequest)
class MeritRequestAdmin(SimpleHistoryAdmin):
    list_display    = ["uid", "kind", "title", "status", "requester", "amount", "submitted_at"]
    list_filter     = ["kind", "status"]
    search_fields   = ["title", "requester__username", "rsi_handle"]
    readonly_fields = ["uid", "submitted_at"]
