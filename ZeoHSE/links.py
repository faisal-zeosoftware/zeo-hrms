"""v1.17.0 – links between HSE and the other ZEO apps.

HSE runs on its own. Each link below switches on only when the other app is installed for the company (its tables
exist in the company schema) and the HSE manager has not switched it off in Setup › Links with other apps. When a link
is off, HSE keeps its manual way (hours typed in settings, lost days typed on the incident, competencies entered in HSE,
equipment as free text …).

HSE stores other apps' records as plain ids (project_id, asset_id, course_id …), never as foreign keys, so the HSE
tables and migrations do not depend on any other app. Every call into another app is lazy and guarded: a failure there
is logged and never stops the HSE record (or the other app's record) from being saved.
"""
import logging
import time
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.apps import apps as djapps
from django.db import connection, transaction
from django.db.models import Q, Sum

from . import access as A
from . import services as S
from .models import (Action, Audit, ChangeRequest, Chemical, Claim, Competency, CompetencyType, EnvAspect, EnvParameter, Handover, HealthSurveillance,
                     HSEDocument, HSESettings, Incident, Inspection, KPI, Nonconformity, Obligation, Observation, Permit, RiskAssessment, Site)

log = logging.getLogger(__name__)

# key: (label, [alternative sets of (app, model) – any one set is enough], what the link does, what happens without it)
LINKS = {
    'training': ('Training ↔ HR Learning', [[('LearningManagement', 'Course'), ('LearningManagement', 'Certificate'), ('LearningManagement', 'TrainingNeed')]],
                 'Link a competency type to an HR course: an HR certificate for that course creates or renews the employee\'s HSE competency, so '
                 'permit checks use it at once. Missing or expiring competencies raise a training need in HR Learning. The training-compliance KPI '
                 'also counts the mandatory HR courses.',
                 'Competency records are entered in HSE.'),
    'projects': ('Projects', [[('ProjectManagement', 'Project')]],
                 'A site belongs to a project; the dashboard filters by project; each project shows an HSE tab to its managers and team (figures '
                 'only). A serious incident or a stopped permit adds an issue to the project\'s risks and issues; a settled claim is added to the '
                 'project costs.',
                 'Sites stand alone.'),
    'attendance': ('Attendance → hours worked', [[('AttendancePlus', 'AttendanceDay')], [('calendars', 'Attendance')]],
                   'TRIR and LTIFR use the hours worked from attendance (per site through the site\'s branch) instead of the hours typed in settings.',
                   'The monthly hours typed in HSE settings are used.'),
    'leave': ('Leave → lost days and return to work', [[('calendars', 'employee_leave_request')]],
              'Approved sick or injury leave of the injured employee after an incident is linked to the incident and counted as lost days. When '
              'the employee rejoins, occupational health gets a return-to-work fitness check. A permit receiver or crew member on approved leave '
              'blocks the permit.',
              'Lost days are typed on the incident.'),
    'assets': ('Assets → equipment', [[('OrganisationManager', 'Asset')]],
               'Permits, incidents and inspections pick the equipment from the asset register. With the Asset Plus app, overdue maintenance or '
               'equipment under maintenance blocks the permit, and incident damage creates the asset damage record.',
               'Equipment is typed as free text.'),
    'roster': ('Shift roster → permit crew', [[('ShiftPlanner', 'RosterEntry')]],
               'Warns when the permit receiver or a crew member is rostered off on the permit date. With attendance, it also warns when someone '
               'worked more than the fatigue limit the day before.',
               'No roster check.'),
    'muster': ('Attendance → evacuation muster', [[('AttendancePlus', 'AttendanceDay')], [('calendars', 'Attendance')]],
               'Lists who punched in at a site today and has not punched out, for a drill or an emergency head count.',
               'Head counts are done on paper.'),
    'onboarding': ('Recruitment → HSE induction', [[('RecruitmentManagement', 'Offer'), ('RecruitmentManagement', 'OnboardingTask')]],
                   'An accepted offer gets onboarding tasks for the HSE induction and the pre-employment medical.',
                   'Inductions are arranged by hand.'),
    'handover': ('Transfers and resignations → handover', [[('HRActions', 'EmployeeTransfer')], [('EmpManagement', 'EmployeeResignation')]],
                 'When an employee moves branch or their resignation is approved, HSE lists their open actions, permits, investigations, risk '
                 'assessments and other HSE work for reassignment; an exit medical is planned for people under health surveillance.',
                 'Open HSE work stays with the person until someone notices.'),
    'grievance': ('Self-service complaints → HSE', [[('SelfService', 'Grievance')]],
                  'A complaint raised in the "Safety" category is passed to HSE as a confidential observation (the reporter is never passed on '
                  'for anonymous complaints).',
                  'Safety complaints stay with HR.'),
    'alerts': ('Announcements → safety alerts', [[('OrganisationManager', 'Announcement')]],
               'Publish the lessons learned from an incident as a company announcement for the site\'s branch, and see how many people read it.',
               'Alerts are sent outside ZEO.'),
    'performance': ('Performance → safety goals', [[('PerformanceManagement', 'KPI'), ('PerformanceManagement', 'Goal')]],
                    'Add an HSE KPI to the Performance KPI library; goals that use it get their progress from the monthly HSE value.',
                    'Safety goals are updated by hand.'),
    'costs': ('Cost centres → HSE cost', [[('ExpenseManagement', 'CostCenter')]],
              'A site has a cost centre; the HSE cost report shows incident losses and claim costs by site, cost centre and project.',
              'The cost report shows sites only.'),
}
ORDER = list(LINKS)

_TABLES = {}


def _tables():
    schema = getattr(connection, 'schema_name', 'public')
    hit = _TABLES.get(schema)
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    try:
        names = set(connection.introspection.table_names())
    except Exception:
        names = set()
    _TABLES[schema] = (time.time(), names)
    return names


def forget_tables():
    _TABLES.clear()


def model(app, name):
    """The other app's model when it is installed and its table exists in this company, else None."""
    if not djapps.is_installed(app) and app not in {a.label for a in djapps.get_app_configs()}:
        return None
    try:
        m = djapps.get_model(app, name)
    except Exception:
        return None
    return m if m._meta.db_table in _tables() else None


# ZEO 1.21.0: which ZEO app (Settings → Apps) each link belongs to – a link works only while that app is active in the company
APP_OF_LINK = {'projects': 'projects'}


def app_active(key):
    try:
        from ZeoApps.services import is_active
        return is_active(APP_OF_LINK.get(key, 'hrms'))
    except Exception:
        return True


def available(key):
    return app_active(key) and any(all(model(a, n) is not None for a, n in alt) for alt in LINKS[key][1])


def switched_off(key):
    try:
        return key in (HSESettings.get().links_off or [])
    except Exception:
        return False


def on(key):
    return key in LINKS and not switched_off(key) and available(key)


def status():
    out = []
    off = set(HSESettings.get().links_off or [])
    for k in ORDER:
        label, _, what, without = LINKS[k]
        av = available(k)
        out.append({'key': k, 'label': label, 'available': av, 'switched_off': k in off, 'on': av and k not in off, 'what': what, 'without': without})
    extra = {'asset_plus': model('AssetPlus', 'MaintenanceSchedule') is not None, 'zeo_projects': model('ZeoProjects', 'ProjectRisk') is not None}
    return out, extra


def flags():
    return {k: on(k) for k in ORDER}


def safe(fn):
    """Never let a link break the record being saved (here or in the other app)."""
    def wrap(*a, **kw):
        try:
            with transaction.atomic():
                return fn(*a, **kw)
        except S.HSEError:
            raise
        except Exception:
            log.exception('HSE link %s failed', fn.__name__)
            return None
    wrap.__name__ = fn.__name__
    return wrap


def _local(dt):
    """Company local time (same zone as services.today())."""
    from zoneinfo import ZoneInfo
    tz = 'Asia/Dubai'
    try:
        from ZeoProjects.models import ProjectsSettings
        tz = ProjectsSettings.get().timezone or tz
    except Exception:
        pass
    try:
        return dt.astimezone(ZoneInfo(tz))
    except Exception:
        return dt


def _emp(pk):
    from EmpManagement.models import emp_master
    return emp_master.objects.filter(pk=pk).first() if pk else None


def _users_of(e):
    return [e.users] if e is not None and getattr(e, 'users_id', None) else []


def _manager_user(e):
    return getattr(e, 'emp_reporting_manager', None) if e is not None else None


# ------------------------------------------------------------------ options for pickers (Setup and forms)
def _opt_courses():
    C = model('LearningManagement', 'Course')
    return (C.objects.filter(is_active=True), lambda x: f'{x.code} – {x.title}', ('code', 'title')) if C else None


def _opt_projects():
    P = model('ProjectManagement', 'Project')
    return (P.objects.filter(is_active=True).exclude(status__in=('cancelled',)), lambda x: x.title, ('title',)) if P else None


def _asset_label(x):
    return f'{x.name} ({x.serial_number})' if getattr(x, 'serial_number', '') else x.name


def _opt_assets():
    As = model('OrganisationManager', 'Asset')
    return (As.objects.exclude(status='disposed').order_by('name'), _asset_label, ('name', 'serial_number', 'model')) if As else None


def _opt_cost_centers():
    C = model('ExpenseManagement', 'CostCenter')
    return (C.objects.filter(active=True), lambda x: f'{x.code} – {x.name}', ('code', 'name')) if C else None


OPTION_LINK = {'courses': ('training', _opt_courses), 'projects': ('projects', _opt_projects), 'assets': ('assets', _opt_assets),
               'cost-centers': ('costs', _opt_cost_centers)}


def options(name, q='', ids=None, limit=500):
    key, fn = OPTION_LINK[name]
    if not on(key):
        return []
    got = fn()
    if not got:
        return []
    qs, lbl, search = got
    if q:
        cond = Q()
        for s_ in search:
            cond |= Q(**{s_ + '__icontains': q})
        qs = qs.filter(cond)
    if ids:
        qs = qs.model.objects.filter(pk__in=ids)
    return [{'id': x.pk, 'label': lbl(x)} for x in qs[:limit]]


def label(name, pk):
    if not pk or name not in OPTION_LINK:
        return ''
    got = OPTION_LINK[name][1]()
    if not got:
        return f'#{pk}'
    x = got[0].model.objects.filter(pk=pk).first()
    return got[1](x) if x else f'#{pk} (removed)'


def check_id(name, pk, field):
    """Validate an id typed into a link field (None when the link is off)."""
    if pk in (None, '', 0):
        return None
    key, fn = OPTION_LINK[name]
    if not on(key):
        return None
    got = fn()
    if not got or not got[0].model.objects.filter(pk=pk).exists():
        raise S.HSEError('Choose an existing record.', field)
    return int(pk)


# ------------------------------------------------------------------ 1. training ↔ HR Learning
@safe
def certificate_saved(cert):
    if not on('training') or not cert.course_id:
        return 0
    from .modules import add_months
    n = 0
    for ct in CompetencyType.objects.filter(course_id=cert.course_id, is_active=True):
        exp = cert.expiry_date or (add_months(cert.issued_on, ct.validity_months) if ct.validity_months else None)
        rec, created = Competency.objects.update_or_create(
            source='learning', source_id=cert.pk, ctype=ct,
            defaults={'employee_id': cert.employee_id, 'issued_on': cert.issued_on, 'expires_on': exp, 'assessed_competent': True,
                      'provider': (cert.issued_by or 'HR Learning')[:150], 'certificate_no': (cert.certificate_number or '')[:60]})
        S.event('competency', rec.pk, f'{"Created" if created else "Updated"} from HR Learning certificate {cert.certificate_number or cert.pk}')
        n += 1
    return n


@safe
def certificate_deleted(cert):
    Competency.objects.filter(source='learning', source_id=cert.pk).delete()


def sync_certificates(ctype=None):
    """Bring in every HR certificate of the linked courses (after a course is linked, and daily as a safety net)."""
    if not on('training'):
        return 0
    Cert = model('LearningManagement', 'Certificate')
    types = [ctype] if ctype is not None else list(CompetencyType.objects.filter(course_id__isnull=False, is_active=True))
    n = 0
    for ct in types:
        if not ct.course_id:
            continue
        done = set(Competency.objects.filter(source='learning', ctype=ct).values_list('source_id', flat=True))
        for cert in Cert.objects.filter(course_id=ct.course_id).exclude(pk__in=done):
            n += certificate_saved(cert) or 0
    return n


@safe
def raise_training_need(employee, ctype, reason, target=None):
    """Missing / expiring competency → training need in HR Learning (once while one is open)."""
    if not on('training') or not ctype.course_id or employee is None:
        return None
    Need = model('LearningManagement', 'TrainingNeed')
    if Need.objects.filter(employee=employee, course_id=ctype.course_id).exclude(status__in=('completed', 'cancelled')).exists():
        return None
    return Need.objects.create(employee=employee, course_id=ctype.course_id, need=f'HSE competency {ctype.name}: {reason}'[:255],
                               source='compliance', source_reference=f'ZEO HSE {ctype.code}'[:150], priority='high', target_date=target,
                               notes='Raised by ZEO HSE – the competency is needed for permits to work.')


def needs_for_gaps(people, codes, on_date):
    if not on('training'):
        return 0
    n = 0
    d = on_date.date() if hasattr(on_date, 'date') else on_date
    for ct in CompetencyType.objects.filter(code__in=codes, course_id__isnull=False):
        for e in people:
            rec = Competency.objects.filter(employee=e, ctype=ct, assessed_competent=True).order_by('-issued_on').first()
            if rec is None:
                n += bool(raise_training_need(e, ct, 'missing', d))
            elif rec.expires_on and rec.expires_on < d:
                n += bool(raise_training_need(e, ct, f'expires {rec.expires_on}', rec.expires_on))
    return n


def expiring_needs():
    if not on('training'):
        return 0
    t = S.today()
    n = 0
    latest = {}
    for c in Competency.objects.filter(ctype__course_id__isnull=False, assessed_competent=True).select_related('ctype', 'employee').order_by('issued_on'):
        latest[(c.employee_id, c.ctype_id)] = c
    for c in latest.values():
        if c.expires_on and c.expires_on <= t + timedelta(days=60):
            n += bool(raise_training_need(c.employee, c.ctype, ('expired ' if c.expires_on < t else 'expires ') + str(c.expires_on), c.expires_on))
    return n


def training_compliance(to):
    """(valid, total) for HR mandatory courses with a validity: every active employee × course."""
    if not on('training'):
        return 0, 0
    Course, Cert = model('LearningManagement', 'Course'), model('LearningManagement', 'Certificate')
    from EmpManagement.models import emp_master
    courses = list(Course.objects.filter(is_active=True, course_type='mandatory').values_list('pk', flat=True))
    if not courses:
        return 0, 0
    emps = list(emp_master.objects.filter(is_active=True).values_list('pk', flat=True)[:5000])
    valid = set(Cert.objects.filter(course_id__in=courses, employee_id__in=emps).filter(Q(expiry_date__isnull=True) | Q(expiry_date__gte=to))
                .values_list('employee_id', 'course_id'))
    return len(valid), len(courses) * len(emps)


# ------------------------------------------------------------------ 2. projects
def project_site_ids(pid):
    return list(Site.objects.filter(project_id=pid).values_list('pk', flat=True))


def project_name(pid):
    return label('projects', pid) if pid else ''


def _project_issue(pid, title, description, impact='high', probability='medium'):
    PR = model('ZeoProjects', 'ProjectRisk')
    if PR is None or not on('projects') or not pid:
        return None
    x = PR.objects.filter(project_id=pid, title=title[:200]).first()
    if x:
        return x
    return PR.objects.create(project_id=pid, kind='issue', title=title[:200], description=description[:4000], impact=impact,
                             probability=probability, status='open', mitigation='Investigation and corrective actions are followed in ZEO HSE.')


@safe
def incident_serious(i):
    if i.project_risk_id or not i.site_id or not i.site.project_id:
        return None
    if not (i.high_potential or i.treatment in ('lost_time', 'fatality')):
        return None
    r = _project_issue(i.site.project_id, f'HSE {i.number}: {i.title}',
                       f'{"High-potential " if i.high_potential else ""}incident at {i.site.name} on {i.occurred_at:%Y-%m-%d}.\n{i.description[:1500]}')
    if r is not None:
        Incident.objects.filter(pk=i.pk).update(project_risk_id=r.pk)
        S.event('incident', i.pk, f'Added to the project risks and issues of {project_name(i.site.project_id)}')
    return r


@safe
def permit_stopped(p, reason):
    if not p.site.project_id:
        return None
    return _project_issue(p.site.project_id, f'Work stopped – permit {p.number}', f'{p.permit_type.name} at {p.site.name}: {reason}', 'medium', 'medium')


@safe
def claim_settled(c):
    """Net cost of a settled claim (paid − recovered) is added to the project costs of the incident's site."""
    PE = model('ZeoProjects', 'ProjectExpense')
    if PE is None or not on('projects') or c.project_expense_id or not c.incident_id or not c.incident.site_id or not c.incident.site.project_id:
        return None
    net = (c.paid_amount or 0) - (c.recovered_amount or 0)
    if net <= 0:
        return None
    x = PE.objects.create(project_id=c.incident.site.project_id, date=S.today(), category='other', description=f'HSE claim {c.number} ({c.get_claim_type_display()})'[:255],
                          vendor=(c.insurer or '')[:150], amount=net)
    Claim.objects.filter(pk=c.pk).update(project_expense_id=x.pk)
    S.event('claim', c.pk, f'Net cost {net} added to the costs of project {project_name(c.incident.site.project_id)}')
    return x


def can_see_project(request, pid):
    if A.is_admin(request) or A.can_view_all(request):
        return True
    P = model('ProjectManagement', 'Project')
    me = A.my_emp(request)
    if P is None or me is None:
        return False
    return P.objects.filter(pk=pid).filter(Q(managers=me) | Q(members=me)).exists()


def project_summary(pid, request):
    """The HSE tab of a project: figures and record numbers only – no injured names, treatment or medical details."""
    sites = list(Site.objects.filter(project_id=pid))
    ids = [s.pk for s in sites]
    t = S.today()
    year = t.replace(month=1, day=1)
    inc = Incident.objects.filter(site_id__in=ids)
    inc_y = inc.filter(occurred_at__date__gte=year)
    lti = inc.filter(treatment__in=('lost_time', 'fatality')).order_by('-occurred_at').first()
    acts = Action.objects.filter(site_id__in=ids).exclude(status__in=('closed', 'cancelled'))
    last_ins = Inspection.objects.filter(site_id__in=ids, status='completed', score__isnull=False).order_by('-date').first()
    from django.utils import timezone
    return {
        'project': pid, 'project_name': project_name(pid), 'sites': [{'id': s.pk, 'name': s.name} for s in sites],
        'incidents_year': inc_y.count(), 'near_misses_year': inc_y.filter(category__kind='near_miss').count(),
        'recordable_year': inc_y.filter(treatment__in=('medical', 'restricted', 'lost_time', 'fatality')).count(),
        'lti_year': inc_y.filter(treatment__in=('lost_time', 'fatality')).count(),
        'days_since_lti': (t - _local(lti.occurred_at).date()).days if lti else None,
        'open_incidents': inc.exclude(status='closed').count(), 'high_potential_open': inc.exclude(status='closed').filter(high_potential=True).count(),
        'live_permits': Permit.objects.filter(site_id__in=ids, status='active').count(),
        'suspended_permits': Permit.objects.filter(site_id__in=ids, status='suspended').count(),
        'open_actions': acts.count(), 'overdue_actions': acts.filter(due_date__lt=t).count(),
        'last_inspection': {'number': last_ins.number, 'date': last_ins.date.isoformat(), 'score': float(last_ins.score)} if last_ins else None,
        'recent_incidents': [{'id': x.pk, 'number': x.number, 'title': x.title, 'date': _local(x.occurred_at).date().isoformat(),
                              'status': x.get_status_display(), 'high_potential': x.high_potential,
                              'category': x.category.name if x.category_id and not x.category.restricted else ''}
                             for x in inc.select_related('category').order_by('-occurred_at')[:8]],
        'live_permit_list': [{'id': p.pk, 'number': p.number, 'type': p.permit_type.name, 'site': p.site.name, 'valid_to': p.valid_to.isoformat()}
                             for p in Permit.objects.filter(site_id__in=ids, status__in=('active', 'suspended')).select_related('permit_type', 'site')[:10]],
        'hse_staff': A.is_staff(request) or A.is_admin(request),
    }


# ------------------------------------------------------------------ 3. attendance → hours worked
def attendance_hours(frm, to, site=None):
    """Hours worked from attendance, or None when there is none for the period."""
    if not on('attendance'):
        return None
    branch = site.branch_id if site is not None else None
    if site is not None and not branch:
        return None
    D = model('AttendancePlus', 'AttendanceDay')
    if D is not None:
        qs = D.objects.filter(date__range=(frm, to))
        if branch:
            qs = qs.filter(branch_id=branch)
        mins = qs.aggregate(m=Sum('worked_minutes'))['m'] or 0
        if mins:
            return round(mins / 60)
    At = model('calendars', 'Attendance')
    if At is not None:
        qs = At.objects.filter(date__range=(frm, to), total_hours__isnull=False)
        if branch:
            qs = qs.filter(employee__emp_branch_id=branch)
        tot = qs.aggregate(t=Sum('total_hours'))['t']
        if tot:
            return round(tot.total_seconds() / 3600)
    return None


def hours_worked(frm, to, site=None):
    """(hours, source) for TRIR / LTIFR – attendance when linked and recorded, else the monthly hours in settings."""
    h = attendance_hours(frm, to, site)
    if h:
        return h, 'attendance'
    s = HSESettings.get()
    months = max(1, (to.year - frm.year) * 12 + to.month - frm.month + 1)
    return (s.hours_worked_month or 0) * months, 'settings'


def project_hours(frm, to, site_ids):
    """Hours of a project's sites – only from attendance (company hours in settings would mis-state a project rate)."""
    seen, total = set(), 0
    for s in Site.objects.filter(pk__in=site_ids):
        if s.branch_id and s.branch_id not in seen:
            seen.add(s.branch_id)
            total += attendance_hours(frm, to, s) or 0
    return (total, 'attendance') if total else (0, 'none')


# ------------------------------------------------------------------ 4. leave → lost days, return to work
def _is_injury_leave(lr):
    lt = lr.leave_type
    name = (lt.name or '').lower()
    return getattr(lt, 'leave_category', '') == 'sick' or 'injur' in name or 'accident' in name


def _leave_days(lr):
    return float(lr.approved_days or lr.number_of_days or 0)


def leave_info(i):
    if not i.leave_ids or not on('leave'):
        return []
    LR = model('calendars', 'employee_leave_request')
    return [{'id': x.pk, 'number': x.document_number, 'type': x.leave_type.name, 'from': x.start_date.isoformat(), 'to': x.end_date.isoformat(),
             'days': _leave_days(x), 'status': x.status} for x in LR.objects.filter(pk__in=i.leave_ids).select_related('leave_type')]


@safe
def link_leave(i, notify=True):
    """Approved sick / injury leave of the injured employee starting within 7 days of the incident → lost days."""
    if not on('leave') or not i.injured_employee_id or i.status == 'closed':
        return False
    LR = model('calendars', 'employee_leave_request')
    from django.utils import timezone
    d = _local(i.occurred_at).date()
    cands = LR.objects.filter(employee_id=i.injured_employee_id, status='approved', start_date__lte=d + timedelta(days=7), end_date__gte=d) \
        .select_related('leave_type')
    ids = list(i.leave_ids or [])
    new = [x for x in cands if x.pk not in ids and _is_injury_leave(x)]
    if not new:
        return False
    ids += [x.pk for x in new]
    days = sum(_leave_days(x) for x in LR.objects.filter(pk__in=ids, status='approved'))
    old = i.lost_days
    Incident.objects.filter(pk=i.pk).update(leave_ids=ids, lost_days=int(round(days)))
    S.event('incident', i.pk, 'Leave linked: ' + ', '.join(f'{x.document_number or x.pk} ({x.leave_type.name} {x.start_date}–{x.end_date})' for x in new)
            + f'. Lost days {old} → {int(round(days))} (from leave).')
    if notify and i.treatment not in ('lost_time', 'fatality'):
        S.tell_users(S.users_with_role('hse_officer') + S.users_with_role('hse_manager'), f'Check classification of {i.number}',
                     f'{i.number} {i.title}: the injured employee is on approved sick / injury leave ({int(round(days))} days), but the incident '
                     f'is classified as "{i.get_treatment_display()}". Check whether it is a lost-time injury.',
                     'incident', i.pk, i.number, S.PAGE + f'incidents/{i.pk}', f'Check classification of {i.number} (leave taken)')
    return True


@safe
def leave_saved(lr):
    if not on('leave') or lr.status != 'approved':
        return 0
    n = 0
    since = lr.start_date - timedelta(days=7)
    for i in Incident.objects.filter(injured_employee_id=lr.employee_id, occurred_at__date__gte=since, occurred_at__date__lte=lr.end_date) \
            .exclude(status='closed'):
        n += bool(link_leave(i))
    return n


def _health_number():
    return S.next_number('health')


@safe
def rejoined(rj):
    """Return to work after injury leave → fitness check in occupational health (once)."""
    if not on('leave') or not rj.leave_request_id:
        return None
    inc = next((i for i in Incident.objects.filter(injured_employee_id=rj.employee_id).exclude(leave_ids=[]) if rj.leave_request_id in (i.leave_ids or [])), None)
    if inc is None:
        return None
    origin = f'rtw:{rj.leave_request_id}'
    if HealthSurveillance.objects.filter(origin=origin).exists():
        return None
    last = HealthSurveillance.objects.filter(employee_id=rj.employee_id).order_by('-due_date').first()
    h = HealthSurveillance.objects.create(number=_health_number(), employee_id=rj.employee_id, assessment_type='return', due_date=rj.rejoining_date,
                                          exposure=last.exposure if last else 'other', job_role=last.job_role if last else '', origin=origin,
                                          interval_months=0)
    S.event('health', h.pk, f'Return-to-work check after {inc.number} (rejoined {rj.rejoining_date})')
    S.tell_users(S.users_with_role('occ_health'), f'Return-to-work check: {h.number}',
                 f'The employee injured in {inc.number} rejoined on {rj.rejoining_date}. Record the fitness for work and any restrictions.',
                 'health', h.pk, h.number, S.PAGE + f'r/health/{h.pk}', f'Return-to-work check {h.number}')
    return h


def on_leave(e, frm, to):
    if not on('leave') or e is None:
        return None
    LR = model('calendars', 'employee_leave_request')
    return LR.objects.filter(employee=e, status='approved', start_date__lte=to, end_date__gte=frm).select_related('leave_type').first()


# ------------------------------------------------------------------ 5. assets → equipment
def asset_info(asset_id):
    if not asset_id or not on('assets'):
        return None
    As = model('OrganisationManager', 'Asset')
    a = As.objects.filter(pk=asset_id).first()
    if a is None:
        return {'id': asset_id, 'name': f'#{asset_id} (removed)', 'status': '', 'maintenance': []}
    out = {'id': a.pk, 'name': a.name, 'serial': a.serial_number, 'label': _asset_label(a), 'status': a.status, 'status_label': a.get_status_display(),
           'code': '', 'maintenance': [], 'asset_plus': False}
    if model('AssetPlus', 'MaintenanceSchedule') is not None:
        try:
            from AssetPlus import services as APS
            from AssetPlus.models import AssetProfile, MaintenanceSchedule
            prof = AssetProfile.objects.filter(asset_id=a.pk).first()
            out['asset_plus'] = True
            out['code'] = prof.asset_code if prof else ''
            st = APS.effective_status(a, prof)
            out['status'], out['status_label'] = st, APS.STATUS_LABEL.get(st, st)
            for s in MaintenanceSchedule.objects.filter(asset_id=a.pk, active=True):
                out['maintenance'].append({'title': s.title, 'next_due_date': s.next_due_date.isoformat() if s.next_due_date else None,
                                           'state': APS.schedule_due(s, prof)})
        except Exception:
            log.exception('Asset Plus details failed')
    return out


def asset_checks(p):
    """Permit prerequisite rows for the linked equipment."""
    info = asset_info(p.asset_id)
    if info is None:
        return []
    out = []
    bad = {'disposed': 'disposed', 'lost': 'reported lost', 'maintenance': 'under maintenance'}
    st = info.get('status')
    out.append({'key': 'asset', 'label': f'Equipment available: {info.get("label") or info["name"]}', 'ok': st not in bad,
                'detail': f'The equipment is {bad[st]}' if st in bad else ''})
    overdue = [m['title'] for m in info['maintenance'] if m['state'] == 'overdue']
    if info.get('asset_plus'):
        out.append({'key': 'asset_maintenance', 'label': 'Equipment maintenance / inspection not overdue', 'ok': not overdue,
                    'detail': 'Overdue: ' + ', '.join(overdue) if overdue else ''})
    return out


def report_asset_damage(i, request, severity, repair_cost, note=''):
    if not on('assets') or model('AssetPlus', 'AssetDamage') is None:
        raise S.HSEError('Asset damage needs the Asset Plus app.')
    if not i.asset_id:
        raise S.HSEError('Link the equipment to the incident first.', 'asset_id')
    if i.asset_damage_id:
        raise S.HSEError('The asset damage record already exists.')
    from AssetPlus import services as APS
    from django.utils import timezone
    As = model('OrganisationManager', 'Asset')
    a = As.objects.filter(pk=i.asset_id).first()
    if a is None:
        raise S.HSEError('The equipment no longer exists in the asset register.')
    try:
        d = APS.report_damage(a, request.user, damage_date=min(_local(i.occurred_at).date(), S.today()),
                              description=f'HSE incident {i.number}: {i.title}' + (f'\n{note}' if note else ''), severity=severity or 'minor',
                              repair_cost=repair_cost or 0, source='hse')
    except Exception as e:
        raise S.HSEError(str(getattr(e, 'message', None) or e))
    Incident.objects.filter(pk=i.pk).update(asset_damage_id=d.pk)
    S.event('incident', i.pk, f'Asset damage {d.number} created in Asset Plus (waits for approval there)', request.user)
    return d


def damage_info(i):
    if not i.asset_damage_id or model('AssetPlus', 'AssetDamage') is None:
        return None
    from AssetPlus.models import AssetDamage
    d = AssetDamage.objects.filter(pk=i.asset_damage_id).first()
    return {'id': d.pk, 'number': d.number, 'status': d.get_status_display(), 'severity': d.get_severity_display()} if d else None


# ------------------------------------------------------------------ 6. roster, leave and fatigue on the permit crew
def crew_checks(p):
    people = [x for x in [p.receiver] + list(p.crew.all()) if x is not None]
    if not people:
        return []
    out = []
    from django.utils import timezone
    d0, d1 = _local(p.valid_from).date(), _local(p.valid_to).date()
    if on('leave'):
        away = []
        for e in people:
            lr = on_leave(e, d0, d1)
            if lr:
                away.append(f'{S.emp_name(e)} ({lr.leave_type.name} {lr.start_date}–{lr.end_date})')
        out.append({'key': 'leave', 'label': 'Receiver and crew not on approved leave', 'ok': not away, 'detail': '; '.join(away)})
    if on('roster'):
        RE = model('ShiftPlanner', 'RosterEntry')
        off = [S.emp_name(e) for e in people if RE.objects.filter(employee_id=e.pk, date=d0, off=True).exists()]
        out.append({'key': 'roster', 'label': 'Crew rostered on the permit date (warning only)', 'ok': True,
                    'detail': ('Rostered off: ' + ', '.join(off)) if off else ''})
    s = HSESettings.get()
    if s.fatigue_max_hours and on('attendance'):
        D = model('AttendancePlus', 'AttendanceDay')
        tired = []
        if D is not None:
            for e in people:
                day = D.objects.filter(employee_id=e.pk, date=d0 - timedelta(days=1)).first()
                if day and day.worked_minutes > s.fatigue_max_hours * 60:
                    tired.append(f'{S.emp_name(e)} ({round(day.worked_minutes / 60, 1)} h)')
        out.append({'key': 'fatigue', 'label': f'No one worked over {s.fatigue_max_hours} h the day before (warning only)', 'ok': True,
                    'detail': ('Long day: ' + ', '.join(tired)) if tired else ''})
    return out


# ------------------------------------------------------------------ 7. muster list
def muster(site=None):
    """Employees who punched in today (or a night shift from yesterday) and have not punched out."""
    if not on('muster'):
        raise S.HSEError('The muster list needs the attendance app.')
    from EmpManagement.models import emp_master
    t = S.today()
    rows = {}
    branch = site.branch_id if site is not None else None
    D = model('AttendancePlus', 'AttendanceDay')
    if D is not None:
        qs = D.objects.filter(date__in=(t, t - timedelta(days=1)), first_in__isnull=False, last_out__isnull=True)
        if branch:
            qs = qs.filter(branch_id=branch)
        for x in qs:
            if x.date < t and not x.is_night_shift:
                continue
            rows[x.employee_id] = x.first_in
    At = model('calendars', 'Attendance')
    if At is not None:
        qs = At.objects.filter(date=t, check_in_time__isnull=False, check_out_time__isnull=True)
        if branch:
            qs = qs.filter(employee__emp_branch_id=branch)
        for x in qs:
            rows.setdefault(x.employee_id, datetime.combine(t, x.check_in_time))
    from django.utils import timezone
    crews = {}
    for p in Permit.objects.filter(status__in=('active', 'suspended')).filter(**({'site': site} if site else {})).prefetch_related('crew'):
        for e in [p.receiver] + list(p.crew.all()):
            if e is not None:
                crews.setdefault(e.pk, []).append(p.number)
    out = []
    for e in emp_master.objects.filter(pk__in=rows.keys()).select_related('emp_dept_id', 'emp_branch_id').order_by('emp_first_name'):
        since = rows[e.pk]
        if isinstance(since, datetime) and timezone.is_aware(since):
            since = _local(since)
        out.append({'id': e.pk, 'name': S.emp_name(e), 'code': e.emp_code, 'department': getattr(e.emp_dept_id, 'dept_name', '') if e.emp_dept_id_id else '',
                    'branch': getattr(e.emp_branch_id, 'branch_name', '') if e.emp_branch_id_id else '',
                    'since': since.strftime('%H:%M') if since else '', 'phone': getattr(e, 'emp_mobile_number_1', '') or '',
                    'permits': crews.get(e.pk, [])})
    return {'site': site.name if site else '', 'branch_filtered': bool(branch), 'at': _local(S.now()).strftime('%Y-%m-%d %H:%M'),
            'count': len(out), 'people': out,
            'note': 'Contractor workers and visitors are not in attendance – add them from the gate register.'}


# ------------------------------------------------------------------ 8. onboarding
INDUCTION = ('L&D', 'HSE induction and site safety rules')
MEDICAL = ('HR', 'Pre-employment medical (occupational health)')


@safe
def offer_saved(o):
    if not on('onboarding') or o.status not in ('accepted', 'joined'):
        return 0
    T = model('RecruitmentManagement', 'OnboardingTask')
    n = 0
    for team, title in (INDUCTION, MEDICAL):
        if not T.objects.filter(offer=o, title=title).exists():
            T.objects.create(offer=o, team=team, title=title, due_date=o.joining_date)
            n += 1
    return n


# ------------------------------------------------------------------ 9. handover on transfer / leaving
def _open(model_, **kw):
    qs = model_.objects.filter(**kw)
    if hasattr(model_, 'is_active'):
        qs = qs.filter(is_active=True)
    return qs


HANDOVER = (
    ('actions', 'Corrective actions to do', Action, 'owner', lambda q: q.exclude(status__in=('closed', 'cancelled', 'done'))),
    ('verify', 'Actions to verify', Action, 'verifier', lambda q: q.exclude(status__in=('closed', 'cancelled'))),
    ('permits', 'Permits (receiver)', Permit, 'receiver', lambda q: q.filter(status__in=('draft', 'submitted', 'approved', 'active', 'suspended'))),
    ('incidents', 'Investigations', Incident, 'investigator', lambda q: q.exclude(status='closed')),
    ('risks', 'Risk assessments (owner)', RiskAssessment, 'owner', lambda q: q.filter(status__in=('draft', 'submitted', 'approved'))),
    ('sites', 'Sites managed', Site, 'manager', lambda q: q),
    ('moc', 'Change requests (coordinator)', ChangeRequest, 'coordinator', lambda q: q.exclude(status__in=('closed', 'rejected', 'cancelled'))),
    ('audits', 'Audits (lead auditor)', Audit, 'lead_auditor', lambda q: q.exclude(status__in=('closed', 'cancelled'))),
    ('ncs', 'Quality nonconformities', Nonconformity, 'owner', lambda q: q.exclude(status__in=('closed', 'cancelled'))),
    ('kpis', 'KPIs owned', KPI, 'owner', lambda q: q),
    ('aspects', 'Environmental aspects', EnvAspect, 'owner', lambda q: q),
    ('parameters', 'Monitoring parameters', EnvParameter, 'owner', lambda q: q),
    ('chemicals', 'Chemicals owned', Chemical, 'owner', lambda q: q.exclude(status__in=('archived', 'rejected'))),
    ('claims', 'Claims owned', Claim, 'owner', lambda q: q.exclude(status='closed')),
    ('obligations', 'Compliance obligations (owner)', Obligation, 'owner', lambda q: q),
    ('obligations_review', 'Compliance obligations (reviewer)', Obligation, 'reviewer', lambda q: q),
    ('documents', 'Documents owned', HSEDocument, 'owner', lambda q: q.exclude(status__in=('superseded', 'obsolete', 'withdrawn'))),
    ('doc_approvals', 'Documents to approve', HSEDocument, 'approver', lambda q: q.filter(status__in=('draft', 'review'))),
)


def open_items(e):
    out = []
    for key, lbl, m, field, flt in HANDOVER:
        qs = flt(_open(m, **{field: e}))
        n = qs.count()
        if n:
            out.append({'key': key, 'label': lbl, 'count': n,
                        'records': [getattr(x, 'number', None) or getattr(x, 'code', None) or getattr(x, 'ref', None) or str(x.pk) for x in qs[:12]]})
    return out


def _handover(e, reason, ref, eff, detail):
    if e is None or Handover.objects.filter(ref=ref).exists():
        return None
    items = open_items(e)
    h = Handover.objects.create(employee=e, reason=reason, ref=ref, effective_date=eff, detail=detail[:255], status='open' if items else 'nothing')
    if items:
        txt = (f'{S.emp_name(e)} – {h.get_reason_display().lower()} ({detail}, from {eff}). Open HSE work:\n'
               + '\n'.join(f'- {x["label"]}: {x["count"]} ({", ".join(x["records"][:6])})' for x in items))
        S.tell_users(S.users_with_role('hse_manager') + [u for u in [_manager_user(e)] if u is not None], f'HSE handover: {S.emp_name(e)}', txt,
                     'handover', h.pk, S.emp_name(e), S.PAGE + 'links/handovers', f'Reassign the HSE work of {S.emp_name(e)}')
    return h


@safe
def transfer_saved(t):
    if not on('handover') or t.status != 'done' or not t.from_branch_id or t.from_branch_id == t.to_branch_id:
        return None
    from OrganisationManager.models import brnch_mstr
    names = dict(brnch_mstr.objects.filter(pk__in=[t.from_branch_id, t.to_branch_id]).values_list('pk', 'branch_name'))
    return _handover(_emp(t.employee_id), 'transfer', f'transfer:{t.pk}', t.effective_date,
                     f'{names.get(t.from_branch_id, t.from_branch_id)} → {names.get(t.to_branch_id, t.to_branch_id)}')


@safe
def resignation_saved(r):
    if not on('handover') or str(r.status).lower() != 'approved':
        return None
    h = _handover(r.employee, 'leaving', f'resignation:{r.pk}', r.last_working_date, f'{r.get_termination_type_display()}, last day {r.last_working_date}')
    origin = f'exit:{r.pk}'
    last = HealthSurveillance.objects.filter(employee=r.employee).exclude(assessment_type__in=('exit', 'return')).order_by('-due_date').first()
    if last is not None and not HealthSurveillance.objects.filter(origin=origin).exists():
        x = HealthSurveillance.objects.create(number=_health_number(), employee=r.employee, assessment_type='exit', due_date=r.last_working_date,
                                              exposure=last.exposure, job_role=last.job_role, origin=origin, interval_months=0)
        S.event('health', x.pk, f'Exit medical planned – last working day {r.last_working_date}')
        S.tell_users(S.users_with_role('occ_health'), f'Exit medical: {x.number}', f'Plan the exit medical before {r.last_working_date}.',
                     'health', x.pk, x.number, S.PAGE + f'r/health/{x.pk}', f'Exit medical {x.number}')
    return h


def reassign(h, to, request, note=''):
    if h.status != 'open':
        raise S.HSEError('This handover is already done.')
    if to is None:
        raise S.HSEError('Choose who takes over.', 'to_employee')
    if to.pk == h.employee_id:
        raise S.HSEError('Choose another person.', 'to_employee')
    moved = []
    with transaction.atomic():
        for key, lbl, m, field, flt in HANDOVER:
            qs = list(flt(_open(m, **{field: h.employee})))
            if not qs:
                continue
            mk = {Action: 'action', Permit: 'permit', Incident: 'incident', RiskAssessment: 'risk'}.get(m)
            for x in qs:
                setattr(x, field, to)
                x.save(update_fields=[field])
                if mk:
                    S.event(mk, x.pk, f'{lbl}: {S.emp_name(h.employee)} → {S.emp_name(to)} (handover – {h.get_reason_display().lower()})', request.user)
            moved.append(f'{lbl}: {len(qs)}')
        h.status, h.done_by, h.done_at = 'done', request.user, S.now()
        h.note = (f'Reassigned to {S.emp_name(to)}. ' + '; '.join(moved) + (f'\n{note}' if note else ''))[:4000]
        h.save()
    S.close_todos('handover', h.pk)
    S.tell_emp(to, 'HSE work handed over to you', f'You now own the HSE work of {S.emp_name(h.employee)}:\n' + '\n'.join(moved),
               'handover', h.pk, S.emp_name(h.employee), S.PAGE + 'home')
    return h


def handover_row(h):
    return {'id': h.pk, 'employee': {'id': h.employee_id, 'name': S.emp_name(h.employee), 'code': h.employee.emp_code}, 'reason': h.reason,
            'reason_label': h.get_reason_display(), 'effective_date': h.effective_date.isoformat() if h.effective_date else None, 'detail': h.detail,
            'status': h.status, 'status_label': h.get_status_display(), 'note': h.note, 'done_by': S.user_name(h.done_by),
            'items': open_items(h.employee) if h.status == 'open' else []}


# ------------------------------------------------------------------ 10. self-service safety complaints
@safe
def grievance_saved(g):
    if not on('grievance') or g.category != 'safety' or Observation.objects.filter(grievance_id=g.pk).exists():
        return None
    e = None if g.anonymous else _emp(g.employee_id)
    site = Site.objects.filter(branch_id=g.branch_id, is_active=True).first() if g.branch_id else None
    o = Observation.objects.create(number=S.next_number('observation'), kind='unsafe_condition', category='other', observed_risk='medium',
                                   description=f'Safety complaint {g.number}: {g.subject}\n{g.description}'[:8000], site=site,
                                   location=(g.against or '')[:255], confidential=True, grievance_id=g.pk,
                                   reported_by=e.users if e is not None and e.users_id else None)
    S.event('observation', o.pk, f'Passed on from self-service complaint {g.number or g.pk}' + (' (anonymous)' if g.anonymous else ''))
    S.tell_users(S.users_with_role('hse_manager'), f'Safety complaint passed to HSE: {o.number}',
                 f'A self-service complaint in the Safety category was passed to HSE as confidential observation {o.number}.\n{g.subject}',
                 'observation', o.pk, o.number, S.PAGE + 'observations', f'Review safety complaint {o.number}')
    return o


# ------------------------------------------------------------------ 11. safety alerts
def publish_alert(i, request, title, message):
    if not on('alerts'):
        raise S.HSEError('Announcements are not available.')
    if i.alert_id:
        raise S.HSEError('A safety alert was already published for this incident.')
    if not (title or '').strip() or not (message or '').strip():
        raise S.HSEError('Give the alert a title and the message.', 'message')
    An = model('OrganisationManager', 'Announcement')
    a = An.objects.create(title=title.strip()[:255], message=message.strip(), send_email=False, allow_comments=False, created_by=request.user)
    if i.site_id and i.site.branch_id:
        a.branches.set([i.site.branch_id])
    Incident.objects.filter(pk=i.pk).update(alert_id=a.pk)
    S.event('incident', i.pk, f'Safety alert published as announcement "{a.title}"', request.user)
    return a


def alert_info(i):
    if not i.alert_id or model('OrganisationManager', 'Announcement') is None:
        return None
    An = model('OrganisationManager', 'Announcement')
    a = An.objects.filter(pk=i.alert_id).first()
    if a is None:
        return None
    return {'id': a.pk, 'title': a.title, 'published': a.created_at.isoformat(), 'read_by': a.announcement_views.count(),
            'branches': [b.branch_name for b in a.branches.all()]}


# ------------------------------------------------------------------ 12. performance goals
def kpi_to_performance(k, request):
    if not on('performance'):
        raise S.HSEError('The Performance app is not available.')
    PK = model('PerformanceManagement', 'KPI')
    if k.perf_kpi_id and PK.objects.filter(pk=k.perf_kpi_id).exists():
        raise S.HSEError('This KPI is already in the Performance KPI library.')
    code = f'HSE-{k.code}'[:30]
    x = PK.objects.filter(code=code).first() or PK.objects.create(
        code=code, name=k.name[:200], kpi_type='kpi', measure='quantitative', default_weight=10, created_by=request.user,
        description=(f'From ZEO HSE ({k.get_formula_display()}). Target {k.target if k.target is not None else "-"} {k.unit}. '
                     f'{"Higher" if k.higher_is_better else "Lower"} is better. Goal progress is filled from the monthly HSE value.'))
    KPI.objects.filter(pk=k.pk).update(perf_kpi_id=x.pk)
    S.event('kpi', k.pk, f'Added to the Performance KPI library as {x.code}', request.user)
    return k


def progress(k, value):
    if value is None or k.target in (None, 0):
        return None
    v, t = Decimal(value), Decimal(k.target)
    if k.higher_is_better:
        pct = v * 100 / t
    else:
        pct = Decimal(100) if v <= t else (t * 100 / v if v else Decimal(100))
    return max(0, min(100, int(round(pct))))


@safe
def push_goal_progress(k, value):
    if not k.perf_kpi_id or not on('performance'):
        return 0
    pct = progress(k, value)
    if pct is None:
        return 0
    G = model('PerformanceManagement', 'Goal')
    st = 'on_track' if pct >= 95 else ('at_risk' if pct >= 75 else 'off_track')
    return G.objects.filter(kpi_id=k.perf_kpi_id, sheet__cycle__status='active', sheet__status__in=('draft', 'submitted', 'approved')) \
        .update(progress_percent=pct, progress_status=st)


# ------------------------------------------------------------------ 13. HSE cost by site / cost centre / project
def cost_report(frm, to):
    cc = {}
    if on('costs'):
        C = model('ExpenseManagement', 'CostCenter')
        cc = {x.pk: f'{x.code} – {x.name}' for x in C.objects.all()}
    rows = {}
    for i in Incident.objects.filter(occurred_at__date__range=(frm, to)).select_related('site'):
        r = rows.setdefault(i.site_id, {'incidents': 0, 'loss': Decimal(0), 'claims_paid': Decimal(0), 'claims_recovered': Decimal(0), 'claims': 0})
        r['incidents'] += 1
        r['loss'] += i.estimated_loss or 0
    for c in Claim.objects.filter(created_at__date__range=(frm, to)).select_related('incident'):
        sid = c.incident.site_id if c.incident_id else None
        r = rows.setdefault(sid, {'incidents': 0, 'loss': Decimal(0), 'claims_paid': Decimal(0), 'claims_recovered': Decimal(0), 'claims': 0})
        r['claims'] += 1
        r['claims_paid'] += c.paid_amount or 0
        r['claims_recovered'] += c.recovered_amount or 0
    sites = {s.pk: s for s in Site.objects.filter(pk__in=[k for k in rows if k])}
    out = []
    for sid, r in rows.items():
        s = sites.get(sid)
        net = r['loss'] + r['claims_paid'] - r['claims_recovered']
        out.append({'site': s.name if s else '(no site)', 'cost_center': cc.get(s.cost_center_id, '') if s and s.cost_center_id else '',
                    'project': project_name(s.project_id) if s and s.project_id and on('projects') else '', 'incidents': r['incidents'],
                    'estimated_loss': float(r['loss']), 'claims': r['claims'], 'claims_paid': float(r['claims_paid']),
                    'claims_recovered': float(r['claims_recovered']), 'net_cost': float(net)})
    out.sort(key=lambda x: -x['net_cost'])
    return {'from': frm.isoformat(), 'to': to.isoformat(), 'rows': out, 'total': round(sum(x['net_cost'] for x in out), 2),
            'by_cost_centre': on('costs'), 'by_project': on('projects')}


# ------------------------------------------------------------------ signals and the daily safety net
def _handler(fn):
    def h(sender, instance, **kw):
        try:
            if getattr(connection, 'schema_name', 'public') == 'public':
                return
            if 'ZeoHSE_hsesettings'.lower() not in {t.lower() for t in _tables()}:
                return
            if not HSESettings.activated():   # HSE not activated in this company
                return
            fn(instance)
        except Exception:
            log.exception('HSE link signal %s failed', fn.__name__)
    h.__name__ = 'hse_' + fn.__name__
    return h


_HANDLERS = []


def connect():
    """Called from apps.ready(): listen to the other apps only when they are installed (lazy, nothing imported otherwise)."""
    from django.db.models.signals import post_delete, post_save
    pairs = [('LearningManagement', 'Certificate', post_save, certificate_saved), ('LearningManagement', 'Certificate', post_delete, certificate_deleted),
             ('calendars', 'employee_leave_request', post_save, leave_saved), ('calendars', 'EmployeeRejoining', post_save, rejoined),
             ('RecruitmentManagement', 'Offer', post_save, offer_saved), ('HRActions', 'EmployeeTransfer', post_save, transfer_saved),
             ('EmpManagement', 'EmployeeResignation', post_save, resignation_saved), ('SelfService', 'Grievance', post_save, grievance_saved)]
    for app, name, sig, fn in pairs:
        try:
            m = djapps.get_model(app, name)
        except Exception:
            continue
        h = _handler(fn)
        _HANDLERS.append(h)
        sig.connect(h, sender=m, weak=False, dispatch_uid=f'zeohse-link-{app}-{name}-{fn.__name__}')


def daily():
    """Catch anything the signals missed (bulk updates, imports, links switched on later)."""
    out = {}
    if not HSESettings.activated():
        return out
    if on('training'):
        out['certificates'] = sync_certificates()
        out['training_needs'] = expiring_needs()
    if on('leave'):
        n = 0
        since = S.today() - timedelta(days=120)
        for i in Incident.objects.filter(injured_employee__isnull=False, occurred_at__date__gte=since).exclude(status='closed'):
            n += bool(link_leave(i))
        out['leave_linked'] = n
        RJ = model('calendars', 'EmployeeRejoining')
        if RJ is not None:
            ids = {x for i in Incident.objects.exclude(leave_ids=[]) for x in (i.leave_ids or [])}
            out['return_to_work'] = sum(bool(rejoined(r)) for r in RJ.objects.filter(leave_request_id__in=ids))
    if on('grievance'):
        G = model('SelfService', 'Grievance')
        done = set(Observation.objects.filter(grievance_id__isnull=False).values_list('grievance_id', flat=True))
        out['complaints'] = sum(bool(grievance_saved(g)) for g in G.objects.filter(category='safety').exclude(pk__in=done)[:200])
    if on('handover'):
        n = 0
        T = model('HRActions', 'EmployeeTransfer')
        if T is not None:
            for t in T.objects.filter(status='done', effective_date__gte=S.today() - timedelta(days=60)):
                n += bool(transfer_saved(t))
        R = model('EmpManagement', 'EmployeeResignation')
        if R is not None:
            for r in R.objects.filter(status__iexact='approved', last_working_date__gte=S.today()):
                n += bool(resignation_saved(r))
        out['handovers'] = n
    if on('onboarding'):
        O = model('RecruitmentManagement', 'Offer')
        out['onboarding'] = sum(offer_saved(o) or 0 for o in O.objects.filter(status='accepted', joining_date__gte=S.today()))
    return out
