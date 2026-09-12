"""
app/schedevents/permissions.py

Thin permission helpers used by views.  Mirrors the scorecard/permissions.py pattern.
All checks respect is_staff/is_superuser as a universal override.
"""
from app.schedevents.models import EventPlan


def user_can_view_events(user) -> bool:
    return bool(user and user.is_authenticated)


def user_can_create_events(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_staff or user.is_superuser:
        return True
    return user.has_perm("schedevents.add_eventplan")


def user_can_publish_events(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_staff or user.is_superuser:
        return True
    return user.has_perm("schedevents.can_publish_events")


def user_can_manage_all_events(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_staff or user.is_superuser:
        return True
    return user.has_perm("schedevents.can_manage_all_events")


def user_can_edit_event(user, plan: EventPlan) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_staff or user.is_superuser:
        return True
    if user_can_manage_all_events(user):
        return True
    # Creator or organizer can edit their own events
    try:
        from app.unifieduser.models import OrgPlayer
        org_user = OrgPlayer.objects.get(discorduser__discorduid=user.discorduser.discorduid)
        if plan.created_by_id == org_user.pk:
            return user.has_perm("schedevents.can_modify_delete_created")
        if plan.organizers.filter(pk=org_user.pk).exists():
            return user.has_perm("schedevents.can_modify_delete_organizer")
    except Exception:
        pass
    return False


def user_can_force_checkin(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_staff or user.is_superuser:
        return True
    return user.has_perm("schedevents.can_force_checkin")


def get_org_player(user):
    """Return the OrgPlayer for a Django auth user, or None.

    OrgPlayer IS the AUTH_USER_MODEL, so request.user is already the OrgPlayer.
    Just validate it's authenticated and is actually an OrgPlayer instance.
    """
    from app.unifieduser.models import OrgPlayer
    if user and getattr(user, "is_authenticated", False) and isinstance(user, OrgPlayer):
        return user
    return None
