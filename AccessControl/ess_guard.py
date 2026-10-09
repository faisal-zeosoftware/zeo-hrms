"""
v1.13.0 – employee self-service guard (installed next to ZeoAccess by AccessControl.access.install).

ZeoAccess lets an employee write any record that belongs to them (self-service requests).
That also let an employee:
  * PATCH HR master data on their own employee (emp_code, branch, department, manager, is_ess …);
  * approve their own leave / general / document … request by sending `status`;
  * add / change / delete their bank account, family, qualifications, job history and documents
    without anybody checking it;
  * change their own payslip, salary structure, leave balance …;
  * upload a payslip PDF and trigger payslip e-mails.

Rules for users who are not company admins and do not hold the model's add/change/delete right:
  * employee master: only fields whose ESS policy is `self` may change (policy from
    SelfService "Self-service settings" when installed, else the defaults below);
  * profile records (family, bank, qualification, job history, documents, skills,
    EmployeeProfile extras): only when the group's policy is `self`; otherwise the
    employee uses "Request a change" in My profile (SelfService change requests);
  * request records: status / approval / processing fields cannot be set or changed;
    a request can only be edited or deleted while it is pending (a pending request can be
    withdrawn); an approve action on the request itself needs the pending step's approver;
  * payroll results, salary, balances: read only.
"""
import logging

from rest_framework import permissions

logger = logging.getLogger(__name__)

SELF, REQUEST, HR, HIDDEN = 'self', 'request', 'hr', 'hidden'
POLICIES = (SELF, REQUEST, HR, HIDDEN)

# --------------------------------------------------------------------------------------------- policy defaults
# (group, field) -> policy;  (group, '') is the policy of adding / removing records of the group
DEFAULT_POLICY = {
    # personal (employee master)
    ('personal', ''): HR,
    ('personal', 'emp_code'): HR, ('personal', 'emp_first_name'): HR, ('personal', 'emp_middle_name'): HR,
    ('personal', 'emp_last_name'): HR, ('personal', 'emp_gender'): HR, ('personal', 'emp_date_of_birth'): HR,
    ('personal', 'emp_nationality'): HR, ('personal', 'person_id'): HR,
    ('personal', 'emp_marital_status'): REQUEST, ('personal', 'emp_relegion'): REQUEST,
    ('personal', 'emp_father_name'): REQUEST, ('personal', 'emp_mother_name'): REQUEST,
    ('personal', 'emp_blood_group'): SELF, ('personal', 'emp_profile_pic'): SELF,
    # contact / address
    ('contact', ''): SELF,
    ('contact', 'emp_personal_email'): SELF, ('contact', 'emp_mobile_number_1'): SELF,
    ('contact', 'emp_mobile_number_2'): SELF, ('contact', 'emp_company_email'): HR,
    ('address', ''): SELF,
    ('address', 'emp_present_address'): SELF, ('address', 'emp_permenent_address'): SELF,
    ('address', 'emp_city'): SELF, ('address', 'emp_state_id'): SELF, ('address', 'emp_country_id'): SELF,
    # record groups
    ('identity', ''): HR, ('identity', 'title'): REQUEST, ('identity', 'preferred_name'): SELF,
    ('identity', 'place_of_birth'): HR,   # v1.13.0: driving licence – employee proposes, HR approves
    ('identity', 'driving_licence_no'): REQUEST, ('identity', 'driving_licence_issue_date'): REQUEST,
    ('identity', 'driving_licence_expiry_date'): REQUEST, ('identity', 'driving_licence_emirate'): REQUEST,
    ('identity', 'driving_licence_categories'): REQUEST,
    ('emergency', ''): SELF,
    ('family', ''): REQUEST,
    ('bank', ''): REQUEST,
    ('qualifications', ''): REQUEST,
    ('experience', ''): REQUEST,
    ('skills', ''): SELF,
    ('documents', ''): REQUEST,
    # job / organisation, employment status, salary: HR only
    ('org', ''): HR, ('employment', ''): HR, ('salary', ''): HR,
    ('org', 'emp_branch_id'): HR, ('org', 'emp_dept_id'): HR, ('org', 'emp_desgntn_id'): HR, ('org', 'emp_ctgry_id'): HR,
    ('org', 'emp_reporting_manager'): HR, ('org', 'work_location'): HR, ('org', 'visa_location'): HR,
    ('org', 'emp_joined_date'): HR, ('org', 'emp_date_of_confirmation'): HR, ('org', 'emp_status'): HR,
    ('org', 'is_active'): HR, ('org', 'emp_ot_applicable'): HR, ('org', 'emp_weekend_calendar'): HR,
    ('org', 'holiday_calendar'): HR, ('org', 'attendance_source'): HR, ('org', 'barcode_number'): HR,
    # never shown in ESS
    ('system', ''): HIDDEN,
    ('system', 'users'): HIDDEN, ('system', 'is_ess'): HIDDEN, ('system', 'face_encoding'): HIDDEN,
    ('system', 'created_at'): HIDDEN, ('system', 'created_by'): HIDDEN, ('system', 'updated_at'): HIDDEN,
    ('system', 'updated_by'): HIDDEN,
}
# fields HR may only set to hr or hidden (never self / request): identity of the record, login, org, pay
LOCKED = {('personal', 'emp_code'), ('personal', 'person_id')} | {k for k in DEFAULT_POLICY if k[0] in ('org', 'employment', 'salary', 'system')}

# emp_master field -> group
EMP_FIELD_GROUP = {f: g for (g, f) in DEFAULT_POLICY if f and g in ('personal', 'contact', 'address', 'org', 'system')}

# model label (lower) -> group for the profile record tables
MODEL_GROUP = {
    'empmanagement.emp_family': 'family', 'empmanagement.fam_customfieldvalue': 'family',
    'empmanagement.employeebankdetail': 'bank',
    'empmanagement.empqualification': 'qualifications', 'empmanagement.qualification_customfieldvalue': 'qualifications',
    'empmanagement.empjobhistory': 'experience', 'empmanagement.jobhistory_customfieldvalue': 'experience',
    'empmanagement.emp_documents': 'documents', 'empmanagement.doc_customfieldvalue': 'documents',
    'empmanagement.employeemarketingskill': 'skills', 'empmanagement.employeeprogramskill': 'skills',
    'empmanagement.employeelangskill': 'skills',
    'empmanagement.emp_customfieldvalue': 'personal',
    # EmployeeProfile (v1.13.0, built by "empmaster")
    'employeeprofile.employeeidentity': 'identity', 'employeeprofile.emergencycontact': 'emergency',
    'employeeprofile.dependentextra': 'family', 'employeeprofile.bankextra': 'bank',
    'employeeprofile.qualificationextra': 'qualifications', 'employeeprofile.employmentinfo': 'employment',
    'employeeprofile.probationextension': 'employment', 'employeeprofile.employeecodesetting': 'org',
}
# EmpViewSet nested actions -> (group, model name for the right)
NESTED = {
    'emp_family': ('family', 'emp_family'), 'emp_qalification': ('qualifications', 'empqualification'),
    'emp_qualification': ('qualifications', 'empqualification'), 'emp_job_history': ('experience', 'empjobhistory'),
    'emp_documents': ('documents', 'emp_documents'), 'emp_bank_details': ('bank', 'employeebankdetail'),
    'emp_market_skills': ('skills', 'employeemarketingskill'), 'emp_programlangskill': ('skills', 'employeeprogramskill'),
    'emp_languageskill': ('skills', 'employeelangskill'),
}

# request models: fields only an approver / HR may set
REQUEST_MODELS = {
    'calendars.employee_leave_request': ('status', 'approved_days'),
    'calendars.compensatoryleaverequest': ('status',),
    'calendars.lateinearlyoutrequest': ('status',),
    'empmanagement.generalrequest': ('status', 'is_processed'),
    'empmanagement.documentrequest': ('status',),
    'empmanagement.employeeresignation': ('status',),
    'empmanagement.empleaverequest': ('status',),
    'payrollmanagement.advancesalaryrequest': ('status',),
    'payrollmanagement.loanapplication': ('status', 'disbursement_date', 'approved_on', 'rejection_reason'),
    'payrollmanagement.airticketrequest': ('status', 'approved_by', 'approved_date'),
    'payrollmanagement.leaveencashment': ('status', 'approved_by', 'approved_at', 'processed_at'),
    'organisationmanager.assetrequest': ('status',),
}
APPROVAL_PREFIXES = ('approved', 'approver', 'is_approved', 'processed', 'is_processed', 'rejected', 'rejection')
PENDING = ('pending', 'pend', 'draft', 'submitted', 'new', 'requested', 'open', 'escalat')
WITHDRAWN = ('cancelled', 'canceled', 'withdrawn', 'withdraw', 'cancel')
APPROVE_ACTIONS = {'approve', 'reject', 'approve_request', 'reject_request', 'bulk_approve', 'process', 'mark_processed'}

# results / balances an employee may read but never write
READ_ONLY_MODELS = {
    'payrollmanagement.payslip', 'payrollmanagement.payslipcomponent', 'payrollmanagement.payslipleave',
    'payrollmanagement.employeesalarystructure', 'payrollmanagement.salaryrevisionhistory',
    'payrollmanagement.loanrepayment', 'payrollmanagement.airticketallocation',
    'calendars.emp_leave_balance', 'calendars.leave_accrual_transaction', 'calendars.leave_reset_transaction',
    'calendars.leaveencashmenttransaction', 'calendars.leavecarryforwardtransaction',
    'empmanagement.endofservice',
}
VERBS = {'create': 'add', 'update': 'change', 'partial_update': 'change', 'destroy': 'delete'}
REQUEST_HINT = 'Use "Request a change" in My profile – HR will review it.'


# --------------------------------------------------------------------------------------------- policy lookup
def policy_map():
    """Effective policy table: defaults overridden by SelfService settings when that app is installed."""
    out = dict(DEFAULT_POLICY)
    try:
        from django.apps import apps
        if apps.is_installed('SelfService'):
            M = apps.get_model('SelfService', 'EssFieldPolicy')
            for g, f, p in M.objects.values_list('group', 'field', 'policy'):
                if (g, f) in LOCKED and p in (SELF, REQUEST):
                    continue
                out[(g, f or '')] = p
    except Exception:  # table missing during migrate etc.
        logger.debug('ess policy table not readable', exc_info=True)
    return out


def field_policy(group, field, pm=None):
    pm = pm if pm is not None else policy_map()
    if (group, field) in pm:
        return pm[(group, field)]
    return pm.get((group, ''), HR)


def emp_field_policy(field, pm=None):
    group = EMP_FIELD_GROUP.get(field)
    if group is None:
        return HR
    return field_policy(group, field, pm)


# --------------------------------------------------------------------------------------------- helpers
def _ctx(request):
    from .access import ctx
    return ctx(request)


def _label(model):
    return f'{model._meta.app_label}.{model._meta.model_name}'.lower()


def _data(request):
    d = getattr(request, 'data', None)
    if d is None:
        return {}
    try:
        if hasattr(d, 'lists'):
            return {k: (v[0] if len(v) == 1 else v) for k, v in d.lists()}
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _norm(v):
    if v is None:
        return ''
    if isinstance(v, bool):
        return 'true' if v else 'false'
    s = str(v).strip()
    if s.lower() in ('true', 'false'):
        return s.lower()
    if s.lower() in ('none', 'null'):
        return ''
    return s


def same_value(obj, fname, value):
    """True when the submitted value is the record's current value (forms post unchanged fields back)."""
    from django.db import models as dj
    try:
        f = obj._meta.get_field(fname)
    except Exception:
        return True  # not a model field: ignored by the serializer
    if hasattr(value, 'read') and hasattr(value, 'name'):
        return False  # a new file
    if isinstance(f, dj.ManyToManyField):
        cur = {str(x) for x in getattr(obj, fname).values_list('pk', flat=True)}
        vals = value if isinstance(value, (list, tuple)) else ([] if value in (None, '') else str(value).split(','))
        return cur == {str(v).strip() for v in vals if str(v).strip()}
    if f.is_relation:
        cur_id = getattr(obj, f.attname, None)
        if _norm(value) == _norm(cur_id):
            return True
        rel = getattr(obj, fname, None)
        if rel is None:
            return _norm(value) == ''
        labels = {_norm(str(rel))}
        for attr in ('username', 'branch_name', 'dept_name', 'desgntn_job_title', 'ctgry_title', 'N_name', 'religion',
                     'country_name', 'state_name', 'name', 'calendar_name', 'title'):
            if hasattr(rel, attr):
                labels.add(_norm(getattr(rel, attr)))
        return _norm(value) in labels
    cur = getattr(obj, fname, None)
    if isinstance(f, (dj.FileField,)):
        return _norm(value) in ('', _norm(cur.url if cur else '')) or (cur and str(value).endswith(cur.name or '\0'))
    if isinstance(f, dj.BooleanField):
        return _norm(value) in (('true', '1') if cur else ('false', '0', ''))
    if isinstance(f, dj.DateField):
        return _norm(value)[:10] == (cur.isoformat()[:10] if cur else '')
    if isinstance(f, (dj.DecimalField, dj.FloatField, dj.IntegerField)):
        try:
            return (cur is None and _norm(value) == '') or (cur is not None and value not in (None, '') and float(value) == float(cur))
        except (TypeError, ValueError):
            return False
    if isinstance(f, dj.JSONField):
        return value == cur
    return _norm(value) == _norm(cur)


def is_pending(status):
    s = str(status or '').strip().lower()
    return s == '' or s.startswith(PENDING)


def is_withdraw(status):
    return str(status or '').strip().lower() in WITHDRAWN


def guarded_fields(model):
    base = REQUEST_MODELS.get(_label(model), ())
    names = [f.name for f in model._meta.concrete_fields]
    extra = [n for n in names if n.startswith(APPROVAL_PREFIXES)]
    return tuple(dict.fromkeys([n for n in base if n in names] + extra))


def view_action(view):
    return (getattr(view, 'action', '') or '').lower()


# --------------------------------------------------------------------------------------------- the guard
class EssGuard(permissions.BasePermission):
    message = 'You do not have permission to do this.'

    def _skip(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        from .access import request_schema, view_model
        if not getattr(request.user, 'is_authenticated', False) or not request_schema(request):
            return True
        if getattr(view, 'ess_guard', True) is False:
            return True
        if view_model(view) is None:
            return True
        return False

    def has_permission(self, request, view):
        try:
            if self._skip(request, view):
                return True
            from .access import view_model
            c = _ctx(request)
            if c.admin:
                return True
            model = view_model(view)
            label, name = _label(model), model._meta.model_name
            action = view_action(view)
            verb = VERBS.get(action) or {'POST': 'add', 'DELETE': 'delete'}.get(request.method, 'change')
            # payslip PDF upload / e-mail
            if label == 'payrollmanagement.payslip' and action == 'upload_pdf':
                if 'change_payslip' not in c.codes:
                    self.message = 'Only payroll users can upload payslip PDFs or send payslip e-mails.'
                    return False
                return True
            # nested profile records on the employee screen
            if name == 'emp_master' and action in NESTED:
                group, sub = NESTED[action]
                if 'change_emp_master' in c.codes or f'{verb}_{sub}' in c.codes:
                    return True
                return self._group_ok(group)
            if label in READ_ONLY_MODELS and action in VERBS:
                if f'{verb}_{name}' in c.codes:
                    return True
                self.message = f'You cannot change {model._meta.verbose_name} records – HR or payroll keeps them.'
                return False
            if label in MODEL_GROUP and action in VERBS:
                if f'{verb}_{name}' in c.codes:
                    return True
                return self._group_ok(MODEL_GROUP[label])
            if name == 'emp_master' and action in ('update', 'partial_update'):
                return True  # field check on the object below
            if label in REQUEST_MODELS and action == 'create' and f'change_{name}' not in c.codes:
                data = _data(request)
                for f in guarded_fields(model):
                    if f not in data:
                        continue
                    v = data.get(f)
                    if f == 'status':
                        if not is_pending(v):
                            self.message = 'A new request is always pending: its status is set by the approvers.'
                            return False
                    elif _norm(v) not in ('', 'false', '0'):
                        self.message = f'"{f}" is filled in by the approver, not by the requester.'
                        return False
            return True
        except Exception:
            logger.exception('ess guard failed')
            return True

    def _group_ok(self, group):
        pol = field_policy(group, '')
        if pol == SELF:
            return True
        if pol == REQUEST:
            self.message = f'Changes to your {group} details need HR approval. {REQUEST_HINT}'
        else:
            self.message = f'Your {group} details are kept by HR. Ask HR if something is wrong.'
        return False

    def has_object_permission(self, request, view, obj):
        try:
            if self._skip(request, view):
                return True
            c = _ctx(request)
            if c.admin:
                return True
            model = type(obj)
            label, name = _label(model), model._meta.model_name
            action = view_action(view)
            if name == 'emp_master' and action in ('update', 'partial_update'):
                if 'change_emp_master' in c.codes:
                    return True
                return self._emp_fields_ok(request, obj)
            if label in REQUEST_MODELS:
                return self._request_ok(request, view, obj, c, action, name)
            return True
        except Exception:
            logger.exception('ess guard (object) failed')
            return True

    def _emp_fields_ok(self, request, obj):
        pm = policy_map()
        data = _data(request)
        blocked, needs_request = [], []
        for k, v in data.items():
            if k in ('schema', 'id', 'pk'):
                continue
            try:
                obj._meta.get_field(k)
            except Exception:
                if k == 'is_active' and not same_value(obj, 'is_active', v):
                    blocked.append(k)
                continue
            if same_value(obj, k, v):
                continue
            pol = emp_field_policy(k, pm)
            if pol == SELF:
                continue
            (needs_request if pol == REQUEST else blocked).append(k)
        if blocked or needs_request:
            parts = []
            if needs_request:
                parts.append(f"{', '.join(needs_request)} can only be changed with HR approval. {REQUEST_HINT}")
            if blocked:
                parts.append(f"{', '.join(blocked)} {'is' if len(blocked) == 1 else 'are'} kept by HR and cannot be changed here.")
            self.message = ' '.join(parts)
            return False
        return True

    def _request_ok(self, request, view, obj, c, action, name):
        if f'change_{name}' in c.codes:
            return True
        cur = getattr(obj, 'status', '')
        mine = self._is_requester(obj, c)
        if action in APPROVE_ACTIONS:
            if mine:
                self.message = 'You cannot approve or reject your own request.'
                return False
            if not self._is_step_approver(obj, request.user.pk):
                self.message = 'Only the approver of the pending step can approve or reject this request.'
                return False
            return True
        if action in ('update', 'partial_update', 'destroy'):
            if not is_pending(cur):
                self.message = f'This request is already {cur}; it can no longer be changed or deleted.'
                return False
            data = _data(request) if action != 'destroy' else {}
            for f in guarded_fields(type(obj)):
                if f not in data or same_value(obj, f, data.get(f)):
                    continue
                if f == 'status' and is_withdraw(data.get(f)) and mine:
                    continue  # the requester withdraws a pending request
                if f == 'status' and is_pending(data.get(f)):
                    continue
                self.message = ('Use Approve or Reject: the requester cannot set the status of a request.' if f == 'status'
                                else f'"{f}" is filled in by the approver, not by the requester.')
                return False
        return True

    @staticmethod
    def _is_requester(obj, c):
        from .access import emp_path
        ep = emp_path(type(obj))
        emp = obj
        try:
            for part in [p for p in (ep or '').split('__') if p]:
                emp = getattr(emp, part, None)
        except Exception:
            emp = None
        if emp is not None and c.emp is not None and getattr(emp, 'pk', None) == c.emp.pk:
            return True
        return getattr(obj, 'created_by_id', None) == c.user.pk

    @staticmethod
    def _is_step_approver(obj, uid):
        from .approvals import is_approval_step, OPEN
        for rel in obj._meta.related_objects:
            if not rel.one_to_many or not is_approval_step(rel.related_model):
                continue
            try:
                steps = getattr(obj, rel.get_accessor_name()).all()
            except Exception:
                continue
            for s in steps:
                st = str(getattr(s, 'status', '') or '').lower()
                if not st.startswith(OPEN):
                    continue
                actors = {getattr(s, f, None) for f in ('approver_id', 'deligate_to_id', 'delegate_to_id', 'delegate_id')}
                if uid in actors:
                    return True
        return False
