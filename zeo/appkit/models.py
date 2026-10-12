"""Abstract models every kit app makes concrete (Settings, Sequence, Event, File, pack items)."""
from django.conf import settings
from django.db import models

USER = settings.AUTH_USER_MODEL
REVIEW = (('to_review', 'To review'), ('reviewed', 'Reviewed'))


class AppSettingsBase(models.Model):
    industry = models.CharField(max_length=30, default='general')
    industry_source = models.CharField(max_length=10, blank=True, help_text='company = same as the company master; chosen = picked for this app')
    packs_installed = models.JSONField(default=list, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    activated_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    require_review = models.BooleanField(default=True, help_text='Pack items can be used only after a manager reviewed them')
    terms = models.JSONField(default=dict, blank=True, help_text='Wording used on screens, e.g. {"deal": "Opportunity"}')
    links_off = models.JSONField(default=list, blank=True, help_text='Links to other apps switched off')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    @classmethod
    def get(cls):
        return cls.objects.order_by('id').first() or cls.objects.create()


class SequenceBase(models.Model):
    kind = models.CharField(max_length=30, unique=True)
    last = models.PositiveIntegerField(default=0)

    class Meta:
        abstract = True


class EventBase(models.Model):
    """History of a record: creation, changes, workflow steps (records are never silently changed)."""
    model = models.CharField(max_length=30, db_index=True)
    object_id = models.IntegerField(db_index=True)
    text = models.TextField()
    user = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True
        ordering = ['-at', '-id']


class FileBase(models.Model):
    model = models.CharField(max_length=30, db_index=True)
    object_id = models.IntegerField(db_index=True)
    file = models.FileField(upload_to='zeo_apps/%Y/%m/')
    name = models.CharField(max_length=255)
    size = models.PositiveIntegerField(default=0)
    note = models.CharField(max_length=255, blank=True)
    portal = models.BooleanField(default=False, help_text='Shown in the portal')
    uploaded_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True


class PackItem(models.Model):
    """Configuration that may come from an industry pack."""
    pack = models.CharField(max_length=30, blank=True, help_text='Pack it came from; empty = made by the company')
    review_status = models.CharField(max_length=10, choices=REVIEW, default='reviewed')
    is_active = models.BooleanField(default=True)

    class Meta:
        abstract = True
