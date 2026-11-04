"""v1.49.0 – attendance additions:

* regularisation reasons (proof photo, document, monthly limit) checked when an employee asks to correct a day;
* the overtime law of the company country (rates for normal, night, rest-day and public-holiday overtime) as the default of
  the attendance rules, and the overtime calculation of a month shown to the employee next to the payslip;
* punch photos (kiosk photos and selfies) for HR to review – accept, flag or void a punch.
"""
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.apps import apps
from django.db.models import Q

from . import models as M

REASONS = [
    ('FORGOT', 'Forgot to punch', 'forgot', False, False, 3, False),
    ('DEVICE', 'Device / app not working', 'missing_punch', True, False, None, True),
    ('SITE', 'Client or site visit', 'on_duty', True, False, None, True),
    ('MEETING', 'Meeting outside the office', 'on_duty', False, False, None, True),
    ('WFH', 'Worked from home (approved)', 'wfh', False, False, None, False),
    ('TRAFFIC', 'Traffic accident / road closure', 'wrong_time', True, False, 2, True),
    ('MEDICAL', 'Medical appointment', 'wrong_time', False, True, 2, True),
    ('OTHER', 'Other', 'missing_punch', False, False, None, False),
]


def reasons():
    if not M.RegularisationReason.objects.exists():
        for i, (code, name, kind, ph, att, mx, waive) in enumerate(REASONS):
            M.RegularisationReason.objects.create(code=code, name=name, kind=kind, needs_photo=ph, needs_attachment=att, max_per_month=mx,
                                                  waive_penalty=waive, order=i)
    return M.RegularisationReason.objects.all()


def reason_json(r):
    return {'id': r.pk, 'code': r.code, 'name': r.name, 'kind': r.kind, 'needs_photo': r.needs_photo, 'needs_attachment': r.needs_attachment,
            'max_per_month': r.max_per_month, 'waive_penalty': r.waive_penalty, 'is_active': r.is_active, 'order': r.order}


def check_reason(employee_id, code, day, has_photo, has_attachment):
    """'' when the reason may be used, else the problem."""
    if not code:
        return ''
    r = M.RegularisationReason.objects.filter(code=code, is_active=True).first()
    if r is None:
        return 'Choose a reason from the list.'
    if r.needs_photo and not has_photo:
        return f'"{r.name}" needs a proof photo – take one with the camera.'
    if r.needs_attachment and not has_attachment:
        return f'"{r.name}" needs a document (e.g. the appointment slip).'
    if r.max_per_month:
        n = M.CorrectionRequest.objects.filter(employee_id=employee_id, reason_code=code, date__year=day.year, date__month=day.month).exclude(
            status__in=('rejected', 'cancelled')).count()
        if n >= r.max_per_month:
            return f'"{r.name}" can be used {r.max_per_month} time(s) a month – this month is used up.'
    return ''


# ------------------------------------------------------------------ overtime law per country
# (normal, night, rest day / weekend, public holiday) – multipliers of the hourly wage
OT_LAW = {
    'AE': ((Decimal('1.25'), Decimal('1.50'), Decimal('1.50'), Decimal('1.50')),
           'UAE Decree-Law 33/2021 Art. 19: +25% of the basic hourly wage; +50% between 10 pm and 4 am; work on the rest day or a public '
           'holiday: a day off in lieu or +50%. Overtime at most 2 hours a day (144 hours in 3 weeks).'),
    'SA': ((Decimal('1.50'), Decimal('1.50'), Decimal('1.50'), Decimal('1.50')),
           'KSA Labour Law Art. 107: the hourly wage + 50% of the basic hourly wage for every overtime hour, rest days and holidays included.'),
    'OM': ((Decimal('1.25'), Decimal('1.50'), Decimal('2.00'), Decimal('2.00')),
           'Oman Labour Law (RD 53/2023): +25% day overtime, +50% night overtime; rest day and official holiday work at least +100% or a day off.'),
    'QA': ((Decimal('1.25'), Decimal('1.50'), Decimal('1.50'), Decimal('1.50')),
           'Qatar Labour Law 14/2004 Art. 74: +25%; +50% between 9 pm and 6 am (except shift workers); rest days and holidays +50%.'),
    'KW': ((Decimal('1.25'), Decimal('1.25'), Decimal('1.50'), Decimal('2.00')),
           'Kuwait Labour Law 6/2010 Art. 66–68: +25%; weekly rest day +50% and a day off; official holidays +100%.'),
    'IN': ((Decimal('2.00'), Decimal('2.00'), Decimal('2.00'), Decimal('2.00')),
           'India (Factories Act s.59 / state Shops Acts): overtime at twice the ordinary rate of wages.'),
}


# v1.62.6 (item 24): the night window of the overtime law and who gets the night rate. Only the UAE is filled in – it was checked
# against these sources on 3 Nov 2026 (all four say the same):
#   1. the law text, Federal Decree-Law No. 33 of 2021, Art. 19(3) (MOHRE PDF "Federal Decree-Law No. 33 of 2021 Regarding the
#      Regulation of Employment Relationship and its amendments", mohre.gov.ae): "If the work conditions require that the worker works
#      overtime between 10 pm and 4 am, the worker shall be entitled ... basic wage plus an increase of not less than (50 %) ...
#      The workers working based on shifts shall be excluded from this clause."
#   2. u.ae – Working hours in the private sector: "It could increase to 50 per cent if overtime is done between 10 pm and 4 am.
#      This rule does not apply on workers who work on basis of shifts."
#   3. MOHRE – Workers' rights, Working hours (mohre.gov.ae/en/media-center/awareness-and-guidance/workers-rights.aspx): 10.00 pm to
#      4.00 am, at least 50 %, "this provision does not apply to shift workers".
#   4. Ashish Mehta (Ashish Mehta & Associates, legal consultants) in Khaleej Times, "Overtime pay explained": quotes Art. 19(3) the same.
# Other countries: not checked in this release – their rules keep their own night window and the night rate for everybody.
NIGHT_LAW = {
    'AE': {'start': '22:00', 'end': '04:00', 'shift_workers_excluded': True,
           'law': 'UAE Decree-Law 33/2021 Art. 19(3): overtime between 10 pm and 4 am – basic wage + at least 50%; '
                  'workers working on shifts are excluded.',
           'sources': ['Federal Decree-Law No. 33 of 2021, Art. 19(3) (law text, mohre.gov.ae)',
                       'u.ae – Working hours in the private sector',
                       'MOHRE – Workers\' rights: Working hours',
                       'Ashish Mehta & Associates (Khaleej Times, "Overtime pay explained")']},
}
NIGHT_KEY = 'attendance_night_rules'
OLD_DEFAULT_WINDOW = ('22:00', '06:00')   # the standard window of every attendance rule before v1.62.6


def night_law(code=None):
    """v1.62.6 (item 24): the verified night rule of the country, or None."""
    return NIGHT_LAW.get((code or country()).upper())


def night_settings():
    """v1.62.6 (item 24) company setting (ZeoApps CompanyOption, JSON – no new column):
    window_from_law  – rules that still have the old standard window 22:00–06:00 count the law's window (UAE 22:00–04:00);
    non_shift_only   – the night overtime rate only for workers not on shifts (Art. 19(3)).
    Both default to what the law says for a country in NIGHT_LAW; elsewhere off (today's behaviour)."""
    nl = night_law()
    v = {}
    try:
        o = apps.get_model('ZeoApps', 'CompanyOption').objects.filter(key=NIGHT_KEY).first()
        v = (o.value or {}) if o is not None else {}
    except Exception:
        v = {}
    dflt = bool(nl)
    return {'window_from_law': bool(v.get('window_from_law', dflt)) and bool(nl),
            'non_shift_only': bool(v.get('non_shift_only', dflt and bool(nl and nl.get('shift_workers_excluded')))),
            'law_window': f"{nl['start']}–{nl['end']}" if nl else '', 'law': nl['law'] if nl else '',
            'sources': list(nl['sources']) if nl else [], 'saved': bool(v)}


def save_night_settings(data):
    cur = {}
    try:
        o = apps.get_model('ZeoApps', 'CompanyOption').objects.filter(key=NIGHT_KEY).first()
        cur = dict(o.value or {}) if o is not None else {}
    except Exception:
        cur = {}
    for k in ('window_from_law', 'non_shift_only'):
        if k in data:
            cur[k] = bool(data[k]) if not isinstance(data[k], str) else data[k].lower() in ('1', 'true', 'yes', 'on')
    apps.get_model('ZeoApps', 'CompanyOption').objects.update_or_create(key=NIGHT_KEY, defaults={'value': cur})
    return night_settings()


def country():
    try:
        from CountryPack.services import pack_code
        c = (pack_code() or 'AE').upper()
        return c if c in OT_LAW else 'AE'
    except Exception:
        return 'AE'


def law(code=None):
    c = (code or country()).upper()
    (n, ni, we, ho), text = OT_LAW.get(c, OT_LAW['AE'])
    return {'country': c, 'normal': str(n), 'night': str(ni), 'weekend': str(we), 'holiday': str(ho), 'law': text}


def default_rate(ot_type, night=False):
    (n, ni, we, ho), _ = OT_LAW.get(country(), OT_LAW['AE'])
    if night and ot_type == 'NORMAL':
        return ni
    return {'NORMAL': n, 'WEEKEND': we, 'HOLIDAY': ho}.get(ot_type, n)


def apply_law(rule_ids=None, code=None):
    L = law(code)
    qs = M.AttendanceRule.objects.all()
    if rule_ids:
        qs = qs.filter(pk__in=rule_ids)
    n = qs.update(ot_rate_normal=Decimal(L['normal']), ot_rate_night=Decimal(L['night']), ot_rate_weekend=Decimal(L['weekend']),
                  ot_rate_holiday=Decimal(L['holiday']))
    return n, L


OT_VARS = {'ot_hours', 'ot_weighted_hours', 'normal_ot_weighted_hours', 'weekend_ot_weighted_hours', 'holiday_ot_weighted_hours', 'normal_ot_hours',
           'weekend_ot_hours', 'holiday_ot_hours', 'night_ot_hours', 'overtime_hours', 'ot_amount'}


def _ot_components(payslip):
    PC = apps.get_model('PayrollManagement', 'PayslipComponent')
    out = []
    try:
        from PayrollManagement.formula import names_in as _ni

        def names_in(f):
            return set(_ni(f)[0])
    except Exception:
        names_in = lambda f: set()   # noqa: E731
    for pc in PC.objects.filter(payslip=payslip).select_related('component'):
        c = pc.component
        f = c.formula or ''
        uses = c.payroll_category == 'overtime' or bool(f and names_in(f) & OT_VARS)
        if uses and pc.amount:
            out.append({'component': c.name, 'formula': f, 'amount': str(pc.amount)})
    return out


def breakdown(employee, year, month):
    """The overtime of a month: every approved overtime day with its type, hours, rate and weighted hours; the overtime
    components paid on the payslip of that month and the hourly wage they imply."""
    import calendar
    from calendars.models import EmployeeOvertime
    from . import engine as E
    start, end = date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
    rule = E.rule_for(employee)
    rates = {d: r for d, r in M.AttendanceDay.objects.filter(employee_id=employee.id, date__range=(start, end)).values_list('date', 'ot_rate')}
    splits = {d: fl for d, fl in M.AttendanceDay.objects.filter(employee_id=employee.id, date__range=(start, end),   # v1.62.11 (item 255)
                                                                  flags__has_key='night_ot_minutes').values_list('date', 'flags')}
    nights = set(M.AttendanceDay.objects.filter(employee_id=employee.id, date__range=(start, end), night_minutes__gt=0).values_list('date', flat=True))
    # v1.62.6 (item 24): a day the night rate was not given (worker on shifts, Art. 19(3)) is not a night-rate day here either
    nights -= set(M.AttendanceDay.objects.filter(employee_id=employee.id, date__range=(start, end),
                                                 flags__has_key='night_rate_not_for_shift').values_list('date', flat=True))
    rows, tot = [], defaultdict(lambda: {'hours': Decimal(0), 'weighted': Decimal(0)})
    labels = {'NORMAL': 'Normal day', 'WEEKEND': 'Rest day / weekend', 'HOLIDAY': 'Public holiday'}
    rdx = {d: fl.get('rest_day_ot') or {} for d, fl in M.AttendanceDay.objects.filter(   # v1.67.0 (item 25d)
        employee_id=employee.id, date__range=(start, end), flags__has_key='rest_day_ot').values_list('date', 'flags')}
    for o in EmployeeOvertime.objects.filter(employee=employee, date__range=(start, end)).order_by('date'):
        t = o.ot_type or 'NORMAL'
        approved = bool(o.approved or o.source == 'MANUAL')
        rate = rates.get(o.date)
        if o.date in rdx and t == rdx[o.date].get('type') and o.source != 'MANUAL' and rdx[o.date].get('rate'):
            rate = Decimal(str(rdx[o.date]['rate']))   # v1.67.0 (item 25d): the rest-day row of a working day
        if not rate or rate == 1:
            rate = E._ot_rate(employee, rule, t, night=o.date in nights)
        h = Decimal(str(o.hours or 0))
        w = ((E.weighted_ot(h, rate, splits[o.date]) if o.date in splits and o.source != 'MANUAL' and t == 'NORMAL'   # v1.62.11 (item 255)
              else h * Decimal(str(rate)))).quantize(Decimal('0.01'))
        rows.append({'date': o.date, 'type': labels.get(t, t), 'hours': str(h), 'rate': str(rate), 'weighted': str(w), 'approved': approved,
                     'night': o.date in nights})
        if approved:
            tot[t]['hours'] += h
            tot[t]['weighted'] += w
    P = apps.get_model('PayrollManagement', 'Payslip')
    slip = P.objects.filter(employee=employee, payroll_run__year=year, payroll_run__month=month).order_by('-id').first()
    comps = _ot_components(slip) if slip else []
    paid = sum(Decimal(c['amount']) for c in comps)
    weighted = sum(v['weighted'] for v in tot.values())
    return {'employee_id': employee.id, 'year': year, 'month': month, 'rows': rows,
            'totals': [{'type': labels[k], 'hours': str(v['hours']), 'weighted': str(v['weighted'])} for k, v in tot.items()],
            'weighted_hours': str(weighted), 'payslip_id': slip.pk if slip else None, 'components': comps, 'paid': str(paid),
            'hourly': str((paid / weighted).quantize(Decimal('0.01'))) if weighted and paid else '',
            'law': law()}


# ------------------------------------------------------------------ punch photos
def photos(qs_emp_ids, d_from, d_to, only=''):
    E = apps.get_model('EmpManagement', 'emp_master')
    qs = M.Punch.objects.filter(work_date__range=(d_from, d_to)).exclude(photo='').exclude(photo__isnull=True)
    if qs_emp_ids is not None:
        qs = qs.filter(employee_id__in=qs_emp_ids)
    if only == 'flagged':
        qs = qs.filter(note__startswith='[flagged]')
    elif only == 'void':
        qs = qs.filter(is_void=True)
    qs = qs.select_related('kiosk').order_by('-ts')[:400]
    rows = list(qs)
    emps = {e.pk: e for e in E.objects.filter(pk__in={p.employee_id for p in rows})}
    out = []
    for p in rows:
        e = emps.get(p.employee_id)
        out.append({'id': p.pk, 'employee_id': p.employee_id, 'employee': ' '.join(x for x in (e.emp_first_name, e.emp_last_name) if x) if e else '',
                    'code': e.emp_code if e else '', 'profile': e.emp_profile_pic.url if e and e.emp_profile_pic else None,
                    'ts': p.ts, 'kind': p.resolved_kind or p.kind, 'source': p.source, 'kiosk': p.kiosk.name if p.kiosk_id else '',
                    'photo': p.photo.url if p.photo else None, 'void': p.is_void, 'flagged': (p.note or '').startswith('[flagged]'), 'note': p.note,
                    'geofence_ok': p.geofence_ok, 'location': p.location})
    return out
