"""v1.62.11 (item 230) – bulk final settlement: calculate, review and pay the final settlements of many leavers at once.

    GET  api/leavers/bulk/?from=&to=&branch=&stage=   leavers whose last working day is in the period, each with its statement lines
    POST api/leavers/bulk/calculate/  {ids}           calculate (or recalculate a draft) – leavers.calculate → services.start / compute
    POST api/leavers/bulk/<step>/     {ids, …}        submit | validate | approve | pay  → services.act for each one

Nothing of the single-settlement logic is copied: every leaver goes through the same services.compute (salary, notice, leave,
gratuity, v1.62.7 accruals …) and services.act (segregation of duties, the asset clearance block, _close_eos → v1.62.3
close_recovered / make_leaver_inactive, v1.62.7 exit_paid). Each leaver runs in its own savepoint, so one refusal (e.g. assets
not returned, the approver paying) leaves the others done and is reported on its row.
"""
import logging
from datetime import date

from django.apps import apps
from django.db import transaction

from Translations.i18n import tr

from . import services as S

log = logging.getLogger(__name__)
M = apps.get_model
STEPS = ('submit', 'validate', 'approve', 'pay')
MAX_IDS = 200


def _day(v, field):
    if v in (None, ''):
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        raise S.Refused(tr('Enter a valid date.'), problems={field: tr('Enter a valid date.')})


def _short_line(ln):
    return {'key': ln.get('key'), 'label': ln.get('label'), 'kind': ln.get('kind'), 'amount': ln.get('amount'), 'basis': ln.get('basis') or '',
            'section': ln.get('section') or ''}


def rows(c, user, d_from=None, d_to=None, branch='', stage=''):
    """The leavers with the last working day in [from, to] (both optional) and their statements."""
    from . import leavers as L
    qs = L.leaver_queryset(c)
    if d_from:
        qs = qs.filter(last_working_date__gte=d_from)
    if d_to:
        qs = qs.filter(last_working_date__lte=d_to)
    if branch:
        qs = qs.filter(employee__emp_branch_id=branch)
    res = list(qs.order_by('last_working_date', 'employee__emp_code', 'id')[:1000])
    eos, sts = L._maps([r.pk for r in res])
    out = []
    for r in res:
        ev = eos.get(r.pk)
        st = sts.get(ev.pk) if ev else None
        x = L.row(r, ev, st)
        if stage and x['stage'] not in stage.split(','):
            continue
        x['rights'] = L.rights(st, c, user)
        if st is not None:
            lines = S.localize_lines(st.lines or [])
            x.update({'lines': [_short_line(l) for l in lines], 'total_earnings': float(st.total_earnings), 'total_deductions': float(st.total_deductions),
                      'calc_date': st.calc_date, 'payment_ref': st.payment_ref, 'gratuity': float(sum(l.get('amount') or 0 for l in lines if l.get('key') == 'gratuity')),
                      'block': '; '.join(l.get('label') or '' for l in lines if l.get('kind') == 'block')})
        else:
            x.update({'lines': [], 'total_earnings': None, 'total_deductions': None, 'calc_date': None, 'payment_ref': '', 'gratuity': None, 'block': ''})
        out.append(x)
    return out


def summary(items):
    counts, net = {}, 0.0
    for x in items:
        counts[x['stage']] = counts.get(x['stage'], 0) + 1
        if x.get('net') is not None and x['stage'] != 'cancelled':
            net += x['net']
    return {'count': len(items), 'counts': counts, 'net': round(net, 2)}


def _ids(data):
    ids = data.get('ids')
    if not isinstance(ids, (list, tuple)) or not ids:
        raise S.Refused(tr('Select at least one leaver.'), problems={'ids': tr('Select at least one leaver.')})
    try:
        ids = list(dict.fromkeys(int(i) for i in ids))
    except (TypeError, ValueError):
        raise S.Refused(tr('Select at least one leaver.'), problems={'ids': tr('Select at least one leaver.')})
    if len(ids) > MAX_IDS:
        raise S.Refused(tr('Select at most {0} leavers at a time.', MAX_IDS))
    return ids


def _leavers(c, ids):
    """{resignation id: resignation} of the ids the user may see (approved exits in the user's branches)."""
    from . import leavers as L
    return {r.pk: r for r in L.leaver_queryset(c).filter(pk__in=ids)}


def _one(fn):
    """Runs fn() in its own savepoint; (True, None) or (False, message)."""
    try:
        with transaction.atomic():
            fn()
        return True, None
    except S.Refused as e:
        return False, str(e.message)
    except Exception as e:   # the reason of an unexpected error is logged; the row says it could not be done
        log.exception('bulk final settlement')
        return False, tr('It could not be done: {0}', str(e)[:200])


def _result(res, ok, msg, extra=None):
    from . import leavers as L
    eos, sts = L._maps([res.pk])
    ev = eos.get(res.pk)
    st = sts.get(ev.pk) if ev else None
    x = {'id': res.pk, 'employee': L.emp_name(res.employee), 'employee_code': res.employee.emp_code, 'ok': ok, 'error': msg or '',
         'stage': L.stage_of(st), 'stage_label': tr(L.STAGE_LABEL.get(L.stage_of(st), L.stage_of(st))), 'net': float(st.net) if st is not None else None,
         'number': st.number if st is not None else ''}
    if extra:
        x.update(extra)
    return x


def calculate_all(c, user, data):
    """Calculate every selected leaver that has no settlement yet or a draft one; others are left as they are (reported)."""
    from . import leavers as L
    if not (c.admin or {'add_exitsettlement', 'change_exitsettlement'} & c.codes):
        raise S.Refused(tr('You may not start a final settlement.'), status=403)
    ids = _ids(data)
    found = _leavers(c, ids)
    eos, sts = L._maps(list(found))
    out = []
    for i in ids:
        res = found.get(i)
        if res is None:
            out.append({'id': i, 'ok': False, 'error': tr('Leaver not found.'), 'stage': '', 'stage_label': '', 'net': None, 'employee': '', 'employee_code': '', 'number': ''})
            continue
        ev = eos.get(res.pk)
        st = sts.get(ev.pk) if ev else None
        if st is not None and st.status != 'draft':
            out.append(_result(res, False, tr('Already {0} – only a draft is calculated again.', st.get_status_display().lower())))
            continue
        ok, msg = _one(lambda res=res: L.calculate(res, user))
        out.append(_result(res, ok, msg))
    return out


def step_all(c, user, step, data):
    """submit / validate / approve / pay every selected settlement through services.act (its own checks for each one)."""
    from . import leavers as L
    if step not in STEPS:
        raise S.Refused(tr('Unknown action.'), status=405)
    ids = _ids(data)
    body = {'note': (data.get('note') or '').strip()}
    if step == 'pay':
        ref = (data.get('payment_ref') or '').strip()
        refs = data.get('refs') if isinstance(data.get('refs'), dict) else {}
        if not ref and not any(str(v or '').strip() for v in refs.values()):
            raise S.Refused(tr('Enter the payment reference (bank transfer / WPS).'), problems={'payment_ref': tr('Required.')})
        paid_on = _day(data.get('paid_on'), 'paid_on')
        body['paid_on'] = paid_on.isoformat() if paid_on else None
    found = _leavers(c, ids)
    eos, sts = L._maps(list(found))
    out = []
    for i in ids:
        res = found.get(i)
        ev = eos.get(res.pk) if res is not None else None
        st = sts.get(ev.pk) if ev else None
        if res is None or st is None:
            r = _result(res, False, tr('Calculate the settlement first.')) if res is not None else \
                {'id': i, 'ok': False, 'error': tr('Leaver not found.'), 'stage': '', 'stage_label': '', 'net': None, 'employee': '', 'employee_code': '', 'number': ''}
            out.append(r)
            continue
        b = dict(body)
        if step == 'pay':
            b['payment_ref'] = (str(refs.get(str(i)) or refs.get(i) or '').strip() or ref)
        ok, msg = _one(lambda st=st, b=b: S.act(st, step, user, c.codes, c.admin, b))
        extra = None
        if ok and step == 'pay':
            st.refresh_from_db()
            extra = {'paid_on': st.paid_on, 'payment_ref': st.payment_ref, 'closed': S.closed_note(getattr(st, '_v1623_closed', None) or {})}
        out.append(_result(res, ok, msg, extra))
    return out


# ------------------------------------------------------------------ API
from rest_framework.response import Response   # noqa: E402

from .views import Base, READ   # noqa: E402


class BulkLeaversView(Base):
    """v1.62.11 (item 230) – GET api/leavers/bulk/   POST api/leavers/bulk/<calculate|submit|validate|approve|pay>/"""
    from EmpManagement.models import EmployeeResignation as zeo_model

    def get(self, request, action=None):
        if not self.can(READ):
            raise S.Refused(tr('You may not see final settlements.'), status=403)
        from . import leavers as L
        d_from, d_to = _day(request.GET.get('from'), 'from'), _day(request.GET.get('to'), 'to')
        if d_from and d_to and d_to < d_from:
            raise S.Refused(tr('The end of the period is before its start.'), problems={'to': tr('The end of the period is before its start.')})
        items = rows(self.c, request.user, d_from, d_to, request.GET.get('branch', ''), request.GET.get('stage', ''))
        Br = M('OrganisationManager', 'brnch_mstr')
        brs = Br.objects.order_by('branch_name')
        if self.c.branches is not None:
            brs = brs.filter(pk__in=self.c.branches)
        codes, admin = self.c.codes, self.c.admin
        return Response({'rows': items, 'summary': summary(items), 'stages': [{'key': k, 'label': tr(v)} for k, v in L.STAGES],
                         'branches': [{'id': b.pk, 'name': b.branch_name} for b in brs], 'due_days': L.due_days(),
                         'can': {'calculate': admin or bool({'add_exitsettlement', 'change_exitsettlement'} & codes),
                                 'submit': admin or bool({'add_exitsettlement', 'change_exitsettlement'} & codes),
                                 'validate': admin or 'validate_exitsettlement' in codes, 'approve': admin or 'approve_exitsettlement' in codes,
                                 'pay': admin or 'pay_exitsettlement' in codes}})

    def post(self, request, action=None):
        if action == 'calculate':
            done = calculate_all(self.c, request.user, request.data)
        elif action in STEPS:
            done = step_all(self.c, request.user, action, request.data)
        else:
            raise S.Refused(tr('Unknown action.'), status=405)
        ok = sum(1 for x in done if x['ok'])
        return Response({'results': done, 'done': ok, 'failed': len(done) - ok})
