"""Business logic of LearningPlus. Views and tasks call these; LearningManagement.services calls
sync_attendance / skills_from_course through plus_hook()."""
import logging
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from dateutil.relativedelta import relativedelta
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Avg, Count, Q, Sum
from django.utils import timezone

from zeo.module_helpers import notify
from .models import (DEFAULT_SKILL_LEVELS, AlertLog, CourseModule, CourseSkill, EmployeeSkill, ModuleProgress, OnlineEnrolment,
                     RoleSkill, SessionAttendance, SessionCost, SessionExtra, Skill, SkillLevel, Trainer, TrainingBudget)

log = logging.getLogger(__name__)
Z = Decimal('0')


def _lm():
    from LearningManagement import models as m
    return m


def _emp_model():
    from EmpManagement.models import emp_master
    return emp_master


def money(v):
    return float(Decimal(v or 0).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def emp_name(e):
    if e is None:
        return ''
    n = ' '.join(x for x in [e.emp_first_name, e.emp_last_name] if x)
    return f"{n} ({e.emp_code})" if n else e.emp_code


def skill_levels():
    labels = dict(DEFAULT_SKILL_LEVELS)
    labels.update({s.level: s.label for s in SkillLevel.objects.all()})
    return [{'level': k, 'label': labels[k]} for k in sorted(labels)]


# ------------------------------------------------------------------ trainers
def trainer_stats(trainer):
    m = _lm()
    ids = set(SessionExtra.objects.filter(trainer=trainer).values_list('session_id', flat=True))
    if trainer.employee_id:
        ids |= set(m.TrainingSession.objects.filter(trainer_employee_id=trainer.employee_id).values_list('id', flat=True))
    agg = m.ParticipantResult.objects.filter(nomination__session_id__in=ids, trainer_rating__isnull=False).aggregate(
        avg=Avg('trainer_rating'), n=Count('id'))
    return {'sessions': len(ids), 'avg_rating': round(float(agg['avg']), 2) if agg['avg'] is not None else None, 'ratings': agg['n']}


# ------------------------------------------------------------------ calendar
STATUS_COLOR = {'draft': '#9aa0b4', 'published': '#5b4fe0', 'in_progress': '#0e9db0', 'completed': '#2e7d4f', 'cancelled': '#b3263a'}


def calendar_events(frm, to, employee=None, include_drafts=False, include_cancelled=False, course=None):
    m = _lm()
    qs = m.TrainingSession.objects.select_related('course', 'trainer_employee').filter(end_date__gte=frm, start_date__lte=to)
    if not include_drafts:
        qs = qs.exclude(status='draft')
    if not include_cancelled:
        qs = qs.exclude(status='cancelled')
    if course:
        qs = qs.filter(course_id=course)
    sessions = list(qs.order_by('start_date'))
    ids = [s.id for s in sessions]
    extras = {x.session_id: x for x in SessionExtra.objects.filter(session_id__in=ids).select_related('trainer', 'venue')}
    confirmed = dict(m.Nomination.objects.filter(session_id__in=ids, seat_status='confirmed').values('session_id')
                     .annotate(n=Count('id')).values_list('session_id', 'n'))
    mine = {}
    if employee is not None:
        mine = dict(m.Nomination.objects.filter(session_id__in=ids, employee=employee).values_list('session_id', 'seat_status'))
    out = []
    for s in sessions:
        x = extras.get(s.id)
        trainer = (x.trainer and str(x.trainer)) if x and x.trainer else (emp_name(s.trainer_employee) if s.trainer_employee else s.trainer_external)
        venue = (x.venue.name if x and x.venue else None) or s.venue or ('Online' if s.online_link else None)
        start = f"{s.start_date.isoformat()}T{s.start_time.strftime('%H:%M')}" if s.start_time else s.start_date.isoformat()
        end = f"{s.end_date.isoformat()}T{s.end_time.strftime('%H:%M')}" if s.end_time else s.end_date.isoformat()
        out.append({
            'id': f"training-{s.id}", 'session_id': s.id, 'type': 'training', 'code': s.code,
            'title': s.course.title, 'start': start, 'end': end, 'all_day': not (s.start_time and s.end_time),
            'start_date': s.start_date, 'end_date': s.end_date, 'start_time': s.start_time, 'end_time': s.end_time,
            'venue': venue, 'online_link': s.online_link, 'trainer': trainer, 'status': s.status,
            'seats': s.seats, 'confirmed': confirmed.get(s.id, 0), 'color': STATUS_COLOR.get(s.status, '#5b4fe0'),
            'my_status': mine.get(s.id), 'url': '/main-sidebar/learning-options/calendar',
        })
    return out


# ------------------------------------------------------------------ attendance sheet
def session_days(session):
    d, out = session.start_date, []
    while d <= session.end_date:
        out.append(d)
        d += timedelta(days=1)
    return out


def attendance_sheet(session):
    m = _lm()
    noms = list(m.Nomination.objects.filter(session=session, seat_status='confirmed').select_related('employee').order_by('employee__emp_code'))
    marks = defaultdict(dict)
    for a in SessionAttendance.objects.filter(session_id=session.id):
        marks[a.nomination_id][a.date.isoformat()] = {'present': a.present, 'hours': float(a.hours) if a.hours is not None else None}
    results = {r.nomination_id: r for r in m.ParticipantResult.objects.filter(nomination__in=noms)}
    return {
        'session': session.id, 'code': session.code, 'course': session.course.title,
        'dates': [d.isoformat() for d in session_days(session)],
        'rows': [{'nomination': n.id, 'employee_id': n.employee_id, 'employee': emp_name(n.employee), 'marks': marks.get(n.id, {}),
                  'attendance_percent': results[n.id].attendance_percent if n.id in results else None} for n in noms],
    }


@transaction.atomic
def save_attendance(session, rows):
    """rows: [{nomination, date, present, hours?, remarks?}] -> upsert, then attendance % on each result."""
    m = _lm()
    noms = set(m.Nomination.objects.filter(session=session).exclude(seat_status='cancelled').values_list('id', flat=True))
    saved = 0
    for r in rows or []:
        nid = int(r.get('nomination') or r.get('nomination_id') or 0)
        if nid not in noms:
            raise ValidationError(f"Nomination {nid} is not a participant of {session.code}.")
        d = r.get('date')
        d = date.fromisoformat(d) if isinstance(d, str) else d
        if not d or d < session.start_date or d > session.end_date:
            raise ValidationError(f"{d} is outside the session dates ({session.start_date} - {session.end_date}).")
        hours = r.get('hours')
        SessionAttendance.objects.update_or_create(
            nomination_id=nid, date=d,
            defaults={'session_id': session.id, 'present': bool(r.get('present', True)),
                      'hours': Decimal(str(hours)) if hours not in (None, '') else None, 'remarks': r.get('remarks') or None})
        saved += 1
    return {'saved': saved, 'percent': sync_attendance(session.id)}


def sync_attendance(session_id):
    """attendance % = days present / training days on the sheet (distinct dates marked for the session)."""
    m = _lm()
    rows = list(SessionAttendance.objects.filter(session_id=session_id))
    dates = {r.date for r in rows}
    if not dates:
        return {}
    present = defaultdict(int)
    for r in rows:
        if r.present:
            present[r.nomination_id] += 1
    out = {}
    for nom in m.Nomination.objects.filter(session_id=session_id, id__in={r.nomination_id for r in rows}).select_related('session__course'):
        pct = int(round(present[nom.id] * 100 / len(dates)))
        res, _ = m.ParticipantResult.objects.get_or_create(nomination=nom)
        res.attendance_percent, res.attended = pct, pct > 0
        res.evaluate()
        res.save()
        out[nom.id] = pct
    return out


# ------------------------------------------------------------------ skills
def skills_from_course(employee_id, course, source='course', ref=None):
    """Raise EmployeeSkill to the level the course gives (never lowers a higher level)."""
    n = 0
    today = timezone.localdate()
    cid = course.id if hasattr(course, 'id') else int(course)
    for cs in CourseSkill.objects.filter(course_id=cid).select_related('skill'):
        es = EmployeeSkill.objects.filter(employee_id=employee_id, skill=cs.skill).first()
        if es is None:
            EmployeeSkill.objects.create(employee_id=employee_id, skill=cs.skill, level=cs.level_gained, source=source,
                                         source_ref=ref or getattr(course, 'code', None), date=today)
            n += 1
        elif es.level < cs.level_gained:
            es.level, es.source, es.source_ref, es.date = cs.level_gained, source, ref or getattr(course, 'code', None), today
            es.save()
            n += 1
    return n


def skill_matrix(employees, skill_ids=None, only_gaps=False):
    employees = list(employees.select_related('emp_dept_id', 'emp_desgntn_id'))
    emp_ids = [e.id for e in employees]
    desig_ids = {e.emp_desgntn_id_id for e in employees if e.emp_desgntn_id_id}
    req = defaultdict(dict)
    for r in RoleSkill.objects.filter(designation_id__in=desig_ids):
        req[r.designation_id][r.skill_id] = r.required_level
    have = defaultdict(dict)
    for s in EmployeeSkill.objects.filter(employee_id__in=emp_ids):
        have[s.employee_id][s.skill_id] = {'level': s.level, 'source': s.source, 'verified': bool(s.verified_by)}
    skills = Skill.objects.filter(is_active=True)
    if skill_ids:
        skills = skills.filter(id__in=skill_ids)
    else:  # skills that matter for these people
        used = {sid for d in req.values() for sid in d} | {sid for d in have.values() for sid in d}
        skills = skills.filter(id__in=used)
    skills = list(skills)
    rows, gaps = [], 0
    for e in employees:
        cells, emp_gaps = {}, 0
        for sk in skills:
            h = have[e.id].get(sk.id)
            level = h['level'] if h else 0
            required = req.get(e.emp_desgntn_id_id, {}).get(sk.id)
            gap = max((required or 0) - level, 0)
            emp_gaps += 1 if gap else 0
            cells[str(sk.id)] = {'level': level, 'required': required, 'gap': gap, 'source': h['source'] if h else None,
                                 'verified': h['verified'] if h else False}
        gaps += emp_gaps
        if only_gaps and not emp_gaps:
            continue
        rows.append({'employee_id': e.id, 'code': e.emp_code, 'name': emp_name(e),
                     'department': e.emp_dept_id.dept_name if e.emp_dept_id else None, 'department_id': e.emp_dept_id_id,
                     'designation': e.emp_desgntn_id.desgntn_job_title if e.emp_desgntn_id else None,
                     'designation_id': e.emp_desgntn_id_id, 'gaps': emp_gaps, 'cells': cells})
    return {'levels': skill_levels(), 'skills': [{'id': s.id, 'name': s.name, 'category': s.category} for s in skills],
            'rows': rows, 'gaps': gaps}


# ------------------------------------------------------------------ online courses
def enrol(course, employee, user=None, nomination=None):
    en, created = OnlineEnrolment.objects.get_or_create(
        course_id=course.id, employee_id=employee.id,
        defaults={'assigned_by': getattr(user, 'id', None), 'nomination_id': getattr(nomination, 'id', None)})
    if created and user is not None and getattr(employee, 'users_id', None) != user.id:
        notify(employee=employee, title='Online course assigned', notification_type='learning',
               message=f"{course.title} ({course.code}) was assigned to you. Open My Learning to start.")
    return en


def course_progress(employee_id, course_id):
    mods = list(CourseModule.objects.filter(course_id=course_id, is_active=True))
    prog = {p.module_id: p for p in ModuleProgress.objects.filter(employee_id=employee_id, module__in=mods)}
    done = sum(1 for md in mods if prog.get(md.id) and prog[md.id].status == 'completed')
    quizzes = [md for md in mods if md.content_type == 'quiz']
    quiz_ok = all(prog.get(q.id) and prog[q.id].passed for q in quizzes)
    scores = [prog[q.id].score for q in quizzes if prog.get(q.id) and prog[q.id].score is not None]
    return {'modules': len(mods), 'completed': done, 'percent': int(done * 100 / len(mods)) if mods else 0,
            'quiz_ok': quiz_ok, 'score': int(round(sum(scores) / len(scores))) if scores else None,
            'minutes': sum(md.duration_minutes for md in mods), 'minutes_done': sum(md.duration_minutes for md in mods if prog.get(md.id) and prog[md.id].status == 'completed'),
            'items': [{'id': md.id, 'title': md.title, 'order': md.order, 'content_type': md.content_type, 'url': md.url,
                       'file': md.file.url if md.file else None, 'duration_minutes': md.duration_minutes,
                       'status': prog[md.id].status if md.id in prog else 'not_started',
                       'progress': prog[md.id].progress if md.id in prog else 0,
                       'score': prog[md.id].score if md.id in prog else None,
                       'passed': prog[md.id].passed if md.id in prog else None} for md in mods]}


def _module_course(module):
    return _lm().Course.objects.get(pk=module.course_id)


def open_module(module, employee):
    course = _module_course(module)
    enrol(course, employee)
    p, _ = ModuleProgress.objects.get_or_create(employee_id=employee.id, module=module)
    if p.status == 'not_started':
        p.status, p.started_at, p.progress = 'in_progress', timezone.now(), max(p.progress, 1)
        p.save()
    OnlineEnrolment.objects.filter(course_id=course.id, employee_id=employee.id, status='enrolled').update(status='in_progress')
    return p


def set_progress(module, employee, progress):
    p = open_module(module, employee)
    progress = max(0, min(int(progress), 100))
    if p.status != 'completed':
        p.progress = max(p.progress, progress)
        p.save()
        if progress >= 100 and module.content_type != 'quiz':
            return complete_module(module, employee)
    return {'progress': p, 'course': course_progress(employee.id, module.course_id)}


@transaction.atomic
def complete_module(module, employee, score=None):
    course = _module_course(module)
    p = open_module(module, employee)
    if module.content_type == 'quiz':
        if score in (None, ''):
            raise ValidationError('Enter the quiz score.')
        score = int(score)
        if not 0 <= score <= 100:
            raise ValidationError('Score must be 0-100.')
        mark = module.pass_mark if module.pass_mark is not None else course.pass_mark
        p.score, p.passed = score, score >= mark
        if p.passed:
            p.status, p.progress, p.completed_at = 'completed', 100, timezone.now()
        else:
            p.progress = max(p.progress, 50)
    else:
        p.status, p.progress, p.completed_at, p.passed = 'completed', 100, timezone.now(), True
    p.save()
    done = maybe_complete_course(course, employee)
    return {'progress': p, 'course': course_progress(employee.id, course.id), 'course_completed': done}


def maybe_complete_course(course, employee):
    m = _lm()
    en = enrol(course, employee)
    if en.status == 'completed':
        return False
    pr = course_progress(employee.id, course.id)
    if not pr['modules'] or pr['percent'] < 100 or not pr['quiz_ok']:
        if en.status == 'enrolled':
            en.status = 'in_progress'
            en.save(update_fields=['status'])
        return False
    en.status, en.completed_at, en.score = 'completed', timezone.now(), pr['score']
    nom = (m.Nomination.objects.filter(employee=employee, session__course=course, seat_status='confirmed')
           .exclude(session__status__in=('completed', 'cancelled')).select_related('session').order_by('session__start_date').first())
    if nom:
        # part of a scheduled (blended / online) session: the result feeds the normal close -> certificate flow
        res, _ = m.ParticipantResult.objects.get_or_create(nomination=nom)
        res.attended = True
        if pr['score'] is not None:
            res.post_test_score = pr['score']
        res.evaluate()
        res.save()
        en.nomination_id = nom.id
    else:
        # self-paced: completion record + certificate + skills now
        today = timezone.localdate()
        expiry = today + relativedelta(months=course.certificate_validity_months) if course.certificate_validity_months else None
        cert = m.Certificate.objects.create(
            employee=employee, course=course, title=course.title, issued_on=today, expiry_date=expiry,
            issued_by=course.provider or 'Internal (e-learning)', certificate_number=f"OL-{course.code}-{employee.emp_code}-{today:%y%m%d}")
        en.certificate_id = cert.id
        m.TrainingNeed.objects.filter(employee=employee, course=course).exclude(status__in=('completed', 'cancelled')).update(status='completed')
        skills_from_course(employee.id, course, source='online', ref=course.code)
        notify(employee=employee, title='Course completed', notification_type='learning',
               message=f"Well done - you completed {course.title}. Your certificate {cert.certificate_number} is in My Learning.")
    en.save()
    return True


# ------------------------------------------------------------------ cost & budget
def session_cost(session, confirmed=None):
    m = _lm()
    if confirmed is None:
        confirmed = list(m.Nomination.objects.filter(session=session, seat_status='confirmed').select_related('employee'))
    per_head = Decimal(session.effective_cost or 0)
    lines = SessionCost.objects.filter(session_id=session.id).aggregate(t=Sum('amount'))['t'] or Z
    x = SessionExtra.objects.filter(session_id=session.id).first()
    other = (x.other_costs if x else Z) or Z
    shared = lines + other
    share = (shared / len(confirmed)) if confirmed else Z
    return {'per_head': per_head, 'participants': len(confirmed), 'per_head_total': per_head * len(confirmed),
            'lines': lines, 'other': other, 'shared': shared, 'share_per_participant': share,
            'total': per_head * len(confirmed) + shared, 'confirmed': confirmed}


def budget_vs_actual(year, department_ids=None):
    m = _lm()
    from OrganisationManager.models import dept_master
    names = dict(dept_master.objects.values_list('id', 'dept_name'))
    actual = defaultdict(lambda: {'per_head': Z, 'session_costs': Z, 'online': Z, 'participants': 0})
    for s in m.TrainingSession.objects.filter(start_date__year=year).exclude(status='cancelled').select_related('course'):
        c = session_cost(s)
        if not c['confirmed']:
            if c['shared']:
                actual[None]['session_costs'] += c['shared']
            continue
        for nom in c['confirmed']:
            d = nom.employee.emp_dept_id_id
            actual[d]['per_head'] += c['per_head']
            actual[d]['session_costs'] += c['share_per_participant']
            actual[d]['participants'] += 1
    courses = {}
    for en in OnlineEnrolment.objects.filter(status='completed', completed_at__year=year, nomination_id__isnull=True):
        if en.course_id not in courses:
            courses[en.course_id] = m.Course.objects.filter(pk=en.course_id).first()
        crs = courses[en.course_id]
        emp = _emp_model().objects.filter(pk=en.employee_id).only('emp_dept_id').first()
        if crs and emp:
            actual[emp.emp_dept_id_id]['online'] += Decimal(crs.cost_per_head or 0)
            actual[emp.emp_dept_id_id]['participants'] += 1
    budgets = {b.department_id: b.amount for b in TrainingBudget.objects.filter(year=year)}
    keys = (set(actual) | set(budgets)) - {None}
    if department_ids:
        keys &= {int(d) for d in department_ids}
    rows = []
    for d in sorted(keys, key=lambda k: names.get(k, '')):
        a = actual.get(d) or {'per_head': Z, 'session_costs': Z, 'online': Z, 'participants': 0}
        total = a['per_head'] + a['session_costs'] + a['online']
        b = budgets.get(d)
        rows.append({'department_id': d, 'department': names.get(d, f'#{d}'), 'budget': money(b) if b is not None else None,
                     'actual': money(total), 'per_head': money(a['per_head']), 'session_costs': money(a['session_costs']),
                     'online': money(a['online']), 'participants': a['participants'],
                     'variance': money(b - total) if b is not None else None,
                     'utilisation': round(float(total * 100 / b), 1) if b else None})
    unalloc = actual.get(None)
    total_actual = sum(Decimal(str(r['actual'])) for r in rows) + ((unalloc['session_costs'] + unalloc['per_head'] + unalloc['online']) if unalloc and not department_ids else Z)
    company_budget = budgets.get(None)
    dept_budget = sum((v for k, v in budgets.items() if k is not None), Z)
    budget_total = company_budget if company_budget is not None else dept_budget
    participants = sum(r['participants'] for r in rows)
    return {'year': int(year), 'rows': rows,
            'unallocated': money(unalloc['session_costs']) if unalloc and not department_ids else 0,
            'company_budget': money(company_budget) if company_budget is not None else None,
            'department_budgets': money(dept_budget), 'budget_total': money(budget_total), 'actual_total': money(total_actual),
            'variance_total': money(budget_total - total_actual),
            'utilisation': round(float(total_actual * 100 / budget_total), 1) if budget_total else None,
            'participants': participants, 'cost_per_participant': money(total_actual / participants) if participants else None}


def employee_costs(employee, year=None):
    m = _lm()
    noms = m.Nomination.objects.filter(employee=employee, seat_status='confirmed').exclude(session__status='cancelled').select_related('session__course')
    if year:
        noms = noms.filter(session__start_date__year=year)
    lines, total = [], Z
    for nom in noms:
        c = session_cost(nom.session)
        amt = c['per_head'] + c['share_per_participant']
        total += amt
        lines.append({'nomination': nom.id, 'session': nom.session.code, 'course': nom.session.course.title, 'date': nom.session.start_date,
                      'per_head': money(c['per_head']), 'shared': money(c['share_per_participant']), 'amount': money(amt)})
    ens = OnlineEnrolment.objects.filter(employee_id=employee.id, status='completed', nomination_id__isnull=True)
    if year:
        ens = ens.filter(completed_at__year=year)
    for en in ens:
        crs = m.Course.objects.filter(pk=en.course_id).first()
        amt = Decimal(crs.cost_per_head or 0) if crs else Z
        total += amt
        lines.append({'enrolment': en.id, 'course': crs.title if crs else '', 'date': en.completed_at.date() if en.completed_at else None,
                      'per_head': money(amt), 'shared': 0, 'amount': money(amt)})
    return {'employee_id': employee.id, 'employee': emp_name(employee), 'year': year, 'lines': lines, 'total': money(total)}


# ------------------------------------------------------------------ training history
def history(employee):
    m = _lm()
    items = []
    tot = defaultdict(float)
    noms = (m.Nomination.objects.filter(employee=employee).select_related('session__course').order_by('-session__start_date'))
    att = defaultdict(list)
    for a in SessionAttendance.objects.filter(nomination_id__in=[n.id for n in noms]):
        att[a.nomination_id].append(a)
    results = {r.nomination_id: r for r in m.ParticipantResult.objects.filter(nomination__in=noms)}
    certs = list(m.Certificate.objects.filter(employee=employee).select_related('course', 'session'))
    cert_by_session = {c.session_id: c for c in certs if c.session_id}
    for n in noms:
        s, crs = n.session, n.session.course
        r = results.get(n.id)
        hours = 0.0
        if n.seat_status == 'confirmed':
            rows = att.get(n.id)
            if rows and any(a.hours is not None for a in rows):
                hours = float(sum((a.hours or Z) for a in rows if a.present))
            elif r and r.attended and s.status == 'completed':
                hours = float(crs.duration_hours or 0) * (r.attendance_percent or 0) / 100
        cost = 0.0
        if n.seat_status == 'confirmed' and s.status != 'cancelled':
            c = session_cost(s)
            cost = money(c['per_head'] + c['share_per_participant'])
        cert = cert_by_session.get(s.id)
        items.append({'type': 'session', 'date': s.start_date, 'end_date': s.end_date, 'course_id': crs.id, 'course': crs.title,
                      'course_code': crs.code, 'session': s.code, 'session_status': s.status, 'nomination': n.id,
                      'seat_status': n.seat_status, 'manager_status': n.manager_status, 'ld_status': n.ld_status,
                      'attended': r.attended if r else None, 'attendance_percent': r.attendance_percent if r else None,
                      'pre_test_score': r.pre_test_score if r else None, 'post_test_score': r.post_test_score if r else None,
                      'passed': r.passed if r else None, 'feedback_rating': r.feedback_rating if r else None,
                      'hours': round(hours, 1), 'cost': cost,
                      'certificate': {'id': cert.id, 'number': cert.certificate_number, 'status': cert.status,
                                      'expiry_date': cert.expiry_date} if cert else None})
        tot['sessions'] += 1
        tot['hours'] += hours
        tot['cost'] += cost
        if s.status == 'completed' and n.seat_status == 'confirmed':
            tot['completed'] += 1
            if r and r.passed:
                tot['passed'] += 1
    for en in OnlineEnrolment.objects.filter(employee_id=employee.id):
        crs = m.Course.objects.filter(pk=en.course_id).first()
        if not crs:
            continue
        pr = course_progress(employee.id, crs.id)
        hours = pr['minutes_done'] / 60.0
        cost = money(crs.cost_per_head) if (en.status == 'completed' and not en.nomination_id) else 0.0
        items.append({'type': 'online', 'date': (en.completed_at or en.enrolled_at).date(), 'course_id': crs.id, 'course': crs.title,
                      'course_code': crs.code, 'status': en.status, 'progress': pr['percent'], 'score': en.score,
                      'passed': True if en.status == 'completed' else None, 'hours': round(hours, 1), 'cost': cost,
                      'certificate_id': en.certificate_id})
        tot['online'] += 1
        tot['online_completed'] += 1 if en.status == 'completed' else 0
        tot['hours'] += hours
        tot['cost'] += cost
    cert_rows = []
    for c in certs:
        st = c.status
        tot['certificates_' + st] += 1
        cert_rows.append({'id': c.id, 'title': c.title, 'number': c.certificate_number, 'issued_on': c.issued_on,
                          'expiry_date': c.expiry_date, 'status': st, 'issued_by': c.issued_by,
                          'file': c.file.url if c.file else None})
    items.sort(key=lambda i: i['date'] or date.min, reverse=True)
    skills = [{'skill': s.skill.name, 'level': s.level, 'source': s.source, 'date': s.date}
              for s in EmployeeSkill.objects.filter(employee_id=employee.id).select_related('skill')]
    totals = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in tot.items()}
    for k in ('sessions', 'completed', 'passed', 'online', 'online_completed', 'certificates_valid', 'certificates_expiring',
              'certificates_expired', 'certificates_renewed'):
        totals[k] = int(totals.get(k, 0))
    totals['hours'] = round(tot['hours'], 1)
    totals['cost'] = round(tot['cost'], 2)
    return {'employee': {'id': employee.id, 'code': employee.emp_code, 'name': emp_name(employee),
                         'department': employee.emp_dept_id.dept_name if employee.emp_dept_id else None,
                         'designation': employee.emp_desgntn_id.desgntn_job_title if employee.emp_desgntn_id else None},
            'items': items, 'certificates': cert_rows, 'skills': skills, 'totals': totals}


# ------------------------------------------------------------------ alerts
def _send_once(kind, ref_id, key, recipients, title, message):
    """recipients: list of ('user', user) / ('employee', emp). Returns how many were notified (0 when already sent)."""
    log_row, created = AlertLog.objects.get_or_create(kind=kind, ref_id=ref_id, key=key)
    if not created:
        return 0
    seen, n = set(), 0
    for kind_, obj in recipients:
        if obj is None:
            continue
        uid = obj.id if kind_ == 'user' else (obj.users_id or f"e{obj.id}")
        if uid in seen:
            continue
        seen.add(uid)
        if kind_ == 'user':
            notify(user=obj, title=title, message=message, notification_type='learning')
        else:
            notify(employee=obj, title=title, message=message, notification_type='learning')
        n += 1
    log_row.recipients = n
    log_row.save(update_fields=['recipients'])
    return n


def expiry_bucket(days):
    if days < 0:
        return 'expired'
    if days <= 7:
        return '7'
    if days <= 30:
        return '30'
    if days <= 60:
        return '60'
    return None


def certificate_expiry_alerts(today=None, run_scan=True):
    """Daily: certificates expiring within 60 / 30 / 7 days or just expired -> employee + manager + HR, once per threshold."""
    from LearningManagement.services import expiry_scan, ld_users
    m = _lm()
    today = today or timezone.localdate()
    out = {'alerts': 0, 'notified': 0, 'needs_created': 0}
    certs = (m.Certificate.objects.filter(expiry_date__isnull=False, expiry_date__lte=today + timedelta(days=60),
                                         expiry_date__gte=today - timedelta(days=30))
             .select_related('employee', 'employee__emp_reporting_manager', 'course'))
    for c in certs:
        if c.superseded:
            continue
        days = (c.expiry_date - today).days
        key = expiry_bucket(days)
        if key is None:
            continue
        emp = c.employee
        rec = [('employee', emp), ('user', emp.emp_reporting_manager)] + [('user', u) for u in ld_users(emp)]
        if key == 'expired':
            title, msg = 'Certificate expired', f"{c.title} ({c.certificate_number or c.id}) of {emp_name(emp)} expired on {c.expiry_date:%d/%m/%Y}."
        else:
            title, msg = 'Certificate expiring', f"{c.title} ({c.certificate_number or c.id}) of {emp_name(emp)} expires on {c.expiry_date:%d/%m/%Y} ({days} days)."
        n = _send_once('cert_expiry', c.id, key, rec, title, msg)
        if n:
            out['alerts'] += 1
            out['notified'] += n
    if run_scan:
        out['needs_created'] = expiry_scan(60)
    return out


def escalate_nominations(days=3, now=None):
    """Nominations waiting longer than `days` for the manager (-> reminder + L&D) or for L&D (-> L&D reminder). Once each."""
    from LearningManagement.services import ld_users
    m = _lm()
    now = now or timezone.now()
    cutoff = now - timedelta(days=days)
    qs = (m.Nomination.objects.filter(created_at__lte=cutoff, session__status__in=('draft', 'published'),
                                      session__start_date__gte=timezone.localdate(now))
          .exclude(seat_status='cancelled').filter(Q(manager_status='pending') | Q(manager_status='approved', ld_status='pending'))
          .select_related('employee', 'employee__emp_reporting_manager', 'session__course'))
    sent = 0
    for nom in qs:
        label = f"{emp_name(nom.employee)} - {nom.session.course.title} ({nom.session.code})"
        if nom.manager_status == 'pending':
            rec = [('user', nom.employee.emp_reporting_manager)] + [('user', u) for u in ld_users(nom.employee)]
            n = _send_once('nom_escalation', nom.id, 'manager', rec, 'Nomination waiting for manager',
                           f"{label} has waited more than {days} days for the manager's approval.")
        else:
            rec = [('user', u) for u in ld_users(nom.employee)]
            n = _send_once('nom_escalation', nom.id, 'ld', rec, 'Nomination waiting for L&D',
                           f"{label} has waited more than {days} days for L&D approval.")
        sent += 1 if n else 0
    return sent


# ------------------------------------------------------------------ certificate document
def certificate_html(cert):
    from django.utils.html import escape
    emp = cert.employee
    company = ''
    try:
        from django.db import connection
        company = getattr(connection.tenant, 'name', '') or ''
    except Exception:
        pass
    exp = f"<p class='m'>Valid until {cert.expiry_date:%d %B %Y}</p>" if cert.expiry_date else ''
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Certificate {escape(cert.certificate_number or cert.id)}</title>
<style>@page{{size:A4 landscape;margin:12mm}}body{{font-family:Georgia,serif;margin:0;color:#1f1d3a}}
.c{{border:6px double #5b4fe0;padding:48px 60px;text-align:center;min-height:150mm;box-sizing:border-box}}
h1{{font-size:40px;letter-spacing:4px;color:#5b4fe0;margin:8px 0 4px}}.s{{font-size:15px;color:#666;letter-spacing:2px;text-transform:uppercase}}
.n{{font-size:32px;margin:26px 0 6px;border-bottom:1px solid #ccc;display:inline-block;padding:0 30px 6px}}
.t{{font-size:24px;font-style:italic;margin:6px 0 20px}}.m{{font-size:14px;color:#444;margin:4px 0}}
.f{{display:flex;justify-content:space-between;margin-top:46px;font-size:13px;color:#555}}</style></head>
<body><div class="c"><div class="s">{escape(company)}</div><h1>CERTIFICATE</h1><div class="s">of completion</div>
<p class="m" style="margin-top:28px">This is to certify that</p><div class="n">{escape(emp_name(emp))}</div>
<p class="m">has successfully completed</p><div class="t">{escape(cert.title)}</div>
<p class="m">Issued on {cert.issued_on:%d %B %Y} by {escape(cert.issued_by or 'Internal')}</p>{exp}
<div class="f"><span>Certificate no. {escape(cert.certificate_number or str(cert.id))}</span><span>Learning &amp; Development</span></div>
</div></body></html>"""


def certificate_pdf(cert):
    from io import BytesIO
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.pdfgen import canvas
    buf = BytesIO()
    w, h = landscape(A4)
    c = canvas.Canvas(buf, pagesize=(w, h))
    c.setTitle(f"Certificate {cert.certificate_number or cert.id}")
    purple = HexColor('#5b4fe0')
    c.setStrokeColor(purple)
    c.setLineWidth(5)
    c.rect(28, 28, w - 56, h - 56)
    c.setLineWidth(1.2)
    c.rect(38, 38, w - 76, h - 76)
    company = ''
    try:
        from django.db import connection
        company = getattr(connection.tenant, 'name', '') or ''
    except Exception:
        pass
    c.setFillColor(HexColor('#666666'))
    c.setFont('Helvetica', 12)
    c.drawCentredString(w / 2, h - 90, company.upper())
    c.setFillColor(purple)
    c.setFont('Times-Bold', 42)
    c.drawCentredString(w / 2, h - 145, 'CERTIFICATE')
    c.setFont('Helvetica', 13)
    c.setFillColor(HexColor('#666666'))
    c.drawCentredString(w / 2, h - 168, 'OF COMPLETION')
    c.setFillColor(HexColor('#1f1d3a'))
    c.setFont('Times-Roman', 15)
    c.drawCentredString(w / 2, h - 215, 'This is to certify that')
    c.setFont('Times-Bold', 30)
    c.drawCentredString(w / 2, h - 258, emp_name(cert.employee))
    c.setStrokeColor(HexColor('#cccccc'))
    c.line(w / 2 - 200, h - 268, w / 2 + 200, h - 268)
    c.setFont('Times-Roman', 15)
    c.drawCentredString(w / 2, h - 298, 'has successfully completed')
    c.setFont('Times-Italic', 24)
    c.drawCentredString(w / 2, h - 335, cert.title[:80])
    c.setFont('Helvetica', 12)
    c.drawCentredString(w / 2, h - 372, f"Issued on {cert.issued_on:%d %B %Y} by {cert.issued_by or 'Internal'}")
    if cert.expiry_date:
        c.drawCentredString(w / 2, h - 390, f"Valid until {cert.expiry_date:%d %B %Y}")
    c.setFont('Helvetica', 10)
    c.setFillColor(HexColor('#555555'))
    c.drawString(70, 70, f"Certificate no. {cert.certificate_number or cert.id}")
    c.drawRightString(w - 70, 70, 'Learning & Development')
    c.showPage()
    c.save()
    return buf.getvalue()
