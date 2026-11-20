"""v1.75.1 Settings › Features of HRMS (design rule #155, approved 19 Nov 2026): a NEW company sees the standard HR and payroll
screens only; every advanced feature is off until an administrator switches it on – including the approval hierarchy (a new
company's requests have no approval set-up: they are approved as they are made, until "Approval hierarchy" is switched on).

Stored in ZeoApps.CompanyOption "features:hrms" (no table of its own). A company WITHOUT that row (every company made before
1.75.1) has every feature on – nothing changes under a live company. A feature in use (records, approval chains with levels …)
stays on. The menu hides the screens of a feature that is off ("feat:hrms.<key>" in shared-ui/menu-config.ts)."""
import logging

from django.apps import apps

log = logging.getLogger(__name__)
KEY = 'features:hrms'


def _n(label, **flt):
    def f():
        try:
            return apps.get_model(label).objects.filter(**flt).count()
        except Exception:   # noqa: BLE001
            return 0
    return f


def _sum(*fs):
    return lambda: sum(f() for f in fs)


# workflow models of the request types (approval_type) – the approval hierarchy is in use when one is not "No approval"
WORKFLOWS = ('calendars.LatinEarlyApprovalWorkflow', 'PayrollManagement.AdvanceApprovalWorkflow', 'EmpManagement.ResignationApprovalWorkflow',
             'PayrollManagement.AirticketApprovalWorkflow', 'calendars.LVApprovalWorkflow', 'PayrollManagement.LoanApprovalWorkflow',
             'EmpManagement.ApprovalWorkflow', 'EmpManagement.DocumentApprovalWorkflow', 'OrganisationManager.AssetApprovalWorkflow')


def _approvals_used():
    n = 0
    for lbl in WORKFLOWS:
        try:
            n += apps.get_model(lbl).objects.exclude(approval_type='no_approval').count()
        except Exception:   # noqa: BLE001
            pass
    return n


class F:
    def __init__(self, key, label, group, help, who='', in_use=None, needs=()):
        self.key, self.label, self.group, self.help, self.who, self.in_use, self.needs = key, label, group, help, who, in_use, tuple(needs)


FEATURES = [
    F('approvals', 'Approval hierarchy', 'Requests and approvals',
      'Approval levels and chains for leave, requests, late in / early out, resignation, loans, advances, air tickets and assets; escalation. '
      'Off: requests are approved as they are made (no approval set-up); chains you gave levels of your own keep them.',
      'Companies where managers approve requests'),   # can always be switched off: the reporting-manager chains go back to "No approval"
    F('devices', 'Attendance devices and kiosks', 'Time and attendance', 'ZKTeco / JSON devices, kiosks, kiosk PINs, punch photos.',
      'Biometric devices or shared kiosks', _sum(_n('AttendancePlus.Device'), _n('AttendancePlus.Kiosk'))),
    F('mobile_punch', 'Mobile punching controls', 'Time and attendance', 'Geo fences, the punch map, office networks (IP), face register.',
      'Field staff and remote punching', _sum(_n('AttendancePlus.IPRestriction'), _n('AttendancePlus.FaceProfile'))),
    F('att_rules', 'Advanced attendance rules', 'Time and attendance', 'Recheck, round-off policy, late / early approval levels.', 'Strict time policies'),
    F('shifts', 'Shift planner', 'Time and attendance', 'Rosters, roster grid, shift patterns, overrides, availability, on-call duty, shift requests and reports.',
      'Shift-based work (retail, security, hospitality)', _n('ShiftPlanner.RosterPeriod')),
    F('adv_leave', 'Advanced leave', 'Leave', 'Compensatory leave, leave grants, leave conflicts, leave rules (sandwich, projected balance …), leave categories.',
      'Larger HR teams'),
    F('adv_payroll', 'Advanced payroll', 'Payroll', 'AI payroll auditor, pay calendars, off-cycle runs, arrears, bank and file layouts, payroll extras, accrue now – '
      'pay later, cost allocation, salary history import, re-hire, what-if, recoveries, salary splits, health check, payroll rules and controls.',
      'Multi-entity or complex payroll'),
    F('statutory', 'Statutory schemes', 'Payroll', 'GPSSA / ADPF pension, DIFC DEWS, ILOE, Emiratisation and Nafis, branch pension schemes.',
      'Nationals on payroll, DIFC entities'),
    F('variable_pay', 'Commission, retention and service charge', 'Payroll', 'Commission plans, retention bonuses, service charge shares.',
      'Sales and hospitality', _sum(_n('PayrollExtras.CommissionPlan'), _n('PayrollExtras.ServiceChargePool'))),
    F('air_tickets', 'Air tickets', 'Payroll', 'Air ticket policy, rules, requests, approvals, allocation and the self-service page.',
      'Companies giving ticket allowances', _sum(_n('PayrollManagement.AirTicketPolicy'), _n('PayrollManagement.AirTicketRequest'))),
    F('hr_assets', 'HR assets', 'People', 'Laptops, phones and vehicles issued to employees: register, allocation, returns, damage, exit clearance.',
      'Companies issuing assets', _n('OrganisationManager.Asset')),
    F('custom_forms', 'Custom forms and workflows', 'Company', 'Form designer, field help, custom modules, workflow approvals.', 'Administrators'),
    F('performance', 'Performance', 'Talent', 'Goals, check-ins, appraisals, KPIs, nine-box.', 'Companies with an appraisal cycle',
      _sum(_n('PerformanceManagement.AppraisalCycle'), _n('PerformanceManagement.GoalSheet'))),
    F('recruitment', 'Recruitment', 'Talent', 'Requisitions, job openings, candidates, careers page, interviews, offers.', 'In-house hiring',
      _sum(_n('RecruitmentManagement.JobOpening'), _n('RecruitmentManagement.Candidate'))),
    F('learning', 'Learning', 'Talent', 'Courses, sessions, nominations, certificates, training bonds.', 'Training programmes', _n('LearningManagement.Course')),
    F('hr_ops', 'HR operations', 'People', 'HR helpdesk, knowledge base, disciplinary cases, exit interviews, PRO services, document renewals, assignments and matrix.',
      'Large HR / PRO teams', _sum(_n('ZeoHR.HRTicket'), _n('ZeoHR.DisciplinaryCase'), _n('ZeoHR.ProService'))),
    F('complaints', 'Complaints and grievances', 'People', 'Complaints in self service, the grievance desk, announcement read receipts.', 'A grievance procedure'),
    F('ai', 'AI assistants', 'Company', 'Ask ZEO and the assistants (attendance, recruitment, performance, workforce analytics), external AI.', 'Companies using the AI layer'),
    F('report_builder', 'Report builder and dashboards', 'Company', 'Build your own reports and dashboards; scheduled reports.', 'Analysts'),
]
BY = {f.key: f for f in FEATURES}
PRESETS = {
    'shifts': ('Shift-based operations (retail, security, hospitality)', ('shifts', 'devices', 'mobile_punch', 'variable_pay', 'approvals')),
    'field': ('Field workforce (contracting, facilities)', ('mobile_punch', 'shifts', 'hr_assets', 'air_tickets', 'approvals')),
    'office': ('Corporate office', ('performance', 'recruitment', 'learning', 'hr_ops', 'report_builder', 'approvals')),
    'freezone': ('Free zone / DIFC entity', ('statutory', 'adv_payroll')),
    'all': ('Everything', tuple(f.key for f in FEATURES)),
}


def _opt():
    return apps.get_model('ZeoApps', 'CompanyOption')


def used(f):
    try:
        return int(f.in_use()) if f.in_use else 0
    except Exception:   # noqa: BLE001
        return 0


def saved():
    try:
        r = _opt().objects.filter(key=KEY).first()
        return dict(r.value or {}) if r is not None else None
    except Exception:   # noqa: BLE001
        return None


def state():
    """{key: on}. No saved choice (a company made before 1.75.1): every feature on. A feature in use is on."""
    s = saved()
    if s is None:
        return {f.key: True for f in FEATURES}
    return {f.key: bool(s.get(f.key, False)) or bool(used(f)) for f in FEATURES}


def on(key):
    return state().get(key, True)


def start_new_company():
    """company creation: the standard features only (every advanced one off) – called by setup_new_company"""
    _opt().objects.update_or_create(key=KEY, defaults={'value': {f.key: False for f in FEATURES}})
    try:   # Ask ZEO belongs to the AI feature
        AS = apps.get_model('AISettings', 'AISettings')
        a = AS.objects.order_by('id').first()
        if a is not None and a.use_ask:
            a.use_ask = False
            a.save(update_fields=['use_ask'])
    except Exception:   # noqa: BLE001
        log.exception('Ask ZEO default of a new company')


def save(values, user=None):
    cur = state()
    changed = []
    for k, v in values.items():
        if k not in BY or cur.get(k) == bool(v):
            continue
        if not v and used(BY[k]):
            from zeo.appkit.engine import AppError
            raise AppError(f'"{BY[k].label}" is in use ({used(BY[k])}) – it stays on.', k)
        cur[k] = bool(v)
        changed.append((k, bool(v)))
    for k, f in BY.items():   # what a feature needs comes with it
        if cur.get(k):
            for n in f.needs:
                if not cur.get(n):
                    cur[n] = True
                    changed.append((n, True))
    _opt().objects.update_or_create(key=KEY, defaults={'value': cur})
    for k, v in changed:
        if k == 'approvals':
            approvals_switched(v, user)
        if k == 'ai' and v:
            try:
                AS = apps.get_model('AISettings', 'AISettings')
                AS.objects.filter(use_ask=False).update(use_ask=True)
            except Exception:   # noqa: BLE001
                pass
    if changed:
        try:
            from Chatter.models import AuditLog   # noqa: F401 – optional
        except Exception:   # noqa: BLE001
            pass
        log.info('HRMS features changed by %s: %s', getattr(user, 'pk', None), changed)
        hist = _opt().objects.filter(key=KEY + ':log').first()
        rows = list((hist.value or {}).get('rows', [])) if hist else []
        from django.utils import timezone
        rows += [{'at': timezone.now().isoformat(), 'by': getattr(user, 'email', '') or '', 'feature': BY[k].label, 'on': v} for k, v in changed]
        _opt().objects.update_or_create(key=KEY + ':log', defaults={'value': {'rows': rows[-500:]}})
    return cur


def approvals_switched(on_, user=None):
    """Approval hierarchy on: the "No approval" chains ZEO made become reporting-manager chains (the reporting manager approves; set more
    levels in Approval levels). Off (allowed only while not in use): every chain is "No approval"."""
    from ZeoAdmin.approval_defaults import ROLE
    for lbl in WORKFLOWS:
        try:
            W = apps.get_model(lbl)
        except LookupError:
            continue
        was = (_was().get(lbl) or {}) if on_ else {}
        for w in W.objects.filter(approval_type='no_approval' if on_ else 'reporting_manager'):
            kind = was.get(str(w.pk)) or 'reporting_manager'   # the template's own chain kind comes back (multi-level, role …)
            w.approval_type = kind if on_ else 'no_approval'
            w.save(update_fields=['approval_type'])
            if on_ and kind != 'reporting_manager':
                continue
            for rel in w._meta.related_objects:   # the single level: "Reporting Manager" / "No approval"
                if rel.one_to_many and rel.related_model.__name__.lower().endswith(('level', 'levels', 'workflow')) and \
                        any(f.name == 'role' for f in rel.related_model._meta.concrete_fields):
                    rel.related_model.objects.filter(**{rel.field.name: w}).update(role=ROLE if on_ else 'No approval')


def to_no_approval():
    """a new company (no employee yet): every approval chain copied from the template becomes "No approval\""""
    try:
        if apps.get_model('EmpManagement', 'emp_master').objects.exists():
            return 0
    except LookupError:
        return 0
    n, was = 0, {}
    for lbl in WORKFLOWS:
        try:
            qs = apps.get_model(lbl).objects.exclude(approval_type='no_approval')
            was[lbl] = {str(pk): k for pk, k in qs.values_list('pk', 'approval_type')}
            n += qs.update(approval_type='no_approval')
        except Exception:   # noqa: BLE001
            pass
    if n:   # remembered: switching the approval hierarchy on brings each chain's own kind back
        _opt().objects.update_or_create(key=KEY + ':chains', defaults={'value': was})
    return n


def _was():
    try:
        r = _opt().objects.filter(key=KEY + ':chains').first()
        return dict(r.value or {}) if r is not None else {}
    except Exception:   # noqa: BLE001
        return {}


def listing():
    s = state()
    return {'features': [{'key': f.key, 'label': f.label, 'group': f.group, 'help': f.help, 'who': f.who, 'standard': False, 'on': s.get(f.key, True),
                          'needs': [BY[n].label for n in f.needs], 'used': used(f)} for f in FEATURES],
            'presets': [{'key': k, 'label': v[0], 'features': [BY[x].label for x in v[1]]} for k, v in PRESETS.items()],
            'new_company_rule': saved() is not None,
            'log': ((_opt().objects.filter(key=KEY + ':log').first() or type('x', (), {'value': {}})()).value or {}).get('rows', [])[-20:]}


# ---------------------------------------------------------------- v1.75.1 a type added while the approval hierarchy is off
TYPE_MODELS = ('calendars.leave_type', 'PayrollManagement.LoanType', 'EmpManagement.RequestType', 'EmpManagement.DocRequestType',
               'OrganisationManager.AssetType')


def _type_saved(sender, instance, created, **kw):
    """A leave type, loan type or request type made while "Approval hierarchy" is off gets its "No approval" chain at once – a
    type without a chain would go to the reporting manager."""
    if not created or kw.get('raw'):
        return
    try:
        if saved() is None or on('approvals'):
            return
        from django.db import transaction
        from ZeoAdmin.approval_defaults import ensure
        transaction.on_commit(lambda: ensure())
    except Exception:   # noqa: BLE001
        log.exception('no-approval chain of a new type')


def _chain_made(sender, instance, **kw):
    """A chain made while "Approval hierarchy" is off is "No approval", whichever screen or default made it (leave templates,
    country defaults, a new branch, a loan type … still make "Reporting manager" chains of their own)."""
    if kw.get('raw') or not instance._state.adding or getattr(instance, 'approval_type', None) != 'reporting_manager':
        return
    try:
        if saved() is not None and not on('approvals'):
            instance.approval_type = 'no_approval'
    except Exception:   # noqa: BLE001
        pass


def connect():
    from django.db.models.signals import post_save, pre_save
    for lbl in TYPE_MODELS:
        try:
            post_save.connect(_type_saved, sender=apps.get_model(lbl), dispatch_uid='hrfeat-' + lbl)
        except LookupError:
            pass
    for lbl in WORKFLOWS:
        try:
            pre_save.connect(_chain_made, sender=apps.get_model(lbl), dispatch_uid='hrfeat-chain-' + lbl)
        except LookupError:
            pass
