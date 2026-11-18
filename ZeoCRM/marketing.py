"""v1.67.29 – CRM release 3: communication and marketing (scope 1, 8, 9 and part 6).

  * Lead connectors (Setup › Lead connectors): Facebook / Instagram lead ads (Meta web-hook + Graph API), Google Ads lead forms,
    LinkedIn lead gen forms (through the company's integration tool), website forms / landing pages, WhatsApp inbox and telephony.
    Each has its own secret web-hook address; leads are scored, assigned and attributed to the campaign and source.
  * Conversations (Sales › Conversations): one inbox for WhatsApp, SMS and e-mail threads, matched to the contact or lead (else a
    new lead), assigned to an agent; Reply, Assign, Close; every message is also an activity on the customer's timeline.
  * E-mail inbox (Setup › E-mail inboxes): an IMAP box read every few minutes – e-mails of known people become activities and
    conversations, unknown senders become leads (channel E-mail).
  * Templates (Setup › Message templates): e-mail / WhatsApp / SMS texts with placeholders; *Send e-mail* and *Send WhatsApp*
    on leads, contacts, opportunities, quotations and complaints – logged as activities.
  * Segments and campaign sending: target audiences by rules; *Send* a campaign by e-mail, WhatsApp or SMS now or at a time; only
    people who agreed to marketing and did not opt out; open and click tracking, unsubscribe link, delivery figures per send.
  * Telephony: calls posted by the phone system become call activities (with duration and recording); missed calls become call-back tasks.
  * Reports: campaign sends, lead channels, conversations per agent, connectors.

Sending uses the company's own e-mail settings and its WhatsApp / SMS gateway (Settings › WhatsApp / SMS – Twilio, WhatsApp Cloud
API of Meta or any HTTP gateway). Nothing is sent while those are not set up.
"""
import email
import email.utils
import html
import imaplib
import json
import logging
import re
import secrets
from collections import defaultdict
from datetime import timedelta
from email.header import decode_header, make_header
from urllib.parse import quote, urlparse

from django.db import transaction
from django.db.models import Count, F, Q
from django.http import HttpResponse, HttpResponseRedirect
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from zeo.appkit.engine import Act, AppError, Reg, d, emp_name, now, today, user_name

from .models import (Activity, Campaign, CampaignRecipient, CampaignSend, Contact, Conversation, ConversationMessage, Lead, LeadConnector, MailInbox,
                     MessageTemplate, Opportunity, Segment, Source)

log = logging.getLogger(__name__)
CTX = {}
SOURCE_OF = {'meta': ('FACEBOOK', 'Facebook / Instagram ads'), 'google': ('GOOGLEADS', 'Google Ads'), 'linkedin': ('LINKEDIN', 'LinkedIn'),
             'whatsapp': ('WHATSAPP', 'WhatsApp'), 'webform': ('WEB', 'Website'), 'telephony': ('PHONE', 'Phone call')}
CHANNEL_OF = {'meta': 'facebook', 'google': 'google', 'linkedin': 'linkedin', 'whatsapp': 'whatsapp', 'webform': 'web', 'telephony': 'phone'}


# ------------------------------------------------------------------ secrets, addresses
def put_secret(v):
    from Chatter.messaging import put_secret as p
    return p(v)


def get_secret(v):
    from Chatter.messaging import get_secret as g
    return g(v) if v else ''


def base_url():
    u = (CTX['app'].settings().public_api_url or '').rstrip('/')
    if not u:
        try:
            from django.conf import settings as dj
            u = (getattr(dj, 'ZEO_APP_URL', '') or '').rstrip('/')
        except Exception:   # noqa: BLE001
            u = ''
    return u


def schema():
    from django.db import connection
    return getattr(connection, 'schema_name', '') or ''


def hook_url(c):
    b = base_url()
    return f'{b}/zeo-crm/api/hooks/{c.key}/?schema={schema()}' if b else f'/zeo-crm/api/hooks/{c.key}/?schema={schema()}'


def track_url(kind, token, extra=''):
    b = base_url()
    if not b:
        return ''
    return f'{b}/zeo-crm/api/m/{kind}/{token}/?schema={schema()}{extra}'


def norm_phone(p):
    from Chatter.messaging import normalise
    try:
        from Chatter.messaging import settings as ms
        cc = ms().country_code or '971'
    except Exception:   # noqa: BLE001
        cc = '971'
    return normalise(p, cc)


def tail9(p):
    digits = re.sub(r'\D', '', str(p or ''))
    return digits[-9:] if len(digits) >= 7 else ''


# ------------------------------------------------------------------ sending
def send_email(to, subject, text, html_body=None, reply_to=None):
    """(ok, result). The company's e-mail settings (Settings › E-mail), like every ZEO e-mail."""
    to = [x for x in dict.fromkeys(t.strip() for t in ([to] if isinstance(to, str) else to or []) if t and '@' in t)]
    if not to:
        return False, 'no e-mail address'
    try:
        from django.conf import settings as dj
        from django.core.mail import EmailMultiAlternatives, get_connection
        conn, sender = None, dj.DEFAULT_FROM_EMAIL
        try:
            from EmpManagement.models import EmailConfiguration
            cfg = EmailConfiguration.objects.filter(is_active=True).first()
            if cfg and cfg.email_host_user:
                conn = get_connection(host=cfg.email_host, port=cfg.email_port, username=cfg.email_host_user, password=cfg.email_host_password,
                                      use_tls=cfg.email_use_tls, fail_silently=False)
                sender = cfg.email_host_user
        except Exception:   # noqa: BLE001
            conn = None
        s = CTX['app'].settings()
        if s.sender_name and '<' not in sender:
            sender = email.utils.formataddr((s.sender_name, sender))
        m = EmailMultiAlternatives(subject=(subject or '')[:200], body=text, from_email=sender, to=to, connection=conn,
                                   reply_to=[reply_to or s.reply_to] if (reply_to or s.reply_to) else None)
        if html_body:
            m.attach_alternative(html_body, 'text/html')
        m.send(fail_silently=False)
        return True, 'sent'
    except Exception as e:   # noqa: BLE001
        log.exception('CRM e-mail')
        return False, str(e)[:200]


def send_text(to, text, channel='whatsapp'):
    """(ok, result) through the company's WhatsApp / SMS gateway (Settings › WhatsApp / SMS)."""
    from Chatter import messaging as M
    s = M.settings()
    if not s.enabled:
        return False, 'WhatsApp / SMS is not switched on (Settings › WhatsApp / SMS)'
    msg = M.problems(s)
    if msg:
        return False, msg
    num = norm_phone(to)
    if not num:
        return False, 'no valid phone number'
    s.channel = channel if channel in ('whatsapp', 'sms') else s.channel   # this message only – not saved
    try:
        ok, res = M.deliver(s, num, text)
    except Exception as e:   # noqa: BLE001
        ok, res = False, str(e)[:200]
    try:
        from Chatter.models import MessageLog
        MessageLog.objects.create(to=num, channel=s.channel, kind='crm', text=text[:2000], status='sent' if ok else 'failed', result=str(res)[:255])
    except Exception:   # noqa: BLE001
        pass
    return ok, res


class _Safe(dict):
    def __missing__(self, k):
        return ''


def render(text, ctx):
    try:
        return (text or '').format_map(_Safe(ctx))
    except (ValueError, IndexError):
        out = text or ''
        for k, v in ctx.items():
            out = out.replace('{' + k + '}', str(v))
        return out


def ctx_for(o):
    from .models import Complaint, Quotation
    c = {'number': getattr(o, 'number', ''), 'title': getattr(o, 'title', '') or getattr(o, 'subject', ''), 'value': ''}
    person = o if isinstance(o, (Contact, Lead)) else getattr(o, 'contact', None)
    c['name'] = getattr(person, 'name', '') or ''
    c['first_name'] = (c['name'].split() or [''])[0]
    c['company'] = getattr(o, 'company_name', '') or (getattr(getattr(o, 'account', None), 'name', '') if getattr(o, 'account_id', None) else '') or \
        getattr(person, 'company_name', '') or ''
    owner = getattr(o, 'owner', None)
    c['agent'] = emp_name(owner) if owner else ''
    c['agent_phone'] = getattr(owner, 'emp_mobile_number_1', '') or '' if owner else ''
    if isinstance(o, Opportunity):
        c['value'] = f'{float(o.value or 0):,.2f}'
    if isinstance(o, Quotation):
        c['value'] = f'{o.currency} {float(o.total or 0):,.2f}'
    if isinstance(o, Complaint):
        c['title'] = o.subject
    return c


def party_of(o):
    """(contact, lead, e-mail, phone) of a record."""
    if isinstance(o, Contact):
        return o, None, o.email, o.phone
    if isinstance(o, Lead):
        return o.contact, o, o.email, o.phone
    c = getattr(o, 'contact', None) if getattr(o, 'contact_id', None) else None
    return c, None, getattr(c, 'email', ''), getattr(c, 'phone', '')


def log_activity(o, kind, subject, text, user=None, direction='out', phone=''):
    app = CTX['app']
    contact, lead, _, _ = party_of(o)
    opp = o if isinstance(o, Opportunity) else getattr(o, 'opportunity', None) if getattr(o, 'opportunity_id', None) else None
    a = Activity.objects.create(number=app.next_number('activity', 'ACT'), kind=kind, subject=subject[:200], contact=contact, lead=lead, opportunity=opp,
                                owner=getattr(o, 'owner', None),
                                status='done', done_at=now(), outcome=text[:4000], direction=direction, phone=phone or '',
                                branch_id=getattr(o, 'branch_id', None))
    return a


# ------------------------------------------------------------------ conversations
def find_party(phone='', email_=''):
    """(contact, lead) with this phone (last 9 digits) or e-mail."""
    c = l = None
    if email_:
        c = Contact.objects.filter(email__iexact=email_, is_active=True).first()
        l = None if c else Lead.objects.filter(email__iexact=email_).exclude(status='disqualified').order_by('-id').first()
    if c is None and l is None and phone:
        t = tail9(phone)
        if t:
            c = next((x for x in Contact.objects.filter(phone__contains=t[-4:], is_active=True) if tail9(x.phone) == t), None)   # stored as typed (spaces, 0 / +971)
            if c is None:
                l = next((x for x in Lead.objects.filter(phone__contains=t[-4:]).exclude(status='disqualified').order_by('-id') if tail9(x.phone) == t), None)
    return c, l


def new_lead(name, phone='', email_='', message='', connector=None, channel='api', extra_fields=None, company='', city='', job_title='', ref=''):
    app = CTX['app']
    from .app import SALES
    src = connector.source if connector and connector.source_id else None
    kind = connector.kind if connector else ''
    if src is None and kind in SOURCE_OF:
        code, nm = SOURCE_OF[kind]
        src, _ = Source.objects.get_or_create(code=code, defaults={'name': nm})
    out = SALES['lead_capture'](name=name or phone or email_ or 'Enquiry', email=email_ or '', phone=phone or '', company=company, message=message,
                                source=src.code if src else 'WEB', channel=channel, city=city,
                                campaign=connector.campaign.code if connector and connector.campaign_id else '',
                                industry=connector.industry if connector else '', fields=extra_fields or None)
    lead = Lead.objects.get(pk=out['id'])
    upd = []
    if job_title and not lead.job_title:
        lead.job_title = job_title[:80]
        upd.append('job_title')
    if ref:
        lead.external_ref = ref[:80]
        upd.append('external_ref')
    if connector and connector.owner_id and not lead.owner_id:
        lead.owner = connector.owner
        upd.append('owner')
    if upd:
        lead.save(update_fields=upd)
    return lead


def open_conversation(channel, address, name='', subject='', connector=None, create_lead=True, first_text=''):
    app = CTX['app']
    conv = Conversation.objects.filter(channel=channel, address=address).exclude(status='closed').order_by('-id').first()
    if conv is not None:
        return conv
    c, l = find_party(phone=address if channel != 'email' else '', email_=address if channel == 'email' else '')
    if c is None and l is None and create_lead:
        l = new_lead(name, phone=address if channel != 'email' else '', email_=address if channel == 'email' else '', message=first_text, connector=connector,
                     channel='whatsapp' if channel == 'whatsapp' else ('email' if channel == 'email' else 'api'))
    owner = (connector.owner if connector and connector.owner_id else None) or getattr(c, 'owner', None) or getattr(l, 'owner', None)
    opp = Opportunity.objects.filter(contact=c, status='open').order_by('-id').first() if c else None
    conv = Conversation.objects.create(number=app.next_number('conversation', 'CNV'), channel=channel, address=address, name=name or getattr(c or l, 'name', ''),
                                       subject=subject[:200], contact=c, lead=l, opportunity=opp, owner=owner,
                                       branch_id=getattr(c or l, 'branch_id', None))
    app.event('conversation', conv.pk, f'Started ({conv.get_channel_display()})', None)
    return conv


def add_incoming(conv, text, ext_id='', subject=''):
    app = CTX['app']
    if ext_id and ConversationMessage.objects.filter(conversation=conv, external_id=ext_id).exists():
        return None
    m = ConversationMessage.objects.create(conversation=conv, direction='in', text=text[:8000], external_id=ext_id[:120], status='received')
    conv.unread += 1
    conv.status = 'open'
    conv.last_message, conv.last_message_at = text[:200], now()
    conv.save(update_fields=['unread', 'status', 'last_message', 'last_message_at'])
    who = conv.contact or conv.lead
    Activity.objects.create(number=app.next_number('activity', 'ACT'), kind='email' if conv.channel == 'email' else 'message',
                            subject=(subject or f'{conv.get_channel_display()} from {conv.name or conv.address}')[:200], contact=conv.contact, lead=conv.lead,
                            opportunity=conv.opportunity, owner=conv.owner, status='done', done_at=now(), outcome=text[:4000], direction='in', phone=conv.address if conv.channel != 'email' else '',
                            branch_id=conv.branch_id)
    if conv.owner_id and getattr(conv.owner, 'users_id', None):
        app.close_todos('conversation', conv.pk)
        app.todo(conv.owner.users, f'New {conv.get_channel_display()} message: {conv.name or conv.address}', 'conversation', conv.pk, conv.number,
                 app.page + f'r/conversation/{conv.pk}', text[:200])
    _ = who
    return m


def reply(conv, text, user=None):
    app = CTX['app']
    if conv.channel == 'email':
        ok, res = send_email([conv.address], f'Re: {conv.subject}' if conv.subject else 'Re:', text)
    else:
        ok, res = send_text(conv.address, text, conv.channel)
    m = ConversationMessage.objects.create(conversation=conv, direction='out', text=text[:8000], by=user, status='sent' if ok else 'failed', result=str(res)[:200])
    conv.unread = 0
    conv.status = 'waiting' if ok else conv.status
    conv.last_message, conv.last_message_at = text[:200], now()
    conv.save(update_fields=['unread', 'status', 'last_message', 'last_message_at'])
    Activity.objects.create(number=app.next_number('activity', 'ACT'), kind='email' if conv.channel == 'email' else 'message',
                            subject=f'Reply to {conv.name or conv.address}'[:200], contact=conv.contact, lead=conv.lead, opportunity=conv.opportunity, owner=conv.owner,
                            status='done', done_at=now(), outcome=text[:4000], direction='out', phone=conv.address if conv.channel != 'email' else '', branch_id=conv.branch_id)
    app.close_todos('conversation', conv.pk)
    if not ok:
        raise AppError(f'Not sent: {res}')
    return m


# ------------------------------------------------------------------ incoming: web-hooks
def _field_map(pairs):
    """Lead fields from {name: value} of an ad form (Meta field_data, Google column ids, LinkedIn answers)."""
    out = {'name': '', 'first': '', 'last': '', 'email': '', 'phone': '', 'company': '', 'city': '', 'job_title': '', 'other': {}}
    for k, v in pairs.items():
        kk = re.sub(r'[^a-z0-9]', '', str(k).lower())
        v = v[0] if isinstance(v, list) and v else v
        v = '' if v is None else str(v)
        if kk in ('fullname', 'name'):
            out['name'] = v
        elif kk in ('firstname', 'givenname'):
            out['first'] = v
        elif kk in ('lastname', 'familyname', 'surname'):
            out['last'] = v
        elif kk in ('email', 'emailaddress', 'workemail', 'useremail'):
            out['email'] = v
        elif kk in ('phone', 'phonenumber', 'mobile', 'mobilenumber', 'workphone', 'whatsapp'):
            out['phone'] = v
        elif kk in ('company', 'companyname', 'organization', 'organisation', 'businessname'):
            out['company'] = v
        elif kk in ('city', 'town', 'location'):
            out['city'] = v
        elif kk in ('jobtitle', 'title', 'position', 'designation'):
            out['job_title'] = v
        else:
            out['other'][str(k)] = v
    if not out['name']:
        out['name'] = ' '.join(x for x in (out['first'], out['last']) if x)
    return out


def _lead_from_form(connector, pairs, ref='', extra_note=''):
    if ref and Lead.objects.filter(external_ref=ref).exists():
        return Lead.objects.filter(external_ref=ref).first(), False
    f = _field_map(pairs)
    from .models import IndustryField
    codes = set(IndustryField.objects.filter(is_active=True, on_lead=True).values_list('code', flat=True))
    fields = {k: v for k, v in f['other'].items() if k in codes}
    note = '\n'.join(f'{k}: {v}' for k, v in f['other'].items() if k not in codes)
    if extra_note:
        note = (extra_note + '\n' + note).strip()
    lead = new_lead(f['name'], phone=f['phone'], email_=f['email'], message=note, connector=connector, channel=CHANNEL_OF.get(connector.kind, 'api'),
                    extra_fields=fields, company=f['company'], city=f['city'], job_title=f['job_title'], ref=ref)
    return lead, True


def handle_meta(c, data):
    """Meta lead ads web-hook: {object: 'page', entry: [{changes: [{field: 'leadgen', value: {leadgen_id, form_id, ad_id, ...}}]}]}.
    The lead itself is read from the Graph API with the page token; a test payload may carry field_data directly."""
    made = 0
    for e in data.get('entry') or []:
        for ch in e.get('changes') or []:
            if ch.get('field') != 'leadgen':
                continue
            v = ch.get('value') or {}
            lid = str(v.get('leadgen_id') or '')
            fd = v.get('field_data')
            if fd is None and lid:
                tok = get_secret(c.secret)
                if not tok:
                    raise AppError('Give the page access token on the connector.')
                import requests
                r = requests.get(f'https://graph.facebook.com/v20.0/{lid}', params={'access_token': tok, 'fields': 'field_data,created_time,ad_name,campaign_name,form_id'}, timeout=20)
                if r.status_code != 200:
                    raise AppError(f'Meta answered {r.status_code}: {r.text[:150]}')
                j = r.json()
                fd = j.get('field_data') or []
                v.setdefault('campaign_name', j.get('campaign_name', ''))
            pairs = {x.get('name'): x.get('values') for x in (fd or [])}
            camp = _campaign_for(c, v.get('form_id'), v.get('campaign_id'), v.get('campaign_name'))
            if camp is not None and c.campaign_id is None:
                c.campaign = camp
            _, new = _lead_from_form(c, pairs, ref=f'meta:{lid}' if lid else '', extra_note=f'Meta form {v.get("form_id", "")} ad {v.get("ad_id", "")}'.strip())
            made += int(new)
    return made


def _campaign_for(c, *refs):
    for r in refs:
        if r:
            x = Campaign.objects.filter(Q(external_ref=str(r)) | Q(name__iexact=str(r))).first()
            if x is not None:
                return x
    return None


def handle_google(c, data):
    """Google Ads lead form web-hook: {google_key, lead_id, campaign_id, form_id, user_column_data: [{column_id, column_name, string_value}]}."""
    key = get_secret(c.secret)
    if key and str(data.get('google_key') or '') != key:
        raise AppError('Wrong Google key.', status=403)
    pairs = {}
    for x in data.get('user_column_data') or []:
        cid = str(x.get('column_id') or '')
        name = {'FULL_NAME': 'full_name', 'FIRST_NAME': 'first_name', 'LAST_NAME': 'last_name', 'EMAIL': 'email', 'PHONE_NUMBER': 'phone_number',
                'COMPANY_NAME': 'company_name', 'JOB_TITLE': 'job_title', 'CITY': 'city', 'WORK_EMAIL': 'work_email', 'WORK_PHONE': 'work_phone'}.get(cid) or x.get('column_name') or cid
        pairs[name] = x.get('string_value')
    camp = _campaign_for(c, data.get('campaign_id'), data.get('form_id'))
    if camp is not None and c.campaign_id is None:
        c.campaign = camp
    if data.get('is_test'):
        pairs.setdefault('note', 'Google test lead')
    _, new = _lead_from_form(c, pairs, ref=f'google:{data.get("lead_id")}' if data.get('lead_id') else '')
    return int(new)


def handle_linkedin(c, data):
    """LinkedIn lead gen form (sent by the company's integration tool – Zapier, Make, LinkedIn Lead Sync – as JSON):
    flat fields (firstName, lastName, emailAddress, phoneNumber, companyName, jobTitle, city) or {answers: [{question, answer}]}."""
    pairs = {k: v for k, v in data.items() if not isinstance(v, (dict, list)) and k not in ('leadId', 'formId', 'campaignId', 'campaignName')}
    for a in data.get('answers') or []:
        pairs[a.get('question') or a.get('name') or ''] = a.get('answer') or a.get('value')
    camp = _campaign_for(c, data.get('campaignId'), data.get('formId'), data.get('campaignName'))
    if camp is not None and c.campaign_id is None:
        c.campaign = camp
    _, new = _lead_from_form(c, pairs, ref=f'linkedin:{data.get("leadId")}' if data.get('leadId') else '')
    return int(new)


def handle_webform(c, data):
    pairs = {k: v for k, v in data.items() if not isinstance(v, (dict, list)) and k not in ('website', 'key')}
    if str(data.get('website') or '').strip():   # honeypot
        return 0
    _, new = _lead_from_form(c, pairs)
    return int(new)


def handle_whatsapp(c, data):
    """Incoming WhatsApp: Meta Cloud API ({entry: [{changes: [{value: {messages, contacts}}]}]}), Twilio (From, Body, ProfileName)
    or a generic gateway ({from, text, name, id})."""
    msgs = []
    for e in data.get('entry') or []:
        for ch in e.get('changes') or []:
            v = ch.get('value') or {}
            names = {x.get('wa_id'): (x.get('profile') or {}).get('name', '') for x in v.get('contacts') or []}
            for m in v.get('messages') or []:
                text = (m.get('text') or {}).get('body') or (m.get('button') or {}).get('text') or (m.get('interactive') or {}).get('button_reply', {}).get('title') \
                    or f'[{m.get("type", "message")}]'
                msgs.append(('+' + str(m.get('from', '')).lstrip('+'), names.get(m.get('from'), ''), text, str(m.get('id', ''))))
    if not msgs and (data.get('From') or data.get('from')):
        frm = str(data.get('From') or data.get('from')).replace('whatsapp:', '')
        msgs.append((frm, str(data.get('ProfileName') or data.get('name') or ''), str(data.get('Body') or data.get('text') or ''), str(data.get('MessageSid') or data.get('id') or '')))
    n = 0
    for frm, name, text, mid in msgs:
        num = norm_phone(frm) or frm
        conv = open_conversation('whatsapp', num, name, connector=c, first_text=text)
        if add_incoming(conv, text, mid) is not None:
            n += 1
    return n


def handle_call(c, data):
    """A call from the phone system: {from, to, direction (in / out), status (answered / missed / busy), duration (seconds), recording_url,
    agent (e-mail or extension), id}."""
    app = CTX['app']
    direction = 'out' if str(data.get('direction') or '').lower().startswith('out') else 'in'
    other = str(data.get('to') if direction == 'out' else data.get('from') or '')
    contact, lead = find_party(phone=other)
    agent = None
    ag = str(data.get('agent') or '')
    if ag:
        from EmpManagement.models import emp_master
        agent = emp_master.objects.filter(Q(emp_personal_email__iexact=ag) | Q(emp_code__iexact=ag)).first() if '@' in ag or ag else None
    status_ = str(data.get('status') or 'answered').lower()
    missed = direction == 'in' and status_ in ('missed', 'no-answer', 'noanswer', 'busy', 'unanswered')
    if contact is None and lead is None and direction == 'in':
        lead = new_lead(str(data.get('name') or ''), phone=other, message='Incoming call' + (' (missed)' if missed else ''), connector=c, channel='phone')
    owner = agent or getattr(contact, 'owner', None) or getattr(lead, 'owner', None) or (c.owner if c.owner_id else None)
    dur = int(float(data.get('duration') or 0)) if str(data.get('duration') or '').replace('.', '', 1).isdigit() else None
    a = Activity.objects.create(number=app.next_number('activity', 'ACT'), kind='call',
                                subject=('Missed call – call back ' if missed else ('Call to ' if direction == 'out' else 'Call from ')) + (getattr(contact or lead, 'name', '') or other),
                                contact=contact, lead=lead, owner=owner, status='open' if missed else 'done', done_at=None if missed else now(),
                                due_at=now() + timedelta(hours=1) if missed else None, direction=direction, phone=other, duration_sec=dur,
                                recording_url=str(data.get('recording_url') or '')[:300], outcome=str(data.get('note') or '')[:2000],
                                priority='high' if missed else 'normal')
    if missed and owner is not None and getattr(owner, 'users_id', None):
        app.todo(owner.users, f'Missed call: {a.subject}', 'activity', a.pk, a.number, app.page + f'r/activity/{a.pk}')
    return 1


HANDLERS = {'meta': handle_meta, 'google': handle_google, 'linkedin': handle_linkedin, 'webform': handle_webform, 'whatsapp': handle_whatsapp, 'telephony': handle_call}


@method_decorator(csrf_exempt, name='dispatch')
class HookView(APIView):
    """/zeo-crm/api/hooks/<key>/?schema=<company> – GET: Meta / WhatsApp verification (hub.challenge); POST: leads, messages, calls."""
    permission_classes = [AllowAny]
    authentication_classes = []
    zeo_access = False
    zeo_scope = False

    def _connector(self, key):
        return LeadConnector.objects.filter(key=key, is_active=True).first()

    def get(self, request, key):
        c = self._connector(key)
        if c is None:
            return HttpResponse('unknown', status=404)
        q = request.query_params
        if q.get('hub.mode') == 'subscribe' and q.get('hub.verify_token') == (c.verify_token or '') and c.verify_token:
            return HttpResponse(q.get('hub.challenge', ''), content_type='text/plain')
        return HttpResponse('ok', content_type='text/plain')

    def post(self, request, key):
        c = self._connector(key)
        if c is None:
            return Response({'detail': 'Unknown key.'}, status=404)
        data = request.data if isinstance(request.data, dict) else {}
        if hasattr(request.data, 'dict'):
            data = request.data.dict()
        try:
            with transaction.atomic():
                n = HANDLERS[c.kind](c, data)
            c.received += n
            c.last_received_at, c.last_error = now(), ''
            c.save(update_fields=['received', 'last_received_at', 'last_error'])
            return Response({'ok': True, 'received': n})
        except AppError as e:
            c.last_error = e.message[:255]
            c.save(update_fields=['last_error'])
            return Response({'detail': e.message}, status=e.status if e.status in (400, 403) else 400)
        except Exception as e:   # noqa: BLE001
            log.exception('CRM web-hook %s', c.kind)
            c.last_error = str(e)[:255]
            c.save(update_fields=['last_error'])
            return Response({'detail': 'Could not take it.'}, status=500)


# ------------------------------------------------------------------ tracking and unsubscribe
PIXEL = b'GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;'


def _rec(token):
    return CampaignRecipient.objects.filter(token=token).select_related('send').first()


@method_decorator(csrf_exempt, name='dispatch')
class TrackView(APIView):
    """o = opened (1×1 picture), c = clicked (redirect to ?u=), u = unsubscribe (page)."""
    permission_classes = [AllowAny]
    authentication_classes = []
    zeo_access = False
    zeo_scope = False

    def get(self, request, kind, token):
        r = _rec(token)
        if kind == 'o':
            if r is not None and r.opened_at is None:
                r.opened_at = now()
                r.save(update_fields=['opened_at'])
                CampaignSend.objects.filter(pk=r.send_id).update(opened=F('opened') + 1)
            return HttpResponse(PIXEL, content_type='image/gif')
        if kind == 'c':
            u = request.query_params.get('u') or ''
            if r is not None and r.clicked_at is None:
                r.clicked_at = now()
                if r.opened_at is None:
                    r.opened_at = r.clicked_at
                    CampaignSend.objects.filter(pk=r.send_id).update(opened=F('opened') + 1)
                r.save(update_fields=['clicked_at', 'opened_at'])
                CampaignSend.objects.filter(pk=r.send_id).update(clicked=F('clicked') + 1)
            if not re.match(r'^https?://', u) or urlparse(u).scheme not in ('http', 'https'):
                return HttpResponse('Link not valid.', status=400)
            return HttpResponseRedirect(u)
        if kind == 'u':
            if r is not None:
                unsubscribe(r)
            body = ('<html><body style="font-family:sans-serif;max-width:520px;margin:60px auto;text-align:center">'
                    '<h2>You are unsubscribed</h2><p>You will not get these messages from us any more.</p></body></html>')
            return HttpResponse(body, content_type='text/html')
        return HttpResponse(status=404)

    post = get


def unsubscribe(r):
    if r.unsubscribed_at:
        return
    field = {'email': 'email_opt_out', 'sms': 'sms_opt_out', 'whatsapp': 'whatsapp_opt_out'}[r.send.channel]
    for o in (r.contact, r.lead):
        if o is not None:
            setattr(o, field, True)
            if isinstance(o, Contact):
                o.opted_out_at = now()
            o.save()
            CTX['app'].event('contact' if isinstance(o, Contact) else 'lead', o.pk, f'Unsubscribed from {r.send.get_channel_display()} marketing ({r.send.number})', None)
    r.unsubscribed_at = now()
    r.save(update_fields=['unsubscribed_at'])
    CampaignSend.objects.filter(pk=r.send_id).update(unsubscribed=F('unsubscribed') + 1)


# ------------------------------------------------------------------ segments and campaign sending
def segment_people(sg):
    """[(contact, lead, name, email, phone, opt-outs)] of a segment."""
    out = []
    if sg.target == 'contacts':
        qs = Contact.objects.filter(is_active=True, merged_into__isnull=True)
        if sg.consent_only:
            qs = qs.filter(consent=True)
        if sg.contact_type:
            qs = qs.filter(contact_type=sg.contact_type)
        if sg.tags:
            qs = qs.filter(tags__contains=[sg.tags])
        if sg.source_id:
            qs = qs.filter(source_id=sg.source_id)
        if sg.territory_id:
            qs = qs.filter(territory_id=sg.territory_id)
        if sg.city:
            qs = qs.filter(Q(account__city__icontains=sg.city) | Q(notes__icontains=sg.city))
        if sg.industry:
            qs = qs.filter(opportunities__industry=sg.industry).distinct()
        if sg.customers_only:
            qs = qs.filter(Q(opportunities__status='won') | Q(contact_type='customer')).distinct()
        if sg.created_from:
            qs = qs.filter(created_at__date__gte=sg.created_from)
        if sg.branch_id:
            qs = qs.filter(Q(branch_id=sg.branch_id) | Q(branch__isnull=True))
        for c in qs[:50000]:
            out.append((c, None, c.name, c.email, c.phone, {'email': c.email_opt_out, 'sms': c.sms_opt_out, 'whatsapp': c.whatsapp_opt_out}))
    else:
        qs = Lead.objects.exclude(status__in=('converted', 'disqualified'))
        if sg.consent_only:
            qs = qs.filter(consent=True)
        if sg.lead_status:
            qs = qs.filter(status=sg.lead_status)
        if sg.source_id:
            qs = qs.filter(source_id=sg.source_id)
        if sg.campaign_id:
            qs = qs.filter(campaign_id=sg.campaign_id)
        if sg.territory_id:
            qs = qs.filter(territory_id=sg.territory_id)
        if sg.industry:
            qs = qs.filter(industry=sg.industry)
        if sg.city:
            qs = qs.filter(city__icontains=sg.city)
        if sg.min_score is not None:
            qs = qs.filter(score__gte=sg.min_score)
        if sg.created_from:
            qs = qs.filter(created_at__date__gte=sg.created_from)
        if sg.branch_id:
            qs = qs.filter(Q(branch_id=sg.branch_id) | Q(branch__isnull=True))
        for x in qs[:50000]:
            out.append((None, x, x.name, x.email, x.phone, {'email': x.email_opt_out, 'sms': x.sms_opt_out, 'whatsapp': x.whatsapp_opt_out}))
    return out


def prepare(send):
    """The recipients of a send (once)."""
    if send.recipients.exists():
        return
    rows, seen = [], set()
    skipped = 0
    for c, l, name, em, ph, opt in segment_people(send.segment):
        to = (em or '').strip().lower() if send.channel == 'email' else norm_phone(ph)
        if not to or opt.get(send.channel) or to in seen:
            skipped += 1
            continue
        seen.add(to)
        rows.append(CampaignRecipient(send=send, contact=c, lead=l, name=name[:150], to=to[:150], token=secrets.token_urlsafe(18)))
    CampaignRecipient.objects.bulk_create(rows, batch_size=500)
    send.total, send.skipped = len(rows), skipped
    send.save(update_fields=['total', 'skipped'])


def _html(text, r):
    esc = html.escape(text)

    def link(m):
        u = html.unescape(m.group(0))
        t = track_url('c', r.token, '&u=' + quote(u, safe=''))
        return f'<a href="{html.escape(t or u)}">{html.escape(u)}</a>'
    body = re.sub(r'https?://[^\s<]+', link, esc).replace('\n', '<br>')
    px = track_url('o', r.token)
    return f'<div style="font-family:Arial,sans-serif;font-size:14px;line-height:1.5">{body}</div>' + (f'<img src="{html.escape(px)}" width="1" height="1" alt="">' if px else '')


def process(send, limit=500):
    """Send up to `limit` queued messages of a send; marks it sent when none are left."""
    app = CTX['app']
    s = app.settings()
    prepare(send)
    if send.status in ('draft', 'scheduled'):
        send.status = 'sending'
        send.save(update_fields=['status'])
    tpl = send.template
    subject = send.subject or (tpl.subject if tpl else '') or send.campaign.name
    body = send.body or (tpl.body if tpl else '')
    done = 0
    for r in send.recipients.filter(status='queued')[:limit]:
        person = r.contact or r.lead
        ctx = ctx_for(person) if person is not None else {}
        ctx['campaign'] = send.campaign.name
        unsub = track_url('u', r.token)
        ctx['unsubscribe'] = unsub or ('Reply STOP to stop these messages.' if send.channel != 'email' else 'Reply "unsubscribe" to stop these e-mails.')
        text = render(body, ctx)
        if '{unsubscribe}' not in body:
            text = text.rstrip() + '\n\n' + (s.marketing_footer or '') + ('\n' + ctx['unsubscribe'] if ctx['unsubscribe'] else '')
        if send.channel == 'email':
            ok, res = send_email([r.to], render(subject, ctx), text, _html(text, r))
        else:
            ok, res = send_text(r.to, text, send.channel)
        r.status, r.error, r.sent_at = ('sent' if ok else 'failed'), ('' if ok else str(res)[:200]), now() if ok else None
        r.save(update_fields=['status', 'error', 'sent_at'])
        done += 1
    agg = send.recipients.aggregate(sent=Count('id', filter=Q(status='sent')), failed=Count('id', filter=Q(status='failed')), left=Count('id', filter=Q(status='queued')))
    send.sent, send.failed = agg['sent'], agg['failed']
    if agg['left'] == 0:
        send.status, send.sent_at = 'sent', now()
        app.event('campaign_send', send.pk, f'Sent: {send.sent} sent, {send.failed} failed, {send.skipped} skipped', None)
    send.save(update_fields=['sent', 'failed', 'status', 'sent_at'])
    return done


# ------------------------------------------------------------------ e-mail inbox (IMAP)
def _dec(v):
    try:
        return str(make_header(decode_header(v or '')))
    except Exception:   # noqa: BLE001
        return v or ''


def parse_email(raw):
    """(from e-mail, name, subject, text, message id) of a raw e-mail."""
    m = email.message_from_bytes(raw)
    name, addr = email.utils.parseaddr(_dec(m.get('From')))
    subject = _dec(m.get('Subject'))
    text = ''
    if m.is_multipart():
        for part in m.walk():
            if part.get_content_type() == 'text/plain' and 'attachment' not in str(part.get('Content-Disposition') or ''):
                text = part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8', 'replace')
                break
        if not text:
            for part in m.walk():
                if part.get_content_type() == 'text/html':
                    text = re.sub(r'<[^>]+>', ' ', part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8', 'replace'))
                    break
    else:
        text = (m.get_payload(decode=True) or b'').decode(m.get_content_charset() or 'utf-8', 'replace')
        if m.get_content_type() == 'text/html':
            text = re.sub(r'<[^>]+>', ' ', text)
    text = re.split(r'\n\s*On .{5,80} wrote:\s*\n|\n-{2,}\s*Original Message', text)[0]   # quoted replies cut
    return addr.lower(), name, subject, re.sub(r'\n{3,}', '\n\n', text).strip(), (m.get('Message-ID') or '').strip()


def take_email(inbox, raw):
    addr, name, subject, text, mid = parse_email(raw)
    if not addr or (inbox.username and addr == inbox.username.lower()):
        return False
    c, l = find_party(email_=addr)
    if c is None and l is None and not inbox.create_leads:
        return False
    if c is None and l is None:
        fake = LeadConnector(kind='webform', owner=inbox.owner, name=inbox.name)
        l = new_lead(name or addr, email_=addr, message=f'{subject}\n\n{text}'[:4000], connector=fake, channel='email')
    conv = Conversation.objects.filter(channel='email', address=addr).exclude(status='closed').order_by('-id').first()
    if conv is None:
        app = CTX['app']
        conv = Conversation.objects.create(number=app.next_number('conversation', 'CNV'), channel='email', address=addr, name=name or getattr(c or l, 'name', ''),
                                           subject=subject[:200], contact=c, lead=l, owner=getattr(c or l, 'owner', None) or inbox.owner,
                                           opportunity=Opportunity.objects.filter(contact=c, status='open').order_by('-id').first() if c else None)
    return add_incoming(conv, text or '(no text)', mid, subject=subject) is not None


def poll(inbox, limit=50):
    """Read new e-mails of an inbox. Returns how many were taken."""
    pw = get_secret(inbox.secret)
    M = None
    n = 0
    try:
        M = imaplib.IMAP4_SSL(inbox.host, inbox.port, timeout=30) if inbox.use_ssl else imaplib.IMAP4(inbox.host, inbox.port, timeout=30)
        M.login(inbox.username, pw)
        M.select(inbox.folder or 'INBOX')
        typ, data = M.uid('search', None, f'UID {inbox.last_uid + 1}:*')
        uids = [int(x) for x in (data[0] or b'').split() if int(x) > inbox.last_uid][:limit]
        for uid in uids:
            typ, msg = M.uid('fetch', str(uid), '(RFC822)')
            raw = msg[0][1] if msg and msg[0] else b''
            try:
                with transaction.atomic():
                    if take_email(inbox, raw):
                        n += 1
            except Exception:   # noqa: BLE001
                log.exception('inbox %s uid %s', inbox.name, uid)
            inbox.last_uid = uid
        inbox.last_checked_at, inbox.last_result = now(), f'{n} e-mail(s) taken'
    except Exception as e:   # noqa: BLE001
        inbox.last_checked_at, inbox.last_result = now(), f'Error: {e}'[:255]
    finally:
        try:
            if M is not None:
                M.logout()
        except Exception:   # noqa: BLE001
            pass
    inbox.save(update_fields=['last_uid', 'last_checked_at', 'last_result'])
    return n


def run_jobs():
    """Every few minutes (python manage.py zeocrm_marketing): scheduled sends and e-mail inboxes."""
    out = {'sent': 0, 'inbox': 0}
    for s in CampaignSend.objects.filter(Q(status='scheduled', scheduled_at__lte=now()) | Q(status='sending')):
        try:
            out['sent'] += process(s)
        except Exception:   # noqa: BLE001
            log.exception('campaign send %s', s.number)
    for ib in MailInbox.objects.filter(is_active=True):
        try:
            out['inbox'] += poll(ib)
        except Exception:   # noqa: BLE001
            log.exception('inbox %s', ib.name)
    return out


# ------------------------------------------------------------------ reports
def reports(a):
    from ZeoApps.reports import SLUG_PAGE, _branch_pick
    codes = [a.code(r) for r, _ in a.roles]
    page = SLUG_PAGE['crm']

    def sends(run):
        rows = []
        for s in _branch_pick(run, a.regs['campaign_send'].scope(run.request), 'branch').filter(created_at__date__gte=run.date_from, created_at__date__lte=run.date_to).select_related('campaign', 'segment'):
            leads = Lead.objects.filter(campaign=s.campaign, created_at__gte=s.sent_at or s.created_at).count() if s.sent_at else 0
            rows.append({'send': s.number, 'campaign': s.campaign.name, 'segment': s.segment.name, 'channel': s.get_channel_display(), 'date': d(s.sent_at or s.scheduled_at),
                         'total': s.total, 'sent': s.sent, 'failed': s.failed, 'skipped': s.skipped, 'opened': s.opened, 'clicked': s.clicked, 'unsubscribed': s.unsubscribed,
                         'open_rate': round(100 * s.opened / s.sent, 1) if s.sent and s.channel == 'email' else None,
                         'click_rate': round(100 * s.clicked / s.sent, 1) if s.sent and s.channel == 'email' else None, 'leads_after': leads,
                         '_url': f'{page}r/campaign_send/{s.pk}'})
        return [('send', 'Send', 'text'), ('campaign', 'Campaign', 'text'), ('segment', 'Segment', 'text'), ('channel', 'Channel', 'text'), ('date', 'Sent', 'date'),
                ('total', 'Recipients', 'number'), ('sent', 'Sent', 'number'), ('failed', 'Failed', 'number'), ('skipped', 'Skipped', 'number'), ('opened', 'Opened', 'number'),
                ('clicked', 'Clicked', 'number'), ('unsubscribed', 'Unsubscribed', 'number'), ('open_rate', 'Open rate %', 'number'), ('click_rate', 'Click rate %', 'number'),
                ('leads_after', 'Campaign leads since', 'number')], rows, {'no_totals': ['open_rate', 'click_rate']}

    def channels(run):
        qs = _branch_pick(run, a.regs['lead'].scope(run.request), 'branch').filter(created_at__date__gte=run.date_from, created_at__date__lte=run.date_to)
        rows = []
        lab = dict(Lead._meta.get_field('channel').choices)
        for x in qs.values('channel').annotate(n=Count('id'), conv=Count('id', filter=Q(status='converted')), q=Count('id', filter=Q(status='qualified'))).order_by('-n'):
            rows.append({'channel': lab.get(x['channel'], x['channel']), 'leads': x['n'], 'qualified': x['q'], 'converted': x['conv'],
                         'rate': round(100 * x['conv'] / x['n'], 1) if x['n'] else 0})
        return [('channel', 'Came in by', 'text'), ('leads', 'Leads', 'number'), ('qualified', 'Qualified now', 'number'), ('converted', 'Converted', 'number'),
                ('rate', 'Conversion %', 'number')], rows

    def convs(run):
        qs = _branch_pick(run, a.regs['conversation'].scope(run.request), 'branch')
        data = defaultdict(lambda: {'open': 0, 'unread': 0, 'waiting': 0, 'closed': 0, 'in': 0, 'out': 0})
        for c in qs.select_related('owner'):
            k = emp_name(c.owner) or 'Unassigned'
            data[k][c.status] += 1
            data[k]['unread'] += c.unread
        for m in ConversationMessage.objects.filter(conversation__in=qs, at__date__gte=run.date_from, at__date__lte=run.date_to).select_related('conversation__owner'):
            data[emp_name(m.conversation.owner) or 'Unassigned'][m.direction] += 1
        rows = [{'agent': k, **v} for k, v in data.items()]
        return [('agent', 'Agent', 'text'), ('open', 'Open', 'number'), ('waiting', 'Waiting for the customer', 'number'), ('unread', 'Unread messages', 'number'),
                ('closed', 'Closed', 'number'), ('in', 'Messages in (period)', 'number'), ('out', 'Replies (period)', 'number')], rows

    def connectors(run):
        rows = [{'connector': c.name, 'kind': c.get_kind_display(), 'active': 'Yes' if c.is_active else 'No', 'received': c.received, 'last': d(c.last_received_at),
                 'error': c.last_error} for c in LeadConnector.objects.all()]
        return [('connector', 'Connector', 'text'), ('kind', 'Type', 'text'), ('active', 'Active', 'text'), ('received', 'Received', 'number'), ('last', 'Last received', 'date'),
                ('error', 'Last error', 'text')], rows

    return {
        'crm-campaign-sends': ('Campaign sends', 'CRM', 'Every campaign send in the period: recipients, sent, failed, opened, clicked, unsubscribed, leads since.', sends, 'year', codes),
        'crm-lead-channels': ('Leads by channel', 'CRM', 'Leads in the period by how they came in (web, Facebook / Instagram, Google, LinkedIn, WhatsApp, phone …) with conversion.',
                              channels, 'year', codes),
        'crm-conversations': ('Conversations per agent', 'CRM', 'WhatsApp, SMS and e-mail conversations per agent: open, waiting, unread, closed, messages in and replies.', convs, 'month', codes),
        'crm-connectors': ('Lead connectors', 'CRM', 'Every lead connector with what it received and its last error.', connectors, None, codes),
    }


# ------------------------------------------------------------------ registers
def setup(app, MANAGE, VIEW, can_work, PAGE):
    CTX['app'] = app
    regs = app.regs
    MKT = ('crm_marketing',)
    ALLV = VIEW + ('crm_director',)
    SETUP_VIEW = MANAGE + VIEW + ('crm_agent', 'crm_marketing', 'crm_service', 'crm_account')

    # templates
    app.reg(Reg('message_template', MessageTemplate, 'Message template', 'Message templates', 'Setup',
                cols=('code', 'name', 'channel', 'use_for', 'subject', 'is_active', 'review_status'), form=('code', 'name', 'channel', 'use_for', 'subject', 'body', 'is_active'),
                manage=('crm_manager',) + MKT, view=SETUP_VIEW, search=('code', 'name', 'subject'), filters=('channel', 'use_for', 'is_active'), title='name', review=True,
                deletable=True, files=False, help='E-mail, WhatsApp and SMS texts. Placeholders: {name} {first_name} {company} {agent} {agent_phone} {number} {title} {value} '
                                                  '{campaign} {unsubscribe}.'))
    app.option('message-templates', lambda: MessageTemplate.objects.filter(is_active=True), lambda x: f'{x.name} ({x.get_channel_display()})', ('name', 'code'), MessageTemplate)

    # sending from records
    def a_send(channel):
        def run(o, request, f):
            contact, lead, em, ph = party_of(o)
            tpl = f.obj('template', MessageTemplate, False, 'Template')
            ctx = ctx_for(o)
            subject = render(f.s('subject') or (tpl.subject if tpl else '') or ctx.get('title') or 'Message', ctx)
            text = render(f.s('text') or (tpl.body if tpl else ''), ctx)
            if not text.strip():
                raise AppError('Write the message or choose a template.', 'text')
            if channel == 'email':
                to = f.s('to') or em
                ok, res = send_email([to], subject, text)
                addr = to
            else:
                to = f.s('to') or ph
                ok, res = send_text(to, text, channel)
                addr = norm_phone(to) or to
            if not ok:
                raise AppError(f'Not sent: {res}')
            kind = 'email' if channel == 'email' else 'message'
            log_activity(o, kind, subject if channel == 'email' else f'{channel.capitalize()} to {addr}', text, request.user, 'out', '' if channel == 'email' else addr)
            if channel != 'email' or True:
                conv = Conversation.objects.filter(channel=channel, address=addr).exclude(status='closed').order_by('-id').first()
                if conv is None:
                    conv = Conversation.objects.create(number=app.next_number('conversation', 'CNV'), channel=channel, address=addr, name=getattr(contact or lead, 'name', ''),
                                                       subject=subject[:200] if channel == 'email' else '', contact=contact, lead=lead,
                                                       opportunity=o if isinstance(o, Opportunity) else None, owner=getattr(o, 'owner', None) or app.my_emp(request),
                                                       branch_id=getattr(o, 'branch_id', None))
                ConversationMessage.objects.create(conversation=conv, direction='out', text=text[:8000], by=request.user, status='sent', result=str(res)[:200])
                conv.status, conv.last_message, conv.last_message_at = 'waiting', text[:200], now()
                conv.save(update_fields=['status', 'last_message', 'last_message_at'])
            app.event(regs_kind(o), o.pk, f'{channel.capitalize()} sent to {addr}', request.user)
            if isinstance(o, Opportunity):
                from .app import first_contact
                first_contact(o)
            return {'message': f'Sent to {addr}.'}
        return run

    def regs_kind(o):
        for k, r in regs.items():
            if r.model is type(o):
                return k
        return ''

    EMAIL_F = (('template', 'Template', 'opt:message-templates', False), ('to', 'To (empty = the contact)', 'text', False), ('subject', 'Subject', 'text', False),
               ('text', 'Message (empty = the template)', 'textarea', False))
    WA_F = (('template', 'Template', 'opt:message-templates', False), ('to', 'Phone (empty = the contact)', 'text', False), ('text', 'Message (empty = the template)', 'textarea', False))
    for k in ('lead', 'contact', 'opportunity', 'quotation', 'complaint'):
        r_ = regs.get(k)
        if r_ is None:
            continue
        ok_ = (lambda o, r: can_work(o, r) or app.any(r, 'crm_service', 'crm_account', 'crm_marketing')) if k != 'contact' else (lambda o, r: True)
        r_.actions.append(Act('send_email', 'Send e-mail', a_send('email'), ok_, fields=EMAIL_F))
        r_.actions.append(Act('send_whatsapp', 'Send WhatsApp', a_send('whatsapp'), ok_, fields=WA_F))
        r_.actions.append(Act('send_sms', 'Send SMS', a_send('sms'), ok_, fields=WA_F))
    for k in ('contact', 'lead'):
        r_ = regs[k]
        r_.form = tuple(r_.form) + tuple(n for n in ('email_opt_out', 'sms_opt_out', 'whatsapp_opt_out') if n not in r_.form)
    regs['contact'].children = tuple(regs['contact'].children) + (('Conversations', 'conversation', 'contact'),)
    regs['lead'].children = tuple(regs['lead'].children) + (('Conversations', 'conversation', 'lead'),)
    areg = regs['activity']
    areg.form = tuple(areg.form) + tuple(n for n in ('direction', 'phone', 'duration_sec', 'recording_url') if n not in areg.form)
    areg.filters = tuple(areg.filters) + ('direction',)

    # campaigns: more fields + sends
    creg = regs.get('campaign')
    if creg is not None:
        creg.form = tuple(creg.form) + tuple(n for n in ('spent', 'segment', 'target_audience', 'landing_url', 'external_ref', 'expected_leads') if n not in creg.form)
        creg.children = tuple(creg.children) + (('Sends', 'campaign_send', 'campaign'),)

    # segments
    def a_count(o, request, f):
        people = segment_people(o)
        o.size, o.counted_at = len(people), now()
        o.save(update_fields=['size', 'counted_at'])
        ch = {k: sum(1 for p in people if (p[3] if k == 'email' else p[4]) and not p[5].get(k)) for k in ('email', 'whatsapp', 'sms')}
        return {'message': f'{o.size} people – reachable: e-mail {ch["email"]}, WhatsApp {ch["whatsapp"]}, SMS {ch["sms"]}.'}

    app.option('segments', lambda: Segment.objects.filter(is_active=True), lambda x: f'{x.name} ({x.size})', ('name', 'code'), Segment)
    app.reg(Reg('segment', Segment, 'Segment', 'Segments', 'Marketing',
                cols=('code', 'name', 'target', 'size', 'counted_at', 'is_active'),
                form=('code', 'name', 'target', 'consent_only', 'contact_type', 'tags', 'source', 'territory', 'campaign', 'industry', 'city', 'lead_status', 'min_score',
                      'customers_only', 'created_from', 'is_active'),
                manage=('crm_manager',) + MKT, view=SETUP_VIEW, search=('code', 'name'), filters=('target', 'is_active'), title='name', files=False, deletable=True,
                actions=[Act('count', 'Count the people', a_count, lambda o, r: True)],
                help='Target audiences by rules. Only people who agreed to marketing (and did not opt out of the channel) are messaged.'))
    from .industry import all_industries
    regs['segment'].choices_fn = {'industry': all_industries}

    # campaign sends
    def snd_defaults(o, request):
        if o.template_id and not o.channel:
            o.channel = o.template.channel

    def snd_check(o, request, old):
        if o.template_id and o.template.channel != o.channel:
            raise AppError(f'The template is for {o.template.get_channel_display()}.', 'template')
        if not (o.body or o.template_id):
            raise AppError('Write the message or choose a template.', 'body')

    def a_schedule(o, request, f):
        o.scheduled_at = f.dtm('at', True, 'Send at')
        o.status = 'scheduled'
        o.save(update_fields=['scheduled_at', 'status'])
        prepare(o)
        app.event('campaign_send', o.pk, f'Scheduled for {o.scheduled_at:%d/%m/%Y %H:%M} – {o.total} recipients', request.user)
        return o

    def a_send_now(o, request, f):
        n = process(o, limit=500)
        o.refresh_from_db()
        left = o.recipients.filter(status='queued').count()
        return {'message': f'{n} sent now' + (f', {left} more by the marketing job (every few minutes)' if left else '') + f' – {o.failed} failed, {o.skipped} skipped.'}

    def a_test(o, request, f):
        to = f.s('to')
        tpl = o.template
        ctx = {'name': 'Test', 'first_name': 'Test', 'company': 'Test company', 'campaign': o.campaign.name, 'unsubscribe': '(unsubscribe link)'}
        text = render(o.body or (tpl.body if tpl else ''), ctx)
        ok, res = send_email([to], '[TEST] ' + render(o.subject or (tpl.subject if tpl else '') or o.campaign.name, ctx), text) if o.channel == 'email' else send_text(to, text, o.channel)
        if not ok:
            raise AppError(f'Not sent: {res}')
        return {'message': f'Test sent to {to}.'}

    app.reg(Reg('campaign_send', CampaignSend, 'Campaign send', 'Campaign sends', 'Marketing',
                cols=('number', 'campaign', 'segment', 'channel', 'scheduled_at', 'total', 'sent', 'opened', 'clicked', 'status'),
                form=('campaign', 'segment', 'channel', 'template', 'subject', 'body'), number=('campaign_send', 'SND'),
                manage=('crm_manager',) + MKT, view=ALLV + ('crm_team_lead',), search=('number', 'campaign__name', 'subject'), filters=('status', 'channel', 'campaign'),
                title='number', subtitle='campaign', defaults=snd_defaults, check=snd_check, edit_when=lambda o: o.status == 'draft',
                info=('status', 'sent_at', 'total', 'sent', 'failed', 'skipped', 'opened', 'clicked', 'unsubscribed'),
                children=(('Recipients', 'campaign_recipient', 'send'),),
                actions=[Act('test', 'Send a test', a_test, lambda o, r: o.status == 'draft', fields=(('to', 'To (e-mail / phone)', 'text', True),)),
                         Act('send', 'Send now', a_send_now, lambda o, r: o.status in ('draft', 'scheduled', 'sending') and app.any(r, 'crm_manager', 'crm_marketing'),
                             style='primary', confirm='Send this campaign to everybody in the segment who agreed to marketing?'),
                         Act('schedule', 'Schedule', a_schedule, lambda o, r: o.status == 'draft' and app.any(r, 'crm_manager', 'crm_marketing'),
                             fields=(('at', 'Send at', 'datetime', True),)),
                         Act('cancel', 'Cancel', lambda o, r, f: _cancel(o, r), lambda o, r: o.status in ('draft', 'scheduled', 'sending'), style='danger')],
                help='A campaign message by e-mail, WhatsApp or SMS to a segment – now or at a time. Opens, clicks and unsubscribes are counted (e-mail).'))

    def _cancel(o, request):
        o.status = 'cancelled'
        o.save(update_fields=['status'])
        o.recipients.filter(status='queued').update(status='skipped', error='cancelled')
        app.event('campaign_send', o.pk, 'Cancelled', request.user)
        return o

    app.reg(Reg('campaign_recipient', CampaignRecipient, 'Recipient', 'Recipients', 'Marketing',
                cols=('name', 'to', 'status', 'sent_at', 'opened_at', 'clicked_at', 'unsubscribed_at', 'error'), form=(), manage=('crm_manager',) + MKT,
                view=ALLV, create='none', search=('name', 'to'), filters=('status',), title='name', subtitle='to', child_of='send', files=False, edit_when=lambda o: False))

    # conversations
    def a_reply(o, request, f):
        tpl = f.obj('template', MessageTemplate, False, 'Template')
        text = f.s('text') or (render(tpl.body, ctx_for(o.contact or o.lead)) if tpl and (o.contact or o.lead) else (tpl.body if tpl else ''))
        if not text.strip():
            raise AppError('Write the reply.', 'text')
        reply(o, text, request.user)
        return o

    def a_conv_assign(o, request, f):
        o.owner = f.emp('owner')
        o.save(update_fields=['owner'])
        app.close_todos('conversation', o.pk)
        if o.owner and getattr(o.owner, 'users_id', None):
            app.todo(o.owner.users, f'Conversation: {o.name or o.address}', 'conversation', o.pk, o.number, PAGE + f'r/conversation/{o.pk}', o.last_message)
        app.event('conversation', o.pk, f'Assigned to {emp_name(o.owner)}', request.user)
        return o

    def a_conv_status(status_):
        def run(o, request, f):
            o.status = status_
            if status_ == 'closed':
                o.unread = 0
            o.save(update_fields=['status', 'unread'])
            app.close_todos('conversation', o.pk)
            app.event('conversation', o.pk, o.get_status_display(), request.user)
            return o
        return run

    def a_conv_read(o, request, f):
        o.unread = 0
        o.save(update_fields=['unread'])
        app.close_todos('conversation', o.pk)
        return o

    def conv_scope(qs, request):
        return qs

    app.reg(Reg('conversation', Conversation, 'Conversation', 'Conversations', 'Sales',
                cols=('number', 'channel', 'name', 'address', 'owner', 'last_message', 'last_message_at', 'unread', 'status'),
                form=('channel', 'address', 'name', 'subject', 'contact', 'lead', 'opportunity', 'owner'), number=('conversation', 'CNV'),
                manage=MANAGE + ('crm_service',), view=ALLV + MKT, create=('crm_agent', 'crm_account', 'crm_service'), own=(('owner', 'emp'),), own_edit=True,
                search=('number', 'name', 'address', 'last_message', 'subject'), filters=('channel', 'status', 'owner'), title='name', subtitle='address',
                info=('unread', 'last_message_at', 'status'), children=(('Messages', 'conversation_message', 'conversation'),),
                actions=[Act('reply', 'Reply', a_reply, lambda o, r: o.status != 'closed', style='primary',
                             fields=(('template', 'Template', 'opt:message-templates', False), ('text', 'Reply', 'textarea', False))),
                         Act('read', 'Mark read', a_conv_read, lambda o, r: o.unread > 0),
                         Act('assign', 'Assign', a_conv_assign, lambda o, r: app.any(r, *MANAGE, 'crm_service'), fields=(('owner', 'Agent', 'opt:employees', True),)),
                         Act('close', 'Close', a_conv_status('closed'), lambda o, r: o.status != 'closed'),
                         Act('reopen', 'Reopen', a_conv_status('open'), lambda o, r: o.status == 'closed')],
                help='One inbox for WhatsApp, SMS and e-mail. New messages go to the assigned agent; every message is on the customer\'s timeline.'))
    app.reg(Reg('conversation_message', ConversationMessage, 'Message', 'Messages', 'Sales',
                cols=('at', 'direction', 'text', 'status', 'by'), form=(), manage=MANAGE + ('crm_service',), view=ALLV + ('crm_agent',), create='none',
                search=('text',), filters=('direction',), title='text', child_of='conversation', files=False, edit_when=lambda o: False))

    # connectors and inboxes
    def con_saver(o, request, creating, old):
        if not o.key:
            o.key = secrets.token_urlsafe(24)
        if o.token:
            o.secret, o.secret_last4, o.token = put_secret(o.token), o.token[-4:], ''
        if o.kind in ('meta', 'whatsapp') and not o.verify_token:
            o.verify_token = secrets.token_hex(12)
        o.save()
        return o

    def a_new_key(o, request, f):
        o.key = secrets.token_urlsafe(24)
        o.save(update_fields=['key'])
        app.event('lead_connector', o.pk, 'New web-hook key – update the address at the provider', request.user)
        return o

    GUIDE = {'meta': 'Meta for Developers › your app › Webhooks › Page › leadgen: callback URL = the address above, verify token = the one above; '
                     'give a Page access token with leads_retrieval. Map the form questions to industry field codes to fill them.',
             'google': 'Google Ads › Lead form asset › Webhook integration: URL = the address above, key = the token you stored here.',
             'linkedin': 'LinkedIn lead gen forms through your integration tool (Zapier / Make / LinkedIn Lead Sync): POST the answers as JSON to the address.',
             'whatsapp': 'WhatsApp Cloud API › Webhooks › messages: callback URL = the address, verify token = the one above. Twilio: "A message comes in" = the address.',
             'telephony': 'Your phone system posts each call: {from, to, direction, status, duration, recording_url, agent}.',
             'webform': 'Your website / landing page form posts its fields (name, email, phone, company, message, industry field codes) to the address.'}
    app.reg(Reg('lead_connector', LeadConnector, 'Lead connector', 'Lead connectors', 'Setup',
                cols=('name', 'kind', 'campaign', 'source', 'owner', 'is_active', 'received', 'last_received_at', 'last_error'),
                form=('name', 'kind', 'verify_token', 'token', 'campaign', 'source', 'owner', 'industry', 'is_active'),
                manage=('crm_manager',) + MKT, view=('crm_manager', 'crm_director') + MKT, search=('name',), filters=('kind', 'is_active'), title='name', subtitle='kind',
                saver=con_saver, files=False, deletable=True, info=('received', 'last_received_at', 'last_error', 'secret_last4'),
                actions=[Act('new_key', 'Make a new web-hook key', a_new_key, lambda o, r: app.any(r, 'crm_manager'), confirm='The old address stops working. Go on?')],
                extra=lambda o, r: {'links': {'web-hook address': hook_url(o), 'verify token': o.verify_token, 'how to connect': GUIDE.get(o.kind, '')}},
                help='Facebook / Instagram lead ads, Google Ads lead forms, LinkedIn, website forms, WhatsApp inbox and telephony. Each has a secret web-hook address '
                     '(CRM setup › "Public address of the ZEO server").'))
    regs['lead_connector'].choices_fn = {'industry': all_industries}

    def ib_saver(o, request, creating, old):
        if o.password:
            o.secret, o.password = put_secret(o.password), ''
        o.save()
        return o

    def a_check(o, request, f):
        n = poll(o)
        o.refresh_from_db()
        return {'message': o.last_result or f'{n} e-mail(s) taken.'}

    app.reg(Reg('mail_inbox', MailInbox, 'E-mail inbox', 'E-mail inboxes', 'Setup',
                cols=('name', 'host', 'username', 'folder', 'create_leads', 'owner', 'is_active', 'last_checked_at', 'last_result'),
                form=('name', 'host', 'port', 'use_ssl', 'username', 'password', 'folder', 'create_leads', 'owner', 'is_active'),
                manage=('crm_manager',), view=('crm_manager', 'crm_director'), search=('name', 'username'), filters=('is_active',), title='name', subtitle='username',
                saver=ib_saver, files=False, deletable=True, info=('last_uid', 'last_checked_at', 'last_result'),
                actions=[Act('check', 'Check now', a_check, lambda o, r: o.is_active)],
                help='A mailbox (e.g. sales@) read every few minutes by "python manage.py zeocrm_marketing": e-mails become activities and conversations; '
                     'unknown senders become leads. The password is stored encrypted.'))

    # CRM home and my work
    base_dash, base_my = app.dashboard, app.my_work

    def dashboard(request):
        out = base_dash(request) if base_dash else {'cards': [], 'bars': [], 'lists': []}
        try:
            cv = regs['conversation'].scope(request)
            out['cards'] = list(out.get('cards') or []) + [
                {'label': 'Unread conversations', 'value': cv.filter(unread__gt=0).exclude(status='closed').count(), 'link': 'r/conversation?status=open',
                 'tone': 'bad' if cv.filter(unread__gt=0).exclude(status='closed').exists() else 'ok'},
                {'label': 'Campaign sends this month', 'value': CampaignSend.objects.filter(sent_at__date__gte=today().replace(day=1)).count(), 'link': 'r/campaign_send'}]
        except Exception:   # noqa: BLE001
            log.exception('release 3 dashboard')
        return out

    def my_work(request):
        out = list(base_my(request) or []) if base_my else []
        me = app.my_emp(request)
        if me is not None:
            out.append({'title': 'My conversations', 'rows': [{'kind': 'conversation', 'id': c.pk, 'title': f'{c.name or c.address}', 'sub': c.last_message,
                                                               'badge': str(c.unread) if c.unread else ''}
                                                              for c in Conversation.objects.filter(owner=me).exclude(status='closed')[:20]]})
        return out
    app.dashboard, app.my_work = dashboard, my_work
    app.settings_fields = tuple(app.settings_fields) + ('public_api_url', 'marketing_footer', 'sender_name', 'reply_to')
    return {'reports': reports, 'run_jobs': run_jobs, 'process': process, 'parse_email': parse_email, 'take_email': take_email, 'HANDLERS': HANDLERS}
