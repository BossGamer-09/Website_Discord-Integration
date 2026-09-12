"""
app/merits/prison_time.py

Convert Star Citizen prison time strings to merit points (seconds).
1 second of prison time = 1 merit point.
"""
import re


_PATTERN = re.compile(
    r"(?:(\d+)\s*h(?:ours?)?)?\s*(?:(\d+)\s*m(?:in(?:utes?)?)?)?",
    re.IGNORECASE,
)


def parse_prison_time(time_string: str) -> int | None:
    """
    Parse a prison time string like '1h 30m', '45m', '2h', '90 minutes'.
    Returns total seconds (= merit points), or None if the string is invalid/zero.
    """
    match = _PATTERN.match(time_string.strip())
    if not match:
        return None
    hours   = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    total   = (hours * 3600) + (minutes * 60)
    return total if total > 0 else None


def format_prison_time(seconds: int) -> str:
    """Format seconds back to a readable string like '1h 30m 15s'."""
    if not seconds or seconds <= 0:
        return "0s"
    h  = seconds // 3600
    m  = (seconds % 3600) // 60
    s  = seconds % 60
    parts = []
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    if s or not parts: parts.append(f"{s}s")
    return " ".join(parts)
