"""Business rules of the blueprint modules added in v1.16.0: change management, occupational health, audits, quality,
performance (KPIs, management review), ergonomics, environmental, chemicals / SDS, claims, industrial hygiene,
compliance, document control and training / competency. The core (risk, permits, incidents, inspections, CAPA) is in
services.py; the generic register API that exposes these records is registers.py."""
import calendar
import logging
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Q

from . import access as A
from .models import (Action, ApprovalRule, Audit, AuditFinding, Chemical, Claim, Competency, ComplianceAssessment, DocumentAck,
                     EnvParameter, EnvReading, HealthSurveillance, HSEDocument, HSEEvent, HSEFile, HygieneSample, Incident, Inspection, KPI,
                     KPIValue, ManagementReview, Nonconformity, Obligation, Observation, Permit, RiskAssessment, ChangeRequest)
from .services import (HSEError, PAGE, approve_step, close_todos, create_action, current_step, emp_name, event, make_steps, next_number,
                       notify_step, now, settings, tell_emp, tell_users, today, user_name, users_with_role)

log = logging.getLogger(__name__)
OPEN_ACTION = ('open', 'in_progress', 'rejected', 'done')


def add_months(d, n):
    y, m = divmod(d.month - 1 + n, 12)
    y += d.year
    m += 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def open_actions(source, oid):
    return Action.objects.filter(source=source, source_id=oid, status__in=OPEN_ACTION)


def rule_steps(subject, key, default):
    r = ApprovalRule.objects.filter(subject=subject, key=key, is_active=True).first() or \
        ApprovalRule.objects.filter(subject=subject, key='*', is_active=True).first()
    return (r.steps if r and r.steps else None) or default


def users_of_emp(e):
    return [e.users] if e is not None and e.users_id else []


def manager_of(e):
    """The employee's reporting manager (a user) – for escalations and the operational (non-clinical) health outcome."""
    return getattr(e, 'emp_reporting_manager', None) if e is not None else None


# ------------------------------------------------------------------ shared: risk review, competency
def flag_risk_review(ra, reason, user=None):
    if ra is None or ra.status not in ('approved', 'submitted'):
        return
    ra.review_required, ra.review_reason = True, f'Review required after {reason}'[:255]
    ra.save(update_fields=['review_required', 'review_reason'])
    event('risk', ra.pk, ra.review_reason, user)
    if ra.owner_id:
        tell_emp(ra.owner, f'{ra.number}: review the risk assessment', f'{ra.title}\n{ra.review_reason}', 'risk', ra.pk, ra.number,
                 PAGE + f'risks/{ra.pk}', f'Review risk assessment {ra.number}')


def competency_gaps(people, codes, on_date=None):
    """['Name: Confined space entry expired 2026-01-01', …] for everyone missing a valid competency."""
    from .models import CompetencyType
    d = on_date.date() if hasattr(on_date, 'date') else (on_date or today())
    names = dict(CompetencyType.objects.filter(code__in=codes).values_list('code', 'name'))
    gaps = []
    for e in people:
        for c in codes:
            rec = Competency.objects.filter(employee=e, ctype__code=c, assessed_competent=True).order_by('-issued_on').first()
            label = names.get(c, c)
            if rec is None:
                gaps.append(f'{emp_name(e)}: {label} missing')
            elif rec.expires_on and rec.expires_on < d:
                gaps.append(f'{emp_name(e)}: {label} expired {rec.expires_on}')
    return gaps


def competency_saved(c, request):
    if not c.expires_on and c.ctype.validity_months:
        c.expires_on = add_months(c.issued_on, c.ctype.validity_months)
    if c.approved_by_id is None:
        c.approved_by = request.user
    c.save()


def raise_action(source, obj, request, f, ref=None, site=None):
    a = create_action(source=source, source_id=obj.pk, source_ref=ref or getattr(obj, 'number', '') or getattr(obj, 'ref', '') or str(obj.pk),
                      title=f.s('title'), description=f.s('description'), owner=f.emp('owner'), due_date=f.dt('due_date'),
                      priority=f.choice('priority', Action.PRIORITY, 'medium'), site=site if site is not None else getattr(obj, 'site', None),
                      verifier=f.emp('verifier'), user=request.user, kind=f.s('kind') or 'corrective')
    return a


# ------------------------------------------------------------------ 2. management of change
MOC_AREAS = ('safety', 'environment', 'health', 'quality', 'legal', 'operational')
PRESTART = (('training', 'People trained on the change'), ('documents', 'Procedures / drawings updated'),
            ('permits', 'Permits and authorisations in place'), ('competency', 'Competency of the people confirmed'),
            ('emergency', 'Emergency arrangements reviewed'))


def moc_submit(m, request, f):
    A.need(m.requester_id == request.user.pk or A.any_role(request, 'moc_coordinator', 'hse_manager'), 'Only the requester can submit.')
    if m.status not in ('draft', 'rejected'):
        raise HSEError('Only a draft can be submitted.')
    if m.kind == 'temporary' and not m.end_date:
        raise HSEError('A temporary change needs the date it ends.', 'end_date')
    if m.kind == 'emergency' and not m.emergency_justification.strip():
        raise HSEError('Explain why this is an emergency change.', 'emergency_justification')
    m.status = 'submitted'
    m.save()
    event('moc', m.pk, f'Submitted by {user_name(request.user)}', request.user)
    tell_users(users_with_role('moc_coordinator'), f'{m.number}: change request to screen', f'{m.title}\n{m.description[:1500]}', 'moc', m.pk,
               m.number, PAGE + f'r/moc/{m.pk}', f'Screen change {m.number}')


def moc_screen(m, request, f):
    A.need(A.any_role(request, 'moc_coordinator', 'hse_manager'), 'Only the change coordinator screens changes.')
    if m.status != 'submitted':
        raise HSEError('The change is not waiting for screening.')
    m.formal_required = f.b('formal_required', True)
    m.screening_note = f.s('screening_note')
    m.coordinator = f.emp('coordinator') or m.coordinator or A.my_emp(request)
    close_todos('moc', m.pk, 'Screen')
    if not m.formal_required:
        if not m.screening_note:
            raise HSEError('Record why formal change control is not needed.', 'screening_note')
        m.status, m.closed_at = 'closed', now()
        event('moc', m.pk, f'Screened – no formal change control needed: {m.screening_note}', request.user)
    else:
        m.status = 'impact'
        event('moc', m.pk, f'Screened – formal change control. Impact assessment by {emp_name(m.coordinator)}', request.user)
    m.save()


def moc_for_approval(m, request, f):
    A.need(A.any_role(request, 'moc_coordinator', 'hse_manager'), 'Only the change coordinator sends a change for approval.')
    if m.status != 'impact':
        raise HSEError('Finish the impact assessment first.')
    imp = m.impacts or {}
    missing = [a for a in MOC_AREAS if not (imp.get(a) or {}).get('level')]
    if missing:
        raise HSEError('Assess every impact area: ' + ', '.join(missing), 'impacts')
    high = [a for a in MOC_AREAS if imp[a]['level'] in ('medium', 'high')]
    if high and (m.risk_assessment is None or m.risk_assessment.status != 'approved' or m.risk_assessment.review_required):
        raise HSEError('Impacts rated medium / high (' + ', '.join(high) + ') – link the updated, approved HIRA / JSA first.', 'risk_assessment')
    make_steps('moc', m.pk, rule_steps('moc', m.kind, [{'role': 'site_manager', 'label': 'Operations'}, {'role': 'hse_manager', 'label': 'HSE'}]))
    m.status = 'approval'
    m.save()
    event('moc', m.pk, 'Sent for approval', request.user)
    notify_step('moc', m.pk, m.number, PAGE + f'r/moc/{m.pk}', f'{m.number} {m.title}')


def moc_decide(m, request, f, approve):
    if m.status != 'approval':
        raise HSEError('The change is not waiting for approval.')
    st, nxt = approve_step(request, 'moc', m.pk, approve, f.s('note'), m.requester_id)
    close_todos('moc', m.pk, 'Approve')
    if not approve:
        m.status = 'rejected'
        event('moc', m.pk, f'Rejected by {user_name(request.user)} ({st.label}): {f.s("note")}', request.user)
    elif nxt is None:
        m.status = 'approved'
        event('moc', m.pk, f'Approved for implementation by {user_name(request.user)} ({st.label})', request.user)
        tell_users([m.requester] + users_of_emp(m.coordinator), f'{m.number} approved for implementation', m.title, 'moc', m.pk, m.number,
                   PAGE + f'r/moc/{m.pk}')
    else:
        event('moc', m.pk, f'{st.label} approved by {user_name(request.user)}', request.user)
        notify_step('moc', m.pk, m.number, PAGE + f'r/moc/{m.pk}', f'{m.number} {m.title}')
    m.save()


def moc_start(m, request, f):
    A.need(A.any_role(request, 'moc_coordinator', 'hse_manager') or m.requester_id == request.user.pk, 'Only the coordinator or requester can start.')
    if m.status != 'approved':
        raise HSEError('The change is not approved for implementation.')
    checks = {**(m.prestart_checks or {}), **(f.j('prestart_checks') or {})}
    miss = [lbl for k, lbl in PRESTART if not checks.get(k)]
    if miss:
        raise HSEError('Pre-start verification not complete: ' + '; '.join(miss), 'prestart_checks')
    crit = open_actions('moc', m.pk).filter(priority__in=('high', 'critical'))
    if crit.exists():
        raise HSEError('Critical actions are not closed: ' + ', '.join(crit.values_list('number', flat=True)))
    m.prestart_checks = checks
    m.status = 'implementing'
    m.save()
    event('moc', m.pk, f'Pre-start verification done by {user_name(request.user)} – implementation started', request.user)


def moc_implemented(m, request, f):
    if m.status != 'implementing':
        raise HSEError('The change is not being implemented.')
    m.implemented_on = f.dt('implemented_on') or today()
    m.implemented_by = f.emp('implemented_by') or A.my_emp(request)
    m.status = 'verification'
    m.save()
    event('moc', m.pk, f'Implemented on {m.implemented_on} by {emp_name(m.implemented_by)}', request.user)


def moc_close(m, request, f):
    A.need(A.any_role(request, 'moc_coordinator', 'hse_manager'), 'Only the change coordinator closes changes.')
    if m.status != 'verification':
        raise HSEError('Do the post-change review first.')
    if f.d.get('objective_met') in (None, ''):
        raise HSEError('Did the change achieve its objective?', 'objective_met')
    m.objective_met, m.new_hazards, m.review_note = f.b('objective_met'), f.s('new_hazards'), f.s('review_note')
    oa = open_actions('moc', m.pk)
    if oa.exists():
        raise HSEError('Actions still open: ' + ', '.join(oa.values_list('number', flat=True)))
    if m.new_hazards:
        flag_risk_review(m.risk_assessment, f'change {m.number}: new hazards', request.user)
    m.status, m.closed_at = 'closed', now()
    m.save()
    event('moc', m.pk, f'Closed – objective {"met" if m.objective_met else "NOT met"}. {m.review_note}', request.user)


def moc_overdue_temporary():
    n = 0
    for m in ChangeRequest.objects.filter(kind='temporary', status__in=('implementing', 'verification'), end_date__lt=today()):
        tell_users([m.requester] + users_of_emp(m.coordinator) + users_with_role('moc_coordinator'),
                   f'{m.number}: temporary change past its end date', f'{m.title}\nEnded {m.end_date} – remove it or extend it through a new approval.',
                   'moc', m.pk, m.number, PAGE + f'r/moc/{m.pk}')
        n += 1
    return n


# ------------------------------------------------------------------ 3. occupational health
def health_result(h, request, f):
    A.need(A.can_see_health(request), 'Only occupational health records results.')
    h.completed_on = f.dt('completed_on', True, 'Completed on')
    fit = f.choice('fitness', HealthSurveillance.FITNESS, None)
    if not fit or fit == 'pending':
        raise HSEError('Choose the fitness outcome.', 'fitness')
    h.fitness = fit
    if h.fitness == 'fit_restrictions' and not f.s('restrictions'):
        raise HSEError('Describe the work restrictions.', 'restrictions')
    h.restrictions, h.clinical_notes = f.s('restrictions'), f.s('clinical_notes')
    h.followup_date = f.dt('followup_date')
    h.save()
    event('health', h.pk, f'Assessment recorded by {user_name(request.user)}: {h.get_fitness_display()}', request.user)
    close_todos('health', h.pk)
    nxt = None
    if h.assessment_type in ('periodic', 'pre_employment') and h.interval_months:
        nxt = HealthSurveillance.objects.create(number=next_number('health'), employee=h.employee, job_role=h.job_role, exposure=h.exposure,
                                                assessment_type='periodic', due_date=add_months(h.completed_on, h.interval_months),
                                                provider=h.provider, interval_months=h.interval_months, created_by=request.user)
        event('health', nxt.pk, f'Scheduled from {h.number}', request.user)
    # the manager is told only the operational outcome – never the clinical findings
    mgr = manager_of(h.employee)
    if mgr is not None and h.fitness != 'fit':
        tell_users([mgr], f'Fitness for work: {emp_name(h.employee)}',
                   f'{emp_name(h.employee)} – {h.get_fitness_display()}' + (f'\nRestrictions: {h.restrictions}' if h.restrictions else ''),
                   'health', h.pk, h.number, PAGE + 'r/health')
    return nxt


def health_due():
    t = today()
    n = 0
    for h in HealthSurveillance.objects.filter(completed_on__isnull=True, due_date__lte=t + timedelta(days=14)).select_related('employee'):
        late = (t - h.due_date).days
        tell_users(users_with_role('occ_health'), f'{h.number}: health assessment {"overdue" if late > 0 else "due"} – {emp_name(h.employee)}',
                   f'{h.get_assessment_type_display()} ({h.get_exposure_display()}) due {h.due_date}', 'health', h.pk, h.number,
                   PAGE + f'r/health/{h.pk}', f'Health assessment {h.number}')
        n += 1
    return n


# ------------------------------------------------------------------ 4. audits
AUDIT_DUE = {'critical': 7, 'major': 30, 'minor': 60}


def audit_check(x, request):
    if x.auditee_id and x.auditee_id == x.lead_auditor_id:
        raise HSEError('The auditor cannot audit their own area (conflict of interest).', 'lead_auditor')
    if x.site_id and x.site.manager_id and x.site.manager_id == x.lead_auditor_id:
        raise HSEError('The site manager cannot be the lead auditor of their own site (conflict of interest).', 'lead_auditor')
    if x.template_id and settings().require_review and x.template.review_status != 'reviewed':
        raise HSEError('This checklist comes from the industry pack and has not been reviewed yet.', 'template')


def audit_start(x, request, f):
    me = A.my_emp(request)
    A.need((me and x.lead_auditor_id == me.pk) or A.any_role(request, 'hse_manager', 'auditor'), 'Only the lead auditor starts the audit.')
    if x.status != 'planned':
        raise HSEError('The audit is not planned.')
    x.status = 'in_progress'
    x.save()
    event('audit', x.pk, f'Started by {user_name(request.user)}', request.user)


def audit_finish(x, request, f):
    me = A.my_emp(request)
    A.need((me and x.lead_auditor_id == me.pk) or A.has_role(request, 'hse_manager'), 'Only the lead auditor finishes the audit.')
    if x.status != 'in_progress':
        raise HSEError('The audit is not in progress.')
    qs = x.template.questions or []
    ans = x.answers or {}
    miss = [q['text'] for q in qs if not (ans.get(q['key']) or {}).get('value')]
    if miss:
        raise HSEError('Answer every question: ' + '; '.join(miss[:3]) + (' …' if len(miss) > 3 else ''))
    x.summary = f.s('summary') or x.summary
    if not x.summary:
        raise HSEError('Write the report summary.', 'summary')
    scored = [q for q in qs if ans[q['key']]['value'] in ('compliant', 'noncompliant', 'yes', 'no')]
    ok = [q for q in scored if ans[q['key']]['value'] in ('compliant', 'yes')]
    x.score = Decimal(len(ok) * 100) / Decimal(len(scored)) if scored else None
    made = 0
    for q in qs:
        v = ans[q['key']]['value']
        if v not in ('noncompliant', 'no', 'observation') or x.findings.filter(question=q['key']).exists():
            continue
        grade = 'observation' if v == 'observation' else ('major' if q.get('critical') else 'minor')
        repeat = AuditFinding.objects.filter(audit__template=x.template, audit__site=x.site, question=q['key']).exclude(audit=x).exists()
        AuditFinding.objects.create(audit=x, number=f'{x.number}-F{x.findings.count() + 1:02d}', question=q['key'], text=q['text'],
                                    grade=grade, evidence=(ans[q['key']] or {}).get('note', ''), repeat=repeat)
        made += 1
    x.done_date = x.done_date or today()
    make_steps('audit', x.pk, rule_steps('audit', x.kind, [{'role': 'hse_manager', 'label': 'HSE manager'}]))
    x.status = 'reported'
    x.save()
    event('audit', x.pk, f'Report submitted – score {x.score or 0:.0f} %, {made} finding(s)', request.user)
    notify_step('audit', x.pk, x.number, PAGE + f'r/audits/{x.pk}', f'{x.number} {x.title} – audit report')


def audit_decide(x, request, f, approve):
    if x.status != 'reported':
        raise HSEError('The report is not waiting for approval.')
    st, nxt = approve_step(request, 'audit', x.pk, approve, f.s('note'), x.lead_auditor.users_id if x.lead_auditor_id else None)
    close_todos('audit', x.pk, 'Approve')
    if not approve:
        x.status = 'in_progress'
        event('audit', x.pk, f'Report returned by {user_name(request.user)}: {f.s("note")}', request.user)
    elif nxt is None:
        owner = x.auditee or (x.site.manager if x.site_id else None) or x.lead_auditor
        for fd in x.findings.filter(action__isnull=True, grade__in=AUDIT_DUE):
            fd.action = create_action(source='audit', source_id=x.pk, source_ref=fd.number, title=f'{fd.text}'[:240], description=fd.evidence,
                                      owner=owner, due_date=today() + timedelta(days=AUDIT_DUE[fd.grade]),
                                      priority={'critical': 'critical', 'major': 'high'}.get(fd.grade, 'medium'), site=x.site, user=request.user)
            fd.save()
        x.status = 'approved'
        event('audit', x.pk, f'Report approved by {user_name(request.user)} – corrective actions raised', request.user)
    else:
        notify_step('audit', x.pk, x.number, PAGE + f'r/audits/{x.pk}', f'{x.number} {x.title}')
    x.save()


def finding_action(fd, request, f):
    A.need(A.any_role(request, 'hse_manager', 'hse_officer', 'auditor'), 'Only HSE or the auditor raise finding actions.')
    if fd.action_id:
        raise HSEError('This finding already has an action.')
    fd.action = raise_action('audit', fd.audit, request, f, ref=fd.number, site=fd.audit.site)
    fd.save()


def audit_close(x, request, f):
    A.need(A.any_role(request, 'hse_manager', 'auditor', 'hse_officer'), 'Only HSE closes audits.')
    if x.status != 'approved':
        raise HSEError('The report must be approved first.')
    oa = open_actions('audit', x.pk)
    if oa.exists():
        raise HSEError('Finding actions still open (they must be verified): ' + ', '.join(oa.values_list('number', flat=True)))
    x.status = 'closed'
    x.save()
    event('audit', x.pk, f'Closed by {user_name(request.user)} – all actions verified', request.user)


# ------------------------------------------------------------------ 5. quality
def nc_contain(n, request, f):
    n.containment = f.s('containment') or n.containment
    if not n.containment:
        raise HSEError('Describe the containment / quarantine.', 'containment')
    if n.status == 'open':
        n.status = 'contained'
    n.save()
    event('nc', n.pk, f'Containment recorded by {user_name(request.user)}', request.user)


def nc_disposition(n, request, f):
    A.need(A.has_role(request, 'quality_manager'), 'Only the quality manager approves the disposition.')
    d = f.choice('disposition', Nonconformity.DISPOSITION, '')
    if not d:
        raise HSEError('Choose the disposition.', 'disposition')
    if n.reported_by_id == request.user.pk and not A.is_admin(request):
        raise HSEError('You cannot approve the disposition of a nonconformity you reported.')
    n.disposition, n.disposition_approved_by = d, request.user
    if f.s('root_cause'):
        n.root_cause = f.s('root_cause')
    n.status = 'investigation' if n.status in ('open', 'contained') else n.status
    n.save()
    event('nc', n.pk, f'Disposition "{n.get_disposition_display()}" approved by {user_name(request.user)}', request.user)


def nc_close(n, request, f):
    A.need(A.has_role(request, 'quality_manager'), 'Only the quality manager closes nonconformities.')
    n.reinspection_result = f.s('reinspection_result') or n.reinspection_result
    probs = []
    if not n.disposition_approved_by_id:
        probs.append('disposition not approved')
    if not n.root_cause.strip():
        probs.append('root cause not recorded')
    if not n.reinspection_result.strip():
        probs.append('re-inspection result missing')
    oa = open_actions('quality', n.pk)
    if oa.exists():
        probs.append('actions not verified: ' + ', '.join(oa.values_list('number', flat=True)))
    if probs:
        raise HSEError('Not ready to close: ' + '; '.join(probs))
    n.status, n.closed_at = 'closed', now()
    n.save()
    event('nc', n.pk, f'Closed by {user_name(request.user)}', request.user)


# ------------------------------------------------------------------ 7. performance
def month_range(period):
    start = period.replace(day=1)
    return start, add_months(start, 1) - timedelta(days=1)


def kpi_value(k, frm, to):
    s = settings()
    inc = Incident.objects.filter(occurred_at__date__range=(frm, to))
    if k.site_id:
        inc = inc.filter(site=k.site)
    from . import links as L
    hours, _src = L.hours_worked(frm, to, k.site if k.site_id else None)   # attendance when linked, else settings
    f = k.formula
    if f in ('trir', 'ltifr'):
        if not hours:
            return None
        n = inc.filter(treatment__in=('medical', 'restricted', 'lost_time', 'fatality') if f == 'trir' else ('lost_time', 'fatality')).count()
        return Decimal(n * s.rate_multiplier) / Decimal(hours)
    if f == 'near_misses':
        return Decimal(inc.filter(category__kind='near_miss').count())
    if f == 'audit_completion':
        planned = Audit.objects.filter(planned_date__range=(frm, to), unplanned=False).exclude(status='cancelled')
        return (Decimal(planned.exclude(status__in=('planned', 'in_progress')).count() * 100) / planned.count()) if planned.count() else None
    if f == 'action_closure':
        due = Action.objects.filter(due_date__range=(frm, to)).exclude(status='cancelled')
        ok = sum(1 for a in due.filter(status='closed') if a.done_at and a.done_at.date() <= a.due_date)
        return (Decimal(ok * 100) / due.count()) if due.count() else None
    if f == 'inspection_compliance':
        return Decimal(Inspection.objects.filter(date__range=(frm, to), status='completed').count())
    if f == 'training_compliance':
        recs = Competency.objects.all()
        total = recs.count()
        valid = recs.filter(Q(expires_on__isnull=True) | Q(expires_on__gte=to)).count()
        hv, ht = L.training_compliance(to)   # + mandatory HR courses when HR Learning is linked
        valid, total = valid + hv, total + ht
        return (Decimal(valid * 100) / total) if total else None
    if f == 'permit_compliance':
        ps = Permit.objects.filter(valid_from__date__range=(frm, to), status__in=('closed', 'active', 'suspended'))
        bad = ps.filter(Q(status='suspended') | Q(incidents__isnull=False)).distinct().count()
        return (Decimal((ps.count() - bad) * 100) / ps.count()) if ps.count() else None
    if f == 'env_exceedances':
        return Decimal(EnvReading.objects.filter(sampled_on__range=(frm, to), exceeded=True).count())
    if f == 'quality_ncs':
        return Decimal(Nonconformity.objects.filter(created_at__date__range=(frm, to)).count())
    if f == 'observations':
        return Decimal(Observation.objects.filter(reported_at__date__range=(frm, to)).count())
    if f == 'waste_hazardous':
        from .models import WasteRecord
        return sum((w.quantity for w in WasteRecord.objects.filter(generated_on__range=(frm, to), hazardous=True, unit='kg')), Decimal(0))
    return None


def kpi_calculate(period=None, user=None):
    """Monthly: calculate every automatic KPI for the month; flag deviations from target."""
    period = (period or add_months(today(), -1)).replace(day=1)
    frm, to = month_range(period)
    out = 0
    for k in KPI.objects.filter(is_active=True).exclude(formula='manual'):
        v = kpi_value(k, frm, to)
        kv = KPIValue.objects.filter(kpi=k, period=period).first()
        if kv and kv.source == 'manual':
            continue
        if v is not None:
            v = Decimal(v).quantize(Decimal('0.01'))
        dev = bool(v is not None and k.target is not None and ((v < k.target) if k.higher_is_better else (v > k.target)))
        KPIValue.objects.update_or_create(kpi=k, period=period, defaults={'value': v, 'target': k.target, 'source': 'auto', 'deviation': dev})
        from . import links as L
        L.push_goal_progress(k, v)   # v1.17.0 – Performance goals using this KPI
        if dev and k.owner_id:
            tell_emp(k.owner, f'KPI {k.code} off target for {period:%b %Y}', f'{k.name}: {v} {k.unit} (target {k.target})', 'kpi', k.pk, k.code,
                     PAGE + f'r/kpis/{k.pk}')
        out += 1
    return out


def kpi_value_saved(kv, request):
    kv.period = kv.period.replace(day=1)
    kv.source, kv.validated_by = 'manual', request.user
    k = kv.kpi
    kv.target = kv.target if kv.target is not None else k.target
    kv.deviation = bool(kv.value is not None and kv.target is not None and ((kv.value < kv.target) if k.higher_is_better else (kv.value > kv.target)))
    clash = KPIValue.objects.filter(kpi=k, period=kv.period).exclude(pk=kv.pk).first()
    if clash:
        clash.delete()
    kv.save()
    from . import links as L
    L.push_goal_progress(k, kv.value)


def review_collect(r, request, f):
    if r.status == 'closed':
        raise HSEError('The review is closed.')
    rows = []
    for k in KPI.objects.filter(is_active=True):
        vals = KPIValue.objects.filter(kpi=k, period__range=(r.period_from.replace(day=1), r.period_to)).order_by('period')
        last = vals.last()
        rows.append({'code': k.code, 'name': k.name, 'unit': k.unit, 'target': float(k.target) if k.target is not None else None,
                     'latest': float(last.value) if last and last.value is not None else None, 'deviations': vals.filter(deviation=True).count()})
    r.inputs = {
        'kpis': rows,
        'incidents': Incident.objects.filter(occurred_at__date__range=(r.period_from, r.period_to)).count(),
        'open_actions': Action.objects.filter(status__in=OPEN_ACTION).count(),
        'overdue_actions': Action.objects.filter(status__in=('open', 'in_progress', 'rejected'), due_date__lt=today()).count(),
        'audits_closed': Audit.objects.filter(done_date__range=(r.period_from, r.period_to)).count(),
        'noncompliant_obligations': Obligation.objects.filter(status__in=('noncompliant', 'partial'), is_active=True).count(),
        'collected_at': now().isoformat(),
    }
    r.save()
    event('review', r.pk, 'Inputs collected', request.user)


def review_close(r, request, f):
    A.need(A.has_role(request, 'hse_manager'), 'Only the HSE manager closes the management review.')
    r.decisions = f.s('decisions') or r.decisions
    if not r.decisions.strip():
        raise HSEError('Record the management decisions.', 'decisions')
    r.status = 'closed'
    r.save()
    event('review', r.pk, f'Closed by {user_name(request.user)}', request.user)


# ------------------------------------------------------------------ 9. ergonomics
def ergo_reassess(x, request, f):
    x.reassessed_score = f.n('reassessed_score')
    x.reassessed_level = f.choice('reassessed_level', x.LEVELS, '')
    x.effectiveness = f.s('effectiveness')
    if x.reassessed_score is None or not x.reassessed_level:
        raise HSEError('Enter the new score and risk level.', 'reassessed_score')
    oa = open_actions('ergonomics', x.pk)
    if oa.exists():
        raise HSEError('Controls still open: ' + ', '.join(oa.values_list('number', flat=True)))
    x.status = 'closed' if x.reassessed_level in ('low', 'medium') else 'actions'
    x.reassess_on = add_months(today(), 12)
    x.save()
    event('ergo', x.pk, f'Reassessed: {x.reassessed_score} ({x.get_reassessed_level_display()}) – {x.effectiveness}', request.user)


# ------------------------------------------------------------------ 10. environmental
def aspect_saved(a, request):
    for k in ('severity', 'probability'):
        if not 1 <= (getattr(a, k) or 0) <= 5:
            raise HSEError('1 to 5.', k)
    a.significant = a.severity * a.probability >= settings().significance_threshold
    a.save()


def parameter_saved(p, request):
    if p.limit_min is not None and p.limit_max is not None and p.limit_min > p.limit_max:
        raise HSEError('The lower limit is above the upper limit.', 'limit_min')
    if p.next_due is None:
        p.next_due = today()
    p.save()


def reading_saved(r, request):
    p = r.parameter
    r.exceeded = bool((p.limit_max is not None and r.value > p.limit_max) or (p.limit_min is not None and r.value < p.limit_min))
    r.recorded_by = r.recorded_by or request.user
    r.save()
    p.next_due = r.sampled_on + timedelta(days=p.frequency_days or 30)
    p.save(update_fields=['next_due'])
    if r.exceeded and r.action_id is None:
        owner = p.owner or (p.site.manager if p.site_id else None)
        lim = f'{p.limit_min if p.limit_min is not None else ""}–{p.limit_max if p.limit_max is not None else ""} {p.unit}'
        text = f'{p.name} at {p.site.name if p.site_id else ""} {p.point}: {r.value} {p.unit} on {r.sampled_on} – limit {lim}'
        if owner is not None:
            r.action = create_action(source='environment', source_id=p.pk, source_ref=f'{p.name} {r.sampled_on}', title=f'Limit exceeded: {p.name}'[:240],
                                     description=text + '\nInvestigate, contain and correct.', owner=owner, due_date=today() + timedelta(days=7),
                                     priority='high', site=p.site, user=request.user)
            r.save(update_fields=['action'])
        tell_users(users_with_role('env_officer') + users_of_emp(owner), f'Environmental limit exceeded: {p.name}', text, 'environment', p.pk,
                   p.name, PAGE + 'r/env-parameters')


def env_due():
    n = 0
    for p in EnvParameter.objects.filter(is_active=True, next_due__lt=today()):
        tell_users(users_with_role('env_officer') + users_of_emp(p.owner), f'Environmental monitoring overdue: {p.name}',
                   f'{p.name} ({p.site.name if p.site_id else ""} {p.point}) was due {p.next_due}', 'environment', p.pk, p.name, PAGE + 'r/env-parameters')
        n += 1
    return n


def waste_transfer(w, request, f):
    A.need(A.any_role(request, 'env_officer', 'hse_officer', 'hse_manager'), 'Only the environmental officer records waste transfers.')
    if w.status != 'stored':
        raise HSEError('Already transferred.')
    from .models import Contractor
    w.carrier = f.obj('carrier', Contractor) or w.carrier
    w.destination = f.s('destination') or w.destination
    w.manifest_no = f.s('manifest_no') or w.manifest_no
    w.disposal_permit = f.s('disposal_permit') or w.disposal_permit
    w.transferred_on = f.dt('transferred_on') or today()
    if w.carrier is None or w.carrier.status != 'approved':
        raise HSEError('Choose an approved waste carrier.', 'carrier')
    if not w.destination:
        raise HSEError('Enter the approved disposal destination.', 'destination')
    if w.hazardous and not (w.manifest_no and w.disposal_permit):
        raise HSEError('Hazardous waste needs the manifest number and the carrier / facility permit number.', 'manifest_no')
    w.status = 'transferred'
    w.save()
    event('waste', w.pk, f'Transferred to {w.carrier.name} → {w.destination} (manifest {w.manifest_no or "-"})', request.user)


def waste_disposed(w, request, f):
    if w.status != 'transferred':
        raise HSEError('Record the transfer first.')
    if not HSEFile.objects.filter(model='waste', object_id=w.pk).exists():
        raise HSEError('Attach the disposal certificate / signed manifest first.')
    w.disposed_on = f.dt('disposed_on') or today()
    w.status = 'disposed'
    w.save()
    event('waste', w.pk, f'Disposal confirmed {w.disposed_on}', request.user)


# ------------------------------------------------------------------ 11. chemicals
DEFAULT_INCOMPATIBLE = [['acid', 'base'], ['flammable', 'oxidizer'], ['water_reactive', 'acid'], ['water_reactive', 'base'],
                        ['oxidizer', 'peroxide'], ['flammable', 'peroxide'], ['acid', 'oxidizer']]


def incompatible_with(c):
    pairs = settings().incompatible_groups or DEFAULT_INCOMPATIBLE
    bad = {b for a, b in pairs if a == c.compat_group} | {a for a, b in pairs if b == c.compat_group}
    if not bad or not c.storage_location:
        return []
    return list(Chemical.objects.filter(status='approved', site=c.site, storage_location__iexact=c.storage_location, compat_group__in=bad)
                .exclude(pk=c.pk))


def chem_sds(c, request, f):
    files = f.filelist('file')
    if not files:
        raise HSEError('Choose the SDS file.', 'file')
    if not f.s('sds_revision') or not f.dt('sds_date'):
        raise HSEError('Enter the SDS revision and date.', 'sds_revision')
    x = files[0]
    hf = HSEFile.objects.create(model='chemical', object_id=c.pk, file=x, name=x.name[:255], size=x.size or 0, note=f'SDS rev {f.s("sds_revision")}',
                                uploaded_by=request.user)
    revised = c.sds_file_id is not None
    c.sds_file, c.sds_revision, c.sds_date = hf, f.s('sds_revision'), f.dt('sds_date')
    if revised and c.status == 'approved':
        c.status = 'review'          # a new SDS revision must be reviewed before the chemical is used further
        tell_users(users_with_role('hse_officer'), f'{c.number}: new SDS revision to review', f'{c.name} – SDS rev {c.sds_revision} ({c.sds_date})',
                   'chemical', c.pk, c.number, PAGE + f'r/chemicals/{c.pk}', f'Review SDS {c.number}')
    c.save()
    event('chemical', c.pk, f'SDS rev {c.sds_revision} ({c.sds_date}) uploaded by {user_name(request.user)}' + (' – review required' if revised else ''), request.user)


def chem_submit(c, request, f):
    if c.status not in ('requested', 'rejected'):
        raise HSEError('Already in review.')
    c.status = 'review'
    c.save()
    event('chemical', c.pk, 'Sent for HSE review', request.user)
    tell_users(users_with_role('hse_officer'), f'{c.number}: chemical to review', c.name, 'chemical', c.pk, c.number, PAGE + f'r/chemicals/{c.pk}',
               f'Review chemical {c.number}')


def chem_approve(c, request, f):
    A.need(A.any_role(request, 'hse_officer', 'hse_manager'), 'Only HSE approves chemicals.')
    if c.status != 'review':
        raise HSEError('The chemical is not in review.')
    miss = [lbl for ok, lbl in ((c.sds_file_id, 'SDS file'), (c.sds_date, 'SDS date'), (c.hazard_classes, 'hazard classification'),
                                (c.ppe, 'required PPE'), (c.storage_location, 'storage location'), (c.emergency_measures, 'emergency measures')) if not ok]
    if miss:
        raise HSEError('Required documentation missing: ' + ', '.join(miss))
    if c.sds_date and c.sds_date < today() - timedelta(days=5 * 365):
        raise HSEError('The SDS is more than 5 years old – get the current revision from the supplier.', 'sds_date')
    clash = incompatible_with(c)
    if clash:
        raise HSEError(f'Incompatible storage: {c.get_compat_group_display()} stored with ' +
                       ', '.join(f'{x.name} ({x.get_compat_group_display()})' for x in clash) + ' – choose another storage location.', 'storage_location')
    c.status, c.approved_by = 'approved', request.user
    c.review_due = add_months(today(), 36)
    c.save()
    close_todos('chemical', c.pk)
    event('chemical', c.pk, f'Approved for use by {user_name(request.user)}', request.user)


def chem_reject(c, request, f):
    A.need(A.any_role(request, 'hse_officer', 'hse_manager'), 'Only HSE rejects chemicals.')
    if not f.s('note'):
        raise HSEError('Give the reason.', 'note')
    c.status = 'rejected'
    c.save()
    close_todos('chemical', c.pk)
    event('chemical', c.pk, f'Rejected: {f.s("note")}', request.user)


def chem_archive(c, request, f):
    A.need(A.any_role(request, 'hse_officer', 'hse_manager'), 'Only HSE archives chemicals.')
    c.status = 'archived'
    c.save()
    event('chemical', c.pk, f'Archived: {f.s("note")}', request.user)


def chem_due():
    n = 0
    for c in Chemical.objects.filter(status='approved', review_due__lt=today()):
        tell_users(users_with_role('hse_officer'), f'{c.number}: chemical / SDS review due', f'{c.name} – review was due {c.review_due}', 'chemical', c.pk,
                   c.number, PAGE + f'r/chemicals/{c.pk}', f'Review chemical {c.number}')
        n += 1
    return n


# ------------------------------------------------------------------ 13. claims
def claim_outstanding(c):
    return (c.approved_amount or 0) - (c.paid_amount or 0)


def claim_submit(c, request, f):
    if c.status != 'open':
        raise HSEError('Already submitted.')
    c.submitted_on = f.dt('submitted_on') or today()
    if not c.insurer or not c.policy_ref:
        raise HSEError('Enter the insurer and policy reference.', 'insurer')
    c.status = 'submitted'
    c.save()
    event('claim', c.pk, f'Submitted to {c.insurer} on {c.submitted_on}', request.user)


def claim_decision(c, request, f):
    if c.status not in ('submitted', 'assessment', 'disputed'):
        raise HSEError('Submit the claim first.')
    st = f.choice('decision', (('assessment', ''), ('approved', ''), ('disputed', ''), ('rejected', '')), None)
    if not st:
        raise HSEError('Choose the insurer decision.', 'decision')
    if st == 'approved':
        c.approved_amount = f.n('approved_amount', None) if f.has('approved_amount') else c.approved_amount
        if not c.approved_amount:
            raise HSEError('Enter the approved amount.', 'approved_amount')
    c.status = st
    c.save()
    event('claim', c.pk, f'Insurer decision: {c.get_status_display()}' + (f' – {c.approved_amount}' if st == 'approved' else '') + f'. {f.s("note")}', request.user)


def claim_request_settlement(c, request, f):
    if c.status != 'approved':
        raise HSEError('Only an approved claim can be settled.')
    make_steps('claim', c.pk, rule_steps('claim', c.claim_type, [{'role': 'hse_manager', 'label': 'HSE manager'}]))
    event('claim', c.pk, 'Settlement approval requested', request.user)
    notify_step('claim', c.pk, c.number, PAGE + f'r/claims/{c.pk}', f'{c.number} settlement {c.approved_amount}')


def claim_decide(c, request, f, approve):
    st, nxt = approve_step(request, 'claim', c.pk, approve, f.s('note'), c.created_by_id)
    close_todos('claim', c.pk, 'Approve')
    if approve and nxt is None:
        c.settlement_approved_by = request.user
        c.save()
        event('claim', c.pk, f'Settlement approved by {user_name(request.user)}', request.user)
    elif not approve:
        event('claim', c.pk, f'Settlement refused: {f.s("note")}', request.user)


def claim_settle(c, request, f):
    if c.status != 'approved' or not c.settlement_approved_by_id:
        raise HSEError('The settlement needs internal approval first.')
    c.paid_amount = f.n('paid_amount', c.paid_amount)
    c.status = 'settled'
    c.save()
    event('claim', c.pk, f'Settled – paid {c.paid_amount}, outstanding {claim_outstanding(c)}', request.user)
    from . import links as L
    L.claim_settled(c)


def claim_close(c, request, f):
    if c.status not in ('settled', 'rejected'):
        raise HSEError('Only a settled or rejected claim can be closed.')
    if c.status == 'settled' and claim_outstanding(c) > 0:
        raise HSEError(f'Not reconciled – {claim_outstanding(c)} still outstanding.')
    c.status = 'closed'
    c.save()
    event('claim', c.pk, f'Closed by {user_name(request.user)}', request.user)


# ------------------------------------------------------------------ 14. industrial hygiene
def limit_saved(lim, request, old=None):
    """Exposure limits are version-controlled: changing a used limit makes a new version."""
    if old is not None and old.samples.exists() and (old.limit_value != lim.limit_value or old.action_level != lim.action_level or old.unit != lim.unit):
        from .models import ExposureLimit
        new = ExposureLimit.objects.create(agent=lim.agent, category=lim.category, unit=lim.unit, limit_value=lim.limit_value,
                                           action_level=lim.action_level, averaging=lim.averaging, jurisdiction=lim.jurisdiction,
                                           version=str(int(old.version) + 1 if str(old.version).isdigit() else f'{old.version}.1'),
                                           effective_from=today(), is_active=True)
        ExposureLimit.objects.filter(pk=old.pk).update(is_active=False)
        event('limit', new.pk, f'New version of {old.agent} (was {old.limit_value} {old.unit})', request.user)
        return new
    lim.save()
    return lim


def sample_result(x, request, f):
    A.need(A.any_role(request, 'hse_officer', 'hse_manager', 'occ_health'), 'Only HSE records monitoring results.')
    x.sampled_on = f.dt('sampled_on', True, 'Sample date')
    x.result = f.n('result')
    if x.result is None:
        raise HSEError('Enter the result.', 'result')
    for k in ('instrument', 'laboratory', 'interpretation', 'conditions', 'method'):
        if f.has(k):
            setattr(x, k, f.s(k))
    if f.has('calibration_date'):
        x.calibration_date = f.dt('calibration_date')
    if f.has('duration_min'):
        x.duration_min = f.i('duration_min')
    if not x.calibration_date or x.calibration_date < x.sampled_on - timedelta(days=365) or x.calibration_date > x.sampled_on:
        raise HSEError('The instrument calibration must be within the 12 months before the sample.', 'calibration_date')
    lim = x.limit
    low = lim.averaging == 'minimum'
    x.exceeded = (x.result < lim.limit_value) if low else (x.result > lim.limit_value)
    x.above_action_level = bool(lim.action_level is not None and ((x.result < lim.action_level) if low else (x.result >= lim.action_level)))
    x.status = 'action' if x.exceeded else 'done'
    x.retest_on = f.dt('retest_on') or (x.sampled_on + timedelta(days=30 if x.exceeded else (180 if x.above_action_level else 365)))
    x.save()
    event('hygiene', x.pk, f'Result {x.result} {lim.unit} vs limit {lim.limit_value} ({lim.get_averaging_display()}, v{lim.version})'
          + (' – EXCEEDED' if x.exceeded else (' – above action level' if x.above_action_level else '')), request.user)
    if x.exceeded or x.above_action_level:
        owner = f.emp('owner') or (x.site.manager if x.site_id else None)
        if x.exceeded and owner is not None and x.action_id is None:
            x.action = create_action(source='hygiene', source_id=x.pk, source_ref=x.number, title=f'Exposure above limit: {lim.agent} at {x.location}'[:240],
                                     description=f'{x.result} {lim.unit} > {lim.limit_value}. Investigate and implement engineering / other controls.',
                                     owner=owner, due_date=today() + timedelta(days=14), priority='high', site=x.site, user=request.user)
            x.save(update_fields=['action'])
        # exposure is passed to occupational health (restricted) – no medical conclusion is drawn here
        tell_users(users_with_role('occ_health') + users_with_role('hse_officer'), f'{x.number}: exposure {"above the limit" if x.exceeded else "above the action level"} – {lim.agent}',
                   f'{x.location} {x.job_group}: {x.result} {lim.unit} (limit {lim.limit_value}). Retest by {x.retest_on}.', 'hygiene', x.pk, x.number,
                   PAGE + f'r/hygiene/{x.pk}')


def sample_close(x, request, f):
    if x.status not in ('done', 'action'):
        raise HSEError('Record the result first.')
    if x.action_id and x.action.status not in ('closed', 'cancelled'):
        raise HSEError(f'Control action {x.action.number} is not verified yet.')
    x.control_action = f.s('control_action') or x.control_action
    if x.exceeded and not x.control_action:
        raise HSEError('Record the controls implemented.', 'control_action')
    x.status = 'closed'
    x.save()
    event('hygiene', x.pk, 'Closed', request.user)


# ------------------------------------------------------------------ 15. compliance
def obligation_assess(o, request, f):
    me = A.my_emp(request)
    A.need((me and o.owner_id == me.pk) or A.any_role(request, 'hse_manager', 'hse_officer', 'env_officer'), 'Only the owner or HSE assess obligations.')
    res = f.choice('result', Obligation.STATUS, None)
    if not res or res == 'not_assessed':
        raise HSEError('Choose the assessment result.', 'result')
    ev = f.s('evidence')
    if res in ('compliant', 'partial') and not ev and not f.filelist('files'):
        raise HSEError('Compliance needs evidence – describe or attach it.', 'evidence')
    a = ComplianceAssessment.objects.create(obligation=o, assessed_on=f.dt('assessed_on') or today(), result=res, evidence=ev,
                                            finding=f.s('finding'), assessor=request.user)
    for x in f.filelist('files'):
        HSEFile.objects.create(model='obligation', object_id=o.pk, file=x, name=x.name[:255], size=x.size or 0, note=f'Evidence {a.assessed_on}',
                               uploaded_by=request.user)
    event('obligation', o.pk, f'Assessed as "{a.get_result_display()}" by {user_name(request.user)} – waiting for review', request.user)
    rv = users_of_emp(o.reviewer) or users_with_role('hse_manager')
    tell_users(rv, f'{o.ref}: compliance assessment to approve', f'{o.title}\nResult: {a.get_result_display()}\n{ev}', 'obligation', o.pk, o.ref,
               PAGE + f'r/obligations/{o.pk}', f'Approve compliance {o.ref}')
    return a


def obligation_review(o, request, f, approve):
    a = o.assessments.filter(state='pending').first()
    if a is None:
        raise HSEError('No assessment is waiting for approval.')
    me = A.my_emp(request)
    A.need((me and o.reviewer_id == me.pk) or A.has_role(request, 'hse_manager'), 'Only the reviewer approves the assessment.')
    if a.assessor_id == request.user.pk and not A.is_admin(request):
        raise HSEError('You cannot approve your own assessment (separation of duties).')
    close_todos('obligation', o.pk, 'Approve')
    if not approve:
        if not f.s('note'):
            raise HSEError('Give the reason.', 'note')
        a.state = 'rejected'
        a.save()
        event('obligation', o.pk, f'Assessment returned: {f.s("note")}', request.user)
        return
    a.state, a.approved_by, a.approved_at = 'approved', request.user, now()
    o.status, o.last_assessed = a.result, a.assessed_on
    if a.result in ('compliant', 'na'):
        o.due_date = add_months(a.assessed_on, o.recurrence_months) if o.recurrence_months else None
        o.escalated_on = None
    if a.result in ('noncompliant', 'partial') and o.owner_id and not a.action_id:
        a.action = create_action(source='compliance', source_id=o.pk, source_ref=o.ref, title=f'Close compliance gap: {o.title}'[:240],
                                 description=a.finding or a.evidence, owner=o.owner, due_date=today() + timedelta(days=30),
                                 priority='high' if a.result == 'noncompliant' else 'medium', site=o.site, user=request.user)
    a.save()
    o.save()
    event('obligation', o.pk, f'Assessment approved by {user_name(request.user)}: {a.get_result_display()}', request.user)


def compliance_due():
    """Daily: obligations due soon / overdue to the owner; overdue > 7 days escalate to the owner's manager and the HSE manager."""
    t = today()
    n = 0
    for o in Obligation.objects.filter(is_active=True, due_date__isnull=False).exclude(status='na').select_related('owner', 'site'):
        if o.due_date > t + timedelta(days=14):
            continue
        late = (t - o.due_date).days
        if o.owner_id:
            tell_emp(o.owner, f'{o.ref}: compliance {"overdue" if late > 0 else "due"} {o.due_date}', o.title, 'obligation', o.pk, o.ref,
                     PAGE + f'r/obligations/{o.pk}', f'Assess {o.ref}')
        if late > 7 and (o.escalated_on is None or (t - o.escalated_on).days >= 7):
            esc = [manager_of(o.owner)] + users_with_role('hse_manager') + (users_of_emp(o.site.manager) if o.site_id else [])
            tell_users([u for u in esc if u], f'ESCALATION: {o.ref} overdue {late} days', f'{o.title}\nOwner: {emp_name(o.owner)}\nDue: {o.due_date}',
                       'obligation', o.pk, o.ref, PAGE + f'r/obligations/{o.pk}', f'Escalated compliance {o.ref}')
            o.escalated_on = t
            o.save(update_fields=['escalated_on'])
            event('obligation', o.pk, f'Escalated – {late} days overdue')
        n += 1
    return n


# ------------------------------------------------------------------ document control
def doc_submit(d, request, f):
    if d.status != 'draft':
        raise HSEError('Only a draft can be sent for review.')
    if not HSEFile.objects.filter(model='document', object_id=d.pk).exists():
        raise HSEError('Attach the document file first.')
    if not d.approver_id:
        raise HSEError('Choose the approver.', 'approver')
    d.status = 'review'
    d.save()
    event('document', d.pk, f'Sent for review by {user_name(request.user)}', request.user)
    tell_emp(d.approver, f'{d.number} rev {d.revision}: document to approve', d.title, 'document', d.pk, d.number, PAGE + f'r/documents/{d.pk}',
             f'Approve document {d.number}')


def doc_decide(d, request, f, approve):
    if d.status != 'review':
        raise HSEError('The document is not in review.')
    me = A.my_emp(request)
    A.need((me and d.approver_id == me.pk) or A.has_role(request, 'hse_manager'), 'Only the approver can approve.')
    if (d.created_by_id == request.user.pk or (me and d.owner_id == me.pk)) and not A.is_admin(request):
        raise HSEError('The author cannot approve their own document.')
    close_todos('document', d.pk)
    if not approve:
        if not f.s('note'):
            raise HSEError('Give the reason.', 'note')
        d.status = 'draft'
        event('document', d.pk, f'Returned by {user_name(request.user)}: {f.s("note")}', request.user)
    else:
        d.status, d.approved_by, d.approved_at = 'approved', request.user, now()
        event('document', d.pk, f'Approved by {user_name(request.user)}', request.user)
    d.save()


def doc_publish(d, request, f):
    A.need(A.any_role(request, 'hse_manager', 'hse_officer'), 'Only HSE publishes documents.')
    if d.status != 'approved':
        raise HSEError('Only an approved document can be published.')
    d.effective_date = f.dt('effective_date') or d.effective_date or today()
    d.review_date = d.review_date or add_months(d.effective_date, 24)
    d.status = 'published'
    d.save()
    for old in HSEDocument.objects.filter(number=d.number, status='published').exclude(pk=d.pk):
        old.status = 'superseded'
        old.save(update_fields=['status'])
        event('document', old.pk, f'Superseded by revision {d.revision}', request.user)
    event('document', d.pk, f'Published, effective {d.effective_date}', request.user)


def doc_revise(d, request, f):
    A.need(A.any_role(request, 'hse_manager', 'hse_officer'), 'Only HSE revises documents.')
    if d.status != 'published':
        raise HSEError('Only the published revision can be revised.')
    if HSEDocument.objects.filter(number=d.number, revision__gt=d.revision).exclude(status__in=('archived',)).exists():
        raise HSEError('A newer revision is already in progress.')
    new = HSEDocument.objects.create(number=d.number, revision=d.revision + 1, title=d.title, doc_type=d.doc_type, site=d.site,
                                     summary=f.s('summary') or '', owner=d.owner, approver=d.approver, restricted=d.restricted,
                                     requires_ack=d.requires_ack, previous=d, created_by=request.user)
    event('document', new.pk, f'Revision {new.revision} started from rev {d.revision}', request.user)
    return new


def doc_ack(d, request, f):
    me = A.my_emp(request)
    if me is None:
        raise HSEError('Only employees acknowledge documents.')
    if d.status != 'published':
        raise HSEError('Only a published document can be acknowledged.')
    DocumentAck.objects.get_or_create(document=d, employee=me)
    close_todos('document', d.pk, 'Read')


def doc_archive(d, request, f):
    A.need(A.has_role(request, 'hse_manager'), 'Only the HSE manager archives documents.')
    d.status = 'archived'
    d.save()
    event('document', d.pk, f'Archived: {f.s("note")}', request.user)


def doc_due():
    n = 0
    for d in HSEDocument.objects.filter(status='published', review_date__lt=today()):
        tell_emp(d.owner, f'{d.number}: document review due', f'{d.title} rev {d.revision} – review was due {d.review_date}', 'document', d.pk, d.number,
                 PAGE + f'r/documents/{d.pk}', f'Review document {d.number}')
        n += 1
    return n


def competency_expiring():
    t = today()
    n = 0
    for c in Competency.objects.filter(expires_on__range=(t, t + timedelta(days=30))).select_related('employee', 'ctype'):
        tell_emp(c.employee, f'Your {c.ctype.name} expires on {c.expires_on}', 'Arrange the renewal training.', 'competency', c.pk, c.ctype.name, PAGE)
        n += 1
    return n


# ------------------------------------------------------------------ BBS
def observation_escalate(o, request, f):
    A.need(A.is_staff(request), 'Only HSE escalates observations.')
    if o.escalated_incident_id:
        raise HSEError('Already escalated.')
    from .models import IncidentCategory
    cat = IncidentCategory.objects.filter(kind='near_miss', is_active=True).first()
    i = Incident.objects.create(number=next_number('incident'), category=cat, title=f'From observation {o.number}: {o.get_category_display()}',
                                description=o.description, occurred_at=o.reported_at, reported_by=o.reported_by, site=o.site, area=o.area,
                                location=o.location, immediate_actions=o.immediate_action, potential_severity=4 if o.observed_risk == 'high' else 3)
    i.high_potential = i.potential_severity >= 4
    i.save()
    o.escalated_incident, o.status = i, 'actioned'
    o.save()
    event('observation', o.pk, f'Escalated to incident {i.number}', request.user)
    event('incident', i.pk, f'Raised from observation {o.number} by {user_name(request.user)}', request.user)
    return i


def daily_all():
    return {'moc_temporary': moc_overdue_temporary(), 'health_due': health_due(), 'env_due': env_due(), 'chemicals_due': chem_due(),
            'compliance': compliance_due(), 'documents_due': doc_due(), 'competency_expiring': competency_expiring(),
            'kpis': kpi_calculate() if today().day == 1 else 0, 'links': _links_daily()}


def _links_daily():
    try:
        from . import links as L
        return L.daily()
    except Exception:
        log.exception('HSE links daily failed')
        return {}
