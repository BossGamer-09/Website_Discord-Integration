from django.db import models
from django.utils import timezone


class EmailMessage(models.Model):
    # IMAP identity
    imap_uid = models.IntegerField(db_index=True)
    message_id = models.CharField(max_length=500, unique=True, db_index=True)

    # Threading
    in_reply_to = models.CharField(max_length=500, blank=True, default="")
    thread_root = models.CharField(max_length=500, blank=True, default="", db_index=True)

    # Headers
    subject = models.CharField(max_length=500, blank=True, default="")
    from_address = models.CharField(max_length=500)
    from_name = models.CharField(max_length=200, blank=True, default="")
    to_address = models.CharField(max_length=500)

    # Body
    body_text = models.TextField(blank=True, default="")
    body_html = models.TextField(blank=True, default="")

    # Flags
    is_read = models.BooleanField(default=False, db_index=True)
    is_outbound = models.BooleanField(default=False)  # True = sent by staff via this system

    received_at = models.DateTimeField(db_index=True)
    fetched_at = models.DateTimeField(default=timezone.now)

    class Meta:
        default_permissions = ()
        ordering = ["received_at"]
        permissions = [
            ("view_inbox", "Can view the shared inbox"),
            ("reply_email", "Can reply to emails"),
        ]

    def __str__(self):
        return f"[{self.received_at:%Y-%m-%d}] {self.subject} — {self.from_address}"

    @property
    def display_name(self):
        return self.from_name or self.from_address
