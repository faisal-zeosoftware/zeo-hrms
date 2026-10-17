"""v1.28.0 – Enterprise licence.

Enterprise runs on the client's own servers with no connection to Adept. Adept issues a licence file (.zeolic) signed
with its private key: client, installation ID, plan, limits, apps, features and the end date. ZEO checks the signature
with Adept's public key, so the file cannot be edited; renewal = a new file. Enterprise is never self-service: the
client requests it from Adept (sign-up page or Settings → Subscription), Adept answers and issues the licence.

Tables (shared schema):
  on the client's server   Installation (its ID), LicenceFile (uploaded files, the newest valid one counts)
  on Adept's cloud server  EnterpriseRequest (requests from the sign-up page / owners), IssuedLicence (what Adept signed)
"""
import uuid

from django.conf import settings
from django.db import models


class Installation(models.Model):
    """One row: the ID of this installation (shown in Settings → Licence, written into the licence by Adept)."""
    installation_id = models.CharField(max_length=40, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @classmethod
    def current(cls):
        row = cls.objects.order_by('id').first()
        if row is None:
            row = cls.objects.create(installation_id='ZEO-' + uuid.uuid4().hex[:20].upper())
        return row


class LicenceFile(models.Model):
    text = models.TextField()
    licence_id = models.CharField(max_length=40, blank=True)
    client = models.CharField(max_length=200, blank=True)
    valid_until = models.DateField(null=True, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']


class EnterpriseRequest(models.Model):
    STATES = [('new', 'New'), ('contacted', 'Contacted'), ('licensed', 'Licence issued'), ('closed', 'Closed')]
    company = models.CharField(max_length=200)
    name = models.CharField(max_length=150)
    email = models.EmailField()
    phone = models.CharField(max_length=40, blank=True)
    country = models.CharField(max_length=80, blank=True)
    employees = models.PositiveIntegerField(null=True, blank=True)
    companies = models.PositiveIntegerField(null=True, blank=True)
    message = models.TextField(blank=True)
    account = models.ForeignKey('ZeoCloud.Account', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                help_text='Sent by the owner of a ZEO Cloud account')
    state = models.CharField(max_length=12, choices=STATES, default='new')
    note = models.TextField(blank=True, help_text='Adept notes')
    ip = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']


class IssuedLicence(models.Model):
    licence_id = models.CharField(max_length=40, unique=True)
    client = models.CharField(max_length=200)
    installation_id = models.CharField(max_length=40)
    valid_from = models.DateField()
    valid_until = models.DateField()
    limits = models.JSONField(default=dict, blank=True)     # {companies, employees, users} – empty = no limit
    apps = models.JSONField(default=list, blank=True)
    features = models.JSONField(default=list, blank=True)
    text = models.TextField()
    request = models.ForeignKey(EnterpriseRequest, on_delete=models.SET_NULL, null=True, blank=True, related_name='licences')
    note = models.TextField(blank=True)
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    issued_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']
