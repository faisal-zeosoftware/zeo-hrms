"""v1.67.26 (pending item 1.62.12, item 237) – Microsoft Teams and Slack: company events posted to a channel.

Settings › Integrations › Teams and Slack (company administrators): the incoming-webhook address of a Slack channel
(https://hooks.slack.com/services/…) and / or of a Teams channel (Workflows "Post to a channel when a webhook request is received",
or the older Office 365 connector, https://….webhook.office.com/…). Nothing is sent until an address is saved and an event ticked.
The addresses are kept encrypted (as the WhatsApp / SMS keys). Only https addresses of Slack and Microsoft are accepted
(ZEO_WEBHOOK_ALLOW_HOSTS in .env adds other hosts, e.g. a proxy).

Events (each can be ticked):
  leave_approved    "<name> is on <leave type> from … to …" – who is out
  new_joiner        a new employee is added
  announcement      a company announcement is published
  payroll_approved  a payroll run is approved (no amounts)
  helpdesk_new      a new question to the HR helpdesk (number and category – no text, it may be personal)
v1.67.28 (optional by-branch connections): a branch can have its own Slack / Teams channel. Its events (leave, new joiner, helpdesk
question, a payroll run or announcement of that branch) go to the branch channel instead of the company channel; a branch without
its own channel uses the company channel, as before.
Messages never hold salaries, documents or personal details beyond the name. Sent after the transaction commits, in a thread;
a failure is logged (Settings › Integrations shows the last result) and never stops the business transaction.
"""
import json
import logging
import os
import threading
from urllib.parse import urlparse

import requests
from django.db import connection, transaction
from django.utils import timezone

log = logging.getLogger(__name__)
KEY = 'chat_webhooks'
EVENTS = (('leave_approved', 'Leave approved – who is out'), ('new_joiner', 'New joiner'), ('announcement', 'Announcement published'),
          ('payroll_approved', 'Payroll run approved'), ('helpdesk_new', 'New HR helpdesk question'))
HOSTS = {'slack': ('hooks.slack.com',), 'teams': ('webhook.office.com', 'logic.azure.com', 'powerautomate.com', 'powerplatform.com')}


def _tr(s, *a):
    try:
        from Translations.i18n import tr
        return tr(s, *a)
    except Exception:   # noqa: BLE001
        return s.format(*a)


def _opt():
    from django.apps import apps
    CO = apps.get_model('ZeoApps', 'CompanyOption')
    o = CO.objects.filter(key=KEY).first()
    return CO, dict(o.value or {}) if o else {}


def settings_view():
    _co, v = _opt()
    from .messaging import get_secret
    def mask(u):   # the host and the last 4 characters – the secret part of the address is never shown again
        u = get_secret(u)
        return (f'{urlparse(u).scheme}://{urlparse(u).hostname}/…{u[-4:]}') if u else ''
    from django.apps import apps
    names = dict(apps.get_model('OrganisationManager', 'brnch_mstr').objects.values_list('pk', 'branch_name'))
    per = [{'branch': int(b), 'name': names.get(int(b), f'#{b}'), 'slack_set': bool(x.get('slack')), 'teams_set': bool(x.get('teams')),
            'slack': mask(x.get('slack')), 'teams': mask(x.get('teams'))}
           for b, x in sorted((v.get('branches') or {}).items(), key=lambda kv: names.get(int(kv[0]), '')) if str(b).isdigit()]
    return {'slack_set': bool(v.get('slack')), 'teams_set': bool(v.get('teams')), 'slack': mask(v.get('slack')), 'teams': mask(v.get('teams')),
            'events': v.get('events') or [], 'all_events': [{'key': k, 'label': _tr(l)} for k, l in EVENTS], 'last': v.get('last') or {},
            'branches': per, 'all_branches': [{'id': k, 'name': n} for k, n in sorted(names.items(), key=lambda kv: kv[1])]}   # v1.67.28


def check_url(kind, url):
    extra = tuple(h.strip() for h in os.environ.get('ZEO_WEBHOOK_ALLOW_HOSTS', '').split(',') if h.strip())
    u = urlparse(url or '')
    host = (u.hostname or '').lower()
    ok_host = any(host == h or host.endswith('.' + h) for h in HOSTS[kind]) or host in extra
    if host in extra and u.scheme in ('http', 'https'):
        return url
    if u.scheme != 'https' or not ok_host:
        raise ValueError(_tr('Enter the incoming-webhook address of a {0} channel (https://{1}/…).', 'Slack' if kind == 'slack' else 'Microsoft Teams',
                             HOSTS[kind][0]))
    return url


def save(data):
    from .messaging import put_secret
    CO, v = _opt()
    if data.get('branch') not in (None, ''):   # v1.67.28: the channels of one branch (both empty = the branch uses the company channel)
        from django.apps import apps
        try:
            bid = int(data.get('branch'))
        except (TypeError, ValueError):
            raise ValueError(_tr('Choose the branch.'))
        if not apps.get_model('OrganisationManager', 'brnch_mstr').objects.filter(pk=bid).exists():
            raise ValueError(_tr('Choose the branch.'))
        per = dict(v.get('branches') or {})
        x = dict(per.get(str(bid)) or {})
        for kind in ('slack', 'teams'):
            if kind in data:
                url = (data.get(kind) or '').strip()
                x[kind] = put_secret(check_url(kind, url)) if url else ''
        if data.get('remove') or not (x.get('slack') or x.get('teams')):
            per.pop(str(bid), None)
        else:
            per[str(bid)] = x
        v['branches'] = per
        CO.objects.update_or_create(key=KEY, defaults={'value': v})
        return settings_view()
    for kind in ('slack', 'teams'):
        if kind in data:
            url = (data.get(kind) or '').strip()
            v[kind] = put_secret(check_url(kind, url)) if url else ''
    if 'events' in data:
        ev = [e for e in (data.get('events') or []) if e in dict(EVENTS)]
        v['events'] = ev
    CO.objects.update_or_create(key=KEY, defaults={'value': v})
    return settings_view()


def _payload(kind, title, text):
    if kind == 'slack':
        return {'text': f'*{title}*\n{text}'}
    # Teams: an Adaptive Card (Workflows webhooks); the old connectors read the same card in "attachments"
    return {'type': 'message', 'attachments': [{'contentType': 'application/vnd.microsoft.card.adaptive', 'contentUrl': None, 'content': {
        '$schema': 'http://adaptivecards.io/schemas/adaptive-card.json', 'type': 'AdaptiveCard', 'version': '1.4',
        'body': [{'type': 'TextBlock', 'text': title, 'weight': 'Bolder', 'wrap': True}, {'type': 'TextBlock', 'text': text, 'wrap': True}]}}]}


def _send(schema, targets, title, text, event):
    out = {}
    for kind, url in targets:
        try:
            r = requests.post(url, data=json.dumps(_payload(kind, title, text)), headers={'Content-Type': 'application/json'}, timeout=15)
            out[kind] = {'ok': 200 <= r.status_code < 300, 'status': r.status_code, 'at': timezone.now().isoformat(), 'event': event}
        except Exception as e:   # noqa: BLE001
            out[kind] = {'ok': False, 'status': str(e)[:120], 'at': timezone.now().isoformat(), 'event': event}
            log.warning('chat webhook %s failed: %s', kind, e)
    try:   # remember the last result (shown on the settings page)
        from django_tenants.utils import schema_context
        with schema_context(schema):
            CO, v = _opt()
            v['last'] = {**(v.get('last') or {}), **out}
            CO.objects.update_or_create(key=KEY, defaults={'value': v})
    except Exception:   # noqa: BLE001
        log.debug('last webhook result not kept', exc_info=True)
    return out


def post(event, title, text, now=False, branch=None):
    """Post a company event to the channels that want it. Never raises. v1.67.28: an event of a branch with its own channel(s)
    goes there instead of the company channel."""
    try:
        _co, v = _opt()
        if event != 'test' and event not in (v.get('events') or []):
            return None
        from .messaging import get_secret
        src = v
        own = (v.get('branches') or {}).get(str(branch)) if branch is not None else None
        if own and (own.get('slack') or own.get('teams')):
            src = own
        targets = [(k, get_secret(src.get(k))) for k in ('slack', 'teams') if src.get(k)]
        targets = [(k, u) for k, u in targets if u]
        if not targets:
            return None
        schema = connection.schema_name
        if now:
            return _send(schema, targets, title, text, event)
        transaction.on_commit(lambda: threading.Thread(target=_send, args=(schema, targets, title, text, event), daemon=True).start())
        return True
    except Exception:   # noqa: BLE001
        log.exception('chat webhook skipped')
        return None


def test(branch=None):
    from SelfService.letters import company_name
    out = post('test', _tr('ZEO Books – test message'), _tr('{0}: this channel will get the events ticked in Settings › Integrations › Teams and Slack.', company_name()), now=True,
               branch=branch)
    if not out:
        raise ValueError(_tr('Save a Slack or Teams address first.'))
    return out


# ------------------------------------------------------------------ the events (signals, connected in Chatter.apps.ready)
def _name(e):
    return ' '.join(x for x in (getattr(e, 'emp_first_name', ''), getattr(e, 'emp_last_name', '')) if x)


def _one_branch(obj, field):
    """v1.67.28: the branch of a record that may cover several branches (payroll run, announcement) – only when it is exactly one"""
    try:
        ids = list(getattr(obj, field).values_list('pk', flat=True)[:2])
        return ids[0] if len(ids) == 1 else None
    except Exception:   # noqa: BLE001
        return None


def _pre_leave(sender, instance, **kw):
    if instance.pk:
        instance._zeo_old_status = sender.objects.filter(pk=instance.pk).values_list('status', flat=True).first()


def _post_leave(sender, instance, created, **kw):
    if instance.status == 'approved' and getattr(instance, '_zeo_old_status', None) != 'approved':
        lt = instance.leave_type.name if instance.leave_type_id else 'leave'
        post('leave_approved', _tr('Out of office'), _tr('{0} is on {1} from {2} to {3}.', _name(instance.employee), lt,
                                                       instance.start_date.strftime('%d/%m/%Y'), instance.end_date.strftime('%d/%m/%Y')),
             branch=getattr(instance, 'branch_id', None) or getattr(instance.employee, 'emp_branch_id_id', None))


def _post_emp(sender, instance, created, **kw):
    if created and getattr(instance, 'is_active', True):
        des = getattr(getattr(instance, 'emp_desgntn_id', None), 'desgntn_job_title', '') if getattr(instance, 'emp_desgntn_id_id', None) else ''
        post('new_joiner', _tr('Welcome to the team'), _tr('{0} joins us{1}{2}.', _name(instance), (' as ' + des) if des else '',
                                                         (' on ' + instance.emp_joined_date.strftime('%d/%m/%Y')) if instance.emp_joined_date else ''),
             branch=getattr(instance, 'emp_branch_id_id', None))


def _pre_run(sender, instance, **kw):
    if instance.pk:
        instance._zeo_old_status = sender.objects.filter(pk=instance.pk).values_list('status', flat=True).first()


def _post_run(sender, instance, created, **kw):
    if str(instance.status).lower() == 'approved' and str(getattr(instance, '_zeo_old_status', '') or '').lower() != 'approved':
        post('payroll_approved', _tr('Payroll approved'), _tr('The payroll "{0}" ({1:02d}/{2}) is approved.', instance.name, int(instance.month or 0), instance.year),
             branch=_one_branch(instance, 'branch'))


def _post_ann(sender, instance, created, **kw):
    if created:
        post('announcement', _tr('Announcement'), str(getattr(instance, 'title', '') or getattr(instance, 'subject', '') or ''),
             branch=_one_branch(instance, 'branches'))


def _post_ticket(sender, instance, created, **kw):
    if created:
        post('helpdesk_new', _tr('New HR helpdesk question'), _tr('{0} – {1}.', instance.number, instance.category.name if instance.category_id else ''),
             branch=getattr(instance, 'branch_id', None) or getattr(instance.employee, 'emp_branch_id_id', None))


def connect():
    from django.apps import apps
    from django.db.models.signals import post_save, pre_save
    pairs = [('calendars', 'employee_leave_request', _pre_leave, _post_leave), ('EmpManagement', 'emp_master', None, _post_emp),
             ('PayrollManagement', 'PayrollRun', _pre_run, _post_run), ('OrganisationManager', 'Announcement', None, _post_ann),
             ('ZeoHR', 'HRTicket', None, _post_ticket)]
    for app, model, pre, post_ in pairs:
        try:
            m = apps.get_model(app, model)
        except LookupError:
            continue
        if pre:
            pre_save.connect(pre, sender=m, dispatch_uid=f'zeo-chathook-pre-{model}')
        post_save.connect(post_, sender=m, dispatch_uid=f'zeo-chathook-post-{model}')
