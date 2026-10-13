"""v1.22.3 – one gate for every e-mail ZEO sends: a neutralized company (a duplicate made for testing) sends none.

settings.EMAIL_BACKEND = 'zeo.mailguard.GuardedBackend' and the real backend in settings.ZEO_REAL_EMAIL_BACKEND.
Every way ZEO sends mail – Django's send_mail, EmailMessage, get_connection(host=…) with the company's own SMTP
settings – goes through EMAIL_BACKEND, so the check here covers all of them.
"""
import logging
import time

from django.conf import settings
from django.core.mail import get_connection
from django.core.mail.backends.base import BaseEmailBackend
from django.db import connection

log = logging.getLogger(__name__)
_cache = {}   # schema → (until, neutralized)


def is_neutralized(schema=None):
    schema = schema or getattr(connection, 'schema_name', 'public')
    if not schema or schema == 'public':
        return False
    hit = _cache.get(schema)
    if hit and hit[0] > time.time():
        return hit[1]
    value = False
    try:
        from ZeoApps.models import CompanyOption
        o = CompanyOption.objects.filter(key='neutralized').first()
        value = bool(o and (o.value or {}).get('on'))
    except Exception:
        value = False
    _cache[schema] = (time.time() + 60, value)
    return value


def forget(schema=None):
    if schema:
        _cache.pop(schema, None)
    else:
        _cache.clear()


class GuardedBackend(BaseEmailBackend):
    def __init__(self, fail_silently=False, **kwargs):
        super().__init__(fail_silently=fail_silently)
        real = getattr(settings, 'ZEO_REAL_EMAIL_BACKEND', None) or 'django.core.mail.backends.smtp.EmailBackend'
        self.real = get_connection(backend=real, fail_silently=fail_silently, **kwargs)

    def open(self):
        if is_neutralized():
            return False
        return self.real.open()

    def close(self):
        try:
            return self.real.close()
        except Exception:
            if not self.fail_silently:
                raise

    def send_messages(self, email_messages):
        msgs = list(email_messages or [])
        if msgs and is_neutralized():
            log.info('neutralized company %s: %d e-mail(s) not sent', getattr(connection, 'schema_name', '?'), len(msgs))
            return len(msgs)
        return self.real.send_messages(msgs)
