"""
app/infantryboard/permissions.py

Permission helpers for the infantry roster.
is_staff / is_superuser act as a universal override.
"""


def user_can_view_roster(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_staff or user.is_superuser:
        return True
    return user.has_perm("infantryboard.view_infantry_roster")


def user_can_manage_roster(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_staff or user.is_superuser:
        return True
    return user.has_perm("infantryboard.manage_infantry_roster")
