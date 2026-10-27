"""v1.62.0 print – print templates: the design of the payslip and every other printed document (HTML + CSS → PDF).

* PrintBranding   – one row per company: logo, colours, font, letterhead texts, stamp / signature, watermark.
* PrintTemplate   – a design of one document type. Samples (is_sample) come with ZEO and are read only; the company copies one
                    and edits the copy (block editor or HTML / CSS).
* PrintTemplateVersion – every saved state of a company template (restore).
* PrintDefault    – the design used for a document type (optionally per branch and per language).
"""
from django.db import models


class PrintBranding(models.Model):   # v1.62.0 print
    LOGO_POS = [('left', 'Left'), ('center', 'Centre'), ('right', 'Right')]
    LOGO_SRC = [('auto', 'Branch logo, else company logo'), ('company', 'Company logo'), ('own', 'Logo uploaded here')]
    logo = models.ImageField(upload_to='print_templates/', null=True, blank=True)
    logo_source = models.CharField(max_length=10, choices=LOGO_SRC, default='auto')
    logo_position = models.CharField(max_length=10, choices=LOGO_POS, default='left')
    primary_color = models.CharField(max_length=9, default='#1f3a5f')
    accent_color = models.CharField(max_length=9, default='#c8a24a')
    text_color = models.CharField(max_length=9, default='#1f2430')
    font = models.CharField(max_length=40, default='Inter')
    font_arabic = models.CharField(max_length=40, default='Cairo')
    header_text = models.TextField(blank=True, default='', help_text='Extra letterhead line(s) under the company name')
    footer_text = models.TextField(blank=True, default='', help_text='Footer of every page (address, licence, phone …)')
    address = models.TextField(blank=True, default='', help_text='Empty = the address of the branch / company')
    trn = models.CharField(max_length=40, blank=True, default='', help_text='Empty = the TRN of Organisation settings')
    stamp = models.ImageField(upload_to='print_templates/', null=True, blank=True)
    signature = models.ImageField(upload_to='print_templates/', null=True, blank=True)
    show_stamp = models.BooleanField(default=True)
    show_signature = models.BooleanField(default=True)
    show_watermark = models.BooleanField(default=False)
    watermark_text = models.CharField(max_length=60, blank=True, default='CONFIDENTIAL')
    updated_at = models.DateTimeField(auto_now=True)
    updated_by_id = models.IntegerField(null=True, blank=True)

    class Meta:
        permissions = [('manage_printtemplate', 'Design the printed documents (print templates and branding)')]

    @classmethod
    def get(cls):
        return cls.objects.order_by('id').first() or cls.objects.create()


class PrintTemplate(models.Model):   # v1.62.0 print
    MODES = [('blocks', 'Block editor'), ('html', 'HTML / CSS')]
    LANGS = [('en', 'English'), ('ar', 'Arabic'), ('bi', 'English and Arabic')]
    doc_type = models.CharField(max_length=40, db_index=True)
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=300, blank=True, default='')
    is_sample = models.BooleanField(default=False, db_index=True)
    sample_key = models.CharField(max_length=40, blank=True, default='', help_text='Design of ZEO this template is (or was copied from)')
    language = models.CharField(max_length=2, choices=LANGS, default='en')
    mode = models.CharField(max_length=10, choices=MODES, default='blocks')
    settings = models.JSONField(default=dict, blank=True, help_text='Block editor: blocks, colours, font, paper, margins …')
    html = models.TextField(blank=True, default='')
    css = models.TextField(blank=True, default='')
    version = models.PositiveIntegerField(default=1)
    copied_from = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_by_id = models.IntegerField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['doc_type', '-is_sample', 'id']


class PrintTemplateVersion(models.Model):   # v1.62.0 print
    template = models.ForeignKey(PrintTemplate, on_delete=models.CASCADE, related_name='versions')
    version = models.PositiveIntegerField()
    name = models.CharField(max_length=120)
    language = models.CharField(max_length=2, default='en')
    mode = models.CharField(max_length=10, default='blocks')
    settings = models.JSONField(default=dict, blank=True)
    html = models.TextField(blank=True, default='')
    css = models.TextField(blank=True, default='')
    note = models.CharField(max_length=200, blank=True, default='')
    saved_by_id = models.IntegerField(null=True, blank=True)
    saved_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['template', '-version']
        unique_together = [('template', 'version')]


class PrintDefault(models.Model):   # v1.62.0 print
    doc_type = models.CharField(max_length=40)
    branch_id = models.IntegerField(null=True, blank=True, help_text='empty = every branch')
    language = models.CharField(max_length=2, blank=True, default='', help_text="empty = any language; 'en' / 'ar'")
    template = models.ForeignKey(PrintTemplate, on_delete=models.CASCADE, related_name='defaults')
    updated_by_id = models.IntegerField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('doc_type', 'branch_id', 'language')]
