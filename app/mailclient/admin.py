from django.contrib import admin
from app.mailclient.models import EmailMessage


@admin.register(EmailMessage)
class EmailMessageAdmin(admin.ModelAdmin):
    list_display = ("subject", "from_address", "to_address", "received_at", "is_read", "is_outbound")
    list_filter = ("is_read", "is_outbound")
    search_fields = ("subject", "from_address", "to_address", "body_text")
    readonly_fields = ("message_id", "imap_uid", "thread_root", "fetched_at")
    date_hierarchy = "received_at"
    ordering = ("-received_at",)
