from django.conf import settings
from django.db import models

KINDS = (('sales_quote', 'Customer enquiry → sales quotation'), ('sales_order', 'Customer PO → sales order'),
         ('purchase_quote', 'Supplier quote → supplier quote / purchase order'), ('purchase_request', 'Purchase request → request / RFQ / PO'),
         ('price_list', 'Supplier price list → supplier prices'))


class CaptureJob(models.Model):
    kind = models.CharField(max_length=20, choices=KINDS)
    source_name = models.CharField('file or "pasted text"', max_length=200, blank=True)
    file = models.FileField(upload_to='doc_capture/%Y/%m/', null=True, blank=True)
    text = models.TextField(blank=True)
    reader = models.CharField(max_length=20, blank=True, help_text='ai, built-in, ai+vision')
    extracted = models.JSONField(default=dict, blank=True)
    proposal = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=10, choices=(('read', 'Read'), ('created', 'Created'), ('failed', 'Failed')), default='read')
    target = models.CharField(max_length=30, blank=True)
    target_id = models.IntegerField(null=True, blank=True)
    target_number = models.CharField(max_length=40, blank=True)
    target_link = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']


class CaptureAlias(models.Model):
    """what a customer / supplier calls an item (or how a sender is written) → the ZEO record; learnt when the user corrects a match"""
    kind = models.CharField(max_length=10, choices=(('product', 'Item'), ('partner', 'Customer / supplier')))
    partner_id = models.IntegerField(null=True, blank=True, help_text='the sender the item text belongs to (empty: any)')
    text = models.CharField(max_length=200, db_index=True)
    target_id = models.IntegerField()
    uses = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('kind', 'partner_id', 'text')]
