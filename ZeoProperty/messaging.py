"""v1.40.0 – messages to tenants, buyers and owners: e-mail (company mail settings), portal notification, and SMS / WhatsApp through
the provider configured in Property settings (an HTTP endpoint; ZEO posts JSON {to, text, channel, key}). No outside service is
bundled – the company brings its own provider. The provider key is kept encrypted (Fernet with the server SECRET_KEY)."""
import base64
import hashlib
import json
import logging

from django.conf import settings as dj

from .app import app
from .models import MessageTemplate

log = logging.getLogger('zeo.property')


def _fernet():
    from cryptography.fernet import Fernet
    key = hashlib.sha256(('zeo-property:' + dj.SECRET_KEY).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def set_secret(st, value):
    st.messaging_secret = _fernet().encrypt(value.encode()).decode() if value else ''


def get_secret(st):
    if not st.messaging_secret:
        return ''
    try:
        return _fernet().decrypt(st.messaging_secret.encode()).decode()
    except Exception:
        return ''


def fill(text, ctx):
    out = text or ''
    for k, v in (ctx or {}).items():
        out = out.replace('{' + k + '}', str(v if v is not None else ''))
    return out


def template(event, channel, lang='en'):
    qs = MessageTemplate.objects.filter(event=event, channel=channel, is_active=True).exclude(review_status='to_review')
    return qs.filter(language=lang).first() or qs.first()


def send_sms(to, text, channel='sms'):
    """POST to the configured provider; True when it answered 2xx."""
    s = app.settings()
    if not s.messaging_url or not to:
        return False
    try:
        import requests
        r = requests.post(s.messaging_url, json={'to': to, 'text': text, 'channel': channel, 'key': get_secret(s)}, timeout=10)
        return 200 <= r.status_code < 300
    except Exception as e:   # the provider is down – the e-mail / portal message still goes
        log.warning('property messaging: %s', e)
        return False


def notify(event, person, subject, body, ctx, model='', obj_id=0, label='', page=''):
    """Send what the templates say for this event (e-mail by default, SMS / WhatsApp when a template exists and a provider is set, portal
    notification when the person has a portal login); without templates the plain subject / body go by e-mail and portal."""
    ctx = dict(ctx or {})
    ctx.setdefault('name', getattr(person, 'name', ''))
    ctx.setdefault('company', app.company_name() if hasattr(app, 'company_name') else '')
    ctx.setdefault('link', app.settings().portal_url or '')
    lang = 'ar' if getattr(getattr(person, 'portal_user', None), 'language', '') == 'ar' else 'en'
    sent = []
    email = getattr(person, 'email', '')
    t = template(event, 'email', lang)
    if email:
        ok = app.send_mail([email], fill(t.subject, ctx) if t and t.subject else subject, fill(t.body, ctx) if t else body)
        if ok:
            sent.append('email')
    phone = getattr(person, 'phone', '')
    for ch in ('sms', 'whatsapp'):
        t2 = template(event, ch, lang)
        if t2 and phone and send_sms(phone, fill(t2.body, ctx), ch):
            sent.append(ch)
    pu = getattr(person, 'portal_user', None)
    if pu is not None and pu.is_active:
        t3 = template(event, 'push', lang)
        try:
            app.tell([pu], fill(t3.subject, ctx) if t3 and t3.subject else subject, fill(t3.body, ctx) if t3 else body, model, obj_id, label, page)
            sent.append('portal')
        except Exception as e:
            log.warning('property portal message: %s', e)
    if model and obj_id:
        try:
            app.event(model, obj_id, f'Message "{subject}" sent to {getattr(person, "name", "")} by {", ".join(sent) or "nobody (no e-mail / phone / portal login)"}', None)
        except Exception:
            pass
    return sent
