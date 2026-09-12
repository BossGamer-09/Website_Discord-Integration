from django import template

register = template.Library()


@register.filter
def commafy(value):
    """Format integer with thousands separators."""
    try:
        return f"{int(value):,}"
    except (ValueError, TypeError):
        return value


@register.filter
def auec_suffix(value):
    """Returns the human-readable suffix only: '(4.94B)', '(2.3M)', or ''."""
    try:
        v = int(value)
        if v >= 1_000_000_000:
            return f"({v / 1_000_000_000:.2f}B)"
        if v >= 1_000_000:
            return f"({v / 1_000_000:.2f}M)"
        if v >= 10_000:
            return f"({v / 1_000:.1f}K)"
        return ""
    except (ValueError, TypeError):
        return ""


@register.filter
def prison_time_suffix(value):
    """Returns the prison-time string only: '(443h 26m)' or ''."""
    try:
        v = int(value)
        if v == 0:
            return ""
        h = v // 3600
        m = (v % 3600) // 60
        if h > 0:
            return f"({h}h {m:02d}m)"
        return f"({m}m)"
    except (ValueError, TypeError):
        return ""


# Keep combined versions for backwards compat / Discord use
@register.filter
def auec_display(value):
    """4,938,785,024 (4.94B)"""
    s   = commafy(value)
    sfx = auec_suffix(value)
    return f"{s} {sfx}".strip() if sfx else s


@register.filter
def prison_time_display(value):
    """1,596,400 (443h 26m)"""
    s   = commafy(value)
    sfx = prison_time_suffix(value)
    return f"{s} {sfx}".strip() if sfx else s
