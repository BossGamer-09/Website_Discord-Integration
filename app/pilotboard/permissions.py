"""
app/pilotboard/permissions.py

Permission helpers for the pilot roster.  All checks respect
is_staff/is_superuser as a universal override.
"""


def user_can_view_roster(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_staff or user.is_superuser:
        return True
    return user.has_perm("pilotboard.view_pilot_roster")


def user_can_manage_roster(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_staff or user.is_superuser:
        return True
    return user.has_perm("pilotboard.manage_pilot_roster")
