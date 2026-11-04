"""v1.65.0 – leave rules missing after the leave audit of 1 November 2026 (pending items 82–97; 95 out of office done in 1.62.11).

Every rule is a leave-type rule (LeaveTypeRule, Leave › Setup › Leave rules) or a company setting (LeaveRuleSettings) and is OFF –
the behaviour before 1.65.0 – unless the law requires it (nursing breaks, item 94).

Legal facts used (each checked in two sources, 3 Nov 2026):
* Parental leave: 5 working days, paid, from the birth until the child is 6 months old, may be split
  – MOHRE "Parental leave" https://www.mohre.gov.ae/en/paternity-leave ; Khaleej Times "New UAE labour laws: Maternity, paternity
    leaves explained" (Decree-Law 33/2021 Art. 32) https://www.khaleejtimes.com/uae/legal/new-uae-labour-laws-maternity-paternity-leaves-explained
* Maternity leave may start up to 30 days before the expected delivery (u.ae "Maternity leave").
* Nursing breaks: one or two breaks a day of at most one hour in total, fully paid, for 6 months after the delivery (Art. 30)
  – u.ae https://u.ae/en/information-and-services/jobs/employment-in-the-private-sector/types-of-leaves-and-entitlements-in-the-private-sector/maternity-leave ;
    Gulf News https://gulfnews.com/living-in-uae/ask-us/new-uae-labour-law-how-your-maternity-leave-and-entitlements-have-changed-1.1691762077443 ;
    Khaleej Times (above, Art. 30(9)).
* Unpaid leave is not counted in the period of service (Art. 33(2)); days of absence without pay are not counted within the period
  of service (Art. 51(4)) – Gulf News https://gulfnews.com/living-in-uae/ask-us/new-uae-labour-law-can-i-take-unpaid-leave-1.1643361918409 ;
  Khaleej Times https://www.khaleejtimes.com/uae/legal/gratuity-in-uae-will-leave-without-pay-impact-final-settlement-calculations .
  How this reduces ANNUAL LEAVE days is not stated by either source: the company setting (item 89) is "to be confirmed".
* Annual leave in the first year: 2 days a month after 6 months; leave pay for the fractions of the last year (Art. 29) – u.ae
  "Annual leave"; Gulf News (resignation / final settlement). Nothing found for service of less than 6 months: item 90 "to be confirmed".
* Annual leave pay: Art. 29(6) the worker is paid for the leave period – the law does not say "before the leave": item 85 is a
  company choice (Gulf News "Not paying employees for annual leave").
* Hajj: up to 30 days unpaid, once with the same employer – Gulf News "Hajj leave in UAE private sector"; Meydan FZ "Hajj leave UAE".
  No official MOHRE text found and no minimum service: the N years of service of item 91 are "to be confirmed".
* Public holidays inside annual leave count as part of the leave (u.ae "Annual leave") – the law's default; "calendar days except
  public holidays" (item 87) and the sandwich rule (item 86) are company options.
"""
import calendar as _cal
import logging
import threading
from contextlib import contextmanager
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.apps import apps
from django.db import transaction
from django.db.models import Q, Sum

from Translations.i18n import tr

log = logging.getLogger(__name__)
M = apps.get_model
Q2 = Decimal('0.01')
_x = threading.local()


# ------------------------------------------------------------------ settings and rules
def settings():
    from .models import LeaveRuleSettings
    try:   # read only: the row is made when HR first saves the settings
        return LeaveRuleSettings.objects.order_by('id').first() or LeaveRuleSettings()
    except Exception:   # table not migrated in this schema: the defaults (= before 1.65.0)
        return LeaveRuleSettings()


def _twins(lt_id):
    """The leave type and its copies in the other branches (same code, else same name)."""
    from . import engine
    by_id, _, _ = engine._type_index()
    me = by_id.get(lt_id)
    if not me:
        return [lt_id]
    out = [lt_id]
    for i, (b, c, n) in by_id.items():
        if i != lt_id and ((me[1] and c == me[1]) or (not me[1] and me[2] and n == me[2])):
            out.append(i)
    return out


def rule_for(lt):
    """The LeaveTypeRule of a leave type (or of its branch copy), or None (= no rule switched on)."""
    from .models import LeaveTypeRule
    lt_id = lt if isinstance(lt, int) else getattr(lt, 'pk', None)
    if not lt_id:
        return None
    cache = getattr(_x, 'rules', None)
    if cache is not None and lt_id in cache:
        return cache[lt_id]
    try:
        r = LeaveTypeRule.objects.filter(leave_type_id=lt_id).first()
        if r is None:
            ids = _twins(lt_id)
            r = LeaveTypeRule.objects.filter(leave_type_id__in=ids).order_by('id').first() if len(ids) > 1 else None
    except Exception:
        r = None
    if cache is not None:
        cache[lt_id] = r
    return r


@contextmanager
def extras(**kw):
    """Around a save from a screen: what the request carries besides its columns (birth_date, extension_of)."""
    prev = getattr(_x, 'extras', None)
    _x.extras = dict(prev or {}, **{k: v for k, v in kw.items() if v not in (None, '')})   # nested: the outer values stay
    try:
        yield
    finally:
        _x.extras = prev


def info_of(req):
    from .models import LeaveRequestInfo
    if not getattr(req, 'pk', None):
        return None
    try:
        return LeaveRequestInfo.objects.filter(request_id=req.pk).first()
    except Exception:
        return None


def extra(req, key):
    """The request's birth date / extension_of: set on the instance, given with the save, else stored."""
    v = getattr(req, '_lv_' + key, None)
    if v is not None:
        return v
    x = getattr(_x, 'extras', None) or {}
    if key in x:
        v = x[key]
        if key == 'birth_date' and isinstance(v, str):
            from django.utils.dateparse import parse_date
            v = parse_date(v)
        return v
    i = info_of(req)
    return getattr(i, key + ('_id' if key == 'extension_of' else ''), None) if i else None


def remember(req, created=False):
    """post_save of a leave request: keep the birth date / extension link given with it."""
    from .models import LeaveRequestInfo
    bd, ext = None, None
    x = getattr(_x, 'extras', None) or {}
    if getattr(req, '_lv_birth_date', None) is not None or 'birth_date' in x:
        bd = extra(req, 'birth_date')
    if getattr(req, '_lv_extension_of', None) is not None or 'extension_of' in x:
        ext = extra(req, 'extension_of')
    if bd is None and ext is None:
        return
    i, _ = LeaveRequestInfo.objects.get_or_create(request_id=req.pk)
    if bd is not None:
        i.birth_date = bd
    if ext is not None:
        i.extension_of_id = int(ext)
        if bd is None and i.birth_date is None:   # an extension keeps the birth of its leave
            src = LeaveRequestInfo.objects.filter(request_id=int(ext)).values_list('birth_date', flat=True).first()
            i.birth_date = src
    i.save()


def add_months(d, n):
    y, m = divmod(d.month - 1 + n, 12)
    y, m = d.year + y, m + 1
    return date(y, m, min(d.day, _cal.monthrange(y, m)[1]))


def _active(req, lt_ids):
    LR = M('calendars', 'employee_leave_request')
    qs = LR.objects.filter(employee_id=req.employee_id, leave_type_id__in=lt_ids, status__in=('pending', 'approved'))
    return qs.exclude(pk=req.pk) if req.pk else qs


# ------------------------------------------------------------------ checks on a request (engine.validate_request)
def request_problems(req, line, days):
    """Errors of the 1.65.0 rules for a request being sent / changed (pending)."""
    out = []
    lt, emp = req.leave_type, req.employee
    rule = rule_for(lt)
    if rule is not None and rule.birth_required:
        out += birth_problems(req, rule, line, days)
    if rule is not None and (rule.hr_eligibility or rule.min_service_years):
        out += eligibility_problems(req, rule)
    if rule is not None and rule.document_above_days is not None and line is None and days > float(rule.document_above_days) + 1e-9 \
            and not req.lv_document:   # item 92 – a leave type without a policy line: the document above N days
        out.append(tr('{0} of more than {1:g} days needs a supporting document – attach it to the request.', lt.name, float(rule.document_above_days)))
    e, _w = notice_period(req, rule)
    out += e
    return out


def birth_problems(req, rule, line, days):
    """Item 82: the birth date, the window from the birth, the days per birth."""
    out, lt = [], req.leave_type
    bd = extra(req, 'birth_date')
    if not bd:
        return [tr('{0}: give the date of birth of the child.', lt.name)]
    first = bd - timedelta(days=30) if rule.birth_window_from == 'before' else bd
    last = add_months(bd, int(rule.birth_window_months or 6)) - timedelta(days=1)
    if req.start_date < first:
        out.append(tr('{0} starts on or after {1:%d/%m/%Y} (the birth {2:%d/%m/%Y}).', lt.name, first, bd))
    if req.end_date > last:
        out.append(tr('{0} is taken within {1} months of the birth: by {2:%d/%m/%Y}.', lt.name, int(rule.birth_window_months or 6), last))
    if rule.one_per_birth:
        from .models import LeaveRequestInfo
        ids = [r.pk for r in _active(req, _twins(lt.pk))]
        same = set(LeaveRequestInfo.objects.filter(request_id__in=ids, birth_date=bd).values_list('request_id', flat=True))
        from . import engine
        LR = M('calendars', 'employee_leave_request')
        taken = round(sum(engine._days_of(r) for r in LR.objects.filter(pk__in=same)), 2)
        allowed = None
        if line is not None:
            allowed = float(line.max_per_request or line.days_per_year or 0) or None
        if allowed is None:
            if same:
                out.append(tr('{0} for the birth of {1:%d/%m/%Y} is already taken or requested.', lt.name, bd))
        elif taken + days > allowed + 1e-9:
            out.append(tr('{0} is {1:g} days for each birth: {2:g} already taken or requested for the birth of {3:%d/%m/%Y}, {4:g} left.',
                          lt.name, allowed, taken, bd, max(allowed - taken, 0)))
    return out


def skip_year_limit(req):
    """Item 82: with the birth rule the days are per birth – the yearly limit of the policy line does not apply (a second child in the
    same leave year)."""
    rule = rule_for(req.leave_type)
    return bool(rule is not None and rule.birth_required and rule.one_per_birth and extra(req, 'birth_date'))


def eligibility_problems(req, rule):
    """Item 91: HR confirmed the employee may take the leave (e.g. Hajj) – no religion or other protected attribute is stored – and the
    years of service."""
    from .models import LeaveEligibility
    from . import engine
    out, lt, emp = [], req.leave_type, req.employee
    if rule.hr_eligibility:
        e = LeaveEligibility.objects.filter(employee_id=emp.pk, leave_type_id__in=_twins(lt.pk)).order_by('-confirmed').first()
        if e is not None and e.taken_before:
            out.append(tr('{0} is taken once in service: HR recorded that {1} has taken it before.', lt.name, emp.emp_first_name))
        elif e is None or not e.confirmed:
            out.append(tr('{0}: HR has not confirmed that {1} may take it yet (Leave rules › Eligibility).', lt.name, emp.emp_first_name))
    if rule.min_service_years:
        svc = engine.months_of_service(emp, req.start_date)
        if svc < float(rule.min_service_years) * 12:
            out.append(tr('{0} needs {1:g} year(s) of service; {2} will have {3} months on {4:%d/%m/%Y}.', lt.name, float(rule.min_service_years),
                          emp.emp_first_name, svc, req.start_date))
    return out


def document_needed(req, line, days):
    """Item 92: the policy line needs a document – only above N days when the leave type's rule says so."""
    if not line.requires_document:
        return False
    rule = rule_for(req.leave_type)
    if rule is not None and rule.document_above_days is not None:
        return days > float(rule.document_above_days) + 1e-9
    return True


# ------------------------------------------------------------------ item 97 leave during the notice period
def notice_of(emp):
    """(resigned on, last working day) of the employee's resignation of this employment (not rejected / cancelled), or None."""
    try:
        R = M('EmpManagement', 'EmployeeResignation')
        r = R.objects.filter(employee=emp).exclude(status__iexact='rejected').exclude(status__iexact='cancelled').order_by('-last_working_date').first()
    except Exception:
        return None
    if r is None or not r.last_working_date:
        return None
    if emp.emp_joined_date and r.last_working_date < emp.emp_joined_date:   # an earlier employment (re-hired)
        return None
    return (r.resigned_on or r.document_date or r.last_working_date, r.last_working_date)


def notice_period(req, rule=None):
    """([errors], [warnings]) – company setting: allow (before 1.65.0) / warn / block (HR may still enter it)."""
    s = settings()
    if s.notice_leave == 'allow':
        return [], []
    rule = rule if rule is not None else rule_for(req.leave_type)
    if rule is not None and rule.notice_exempt:
        return [], []
    n = notice_of(req.employee)
    if n is None or req.start_date > n[1] or req.end_date < n[0]:
        return [], []
    txt = tr('{0} is in the notice period ({1:%d/%m/%Y} – {2:%d/%m/%Y}).', req.employee.emp_first_name, n[0], n[1])
    by_hr = bool(getattr(req, 'created_by_id', None)) and req.created_by_id != getattr(req.employee, 'users_id', None)
    if s.notice_leave == 'block' and not by_hr:
        return [txt + ' ' + tr('Leave during the notice period is not allowed – ask HR.')], []
    return [], [txt + ' ' + tr('Leave during the notice period needs HR / the manager to agree.')]


# ------------------------------------------------------------------ items 86, 87 counting the days
def special_days(emp, lt, line, start, end, half=False):
    """The days a request counts when the leave type's sandwich rule (86) or the policy line's "calendar days except public holidays"
    (87) applies; None otherwise (the counting before 1.65.0)."""
    mode = line.count_days if line is not None else None
    rule = rule_for(lt)
    sandwich = bool(rule is not None and rule.sandwich)
    if mode != 'cal_nohol' and not sandwich:
        return None
    if half or getattr(lt, 'unit', 'days') == 'hours' or end < start:
        return None
    if mode == 'cal_nohol':
        inc_wk, inc_hol = True, False
    elif mode in ('calendar', 'working'):
        inc_wk = inc_hol = mode == 'calendar'
    else:
        inc_wk, inc_hol = bool(lt.include_weekend), bool(lt.include_holiday)
    from calendars.workdays import Plan
    plan = Plan(start, end, [emp.pk])
    kept, d = [], start
    while d <= end:
        f = plan.day(emp, d)
        off, hol = f.off, (f.holiday and not f.working)
        if (inc_wk or not off) and (inc_hol or not hol):
            kept.append(d)
        d += timedelta(days=1)
    if sandwich and kept:   # weekends / holidays between two leave days count as leave
        a, b = kept[0], kept[-1]
        kept = [a + timedelta(days=i) for i in range((b - a).days + 1)]
    return kept


# ------------------------------------------------------------------ item 88 balance projected to the leave start
def projected_extra(emp, line, start, today=None):
    """Days of a monthly policy line that will be credited before the leave starts (months ending before the start date, not credited
    yet) – the accrual run's own calculation (engine.to_earn_by_year_end)."""
    from . import engine
    if line is None or line.accrual != 'monthly':
        return 0.0
    today = today or date.today()
    upto = start.replace(day=1) - timedelta(days=1)   # the end of the month before the leave starts
    if upto < today.replace(day=1) - timedelta(days=1):
        return 0.0
    try:
        return float(engine.to_earn_by_year_end(emp, line, today=today, ye=upto))
    except Exception:
        log.exception('projected balance')
        return 0.0


def allow_projected(lt):
    r = rule_for(lt)
    return bool(r is not None and r.allow_projected)


# ------------------------------------------------------------------ item 89 unpaid leave / absence reduce annual leave earned
def reduce_unpaid(line):
    """True when the company setting makes unpaid days reduce this (annual) line's monthly earning, besides the line's own switch."""
    s = settings()
    if s.accrual_reduction == 'policy':
        return False
    lt = M('calendars', 'leave_type').objects.filter(pk=line.leave_type_id).values_list('leave_category', flat=True).first()
    return lt == 'annual'


def absent_days(emp, a, b, line):
    """Item 89 (wider count, to be confirmed): absent days of the attendance calendar in a..b."""
    if settings().accrual_reduction != 'unpaid_absence' or not reduce_unpaid(line):
        return 0.0
    AC = M('calendars', 'AttendanceCalendar')
    n = 0.0
    for st, half in AC.objects.filter(employee=emp, date__range=(a, b), status='Absent').values_list('status', 'is_half_day'):
        n += 0.5 if half else 1.0
    return n


# ------------------------------------------------------------------ item 90 leave pay at exit with less than 6 months
def under_6m_days(emp, line, lwd):
    """(days, note) the company setting gives at exit to an employee with less than 6 months of service on a first-year UAE annual line
    (the law is not clear – to be confirmed). ('none' = 0, the rule before 1.65.0.)"""
    from . import engine
    s = settings()
    if s.exit_under_6m == 'none' or line is None or not line.first_year_uae or not emp.emp_joined_date or not lwd:
        return 0.0, ''
    if engine.months_of_service(emp, lwd) >= 6:
        return 0.0, ''
    days = (lwd - emp.emp_joined_date).days + 1
    months = days / (365.0 / 12)
    if s.exit_under_6m == 'two_days':
        v = round(2.0 * months, 2)
        return v, tr('less than 6 months of service: 2 days × {0:.2f} months (company setting, to be confirmed)', months)
    v = round(float(line.days_per_year or 30) * days / 365.0, 2)
    return v, tr('less than 6 months of service: {0:g} days a year × {1} / 365 days (company setting, to be confirmed)', float(line.days_per_year or 30), days)


# ------------------------------------------------------------------ item 93 accrual catch-up
def catch_up(today=None, dry=False, user_id=None):
    """Months whose monthly accrual did not run (server down on the 1st) are credited on the next run of the leave jobs: every month
    after the newest month credited in this company up to last month (at most `catch_up_max_months`). The accrual skips what is
    credited already (per employee, leave type and month), so a month posted by hand (1 Nov 2026 on gdis, posted on 3 Nov) is never
    doubled; a company that never ran the accrual gets nothing (no history is made up)."""
    import re
    from . import engine
    from .models import LeaveLedger
    s = settings()
    if not s.catch_up:
        return {'months': [], 'count': 0}
    today = today or date.today()
    last = today.replace(day=1) - timedelta(days=1)
    tags = set()
    for n in LeaveLedger.objects.filter(kind='accrual', date__gte=last - timedelta(days=31 * (s.catch_up_max_months + 2)), date__lte=last) \
            .values_list('note', flat=True):
        m = re.match(r'^(\d{4})-(\d{2})', n or '')
        if m:
            tags.add((int(m.group(1)), int(m.group(2))))
    if not tags:
        return {'months': [], 'count': 0}
    y, mo = max(tags)
    months, cnt = [], 0
    d = date(y, mo, 1)
    while True:
        d = add_months(d, 1)
        m_end = date(d.year, d.month, _cal.monthrange(d.year, d.month)[1])
        if m_end > last or len(months) >= s.catch_up_max_months:
            break
        res = engine.run_accrual(m_end, user_id=user_id, preview=dry)
        months.append(res['month'])
        cnt += res['count']
    return {'months': months, 'count': cnt}


# ------------------------------------------------------------------ item 94 nursing breaks
def nursing_minutes(emp, day):
    """Minutes of the paid nursing break on `day` (law: at most 1 hour a day for 6 months after the delivery), or 0. From the day after a
    maternity leave (approved, birth date recorded) to the birth + N months."""
    s = settings()
    if not s.nursing_enabled or not s.nursing_minutes:
        return 0
    from .models import LeaveRequestInfo, LeaveTypeRule
    LR = M('calendars', 'employee_leave_request')
    extra_ids = set(LeaveTypeRule.objects.filter(nursing_breaks=True).values_list('leave_type_id', flat=True))
    qs = LR.objects.filter(employee_id=getattr(emp, 'pk', emp), status='approved', end_date__lt=day,
                           end_date__gte=day - timedelta(days=31 * (s.nursing_months + 1)))
    qs = qs.filter(Q(leave_type__leave_category='maternity', leave_type__type='paid') | Q(leave_type_id__in=extra_ids))
    ids = list(qs.values_list('pk', flat=True))
    if not ids:
        return 0
    for bd in LeaveRequestInfo.objects.filter(request_id__in=ids, birth_date__isnull=False).values_list('birth_date', flat=True):
        if day < add_months(bd, int(s.nursing_months or 6)):
            return int(s.nursing_minutes)
    return 0


def nursing_window(req):
    """(first day, last day, minutes) of the nursing breaks after this maternity leave, or None."""
    s = settings()
    i = info_of(req)
    if not s.nursing_enabled or i is None or not i.birth_date or req.status != 'approved':
        return None
    lt = req.leave_type
    r = rule_for(lt)
    if not ((lt.leave_category == 'maternity' and lt.type == 'paid') or (r is not None and r.nursing_breaks)):
        return None
    last = add_months(i.birth_date, int(s.nursing_months or 6)) - timedelta(days=1)
    first = req.end_date + timedelta(days=1)
    return (first, last, int(s.nursing_minutes)) if first <= last else None


# ------------------------------------------------------------------ item 96 re-hire
def close_at_rehire(emp, lwd, settlement=None, user_id=None):
    """The leave side of a re-hire (PayrollPlus.payctl.rehire, item 228): the balances of the old employment end on its last working day.
    With a final settlement the annual leave was paid in it (line 'encashed'), the other types end with the employment (line
    'lapsed'); the new employment earns from the re-hire date (the joining date). Without a settlement nothing is paid, so the
    balances are kept (the reply says so). Returns [{leave_type_id, days, kind}]."""
    from . import engine
    B = M('calendars', 'emp_leave_balance')
    if settlement is None:
        return {'closed': [], 'kept': True}
    AL = None
    try:
        from PayrollPlus.services import _annual_type
        AL = _annual_type(emp)
    except Exception:
        AL = None
    out = []
    num = getattr(settlement, 'number', '') or getattr(settlement, 'pk', '')
    for b in B.objects.filter(employee=emp).exclude(balance=0):
        v = round(float(b.balance or 0), 2)
        if abs(v) < 0.005:
            continue
        paid = AL is not None and b.leave_type_id in _twins(AL.pk)
        kind = 'encashed' if paid else 'lapsed'
        note = (tr('Re-hire: annual leave of the old employment paid in the final settlement {0} (last day {1:%d/%m/%Y})', num, lwd) if paid
                else tr('Re-hire: balance of the old employment ended on {0:%d/%m/%Y}', lwd))
        engine.post(emp.pk, b.leave_type_id, lwd, kind, -v, note=note, user_id=user_id, move_balance=True)
        out.append({'leave_type_id': b.leave_type_id, 'days': -v, 'kind': kind})
    return {'closed': out, 'kept': False}


# ------------------------------------------------------------------ item 83 cancel part of an approved leave
class RuleError(Exception):
    def __init__(self, text, status=400):
        super().__init__(text)
        self.text, self.status = text, status


def cancel_part(lr, from_date, reason, user=None):
    """Give back the days from `from_date` to the end of an approved leave (the employee returns early). The request keeps its first
    days (end date moves to the day before), the balance gets the days back with a 'cancelled' ledger line, the attendance calendar
    loses the days; days in a payroll already made follow the 1.62.5 rule (refused, or HR adds the difference to the next salary)."""
    from . import engine, paid_period as pp
    import copy
    if lr.status != 'approved':
        raise RuleError(tr('Only approved leave can be cancelled.'))
    if engine.is_hourly(lr.leave_type):
        raise RuleError(tr('Short leave in hours is cancelled as a whole.'))
    if not reason:
        raise RuleError(tr('Say why the leave is cancelled.'))
    if from_date is None or from_date <= lr.start_date or from_date > lr.end_date:
        raise RuleError(tr('Give the first day given back: after {0:%d/%m/%Y} and at most {1:%d/%m/%Y} (to give back every day, cancel the leave).',
                           lr.start_date, lr.end_date))
    line = engine.line_for(lr.employee, lr.leave_type_id)
    taken = engine.approved_and_rest(lr, engine._req_days(lr, line))[0]
    unit = 0.5 if lr.dis_half_day else 1.0
    back = round(sum(unit for d in taken if d >= from_date), 2)
    old_end, old_approved = lr.end_date, float(lr.approved_days or lr.number_of_days or 0)
    if not [d for d in taken if d < from_date] or old_approved - back <= 0:
        raise RuleError(tr('No leave day is left before {0:%d/%m/%Y}: cancel the whole leave instead.', from_date))
    # 1.62.5 rule on the days given back that are in a payroll already made
    fake = copy.copy(lr)
    prev = {'status': 'approved', 'start_date': from_date, 'end_date': old_end, 'leave_type_id': lr.leave_type_id, 'dis_half_day': lr.dis_half_day}
    fake.start_date = from_date
    pl = pp.plan(fake, 'cancel', prev)
    if pl is not None:
        c = pp._ctx()
        if not (c.get('choice') == pp.CHOICE and pp.may_override(c.get('user'), lr.employee)):
            raise pp.PaidPeriodError(pp.info_of(fake, pl))
        pl['user_id'] = getattr(c.get('user'), 'pk', None)
        pl['note'] = True
        pl['info'] = pp.info_of(fake, pl)
    AC = M('calendars', 'AttendanceCalendar')
    uid = getattr(user, 'pk', None)
    with transaction.atomic():
        lr.end_date = from_date - timedelta(days=1)
        lr.approved_days = round(max(old_approved - back, 0), 2)
        lr._v1650_part = True          # paid_period.check / the old balance check: the days given back are handled here
        lr.save()
        lr.refresh_from_db()
        if back:
            engine.post(lr.employee_id, lr.leave_type_id, date.today(), 'cancelled', back, ref=lr, user_id=uid, move_balance=True,
                        note=f'{lr.document_number} {from_date:%d/%m/%Y}–{old_end:%d/%m/%Y} given back ({back:g})'[:255])
        AC.objects.filter(employee=lr.employee, date__range=(from_date, old_end), is_manual=False, remarks__startswith=engine.TAG,
                          leave_type=lr.leave_type).delete()
        engine.sync_calendar(lr, approved=True)
        engine.resync_year(lr)
        from .models import LeaveRequestInfo
        i, _ = LeaveRequestInfo.objects.get_or_create(request_id=lr.pk)
        if i.original_end_date is None:
            i.original_end_date = old_end
        i.cancelled_days = round(float(i.cancelled_days or 0) + back, 2)
        i.cancel_note = (reason or '')[:255]
        i.save()
        arrears = None
        if pl is not None:
            fake._pp = pl
            arrears = pp.after_save(fake)
        adv = advance_shortened(lr, back, user)
        try:
            from Chatter.models import Message
            Message.objects.create(model='calendars.employee_leave_request', object_id=str(lr.pk), author_id=uid, kind='note',
                                   body=tr('Part cancelled: {0:g} day(s) from {1:%d/%m/%Y} given back – {2}', back, from_date, reason),
                                   record_label=lr.document_number or '')
        except Exception:
            pass
    return {'given_back': back, 'end_date': lr.end_date.isoformat(), 'approved_days': lr.approved_days, 'arrears': arrears, 'advance': adv}


# ------------------------------------------------------------------ item 84 extend an approved leave
def extend(lr, new_end, reason, request):
    """A linked extension request (the day after the leave to `new_end`) through the same approval as any leave: balance check, the
    rules of the leave type, the 1.62.5 paid-period rule. The notice rule does not refuse an extension (company setting)."""
    from calendars.views import LeaveRequestviewset
    from . import engine
    from .paid_period import deciding
    if lr.status != 'approved':
        raise RuleError(tr('Only approved leave can be extended.'))
    if engine.is_hourly(lr.leave_type):
        raise RuleError(tr('Short leave in hours is not extended – request another one.'))
    if new_end is None or new_end <= lr.end_date:
        raise RuleError(tr('The new last day must be after {0:%d/%m/%Y}.', lr.end_date))
    if (new_end - lr.end_date).days > 400:
        raise RuleError(tr('Choose at most a year.'))
    data = {'employee': lr.employee_id, 'branch': lr.branch_id or lr.employee.emp_branch_id_id, 'leave_type': lr.leave_type_id,
            'start_date': (lr.end_date + timedelta(days=1)).isoformat(), 'end_date': new_end.isoformat(),
            'reason': (tr('Extension of {0}', lr.document_number) + (f': {reason}' if reason else ''))[:500], 'dis_half_day': False}
    v = LeaveRequestviewset()
    v.request, v.args, v.kwargs, v.format_kwarg, v.action = request, (), {}, None, 'create'
    v.check_permissions(request)
    ser = v.get_serializer(data=data)
    ser.is_valid(raise_exception=True)
    bd = extra(lr, 'birth_date')
    with deciding(request), extras(extension_of=lr.pk, birth_date=bd):
        v.perform_create(ser)
    return ser.instance


def is_extension(req):
    return bool(extra(req, 'extension_of'))


# ------------------------------------------------------------------ item 85 leave salary in advance
TAG_ADV = 'Leave salary in advance'


def _per_day(emp, basis):
    """(per day, [(component, monthly amount)]) of the employee's fixed additions: basic only, or every addition prorated by days."""
    from PayrollPlus.cycles import scalable
    ESS = M('PayrollManagement', 'EmployeeSalaryStructure')
    rows = []
    for x in ESS.objects.filter(employee=emp, is_active=True, component__component_type='addition').select_related('component'):
        c = x.component
        amt = Decimal(str(x.amount or 0))
        if amt <= 0 or c.component_value_type != 'fixed':
            continue
        if basis == 'basic' and c.payroll_category != 'basic':
            continue
        if basis != 'basic' and not scalable(c):
            continue
        rows.append((c, amt))
    total = sum((a for _c, a in rows), Decimal('0'))
    return (total / 30), rows


def advance_plan(lr):
    """{days, per_day, amount, lines: [{component_id, code, amount}]} of the leave salary of an approved leave (paid days × monthly ÷ 30)."""
    from . import engine
    s = settings()
    paid_days = sum(Decimal(str(round(unit * paid, 4))) for _d, paid, unit in engine.paid_split(lr))
    per_day, rows = _per_day(lr.employee, s.advance_basis)
    total_m = sum((a for _c, a in rows), Decimal('0'))
    amount = (per_day * paid_days).quantize(Q2, ROUND_HALF_UP)
    lines, left = [], amount
    for i, (c, a) in enumerate(rows):
        part = left if i == len(rows) - 1 else (amount * a / total_m).quantize(Q2, ROUND_HALF_UP)
        left -= part
        lines.append({'component_id': c.pk, 'code': c.code, 'amount': str(part)})
    return {'days': float(paid_days), 'per_day': str(per_day.quantize(Q2, ROUND_HALF_UP)), 'amount': str(amount), 'lines': lines, 'basis': s.advance_basis}


def advance_on_approved(lr, user_id=None):
    """Item 85: the leave salary of the leave days is paid before the leave – in the next payroll (an arrear line from today) or at once
    in an off-cycle run – and the payroll of the leave days takes it back (an arrear line from the leave start), so those days are not
    paid twice. Nothing when the next payroll is already the payroll of the leave (the normal payroll pays the days)."""
    from .models import LeaveRequestInfo
    from PayrollPlus.models import ArrearLine
    rule = rule_for(lr.leave_type)
    if rule is None or not rule.advance_salary or lr.leave_type.type == 'unpaid' or getattr(lr.leave_type, 'unit', 'days') == 'hours':
        return None
    i, _ = LeaveRequestInfo.objects.get_or_create(request_id=lr.pk)
    if (i.advance or {}).get('status') in ('pending', 'paid'):
        return i.advance
    s = settings()
    today = date.today()
    p = advance_plan(lr)
    amount = Decimal(p['amount'])
    if amount <= 0:
        i.advance = dict(p, status='none', why='no salary to pay')
        i.save(update_fields=['advance', 'updated_at'])
        return i.advance
    mon = lr.start_date.replace(day=1)
    if s.advance_when == 'next_payroll' and lr.start_date <= date(today.year, today.month, _cal.monthrange(today.year, today.month)[1]):
        i.advance = dict(p, status='same_payroll', why=tr('The leave starts in this month: the normal payroll pays the leave days.'))
        i.save(update_fields=['advance', 'updated_at'])
        return i.advance
    note = f'{TAG_ADV} – {lr.document_number} {lr.start_date:%d/%m/%Y}–{lr.end_date:%d/%m/%Y}'
    adv_ids, off_ids, run_id = [], [], None
    with transaction.atomic():
        if s.advance_when == 'off_cycle':
            from PayrollPlus.cycles import off_cycle, _component
            comp = _component('LVADV', 'Leave salary in advance', 'addition', lr.employee.emp_branch_id_id)
            oc = off_cycle('advance', f'{TAG_ADV} {lr.document_number}', today,
                           [{'employee_id': lr.employee_id, 'component_id': comp.pk, 'amount': str(amount), 'note': note}])
            run_id = oc.payroll_run_id
            a = ArrearLine.objects.create(employee_id=lr.employee_id, component_id=comp.pk, month=mon, amount=-amount,
                                          effective_from=lr.start_date, created_by_id=user_id,
                                          note=(f'{note} – paid {today:%d/%m/%Y}, taken back from the payroll of the leave')[:200])
            off_ids.append(a.pk)
        else:
            for ln in p['lines']:
                v = Decimal(ln['amount'])
                if not v:
                    continue
                a = ArrearLine.objects.create(employee_id=lr.employee_id, component_id=ln['component_id'], month=mon, amount=v,
                                              effective_from=today, created_by_id=user_id, note=(f'{note} – paid before the leave')[:200])
                adv_ids.append(a.pk)
                b = ArrearLine.objects.create(employee_id=lr.employee_id, component_id=ln['component_id'], month=mon, amount=-v,
                                              effective_from=lr.start_date, created_by_id=user_id,
                                              note=(f'{note} – taken back from the payroll of the leave')[:200])
                off_ids.append(b.pk)
        i.advance = dict(p, status='paid' if run_id else 'pending', mode=s.advance_when, advance_lines=adv_ids, offset_lines=off_ids,
                         run_id=run_id, at=today.isoformat())
        i.save(update_fields=['advance', 'updated_at'])
    return i.advance


def _lines(ids):
    from PayrollPlus.models import ArrearLine
    return list(ArrearLine.objects.filter(pk__in=ids or []))


def advance_state(lr):
    """The advance of a leave as it is now: waiting / paid / taken back / cancelled."""
    i = info_of(lr)
    a = dict((i.advance or {}) if i else {})
    if not a or a.get('status') in (None, 'none', 'same_payroll'):
        return a or None
    adv, off = _lines(a.get('advance_lines')), _lines(a.get('offset_lines'))
    paid = bool(a.get('run_id')) or (adv and all(x.status == 'included' for x in adv))
    back = off and all(x.status == 'included' for x in off)
    if all(x.status == 'cancelled' for x in adv + off) and (adv or off):
        a['state'] = 'cancelled'
    elif back:
        a['state'] = 'settled'
    elif paid:
        a['state'] = 'paid'
    else:
        a['state'] = 'waiting'
    return a


def advance_cancelled(lr, user=None):
    """The leave is cancelled: an advance not paid yet is cancelled (both lines); an advance already paid is taken back from the next
    payroll (the take-back line moves to today) – the days are then paid as work days."""
    from PayrollPlus.models import ArrearLine
    a = advance_state(lr)
    if not a or a.get('state') in ('cancelled', 'settled'):
        return a
    i = info_of(lr)
    if a['state'] == 'waiting':
        ArrearLine.objects.filter(pk__in=(a.get('advance_lines') or []) + (a.get('offset_lines') or []), status='pending').update(status='cancelled')
        i.advance = dict(i.advance, status='cancelled')
    else:
        ArrearLine.objects.filter(pk__in=a.get('offset_lines') or [], status='pending').update(effective_from=date.today())
        i.advance = dict(i.advance, status='recovering', why=tr('Leave cancelled: the advance is taken back from the next payroll.'))
    i.save(update_fields=['advance', 'updated_at'])
    return advance_state(lr)


def advance_shortened(lr, back_days, user=None):
    """Part of the leave given back: an advance not paid yet is made again for the days left; one already paid stays (the take-back
    line recovers it all in the payroll of the leave, and the days given back are paid as work days there)."""
    from PayrollPlus.models import ArrearLine
    a = advance_state(lr)
    if not a or a.get('state') != 'waiting' or not back_days:
        return a
    i = info_of(lr)
    ArrearLine.objects.filter(pk__in=(a.get('advance_lines') or []) + (a.get('offset_lines') or []), status='pending').update(status='cancelled')
    i.advance = {}
    i.save(update_fields=['advance', 'updated_at'])
    lr.refresh_from_db()
    return advance_on_approved(lr, getattr(user, 'pk', None))
