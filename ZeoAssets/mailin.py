"""E-mail to helpdesk ticket (v1.34.0): unread mails of the Asset Management mailbox (Setup › Settings › mailbox, IMAP).
A mail whose subject holds a ticket number ([AHD-00012]) is added to that ticket as the user's reply (and reopens it);
any other mail opens a new ticket. An asset code in the subject or body (e.g. IT-0003) links the asset. Automatic replies
are skipped. Standard library only."""
import email
import imaplib
import logging
import re
from email.header import decode_header, make_header
from email.utils import parseaddr

from django.core.files.base import ContentFile

log = logging.getLogger(__name__)
MAX_PER_RUN = 50
SKIP = ('mailer-daemon', 'postmaster', 'no-reply', 'noreply', 'do-not-reply', 'donotreply')


def _h(v):
    try:
        return str(make_header(decode_header(v or '')))
    except Exception:
        return v or ''


def _body(msg):
    text, html, files = '', '', []
    for part in (msg.walk() if msg.is_multipart() else [msg]):
        ctype, disp, name = part.get_content_type(), (part.get('Content-Disposition') or '').lower(), part.get_filename()
        if name or 'attachment' in disp:
            data = part.get_payload(decode=True)
            if data:
                files.append(ContentFile(data, name=_h(name) or 'attachment'))
            continue
        if ctype == 'text/plain' and not text:
            text = (part.get_payload(decode=True) or b'').decode(part.get_content_charset() or 'utf-8', 'replace')
        elif ctype == 'text/html' and not html:
            html = (part.get_payload(decode=True) or b'').decode(part.get_content_charset() or 'utf-8', 'replace')
    if not text and html:
        text = re.sub(r'[ \t]+', ' ', re.sub(r'<[^>]+>', ' ', re.sub(r'(?is)<(script|style).*?</\1>', '', html)))
    for marker in ('\nOn ', '\n-----Original Message-----', '\nFrom: '):
        i = text.find(marker)
        if i > 0:
            text = text[:i]
    return text.strip()[:20000], files


def is_auto(msg, sender):
    if any(s in sender.lower() for s in SKIP):
        return True
    auto = (msg.get('Auto-Submitted') or '').lower()
    return bool((auto and auto != 'no') or (msg.get('Precedence') or '').lower() in ('bulk', 'junk', 'list', 'auto_reply') or msg.get('X-Autoreply'))


def handle(msg):
    """One mail → a ticket (new or a reply). Returns the ticket number or ''."""
    from .app import app
    from .app2 import h_request
    from .models import AssetCard, Ticket
    from EmpManagement.models import emp_master
    s = app.settings()
    name, sender = parseaddr(msg.get('From') or '')
    if not sender or is_auto(msg, sender):
        return ''
    subject = _h(msg.get('Subject')) or '(no subject)'
    text, files = _body(msg)
    mid = (msg.get('Message-ID') or '').strip()[:255]
    if mid and Ticket.objects.filter(mail_message_id=mid).exists():
        return ''
    prefix = re.escape(s.ticket_prefix or 'AHD')
    m = re.search(r'\[?(' + prefix + r'-\d+)\]?', subject)
    t = Ticket.objects.filter(number=m.group(1)).first() if m else None
    if t is not None:
        app.event('ticket', t.pk, f'E-mail from {sender}: {text[:3000]}', None)
        if t.status in ('waiting', 'resolved'):
            t.status = 'in_progress'
            t.save(update_fields=['status'])
    else:
        emp = emp_master.objects.filter(emp_company_email__iexact=sender).first() or emp_master.objects.filter(emp_personal_email__iexact=sender).first()
        card = None
        for code in re.findall(r'\b[A-Z]{1,6}-\d{3,6}\b', f'{subject} {text}'.upper()):
            card = AssetCard.objects.filter(code__iexact=code).first()
            if card:
                break
        res = h_request(title=subject, description=text, card_id=getattr(card, 'pk', None), reporter_id=getattr(emp, 'pk', None),
                        email='' if emp else sender, source='email')
        t = Ticket.objects.get(pk=res['id'])
        Ticket.objects.filter(pk=t.pk).update(mail_message_id=mid)
    for f in files[:10]:
        app.file_model.objects.create(model='ticket', object_id=t.pk, file=f, name=f.name[:255], size=f.size or 0)
    return t.number


def check_mailbox():
    from .app import app
    s = app.settings()
    if not (app.activated() and s.mail_enabled and s.mail_host and s.mail_user and s.mail_password):
        return 0
    n = 0
    try:
        box = imaplib.IMAP4_SSL(s.mail_host, s.mail_port or 993, timeout=30) if s.mail_ssl else imaplib.IMAP4(s.mail_host, s.mail_port or 143, timeout=30)
        box.login(s.mail_user, s.mail_password)
        box.select('INBOX')
        typ, data = box.search(None, 'UNSEEN')
        for num in (data[0].split() if typ == 'OK' and data and data[0] else [])[:MAX_PER_RUN]:
            typ, parts = box.fetch(num, '(RFC822)')
            if typ != 'OK' or not parts or not parts[0]:
                continue
            try:
                if handle(email.message_from_bytes(parts[0][1])):
                    n += 1
            except Exception:
                log.exception('mail to ticket failed')
            box.store(num, '+FLAGS', '\\Seen')
        box.logout()
    except Exception as e:
        log.warning('Asset Management mailbox: %s', e)
        raise
    return n
