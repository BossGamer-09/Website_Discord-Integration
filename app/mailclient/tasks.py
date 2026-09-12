import email
import imaplib
import logging
import re
from email.header import decode_header
from email.utils import parseaddr, parsedate_to_datetime

from celery import shared_task
from celery.utils.log import get_task_logger
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from app.celerytools.utils import QueueOnce, register_periodic_task

logger = get_task_logger(__name__)

_UIDVALIDITY_CACHE_KEY = "mailclient:uidvalidity"


def _decode_header_value(value: str) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return " ".join(decoded).strip()


def _get_body(msg) -> tuple[str, str]:
    text, html = "", ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get("Content-Disposition", ""))
            if "attachment" in cd:
                continue
            charset = part.get_content_charset() or "utf-8"
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            if ct == "text/plain" and not text:
                text = payload.decode(charset, errors="replace")
            elif ct == "text/html" and not html:
                html = payload.decode(charset, errors="replace")
    else:
        ct = msg.get_content_type()
        charset = msg.get_content_charset() or "utf-8"
        payload = msg.get_payload(decode=True)
        if payload:
            if ct == "text/html":
                html = payload.decode(charset, errors="replace")
            else:
                text = payload.decode(charset, errors="replace")
    return text, html


def _read_uidvalidity(conn) -> int | None:
    """Return the mailbox UIDVALIDITY, or None if it can't be determined."""
    try:
        typ, data = conn.status("INBOX", "(UIDVALIDITY)")
        if typ != "OK" or not data:
            return None
        m = re.search(rb"UIDVALIDITY (\d+)", data[0] or b"")
        return int(m.group(1)) if m else None
    except Exception:
        return None


@register_periodic_task(every=5, name="mailclient: fetch inbox")
@shared_task(base=QueueOnce, once={"graceful": True})
def fetch_inbox():
    from app.mailclient.models import EmailMessage

    imap_host = getattr(settings, "MAIL_IMAP_HOST", "")
    imap_port = getattr(settings, "MAIL_IMAP_PORT", 993)
    username = getattr(settings, "MAIL_USERNAME", "")
    password = getattr(settings, "MAIL_PASSWORD", "")

    if not all([imap_host, username, password]):
        logger.warning("mailclient: IMAP credentials not configured, skipping fetch")
        return

    try:
        conn = imaplib.IMAP4_SSL(imap_host, imap_port)
        conn.login(username, password)
        conn.select("INBOX")

        # BUG-05: IMAP UIDs are only stable within a UIDVALIDITY epoch. If the server
        # resets UIDVALIDITY (folder rebuild/migration), every stored UID would look
        # "missing" and the stale-purge below would wipe our entire local history.
        # Detect a change and skip the purge in that case.
        current_uidvalidity = _read_uidvalidity(conn)
        prev_uidvalidity = cache.get(_UIDVALIDITY_CACHE_KEY)
        uidvalidity_changed = (
            current_uidvalidity is not None
            and prev_uidvalidity is not None
            and current_uidvalidity != prev_uidvalidity
        )
        if current_uidvalidity is not None:
            cache.set(_UIDVALIDITY_CACHE_KEY, current_uidvalidity, None)

        # Fetch all UIDs we haven't stored yet
        _, data = conn.uid("search", None, "ALL")
        all_uids = data[0].split()

        server_uids = {int(uid) for uid in all_uids}

        existing_uids = set(
            EmailMessage.objects.filter(is_outbound=False)
            .values_list("imap_uid", flat=True)
        )

        # Remove inbound messages whose UIDs are gone from the server — but only when
        # UIDVALIDITY is unchanged. Otherwise the comparison is meaningless.
        stale_uids = existing_uids - server_uids - {0}
        if stale_uids and uidvalidity_changed:
            logger.warning(
                "mailclient: UIDVALIDITY changed (%s -> %s); skipping purge of %d UID(s) "
                "to avoid data loss. Stored UIDs will be reconciled on subsequent runs.",
                prev_uidvalidity, current_uidvalidity, len(stale_uids),
            )
        elif stale_uids:
            deleted, _ = EmailMessage.objects.filter(
                imap_uid__in=stale_uids, is_outbound=False
            ).delete()
            logger.info("mailclient: purged %d deleted message(s)", deleted)

        new_uids = [uid for uid in all_uids if int(uid) not in existing_uids]

        fetched = 0
        for uid_bytes in new_uids:
            uid = int(uid_bytes)
            _, msg_data = conn.uid("fetch", uid_bytes, "(RFC822)")
            if not msg_data or not msg_data[0]:
                continue

            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            message_id = msg.get("Message-ID", "").strip()
            if not message_id:
                message_id = f"generated-{uid}@blightveil.org"

            if EmailMessage.objects.filter(message_id=message_id).exists():
                continue

            subject = _decode_header_value(msg.get("Subject", ""))
            from_raw = _decode_header_value(msg.get("From", ""))
            from_name, from_address = parseaddr(from_raw)
            to_address = _decode_header_value(msg.get("To", ""))
            in_reply_to = msg.get("In-Reply-To", "").strip()

            # thread_root: use In-Reply-To chain root, fallback to own message_id
            thread_root = in_reply_to or message_id

            try:
                received_at = parsedate_to_datetime(msg.get("Date", ""))
                if received_at.tzinfo is None:
                    received_at = timezone.make_aware(received_at)
            except Exception:
                received_at = timezone.now()

            text, html = _get_body(msg)

            EmailMessage.objects.create(
                imap_uid=uid,
                message_id=message_id,
                in_reply_to=in_reply_to,
                thread_root=thread_root,
                subject=subject,
                from_name=from_name,
                from_address=from_address,
                to_address=to_address,
                body_text=text,
                body_html=html,
                received_at=received_at,
            )
            fetched += 1

        conn.logout()

        if fetched:
            logger.info("mailclient: fetched %d new message(s)", fetched)

    except Exception:
        logger.exception("mailclient: IMAP fetch failed")
