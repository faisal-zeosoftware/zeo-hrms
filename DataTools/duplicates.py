"""
Duplicate checks for master data, applied to every DRF serializer.

RULES maps a model to the fields that must not repeat. Matching ignores
upper/lower case and surrounding spaces. Scope:
  'company' - unique in the company
  'branch'  - unique within the same branch (FK) or overlapping branches (M2M);
              records without a branch count as company-wide.
The error is returned as a normal 400 field error, e.g.
  {"dept_name": ["Department name “Sales” already exists in Adept Dubai Branch."]}
"""
import logging
from django.apps import apps
from django.db.models import ManyToManyField, Q
from rest_framework import serializers

logger = logging.getLogger(__name__)

RULES = {
    'EmpManagement.emp_master': [('emp_code', 'company'), ('emp_personal_email', 'company'), ('emp_company_email', 'company')],
    'EmpManagement.requesttype': [('name', 'company')],
    'EmpManagement.docrequesttype': [('type_name', 'company')],
    'EmpManagement.document_type': [('type_name', 'company')],
    'EmpManagement.docexpemailtemplate': [('template_name', 'branch')],
    'EmpManagement.employeebankdetail': [('account_number', 'company')],
    'EmpManagement.emp_documents': [('emp_doc_number', 'company')],
    'calendars.leave_type': [('name', 'branch'), ('code', 'branch')],
    'calendars.weekend_calendar': [('calendar_code', 'company')],
    'calendars.holiday_calendar': [('calendar_title', 'company')],
    'calendars.shift': [('name', 'company')],
    'calendars.shiftpattern': [('name', 'company')],
    'calendars.employeeshiftschedule': [('schedule_name', 'branch')],
    'calendars.attendancepolicy': [('name', 'branch')],
    'calendars.overtimepolicy': [('name', 'branch')],
    'OrganisationManager.brnch_mstr': [('branch_name', 'company'), ('branch_code', 'company')],
    'OrganisationManager.dept_master': [('dept_name', 'branch'), ('dept_code', 'company')],
    'OrganisationManager.desgntn_master': [('desgntn_job_title', 'branch'), ('desgntn_code', 'company')],
    'OrganisationManager.ctgry_master': [('ctgry_title', 'branch'), ('ctgry_code', 'company')],
    'OrganisationManager.fiscalyear': [('name', 'branch')],
    'OrganisationManager.companypolicy': [('title', 'branch')],
    'OrganisationManager.assettype': [('name', 'company')],
    'OrganisationManager.asset': [('serial_number', 'company')],
    'OrganisationManager.branchgeofence': [('location_name', 'branch')],
    'PayrollManagement.salarycomponent': [('name', 'branch'), ('code', 'branch')],
    'PayrollManagement.salarystructure': [('name', 'branch')],
    'PayrollManagement.loantype': [('loan_type', 'branch')],
    'PayrollManagement.airticketpolicy': [('name', 'branch')],
    'ProjectManagement.project': [('title', 'company')],
    'PerformanceManagement.kpi': [('name', 'company'), ('code', 'company')],
    'LearningManagement.course': [('title', 'company'), ('code', 'company')],
    'RecruitmentManagement.candidate': [('email', 'company')],
    'RecruitmentManagement.jobopening': [('job_code', 'company')],
    # custom fields of the form designer ("Has Car" and "has car" are the same field)
    'EmpManagement.Emp_CustomField': [('emp_custom_field', 'company')],
    'EmpManagement.EmpFamily_CustomField': [('emp_custom_field', 'company')],
    'EmpManagement.EmpJobHistory_CustomField': [('emp_custom_field', 'company')],
    'EmpManagement.EmpQualification_CustomField': [('emp_custom_field', 'company')],
    'EmpManagement.EmpDocuments_CustomField': [('emp_custom_field', 'company')],
}

_rules = None


def rules_for(model):
    global _rules
    if _rules is None:
        _rules = {}
        for label, rr in RULES.items():
            try:
                _rules[apps.get_model(label)] = rr
            except LookupError:
                logger.warning('duplicate rule for unknown model %s', label)
    return _rules.get(model)


def _branch_field(model):
    from AccessControl.access import branch_path
    bp, m2m = branch_path(model)
    if not bp or '__' in bp or bp == 'id':
        return None, False
    return bp, m2m


def _label(model, field):
    return 'Field name' if field == 'emp_custom_field' else pretty(field)


def find_duplicate(model, field, value, scope, branch_value, instance=None):
    """Return a short description of where the value already exists, or None."""
    if value in (None, '') or not isinstance(value, str):
        return None
    qs = model._default_manager.filter(**{f'{field}__iexact': value.strip()})
    if instance is not None and instance.pk:
        qs = qs.exclude(pk=instance.pk)
    if scope == 'branch':
        bf, m2m = _branch_field(model)
        if bf:
            if m2m:
                ids = [getattr(b, 'pk', b) for b in (branch_value or [])]
                if ids:
                    qs = qs.filter(Q(**{f'{bf}__in': ids}) | Q(**{f'{bf}__isnull': True})).distinct()
            else:
                bid = getattr(branch_value, 'pk', branch_value)
                qs = qs.filter(Q(**{bf: bid}) | Q(**{f'{bf}__isnull': True})) if bid else qs
    hit = qs.first()
    if not hit:
        return None
    if scope == 'branch':
        bf, m2m = _branch_field(model)
        try:
            if bf and m2m:
                names = ', '.join(str(b) for b in getattr(hit, bf).all()[:3])
                return f' in {names}' if names else ''
            if bf and getattr(hit, bf, None):
                return f' in {getattr(hit, bf)}'
        except Exception:
            pass
    return ''


def check(serializer, attrs):
    meta = getattr(serializer, 'Meta', None)
    model = getattr(meta, 'model', None)
    if model is None or not isinstance(attrs, dict):
        return
    rr = rules_for(model)
    if not rr:
        return
    inst = getattr(serializer, 'instance', None)
    if inst is not None and not hasattr(inst, 'pk'):
        inst = None
    errors = {}
    for field, scope in rr:
        if field in attrs:
            value = attrs.get(field)
        elif inst is not None and scope == 'branch':
            value = getattr(inst, field, None)  # branch changed on an update
        else:
            continue
        branch_value = None
        if scope == 'branch':
            bf, m2m = _branch_field(model)
            if bf:
                if bf in attrs:
                    branch_value = attrs[bf]
                elif inst is not None:
                    branch_value = list(getattr(inst, bf).all()) if m2m else getattr(inst, bf, None)
            if field not in attrs and bf not in attrs:
                continue
        where = find_duplicate(model, field, value, scope, branch_value, inst)
        if where is not None:
            errors[field] = [f'{_label(model, field)} “{value.strip()}” already exists{where}.']
    if errors:
        raise serializers.ValidationError(errors)


_installed = False


def install():
    global _installed
    if _installed:
        return
    orig = serializers.ModelSerializer.run_validation

    def run_validation(self, data=serializers.empty):
        try:
            from .forms import pre_data
            data = pre_data(self, data)   # v1.12.0: Yes / No, lists and numbers sent as JSON for custom field values
        except Exception:
            pass
        try:
            value = orig(self, data)
        except serializers.ValidationError as exc:
            # the database's own "… with this … already exists." in the same words as the checks above
            model = getattr(getattr(self, 'Meta', None), 'model', None)
            detail = exc.detail if isinstance(exc.detail, dict) else None
            if model is not None and detail and hasattr(data, 'get'):
                for field, errs in list(detail.items()):
                    if isinstance(errs, list) and any(getattr(e, 'code', '') == 'unique' and ' with this ' in str(e) for e in errs):
                        raw = data.get(field)
                        if isinstance(raw, str) and raw.strip():
                            detail[field] = [serializers.ErrorDetail(f'{_label(model, field)} “{raw.strip()}” already exists.', code='unique')]
            raise

        if not getattr(self, 'skip_duplicate_check', False):
            check(self, value)
        from .forms import check_value
        check_value(self, value)
        return value

    serializers.ModelSerializer.run_validation = run_validation
    _installed = True


ABBREV = {'custom': 'Custom', 'dept': 'Department', 'desgntn': 'Designation', 'ctgry': 'Category', 'emp': 'Employee', 'br': 'Branch',
          'lv': 'Leave', 'doc': 'Document', 'jh': 'Job history', 'ef': 'Family'}


def pretty(name):
    """dept_name -> Department name, desgntn_job_title -> Designation job title."""
    parts = [p for p in str(name).replace('-', '_').split('_') if p]
    if not parts:
        return str(name)
    words = [ABBREV.get(p.lower(), p) for p in parts]
    s = ' '.join(words).strip()
    return s[:1].upper() + s[1:].lower() if s.lower() == s else s[:1].upper() + s[1:]
