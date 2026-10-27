"""v1.62.0 reports – presentation of the report centre (one place for every report, numbers unchanged).

ReportView calls ``present(key, columns, rows, totals, request)`` after a report ran.  It only changes how values read:
  * booleans → "Yes" / "No", ISO timestamps flagged as date-time, times without seconds, nationality in title case,
    login names ("gdis_khalid", "Maria Santos (gdis_maria)") → the person's name;
  * column hints for the screen: fmt (money / pct / hours / int / dec / datetime), status (coloured word), long (ellipsis),
    hidden (Branch / Department / Designation / Category start hidden on employee reports – still under Columns);
  * totals hints: which totals are averages (never summed), the few main totals of the summary strip;
  * report hints (REPORT_UI): chart, empty-state text, variants (merged reports shown as tabs), redirects of merged
    reports, default filters, title fixes.
The figures of a report are never recalculated here – except averages that were summed (marked ``agg: avg``).
"""
import re
from datetime import datetime, time as dtime

# ------------------------------------------------------------------ report hints
# chart: {type: bar | hbar | line | funnel, x: column, series: [columns], stack: bool, facet: column, agg: sum}
REPORT_UI = {
    # People
    'headcount': {'chart': {'type': 'hbar', 'x': 'department', 'series': ['active'], 'agg': 'sum'}, 'main': ['active', 'joined', 'left'],
                  'variants': 'org', 'about': ('branch', 'department'),
                  'empty': 'Comes from the employee master: active employees and their joining / leaving dates.'},
    'departments': {'variants': 'org', 'about': ('branches',)},
    'designations': {'variants': 'org', 'about': ('branches',)},
    'categories': {'variants': 'org', 'about': ('branches',)},
    'documents': {'sort': ('days_left', 1), 'empty': 'Comes from the employee documents with an expiry date (Employees › Documents).'},
    'location': {'chart': {'type': 'bar', 'x': 'branch', 'series': ['nationals', 'expats'], 'stack': True, 'agg': 'sum'}, 'about': ('branch',)},
    'demographics': {'chart': {'type': 'hbar', 'x': 'group', 'series': ['count'], 'facet': 'dimension'}},
    'turnover-trend': {'chart': {'type': 'bar', 'x': 'month', 'series': ['joined', 'left']}, 'main': ['joined', 'left']},
    'probation': {'empty': 'Comes from the joining date and the probation period of each employee.'},
    'birthdays': {'title': 'Birthdays and anniversaries'},
    # Leave & attendance
    'leave-statement': {'no_totals': ('days',), 'empty': 'Comes from the leave requests, encashments and the yearly leave allocation.'},
    'leave-balance': {'hide_zero': ('per_year', 'earned', 'taken', 'balance', 'pending', 'available'), 'main': ['earned', 'taken', 'available', 'pending']},
    'attendance': {'variants': 'att-daily', 'main': ['hours', 'late', 'early', 'ot'],
                   'empty': 'Comes from punches and attendance entries (Time › Punches).'},
    'att-daily-status': {'variants': 'att-daily', 'empty': 'Comes from the attendance rules engine (daily results of punches and shifts).'},
    'attendance-summary': {'main': ['present', 'absent', 'leave', 'late', 'ot', 'hours']},
    'att-late-early': {'title': 'Late arrivals and early departures', 'main': ['late', 'early'],
                       'empty': 'Comes from punches and the shift of the day – check Time › Punches.'},
    'attendance-late-early': {'redirect': 'att-late-early'},
    'late-arrivals': {'redirect': 'att-late-early'},
    'early-departures': {'redirect': 'att-late-early'},
    'missing-punch': {'redirect': 'att-missing-punch'},
    'att-missing-punch': {'empty': 'Days with a check-in and no check-out (or the reverse), from the attendance rules.'},
    'late-early': {'title': 'Late-in / early-out requests', 'empty': 'Late-in and early-out requests (excuses) that employees sent.'},
    'overtime-summary': {'variants': 'ot', 'main': ['hours', 'approved', 'waiting']},
    'overtime': {'variants': 'ot'},
    'department-leave': {'chart': {'type': 'bar', 'x': 'department', 'series': ['approved'], 'stackby': 'type', 'agg': 'sum'}, 'about': ('department',)},
    'shift-requests': {'empty': 'Shift swaps, changes and open-shift claims that employees sent (Time › Shift requests).'},
    'absence': {'main': ['days']},
    # Payroll
    'payroll-register': {'main': ['gross', 'deductions', 'net', 'arrears']},
    'payroll-department': {'main': ['employees', 'gross', 'deductions', 'net'], 'about': ('branch', 'department')},
    'payroll-components': {'main': ['signed']},
    'payroll-mom': {'default_filters': {'changed': 'Yes'}, 'main': ['prev_net', 'net', 'net_change']},
    'payroll-variance': {'default_filters': {'changed': 'Yes'}},
    'payslip-status': {'main': ['gross', 'net']},
    'salary': {'main': ['total']},
    'salary-revisions': {'main': ['change'], 'empty': 'Salary changes with an effective date (Payroll › Salary revisions).'},
    'gratuity': {'main': ['amount']},
    'leave-liability': {'main': ['liability', 'liability_gross']},
    'loans': {'main': ['amount', 'repaid', 'balance']},
    'allowance-register': {'main': ['total', 'gross']},
    'deduction-register': {'main': ['total', 'gross']},
    'overtime-pay': {'main': ['hours', 'due', 'paid']},
    'payroll-cost': {'chart': {'type': 'hbar', 'x': 'department', 'series': ['cost'], 'agg': 'sum'}, 'main': ['gross', 'net', 'cost']},
    'department-cost': {'chart': {'type': 'hbar', 'x': 'department', 'series': ['cost'], 'agg': 'sum'}, 'main': ['gross', 'cost'],
                        'about': ('branch', 'department')},
    'bank-transfer': {'main': ['amount']},
    'wps-status': {'main': ['employees', 'net']},
    # Requests, exits & assets
    'asset-register': {'title': 'Asset register', 'variants': 'assets', 'main': ['cost', 'accumulated', 'book_value'], 'about': ('branch',)},
    'assets': {'variants': 'assets'},
    'exits': {'main': ['gratuity', 'settlement']},
    'final-settlements': {'main': ['gratuity', 'net']},
    # Talent
    'recruitment': {'chart': {'type': 'funnel', 'series': ['applicants', 'screening', 'interview', 'offer', 'hired']}, 'about': ('branch', 'department')},
    'source-effectiveness': {'chart': {'type': 'hbar', 'x': 'source', 'series': ['candidates', 'hired']}},
    'attendance-register': {'main': ['p', 'a', 'l', 'late', 'ot']},
    'department-performance': {'main': ['sheets', 'rated'], 'chart': {'type': 'bar', 'x': 'department', 'series': ['r1', 'r2', 'r3', 'r4', 'r5'], 'stack': True, 'agg': 'sum'},
                               'about': ('department',)},
    'time-to-hire': {'about': ('branch', 'department')},
    'time-to-fill': {'about': ('branch', 'department')},
    'cost-per-hire': {'about': ('branch', 'department'),
                      'empty': 'Hires per position; recruitment expenses are booked in Expense management.'},
    'candidates': {'about': ('branch', 'department')},
    # Benefits and engagement
    'ppl-engagement': {'variants': 'survey'},
    'ppl-survey': {'variants': 'survey'},
    'ppl-recognition-leaders': {'variants': 'recog', 'main': ['received', 'points']},
    'ppl-recognition': {'variants': 'recog'},
    'ppl-360-summary': {'variants': '360', 'avg': ('overall',)},
    'ppl-review-360': {'variants': '360', 'avg': ('overall',)},
}

# merged reports shown as tabs (the first one is in the menu; the others keep their address)
VARIANTS = {
    'org': [('headcount', 'Headcount'), ('departments', 'Departments'), ('designations', 'Designations'), ('categories', 'Categories')],
    'att-daily': [('attendance', 'Day by day'), ('att-daily-status', 'Shift rules detail')],
    'ot': [('overtime-summary', 'By employee'), ('overtime', 'By day')],
    'assets': [('asset-register', 'Register and book value'), ('assets', 'Who holds what')],
    'survey': [('ppl-engagement', 'Engagement'), ('ppl-survey', 'Surveys')],
    'recog': [('ppl-recognition-leaders', 'Leaders'), ('ppl-recognition', 'Every recognition')],
    '360': [('ppl-360-summary', 'Summary'), ('ppl-review-360', 'Every review')],
}

# role packs of the report home ("Start here")
PACKS = [
    {'key': 'hr', 'label': 'HR', 'reports': ['headcount', 'joiners-leavers', 'documents', 'probation', 'leave-balance', 'absence', 'certificates', 'employees']},
    {'key': 'payroll', 'label': 'Payroll', 'reports': ['payroll-register', 'payroll-variance', 'payslip-status', 'bank-transfer', 'wps-status', 'loans', 'overtime-pay', 'leave-encashment']},
    {'key': 'finance', 'label': 'Finance', 'reports': ['payroll-cost', 'department-cost', 'gratuity', 'leave-liability', 'payroll-components', 'final-settlements', 'asset-register', 'advances']},
    {'key': 'management', 'label': 'Management', 'reports': ['headcount', 'turnover-trend', 'department-cost', 'demographics', 'recruitment', 'department-performance', 'attendance-summary', 'location']},
]

ORG_KEYS = ('branch', 'department', 'designation', 'category')
STATUS_KEYS = re.compile(r'^(status|state|stage|run_status|eos_status|result|correction|check|probation|offer|seat|ld|ack|overdue|expiring_\d+)$')
STATUS_LABELS = re.compile(r'(status|state|^stage|^result|^check$|^wps$|^payment$)', re.I)
LONG_KEYS = re.compile(r'(reason|note|remarks|description|why|strengths|to_develop|basis|plan|title|issue)', re.I)
MONEY_LABEL = re.compile(r'\((aed|usd|sar|qar|omr|bhd|kwd|inr)\)|\b(cost|amount|salary|gross|net pay|liability|gratuity|book value|depreciation|'
                         r'claimed|share / month|cost / month|total / (month|year)|to recover|recovered|outstanding|basic|package|earnings|'
                         r'deductions|value|emi|repaid|balance due|instalment)\b', re.I)
NOT_MONEY = re.compile(r'(days?|hours?|\(h\)|\(min\)|minutes|%|rate|count|employees|headcount|instalments left|years|months|points)', re.I)
PCT = re.compile(r'(%|percent|turnover|annualised|emiratisation|share)', re.I)
HOURS = re.compile(r'(hours|\(h\)|hours worked|ot hours)', re.I)
AVG_LABEL = re.compile(r'(overall|average|\bavg\b|per head|per employee|per hire|rate\b)', re.I)
ISO_TS = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}')
HMS = re.compile(r'^\d{2}:\d{2}:\d{2}(\.\d+)?$')
USERISH = re.compile(r'^[a-z][a-z0-9]*_[a-z0-9_.]+$')


def _people(request):
    """username → the person's name (employee name, else the login's e-mail name)."""
    out = {}
    try:
        from django.apps import apps
        E = apps.get_model('EmpManagement', 'emp_master')
        for u, f, l in E.objects.filter(users__isnull=False).values_list('users__username', 'emp_first_name', 'emp_last_name'):
            if u:
                out[u] = ' '.join(x for x in (f, l) if x).strip() or u
        U = apps.get_model('UserManagement', 'CustomUser')
        for u in U.objects.filter(username__endswith='_admin').values_list('username', flat=True):
            out.setdefault(u, 'Administrator')
    except Exception:
        pass
    return out


def _person(v, people):
    if not isinstance(v, str) or not v:
        return v
    s = v.strip()
    if s in people:
        return people[s]
    m = re.match(r'^(.*\S)\s*\(([^()]+)\)$', s)   # "Maria Santos (gdis_maria)"
    if m and m.group(2) in people:
        return m.group(1)
    if ',' in s and all(p.strip() in people for p in s.split(',') if p.strip()):
        return ', '.join(people[p.strip()] for p in s.split(',') if p.strip())
    return v


def _title_case(v):
    if isinstance(v, str) and v.isupper() and len(v) > 2:
        return ' '.join(w.capitalize() for w in v.lower().split(' '))
    return v


def _fmt(key, label, values):
    """money | pct | hours | int | dec for a number column."""
    if PCT.search(label) or key in ('pct', 'turnover', 'annualised', 'emiratisation', 'share', 'rate', 'hire_rate', 'interview_rate', 'engagement'):
        if not MONEY_LABEL.search(label) or '%' in label:
            return 'pct'
    if MONEY_LABEL.search(label) and not NOT_MONEY.search(label):
        return 'money'
    if key.startswith('c_') or key in ('gross', 'net', 'cost', 'amount', 'basic', 'liability', 'liability_gross', 'gratuity', 'leave_accrual',
                                       'pension', 'book_value', 'accumulated', 'per_head', 'emi', 'repaid', 'due', 'paid', 'difference',
                                       'employee_share', 'employer_cost', 'total_month', 'total_year', 'employee_cost', 'approved_amount'):
        if not NOT_MONEY.search(label) or '(AED)' in label:
            return 'money'
    if HOURS.search(label):
        return 'hours'
    nums = [x for x in values if isinstance(x, (int, float)) and not isinstance(x, bool)]
    if nums and all(float(x).is_integer() for x in nums):
        return 'int'
    return 'dec'


def present(key, columns, rows, totals, request=None, has_employee=None):
    """Changes rows in place; returns (columns, totals, meta)."""
    ui = REPORT_UI.get(key, {})
    people = None
    keys = [c['key'] for c in columns]
    has_emp = 'employee' in keys if has_employee is None else has_employee
    about = set(ui.get('about') or ())
    for c in columns:
        k, t = c['key'], c['type']
        vals = [r.get(k) for r in rows]
        # booleans → Yes / No
        if any(isinstance(v, bool) for v in vals):
            for r in rows:
                if isinstance(r.get(k), bool):
                    r[k] = 'Yes' if r[k] else 'No'
            c['type'] = t = 'text'
            c['fmt'] = 'bool'
        if t == 'date':
            if any(isinstance(v, str) and ISO_TS.match(v) for v in vals):
                c['fmt'] = 'datetime'
        elif t == 'number':
            c['fmt'] = _fmt(k, c.get('label') or '', vals)
        else:
            if any(isinstance(v, dtime) for v in vals):   # times without seconds
                for r in rows:
                    if isinstance(r.get(k), dtime):
                        r[k] = r[k].strftime('%H:%M')
                vals = [r.get(k) for r in rows]
            sv = [v for v in vals if isinstance(v, str) and v]
            if sv and all(HMS.match(v) for v in sv[:50]):
                for r in rows:
                    if isinstance(r.get(k), str) and HMS.match(r[k]):
                        r[k] = r[k][:5]
            if sv and any(USERISH.match(v.strip()) or re.search(r'\([a-z][a-z0-9]*_[a-z0-9_.]+\)$', v) for v in sv[:200]):
                people = people if people is not None else _people(request)
                if people:
                    for r in rows:
                        r[k] = _person(r.get(k), people)
            if k == 'nationality' or (c.get('label') or '').lower() == 'nationality':
                for r in rows:
                    r[k] = _title_case(r.get(k))
            if STATUS_KEYS.match(k) or (STATUS_LABELS.search(c.get('label') or '') and len({str(v) for v in sv}) <= 25):
                c['status'] = True
            if sv and set(sv) <= {'Yes', 'No'}:
                c['fmt'] = 'bool'
            if LONG_KEYS.search(k) and sv and sum(len(v) for v in sv) / len(sv) > 28:
                c['long'] = True
            if k == 'type':
                for r in rows:
                    if r.get(k) == 'Kpi':
                        r[k] = 'KPI'
        if has_emp and k in ORG_KEYS and k not in about:
            c['hidden'] = True
        # salary components nobody has (employer pension, gratuity structure lines …) start hidden
        if c['type'] == 'number' and k.startswith('c_') and rows and all(not r.get(k) for r in rows):
            c['hidden'] = True
        # day codes of the attendance grid / daily list: coloured codes
        if re.match(r'^d\d{8}$', k) or (k == 'code' and key == 'attendance'):
            c['daycode'] = True
        # a note that says the same on every row ("No punch and no leave") starts hidden
        if c['type'] == 'text' and k in ('note', 'source', 'sources') and len(rows) > 5 and len({str(r.get(k) or '') for r in rows}) == 1:
            c['hidden'] = True
    # a record named "BEN-00064 Medical insurance – basic – Ashok Rao" in a row of Ashok Rao → "Medical insurance – basic"
    if key in ('ppl-claim',) and 'enrolment' in keys:
        for r in rows:
            v, emp = r.get('enrolment'), re.sub(r'\s*\([^()]*\)$', '', str(r.get('employee') or ''))
            if isinstance(v, str):
                v = re.sub(r'^[A-Z]{2,6}-\d+\s+', '', v)
                if emp and v.endswith(' – ' + emp):
                    v = v[:-len(' – ' + emp)]
                r['enrolment'] = v
    # demographics: nationality groups in title case
    if key == 'demographics':
        for r in rows:
            if str(r.get('dimension', '')).lower().startswith('nationality'):
                r['group'] = _title_case(r.get('group'))
    # totals: averages are averaged, never summed
    tmeta = {}
    avg = set(ui.get('avg') or ())
    for c in columns:
        k = c['key']
        if c['type'] != 'number':
            continue
        if k in avg or (k in totals and AVG_LABEL.search(c.get('label') or '')):
            nums = [r.get(k) for r in rows if isinstance(r.get(k), (int, float)) and not isinstance(r.get(k), bool)]
            if k == 'per_head' and totals.get('employees') and 'cost' in totals:   # cost per employee of the whole report
                totals[k] = round(totals['cost'] / totals['employees'], 2)
                tmeta[k] = {'agg': 'ratio', 'of': ['cost', 'employees']}
            elif nums:
                totals[k] = round(sum(nums) / len(nums), 2)
                tmeta[k] = {'agg': 'avg'}
            else:
                totals.pop(k, None)
    for k in ui.get('no_totals') or ():
        totals.pop(k, None)
    main = [k for k in (ui.get('main') or []) if k in totals]
    if not main:
        order = sorted(totals, key=lambda k: _rank(next((c for c in columns if c['key'] == k), {})))
        main = order[:4]
    meta = {'totals_meta': tmeta, 'totals_main': main[:6]}
    if ui.get('chart'):
        meta['chart'] = ui['chart']
    if ui.get('empty'):
        meta['empty_hint'] = ui['empty']
    if ui.get('default_filters'):
        meta['default_filters'] = ui['default_filters']
    if ui.get('hide_zero'):
        meta['hide_zero'] = list(ui['hide_zero'])
    if ui.get('sort'):
        meta['sort'] = list(ui['sort'])
    if ui.get('redirect'):
        meta['redirect'] = ui['redirect']
    if ui.get('variants'):
        meta['variants'] = [{'key': k, 'label': l} for k, l in VARIANTS[ui['variants']]]
    return columns, totals, meta


def _rank(c):
    """Main totals first: net, gross, cost, amounts, then hours and counts."""
    k, label, f = c.get('key', ''), (c.get('label') or '').lower(), c.get('fmt')
    for i, w in enumerate(('net', 'gross', 'cost', 'total', 'amount', 'balance', 'liability')):
        if k == w or label.startswith(w):
            return i
    return {'money': 10, 'hours': 20, 'int': 30, 'dec': 40}.get(f, 50)


def title_of(key, title):
    return REPORT_UI.get(key, {}).get('title') or title


def redirect_of(key):
    return REPORT_UI.get(key, {}).get('redirect')


def hidden_in_home():
    """Reports reached through another report (merged: redirects and the non-first tabs)."""
    out = {k for k, v in REPORT_UI.items() if v.get('redirect')}
    for members in VARIANTS.values():
        out.update(k for k, _ in members[1:])
    return out


def home_meta():
    return {'packs': PACKS, 'merged': sorted(hidden_in_home()), 'redirects': {k: v['redirect'] for k, v in REPORT_UI.items() if v.get('redirect')}}
