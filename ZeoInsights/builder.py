"""v1.53.0 – the report builder: a record type, columns, filters, grouping with totals → a report of the report centre.

A custom report is the report `c-<id>` of DashboardManagement.reports.REPORTS (the registry resolves these keys here), so it
opens in the report centre, can be e-mailed, exported and scheduled like the built-in reports.

Rights: the record type must be one the user may view (company administrator, or the view right of that record type);
the rows are limited like the lists (branches, own records – AccessControl.scope_queryset). Password and code fields are
never offered.
"""
import logging
import re
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.db.models import Q

log = logging.getLogger(__name__)
MAX_ROWS = 20000
OPS = ('eq', 'ne', 'contains', 'not_contains', 'starts', 'ends', 'gt', 'lt', 'gte', 'lte', 'empty', 'not_empty', 'true', 'false',
       'between', 'in', 'not_in', 'date_before', 'date_after', 'date_today', 'date_overdue', 'this_month', 'this_year', 'last_days', 'next_days')
FNS = ('count', 'sum', 'avg', 'min', 'max')
KEY_RE = re.compile(r'^c-(\d+)$')


class _View:
    """scope_queryset reads a few attributes of the view."""
    zeo_scope = True
    self_service_actions = ()


def user_groups(user):
    try:
        from tenant_users.permissions.models import UserTenantPermissions
        return set(UserTenantPermissions.objects.filter(profile=user).values_list('groups', flat=True)) - {None}
    except Exception:
        return set()


def visible(obj, user, c=None):
    """Owner, shared with everybody, or with one of the user's permission groups (administrators see all)."""
    if obj.owner_id == user.pk or (c is not None and c.admin):
        return True
    if obj.shared == 'all':
        return True
    if obj.shared == 'groups':
        return bool(user_groups(user) & {int(g) for g in (obj.groups or []) if str(g).isdigit()})
    return False


def model_allowed(c, key):
    from ZeoFlow.registry import get_model
    m = get_model(key or '')
    if m is None:
        return None
    if c.admin or f'view_{m._meta.model_name}' in c.codes:
        return m
    return None


MODEL_NAMES = {'EmpManagement.emp_master': 'Employees', 'OrganisationManager.brnch_mstr': 'Branches', 'OrganisationManager.dept_master': 'Departments',
               'OrganisationManager.desgntn_master': 'Designations', 'OrganisationManager.ctgry_master': 'Categories',
               'calendars.employee_leave_request': 'Leave requests', 'calendars.emp_leave_balance': 'Leave balances', 'calendars.leave_type': 'Leave types',
               'EmpManagement.emp_family': 'Family members', 'EmpManagement.Emp_Documents': 'Employee documents', 'EmpManagement.EmpQualification': 'Qualifications',
               'EmpManagement.EmpJobHistory': 'Job history', 'PayrollManagement.Payslip': 'Payslips', 'PayrollManagement.PayrollRun': 'Payroll runs'}
WORDS = {'emp': '', 'ef': '', 'br': 'branch', 'dept': 'department', 'desgntn': 'designation', 'ctgry': 'category', 'brnch': 'branch', 'mstr': '',
         'nmbr': 'number', 'relegion': 'religion', 'permenent': 'permanent', 'lv': 'leave', 'expence': 'expense', 'doc': 'document', 'crncy': 'currency',
         'cntry': 'country', 'dob': 'date of birth', 'ot': 'overtime'}


def nice(label, link=False):
    """Readable labels for the old field names: "Emp dept id" → "Department", "Br branch nmbr 1" → "Branch number 1"."""
    if str(label or '').strip().upper() == 'ID':
        return 'ID'
    words = [WORDS.get(w.lower(), w.lower()) for w in str(label or '').replace('_', ' ').split()]
    words = [w for w in words if w]
    if link and len(words) > 1 and words[-1] == 'id':
        words = words[:-1]
    out = []
    for w in words:   # "branch branch name" → "branch name"
        if not out or out[-1] != w:
            out.append(w)
    t = ' '.join(out) or str(label or '')
    return t[:1].upper() + t[1:]


def models_for(c):
    from ZeoFlow.registry import models_of
    out = []
    for d in models_of():
        name = d['key'].split('.', 1)[1]
        if c.admin or f'view_{name}' in c.codes:
            out.append(dict(d, label=MODEL_NAMES.get(d['key'], nice(d['label']))))
    out.sort(key=lambda d: (d['module'], d['label']))
    return out


def fields_for(key):
    from ZeoFlow.registry import fields_of
    out = []
    for f in fields_of(key):
        if re.search(r'password|secret|token|otp|key_hash|totp|recovery|face_encoding', f['key'], re.I):
            continue
        if ' › ' in f['label']:
            a, b = f['label'].split(' › ', 1)
            f = dict(f, label=f"{nice(a, True)} › {nice(b)}")
        else:
            f = dict(f, label=nice(f['label'], f['type'] == 'link'))
        out.append(f)
    return out


def col_key(path):
    return 'f_' + path.replace('__', '_')


# ------------------------------------------------------------------ checking a definition
def clean(d, c):
    """The definition from the screen → (fields, error)."""
    m = model_allowed(c, d.get('model'))
    if m is None:
        return None, 'Choose a record type you may view.'
    fmap = {f['key']: f for f in fields_for(d['model'])}
    cols = [x for x in (d.get('columns') or []) if isinstance(x, dict) and x.get('field') in fmap]
    if not cols:
        return None, 'Choose at least one column.'
    if len(cols) > 40:
        return None, 'At most 40 columns.'
    for f in d.get('filters') or []:
        if f.get('field') not in fmap or f.get('op') not in OPS:
            return None, f'Check the filter on "{f.get("field")}".'
    g = d.get('group_by') or ''
    if g and g not in fmap:
        return None, 'Choose the grouping field again.'
    for a in d.get('aggregates') or []:
        if a.get('fn') not in FNS or (a.get('fn') != 'count' and fmap.get(a.get('field'), {}).get('type') != 'number'):
            return None, 'Totals: count, or the sum / average / lowest / highest of a number field.'
    s = (d.get('sort') or '').lstrip('-')
    if s and s not in fmap:
        return None, 'Choose the sort field again.'
    df = d.get('date_field') or ''
    if df and fmap.get(df, {}).get('type') not in ('date', 'datetime'):
        return None, 'The period field must be a date.'
    return fmap, ''


# ------------------------------------------------------------------ query
def _val(v, typ):
    if typ == 'number':
        try:
            return Decimal(str(v))
        except Exception:
            raise ValueError(f'"{v}" is not a number.')
    if typ in ('date', 'datetime'):
        try:
            return date.fromisoformat(str(v)[:10])
        except ValueError:
            raise ValueError(f'"{v}" is not a date (YYYY-MM-DD).')
    if typ == 'bool':
        return str(v).lower() in ('1', 'true', 'yes')
    return v


def q_of(filters, fmap, today=None):
    today = today or date.today()
    q = Q()
    for f in filters or []:
        k, op, v, v2 = f['field'], f['op'], f.get('value'), f.get('value2')
        typ = fmap[k]['type']
        dk = f'{k}__date' if typ == 'datetime' else k
        if typ == 'link' and not fmap[k].get('related'):
            k_id = f'{k}__pk'
        else:
            k_id = k
        if op == 'eq':
            q &= Q(**{k_id if typ == 'link' else dk: _val(v, typ) if typ != 'link' else v})
        elif op == 'ne':
            q &= ~Q(**{k_id if typ == 'link' else dk: _val(v, typ) if typ != 'link' else v})
        elif op == 'contains':
            q &= Q(**{f'{k}__icontains': v})
        elif op == 'not_contains':
            q &= ~Q(**{f'{k}__icontains': v})
        elif op == 'starts':
            q &= Q(**{f'{k}__istartswith': v})
        elif op == 'ends':
            q &= Q(**{f'{k}__iendswith': v})
        elif op in ('gt', 'lt', 'gte', 'lte'):
            q &= Q(**{f'{dk}__{op}': _val(v, typ)})
        elif op == 'between':
            q &= Q(**{f'{dk}__gte': _val(v, typ), f'{dk}__lte': _val(v2, typ)})
        elif op == 'empty':
            q &= Q(**{f'{k}__isnull': True}) | (Q(**{k: ''}) if typ in ('text', 'email', 'choice') else Q(pk__in=[]))
        elif op == 'not_empty':
            q &= Q(**{f'{k}__isnull': False}) & (~Q(**{k: ''}) if typ in ('text', 'email', 'choice') else Q())
        elif op == 'true':
            q &= Q(**{k: True})
        elif op == 'false':
            q &= Q(**{k: False})
        elif op in ('in', 'not_in'):
            vals = [x.strip() for x in str(v or '').split(',') if x.strip()]
            c = Q(**{f'{k_id if typ == "link" else k}__in': vals})
            q &= c if op == 'in' else ~c
        elif op == 'date_before':
            q &= Q(**{f'{dk}__lt': _val(v, 'date')})
        elif op == 'date_after':
            q &= Q(**{f'{dk}__gt': _val(v, 'date')})
        elif op == 'date_today':
            q &= Q(**{dk: today})
        elif op == 'date_overdue':
            q &= Q(**{f'{dk}__lt': today})
        elif op == 'this_month':
            q &= Q(**{f'{dk}__year': today.year, f'{dk}__month': today.month})
        elif op == 'this_year':
            q &= Q(**{f'{dk}__year': today.year})
        elif op in ('last_days', 'next_days'):
            try:
                n = int(v or 30)
            except (TypeError, ValueError):
                raise ValueError('Give the number of days.')
            q &= Q(**{f'{dk}__gte': today - timedelta(days=n), f'{dk}__lte': today}) if op == 'last_days' else \
                Q(**{f'{dk}__gte': today, f'{dk}__lte': today + timedelta(days=n)})
    return q


def value_of(obj, path, f):
    cur = obj
    parts = path.split('__')
    for i, p in enumerate(parts):
        if cur is None:
            return None
        if i == len(parts) - 1:
            if f.get('choices'):
                disp = getattr(cur, f'get_{p}_display', None)
                if disp:
                    try:
                        return str(disp())
                    except Exception:
                        pass
            v = getattr(cur, p, None)
        else:
            v = getattr(cur, p, None)
        cur = v
    v = cur
    if v is None:
        return None
    if hasattr(v, 'all') and callable(v.all):    # many-to-many
        try:
            return ', '.join(str(x) for x in v.all()[:20])
        except Exception:
            return ''
    if hasattr(v, '_meta'):
        return str(v)
    if isinstance(v, bool):
        return 'Yes' if v else 'No'
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return v.isoformat(timespec='minutes')
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, (int, float, str)):
        return v
    return str(v)


def col_type(f):
    t = f['type']
    return 'number' if t == 'number' else ('date' if t in ('date', 'datetime') else 'text')


def queryset(d, fmap, request, date_from=None, date_to=None):
    from AccessControl.access import scope_queryset
    from ZeoFlow.registry import get_model
    m = get_model(d['model'])
    qs = scope_queryset(request, _View(), m.objects.all())
    qs = qs.filter(q_of(d.get('filters'), fmap))
    df = d.get('date_field') or ''
    if df and (date_from or date_to):
        dk = f'{df}__date' if fmap[df]['type'] == 'datetime' else df
        if date_from:
            qs = qs.filter(**{f'{dk}__gte': date_from})
        if date_to:
            qs = qs.filter(**{f'{dk}__lte': date_to})
    rel = sorted({c['field'].split('__')[0] for c in d.get('columns') or [] if fmap.get(c['field'].split('__')[0], {}).get('type') == 'link'
                  and not fmap.get(c['field'].split('__')[0], {}).get('many')})
    if rel:
        try:
            qs = qs.select_related(*rel)
        except Exception:
            pass
    s = d.get('sort') or ''
    try:
        qs = qs.order_by(s, '-pk') if s else qs.order_by('-pk')
    except Exception:
        qs = qs.order_by('-pk')
    return qs.distinct() if '__' in ''.join(f['field'] for f in d.get('filters') or []) else qs


def run(d, request, date_from=None, date_to=None, limit=None):
    """(cols, rows, extra) like the report functions of DashboardManagement.reports."""
    from AccessControl.access import ctx
    from DashboardManagement.reports import src
    c = ctx(request)
    fmap, err = clean(d, c)
    if err:
        raise ValueError(err)
    qs = queryset(d, fmap, request, date_from, date_to)
    n = min(int(limit or d.get('limit') or MAX_ROWS), MAX_ROWS)
    cols_def = d['columns']
    rows = []
    objs = list(qs[:n])
    for obj in objs:
        r = {}
        for cdef in cols_def:
            r[col_key(cdef['field'])] = value_of(obj, cdef['field'], fmap[cdef['field']])
        r.update(src(obj))
        rows.append(r)
    cols = [(col_key(cdef['field']), (cdef.get('label') or fmap[cdef['field']]['label'])[:80], col_type(fmap[cdef['field']])) for cdef in cols_def]
    g = d.get('group_by') or ''
    if not g:
        return cols, rows, {'notes': [f'The first {n:,} rows are shown.'] if len(rows) >= n and n == MAX_ROWS else []}
    # grouping: one row per value of the grouping field, with the totals asked for
    gf = fmap[g]
    groups = {}
    order = []
    for obj in objs:
        key = value_of(obj, g, gf)
        key = '(empty)' if key in (None, '') else key
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(obj)
    aggs = d.get('aggregates') or [{'fn': 'count'}]
    gcols = [('group', (gf['label'])[:80], col_type(gf))]
    for a in aggs:
        if a['fn'] == 'count':
            gcols.append(('count', 'Number', 'number'))
        else:
            lab = {'sum': 'Total', 'avg': 'Average', 'min': 'Lowest', 'max': 'Highest'}[a['fn']]
            gcols.append((f"{a['fn']}_{a['field'].replace('__', '_')}", f"{lab} {fmap[a['field']]['label']}"[:80], 'number'))
    out = []
    for key in order:
        objs = groups[key]
        r = {'group': key}
        for a in aggs:
            if a['fn'] == 'count':
                r['count'] = len(objs)
                continue
            vals = [value_of(o, a['field'], fmap[a['field']]) for o in objs]
            vals = [float(v) for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
            k = f"{a['fn']}_{a['field'].replace('__', '_')}"
            r[k] = round(sum(vals), 2) if a['fn'] == 'sum' else (round(sum(vals) / len(vals), 2) if vals and a['fn'] == 'avg' else
                                                                  (min(vals) if vals and a['fn'] == 'min' else (max(vals) if vals and a['fn'] == 'max' else None)))
        out.append(r)
    sk = next((k for k, _, t in gcols[1:] if t == 'number'), None)
    if sk and not d.get('sort'):
        out.sort(key=lambda r: -(r.get(sk) or 0))
    return gcols, out, {'no_totals': [k for k, _, _ in gcols if k.startswith(('avg_', 'min_', 'max_'))]}


def as_dict(cr):
    return {'model': cr.model, 'columns': cr.columns, 'filters': cr.filters, 'group_by': cr.group_by, 'aggregates': cr.aggregates,
            'sort': cr.sort, 'limit': cr.limit, 'date_field': cr.date_field}


# ------------------------------------------------------------------ the report centre
def spec(key):
    """REPORTS entry for c-<id>: (title, section, description, function, period, permission codes) or None."""
    m = KEY_RE.match(str(key or ''))
    if not m:
        return None
    from django.db import connection
    if getattr(connection, 'schema_name', 'public') == 'public':
        return None
    try:
        from .models import CustomReport
        cr = CustomReport.objects.filter(pk=int(m.group(1))).first()
    except Exception:
        return None
    if cr is None:
        return None
    d = as_dict(cr)
    name = cr.model.split('.', 1)[-1]

    def fn(run_obj):
        req = run_obj.request
        if not visible(cr, req.user, run_obj.c):
            raise ValueError('This report is not shared with you.')
        a = run_obj.date_from if run_obj.date_from and run_obj.date_from.year > 1900 else None
        b = run_obj.date_to if run_obj.date_to and run_obj.date_to.year < 2999 else None
        return run(d, req, a, b)

    return (cr.name, cr.section or 'My reports', cr.description or f'Made with the report builder by {cr.owner}.', fn,
            'year' if cr.date_field else None, [f'view_{name}'])


def listing(request, c):
    from .models import CustomReport
    out = []
    for cr in CustomReport.objects.select_related('owner'):
        if not visible(cr, request.user, c):
            continue
        name = cr.model.split('.', 1)[-1]
        if not (c.admin or f'view_{name}' in c.codes):
            continue
        out.append({'key': f'c-{cr.pk}', 'title': cr.name, 'section': cr.section or 'My reports', 'description': cr.description,
                    'period': 'year' if cr.date_field else None, 'perms': [f'view_{name}'], 'custom': True})
    return out
