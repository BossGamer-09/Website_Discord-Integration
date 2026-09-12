from django.db import models


class AnalyticsAccess(models.Model):
    """Dummy model — exists only to host the view_analytics permission."""

    class Meta:
        managed = False
        default_permissions = ()
        permissions = [
            ("view_analytics", "Can view analytics dashboards"),
        ]
