from django.conf import settings
from django.db import models


class FormSetting(models.Model):
    """
    Company-wide layout of a form designed in Settings → Customization → Form designer:
    field labels, mandatory / hidden flags, dropdown values, and the mandatory flag of custom fields.
    One row per form ('employee', 'custom:employee', 'custom:family' ...). Lives in the company schema,
    so every user of the company gets the same form.
    """
    form = models.CharField(max_length=60, unique=True)
    data = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        verbose_name = 'form setting'

    def __str__(self):
        return self.form


# ---------------------------------------------------------------------------------------------------------------------
# v1.62.0 workbook – Settings › Data workbook: a client's data workbook uploaded, checked (trial) and loaded in the background
class WorkbookUpload(models.Model):
    file = models.FileField(upload_to='workbooks/%Y/%m/')
    file_name = models.CharField(max_length=255)
    size = models.PositiveIntegerField(default=0)
    sha1 = models.CharField(max_length=40, blank=True, default='')
    sheets = models.JSONField(default=list, blank=True)        # [{sheet, how, phase, rows, months}] read from the Index
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-pk']

    def __str__(self):
        return self.file_name


class WorkbookRun(models.Model):
    KINDS = [('check', 'Check'), ('quick', 'Quick check'), ('load', 'Load')]
    STATES = [('queued', 'Waiting'), ('running', 'Running'), ('done', 'Finished'), ('failed', 'Could not run'),
              ('stopped', 'Stopped'), ('interrupted', 'Interrupted')]
    upload = models.ForeignKey(WorkbookUpload, on_delete=models.CASCADE, related_name='runs')
    kind = models.CharField(max_length=10, choices=KINDS, default='check')
    state = models.CharField(max_length=12, choices=STATES, default='queued')
    options = models.JSONField(default=dict, blank=True)       # only, from_month
    progress = models.JSONField(default=dict, blank=True)      # sheet, month, done, total, ok, failed, skipped
    stats = models.JSONField(default=dict, blank=True)         # per sheet: ok, failed, skipped, examples, past
    totals = models.JSONField(default=dict, blank=True)
    errors = models.JSONField(default=list, blank=True)        # [{sheet, row, column, letter, value, error, fix, level}]
    message = models.TextField(blank=True, default='')
    runner = models.CharField(max_length=40, blank=True, default='')
    stop_requested = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-pk']


class WorkbookRow(models.Model):
    """A workbook row that was loaded – a second load (resume, or the corrected file) skips it."""
    sheet = models.CharField(max_length=64)
    digest = models.CharField(max_length=40)
    record_id = models.CharField(max_length=64, blank=True, default='')
    ref = models.CharField(max_length=200, blank=True, default='')
    run = models.ForeignKey(WorkbookRun, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('sheet', 'digest')]
