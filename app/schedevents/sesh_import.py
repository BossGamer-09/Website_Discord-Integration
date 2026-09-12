"""
app/schedevents/sesh_import.py

Parser for Sesh.fyi event info dump text format.

Usage:
    from app.schedevents.sesh_import import parse_sesh_dump
    result = parse_sesh_dump(raw_text)
    # result is a dict ready to feed into EventPlan creation
"""
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Any

# ---------------------------------------------------------------------------
# Section / sub-key registry
# ---------------------------------------------------------------------------

# Top-level section keys (case-sensitive, exactly as Sesh outputs them)
_SECTION_KEYS: set[str] = {
    "Name", "Start Time", "Duration", "Description", "Channel",
    "Repeat", "Repeat Timezone", "RSVP Options", "Color",
    "Mentions on Create", "Role Restrictions", "Hide Attendees",
    "Image URL", "Attendee Roles", "Thread Settings", "Notifications",
    "RSVP Lock Before Time",
}

# Sub-keys that are only valid inside a specific parent section
_SECTION_SUBKEYS: dict[str, set[str]] = {
    "Repeat":          {"Interval", "Allow Maintain RSVP"},
    "Attendee Roles":  {"Roles", "Role Add Time", "Remove on End"},
    "Thread Settings": {"Enabled", "Thread Title", "Auto Archive Duration", "Join on RSVP", "Start Message"},
    "Notifications":   {"Channel", "Mentions", "Title"},
}

# ---------------------------------------------------------------------------
# Value normalisation helpers
# ---------------------------------------------------------------------------

def _bool(value: str) -> bool:
    return value.strip().lower() in ("true", "yes", "1", "enabled")


def _parse_sesh_start_time(date_str: str, tz_name: str) -> datetime | None:
    """Parse 'Wednesday, March 25, 2026 7am' → UTC-aware datetime."""
    s = date_str.strip()
    # Strip leading day-of-week "Wednesday, "
    s = re.sub(r'^[A-Za-z]+,\s*', '', s)
    # Insert space before am/pm if missing: "7am" → "7 am"
    s = re.sub(r'(\d)(am|pm)\b', r'\1 \2', s, flags=re.IGNORECASE)
    s = s.strip().upper()

    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, Exception):
        tz = ZoneInfo("UTC")

    for fmt in ("%B %d, %Y %I %p", "%B %d, %Y %I:%M %p"):
        try:
            local_dt = datetime.strptime(s, fmt)
            return local_dt.replace(tzinfo=tz)
        except ValueError:
            continue
    return None


_SESH_INTERVAL_TO_RRULE: dict[str, str] = {
    "every day":       "FREQ=DAILY",
    "every 2 days":    "FREQ=DAILY;INTERVAL=2",
    "every 3 days":    "FREQ=DAILY;INTERVAL=3",
    "every week":      "FREQ=WEEKLY",
    "every 2 weeks":   "FREQ=WEEKLY;INTERVAL=2",
    "every 3 weeks":   "FREQ=WEEKLY;INTERVAL=3",
    "every month":     "FREQ=MONTHLY",
    "every year":      "FREQ=YEARLY",
    "every weekday":   "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
    "every weekend":   "FREQ=WEEKLY;BYDAY=SA,SU",
    "every monday":    "FREQ=WEEKLY;BYDAY=MO",
    "every tuesday":   "FREQ=WEEKLY;BYDAY=TU",
    "every wednesday": "FREQ=WEEKLY;BYDAY=WE",
    "every thursday":  "FREQ=WEEKLY;BYDAY=TH",
    "every friday":    "FREQ=WEEKLY;BYDAY=FR",
    "every saturday":  "FREQ=WEEKLY;BYDAY=SA",
    "every sunday":    "FREQ=WEEKLY;BYDAY=SU",
}

_ARCHIVE_DURATION_MAP: dict[str, str] = {
    "onehour":    "OneHour",
    "1 hour":     "OneHour",
    "60 minutes": "OneHour",
    "oneday":     "OneDay",
    "1 day":      "OneDay",
    "threedays":  "ThreeDays",
    "3 days":     "ThreeDays",
    "oneweek":    "OneWeek",
    "1 week":     "OneWeek",
    "sevendays":  "OneWeek",
    "7 days":     "OneWeek",
}


def _parse_notification_time(time_str: str) -> int | None:
    """'0 seconds before' / '30 minutes before' / '1 hour before' → minutes_before int."""
    m = re.match(r'(\d+)\s+(second|minute|hour)s?\s+before', time_str.strip(), re.IGNORECASE)
    if not m:
        return None
    value, unit = int(m.group(1)), m.group(2).lower()
    if unit == "second":
        return value // 60
    if unit == "minute":
        return value
    if unit == "hour":
        return value * 60
    return None


def _role_name(raw: str) -> str:
    """Strip leading @ from role mentions like '@Division - BlightVeil Legion'."""
    return raw.lstrip("@").strip()

# ---------------------------------------------------------------------------
# Core segment parser
# ---------------------------------------------------------------------------

def _parse_to_segments(text: str) -> list[tuple[str, list[str]]]:
    """
    Walk through the dump line by line, splitting on known keys.
    Returns [(key, [value_line, ...]), ...].
    """
    lines = [ln.rstrip() for ln in text.splitlines()]
    segments: list[tuple[str, list[str]]] = []
    current_key: str | None = None
    current_values: list[str] = []
    current_section: str | None = None  # parent section for sub-key resolution

    for raw_line in lines:
        line = raw_line.strip()

        is_top = line in _SECTION_KEYS
        is_sub = (
            current_section is not None
            and line in _SECTION_SUBKEYS.get(current_section, set())
        )

        if is_top or is_sub:
            if current_key is not None:
                segments.append((current_key, current_values))
            current_key = line
            current_values = []
            if is_top:
                current_section = line
        else:
            if current_key is not None and line:
                current_values.append(line)

    if current_key is not None:
        segments.append((current_key, current_values))

    return segments

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_sesh_dump(text: str) -> dict[str, Any]:
    """
    Parse a Sesh.fyi event info dump into a dict of EventPlan-compatible values.

    Returned keys:
      title                    str
      planned_start_at         datetime | None  (tz-aware)
      planned_duration         timedelta | None  (None if "None" in dump)
      description              str
      timezone_name            str
      recurring_rule           str | None
      allow_maintain_rsvp      bool
      color_hex                str | None
      hide_attendees           bool
      cover_image_url          str | None
      mentions_on_create       list[str]  (raw role name strings, no IDs)
      role_restriction_names   list[str]
      rsvp_option_specs        list[dict]  each: {emoji, label}
      thread_enabled           bool
      thread_title_template    str
      thread_auto_archive_duration  str
      thread_join_on_rsvp      bool
      thread_start_message_type str
      attendee_role_specs      list[dict]  each: {role_name, add_time, remove_on_end}
      notification_specs       list[dict]  each: {minutes_before, channel_name, mentions, title_template}
      rsvp_lock_minutes_before int | None
      warnings                 list[str]
    """
    segments = _parse_to_segments(text)
    seg_dict: dict[str, list[str]] = {}
    # Some keys can appear multiple times (Notifications sub-keys); handle via list index
    seg_list = segments  # keep ordered list for section-aware parsing

    # Build flat dict for simple keys
    for key, values in seg_list:
        seg_dict[key] = values

    warnings: list[str] = []

    # -- timezone (parsed early as it's needed for start time) --
    tz_name = (seg_dict.get("Repeat Timezone") or ["UTC"])[0].strip() or "UTC"
    try:
        ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, Exception):
        warnings.append(f"Unknown timezone '{tz_name}', defaulting to UTC.")
        tz_name = "UTC"

    # -- title --
    title = (seg_dict.get("Name") or [""])[0].strip()
    if not title:
        warnings.append("Name not found in dump.")

    # -- start time --
    start_raw_parts = seg_dict.get("Start Time") or []
    start_raw = " ".join(start_raw_parts).strip()
    planned_start_at = None
    if start_raw:
        planned_start_at = _parse_sesh_start_time(start_raw, tz_name)
        if planned_start_at is None:
            warnings.append(f"Could not parse Start Time: '{start_raw}'. Set it manually.")

    # -- duration --
    dur_parts = seg_dict.get("Duration") or ["None"]
    dur_raw = " ".join(dur_parts).strip()
    planned_duration: timedelta | None = None
    if dur_raw.lower() not in ("none", ""):
        # Try parsing "1h", "1h 30m", "90m", "1 hour", "1 hour 30 minutes"
        dur_raw_norm = dur_raw.lower()
        m = re.match(r'(\d+)\s*h(?:our)?s?\s*(?:(\d+)\s*m(?:in)?s?)?', dur_raw_norm)
        if m:
            h = int(m.group(1))
            mins = int(m.group(2)) if m.group(2) else 0
            planned_duration = timedelta(hours=h, minutes=mins)
        else:
            m2 = re.match(r'(\d+)\s*m(?:in)?s?', dur_raw_norm)
            if m2:
                planned_duration = timedelta(minutes=int(m2.group(1)))
            else:
                warnings.append(f"Could not parse Duration: '{dur_raw}'.")

    # -- description --
    description = "\n".join(seg_dict.get("Description") or []).strip()

    # -- recurring --
    interval_raw = " ".join(seg_dict.get("Interval") or []).strip()
    recurring_rule: str | None = None
    if interval_raw:
        recurring_rule = _SESH_INTERVAL_TO_RRULE.get(interval_raw.lower())
        if not recurring_rule:
            warnings.append(f"Unrecognised repeat interval '{interval_raw}'. Set RRULE manually.")

    allow_maintain_rsvp = _bool(" ".join(seg_dict.get("Allow Maintain RSVP") or ["false"]))

    # -- color --
    color_raw = " ".join(seg_dict.get("Color") or []).strip()
    color_hex: str | None = None
    if color_raw and re.fullmatch(r'#[0-9a-fA-F]{6}', color_raw):
        color_hex = color_raw
    elif color_raw and color_raw.lower() not in ("none", ""):
        warnings.append(f"Unrecognised color value '{color_raw}', ignoring.")

    # -- hide attendees --
    hide_attendees = _bool(" ".join(seg_dict.get("Hide Attendees") or ["false"]))

    # -- image URL --
    image_parts = seg_dict.get("Image URL") or []
    cover_image_url: str | None = " ".join(image_parts).strip() or None
    if cover_image_url and not cover_image_url.startswith("http"):
        warnings.append(f"Image URL '{cover_image_url}' doesn't look valid.")
        cover_image_url = None

    # -- mentions on create --
    mentions_on_create = [
        _role_name(r) for r in (seg_dict.get("Mentions on Create") or [])
        if r.strip() and r.strip().lower() != "none"
    ]

    # -- role restrictions --
    role_restriction_names = [
        _role_name(r) for r in (seg_dict.get("Role Restrictions") or [])
        if r.strip() and r.strip().lower() != "none"
    ]

    # -- RSVP options --
    rsvp_option_specs: list[dict] = []
    for item in (seg_dict.get("RSVP Options") or []):
        item = item.strip()
        if not item:
            continue
        # Each item can be "emoji Label" or just an emoji
        parts = item.split(None, 1)
        emoji = parts[0]
        label = parts[1] if len(parts) > 1 else emoji
        rsvp_option_specs.append({"emoji": emoji, "label": label})

    # -- thread settings --
    thread_enabled = _bool(" ".join(seg_dict.get("Enabled") or ["true"]))
    thread_title_template = " ".join(seg_dict.get("Thread Title") or ["$EventName"]).strip() or "$EventName"
    archive_raw = " ".join(seg_dict.get("Auto Archive Duration") or ["OneDay"]).strip()
    thread_auto_archive_duration = _ARCHIVE_DURATION_MAP.get(archive_raw.lower(), archive_raw or "OneDay")
    thread_join_on_rsvp = _bool(" ".join(seg_dict.get("Join on RSVP") or ["true"]))
    thread_start_raw = " ".join(seg_dict.get("Start Message") or ["Thread"]).strip()
    # Map Sesh "Thread" / "Channel" / "None" → our choices
    thread_start_message_type = thread_start_raw if thread_start_raw in ("Thread", "Channel", "None") else "Thread"

    # -- attendee roles --
    attendee_role_specs: list[dict] = []
    roles_raw = [
        r.strip() for r in (seg_dict.get("Roles") or [])
        if r.strip() and r.strip().lower() not in ("none", "")
    ]
    add_time_raw = " ".join(seg_dict.get("Role Add Time") or ["RSVP"]).strip()
    add_time = "EVENT_START" if "start" in add_time_raw.lower() else "RSVP"
    remove_on_end = _bool(" ".join(seg_dict.get("Remove on End") or ["true"]))
    for role_name in roles_raw:
        attendee_role_specs.append({
            "role_name": _role_name(role_name),
            "add_time": add_time,
            "remove_on_end": remove_on_end,
        })

    # -- notifications --
    # Walk the segment list to extract Notifications block (handles sub-keys in order)
    notification_specs: list[dict] = []
    in_notif = False
    notif: dict = {}
    for key, values in seg_list:
        if key == "Notifications":
            in_notif = True
            # First value line is the timing ("0 seconds before", "30 minutes before", etc.)
            timing_line = " ".join(values).strip()
            minutes_before = _parse_notification_time(timing_line)
            if minutes_before is None and timing_line:
                warnings.append(f"Could not parse notification time '{timing_line}', defaulting to 0.")
            notif = {
                "minutes_before": minutes_before or 0,
                "channel_name": None,
                "mentions": [],
                "title_template": None,
            }
        elif in_notif:
            if key == "Channel":
                notif["channel_name"] = " ".join(values).strip().lstrip("#") or None
            elif key == "Mentions":
                notif["mentions"] = [_role_name(r) for r in values if r.strip()]
            elif key == "Title":
                notif["title_template"] = " ".join(values).strip() or None
                # Title is typically the last sub-key, save and reset
                notification_specs.append(notif)
                notif = {}
                in_notif = False
            else:
                # Hit a new top-level key; save whatever we have
                if notif:
                    notification_specs.append(notif)
                    notif = {}
                in_notif = False
    if notif:
        notification_specs.append(notif)

    # -- RSVP lock --
    lock_raw = " ".join(seg_dict.get("RSVP Lock Before Time") or ["None"]).strip()
    rsvp_lock_minutes_before: int | None = None
    if lock_raw.lower() not in ("none", ""):
        rsvp_lock_minutes_before = _parse_notification_time(lock_raw)
        if rsvp_lock_minutes_before is None:
            warnings.append(f"Could not parse RSVP Lock Before Time '{lock_raw}'.")

    return {
        "title": title,
        "planned_start_at": planned_start_at,
        "planned_duration": planned_duration,
        "description": description,
        "timezone_name": tz_name,
        "recurring_rule": recurring_rule,
        "allow_maintain_rsvp": allow_maintain_rsvp,
        "color_hex": color_hex,
        "hide_attendees": hide_attendees,
        "cover_image_url": cover_image_url,
        "mentions_on_create": mentions_on_create,
        "role_restriction_names": role_restriction_names,
        "rsvp_option_specs": rsvp_option_specs,
        "thread_enabled": thread_enabled,
        "thread_title_template": thread_title_template,
        "thread_auto_archive_duration": thread_auto_archive_duration,
        "thread_join_on_rsvp": thread_join_on_rsvp,
        "thread_start_message_type": thread_start_message_type,
        "attendee_role_specs": attendee_role_specs,
        "notification_specs": notification_specs,
        "rsvp_lock_minutes_before": rsvp_lock_minutes_before,
        "warnings": warnings,
    }
