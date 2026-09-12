"""
app/pilotboard/admin.py

Django admin for the Pilot Roster system.
"""
import csv
import io
import json
from datetime import datetime, timezone as dt_timezone

from django import forms
from django.contrib import admin, messages
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import path
from django.utils.html import format_html
from simple_history.admin import SimpleHistoryAdmin

from app.pilotboard.models import GoalNote, Pilot, PilotGoal, PilotGroupFilter, PilotTag


# ---------------------------------------------------------------------------
# Inlines
# ---------------------------------------------------------------------------

class GoalNoteInline(admin.TabularInline):
    model = GoalNote
    extra = 0
    readonly_fields = ("author", "created_at")
    fields = ("content", "author", "created_at")


class PilotGoalInline(admin.StackedInline):
    model = PilotGoal
    extra = 0
    readonly_fields = ("created_at",)
    fields = ("title", "completed", "created_at")
    show_change_link = True


# ---------------------------------------------------------------------------
# CSV import helpers
# ---------------------------------------------------------------------------

class PilotCsvImportForm(forms.Form):
    csv_file = forms.FileField(
        label="CSV file",
        help_text="Export from the old project. Required columns: name, team_fighting, duelling, leadership. Optional: tags, notes, notes_updated_at.",
    )
    overwrite = forms.BooleanField(
        required=False,
        label="Overwrite existing pilots",
        help_text="If unchecked, pilots whose name already exists are skipped.",
    )


def _parse_tags(raw):
    try:
        val = json.loads(raw)
        return val if isinstance(val, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _parse_dt(raw):
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f+00", "%Y-%m-%d %H:%M:%S+00"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=dt_timezone.utc)
        except ValueError:
            continue
    return None


def _import_rows(rows, overwrite, request_user):
    from django.contrib.auth import get_user_model
    User = get_user_model()

    created = updated = skipped = errors = 0
    user_cache = {}
    detail = []

    def resolve_user(username):
        if not username:
            return None
        if username not in user_cache:
            user_cache[username] = User.objects.filter(username__iexact=username).first()
        return user_cache[username]

    with transaction.atomic():
        for row in rows:
            name = (row.get("name") or "").strip()
            if not name:
                errors += 1
                detail.append(("error", f"Blank name skipped: {row}"))
                continue

            try:
                tf = max(0, min(5, int(row.get("team_fighting") or 0)))
                du = max(0, min(5, int(row.get("duelling") or 0)))
                le = max(0, min(5, int(row.get("leadership") or 0)))
            except ValueError:
                errors += 1
                detail.append(("error", f"{name}: invalid skill value"))
                continue

            tags             = _parse_tags(row.get("tags", "[]"))
            is_active        = "inactive" not in tags
            notes            = (row.get("notes") or "").strip()
            notes_updated_at = _parse_dt(row.get("notes_updated_at", ""))
            updated_by       = resolve_user(row.get("updated_by", "")) or request_user

            existing = Pilot.objects.filter(name__iexact=name).first()

            if existing and not overwrite:
                skipped += 1
                detail.append(("skip", f"{name} (already exists)"))
                continue

            if existing:
                existing.team_fighting    = tf
                existing.duelling         = du
                existing.leadership       = le
                existing.tags             = tags
                existing.is_active        = is_active
                existing.updated_by       = updated_by
                if notes:
                    existing.notes            = notes
                    existing.notes_updated_at = notes_updated_at
                existing.save(update_fields=[
                    "team_fighting", "duelling", "leadership",
                    "tags", "is_active", "notes", "notes_updated_at", "updated_by",
                ])
                updated += 1
                detail.append(("update", name))
            else:
                Pilot.objects.create(
                    name             = name,
                    team_fighting    = tf,
                    duelling         = du,
                    leadership       = le,
                    tags             = tags,
                    is_active        = is_active,
                    notes            = notes,
                    notes_updated_at = notes_updated_at,
                    updated_by       = updated_by,
                    # org_player intentionally null — linked when user signs in
                )
                created += 1
                detail.append(("create", name))

    return created, updated, skipped, errors, detail


# ---------------------------------------------------------------------------
# Pilot
# ---------------------------------------------------------------------------

@admin.register(Pilot)
class PilotAdmin(SimpleHistoryAdmin):
    list_display = (
        "name", "is_active", "org_player", "skill_chips", "tag_chips",
        "open_goal_title", "updated_by", "created_at",
    )
    list_filter   = ("is_active", "tags")
    list_editable  = ("is_active",)
    search_fields  = ("name", "updated_by__username", "org_player__username")
    readonly_fields = ("created_at", "notes_updated_at", "updated_by")
    autocomplete_fields = ("org_player",)
    actions = ["auto_link_accounts"]
    inlines = [PilotGoalInline]

    fieldsets = (
        (None, {"fields": ("name", "is_active", "org_player", "tags")}),
        ("Skills", {"fields": ("team_fighting", "duelling", "leadership")}),
        ("Notes", {"fields": ("notes", "notes_updated_at")}),
        ("Meta", {"fields": ("updated_by", "created_at")}),
    )

    # ── Actions ───────────────────────────────────────────────────────────────

    @admin.action(description="Auto-link selected pilots to accounts by username match")
    def auto_link_accounts(self, request, queryset):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        linked = skipped = already = 0
        for pilot in queryset.filter(org_player__isnull=True):
            user = User.objects.filter(username__iexact=pilot.name).first()
            if user:
                try:
                    pilot.org_player = user
                    pilot.save(update_fields=["org_player"])
                    linked += 1
                except Exception:
                    skipped += 1
            else:
                skipped += 1
        already = queryset.filter(org_player__isnull=False).count()
        self.message_user(
            request,
            f"Linked: {linked}  ·  No match found: {skipped}  ·  Already linked (skipped): {already}",
            messages.SUCCESS if linked else messages.WARNING,
        )

    # ── Custom URLs ───────────────────────────────────────────────────────────

    def get_urls(self):
        return [
            path("import-csv/", self.admin_site.admin_view(self.import_csv_view), name="pilotboard_pilot_import_csv"),
        ] + super().get_urls()

    # ── CSV import view ───────────────────────────────────────────────────────

    def import_csv_view(self, request):
        if request.method == "POST":
            form = PilotCsvImportForm(request.POST, request.FILES)
            if form.is_valid():
                csv_file = request.FILES["csv_file"]
                overwrite = form.cleaned_data["overwrite"]
                try:
                    text = csv_file.read().decode("utf-8-sig")
                    reader = csv.DictReader(io.StringIO(text))
                    rows = list(reader)
                except Exception as exc:
                    self.message_user(request, f"Could not read file: {exc}", messages.ERROR)
                    return redirect("..")

                created, updated, skipped, errors, detail = _import_rows(rows, overwrite, request.user)

                level = messages.ERROR if errors and not created and not updated else messages.SUCCESS
                self.message_user(
                    request,
                    f"Import complete — Created: {created}, Updated: {updated}, Skipped: {skipped}, Errors: {errors}",
                    level,
                )
                return redirect("../")
        else:
            form = PilotCsvImportForm()

        context = {
            **self.admin_site.each_context(request),
            "title": "Import Pilots from CSV",
            "form": form,
            "opts": self.model._meta,
        }
        return render(request, "admin/pilotboard/pilot/import_csv.html", context)

    # ── Changelist button ─────────────────────────────────────────────────────

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context["import_csv_url"] = "import-csv/"
        return super().changelist_view(request, extra_context=extra_context)

    # ── Display helpers ───────────────────────────────────────────────────────

    def skill_chips(self, obj):
        skulls = lambda n: "☠" * n + "·" * (5 - n)
        return format_html(
            "<small>TF {tf} | Duel {d} | Lead {l}</small>",
            tf=skulls(obj.team_fighting),
            d=skulls(obj.duelling),
            l=skulls(obj.leadership),
        )
    skill_chips.short_description = "Skills"

    def tag_chips(self, obj):
        if not obj.tags:
            return "—"
        labels = {c.value: c.label for c in PilotTag}
        return ", ".join(labels.get(t, t) for t in obj.tags)
    tag_chips.short_description = "Tags"

    def open_goal_title(self, obj):
        goal = obj.open_goal
        return goal.title if goal else "—"
    open_goal_title.short_description = "Current Goal"


# ---------------------------------------------------------------------------
# PilotGoal (standalone for deep editing)
# ---------------------------------------------------------------------------

@admin.register(PilotGoal)
class PilotGoalAdmin(SimpleHistoryAdmin):
    list_display  = ("pilot", "title", "completed", "created_at")
    list_filter   = ("completed",)
    search_fields = ("pilot__name", "title")
    readonly_fields = ("created_at",)
    inlines = [GoalNoteInline]


# ---------------------------------------------------------------------------
# GoalNote
# ---------------------------------------------------------------------------

@admin.register(GoalNote)
class GoalNoteAdmin(admin.ModelAdmin):
    list_display  = ("goal", "author", "short_content", "created_at")
    search_fields = ("content", "author__username", "goal__pilot__name")
    readonly_fields = ("created_at",)

    def short_content(self, obj):
        return obj.content[:60]
    short_content.short_description = "Content"


# ---------------------------------------------------------------------------
# PilotGroupFilter — configures roster filter buttons
# ---------------------------------------------------------------------------

@admin.register(PilotGroupFilter)
class PilotGroupFilterAdmin(admin.ModelAdmin):
    list_display  = ("display_label", "group", "order")
    list_editable = ("order",)
    autocomplete_fields = ("group",)

    def display_label(self, obj):
        return obj.display_label
    display_label.short_description = "Label"
