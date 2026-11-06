"""v1.67.5 (bugs) – P1: the payslip is e-mailed to the employee when it is approved.

Before: approving a payslip (Payslip approvals, one or many) only changed its status. The only e-mail was the PDF upload's
"Also e-mail it to the employee" (and a Celery task that was never scheduled), and that one needed an active e-mail
configuration without using its server, and an uploaded PDF.

Now:
* Payroll › Setup › Payroll controls › "E-mail the payslip to the employee when it is approved" (on unless switched off;
  ZeoApps.CompanyOption 'payslip_email' {on_approval} – no migration). A payslip whose PDF was saved with "Also e-mail it"
  (payslip.send_email) is e-mailed on approval even when the setting is off.
* The e-mail is sent once the last approval step is done (the payslip becomes Approved), with the company's mail connection
  (ZeoAdmin.schedules.mail_connection), the payslip design PDF (or the standard one) and the payslip password rule.
* The approver is told the result: n sent, n skipped (no e-mail address), n failed with the reason. Every result is also
  kept in the payslip's log (Chatter note) and the server log – nothing is swallowed.
"""
import logging

from django.apps import apps

log = logging.getLogger(__name__)
KEY = 'payslip_email'


def settings():
    try:
        o = apps.get_model('ZeoApps', 'CompanyOption').objects.filter(key=KEY).first()
        v = dict(o.value or {}) if o else {}
    except Exception:   # noqa: BLE001
        v = {}
    return {'on_approval': bool(v.get('on_approval', True))}


def save(data):
    cur = settings()
    if 'payslip_email_on_approval' in data:
        cur['on_approval'] = str(data['payslip_email_on_approval']).lower() in ('1', 'true', 'yes', 'on')
    apps.get_model('ZeoApps', 'CompanyOption').objects.update_or_create(key=KEY, defaults={'value': cur})
    return cur


def public():
    return {'payslip_email_on_approval': settings()['on_approval']}


def _note(payslip, text, user=None):
    try:
        from Chatter.models import Message
        from Chatter.tracking import model_key
        Message.objects.create(model=model_key(type(payslip)), object_id=str(payslip.pk), body=text[:1000], author=user, kind='note')
    except Exception:   # noqa: BLE001
        log.exception('payslip log note')


def released(status):
    return str(status or '').lower() in ('approved', 'paid')


def after_approval(payslips, user=None):
    """E-mail the payslips that are now Approved. Returns {'sent', 'skipped', 'failed', 'details': [...], 'off': bool}."""
    from PayrollManagement.utils import send_payslip_email_detail
    out = {'sent': 0, 'skipped': 0, 'failed': 0, 'details': [], 'off': False}
    on = settings()['on_approval']
    todo = [p for p in payslips if released(p.status) and (on or getattr(p, 'send_email', False))]
    if not todo:
        out['off'] = not on and bool(payslips)
        return out
    conn = sender = None
    try:
        from ZeoAdmin.schedules import mail_connection
        conn, sender = mail_connection()
    except Exception as e:   # noqa: BLE001 – no connection: every payslip fails with the reason
        log.warning('payslip e-mail connection: %s', e)
    for p in todo:
        if conn is None:
            state, text = 'failed', f'{p.employee.emp_code}: the mail connection could not be made'
        else:
            state, text = send_payslip_email_detail(p, connection=conn, sender=sender)
        out[state] += 1
        out['details'].append({'payslip_id': p.pk, 'state': state, 'text': text})
        _note(p, {'sent': 'Payslip e-mailed to the employee', 'skipped': 'Payslip not e-mailed', 'failed': 'Payslip e-mail failed'}[state]
              + f' ({text})', user)
    try:
        if conn is not None:
            conn.close()
    except Exception:   # noqa: BLE001
        pass
    return out


def summary(res):
    """One line for the approver, e.g. "Payslip e-mails: 2 sent, 1 skipped (no e-mail address: E1), 1 failed: E3: …"."""
    from Translations.i18n import tr
    if not res or not (res['sent'] or res['skipped'] or res['failed']):
        return ''
    parts = []
    if res['sent']:
        parts.append(tr('{0} sent', res['sent']))
    sk = [d['text'] for d in res['details'] if d['state'] == 'skipped']
    if sk:
        parts.append(tr('{0} skipped', len(sk)) + ' (' + '; '.join(sk[:5]) + ('…' if len(sk) > 5 else '') + ')')
    fl = [d['text'] for d in res['details'] if d['state'] == 'failed']
    if fl:
        parts.append(tr('{0} failed', len(fl)) + ': ' + '; '.join(fl[:3]) + ('…' if len(fl) > 3 else ''))
    return tr('Payslip e-mails:') + ' ' + ', '.join(parts) + '.'
