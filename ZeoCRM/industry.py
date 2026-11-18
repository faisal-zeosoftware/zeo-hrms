"""v1.67.27 – CRM industry release 1: industry fields, several industries per company, stage rules, industry reports.

* Industry fields (Setup › Industry fields): fields the company adds to leads, opportunities, quotations and sales orders, per industry,
  without programming. The kit shows them on the forms (under their section), in the lists (when marked), in the filters, the record
  page, the history, the Excel export and – when marked – on the quotation PDF. Values live in the record's `extra` JSON.
* Several industries per company: every industry pack installed in CRM (Setup › Industry pack) is an industry the records can have.
  A lead has an industry; converting it gives the opportunity the industry and the values; the opportunity's pipeline decides its
  industry when the pipeline belongs to one; quotations and sales orders take it (and the values) from the opportunity / quotation.
* Stage rules: a field "required from stage X" must be filled before the opportunity moves to X or later in that pipeline, or is won.
* Industry reports (Setup › Industry reports): grouped analyses in the report centre (Reports › CRM – <industry>) and on the CRM home.
"""
import logging
import re
from collections import OrderedDict, defaultdict
from datetime import date, datetime

from django.db.models import Q

from zeo.appkit.engine import (DYN, Act, AppError, Reg, dyn_applies, dyn_defs, dyn_missing, dyn_raw, dyn_show, emp_name, in_branches, linked)

from .models import (REPORT_SOURCES, IndustryField, IndustryReport, Lead, Opportunity, Pipeline, Quotation, SalesOrder, Stage)

log = logging.getLogger(__name__)
ON = {'lead': 'on_lead', 'opportunity': 'on_opportunity', 'quotation': 'on_quotation', 'sales_order': 'on_order', 'enquiry': 'on_enquiry'}
CODE_RE = re.compile(r'^[a-z][a-z0-9_]{1,39}$')
GROUPS = ('stage', 'pipeline', 'agent', 'source', 'status', 'territory', 'campaign', 'account', 'month', 'lost_reason', 'industry')
APP = {}   # 'app' → the CRM app (set by setup)


# ------------------------------------------------------------------ industries of the company
def pack_label(code):
    app = APP.get('app')
    p = app.packs.get(code) if app and code else None
    return p['label'] if p else (code or '')


def active_industries():
    """[(code, label)] – the industry packs installed in CRM plus the industries of active pipelines and fields (General left out)."""
    app = APP['app']
    s = app.settings()
    codes = [c for c in (s.packs_installed or []) if c and c != 'general']
    for c in Pipeline.objects.filter(is_active=True).exclude(industry='').values_list('industry', flat=True).distinct():
        if c not in codes:
            codes.append(c)
    return [(c, pack_label(c)) for c in codes if c in app.packs]


def all_industries():
    app = APP['app']
    return [(c, p['label']) for c, p in app.packs.items() if c != 'general']


def default_industry():
    """The company's industry for a new record: CRM's own industry, else the only active one."""
    app = APP['app']
    act = [c for c, _ in active_industries()]
    s = app.settings()
    if s.industry and s.industry != 'general' and s.industry in act:
        return s.industry
    return act[0] if len(act) == 1 else ''


# ------------------------------------------------------------------ field definitions for the kit
def _fields(kind):
    return list(IndustryField.objects.filter(is_active=True, **{ON[kind]: True}).order_by('industry', 'order', 'id'))


def _pipelines_by_industry():
    out = defaultdict(list)
    for pk, ind in Pipeline.objects.exclude(industry='').values_list('pk', 'industry'):
        out[ind].append(pk)
    return out


def as_def(x, kind, pipes=None):
    d = {'code': x.code, 'label': x.label, 'type': x.field_type, 'choices': x.choice_list(), 'required': x.required, 'unit': x.unit,
         'section': x.section or (f'{pack_label(x.industry)} details' if x.industry else 'More details'), 'list': x.in_list, 'filter': x.in_filter,
         'help': x.help, 'industry': x.industry, 'required_from': x.required_from if kind == 'opportunity' else '', 'pdf': x.on_pdf, 'when': []}
    if x.industry:
        d['when'] = [{'field': 'industry', 'in': [x.industry]}]
        if kind == 'opportunity' and pipes is not None and pipes.get(x.industry):
            d['when'].append({'field': 'pipeline', 'in': pipes[x.industry]})
    return d


def dyn_for(kind):
    def fn(request=None):
        pipes = _pipelines_by_industry() if kind == 'opportunity' else None
        return [as_def(x, kind, pipes) for x in _fields(kind)]
    return fn


# ------------------------------------------------------------------ stage rules
def _stage_ref(code):
    s = Stage.objects.filter(code=code).values('pipeline_id', 'order').first()
    return (s['pipeline_id'], s['order']) if s else None


def reached(opp, stage_code, target=None, won=False):
    """True when the opportunity is at (or past) the stage `stage_code` of its pipeline – or is being won."""
    ref = _stage_ref(stage_code)
    if ref is None or (opp.pipeline_id and ref[0] != opp.pipeline_id):   # a stage rule of another pipeline does not apply
        return False
    if won or opp.status == 'won':
        return True
    st = target if target is not None else (opp.stage if opp.stage_id else None)
    if st is None:
        return False
    return st.pipeline_id == ref[0] and (st.order >= ref[1] or st.is_won)


def opp_required(obj, df):
    if not dyn_applies(obj, df):
        return False
    if df.get('required'):
        return True
    rf = df.get('required_from')
    return bool(rf) and reached(obj, rf)


def stage_guard(reg, act_name, target_fn, won=False):
    """Before "Move stage" / "Won": the fields the target stage needs must be filled."""
    act = next((a for a in reg.actions if a.name == act_name), None)
    if act is None:
        return
    orig = act.run

    def run(o, request, f):
        target = target_fn(o, f)
        miss = dyn_missing(reg, o, request, lambda df: dyn_applies(o, df) and (df.get('required') or (df.get('required_from') and
                                                                                                         reached(o, df['required_from'], target, won))))
        if miss:
            where = 'to win the opportunity' if won else f'for the stage "{target.name}"' if target is not None else 'first'
            raise AppError(f'Fill in {", ".join(miss)} {where} (Change the opportunity).', DYN + 'required')
        return orig(o, request, f)
    act.run = run


# ------------------------------------------------------------------ carrying the industry and the values
def carry(src, dst, kind):
    """Industry and the values of the fields `kind` has, from a lead / opportunity / quotation to the next record."""
    if src is None:
        return
    if not getattr(dst, 'industry', '') and getattr(src, 'industry', ''):
        dst.industry = src.industry
    ind = getattr(dst, 'industry', '') or ''
    codes = set(IndustryField.objects.filter(is_active=True, **{ON[kind]: True}).filter(Q(industry='') | Q(industry=ind)).values_list('code', flat=True))
    vals = dict(dst.extra or {})
    for k, v in (src.extra or {}).items():
        if k in codes and k not in vals and v not in (None, ''):
            vals[k] = v
    dst.extra = vals


def _check_industry(obj):
    app = APP['app']
    if obj.industry and obj.industry not in app.packs:
        raise AppError('Choose one of the industries of CRM (Setup › Industry pack).', 'industry')


def prep_lead(o, request, creating):
    if creating and not o.industry:
        o.industry = default_industry()
    _check_industry(o)


def prep_opportunity(o, request, creating):
    if o.pipeline_id and o.pipeline.industry:
        o.industry = o.pipeline.industry
    elif creating and not o.industry:
        o.industry = (o.lead.industry if o.lead_id else '') or default_industry()
    if creating and o.lead_id:
        carry(o.lead, o, 'opportunity')
    _check_industry(o)


def prep_enquiry(o, request, creating):
    if creating:
        carry(o.opportunity if o.opportunity_id else (o.lead if o.lead_id else None), o, 'enquiry')
    if not o.industry:
        o.industry = (o.opportunity.industry if o.opportunity_id else '') or (o.lead.industry if o.lead_id else '') or (default_industry() if creating else '')
    _check_industry(o)


def prep_quotation(o, request, creating):
    if creating and o.opportunity_id:
        carry(o.opportunity, o, 'quotation')
    if not o.industry:
        o.industry = (o.opportunity.industry if o.opportunity_id else '') or (default_industry() if creating else '')
    _check_industry(o)


def prep_order(o, request, creating):
    if creating:
        carry(o.quotation if o.quotation_id else (o.opportunity if o.opportunity_id else None), o, 'sales_order')
    if not o.industry:
        o.industry = (o.quotation.industry if o.quotation_id else '') or (o.opportunity.industry if o.opportunity_id else '')
    _check_industry(o)


# ------------------------------------------------------------------ lead scoring on industry fields
def score_hit(lead, when, value):
    """Score rules "Industry field" (code / code=value / code>=n / code<=n) and "Industry is"."""
    v = (value or '').strip()
    if when == 'industry':
        return bool(v) and (lead.industry or '').lower() == v.lower()
    m = re.match(r'^\s*([a-z0-9_]+)\s*(>=|<=|=)?\s*(.*)$', v.lower())
    if not m:
        return False
    code, op, want = m.group(1), m.group(2), m.group(3).strip()
    have = (lead.extra or {}).get(code)
    if have in (None, ''):
        return False
    if not op:
        return have is not False
    if op == '=':
        if isinstance(have, bool):
            return (want in ('yes', 'true', '1')) == have
        return str(have).strip().lower() == want
    try:
        a, b = float(have), float(want)
    except (TypeError, ValueError):
        return False
    return a >= b if op == '>=' else a <= b


# ------------------------------------------------------------------ setup checks
def field_check(o, request, old):
    o.code = (o.code or '').strip().lower()
    if not CODE_RE.match(o.code):
        raise AppError('Code: small letters, digits and _ only, starting with a letter (2–40 characters).', 'code')
    if old is not None and old.code != o.code and _in_use(old.code):
        raise AppError(f'The code {old.code} is already used on records – make a new field instead.', 'code')
    if o.field_type == 'choice' and not o.choice_list():
        raise AppError('Give the pick-list values (one per line).', 'choices')
    if o.required_from and not Stage.objects.filter(code=o.required_from).exists():
        raise AppError(f'No stage with the code {o.required_from} (Setup › Pipelines and stages).', 'required_from')
    if o.industry and o.industry not in APP['app'].packs:
        raise AppError('Unknown industry.', 'industry')
    if not (o.on_lead or o.on_opportunity or o.on_quotation or o.on_order):
        raise AppError('Show the field on leads, opportunities, quotations or sales orders.', 'on_lead')
    if o.on_pdf and not o.on_quotation:
        o.on_quotation = True


def _in_use(code):
    for M in (Lead, Opportunity, Quotation, SalesOrder):
        if M.objects.filter(**{'extra__has_key': code}).exists():
            return True
    return False


def report_check(o, request, old):
    o.code = (o.code or '').strip().lower()
    if not CODE_RE.match(o.code):
        raise AppError('Code: small letters, digits and _ only, starting with a letter (2–40 characters).', 'code')
    g = (o.group_by or '').strip()
    o.group_by = g
    if g.startswith('field:'):
        fx = IndustryField.objects.filter(code=g[6:]).first()
        if fx is None:
            raise AppError(f'No industry field with the code {g[6:]}.', 'group_by')
        if not getattr(fx, ON[o.source]):
            raise AppError(f'The field {fx.label} is not shown on {dict(REPORT_SOURCES)[o.source].lower()}.', 'group_by')
    elif g not in GROUPS:
        raise AppError('Group by: ' + ', '.join(GROUPS) + ' or field:<field code>.', 'group_by')
    if g in ('stage', 'pipeline', 'lost_reason') and o.source != 'opportunity':
        raise AppError('Stage, pipeline and lost reason groups are for opportunities.', 'group_by')
    m = (o.measure or '').strip()
    o.measure = m
    if m:
        fx = IndustryField.objects.filter(code=m[6:]).first() if m.startswith('field:') else None
        if fx is None or fx.field_type not in ('number', 'money'):
            raise AppError('Add up: field:<code> of a number or amount field.', 'measure')
    if o.industry and o.industry not in APP['app'].packs:
        raise AppError('Unknown industry.', 'industry')


# ------------------------------------------------------------------ industry reports
SRC = {
    'lead': {'value': 'value', 'won': ('converted',), 'lost': ('disqualified',), 'open_ex': ('converted', 'disqualified'), 'created': 'created_at',
             'closed': 'converted_at', 'won_label': 'Converted', 'rate_label': 'Conversion %'},
    'opportunity': {'value': 'value', 'won': ('won',), 'lost': ('lost',), 'open_ex': ('won', 'lost'), 'created': 'created_at', 'closed': 'closed_at',
                    'expected_close': 'expected_close', 'won_label': 'Won', 'rate_label': 'Win rate %'},
    'quotation': {'value': 'total', 'won': ('accepted',), 'lost': ('rejected', 'expired', 'cancelled'), 'open_ex': ('accepted', 'rejected', 'expired', 'cancelled'),
                  'created': 'date', 'closed': 'decided_at', 'won_label': 'Accepted', 'rate_label': 'Hit rate %'},
}


def report_rows(rep, request, date_from=None, date_to=None, branches=None, limit=20000):
    """(columns, rows) of an industry report for what the user may see."""
    app = APP['app']
    meta = SRC[rep.source]
    reg = app.regs[rep.source]
    qs = reg.scope(request)
    if branches is not None:
        qs = qs.filter(Q(branch__in=branches) | Q(branch__isnull=True))
    if rep.industry:
        qs = qs.filter(industry=rep.industry)
    if rep.status == 'open':
        qs = qs.exclude(status__in=meta['open_ex'])
    elif rep.status == 'won':
        qs = qs.filter(status__in=meta['won'])
    elif rep.status == 'lost':
        qs = qs.filter(status__in=meta['lost'])
    dfield = meta.get(rep.date_field) if rep.date_field != 'none' else None
    if rep.date_field == 'expected_close' and not dfield:
        dfield = meta['created']
    if dfield:
        fld = qs.model._meta.get_field(dfield)
        suffix = '__date' if fld.get_internal_type() == 'DateTimeField' else ''
        if date_from and date_from.year > 1900:
            qs = qs.filter(**{f'{dfield}{suffix}__gte': date_from})
        if date_to and date_to.year < 2999:
            qs = qs.filter(**{f'{dfield}{suffix}__lte': date_to})
    rel = [x for x in ('owner', 'source', 'territory', 'campaign', 'account') if hasattr(qs.model, x)]
    if rep.source == 'opportunity':
        rel += ['stage', 'pipeline', 'lost_reason']
    qs = qs.select_related(*rel)
    g = rep.group_by
    gfield = IndustryField.objects.filter(code=g[6:]).first() if g.startswith('field:') else None
    mfield = IndustryField.objects.filter(code=rep.measure[6:]).first() if (rep.measure or '').startswith('field:') else None
    page = app.page
    data = OrderedDict()
    order = {}

    def key_of(o):
        if gfield is not None:
            v = (o.extra or {}).get(gfield.code)
            if v in (None, ''):
                return ('', '(not given)', None)
            if gfield.field_type == 'date':
                return (str(v)[:7], str(v)[:7], None)
            return (str(v), dyn_show({'type': gfield.field_type}, v), f'x_{gfield.code}=' + ('1' if v is True else '0' if v is False else str(v)))
        if g == 'stage':
            s = getattr(o, 'stage', None)
            if s is None:
                return ('', 'No stage', None)
            order[str(s.pk)] = (s.pipeline_id, s.order)
            return (str(s.pk), f'{s.name}', f'stage={s.pk}')
        if g == 'pipeline':
            p = getattr(o, 'pipeline', None)
            return (str(p.pk), p.name, f'pipeline={p.pk}') if p else ('', 'No pipeline', None)
        if g == 'agent':
            e = getattr(o, 'owner', None)
            return (str(e.pk), emp_name(e), f'owner={e.pk}') if e else ('', 'Unassigned', None)
        if g in ('source', 'territory', 'campaign', 'account', 'lost_reason'):
            x = getattr(o, g, None)
            if x is None:
                return ('', f'No {g.replace("_", " ")}', None)
            return (str(x.pk), getattr(x, 'name', str(x)), f'{g}={x.pk}' if g != 'lost_reason' else None)
        if g == 'status':
            return (o.status, o.get_status_display(), f'status={o.status}')
        if g == 'industry':
            return (o.industry or '', pack_label(o.industry) or 'No industry', f'industry={o.industry}' if o.industry else None)
        if g == 'month':
            v = getattr(o, dfield or meta['created'], None)
            if isinstance(v, datetime):
                v = v.date()
            return (v.strftime('%Y-%m'), v.strftime('%Y-%m'), None) if isinstance(v, date) else ('', 'No date', None)
        return ('', '', None)

    for o in qs[:limit]:
        k, label, q = key_of(o)
        d = data.get(k)
        if d is None:
            d = data[k] = {'group': label, 'records': 0, 'value': 0.0, 'measure': 0.0, 'won': 0, 'lost': 0, '_q': q}
        d['records'] += 1
        d['value'] += float(getattr(o, meta['value'], 0) or 0)
        if mfield is not None:
            try:
                d['measure'] += float((o.extra or {}).get(mfield.code) or 0)
            except (TypeError, ValueError):
                pass
        if o.status in meta['won']:
            d['won'] += 1
        elif o.status in meta['lost']:
            d['lost'] += 1
    rows = []
    for k, d in data.items():
        closed = d['won'] + d['lost']
        r = {'group': d['group'], 'records': d['records'], 'value': round(d['value'], 2), 'won': d['won'],
             'rate': round(100 * d['won'] / closed, 1) if closed else 0}
        if mfield is not None:
            r['measure'] = round(d['measure'], 2)
        r['_url'] = f'{page}r/{rep.source}' + (f'?{d["_q"]}' if d['_q'] else '')
        r['_k'] = k
        rows.append(r)
    if g == 'stage':
        rows.sort(key=lambda r: order.get(r['_k'], (0, 0)))
    elif g == 'month' or (gfield is not None and gfield.field_type == 'date'):
        rows.sort(key=lambda r: r['_k'])
    else:
        rows.sort(key=lambda r: (-r['records'], r['group']))
    for r in rows:
        r.pop('_k', None)
    glabel = gfield.label if gfield is not None else {'agent': 'Agent', 'lost_reason': 'Lost reason'}.get(g, g.replace('_', ' ').capitalize())
    cols = [('group', glabel, 'text'), ('records', dict(REPORT_SOURCES)[rep.source], 'number'),
            ('value', 'Total value' if rep.source == 'quotation' else 'Value', 'number')]
    if mfield is not None:
        cols.append(('measure', mfield.label + (f' ({mfield.unit})' if mfield.unit else ''), 'number'))
    cols += [('won', meta['won_label'], 'number'), ('rate', meta['rate_label'], 'number')]
    return cols, rows


REPORT_KEY = re.compile(r'^crmx-([a-z0-9_]+)$')


def _crm_on():
    try:
        from ZeoApps.services import is_active
        return is_active('crm')
    except Exception:   # noqa: BLE001
        return True


def report_codes():
    app = APP['app']
    return [app.code(r) for r, _ in app.roles]


def report_listing(request, c):
    """The industry reports of the open company for the report centre list (keys crmx-<code>)."""
    if not _crm_on():
        return []
    codes = report_codes()
    if not (c.admin or any(x in c.codes for x in codes)):
        return []
    out = []
    for rep in IndustryReport.objects.filter(is_active=True).order_by('industry', 'order', 'id'):
        out.append({'key': f'crmx-{rep.code}', 'title': rep.name, 'section': f'CRM – {pack_label(rep.industry)}' if rep.industry else 'CRM – industry',
                    'description': rep.description or _describe(rep), 'period': None if rep.date_field == 'none' else 'year', 'perms': codes})
    return out


def _describe(rep):
    src = dict(REPORT_SOURCES)[rep.source].lower()
    g = rep.group_by[6:] if rep.group_by.startswith('field:') else rep.group_by
    if rep.group_by.startswith('field:'):
        fx = IndustryField.objects.filter(code=g).first()
        g = fx.label if fx else g
    st = {'open': 'open ', 'won': 'won ', 'lost': 'lost ', 'all': ''}[rep.status]
    return f'{st.capitalize() or ""}{src if st else src.capitalize()} by {g.lower()}, with value, won and rate. Opens the records.'


def report_spec(key):
    """REPORTS entry for crmx-<code>: (title, section, description, fn, period, permission codes) or None."""
    m = REPORT_KEY.match(str(key or ''))
    if not m:
        return None
    from django.db import connection
    if getattr(connection, 'schema_name', 'public') == 'public':
        return None
    try:
        rep = IndustryReport.objects.filter(code=m.group(1), is_active=True).first()
    except Exception:   # noqa: BLE001
        return None
    if rep is None:
        return None

    def fn(run):
        if not _crm_on():
            raise ValueError('CRM is not active in the company (Settings → Apps).')
        cols, rows = report_rows(rep, run.request, getattr(run, 'date_from', None), getattr(run, 'date_to', None), getattr(run, 'branches', None))
        notes = [f'Industry: {pack_label(rep.industry)}.' if rep.industry else 'Every industry.']
        if rep.date_field != 'none':
            notes.append('Period on: ' + dict(IndustryReport._meta.get_field('date_field').choices)[rep.date_field].lower() + '.')
        return cols, rows, {'notes': notes, 'filters': ['group']}

    return (rep.name, f'CRM – {pack_label(rep.industry)}' if rep.industry else 'CRM – industry', rep.description or _describe(rep), fn,
            None if rep.date_field == 'none' else 'year', report_codes())


def dashboard_bars(request):
    """The industry reports marked "show on the CRM home" (at most 6) as bars of the CRM home, for the dashboard period."""
    out = []
    try:
        from zeo.appkit.engine import dash_period
        frm, to = dash_period(request)
    except Exception:   # noqa: BLE001
        frm = to = None
    act = {c for c, _ in active_industries()}
    for rep in IndustryReport.objects.filter(is_active=True, on_dashboard=True).order_by('industry', 'order', 'id'):
        if rep.industry and rep.industry not in act:
            continue
        try:
            cols, rows = report_rows(rep, request, frm if rep.date_field != 'none' else None, to if rep.date_field != 'none' else None, limit=5000)
        except Exception:   # noqa: BLE001
            log.exception('industry report %s', rep.code)
            continue
        if not rows:
            continue
        use = 'measure' if any('measure' in r for r in rows) else 'records'
        lab = next((c[1] for c in cols if c[0] == use), '')
        out.append({'title': f'{rep.name}' + (f' – {lab.lower()}' if use == 'measure' else ''),
                    'items': [{'label': r['group'], 'value': r.get(use, 0), 'link': r['_url'].replace(APP['app'].page, '')} for r in rows[:12]]})
        if len(out) >= 6:
            break
    return out


# ------------------------------------------------------------------ quotation PDF
def pdf_rows(doc):
    """[(label, value)] of the fields marked "print on quotations" that belong to this document."""
    vals = doc.extra or {}
    if not vals:
        return []
    out = []
    for x in IndustryField.objects.filter(is_active=True, on_pdf=True).order_by('industry', 'order', 'id'):
        if x.industry and x.industry != (doc.industry or ''):
            continue
        v = vals.get(x.code)
        if v in (None, ''):
            continue
        out.append((x.label + (f' ({x.unit})' if x.unit else ''), dyn_show({'type': x.field_type}, v)))
    return out


# ------------------------------------------------------------------ pick lists of the setup forms (v1.67.31)
GROUP_LABELS = {'stage': 'Stage', 'pipeline': 'Pipeline', 'agent': 'Agent', 'source': 'Lead source', 'status': 'Status', 'territory': 'Territory',
                'campaign': 'Campaign', 'account': 'Account', 'month': 'Month', 'lost_reason': 'Lost reason', 'industry': 'Industry'}


def _stage_choices():
    return [(st.code, f'{st.pipeline.name} › {st.name}') for st in Stage.objects.filter(is_active=True).select_related('pipeline')
            .order_by('pipeline__name', 'order', 'id')]


def _group_choices():
    out = [(g, GROUP_LABELS.get(g, g)) for g in GROUPS]
    out += [(f'field:{x.code}', f'Field: {x.label}' + (f' ({pack_label(x.industry)})' if x.industry else ''))
            for x in IndustryField.objects.filter(is_active=True).order_by('industry', 'order', 'id')]
    return out


def _measure_choices():
    return [(f'field:{x.code}', f'{x.label}' + (f' ({pack_label(x.industry)})' if x.industry else ''))
            for x in IndustryField.objects.filter(is_active=True, field_type__in=('number', 'decimal', 'money', 'int', 'amount')).order_by('industry', 'order', 'id')]


# ------------------------------------------------------------------ wiring
def setup(app, MANAGE, VIEW):
    APP['app'] = app
    app.pack_models.update({'field': (IndustryField, 'code'), 'report': (IndustryReport, 'code')})
    SETUP_VIEW = MANAGE + VIEW + ('crm_agent',)
    regs = app.regs

    # the industry on leads, opportunities, quotations and sales orders + their industry fields
    for kind, prep in (('lead', prep_lead), ('opportunity', prep_opportunity), ('quotation', prep_quotation), ('sales_order', prep_order), ('enquiry', prep_enquiry)):
        r = regs.get(kind)
        if r is None:
            continue
        r.dyn = dyn_for(kind)
        r.dyn_store = 'extra'
        r.dyn_prepare = prep
        r.choices_fn = dict(r.choices_fn or {}, industry=active_industries)
        if 'industry' not in r.form:
            form = list(r.form)
            at = {'lead': 0, 'enquiry': 1, 'opportunity': form.index('pipeline') + 1 if 'pipeline' in form else 0,
                  'quotation': form.index('opportunity') + 1 if 'opportunity' in form else 0}.get(kind, len(form))
            form.insert(at, 'industry')
            r.form = tuple(form)
        if 'industry' not in r.filters:
            r.filters = tuple(r.filters) + ('industry',)
        if 'industry' not in r.cols and kind != 'sales_order':   # shown only when the company has two or more industries (kit views)
            cols = list(r.cols)
            cols.insert(min(len(cols), 3), 'industry')
            r.cols = tuple(cols)
    oreg = regs['opportunity']
    oreg.dyn_required = opp_required
    stage_guard(oreg, 'move', lambda o, f: f.obj('stage', Stage, False, 'Stage'))
    stage_guard(oreg, 'won', lambda o, f: None, won=True)

    # pipelines: their industry
    preg = regs['pipeline']
    preg.cols = tuple(c for c in preg.cols if c != 'review_status') + ('industry', 'review_status')
    preg.form = tuple(preg.form) + ('industry',)
    preg.filters = tuple(preg.filters) + ('industry',)
    preg.choices_fn = dict(preg.choices_fn or {}, industry=all_industries)

    app.option('industry-fields', lambda: IndustryField.objects.filter(is_active=True), lambda x: f'{x.label} ({x.code})', ('label', 'code'), IndustryField)
    app.reg(Reg('industry_field', IndustryField, 'Industry field', 'Industry fields', 'Setup',
                cols=('industry', 'section', 'order', 'label', 'code', 'field_type', 'on_lead', 'on_opportunity', 'on_quotation', 'required', 'required_from',
                      'in_list', 'is_active', 'review_status'),
                form=('industry', 'label', 'code', 'field_type', 'choices', 'unit', 'section', 'order', 'help', 'on_lead', 'on_opportunity', 'on_quotation',
                      'on_order', 'required', 'required_from', 'in_list', 'in_filter', 'on_pdf', 'is_active'),
                manage=('crm_manager',), view=SETUP_VIEW, search=('label', 'code', 'section'), filters=('industry', 'field_type', 'is_active', 'review_status'),
                title='label', subtitle='code', review=True, deletable=True, files=False, check=field_check,
                help='Fields added to leads, opportunities, quotations and sales orders for one industry (or every industry). Code = the key of the value; '
                     '"Required from stage" uses a stage code of Setup › Pipelines and stages.'))
    # v1.67.31 (screen test): "Required from stage" was a text box for a stage code the user had to know – now a pick list
    app.regs['industry_field'].choices_fn = {'industry': all_industries, 'required_from': _stage_choices}
    app.reg(Reg('industry_report', IndustryReport, 'Industry report', 'Industry reports', 'Setup',
                cols=('industry', 'order', 'name', 'source', 'group_by', 'measure', 'status', 'date_field', 'on_dashboard', 'is_active', 'review_status'),
                form=('industry', 'name', 'code', 'source', 'group_by', 'measure', 'status', 'date_field', 'on_dashboard', 'order', 'description', 'is_active'),
                manage=('crm_manager',), view=SETUP_VIEW, search=('name', 'code', 'group_by'), filters=('industry', 'source', 'on_dashboard', 'is_active', 'review_status'),
                title='name', subtitle='code', review=True, deletable=True, files=False, check=report_check,
                help='Analyses of one industry in Reports (section "CRM – <industry>") and, when marked, on the CRM home. '
                     'Group by stage, pipeline, agent, source, status, territory, campaign, account, month, lost_reason, industry or field:<field code>.'))
    # v1.67.31 (screen test): "Group by" and "Add up" were typed codes – now pick lists (field:<code> of the industry fields)
    app.regs['industry_report'].choices_fn = {'industry': all_industries, 'group_by': _group_choices, 'measure': _measure_choices}

    # the CRM home: the industry reports marked for it
    base = app.dashboard

    def dashboard(request):
        out = base(request) if base else {'cards': [], 'bars': [], 'lists': []}
        try:
            out['bars'] = list(out.get('bars') or []) + dashboard_bars(request)
        except Exception:   # noqa: BLE001
            log.exception('industry bars')
        return out
    app.dashboard = dashboard
    return {'report_listing': report_listing, 'report_spec': report_spec, 'score_hit': score_hit, 'carry': carry, 'pdf_rows': pdf_rows,
            'active_industries': active_industries, 'default_industry': default_industry}
