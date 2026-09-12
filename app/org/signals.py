import json

from django.dispatch import receiver
from django.db.models.signals import m2m_changed
from django.contrib.auth.models import Group
from django.core.exceptions import *
from django.contrib.auth import get_user_model
from django.db import transaction

from django_redis import get_redis_connection
from app.unifieduser.models import OrgRank


PUBSUB_CHANNEL = "app.org.sync"


def publish(payload):
    r = get_redis_connection("default")
    r.publish(PUBSUB_CHANNEL, json.dumps(payload))


@receiver(signal=m2m_changed, sender=get_user_model().groups.through)
def on_user_group_m2m_changed(instance, action, reverse, model, pk_set, *args, **kwargs):
    if model == Group and not reverse:  # e.g., user.groups.add(), remove(), clear()

        if action == 'post_remove':  # user left groups
            user = instance
            groups = Group.objects.filter(pk__in=pk_set)
            for group in groups:
                for role in group.discord_roles.all():
                    role._on_users_remove([user])

        elif action == 'post_add':  # user joined groups
            user = instance
            groups = Group.objects.filter(pk__in=pk_set)
            for group in groups:
                for role in group.discord_roles.all():
                    role._on_users_add([user])

        elif action == 'pre_clear':
            user = instance
            groups = user.groups.all()
            for group in groups:
                for role in group.discord_roles.all():
                    role._on_users_remove([user])

    else:  # e.g., group.user_set.add(), remove(), clear()

        if action == 'post_remove':  # users left group
            group = instance
            users = get_user_model().objects.filter(pk__in=pk_set)

            for role in group.discord_roles.all():
                role._on_users_remove(users)

        elif action == 'post_add':  # users joined group
            group = instance
            users = get_user_model().objects.filter(pk__in=pk_set)

            for role in group.discord_roles.all():
                role._on_users_add(users)

        elif action == 'pre_clear':
            group = instance
            users = group.user_set.all()
            for role in group.discord_roles.all():
                role._on_users_remove(users)


def pub_user_add(instance):
    if getattr(instance, '_skip_signals', False):
        return

    publish({
        "action": "User.add",
        "type": "org.DiscordRole",
        "id": str(instance.pk)
    })


def pub_user_remove(instance):
    if getattr(instance, '_skip_signals', False):
        return

    publish({
        "action": "User.remove",
        "type": "org.DiscordRole",
        "id": str(instance.pk)
    })


@receiver(m2m_changed, sender=OrgRank.groups.through)
def sync_rank_groups_on_modification(sender, instance, action, pk_set, model, **kwargs):
    """
    If a Rank's groups are updated, apply those changes directly to all custom users currently holding that rank.
    """
    if action == "post_add":
        groups = model.objects.filter(pk__in=pk_set)
        for user in instance.users.all():
            user.groups.add(*groups)

    elif action == "post_remove":
        groups = model.objects.filter(pk__in=pk_set)
        for user in instance.users.all():
            user.groups.remove(*groups)

    elif action == "pre_clear":
        groups = instance.groups.all()
        for user in instance.users.all():
            user.groups.remove(*groups)


def pub_rank_change(instance, before_pk, after_pk):
    if getattr(instance, '_skip_signals', False):
        return

    publish({
        "action": "User.update",
        "type": "unifieduser.OrgRank",
        "id": str(instance.pk),
        "rank_before_id": str(before_pk),
        "rank_after_id": str(after_pk),
    })

    # Write a DecisionLog entry for audit trail
    try:
        from app.leadership.models import DecisionLog
        event_type = (
            DecisionLog.EventType.PROMOTION if after_pk and (not before_pk or after_pk > before_pk)
            else DecisionLog.EventType.DEMOTION
        )
        DecisionLog.objects.create(
            event_type=event_type,
            subject=instance,
            actor=None,
            summary=f'Rank changed from pk={before_pk} to pk={after_pk}',
            rank_before_id=before_pk,
            rank_after_id=after_pk,
        )
    except Exception:
        pass  # never block the rank save


def pub_rank_update(instance):
    if getattr(instance, '_skip_signals', False):
        return

    publish({
        "action": "User.OrgRank.rename",
        "type": "unifieduser.User",
        "id": str(instance.pk)
    })
