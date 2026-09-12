import json
from django.dispatch import receiver
from django.db.models.signals import m2m_changed, post_save, post_delete
from django.contrib.auth.models import Group, Permission
from django.contrib.auth import get_user_model
from django.db.models import Q
from django_redis import get_redis_connection
from guardian.models import UserObjectPermission, GroupObjectPermission


USER_PERMISSION_PUBSUB_CHANNEL = "permissions.unifieduser.sync"


def publish(payload):
    r = get_redis_connection("default")
    r.publish(USER_PERMISSION_PUBSUB_CHANNEL, json.dumps(payload))


def pub_user_perm(user_id, perm, action, obj=None):
    payload = {
        "user_id": str(user_id),
        "permission": perm,
        "type": "User.Permission",
        "action": action,
    }
    if obj:
        payload["obj_id"] = obj.pk
        payload["obj_type"] = f"{obj._meta.app_label}.{obj._meta.model_name}"

    publish(payload)


# 1. User specific permissions (Direct assignment)
@receiver(m2m_changed, sender=get_user_model().user_permissions.through)
def on_user_user_permissions_changed(sender, instance, action, reverse, model, pk_set, **kwargs):
    # Case A: Modifying a User's permissions directly (instance=User)
    if not reverse:
        user = instance
        if action == 'pre_clear':
            # Snapshot IDs only for clear
            user._cleared_pks = list(user.user_permissions.values_list('pk', flat=True))
            return
        
        pks = None
        if action == 'post_clear':
            pks = getattr(user, '_cleared_pks', [])
            if hasattr(user, '_cleared_pks'): del user._cleared_pks
        elif action in ('post_add', 'post_remove'):
            pks = pk_set
            
        if pks:
            verb = "added" if action == 'post_add' else "removed"
            # Permissions involved in this change
            changed_perms = Permission.objects.filter(pk__in=pks).select_related('content_type')
            
            # Permissions user has from Groups (Stable source)
            # We only care if the user has these permissions via groups
            stable_perms_qs = Permission.objects.filter(group__user=user)
            
            # Effective change: Perms involved that are NOT in stable source
            # If Adding: We add P1. If P1 in stable (Groups), effective change is None.
            # If Removing: We remove P1. If P1 in stable (Groups), effective change is None (still have it).
            effective_changed = changed_perms.exclude(pk__in=stable_perms_qs)
            
            for perm in effective_changed:
                perm_string = f"{perm.content_type.app_label}.{perm.codename}"
                pub_user_perm(user.pk, perm_string, verb)

    # Case B: Modifying a Permission's users (instance=Permission, reverse=True)
    # This is rare but possible (Permission.user_set.add(user)).
    else:
        if action in ('post_add', 'post_remove'):
            perm_codename = f"{instance.content_type.app_label}.{instance.codename}"
            users = get_user_model().objects.filter(pk__in=pk_set)

            for user in users:
                # Check if this change actually affected the user's status
                # (i.e., do they have/miss it via groups?)
                has_via_group = user.groups.filter(permissions=instance).exists()
                if user.is_superuser: continue

                if action == 'post_add':
                    if not has_via_group: # Only added if not already present via group
                        pub_user_perm(user.pk, perm_codename, "added")
                elif action == 'post_remove':
                    if not has_via_group: # Only removed if not present via group
                        pub_user_perm(user.pk, perm_codename, "removed")


# 2. Group permissions
@receiver(m2m_changed, sender=Group.permissions.through)
def on_group_permissions_changed(sender, instance, action, reverse, model, pk_set, **kwargs):
    # Case A: Modifying Group's permissions (instance=Group)
    if not reverse:
        if action in ('post_add', 'post_remove'):
            relevant_perms = Permission.objects.filter(pk__in=pk_set).select_related('content_type')

            User = get_user_model()
            group = instance

            for perm in relevant_perms:
                # Find users in this group
                group_users = group.user_set.all()

                # We need to find users for whom this group is the ONLY source (for remove) 
                # or users for whom this group provides a NEW source where none existed (for add).

                perm_string = f"{perm.content_type.app_label}.{perm.codename}"
                # Users who have this perm from OTHER sources (Direct or Other Groups)
                users_with_other_source = User.objects.filter(
                    Q(is_superuser=True) |
                    Q(user_permissions=perm) |
                    (Q(groups__permissions=perm) & ~Q(groups=group))
                )

                # For ADD: Publish to users in group who DO NOT have other source
                if action == 'post_add':
                    affected_users = group_users.exclude(pk__in=users_with_other_source.values('pk'))
                    for user in affected_users:
                        pub_user_perm(user.pk, perm_string, "added")

                # For REMOVE: Publish to users in group who DO NOT have other source
                elif action == 'post_remove':
                    affected_users = group_users.exclude(pk__in=users_with_other_source.values('pk'))
                    for user in affected_users:
                        pub_user_perm(user.pk, perm_string, "removed")

    # Case B: Modifying Permission's groups (instance=Permission, reverse=True)
    else:
        if action in ('post_add', 'post_remove'):

            perm = instance
            perm_string = f"{perm.content_type.app_label}.{perm.codename}"
            groups = Group.objects.filter(pk__in=pk_set)
            User = get_user_model()

            for group in groups:
                group_users = group.user_set.all()
                # Same logic as above, but for each group involved
                users_with_other_source = User.objects.filter(
                    Q(is_superuser=True) |
                    Q(user_permissions=perm) |
                    (Q(groups__permissions=perm) & ~Q(groups=group))
                )

                if action == 'post_add':
                    affected_users = group_users.exclude(pk__in=users_with_other_source.values('pk'))
                    for user in affected_users:
                        pub_user_perm(user.pk, perm_string, "added")
                elif action == 'post_remove':
                    affected_users = group_users.exclude(pk__in=users_with_other_source.values('pk'))
                    for user in affected_users:
                        pub_user_perm(user.pk, perm_string, "removed")


# 3. User Groups (User joining/leaving groups)
@receiver(m2m_changed, sender=get_user_model().groups.through)
def on_user_groups_changed(sender, instance, action, reverse, model, pk_set, **kwargs):
    # Case A: User joining/leaving groups (instance=User)
    if not reverse:
        user = instance
        if action == 'pre_clear':
            user._cleared_group_pks = list(user.groups.values_list('pk', flat=True))
            return

        pks = None
        if action == 'post_clear':
            pks = getattr(user, '_cleared_group_pks', [])
            if hasattr(user, '_cleared_group_pks'): del user._cleared_group_pks
        elif action in ('post_add', 'post_remove'):
            pks = pk_set

        if pks:
            verb = "added" if action == 'post_add' else "removed"
            
            # Permissions granted by the CHANGED groups
            changed_perms = Permission.objects.filter(group__pk__in=pks).select_related('content_type').distinct()
            
            # Permissions the user has from STABLE sources (Direct + Other Groups)
            # We must exclude the groups currently being processed to see what "else" provides the perm
            stable_perms_qs = Permission.objects.filter(
                Q(user=user) | 
                (Q(group__user=user) & ~Q(group__pk__in=pks))
            ).distinct()
            
            # Effective change = Changed Perms NOT present in Stable Sources
            effective_changed = changed_perms.exclude(pk__in=stable_perms_qs)
            
            for perm in effective_changed:
                perm_string = f"{perm.content_type.app_label}.{perm.codename}"
                pub_user_perm(user.pk, perm_string, verb)

    # Case B: Group adding/removing users (instance=Group, reverse=True)
    else:
        if action in ('post_add', 'post_remove'):
            # Check if group has any relevant perms
            group_perms = instance.permissions.all().select_related('content_type')
            if not group_perms.exists():
                return

            User = get_user_model()
            users = User.objects.filter(pk__in=pk_set)

            for perm in group_perms:
                perm_string = f"{perm.content_type.app_label}.{perm.codename}"
                for user in users:
                    # Check if user has this perm from other sources
                    # (We exclude 'instance' group from the check)
                    has_other = User.objects.filter(pk=user.pk).filter(
                        Q(is_superuser=True) |
                        Q(user_permissions=perm) |
                        (Q(groups__permissions=perm) & ~Q(groups=instance))
                    ).exists()

                    if action == 'post_add':
                        if not has_other:
                            pub_user_perm(user.pk, perm_string, "added")
                    elif action == 'post_remove':
                        if not has_other:
                            pub_user_perm(user.pk, perm_string, "removed")


# 4. Guardian Object Permissions (User)
@receiver([post_save, post_delete], sender=UserObjectPermission)
def on_guardian_user_obj_perm_changed(sender, instance, **kwargs):
    user = instance.user
    perm_codename = instance.permission.codename
    perm_full = f"{instance.permission.content_type.app_label}.{perm_codename}"
    obj = instance.content_object

    # Check effective permission
    # If action was save (add), check if they already had it via global or group
    # If action was delete (remove), check if they still have it via global or group

    has_global = user.has_perm(perm_full) # Checks direct/group global
    if has_global:
        return # Redundant change

    # Check GroupObjectPermission
    # user.has_perm(perm, obj) includes GroupObjectPermission check in Guardian backend
    has_effective = user.has_perm(perm_full, obj)

    # For Post Save (Add):
    if 'created' in kwargs: # post_save
        # If they have Global or GroupObjPerm, then adding UserObjPerm was redundant.
        # We know Global is False (checked above).
        # Check GroupObjPerms:
        has_group_obj_perm = GroupObjectPermission.objects.filter(
            group__user=user, permission=instance.permission, object_pk=instance.object_pk
        ).exists()

        if not has_group_obj_perm:
            pub_user_perm(user.pk, perm_full, "added", obj)

    # For Post Delete:
    else: # post_delete
        # If has_effective is False, then they lost it.
        if not has_effective:
            pub_user_perm(user.pk, perm_full, "removed", obj)


# 5. Guardian Object Permissions (Group)
@receiver([post_save, post_delete], sender=GroupObjectPermission)
def on_guardian_group_obj_perm_changed(sender, instance, **kwargs):
    group = instance.group
    perm_codename = instance.permission.codename
    perm_full = f"{instance.permission.content_type.app_label}.{perm_codename}"
    obj = instance.content_object

    users = group.user_set.all()

    for user in users:
        # Check if user has Global perm
        if user.has_perm(perm_full):
            continue

        # Check UserObjectPermission
        has_user_obj_perm = UserObjectPermission.objects.filter(
            user=user, permission=instance.permission, object_pk=instance.object_pk
        ).exists()
        if has_user_obj_perm:
            continue

        # Check *Other* GroupObjectPermissions
        has_other_group_obj_perm = GroupObjectPermission.objects.filter(
            group__user=user, permission=instance.permission, object_pk=instance.object_pk
        ).exclude(pk=instance.pk).exists()
        if has_other_group_obj_perm:
            continue

        # If none of the above, this GroupObjPerm was the deciding factor.
        if 'created' in kwargs: # Add
            pub_user_perm(user.pk, perm_full, "added", obj)
        else: # Remove
            pub_user_perm(user.pk, perm_full, "removed", obj)
