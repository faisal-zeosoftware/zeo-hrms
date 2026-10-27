"""v1.22.0 – the reports of CRM, Property, Maintenance, HSE and Projects in the HRMS report centre (Reports, z-report).

Every report has the HRMS structure: title, section, description, period picker, the user's branches (and the branches picked in
the top bar), Excel / CSV / PDF, group by, filters, favourites, e-mail and drill-down to the record (`_url`).
Rights: the app's roles (permission groups "<App> – <role>"); company administrators see everything.
A report of an app that is not active in the company is hidden (Settings → Apps).

    REPORTS spec: key → (title, section, description, fn(run), period, permission codes)
"""
import logging
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Avg, Count, Max, Q, Sum

log = logging.getLogger(__name__)

# report key prefix → the app (Settings → Apps) it belongs to
PREFIX_APP = {'crm-': 'crm', 'prop-': 'property', 'mnt-': 'maintenance', 'ast-': 'assets', 'ppl-': 'people', 'hse-': 'hse', 'pj-': 'projects'}
SLUG_PAGE = {'crm': '/main-sidebar/zeo-crm/', 'property': '/main-sidebar/zeo-property/', 'maintenance': '/main-sidebar/zeo-maintenance/', 'assets': '/main-sidebar/zeo-assets/', 'people': '/main-sidebar/zeo-people/', 'hr': '/main-sidebar/zeo-hr/',
             'hse': '/main-sidebar/zeo-hse/', 'projects': '/main-sidebar/zeo-projects/'}
DATE_FIELDS = ('date', 'occurred_at', 'reported_at', 'scheduled_at', 'due_date', 'start', 'start_date', 'cheque_date', 'created_at', 'requested_at')


def app_of(key):
    return next((a for p, a in PREFIX_APP.items() if key.startswith(p)), None)


PAYROLL_EXTRA = {'final-settlements', 'leave-encashment'}   # v1.23.0 payroll reports outside the Payroll section


def hidden_keys(keys, specs=None):
    """Report keys of apps that are not active in the company."""
    try:
        from .services import is_active
    except Exception:
        return set()
    state = {}
    out = set()
    for k in keys:
        a = app_of(k) or 'hrms'   # v1.22.0: reports without an app prefix are HRMS reports
        if a == 'hrms' and (k in PAYROLL_EXTRA or (specs and len(specs.get(k) or ()) > 1 and specs[k][1] == 'Payroll')):
            a = 'payroll'   # v1.23.0
        if a not in state:
            try:
                state[a] = is_active(a)
            except Exception:
                state[a] = False
        if not state[a]:
            out.add(k)
    return out


def _num(v):
    if isinstance(v, Decimal):
        return float(v)
    return v


def _branch_pick(run, qs, path):
    """Branches picked in the top bar (already limited to the user's branches by the report centre)."""
    b = getattr(run, 'branches', None)
    if b is None or not path:
        return qs
    return qs.filter(Q(**{f'{path}__in': b}) | Q(**{f'{path}__isnull': True}))


def _period(run, qs, model):
    names = {f.name: f for f in model._meta.fields}
    for n in DATE_FIELDS:
        if n in names:
            from django.db import models as M
            suffix = '__date' if isinstance(names[n], M.DateTimeField) else ''
            q = Q()
            if run.date_from and run.date_from.year > 1900:
                q &= Q(**{f'{n}{suffix}__gte': run.date_from})
            if run.date_to and run.date_to.year < 2999:
                q &= Q(**{f'{n}{suffix}__lte': run.date_to})
            return qs.filter(q), n
    return qs, None


def _coltype(t):
    return {'decimal': 'number', 'int': 'number', 'date': 'date', 'datetime': 'date'}.get(t, 'text')


# ------------------------------------------------------------------ one report per register (kit apps and HSE)
def register_report(app_key, page_slug, reg, value_fn, ftype_fn, field_of, branch_path, extra_cols=None):
    """A list report of one register: its list columns plus Branch, rows the user may see, period on its main date."""
    def fn(run):
        request = run.request
        qs = reg.scope(request)
        qs = _branch_pick(run, qs, branch_path)
        qs, dfield = _period(run, qs, reg.model)
        cols = [c for c in reg.cols if c != 'branch']
        out_cols = []
        for c in cols:
            try:
                f = field_of(reg.model, c)
                out_cols.append((c, (str(f.verbose_name)[:1].upper() + str(f.verbose_name)[1:]), _coltype(ftype_fn(f))))
            except Exception:
                continue
        if branch_path:
            out_cols.insert(1, ('branch', 'Branch', 'text'))
        rows = []
        for obj in qs[:5000]:
            r = {'_url': f'{page_slug}r/{reg.kind}/{obj.pk}', '_m': obj._meta.label, '_id': obj.pk}
            for c, _, t in out_cols:
                if c == 'branch':
                    x = obj
                    for part in branch_path.split('__'):
                        x = getattr(x, part, None) if x is not None else None
                    r['branch'] = getattr(x, 'branch_name', '') if x is not None else ''
                    if not r['branch']:   # v1.62.0 reports: the employee's branch when the record has none of its own
                        e = getattr(obj, 'employee', None)
                        r['branch'] = getattr(getattr(e, 'emp_branch_id', None), 'branch_name', '') or ''
                    continue
                try:
                    v, lbl = value_fn(reg, obj, c, not reg.show_restricted(request, obj) if hasattr(reg, 'show_restricted') else False)
                except Exception:
                    v, lbl = getattr(obj, c, None), None
                r[c] = lbl if lbl not in (None, '') else _num(v)
            rows.append(r)
        notes = [f'Period on: {field_of(reg.model, dfield).verbose_name}.'] if dfield else []
        return out_cols, rows, {'notes': notes, 'filters': [c for c, _, t in out_cols if t == 'text'][:4]}
    return fn


def kit_reports():
    out = {}
    try:
        from zeo.appkit.engine import REGISTRY, field_of, ftype, value
    except Exception:
        return out
    import importlib
    for label, key, prefix, section in (('ZeoCRM', 'crm', 'crm-', 'CRM'), ('ZeoProperty', 'property', 'prop-', 'Property'),
                                        ('ZeoMaintenance', 'maintenance', 'mnt-', 'Maintenance'),
                                        ('ZeoAssets', 'assets', 'ast-', 'Asset Management'), ('ZeoPeople', 'people', 'ppl-', 'People'),
                                        ('ZeoHR', 'hr', 'hro-', 'HR operations')):   # v1.48.0
        try:
            importlib.import_module(f'{label}.app')
        except Exception:
            continue
        a = REGISTRY.get(label)
        if a is None:
            continue
        page = SLUG_PAGE[key]
        all_roles = [a.code(r) for r, _ in a.roles]
        for reg in a.regs.values():
            # v1.38.0: child registers with records of their own (units, cheques, activities, viewings, offers, movements…)
            # get a report too; only line items (quotation lines, count lines, depreciation lines) stay out
            if reg.group == 'Setup' or (reg.child_of and (reg.kind.endswith('_line') or reg.kind.endswith('line') or reg.kind in ('reviewer', 'dep_line'))):
                continue
            codes = list(dict.fromkeys(a.code(r) for r in tuple(reg.manage) + tuple(reg.view))) or all_roles
            if label == 'ZeoHR':   # v1.48.0: the HR team works here with its employee rights
                codes += ['change_emp_master', 'view_emp_master']
            bp = 'branch' if 'branch' in {f.name for f in reg.model._meta.fields} else None
            out[f'{prefix}{reg.kind.replace("_", "-")}'] = (
                f'{reg.plural}', section, f'Every {reg.label.lower()} you may see, with its status and branch. Opens the record.',
                register_report(key, page, reg, value, ftype, field_of, bp), 'year' if _has_date(reg.model) else None, codes)
        out.update(KIT_EXTRA.get(key, lambda a: {})(a))
    return out


def _has_date(model):
    names = {f.name for f in model._meta.fields}
    return any(n in names for n in DATE_FIELDS)


# ------------------------------------------------------------------ analysis reports of the kit apps
def _crm(a):
    from ZeoCRM.models import Opportunity
    codes = [a.code(r) for r, _ in a.roles]
    page = SLUG_PAGE['crm']

    def pipeline(run):
        qs = _branch_pick(run, a.regs['opportunity'].scope(run.request), 'branch')
        rows = []
        for x in qs.filter(status='open').values('pipeline__name', 'stage__name', 'stage_id').annotate(n=Count('id'), v=Sum('value')).order_by('pipeline__name', 'stage__name'):
            rows.append({'pipeline': x['pipeline__name'] or '', 'stage': x['stage__name'] or '', 'open': x['n'], 'value': float(x['v'] or 0),
                         '_url': f"{page}r/opportunity?status=open&stage={x['stage_id']}"})
        return [('pipeline', 'Pipeline', 'text'), ('stage', 'Stage', 'text'), ('open', 'Open opportunities', 'number'), ('value', 'Value', 'number')], rows

    def by_agent(run):
        qs = _branch_pick(run, a.regs['opportunity'].scope(run.request), 'branch').filter(
            closed_at__date__gte=run.date_from, closed_at__date__lte=run.date_to)
        data = defaultdict(lambda: {'won': 0, 'lost': 0, 'won_value': 0.0})
        for o in qs.select_related('owner'):
            k = ' '.join(x for x in (getattr(o.owner, 'emp_first_name', ''), getattr(o.owner, 'emp_last_name', '')) if x) or 'Unassigned'
            if o.status == 'won':
                data[k]['won'] += 1
                data[k]['won_value'] += float(o.value or 0)
            elif o.status == 'lost':
                data[k]['lost'] += 1
        rows = [{'agent': k, **v, 'win_rate': round(100 * v['won'] / (v['won'] + v['lost']), 1) if (v['won'] + v['lost']) else 0} for k, v in data.items()]
        rows.sort(key=lambda r: -r['won_value'])
        return [('agent', 'Agent', 'text'), ('won', 'Won', 'number'), ('lost', 'Lost', 'number'), ('win_rate', 'Win rate %', 'number'),
                ('won_value', 'Won value', 'number')], rows

    def sources(run):
        qs = _branch_pick(run, a.regs['opportunity'].scope(run.request), 'branch').filter(
            created_at__date__gte=run.date_from, created_at__date__lte=run.date_to)
        rows = []
        for x in qs.values('source__name').annotate(n=Count('id'), won=Count('id', filter=Q(status='won')), v=Sum('value', filter=Q(status='won'))).order_by('-n'):
            rows.append({'source': x['source__name'] or 'No source', 'enquiries': x['n'], 'won': x['won'],
                         'conversion': round(100 * x['won'] / x['n'], 1) if x['n'] else 0, 'won_value': float(x['v'] or 0)})
        return [('source', 'Lead source', 'text'), ('enquiries', 'Enquiries', 'number'), ('won', 'Won', 'number'),
                ('conversion', 'Conversion %', 'number'), ('won_value', 'Won value', 'number')], rows

    # ---------------------------------------------------------------- v1.33.0 sales management
    from ZeoCRM.models import Lead, Quotation, QuotationLine, SalesOrderLine, SalesTarget
    from ZeoCRM.app import SALES

    def forecast(run):
        qs = _branch_pick(run, a.regs['opportunity'].scope(run.request), 'branch').filter(status='open').select_related('stage', 'owner', 'pipeline')
        data = defaultdict(lambda: {'open': 0, 'value': 0.0, 'weighted': 0.0, 'best': 0.0})
        for o in qs:
            m = o.expected_close.strftime('%Y-%m') if o.expected_close else 'No close date'
            owner = ' '.join(x for x in (getattr(o.owner, 'emp_first_name', ''), getattr(o.owner, 'emp_last_name', '')) if x) or 'Unassigned'
            k = (m, owner)
            p = o.stage.probability if o.stage_id else 0
            v = float(o.value or 0)
            data[k]['open'] += 1
            data[k]['value'] += v
            data[k]['weighted'] += v * p / 100
            if p >= 50:
                data[k]['best'] += v
        rows = [{'month': k[0], 'agent': k[1], **{x: round(y, 2) for x, y in v.items()}} for k, v in sorted(data.items())]
        return [('month', 'Expected close', 'text'), ('agent', 'Agent', 'text'), ('open', 'Open opportunities', 'number'), ('value', 'Pipeline value', 'number'),
                ('weighted', 'Weighted (× probability)', 'number'), ('best', 'Likely (≥ 50%)', 'number')], rows

    def targets(run):
        qs = _branch_pick(run, a.regs['target'].scope(run.request), 'branch').filter(is_active=True, period_end__gte=run.date_from, period_start__lte=run.date_to).select_related('owner', 'territory')
        rows = []
        for t in qs:
            act = float(SALES['attainment'](t))
            tgt = float(t.target or 0)
            rows.append({'target': t.name, 'agent': ' '.join(x for x in (getattr(t.owner, 'emp_first_name', ''), getattr(t.owner, 'emp_last_name', '')) if x) or (t.territory.name if t.territory_id else 'Team'),
                         'period': f'{t.period_start} – {t.period_end}', 'measure': t.get_measure_display(), 'target_value': tgt, 'actual': act,
                         'gap': round(tgt - act, 2), 'attainment': round(100 * act / tgt, 1) if tgt else 0, '_url': f'{page}r/target/{t.pk}', '_m': t._meta.label, '_id': t.pk})
        rows.sort(key=lambda r: -r['attainment'])
        return [('target', 'Target', 'text'), ('agent', 'Agent / territory', 'text'), ('period', 'Period', 'text'), ('measure', 'Measure', 'text'),
                ('target_value', 'Target', 'number'), ('actual', 'Actual', 'number'), ('gap', 'Gap', 'number'), ('attainment', 'Attainment %', 'number')], rows

    def products(run):
        ql = QuotationLine.objects.filter(quotation__in=_branch_pick(run, a.regs['quotation'].scope(run.request), 'branch').filter(date__gte=run.date_from, date__lte=run.date_to))
        sl = SalesOrderLine.objects.filter(sales_order__in=_branch_pick(run, a.regs['sales_order'].scope(run.request), 'branch').filter(date__gte=run.date_from, date__lte=run.date_to).exclude(status='cancelled'))
        data = defaultdict(lambda: {'quoted_qty': 0.0, 'quoted': 0.0, 'quotes': 0, 'ordered_qty': 0.0, 'ordered': 0.0, 'orders': 0})
        for x in ql.values('product__name', 'description').annotate(q=Sum('qty'), v=Sum('net'), n=Count('quotation_id', distinct=True)):
            k = x['product__name'] or x['description'] or 'No product'
            data[k]['quoted_qty'] += float(x['q'] or 0); data[k]['quoted'] += float(x['v'] or 0); data[k]['quotes'] += x['n']
        for x in sl.values('product__name', 'description').annotate(q=Sum('qty'), v=Sum('net'), n=Count('sales_order_id', distinct=True)):
            k = x['product__name'] or x['description'] or 'No product'
            data[k]['ordered_qty'] += float(x['q'] or 0); data[k]['ordered'] += float(x['v'] or 0); data[k]['orders'] += x['n']
        rows = sorted(({'product': k, **{x: round(y, 2) for x, y in v.items()}} for k, v in data.items()), key=lambda r: -r['ordered'])
        return [('product', 'Product', 'text'), ('quotes', 'Quotations', 'number'), ('quoted_qty', 'Quoted qty', 'number'), ('quoted', 'Quoted value', 'number'),
                ('orders', 'Orders', 'number'), ('ordered_qty', 'Ordered qty', 'number'), ('ordered', 'Ordered value', 'number')], rows

    def competitors(run):
        qs = _branch_pick(run, a.regs['opportunity'].scope(run.request), 'branch').filter(created_at__date__gte=run.date_from, created_at__date__lte=run.date_to).prefetch_related('competitors')
        data = defaultdict(lambda: {'faced': 0, 'won': 0, 'lost': 0, 'open': 0, 'lost_value': 0.0})
        for o in qs:
            for c in o.competitors.all():
                d_ = data[c.name]
                d_['faced'] += 1
                d_[o.status] += 1
                if o.status == 'lost':
                    d_['lost_value'] += float(o.value or 0)
        rows = [{'competitor': k, **v, 'win_rate': round(100 * v['won'] / (v['won'] + v['lost']), 1) if (v['won'] + v['lost']) else 0} for k, v in data.items()]
        rows.sort(key=lambda r: -r['faced'])
        return [('competitor', 'Competitor', 'text'), ('faced', 'Opportunities faced', 'number'), ('won', 'Won', 'number'), ('lost', 'Lost', 'number'),
                ('open', 'Open', 'number'), ('win_rate', 'Win rate %', 'number'), ('lost_value', 'Lost value', 'number')], rows

    def leads(run):
        qs = _branch_pick(run, a.regs['lead'].scope(run.request), 'branch').filter(created_at__date__gte=run.date_from, created_at__date__lte=run.date_to)
        rows = []
        for x in qs.values('source__name', 'campaign__name').annotate(n=Count('id'), q=Count('id', filter=Q(status__in=('qualified', 'converted'))),
                                                                     c=Count('id', filter=Q(status='converted')), dq=Count('id', filter=Q(status='disqualified')), sc=Avg('score')).order_by('-n'):
            rows.append({'source': x['source__name'] or 'No source', 'campaign': x['campaign__name'] or '', 'leads': x['n'], 'qualified': x['q'], 'converted': x['c'],
                         'disqualified': x['dq'], 'conversion': round(100 * x['c'] / x['n'], 1) if x['n'] else 0, 'avg_score': round(float(x['sc'] or 0), 1)})
        return [('source', 'Lead source', 'text'), ('campaign', 'Campaign', 'text'), ('leads', 'Leads', 'number'), ('qualified', 'Qualified', 'number'),
                ('converted', 'Converted', 'number'), ('disqualified', 'Disqualified', 'number'), ('conversion', 'Conversion %', 'number'), ('avg_score', 'Average score', 'number')], rows

    def quotes(run):
        qs = _branch_pick(run, a.regs['quotation'].scope(run.request), 'branch').filter(date__gte=run.date_from, date__lte=run.date_to).select_related('owner')
        data = defaultdict(lambda: {'quotations': 0, 'value': 0.0, 'accepted': 0, 'accepted_value': 0.0, 'rejected': 0, 'open': 0, 'discount': 0.0})
        for q in qs:
            k = ' '.join(x for x in (getattr(q.owner, 'emp_first_name', ''), getattr(q.owner, 'emp_last_name', '')) if x) or 'Unassigned'
            d_ = data[k]
            d_['quotations'] += 1
            d_['value'] += float(q.total or 0)
            d_['discount'] += float(q.discount_total or 0)
            if q.status == 'accepted':
                d_['accepted'] += 1
                d_['accepted_value'] += float(q.total or 0)
            elif q.status in ('rejected', 'expired', 'cancelled'):
                d_['rejected'] += 1
            else:
                d_['open'] += 1
        rows = [{'agent': k, **{x: round(y, 2) for x, y in v.items()}, 'hit_rate': round(100 * v['accepted'] / (v['accepted'] + v['rejected']), 1) if (v['accepted'] + v['rejected']) else 0} for k, v in data.items()]
        rows.sort(key=lambda r: -r['accepted_value'])
        return [('agent', 'Agent', 'text'), ('quotations', 'Quotations', 'number'), ('value', 'Quoted value', 'number'), ('discount', 'Discount given', 'number'),
                ('accepted', 'Accepted', 'number'), ('accepted_value', 'Accepted value', 'number'), ('rejected', 'Rejected / expired', 'number'), ('open', 'Open', 'number'), ('hit_rate', 'Hit rate %', 'number')], rows

    return {'crm-pipeline': ('Sales pipeline by stage', 'CRM', 'Open opportunities and their value per pipeline stage.', pipeline, None, codes),
            'crm-agents': ('Won and lost by agent', 'CRM', 'Deals closed in the period per agent: won, lost, win rate and won value.', by_agent, 'year', codes),
            'crm-sources': ('Lead source effectiveness', 'CRM', 'Enquiries in the period per lead source with conversion to won deals.', sources, 'year', codes),
            'crm-forecast': ('Sales forecast', 'CRM', 'Open opportunities by expected close month and agent: pipeline value, weighted by stage probability, and likely (≥ 50%).', forecast, None, codes),
            'crm-targets': ('Targets vs actual', 'CRM', 'Sales targets active in the period with the actual figure, gap and attainment %.', targets, 'year', codes),
            'crm-products': ('Sales by product', 'CRM', 'Quoted and ordered quantity and value per product in the period (quotation and sales order lines).', products, 'year', codes),
            'crm-competitors': ('Lost to competitors', 'CRM', 'Opportunities in the period where a competitor was noted: won, lost, win rate and value lost.', competitors, 'year', codes),
            'crm-leads-funnel': ('Lead funnel by source and campaign', 'CRM', 'Leads in the period: qualified, converted, disqualified, conversion % and average score.', leads, 'year', codes),
            'crm-quotations-agent': ('Quotations by agent', 'CRM', 'Quotations in the period per agent: value, discount given, accepted, hit rate.', quotes, 'year', codes)}


def _property(a):
    from ZeoProperty.models import Bill, Lease, Unit
    codes = [a.code(r) for r, _ in a.roles]
    page = SLUG_PAGE['property']

    def occupancy(run):
        qs = _branch_pick(run, a.regs['unit'].scope(run.request), 'branch')
        data = defaultdict(lambda: defaultdict(int))
        for x in qs.values('building__name', 'status').annotate(n=Count('id')):
            data[x['building__name'] or 'No building'][x['status']] += x['n']
        rows = []
        for b, st in sorted(data.items()):
            total = sum(st.values())
            let_ = st.get('let', 0) + st.get('notice', 0)
            rows.append({'building': b, 'units': total, 'let': let_, 'available': st.get('available', 0), 'reserved': st.get('reserved', 0),
                         'sold': st.get('sold', 0), 'occupancy': round(100 * let_ / total, 1) if total else 0})
        return [('building', 'Building', 'text'), ('units', 'Units', 'number'), ('let', 'Let', 'number'), ('available', 'Available', 'number'),
                ('reserved', 'Reserved', 'number'), ('sold', 'Sold', 'number'), ('occupancy', 'Occupancy %', 'number')], rows

    def rent_roll(run):
        qs = _branch_pick(run, a.regs['lease'].scope(run.request), 'branch').filter(status='active').select_related('unit__building', 'party')
        rows = [{'lease': l.number, 'unit': l.unit.label if l.unit_id else '', 'building': l.unit.building.name if l.unit_id and l.unit.building_id else '',
                 'tenant': l.party.name if l.party_id else '', 'start': l.start, 'end': l.end, 'annual_rent': float(getattr(l, 'annual_rent', 0) or getattr(l, 'rent', 0) or 0),
                 'branch': l.branch.branch_name if l.branch_id else '', '_url': f'{page}r/lease/{l.pk}', '_m': l._meta.label, '_id': l.pk} for l in qs]
        return [('lease', 'Lease', 'text'), ('branch', 'Branch', 'text'), ('building', 'Building', 'text'), ('unit', 'Unit', 'text'),
                ('tenant', 'Tenant', 'text'), ('start', 'Start', 'date'), ('end', 'End', 'date'), ('annual_rent', 'Annual rent', 'number')], rows

    def aged(run):
        t = date.today()
        qs = _branch_pick(run, a.regs['bill'].scope(run.request), 'branch').filter(status__in=('issued', 'part_paid')).select_related('party')
        data = defaultdict(lambda: {'current': 0.0, 'd30': 0.0, 'd60': 0.0, 'd90': 0.0, 'over90': 0.0})
        for b in qs:
            bal = float(getattr(b, 'balance', 0) or (float(b.total or 0) - float(b.paid or 0)))
            days = (t - b.due_date).days if b.due_date else 0
            k = b.party.name if b.party_id else 'No tenant / buyer'
            bucket = 'current' if days <= 0 else 'd30' if days <= 30 else 'd60' if days <= 60 else 'd90' if days <= 90 else 'over90'
            data[k][bucket] += bal
        rows = [{'party': k, **v, 'total': round(sum(v.values()), 2)} for k, v in data.items()]
        rows.sort(key=lambda r: -r['total'])
        return [('party', 'Tenant / buyer', 'text'), ('current', 'Not due', 'number'), ('d30', '1–30 days', 'number'), ('d60', '31–60 days', 'number'),
                ('d90', '61–90 days', 'number'), ('over90', 'Over 90 days', 'number'), ('total', 'Total due', 'number')], rows

    # ---------------------------------------------------------------- v1.40.0 property analytics (SRS 24)
    from ZeoProperty.models import Allocation, Commission, Listing, Meter, MeterReading, MoveOut, PurchaseOrder, Refund
    fin = [a.code(r) for r in ('prop_manager', 'prop_finance', 'prop_viewer')]
    LIVE = ('active', 'notice')                    # leases occupying the unit now
    OCC = ('active', 'notice', 'ended', 'renewed', 'terminated', 'amended')   # leases that occupied the unit at some time
    VACANT = ('available', 'maintenance', 'unavailable')
    SOLD = ('sold', 'sold_pending')

    def nm(e):
        return ' '.join(x for x in (getattr(e, 'emp_first_name', ''), getattr(e, 'emp_last_name', '')) if x)

    def scope(run, kind, model):
        """The register's rows the user may see (or every row when the register is not there), limited to the branches picked."""
        return _branch_pick(run, a.regs[kind].scope(run.request) if kind in a.regs else model.objects.all(), 'branch')

    def overlap(a1, b1, a2, b2):
        lo, hi = max(a1, a2), min(b1, b2)
        return max((hi - lo).days + 1, 0)

    def vacancy(run, units):
        """Per unit: (vacant unit-days, vacancy loss, period days) for the run period – leases give the occupied days; a unit without
        a lease record counts by its status (let / notice occupied until `next_available`, sold not counted, the rest vacant)."""
        f, t = run.date_from, run.date_to
        days = (t - f).days + 1
        occ = defaultdict(int)
        for l in Lease.objects.filter(unit__in=units, status__in=OCC, start__lte=t, end__gte=f).values('unit_id', 'start', 'end'):
            occ[l['unit_id']] += overlap(l['start'], l['end'], f, t)
        out = {}
        for u in units:
            if u.status in SOLD:
                out[u.pk] = (0, 0.0, days)
                continue
            o = occ.get(u.pk, 0)
            if not o and u.status in ('let', 'notice', 'reserved'):
                o = days - (overlap(u.next_available, t, f, t) if u.next_available and u.next_available <= t else 0)
            v = max(days - min(o, days), 0)
            out[u.pk] = (v, round(v * float(u.rent or 0) / 365, 2), days)
        return out

    def collected(run, bills, f=None, t=None):
        """Allocation amounts of posted receipts (no credit notes) received in the period, per bill id."""
        qs = Allocation.objects.filter(bill__in=bills, receipt__status='posted', receipt__received_on__gte=f or run.date_from,
                                       receipt__received_on__lte=t or run.date_to).exclude(receipt__method='credit')
        return {x['bill_id']: float(x['v'] or 0) for x in qs.values('bill_id').annotate(v=Sum('amount'))}

    def live_bills(run):
        return scope(run, 'bill', Bill).exclude(status__in=('draft', 'void')).filter(is_credit=False)

    def sqft(l):
        area = float(l.unit.rentable_area or l.unit.area or 0) if l.unit_id else 0
        return round(float(l.annual_rent or 0) / area, 2) if area > 0 else None

    def vacancy_loss(run):
        units = list(scope(run, 'unit', Unit).select_related('building'))
        vac = vacancy(run, units)
        data = defaultdict(lambda: {'units': 0, 'vacant_now': 0, 'unit_days': 0, 'vacant_days': 0, 'loss': 0.0, '_id': None})
        for u in units:
            d_ = data[u.building.name if u.building_id else 'No building']
            v, loss, days = vac[u.pk]
            d_['_id'] = u.building_id
            d_['units'] += 1
            d_['vacant_now'] += u.status in VACANT
            d_['unit_days'] += days
            d_['vacant_days'] += v
            d_['loss'] += loss
        rows = [{'building': b, 'units': v['units'], 'vacant_now': v['vacant_now'], 'unit_days': v['unit_days'], 'vacant_days': v['vacant_days'],
                 'occupancy': round(100 * (1 - v['vacant_days'] / v['unit_days']), 1) if v['unit_days'] else 0, 'loss': round(v['loss'], 2),
                 '_url': f"{page}r/unit?building={v['_id']}&status=available"} for b, v in sorted(data.items())]
        return [('building', 'Building', 'text'), ('units', 'Units', 'number'), ('vacant_now', 'Vacant now', 'number'), ('unit_days', 'Unit-days in period', 'number'),
                ('vacant_days', 'Vacant unit-days', 'number'), ('occupancy', 'Occupancy %', 'number'), ('loss', 'Vacancy loss (asking rent)', 'number')], rows

    def rent_sqft(run):
        leases = scope(run, 'lease', Lease).filter(status__in=LIVE).select_related('unit__building', 'unit__unit_type')
        units = scope(run, 'unit', Unit).filter(status__in=VACANT).select_related('building', 'unit_type')
        data = defaultdict(lambda: {'let': [], 'ask': []})
        for l in leases:
            r = sqft(l)
            if r is not None and l.unit_id:
                for t in (l.unit.unit_type.name if l.unit.unit_type_id else 'No type', 'All types'):
                    data[(l.unit.building.name, t)]['let'].append(r)
        for u in units:
            area = float(u.rentable_area or u.area or 0)
            if area > 0 and u.rent:
                for t in (u.unit_type.name if u.unit_type_id else 'No type', 'All types'):
                    data[(u.building.name, t)]['ask'].append(float(u.rent) / area)
        rows = [{'building': b, 'unit_type': t, 'leases': len(v['let']), 'avg': round(sum(v['let']) / len(v['let']), 2) if v['let'] else None,
                 'min': round(min(v['let']), 2) if v['let'] else None, 'max': round(max(v['let']), 2) if v['let'] else None,
                 'vacant': len(v['ask']), 'asking': round(sum(v['ask']) / len(v['ask']), 2) if v['ask'] else None}
                for (b, t), v in sorted(data.items(), key=lambda x: (x[0][0], x[0][1] != 'All types', x[0][1]))]
        return ([('building', 'Building', 'text'), ('unit_type', 'Unit type', 'text'), ('leases', 'Active leases', 'number'), ('avg', 'Rent / sq ft (average)', 'number'),
                 ('min', 'Rent / sq ft (min)', 'number'), ('max', 'Rent / sq ft (max)', 'number'), ('vacant', 'Vacant units', 'number'), ('asking', 'Asking rent / sq ft (vacant)', 'number')],
                rows, {'no_totals': ('leases', 'avg', 'min', 'max', 'vacant', 'asking')})

    def renewal(run):
        f, t = run.date_from, run.date_to
        qs = scope(run, 'lease', Lease).select_related('unit__building')
        ended = {l.pk: l for l in qs.filter(status__in=('renewed', 'ended', 'terminated', 'amended'), end__gte=f, end__lte=t)}
        renewals = {l.previous_id: l for l in qs.filter(change='renewal', previous__isnull=False, start__gte=f, start__lte=t).exclude(status='cancelled')}
        for l in qs.filter(pk__in=[k for k in renewals if k not in ended]):
            ended[l.pk] = l
        renewals.update({l.previous_id: l for l in Lease.objects.filter(previous_id__in=list(ended), change='renewal').exclude(status='cancelled') if l.previous_id not in renewals})
        data = defaultdict(lambda: {'expired': 0, 'renewed': 0, 'left': 0, 'changes': [], 'renewed_rent': 0.0, 'lost_rent': 0.0, '_id': None})
        for l in ended.values():
            d_ = data[l.unit.building.name if l.unit_id else 'No building']
            d_['_id'] = l.unit.building_id if l.unit_id else None
            d_['expired'] += 1
            n = renewals.get(l.pk)
            if n is not None or l.status == 'renewed':
                d_['renewed'] += 1
                if n is not None:
                    d_['renewed_rent'] += float(n.annual_rent or 0)
                    if l.annual_rent:
                        d_['changes'].append(100 * (float(n.annual_rent or 0) - float(l.annual_rent)) / float(l.annual_rent))
            else:
                d_['left'] += 1
                d_['lost_rent'] += float(l.annual_rent or 0)
        rows = [{'building': b, 'expired': v['expired'], 'renewed': v['renewed'], 'left': v['left'], 'rate': round(100 * v['renewed'] / v['expired'], 1) if v['expired'] else 0,
                 'change': round(sum(v['changes']) / len(v['changes']), 1) if v['changes'] else None, 'renewed_rent': round(v['renewed_rent'], 2), 'lost_rent': round(v['lost_rent'], 2),
                 '_url': f"{page}r/lease?status=renewed"} for b, v in sorted(data.items())]
        return [('building', 'Building', 'text'), ('expired', 'Leases ended', 'number'), ('renewed', 'Renewed', 'number'), ('left', 'Left', 'number'), ('rate', 'Renewal %', 'number'),
                ('change', 'Rent change on renewal %', 'number'), ('renewed_rent', 'Renewed annual rent', 'number'), ('lost_rent', 'Annual rent lost', 'number')], rows

    def months(f, t):
        d = f.replace(day=1)
        while d <= t:
            nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
            yield d, min(nxt - timedelta(days=1), t)
            d = nxt

    def collection(run):
        f, t = run.date_from, run.date_to
        bills = list(live_bills(run).filter(due_date__lte=t).values('id', 'due_date', 'total'))
        allocs = defaultdict(list)
        for x in Allocation.objects.filter(bill_id__in=[b['id'] for b in bills], receipt__status='posted').values('bill_id', 'amount', 'receipt__received_on', 'receipt__method'):
            allocs[x['bill_id']].append((x['receipt__received_on'], float(x['amount'] or 0), x['receipt__method']))
        rows = []
        for ms, me in months(f, t):
            billed = sum(float(b['total'] or 0) for b in bills if ms <= b['due_date'] <= me)
            got = sum(v for al in allocs.values() for d, v, m in al if ms <= d <= me and m != 'credit')
            overdue = sum(max(float(b['total'] or 0) - sum(v for d, v, m in allocs.get(b['id'], ()) if d <= me), 0) for b in bills if b['due_date'] <= me)
            rows.append({'month': ms.strftime('%Y-%m'), 'billed': round(billed, 2), 'collected': round(got, 2), 'efficiency': round(100 * got / billed, 1) if billed else None,
                         'overdue': round(overdue, 2), '_url': f'{page}r/bill?status=issued'})
        return ([('month', 'Month', 'text'), ('billed', 'Billed (due in month)', 'number'), ('collected', 'Collected', 'number'), ('efficiency', 'Collection efficiency %', 'number'),
                 ('overdue', 'Overdue at month end', 'number')], rows, {'no_totals': ('overdue',)})

    def deposits(run):
        qs = scope(run, 'lease', Lease).filter(status__in=('active', 'notice', 'ended', 'terminated')).select_related('unit__building', 'party')
        ids = [l.pk for l in qs]
        dep = defaultdict(lambda: [0.0, 0.0])
        for x in Bill.objects.filter(lease_id__in=ids, charge__kind='deposit', is_credit=False).exclude(status__in=('draft', 'void')).values('lease_id').annotate(b=Sum('total'), p=Sum('paid')):
            dep[x['lease_id']] = [float(x['b'] or 0), float(x['p'] or 0)]
        mo = {}
        for m in MoveOut.objects.filter(lease_id__in=ids).exclude(status='cancelled').order_by('move_out_on'):
            mo[m.lease_id] = m
        rf = defaultdict(lambda: {'paid': 0.0, 'status': ''})
        for r in Refund.objects.filter(lease_id__in=ids, kind='deposit').exclude(status='rejected').order_by('id'):
            rf[r.lease_id]['status'] = r.get_status_display()
            if r.status == 'paid':
                rf[r.lease_id]['paid'] += float(r.amount or 0)
        rows = []
        for l in qs:
            billed, paid = dep[l.pk]
            m = mo.get(l.pk)
            ded = float(m.deductions or 0) if m else 0.0
            refund_due = float(m.refund or 0) if m else 0.0
            held = round(paid - ded - rf[l.pk]['paid'], 2)
            rows.append({'lease': l.number, 'building': l.unit.building.name if l.unit_id else '', 'unit': l.unit.unit_no if l.unit_id else '', 'tenant': l.party.name if l.party_id else '',
                         'status': l.get_status_display(), 'deposit': float(l.deposit or 0), 'billed': billed, 'paid': paid, 'deductions': ded, 'refund_due': refund_due,
                         'refunded': rf[l.pk]['paid'], 'refund_status': rf[l.pk]['status'] or (m.get_status_display() if m else ''), 'held': held,
                         '_url': f'{page}r/lease/{l.pk}', '_m': l._meta.label, '_id': l.pk})
        if rows:
            rows.append({'lease': 'Total', **{k: round(sum(r[k] for r in rows), 2) for k in ('deposit', 'billed', 'paid', 'deductions', 'refund_due', 'refunded', 'held')}})
        return ([('lease', 'Lease', 'text'), ('building', 'Building', 'text'), ('unit', 'Unit', 'text'), ('tenant', 'Tenant', 'text'), ('status', 'Lease status', 'text'),
                 ('deposit', 'Deposit (lease)', 'number'), ('billed', 'Deposit billed', 'number'), ('paid', 'Deposit paid', 'number'), ('deductions', 'Deductions', 'number'),
                 ('refund_due', 'Refund due (move-out)', 'number'), ('refunded', 'Refunded', 'number'), ('refund_status', 'Refund status', 'text'), ('held', 'Liability held', 'number')],
                rows, {'no_totals': ('deposit', 'billed', 'paid', 'deductions', 'refund_due', 'refunded', 'held'), 'filters': ['building', 'status', 'refund_status']})

    def commissions(run):
        qs = scope(run, 'commission', Commission).filter(Q(created_at__date__gte=run.date_from, created_at__date__lte=run.date_to) | Q(created_at__isnull=True)).select_related('agent', 'broker')
        data = defaultdict(lambda: {'deals': 0, 'pending': 0.0, 'approved': 0.0, 'paid': 0.0, 'rejected': 0.0, 'reversed': 0.0, '_url': ''})
        for c in qs:
            if c.broker_id:
                k, kind, url = c.broker.name, 'Broker', f'{page}r/commission?broker={c.broker_id}'
            else:
                k, kind, url = nm(c.agent) or 'Unassigned', 'Agent', f'{page}r/commission?agent={c.agent_id}' if c.agent_id else f'{page}r/commission'
            d_ = data[(k, kind)]
            d_['deals'] += 1
            d_['_url'] = url
            d_['pending' if c.status in ('waiting', 'pending') else c.status] += float(c.amount or 0)
        rows = [{'name': k, 'kind': kind, **{x: (round(y, 2) if isinstance(y, float) else y) for x, y in v.items()}, 'total': round(v['pending'] + v['approved'] + v['paid'], 2)}
                for (k, kind), v in data.items()]
        rows.sort(key=lambda r: -r['total'])
        return [('name', 'Agent / broker', 'text'), ('kind', 'Kind', 'text'), ('deals', 'Deals', 'number'), ('pending', 'Pending', 'number'), ('approved', 'Approved', 'number'),
                ('paid', 'Paid', 'number'), ('reversed', 'Reversed', 'number'), ('rejected', 'Rejected', 'number'), ('total', 'Earned (pending + approved + paid)', 'number')], rows

    def noi_rows(run, groups):
        """groups: {name: (units, valuation, url)} → NOI rows for the period."""
        f, t = run.date_from, run.date_to
        days = (t - f).days + 1
        bills = live_bills(run)
        rows = []
        for name, (units, valuation, url) in sorted(groups.items()):
            ub = bills.filter(unit__in=units)
            rent = sum(collected(run, ub.filter(party__isnull=False, charge__kind__in=('rent', 'service', 'utility'))).values())
            exp = float(ub.filter(owner__isnull=False, charge__kind__in=('maintenance', 'service'), due_date__gte=f, due_date__lte=t).aggregate(v=Sum('total'))['v'] or 0)
            po = 0.0
            for p in PurchaseOrder.objects.filter(status__in=('received', 'invoiced', 'paid'), charge_to='company').filter(Q(unit__in=units) | Q(building__units__in=units)).distinct():
                d = p.ordered_on or (p.created_at.date() if p.created_at else None)
                if d is None or f <= d <= t:
                    po += float(p.invoice_amount if p.invoice_amount is not None else p.total or 0)
            noi = rent - exp - po
            annual = noi * 365 / days if days else noi
            rows.append({'name': name, 'units': len(units), 'rent': round(rent, 2), 'expenses': round(exp, 2), 'purchases': round(po, 2), 'noi': round(noi, 2),
                         'annualised': round(annual, 2), 'valuation': round(valuation, 2) if valuation else None,
                         'yield': round(100 * annual / valuation, 2) if valuation else None, '_url': url})
        return rows

    def noi(run):
        units = list(scope(run, 'unit', Unit).select_related('building'))
        groups = {}
        for u in units:
            b = u.building
            g = groups.setdefault(b.name, [[], 0.0, f'{page}r/building/{b.pk}', b])
            g[0].append(u)
            g[1] += float(u.valuation or 0)
        rows = noi_rows(run, {k: (g[0], g[1] or float(g[3].valuation or 0), g[2]) for k, g in groups.items()})
        for r in rows:
            r['building'] = r.pop('name')
        return ([('building', 'Building', 'text'), ('units', 'Units', 'number'), ('rent', 'Rent collected', 'number'), ('expenses', 'Owner-side expenses', 'number'),
                 ('purchases', 'Vendor purchases (company)', 'number'), ('noi', 'NOI', 'number'), ('annualised', 'NOI annualised', 'number'), ('valuation', 'Valuation', 'number'),
                 ('yield', 'Yield %', 'number')], rows, {'no_totals': ('yield',)})

    def portfolio(run):
        units = list(scope(run, 'unit', Unit).select_related('building__project'))
        vac = vacancy(run, units)
        leases = scope(run, 'lease', Lease).filter(status__in=LIVE).select_related('unit')
        bills = live_bills(run)
        got = collected(run, bills)
        today = date.today()
        over = {x['unit_id']: float(x['v'] or 0) for x in bills.filter(status__in=('issued', 'part_paid'), due_date__lt=today).values('unit_id').annotate(v=Sum('total') - Sum('paid'))}
        bill_unit = {x['id']: x['unit_id'] for x in bills.filter(id__in=got).values('id', 'unit_id')}
        groups = defaultdict(lambda: {'units': [], 'let': 0, 'rent_roll': 0.0, 'collected': 0.0, 'overdue': 0.0, 'loss': 0.0, 'sqft': [], 'valuation': 0.0, '_url': ''})
        by_unit = {}
        for u in units:
            b = u.building
            k = b.project.name if b.project_id else b.name
            g = groups[k]
            g['_url'] = f'{page}r/project/{b.project_id}' if b.project_id else f'{page}r/building/{b.pk}'
            g['units'].append(u)
            g['let'] += u.status in ('let', 'notice')
            g['overdue'] += over.get(u.pk, 0)
            g['loss'] += vac[u.pk][1]
            by_unit[u.pk] = k
        for bid, v in got.items():
            k = by_unit.get(bill_unit.get(bid))
            if k:
                groups[k]['collected'] += v
        for l in leases:
            k = by_unit.get(l.unit_id)
            if k:
                groups[k]['rent_roll'] += float(l.annual_rent or 0)
                r = sqft(l)
                if r is not None:
                    groups[k]['sqft'].append(r)
        rows = [{'project': k, 'units': len(g['units']), 'occupancy': round(100 * g['let'] / len(g['units']), 1) if g['units'] else 0, 'rent_roll': round(g['rent_roll'], 2),
                 'collected': round(g['collected'], 2), 'overdue': round(g['overdue'], 2), 'loss': round(g['loss'], 2),
                 'sqft': round(sum(g['sqft']) / len(g['sqft']), 2) if g['sqft'] else None, '_url': g['_url']} for k, g in sorted(groups.items())]
        return ([('project', 'Project / building', 'text'), ('units', 'Units', 'number'), ('occupancy', 'Occupancy %', 'number'), ('rent_roll', 'Annual rent roll', 'number'),
                 ('collected', 'Collected in period', 'number'), ('overdue', 'Overdue', 'number'), ('loss', 'Vacancy loss', 'number'), ('sqft', 'Average rent / sq ft', 'number')],
                rows, {'no_totals': ('sqft',)})

    def consumption(run):
        meters = scope(run, 'meter', Meter).select_related('building', 'unit__building')
        rd = {x['meter_id']: x for x in MeterReading.objects.filter(meter__in=meters, read_on__gte=run.date_from, read_on__lte=run.date_to).values('meter_id').annotate(
            n=Count('id'), c=Sum('consumption'), v=Sum('amount', filter=Q(status='billed')), u=Count('id', filter=Q(unusual=True)), last=Max('read_on'))}
        rows = []
        for m in meters:
            x = rd.get(m.pk)
            if x is None:
                continue
            b = m.building or (m.unit.building if m.unit_id else None)
            rows.append({'building': b.name if b else '', 'unit': m.unit.unit_no if m.unit_id else ('Shared' if m.shared else 'Common area'), 'kind': m.get_kind_display(), 'meter': m.meter_no,
                         'readings': x['n'], 'consumption': float(x['c'] or 0), 'unit_name': m.unit_name, 'amount': float(x['v'] or 0), 'unusual': x['u'], 'last_read': x['last'],
                         '_url': f'{page}r/meter/{m.pk}', '_m': m._meta.label, '_id': m.pk})
        rows.sort(key=lambda r: (r['building'], r['unit'], r['kind']))
        return ([('building', 'Building', 'text'), ('unit', 'Unit', 'text'), ('kind', 'Kind', 'text'), ('meter', 'Meter no.', 'text'), ('readings', 'Readings', 'number'),
                 ('consumption', 'Consumption', 'number'), ('unit_name', 'Unit', 'text'), ('amount', 'Amount billed', 'number'), ('unusual', 'Unusual readings', 'number'),
                 ('last_read', 'Last reading', 'date')], rows, {'filters': ['building', 'kind']})

    def listings(run):
        today = date.today()
        rows = []
        for l in scope(run, 'listing', Listing).exclude(status='draft').select_related('unit__building', 'agent'):
            days = None
            if l.publish_from and l.status in ('published', 'withdrawn'):
                days = max((min(l.publish_to or today, today) - l.publish_from).days + 1, 0)
            rows.append({'listing': l.number, 'title': l.title, 'unit': l.unit.label if l.unit_id else '', 'kind': l.get_kind_display(), 'status': l.get_status_display(),
                         'price': float(l.price or 0), 'published_on': l.publish_from, 'days': days, 'views': l.views, 'enquiries': l.enquiries,
                         'enquiry_rate': round(100 * l.enquiries / l.views, 1) if l.views else None, 'price_changes': len(l.price_history or []),
                         'unit_status': l.unit.get_status_display() if l.unit_id else '', 'agent': nm(l.agent), 'channels': ', '.join(l.channels or []),
                         '_url': f'{page}r/listing/{l.pk}', '_m': l._meta.label, '_id': l.pk})
        rows.sort(key=lambda r: -(r['enquiries'] or 0))
        return ([('listing', 'Listing', 'text'), ('title', 'Title', 'text'), ('unit', 'Unit', 'text'), ('kind', 'For', 'text'), ('status', 'Status', 'text'), ('price', 'Price', 'number'),
                 ('published_on', 'Published on', 'date'), ('days', 'Days published', 'number'), ('views', 'Views', 'number'), ('enquiries', 'Enquiries', 'number'),
                 ('enquiry_rate', 'Enquiries per 100 views', 'number'), ('price_changes', 'Price changes', 'number'), ('unit_status', 'Unit status', 'text'), ('agent', 'Agent', 'text'),
                 ('channels', 'Channels', 'text')], rows, {'no_totals': ('price', 'days', 'enquiry_rate'), 'filters': ['status', 'kind', 'unit_status', 'agent']})

    return {'prop-occupancy': ('Occupancy by building', 'Property', 'Units per building by status and occupancy %.', occupancy, None, codes),
            'prop-rent-roll': ('Rent roll', 'Property', 'Every active lease with tenant, dates and annual rent.', rent_roll, None, codes),
            'prop-aged': ('Aged receivables', 'Property', 'Unpaid billing records per tenant / buyer by days overdue.', aged, None, codes),
            # v1.40.0 analytics
            'prop-vacancy': ('Vacancy loss', 'Property', 'Vacant unit-days per building in the period, occupancy % and the rent lost at the asking rent.', vacancy_loss, 'year', codes),
            'prop-rent-sqft': ('Rent per sq ft', 'Property', 'Annual rent per rentable sq ft of the active leases per building and unit type (average, min, max) and the asking rent of the vacant units.', rent_sqft, None, codes),
            'prop-renewal': ('Renewal rate', 'Property', 'Leases that ended in the period per building: renewed vs left, renewal % and the average rent change on renewal.', renewal, 'year', codes),
            'prop-collection': ('Collection efficiency', 'Property', 'Per month: billed (due in the month) vs collected, efficiency % and the overdue amount at month end.', collection, 'year', fin),
            'prop-deposits': ('Deposit register', 'Property', 'Security deposit per lease: billed, paid, move-out deductions, refund and the liability held.', deposits, None, fin),
            'prop-commissions': ('Commission report', 'Property', 'Commissions raised in the period per agent and external broker: pending, approved, paid, reversed.', commissions, 'year',
                                 [a.code(r) for r in ('prop_manager', 'prop_commission', 'prop_finance', 'prop_viewer')]),
            'prop-noi': ('NOI and yield', 'Property', 'Per building: rent collected less owner-side expenses and company purchases = net operating income; annualised and as yield % on the valuation.', noi, 'year', fin),
            'prop-portfolio': ('Portfolio comparison', 'Property', 'One row per project (or building): units, occupancy, rent roll, collected, overdue, vacancy loss and rent per sq ft.', portfolio, 'year', codes),
            'prop-consumption': ('Utility consumption', 'Property', 'Per meter: readings in the period, consumption, amount billed and unusual readings.', consumption, 'year', codes),
            'prop-listings': ('Listings performance', 'Property', 'Every listing: days published, views, enquiries, price changes and the unit status.', listings, None, codes)}


def _maintenance(a):
    codes = [a.code(r) for r, _ in a.roles]

    def sla(run):
        qs = _branch_pick(run, a.regs['work_order'].scope(run.request), 'branch').filter(created_at__date__gte=run.date_from, created_at__date__lte=run.date_to)
        data = defaultdict(lambda: {'orders': 0, 'closed': 0, 'on_time': 0, 'cost': 0.0})
        for w in qs.select_related('category'):
            k = w.category.name if w.category_id else 'No category'
            d = data[k]
            d['orders'] += 1
            if w.status in ('done', 'closed'):
                d['closed'] += 1
                if getattr(w, 'done_at', None) and w.resolve_due and w.done_at <= w.resolve_due:
                    d['on_time'] += 1
            d['cost'] += float(getattr(w, 'cost', 0) or 0)
        rows = [{'category': k, **v, 'on_time_pct': round(100 * v['on_time'] / v['closed'], 1) if v['closed'] else 0} for k, v in data.items()]
        rows.sort(key=lambda r: -r['orders'])
        return [('category', 'Category', 'text'), ('orders', 'Work orders', 'number'), ('closed', 'Closed', 'number'),
                ('on_time_pct', 'Fixed on time %', 'number'), ('cost', 'Cost', 'number')], rows

    def technicians(run):
        qs = _branch_pick(run, a.regs['work_order'].scope(run.request), 'branch').filter(created_at__date__gte=run.date_from, created_at__date__lte=run.date_to)
        data = defaultdict(lambda: {'assigned': 0, 'closed': 0, 'open': 0})
        for w in qs.select_related('technician'):
            t = w.technician
            k = ' '.join(x for x in (getattr(t, 'emp_first_name', ''), getattr(t, 'emp_last_name', '')) if x) or (w.vendor.name if getattr(w, 'vendor_id', None) else 'Not assigned')
            data[k]['assigned'] += 1
            data[k]['closed' if w.status in ('done', 'closed') else 'open'] += 1
        rows = sorted(({'technician': k, **v} for k, v in data.items()), key=lambda r: -r['assigned'])
        return [('technician', 'Technician / vendor', 'text'), ('assigned', 'Work orders', 'number'), ('closed', 'Closed', 'number'), ('open', 'Open', 'number')], rows

    return {'mnt-sla': ('Work orders by category and SLA', 'Maintenance', 'Work orders raised in the period per category: closed, fixed on time and cost.', sla, 'year', codes),
            'mnt-technicians': ('Technician workload', 'Maintenance', 'Work orders raised in the period per technician or vendor.', technicians, 'year', codes)}


def _people(a):
    from ZeoPeople.models import Enrolment, Recognition, Survey, SurveyResponse, Review360
    codes = [a.code(r) for r, _ in a.roles]
    page = SLUG_PAGE['people']

    def benefits_cost(run):
        qs = _branch_pick(run, a.regs['enrolment'].scope(run.request), 'branch').filter(status='active').select_related('plan')
        data = defaultdict(lambda: {'enrolled': 0, 'employee_share': 0.0, 'employer_cost': 0.0})
        for e in qs:
            d_ = data[e.plan.name]
            d_['enrolled'] += 1
            d_['employee_share'] += float(e.employee_cost or 0)
            d_['employer_cost'] += float(e.plan.employer_cost or 0)
        rows = [{'plan': k, **{x: round(y, 2) for x, y in v.items()}, 'total_month': round(v['employee_share'] + v['employer_cost'], 2), 'total_year': round(12 * (v['employee_share'] + v['employer_cost']), 2)} for k, v in data.items()]
        return [('plan', 'Plan', 'text'), ('enrolled', 'Enrolled', 'number'), ('employee_share', 'Employee share / month', 'number'), ('employer_cost', 'Employer cost / month', 'number'),
                ('total_month', 'Total / month', 'number'), ('total_year', 'Total / year', 'number')], rows

    def engagement(run):
        rows = []
        for s in _branch_pick(run, a.regs['survey'].scope(run.request), 'branch').exclude(status='draft'):
            rs = list(s.responses.all())
            favs = []
            for i, q in enumerate(s.questions or []):
                if q.get('type') == 'rating':
                    nums = [float(r.answers[i]) for r in rs if i < len(r.answers or []) and str(r.answers[i]).replace('.', '').isdigit()]
                    if nums:
                        favs.append(100 * sum(1 for n in nums if n >= 4) / len(nums))
            from ZeoPeople.app import audience
            tot = audience(s).count()
            rows.append({'survey': s.title, 'kind': s.get_kind_display(), 'status': s.get_status_display(), 'responses': len(rs), 'invited': tot,
                         'rate': round(100 * len(rs) / tot, 1) if tot else 0, 'engagement': round(sum(favs) / len(favs)) if favs else None,
                         '_url': f'{page}survey/{s.pk}/results', '_m': s._meta.label, '_id': s.pk})
        return [('survey', 'Survey', 'text'), ('kind', 'Kind', 'text'), ('status', 'Status', 'text'), ('responses', 'Responses', 'number'), ('invited', 'Invited', 'number'),
                ('rate', 'Response %', 'number'), ('engagement', 'Favourable %', 'number')], rows

    def leaders(run):
        qs = _branch_pick(run, a.regs['recognition'].scope(run.request), 'branch').filter(status='published', created_at__date__gte=run.date_from, created_at__date__lte=run.date_to)
        data = defaultdict(lambda: {'received': 0, 'points': 0, 'given': 0})
        for r in qs.select_related('employee', 'given_by'):
            k = ' '.join(x for x in (getattr(r.employee, 'emp_first_name', ''), getattr(r.employee, 'emp_last_name', '')) if x)
            data[k]['received'] += 1
            data[k]['points'] += r.points
            if r.given_by_id:
                g = ' '.join(x for x in (getattr(r.given_by, 'emp_first_name', ''), getattr(r.given_by, 'emp_last_name', '')) if x)
                data[g]['given'] += 1
        rows = sorted(({'employee': k, **v} for k, v in data.items()), key=lambda r: -r['points'])
        return [('employee', 'Employee', 'text'), ('received', 'Recognitions received', 'number'), ('points', 'Points', 'number'), ('given', 'Given to others', 'number')], rows

    def reviews(run):
        qs = _branch_pick(run, a.regs['review_360'].scope(run.request), 'branch').filter(status__in=('closed', 'shared')).select_related('employee')
        rows = []
        for r in qs:
            comps = (r.summary or {}).get('competencies', [])
            low = sorted([c for c in comps if c.get('avg')], key=lambda c: c['avg'])[:2]
            high = sorted([c for c in comps if c.get('avg')], key=lambda c: -c['avg'])[:2]
            rows.append({'employee': ' '.join(x for x in (r.employee.emp_first_name, r.employee.emp_last_name) if x), 'cycle': r.cycle_name, 'overall': float(r.overall or 0),
                         'strengths': ', '.join(c['name'] for c in high), 'to_develop': ', '.join(c['name'] for c in low), 'reviewers': r.reviewers.filter(status='done').count(),
                         '_url': f'{page}r/review_360/{r.pk}', '_m': r._meta.label, '_id': r.pk})
        rows.sort(key=lambda x: -x['overall'])
        return [('employee', 'Employee', 'text'), ('cycle', 'Cycle', 'text'), ('overall', 'Overall (1–5)', 'number'), ('strengths', 'Strengths', 'text'), ('to_develop', 'To develop', 'text'), ('reviewers', 'Reviewers', 'number')], rows

    return {'ppl-benefits-cost': ('Benefits cost by plan', 'People', 'Active enrolments per plan with the employee share and employer cost per month and year.', benefits_cost, None, codes),
            'ppl-engagement': ('Survey engagement', 'People', 'Every survey with response rate and favourable % (ratings of 4 and 5).', engagement, None, codes),
            'ppl-recognition-leaders': ('Recognition leaders', 'People', 'Recognitions and points per employee in the period, and how many each person gave.', leaders, 'year', codes),
            'ppl-360-summary': ('360° review summary', 'People', 'Closed reviews with the overall score, strongest and weakest competencies.', reviews, None, codes)}


KIT_EXTRA = {'crm': _crm, 'property': _property, 'maintenance': _maintenance, 'people': _people}


# ------------------------------------------------------------------ HSE
def hse_reports():
    out = {}
    try:
        from ZeoHSE import access as A
        from ZeoHSE import views as V
        from ZeoHSE.registers import REGS, field_of, ftype, value
        from ZeoHSE import rows as RW
        from ZeoHSE.models import Incident
    except Exception:
        return out
    codes = sorted(A.STAFF_CODES | {'hse_senior_mgmt'})
    page = SLUG_PAGE['hse']

    def main(kind, scope, fields, label_fn, link, date_field):
        def fn(run):
            qs = _branch_pick(run, scope(run.request), A.bpath(scope(run.request).model))
            qs, _ = _period(run, qs, qs.model)
            rows = []
            for o in qs[:5000]:
                r = label_fn(o)
                r.update({'_url': page + link.format(id=o.pk), '_m': o._meta.label, '_id': o.pk})
                rows.append(r)
            return fields, rows
        return fn

    def site(o):
        return o.site.name if getattr(o, 'site_id', None) else ''

    def branch(o):
        return o.site.branch.branch_name if getattr(o, 'site_id', None) and o.site.branch_id else ''

    out['hse-incidents'] = ('Incident register', 'HSE', 'Incidents and near misses in the period with category, severity, treatment and status.',
                            main('incidents', V.incident_scope, [('number', 'Incident', 'text'), ('title', 'Title', 'text'), ('branch', 'Branch', 'text'),
                                 ('site', 'Site', 'text'), ('occurred', 'Occurred', 'date'), ('category', 'Category', 'text'), ('treatment', 'Treatment', 'text'),
                                 ('lost_days', 'Lost days', 'number'), ('high_potential', 'High potential', 'text'), ('status', 'Status', 'text')],
                                 lambda o: {'number': o.number, 'title': o.title, 'branch': branch(o), 'site': site(o), 'occurred': o.occurred_at.date() if o.occurred_at else None,
                                            'category': o.category.name if o.category_id else 'Not classified', 'treatment': o.get_treatment_display(),
                                            'lost_days': o.lost_days or 0, 'high_potential': 'Yes' if o.high_potential else 'No', 'status': o.get_status_display()},
                                 'incidents/{id}', 'occurred_at'), 'year', codes)
    out['hse-permits'] = ('Permit register', 'HSE', 'Permits to work in the period with type, site, validity and status.',
                          main('permits', V.permit_scope, [('number', 'Permit', 'text'), ('type', 'Type', 'text'), ('branch', 'Branch', 'text'), ('site', 'Site', 'text'),
                               ('valid_from', 'Valid from', 'date'), ('valid_to', 'Valid to', 'date'), ('status', 'Status', 'text')],
                               lambda o: {'number': o.number, 'type': o.permit_type.name if o.permit_type_id else '', 'branch': branch(o), 'site': site(o),
                                          'valid_from': o.valid_from.date() if o.valid_from else None, 'valid_to': o.valid_to.date() if o.valid_to else None,
                                          'status': o.get_status_display()}, 'permits/{id}', 'created_at'), 'year', codes)
    out['hse-actions'] = ('Corrective actions (CAPA)', 'HSE', 'Actions with owner, due date, days late and status.',
                          main('actions', V.action_scope, [('number', 'Action', 'text'), ('title', 'Title', 'text'), ('branch', 'Branch', 'text'),
                               ('owner', 'Owner', 'text'), ('due', 'Due', 'date'), ('priority', 'Priority', 'text'), ('status', 'Status', 'text')],
                               lambda o: {'number': o.number, 'title': o.title, 'branch': branch(o),
                                          'owner': ' '.join(x for x in (getattr(o.owner, 'emp_first_name', ''), getattr(o.owner, 'emp_last_name', '')) if x),
                                          'due': o.due_date, 'priority': o.get_priority_display(), 'status': o.get_status_display()},
                               'actions?open={id}', 'due_date'), 'year', codes)

    def monthly(run):
        qs = _branch_pick(run, V.incident_scope(run.request), 'site__branch').filter(occurred_at__date__gte=run.date_from, occurred_at__date__lte=run.date_to)
        data = defaultdict(lambda: {'incidents': 0, 'near_misses': 0, 'recordable': 0, 'lti': 0, 'lost_days': 0})
        for i in qs.select_related('category'):
            m = i.occurred_at.strftime('%Y-%m')
            d = data[m]
            d['incidents'] += 1
            d['near_misses'] += 1 if i.category_id and i.category.kind == 'near_miss' else 0
            d['recordable'] += 1 if i.treatment in ('medical', 'restricted', 'lost_time', 'fatality') else 0
            d['lti'] += 1 if i.treatment in ('lost_time', 'fatality') else 0
            d['lost_days'] += i.lost_days or 0
        rows = [{'month': k, **v} for k, v in sorted(data.items())]
        return [('month', 'Month', 'text'), ('incidents', 'Incidents', 'number'), ('near_misses', 'Near misses', 'number'),
                ('recordable', 'Recordable', 'number'), ('lti', 'Lost-time injuries', 'number'), ('lost_days', 'Lost days', 'number')], rows
    out['hse-monthly'] = ('HSE performance by month', 'HSE', 'Incidents, near misses, recordable cases, lost-time injuries and lost days per month.', monthly, 'year', codes)

    for r in REGS.values():
        if r.group == 'Setup' or r.child_of:
            continue
        bp = A.bpath(r.model)
        out[f'hse-r-{r.kind}'] = (r.plural, 'HSE', f'Every {r.label.lower()} you may see, with its status and branch. Opens the record.',
                                  register_report('hse', page, r, value, ftype, field_of, bp), 'year' if _has_date(r.model) else None, codes)
    return out


# ------------------------------------------------------------------ Projects (its 14 reports, in the report centre)
def projects_reports():
    out = {}
    try:
        from ZeoProjects import access as A
        from ZeoProjects import reports as REP
    except Exception:
        return out
    page = SLUG_PAGE['projects']

    def adapter(key):
        def fn(run):
            req = run.request
            if getattr(run, 'branches', None) and len(run.branches) == 1:
                req._zeo_dash_branch = run.branches[0]
            res = REP.run(req, key)
            cols = [(c['key'], c['label'], {'number': 'number', 'money': 'number', 'hours': 'number', 'date': 'date', 'datetime': 'date'}.get(c.get('type'), 'text'))
                    for c in res.get('columns', [])]
            rows = []
            for r in res.get('rows', []):
                x = {k: (_num(v) if not isinstance(v, (dict, list)) else str(v)) for k, v in r.items() if k != 'link_to'}
                lt = r.get('link_to')
                if isinstance(lt, dict) and lt.get('page'):
                    q = '&'.join(f'{k}={v}' for k, v in (lt.get('query') or {}).items() if v not in (None, ''))
                    x['_url'] = f"{page}{lt['page']}" + (f'?{q}' if q else '')
                rows.append(x)
            return cols, rows
        return fn

    for k, g, title, desc, fl, right, _ in REP.REPORTS:
        rights = (right,) if isinstance(right, str) else (right or ('ticket_all', 'ticket_work', 'project_view', 'project_edit', 'dashboard'))
        codes = sorted({c for r in rights for c in A.R.get(r, ())})
        out[f'pj-{k}'] = (title, 'Projects', desc, adapter(k), 'month' if 'period' in fl else None, codes)
    return out


def all_reports():
    out = {}
    for fn in (kit_reports, hse_reports, projects_reports):
        try:
            out.update(fn())
        except Exception:
            log.exception('add-on reports %s', fn.__name__)
    return out
