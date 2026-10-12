"""E-mail to ticket: read unread mails from the support mailbox (IMAP) of each company.

* A mail whose subject holds a ticket number ([TKT-00012]) or that answers a ticket mail is added to that ticket as the
  customer's reply (and reopens / resumes it).
* Any other mail opens a new ticket. The sender is matched to a contact by e-mail, else to a customer by e-mail domain;
  the team by the address it was sent to (team e-mail alias).
* Automatic replies (out-of-office, bounces, no-reply) are skipped, so mail loops cannot start.
Settings → Projects → Mailbox. Only the standard library is used (no paid service).
"""
import email
import imaplib
import logging
import re
from email.header import decode_header, make_header
from email.utils import getaddresses, parseaddr

from django.core.files.base import ContentFile
from django.utils import timezone

from . import services as S
from .models import Contact, Customer, ProjectsSettings, SupportTeam, Ticket

log = logging.getLogger(__name__)
MAX_PER_RUN = 50
SKIP_SENDERS = ('mailer-daemon', 'postmaster', 'no-reply', 'noreply', 'do-not-reply', 'donotreply')


def _h(v):
    try:
        return str(make_header(decode_header(v or '')))
    except Exception:
        return v or ''


def _body(msg):
    text, html = '', ''
    files = []
    for part in (msg.walk() if msg.is_multipart() else [msg]):
        ctype = part.get_content_type()
        disp = (part.get('Content-Disposition') or '').lower()
        name = part.get_filename()
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
        text = re.sub(r'<[^>]+>', ' ', re.sub(r'(?is)<(script|style).*?</\1>', '', html))
        text = re.sub(r'[ \t]+', ' ', text)
    # cut the quoted history of replies
    for marker in ('\nOn ', '\n-----Original Message-----', '\nFrom: ', '\n________________________________'):
        i = text.find(marker)
        if i > 0:
            text = text[:i]
    return text.strip()[:20000], files


def is_auto(msg, sender):
    if any(s in sender.lower() for s in SKIP_SENDERS):
        return True
    auto = (msg.get('Auto-Submitted') or '').lower()
    if auto and auto != 'no':
        return True
    if (msg.get('Precedence') or '').lower() in ('bulk', 'junk', 'list', 'auto_reply'):
        return True
    return bool(msg.get('X-Autoreply') or msg.get('X-Autorespond'))


def find_ticket(msg, subject):
    s = ProjectsSettings.get()
    m = re.search(r'\[?(' + re.escape(s.ticket_prefix) + r'-\d+)\]?', subject or '')
    if m:
        t = Ticket.objects.filter(number=m.group(1)).first()
        if t:
            return t
    refs = ' '.join([msg.get('In-Reply-To') or '', msg.get('References') or ''])
    for mid in re.findall(r'<[^>]+>', refs):
        t = Ticket.objects.filter(email_message_id=mid).first()
        if t:
            return t
    return None


def match_sender(addr):
    addr = (addr or '').lower()
    c = Contact.objects.filter(email__iexact=addr, is_active=True, customer__is_active=True).select_related('customer').first()
    if c:
        return c.customer, c
    domain = addr.split('@')[-1] if '@' in addr else ''
    if domain:
        for cust in Customer.objects.filter(is_active=True).exclude(email_domains=''):
            if domain in [x.strip().lower().lstrip('@') for x in cust.email_domains.split(',')]:
                return cust, None
    return None, None


def match_team(msg):
    to = [a.lower() for _, a in getaddresses(msg.get_all('To', []) + msg.get_all('Cc', []))]
    for team in SupportTeam.objects.filter(is_active=True).exclude(email_alias=''):
        if team.email_alias.lower() in to:
            return team
    return None


def process(msg):
    subject = _h(msg.get('Subject')) or '(no subject)'
    name, sender = parseaddr(_h(msg.get('From')))
    if not sender or is_auto(msg, sender):
        return 'skipped'
    body, files = _body(msg)
    t = find_ticket(msg, subject)
    if t is not None:
        contact = t.contact if (t.contact_id and t.contact.email.lower() == sender.lower()) else None
        if contact is None:
            _, contact = match_sender(sender)
            if contact is not None and contact.customer_id != t.customer_id:
                contact = None
        S.add_message(t, body or '(empty e-mail)', contact=contact, via='email', files=files, author_name=name or sender)
        return 'reply'
    customer, contact = match_sender(sender)
    S.create_ticket(subject=subject, description=body, channel='email', customer=customer, contact=contact,
                    team=match_team(msg), requester_email=sender, requester_name=name or sender, files=files,
                    message_id=(msg.get('Message-ID') or '').strip()[:255])
    return 'new'


def check_mailbox():
    """Read unread mails of this company's mailbox. Returns counts."""
    s = ProjectsSettings.get()
    if not (s.mailbox_enabled and s.mailbox_host and s.mailbox_user and s.mailbox_password):
        return {'skipped': 'mailbox off'}
    out = {'new': 0, 'reply': 0, 'skipped': 0, 'failed': 0}
    try:
        box = imaplib.IMAP4_SSL(s.mailbox_host, s.mailbox_port or 993) if s.mailbox_ssl else imaplib.IMAP4(s.mailbox_host, s.mailbox_port or 143)
        box.login(s.mailbox_user, s.mailbox_password)
        box.select(s.mailbox_folder or 'INBOX')
        typ, data = box.search(None, 'UNSEEN')
        ids = (data[0] or b'').split()[:MAX_PER_RUN]
        for num in ids:
            typ, raw = box.fetch(num, '(RFC822)')
            try:
                msg = email.message_from_bytes(raw[0][1])
                out[process(msg)] += 1
            except Exception:
                log.exception('mail %s could not be turned into a ticket', num)
                out['failed'] += 1
            box.store(num, '+FLAGS', '\\Seen')
        box.logout()
        s.mailbox_last_error = ''
    except Exception as exc:
        log.exception('mailbox check failed')
        s.mailbox_last_error = str(exc)[:300]
    s.mailbox_last_check = timezone.now()
    s.save(update_fields=['mailbox_last_check', 'mailbox_last_error'])
    return out
