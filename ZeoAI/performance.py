"""AI Performance & Development – review summaries, skill gaps, course recommendations, appraisal templates/objectives,
training completion and certificate expiry, development plan drafts. Rule-based on ZEO's own data; wording optionally polished by
an external model when the company configured one."""
from collections import Counter, defaultdict
from datetime import date, timedelta

from django.apps import apps
from django.db.models import Avg, Count, Q

M = apps.get_model


def _emp(e):
    return {'id': e.id, 'code': e.emp_code, 'name': f'{e.emp_first_name} {e.emp_last_name or ""}'.strip(),
            'department': getattr(e.emp_dept_id, 'dept_name', '') if e.emp_dept_id_id else '',
            'designation': getattr(e.emp_desgntn_id, 'desgntn_job_title', '') if e.emp_desgntn_id_id else ''}


# ---------------------------------------------------------------- reviews
def review_summary(employee_id, cycles=3):
    """Plain-language summary of the last goal sheets + any 360° review: scores, strongest/weakest goals, recurring manager themes."""
    GoalSheet = M('PerformanceManagement', 'GoalSheet')
    sheets = list(GoalSheet.objects.filter(employee_id=employee_id).select_related('cycle').order_by('-cycle__period_to')[:cycles])
    emp = M('EmpManagement', 'emp_master').objects.select_related('emp_dept_id', 'emp_desgntn_id').get(id=employee_id)
    out = {'employee': _emp(emp), 'sheets': [], 'themes': [], 'trend': '', 'text': ''}
    words = Counter()
    for s in sheets:
        goals = list(s.goals.all())
        rated = [g for g in goals if g.manager_rating]
        best = max(rated, key=lambda g: g.manager_rating, default=None)
        worst = min(rated, key=lambda g: g.manager_rating, default=None)
        out['sheets'].append({'cycle': s.cycle.name, 'period': f'{s.cycle.period_from} – {s.cycle.period_to}', 'status': s.status, 'self_score': s.self_score, 'manager_score': s.manager_score,
                              'final_rating': s.final_rating, 'goals': len(goals), 'best': best.title if best else '', 'best_rating': best.manager_rating if best else None,
                              'weakest': worst.title if worst else '', 'weakest_rating': worst.manager_rating if worst else None,
                              'achievements': s.key_achievements or '', 'development_needs': s.development_needs or '', 'manager_comments': s.manager_comments or '',
                              'disagrees': bool(s.employee_disagrees)})
        for txt in (s.development_needs, s.manager_comments, *[g.manager_comment for g in goals]):
            for w in _keywords(txt):
                words[w] += 1
    out['themes'] = [w for w, n in words.most_common(8) if n > 1] or [w for w, _ in words.most_common(5)]
    scores = [float(s.manager_score) for s in reversed(sheets) if s.manager_score is not None]
    if len(scores) >= 2:
        d = scores[-1] - scores[0]
        out['trend'] = 'improving' if d > 0.2 else 'declining' if d < -0.2 else 'steady'
    # 360 from ZeoPeople when present
    try:
        R = M('ZeoPeople', 'Review360')
        r = R.objects.filter(employee_id=employee_id).exclude(summary=None).order_by('-id').first()
        if r:
            out['review360'] = {'name': getattr(r, 'name', '') or f'360° #{r.id}', 'overall': r.overall, 'summary': r.summary}
    except Exception:
        pass
    out['text'] = _review_text(out)
    return out


def _review_text(o):
    e = o['employee']
    if not o['sheets']:
        return f'{e["name"]} has no completed appraisal yet.'
    s = o['sheets'][0]
    bits = [f'{e["name"]} ({e["designation"] or "—"}, {e["department"] or "—"}).']
    bits.append(f'Latest cycle {s["cycle"]}: manager score {s["manager_score"] if s["manager_score"] is not None else "not yet"}' + (f' (self {s["self_score"]})' if s['self_score'] is not None else '') + (f', final rating {s["final_rating"]}' if s['final_rating'] else '') + '.')
    if s['best']:
        bits.append(f'Strongest goal: "{s["best"]}" ({s["best_rating"]}/5); needs most work: "{s["weakest"]}" ({s["weakest_rating"]}/5).')
    if o['trend']:
        bits.append(f'Over the last {len(o["sheets"])} cycles the manager score is {o["trend"]}.')
    if s['development_needs']:
        bits.append('Manager noted development needs: ' + s['development_needs'].strip()[:200] + ('…' if len(s['development_needs']) > 200 else ''))
    if o['themes']:
        bits.append('Recurring themes in feedback: ' + ', '.join(o['themes']) + '.')
    if s['disagrees']:
        bits.append('The employee disagreed with the last review – discuss before setting new objectives.')
    if o.get('review360'):
        bits.append(f'360° feedback overall {o["review360"]["overall"]}.')
    return _polish(' '.join(bits), 'Summarise this performance review in 4–5 clear sentences for the manager. Keep every number; do not invent anything.')


STOP = set('and or the a an of to in for with on at by from as is are be this that we he she his her they their it its not no has have had was were will can more very good needs need to should could'.split())


def _keywords(txt):
    import re
    return [w for w in re.findall(r'[a-z]{4,}', (txt or '').lower()) if w not in STOP]


# ---------------------------------------------------------------- skill gaps & courses
def skill_gaps(employee_id):
    """Required level (RoleSkill for the designation) vs the employee's recorded level; courses that close each gap (CourseSkill)."""
    e = M('EmpManagement', 'emp_master').objects.select_related('emp_dept_id', 'emp_desgntn_id').get(id=employee_id)
    RoleSkill, EmployeeSkill, CourseSkill = M('LearningPlus', 'RoleSkill'), M('LearningPlus', 'EmployeeSkill'), M('LearningPlus', 'CourseSkill')
    levels = {1: 'Awareness', 2: 'Basic', 3: 'Intermediate', 4: 'Advanced', 5: 'Expert'}
    try:
        for sl in M('LearningPlus', 'SkillLevel').objects.all():
            levels[sl.level] = sl.name
    except Exception:
        pass
    have = {s.skill_id: s.level for s in EmployeeSkill.objects.filter(employee_id=employee_id)}
    req = list(RoleSkill.objects.filter(designation_id=e.emp_desgntn_id_id).select_related('skill')) if e.emp_desgntn_id_id else []
    Course = M('LearningManagement', 'Course')
    gaps, met = [], []
    for r in req:
        lvl = have.get(r.skill_id, 0)
        row = {'skill': r.skill.name, 'skill_id': r.skill_id, 'category': getattr(r.skill, 'category', ''), 'required': r.required_level, 'required_label': levels.get(r.required_level, ''),
               'current': lvl, 'current_label': levels.get(lvl, 'Not recorded'), 'gap': max(0, r.required_level - lvl)}
        if row['gap']:
            cs = CourseSkill.objects.filter(skill_id=r.skill_id, level_gained__gt=lvl).order_by('-level_gained')[:3]
            ids = [c.course_id for c in cs]
            courses = {c.id: c for c in Course.objects.filter(id__in=ids)}
            row['courses'] = [{'id': c.course_id, 'title': courses[c.course_id].title if c.course_id in courses else f'Course {c.course_id}', 'level_gained': c.level_gained,
                               'hours': float(courses[c.course_id].duration_hours or 0) if c.course_id in courses else None, 'cost': float(courses[c.course_id].cost_per_head or 0) if c.course_id in courses else None} for c in cs]
            gaps.append(row)
        else:
            met.append(row)
    gaps.sort(key=lambda r: -r['gap'])
    # extra skills the employee has beyond the role
    extra = [{'skill': s.skill.name, 'level': s.level} for s in EmployeeSkill.objects.filter(employee_id=employee_id).exclude(skill_id__in=[r.skill_id for r in req]).select_related('skill')]
    readiness = round(100 * sum(min(have.get(r.skill_id, 0), r.required_level) for r in req) / max(1, sum(r.required_level for r in req))) if req else None
    return {'employee': _emp(e), 'gaps': gaps, 'met': met, 'extra': extra, 'readiness': readiness, 'no_profile': not req,
            'text': (f'{e.emp_first_name} meets {len(met)} of {len(req)} skill requirements of the role ({readiness}% ready). ' + ('Biggest gaps: ' + ', '.join(f'{g["skill"]} ({g["current"]}→{g["required"]})' for g in gaps[:4]) + '.' if gaps else 'No gaps.')) if req else 'No skill requirements are defined for this designation yet – add them under Learning › Skills › Role skills.'}


def recommend_courses(employee_id, limit=6):
    """Courses to recommend: close skill gaps first, then certificates expiring, then training needs already raised."""
    g = skill_gaps(employee_id)
    seen, recs = set(), []
    for row in g['gaps']:
        for c in row.get('courses', []):
            if c['id'] in seen:
                continue
            seen.add(c['id'])
            recs.append({**c, 'reason': f'Closes skill gap {row["skill"]} ({row["current"]}→{row["required"]})', 'priority': 'high' if row['gap'] >= 2 else 'medium'})
    for c in certificates(employee_id=employee_id)['expiring']:
        if c['course_id'] and c['course_id'] not in seen:
            seen.add(c['course_id'])
            recs.append({'id': c['course_id'], 'title': c['course'], 'reason': f'Certificate "{c["title"]}" expires {c["expiry_date"]} ({c["days"]} days)', 'priority': 'high' if c['days'] <= 30 else 'medium'})
    TN = M('LearningManagement', 'TrainingNeed')
    for n in TN.objects.filter(employee_id=employee_id).exclude(status__in=('closed', 'completed', 'cancelled')).select_related('course')[:5]:
        if n.course_id and n.course_id not in seen:
            seen.add(n.course_id)
            recs.append({'id': n.course_id, 'title': n.course.title, 'reason': f'Training need raised ({n.source or "manual"}): {n.need or ""}'.strip(), 'priority': 'low'})
    return {'employee': g['employee'], 'recommendations': recs[:limit]}


# ---------------------------------------------------------------- appraisal template / objectives
def objectives(employee_id, cycle_id=None, n=5):
    """Suggested objectives for the next appraisal: KPIs of the designation/department, weakest goals last cycle, skill gaps, 360 development needs."""
    e = M('EmpManagement', 'emp_master').objects.select_related('emp_dept_id', 'emp_desgntn_id').get(id=employee_id)
    KPI, GoalSheet = M('PerformanceManagement', 'KPI'), M('PerformanceManagement', 'GoalSheet')
    out = []
    kpis = KPI.objects.filter(is_active=True).filter(Q(designation_id=e.emp_desgntn_id_id) | Q(department_id=e.emp_dept_id_id) | Q(designation=None, department=None)).order_by('-default_weight')
    for k in kpis[:n]:
        out.append({'title': k.name, 'kpi_id': k.id, 'weight': float(k.default_weight or 0), 'measure': k.measure or '', 'source': 'KPI of the role'})
    last = GoalSheet.objects.filter(employee_id=employee_id).order_by('-cycle__period_to').first()
    if last:
        for g in last.goals.filter(manager_rating__lte=2).order_by('manager_rating')[:2]:
            out.append({'title': f'Improve: {g.title}', 'kpi_id': g.kpi_id, 'weight': float(g.weight or 0), 'measure': g.target or '', 'source': f'Rated {g.manager_rating}/5 in {last.cycle.name}'})
        if last.development_needs:
            out.append({'title': f'Development: {last.development_needs.strip()[:80]}', 'kpi_id': None, 'weight': 10, 'measure': 'Completed by cycle end', 'source': 'Manager development needs'})
    for row in skill_gaps(employee_id)['gaps'][:2]:
        out.append({'title': f'Reach {row["required_label"]} level in {row["skill"]}', 'kpi_id': None, 'weight': 10, 'measure': f'Skill level {row["required"]} assessed', 'source': 'Skill gap vs role'})
    # normalise weights to 100
    tot = sum(o['weight'] for o in out) or 1
    for o in out:
        o['weight'] = round(100 * o['weight'] / tot)
    diff = 100 - sum(o['weight'] for o in out)
    if out:
        out[0]['weight'] += diff
    return {'employee': _emp(e), 'objectives': out}


def appraisal_template(designation_id=None, department_id=None):
    """A template the HR team can edit: sections, KPI list with weights, competency list, rating scale."""
    KPI = M('PerformanceManagement', 'KPI')
    kp = KPI.objects.filter(is_active=True)
    if designation_id:
        kp = kp.filter(Q(designation_id=designation_id) | Q(designation=None))
    if department_id:
        kp = kp.filter(Q(department_id=department_id) | Q(department=None))
    kpis = [{'code': k.code, 'name': k.name, 'type': k.kpi_type, 'measure': k.measure or '', 'weight': float(k.default_weight or 0)} for k in kp.order_by('-default_weight')[:8]]
    comps = []
    try:
        comps = [{'name': c.name, 'levels': c.levels} for c in M('ZeoPeople', 'Competency').objects.all()[:8]]
    except Exception:
        pass
    if not comps:
        comps = [{'name': n} for n in ('Communication', 'Teamwork', 'Quality of work', 'Reliability & attendance', 'Initiative', 'Customer focus', 'Safety awareness')]
    return {'sections': [{'name': 'Goals / KPIs', 'weight': 70, 'items': kpis}, {'name': 'Competencies', 'weight': 30, 'items': comps},
                         {'name': 'Self assessment', 'fields': ['Key achievements', 'Challenges faced', 'Support needed']},
                         {'name': 'Manager review', 'fields': ['Overall comments', 'Development needs', 'Proposed rating', 'Recommended training']}],
            'rating_scale': [{'value': 5, 'label': 'Outstanding'}, {'value': 4, 'label': 'Exceeds expectations'}, {'value': 3, 'label': 'Meets expectations'}, {'value': 2, 'label': 'Needs improvement'}, {'value': 1, 'label': 'Unsatisfactory'}],
            'guidance': 'Weights of goals should add up to 100. Rate each goal on evidence (numbers, dates, examples) not impression. Discuss development needs before the rating.'}


# ---------------------------------------------------------------- training & certificates
def training_status(department_id=None, branch_id=None, days=90):
    """Completion of nominated training (attended/passed) and overdue training needs."""
    Nom, PR, TN = M('LearningManagement', 'Nomination'), M('LearningManagement', 'ParticipantResult'), M('LearningManagement', 'TrainingNeed')
    since = date.today() - timedelta(days=days)
    noms = Nom.objects.filter(session__start_date__gte=since).select_related('employee', 'session__course')
    if department_id:
        noms = noms.filter(employee__emp_dept_id=department_id)
    if branch_id:
        noms = noms.filter(employee__emp_branch_id=branch_id)
    res = {r.nomination_id: r for r in PR.objects.filter(nomination__in=noms)}
    rows = []
    for n in noms:
        r = res.get(n.id)
        rows.append({'employee': f'{n.employee.emp_first_name} {n.employee.emp_last_name or ""}'.strip(), 'course': n.session.course.title, 'date': n.session.start_date,
                     'status': 'passed' if r and r.passed else 'attended' if r and r.attended else 'failed' if r and r.attended is False else 'scheduled' if n.session.start_date >= date.today() else 'no result'})
    by = Counter(r['status'] for r in rows)
    needs = TN.objects.exclude(status__in=('closed', 'completed', 'cancelled')).filter(target_date__lt=date.today()).select_related('employee', 'course')
    overdue = [{'employee': f'{n.employee.emp_first_name} {n.employee.emp_last_name or ""}'.strip(), 'course': n.course.title if n.course_id else n.need, 'target_date': n.target_date, 'priority': n.priority} for n in needs[:50]]
    return {'rows': rows, 'counts': dict(by), 'completion_pct': round(100 * (by['passed'] + by['attended']) / max(1, len([r for r in rows if r['status'] != 'scheduled']))), 'overdue_needs': overdue}


def certificates(employee_id=None, within=90):
    """Certificates (Learning) and employee documents expiring within N days, or already expired."""
    Cert, Doc = M('LearningManagement', 'Certificate'), M('EmpManagement', 'Emp_Documents')
    lim = date.today() + timedelta(days=within)
    cq = Cert.objects.filter(expiry_date__lte=lim).select_related('employee', 'course')
    dq = Doc.objects.filter(emp_doc_expiry_date__lte=lim).select_related('emp_id', 'document_type')
    if employee_id:
        cq, dq = cq.filter(employee_id=employee_id), dq.filter(emp_id=employee_id)
    exp, done = [], []
    for c in cq.order_by('expiry_date')[:200]:
        d = (c.expiry_date - date.today()).days
        row = {'kind': 'certificate', 'employee': f'{c.employee.emp_first_name} {c.employee.emp_last_name or ""}'.strip(), 'employee_id': c.employee_id, 'title': c.title, 'course': c.course.title if c.course_id else '', 'course_id': c.course_id, 'expiry_date': c.expiry_date, 'days': d}
        (exp if d >= 0 else done).append(row)
    for c in dq.order_by('emp_doc_expiry_date')[:200]:
        d = (c.emp_doc_expiry_date - date.today()).days
        row = {'kind': 'document', 'employee': f'{c.emp_id.emp_first_name} {c.emp_id.emp_last_name or ""}'.strip(), 'employee_id': c.emp_id_id, 'title': (c.document_type.type_name if c.document_type_id else 'Document'), 'course': '', 'course_id': None, 'expiry_date': c.emp_doc_expiry_date, 'days': d}
        (exp if d >= 0 else done).append(row)
    return {'expiring': sorted(exp, key=lambda r: r['days']), 'expired': sorted(done, key=lambda r: r['days'])}


# ---------------------------------------------------------------- development plan
def development_plan(employee_id):
    """Draft plan: steps from skill gaps (course + practice), weakest goals (coaching), certificates (renewal), 360 needs."""
    g = skill_gaps(employee_id)
    rv = review_summary(employee_id, cycles=1)
    steps, m = [], date.today()
    for row in g['gaps'][:3]:
        due = m + timedelta(days=90)
        c = (row.get('courses') or [{}])[0]
        steps.append({'title': f'{row["skill"]}: from {row["current_label"]} to {row["required_label"]}', 'how': f'Attend "{c["title"]}"' if c.get('title') else 'On-the-job practice with a senior colleague', 'due': str(due), 'done': False, 'measure': f'Skill assessed at level {row["required"]}'})
    if rv['sheets'] and rv['sheets'][0]['weakest']:
        s = rv['sheets'][0]
        steps.append({'title': f'Improve "{s["weakest"]}"', 'how': 'Monthly coaching session with manager; agree a measurable target', 'due': str(m + timedelta(days=120)), 'done': False, 'measure': 'Rating 3 or above at next cycle'})
    for c in certificates(employee_id=employee_id)['expiring'][:2]:
        steps.append({'title': f'Renew {c["title"]}', 'how': 'Book renewal course / exam', 'due': str(c['expiry_date'] - timedelta(days=14)), 'done': False, 'measure': 'New certificate uploaded'})
    if not steps:
        steps.append({'title': 'Stretch assignment', 'how': 'Lead one improvement project in the department', 'due': str(m + timedelta(days=180)), 'done': False, 'measure': 'Project delivered and reviewed'})
    return {'employee': g['employee'], 'title': f'Development plan {m.year} – {g["employee"]["name"]}', 'steps': steps, 'summary': rv['text'], 'gaps_text': g['text']}


def _polish(text, instruction):
    try:
        from AISettings import service as A
        r = A.chat(None, 'ask', instruction, [{'role': 'user', 'content': text}], max_tokens=800)
        out = ''.join(b.get('text', '') for b in (r.get('content') or []) if b.get('type') == 'text').strip()
        return out or text
    except Exception:
        return text
