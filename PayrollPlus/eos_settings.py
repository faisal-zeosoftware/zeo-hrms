"""v1.62.11 (EOS settings) – end-of-service rules of the company and HR's entries on a draft final settlement.

The gaps against the Odoo app adept_end_of_service (functional document 19.0.1.5.0), all OFF by default – a company that does not change
them keeps today's figures:

  Company settings (ZeoApps.CompanyOption 'eos_settings', no new table) – Payroll › Setup › End of service settings
    gratuity_daily      basic_30 (basic ÷ 30, default) | basic_365 (basic × 12 ÷ 365)
    service_days        both (last day − joining date + 1, default) | difference (last day − joining date)
    leave_base          basic (default) | gross (basic + the fixed allowances of the wage)
    leave_daily         d30 (÷ 30, default) | contract (÷ the contract leave days) | d365 (× 12 ÷ 365); the amount uses the unrounded rate
    contract_leave_days the company's contract leave days (30); per employee: the yearly days of the annual leave policy; the draft can override
    cycle_start_day     1 (calendar month, default) … 28 (25 = the 25th to the 24th)
    salary_daily        d30 (a part month ÷ 30, default) | cycle (÷ the days of that month / cycle)
    salary_exclude      salary components left out of the final salary (codes; e.g. WIFI allowance)
    unpaid_leave_types  leave types counted as unpaid for the service (empty = the types of type 'unpaid', as before)
    air_ticket          policy (default) | hr (the amount HR enters is paid; the policy amount is shown, not paid)
    show_full_emirates_id  false (masked, default) | true on the printed statement
    journal_code        journal of the settlement journals ('' = the journal of Accounting › Settings)

  HR's entries on the draft (ExitSettlement.inputs, migration 0017): gratuity adjustment (+/−, reason), leave balance override (days,
  reason), unpaid leave days override (days, reason), contract leave days, unpaid salary from, air ticket entered by HR (amount, sector,
  class, travellers); the accounting date (ExitSettlement.accounting_date) until the journal is posted.

  Workflow: reset to draft from approved / cancelled (approve right, reason; refused when paid or the journal is posted / exported – an
  open journal is cancelled), cancel from submitted / validated / approved (reason; the journal is cancelled, or reversed when posted),
  delete a draft (delete right; its history is kept in CompanyOption 'eos_deleted_settlements' and shown on the leaver).

Saved settlements (submitted or later) never change – lines are stored; a draft takes the settings when it is calculated again (or when HR
saves an entry, which calculates it again).
"""
import calendar
import json
import logging
from datetime import date, timedelta
from decimal import Decimal

from django.apps import apps
from django.db import transaction
from django.utils import timezone

from Translations.i18n import tr

log = logging.getLogger(__name__)
M = apps.get_model
KEY = 'eos_settings'
DELETED_KEY = 'eos_deleted_settlements'
CHOICES = {'gratuity_daily': ('basic_30', 'basic_365'), 'service_days': ('both', 'difference'), 'leave_base': ('basic', 'gross'),
           'leave_daily': ('d30', 'contract', 'd365'), 'salary_daily': ('d30', 'cycle'), 'air_ticket': ('policy', 'hr')}
DEFAULTS = {'gratuity_daily': 'basic_30', 'service_days': 'both', 'leave_base': 'basic', 'leave_daily': 'd30', 'contract_leave_days': 30,
            'cycle_start_day': 1, 'salary_daily': 'd30', 'salary_exclude': [], 'unpaid_leave_types': [], 'air_ticket': 'policy',
            'show_full_emirates_id': False, 'journal_code': ''}


def _S():
    from . import services as S
    return S


def D(v):
    return Decimal(str(v if v not in (None, '') else 0))


def _n(n):
    """26.0 → '26', 22.5 → '22.5' (days shown in the texts)."""
    return format(D(n).normalize(), 'f')


# ------------------------------------------------------------------ company settings
def settings():
    try:
        o = M('ZeoApps', 'CompanyOption').objects.filter(key=KEY).first()
        v = dict(o.value or {}) if o is not None else {}
    except Exception:
        v = {}
    out = {k: (list(x) if isinstance(x, list) else x) for k, x in DEFAULTS.items()}
    out.update({k: v[k] for k in DEFAULTS if k in v})
    for k in ('updated_by', 'updated_at'):
        if v.get(k):
            out[k] = v[k]
    return out


def is_default(s=None):
    s = s or settings()
    return all(s.get(k) == DEFAULTS[k] for k in DEFAULTS if k not in ('contract_leave_days', 'show_full_emirates_id', 'journal_code'))


def save_settings(data, user=None):
    S = _S()
    cur = settings()
    problems = {}
    for k, ok in CHOICES.items():
        if k in data:
            if data[k] not in ok:
                problems[k] = tr('Choose one of the options.')
            else:
                cur[k] = data[k]
    for k, lo, hi in (('contract_leave_days', 1, 366), ('cycle_start_day', 1, 28)):
        if k in data:
            try:
                n = D(data[k])
                if not lo <= n <= hi:
                    raise ValueError
                cur[k] = int(n) if n == int(n) else float(n)
            except Exception:
                problems[k] = tr('Enter a number from {0} to {1}.', lo, hi)
    if 'salary_exclude' in data:
        cur['salary_exclude'] = sorted({str(x).strip()[:40] for x in (data.get('salary_exclude') or []) if str(x).strip()})
    if 'unpaid_leave_types' in data:
        try:
            ids = sorted({int(x) for x in (data.get('unpaid_leave_types') or [])})
        except (TypeError, ValueError):
            ids = None
            problems['unpaid_leave_types'] = tr('Choose leave types.')
        if ids is not None:
            have = set(M('calendars', 'leave_type').objects.filter(pk__in=ids).values_list('pk', flat=True))
            if set(ids) - have:
                problems['unpaid_leave_types'] = tr('Choose leave types.')
            cur['unpaid_leave_types'] = ids
    if 'show_full_emirates_id' in data:
        cur['show_full_emirates_id'] = str(data['show_full_emirates_id']).lower() in ('1', 'true', 'yes', 'on')
    if 'journal_code' in data:
        cur['journal_code'] = str(data.get('journal_code') or '').strip()[:20]
    if problems:
        raise S.Refused(tr('Check the highlighted fields.'), problems=problems)
    cur['updated_by'] = S.person(getattr(user, 'pk', None))
    cur['updated_at'] = timezone.now().isoformat(timespec='seconds')
    M('ZeoApps', 'CompanyOption').objects.update_or_create(key=KEY, defaults={'value': cur})
    return settings()


def options():
    """The lists of the settings page: salary components of the wage (by code), leave types, the global journal."""
    S = _S()
    SC = M('PayrollManagement', 'SalaryComponent')
    comps = {}
    for c in SC.objects.filter(component_type='addition', component_value_type='fixed', payroll_category__in=S.WAGE_CATEGORIES).select_related('branch').order_by('name'):
        if (c.code or '') in S.NOT_WAGE_CODES:
            continue
        k = comp_key(c)
        r = comps.setdefault(k, {'key': k, 'code': c.code or '', 'name': c.name, 'category': c.payroll_category, 'branches': []})
        if c.branch_id and c.branch.branch_name not in r['branches']:
            r['branches'].append(c.branch.branch_name)
    LT = M('calendars', 'leave_type')
    types = [{'id': t.pk, 'name': t.name, 'code': t.code, 'type': t.type, 'branch': t.branch.branch_name if t.branch_id else ''}
             for t in LT.objects.select_related('branch').order_by('name', 'id')]
    try:
        g = M('ZeoAccounting', 'AccountingSettings').objects.order_by('id').first()
        gj = getattr(g, 'journal_code', '') or ''
    except LookupError:
        gj = ''
    return {'components': sorted(comps.values(), key=lambda r: (r['category'] != 'basic', r['name'].lower())), 'leave_types': types,
            'global_journal': gj, 'defaults': DEFAULTS}


def comp_key(c):
    return (c.code or '').strip() or c.name.strip()


# ------------------------------------------------------------------ the calculation (called from services.compute)
def begin(st):
    """The rules of this calculation (kept on the settlement – shown on the case) and HR's entries."""
    s = settings()
    inp = dict(st.inputs or {})
    inp['rules'] = {k: s[k] for k in DEFAULTS}
    inp['rules']['at'] = timezone.now().isoformat(timespec='seconds')
    st.inputs = inp
    return {'s': s, 'inp': inp}


def eos_opts(es):
    s, inp = es['s'], es['inp']
    o = {'gratuity_daily': s['gratuity_daily'], 'service_days': s['service_days'], 'unpaid_types': list(s.get('unpaid_leave_types') or [])}
    u = inp.get('unpaid_days')
    if isinstance(u, dict) and u.get('days') not in (None, ''):
        o['unpaid_days'] = float(u['days'])
    return o


def after_eos(es, opts):
    """The unpaid days the records give (before HR's override) – shown next to the override."""
    comp = es['inp'].setdefault('computed', {})
    if 'computed_unpaid' in opts:
        comp['unpaid_days'] = float(opts['computed_unpaid'])


def wage_rows(emp):
    """The fixed wage components as services._wage reads them: [{key, code, name, category, amount}]."""
    S = _S()
    ESS = M('PayrollManagement', 'EmployeeSalaryStructure')
    out = []
    for r in ESS.objects.filter(employee=emp, is_active=True).select_related('component'):
        c = r.component
        if c.component_type != 'addition' or c.component_value_type != 'fixed' or (c.code or '') in S.NOT_WAGE_CODES:
            continue
        if c.payroll_category not in S.WAGE_CATEGORIES:
            continue
        out.append({'key': comp_key(c), 'code': c.code or '', 'name': c.name, 'category': c.payroll_category, 'amount': S.q2(r.amount)})
    return out


def _add_months(d, n):
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def cycle_of(d, start_day=1):
    """(first day, last day) of the payroll month containing d: start_day 1 = the calendar month, 25 = the 25th to the 24th."""
    if start_day <= 1:
        return d.replace(day=1), d.replace(day=calendar.monthrange(d.year, d.month)[1])
    if d.day >= start_day:
        a = d.replace(day=start_day)
    else:
        p = _add_months(d.replace(day=1), -1)
        a = p.replace(day=start_day)
    b = _add_months(a.replace(day=1), 1).replace(day=start_day) - timedelta(days=1)
    return a, b


def salary_rows(wage, a, b, start_day=1, mode='d30'):
    """Month by month from a to b: a complete payroll month is one month's wage; a part month is days ÷ 30 (at most 30) or days ÷ the
    days of that month / cycle. (total unrounded, [{from, to, days, full, divisor, amount, label}])."""
    S = _S()
    wage = D(wage)
    total, rows, d = Decimal('0'), [], a
    while d <= b:
        cs, ce = cycle_of(d, start_day)
        stop = min(ce, b)
        n = (stop - d).days + 1
        clen = (ce - cs).days + 1
        if d == cs and stop == ce:
            amt, full, div = wage, True, None
            label = tr('{0:%d/%m/%Y} – {1:%d/%m/%Y}: full month {2:,.2f}', d, stop, S.q2(wage))
        else:
            full = False
            if mode == 'cycle':
                div = clen
                amt = wage * n / clen
            else:
                div = 30
                amt = wage * min(n, 30) / 30
            label = tr('{0:%d/%m/%Y} – {1:%d/%m/%Y}: {2:,.2f} × {3} ÷ {4} = {5:,.2f}', d, stop, S.q2(wage), min(n, 30) if div == 30 else n, div, S.q2(amt))
        total += amt
        rows.append({'from': d.isoformat(), 'to': stop.isoformat(), 'days': n, 'full': full, 'divisor': div, 'amount': float(S.q2(amt)), 'label': label})
        d = stop + timedelta(days=1)
    return total, rows


def salary_custom(es):
    s, inp = es['s'], es['inp']
    return bool(int(s.get('cycle_start_day') or 1) != 1 or s.get('salary_daily') != 'd30' or s.get('salary_exclude') or inp.get('salary_from'))


def salary_start(es, start, emp=None):
    """The 'Unpaid salary from' date: HR's entry on the draft, else the day after the month paid by the last payroll (as before)."""
    es['inp'].setdefault('computed', {})['salary_from'] = start.isoformat() if start else None
    v = es['inp'].get('salary_from')
    if v:
        try:
            return date.fromisoformat(str(v)[:10])
        except ValueError:
            pass
    return start


def final_salary(es, emp, wage, a, b):
    """{wage, excluded, total, rows, basis, calc} of the final salary with the company's rules (month by month, cycle, exclusions)."""
    S = _S()
    s = es['s']
    excl = set(s.get('salary_exclude') or [])
    rows = wage_rows(emp)
    out_rows = [r for r in rows if r['key'] in excl]
    w = D(wage) - sum((r['amount'] for r in out_rows), Decimal('0'))
    total, prow = salary_rows(w, a, b, int(s.get('cycle_start_day') or 1), s.get('salary_daily') or 'd30')
    kept = [f"{r['name']} {r['amount']:,.2f}" for r in rows if r['key'] not in excl]
    part = tr('a part month ÷ 30') if (s.get('salary_daily') or 'd30') == 'd30' else tr('a part month ÷ the days of that month')
    basis = tr('Monthly salary {0:,.2f} ({1}); full months paid in full, {2}', S.q2(w), ', '.join(kept), part)
    calc = [x['label'] for x in prow]
    if out_rows:
        calc.insert(0, tr('Left out of the final salary: {0}', ', '.join(f"{r['name']} {r['amount']:,.2f}" for r in out_rows)))
    if int(s.get('cycle_start_day') or 1) != 1:
        calc.insert(0, tr('Payroll month from day {0} to day {1} of the next month', int(s['cycle_start_day']), int(s['cycle_start_day']) - 1))
    return {'wage': S.q2(w), 'excluded': [{'name': r['name'], 'code': r['code'], 'amount': float(r['amount'])} for r in out_rows],
            'total': total, 'rows': prow, 'basis': basis, 'calc': calc}


def contract_days(es, emp, AL, lwd):
    """(days, source): the draft's entry, else the yearly days of the employee's annual leave policy, else the company's days."""
    v = es['inp'].get('contract_leave_days')
    if v not in (None, ''):
        try:
            if D(v) > 0:
                return D(v), 'settlement'
        except Exception:
            pass
    if AL is not None and emp is not None:
        try:
            from LeavePolicy import engine
            line = engine.line_for(emp, AL.pk)
            if line is not None:
                n = D(engine.days_a_year(line, emp, lwd))
                if n > 0:
                    return n, 'policy'
        except Exception:
            log.debug('policy leave days', exc_info=True)
    return D(es['s'].get('contract_leave_days') or 30), 'company'


def leave_lines(es, basic, wage, computed, calc, emp, AL, lwd, add):
    """The leave salary with the company's rules or HR's balance override; None = today's calculation (basic ÷ 30, the computed balance)."""
    S = _S()
    s, inp = es['s'], es['inp']
    inp.setdefault('computed', {})['leave_days'] = float(computed)
    ov = inp.get('leave_balance') if isinstance(inp.get('leave_balance'), dict) and inp['leave_balance'].get('days') not in (None, '') else None
    base, mode = s.get('leave_base') or 'basic', s.get('leave_daily') or 'd30'
    if base == 'basic' and mode == 'd30' and ov is None:
        return None
    days = D(ov['days']) if ov else D(computed)
    calc = list(calc or [])
    if ov:
        calc.append(tr('Balance entered by HR: {0} days (calculated {1} days) – {2}', S.q2(days), S.q2(computed), ov.get('reason') or ''))
    monthly = D(basic) if base == 'basic' else D(wage)
    if mode == 'contract':
        n, src = contract_days(es, emp, AL, lwd)
        inp['computed']['contract_leave_days'] = float(n)
        inp['computed']['contract_leave_days_from'] = src
        rate = monthly / n
        basis = (tr('basic {0:,.2f} ÷ {1} contract leave days × days', S.q2(monthly), _n(n)) if base == 'basic'
                 else tr('gross {0:,.2f} ÷ {1} contract leave days × days', S.q2(monthly), _n(n)))
        calc.append(tr('{0:,.2f} ÷ {1} = {2} a day (not rounded)', S.q2(monthly), _n(n), f'{rate:.4f}'))
        amount = abs(days) * monthly / n
    elif mode == 'd365':
        rate = monthly * 12 / 365
        basis = (tr('basic {0:,.2f} × 12 ÷ 365 × days', S.q2(monthly)) if base == 'basic' else tr('gross {0:,.2f} × 12 ÷ 365 × days', S.q2(monthly)))
        calc.append(tr('{0:,.2f} × 12 ÷ 365 = {1} a day (not rounded)', S.q2(monthly), f'{rate:.4f}'))
        amount = abs(days) * monthly * 12 / 365
    else:
        rate = monthly / 30
        basis = tr('basic {0:,.2f} ÷ 30 × days', S.q2(monthly)) if base == 'basic' else tr('gross {0:,.2f} ÷ 30 × days', S.q2(monthly))
        calc.append(tr('{0:,.2f} ÷ 30 = {1} a day (not rounded)', S.q2(monthly), f'{rate:.4f}'))
        amount = abs(days) * monthly / 30
    calc.append(tr('{0} days × {1} = {2:,.2f}', S.q2(abs(days)), f'{rate:.4f}', S.q2(amount)))
    if days > 0:
        add('leave', tr('Unused annual leave {0} days', S.q2(days)), 'earning', amount, basis, 'leave', calc)
    elif days < 0:
        add('leave_negative', tr('Annual leave taken in advance {0} days', S.q2(-days)), 'deduction', amount, basis, 'leave', calc)
    return True


def gratuity_adjustment(es, add):
    S = _S()
    g = es['inp'].get('gratuity_adjustment')
    if not isinstance(g, dict):
        return
    amt = S.q2(g.get('amount'))
    if amt > 0:
        add('gratuity_adjustment', tr('Gratuity adjustment'), 'earning', amt, g.get('reason') or '', 'gratuity', by=g.get('by') or '', manual_input=True)
    elif amt < 0:
        add('gratuity_adjustment', tr('Gratuity adjustment'), 'deduction', -amt, g.get('reason') or '', 'gratuity', by=g.get('by') or '', manual_input=True)


def air_sinks(es, add, info):
    """(add, info) for the policy air ticket: with 'entered by HR' the policy amount is only shown."""
    if es['s'].get('air_ticket') != 'hr':
        return add, info
    S = _S()

    def shown(key, label, kind, amount, basis='', section='', calc=None, **extra):
        if S.q2(amount):
            info(f'{key}_policy', tr('{0} – not paid: the air ticket is entered by HR', label), tr('policy amount {0:,.2f}', S.q2(amount)), 'ticket')
    return shown, info


def air_ticket_hr(es, add, info):
    S = _S()
    t = es['inp'].get('air_ticket') if isinstance(es['inp'].get('air_ticket'), dict) else None
    amt = S.q2((t or {}).get('amount'))
    if amt > 0:
        basis = tr('Sector {0} · class {1} · {2} travellers', t.get('sector') or '–', t.get('travel_class') or '–', t.get('travellers') or 0)
        add('air_ticket_hr', tr('Air ticket (entered by HR)'), 'earning', amt, basis, 'ticket',
            ticket={k: t.get(k) for k in ('sector', 'travel_class', 'travellers', 'note')}, by=t.get('by') or '', manual_input=True)
    elif es['s'].get('air_ticket') == 'hr':
        info('ticket_hr_missing', tr('Air ticket: no amount entered by HR yet'), '', 'ticket')


# ------------------------------------------------------------------ HR's entries on the draft
def _date(v, field):
    S = _S()
    if v in (None, ''):
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        raise S.Refused(tr('Enter a valid date.'), problems={field: tr('Enter a valid date.')})


def _num(v, field, allow_negative=False):
    S = _S()
    try:
        n = D(v)
    except Exception:
        raise S.Refused(tr('Enter a number.'), problems={field: tr('Enter a number.')})
    if n < 0 and not allow_negative:
        raise S.Refused(tr('Enter a number of zero or more.'), problems={field: tr('Enter a number of zero or more.')})
    return n


def journal_of(st):
    try:
        JB = M('ZeoAccounting', 'JournalBatch')
    except LookupError:
        return None
    return JB.objects.filter(source='settlement', source_id=str(st.pk)).exclude(status__in=('reversed', 'cancelled')).order_by('-id').first()


def set_inputs(st, data, user):
    """POST settlements/<id>/inputs/ – HR's entries. The figures only on a draft (it is calculated again); the accounting date until the
    journal is posted."""
    S = _S()
    who, at = S.person(user.pk), timezone.now().isoformat(timespec='seconds')
    inp = dict(st.inputs or {})
    notes, figures = [], False
    keys = set(data.keys()) - {'accounting_date'}
    if keys & {'gratuity_adjustment', 'leave_balance', 'unpaid_days', 'contract_leave_days', 'salary_from', 'air_ticket'} and st.status != 'draft':
        raise S.Refused(tr('Only a draft settlement can be changed – send it back to HR first.'), status=409)
    if 'gratuity_adjustment' in data:
        g = data.get('gratuity_adjustment') or {}
        amt = _num(g.get('amount') or 0, 'gratuity_adjustment', True) if isinstance(g, dict) else Decimal('0')
        if amt:
            reason = str(g.get('reason') or '').strip()
            if not reason:
                raise S.Refused(tr('Say why the gratuity is adjusted.'), problems={'gratuity_reason': tr('Required.')})
            inp['gratuity_adjustment'] = {'amount': float(S.q2(amt)), 'reason': reason[:300], 'by': who, 'at': at}
            notes.append(tr('Gratuity adjustment {0:+,.2f} – {1}', S.q2(amt), reason[:120]))
        elif inp.pop('gratuity_adjustment', None) is not None:
            notes.append(tr('Gratuity adjustment removed'))
        figures = True
    for key, label, req in (('leave_balance', 'Leave balance', 'Say why the leave balance is changed.'),
                            ('unpaid_days', 'Unpaid leave days', 'Say why the unpaid leave days are changed.')):
        if key in data:
            v = data.get(key)
            if isinstance(v, dict) and v.get('days') not in (None, ''):
                n = _num(v['days'], key, key == 'leave_balance')
                reason = str(v.get('reason') or '').strip()
                if not reason:
                    raise S.Refused(tr(req), problems={key + '_reason': tr('Required.')})
                inp[key] = {'days': float(S.q2(n)), 'reason': reason[:300], 'by': who, 'at': at}
                notes.append(tr('{0}: {1} days – {2}', tr(label), S.q2(n), reason[:120]))
            elif inp.pop(key, None) is not None:
                notes.append(tr('{0}: calculated again from the records', tr(label)))
            figures = True
    if 'contract_leave_days' in data:
        v = data.get('contract_leave_days')
        if v in (None, ''):
            inp.pop('contract_leave_days', None)
        else:
            n = _num(v, 'contract_leave_days')
            if not 1 <= n <= 366:
                raise S.Refused(tr('Enter a number from {0} to {1}.', 1, 366), problems={'contract_leave_days': tr('Enter a number from {0} to {1}.', 1, 366)})
            inp['contract_leave_days'] = float(n)
            notes.append(tr('Contract leave days {0}', _n(n)))
        figures = True
    if 'salary_from' in data:
        d = _date(data.get('salary_from'), 'salary_from')
        if d is None:
            inp.pop('salary_from', None)
        else:
            if d > st.last_working_date or (st.joined and d < st.joined):
                raise S.Refused(tr('The unpaid salary must start between the joining date and the last working day.'), problems={'salary_from': tr('Invalid.')})
            inp['salary_from'] = d.isoformat()
            notes.append(tr('Unpaid salary from {0:%d/%m/%Y}', d))
        figures = True
    if 'air_ticket' in data:
        t = data.get('air_ticket')
        if isinstance(t, dict) and t.get('amount') not in (None, ''):
            amt = _num(t['amount'], 'air_ticket_amount')
            try:
                trav = int(t.get('travellers') or 0)
                if trav < 0:
                    raise ValueError
            except (TypeError, ValueError):
                raise S.Refused(tr('Enter the number of travellers.'), problems={'air_ticket_travellers': tr('Invalid.')})
            inp['air_ticket'] = {'amount': float(S.q2(amt)), 'sector': str(t.get('sector') or '').strip()[:80], 'travel_class': str(t.get('travel_class') or '').strip()[:30],
                                 'travellers': trav, 'note': str(t.get('note') or '').strip()[:200], 'by': who, 'at': at}
            notes.append(tr('Air ticket {0:,.2f} entered by HR', S.q2(amt)))
        elif inp.pop('air_ticket', None) is not None:
            notes.append(tr('Air ticket entered by HR removed'))
        figures = True
    with transaction.atomic():
        if 'accounting_date' in data:
            if st.status == 'cancelled':
                raise S.Refused(tr('This settlement is cancelled.'), status=409)
            b = journal_of(st)
            try:
                from ZeoAccounting.services import DONE
            except ImportError:
                DONE = ('posted', 'exported')
            if b is not None and b.status in DONE:
                raise S.Refused(tr('The journal {0} is already posted – reverse it in Accounting first.', b.number), status=409)
            d = _date(data.get('accounting_date'), 'accounting_date')
            st.accounting_date = d
            notes.append(tr('Accounting date {0}', d.strftime('%d/%m/%Y') if d else tr('as the payment date')))
            if b is not None:   # an open journal takes the new date
                b.date = d or st.paid_on or st.last_working_date
                b.save(update_fields=['date'])
        st.inputs = inp
        S.log_step(st, 'inputs', user, '; '.join(notes))
        if figures:
            S.compute(st, user)
        else:
            st.save()
    return st


# ------------------------------------------------------------------ workflow: reset to draft, cancel with the journal, delete a draft
def _journal_undo(st, user, reverse_posted):
    """Cancels the open journal of the settlement; reverses a posted one when reverse_posted, else refuses. Returns a note."""
    S = _S()
    b = journal_of(st)
    if b is None:
        return ''
    from ZeoAccounting import services as ACC
    if b.status in ACC.DONE:
        if not reverse_posted:
            raise S.Refused(tr('The journal {0} is posted to the accounting app – reverse it in Accounting › Journals first.', b.number), status=409)
        try:
            r = ACC.reverse(b, user)
        except ACC.Refused as x:
            raise S.Refused(tr(x.message), status=x.status)
        return tr('journal {0} reversed by {1}', b.number, r.number)
    try:
        ACC.cancel(b, user)
    except ACC.Refused as x:
        raise S.Refused(tr(x.message), status=x.status)
    return tr('journal {0} cancelled', b.number)


def workflow(st, action, user, codes, admin, data):
    S = _S()
    note = str((data or {}).get('note') or (data or {}).get('reason') or '').strip()
    if not (admin or 'approve_exitsettlement' in codes):
        raise S.Refused(tr('You may not do this step.'), status=403)
    with transaction.atomic():
        if action == 'reset':
            if st.status == 'paid':
                raise S.Refused(tr('A paid settlement cannot be reset to draft.'), status=409)
            if st.status not in ('approved', 'cancelled'):
                raise S.Refused(tr('Only an approved or cancelled settlement can be reset to draft – send a submitted one back to HR.'), status=409)
            if not note:
                raise S.Refused(tr('Say why the settlement is reset to draft.'), problems={'note': tr('Required.')})
            jn = _journal_undo(st, user, reverse_posted=False)
            st.submitted_by_id = st.validated_by_id = st.approved_by_id = None
            st.submitted_at = st.validated_at = st.approved_at = None
            st.notes = (st.notes + '\n' if st.notes else '') + f'{timezone.localdate():%d/%m/%Y} reset to draft by {S.user_name(user.pk)}: {note}'
            S.log_step(st, 'reset', user, note + (f' – {jn}' if jn else ''))
            st.status = 'draft'
            st.save()
            return st
        # cancel from submitted / validated / approved
        if st.status not in ('submitted', 'validated', 'approved'):
            raise S.Refused(tr('This settlement is {0}.', st.get_status_display().lower()), status=409)
        if not note:
            raise S.Refused(tr('Say why the settlement is cancelled.'), problems={'note': tr('Required.')})
        jn = _journal_undo(st, user, reverse_posted=True)
        st.notes = (st.notes + '\n' if st.notes else '') + f'{timezone.localdate():%d/%m/%Y} cancelled by {S.user_name(user.pk)}: {note}'
        S.log_step(st, 'cancel', user, note + (f' – {jn}' if jn else ''))
        st.status = 'cancelled'
        st.save()
        return st


def delete_draft(st, user, codes, admin, note=''):
    S = _S()
    if st.status != 'draft':
        raise S.Refused(tr('Only a draft settlement can be deleted.'), status=409)
    if not (admin or 'delete_exitsettlement' in codes):
        raise S.Refused(tr('You may not delete final settlements.'), status=403)
    EOS = M('EmpManagement', 'EndOfService')
    res_id = EOS.objects.filter(pk=st.eos_id).values_list('resignation_id', flat=True).first()
    CO = M('ZeoApps', 'CompanyOption')
    with transaction.atomic():
        o, _ = CO.objects.select_for_update().get_or_create(key=DELETED_KEY, defaults={'value': {'rows': []}})
        v = dict(o.value or {})
        rows = list(v.get('rows') or [])
        S.log_step(st, 'deleted', user, note)
        rows.append({'resignation_id': res_id, 'eos_id': st.eos_id, 'employee_id': st.employee_id, 'number': st.number, 'net': float(st.net),
                     'calc_date': st.calc_date.isoformat() if st.calc_date else None, 'history': json.loads(json.dumps(S.history_rows(st), default=str)),
                     'by': S.person(user.pk), 'at': timezone.now().isoformat(timespec='seconds'), 'note': (note or '')[:300]})
        v['rows'] = rows[-500:]
        o.value = v
        o.save()
        st.delete()
    return res_id


def deleted_for(res_id):
    try:
        o = M('ZeoApps', 'CompanyOption').objects.filter(key=DELETED_KEY).first()
    except Exception:
        return []
    if o is None:
        return []
    return [r for r in ((o.value or {}).get('rows') or []) if r.get('resignation_id') == res_id]


# ------------------------------------------------------------------ what the case / print shows
def row_extra(st):
    """Extra fields of settlement_row(full): HR's entries, the rules used, the journal, the accounting date, a negative net."""
    S = _S()
    inp = dict(st.inputs or {})
    b = journal_of(st)
    out = {'inputs': {k: v for k, v in inp.items() if k not in ('rules', 'computed')}, 'rules': inp.get('rules') or None,
           'computed': inp.get('computed') or {}, 'accounting_date': st.accounting_date,
           'journal': {'id': b.pk, 'number': b.number, 'status': b.status, 'status_label': b.get_status_display(), 'external_ref': b.external_ref or '',
                       'date': b.date} if b is not None else None,
           'recoverable': st.net < 0, 'net_label': tr('Net recoverable from the employee') if st.net < 0 else tr('Net payable')}
    if st.net < 0:
        out['net_words'] = S.amount_words(-st.net)
    return out


def case_extra(emp, res):
    """Emirates ID (masked unless the setting shows it in full), contract type."""
    eid, ctype = '', ''
    try:
        from SelfService import profile as P
        ident = P.employee_identity(emp)
        eid = getattr(ident, 'emirates_id', '') or ''
        ctype = ident.get_mohre_contract_type_display() if ident is not None and getattr(ident, 'mohre_contract_type', '') else ''
    except Exception:
        pass
    shown = eid
    if eid and not settings().get('show_full_emirates_id'):
        try:
            from PrintTemplates.contexts import mask
            shown = mask(eid, 4)
        except Exception:
            shown = '•' * max(len(eid) - 4, 0) + eid[-4:]
    return {'emirates_id': shown, 'contract_type': tr(ctype) if ctype else ''}
