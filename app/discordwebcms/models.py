from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.db.models.signals import pre_save
from django.dispatch import receiver

from ordered_model.models import OrderedModel
from simple_history.models import HistoricalRecords

from app.main.util.db import OriginalStateMixin


def validate_file_size(value):
    limit_mb = 10
    limit_bytes = limit_mb * 1024 * 1024
    if value.size > limit_bytes:
        raise ValidationError(f"File size cannot exceed {limit_mb}MB.")


class Tag(OrderedModel):
    name = models.CharField(max_length=50, unique=True)
    slug = models.SlugField(unique=True)

    class Meta(OrderedModel.Meta):
        default_permissions = ()

    def __str__(self):
        return self.name


class Post(OrderedModel):
    class Visibility(models.TextChoices):
        PUBLIC = 'PUBLIC', 'Public (all members)'
        STAFF  = 'STAFF',  'Staff only'

    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    title = models.CharField(max_length=200, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    tags = models.ManyToManyField(Tag, related_name='posts', blank=True)
    discord_thread_id = models.BigIntegerField(null=True, blank=True, help_text="ID of the Discord Forum Thread")
    discord_message_id = models.BigIntegerField(null=True, blank=True, help_text="Linked live Discord message ID")
    discord_channel_id = models.BigIntegerField(null=True, blank=True, help_text="Channel containing the linked live message")
    visibility = models.CharField(
        max_length=10,
        choices=Visibility.choices,
        default=Visibility.PUBLIC,
        db_index=True,
        help_text="PUBLIC = all members via /kbsearch; STAFF = only users with discordwebcms.view_staff_post",
    )
    version = models.PositiveIntegerField(default=0, editable=False)
    history = HistoricalRecords()

    class Meta(OrderedModel.Meta):
        default_permissions = ()
        permissions = [
            ('view_staff_post', 'Can view staff-only knowledge base posts'),
            ('edit_post', 'Can create and edit knowledge base posts'),
        ]

    def __str__(self):
        return self.title


class Entry(OriginalStateMixin, OrderedModel):
    class EntryType(models.TextChoices):
        TEXT = 'TEXT', 'Text (Markdown)'
        VIDEO = 'VIDEO', 'Video URL'
        IMAGE = 'IMAGE', 'Image Upload'
        FILE = 'FILE', 'File Upload'

    post = models.ForeignKey(Post, related_name='entries', on_delete=models.CASCADE)
    entry_type = models.CharField(max_length=10, choices=EntryType.choices)
    discord_message_id = models.BigIntegerField(null=True, blank=True, help_text="ID of the Discord Message inside the thread")
    discord_message_attachment_id = models.BigIntegerField(null=True, blank=True, help_text="ID of the Discord Message Attachment")

    text_content = models.TextField(blank=True, null=True, help_text="Supports Markdown")
    video_url = models.URLField(blank=True, null=True)
    image_file = models.ImageField(
        upload_to='cms_post_images/',
        blank=True,
        null=True,
        validators=[validate_file_size]
    )
    file_upload = models.FileField(
        upload_to='cms_post_files/',
        blank=True,
        null=True,
        validators=[validate_file_size]
    )

    order_with_respect_to = 'post'

    class Meta(OrderedModel.Meta):
        default_permissions = ()
        constraints = [
            models.CheckConstraint(
                condition=(
                    # Text
                    (~Q(text_content__in=['', None]) & Q(video_url__in=['', None]) & Q(image_file__in=['', None]) & Q(file_upload__in=['', None])) |
                    # Video
                    (Q(text_content__in=['', None]) & ~Q(video_url__in=['', None]) & Q(image_file__in=['', None]) & Q(file_upload__in=['', None])) |
                    # Image
                    (Q(text_content__in=['', None]) & Q(video_url__in=['', None]) & ~Q(image_file__in=['', None]) & Q(file_upload__in=['', None])) |
                    # File
                    (Q(text_content__in=['', None]) & Q(video_url__in=['', None]) & Q(image_file__in=['', None]) & ~Q(file_upload__in=['', None]))
                ),
                name='one_content_type_only'
            )
        ]

    def to(self, order, extra_update=None):
        super().to(order, extra_update=extra_update)
        from .signals import post_internal_order_changed_signal
        post_internal_order_changed_signal.send(sender=self.post.__class__, instance=self.post)

    def swap(self, replacement):
        super().swap(replacement)
        from .signals import post_internal_order_changed_signal
        post_internal_order_changed_signal.send(sender=self.post.__class__, instance=self.post)

    def clean(self):
        super().clean()

        filled_fields = [
            field for field in ['text_content', 'video_url', 'image_file', 'file_upload']
            if getattr(self, field)
        ]

        if len(filled_fields) > 1:
            raise ValidationError("You can only fill one content field per entry.")

        if len(filled_fields) == 0:
            raise ValidationError("You must fill at least one content field.")

        type_mapping = {
            self.EntryType.TEXT: 'text_content',
            self.EntryType.VIDEO: 'video_url',
            self.EntryType.IMAGE: 'image_file',
            self.EntryType.FILE: 'file_upload',
        }

        required_field = type_mapping.get(self.entry_type)
        if required_field and not getattr(self, required_field):
            raise ValidationError(f"The Entry Type is '{self.get_entry_type_display()}' but the {required_field} field is empty.")

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.get_entry_type_display()} for {self.post.title}"


@receiver(pre_save, sender=Post)
def _increment_post_version(sender, instance, **kwargs):
    if instance.pk and not getattr(instance, '_skip_version', False):
        # Fetch current value from DB to avoid F() expression issues with HistoricalRecords
        current = Post.objects.filter(pk=instance.pk).values_list('version', flat=True).first()
        if current is not None:
            instance.version = current + 1
