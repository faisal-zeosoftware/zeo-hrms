"""
ZEO HSE (v1.17.0) – health, safety and environment on top of ZEO: shared registers (sites, contractors, risk matrix,
form templates, permit types, approval matrices, hazard library), the operational records (risk assessments,
permits to work, incidents, inspections, observations, toolbox talks) and one central action register (CAPA).

Industry packs (packs/) fill the configuration for the company's industry when the company is created
(company.industry_type) – Construction & engineering, Manufacturing, Hospitals & healthcare, Oil & gas – on top of a
general pack. Pack content is marked "to review": permit types and forms can be used only after an HSE manager has
reviewed them for the company's real hazards, procedures and legal requirements.

Existing ZEO tables are not changed (employees, users, branches are referenced by foreign key).
"""
from django.conf import settings
from django.db import models

USER = settings.AUTH_USER_MODEL
EMP = 'EmpManagement.emp_master'
BRANCH = 'OrganisationManager.brnch_mstr'

INDUSTRIES = (('general', 'General'), ('construction', 'Construction and engineering'), ('manufacturing', 'Manufacturing'),
              ('healthcare', 'Hospitals and healthcare'), ('oil_gas', 'Oil and gas'))
# company.industry_type (UserManagement) → pack
INDUSTRY_FROM_COMPANY = {'CON': 'construction', 'MFG': 'manufacturing', 'HC': 'healthcare', 'OG': 'oil_gas'}

REVIEW = (('to_review', 'To review'), ('reviewed', 'Reviewed'))
# HSE roles – permissions on HSESettings, given through the "HSE – …" groups (zeohse_setup --roles)
ROLES = (
    ('hse_manager', 'HSE manager'), ('hse_officer', 'HSE officer'), ('site_manager', 'Site / operations manager'),
    ('permit_issuer', 'Permit issuer'), ('area_authority', 'Area authority'), ('gas_tester', 'Authorised gas tester'),
    ('supervisor', 'Supervisor / permit receiver'), ('occ_health', 'Occupational health officer'),
    ('quality_manager', 'Quality manager'), ('env_officer', 'Environmental officer'), ('infection_control', 'Infection control'),
    ('auditor', 'Auditor'), ('claims_officer', 'Claims officer'), ('moc_coordinator', 'Change (MOC) coordinator'),
    ('dept_head', 'Department head'), ('senior_mgmt', 'Senior management (read only)'),
)


class HSESettings(models.Model):
    industry = models.CharField(max_length=20, choices=INDUSTRIES, default='general')
    packs_installed = models.JSONField(default=list, blank=True)
    pack_installed_at = models.DateTimeField(null=True, blank=True)
    # v1.17.0 – HSE is activated per company: the HSE manager / admin chooses the app's industry (suggested from the company master)
    activated_at = models.DateTimeField(null=True, blank=True)
    activated_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    industry_source = models.CharField(max_length=10, blank=True, help_text='company = same as the company master; chosen = picked for HSE')
    incident_prefix = models.CharField(max_length=10, default='INC')
    permit_prefix = models.CharField(max_length=10, default='PTW')
    risk_prefix = models.CharField(max_length=10, default='RA')
    action_prefix = models.CharField(max_length=10, default='CAPA')
    inspection_prefix = models.CharField(max_length=10, default='INS')
    observation_prefix = models.CharField(max_length=10, default='OBS')
    toolbox_prefix = models.CharField(max_length=10, default='TBT')
    rate_multiplier = models.PositiveIntegerField(default=200000, help_text='TRIR / LTIFR base hours (200 000 or 1 000 000)')
    hours_worked_month = models.PositiveIntegerField(default=0, help_text='Manual hours worked per month when not taken from attendance')
    likelihood_labels = models.JSONField(default=list, blank=True)
    severity_labels = models.JSONField(default=list, blank=True)
    risk_bands = models.JSONField(default=list, blank=True, help_text='[{code, label, min, max, color, block}]')
    require_review = models.BooleanField(default=True, help_text='Pack items can be used only after an HSE manager reviewed them')
    action_reminder_days = models.PositiveSmallIntegerField(default=3)
    block_expired_competency = models.BooleanField(default=True, help_text='An expired or missing competency blocks permit issue (else a warning)')
    significance_threshold = models.PositiveSmallIntegerField(default=9, help_text='Environmental aspect is significant at severity × probability ≥ this')
    incompatible_groups = models.JSONField(default=list, blank=True, help_text='[[group, group], …] chemical groups that must not be stored together')
    # v1.17.0 links with other ZEO apps (each works only when that app is installed; HSE runs alone otherwise)
    links_off = models.JSONField(default=list, blank=True, help_text='Links the HSE manager switched off')
    fatigue_max_hours = models.PositiveSmallIntegerField(default=12, help_text='Warn on a permit when a crew member worked more than this the day before (0 = off)')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'HSE settings'
        permissions = [(f'hse_{k}' if not k.startswith('hse_') else k, f'HSE role: {v}') for k, v in ROLES]

    @classmethod
    def get(cls):
        # ZEO 1.21.0: HSE is merged into ZEO and activated in Settings → Apps (or on its activation page); a company that
        # switched HSE off keeps its packs and data, so "has a pack" no longer means "active" (the add-on's pre-1.17 rule is gone)
        return cls.objects.order_by('id').first() or cls.objects.create()

    @classmethod
    def activated(cls):
        return cls.get().activated_at is not None


class Sequence(models.Model):
    kind = models.CharField(max_length=20, unique=True)
    last = models.PositiveIntegerField(default=0)


class Site(models.Model):
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=150)
    kind = models.CharField(max_length=60, blank=True, help_text='Project site, plant, hospital, platform, warehouse …')
    branch = models.ForeignKey(BRANCH, null=True, blank=True, on_delete=models.SET_NULL, related_name='hse_sites')
    address = models.CharField(max_length=255, blank=True)
    manager = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='hse_sites')
    is_active = models.BooleanField(default=True)
    project_id = models.IntegerField(null=True, blank=True, db_index=True, help_text='Project (Projects app) this site belongs to')
    cost_center_id = models.IntegerField(null=True, blank=True, help_text='Cost centre (Expenses app)')

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Area(models.Model):
    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name='areas')
    name = models.CharField(max_length=150)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['site__name', 'name']
        unique_together = (('site', 'name'),)

    def __str__(self):
        return f'{self.site.name} / {self.name}'


class Contractor(models.Model):
    STATUS = (('pending', 'Waiting for approval'), ('approved', 'Approved'), ('suspended', 'Suspended'))
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=200)
    trade = models.CharField(max_length=120, blank=True)
    contact_name = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)
    trade_licence_no = models.CharField(max_length=60, blank=True)
    licence_expiry = models.DateField(null=True, blank=True)
    insurance_expiry = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default='pending')
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


# ------------------------------------------------------------------ pack-sourced configuration
class PackItem(models.Model):
    pack = models.CharField(max_length=20, blank=True, help_text='Industry pack this came from (empty = made by the company)')
    review_status = models.CharField(max_length=10, choices=REVIEW, default='reviewed')
    reviewed_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        abstract = True


class Term(PackItem):
    key = models.CharField(max_length=40, unique=True)
    label = models.CharField(max_length=120)


class FormTemplate(PackItem):
    KINDS = (('inspection', 'Inspection checklist'), ('permit_checklist', 'Permit checklist'), ('risk_assessment', 'Risk assessment'),
             ('method_statement', 'Method statement'), ('toolbox_talk', 'Toolbox talk'), ('prequalification', 'Contractor pre-qualification'),
             ('drill', 'Emergency drill'), ('audit', 'Audit checklist'), ('other', 'Other form'))
    kind = models.CharField(max_length=20, choices=KINDS)
    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    questions = models.JSONField(default=list, blank=True,
                                 help_text='[{key, section, text, type: yesno|text|number|date, required, critical, hint}]')
    frequency = models.CharField(max_length=40, blank=True, help_text='Daily, weekly, before use …')
    version = models.PositiveSmallIntegerField(default=1)

    class Meta:
        ordering = ['kind', 'name']


class PermitType(PackItem):
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    color = models.CharField(max_length=7, default='#5b4fe0')
    max_hours = models.PositiveSmallIntegerField(default=12, help_text='Longest validity before revalidation')
    requires_risk_assessment = models.BooleanField(default=True)
    requires_method_statement = models.BooleanField(default=False)
    requires_gas_test = models.BooleanField(default=False)
    requires_isolation = models.BooleanField(default=False)
    requires_contractor_approval = models.BooleanField(default=True)
    gas_limits = models.JSONField(default=dict, blank=True, help_text='{"O2": [19.5, 23.5], "LEL": [0, 10], "H2S": [0, 5], "CO": [0, 25]}')
    gas_retest_hours = models.PositiveSmallIntegerField(default=0, help_text='Gas test older than this blocks issue / continuation (0 = not checked)')
    checklist = models.ForeignKey(FormTemplate, null=True, blank=True, on_delete=models.SET_NULL, related_name='permit_types')
    approval_steps = models.JSONField(default=list, blank=True, help_text='[{role, label}] in order')
    simops_conflicts = models.JSONField(default=list, blank=True, help_text='Permit type codes that must not run in the same area at the same time')
    required_competencies = models.JSONField(default=list, blank=True, help_text='Competency codes every crew member and the receiver must hold')

    class Meta:
        ordering = ['name']


class ApprovalRule(PackItem):
    """Approval matrix: who approves what. subject = risk | incident | action; key = risk band / severity / '*'."""
    SUBJECTS = (('risk', 'Risk assessment'), ('incident', 'Incident closure'), ('action', 'Corrective action'), ('moc', 'Change (MOC)'),
                ('audit', 'Audit report'), ('claim', 'Claim settlement'), ('document', 'Document'), ('compliance', 'Compliance assessment'),
                ('quality', 'Quality disposition'))
    subject = models.CharField(max_length=10, choices=SUBJECTS)
    key = models.CharField(max_length=20, help_text='Risk band code, incident severity 1–5, or * for all')
    steps = models.JSONField(default=list, blank=True, help_text='[{role, label}]')
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = (('subject', 'key'),)
        ordering = ['subject', 'key']


class HazardLibrary(PackItem):
    category = models.CharField(max_length=80)
    activity = models.CharField(max_length=200)
    hazard = models.CharField(max_length=255)
    consequence = models.CharField(max_length=255, blank=True)
    controls = models.TextField(blank=True)
    likelihood = models.PositiveSmallIntegerField(default=3)
    severity = models.PositiveSmallIntegerField(default=3)

    class Meta:
        ordering = ['category', 'activity']
        verbose_name_plural = 'hazard library'


class IncidentCategory(PackItem):
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=20, default='injury', help_text='injury, near_miss, property, environment, occupational, quality, security, other')
    restricted = models.BooleanField(default=False, help_text='Health details: visible only to HSE manager and occupational health')

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'incident categories'


# ------------------------------------------------------------------ records
class RiskAssessment(models.Model):
    KINDS = (('hira', 'HIRA'), ('jsa', 'Job safety analysis'), ('tra', 'Task risk assessment'), ('aspect', 'Environmental aspects'),
             ('health', 'Health risk assessment'), ('icra', 'Infection control risk assessment'))
    STATUS = (('draft', 'Draft'), ('submitted', 'Waiting for approval'), ('approved', 'Approved'), ('rejected', 'Rejected'),
              ('superseded', 'Superseded'))
    number = models.CharField(max_length=30, unique=True)
    kind = models.CharField(max_length=10, choices=KINDS, default='tra')
    title = models.CharField(max_length=255)
    site = models.ForeignKey(Site, null=True, blank=True, on_delete=models.SET_NULL, related_name='risk_assessments')
    area = models.ForeignKey(Area, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    activity = models.TextField(blank=True)
    team = models.CharField(max_length=255, blank=True)
    owner = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='hse_risk_assessments')
    status = models.CharField(max_length=12, choices=STATUS, default='draft', db_index=True)
    review_date = models.DateField(null=True, blank=True)
    review_required = models.BooleanField(default=False, help_text='Set after a material change or an incident – revise before further use')
    review_reason = models.CharField(max_length=255, blank=True)
    previous = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='revisions')
    approved_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class RiskLine(models.Model):
    assessment = models.ForeignKey(RiskAssessment, on_delete=models.CASCADE, related_name='lines')
    sequence = models.PositiveSmallIntegerField(default=1)
    step = models.CharField(max_length=255, blank=True, help_text='Job step / activity')
    hazard = models.CharField(max_length=255)
    consequence = models.CharField(max_length=255, blank=True)
    existing_controls = models.TextField(blank=True)
    likelihood = models.PositiveSmallIntegerField(default=3)
    severity = models.PositiveSmallIntegerField(default=3)
    additional_controls = models.TextField(blank=True)
    residual_likelihood = models.PositiveSmallIntegerField(default=2)
    residual_severity = models.PositiveSmallIntegerField(default=3)
    action_owner = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['assessment', 'sequence', 'id']


class Permit(models.Model):
    STATUS = (('draft', 'Draft'), ('submitted', 'Waiting for approval'), ('approved', 'Approved – ready to issue'),
              ('active', 'Issued – work in progress'), ('suspended', 'Suspended'), ('closed', 'Closed'), ('cancelled', 'Cancelled'),
              ('rejected', 'Rejected'), ('expired', 'Expired'))
    number = models.CharField(max_length=30, unique=True)
    permit_type = models.ForeignKey(PermitType, on_delete=models.PROTECT, related_name='permits')
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name='permits')
    area = models.ForeignKey(Area, null=True, blank=True, on_delete=models.SET_NULL, related_name='permits')
    location = models.CharField(max_length=255, blank=True, help_text='Exact place / equipment tag')
    description = models.TextField()
    equipment = models.CharField(max_length=255, blank=True)
    asset_id = models.IntegerField(null=True, blank=True, db_index=True, help_text='Equipment from the asset register')
    contractor = models.ForeignKey(Contractor, null=True, blank=True, on_delete=models.SET_NULL, related_name='permits')
    applicant = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='hse_permits')
    receiver = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='hse_received_permits',
                                 help_text='Supervisor in charge of the work team')
    crew_size = models.PositiveSmallIntegerField(default=1)
    crew = models.ManyToManyField(EMP, blank=True, related_name='hse_permit_crews')
    valid_from = models.DateTimeField()
    valid_to = models.DateTimeField()
    risk_assessment = models.ForeignKey(RiskAssessment, null=True, blank=True, on_delete=models.SET_NULL, related_name='permits')
    method_statement = models.TextField(blank=True)
    checklist_answers = models.JSONField(default=dict, blank=True)
    precautions = models.TextField(blank=True, help_text='Conditions written on the permit')
    status = models.CharField(max_length=12, choices=STATUS, default='draft', db_index=True)
    issued_at = models.DateTimeField(null=True, blank=True)
    issued_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    team_briefed = models.BooleanField(default=False)
    closed_at = models.DateTimeField(null=True, blank=True)
    closure_note = models.TextField(blank=True)
    suspension_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class GasTest(models.Model):
    permit = models.ForeignKey(Permit, on_delete=models.CASCADE, related_name='gas_tests')
    tested_at = models.DateTimeField()
    o2 = models.DecimalField('O₂ %', max_digits=5, decimal_places=2, null=True, blank=True)
    lel = models.DecimalField('LEL %', max_digits=5, decimal_places=2, null=True, blank=True)
    h2s = models.DecimalField('H₂S ppm', max_digits=7, decimal_places=2, null=True, blank=True)
    co = models.DecimalField('CO ppm', max_digits=7, decimal_places=2, null=True, blank=True)
    instrument = models.CharField(max_length=80, blank=True)
    tester = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    passed = models.BooleanField(default=False)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-tested_at']


class Isolation(models.Model):
    KINDS = (('electrical', 'Electrical'), ('mechanical', 'Mechanical'), ('process', 'Process / piping'), ('pneumatic', 'Pneumatic'),
             ('hydraulic', 'Hydraulic'), ('other', 'Other'))
    permit = models.ForeignKey(Permit, on_delete=models.CASCADE, related_name='isolations')
    point = models.CharField(max_length=150, help_text='Isolation point / tag')
    kind = models.CharField(max_length=12, choices=KINDS, default='electrical')
    method = models.CharField(max_length=150, blank=True, help_text='Lock, blind, double block and bleed …')
    lock_no = models.CharField(max_length=40, blank=True)
    isolated_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    isolated_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    verified_at = models.DateTimeField(null=True, blank=True)
    removed_at = models.DateTimeField(null=True, blank=True)


class Incident(models.Model):
    STATUS = (('reported', 'Reported'), ('classified', 'Classified'), ('investigation', 'Under investigation'),
              ('actions', 'Actions in progress'), ('closed', 'Closed'))
    TREATMENT = (('none', 'No treatment'), ('first_aid', 'First aid'), ('medical', 'Medical treatment'),
                 ('restricted', 'Restricted work'), ('lost_time', 'Lost time'), ('fatality', 'Fatality'))
    number = models.CharField(max_length=30, unique=True)
    category = models.ForeignKey(IncidentCategory, null=True, blank=True, on_delete=models.SET_NULL, related_name='incidents')
    title = models.CharField(max_length=255)
    description = models.TextField()
    occurred_at = models.DateTimeField()
    reported_at = models.DateTimeField(auto_now_add=True)
    reported_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='hse_reported_incidents')
    site = models.ForeignKey(Site, null=True, blank=True, on_delete=models.SET_NULL, related_name='incidents')
    area = models.ForeignKey(Area, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    location = models.CharField(max_length=255, blank=True)
    injured_employee = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='hse_injuries')
    injured_name = models.CharField(max_length=150, blank=True, help_text='Contractor / visitor / patient')
    body_part = models.CharField(max_length=100, blank=True)
    treatment = models.CharField(max_length=12, choices=TREATMENT, default='none')
    actual_severity = models.PositiveSmallIntegerField(default=1)
    potential_severity = models.PositiveSmallIntegerField(default=1)
    high_potential = models.BooleanField(default=False)
    immediate_actions = models.TextField(blank=True)
    witnesses = models.TextField(blank=True)
    estimated_loss = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    status = models.CharField(max_length=14, choices=STATUS, default='reported', db_index=True)
    investigation_required = models.BooleanField(default=False)
    investigator = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='hse_investigations')
    root_cause = models.JSONField(default=dict, blank=True, help_text='{method: 5whys|fishbone, whys: [], immediate, underlying}')
    findings = models.TextField(blank=True)
    lessons_learned = models.TextField(blank=True)
    lost_days = models.PositiveSmallIntegerField(default=0)
    restricted_days = models.PositiveSmallIntegerField(default=0)
    reportable = models.BooleanField(default=False)
    authority_ref = models.CharField(max_length=120, blank=True)
    report_due = models.DateField(null=True, blank=True)
    permit = models.ForeignKey(Permit, null=True, blank=True, on_delete=models.SET_NULL, related_name='incidents')
    contractor = models.ForeignKey(Contractor, null=True, blank=True, on_delete=models.SET_NULL, related_name='incidents')
    risk_assessment = models.ForeignKey(RiskAssessment, null=True, blank=True, on_delete=models.SET_NULL, related_name='incidents')
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    # v1.17.0 links
    asset_id = models.IntegerField(null=True, blank=True, db_index=True)
    asset_damage_id = models.IntegerField(null=True, blank=True)
    leave_ids = models.JSONField(default=list, blank=True, help_text='Sick / injury leave requests counted as lost days')
    alert_id = models.IntegerField(null=True, blank=True, help_text='Safety alert announcement')
    project_risk_id = models.IntegerField(null=True, blank=True)

    class Meta:
        ordering = ['-occurred_at']


class Inspection(models.Model):
    STATUS = (('draft', 'In progress'), ('completed', 'Completed'))
    number = models.CharField(max_length=30, unique=True)
    template = models.ForeignKey(FormTemplate, on_delete=models.PROTECT, related_name='inspections')
    site = models.ForeignKey(Site, null=True, blank=True, on_delete=models.SET_NULL, related_name='inspections')
    area = models.ForeignKey(Area, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    subject = models.CharField(max_length=200, blank=True, help_text='Equipment, scaffold tag, ward …')
    asset_id = models.IntegerField(null=True, blank=True, db_index=True)
    inspector = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='hse_inspections')
    date = models.DateField()
    answers = models.JSONField(default=dict, blank=True, help_text='{question key: {value, note}}')
    score = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default='draft')
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['-date', '-id']


class Observation(models.Model):
    KINDS = (('unsafe_act', 'At-risk behaviour'), ('unsafe_condition', 'Unsafe condition'), ('near_miss', 'Near-miss hazard'),
             ('safe_act', 'Safe behaviour'), ('environment', 'Environmental concern'), ('suggestion', 'Positive safety suggestion'))
    CATEGORIES = (('ppe', 'PPE compliance'), ('manual_handling', 'Manual handling'), ('height', 'Work at height'), ('housekeeping', 'Housekeeping'),
                  ('guarding', 'Machine guarding'), ('permit', 'Permit compliance'), ('loto', 'LOTO and isolation'), ('driving', 'Driving / vehicles'),
                  ('tools', 'Tools and equipment'), ('other', 'Other'))
    RISK = (('low', 'Low'), ('medium', 'Medium'), ('high', 'High – serious hazard'))
    STATUS = (('open', 'Open'), ('actioned', 'Action raised'), ('closed', 'Closed'))
    number = models.CharField(max_length=30, unique=True)
    kind = models.CharField(max_length=20, choices=KINDS)
    description = models.TextField()
    site = models.ForeignKey(Site, null=True, blank=True, on_delete=models.SET_NULL, related_name='observations')
    area = models.ForeignKey(Area, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    location = models.CharField(max_length=255, blank=True)
    immediate_action = models.TextField(blank=True, help_text='Discussion / intervention and immediate correction')
    category = models.CharField(max_length=20, choices=CATEGORIES, default='other')
    task = models.CharField(max_length=200, blank=True)
    observed_risk = models.CharField(max_length=10, choices=RISK, default='low')
    confidential = models.BooleanField(default=False, help_text='Reporter name visible only to the HSE manager')
    escalated_incident = models.ForeignKey('Incident', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    grievance_id = models.IntegerField(null=True, blank=True, unique=True, help_text='Safety complaint passed on from Self-service')
    reported_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    reported_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=10, choices=STATUS, default='open')

    class Meta:
        ordering = ['-reported_at']


class ToolboxTalk(models.Model):
    number = models.CharField(max_length=30, unique=True)
    topic = models.CharField(max_length=200)
    template = models.ForeignKey(FormTemplate, null=True, blank=True, on_delete=models.SET_NULL, related_name='talks')
    site = models.ForeignKey(Site, null=True, blank=True, on_delete=models.SET_NULL, related_name='toolbox_talks')
    date = models.DateField()
    presenter = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    attendees = models.ManyToManyField(EMP, blank=True, related_name='hse_toolbox_talks')
    other_attendees = models.TextField(blank=True, help_text='Contractor workers, one per line')
    key_points = models.TextField(blank=True)
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['-date', '-id']


class Action(models.Model):
    """Central CAPA register – every corrective / preventive action, whatever raised it."""
    SOURCES = (('incident', 'Incident'), ('inspection', 'Inspection'), ('permit', 'Permit'), ('risk', 'Risk assessment'),
               ('observation', 'BBS observation'), ('moc', 'Change (MOC)'), ('audit', 'Audit finding'), ('quality', 'Quality NC'),
               ('environment', 'Environmental'), ('hygiene', 'Industrial hygiene'), ('compliance', 'Compliance'), ('review', 'Management review'),
               ('ergonomics', 'Ergonomics'), ('chemical', 'Chemical'), ('health', 'Occupational health'), ('kpi', 'KPI deviation'),
               ('manual', 'Other'))
    STATUS = (('open', 'Open'), ('in_progress', 'In progress'), ('done', 'Done – waiting for verification'), ('closed', 'Verified and closed'),
              ('rejected', 'Not effective – reopened'), ('cancelled', 'Cancelled'))
    PRIORITY = (('low', 'Low'), ('medium', 'Medium'), ('high', 'High'), ('critical', 'Critical'))
    number = models.CharField(max_length=30, unique=True)
    source = models.CharField(max_length=12, choices=SOURCES, default='manual')
    source_id = models.IntegerField(null=True, blank=True)
    source_ref = models.CharField(max_length=60, blank=True)
    kind = models.CharField(max_length=12, default='corrective', help_text='corrective / preventive')
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    site = models.ForeignKey(Site, null=True, blank=True, on_delete=models.SET_NULL, related_name='actions')
    owner = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='hse_actions')
    verifier = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='hse_verifications')
    priority = models.CharField(max_length=10, choices=PRIORITY, default='medium')
    due_date = models.DateField()
    status = models.CharField(max_length=12, choices=STATUS, default='open', db_index=True)
    evidence = models.TextField(blank=True)
    done_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    verification_note = models.TextField(blank=True)
    reminded_on = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['due_date', 'id']


class ApprovalStep(models.Model):
    """One approval of a permit, risk assessment or incident closure (from the permit type / approval matrix)."""
    model = models.CharField(max_length=20, db_index=True)
    object_id = models.IntegerField(db_index=True)
    sequence = models.PositiveSmallIntegerField(default=1)
    role = models.CharField(max_length=30)
    label = models.CharField(max_length=120)
    status = models.CharField(max_length=10, default='pending')
    decided_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    decided_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['model', 'object_id', 'sequence']


class HSEFile(models.Model):
    model = models.CharField(max_length=20, db_index=True)
    object_id = models.IntegerField(db_index=True)
    file = models.FileField(upload_to='zeo_hse/%Y/%m/')
    name = models.CharField(max_length=255)
    size = models.PositiveIntegerField(default=0)
    note = models.CharField(max_length=255, blank=True)
    uploaded_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    uploaded_at = models.DateTimeField(auto_now_add=True)


class HSEEvent(models.Model):
    """Audit history – every creation, approval, status change and correction (records are not silently edited)."""
    model = models.CharField(max_length=20, db_index=True)
    object_id = models.IntegerField(db_index=True)
    text = models.TextField()
    user = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-at', '-id']



# ================================================================== v1.16.0 – the other blueprint modules
DATE_HELP = ''


class ChangeRequest(models.Model):
    """Module 2 – Management of change."""
    KINDS = (('permanent', 'Permanent'), ('temporary', 'Temporary'), ('emergency', 'Emergency'))
    CATS = (('equipment', 'Equipment'), ('process', 'Process'), ('chemical', 'Chemical / material'), ('layout', 'Layout / facility'),
            ('software', 'Software / control system'), ('personnel', 'Personnel / responsibilities'), ('operating', 'Operating conditions'),
            ('other', 'Other'))
    STATUS = (('draft', 'Draft'), ('submitted', 'Submitted'), ('screening', 'Screening'), ('impact', 'Impact assessment'),
              ('approval', 'Pending approval'), ('approved', 'Approved for implementation'), ('implementing', 'Implementing'),
              ('verification', 'Post-change review'), ('closed', 'Closed'), ('rejected', 'Rejected'), ('cancelled', 'Cancelled'))
    number = models.CharField(max_length=30, unique=True)
    title = models.CharField(max_length=255)
    kind = models.CharField('Type of change', max_length=10, choices=KINDS, default='permanent')
    category = models.CharField(max_length=12, choices=CATS, default='process')
    site = models.ForeignKey(Site, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    description = models.TextField('Proposed change')
    reason = models.TextField(blank=True)
    scope = models.TextField(blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField('Temporary until', null=True, blank=True, help_text='Temporary changes: when it must be removed or extended')
    requester = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    coordinator = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    formal_required = models.BooleanField('Formal change control required', default=True)
    screening_note = models.TextField(blank=True)
    impacts = models.JSONField(default=dict, blank=True, help_text='{safety|environment|health|quality|legal|operational: {level: none|low|medium|high, note}}')
    risk_assessment = models.ForeignKey(RiskAssessment, null=True, blank=True, on_delete=models.SET_NULL, related_name='changes')
    emergency_justification = models.TextField(blank=True)
    prestart_checks = models.JSONField(default=dict, blank=True, help_text='{training, documents, permits, competency, emergency}: true')
    implemented_on = models.DateField(null=True, blank=True)
    implemented_by = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    objective_met = models.BooleanField(null=True, blank=True)
    new_hazards = models.TextField('New hazards found after the change', blank=True)
    review_note = models.TextField(blank=True)
    status = models.CharField(max_length=14, choices=STATUS, default='draft', db_index=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class HealthSurveillance(models.Model):
    """Module 3 – Occupational health (clinical fields restricted)."""
    EXPOSURES = (('noise', 'Noise'), ('dust', 'Dust'), ('chemical', 'Chemicals'), ('biological', 'Biological agents'), ('ergonomic', 'Ergonomic'),
                 ('heat', 'Heat stress'), ('radiation', 'Radiation'), ('vibration', 'Vibration'), ('driving', 'Driving / fitness to drive'),
                 ('food', 'Food handling'), ('other', 'Other'))
    TYPES = (('pre_employment', 'Pre-employment'), ('periodic', 'Periodic'), ('return', 'Return to work'), ('exit', 'Exit'), ('special', 'Special / exposure follow-up'))
    FITNESS = (('pending', 'Pending'), ('fit', 'Fit'), ('fit_restrictions', 'Fit with restrictions'), ('temp_unfit', 'Temporarily unfit'), ('unfit', 'Unfit'))
    number = models.CharField(max_length=30, unique=True)
    employee = models.ForeignKey(EMP, on_delete=models.CASCADE, related_name='hse_health')
    job_role = models.CharField(max_length=150, blank=True)
    exposure = models.CharField('Exposure category', max_length=12, choices=EXPOSURES, default='noise')
    assessment_type = models.CharField(max_length=15, choices=TYPES, default='periodic')
    due_date = models.DateField()
    completed_on = models.DateField(null=True, blank=True)
    provider = models.CharField('Occupational health provider', max_length=150, blank=True)
    fitness = models.CharField('Fitness status', max_length=16, choices=FITNESS, default='pending')
    restrictions = models.TextField('Work restrictions (shared with the manager)', blank=True)
    followup_date = models.DateField(null=True, blank=True)
    interval_months = models.PositiveSmallIntegerField('Repeat every (months)', default=12)
    origin = models.CharField(max_length=40, blank=True, help_text='rtw:<leave id>, exit:<resignation id> … when created from another app')
    clinical_notes = models.TextField('Clinical findings (restricted)', blank=True)
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['due_date']


class Audit(models.Model):
    """Module 4 – Audit management."""
    KINDS = (('internal', 'Internal'), ('external', 'External / certification'), ('supplier', 'Supplier / contractor'), ('site', 'Site'),
             ('compliance', 'Compliance'))
    STATUS = (('planned', 'Planned'), ('in_progress', 'In progress'), ('reported', 'Report for approval'), ('approved', 'Report approved – actions open'),
              ('closed', 'Closed'), ('cancelled', 'Cancelled'))
    number = models.CharField(max_length=30, unique=True)
    title = models.CharField(max_length=255)
    kind = models.CharField(max_length=12, choices=KINDS, default='internal')
    standard = models.CharField('Standard / requirement', max_length=150, blank=True, help_text='ISO 45001, ISO 14001, company procedure …')
    site = models.ForeignKey(Site, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    contractor = models.ForeignKey(Contractor, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text='Supplier / contractor audits')
    template = models.ForeignKey(FormTemplate, on_delete=models.PROTECT, related_name='audits', verbose_name='Checklist')
    lead_auditor = models.ForeignKey(EMP, on_delete=models.PROTECT, related_name='+')
    auditee = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='Auditee / area owner')
    planned_date = models.DateField()
    done_date = models.DateField(null=True, blank=True)
    unplanned = models.BooleanField(default=False)
    answers = models.JSONField(default=dict, blank=True, help_text='{key: {value: compliant|noncompliant|na|observation, note}}')
    score = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    summary = models.TextField('Report summary', blank=True)
    status = models.CharField(max_length=12, choices=STATUS, default='planned', db_index=True)
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['-planned_date']


class AuditFinding(models.Model):
    GRADES = (('critical', 'Critical'), ('major', 'Major'), ('minor', 'Minor'), ('observation', 'Observation'), ('ofi', 'Improvement opportunity'))
    audit = models.ForeignKey(Audit, on_delete=models.CASCADE, related_name='findings')
    number = models.CharField(max_length=40)
    question = models.CharField(max_length=60, blank=True)
    text = models.TextField('Finding')
    grade = models.CharField(max_length=12, choices=GRADES, default='minor')
    evidence = models.TextField(blank=True)
    repeat = models.BooleanField('Repeat finding', default=False)
    action = models.ForeignKey(Action, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['audit', 'id']


class Nonconformity(models.Model):
    """Module 5 – Quality nonconformities."""
    SOURCES = (('inspection', 'Inspection / test'), ('customer', 'Customer complaint'), ('supplier', 'Supplier'), ('process', 'Process'),
               ('audit', 'Audit'), ('internal', 'Internal'))
    SEVERITY = (('minor', 'Minor'), ('major', 'Major'), ('critical', 'Critical'))
    DISPOSITION = (('', '—'), ('use_as_is', 'Use as is (concession)'), ('rework', 'Rework'), ('repair', 'Repair'), ('scrap', 'Scrap'),
                   ('return', 'Return to supplier'), ('regrade', 'Regrade'))
    STATUS = (('open', 'Open'), ('contained', 'Contained'), ('investigation', 'Investigation'), ('actions', 'Actions in progress'),
              ('verification', 'Verification'), ('closed', 'Closed'))
    number = models.CharField(max_length=30, unique=True)
    title = models.CharField(max_length=255)
    source = models.CharField(max_length=12, choices=SOURCES, default='inspection')
    product_process = models.CharField('Product / process', max_length=200)
    project = models.CharField(max_length=150, blank=True)
    supplier = models.ForeignKey(Contractor, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    site = models.ForeignKey(Site, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    defect_type = models.CharField(max_length=120, blank=True)
    specification = models.TextField('Specification / acceptance criteria', blank=True)
    actual_result = models.TextField(blank=True)
    severity = models.CharField(max_length=10, choices=SEVERITY, default='minor')
    qty_affected = models.DecimalField('Quantity affected', max_digits=12, decimal_places=2, default=0)
    traceability = models.CharField('Batch / lot / serial', max_length=200, blank=True)
    containment = models.TextField('Containment / quarantine', blank=True)
    disposition = models.CharField(max_length=10, choices=DISPOSITION, blank=True)
    disposition_approved_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    root_cause = models.TextField(blank=True)
    reinspection_result = models.TextField(blank=True)
    owner = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    status = models.CharField(max_length=13, choices=STATUS, default='open', db_index=True)
    reported_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']


class KPI(models.Model):
    """Module 7 – Business performance: objectives and KPIs."""
    FORMULAS = (('manual', 'Entered manually'), ('trir', 'TRIR'), ('ltifr', 'LTIFR'), ('near_misses', 'Near misses reported'),
                ('audit_completion', 'Audit completion %'), ('action_closure', 'Action closure on time %'),
                ('inspection_compliance', 'Inspections completed'), ('training_compliance', 'Training compliance %'),
                ('permit_compliance', 'Permits closed without violation %'), ('env_exceedances', 'Environmental exceedances'),
                ('quality_ncs', 'Quality nonconformities'), ('observations', 'BBS observations'), ('waste_hazardous', 'Hazardous waste (kg)'))
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=150)
    objective = models.CharField('Objective', max_length=255, blank=True)
    formula = models.CharField(max_length=24, choices=FORMULAS, default='manual')
    unit = models.CharField(max_length=20, blank=True)
    target = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    higher_is_better = models.BooleanField(default=False)
    site = models.ForeignKey(Site, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    owner = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    year = models.PositiveSmallIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    perf_kpi_id = models.IntegerField(null=True, blank=True, help_text='Same KPI in the Performance KPI library')

    class Meta:
        ordering = ['code']


class KPIValue(models.Model):
    kpi = models.ForeignKey(KPI, on_delete=models.CASCADE, related_name='values')
    period = models.DateField(help_text='First day of the month')
    value = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    target = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    source = models.CharField(max_length=10, default='auto')
    deviation = models.BooleanField(default=False)
    validated_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = (('kpi', 'period'),)
        ordering = ['kpi', '-period']


class ManagementReview(models.Model):
    STATUS = (('draft', 'Draft'), ('closed', 'Closed'))
    number = models.CharField(max_length=30, unique=True)
    date = models.DateField()
    period_from = models.DateField()
    period_to = models.DateField()
    attendees = models.TextField(blank=True)
    inputs = models.JSONField(default=dict, blank=True, help_text='KPI snapshot taken when the review is prepared')
    decisions = models.TextField(blank=True)
    status = models.CharField(max_length=6, choices=STATUS, default='draft')
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['-date']


class ErgoAssessment(models.Model):
    """Module 9 – Ergonomics."""
    METHODS = (('reba', 'REBA'), ('rula', 'RULA'), ('niosh', 'NIOSH lifting equation'), ('mac', 'MAC tool'), ('checklist', 'Company checklist'),
               ('other', 'Other'))
    LEVELS = (('low', 'Low'), ('medium', 'Medium'), ('high', 'High'), ('very_high', 'Very high'))
    STATUS = (('open', 'Assessed'), ('actions', 'Controls in progress'), ('reassess', 'Reassess'), ('closed', 'Closed'))
    number = models.CharField(max_length=30, unique=True)
    workstation = models.CharField('Workstation / job', max_length=200)
    task = models.CharField(max_length=200)
    department = models.CharField(max_length=120, blank=True)
    site = models.ForeignKey(Site, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    workers = models.ManyToManyField(EMP, blank=True, related_name='+', verbose_name='Affected workers')
    manual_handling = models.BooleanField(default=False)
    repetitive = models.BooleanField('Repetitive motion', default=False)
    awkward_posture = models.BooleanField(default=False)
    force = models.BooleanField('Force exertion', default=False)
    duration_hours = models.DecimalField('Hours per shift', max_digits=4, decimal_places=1, default=0)
    frequency = models.CharField(max_length=80, blank=True, help_text='Lifts per hour, cycles per minute …')
    observations = models.TextField(blank=True)
    method = models.CharField(max_length=10, choices=METHODS, default='reba')
    score = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    risk_level = models.CharField(max_length=10, choices=LEVELS, default='medium')
    controls = models.TextField('Recommended controls', blank=True)
    assessor = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    assessed_on = models.DateField()
    reassess_on = models.DateField(null=True, blank=True)
    reassessed_score = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    reassessed_level = models.CharField(max_length=10, choices=LEVELS, blank=True)
    effectiveness = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default='open')

    class Meta:
        ordering = ['-assessed_on']


class EnvAspect(models.Model):
    """Module 10 – Environmental aspects and impacts register."""
    CONDITIONS = (('normal', 'Normal'), ('abnormal', 'Abnormal'), ('emergency', 'Emergency'))
    site = models.ForeignKey(Site, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    activity = models.CharField(max_length=200)
    aspect = models.CharField(max_length=200)
    impact = models.CharField('Potential impact', max_length=255)
    condition = models.CharField(max_length=10, choices=CONDITIONS, default='normal')
    severity = models.PositiveSmallIntegerField(default=3)
    probability = models.PositiveSmallIntegerField(default=3)
    significant = models.BooleanField(default=False)
    controls = models.TextField('Operational controls', blank=True)
    legal_ref = models.CharField('Legal / other requirement', max_length=200, blank=True)
    owner = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['-significant', 'activity']


class EnvParameter(models.Model):
    CATS = (('air', 'Air emissions'), ('water', 'Water consumption'), ('wastewater', 'Wastewater / discharge'), ('energy', 'Energy'),
            ('carbon', 'Carbon (CO₂e)'), ('noise', 'Environmental noise'), ('soil', 'Soil / ground'), ('other', 'Other'))
    site = models.ForeignKey(Site, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    name = models.CharField('Monitoring parameter', max_length=150)
    category = models.CharField(max_length=10, choices=CATS, default='air')
    point = models.CharField('Monitoring point', max_length=150, blank=True)
    unit = models.CharField(max_length=20)
    limit_min = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    limit_max = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    limit_source = models.CharField('Limit source / jurisdiction', max_length=200, blank=True)
    frequency_days = models.PositiveSmallIntegerField(default=30)
    next_due = models.DateField(null=True, blank=True)
    owner = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['site__name', 'name']


class EnvReading(models.Model):
    parameter = models.ForeignKey(EnvParameter, on_delete=models.CASCADE, related_name='readings')
    sampled_on = models.DateField()
    value = models.DecimalField(max_digits=14, decimal_places=4)
    evidence = models.CharField(max_length=255, blank=True, help_text='Lab report no., meter reading …')
    exceeded = models.BooleanField(default=False)
    action = models.ForeignKey(Action, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    recorded_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['-sampled_on', '-id']


class WasteRecord(models.Model):
    STATUS = (('stored', 'Stored on site'), ('transferred', 'Transferred'), ('disposed', 'Disposal confirmed'))
    number = models.CharField(max_length=30, unique=True)
    site = models.ForeignKey(Site, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    waste_type = models.CharField(max_length=150)
    hazardous = models.BooleanField(default=False)
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    unit = models.CharField(max_length=10, default='kg')
    generated_on = models.DateField()
    storage = models.CharField('Storage / segregation', max_length=150, blank=True)
    carrier = models.ForeignKey(Contractor, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='Waste carrier')
    destination = models.CharField('Approved disposal destination', max_length=200, blank=True)
    manifest_no = models.CharField('Manifest / consignment no.', max_length=60, blank=True)
    disposal_permit = models.CharField('Carrier / facility permit no.', max_length=60, blank=True)
    transferred_on = models.DateField(null=True, blank=True)
    disposed_on = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=STATUS, default='stored')

    class Meta:
        ordering = ['-generated_on']


class Chemical(models.Model):
    """Module 11 – Chemicals and SDS."""
    GROUPS = (('general', 'General'), ('flammable', 'Flammable'), ('oxidizer', 'Oxidiser'), ('acid', 'Corrosive – acid'), ('base', 'Corrosive – base'),
              ('toxic', 'Toxic'), ('gas', 'Compressed gas'), ('water_reactive', 'Water reactive'), ('peroxide', 'Organic peroxide'))
    STATUS = (('requested', 'Requested'), ('review', 'HSE review'), ('approved', 'Approved for use'), ('rejected', 'Rejected'), ('archived', 'Archived'))
    number = models.CharField(max_length=30, unique=True)
    name = models.CharField('Chemical name', max_length=200)
    trade_name = models.CharField(max_length=200, blank=True)
    supplier = models.CharField(max_length=200, blank=True)
    product_code = models.CharField(max_length=60, blank=True)
    cas_no = models.CharField('CAS number', max_length=40, blank=True)
    hazard_classes = models.CharField('Hazard classification (GHS)', max_length=255, blank=True)
    signal_word = models.CharField(max_length=10, blank=True, help_text='Danger / Warning')
    intended_use = models.CharField(max_length=255, blank=True)
    site = models.ForeignKey(Site, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    storage_location = models.CharField(max_length=150, blank=True)
    max_qty = models.DecimalField('Maximum quantity', max_digits=12, decimal_places=2, null=True, blank=True)
    unit = models.CharField(max_length=10, default='L')
    current_qty = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    compat_group = models.CharField('Compatibility group', max_length=15, choices=GROUPS, default='general')
    ppe = models.CharField('Required PPE', max_length=255, blank=True)
    handling = models.TextField('Handling and storage', blank=True)
    emergency_measures = models.TextField(blank=True)
    disposal = models.CharField(max_length=255, blank=True)
    sds_revision = models.CharField('SDS revision', max_length=30, blank=True)
    sds_date = models.DateField('SDS date', null=True, blank=True)
    sds_file = models.ForeignKey('HSEFile', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    review_due = models.DateField(null=True, blank=True)
    risk_assessment = models.ForeignKey(RiskAssessment, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    owner = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    status = models.CharField(max_length=10, choices=STATUS, default='requested', db_index=True)
    approved_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    requested_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['name']


class Claim(models.Model):
    """Module 13 – Claims (restricted)."""
    TYPES = (('injury', 'Workplace injury'), ('property', 'Property damage'), ('third_party', 'Third-party liability'), ('vehicle', 'Vehicle'),
             ('other', 'Other'))
    STATUS = (('open', 'Registered'), ('submitted', 'Submitted to insurer'), ('assessment', 'Under assessment'), ('approved', 'Approved'),
              ('disputed', 'Disputed'), ('rejected', 'Rejected'), ('settled', 'Settled'), ('closed', 'Closed'))
    number = models.CharField(max_length=30, unique=True)
    incident = models.ForeignKey(Incident, null=True, blank=True, on_delete=models.SET_NULL, related_name='claims')
    claim_type = models.CharField(max_length=12, choices=TYPES, default='injury')
    claimant_employee = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    claimant_name = models.CharField('Claimant (third party / contractor)', max_length=200, blank=True)
    contractor = models.ForeignKey(Contractor, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    insurer = models.CharField(max_length=150, blank=True)
    policy_ref = models.CharField('Policy reference', max_length=80, blank=True)
    notified_on = models.DateField('Date notified', null=True, blank=True)
    submitted_on = models.DateField(null=True, blank=True)
    amount_claimed = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    estimated_liability = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    approved_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    paid_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    deductible = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    recovered_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    owner = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='Claims owner')
    next_action_on = models.DateField('Next action date', null=True, blank=True)
    notes = models.TextField('Correspondence / notes', blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default='open', db_index=True)
    settlement_approved_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    project_expense_id = models.IntegerField(null=True, blank=True)
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class ExposureLimit(models.Model):
    """Module 14 – occupational exposure criteria (version-controlled, by jurisdiction)."""
    CATS = (('noise', 'Noise'), ('dust', 'Dust / particulates'), ('chemical', 'Chemical vapours and gases'), ('heat', 'Heat stress'),
            ('lighting', 'Lighting'), ('vibration', 'Vibration'), ('biological', 'Biological agents'), ('radiation', 'Radiation'), ('other', 'Other'))
    AVG = (('twa8', '8-hour TWA'), ('stel', '15-min STEL'), ('ceiling', 'Ceiling'), ('instant', 'Instant reading'), ('dose', 'Daily dose'),
           ('minimum', 'Minimum (e.g. lux)'))
    agent = models.CharField(max_length=150)
    category = models.CharField(max_length=10, choices=CATS, default='chemical')
    unit = models.CharField(max_length=20)
    limit_value = models.DecimalField(max_digits=12, decimal_places=4)
    action_level = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    averaging = models.CharField(max_length=8, choices=AVG, default='twa8')
    jurisdiction = models.CharField('Jurisdiction / source', max_length=150, blank=True)
    version = models.CharField(max_length=20, default='1')
    effective_from = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['category', 'agent']


class HygieneSample(models.Model):
    TYPES = (('personal', 'Personal'), ('area', 'Area'))
    STATUS = (('planned', 'Planned'), ('done', 'Result recorded'), ('action', 'Exceedance – controls'), ('closed', 'Closed'))
    number = models.CharField(max_length=30, unique=True)
    limit = models.ForeignKey(ExposureLimit, on_delete=models.PROTECT, related_name='samples', verbose_name='Agent and limit')
    site = models.ForeignKey(Site, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    location = models.CharField('Sampling location', max_length=150)
    job_group = models.CharField('Job / similar exposure group', max_length=150, blank=True)
    employee = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    sample_type = models.CharField(max_length=8, choices=TYPES, default='area')
    method = models.CharField('Sampling method', max_length=150, blank=True)
    instrument = models.CharField(max_length=120, blank=True)
    calibration_date = models.DateField(null=True, blank=True)
    planned_on = models.DateField()
    sampled_on = models.DateField(null=True, blank=True)
    duration_min = models.PositiveIntegerField('Duration (min)', null=True, blank=True)
    conditions = models.CharField('Environmental conditions', max_length=200, blank=True)
    result = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    laboratory = models.CharField(max_length=150, blank=True)
    interpretation = models.TextField(blank=True)
    exceeded = models.BooleanField(default=False)
    above_action_level = models.BooleanField(default=False)
    control_action = models.TextField(blank=True)
    retest_on = models.DateField(null=True, blank=True)
    action = models.ForeignKey(Action, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    status = models.CharField(max_length=8, choices=STATUS, default='planned')

    class Meta:
        ordering = ['-planned_on']


class Obligation(models.Model):
    """Module 15 – Compliance register."""
    SOURCES = (('law', 'Law / regulation'), ('permit', 'Permit / licence'), ('contract', 'Contract'), ('certification', 'Certification / standard'),
               ('internal', 'Internal requirement'))
    STATUS = (('not_assessed', 'Not yet assessed'), ('compliant', 'Applicable – compliant'), ('partial', 'Applicable – partially compliant'),
              ('noncompliant', 'Noncompliant'), ('pending', 'Pending evidence'), ('na', 'Not applicable'))
    ref = models.CharField('Requirement reference', max_length=60, unique=True)
    title = models.CharField(max_length=255)
    source = models.CharField(max_length=14, choices=SOURCES, default='law')
    jurisdiction = models.CharField(max_length=120, blank=True)
    authority = models.CharField('Issuing authority', max_length=150, blank=True)
    summary = models.TextField('Requirement summary', blank=True)
    applicability = models.TextField('Applicability rationale', blank=True)
    related_document = models.CharField('Related permit / licence / certificate', max_length=150, blank=True)
    site = models.ForeignKey(Site, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    owner = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    reviewer = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    due_date = models.DateField(null=True, blank=True)
    recurrence_months = models.PositiveSmallIntegerField('Repeat every (months)', default=12)
    evidence_required = models.TextField(blank=True)
    status = models.CharField(max_length=14, choices=STATUS, default='not_assessed', db_index=True)
    last_assessed = models.DateField(null=True, blank=True)
    escalated_on = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['due_date', 'ref']


class ComplianceAssessment(models.Model):
    obligation = models.ForeignKey(Obligation, on_delete=models.CASCADE, related_name='assessments')
    assessed_on = models.DateField()
    result = models.CharField(max_length=14, choices=Obligation.STATUS)
    evidence = models.TextField('Evidence submitted', blank=True)
    finding = models.TextField(blank=True)
    action = models.ForeignKey(Action, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    assessor = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    approved_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    approved_at = models.DateTimeField(null=True, blank=True)
    state = models.CharField(max_length=10, default='pending', help_text='pending / approved / rejected')

    class Meta:
        ordering = ['-assessed_on', '-id']


class HSEDocument(models.Model):
    """Shared service – document control."""
    TYPES = (('policy', 'Policy'), ('procedure', 'Procedure'), ('risk_assessment', 'Risk assessment'), ('method_statement', 'Method statement'),
             ('work_instruction', 'Work instruction'), ('emergency_plan', 'Emergency plan'), ('sds', 'Safety data sheet'), ('audit_report', 'Audit report'),
             ('form', 'Form'), ('other', 'Other'))
    STATUS = (('draft', 'Draft'), ('review', 'In review'), ('approved', 'Approved'), ('published', 'Published'), ('superseded', 'Superseded'),
              ('archived', 'Archived'))
    number = models.CharField(max_length=40)
    revision = models.PositiveSmallIntegerField(default=0)
    title = models.CharField(max_length=255)
    doc_type = models.CharField('Type', max_length=18, choices=TYPES, default='procedure')
    site = models.ForeignKey(Site, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    summary = models.TextField('Summary / change note', blank=True)
    owner = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    approver = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    effective_date = models.DateField(null=True, blank=True)
    review_date = models.DateField(null=True, blank=True)
    restricted = models.BooleanField('Restricted (HSE only)', default=False)
    requires_ack = models.BooleanField('Staff must acknowledge', default=False)
    previous = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    status = models.CharField(max_length=10, choices=STATUS, default='draft', db_index=True)
    approved_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    approved_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['number', '-revision']
        unique_together = (('number', 'revision'),)


class DocumentAck(models.Model):
    document = models.ForeignKey(HSEDocument, on_delete=models.CASCADE, related_name='acks')
    employee = models.ForeignKey(EMP, on_delete=models.CASCADE, related_name='+')
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = (('document', 'employee'),)


class CompetencyType(models.Model):
    """Shared service – training and competency."""
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=150)
    validity_months = models.PositiveSmallIntegerField(default=24, help_text='0 = does not expire')
    course_id = models.IntegerField('Linked HR course', null=True, blank=True, help_text='HR Learning course whose certificate gives this competency')
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']


class Competency(models.Model):
    employee = models.ForeignKey(EMP, on_delete=models.CASCADE, related_name='hse_competencies')
    ctype = models.ForeignKey(CompetencyType, on_delete=models.PROTECT, related_name='holders', verbose_name='Competency / course')
    issued_on = models.DateField('Completed on')
    expires_on = models.DateField(null=True, blank=True)
    provider = models.CharField(max_length=150, blank=True)
    certificate_no = models.CharField(max_length=60, blank=True)
    assessed_competent = models.BooleanField(default=True)
    approved_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    source = models.CharField(max_length=10, default='hse', choices=(('hse', 'Entered in HSE'), ('learning', 'HR Learning certificate')))
    source_id = models.IntegerField(null=True, blank=True, help_text='HR certificate id')

    class Meta:
        ordering = ['employee', 'ctype', '-issued_on']
        verbose_name_plural = 'competencies'


class Handover(models.Model):
    """v1.17.0 – an employee who moved site or is leaving still owns open HSE work: list it for reassignment."""
    REASONS = (('transfer', 'Transfer'), ('leaving', 'Leaving the company'))
    STATUS = (('open', 'To reassign'), ('done', 'Reassigned'), ('nothing', 'Nothing open'))
    employee = models.ForeignKey(EMP, on_delete=models.CASCADE, related_name='hse_handovers')
    reason = models.CharField(max_length=10, choices=REASONS)
    ref = models.CharField(max_length=40, unique=True, help_text='transfer:<id> / resignation:<id>')
    effective_date = models.DateField(null=True, blank=True)
    detail = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default='open', db_index=True)
    done_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    done_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['status', '-created_at']
