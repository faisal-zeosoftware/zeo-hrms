"""HSE business rules: numbering, history, notifications, risk scoring, approvals, permit control, incident workflow,
inspections and the central action register."""
import logging
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from . import access as A
from .models import (Action, ApprovalRule, ApprovalStep, FormTemplate, GasTest, HSEEvent, HSESettings, Incident, Inspection,
                     Isolation, Permit, PermitType, RiskAssessment, Sequence)

log = logging.getLogger(__name__)
D0 = Decimal('0')


class HSEError(Exception):
    def __init__(self, message, field=None, status=400, data=None):
        super().__init__(message)
        self.message, self.field, self.status, self.data = message, field, status, data


def now():
    return timezone.now()


def today():
    """Company date (Asia/Dubai unless the Projects app has another time zone)."""
    tz = 'Asia/Dubai'
    try:
        from ZeoProjects.models import ProjectsSettings
        tz = ProjectsSettings.get().timezone or tz
    except Exception:
        pass
    try:
        from zoneinfo import ZoneInfo
        return timezone.now().astimezone(ZoneInfo(tz)).date()
    except Exception:
        return timezone.now().date()


def settings():
    return HSESettings.get()


DEFAULT_PREFIX = {'moc': 'MOC', 'health': 'OH', 'audit': 'AUD', 'nc': 'NC', 'review': 'MR', 'ergo': 'ERG', 'waste': 'WST', 'chemical': 'CHM',
                  'claim': 'CLM', 'hygiene': 'IH', 'document': 'DOC'}
PREFIX = {'incident': 'incident_prefix', 'permit': 'permit_prefix', 'risk': 'risk_prefix', 'action': 'action_prefix',
          'inspection': 'inspection_prefix', 'observation': 'observation_prefix', 'toolbox': 'toolbox_prefix'}


def next_number(kind):
    s = settings()
    with transaction.atomic():
        seq, _ = Sequence.objects.select_for_update().get_or_create(kind=kind)
        seq.last += 1
        seq.save()
    prefix = getattr(s, PREFIX[kind]) if kind in PREFIX else DEFAULT_PREFIX.get(kind, kind.upper()[:4])
    return f'{prefix}-{seq.last:05d}'


def event(model, obj_id, text, user=None):
    HSEEvent.objects.create(model=model, object_id=obj_id, text=text[:4000], user=user if (user and user.is_authenticated) else None)


def user_name(u):
    if u is None:
        return ''
    try:
        from EmpManagement.models import emp_master
        e = emp_master.objects.filter(users=u).first()
        if e:
            return emp_name(e)
    except Exception:
        pass
    return (u.get_full_name() if hasattr(u, 'get_full_name') else '') or u.username


def emp_name(e):
    if e is None:
        return ''
    return ' '.join(x for x in (e.emp_first_name, e.emp_last_name) if x and str(x).strip()) or e.emp_code


# ------------------------------------------------------------------ notifications (e-mail + My To-do)
def send_mail(to, subject, body):
    to = [x for x in dict.fromkeys(t.strip() for t in (to or []) if t and '@' in t)]
    if not to:
        return False
    try:
        from django.conf import settings as dj
        from django.core.mail import EmailMessage, get_connection
        conn, sender = None, dj.DEFAULT_FROM_EMAIL
        try:
            from EmpManagement.models import EmailConfiguration
            cfg = EmailConfiguration.objects.filter(is_active=True).first()
            if cfg and cfg.email_host_user:
                conn = get_connection(host=cfg.email_host, port=cfg.email_port, username=cfg.email_host_user,
                                      password=cfg.email_host_password, use_tls=cfg.email_use_tls, fail_silently=False)
                sender = cfg.email_host_user
        except Exception:
            conn = None
        EmailMessage(subject=subject[:200], body=body, from_email=sender, to=to, connection=conn).send(fail_silently=False)
        return True
    except Exception:
        log.exception('HSE e-mail to %s failed', to)
        return False


def todo(user, summary, model, obj_id, label, page, note=''):
    if user is None:
        return
    try:
        from Chatter.models import Activity
        key = f'ZeoHSE.{model}'
        if Activity.objects.filter(model=key, object_id=str(obj_id), assigned_to=user, summary=summary[:255], state='open').exists():
            return
        Activity.objects.create(model=key, object_id=str(obj_id), record_label=label[:255], page=page[:255], activity_type='todo',
                                summary=summary[:255], note=note, due_date=today(), assigned_to=user)
    except Exception:
        log.exception('HSE to-do failed')


def close_todos(model, obj_id, startswith=None):
    try:
        from Chatter.models import Activity
        qs = Activity.objects.filter(model=f'ZeoHSE.{model}', object_id=str(obj_id), state='open')
        if startswith:
            qs = qs.filter(summary__startswith=startswith)
        qs.update(state='done', done_at=now())
    except Exception:
        log.exception('HSE to-do close failed')


def users_with_role(role):
    from django.contrib.auth.models import Group
    from tenant_users.tenants.models import UserTenantPermissions
    groups = Group.objects.filter(permissions__codename=A.code_of(role), permissions__content_type__app_label='ZeoHSE')
    out = [u.profile for u in _company_perms().filter(groups__in=groups).select_related('profile').distinct()
           if u.profile and u.profile.is_active]
    if not out:
        out = [u.profile for u in _company_perms().filter(is_superuser=True).select_related('profile')
               if u.profile and u.profile.is_active]
    return out


def tell_users(users, subject, text, model, obj_id, label, page, summary=None):
    seen = set()
    for u in users:
        if u is None or u.pk in seen:
            continue
        seen.add(u.pk)
        send_mail([u.email], subject, text)
        if summary:
            todo(u, summary, model, obj_id, label, page, text)


def tell_emp(e, subject, text, model, obj_id, label, page, summary=None):
    if e is None:
        return
    send_mail([e.emp_company_email or e.emp_personal_email or (getattr(e.users, 'email', '') if e.users_id else '')], subject, text)
    if summary and e.users_id:
        todo(e.users, summary, model, obj_id, label, page, text)


PAGE = '/main-sidebar/zeo-hse/'


# ------------------------------------------------------------------ risk matrix
def band_of(score):
    for b in settings().risk_bands or []:
        if b['min'] <= score <= b['max']:
            return b
    return {'code': 'unknown', 'label': '–', 'color': '#64748b', 'block': False}


def risk_summary(ra):
    lines = list(ra.lines.all())
    if not lines:
        return {'max_initial': 0, 'max_residual': 0, 'band': band_of(0), 'initial_band': band_of(0), 'count': 0}
    mi = max(l.likelihood * l.severity for l in lines)
    mr = max(l.residual_likelihood * l.residual_severity for l in lines)
    return {'max_initial': mi, 'max_residual': mr, 'band': band_of(mr), 'initial_band': band_of(mi), 'count': len(lines)}


# ------------------------------------------------------------------ approvals
def make_steps(model, obj_id, steps):
    ApprovalStep.objects.filter(model=model, object_id=obj_id).delete()
    for i, s in enumerate(steps or [], 1):
        ApprovalStep.objects.create(model=model, object_id=obj_id, sequence=i, role=s['role'], label=s.get('label') or A.ROLE_LABEL.get(s['role'], s['role']))


def current_step(model, obj_id):
    return ApprovalStep.objects.filter(model=model, object_id=obj_id, status='pending').order_by('sequence').first()


def steps_of(model, obj_id):
    return list(ApprovalStep.objects.filter(model=model, object_id=obj_id).select_related('decided_by'))


def approve_step(request, model, obj_id, approve, note='', requester_id=None):
    """Decide the current step. Separation of duties: the requester cannot approve their own request."""
    st = current_step(model, obj_id)
    if st is None:
        raise HSEError('Nothing is waiting for approval.')
    A.need(A.has_role(request, st.role), f'This step needs the role "{st.label}".')
    if requester_id and requester_id == request.user.pk and not A.is_admin(request):
        raise HSEError('You cannot approve your own request (separation of duties).')
    if not approve and not (note or '').strip():
        raise HSEError('Give a reason for the rejection.', 'note')
    st.status = 'approved' if approve else 'rejected'
    st.decided_by, st.decided_at, st.note = request.user, now(), (note or '')[:255]
    st.save()
    return st, current_step(model, obj_id)


def notify_step(model, obj_id, label, page, text):
    st = current_step(model, obj_id)
    if st:
        tell_users(users_with_role(st.role), f'{label}: your approval is needed ({st.label})', text, model, obj_id, label, page,
                   f'Approve {label}')


# ------------------------------------------------------------------ risk assessments
def submit_risk(ra, request):
    if ra.status not in ('draft', 'rejected'):
        raise HSEError('Only a draft can be submitted.')
    sm = risk_summary(ra)
    if not sm['count']:
        raise HSEError('Add at least one hazard line.')
    if sm['band'].get('block'):
        raise HSEError(f'The residual risk is {sm["band"]["label"]} – the work is not allowed. Add controls to reduce it first.')
    rule = ApprovalRule.objects.filter(subject='risk', key=sm['band']['code'], is_active=True).first()
    steps = rule.steps if rule else [{'role': 'hse_officer', 'label': 'HSE officer'}]
    make_steps('risk', ra.pk, steps)
    ra.status = 'submitted'
    ra.save()
    event('risk', ra.pk, f'Submitted – residual risk {sm["band"]["label"]} ({sm["max_residual"]})', request.user)
    notify_step('risk', ra.pk, ra.number, PAGE + f'risks/{ra.pk}', f'{ra.number} {ra.title}\nResidual risk: {sm["band"]["label"]}')
    return ra


def decide_risk(ra, request, approve, note=''):
    if ra.status != 'submitted':
        raise HSEError('This risk assessment is not waiting for approval.')
    st, nxt = approve_step(request, 'risk', ra.pk, approve, note, ra.created_by_id)
    close_todos('risk', ra.pk, 'Approve')
    if not approve:
        ra.status = 'rejected'
        event('risk', ra.pk, f'Rejected by {user_name(request.user)} ({st.label}): {note}', request.user)
    elif nxt is None:
        ra.status, ra.approved_at = 'approved', now()
        if not ra.review_date:
            ra.review_date = today() + timedelta(days=365)
        if ra.previous_id:
            old = ra.previous
            if old.status == 'approved':
                old.status = 'superseded'
                old.save(update_fields=['status'])
                event('risk', old.pk, f'Superseded by {ra.number}', request.user)
            Permit.objects.filter(risk_assessment=old, status__in=('draft', 'rejected', 'submitted', 'approved', 'suspended')).update(risk_assessment=ra)
        event('risk', ra.pk, f'Approved by {user_name(request.user)} ({st.label})', request.user)
    else:
        event('risk', ra.pk, f'{st.label} approved by {user_name(request.user)}', request.user)
        notify_step('risk', ra.pk, ra.number, PAGE + f'risks/{ra.pk}', f'{ra.number} {ra.title}')
    ra.save()
    return ra


# ------------------------------------------------------------------ permits
def gas_ok(pt, t):
    limits = pt.gas_limits or {}
    vals = {'O2': t.o2, 'LEL': t.lel, 'H2S': t.h2s, 'CO': t.co}
    fails = []
    for k, (lo, hi) in limits.items():
        v = vals.get(k)
        if v is None:
            fails.append(f'{k} not measured')
        elif not (Decimal(str(lo)) <= v <= Decimal(str(hi))):
            fails.append(f'{k} {v} outside {lo}–{hi}')
    return fails


def prerequisites(p):
    """What must be true before the permit is issued (and stay true while it is active). [{key, label, ok, detail}]"""
    pt = p.permit_type
    out = []

    def add(key, label, ok, detail=''):
        out.append({'key': key, 'label': label, 'ok': bool(ok), 'detail': detail})
    s = settings()
    if s.require_review:
        add('reviewed', 'Permit type reviewed for this company', pt.review_status == 'reviewed',
            '' if pt.review_status == 'reviewed' else 'An HSE manager must review this industry-pack permit type first')
    if pt.requires_risk_assessment:
        ra = p.risk_assessment
        add('risk', 'Approved risk assessment linked', ra is not None and ra.status == 'approved',
            '' if ra else 'Link the task risk assessment')
        if ra is not None and ra.review_date and ra.review_date < today():
            add('risk_review', 'Risk assessment within its review date', False, f'Review was due {ra.review_date}')
        if ra is not None and ra.review_required:
            add('risk_review', 'Risk assessment reviewed after change / incident', False, ra.review_reason or 'Review required')
    if pt.requires_method_statement:
        add('method', 'Method statement attached or written', bool((p.method_statement or '').strip()) or
            p_has_files(p, 'method'), 'Write the method statement or attach it')
    if pt.checklist_id:
        qs = pt.checklist.questions or []
        ans = p.checklist_answers or {}
        missing = [q['text'] for q in qs if q.get('critical') and str((ans.get(q['key']) or {}).get('value', '')).lower() != 'yes']
        add('checklist', 'All critical checklist items confirmed', not missing, '; '.join(missing[:4]) + (' …' if len(missing) > 4 else ''))
    if pt.requires_gas_test:
        t = p.gas_tests.order_by('-tested_at').first()
        fails = gas_ok(pt, t) if t else ['no gas test recorded']
        fresh = True
        if t and pt.gas_retest_hours:
            fresh = (now() - t.tested_at) <= timedelta(hours=pt.gas_retest_hours)
        add('gas', 'Gas test within limits' + (f' and not older than {pt.gas_retest_hours} h' if pt.gas_retest_hours else ''),
            not fails and fresh, '; '.join(fails) if fails else ('' if fresh else 'Retest the atmosphere'))
    if pt.requires_isolation:
        iso = list(p.isolations.all())
        unverified = [i.point for i in iso if not i.verified_at]
        add('isolation', 'Isolations recorded and verified', bool(iso) and not unverified,
            'Record the isolation points' if not iso else ('Not verified: ' + ', '.join(unverified[:4]) if unverified else ''))
    if p.contractor_id and pt.requires_contractor_approval:
        c = p.contractor
        exp = [n for n, d in (('trade licence', c.licence_expiry), ('insurance', c.insurance_expiry)) if d and d < today()]
        add('contractor', 'Contractor approved with valid documents', c.status == 'approved' and not exp,
            ('Status: ' + c.get_status_display()) if c.status != 'approved' else ('Expired: ' + ', '.join(exp) if exp else ''))
    req = list(pt.required_competencies or [])
    if req:
        from .modules import competency_gaps
        gaps = competency_gaps([x for x in [p.receiver] + list(p.crew.all()) if x is not None], req, p.valid_to)
        people = (1 if p.receiver_id else 0) + p.crew.count()
        ok = bool(people) and not gaps
        detail = '; '.join(gaps[:5]) if gaps else ('' if people else 'Name the receiver and the crew')
        if gaps and not s.block_expired_competency:
            add('competency', 'Competencies valid (warning only)', True, detail)
        else:
            add('competency', 'Receiver and crew hold valid competencies', ok, detail)
    try:
        from . import links as L   # v1.17.0 – equipment from the asset register, crew leave / roster / fatigue
        out.extend(L.asset_checks(p))
        out.extend(L.crew_checks(p))
    except Exception:
        log.exception('HSE link checks failed')
    add('briefing', 'Work team briefed on the controls', p.team_briefed, 'Confirm the toolbox briefing')
    add('validity', 'Validity within the permit type maximum', (p.valid_to - p.valid_from) <= timedelta(hours=pt.max_hours) and p.valid_to > p.valid_from,
        f'Max {pt.max_hours} h')
    return out


def p_has_files(p, kind=None):
    from .models import HSEFile
    return HSEFile.objects.filter(model='permit', object_id=p.pk).exists()


def simops(p):
    """Live permits in the same area / site at the same time – conflicts by permit type rules, others as information."""
    from django.db.models import Q
    qs = Permit.objects.filter(status__in=('approved', 'active', 'suspended'), site=p.site, valid_from__lt=p.valid_to,
                               valid_to__gt=p.valid_from).exclude(pk=p.pk).select_related('permit_type', 'area')
    if p.area_id:
        qs = qs.filter(Q(area=p.area) | Q(area__isnull=True))
    out = []
    mine = set(p.permit_type.simops_conflicts or [])
    for o in qs:
        conflict = o.permit_type.code in mine or p.permit_type.code in set(o.permit_type.simops_conflicts or [])
        out.append({'id': o.pk, 'number': o.number, 'type': o.permit_type.name, 'area': o.area.name if o.area_id else '',
                    'status': o.get_status_display(), 'conflict': conflict})
    return out


PERMIT_FLOW = {'draft': ('submitted', 'cancelled'), 'rejected': ('submitted', 'cancelled'), 'submitted': ('cancelled',),
               'approved': ('active', 'cancelled'), 'active': ('suspended', 'closed'), 'suspended': ('active', 'closed', 'cancelled')}


def submit_permit(p, request):
    if p.status not in ('draft', 'rejected'):
        raise HSEError('Only a draft permit can be submitted.')
    pt = p.permit_type
    if settings().require_review and pt.review_status != 'reviewed':
        raise HSEError('This permit type comes from the industry pack and has not been reviewed yet. Ask the HSE manager.')
    if p.valid_to <= p.valid_from:
        raise HSEError('The end of the validity is before the start.', 'valid_to')
    if (p.valid_to - p.valid_from) > timedelta(hours=pt.max_hours):
        raise HSEError(f'A {pt.name} permit is valid for at most {pt.max_hours} hours.', 'valid_to')
    conflicts = [x for x in simops(p) if x['conflict']]
    if conflicts:
        raise HSEError('Conflicting work in the same area at the same time: ' + ', '.join(f"{x['number']} ({x['type']})" for x in conflicts) +
                       '. Agree it in the permit (SIMOPS) meeting and change the time or area.', 'valid_from')
    if pt.required_competencies:
        from . import links as L   # v1.17.0 – gaps become training needs in HR Learning
        from .modules import competency_gaps
        if competency_gaps([x for x in [p.receiver] + list(p.crew.all()) if x is not None], pt.required_competencies, p.valid_to):
            L.needs_for_gaps([x for x in [p.receiver] + list(p.crew.all()) if x is not None], pt.required_competencies, p.valid_to)
    make_steps('permit', p.pk, pt.approval_steps or [{'role': 'permit_issuer', 'label': 'Permit issuer'}])
    p.status = 'submitted'
    p.save()
    event('permit', p.pk, f'Submitted by {user_name(request.user)}', request.user)
    notify_step('permit', p.pk, p.number, PAGE + f'permits/{p.pk}', permit_text(p))
    return p


def permit_text(p):
    return (f'{p.number} – {p.permit_type.name}\nSite: {p.site.name}{" / " + p.area.name if p.area_id else ""} {p.location}\n'
            f'Work: {p.description[:800]}\nValid: {timezone.localtime(p.valid_from):%d %b %H:%M} – {timezone.localtime(p.valid_to):%d %b %H:%M}')


def decide_permit(p, request, approve, note=''):
    if p.status != 'submitted':
        raise HSEError('This permit is not waiting for approval.')
    st, nxt = approve_step(request, 'permit', p.pk, approve, note, p.applicant_id)
    close_todos('permit', p.pk, 'Approve')
    if not approve:
        p.status = 'rejected'
        event('permit', p.pk, f'Rejected by {user_name(request.user)} ({st.label}): {note}', request.user)
        if p.applicant:
            tell_users([p.applicant], f'{p.number} rejected', f'{st.label}: {note}\n\n' + permit_text(p), 'permit', p.pk, p.number, PAGE + f'permits/{p.pk}')
    elif nxt is None:
        p.status = 'approved'
        event('permit', p.pk, f'Approved by {user_name(request.user)} ({st.label}) – ready to issue', request.user)
        tell_users(users_with_role('permit_issuer'), f'{p.number} approved – issue it at the worksite', permit_text(p), 'permit', p.pk,
                   p.number, PAGE + f'permits/{p.pk}', f'Issue permit {p.number}')
    else:
        event('permit', p.pk, f'{st.label} approved by {user_name(request.user)}', request.user)
        notify_step('permit', p.pk, p.number, PAGE + f'permits/{p.pk}', permit_text(p))
    p.save()
    return p


def issue_permit(p, request):
    """Issue only when every prerequisite is met – a complete form alone is not enough."""
    A.need(A.has_role(request, 'permit_issuer'), 'Only a permit issuer can issue a permit.')
    if p.status not in ('approved', 'suspended'):
        raise HSEError('Only an approved (or suspended) permit can be issued.')
    missing = [x for x in prerequisites(p) if not x['ok']]
    if missing:
        raise HSEError('The permit cannot be issued: ' + '; '.join(f"{x['label']}" + (f" ({x['detail']})" if x['detail'] else '') for x in missing),
                       'prerequisites', data={'missing': missing})
    if p.valid_to <= now():
        raise HSEError('The permit validity has already ended – extend it through a new approval.')
    resumed = p.status == 'suspended'
    p.status, p.issued_by = 'active', request.user
    if not p.issued_at:
        p.issued_at = now()
    p.save()
    close_todos('permit', p.pk, 'Issue')
    event('permit', p.pk, ('Resumed' if resumed else 'Issued') + f' by {user_name(request.user)} – work may start', request.user)
    return p


def suspend_permit(p, request, reason):
    A.need(A.has_role(request, 'permit_issuer') or A.has_role(request, 'hse_officer') or A.has_role(request, 'area_authority')
           or A.has_role(request, 'supervisor'), 'You cannot suspend permits.')
    if p.status != 'active':
        raise HSEError('Only an active permit can be suspended.')
    if not (reason or '').strip():
        raise HSEError('Give the reason (stop work).', 'reason')
    p.status, p.suspension_reason = 'suspended', reason.strip()
    p.save()
    event('permit', p.pk, f'Suspended by {user_name(request.user)}: {reason}', request.user)
    tell_users([p.applicant] + users_with_role('permit_issuer'), f'{p.number} SUSPENDED – stop work', reason + '\n\n' + permit_text(p),
               'permit', p.pk, p.number, PAGE + f'permits/{p.pk}')
    from . import links as L
    L.permit_stopped(p, reason)
    return p


def close_permit(p, request, note, checks):
    """Hand-back: people out, tools removed, area safe, isolations removed under the procedure."""
    A.need(A.has_role(request, 'permit_issuer') or (p.applicant_id == request.user.pk), 'Only the applicant or a permit issuer can close.')
    if p.status not in ('active', 'suspended'):
        raise HSEError('Only an active or suspended permit can be closed.')
    need = ['people_out', 'tools_removed', 'area_safe']
    miss = [k for k in need if not (checks or {}).get(k)]
    if miss:
        raise HSEError('Confirm the hand-back: all people out, tools and materials removed, area left safe.', 'checks')
    open_iso = [i.point for i in p.isolations.filter(removed_at__isnull=True)]
    if open_iso and not (checks or {}).get('isolations_handled'):
        raise HSEError('Isolations still in place: ' + ', '.join(open_iso) + '. Remove them or confirm they stay under another certificate.', 'checks')
    p.status, p.closed_at, p.closure_note = 'closed', now(), (note or '').strip()
    p.save()
    event('permit', p.pk, f'Closed by {user_name(request.user)}. {note}', request.user)
    return p


def cancel_permit(p, request, reason):
    A.need(A.has_role(request, 'permit_issuer') or p.applicant_id == request.user.pk, 'Only the applicant or a permit issuer can cancel.')
    if p.status in ('closed', 'cancelled', 'active'):
        raise HSEError('Close an active permit instead of cancelling it.' if p.status == 'active' else 'This permit is already finished.')
    p.status = 'cancelled'
    p.save()
    close_todos('permit', p.pk)
    event('permit', p.pk, f'Cancelled by {user_name(request.user)}: {reason}', request.user)
    return p


def add_gas_test(p, request, data):
    A.need(A.has_role(request, 'gas_tester') or A.has_role(request, 'hse_officer'), 'Only an authorised gas tester can record gas tests.')
    if p.status in ('closed', 'cancelled'):
        raise HSEError('The permit is finished.')
    t = GasTest(permit=p, tested_at=data.get('tested_at') or now(), instrument=data.get('instrument', '')[:80], tester=request.user,
                note=data.get('note', '')[:255])
    for k in ('o2', 'lel', 'h2s', 'co'):
        setattr(t, k, data.get(k))
    fails = gas_ok(p.permit_type, t)
    t.passed = not fails
    t.save()
    event('permit', p.pk, f'Gas test by {user_name(request.user)}: ' + ('within limits' if t.passed else 'FAILED – ' + '; '.join(fails)), request.user)
    if not t.passed and p.status == 'active':
        suspend_permit(p, request, 'Gas test failed: ' + '; '.join(fails))
    return t


def sweep_permits(at=None):
    """Scheduled: permits past their validity become expired; active permits past validity are flagged to the issuer."""
    at = at or now()
    n = 0
    for p in Permit.objects.filter(status__in=('draft', 'submitted', 'approved'), valid_to__lt=at):
        p.status = 'expired'
        p.save(update_fields=['status'])
        event('permit', p.pk, 'Expired – validity ended before it was issued')
        n += 1
    from .models import HSEEvent
    for p in Permit.objects.filter(status='active', valid_to__lt=at).select_related('permit_type', 'site'):
        if HSEEvent.objects.filter(model='permit', object_id=p.pk, text__startswith='Validity ended').exists():
            continue    # told once
        event('permit', p.pk, 'Validity ended while active – close or revalidate')
        tell_users([p.applicant] + users_with_role('permit_issuer'), f'{p.number} validity ended – close or revalidate',
                   permit_text(p), 'permit', p.pk, p.number, PAGE + f'permits/{p.pk}', f'Close or revalidate {p.number}')
    return n


# ------------------------------------------------------------------ incidents
def classify_incident(i, request, data):
    A.need(A.has_role(request, 'hse_officer') or A.has_role(request, 'hse_manager'), 'Only HSE can classify incidents.')
    for k in ('actual_severity', 'potential_severity'):
        if k in data and data[k] is not None:
            v = int(data[k])
            if not 1 <= v <= 5:
                raise HSEError('Severity is 1 to 5.', k)
            setattr(i, k, v)
    for k in ('treatment', 'reportable', 'authority_ref', 'report_due', 'lost_days', 'restricted_days', 'category_id', 'investigator_id'):
        if k in data:
            setattr(i, k, data[k])
    i.high_potential = i.potential_severity >= 4
    # high-potential events are investigated even without injury
    i.investigation_required = i.high_potential or i.actual_severity >= 3 or i.treatment in ('lost_time', 'fatality', 'restricted', 'medical')
    if i.status == 'reported':
        i.status = 'classified'
    if i.investigation_required and i.status == 'classified':
        i.status = 'investigation'
    i.save()
    if i.investigation_required:
        from .modules import flag_risk_review
        for ra in {x for x in (i.risk_assessment, i.permit.risk_assessment if i.permit_id else None) if x is not None}:
            flag_risk_review(ra, f'Incident {i.number}', request.user)
    event('incident', i.pk, f'Classified by {user_name(request.user)}: actual {i.actual_severity}, potential {i.potential_severity}'
          + (' – HIGH POTENTIAL, investigation required' if i.high_potential else (' – investigation required' if i.investigation_required else '')), request.user)
    if i.investigator_id:
        tell_emp(i.investigator, f'{i.number}: you are the investigator', f'{i.number} {i.title}', 'incident', i.pk, i.number,
                 PAGE + f'incidents/{i.pk}', f'Investigate {i.number}')
    if i.high_potential:
        tell_users(users_with_role('hse_manager'), f'HIGH POTENTIAL incident {i.number}', f'{i.title}\n\n{i.description[:1500]}',
                   'incident', i.pk, i.number, PAGE + f'incidents/{i.pk}', f'Review high-potential incident {i.number}')
    from . import links as L   # v1.17.0 – project issue, lost days from leave
    L.incident_serious(i)
    if 'lost_days' not in data:
        L.link_leave(Incident.objects.get(pk=i.pk))
    return Incident.objects.get(pk=i.pk)


def incident_close_check(i):
    problems = []
    if i.investigation_required and not ((i.root_cause or {}).get('whys') or (i.root_cause or {}).get('underlying') or i.findings):
        problems.append('Investigation / root cause not recorded')
    open_actions = Action.objects.filter(source='incident', source_id=i.pk).exclude(status__in=('closed', 'cancelled')).count()
    if open_actions:
        problems.append(f'{open_actions} corrective action(s) not verified and closed')
    if i.investigation_required and not Action.objects.filter(source='incident', source_id=i.pk).exists():
        problems.append('No corrective action raised')
    if i.reportable and not i.authority_ref:
        problems.append('Reportable to the authority – enter the authority reference')
    return problems


def start_incident_closure(i, request):
    A.need(A.has_role(request, 'hse_officer') or A.has_role(request, 'hse_manager'), 'Only HSE can close incidents.')
    if i.status == 'closed':
        raise HSEError('Already closed.')
    probs = incident_close_check(i)
    if probs:
        raise HSEError('Not ready to close: ' + '; '.join(probs), data={'problems': probs})
    key = str(max(i.actual_severity, i.potential_severity))
    rule = ApprovalRule.objects.filter(subject='incident', key=key, is_active=True).first() or \
        ApprovalRule.objects.filter(subject='incident', key='*', is_active=True).first()
    make_steps('incident', i.pk, rule.steps if rule else [{'role': 'hse_officer', 'label': 'HSE officer'}])
    event('incident', i.pk, f'Closure requested by {user_name(request.user)}', request.user)
    notify_step('incident', i.pk, i.number, PAGE + f'incidents/{i.pk}', f'{i.number} {i.title} – closure approval')
    return i


def decide_incident_closure(i, request, approve, note=''):
    st, nxt = approve_step(request, 'incident', i.pk, approve, note)
    close_todos('incident', i.pk, 'Approve')
    if not approve:
        make_steps('incident', i.pk, [])
        event('incident', i.pk, f'Closure refused by {user_name(request.user)}: {note}', request.user)
    elif nxt is None:
        probs = incident_close_check(i)
        if probs:
            raise HSEError('Not ready to close: ' + '; '.join(probs))
        i.status, i.closed_at, i.closed_by = 'closed', now(), request.user
        i.save()
        close_todos('incident', i.pk)
        event('incident', i.pk, f'Closed – approved by {user_name(request.user)} ({st.label})', request.user)
    else:
        event('incident', i.pk, f'{st.label} approved closure', request.user)
        notify_step('incident', i.pk, i.number, PAGE + f'incidents/{i.pk}', f'{i.number} {i.title} – closure approval')
    return i


# ------------------------------------------------------------------ actions (CAPA)
def create_action(*, source, source_id=None, source_ref='', title, description='', owner=None, due_date, priority='medium',
                  site=None, verifier=None, user=None, kind='corrective'):
    if not (title or '').strip():
        raise HSEError('The action needs a title.', 'title')
    if owner is None:
        raise HSEError('Choose the action owner.', 'owner')
    if due_date is None:
        raise HSEError('The due date is required.', 'due_date')
    a = Action.objects.create(number=next_number('action'), source=source, source_id=source_id, source_ref=source_ref, title=title.strip()[:255],
                              description=description or '', owner=owner, due_date=due_date, priority=priority, site=site, verifier=verifier,
                              created_by=user if (user and user.is_authenticated) else None, kind=kind)
    event('action', a.pk, f'Raised from {a.get_source_display()} {source_ref}'.strip(), user)
    tell_emp(owner, f'{a.number}: action assigned to you (due {due_date})', f'{a.title}\n\n{a.description}', 'action', a.pk, a.number,
             PAGE + f'actions?open={a.pk}', f'Complete action {a.number}')
    if source == 'incident' and source_id:
        Incident.objects.filter(pk=source_id, status__in=('classified', 'investigation')).update(status='actions')
    return a


def action_done(a, request, evidence):
    me = A.my_emp(request)
    A.need((me and a.owner_id == me.pk) or A.is_staff(request), 'Only the action owner or HSE can complete it.')
    if a.status not in ('open', 'in_progress', 'rejected'):
        raise HSEError('This action is not open.')
    if not (evidence or '').strip():
        raise HSEError('Describe what was done (evidence).', 'evidence')
    a.status, a.evidence, a.done_at = 'done', evidence.strip(), now()
    a.save()
    close_todos('action', a.pk)
    event('action', a.pk, f'Done – {evidence.strip()[:300]}', request.user)
    if a.verifier_id:
        tell_emp(a.verifier, f'{a.number}: verify the action', f'{a.title}\nEvidence: {a.evidence}', 'action', a.pk, a.number,
                 PAGE + f'actions?open={a.pk}', f'Verify action {a.number}')
    else:
        tell_users(users_with_role('hse_officer'), f'{a.number}: verify the action', f'{a.title}\nEvidence: {a.evidence}', 'action', a.pk,
                   a.number, PAGE + f'actions?open={a.pk}', f'Verify action {a.number}')
    return a


def action_verify(a, request, effective, note=''):
    me = A.my_emp(request)
    A.need((me and a.verifier_id == me.pk) or A.has_role(request, 'hse_officer') or A.has_role(request, 'hse_manager'),
           'Only the verifier or HSE can verify.')
    if a.status != 'done':
        raise HSEError('The action is not done yet.')
    if me and a.owner_id == me.pk and not A.is_admin(request):
        raise HSEError('The action owner cannot verify their own action.')
    if not effective and not (note or '').strip():
        raise HSEError('Say why it is not effective.', 'note')
    a.status = 'closed' if effective else 'rejected'
    a.verified_at, a.verification_note = now(), (note or '').strip()
    a.save()
    close_todos('action', a.pk)
    event('action', a.pk, ('Verified effective and closed' if effective else 'Not effective – reopened') + (f': {note}' if note else ''), request.user)
    if not effective:
        tell_emp(a.owner, f'{a.number}: reopened – not effective', note, 'action', a.pk, a.number, PAGE + f'actions?open={a.pk}',
                 f'Complete action {a.number}')
    return a


def action_reminders():
    """Daily: overdue actions to the owner (and HSE manager), every N days."""
    s = settings()
    t = today()
    n = 0
    for a in Action.objects.filter(status__in=('open', 'in_progress', 'rejected'), due_date__lt=t).select_related('owner'):
        if a.reminded_on and (t - a.reminded_on).days < max(1, s.action_reminder_days):
            continue
        late = (t - a.due_date).days
        tell_emp(a.owner, f'{a.number} is {late} day(s) overdue', a.title, 'action', a.pk, a.number, PAGE + f'actions?open={a.pk}',
                 f'Overdue action {a.number}')
        if late >= 7:
            tell_users(users_with_role('hse_manager'), f'{a.number} overdue {late} days – {a.title}', a.title, 'action', a.pk, a.number,
                       PAGE + f'actions?open={a.pk}')
        a.reminded_on = t
        a.save(update_fields=['reminded_on'])
        n += 1
    return n


# ------------------------------------------------------------------ inspections
def complete_inspection(ins, request, raise_actions=True, due_days=7):
    tpl = ins.template
    qs = [q for q in (tpl.questions or []) if q.get('type', 'yesno') == 'yesno']
    ans = ins.answers or {}
    missing = [q['text'] for q in tpl.questions or [] if q.get('required', True) and not str((ans.get(q['key']) or {}).get('value', '')).strip()]
    if missing:
        raise HSEError('Answer every question: ' + '; '.join(missing[:3]) + (' …' if len(missing) > 3 else ''))
    scored = [q for q in qs if (ans.get(q['key']) or {}).get('value') in ('yes', 'no')]
    yes = [q for q in scored if ans[q['key']]['value'] == 'yes']
    ins.score = Decimal(len(yes) * 100) / Decimal(len(scored)) if scored else None
    ins.status = 'completed'
    ins.save()
    made = 0
    if raise_actions:
        owner = ins.site.manager if ins.site_id and ins.site.manager_id else ins.inspector
        for q in scored:
            if ans[q['key']]['value'] == 'no':
                note = (ans[q['key']] or {}).get('note', '')
                create_action(source='inspection', source_id=ins.pk, source_ref=ins.number, title=f'{q["text"]} – not compliant'[:255],
                              description=f'{tpl.name} at {ins.site.name if ins.site_id else ""} {ins.subject}. {note}'.strip(),
                              owner=owner, due_date=today() + timedelta(days=1 if q.get('critical') else due_days),
                              priority='high' if q.get('critical') else 'medium', site=ins.site, user=request.user)
                made += 1
    event('inspection', ins.pk, f'Completed – score {ins.score:.0f} %' + (f', {made} action(s) raised' if made else '') if ins.score is not None else 'Completed', request.user)
    return ins, made


def usable(qs, request=None):
    """Pack items must be reviewed before use (when the company setting is on)."""
    if settings().require_review:
        qs = qs.filter(review_status='reviewed')
    return qs.filter(is_active=True)



def _company_perms():   # v1.22.0: only the users of the open company (see AccessControl.access.company_perms)
    from AccessControl.access import company_perms
    return company_perms()
