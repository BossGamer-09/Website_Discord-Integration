import discord
from discord import app_commands
from django.contrib.auth import get_user_model
from django.apps import apps
from django.core.cache import cache


OBJECT_PERM_CACHE_TTL_SECONDS = 14
PERM_CACHE_TTL_SECONDS = 14

class MissingDjangoPermission(app_commands.CheckFailure):
    """Custom error raised when a user lacks the Django permission."""
    def __init__(self, missing_perm: str, cached: bool = False):
        self.missing_perm = missing_perm
        self.cached = cached
        super().__init__(f"You lack the required Django permission: {missing_perm}")


class MissingDjangoObjectPermission(app_commands.CheckFailure):
    def __init__(self, missing_perm: str, obj_name: str, cached: bool = False):
        self.cached = cached
        self.missing_perm = missing_perm
        self.obj_name = obj_name
        super().__init__(f"You lack the required Django permission: '{missing_perm}' for '{obj_name}'.")


class AccountNotLinked(app_commands.CheckFailure):
    """Custom error raised when the Discord user isn't in the Django DB."""
    def __init__(self, cached: bool = False):
        self.cached = cached
        super().__init__("Error: Discord account not linked to User Model!")


class UnregisteredObjectError(app_commands.CheckFailure):
    def __init__(self, actual_model_class):
        self.actual_model_class = actual_model_class
        super().__init__(f"The specified {actual_model_class.__name__} is not registered.")


def requires_django_perm(codename: str):
    """
    A decorator to restrict a command to users with a specific Django permission.
    Example: @requires_django_perm("game.change_faction")
    """
    async def predicate(interaction: discord.Interaction) -> bool:
        return await _verify_and_cache_generic_permission(interaction.user.id, codename)

    return app_commands.check(predicate)


async def _verify_and_cache_permission(discord_id: int, codename: str, target_obj) -> bool:
    """
    Core engine that checks Guardian permissions and caches the result
    in Django's cache backend for 10 seconds.
    """
    User = get_user_model()

    # Create a safe, unique string key for Django's cache
    # Django cache keys must be strings, so we format the data into a clean identifier.
    app_label = target_obj._meta.app_label
    model_name = target_obj._meta.model_name
    cache_key = f"discord_perm_{discord_id}_{codename}_{app_label}_{model_name}_{target_obj.pk}"

    # Check the Django cache asynchronously!
    # aget() returns None if the key doesn't exist or has expired.
    cached_result = await cache.aget(cache_key)

    # We explicitly check for 'is not None' because the cached result might be False
    # (We want to cache rejections too, so users can't spam the DB by failing repeatedly)
    if cached_result is not None:
        if cached_result == "VALID":
            return True
        elif cached_result == "MissingDjangoObjectPermission":
            raise MissingDjangoObjectPermission(codename, str(target_obj), cached=True)
        elif cached_result == "AccountNotLinked":
            raise AccountNotLinked(cached=True)

    # CACHE MISS: Hit the database
    try:
        user = await User.objects.aget(discorduser__discorduid=discord_id)
    except User.DoesNotExist:
        await cache.aset(cache_key, "AccountNotLinked", OBJECT_PERM_CACHE_TTL_SECONDS)
        raise AccountNotLinked()

    # Ask Django/Guardian via the async permission check
    has_perm = await user.ahas_perm(codename, target_obj)

    # Save the result to the Django cache asynchronously
    # Django will automatically delete this key after CACHE_TTL_SECONDS
    await cache.aset(cache_key, "VALID" if has_perm else "MissingDjangoObjectPermission", OBJECT_PERM_CACHE_TTL_SECONDS)

    if not has_perm:
        raise MissingDjangoObjectPermission(codename, str(target_obj))

    return True


async def _verify_and_cache_generic_permission(discord_id: int, codename: str) -> bool:
    """
    Core engine that checks permissions and caches the result
    in Django's cache backend for 10 seconds.
    """
    User = get_user_model()

    # Create a safe, unique string key for Django's cache
    # Django cache keys must be strings, so we format the data into a clean identifier.
    cache_key = f"discord_perm_{discord_id}_{codename}_GENERIC"

    # Check the Django cache asynchronously!
    # aget() returns None if the key doesn't exist or has expired.
    cached_result = await cache.aget(cache_key)

    # We explicitly check for 'is not None' because the cached result might be False
    # (We want to cache rejections too, so users can't spam the DB by failing repeatedly)
    if cached_result is not None:
        if cached_result == "VALID":
            return True
        elif cached_result == "MissingDjangoPermission":
            raise MissingDjangoPermission(codename, cached=True)
        elif cached_result == "AccountNotLinked":
            raise AccountNotLinked(cached=True)

    # CACHE MISS: Hit the database
    try:
        user = await User.objects.aget(discorduser__discorduid=discord_id)
    except User.DoesNotExist:
        await cache.aset(cache_key, "AccountNotLinked", OBJECT_PERM_CACHE_TTL_SECONDS)
        raise AccountNotLinked()

    # Ask Django/Guardian via the async permission check
    has_perm = await user.ahas_perm(codename)

    # Save the result to the Django cache asynchronously
    # Django will automatically delete this key after CACHE_TTL_SECONDS
    await cache.aset(cache_key, "VALID" if has_perm else "MissingDjangoPermission", OBJECT_PERM_CACHE_TTL_SECONDS)

    if not has_perm:
        raise MissingDjangoPermission(codename)

    return True


def requires_django_object_perm_by_arg(codename: str, arg_name: str, model_class, lookup_field: str = 'pk'):
    """Checks permissions against an explicitly provided command argument."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if isinstance(interaction.user, discord.User):
            return False

        if isinstance(model_class, str):
            try:
                actual_model_class = apps.get_model(model_class)
            except LookupError:
                raise app_commands.CheckFailure(f"Django model '{model_class}' could not be found.")
        else:
            actual_model_class = model_class

        # Strict extraction for arguments
        if not hasattr(interaction.namespace, arg_name):
            raise app_commands.CheckFailure(f"Missing required argument: {arg_name}")

        arg_value = getattr(interaction.namespace, arg_name)
        lookup_value = getattr(arg_value, 'id', arg_value)

        try:
            target_obj = await actual_model_class.objects.aget(**{lookup_field: lookup_value})
        except actual_model_class.DoesNotExist:
            raise UnregisteredObjectError(actual_model_class)

        # Hand off to the cache
        return await _verify_and_cache_permission(interaction.user.id, codename, target_obj)

    return app_commands.check(predicate)


def requires_django_object_perm_by_interaction_attr(codename: str, attr_path: str, model_class, lookup_field: str = 'pk'):
    """Checks permissions against a contextual attribute (e.g., 'channel_id')."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if isinstance(interaction.user, discord.User):
            return False

        if isinstance(model_class, str):
            try:
                actual_model_class = apps.get_model(model_class)
            except LookupError:
                raise app_commands.CheckFailure(f"Django model '{model_class}' could not be found.")
        else:
            actual_model_class = model_class

        # Dynamic dotted-path traversal (e.g., "channel.category_id")
        attr_value = interaction
        try:
            for attr in attr_path.split('.'):
                attr_value = getattr(attr_value, attr)
        except AttributeError:
            raise app_commands.CheckFailure(f"Interaction has no attribute: {attr_path}")

        lookup_value = getattr(attr_value, 'id', attr_value)

        try:
            target_obj = await actual_model_class.objects.aget(**{lookup_field: lookup_value})
        except actual_model_class.DoesNotExist:
            raise UnregisteredObjectError(actual_model_class)

        # Hand off to the cache
        return await _verify_and_cache_permission(interaction.user.id, codename, target_obj)

    return app_commands.check(predicate)


def requires_django_object_perm_by_arg_or_interaction_attr(codename: str, arg_name: str, attr_path: str, model_class, lookup_field: str = 'pk'):
    """Checks permissions against an explicitly provided command argument or a contextual attribute (e.g., 'channel_id')."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if isinstance(interaction.user, discord.User):
            return False

        if isinstance(model_class, str):
            try:
                actual_model_class = apps.get_model(model_class)
            except LookupError:
                raise app_commands.CheckFailure(f"Django model '{model_class}' could not be found.")
        else:
            actual_model_class = model_class

        # Try to resolve from argument first
        # getattr with None default handles cases where argument is optional and None
        resolved_obj = getattr(interaction.namespace, arg_name, None)

        if resolved_obj is None:
            # Fallback: Dynamic dotted-path traversal (e.g., "channel")
            resolved_obj = interaction
            try:
                for attr in attr_path.split('.'):
                    resolved_obj = getattr(resolved_obj, attr)
            except AttributeError:
                raise app_commands.CheckFailure(f"Permission Check Error: Argument '{arg_name}' missing and fallback attribute '{attr_path}' invalid.")

        # Extract ID (works for Discord objects with .id or direct values)
        lookup_value = getattr(resolved_obj, 'id', resolved_obj)

        try:
            target_obj = await actual_model_class.objects.aget(**{lookup_field: lookup_value})
        except actual_model_class.DoesNotExist:
            raise UnregisteredObjectError(actual_model_class)

        # Hand off to the cache
        return await _verify_and_cache_permission(interaction.user.id, codename, target_obj)

    return app_commands.check(predicate)
