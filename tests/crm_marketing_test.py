"""v1.67.29 – CRM release 3 (communication and marketing), end-to-end checks.

    ZEO_TEST_SCHEMA=<company schema> python manage.py shell < tests/crm_marketing_test.py

The WhatsApp / SMS gateway and the IMAP server are replaced by stand-ins (no message leaves the test). Checks templates and
sending from records, lead connectors (Meta with verification, Google with its key, LinkedIn, website form), the WhatsApp inbox
(Meta Cloud and Twilio formats) with replies, telephony (answered and missed calls), the e-mail inbox, segments, campaign sends
(consent and opt-out, open / click tracking, unsubscribe, scheduling), reports and the CRM home.
"""
import json
import os
import traceback
from datetime import timedelta

from django_tenants.utils import schema_context
from rest_framework.test import APIRequestFactory, force_authenticate

SCHEMA = os.environ.get('ZEO_TEST_SCHEMA') or 'zttest'
RESULTS = []
SENT = []


def check(name, cond, detail=''):
    RESULTS.append((name, bool(cond), detail))
    print(('PASS ' if cond else 'FAIL ') + name + ('' if cond else f'  – {str(detail)[:400]}'))


RAW_KNOWN = (b'From: Omar Falcon <omar@falcon.test>\r\nTo: sales@us.test\r\nSubject: Need 2 more racks\r\nMessage-ID: <m1@falcon.test>\r\n'
             b'Content-Type: text/plain; charset=utf-8\r\n\r\nHello, we need 2 more racks next month.\r\n\r\nOn Mon, 1 Nov 2026 Sales wrote:\r\n> old text\r\n')
RAW_NEW = (b'From: "Nadia New" <nadia@newco.test>\r\nSubject: =?utf-8?q?Price_for_colocation?=\r\nMessage-ID: <m2@newco.test>\r\n'
           b'Content-Type: text/html; charset=utf-8\r\n\r\n<p>Please send a <b>price</b> for 5 racks.</p>\r\n')


class FakeIMAP:
    def __init__(self, host, port, timeout=None):
        pass

    def login(self, u, p):
        assert p == 'imap-secret', 'password not decrypted'

    def select(self, f):
        return 'OK', [b'2']

    def uid(self, cmd, *a):
        if cmd == 'search':
            return 'OK', [b'11 12']
        return 'OK', [(b'1', RAW_KNOWN if a[0] == '11' else RAW_NEW)]

    def logout(self):
        pass


def run():
    from django.contrib.auth import get_user_model
    from django.utils import timezone
    from zeo.appkit import views as V
    from ZeoCRM.app import app, MARKETING
    from ZeoCRM import marketing as MK
    from ZeoCRM.models import (Activity, Campaign, CampaignRecipient, CampaignSend, Contact, Conversation, ConversationMessage, Lead, LeadConnector, MailInbox,
                               MessageTemplate, Segment)
    from ZeoMasters.models import Account
    import Chatter.messaging as M
    from Chatter.models import MessageLog

    def fake_deliver(s, to, text):
        SENT.append((s.channel, to, text))
        return True, 'sent (stand-in)'
    M.deliver = fake_deliver
    import imaplib
    imaplib.IMAP4_SSL = FakeIMAP

    U = get_user_model()
    user = U.objects.filter(is_superuser=True).order_by('id').first()
    rf = APIRequestFactory()

    def call(view, method='get', data=None, **kw):
        q = f'/?schema={SCHEMA}'
        if method == 'get':
            req = rf.get(q + ('&' + '&'.join(f'{k}={v}' for k, v in (data or {}).items()) if data else ''))
        else:
            req = getattr(rf, method)(q, data or {}, format='json')
        force_authenticate(req, user=user)
        resp = view.as_view(app=app)(req, **kw)
        if hasattr(resp, 'render'):
            resp.render()
        return resp

    def body(r):
        try:
            return json.loads(r.content or b'{}')
        except Exception:
            return {}

    def post(kind, data):
        return call(V.RegisterListView, 'post', data, kind=kind)

    def act(kind, pk, action, data=None):
        return call(V.RegisterActionView, 'post', data or {}, kind=kind, pk=pk, action=action)

    def hook(key, data=None, method='post', query=''):
        req = rf.post(f'/?schema={SCHEMA}', data or {}, format='json') if method == 'post' else rf.get(f'/?schema={SCHEMA}{query}')
        resp = MK.HookView.as_view()(req, key=key)
        if hasattr(resp, 'render'):
            resp.render()
        return resp

    def result(r):
        return (body(r).get('result') or {}).get('message', '')

    regs = {m['kind']: m for m in body(call(V.RegistersView))}
    for k in ('message_template', 'segment', 'campaign_send', 'campaign_recipient', 'conversation', 'conversation_message', 'lead_connector', 'mail_inbox'):
        check(f'register {k}', k in regs)
    s = app.settings()
    s.public_api_url = 'https://zeo.test'
    s.save()

    # ---------------------------------------------------------------- people
    acc = Account.objects.create(number='ACC-M1', name='Falcon Hosting LLC', kind='customer')
    omar = Contact.objects.create(number='CON-M1', name='Omar Falcon', email='omar@falcon.test', phone='050 111 2233', account=acc, consent=True, contact_type='customer')
    sara = Contact.objects.create(number='CON-M2', name='Sara Opted', email='sara@x.test', phone='050 222 3344', consent=True, email_opt_out=True, contact_type='customer')
    Contact.objects.create(number='CON-M3', name='No Consent', email='nc@x.test', phone='050 333 4455', consent=False, contact_type='customer')

    # ---------------------------------------------------------------- templates and sending from records
    r = post('message_template', {'code': 'HELLO', 'name': 'Hello', 'channel': 'email', 'subject': 'Hello {first_name}', 'body': 'Dear {name},\nthank you – {agent}'})
    T = body(r)
    check('template created', r.status_code == 201, r.content[:200])
    r = act('contact', omar.pk, 'send_email', {'template': T['id']})
    check('Send e-mail from a contact', r.status_code == 200 and 'Sent to omar@falcon.test' in result(r), r.content[:300])
    check('… logged as an e-mail activity', Activity.objects.filter(contact=omar, kind='email', direction='out').exists())
    from Chatter.models import MessagingSettings
    ms = M.settings()
    ms.enabled = False
    ms.save()
    r = act('contact', omar.pk, 'send_whatsapp', {'text': 'Hi {first_name}'})
    check('WhatsApp refused while the gateway is off', r.status_code == 400 and 'not switched on' in r.content.decode(), r.content[:200])
    ms.enabled, ms.provider, ms.url, ms.channel = True, 'http', 'https://gateway.test/send', 'whatsapp'
    ms.save()
    r = act('contact', omar.pk, 'send_whatsapp', {'text': 'Hi {first_name}, your racks are ready'})
    check('WhatsApp sent through the gateway', r.status_code == 200 and SENT and SENT[-1][0] == 'whatsapp' and SENT[-1][1] == '+971501112233' and 'Hi Omar' in SENT[-1][2],
          (r.content[:200], SENT[-1:]))
    check('… conversation opened and logged', Conversation.objects.filter(channel='whatsapp', address='+971501112233', contact=omar).exists() and MessageLog.objects.filter(kind='crm').exists())
    r = act('contact', omar.pk, 'send_sms', {'text': 'SMS test'})
    check('SMS uses the SMS channel', SENT[-1][0] == 'sms', SENT[-1:])

    # ---------------------------------------------------------------- connectors
    camp = Campaign.objects.create(code='FBNOV', name='Facebook November', kind='social', budget=5000, external_ref='FORM-777', is_active=True)
    r = post('lead_connector', {'name': 'Facebook lead ads', 'kind': 'meta', 'token': 'PAGE-TOKEN-123456'})
    C = LeadConnector.objects.get(pk=body(r)['id'])
    check('Meta connector: key, verify token, token encrypted', C.key and C.verify_token and C.secret and not C.token and C.secret_last4 == '3456', (C.key, C.verify_token, C.token))
    d = body(call(V.RegisterDetailView, kind='lead_connector', pk=C.pk))
    check('web-hook address shown', 'https://zeo.test/zeo-crm/api/hooks/' in str(d.get('links')), d.get('links'))
    r = hook(C.key, method='get', query=f'&hub.mode=subscribe&hub.verify_token={C.verify_token}&hub.challenge=42xyz')
    check('Meta verification answers the challenge', r.status_code == 200 and r.content == b'42xyz', r.content)
    r = hook(C.key, method='get', query='&hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=1')
    check('wrong verify token not answered', r.content != b'1')
    payload = {'object': 'page', 'entry': [{'changes': [{'field': 'leadgen', 'value': {'leadgen_id': 'LG1', 'form_id': 'FORM-777', 'field_data': [
        {'name': 'full_name', 'values': ['Karim Ads']}, {'name': 'email', 'values': ['karim@ads.test']}, {'name': 'phone_number', 'values': ['+971551234567']},
        {'name': 'company_name', 'values': ['Ads Co']}, {'name': 'dc_racks', 'values': ['6']}, {'name': 'when_to_start', 'values': ['Next month']}]}}]}]}
    r = hook(C.key, payload)
    L = Lead.objects.filter(external_ref='meta:LG1').first()
    check('Meta lead created', r.status_code == 200 and L is not None and L.channel == 'facebook' and L.source.code == 'FACEBOOK' and L.company_name == 'Ads Co', r.content[:200])
    check('… campaign from the form ID, answers in requirements', L.campaign_id == camp.pk and 'when_to_start: Next month' in L.requirements, (L.campaign_id, L.requirements))
    hook(C.key, payload)
    check('same Meta lead not made twice', Lead.objects.filter(external_ref='meta:LG1').count() == 1)
    r = hook('nokey', payload)
    check('unknown key refused', r.status_code == 404)
    r = post('lead_connector', {'name': 'Google lead forms', 'kind': 'google', 'token': 'gkey-1', 'industry': 'data_center'})
    G = LeadConnector.objects.get(pk=body(r)['id'])
    gp = {'google_key': 'wrong', 'lead_id': 'GL9', 'campaign_id': 'C1', 'user_column_data': [{'column_id': 'FULL_NAME', 'string_value': 'Lina Google'},
                                                                                              {'column_id': 'EMAIL', 'string_value': 'lina@g.test'},
                                                                                              {'column_name': 'dc_racks', 'string_value': '3'}]}
    r = hook(G.key, gp)
    check('Google: wrong key refused', r.status_code == 403, r.content[:200])
    gp['google_key'] = 'gkey-1'
    r = hook(G.key, gp)
    GL = Lead.objects.filter(external_ref='google:GL9').first()
    check('Google lead with industry and industry field', r.status_code == 200 and GL and GL.industry == 'data_center' and GL.extra.get('dc_racks') == 3 and GL.channel == 'google',
          (r.content[:200], GL and GL.extra))
    r = post('lead_connector', {'name': 'LinkedIn', 'kind': 'linkedin'})
    LI = LeadConnector.objects.get(pk=body(r)['id'])
    r = hook(LI.key, {'leadId': 'LI5', 'firstName': 'Rami', 'lastName': 'Link', 'emailAddress': 'rami@li.test', 'companyName': 'LI Corp', 'jobTitle': 'CTO',
                      'answers': [{'question': 'Budget', 'answer': '100k'}]})
    RL = Lead.objects.filter(external_ref='linkedin:LI5').first()
    check('LinkedIn lead', r.status_code == 200 and RL and RL.name == 'Rami Link' and RL.job_title == 'CTO' and 'Budget: 100k' in RL.requirements, r.content[:200])
    r = post('lead_connector', {'name': 'Website', 'kind': 'webform'})
    W = LeadConnector.objects.get(pk=body(r)['id'])
    hook(W.key, {'name': 'Web Visitor', 'email': 'w@web.test', 'message': 'Call me'})
    hook(W.key, {'name': 'Bot', 'email': 'b@bot.test', 'website': 'spam'})
    check('website form lead (honeypot ignored)', Lead.objects.filter(email='w@web.test').exists() and not Lead.objects.filter(email='b@bot.test').exists())

    # ---------------------------------------------------------------- WhatsApp inbox
    r = post('lead_connector', {'name': 'WhatsApp', 'kind': 'whatsapp'})
    WA = LeadConnector.objects.get(pk=body(r)['id'])
    meta_msg = {'object': 'whatsapp_business_account', 'entry': [{'changes': [{'value': {'contacts': [{'wa_id': '971501112233', 'profile': {'name': 'Omar'}}],
                                                                                         'messages': [{'from': '971501112233', 'id': 'wamid.1', 'type': 'text',
                                                                                                       'text': {'body': 'Is my rack ready?'}}]}}]}]}
    r = hook(WA.key, meta_msg)
    cv = Conversation.objects.filter(channel='whatsapp', address='+971501112233').exclude(status='closed').first()
    check('incoming WhatsApp on the contact\'s conversation', r.status_code == 200 and cv and cv.contact_id == omar.pk and cv.unread == 1, (r.content[:200], cv and cv.unread))
    hook(WA.key, meta_msg)
    check('same message id not taken twice', ConversationMessage.objects.filter(conversation=cv, external_id='wamid.1').count() == 1)
    r = hook(WA.key, {'From': 'whatsapp:+971509998877', 'Body': 'Hello, price please', 'ProfileName': 'New Person', 'MessageSid': 'SM1'})
    nc = Conversation.objects.filter(address='+971509998877').first()
    check('Twilio format: unknown number → lead + conversation', nc is not None and nc.lead_id and Lead.objects.get(pk=nc.lead_id).channel == 'whatsapp', r.content[:200])
    r = act('conversation', cv.pk, 'reply', {'text': 'Yes – ready tomorrow'})
    cv.refresh_from_db()
    check('reply from the conversation', r.status_code == 200 and cv.status == 'waiting' and cv.unread == 0 and SENT[-1][2] == 'Yes – ready tomorrow', r.content[:200])
    check('messages on the timeline', Activity.objects.filter(contact=omar, kind='message', direction='in').exists())

    # ---------------------------------------------------------------- telephony
    r = post('lead_connector', {'name': 'PBX', 'kind': 'telephony'})
    PB = LeadConnector.objects.get(pk=body(r)['id'])
    hook(PB.key, {'from': '+971501112233', 'to': '+97142222222', 'direction': 'inbound', 'status': 'answered', 'duration': '125', 'recording_url': 'https://pbx.test/r/1.mp3'})
    a = Activity.objects.filter(contact=omar, kind='call', direction='in').order_by('-id').first()
    check('answered call logged with duration and recording', a and a.status == 'done' and a.duration_sec == 125 and a.recording_url.endswith('1.mp3'), a and (a.status, a.duration_sec))
    hook(PB.key, {'from': '0507776655', 'direction': 'inbound', 'status': 'missed'})
    mc = Activity.objects.filter(kind='call', phone='0507776655').first()
    check('missed call → call-back task (and a lead)', mc and mc.status == 'open' and mc.priority == 'high' and mc.lead_id, mc and (mc.status, mc.lead_id))

    # ---------------------------------------------------------------- e-mail inbox
    r = post('mail_inbox', {'name': 'Sales mailbox', 'host': 'imap.test', 'username': 'sales@us.test', 'password': 'imap-secret'})
    IB = MailInbox.objects.get(pk=body(r)['id'])
    check('inbox password encrypted', IB.secret and not IB.password)
    r = act('mail_inbox', IB.pk, 'check')
    IB.refresh_from_db()
    check('inbox read: 2 e-mails taken', '2 e-mail' in IB.last_result and IB.last_uid == 12, (IB.last_result, r.content[:200]))
    ec = Conversation.objects.filter(channel='email', address='omar@falcon.test').first()
    check('known sender → his conversation, quoted reply cut', ec and ec.contact_id == omar.pk and 'old text' not in ec.messages.first().text, ec and ec.messages.first().text)
    nl = Lead.objects.filter(email='nadia@newco.test').first()
    check('unknown sender → lead (channel e-mail), subject decoded', nl and nl.channel == 'email' and 'Price for colocation' in nl.requirements, nl and nl.requirements)

    # ---------------------------------------------------------------- segments and campaign sends
    r = post('segment', {'code': 'CUST', 'name': 'Customers', 'target': 'contacts', 'contact_type': 'customer'})
    SG = body(r)
    r = act('segment', SG['id'], 'count')
    check('segment count (consent only)', r.status_code == 200 and Segment.objects.get(pk=SG['id']).size == 2 and 'e-mail 1' in result(r), result(r))
    r = post('campaign_send', {'campaign': camp.pk, 'segment': SG['id'], 'channel': 'email', 'subject': 'News for {company}',
                               'body': 'Hello {first_name},\nsee https://us.test/offer\n{unsubscribe}'})
    SD = body(r)
    r = act('campaign_send', SD['id'], 'send')
    sd = CampaignSend.objects.get(pk=SD['id'])
    check('campaign sent: 1 sent, opted-out skipped', sd.status == 'sent' and sd.sent == 1 and sd.skipped == 1, (sd.status, sd.sent, sd.skipped, result(r)))
    rec = CampaignRecipient.objects.get(send=sd)
    req = rf.get(f'/?schema={SCHEMA}')
    MK.TrackView.as_view()(req, kind='o', token=rec.token)
    req = rf.get(f'/?schema={SCHEMA}&u=https://us.test/offer')
    resp = MK.TrackView.as_view()(req, kind='c', token=rec.token)
    sd.refresh_from_db()
    check('open and click counted, click redirects', sd.opened == 1 and sd.clicked == 1 and resp.status_code == 302 and resp['Location'] == 'https://us.test/offer',
          (sd.opened, sd.clicked, resp.status_code))
    req = rf.get(f'/?schema={SCHEMA}&u=javascript:alert(1)')
    check('click link checked', MK.TrackView.as_view()(req, kind='c', token=rec.token).status_code == 400)
    MK.TrackView.as_view()(rf.get(f'/?schema={SCHEMA}'), kind='u', token=rec.token)
    omar.refresh_from_db()
    sd.refresh_from_db()
    check('unsubscribe opts the contact out', omar.email_opt_out and sd.unsubscribed == 1)
    r = post('campaign_send', {'campaign': camp.pk, 'segment': SG['id'], 'channel': 'whatsapp', 'body': 'WhatsApp news {first_name}'})
    S2 = body(r)
    act('campaign_send', S2['id'], 'schedule', {'at': (timezone.now() - timedelta(minutes=1)).strftime('%Y-%m-%dT%H:%M')})
    out = MARKETING['run_jobs']()
    s2 = CampaignSend.objects.get(pk=S2['id'])
    check('scheduled WhatsApp send done by the job', s2.status == 'sent' and s2.sent == 2 and out['sent'] >= 2, (s2.status, s2.sent, out))

    # ---------------------------------------------------------------- reports and home
    from DashboardManagement.reports import REPORTS, ReportView
    bad = []
    for k in ('crm-campaign-sends', 'crm-lead-channels', 'crm-conversations', 'crm-connectors', 'crm-campaign-roi'):
        req = rf.get(f'/?schema={SCHEMA}&period=year')
        force_authenticate(req, user=user)
        resp = ReportView.as_view()(req, key=k)
        resp.render()
        if resp.status_code != 200:
            bad.append((k, resp.status_code, resp.content[:200]))
    check('release 3 reports run', not bad, bad)
    req = rf.get(f'/?schema={SCHEMA}')
    force_authenticate(req, user=user)
    from rest_framework.views import APIView
    dreq = APIView().initialize_request(req)
    dreq.user = user
    check('CRM home: unread conversations', any(c['label'] == 'Unread conversations' for c in app.dashboard(dreq)['cards']))


with schema_context(SCHEMA):
    try:
        run()
    except Exception:
        traceback.print_exc()
        RESULTS.append(('crashed', False, ''))
fails = [r for r in RESULTS if not r[1]]
print(f'\n{len(RESULTS) - len(fails)} / {len(RESULTS)} checks passed')
