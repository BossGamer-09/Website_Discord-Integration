from . import viewsets
from dynamic_rest.routers import DynamicRouter
from rest_framework.viewsets import ViewSet
from inspect import isclass, getmembers
from contextlib import suppress


router = DynamicRouter()
for _, viewset in getmembers(viewsets, isclass):
    with suppress(AttributeError):
        router.register(viewset.serializer_class.Meta.name, viewset)

urlpatterns = router.urls
