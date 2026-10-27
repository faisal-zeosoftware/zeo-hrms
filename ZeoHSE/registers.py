"""Generic register API for the blueprint modules (config-driven, like ZEO's Performance pages).

Each register (Reg) names its model, the fields shown in lists and forms, who manages / views it, which records a
normal employee sees (own records, published records), restricted fields (medical, financial) and its workflow
actions (Act). The frontend builds list, form, detail and action dialogs from GET /zeo-hse/api/registers/.

    GET    registers/                         meta of every register the user can open
    GET    r/<kind>/?q=&status=&parent=f:id   list
    POST   r/<kind>/                          create
    GET    r/<kind>/<id>/                     detail (+ actions, children, steps, history, files)
    PATCH  r/<kind>/<id>/                     change (while the record is editable)
    POST   r/<kind>/<id>/do/<action>/         workflow action
    POST   r/<kind>/<id>/files/               attach files
    GET    r/<kind>/<id>/pdf/                 printable record
    GET    options/<name>/?q=                 choices for a linked field
"""
import json
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO

from django.db import models as M
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponse
from rest_framework import status
from rest_framework.response import Response

from . import access as A
from . import links as L
from . import modules as MD
from . import rows as RW
from . import services as S
from .models import (Action, Area, Audit, AuditFinding, ChangeRequest, Chemical, Claim, Competency, CompetencyType, ComplianceAssessment,
                     Contractor, EnvAspect, EnvParameter, EnvReading, ErgoAssessment, ExposureLimit, FormTemplate, HealthSurveillance, HSEDocument,
                     HSEFile, HygieneSample, Incident, KPI, KPIValue, ManagementReview, Nonconformity, Obligation, Permit, RiskAssessment, Site,
                     WasteRecord)
from .views import Base, In, guard

# ------------------------------------------------------------------ options for linked fields
def _emp_qs():
    from EmpManagement.models import emp_master
    qs = emp_master.objects.all()
    if hasattr(emp_master, 'is_active'):
        qs = qs.filter(is_active=True)
    return qs.order_by('emp_first_name')


OPTIONS = {
    'employees': (lambda: _emp_qs(), lambda e: f'{S.emp_name(e)} ({e.emp_code})', ('emp_first_name', 'emp_last_name', 'emp_code')),
    'sites': (lambda: Site.objects.filter(is_active=True), lambda x: x.name, ('name', 'code')),
    'areas': (lambda: Area.objects.filter(is_active=True).select_related('site'), lambda x: f'{x.site.name} / {x.name}', ('name',)),
    'contractors': (lambda: Contractor.objects.filter(is_active=True), lambda x: f'{x.name}' + ('' if x.status == 'approved' else f' ({x.get_status_display()})'), ('name', 'code')),
    'incidents': (lambda: Incident.objects.all(), lambda x: f'{x.number} {x.title}', ('number', 'title')),
    'permits': (lambda: Permit.objects.all(), lambda x: f'{x.number} {x.description[:60]}', ('number', 'description')),
    'risks': (lambda: RiskAssessment.objects.filter(status__in=('approved', 'submitted', 'draft')), lambda x: f'{x.number} {x.title} ({x.get_status_display()})', ('number', 'title')),
    'audit-templates': (lambda: S.usable(FormTemplate.objects.filter(kind__in=('audit', 'inspection'))), lambda x: x.name, ('name', 'code')),
    'kpis': (lambda: KPI.objects.filter(is_active=True), lambda x: f'{x.code} {x.name}', ('code', 'name')),
    'limits': (lambda: ExposureLimit.objects.filter(is_active=True), lambda x: f'{x.agent} – {x.limit_value} {x.unit} {x.get_averaging_display()} (v{x.version})', ('agent',)),
    'parameters': (lambda: EnvParameter.objects.filter(is_active=True), lambda x: f'{x.name} {x.point}'.strip(), ('name', 'point')),
    'obligations': (lambda: Obligation.objects.filter(is_active=True), lambda x: f'{x.ref} {x.title}', ('ref', 'title')),
    'competency-types': (lambda: CompetencyType.objects.filter(is_active=True), lambda x: x.name, ('name', 'code')),
    'chemicals': (lambda: Chemical.objects.exclude(status='archived'), lambda x: f'{x.number} {x.name}', ('name', 'number')),
    'documents': (lambda: HSEDocument.objects.all(), lambda x: f'{x.number} rev {x.revision} {x.title}', ('number', 'title')),
    'audits': (lambda: Audit.objects.all(), lambda x: f'{x.number} {x.title}', ('number', 'title')),
    'files': (lambda: HSEFile.objects.none(), lambda x: x.name, ('name',)),
}
MODEL_OPTIONS = {'emp_master': 'employees', 'Site': 'sites', 'Area': 'areas', 'Contractor': 'contractors', 'Incident': 'incidents', 'Permit': 'permits',
                 'RiskAssessment': 'risks', 'FormTemplate': 'audit-templates', 'KPI': 'kpis', 'ExposureLimit': 'limits', 'EnvParameter': 'parameters',
                 'Obligation': 'obligations', 'CompetencyType': 'competency-types', 'Chemical': 'chemicals', 'HSEDocument': 'documents',
                 'Audit': 'audits', 'HSEFile': 'files'}


def opt_label(model_name, obj):
    k = MODEL_OPTIONS.get(model_name)
    if obj is None:
        return ''
    if k:
        return OPTIONS[k][1](obj)
    if hasattr(obj, 'number'):
        return obj.number
    return S.user_name(obj) if model_name in ('CustomUser', 'User') else str(obj)


# ------------------------------------------------------------------ registers
class Act:
    def __init__(self, name, label, run, allowed=None, fields=(), style='', confirm=''):
        self.name, self.label, self.run, self.allowed, self.fields, self.style, self.confirm = name, label, run, allowed, fields, style, confirm


class Reg:
    def __init__(self, kind, model, label, plural, group, *, cols, form, number=None, manage=(), view=(), create='manage', own=(),
                 public=None, restricted=(), private=False, search=('number', 'title'), filters=('status',), edit_when=None, locked=(),
                 actions=(), children=(), extra=None, saver=None, check=None, source=None, child_of=None, widgets=None, title='number',
                 subtitle='title', help='', defaults=None, files=True, can_restricted=None, links=None):
        self.kind, self.model, self.label, self.plural, self.group = kind, model, label, plural, group
        self.cols, self.form, self.number, self.manage, self.view, self.create, self.own = cols, form, number, manage, view, create, own
        self.public, self.restricted, self.private, self.search, self.filters = public, restricted, private, search, filters
        self.edit_when, self.locked, self.actions, self.children, self.extra = edit_when, locked, list(actions), children, extra
        self.saver, self.check, self.source, self.child_of, self.widgets = saver, check, source, child_of, widgets or {}
        self.title, self.subtitle, self.help, self.defaults, self.files = title, subtitle, help, defaults, files
        self.can_restricted = can_restricted or A.can_see_health
        self.links = links or {}   # v1.17.0 {field: option name} – ids of another ZEO app's records, shown only while that link is on

    # rights
    def manages(self, request):
        return A.is_admin(request) or A.any_role(request, *self.manage)

    def views_all(self, request):
        return self.manages(request) or A.any_role(request, *self.view) or (not self.private and A.has_role(request, 'senior_mgmt'))

    def can_create(self, request):
        return self.create == 'all' or self.manages(request) or (self.create not in ('manage', 'all') and A.any_role(request, *self.create))

    def can_open(self, request):
        return self.views_all(request) or self.can_create(request) or bool(self.own) or self.public is not None

    def scope(self, request):
        return A.in_branches(self._scope(request), request)   # v1.22.0: only the user's branches

    def _scope(self, request):
        qs = self.model.objects.all()
        if self.views_all(request):
            return qs
        me = A.my_emp(request)
        q = Q(pk__in=[])
        for field, typ in self.own:
            if typ == 'user':
                q |= Q(**{field: request.user})
            elif me is not None:
                q |= Q(**{field: me})
        if self.public is not None:
            q |= self.public(request)
        return qs.filter(q)

    def is_own(self, obj, request):
        me = A.my_emp(request)
        for field, typ in self.own:
            v = getattr(obj, field + '_id', None)
            if (typ == 'user' and v == request.user.pk) or (typ == 'emp' and me is not None and v == me.pk):
                return True
        return False

    def can_edit(self, obj, request):
        open_ = self.edit_when(obj) if self.edit_when else True
        if not open_:
            return False
        return self.manages(request) or (self.is_own(obj, request) and self.create == 'all')

    def show_restricted(self, request, obj=None):
        return self.can_restricted(request) or (obj is not None and self.kind == 'health' and self.is_own(obj, request))


def st(*s):
    return lambda o, *_: o.status in s


def _role(*r):
    return lambda o, req: A.any_role(req, *r)


def _act_raise(source):
    return Act('raise-action', 'Raise action', lambda o, req, f: MD.raise_action(source, o, req, f),
               lambda o, req: A.is_staff(req), fields=ACTION_FIELDS)


ACTION_FIELDS = (('title', 'Action', 'text', True), ('owner', 'Owner', 'fk:employees', True), ('due_date', 'Due date', 'date', True),
                 ('priority', 'Priority', 'choice:low=Low,medium=Medium,high=High,critical=Critical', False), ('verifier', 'Verifier', 'fk:employees', False),
                 ('description', 'Details', 'textarea', False))
NOTE = (('note', 'Note / reason', 'textarea', False),)


def steps_waiting(model):
    def f(o, req):
        stp = S.current_step(model, o.pk)
        return bool(stp and A.has_role(req, stp.role))
    return f


def actions_of(source):
    def f(o, req):
        return [RW.action(a) for a in Action.objects.filter(source=source, source_id=o.pk).select_related('owner', 'verifier', 'site')]
    return f


HSE = ('hse_manager', 'hse_officer')
REGS = {}


def reg(r):
    REGS[r.kind] = r
    try:   # v1.22.0 record panel (Chatter): the register's own rights decide who may open its records
        from Chatter.views import register_scope
        register_scope(r.model, lambda request, qs, r=r: qs.filter(pk__in=r.scope(request).values('pk')))
    except Exception:
        pass
    return r


# 2 – management of change
reg(Reg('moc', ChangeRequest, 'Change request', 'Management of change', 'assurance',
        cols=('number', 'title', 'kind', 'category', 'site', 'status', 'start_date'),
        form=('title', 'kind', 'category', 'site', 'description', 'reason', 'scope', 'start_date', 'end_date', 'emergency_justification',
              'coordinator', 'impacts', 'risk_assessment', 'prestart_checks'),
        number='moc', manage=('moc_coordinator', 'hse_manager'), view=('hse_officer', 'site_manager'), create='all', own=(('requester', 'user'),),
        filters=('status', 'kind', 'category', 'site'), edit_when=st('draft', 'rejected', 'submitted', 'impact', 'approved'),
        widgets={'impacts': 'impacts', 'prestart_checks': 'checks'}, source='moc',
        defaults=lambda o, req: setattr(o, 'requester', req.user),
        extra=lambda o, req: {'impact_areas': MD.MOC_AREAS, 'prestart': MD.PRESTART, 'actions_list': actions_of('moc')(o, req)},
        actions=[Act('submit', 'Submit', lambda o, r, f: MD.moc_submit(o, r, f), lambda o, r: o.status in ('draft', 'rejected'), style='primary'),
                 Act('screen', 'Screen', lambda o, r, f: MD.moc_screen(o, r, f), lambda o, r: o.status == 'submitted' and A.any_role(r, 'moc_coordinator', 'hse_manager'),
                     fields=(('formal_required', 'Formal change control required', 'bool', False), ('coordinator', 'Coordinator', 'fk:employees', False),
                             ('screening_note', 'Screening note', 'textarea', False)), style='primary'),
                 Act('for-approval', 'Send for approval', lambda o, r, f: MD.moc_for_approval(o, r, f),
                     lambda o, r: o.status == 'impact' and A.any_role(r, 'moc_coordinator', 'hse_manager'), style='primary'),
                 Act('approve', 'Approve', lambda o, r, f: MD.moc_decide(o, r, f, True), lambda o, r: o.status == 'approval' and steps_waiting('moc')(o, r), NOTE, 'primary'),
                 Act('reject', 'Reject', lambda o, r, f: MD.moc_decide(o, r, f, False), lambda o, r: o.status == 'approval' and steps_waiting('moc')(o, r), NOTE, 'danger'),
                 Act('start', 'Pre-start verified – start', lambda o, r, f: MD.moc_start(o, r, f), lambda o, r: o.status == 'approved', style='primary'),
                 Act('implemented', 'Mark implemented', lambda o, r, f: MD.moc_implemented(o, r, f), lambda o, r: o.status == 'implementing',
                     (('implemented_on', 'Implemented on', 'date', True), ('implemented_by', 'Implemented by', 'fk:employees', False))),
                 Act('close', 'Post-change review and close', lambda o, r, f: MD.moc_close(o, r, f),
                     lambda o, r: o.status == 'verification' and A.any_role(r, 'moc_coordinator', 'hse_manager'),
                     (('objective_met', 'Objective achieved', 'bool', True), ('new_hazards', 'New hazards introduced', 'textarea', False),
                      ('review_note', 'Review note', 'textarea', False)), 'primary'),
                 Act('cancel', 'Cancel', lambda o, r, f: (setattr(o, 'status', 'cancelled'), o.save(), S.event('moc', o.pk, f'Cancelled: {f.s("note")}', r.user)),
                     lambda o, r: o.status in ('draft', 'submitted', 'impact', 'rejected', 'approved') and (o.requester_id == r.user.pk or A.has_role(r, 'moc_coordinator')),
                     NOTE, 'danger'),
                 _act_raise('moc')]))

# 3 – occupational health
reg(Reg('health', HealthSurveillance, 'Health assessment', 'Occupational health', 'health',
        cols=('number', 'employee', 'exposure', 'assessment_type', 'due_date', 'completed_on', 'fitness'),
        form=('employee', 'job_role', 'exposure', 'assessment_type', 'due_date', 'provider', 'interval_months', 'completed_on', 'fitness', 'restrictions',
              'followup_date', 'clinical_notes'),
        number='health', manage=('occ_health',), view=('hse_manager',), own=(('employee', 'emp'),), private=True,
        public=lambda req: Q(employee__emp_reporting_manager=req.user),
        restricted=('clinical_notes', 'provider'), search=('number', 'employee__emp_first_name', 'employee__emp_code'),
        filters=('exposure', 'assessment_type', 'fitness'), edit_when=lambda o: o.completed_on is None, subtitle='employee',
        help='Clinical findings are visible only to occupational health and the HSE manager; a line manager sees only the fitness outcome and restrictions of their own staff.',
        actions=[Act('result', 'Record result', lambda o, r, f: (MD.health_result(o, r, f), None)[1], lambda o, r: o.completed_on is None and A.can_see_health(r),
                     (('completed_on', 'Completed on', 'date', True), ('fitness', 'Fitness', 'choice:' + ','.join(f'{k}={v}' for k, v in HealthSurveillance.FITNESS[1:]), True),
                      ('restrictions', 'Restrictions (shared with the manager)', 'textarea', False), ('clinical_notes', 'Clinical findings (restricted)', 'textarea', False),
                      ('followup_date', 'Follow-up date', 'date', False)), 'primary'),
                 _act_raise('health')]))

# 4 – audits
reg(Reg('audits', Audit, 'Audit', 'Audits', 'assurance',
        cols=('number', 'title', 'kind', 'site', 'lead_auditor', 'planned_date', 'score', 'status'),
        form=('title', 'kind', 'standard', 'site', 'contractor', 'template', 'lead_auditor', 'auditee', 'planned_date', 'unplanned', 'answers', 'summary'),
        number='audit', manage=('auditor', 'hse_manager', 'hse_officer', 'quality_manager'), view=('site_manager',),
        own=(('lead_auditor', 'emp'), ('auditee', 'emp')), filters=('status', 'kind', 'site'), edit_when=st('planned', 'in_progress'),
        widgets={'answers': 'audit-checklist'}, check=lambda o, req: MD.audit_check(o, req),
        defaults=lambda o, req: setattr(o, 'created_by', req.user),
        children=(('Findings', 'audit-findings', 'audit'),),
        extra=lambda o, req: {'checklist': o.template.questions if o.template_id else [], 'actions_list': actions_of('audit')(o, req)},
        actions=[Act('start', 'Start audit', lambda o, r, f: MD.audit_start(o, r, f), st('planned'), style='primary'),
                 Act('finish', 'Finish – submit report', lambda o, r, f: MD.audit_finish(o, r, f), st('in_progress'),
                     (('summary', 'Report summary', 'textarea', True),), 'primary'),
                 Act('approve', 'Approve report', lambda o, r, f: MD.audit_decide(o, r, f, True), lambda o, r: o.status == 'reported' and steps_waiting('audit')(o, r), NOTE, 'primary'),
                 Act('reject', 'Return report', lambda o, r, f: MD.audit_decide(o, r, f, False), lambda o, r: o.status == 'reported' and steps_waiting('audit')(o, r), NOTE, 'danger'),
                 Act('close', 'Close audit', lambda o, r, f: MD.audit_close(o, r, f), st('approved'), style='primary'),
                 Act('cancel', 'Cancel', lambda o, r, f: (setattr(o, 'status', 'cancelled'), o.save(), S.event('audit', o.pk, f'Cancelled: {f.s("note")}', r.user)),
                     lambda o, r: o.status == 'planned' and A.any_role(r, 'auditor', 'hse_manager'), NOTE, 'danger')]))
reg(Reg('audit-findings', AuditFinding, 'Finding', 'Audit findings', 'assurance',
        cols=('number', 'text', 'grade', 'repeat', 'action'), form=('audit', 'text', 'grade', 'evidence'),
        manage=('auditor', 'hse_manager', 'hse_officer', 'quality_manager'), view=('site_manager',), child_of='audit', search=('number', 'text'),
        filters=('grade', 'repeat'), edit_when=lambda o: o.action_id is None and o.audit.status in ('in_progress', 'reported'),
        defaults=lambda o, req: setattr(o, 'number', f'{o.audit.number}-F{o.audit.findings.count() + 1:02d}'), subtitle='text',
        actions=[Act('raise-action', 'Raise action', lambda o, r, f: MD.finding_action(o, r, f), lambda o, r: o.action_id is None, ACTION_FIELDS, 'primary')]))

# 5 – quality
reg(Reg('quality', Nonconformity, 'Nonconformity', 'Quality nonconformities', 'assurance',
        cols=('number', 'title', 'source', 'product_process', 'severity', 'disposition', 'status'),
        form=('title', 'source', 'product_process', 'project', 'supplier', 'site', 'defect_type', 'specification', 'actual_result', 'severity',
              'qty_affected', 'traceability', 'containment', 'root_cause', 'reinspection_result', 'owner'),
        number='nc', manage=('quality_manager',), view=('hse_manager', 'hse_officer', 'site_manager'), create='all', own=(('reported_by', 'user'),),
        filters=('status', 'source', 'severity'), edit_when=lambda o: o.status != 'closed', source='quality',
        defaults=lambda o, req: setattr(o, 'reported_by', req.user),
        help='A quality record never closes a linked HSE finding – each action keeps its own verification.',
        extra=lambda o, req: {'actions_list': actions_of('quality')(o, req)},
        actions=[Act('contain', 'Record containment', lambda o, r, f: MD.nc_contain(o, r, f), lambda o, r: o.status in ('open', 'contained'),
                     (('containment', 'Containment / quarantine', 'textarea', True),), 'primary'),
                 Act('disposition', 'Approve disposition', lambda o, r, f: MD.nc_disposition(o, r, f), lambda o, r: o.status != 'closed' and A.has_role(r, 'quality_manager'),
                     (('disposition', 'Disposition', 'choice:' + ','.join(f'{k}={v}' for k, v in Nonconformity.DISPOSITION[1:]), True),
                      ('root_cause', 'Root cause', 'textarea', False))),
                 Act('raise-action', 'Raise action', lambda o, r, f: (MD.raise_action('quality', o, r, f), Nonconformity.objects.filter(pk=o.pk, status__in=('open', 'contained', 'investigation')).update(status='actions')),
                     lambda o, r: o.status != 'closed' and (A.has_role(r, 'quality_manager') or A.is_staff(r)), ACTION_FIELDS),
                 Act('close', 'Verify and close', lambda o, r, f: MD.nc_close(o, r, f), lambda o, r: o.status != 'closed' and A.has_role(r, 'quality_manager'),
                     (('reinspection_result', 'Re-inspection result', 'textarea', True),), 'primary')]))

# 7 – performance
reg(Reg('kpis', KPI, 'KPI', 'KPIs and objectives', 'performance',
        cols=('code', 'name', 'formula', 'target', 'unit', 'owner', 'site'), form=('code', 'name', 'objective', 'formula', 'unit', 'target', 'higher_is_better',
                                                                              'site', 'owner', 'year', 'is_active'),
        manage=HSE, view=('site_manager',), search=('code', 'name'), filters=('formula', 'site'), title='code', subtitle='name',
        children=(('Monthly values', 'kpi-values', 'kpi'),),
        help='Rates use the multiplier and hours set in HSE settings; never compare rates with different definitions.',
        actions=[Act('calculate', 'Calculate a month', lambda o, r, f: MD.kpi_calculate(f.dt('period') or S.today(), r.user),
                     lambda o, r: A.any_role(r, *HSE), (('period', 'Month (any date in it)', 'date', True),), 'primary'),
                 Act('to-performance', 'Add to Performance KPI library', lambda o, r, f: L.kpi_to_performance(o, r),
                     lambda o, r: A.any_role(r, *HSE) and not o.perf_kpi_id and L.on('performance')),
                 _act_raise('kpi')],
        extra=lambda o, req: {'performance_kpi': L.on('performance') and bool(o.perf_kpi_id)}))
reg(Reg('kpi-values', KPIValue, 'KPI value', 'KPI values', 'performance',
        cols=('period', 'value', 'target', 'source', 'deviation', 'validated_by'), form=('kpi', 'period', 'value', 'target', 'note'),
        manage=HSE, view=('site_manager',), child_of='kpi', search=('note',), filters=('deviation',), title='period', subtitle='kpi',
        saver=lambda o, req, created, old: MD.kpi_value_saved(o, req)))
reg(Reg('reviews', ManagementReview, 'Management review', 'Management reviews', 'performance',
        cols=('number', 'date', 'period_from', 'period_to', 'status'), form=('date', 'period_from', 'period_to', 'attendees', 'decisions'),
        number='review', manage=('hse_manager',), view=('hse_officer', 'site_manager'), filters=('status',), edit_when=st('draft'), subtitle='date',
        defaults=lambda o, req: setattr(o, 'created_by', req.user), widgets={'inputs': 'review-inputs'},
        extra=lambda o, req: {'inputs': o.inputs, 'actions_list': actions_of('review')(o, req)},
        actions=[Act('collect', 'Collect inputs (KPIs, actions, audits)', lambda o, r, f: MD.review_collect(o, r, f), st('draft'), style='primary'),
                 _act_raise('review'),
                 Act('close', 'Close review', lambda o, r, f: MD.review_close(o, r, f), st('draft'), (('decisions', 'Management decisions', 'textarea', True),), 'primary')]))

# 9 – ergonomics
reg(Reg('ergonomics', ErgoAssessment, 'Ergonomic assessment', 'Ergonomics', 'health',
        cols=('number', 'workstation', 'task', 'method', 'score', 'risk_level', 'status', 'assessed_on'),
        form=('workstation', 'task', 'department', 'site', 'workers', 'manual_handling', 'repetitive', 'awkward_posture', 'force', 'duration_hours',
              'frequency', 'observations', 'method', 'score', 'risk_level', 'controls', 'assessor', 'assessed_on', 'reassess_on'),
        number='ergo', manage=HSE + ('occ_health',), view=('site_manager', 'supervisor'), filters=('status', 'risk_level', 'method', 'site'),
        search=('number', 'workstation', 'task'), edit_when=st('open', 'actions'), subtitle='workstation', source='ergonomics',
        extra=lambda o, req: {'actions_list': actions_of('ergonomics')(o, req)},
        help='Any recognised method can be used – the score is kept with the method, never compared across methods.',
        actions=[Act('raise-action', 'Raise control action', lambda o, r, f: (MD.raise_action('ergonomics', o, r, f), ErgoAssessment.objects.filter(pk=o.pk, status='open').update(status='actions')),
                     lambda o, r: o.status != 'closed' and A.is_staff(r), ACTION_FIELDS, 'primary'),
                 Act('reassess', 'Reassess after controls', lambda o, r, f: MD.ergo_reassess(o, r, f), lambda o, r: o.status in ('actions', 'reassess', 'open'),
                     (('reassessed_score', 'New score', 'decimal', True), ('reassessed_level', 'New risk level', 'choice:low=Low,medium=Medium,high=High,very_high=Very high', True),
                      ('effectiveness', 'Effectiveness of the controls', 'textarea', False)))]))

# 10 – environmental
ENV = ('env_officer',) + HSE
reg(Reg('env-aspects', EnvAspect, 'Environmental aspect', 'Aspects and impacts', 'environment',
        cols=('activity', 'aspect', 'impact', 'condition', 'severity', 'probability', 'significant', 'site'),
        form=('site', 'activity', 'aspect', 'impact', 'condition', 'severity', 'probability', 'controls', 'legal_ref', 'owner', 'is_active'),
        manage=ENV, view=('site_manager',), search=('activity', 'aspect', 'impact'), filters=('significant', 'condition', 'site'), title='aspect', subtitle='activity',
        saver=lambda o, req, created, old: MD.aspect_saved(o, req), source='environment',
        help='Significance = severity × probability, against the threshold in HSE settings.'))
reg(Reg('env-parameters', EnvParameter, 'Monitoring parameter', 'Environmental monitoring', 'environment',
        cols=('name', 'category', 'site', 'point', 'unit', 'limit_min', 'limit_max', 'next_due'),
        form=('site', 'name', 'category', 'point', 'unit', 'limit_min', 'limit_max', 'limit_source', 'frequency_days', 'next_due', 'owner', 'is_active'),
        manage=ENV, view=('site_manager',), search=('name', 'point'), filters=('category', 'site'), title='name', subtitle='point',
        saver=lambda o, req, created, old: MD.parameter_saved(o, req), children=(('Readings', 'env-readings', 'parameter'),),
        help='Limits depend on the jurisdiction, site and activity – enter them from the applicable permit or regulation.'))
reg(Reg('env-readings', EnvReading, 'Reading', 'Readings', 'environment',
        cols=('sampled_on', 'value', 'exceeded', 'evidence', 'action', 'recorded_by'), form=('parameter', 'sampled_on', 'value', 'evidence'),
        manage=ENV, view=('site_manager',), create=('supervisor', 'site_manager'), child_of='parameter', search=('evidence',), filters=('exceeded',),
        title='sampled_on', subtitle='parameter', edit_when=lambda o: False, saver=lambda o, req, created, old: MD.reading_saved(o, req)))
reg(Reg('waste', WasteRecord, 'Waste record', 'Waste', 'environment',
        cols=('number', 'waste_type', 'hazardous', 'quantity', 'unit', 'site', 'carrier', 'manifest_no', 'status'),
        form=('site', 'waste_type', 'hazardous', 'quantity', 'unit', 'generated_on', 'storage', 'carrier', 'destination', 'manifest_no', 'disposal_permit'),
        number='waste', manage=ENV, view=('site_manager',), create=('supervisor', 'site_manager'), search=('number', 'waste_type', 'manifest_no'),
        filters=('status', 'hazardous', 'site'), edit_when=st('stored'), subtitle='waste_type',
        actions=[Act('transfer', 'Record transfer', lambda o, r, f: MD.waste_transfer(o, r, f), st('stored'),
                     (('carrier', 'Approved carrier', 'fk:contractors', True), ('destination', 'Disposal destination', 'text', True),
                      ('manifest_no', 'Manifest no.', 'text', False), ('disposal_permit', 'Permit no.', 'text', False), ('transferred_on', 'Date', 'date', False)), 'primary'),
                 Act('disposed', 'Confirm disposal', lambda o, r, f: MD.waste_disposed(o, r, f), st('transferred'), (('disposed_on', 'Disposed on', 'date', True),), 'primary')]))

# 11 – chemicals
reg(Reg('chemicals', Chemical, 'Chemical', 'Chemicals and SDS', 'environment',
        cols=('number', 'name', 'trade_name', 'compat_group', 'site', 'storage_location', 'sds_date', 'status'),
        form=('name', 'trade_name', 'supplier', 'product_code', 'cas_no', 'hazard_classes', 'signal_word', 'intended_use', 'site', 'storage_location',
              'max_qty', 'unit', 'current_qty', 'compat_group', 'ppe', 'handling', 'emergency_measures', 'disposal', 'risk_assessment', 'owner'),
        number='chemical', manage=HSE + ('env_officer',), view=('site_manager', 'supervisor', 'occ_health'), create='all', own=(('requested_by', 'user'),),
        public=lambda req: Q(status='approved'), search=('number', 'name', 'trade_name', 'cas_no'), filters=('status', 'compat_group', 'site'),
        edit_when=st('requested', 'review', 'rejected', 'approved'), subtitle='name', source='chemical',
        defaults=lambda o, req: setattr(o, 'requested_by', req.user),
        help='Everyone can read approved chemicals (handling, PPE, emergency measures) – the emergency information for responders.',
        extra=lambda o, req: {'sds': RW.files('chemical', o.pk), 'sds_revision': o.sds_revision, 'review_due': o.review_due, 'incompatible': [f'{x.number} {x.name}' for x in MD.incompatible_with(o)]},
        actions=[Act('sds', 'Upload SDS', lambda o, r, f: MD.chem_sds(o, r, f), lambda o, r: o.status != 'archived',
                     (('file', 'SDS file', 'file', True), ('sds_revision', 'SDS revision', 'text', True), ('sds_date', 'SDS date', 'date', True)), 'primary'),
                 Act('submit', 'Send for HSE review', lambda o, r, f: MD.chem_submit(o, r, f), st('requested', 'rejected')),
                 Act('approve', 'Approve for use', lambda o, r, f: MD.chem_approve(o, r, f), lambda o, r: o.status == 'review' and A.any_role(r, *HSE), style='primary'),
                 Act('reject', 'Reject', lambda o, r, f: MD.chem_reject(o, r, f), lambda o, r: o.status == 'review' and A.any_role(r, *HSE), NOTE, 'danger'),
                 Act('archive', 'Archive', lambda o, r, f: MD.chem_archive(o, r, f), lambda o, r: o.status in ('approved', 'rejected') and A.any_role(r, *HSE), NOTE)]))

# 13 – claims (restricted)
reg(Reg('claims', Claim, 'Claim', 'Claims', 'actions',
        cols=('number', 'claim_type', 'incident', 'insurer', 'amount_claimed', 'approved_amount', 'paid_amount', 'status', 'next_action_on'),
        form=('incident', 'claim_type', 'claimant_employee', 'claimant_name', 'contractor', 'insurer', 'policy_ref', 'notified_on', 'amount_claimed',
              'estimated_liability', 'deductible', 'recovered_amount', 'owner', 'next_action_on', 'notes'),
        number='claim', manage=('claims_officer',), view=('hse_manager',), private=True, search=('number', 'insurer', 'policy_ref', 'claimant_name'),
        filters=('status', 'claim_type'), edit_when=lambda o: o.status not in ('closed',), subtitle='claim_type',
        defaults=lambda o, req: setattr(o, 'created_by', req.user),
        help='Restricted: claims officer, HSE manager and senior management only.',
        extra=lambda o, req: {'outstanding': float(MD.claim_outstanding(o))},
        actions=[Act('submit', 'Submitted to insurer', lambda o, r, f: MD.claim_submit(o, r, f), st('open'), (('submitted_on', 'Date', 'date', False),), 'primary'),
                 Act('decision', 'Record insurer decision', lambda o, r, f: MD.claim_decision(o, r, f), st('submitted', 'assessment', 'disputed'),
                     (('decision', 'Decision', 'choice:assessment=Under assessment,approved=Approved,disputed=Disputed,rejected=Rejected', True),
                      ('approved_amount', 'Approved amount', 'decimal', False), ('note', 'Note', 'textarea', False))),
                 Act('request-settlement', 'Request settlement approval', lambda o, r, f: MD.claim_request_settlement(o, r, f),
                     lambda o, r: o.status == 'approved' and not o.settlement_approved_by_id and S.current_step('claim', o.pk) is None),
                 Act('approve', 'Approve settlement', lambda o, r, f: MD.claim_decide(o, r, f, True), steps_waiting('claim'), NOTE, 'primary'),
                 Act('reject', 'Refuse settlement', lambda o, r, f: MD.claim_decide(o, r, f, False), steps_waiting('claim'), NOTE, 'danger'),
                 Act('settle', 'Record payment / settle', lambda o, r, f: MD.claim_settle(o, r, f), lambda o, r: o.status == 'approved' and bool(o.settlement_approved_by_id),
                     (('paid_amount', 'Paid amount', 'decimal', True),), 'primary'),
                 Act('close', 'Close', lambda o, r, f: MD.claim_close(o, r, f), st('settled', 'rejected'))],
        can_restricted=lambda req: True))

# 14 – industrial hygiene
IH = HSE + ('occ_health',)
reg(Reg('exposure-limits', ExposureLimit, 'Exposure limit', 'Exposure limits', 'health',
        cols=('agent', 'category', 'limit_value', 'action_level', 'unit', 'averaging', 'jurisdiction', 'version', 'is_active'),
        form=('agent', 'category', 'unit', 'limit_value', 'action_level', 'averaging', 'jurisdiction', 'effective_from', 'is_active'),
        manage=IH, search=('agent', 'jurisdiction'), filters=('category', 'is_active'), title='agent', subtitle='jurisdiction',
        saver=lambda o, req, created, old: MD.limit_saved(o, req, old),
        help='Version-controlled: changing a limit that has samples creates a new version. Select limits for the jurisdiction, substance, method and averaging period.'))
reg(Reg('hygiene', HygieneSample, 'Monitoring sample', 'Industrial hygiene', 'health',
        cols=('number', 'limit', 'site', 'location', 'job_group', 'planned_on', 'result', 'exceeded', 'status'),
        form=('limit', 'site', 'location', 'job_group', 'employee', 'sample_type', 'method', 'instrument', 'calibration_date', 'planned_on', 'conditions'),
        number='hygiene', manage=IH, view=('site_manager',), search=('number', 'location', 'job_group'), filters=('status', 'exceeded', 'site'),
        edit_when=st('planned'), subtitle='location', restricted=('employee',),
        help='Results are compared with the selected limit version; the system never draws medical conclusions from them.',
        actions=[Act('result', 'Record result', lambda o, r, f: MD.sample_result(o, r, f), lambda o, r: o.status in ('planned', 'done', 'action') and A.any_role(r, *IH),
                     (('sampled_on', 'Sample date', 'date', True), ('result', 'Result', 'decimal', True), ('instrument', 'Instrument', 'text', False),
                      ('calibration_date', 'Calibration date', 'date', True), ('duration_min', 'Duration (min)', 'int', False), ('laboratory', 'Laboratory', 'text', False),
                      ('interpretation', 'Interpretation', 'textarea', False), ('owner', 'Control action owner (if exceeded)', 'fk:employees', False)), 'primary'),
                 Act('close', 'Close', lambda o, r, f: MD.sample_close(o, r, f), st('done', 'action'), (('control_action', 'Controls implemented', 'textarea', False),))],
        can_restricted=lambda req: A.can_see_health(req) or A.any_role(req, 'hse_officer')))

# 15 – compliance
reg(Reg('obligations', Obligation, 'Obligation', 'Compliance register', 'assurance',
        cols=('ref', 'title', 'source', 'jurisdiction', 'owner', 'due_date', 'status'),
        form=('ref', 'title', 'source', 'jurisdiction', 'authority', 'summary', 'applicability', 'related_document', 'site', 'owner', 'reviewer', 'due_date',
              'recurrence_months', 'evidence_required', 'is_active'),
        manage=HSE + ('env_officer',), view=('site_manager',), own=(('owner', 'emp'), ('reviewer', 'emp')), search=('ref', 'title', 'authority'),
        filters=('status', 'source', 'site'), title='ref', subtitle='title', children=(('Assessments', 'compliance-assessments', 'obligation'),),
        help='Applicability and interpretation must be confirmed by competent local specialists – a complete form is not proof of legal compliance.',
        extra=lambda o, req: {'pending': o.assessments.filter(state='pending').exists(), 'actions_list': actions_of('compliance')(o, req)},
        actions=[Act('assess', 'Record assessment', lambda o, r, f: MD.obligation_assess(o, r, f), lambda o, r: not o.assessments.filter(state='pending').exists(),
                     (('result', 'Result', 'choice:' + ','.join(f'{k}={v}' for k, v in Obligation.STATUS[1:]), True), ('assessed_on', 'Assessed on', 'date', False),
                      ('evidence', 'Evidence', 'textarea', False), ('files', 'Evidence files', 'files', False), ('finding', 'Finding', 'textarea', False)), 'primary'),
                 Act('approve', 'Approve assessment', lambda o, r, f: MD.obligation_review(o, r, f, True), lambda o, r: o.assessments.filter(state='pending').exists(), NOTE, 'primary'),
                 Act('reject', 'Return assessment', lambda o, r, f: MD.obligation_review(o, r, f, False), lambda o, r: o.assessments.filter(state='pending').exists(), NOTE, 'danger'),
                 _act_raise('compliance')]))
reg(Reg('compliance-assessments', ComplianceAssessment, 'Assessment', 'Compliance assessments', 'assurance',
        cols=('assessed_on', 'result', 'state', 'assessor', 'approved_by', 'action'), form=('obligation', 'assessed_on', 'result', 'evidence', 'finding'),
        manage=(), view=HSE + ('env_officer', 'site_manager'), create='none', child_of='obligation', search=('evidence',), filters=('result', 'state'),
        title='assessed_on', subtitle='result', edit_when=lambda o: False))

# document control
reg(Reg('documents', HSEDocument, 'Document', 'Documents', 'registers',
        cols=('number', 'revision', 'title', 'doc_type', 'owner', 'effective_date', 'review_date', 'status'),
        form=('title', 'doc_type', 'site', 'summary', 'owner', 'approver', 'effective_date', 'review_date', 'restricted', 'requires_ack'),
        number='document', manage=HSE, view=('site_manager',), own=(('owner', 'emp'), ('approver', 'emp')),
        public=lambda req: Q(status='published', restricted=False), search=('number', 'title'), filters=('status', 'doc_type', 'site'),
        edit_when=st('draft'), subtitle='title', defaults=lambda o, req: setattr(o, 'created_by', req.user),
        extra=lambda o, req: {'acks': o.acks.count(), 'acked': bool(A.my_emp(req) and o.acks.filter(employee=A.my_emp(req)).exists()),
                              'revisions': [{'id': x.pk, 'revision': x.revision, 'status': x.get_status_display()} for x in HSEDocument.objects.filter(number=o.number).order_by('-revision')]},
        actions=[Act('submit', 'Send for review', lambda o, r, f: MD.doc_submit(o, r, f), st('draft'), style='primary'),
                 Act('approve', 'Approve', lambda o, r, f: MD.doc_decide(o, r, f, True), st('review'), NOTE, 'primary'),
                 Act('reject', 'Return', lambda o, r, f: MD.doc_decide(o, r, f, False), st('review'), NOTE, 'danger'),
                 Act('publish', 'Publish', lambda o, r, f: MD.doc_publish(o, r, f), lambda o, r: o.status == 'approved' and A.any_role(r, *HSE),
                     (('effective_date', 'Effective date', 'date', False),), 'primary'),
                 Act('revise', 'New revision', lambda o, r, f: MD.doc_revise(o, r, f), lambda o, r: o.status == 'published' and A.any_role(r, *HSE),
                     (('summary', 'What changes', 'textarea', False),)),
                 Act('ack', 'I have read it', lambda o, r, f: MD.doc_ack(o, r, f),
                     lambda o, r: o.status == 'published' and o.requires_ack and bool(A.my_emp(r)) and not o.acks.filter(employee=A.my_emp(r)).exists(), style='primary'),
                 Act('archive', 'Archive', lambda o, r, f: MD.doc_archive(o, r, f), lambda o, r: o.status in ('published', 'superseded', 'draft') and A.has_role(r, 'hse_manager'), NOTE)]))

# training and competency
reg(Reg('competency-types', CompetencyType, 'Competency', 'Competency types', 'registers',
        cols=('code', 'name', 'validity_months', 'course_id', 'is_active'), form=('code', 'name', 'validity_months', 'course_id', 'description', 'is_active'),
        manage=HSE, view=('site_manager', 'supervisor', 'permit_issuer'), public=lambda req: Q(is_active=True), search=('code', 'name'), filters=('is_active',),
        title='code', subtitle='name', links={'course_id': 'courses'},
        saver=lambda o, req, created, old: (o.save(), L.sync_certificates(o) if o.course_id and (old is None or old.course_id != o.course_id) else 0),
        help='Link the HR course whose certificate gives this competency: HR certificates then create and renew the competency records.'))
reg(Reg('competencies', Competency, 'Competency record', 'Training and competency', 'registers',
        cols=('employee', 'ctype', 'issued_on', 'expires_on', 'provider', 'certificate_no', 'assessed_competent', 'source'),
        form=('employee', 'ctype', 'issued_on', 'expires_on', 'provider', 'certificate_no', 'assessed_competent'),
        manage=HSE, view=('site_manager', 'supervisor', 'permit_issuer'), own=(('employee', 'emp'),),
        search=('employee__emp_first_name', 'employee__emp_code', 'certificate_no', 'ctype__name'), filters=('ctype', 'assessed_competent'),
        title='ctype', subtitle='employee', saver=lambda o, req, created, old: MD.competency_saved(o, req), edit_when=lambda o: o.source != 'learning',
        help='Expired or missing competencies block permit issue when HSE settings say so (else a warning).'))


# ------------------------------------------------------------------ field meta and values
def field_of(model, name):
    return model._meta.get_field(name)


def ftype(f):
    if isinstance(f, M.ManyToManyField):
        return 'm2m'
    if isinstance(f, M.ForeignKey):
        return 'fk'
    if f.choices:
        return 'choice'
    if isinstance(f, M.BooleanField):
        return 'bool3' if f.null else 'bool'
    if isinstance(f, M.TextField):
        return 'textarea'
    if isinstance(f, M.DecimalField):
        return 'decimal'
    if isinstance(f, (M.IntegerField, M.PositiveSmallIntegerField, M.PositiveIntegerField)):
        return 'int'
    if isinstance(f, M.DateTimeField):
        return 'datetime'
    if isinstance(f, M.DateField):
        return 'date'
    if isinstance(f, M.JSONField):
        return 'json'
    return 'text'


def label_of(f):
    v = str(f.verbose_name)
    return v[:1].upper() + v[1:]


def link_off(r, name):
    return name in r.links and not L.on(L.OPTION_LINK[r.links[name]][0])


def fmeta(r, name):
    f = field_of(r.model, name)
    if name in r.links:
        return {'name': name, 'label': label_of(f), 'type': 'fk', 'options': r.links[name], 'help': str(f.help_text or ''), 'restricted': False,
                'required': False, 'link': L.OPTION_LINK[r.links[name]][0]}
    t = ftype(f)
    m = {'name': name, 'label': label_of(f), 'type': t, 'help': str(f.help_text or ''), 'restricted': name in r.restricted}
    if t in ('fk', 'm2m'):
        m['options'] = MODEL_OPTIONS.get(f.related_model.__name__, '')
    if t == 'choice':
        m['choices'] = [{'value': k, 'label': str(v)} for k, v in f.choices if k != '']
    if name in r.widgets:
        m['widget'] = r.widgets[name]
    req = not f.blank and t not in ('bool', 'bool3', 'json', 'm2m') and f.default is M.NOT_PROVIDED
    m['required'] = bool(req)
    return m


def value(r, obj, name, hide=False):
    f = field_of(r.model, name)
    if name in r.links:
        pk = getattr(obj, name)
        return pk, (L.label(r.links[name], pk) if pk else '')
    t = ftype(f)
    if hide and name in r.restricted:
        return None, '(restricted)'
    if t == 'm2m':
        xs = list(getattr(obj, name).all()) if obj.pk else []
        return [x.pk for x in xs], ', '.join(opt_label(f.related_model.__name__, x) for x in xs)
    if t == 'fk':
        x = getattr(obj, name)
        return (x.pk if x else None), opt_label(f.related_model.__name__, x)
    v = getattr(obj, name)
    if t == 'choice':
        return v, str(dict(f.choices).get(v, v or ''))
    if isinstance(v, Decimal):
        return float(v), None
    if isinstance(v, (date, datetime)):
        return v.isoformat(), None
    return v, None


def ser(r, obj, request, cols=None):
    hide = not r.show_restricted(request, obj)
    out = {'id': obj.pk, 'kind': r.kind}
    for n in cols or r.cols:
        v, lbl = value(r, obj, n, hide)
        out[n] = v
        if lbl is not None:
            out[n + '_label'] = lbl
    t, _ = value(r, obj, r.title)
    out['_title'] = str(t if t is not None else obj.pk)
    bp = A.bpath(r.model)   # v1.22.0: branch of the record (its site's / employee's branch)
    if bp:
        try:
            x = obj
            for part in bp.split('__'):
                x = getattr(x, part, None) if x is not None else None
            if x is not None and not hasattr(x, 'branch_name') and str(x).isdigit():
                from OrganisationManager.models import brnch_mstr
                x = brnch_mstr.objects.filter(pk=x).first()
            out['branch_name'] = getattr(x, 'branch_name', '') if x is not None else ''
        except Exception:
            out['branch_name'] = ''
    sv, sl = value(r, obj, r.subtitle, hide) if r.subtitle else (None, None)
    out['_subtitle'] = sl if sl is not None else (str(sv) if sv is not None else '')
    if hasattr(obj, 'status'):
        out['status'] = obj.status
        out['status_label'] = obj.get_status_display() if hasattr(obj, 'get_status_display') else obj.status
    return out


MODEL_KEY = {'moc': 'moc', 'health': 'health', 'audits': 'audit', 'audit-findings': 'finding', 'quality': 'nc', 'kpis': 'kpi', 'kpi-values': 'kpivalue',
             'reviews': 'review', 'ergonomics': 'ergo', 'env-aspects': 'aspect', 'env-parameters': 'environment', 'env-readings': 'reading', 'waste': 'waste',
             'chemicals': 'chemical', 'claims': 'claim', 'exposure-limits': 'limit', 'hygiene': 'hygiene', 'obligations': 'obligation',
             'compliance-assessments': 'assessment', 'documents': 'document', 'competency-types': 'ctype', 'competencies': 'competency'}


def detail(r, obj, request):
    out = ser(r, obj, request, tuple(dict.fromkeys(r.cols + r.form)))
    mk = MODEL_KEY[r.kind]
    out['can_edit'] = r.can_edit(obj, request)
    out['actions'] = [{'name': a.name, 'label': a.label, 'style': a.style, 'confirm': a.confirm,
                       'fields': [{'name': n, 'label': l, 'type': t, 'required': req} for n, l, t, req in a.fields]}
                      for a in r.actions if (a.allowed is None or a.allowed(obj, request)) and (r.manages(request) or r.is_own(obj, request)
                                                                                              or a.name in ('approve', 'reject', 'ack') or A.is_staff(request))]
    out['children'] = []
    for label, ck, fk in r.children:
        cr = REGS[ck]
        qs = cr.scope(request).filter(**{fk: obj})
        out['children'].append({'label': label, 'kind': ck, 'fk': fk, 'cols': [fmeta(cr, c) for c in cr.cols],
                                'rows': [ser(cr, x, request) for x in qs[:300]],
                                'can_add': cr.can_create(request) or (r.can_edit(obj, request) and cr.create != 'none' and r.manages(request))})
    out['steps'] = RW.steps(mk, obj.pk)
    out['history'] = RW.history(mk, obj.pk)
    out['files'] = RW.files(mk, obj.pk) if r.files else []
    out['restricted_hidden'] = not r.show_restricted(request, obj) and bool(r.restricted)
    if r.extra:
        out.update(r.extra(obj, request))
    return RW.jsonable(out)


def assign(r, obj, f, request, creating):
    hide = not r.show_restricted(request, obj)
    m2m = {}
    for n in r.form:
        if n not in f.d or (hide and n in r.restricted):
            continue
        fld = field_of(r.model, n)
        t = ftype(fld)
        if not creating and r.child_of == n:
            continue
        if n in r.links:
            if not link_off(r, n):
                setattr(obj, n, L.check_id(r.links[n], f.i(n), n))
            continue
        if t == 'fk':
            setattr(obj, n, f.obj(n, fld.related_model, False, label_of(fld)))
        elif t == 'm2m':
            m2m[n] = f.ids(n)
        elif t == 'choice':
            v = f.s(n)
            if v and v not in dict(fld.choices):
                raise S.HSEError(f'"{v}" is not a valid {label_of(fld).lower()}.', n)
            setattr(obj, n, v)
        elif t == 'bool':
            setattr(obj, n, f.b(n))
        elif t == 'bool3':
            setattr(obj, n, None if f.d.get(n) in (None, '') else f.b(n))
        elif t == 'int':
            v = f.i(n)
            setattr(obj, n, v if v is not None else (fld.default if fld.default is not M.NOT_PROVIDED else None))
        elif t == 'decimal':
            v = f.n(n)
            setattr(obj, n, v if v is not None else (fld.default if fld.default is not M.NOT_PROVIDED else None))
        elif t == 'date':
            setattr(obj, n, f.dt(n))
        elif t == 'datetime':
            setattr(obj, n, f.dtm(n))
        elif t == 'json':
            v = f.j(n)
            setattr(obj, n, v if v is not None else (fld.default() if callable(fld.default) else {}))
        else:
            setattr(obj, n, f.s(n)[:fld.max_length] if getattr(fld, 'max_length', None) else f.s(n))
    # required fields
    for n in r.form:
        fm = fmeta(r, n)
        if fm['required'] and fm['type'] not in ('m2m',):
            v = getattr(obj, n + '_id' if fm['type'] == 'fk' else n, None)
            if v in (None, ''):
                raise S.HSEError(f'{fm["label"]} is required.', n)
    return m2m


def save(r, obj, f, request, creating):
    old = r.model.objects.get(pk=obj.pk) if obj.pk else None
    with transaction.atomic():
        m2m = assign(r, obj, f, request, creating)
        if creating:
            if r.number:
                obj.number = S.next_number(r.number)
            if r.defaults:
                r.defaults(obj, request)
        if r.check:
            r.check(obj, request)
        if r.saver:
            res = r.saver(obj, request, creating, old)
            obj = res if isinstance(res, r.model) else obj
        else:
            obj.save()
        for n, ids in m2m.items():
            rel = field_of(r.model, n).related_model
            getattr(obj, n).set(rel.objects.filter(pk__in=ids))
        mk = MODEL_KEY[r.kind]
        if creating:
            S.event(mk, obj.pk, f'Created by {S.user_name(request.user)}', request.user)
            for x in f.filelist('files'):
                HSEFile.objects.create(model=mk, object_id=obj.pk, file=x, name=x.name[:255], size=x.size or 0, uploaded_by=request.user)
        elif old is not None:
            changed = []
            for n in r.form:
                fld = field_of(r.model, n)
                if isinstance(fld, M.ManyToManyField):
                    continue
                if getattr(old, fld.attname) != getattr(obj, fld.attname):
                    changed.append(label_of(fld) if n not in r.restricted else label_of(fld) + ' (restricted)')
            if changed:
                S.event(mk, obj.pk, 'Changed: ' + ', '.join(changed), request.user)
    return obj


# ------------------------------------------------------------------ views
def get_reg(kind, request):
    r = REGS.get(kind)
    if r is None:
        raise Http404
    A.need(r.can_open(request), f'You have no access to {r.plural.lower()}.')
    return r


class RegistersView(Base):
    @guard
    def get(self, request):
        out = []
        for r in REGS.values():
            if not r.can_open(request):
                continue
            out.append({'kind': r.kind, 'label': r.label, 'plural': r.plural, 'group': r.group, 'help': r.help, 'child_of': r.child_of,
                        'cols': [fmeta(r, c) for c in r.cols if not link_off(r, c)]
                        + ([{'name': 'branch_name', 'label': 'Branch', 'type': 'text', 'help': ''}] if A.bpath(r.model) else []),   # v1.22.0
                        'form': [fmeta(r, c) for c in r.form if not link_off(r, c)],
                        'filters': [fmeta(r, c) for c in r.filters], 'can_create': r.can_create(request) and r.create != 'none',
                        'manages': r.manages(request), 'files': r.files})
        return Response(out)


class RegisterListView(Base):
    def zeo_model_for(self, kwargs):   # v1.38.0: the form designer asks the list screen for its model (extra fields on the HSE registers)
        r = REGS.get((kwargs or {}).get('kind'))
        return r.model if r is not None else None

    @guard
    def get(self, request, kind):
        r = get_reg(kind, request)
        q = request.query_params
        qs = r.scope(request)
        if q.get('q'):
            cond = Q()
            for s_ in r.search:
                cond |= Q(**{s_ + '__icontains': q['q']})
            qs = qs.filter(cond)
        for flt in r.filters:
            if q.get(flt) not in (None, ''):
                fld = field_of(r.model, flt)
                v = q[flt]
                if isinstance(fld, M.BooleanField):
                    v = v in ('1', 'true', 'yes')
                qs = qs.filter(**{flt: v})
        if q.get('parent'):
            fk, _, pid = q['parent'].partition(':')
            if fk in [n.name for n in r.model._meta.fields]:
                qs = qs.filter(**{fk: pid})
        if q.get('open') == '1' and hasattr(r.model, 'status'):
            qs = qs.exclude(status__in=('closed', 'cancelled', 'archived', 'superseded', 'rejected', 'disposed'))
        if str(q.get('branch') or '').isdigit() and A.bpath(r.model):   # v1.22.0 Branch filter
            qs = qs.filter(**{A.bpath(r.model): int(q['branch'])})
        limit = max(1, min(1000, int(q.get('limit') or 200)))
        offset = max(0, int(q.get('offset') or 0))
        return Response({'count': qs.count(), 'results': [ser(r, x, request) for x in qs[offset:offset + limit]]})

    @guard
    def post(self, request, kind):
        r = get_reg(kind, request)
        f = In(request)
        allowed = r.can_create(request) and r.create != 'none'
        if not allowed and r.child_of and f.has(r.child_of):
            parent_fld = field_of(r.model, r.child_of)
            p = parent_fld.related_model.objects.filter(pk=f.i(r.child_of)).first()
            pr = next((x for x in REGS.values() if x.model is parent_fld.related_model), None)
            allowed = bool(p and pr and pr.can_edit(p, request) and pr.manages(request))
        A.need(allowed, f'You cannot add {r.plural.lower()}.')
        obj = save(r, r.model(), f, request, True)
        return Response(detail(r, r.model.objects.get(pk=obj.pk), request), status=status.HTTP_201_CREATED)


class RegisterDetailView(Base):
    def zeo_model_for(self, kwargs):   # v1.22.0 record panel
        r = REGS.get((kwargs or {}).get('kind'))
        return r.model if r is not None else None

    @guard
    def get(self, request, kind, pk):
        r = get_reg(kind, request)
        obj = r.scope(request).filter(pk=pk).first()
        if obj is None:
            raise Http404
        return Response(detail(r, obj, request))

    @guard
    def patch(self, request, kind, pk):
        r = get_reg(kind, request)
        obj = r.scope(request).filter(pk=pk).first()
        if obj is None:
            raise Http404
        if not r.can_edit(obj, request):
            raise S.HSEError('This record can no longer be changed here – use its workflow actions.' if (r.manages(request) or r.is_own(obj, request))
                             else 'You cannot change this record.', status=403)
        obj = save(r, obj, In(request), request, False)
        return Response(detail(r, r.model.objects.get(pk=obj.pk), request))

    put = patch


class RegisterActionView(Base):
    @guard
    def post(self, request, kind, pk, action):
        r = get_reg(kind, request)
        obj = r.scope(request).filter(pk=pk).first()
        if obj is None:
            raise Http404
        a = next((x for x in r.actions if x.name == action), None)
        if a is None:
            raise Http404
        if a.allowed is not None and not a.allowed(obj, request):
            raise S.HSEError(f'"{a.label}" is not possible now.', status=400)
        A.need(r.manages(request) or r.is_own(obj, request) or A.is_staff(request) or action in ('approve', 'reject', 'ack'),
               'You cannot do this.')
        f = In(request)
        for n, l, t, req in a.fields:
            if req and t not in ('bool', 'file', 'files') and f.d.get(n) in (None, ''):
                raise S.HSEError(f'{l} is required.', n)
        with transaction.atomic():
            res = a.run(obj, request, f)
        if isinstance(res, tuple):
            res = next((x for x in res if hasattr(x, '_meta')), None)
        if res is not None and hasattr(res, '_meta') and res.__class__ is not obj.__class__:
            other = next((x for x in REGS.values() if x.model is res.__class__), None)
            body = detail(r, r.model.objects.get(pk=obj.pk), request)
            if other:
                body['created'] = {'kind': other.kind, 'id': res.pk}
            elif res.__class__ is Action:
                body['created'] = {'kind': 'action', 'id': res.pk, 'number': res.number}
            elif res.__class__ is Incident:
                body['created'] = {'kind': 'incident', 'id': res.pk, 'number': res.number}
            return Response(body)
        if res is not None and res.__class__ is obj.__class__ and res.pk != obj.pk:
            body = detail(r, res, request)
            body['created'] = {'kind': r.kind, 'id': res.pk}
            return Response(body)
        return Response(detail(r, r.model.objects.get(pk=obj.pk), request))


class RegisterFileView(Base):
    @guard
    def post(self, request, kind, pk):
        r = get_reg(kind, request)
        obj = r.scope(request).filter(pk=pk).first()
        if obj is None:
            raise Http404
        A.need(r.manages(request) or r.is_own(obj, request) or A.is_staff(request), 'You cannot attach files here.')
        f = In(request)
        xs = f.filelist()
        if not xs:
            raise S.HSEError('Choose a file.', 'files')
        mk = MODEL_KEY[r.kind]
        for x in xs:
            if (x.size or 0) > 20 * 1024 * 1024:
                raise S.HSEError(f'{x.name} is larger than 20 MB.', 'files')
            HSEFile.objects.create(model=mk, object_id=obj.pk, file=x, name=x.name[:255], size=x.size or 0, note=f.s('note')[:255], uploaded_by=request.user)
        S.event(mk, obj.pk, f'{len(xs)} file(s) added: ' + ', '.join(x.name for x in xs), request.user)
        return Response(detail(r, obj, request), status=status.HTTP_201_CREATED)


def file_allowed(request, hf):
    """Files of register records follow the record's visibility."""
    for r in REGS.values():
        if MODEL_KEY[r.kind] == hf.model:
            if hf.model == 'claim' or (hf.model == 'health'):
                ok = r.scope(request).filter(pk=hf.object_id).exists() and r.show_restricted(request)
            else:
                ok = r.can_open(request) and r.scope(request).filter(pk=hf.object_id).exists()
            return True, ok
    return False, False


class OptionsView(Base):
    @guard
    def get(self, request, name):
        if name in L.OPTION_LINK:   # v1.17.0 records of other ZEO apps (empty when the link is off)
            ids = [int(x) for x in (request.query_params.get('ids') or '').split(',') if x.strip().isdigit()]
            return Response(L.options(name, request.query_params.get('q') or '', ids or None, int(request.query_params.get('limit') or 500)))
        if name not in OPTIONS:
            raise Http404
        qs_fn, lbl, search = OPTIONS[name]
        qs = qs_fn()
        q = request.query_params.get('q')
        if q:
            cond = Q()
            for s_ in search:
                cond |= Q(**{s_ + '__icontains': q})
            qs = qs.filter(cond)
        ids = request.query_params.get('ids')
        if ids:
            qs = qs.filter(pk__in=[int(x) for x in ids.split(',') if x.strip().isdigit()])
        return Response([{'id': x.pk, 'label': lbl(x)} for x in qs[:int(request.query_params.get('limit') or 500)]])


class RegisterPdfView(Base):
    @guard
    def get(self, request, kind, pk):
        r = get_reg(kind, request)
        obj = r.scope(request).filter(pk=pk).first()
        if obj is None:
            raise Http404
        d = detail(r, obj, request)
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
        buf = BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=14 * mm, title=d['_title'])
        ss = getSampleStyleSheet()
        sm = ParagraphStyle('s', parent=ss['Normal'], fontSize=8.5, leading=11)
        P = lambda x: Paragraph(str(x if x not in (None, '') else '-').replace('&', '&amp;').replace('<', '&lt;').replace('\n', '<br/>'), sm)
        story = [Paragraph(f'{r.label}: {d["_title"]}', ss['Title']), Paragraph(str(d.get('_subtitle') or ''), ss['Normal']), Spacer(1, 6)]
        rows = []
        for n in dict.fromkeys(r.cols + r.form):
            fm = fmeta(r, n)
            if fm['type'] == 'json':
                continue
            v = d.get(n + '_label', d.get(n))
            if isinstance(v, bool):
                v = 'Yes' if v else 'No'
            rows.append([P(fm['label']), P(v)])
        if 'status_label' in d:
            rows.insert(0, [P('Status'), P(d['status_label'])])
        t = Table(rows, colWidths=[50 * mm, 132 * mm])
        t.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), 0.3, colors.lightgrey), ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#f1f5f9')),
                               ('VALIGN', (0, 0), (-1, -1), 'TOP')]))
        story.append(t)
        if kind == 'audits' and d.get('checklist'):
            ans = obj.answers or {}
            rows = [[P('Question'), P('Result'), P('Note')]] + [[P(q['text']), P((ans.get(q['key']) or {}).get('value', '')), P((ans.get(q['key']) or {}).get('note', ''))]
                                                                for q in d['checklist']]
            ct = Table(rows, colWidths=[100 * mm, 28 * mm, 54 * mm], repeatRows=1)
            ct.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), 0.3, colors.lightgrey), ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e2e8f0'))]))
            story += [Spacer(1, 8), Paragraph('Checklist', ss['Heading3']), ct]
        for ch in d.get('children', []):
            if not ch['rows']:
                continue
            cols = ch['cols'][:6]
            rows = [[P(c['label']) for c in cols]] + [[P(x.get(c['name'] + '_label', x.get(c['name']))) for c in cols] for x in ch['rows']]
            ct = Table(rows, repeatRows=1)
            ct.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), 0.3, colors.lightgrey), ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e2e8f0'))]))
            story += [Spacer(1, 8), Paragraph(ch['label'], ss['Heading3']), ct]
        if d.get('actions_list'):
            rows = [[P('Action'), P('Title'), P('Owner'), P('Due'), P('Status')]] + [[P(a['number']), P(a['title']), P((a.get('owner') or {}).get('name', '')),
                                                                                        P(a['due_date']), P(a['status_label'])] for a in d['actions_list']]
            ct = Table(rows, colWidths=[24 * mm, 70 * mm, 36 * mm, 22 * mm, 30 * mm], repeatRows=1)
            ct.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), 0.3, colors.lightgrey), ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e2e8f0'))]))
            story += [Spacer(1, 8), Paragraph('Corrective actions', ss['Heading3']), ct]
        if d.get('history'):
            rows = [[P(h.get('at', '')[:16].replace('T', ' ')), P(h.get('by', '')), P(h.get('text', ''))] for h in d['history'][:60]]
            ht = Table(rows, colWidths=[30 * mm, 36 * mm, 116 * mm])
            ht.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), 0.3, colors.lightgrey)]))
            story += [Spacer(1, 8), Paragraph('History', ss['Heading3']), ht]
        doc.build(story)
        resp = HttpResponse(buf.getvalue(), content_type='application/pdf')
        resp['Content-Disposition'] = f'attachment; filename="{d["_title"]}.pdf"'
        return resp

