"""v1.45.0 – collaboration on every record (Odoo chatter / Discuss).

Followers   who gets the messages of a record: added automatically (the creator, the employee of an employee record, whoever
            writes on it, activity assignees, approvers, @mentioned people) or by hand (users or outside e-mail addresses).
            Unfollow mutes the user: he is not added again automatically.
Messages    "Send message" goes to the followers – an inbox line and a bell notification for users, an e-mail for everyone with an
            address (Reply-To the company reply mailbox); "Log note" stays internal and only notifies the @mentioned people.
Replies     an e-mail reply carries [ZEO#M<id>] in the subject (and our Message-ID in In-Reply-To): the mailbox job puts it back on
            the record as an "E-mail reply" and tells the followers.
"""
import base64
import email
import email.utils
import hashlib
import imaplib
import logging
import re
from email.header import decode_header, make_header

from django.conf import settings as dj
from django.db import connection
from django.utils import timezone

from .models import Activity, Follower, InboxItem, MailSettings, Message, RecordLog

log = logging.getLogger(__name__)
TOKEN = re.compile(r'\[ZEO#M(\d+)\]')
MENTION = re.compile(r'@\[([^\]]{1,80})\]\(user:(\d+)\)')   # @[Name](user:12) – what the mention picker inserts


# ------------------------------------------------------------------ users and names
def user_model():
    from django.contrib.auth import get_user_model
    return get_user_model()


def user_email(u):
    if u is None:
        return ''
    if getattr(u, 'email', ''):
        return u.email
    try:
        from EmpManagement.models import emp_master
        e = emp_master.objects.filter(users=u).only('emp_personal_email').first()
        return (getattr(e, 'emp_personal_email', '') or '') if e else ''
    except Exception:
        return ''


def plain(body):
    """The text with @[Name](user:12) shown as @Name."""
    return MENTION.sub(lambda m: '@' + m.group(1), body or '')


def mention_ids(body, extra=()):
    ids = {int(m.group(2)) for m in MENTION.finditer(body or '')}
    ids.update(int(x) for x in (extra or []) if str(x).isdigit())
    return sorted(ids)


# ------------------------------------------------------------------ followers
def follow(key, obj_id, user=None, email_addr='', name='', reason='manual', label='', page='', force=False):
    """Add a follower; an automatic add (force=False) never brings back someone who unfollowed."""
    if user is None and not email_addr:
        return None
    q = Follower.objects.filter(model=key, object_id=str(obj_id))
    f = q.filter(user=user).first() if user is not None else q.filter(user__isnull=True, email__iexact=email_addr).first()
    if f is not None:
        if f.muted and force:
            f.muted, f.reason = False, reason
            f.save(update_fields=['muted', 'reason'])
        return f
    return Follower.objects.create(model=key, object_id=str(obj_id), user=user, email=(email_addr or '')[:200], name=(name or '')[:150],
                                   reason=reason, record_label=(label or '')[:255], page=(page or '')[:255])


def unfollow(key, obj_id, user):
    f = Follower.objects.filter(model=key, object_id=str(obj_id), user=user).first()
    if f is None:
        Follower.objects.create(model=key, object_id=str(obj_id), user=user, reason='self', muted=True)
    else:
        f.muted = True
        f.save(update_fields=['muted'])


def auto_followers(rec, label='', page=''):
    """The creator, the employee (of an employee record), whoever wrote a note, activity assignees and approvers."""
    key, oid = rec.key, rec.id
    users = []
    first = RecordLog.objects.filter(model=key, object_id=oid, action='created').exclude(user__isnull=True).order_by('at').first()
    if first:
        users.append((first.user, 'creator'))
    if key == 'EmpManagement.emp_master' and rec.obj is not None and getattr(rec.obj, 'users_id', None):
        users.append((rec.obj.users, 'employee'))
    for m in Message.objects.filter(model=key, object_id=oid).exclude(author__isnull=True).select_related('author')[:50]:
        users.append((m.author, 'author'))
    for a in Activity.objects.filter(model=key, object_id=oid).exclude(state='cancelled').select_related('assigned_to')[:50]:
        users.append((a.assigned_to, 'assignee'))
    try:
        from ZeoFlow.models import ApprovalStep
        for s in ApprovalStep.objects.filter(request__model=key, request__object_id=oid).select_related('approver')[:30]:
            if s.approver_id:
                users.append((s.approver, 'approver'))
    except Exception:
        pass
    have = set(Follower.objects.filter(model=key, object_id=oid, user__isnull=False).values_list('user_id', flat=True))
    for u, why in users:
        if u is not None and u.pk not in have:
            follow(key, oid, user=u, reason=why, label=label, page=page)
            have.add(u.pk)


def followers_json(key, obj_id):
    out = []
    for f in Follower.objects.filter(model=key, object_id=str(obj_id), muted=False).select_related('user'):
        from .views import uname
        out.append({'id': f.id, 'user_id': f.user_id, 'name': (uname(f.user) if f.user_id else (f.name or f.email)), 'email': f.email or user_email(f.user),
                    'reason': f.get_reason_display(), 'outside': f.user_id is None})
    return out


def is_following(key, obj_id, user):
    return Follower.objects.filter(model=key, object_id=str(obj_id), user=user, muted=False).exists()


# ------------------------------------------------------------------ delivery
def link(page):
    base = (getattr(dj, 'ZEO_APP_URL', '') or '').rstrip('/')
    return (base + page) if (base and page) else (page or '')


def send_mail(to, subject, body, msg_id='', reply_to='', in_reply_to=''):
    to = [x for x in dict.fromkeys(t.strip() for t in (to or []) if t and '@' in t)]
    if not to:
        return False
    try:
        from django.core.mail import EmailMessage, get_connection
        conn, sender = None, dj.DEFAULT_FROM_EMAIL
        try:
            from EmpManagement.models import EmailConfiguration
            cfg = EmailConfiguration.objects.filter(is_active=True).first()
            if cfg and cfg.email_host_user:
                conn = get_connection(host=cfg.email_host, port=cfg.email_port, username=cfg.email_host_user,
                                      password=cfg.email_host_password, use_tls=cfg.email_use_tls, fail_silently=False)
                sender = cfg.email_host_user
        except Exception:
            conn = None
        headers = {}
        if msg_id:
            headers['Message-ID'] = msg_id
        if in_reply_to:
            headers['In-Reply-To'] = in_reply_to
            headers['References'] = in_reply_to
        EmailMessage(subject=subject[:200], body=body, from_email=sender, to=to, connection=conn, headers=headers,
                     reply_to=[reply_to] if reply_to else None).send(fail_silently=False)
        return True
    except Exception:
        log.exception('chatter e-mail failed')
        return False


def _bell(user, title, message, page, key, oid):
    try:
        from ZeoFlow.models import FlowNotification
        FlowNotification.objects.create(recipient=user, title=title[:200], message=message[:2000], link=page or '', model=key, object_id=str(oid))
    except Exception:
        log.exception('bell notification failed')


def notify(user, kind, m, title, body, from_name=''):
    InboxItem.objects.create(user=user, kind=kind, model=m.model, object_id=m.object_id, record_label=m.record_label, page=m.page,
                             title=title[:255], body=body[:4000], from_name=from_name[:150], message=m)
    _bell(user, title, body[:300], m.page, m.model, m.object_id)


def message_id(m):
    dom = (getattr(dj, 'ZEO_APP_URL', '') or 'zeo.local').split('//')[-1].split('/')[0] or 'zeo.local'
    return f'<zeo-msg-{m.pk}-{connection.schema_name}@{dom}>'


def deliver(m, author, extra_emails=()):
    """A message (kind='message') to the followers; @mentions in a note or a message to the people mentioned."""
    from .views import uname
    who = uname(author) if author else (m.from_name or m.from_email or 'E-mail')
    label = m.record_label or m.model
    text = plain(m.body)
    sent, recipients = set(), []
    mentioned = set(m.mentions or [])
    # mentioned users follow the record
    U = user_model()
    for u in U.objects.filter(pk__in=mentioned, is_active=True):
        follow(m.model, m.object_id, user=u, reason='mentioned', label=m.record_label, page=m.page, force=True)
        if author is None or u.pk != author.pk:
            notify(u, 'mention', m, f'{who} mentioned you on {label}', text, who)
            sent.add(('u', u.pk))
            recipients.append({'name': uname(u), 'email': user_email(u), 'user_id': u.pk})
    if m.kind == 'note':
        m.recipients = recipients
        m.save(update_fields=['recipients'])
        for u in U.objects.filter(pk__in=mentioned):
            if author is None or u.pk != author.pk:
                e = user_email(u)
                if e:
                    send_mail([e], f'{who} mentioned you on {label}', f'{text}\n\n{link(m.page)}'.strip())
        return recipients
    if author is not None:
        follow(m.model, m.object_id, user=author, reason='author', label=m.record_label, page=m.page)
    kind = 'email' if m.kind == 'email_in' else 'message'
    title = (f'{who} replied by e-mail on {label}' if kind == 'email' else f'{who} sent a message on {label}')
    mails = []
    for f in Follower.objects.filter(model=m.model, object_id=m.object_id, muted=False).select_related('user'):
        if f.user_id:
            if (author is not None and f.user_id == author.pk) or ('u', f.user_id) in sent:
                continue
            notify(f.user, kind, m, title, text, who)
            sent.add(('u', f.user_id))
            e = user_email(f.user)
            recipients.append({'name': uname(f.user), 'email': e, 'user_id': f.user_id})
            if e and kind == 'message':
                mails.append(e)
        elif f.email and kind == 'message' and f.email.lower() != (m.from_email or '').lower():
            recipients.append({'name': f.name or f.email, 'email': f.email, 'user_id': None})
            mails.append(f.email)
    for e in extra_emails or []:
        e = (e or '').strip()
        if '@' in e and e.lower() not in {x.lower() for x in mails}:
            follow(m.model, m.object_id, email_addr=e, name=e, reason='manual', label=m.record_label, page=m.page)
            recipients.append({'name': e, 'email': e, 'user_id': None})
            mails.append(e)
    if mails:
        ms = MailSettings.objects.first()
        reply_to = (ms.reply_to if ms and ms.enabled else '') or ''
        mid = message_id(m)
        m.email_id = mid
        subject = f'{m.subject or ("Re: " + label)} [ZEO#M{m.pk}]'
        body = f'{text}\n\n— {who}, about {label}\n{link(m.page)}\n\nReply to this e-mail to answer on the record.' if reply_to else \
               f'{text}\n\n— {who}, about {label}\n{link(m.page)}'
        for e in dict.fromkeys(mails):
            send_mail([e], subject, body, msg_id=mid, reply_to=reply_to)
    m.recipients = recipients
    m.save(update_fields=['recipients', 'email_id'])
    return recipients


# ------------------------------------------------------------------ the reply mailbox
def _secret_key():
    return hashlib.sha256(('zeo-chatter-mail:' + dj.SECRET_KEY).encode()).digest()


def put_password(ms, value):
    from cryptography.fernet import Fernet
    ms.imap_password = Fernet(base64.urlsafe_b64encode(_secret_key())).encrypt(value.encode()).decode() if value else ''


def get_password(ms):
    if not ms.imap_password:
        return ''
    try:
        from cryptography.fernet import Fernet
        return Fernet(base64.urlsafe_b64encode(_secret_key())).decrypt(ms.imap_password.encode()).decode()
    except Exception:
        return ''


def _dec(v):
    try:
        return str(make_header(decode_header(v or '')))
    except Exception:
        return v or ''


def _text(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == 'text/plain' and 'attachment' not in str(part.get('Content-Disposition') or ''):
                return part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8', 'replace')
        for part in msg.walk():
            if part.get_content_type() == 'text/html':
                return re.sub(r'<[^>]+>', ' ', part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8', 'replace'))
        return ''
    return (msg.get_payload(decode=True) or b'').decode(msg.get_content_charset() or 'utf-8', 'replace')


def strip_quote(text):
    """The new part of a reply: stop at the quoted message ('On … wrote:', '-----Original Message-----', '>' lines)."""
    out = []
    for line in (text or '').splitlines():
        s = line.strip()
        if s.startswith('>') or re.match(r'^(On .+ wrote:|-----\s*Original Message\s*-----|From: .+|_{5,})$', s):
            break
        out.append(line)
    return '\n'.join(out).strip()[:20000]


def take_reply(msg):
    """Put one e-mail on its record. Returns the new Message or None (not a reply to a ZEO message)."""
    subject = _dec(msg.get('Subject'))
    if re.search(r'auto-?reply|out of office|automatic reply', subject, re.I) or msg.get('Auto-Submitted', 'no') != 'no':
        return None
    orig = None
    t = TOKEN.search(subject)
    if t:
        orig = Message.objects.filter(pk=int(t.group(1))).first()
    if orig is None:
        refs = ' '.join([msg.get('In-Reply-To') or '', msg.get('References') or ''])
        for mid in re.findall(r'<[^>]+>', refs):
            orig = Message.objects.filter(email_id=mid).first()
            if orig:
                break
    if orig is None:
        return None
    eid = (msg.get('Message-ID') or '').strip()
    if eid and Message.objects.filter(email_id=eid).exists():
        return None
    name, addr = email.utils.parseaddr(_dec(msg.get('From')))
    body = strip_quote(_text(msg)) or '(empty reply)'
    author = user_model().objects.filter(email__iexact=addr, is_active=True).first() if addr else None
    m = Message.objects.create(model=orig.model, object_id=orig.object_id, body=body, author=author, kind='email_in', subject=subject[:200],
                               from_name=(name or addr)[:150], from_email=addr[:200], email_id=eid[:255], record_label=orig.record_label, page=orig.page)
    if author is None and addr:
        follow(orig.model, orig.object_id, email_addr=addr, name=name or addr, reason='author', label=orig.record_label, page=orig.page)
    deliver(m, author)
    return m


def check_mailbox():
    ms = MailSettings.objects.first()
    if ms is None or not (ms.enabled and ms.imap_host and ms.imap_user):
        return 'off'
    n = 0
    try:
        box = imaplib.IMAP4_SSL(ms.imap_host, ms.imap_port) if ms.imap_ssl else imaplib.IMAP4(ms.imap_host, ms.imap_port)
        box.login(ms.imap_user, get_password(ms))
        box.select(ms.folder or 'INBOX')
        _, data = box.search(None, 'UNSEEN')
        for num in (data[0].split() if data and data[0] else [])[:50]:
            _, raw = box.fetch(num, '(RFC822)')
            try:
                if take_reply(email.message_from_bytes(raw[0][1])):
                    n += 1
            except Exception:
                log.exception('chatter mailbox: one e-mail failed')
            box.store(num, '+FLAGS', '\\Seen')
        box.logout()
        ms.last_error = ''
    except Exception as e:
        ms.last_error = str(e)[:255]
    ms.last_check, ms.replies_received = timezone.now(), ms.replies_received + n
    ms.save(update_fields=['last_check', 'last_error', 'replies_received'])
    return n


def move_record(key, from_id, to_id, user=None, note=''):
    """A merge: the notes, files, activities, followers and tags of one record move to the record kept."""
    from .models import Attachment, Star, RecentRecord, TagLink
    f, t = str(from_id), str(to_id)
    for M in (Message, Attachment, Activity):
        M.objects.filter(model=key, object_id=f).update(object_id=t)
    for x in Follower.objects.filter(model=key, object_id=f):
        if Follower.objects.filter(model=key, object_id=t, user=x.user, email=x.email).exists():
            x.delete()
        else:
            x.object_id = t
            x.save(update_fields=['object_id'])
    for l in TagLink.objects.filter(model=key, object_id=f):
        if TagLink.objects.filter(model=key, object_id=t, tag=l.tag).exists():
            l.delete()
        else:
            l.object_id = t
            l.save(update_fields=['object_id'])
    Star.objects.filter(model=key, object_id=f).delete()
    RecentRecord.objects.filter(model=key, object_id=f).delete()
    if note:
        Message.objects.create(model=key, object_id=t, body=note, author=user if getattr(user, 'pk', None) else None)


# ------------------------------------------------------------------ v1.46.0 threads and reminders
def notify_reply(m, parent, author):
    """A reply under a note: the people in the thread (its author and earlier repliers) are told."""
    from .views import uname
    who = uname(author)
    users = {parent.author_id} | set(Message.objects.filter(parent=parent).exclude(author__isnull=True).values_list('author_id', flat=True))
    users.discard(getattr(author, 'pk', None))
    U = user_model()
    for u in U.objects.filter(pk__in=[x for x in users if x], is_active=True):
        notify(u, 'reply', m, f'{who} replied on {m.record_label or m.model}', plain(m.body), who)


def send_reminders(user=None):
    """Reminder activities due today or earlier that were not sent yet → inbox line and bell for the person."""
    from django.utils import timezone as tz
    n = 0
    qs = Activity.objects.filter(activity_type='reminder', state='open', due_date__lte=tz.localdate(), notified_at__isnull=True).select_related('assigned_to')
    if user is not None:
        qs = qs.filter(assigned_to=user)
    for a in qs:
        InboxItem.objects.create(user=a.assigned_to, kind='reminder', model=a.model, object_id=a.object_id, record_label=a.record_label,
                                 page=a.page, title=f'Reminder: {a.summary}'[:255], body=a.note[:4000])
        _bell(a.assigned_to, f'Reminder: {a.summary}', a.note[:300] or a.record_label, a.page, a.model, a.object_id)
        Activity.objects.filter(pk=a.pk).update(notified_at=tz.now())
        n += 1
    return n
