from . import viewsets
from dynamic_rest.routers import DynamicRouter
from inspect import isclass, getmembers
from contextlib import suppress


router = DynamicRouter()
for _, viewset in getmembers(viewsets, isclass):
    with suppress(AttributeError):
        router.register(viewset.serializer_class.Meta.name, viewset)

router.register("user_has_perm", viewsets.UserHasPermAPI)
router.register("bearer_has_perm", viewsets.BearerHasPermAPI)

urlpatterns = router.urls
