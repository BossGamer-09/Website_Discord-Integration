from django import forms
from django.contrib import admin, messages
from simple_history.admin import SimpleHistoryAdmin

from app.killtracker.models import (
    ApiKey,
    BlacklistEntry,
    ClientRelease,
    ExclusionRule,
    GameDataList,
    GameDataMapping,
    GameModeChannel,
    KillEvent,
    ParserRelease,
    PlayerAlias,
)


@admin.register(ClientRelease)
class ClientReleaseAdmin(SimpleHistoryAdmin):
    list_display  = ("version", "is_active", "has_nosound", "notes", "created_at")
    list_filter   = ("is_active",)
    search_fields = ("version", "notes")
    readonly_fields = ("created_at",)

    @admin.display(boolean=True, description="No-sound build")
    def has_nosound(self, obj):
        return bool(obj.file_nosound)


@admin.register(ApiKey)
class ApiKeyAdmin(SimpleHistoryAdmin):
    list_display  = ("user", "label", "key_short", "is_permanent", "revoked", "last_used_at", "created_at")
    list_filter   = ("is_permanent", "revoked")
    search_fields = ("user__username", "label", "key_prefix")
    readonly_fields = ("key_hash", "key_prefix", "created_at", "last_used_at")
    actions = ("revoke_selected", "unrevoke_selected")

    def has_add_permission(self, request):
        # Keys are issued ONLY via Discord device login. No manual creation anywhere, including
        # here — an admin-made key would have no raw value to hand out anyway (key_hash is set
        # by the device flow). Staff can still revoke/un-revoke via the actions above.
        return False

    @admin.display(description="Key")
    def key_short(self, obj):
        return f"{obj.key_prefix}…"

    @admin.action(description="Revoke selected keys")
    def revoke_selected(self, request, qs):
        n = qs.update(revoked=True)
        self.message_user(request, f"Revoked {n} keys.", messages.SUCCESS)

    @admin.action(description="Un-revoke selected keys")
    def unrevoke_selected(self, request, qs):
        n = qs.update(revoked=False)
        self.message_user(request, f"Un-revoked {n} keys.", messages.SUCCESS)


@admin.register(PlayerAlias)
class PlayerAliasAdmin(SimpleHistoryAdmin):
    list_display  = ("game_name", "user", "note", "created_at")
    search_fields = ("game_name", "user__username", "note")


@admin.register(BlacklistEntry)
class BlacklistEntryAdmin(admin.ModelAdmin):
    list_display  = ("game_name", "reason", "created_at")
    search_fields = ("game_name", "reason")


@admin.register(GameModeChannel)
class GameModeChannelAdmin(admin.ModelAdmin):
    list_display  = ("game_mode_key", "display_name", "channel_id", "is_default")
    list_filter   = ("is_default",)
    search_fields = ("game_mode_key", "display_name")


@admin.register(GameDataMapping)
class GameDataMappingAdmin(SimpleHistoryAdmin):
    list_display  = ("category", "key", "value")
    list_filter   = ("category",)
    search_fields = ("key", "value")


@admin.register(GameDataList)
class GameDataListAdmin(SimpleHistoryAdmin):
    list_display  = ("category", "value")
    list_filter   = ("category",)
    search_fields = ("value",)


@admin.register(ExclusionRule)
class ExclusionRuleAdmin(SimpleHistoryAdmin):
    list_display  = ("game_mode", "substring", "message")
    list_filter   = ("game_mode",)
    search_fields = ("game_mode", "substring", "message")


@admin.register(KillEvent)
class KillEventAdmin(admin.ModelAdmin):
    list_display  = ("time", "killer_name", "victim", "crime_type", "game_mode", "weapon", "killers_ship", "anonymous")
    list_filter   = ("crime_type", "game_mode", "anonymous")
    search_fields = ("killer_name", "victim", "weapon", "zone")
    date_hierarchy = "time"
    readonly_fields = ("created_at",)


class ParserReleaseForm(forms.ModelForm):
    class Meta:
        model = ParserRelease
        fields = "__all__"
        widgets = {
            "source": forms.Textarea(attrs={"rows": 36, "style": "font-family:monospace;width:100%;"}),
        }

    def clean_source(self):
        import ast
        source = self.cleaned_data["source"]
        try:
            tree = ast.parse(source, mode="exec")
        except SyntaxError as e:
            raise forms.ValidationError(f"Not valid Python: {e}")
        names = {n.name for n in ast.walk(tree) if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))}
        missing = {"GameLogParser", "Event"} - names
        if missing:
            raise forms.ValidationError(f"Source is missing required definitions: {', '.join(sorted(missing))}")
        return source


@admin.register(ParserRelease)
class ParserReleaseAdmin(SimpleHistoryAdmin):
    form = ParserReleaseForm
    list_display  = ("version", "is_active", "created_at", "notes")
    list_filter   = ("is_active",)
    search_fields = ("version", "notes")
    readonly_fields = ("created_at",)
    actions = ("activate_selected",)

    @admin.action(description="Make selected release the active one (clients will pick it up within ~60s)")
    def activate_selected(self, request, qs):
        release = qs.order_by("-created_at").first()
        if release is None:
            return
        release.is_active = True
        release.save()  # ParserRelease.save() deactivates every other row
        self.message_user(request, f"{release.version} is now the active parser.", messages.SUCCESS)
