import smtplib
import uuid
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from django.conf import settings
from django.contrib import messages  # BUG-04
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db.models import Max, Count, Q
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.generic import ListView, DetailView, View

from app.mailclient.models import EmailMessage

logger = logging.getLogger(__name__)


def _access_denied(request, message=None):
    return render(request, "mailclient/access_denied.html", {"message": message}, status=403)


class InboxView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    permission_required = "mailclient.view_inbox"
    template_name = "mailclient/inbox.html"
    context_object_name = "threads"
    paginate_by = 30

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            return _access_denied(self.request)
        return super().handle_no_permission()

    def get_queryset(self):
        # One row per thread_root, sorted by most recent message
        roots = (
            EmailMessage.objects
            .values("thread_root")
            .annotate(
                latest=Max("received_at"),
                count=Count("id"),
                unread=Count("id", filter=Q(is_read=False)),
            )
            .order_by("-latest")
        )
        return roots

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # PERF-07: one query for all representative (latest) messages on this page
        # instead of a per-thread .first() (was up to 30 extra queries per load).
        page_roots = [row["thread_root"] for row in ctx["threads"]]
        reps = {}
        for m in (
            EmailMessage.objects
            .filter(thread_root__in=page_roots)
            .order_by("thread_root", "-received_at")
        ):
            if m.thread_root not in reps:
                reps[m.thread_root] = m

        thread_data = []
        for row in ctx["threads"]:
            rep = reps.get(row["thread_root"])
            if rep:
                thread_data.append({
                    "root": row["thread_root"],
                    "rep": rep,
                    "count": row["count"],
                    "unread": row["unread"],
                    "latest": row["latest"],
                })
        ctx["thread_data"] = thread_data
        ctx["can_reply"] = self.request.user.has_perm("mailclient.reply_email")
        return ctx


class ThreadView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    permission_required = "mailclient.view_inbox"
    template_name = "mailclient/thread.html"

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            return _access_denied(self.request)
        return super().handle_no_permission()

    def get_object(self):
        root = self.kwargs["thread_root"]
        return EmailMessage.objects.filter(thread_root=root).order_by("received_at")

    def get(self, request, *args, **kwargs):
        messages_qs = self.get_object()
        if not messages_qs.exists():
            raise Http404
        # Mark inbound as read
        messages_qs.filter(is_read=False, is_outbound=False).update(is_read=True)
        rep = messages_qs.last()
        can_reply = request.user.has_perm("mailclient.reply_email")
        return self.response_class(
            request=request,
            template=self.template_name,
            context={
                "messages": messages_qs,
                "rep": rep,
                "thread_root": self.kwargs["thread_root"],
                "can_reply": can_reply,
            },
        )


class SyncView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "mailclient.view_inbox"

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            return _access_denied(self.request)
        return super().handle_no_permission()

    def get(self, request):
        return HttpResponseRedirect(reverse("mailclient:inbox"))

    def post(self, request):
        from app.mailclient.tasks import fetch_inbox
        fetch_inbox.apply_async()
        return HttpResponseRedirect(reverse("mailclient:inbox"))


class ReplyView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "mailclient.reply_email"

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            return _access_denied(self.request)
        return super().handle_no_permission()

    def get(self, request, thread_root):
        return HttpResponseRedirect(
            reverse("mailclient:thread", kwargs={"thread_root": thread_root})
        )

    def post(self, request, thread_root):
        body = request.POST.get("body", "").strip()
        if not body:
            return HttpResponseRedirect(
                reverse("mailclient:thread", kwargs={"thread_root": thread_root})
            )

        # Find latest inbound message in thread to get reply-to address
        original = (
            EmailMessage.objects
            .filter(thread_root=thread_root, is_outbound=False)
            .order_by("-received_at")
            .first()
        )
        if not original:
            return HttpResponseRedirect(reverse("mailclient:inbox"))

        to_addr = original.from_address
        subject = original.subject
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"

        from_addr = getattr(settings, "MAIL_USERNAME", "contact@blightveil.org")
        smtp_host = getattr(settings, "MAIL_SMTP_HOST", "shadow.mxrouting.net")
        smtp_port = getattr(settings, "MAIL_SMTP_PORT", 465)
        password = getattr(settings, "MAIL_PASSWORD", "")

        new_message_id = f"<{uuid.uuid4()}@blightveil.org>"

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = to_addr
        msg["Message-ID"] = new_message_id
        msg["In-Reply-To"] = original.message_id
        msg["References"] = original.message_id
        msg.attach(MIMEText(body, "plain"))

        # BUG-04: only persist the outbound copy and report success if the send actually succeeded.
        sent_ok = False
        try:
            with smtplib.SMTP_SSL(smtp_host, smtp_port) as smtp:
                smtp.login(from_addr, password)
                smtp.sendmail(from_addr, [to_addr], msg.as_string())
            sent_ok = True
        except Exception:
            logger.exception("mailclient: SMTP send failed")

        if not sent_ok:
            messages.error(
                request,
                "Reply could not be sent — the mail server rejected it. Nothing was sent or saved.",
            )
            return HttpResponseRedirect(
                reverse("mailclient:thread", kwargs={"thread_root": thread_root})
            )

        # Store outbound copy (only on success)
        EmailMessage.objects.create(
            imap_uid=0,
            message_id=new_message_id,
            in_reply_to=original.message_id,
            thread_root=thread_root,
            subject=subject,
            from_name="BlightVeil Staff",
            from_address=from_addr,
            to_address=to_addr,
            body_text=body,
            is_read=True,
            is_outbound=True,
            received_at=timezone.now(),
        )
        messages.success(request, "Reply sent.")

        return HttpResponseRedirect(
            reverse("mailclient:thread", kwargs={"thread_root": thread_root})
        )
