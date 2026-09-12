
_custom_backend = None


def get_custom_backend():
    if _custom_backend:
        return _custom_backend

    from ..oauth.backends import ObjectPermissionOAuth2TokenBackend
    return ObjectPermissionOAuth2TokenBackend


def display_name_from_user_id(user_id):
    from django.contrib.auth import get_user_model
    from django.core.cache import cache
    name = cache.get("uuser-dn-{0}".format(user_id))
    if not name:
        user = get_user_model().objects.get(id=user_id)
        name = user.display_name
    return name
