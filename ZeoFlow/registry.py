"""What the engine knows about the apps: which record types each app has, their fields (with type, choices and
links), the operators and the action types. Built from the Django models – a new module registers itself by being
installed (no list to maintain)."""
from django.apps import apps
from django.db import models

# app key -> Django app labels
MODULES = {
    'hrms': ['EmpManagement', 'OrganisationManager', 'calendars', 'LeavePolicy', 'HRActions', 'AttendancePlus', 'ShiftPlanner',
             'AssetPlus', 'EmployeeProfile', 'ExpenseManagement', 'PerformanceManagement', 'RecruitmentManagement',
             'LearningManagement', 'LearningPlus', 'SelfService'],
    'payroll': ['PayrollManagement', 'PayrollPlus'],
    'projects': ['ZeoProjects', 'ProjectManagement', 'ProjectControl'],
    'crm': ['ZeoCRM'],
    'property': ['ZeoProperty'],
    'maintenance': ['ZeoMaintenance'],
    'hse': ['ZeoHSE'],
    'settings': ['UserManagement', 'Chatter'],
}
MODULE_LABELS = {'hrms': 'HR', 'payroll': 'Payroll', 'projects': 'Projects', 'crm': 'CRM', 'property': 'Property',
                 'maintenance': 'Maintenance', 'hse': 'HSE', 'settings': 'Company & users'}
SKIP_WORDS = ('log', 'template', 'notification', 'token', 'history', 'snapshot', 'setting', 'layout', 'udf', 'customfield', 'extra', 'comment', 'announcementview', 'clock', 'critirea', 'attachment',
              'fieldvalue', 'fielddef', 'bulkupload', 'emailconfiguration', 'through', 'cache', 'permission', 'inbox', 'audit')
SKIP_FIELDS = {'password', 'otp', 'otp_created_at'}

OPERATORS = [
    ('eq', 'Equals'), ('ne', 'Not equals'), ('contains', 'Contains'), ('not_contains', 'Does not contain'),
    ('starts', 'Starts with'), ('ends', 'Ends with'), ('gt', 'Greater than'), ('lt', 'Less than'), ('gte', 'Greater than or equal'),
    ('lte', 'Less than or equal'), ('empty', 'Is empty'), ('not_empty', 'Is not empty'), ('true', 'Is true'), ('false', 'Is false'),
    ('between', 'Between'), ('not_between', 'Not between'), ('in', 'In list'), ('not_in', 'Not in list'),
    ('changed_from', 'Changed from'), ('changed_to', 'Changed to'), ('changed', 'Changed (any value)'),
    ('date_before', 'Date before'), ('date_after', 'Date after'), ('date_today', 'Date is today'), ('date_overdue', 'Date is overdue (before today)'),
    ('related_exists', 'Related record exists'), ('related_not_exists', 'No related record'),
]
ACTIONS = [
    ('send_email', 'Send an e-mail', 'E-mail (template or text) to users, roles, the employee, the owner or addresses'),
    ('notify', 'Send an in-app notification', 'Bell notification to users, a role or the record owner'),
    ('create_task', 'Create a task / call / meeting', 'An activity on the record, assigned with a due date'),
    ('update_fields', 'Update fields', 'Set one or more fields of the record'),
    ('create_record', 'Create a related record', 'A new record in another module, with field mapping'),
    ('assign_owner', 'Assign an owner', 'Set a user field by a rule (user, role, manager, round robin)'),
    ('submit_approval', 'Submit for approval', 'Start an approval flow; actions on approval / rejection'),
    ('wait', 'Wait', 'Pause for a duration or until a date field, then continue'),
    ('branch', 'Conditional branch', 'If conditions hold run these actions, otherwise those'),
    ('stop', 'Stop', 'End the run (with an optional note)'),
]
TYPES_FOR_OPERATOR = {
    'text': ['eq', 'ne', 'contains', 'not_contains', 'starts', 'ends', 'empty', 'not_empty', 'in', 'not_in', 'changed', 'changed_from', 'changed_to'],
    'number': ['eq', 'ne', 'gt', 'lt', 'gte', 'lte', 'between', 'not_between', 'empty', 'not_empty', 'changed', 'changed_from', 'changed_to'],
    'date': ['eq', 'ne', 'date_before', 'date_after', 'date_today', 'date_overdue', 'between', 'empty', 'not_empty', 'changed'],
    'datetime': ['date_before', 'date_after', 'date_today', 'date_overdue', 'empty', 'not_empty', 'changed'],
    'bool': ['true', 'false', 'changed'],
    'choice': ['eq', 'ne', 'in', 'not_in', 'empty', 'not_empty', 'changed', 'changed_from', 'changed_to'],
    'link': ['eq', 'ne', 'in', 'not_in', 'empty', 'not_empty', 'changed', 'changed_from', 'changed_to', 'related_exists', 'related_not_exists'],
}


def _nice(s):
    s = str(s or '').replace('_', ' ').strip()
    return s[:1].upper() + s[1:]


def model_key(model):
    return f'{model._meta.app_label}.{model._meta.model_name}'


def get_model(key):
    try:
        app_label, name = key.split('.', 1)
        return apps.get_model(app_label, name)
    except Exception:
        return None


def module_of(model):
    for k, labels in MODULES.items():
        if model._meta.app_label in labels:
            return k
    return ''


def modules():
    out = []
    for key, labels in MODULES.items():
        if any(apps.is_installed(l) for l in labels):
            out.append({'key': key, 'label': MODULE_LABELS[key]})
    return out


def models_of(module=None):
    out = []
    for key, labels in MODULES.items():
        if module and key != module:
            continue
        for label in labels:
            if not apps.is_installed(label):
                continue
            for m in apps.get_app_config(label).get_models():
                name = m._meta.model_name
                if m._meta.auto_created or m._meta.proxy or any(w in name for w in SKIP_WORDS):
                    continue
                out.append({'key': model_key(m), 'module': key, 'label': _nice(m._meta.verbose_name), 'app': label})
    out.sort(key=lambda d: (d['module'], d['label']))
    return out


def field_type(f):
    if isinstance(f, (models.BooleanField,)):
        return 'bool'
    if f.choices:
        return 'choice'
    if f.is_relation:
        return 'link'
    if isinstance(f, (models.DateTimeField,)):
        return 'datetime'
    if isinstance(f, (models.DateField,)):
        return 'date'
    if isinstance(f, (models.IntegerField, models.DecimalField, models.FloatField)):
        return 'number'
    if isinstance(f, models.EmailField):
        return 'email'
    return 'text'


def fields_of(key, with_related=True):
    m = get_model(key)
    if m is None:
        return []
    out = []
    for f in m._meta.get_fields():
        if f.name in SKIP_FIELDS or getattr(f, 'auto_created', False) and not getattr(f, 'concrete', False):
            continue
        if f.many_to_many and not f.concrete:
            continue
        if not getattr(f, 'concrete', False):
            continue
        d = {'key': f.name, 'label': _nice(getattr(f, 'verbose_name', f.name)), 'type': field_type(f),
             'required': not (getattr(f, 'null', False) or getattr(f, 'blank', False)) and not f.primary_key and not getattr(f, 'has_default', lambda: False)()}
        if f.choices:
            d['choices'] = [{'value': k, 'label': str(v)} for k, v in f.flatchoices][:80]
        if f.is_relation and f.related_model is not None:
            d['link'] = model_key(f.related_model)
            d['link_label'] = _nice(f.related_model._meta.verbose_name)
            d['many'] = bool(f.many_to_many)
            rm = f.related_model._meta
            d['user'] = rm.app_label == 'UserManagement' and rm.model_name == 'customuser'
            d['employee'] = rm.app_label == 'EmpManagement' and rm.model_name == 'emp_master'
        if f.primary_key:
            d['label'] = 'ID'
            d['type'] = 'number'
        out.append(d)
    if with_related:   # one level of linked fields: employee.name, branch.code … (read only, for conditions and variables)
        for d in list(out):
            if d['type'] == 'link' and not d.get('many'):
                sub = get_model(d['link'])
                if sub is None:
                    continue
                for f in sub._meta.concrete_fields[:40]:
                    if f.name in SKIP_FIELDS or f.primary_key or f.is_relation:
                        continue
                    out.append({'key': f"{d['key']}__{f.name}", 'label': f"{d['label']} › {_nice(f.verbose_name)}", 'type': field_type(f),
                                'related': True, **({'choices': [{'value': k, 'label': str(v)} for k, v in f.flatchoices][:80]} if f.choices else {})})
    return out


def label_of(obj):
    try:
        return str(obj)[:255]
    except Exception:
        return f'#{getattr(obj, "pk", "")}'


def record_link(key, pk):
    if key == 'EmpManagement.emp_master':
        return f'/main-sidebar/sub-sidebar/employee-details/{pk}/details'
    return f'/main-sidebar/report-options/rec/{key}/{pk}'
