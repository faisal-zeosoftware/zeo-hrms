"""Ask ZEO 1.72.0 (A7) – the monthly AI summary e-mail to HR and management (Settings › Ask ZEO › "Monthly AI summary e-mail").

On the 1st of each month (Celery beat `askzeo-monthly-summary`, 07:10 server time) for every company that switched it on:
* payroll changes of last month and why (the Payroll variance report: the largest changes per employee and component),
* documents that expire in the next 30 days (Documents and expiry report),
* absence and overtime outliers of last month (Attendance summary and Overtime reports: clearly above the others).
The figures are worked out by the reports, as the company's first administrator (rights and branches as for that user). With the
company's AI switched on the e-mail text is written by the AI from these facts (amounts and names stay on the server: tokens, put back
afterwards); without it the same facts are sent as a plain list. `python manage.py askzeo_summary --schema <company> [--dry-run]`.
"""
from zeo.tz import today as _zt_today   # v1.73.16 the user's / company's day, not the server's UTC day
import logging
import statistics
from datetime import date, timedelta

from django.apps import apps

log = logging.getLogger(__name__)
M = apps.get_model


def _request_for(user):
    from rest_framework.test import APIRequestFactory
    r = APIRequestFactory().get('/ask-zeo/summary/')
    r.user = user
    return r


def admin_user():
    from tenant_users.tenants.models import UserTenantPermissions
    p = UserTenantPermissions.objects.filter(is_superuser=True, profile__is_active=True).select_related('profile').order_by('profile_id').first()
    return p.profile if p else None


def facts(user, today=None):
    """{'month': 'October 2026', 'payroll': [...], 'documents': [...], 'absence': [...], 'overtime': [...]} – from the reports."""
    from . import apicall
    today = today or _zt_today()
    end = today.replace(day=1) - timedelta(days=1)
    start = end.replace(day=1)
    req = _request_for(user)
    out = {'month': start.strftime('%B %Y'), 'payroll': [], 'documents': [], 'absence': [], 'overtime': []}

    def rep(key, **q):
        try:
            return apicall.call(req, 'get', f'/dashboard/api/reports/{key}/', query=q)
        except apicall.ApiError as e:
            log.info('Ask ZEO summary: report %s: %s', key, e.detail)
            return None
    d = rep('payroll-variance', **{'from': start.isoformat(), 'to': end.isoformat()})
    if d:
        cols = {c['key']: c['label'] for c in d['columns']}
        chg = next((k for k in cols if 'change' in k and 'pct' not in k and '%' not in cols[k]), None)
        if chg:
            totals = {'gross', 'net', 'total', 'net pay', 'gross pay', 'total deductions', 'deductions'}
            rows = sorted([r for r in d['rows'] if isinstance(r.get(chg), (int, float)) and r.get(chg)
                           and str(r.get('component') or r.get('item') or '').strip().lower() not in totals], key=lambda r: -abs(r[chg]))[:8]
            for r in rows:
                out['payroll'].append({'employee': r.get('employee'), 'what': r.get('component') or r.get('item') or '', 'change': r[chg],
                                       'why': r.get('reason') or r.get('note') or ''})
    d = rep('documents')
    if d:
        out['documents'] = [{'employee': r.get('employee'), 'document': r.get('document_type') or r.get('type'), 'expiry': str(r.get('expiry') or ''),
                             'days_left': r.get('days_left')} for r in d['rows'] if r.get('status') in ('Expiring in 30 days', 'Expired')][:20]
    d = rep('attendance-summary', **{'from': start.isoformat(), 'to': end.isoformat()})
    if d and d['rows']:
        vals = [r.get('absent') or 0 for r in d['rows'] if isinstance(r.get('absent'), (int, float))]
        if vals:
            cut = statistics.mean(vals) + 2 * (statistics.pstdev(vals) or 0)
            out['absence'] = [{'employee': r.get('employee'), 'absent_days': r.get('absent')} for r in d['rows']
                              if isinstance(r.get('absent'), (int, float)) and r['absent'] > max(cut, 2)][:10]
    d = rep('overtime', **{'from': start.isoformat(), 'to': end.isoformat()})
    if d and d['rows']:
        per = {}
        for r in d['rows']:
            h = r.get('hours') or r.get('ot_hours') or 0
            if isinstance(h, (int, float)):
                per[r.get('employee')] = per.get(r.get('employee'), 0) + h
        if per:
            vals = list(per.values())
            cut = statistics.mean(vals) + 2 * (statistics.pstdev(vals) or 0)
            out['overtime'] = [{'employee': k, 'hours': round(v, 1)} for k, v in sorted(per.items(), key=lambda x: -x[1]) if v > max(cut, 10)][:10]
    return out


def plain_text(f):
    from Translations.i18n import tr
    lines = [tr('ZEO monthly summary – {0}', f['month']), '']
    lines.append(tr('Payroll changes (largest first):'))
    lines += [f"  - {x['employee']}: {x['what']} {x['change']:+,.2f}" + (f" – {x['why']}" if x['why'] else '') for x in f['payroll']] or ['  - ' + tr('none')]
    lines += ['', tr('Documents expiring in the next 30 days:')]
    lines += [f"  - {x['employee']}: {x['document']} {x['expiry']}" for x in f['documents']] or ['  - ' + tr('none')]
    lines += ['', tr('Absence clearly above the others:')]
    lines += [f"  - {x['employee']}: {x['absent_days']} " + tr('days') for x in f['absence']] or ['  - ' + tr('none')]
    lines += ['', tr('Overtime clearly above the others:')]
    lines += [f"  - {x['employee']}: {x['hours']} " + tr('hours') for x in f['overtime']] or ['  - ' + tr('none')]
    return '\n'.join(lines)


def build(user):
    """(subject, text, mode) of the summary."""
    from Translations.i18n import tr
    f = facts(user)
    subject = tr('ZEO monthly summary – {0}', f['month'])
    text = plain_text(f)
    mode = 'builtin'
    try:
        from . import ai
        req = _request_for(user)
        if ai.ready(req):
            from .aiwork import _shield
            from .scope import ctx
            import json
            req.META.setdefault('HTTP_HOST', 'localhost')
            c = ctx(req)
            sh = _shield(c)
            meter = ai.Meter('summary')
            r = ai.call(req, meter, 'Write a short monthly e-mail to HR and management from these facts (payroll changes and why, expiring documents, '
                                    'absence and overtime outliers). Plain text, bullet points, no invented facts.',
                        [{'role': 'user', 'content': json.dumps(sh.protect(f), default=str, ensure_ascii=False)}], max_tokens=1200)
            text = sh.restore(ai.text_of(r)) or text
            meter.save(user)
            mode = 'ai'
    except Exception:   # the e-mail always goes out – with the plain list when the AI cannot be used
        log.info('Ask ZEO summary: AI not used', exc_info=True)
    return subject, text, mode


def recipients(s, user):
    to = [x.strip() for x in (s.summary_to or '').split(',') if x.strip()]
    if to:
        return to
    from tenant_users.tenants.models import UserTenantPermissions
    return [p.profile.email for p in UserTenantPermissions.objects.filter(is_superuser=True, profile__is_active=True).select_related('profile') if p.profile.email]


def send_for_company(dry_run=False):
    """For the current company (schema already set). Returns a dict of what was (or would be) sent."""
    from django.core.mail import send_mail
    from .models import AskSettings
    s = AskSettings.get()
    if not s.summary_email and not dry_run:
        return {'sent': False, 'why': 'switched off'}
    user = admin_user()
    if user is None:
        return {'sent': False, 'why': 'no administrator'}
    subject, text, mode = build(user)
    to = recipients(s, user)
    if not dry_run and to:
        send_mail(subject, text, None, to, fail_silently=False)
    return {'sent': bool(to) and not dry_run, 'to': to, 'subject': subject, 'mode': mode, 'text': text}
