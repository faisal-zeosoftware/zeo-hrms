"""v1.53.0 – consolidated payroll cost of the group in one currency (roadmap 24).

Each company's payroll is read in its own currency (the company currency, else the currency of its country pack) and
converted to the group's reporting currency with the rate of the month:
  1. a rate entered for the group for that pair (the latest one from a month on or before the payroll month);
  2. the opposite pair, inverted;
  3. the official US dollar pegs of the pegged currencies (AED, SAR, QAR, OMR, BHD, JOD) – fixed by the central banks.
Kuwaiti dinar and Indian rupee float: enter their rates. A company whose rate is missing is shown, but not added to the total.
"""
import logging
from datetime import date
from decimal import Decimal

from django.db.models import Count, Sum
from django_tenants.utils import schema_context

log = logging.getLogger(__name__)
# units of the currency per 1 US dollar (central bank pegs)
USD_PEGS = {'USD': Decimal('1'), 'AED': Decimal('3.6725'), 'SAR': Decimal('3.75'), 'QAR': Decimal('3.64'), 'OMR': Decimal('0.3845'),
            'BHD': Decimal('0.376'), 'JOD': Decimal('0.709')}
CURRENCIES = ['AED', 'SAR', 'QAR', 'OMR', 'KWD', 'BHD', 'INR', 'USD', 'EUR', 'GBP', 'JOD', 'EGP', 'PKR', 'PHP']


def month_start(d):
    return d.replace(day=1)


def parse_month(s, default=None):
    try:
        y, m = str(s).split('-')[:2]
        return date(int(y), int(m), 1)
    except (ValueError, TypeError):
        return default


def reporting_currency(key):
    from .models import GroupCurrency
    g = GroupCurrency.objects.filter(key=key).first()
    return g.currency if g else 'AED'


class Rates:
    def __init__(self, key):
        from .models import ExchangeRate
        self.rows = list(ExchangeRate.objects.filter(key=key).order_by('-month'))

    def find(self, cur, to, month):
        """(rate, how) – how: 'same', 'entered', 'inverse', 'peg'; (None, 'missing')."""
        cur, to = (cur or '').upper(), (to or '').upper()
        if cur == to:
            return Decimal('1'), 'same'
        for r in self.rows:
            if r.currency == cur and r.to_currency == to and r.month <= month:
                return r.rate, 'entered'
        for r in self.rows:
            if r.currency == to and r.to_currency == cur and r.month <= month and r.rate:
                return Decimal('1') / r.rate, 'inverse'
        if cur in USD_PEGS and to in USD_PEGS:
            return USD_PEGS[to] / USD_PEGS[cur], 'peg'
        return None, 'missing'


def company_currency(comp):
    cur = getattr(comp, 'currency', None)   # the currency set on the company comes first
    if cur is not None and getattr(cur, 'currency_code', ''):
        return cur.currency_code.upper()
    try:
        from CountryPack.services import current_pack
        cur = (current_pack(comp.schema_name) or {}).get('currency') or {}
        code = cur.get('code') if isinstance(cur, dict) else str(cur)
        if code:
            return code.upper()
    except Exception:
        log.exception('currency of %s', comp.schema_name)
    cur = getattr(comp, 'currency', None)
    return (getattr(cur, 'currency_code', '') or 'AED').upper()


def company_payroll(comp, first, last):
    """{YYYY-MM: {gross, deductions, net, payslips, employees}} of the company's payroll runs in the months."""
    out = {}
    with schema_context(comp.schema_name):
        from django.apps import apps
        Slip = apps.get_model('PayrollManagement', 'Payslip')
        qs = Slip.objects.filter(payroll_run__year__gte=first.year, payroll_run__year__lte=last.year)
        for row in qs.values('payroll_run__year', 'payroll_run__month').annotate(g=Sum('gross_salary'), d=Sum('total_deductions'),
                                                                                n=Sum('net_salary'), c=Count('id'),
                                                                                e=Count('employee', distinct=True)):
            try:
                m = date(int(row['payroll_run__year']), int(row['payroll_run__month']), 1)
            except (TypeError, ValueError):
                continue
            if not (first <= m <= last):
                continue
            out[f'{m.year}-{m.month:02d}'] = {'gross': Decimal(row['g'] or 0), 'deductions': Decimal(row['d'] or 0), 'net': Decimal(row['n'] or 0),
                                               'payslips': row['c'], 'employees': row['e']}
    return out


def months(first, last):
    out, cur = [], first
    while cur <= last and len(out) < 36:
        out.append(cur)
        cur = date(cur.year + (cur.month // 12), cur.month % 12 + 1, 1)
    return out


def consolidate(group, first, last, currency=None):
    key = group['key']
    to = (currency or reporting_currency(key)).upper()
    rates = Rates(key)
    ms = months(first, last)
    labels = [f'{m.year}-{m.month:02d}' for m in ms]
    companies, missing = [], set()
    tot_month = {l: Decimal('0') for l in labels}
    total = {'gross': Decimal('0'), 'deductions': Decimal('0'), 'net': Decimal('0'), 'payslips': 0}
    for comp in group['companies']:
        cur = company_currency(comp)
        row = {'company': comp.name, 'schema_name': comp.schema_name, 'currency': cur, 'months': {}, 'gross': 0.0, 'net': 0.0,
               'deductions': 0.0, 'gross_rc': 0.0, 'deductions_rc': 0.0, 'net_rc': 0.0, 'payslips': 0, 'employees': 0, 'rates': {}, 'missing': [], 'error': ''}
        try:
            pay = company_payroll(comp, ms[0], ms[-1])
        except Exception as e:
            log.exception('consolidation %s', comp.schema_name)
            row['error'] = str(e)[:200]
            pay = {}
        for m, lab in zip(ms, labels):
            p = pay.get(lab)
            if not p:
                continue
            rate, how = rates.find(cur, to, m)
            row['rates'][lab] = {'rate': float(rate) if rate is not None else None, 'how': how}
            row['gross'] += float(p['gross'])
            row['net'] += float(p['net'])
            row['deductions'] += float(p['deductions'])
            row['payslips'] += p['payslips']
            row['employees'] = max(row['employees'], p['employees'])
            if rate is None:
                row['missing'].append(lab)
                missing.add(f'{cur}→{to}')
                continue
            g, d, n = p['gross'] * rate, p['deductions'] * rate, p['net'] * rate
            row['months'][lab] = round(float(g), 2)
            row['gross_rc'] += float(g)
            row['deductions_rc'] += float(d)
            row['net_rc'] += float(n)
            tot_month[lab] += g
            total['gross'] += g
            total['deductions'] += d
            total['net'] += n
            total['payslips'] += p['payslips']
        for k in ('gross', 'net', 'deductions', 'gross_rc', 'deductions_rc', 'net_rc'):
            row[k] = round(row[k], 2)
        companies.append(row)
    return {'currency': to, 'from': labels[0] if labels else '', 'to': labels[-1] if labels else '', 'months': labels,
            'by_month': [round(float(tot_month[l]), 2) for l in labels], 'companies': companies,
            'total': {k: (round(float(v), 2) if isinstance(v, Decimal) else v) for k, v in total.items()},
            'missing': sorted(missing), 'pegged': sorted(USD_PEGS)}


def rate_rows(key):
    from .models import ExchangeRate
    return [{'id': r.id, 'currency': r.currency, 'to_currency': r.to_currency, 'month': r.month.isoformat()[:7], 'rate': float(r.rate),
             'note': r.note, 'by': getattr(r.by, 'email', '') if r.by_id else '', 'at': r.at} for r in ExchangeRate.objects.filter(key=key).select_related('by')]
