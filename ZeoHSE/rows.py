"""JSON rows of HSE records."""
from decimal import Decimal

from django.utils import timezone

from . import access as A
from . import services as S
from .models import Action, ApprovalStep, HSEEvent, HSEFile


def d(v):
    if v is None:
        return None
    if hasattr(v, 'tzinfo') and getattr(v, 'hour', None) is not None and timezone.is_aware(v):
        return timezone.localtime(v).isoformat()
    return v.isoformat()


def num(v):
    return None if v is None else float(v)


def emp(e):
    return {'id': e.pk, 'name': S.emp_name(e), 'code': e.emp_code} if e else None


def user(u):
    return {'id': u.pk, 'name': S.user_name(u)} if u else None


def steps(model, oid):
    return [{'sequence': s.sequence, 'role': s.role, 'label': s.label, 'status': s.status, 'by': S.user_name(s.decided_by),
             'at': d(s.decided_at), 'note': s.note} for s in ApprovalStep.objects.filter(model=model, object_id=oid).select_related('decided_by')]


def history(model, oid):
    return [{'text': e.text, 'by': S.user_name(e.user) if e.user_id else 'System', 'at': d(e.at)}
            for e in HSEEvent.objects.filter(model=model, object_id=oid).select_related('user')[:200]]


def files(model, oid):
    return [{'id': f.pk, 'name': f.name, 'size': f.size, 'note': f.note, 'by': S.user_name(f.uploaded_by), 'at': d(f.uploaded_at)}
            for f in HSEFile.objects.filter(model=model, object_id=oid).select_related('uploaded_by')]


def pack_fields(x):
    return {'pack': x.pack, 'review_status': x.review_status, 'reviewed_by': S.user_name(x.reviewed_by) if x.reviewed_by_id else '',
            'reviewed_at': d(x.reviewed_at), 'is_active': x.is_active}


def site(s):
    return {'id': s.pk, 'code': s.code, 'name': s.name, 'kind': s.kind, 'address': s.address, 'manager': emp(s.manager),
            'branch': s.branch_id, 'branch_name': s.branch.branch_name if s.branch_id else '', 'is_active': s.is_active,
            'project_id': s.project_id, 'project_name': _L().label('projects', s.project_id) if s.project_id else '',
            'cost_center_id': s.cost_center_id, 'cost_center_name': _L().label('cost-centers', s.cost_center_id) if s.cost_center_id else '',
            'areas': [{'id': a.pk, 'name': a.name, 'is_active': a.is_active} for a in s.areas.all()]}


def contractor(c):
    t = S.today()
    exp = [n for n, v in (('trade licence', c.licence_expiry), ('insurance', c.insurance_expiry)) if v and v < t]
    return {'id': c.pk, 'code': c.code, 'name': c.name, 'trade': c.trade, 'contact_name': c.contact_name, 'phone': c.phone, 'email': c.email,
            'trade_licence_no': c.trade_licence_no, 'licence_expiry': d(c.licence_expiry), 'insurance_expiry': d(c.insurance_expiry),
            'status': c.status, 'status_label': c.get_status_display(), 'notes': c.notes, 'is_active': c.is_active, 'expired': exp,
            'partner': c.partner_id, 'partner_number': c.partner.number if c.partner_id else ''}   # v1.42.0 business partner


def template(t, full=True):
    r = {'id': t.pk, 'kind': t.kind, 'kind_label': t.get_kind_display(), 'code': t.code, 'name': t.name, 'description': t.description,
         'frequency': t.frequency, 'version': t.version, 'question_count': len(t.questions or []), **pack_fields(t)}
    if full:
        r['questions'] = t.questions or []
    return r


def permit_type(p):
    return {'id': p.pk, 'code': p.code, 'name': p.name, 'description': p.description, 'color': p.color, 'max_hours': p.max_hours,
            'requires_risk_assessment': p.requires_risk_assessment, 'requires_method_statement': p.requires_method_statement,
            'requires_gas_test': p.requires_gas_test, 'requires_isolation': p.requires_isolation,
            'requires_contractor_approval': p.requires_contractor_approval, 'gas_limits': p.gas_limits, 'gas_retest_hours': p.gas_retest_hours,
            'checklist': p.checklist_id, 'checklist_name': p.checklist.name if p.checklist_id else '', 'approval_steps': p.approval_steps,
            'simops_conflicts': p.simops_conflicts, 'required_competencies': p.required_competencies or [], **pack_fields(p)}


def risk(ra, full=False):
    sm = S.risk_summary(ra)
    r = {'id': ra.pk, 'number': ra.number, 'kind': ra.kind, 'kind_label': ra.get_kind_display(), 'title': ra.title,
         'site': ra.site_id, 'site_name': ra.site.name if ra.site_id else '', 'branch_name': (ra.site.branch.branch_name if ra.site_id and ra.site.branch_id else ''), 'area': ra.area_id, 'area_name': ra.area.name if ra.area_id else '',
         'owner': emp(ra.owner), 'status': ra.status, 'status_label': ra.get_status_display(), 'review_date': d(ra.review_date),
         'review_overdue': bool(ra.review_date and ra.review_date < S.today() and ra.status == 'approved'),
         'max_residual': sm['max_residual'], 'band': sm['band'], 'max_initial': sm['max_initial'], 'initial_band': sm['initial_band'],
         'line_count': sm['count'], 'created_at': d(ra.created_at), 'review_required': ra.review_required, 'review_reason': ra.review_reason,
         'previous': {'id': ra.previous_id, 'number': ra.previous.number} if ra.previous_id else None}
    if full:
        r.update({'activity': ra.activity, 'team': ra.team, 'approved_at': d(ra.approved_at),
                  'lines': [{'id': l.pk, 'sequence': l.sequence, 'step': l.step, 'hazard': l.hazard, 'consequence': l.consequence,
                             'existing_controls': l.existing_controls, 'likelihood': l.likelihood, 'severity': l.severity,
                             'additional_controls': l.additional_controls, 'residual_likelihood': l.residual_likelihood,
                             'residual_severity': l.residual_severity, 'action_owner': l.action_owner_id,
                             'score': l.likelihood * l.severity, 'residual_score': l.residual_likelihood * l.residual_severity,
                             'band': S.band_of(l.likelihood * l.severity), 'residual_band': S.band_of(l.residual_likelihood * l.residual_severity)}
                            for l in ra.lines.all()],
                  'steps': steps('risk', ra.pk), 'history': history('risk', ra.pk), 'files': files('risk', ra.pk),
                  'permits': [{'id': p.pk, 'number': p.number, 'status': p.get_status_display()} for p in ra.permits.all()[:20]]})
    return r


def permit(p, full=False, request=None):
    r = {'id': p.pk, 'number': p.number, 'type': p.permit_type_id, 'type_name': p.permit_type.name, 'type_code': p.permit_type.code,
         'color': p.permit_type.color, 'site': p.site_id, 'site_name': p.site.name, 'branch_name': (p.site.branch.branch_name if p.site.branch_id else ''), 'area': p.area_id, 'area_name': p.area.name if p.area_id else '',
         'location': p.location, 'description': p.description, 'contractor': p.contractor_id,
         'contractor_name': p.contractor.name if p.contractor_id else '', 'applicant': user(p.applicant), 'receiver': emp(p.receiver),
         'crew_size': p.crew_size, 'valid_from': d(p.valid_from), 'valid_to': d(p.valid_to), 'status': p.status,
         'status_label': p.get_status_display(), 'issued_at': d(p.issued_at), 'closed_at': d(p.closed_at),
         'overrun': p.status == 'active' and p.valid_to < S.now()}
    if full:
        st = S.current_step('permit', p.pk)
        r.update({'asset_id': p.asset_id, 'asset': _L().asset_info(p.asset_id), 'project_name': _L().project_name(p.site.project_id) if p.site.project_id else '',
                  'equipment': p.equipment, 'risk_assessment': p.risk_assessment_id,
                  'risk_number': p.risk_assessment.number if p.risk_assessment_id else '',
                  'method_statement': p.method_statement, 'checklist_answers': p.checklist_answers or {}, 'precautions': p.precautions,
                  'team_briefed': p.team_briefed, 'closure_note': p.closure_note, 'suspension_reason': p.suspension_reason,
                  'crew': [emp(e) for e in p.crew.all()],
                  'permit_type': permit_type(p.permit_type),
                  'checklist': p.permit_type.checklist.questions if p.permit_type.checklist_id else [],
                  'gas_tests': [{'id': t.pk, 'tested_at': d(t.tested_at), 'o2': num(t.o2), 'lel': num(t.lel), 'h2s': num(t.h2s), 'co': num(t.co),
                                 'instrument': t.instrument, 'tester': S.user_name(t.tester), 'passed': t.passed, 'note': t.note}
                                for t in p.gas_tests.all()],
                  'isolations': [{'id': i.pk, 'point': i.point, 'kind': i.kind, 'kind_label': i.get_kind_display(), 'method': i.method,
                                  'lock_no': i.lock_no, 'isolated_by': S.user_name(i.isolated_by), 'isolated_at': d(i.isolated_at),
                                  'verified_by': S.user_name(i.verified_by), 'verified_at': d(i.verified_at), 'removed_at': d(i.removed_at)}
                                 for i in p.isolations.all()],
                  'prerequisites': S.prerequisites(p), 'simops': S.simops(p), 'steps': steps('permit', p.pk),
                  'current_step': {'role': st.role, 'label': st.label} if st else None,
                  'history': history('permit', p.pk), 'files': files('permit', p.pk)})
        if request is not None:
            me = request.user.pk
            r['can'] = {'edit': p.status in ('draft', 'rejected') and (p.applicant_id == me or A.is_staff(request)),
                        'checklist': p.status in ('draft', 'rejected', 'submitted', 'approved', 'suspended'),
                        'decide': bool(st and A.has_role(request, st.role) and p.status == 'submitted'),
                        'issue': A.has_role(request, 'permit_issuer') and p.status in ('approved', 'suspended'),
                        'gas': (A.has_role(request, 'gas_tester') or A.has_role(request, 'hse_officer')) and p.status not in ('closed', 'cancelled', 'expired'),
                        'isolate': (A.has_role(request, 'area_authority') or A.has_role(request, 'permit_issuer') or A.has_role(request, 'supervisor'))
                        and p.status not in ('closed', 'cancelled', 'expired'),
                        'suspend': p.status == 'active' and any(A.has_role(request, k) for k in ('permit_issuer', 'hse_officer', 'area_authority', 'supervisor')),
                        'close': p.status in ('active', 'suspended') and (A.has_role(request, 'permit_issuer') or p.applicant_id == me),
                        'cancel': p.status in ('draft', 'rejected', 'submitted', 'approved', 'suspended') and (A.has_role(request, 'permit_issuer') or p.applicant_id == me)}
    return r


def incident(i, full=False, request=None):
    health = request is None or A.can_see_health(request) or (i.reported_by_id == request.user.pk)
    restricted = bool(i.category_id and i.category.restricted)
    hide = restricted and not health
    r = {'id': i.pk, 'number': i.number, 'title': i.title, 'category': i.category_id, 'category_name': i.category.name if i.category_id else '',
         'kind': i.category.kind if i.category_id else '', 'restricted': restricted,
         'occurred_at': d(i.occurred_at), 'reported_at': d(i.reported_at), 'reported_by': user(i.reported_by),
         'site': i.site_id, 'site_name': i.site.name if i.site_id else '', 'branch_name': (i.site.branch.branch_name if i.site_id and i.site.branch_id else ''), 'area': i.area_id, 'area_name': i.area.name if i.area_id else '',
         'actual_severity': i.actual_severity, 'potential_severity': i.potential_severity, 'high_potential': i.high_potential,
         'treatment': None if hide else i.treatment, 'treatment_label': '(restricted)' if hide else i.get_treatment_display(),
         'status': i.status, 'status_label': i.get_status_display(), 'investigation_required': i.investigation_required,
         'lost_days': i.lost_days, 'reportable': i.reportable}
    if full:
        r.update({'description': i.description, 'location': i.location, 'immediate_actions': i.immediate_actions, 'witnesses': i.witnesses,
                  'injured_employee': None if hide else emp(i.injured_employee), 'injured_name': '' if hide else i.injured_name,
                  'body_part': '' if hide else i.body_part, 'estimated_loss': num(i.estimated_loss),
                  'investigator': emp(i.investigator), 'root_cause': i.root_cause or {}, 'findings': i.findings,
                  'lessons_learned': i.lessons_learned, 'restricted_days': i.restricted_days, 'authority_ref': i.authority_ref,
                  'report_due': d(i.report_due), 'permit': i.permit_id, 'permit_number': i.permit.number if i.permit_id else '',
                  'contractor': i.contractor_id, 'closed_at': d(i.closed_at), 'closed_by': S.user_name(i.closed_by),
                  'steps': steps('incident', i.pk), 'history': history('incident', i.pk), 'files': files('incident', i.pk),
                  'close_problems': S.incident_close_check(i) if i.status != 'closed' else [],
                  'risk_assessment': i.risk_assessment_id, 'risk_number': i.risk_assessment.number if i.risk_assessment_id else '',
                  'actions_list': [action(a) for a in Action.objects.filter(source='incident', source_id=i.pk).select_related('owner', 'verifier', 'site')],
                  'claims': [{'id': c.pk, 'number': c.number, 'status': c.get_status_display()} for c in i.claims.all()] if request is not None and
                  (A.has_role(request, 'claims_officer') or A.is_manager(request)) else []})
        if request is not None:
            st = S.current_step('incident', i.pk)
            me = A.my_emp(request)
            hse = A.has_role(request, 'hse_officer') or A.has_role(request, 'hse_manager')
            r['can'] = {'classify': hse and i.status != 'closed', 'investigate': (hse or bool(me and i.investigator_id == me.pk)) and i.status != 'closed',
                        'close': hse and i.status != 'closed' and st is None, 'decide': bool(st and A.has_role(request, st.role)),
                        'correct': A.is_manager(request) and i.status == 'closed', 'actions': A.is_staff(request) and i.status != 'closed'}
        L = _L()   # v1.17.0 links with other apps
        r['links'] = {'on': L.flags(), 'asset_id': i.asset_id, 'asset': L.asset_info(i.asset_id), 'asset_damage': damage_info_safe(i),
                      'leave': [] if hide or not A.is_staff(request) else L.leave_info(i), 'lost_days_from_leave': bool(i.leave_ids), 'alert': L.alert_info(i),
                      'project': L.project_name(i.site.project_id) if i.site_id and i.site.project_id else '',
                      'project_issue': bool(i.project_risk_id), 'asset_plus': L.model('AssetPlus', 'AssetDamage') is not None}
    return r


def _L():
    from . import links
    return links


def damage_info_safe(i):
    try:
        return _L().damage_info(i)
    except Exception:
        return None


def inspection(x, full=False):
    r = {'id': x.pk, 'number': x.number, 'template': x.template_id, 'template_name': x.template.name, 'site': x.site_id,
         'site_name': x.site.name if x.site_id else '', 'branch_name': (x.site.branch.branch_name if x.site_id and x.site.branch_id else ''), 'area': x.area_id, 'area_name': x.area.name if x.area_id else '', 'subject': x.subject,
         'inspector': emp(x.inspector), 'date': d(x.date), 'score': num(x.score), 'status': x.status, 'status_label': x.get_status_display(),
         'asset_id': x.asset_id}
    if full:
        r['asset'] = _L().asset_info(x.asset_id)
        r.update({'answers': x.answers or {}, 'questions': x.template.questions or [], 'history': history('inspection', x.pk),
                  'files': files('inspection', x.pk)})
    return r


def observation(o, request=None):
    show = not o.confidential or request is None or A.is_manager(request) or o.reported_by_id == request.user.pk
    return {'id': o.pk, 'number': o.number, 'kind': o.kind, 'kind_label': o.get_kind_display(), 'description': o.description,
            'site': o.site_id, 'site_name': o.site.name if o.site_id else '', 'branch_name': (o.site.branch.branch_name if o.site_id and o.site.branch_id else ''), 'area_name': o.area.name if o.area_id else '',
            'location': o.location, 'immediate_action': o.immediate_action,
            'reported_by': user(o.reported_by) if show else {'id': None, 'name': 'Confidential'},
            'category': o.category, 'category_label': o.get_category_display(), 'task': o.task, 'observed_risk': o.observed_risk,
            'observed_risk_label': o.get_observed_risk_display(), 'confidential': o.confidential,
            'escalated_incident': {'id': o.escalated_incident_id, 'number': o.escalated_incident.number} if o.escalated_incident_id else None,
            'reported_at': d(o.reported_at), 'status': o.status, 'status_label': o.get_status_display()}


def talk(t, full=False):
    r = {'id': t.pk, 'number': t.number, 'topic': t.topic, 'template': t.template_id, 'site': t.site_id,
         'site_name': t.site.name if t.site_id else '', 'branch_name': (t.site.branch.branch_name if t.site_id and t.site.branch_id else ''), 'date': d(t.date), 'presenter': emp(t.presenter),
         'attendee_count': t.attendees.count() + len([x for x in (t.other_attendees or '').splitlines() if x.strip()])}
    if full:
        r.update({'attendees': [emp(e) for e in t.attendees.all()], 'other_attendees': t.other_attendees, 'key_points': t.key_points,
                  'files': files('toolbox', t.pk)})
    return r


def action(a, full=False):
    t = S.today()
    r = {'id': a.pk, 'number': a.number, 'source': a.source, 'source_label': a.get_source_display(), 'source_id': a.source_id,
         'source_ref': a.source_ref, 'kind': a.kind, 'title': a.title, 'description': a.description, 'site_name': a.site.name if a.site_id else '', 'branch_name': (a.site.branch.branch_name if a.site_id and a.site.branch_id else ''),
         'owner': emp(a.owner), 'verifier': emp(a.verifier), 'priority': a.priority, 'priority_label': a.get_priority_display(),
         'due_date': d(a.due_date), 'status': a.status, 'status_label': a.get_status_display(),
         'overdue': a.status in ('open', 'in_progress', 'rejected') and a.due_date < t, 'days_late': (t - a.due_date).days if a.due_date < t else 0,
         'evidence': a.evidence, 'done_at': d(a.done_at), 'verified_at': d(a.verified_at), 'verification_note': a.verification_note}
    if full:
        r.update({'history': history('action', a.pk), 'files': files('action', a.pk)})
    return r


def jsonable(v):
    if isinstance(v, dict):
        return {k: jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    if isinstance(v, Decimal):
        return float(v)
    if hasattr(v, 'isoformat'):
        return v.isoformat()
    return v
