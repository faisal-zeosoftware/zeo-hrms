"""ZEO Developer – the reports and print layouts of custom modules (D1).

Reports are listed in the report centre (Reports, key dev-<id>) next to the built-in ones and run by the same report page (period,
filters, totals, Excel / PDF / e-mail). DashboardManagement.reports asks this module for the keys dev-<id> (spec, listing).
"""
from zeo.tz import today as _zt_today   # v1.73.16 the user's / company's day, not the server's UTC day
import datetime
import html
import re
from decimal import Decimal, InvalidOperation

KEY_RE = re.compile(r'^dev-(\d+)$')
NUM_TYPES = ('number', 'money')


def _may(rep, c):
    if c.admin:
        return True
    if rep.who == 'admin':
        return False
    hr = bool({'change_emp_master', 'add_emp_master', 'view_emp_master'} & set(c.codes))
    if rep.who == 'hr':
        return hr
    return True


def _enabled():
    try:
        from .models import DeveloperSettings
        return DeveloperSettings.get().developer_mode
    except Exception:   # noqa: BLE001
        return False


def listing(request, c):
    from .models import ModuleReport
    from ZeoFlow.models import CustomModule
    if not _enabled():
        return []
    mods = {m.key: m for m in CustomModule.objects.filter(active=True)}
    out = []
    for r in ModuleReport.objects.filter(active=True):
        m = mods.get(r.module_key)
        if m is None or not _may(r, c):
            continue
        out.append({'key': f'dev-{r.pk}', 'title': r.name, 'section': r.section or 'Custom modules', 'description': r.description or m.plural,
                    'period': 'month' if r.date_field else None, 'perms': [], 'custom': True})
    return out


def spec(key):
    m = KEY_RE.match(str(key or ''))
    if not m:
        return None
    from django.db import connection
    if getattr(connection, 'schema_name', 'public') == 'public':
        return None
    try:
        from .models import ModuleReport
        rep = ModuleReport.objects.filter(pk=int(m.group(1)), active=True).first()
    except Exception:   # noqa: BLE001
        return None
    if rep is None or not _enabled():
        return None

    def fn(run):
        if not _may(rep, run.c):
            raise ValueError('You may not run this report.')
        return run_report(rep, run.request, run.date_from, run.date_to)
    # permission codes ['*']: the report centre leaves the check to fn (the report's "who": administrators / HR / everyone who sees the records)
    return (rep.name, rep.section or 'Custom modules', rep.description, fn, 'month' if rep.date_field else None, ['*'])


def _num(v):
    try:
        return Decimal(str(v)) if v not in (None, '') else Decimal('0')
    except (InvalidOperation, ValueError):
        return Decimal('0')


def run_report(rep, request, date_from=None, date_to=None):
    from ZeoFlow.models import CustomModule
    from ZeoFlow import custom as C
    m = CustomModule.objects.get(key=rep.module_key)
    fields = {f['key']: f for f in m.fields}
    qs = C.scope(m, request)
    if rep.states:
        qs = qs.filter(state__in=rep.states)
    states = {s['key']: s['label'] for s in m.blueprint.get('states', [])}
    rows_in = []
    for r in qs.order_by('number'):
        if rep.date_field and (date_from or date_to):
            d = r.data.get(rep.date_field)
            try:
                d = datetime.date.fromisoformat(str(d)[:10]) if d else None
            except ValueError:
                d = None
            if d is None or (date_from and date_from.year > 1900 and d < date_from) or (date_to and date_to.year < 2999 and d > date_to):
                continue
        rows_in.append(r)
    names = _names(m, rows_in)

    def show(r, k):
        f = fields.get(k)
        v = r.data.get(k)
        if f is None:
            return v
        if f['type'] in ('employee', 'link'):
            return names.get((k, v), '')
        if f['type'] in NUM_TYPES or (f['type'] == 'formula' and f.get('result', 'number') in NUM_TYPES):
            return float(_num(v)) if v not in (None, '') else None
        if f['type'] == 'yesno':
            return 'Yes' if v else 'No'
        if f['type'] == 'table':
            return len(v or [])
        return v

    def coltype(k):
        f = fields.get(k) or {}
        if f.get('type') in NUM_TYPES or (f.get('type') == 'formula' and f.get('result', 'number') in NUM_TYPES) or f.get('type') == 'table':
            return 'number'
        if f.get('type') == 'date' or f.get('result') == 'date':
            return 'date'
        return 'text'

    cols_keys = [k for k in (rep.columns or [f['key'] for f in m.fields if f.get('in_list', True)][:8]) if k in fields]
    if rep.kind == 'summary':
        groups = [g for g in (rep.group_by or []) if g in fields or g.startswith('month:') or g == 'state']
        sums = [k for k in cols_keys if coltype(k) == 'number']
        acc = {}
        for r in rows_in:
            key = []
            for g in groups:
                if g == 'state':
                    key.append(states.get(r.state, r.state))
                elif g.startswith('month:'):
                    d = str(r.data.get(g[6:]) or '')[:7]
                    key.append(d or '–')
                else:
                    key.append(show(r, g) or '–')
            a = acc.setdefault(tuple(key), {'count': 0, **{k: Decimal('0') for k in sums}})
            a['count'] += 1
            for k in sums:
                a[k] += _num(r.data.get(k)) if fields[k]['type'] != 'table' else len(r.data.get(k) or [])
        cols = [(f'g{i}', ('Month' if g.startswith('month:') else 'State' if g == 'state' else fields[g]['label']), 'text') for i, g in enumerate(groups)]
        cols += [('count', 'Records', 'number')] + [(k, fields[k]['label'], 'number') for k in sums]
        rows = []
        for key, a in sorted(acc.items(), key=lambda kv: [str(x) for x in kv[0]]):
            row = {f'g{i}': v for i, v in enumerate(key)}
            row['count'] = a['count']
            row.update({k: float(a[k]) for k in sums})
            rows.append(row)
        return cols, rows, {'filters': [f'g{i}' for i in range(len(groups))]}
    cols = [('number', 'No.', 'text')] + [(k, fields[k]['label'], coltype(k)) for k in cols_keys] + [('state', 'State', 'text')]
    rows = []
    for r in rows_in:
        row = {'number': r.number, 'state': states.get(r.state, r.state), '_link': f'/main-sidebar/custom/{m.key}/{r.pk}'}
        row.update({k: show(r, k) for k in cols_keys})
        rows.append(row)
    return cols, rows, {'filters': ['state']}


def _names(m, recs):
    from .fields import link_label
    out = {}
    for f in m.fields:
        if f['type'] not in ('employee', 'link'):
            continue
        target = 'employee' if f['type'] == 'employee' else f['options']
        for r in recs:
            v = r.data.get(f['key'])
            if v not in (None, '') and (f['key'], v) not in out:
                out[(f['key'], v)] = link_label(target, v) or ''
    return out


def check_report(data, m):
    """Checks a report design; returns the cleaned dict or raises ValueError."""
    fields = {f['key']: f for f in m.fields}
    cols = [c for c in (data.get('columns') or []) if c]
    bad = [c for c in cols if c not in fields]
    if bad:
        raise ValueError(f'"{bad[0]}" is not a field of {m.key}.')
    groups = [g for g in (data.get('group_by') or []) if g]
    for g in groups:
        if g != 'state' and not (g.startswith('month:') and fields.get(g[6:], {}).get('type') in ('date',)) and g not in fields:
            raise ValueError(f'Group by "{g}": a field of the module, "state" or month:<date field>.')
    df = str(data.get('date_field') or '')
    if df and (fields.get(df) or {}).get('type') != 'date' and (fields.get(df) or {}).get('result') != 'date':
        raise ValueError('The period works on a date field of the module.')
    kind = data.get('kind') or 'list'
    if kind not in ('list', 'summary'):
        raise ValueError('Kind: list or summary.')
    if kind == 'summary' and not groups:
        raise ValueError('A summary report needs at least one "group by".')
    st = [s for s in (data.get('states') or []) if s]
    known = {s['key'] for s in m.blueprint.get('states', [])}
    if any(s not in known for s in st):
        raise ValueError('A state is not a state of the module.')
    who = data.get('who') or 'hr'
    if who not in ('admin', 'hr', 'all'):
        raise ValueError('Who: admin, hr or all.')
    name = str(data.get('name') or '').strip()
    if not name:
        raise ValueError('Give the report a name.')
    return {'name': name[:120], 'description': str(data.get('description') or '')[:300], 'section': str(data.get('section') or 'Custom modules')[:60],
            'kind': kind, 'columns': cols, 'group_by': groups, 'date_field': df, 'states': st, 'who': who}


# ------------------------------------------------------------------ print layouts
PLACE = re.compile(r'\{\{\s*([a-z][a-z0-9_]*)(?:\.([a-z][a-z0-9_]*))?\s*\}\}')
ROWS = re.compile(r'\{%\s*rows\s+([a-z][a-z0-9_]*)\s*%\}(.*?)\{%\s*endrows\s*%\}', re.S)
SAFE_TAGS = re.compile(r'<\s*(script|iframe|object|embed|link|meta|base|form)\b', re.I)


def check_html(text):
    if SAFE_TAGS.search(text or ''):
        raise ValueError('Print layouts may not hold <script>, <iframe>, <object>, <embed>, <link>, <meta>, <base> or <form>.')
    if re.search(r'\bon[a-z]+\s*=', text or '', re.I) or re.search(r'javascript:', text or '', re.I):
        raise ValueError('Print layouts may not hold event handlers (onclick=…) or javascript: links.')
    return text


def render(layout, r):
    """HTML of a record: {{ field }}, {{ number }}, {{ state }}, {{ company }}, {{ today }}, {{ field.label }} for links,
    {% rows child %} … {{ row.column }} … {% endrows %} for child tables. Every value is escaped."""
    from django.db import connection
    m = r.module
    fields = {f['key']: f for f in m.fields}
    names = _names(m, [r])
    states = {s['key']: s['label'] for s in m.blueprint.get('states', [])}

    def val(k, sub=None, data=None, row=False):
        data = r.data if data is None else data
        if not row:
            if k == 'number':
                return r.number
            if k == 'state':
                return states.get(r.state, r.state)
            if k == 'today':
                return _zt_today().strftime('%d/%m/%Y')
            if k == 'company':
                try:
                    from UserManagement.models import company
                    c = company.objects.filter(schema_name=connection.schema_name).first()
                    return getattr(c, 'name', '') or connection.schema_name
                except Exception:   # noqa: BLE001
                    return connection.schema_name
        v = data.get(k)
        f = fields.get(k)
        if not row and f is not None and f['type'] in ('employee', 'link'):
            return names.get((k, v), '')
        if isinstance(v, bool):
            return 'Yes' if v else 'No'
        if isinstance(v, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', v):
            return datetime.date.fromisoformat(v).strftime('%d/%m/%Y')
        return '' if v is None else v

    def rows(mm):
        child, body = mm.group(1), mm.group(2)
        out = []
        for i, row in enumerate(r.data.get(child) or [], 1):
            def rp(x):
                if x.group(1) == 'row' and x.group(2):
                    return html.escape(str(val(x.group(2), data=row, row=True)))
                if x.group(1) == 'n':
                    return str(i)
                return html.escape(str(val(x.group(1))))
            out.append(PLACE.sub(rp, body))
        return ''.join(out)
    text = ROWS.sub(rows, layout.html or '')
    text = PLACE.sub(lambda x: html.escape(str(val(x.group(1)))), text)
    return check_html(text)


def qr_svg(text, kind='qr'):
    """SVG of a QR code / Code 128 barcode (reportlab, already in the requirements)."""
    from reportlab.graphics import renderSVG
    from reportlab.graphics.shapes import Drawing
    text = str(text or '')[:300] or ' '
    if kind == 'barcode':
        from reportlab.graphics.barcode import createBarcodeDrawing
        d = createBarcodeDrawing('Code128', value=text, barHeight=40, humanReadable=True)
    else:
        from reportlab.graphics.barcode.qr import QrCodeWidget
        w = QrCodeWidget(text)
        b = w.getBounds()
        size = 140
        d = Drawing(size, size, transform=[size / (b[2] - b[0]), 0, 0, size / (b[3] - b[1]), 0, 0])
        d.add(w)
    return renderSVG.drawToString(d)
