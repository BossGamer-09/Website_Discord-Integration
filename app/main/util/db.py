import asyncio

from django.db import models
from django.db.models import Q
from django.db.models.signals import post_save
from guardian.shortcuts import get_groups_with_perms, get_users_with_perms
from django.contrib.auth import get_user_model
from django.db import models, connections
from ordered_model.models import OrderedModelQuerySet


MAX_CONCURRENT_SIGNALS = 20  # max concurrent asyncio db queries
SIGNALS_CHUNK_SIZE = 1000


def get_groups_with_permission_on(codename, obj=None):
    from django.contrib.auth.models import Group

    # Fetch groups with the global permission
    if "." in codename:
        app_label, codename = codename.split(".", 1)
        global_groups = Group.objects.filter(permissions__content_type__app_label=app_label, permissions__codename=codename)
    else:
        global_groups = Group.objects.filter(permissions__codename=codename)

    # Fetch groups with the specific object-level permission
    # (Returns a QuerySet of Groups)
    if obj:
        obj_level_groups = get_groups_with_perms(obj, only_with_perms_in=[codename])

        # Combine the two QuerySets and remove duplicates
        return (obj_level_groups | global_groups).distinct()
    else:
        return global_groups


def get_users_with_permission_on(codename, obj=None, include_from_groups=True):
    User = get_user_model()

    if "." in codename:
        app_label, codename = codename.split(".", 1)
        q_user = Q(user_permissions__content_type__app_label=app_label, user_permissions__codename=codename)
        q_group = Q(groups__permissions__content_type__app_label=app_label, groups__permissions__codename=codename)
    else:
        q_user = Q(user_permissions__codename=codename)
        q_group = Q(groups__permissions__codename=codename)

    # Fetch users with the global permission
    if include_from_groups:
        global_users = User.objects.filter(q_user | q_group | Q(is_superuser=True))
    else:
        global_users = User.objects.filter(q_user | Q(is_superuser=True))

    if obj:
        # Fetch users with the specific object-level permission
        obj_level_users = get_users_with_perms(obj, with_group_users=include_from_groups, only_with_perms_in=[codename])

        # Ensure both querysets are distinct before union
        obj_level_users = obj_level_users.distinct()
        global_users = global_users.distinct()

        # CRITICAL FIX: Use union() instead of | to avoid "Cannot combine a unique query with a non-unique query"
        # Guardian's get_users_with_perms may return a QuerySet that's already been through union operations
        return obj_level_users.union(global_users)
    else:
        return global_users.distinct()


class OriginalStateMixin:  # Stores original model state on db load/update from db
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # If the object is created manually (not from DB) initialize an empty state or handle as needed.
        if not hasattr(self, '_original_state'):
            self._original_state = {}

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._original_state = dict(zip(field_names, values))
        return instance

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Update original values so they are fresh for the next save
        self._original_state = {
            field.attname: getattr(self, field.attname)
            for field in self._meta.fields
        }

    def get_value_loaded_from_db(self, field_name):
        """Returns the value the field had when loaded from the database."""
        return self._original_state.get(field_name)

    def has_value_changed_from_db(self, field_name):
        """Returns True if the current value differs from the database value."""
        # If it's a new record, it hasn't "changed" from a DB state yet
        if self._state.adding:
            return False

        current_value = getattr(self, field_name)
        original_value = self.get_value_loaded_from_db(field_name)
        return current_value != original_value

    @property
    def changed_fields_from_db(self):
        """Returns a list of field names that have changed."""
        return [
            field for field in self._original_state
            if getattr(self, field) != self._original_state[field]
        ]


class SignalEmittingQuerySet(models.QuerySet):
    # sync
    def update(self, **kwargs):
        objs_to_signal = list(self)  # Fetch objects first
        rows_updated = super().update(**kwargs)

        if rows_updated > 0:
            self._send_signal_for_list(objs_to_signal, kwargs, created=False)
        return rows_updated

    def bulk_create(self, objs, **kwargs):
        created_objs = super().bulk_create(objs, **kwargs)
        self._send_signal_for_list(created_objs, {}, created=True)
        return created_objs

    def bulk_update(self, objs, fields, **kwargs):
        super().bulk_update(objs, fields, **kwargs)
        # Note: objs already have new values in memory
        self._send_signal_for_list(objs, {}, created=False, update_fields=fields)

    # async
    async def aupdate(self, **kwargs):
        objs_to_signal = [obj async for obj in self]
        rows_updated = await super().aupdate(**kwargs)

        if rows_updated > 0:
            await self._send_signal_for_list_async(objs_to_signal, kwargs, created=False)
        return rows_updated

    async def abulk_create(self, objs, **kwargs):
        created_objs = await super().abulk_create(objs, **kwargs)
        await self._send_signal_for_list_async(created_objs, {}, created=True)
        return created_objs

    async def abulk_update(self, objs, fields, **kwargs):
        await super().abulk_update(objs, fields, **kwargs)
        await self._send_signal_for_list_async(objs, {}, created=False, update_fields=fields)

    # sync helper
    def _send_signal_for_list(self, objs, updates, created, update_fields=None):
        for obj in objs:
            # Apply updates to in-memory object (so signal sees NEW value)
            if not created and updates:
                for field, value in updates.items():
                    # Check if 'value' is actually a simple value and not a DB expression
                    if not hasattr(value, 'resolve_expression'):
                        setattr(obj, field, value)

            post_save.send(
                sender=self.model,
                instance=obj,
                created=created,
                update_fields=update_fields,
                raw=False,
                using=self.db
            )

            if hasattr(obj, '_original_state'):
                if update_fields:
                    for field in update_fields:
                        obj._original_state[field] = getattr(obj, field)
                else:
                    # Full refresh for creates or full updates
                    obj._original_state = {
                        f.attname: getattr(obj, f.attname)
                        for f in obj._meta.fields
                    }

    # async helper which limits concurrent db queries
    async def _send_signal_for_list_async(self, objs, updates, created, update_fields=None):
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_SIGNALS)

        async def bounded_signal_dispatch(obj):
            # Patch object if needed (for .update())
            if updates:
                for field, value in updates.items():
                    if not hasattr(value, 'resolve_expression'):
                        setattr(obj, field, value)

            async with semaphore:
                # Use asend for async signal dispatch (Django 5.0+)
                await post_save.asend(
                    sender=self.model,
                    instance=obj,
                    created=created,
                    update_fields=update_fields,
                    raw=False,
                    using=self.db
                )

            if hasattr(obj, '_original_state'):
                if update_fields:
                    for field in update_fields:
                        obj._original_state[field] = getattr(obj, field)
                else:
                    obj._original_state = {
                        f.attname: getattr(obj, f.attname)
                        for f in obj._meta.fields
                    }

        # Iterate over objs in slices
        for i in range(0, len(objs), SIGNALS_CHUNK_SIZE):
            chunk = objs[i : i + SIGNALS_CHUNK_SIZE]

            tasks = [bounded_signal_dispatch(obj) for obj in chunk]

            if tasks:
                await asyncio.gather(*tasks)
                await asyncio.sleep(0)


class SignalEmittingManager(models.Manager):
    def get_queryset(self):
        return SignalEmittingQuerySet(self.model, using=self._db)


class FastApproxCountQuerySet(models.query.QuerySet):
    def count(self, accurate=False):
        if accurate:
            return super().count()

        if self._result_cache is not None:
            return len(self._result_cache)

        if not hasattr(connections[self.db].client.connection, 'pg_version'):
            return super().count()

        query = self.query

        if not query.where and query.high_mark is None and query.low_mark == 0 and not query.select and not query.group_by and not query.distinct:
            parts = [p.strip('"') for p in self.model._meta.db_table.split('.')]
            cursor = connections[self.db].cursor()
            if len(parts) == 1:
                cursor.execute("select reltuples::bigint FROM pg_class WHERE relname = %s", parts)
            else:
                cursor.execute("select reltuples::bigint FROM pg_class c JOIN pg_namespace n on (c.relnamespace = n.oid) WHERE n.nspname = %s AND c.relname = %s", parts)
            return cursor.fetchall()[0][0]

        return super().count()


class DBAwareModelMixin:
    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._state.adding = False
        instance._state.db = db
        instance._loaded_values = dict(zip(field_names, (value for value in values if value is not models.DEFERRED)))
        return instance

    def original_value(self, field, default=None):
        try:
            return self._loaded_values[field]
        except Exception:
            return default

    def data_changed(self, fields):
        if self._state.adding or not hasattr(self, '_loaded_values') or not self._loaded_values:
            return True
        for field in fields:
            if getattr(self, field) != self._loaded_values[field]:
                return True
        return False

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

        if not hasattr(self, '_loaded_values'):
            self._loaded_values = {}

        for field in self._meta.concrete_fields:
            self._loaded_values[field.attname] = getattr(self, field.attname)


class RestrictedQuerySetMixin:
    def bulk_create(self, *args, **kwargs):
        raise AttributeError("Method 'bulk_create' is not supported for '{}', params: {} / {}".format(self.__class__, args, kwargs))

    def bulk_update(self, *args, **kwargs):
        raise AttributeError("Method 'bulk_update' is not supported for '{}', params: {} / {}".format(self.__class__, args, kwargs))

    def update(self, *args, **kwargs):
        raise AttributeError("Method 'update' is not supported for '{}', params: {} / {}".format(self.__class__, args, kwargs))

    def delete(self, *args, **kwargs):
        raise AttributeError("Method 'delete' is not supported for '{}', params: {} / {}".format(self.__class__, args, kwargs))


class RestrictedQuerySet(RestrictedQuerySetMixin, models.QuerySet):
    pass


class OrderedRestrictedQuerySet(RestrictedQuerySetMixin, OrderedModelQuerySet):
    pass