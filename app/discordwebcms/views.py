from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from .models import Post, Entry, Tag


def _can_edit(user):
    return user.has_perm('discordwebcms.edit_post')


def _can_view_staff(user):
    return user.has_perm('discordwebcms.view_staff_post') or _can_edit(user)


def _forbidden(request):
    return render(request, 'discordwebcms/access_denied.html', status=403)


@login_required
def post_index(request):
    tags = list(Tag.objects.order_by('order'))
    tag_filter = request.GET.get('tag')

    qs = Post.objects.prefetch_related('tags').order_by('order')
    if not _can_view_staff(request.user):
        qs = qs.filter(visibility=Post.Visibility.PUBLIC)
    if tag_filter:
        qs = qs.filter(tags__slug=tag_filter)

    posts = list(qs)
    active_tag = next((t for t in tags if t.slug == tag_filter), None)

    return render(request, 'discordwebcms/post_index.html', {
        'posts': posts,
        'tags': tags,
        'active_tag': active_tag,
        'can_edit': _can_edit(request.user),
    })


@login_required
def post_detail(request, pk):
    post = get_object_or_404(Post, pk=pk)

    if post.visibility == Post.Visibility.STAFF and not _can_view_staff(request.user):
        return _forbidden(request)

    entries = list(post.entries.order_by('order'))
    tags = list(post.tags.order_by('order'))

    return render(request, 'discordwebcms/post_detail.html', {
        'post': post,
        'entries': entries,
        'tags': tags,
        'can_edit': _can_edit(request.user),
    })


@login_required
def post_create(request):
    if not _can_edit(request.user):
        return _forbidden(request)

    tags = list(Tag.objects.order_by('order'))
    errors = {}
    form_data = {}

    if request.method == 'POST':
        form_data = request.POST
        title = form_data.get('title', '').strip()
        visibility = form_data.get('visibility', Post.Visibility.PUBLIC)
        selected_tags = form_data.getlist('tags')
        text_content = form_data.get('text_content', '').strip()

        if not title:
            errors['title'] = 'Title is required.'
        if visibility not in Post.Visibility.values:
            errors['visibility'] = 'Invalid visibility.'
        if not text_content:
            errors['text_content'] = 'Content is required.'

        if not errors:
            post = Post.objects.create(
                title=title,
                author=request.user,
                visibility=visibility,
            )
            if selected_tags:
                post.tags.set(Tag.objects.filter(id__in=selected_tags))
            Entry.objects.create(
                post=post,
                entry_type=Entry.EntryType.TEXT,
                text_content=text_content,
            )
            return redirect('discordwebcms:post_edit', pk=post.pk)

    selected_tag_ids = {int(t) for t in form_data.getlist('tags')} if form_data else set()
    return render(request, 'discordwebcms/post_form.html', {
        'post': None,
        'tags': tags,
        'errors': errors,
        'form_data': form_data,
        'visibility_choices': Post.Visibility.choices,
        'selected_tag_ids': selected_tag_ids,
    })


@login_required
def post_edit(request, pk):
    if not _can_edit(request.user):
        return _forbidden(request)

    post = get_object_or_404(Post, pk=pk)
    tags = list(Tag.objects.order_by('order'))
    errors = {}
    form_data = {}

    # Only the first TEXT entry is editable here; complex multi-entry editing stays in admin.
    first_entry = post.entries.filter(entry_type=Entry.EntryType.TEXT).order_by('order').first()

    if request.method == 'POST':
        form_data = request.POST
        title = form_data.get('title', '').strip()
        visibility = form_data.get('visibility', post.visibility)
        selected_tags = form_data.getlist('tags')
        text_content = form_data.get('text_content', '').strip()

        if not title:
            errors['title'] = 'Title is required.'
        if visibility not in Post.Visibility.values:
            errors['visibility'] = 'Invalid visibility.'
        if not text_content:
            errors['text_content'] = 'Content is required.'

        if not errors:
            post.title = title
            post.visibility = visibility
            post.save()
            post.tags.set(Tag.objects.filter(id__in=selected_tags))
            if first_entry:
                first_entry.text_content = text_content
                first_entry.save()
            else:
                Entry.objects.create(
                    post=post,
                    entry_type=Entry.EntryType.TEXT,
                    text_content=text_content,
                )
            return redirect('discordwebcms:post_edit', pk=post.pk)

    if form_data:
        selected_tag_ids = {int(t) for t in form_data.getlist('tags')}
    else:
        selected_tag_ids = set(post.tags.values_list('id', flat=True))

    return render(request, 'discordwebcms/post_form.html', {
        'post': post,
        'tags': tags,
        'errors': errors,
        'form_data': form_data,
        'visibility_choices': Post.Visibility.choices,
        'first_entry': first_entry,
        'selected_tag_ids': selected_tag_ids,
    })


@login_required
def post_history(request, pk):
    if not _can_edit(request.user):
        return _forbidden(request)

    post = get_object_or_404(Post, pk=pk)
    historical = post.history.all().order_by('-history_date')

    return render(request, 'discordwebcms/post_history.html', {
        'post': post,
        'historical': historical,
    })
