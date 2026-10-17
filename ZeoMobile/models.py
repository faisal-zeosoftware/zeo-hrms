"""v1.25.0 – the ZEO mobile app: registered phones (biometric unlock, push notifications) and the push log.

Public schema (SHARED_APPS): a phone belongs to a login, not to a company; it remembers the last company opened.
"""
import hashlib
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone


class MobileDevice(models.Model):
    PLATFORMS = [('android', 'Android'), ('ios', 'iPhone'), ('web', 'Web')]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='mobile_devices')
    uid = models.CharField(max_length=80, help_text='Random id the app makes on first start')
    platform = models.CharField(max_length=10, choices=PLATFORMS, default='android')
    model = models.CharField(max_length=120, blank=True, default='')
    os_version = models.CharField(max_length=40, blank=True, default='')
    app_version = models.CharField(max_length=20, blank=True, default='')
    push_token = models.TextField(blank=True, default='')
    key_hash = models.CharField(max_length=64, blank=True, default='', help_text='SHA-256 of the unlock key kept in the phone keystore')
    biometric = models.BooleanField(default=False)
    schema = models.CharField(max_length=63, blank=True, default='', help_text='Last company opened on this phone')
    lang = models.CharField(max_length=10, blank=True, default='')
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(default=timezone.now)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [('user', 'uid')]
        ordering = ['-last_seen']

    def __str__(self):
        return f'{self.user} – {self.model or self.platform}'

    @staticmethod
    def hash(key):
        return hashlib.sha256(str(key).encode()).hexdigest()

    def new_key(self):
        key = secrets.token_urlsafe(32)
        self.key_hash = self.hash(key)
        return key

    def key_ok(self, key):
        return bool(self.key_hash) and secrets.compare_digest(self.key_hash, self.hash(key or ''))


class PushLog(models.Model):
    """Every push ZEO tried to send – also when Firebase is not set up (result 'not configured')."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    schema = models.CharField(max_length=63, blank=True, default='')
    kind = models.CharField(max_length=30, blank=True, default='')
    title = models.CharField(max_length=200)
    body = models.CharField(max_length=500, blank=True, default='')
    data = models.JSONField(default=dict, blank=True)
    devices = models.PositiveSmallIntegerField(default=0)
    sent = models.PositiveSmallIntegerField(default=0)
    result = models.CharField(max_length=200, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']
