from django.contrib import admin
from django.utils.html import format_html
from django.db import models
from django.forms import Textarea, TextInput
from ordered_model.admin import OrderedModelAdmin, OrderedInlineModelAdminMixin, OrderedStackedInline

from .models import Tag, Post, Entry


# TODO: AI generated, verify


@admin.register(Tag)
class TagAdmin(OrderedModelAdmin):
    list_display = ('name', 'slug', 'move_up_down_links')
    prepopulated_fields = {'slug': ('name',)}
    search_fields = ('name',)


class EntryInline(OrderedStackedInline):
    """
    StackedInline is used here because the 'Text' content field 
    needs space, and having 4 mutually exclusive fields in a 
    Tabular row would look cluttered.
    """
    model = Entry
    extra = 0  # Don't show empty extra forms by default
    fields = (
        'entry_type', 
        'text_content', 
        'video_url', 
        ('image_file', 'image_preview'), # Tuple creates a row
        ('file_upload', 'file_link'),
        'move_up_down_links', # Adds the reorder arrows
    )
    readonly_fields = ('image_preview', 'file_link', 'move_up_down_links')

    # Custom styling to make the text area reasonable in size
    formfield_overrides = {
        models.TextField: {'widget': Textarea(attrs={'rows': 4, 'cols': 80})},
    }

    class Media:
        js = ('discordwebcms_admin/js/admin_discordwebcms_entryinline_dynamic_fields.js',)

    def image_preview(self, obj):
        if obj.image_file:
            return format_html(
                '<img src="{}" style="max-height: 150px; border-radius: 5px; border: 1px solid #ccc;" />',
                obj.image_file.url
            )
        return "-"
    image_preview.short_description = "Image Preview"

    def file_link(self, obj):
        if obj.file_upload:
            return format_html(
                '<a href="{}" target="_blank" style="font-weight:bold;">Download {}</a>',
                obj.file_upload.url,
                obj.file_upload.name.split('/')[-1]
            )
        return "-"
    file_link.short_description = "File Download"


@admin.register(Post)
class PostAdmin(OrderedInlineModelAdminMixin, OrderedModelAdmin):
    list_display = (
        'title', 
        'author', 
        'count_entries', 
        'created_at', 
        'move_up_down_links'
    )
    list_filter = ('created_at', 'author', 'tags')
    search_fields = ('title', 'author__username', 'tags__name')

    # User-friendly widget for selecting many Tags
    filter_horizontal = ('tags',) 

    inlines = [EntryInline]

    # Organize the Post form nicely
    fieldsets = (
        (None, {
            'fields': ('title', 'author')
        }),
        ('Metadata', {
            'fields': ('tags',),
            'classes': ('collapse',), # Collapsible section
        }),
    )

    def count_entries(self, obj):
        return obj.entries.count()
    count_entries.short_description = "Entries"

    # Optimization: select_related/prefetch_related to reduce DB queries
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related('author').prefetch_related('tags', 'entries')
