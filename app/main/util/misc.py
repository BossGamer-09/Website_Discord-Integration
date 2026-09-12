from datetime import datetime, timezone
from rest_framework import renderers
from rest_framework.utils import encoders

from ulid import ULID


def	utcnow_aware():
    return datetime.now(timezone.utc)


class ULIDJSONEncoder(encoders.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, ULID):
            return obj.str
        return super().default(obj)


class ULIDJSONRenderer(renderers.JSONRenderer):
    encoder_class = ULIDJSONEncoder
