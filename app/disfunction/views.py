import re

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Min
from django.shortcuts import render, get_object_or_404
from django.utils.html import escape
from django.utils.safestring import mark_safe

from .models import VCTranscript


def _clean_discord_content(content, user_map, channel_map):
    """
    Replaces Discord markdown tokens with human-readable HTML spans:
      <@ID> / <@!ID>   → @display_name
      <#ID>            → #channel-name
      <@&ID>           → @role
      <:name:ID>       → :name:
      <a:name:ID>      → :name:
    Content is HTML-escaped first so the output is safe to mark_safe().
    """
    if not content:
        return ''
    text = escape(content)

    def replace_user(m):
        uid = int(m.group(1))
        name = user_map.get(uid, f'{uid}')
        return f'<span class="dc-mention">@{escape(name)}</span>'

    def replace_channel(m):
        cid = int(m.group(1))
        name = channel_map.get(cid, str(cid))
        return f'<span class="dc-mention dc-channel">#{escape(name)}</span>'

    def replace_role(m):
        return f'<span class="dc-mention dc-role">@role</span>'

    def replace_custom_emoji(m):
        return f'<span class="dc-emoji">:{m.group(1)}:</span>'

    text = re.sub(r'&lt;@!?(\d+)&gt;',  replace_user,         text)
    text = re.sub(r'&lt;#(\d+)&gt;',    replace_channel,      text)
    text = re.sub(r'&lt;@&amp;(\d+)&gt;', replace_role,       text)
    text = re.sub(r'&lt;a?:(\w+):\d+&gt;', replace_custom_emoji, text)

    return mark_safe(text)


def _has_perm(request, perm):
    return request.user.is_authenticated and (
        request.user.is_staff or request.user.is_superuser or request.user.has_perm(perm)
    )


@login_required
def message_log(request):
    if not _has_perm(request, 'discordlogger.view_transcripts'):
        return render(request, '403.html', status=403)

    from app.discordlogger.models import LoggedDiscordMessage, LoggedDiscordChannel

    event_filter  = request.GET.get('event', '')
    channel_filter = request.GET.get('channel', '')
    author_filter  = request.GET.get('author', '').strip()

    # No select_related — channel FK is non-nullable so an INNER JOIN would silently
    # drop messages whose channel_id has no LoggedDiscordChannel row, making the
    # paginator count disagree with what's actually rendered.
    qs = (
        LoggedDiscordMessage.objects
        .exclude(event=LoggedDiscordMessage.Event.NEW)
        .order_by('-updated_at')
    )
    if event_filter in (LoggedDiscordMessage.Event.EDIT, LoggedDiscordMessage.Event.DELETE):
        qs = qs.filter(event=event_filter)
    if channel_filter:
        qs = qs.filter(channel_id=channel_filter)
    if author_filter:
        qs = qs.filter(author_name__icontains=author_filter)

    paginator = Paginator(qs, 50)
    page_obj  = paginator.get_page(request.GET.get('page', 1))
    messages  = list(page_obj)  # evaluate once

    # Batch-fetch channels (avoids N+1 without INNER JOIN dropping rows)
    channel_ids = {m.channel_id for m in messages}
    channel_map = {
        c.id: c
        for c in LoggedDiscordChannel.objects.filter(id__in=channel_ids)
    }

    # Batch-fetch oldest history entry per message for before/after diff
    HistMsg = LoggedDiscordMessage.history.model
    msg_ids = [m.id for m in messages]
    earliest_ids = (
        HistMsg.objects.filter(id__in=msg_ids)
        .values('id')
        .annotate(first=Min('history_id'))
        .values_list('first', flat=True)
    )
    original_map = {
        h.id: h.content
        for h in HistMsg.objects.filter(history_id__in=earliest_ids)
    }

    for msg in messages:
        msg.channel_cached  = channel_map.get(msg.channel_id)
        msg.original_content = original_map.get(msg.id)

    channels = (
        LoggedDiscordChannel.objects
        .filter(deleted_at__isnull=True, channel_type='text')
        .order_by('name')
    )

    return render(request, 'disfunction/message_log.html', {
        'page_obj':       page_obj,
        'messages':       messages,
        'event_filter':   event_filter,
        'channel_filter': channel_filter,
        'author_filter':  author_filter,
        'channels':       channels,
        'Event':          LoggedDiscordMessage.Event,
    })


@login_required
def vc_transcript_list(request):
    if not _has_perm(request, 'disfunction.view_vctranscript'):
        return render(request, '403.html', status=403)
    transcripts = VCTranscript.objects.order_by('-deleted_at').select_related()[:200]
    return render(request, 'disfunction/vc_transcript_list.html', {'transcripts': transcripts})


@login_required
def vc_transcript_detail(request, pk):
    if not _has_perm(request, 'disfunction.view_vctranscript'):
        return render(request, '403.html', status=403)
    transcript = get_object_or_404(VCTranscript, pk=pk)
    messages = list(transcript.messages.all())

    # Build user mention map from this transcript's own messages (no extra queries)
    user_map = {msg.author_id: msg.author_name for msg in messages}

    # Batch-resolve any remaining IDs from DiscordUser table
    mentioned_ids = set()
    for msg in messages:
        for m in re.finditer(r'<@!?(\d+)>', msg.content or ''):
            uid = int(m.group(1))
            if uid not in user_map:
                mentioned_ids.add(uid)
    if mentioned_ids:
        from app.discordauth.models import DiscordUser
        for du in DiscordUser.objects.filter(discorduid__in=mentioned_ids).select_related('user'):
            try:
                user_map[du.discorduid] = du.user.get_full_name() or du.user.username
            except Exception:
                pass

    # Channel mention map
    channel_ids = set()
    for msg in messages:
        for m in re.finditer(r'<#(\d+)>', msg.content or ''):
            channel_ids.add(int(m.group(1)))
    channel_map = {}
    if channel_ids:
        from app.discordlogger.models import LoggedDiscordChannel
        channel_map = {
            c.id: c.name
            for c in LoggedDiscordChannel.objects.filter(id__in=channel_ids)
        }

    # Clean content and tag for grouping
    for i, msg in enumerate(messages):
        msg.prev_author_id = messages[i - 1].author_id if i > 0 else None
        msg.clean_content = _clean_discord_content(msg.content, user_map, channel_map)

    return render(request, 'disfunction/vc_transcript_detail.html', {
        'transcript': transcript,
        'messages': messages,
    })
