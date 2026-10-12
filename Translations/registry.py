"""v1.15.0 – which (model, field) pairs can be translated.

Add a model here and its list / detail APIs show the translated value, the Translate button works for its records and the
report centre translates the column values that are its names. Models of apps that are not installed are skipped.
"""
from django.apps import apps

# 'app_label.Model': (screen label, [(field, field label), ...])
TRANSLATABLE = {
    'OrganisationManager.dept_master': ('Department', [('dept_name', 'Department name')]),
    'OrganisationManager.desgntn_master': ('Designation', [('desgntn_job_title', 'Designation')]),
    'OrganisationManager.ctgry_master': ('Category', [('ctgry_title', 'Category name')]),
    'OrganisationManager.brnch_mstr': ('Branch', [('branch_name', 'Branch name')]),
    'OrganisationManager.AssetType': ('Asset category', [('name', 'Name')]),
    'calendars.leave_type': ('Leave type', [('name', 'Leave type name'), ('description', 'Description')]),
    'calendars.Shift': ('Shift', [('name', 'Shift name')]),
    'calendars.ShiftPattern': ('Shift pattern', [('name', 'Name')]),
    'calendars.holiday_calendar': ('Holiday calendar', [('calendar_title', 'Calendar name')]),
    'calendars.holiday': ('Holiday', [('description', 'Holiday name')]),
    'PayrollManagement.SalaryComponent': ('Salary component', [('name', 'Component name')]),
    'EmpManagement.document_type': ('Document type', [('type_name', 'Document type')]),
    'EmpManagement.DocRequestType': ('Document request type', [('type_name', 'Request type')]),
    'OrgStructure.Location': ('Location', [('name', 'Name')]),
    'OrgStructure.Division': ('Division', [('name', 'Name')]),
    'OrgStructure.Section': ('Section', [('name', 'Name')]),
    'OrgStructure.CostCenter': ('Cost centre', [('name', 'Name')]),
    'OrgStructure.Grade': ('Grade', [('name', 'Name')]),
    'OrgStructure.JobPosition': ('Position', [('name', 'Name')]),
    'OrgStructure.EmploymentType': ('Employment type', [('name', 'Name')]),
    'ExpenseManagement.ExpenseCategory': ('Expense category', [('name', 'Name')]),
    'ExpenseManagement.CostCenter': ('Expense cost centre', [('name', 'Name')]),
    'LearningPlus.TrainingCategory': ('Training category', [('name', 'Name')]),
    'Core.cntry_mstr': ('Country', [('country_name', 'Country name')]),
    'Core.Nationality': ('Nationality', [('N_name', 'Nationality')]),
    'Core.ReligionMaster': ('Religion', [('religion', 'Religion')]),
    # e-mail templates: the company's own Arabic subject / body (used for readers whose language is Arabic)
    'EmpManagement.EmailTemplate': ('E-mail template', [('subject', 'Subject'), ('body', 'Body')]),
    'EmpManagement.DocExpEmailTemplate': ('Document expiry e-mail', [('subject', 'Subject'), ('body', 'Body')]),
    'EmpManagement.DocRequestEmailTemplate': ('Document request e-mail', [('subject', 'Subject'), ('body', 'Body')]),
    'EmpManagement.ResignationEmailTemplate': ('Resignation e-mail', [('subject', 'Subject'), ('body', 'Body')]),
    'calendars.LvEmailTemplate': ('Leave e-mail', [('subject', 'Subject'), ('body', 'Body')]),
    'calendars.LatinEarlyoutEmailTemplate': ('Late in / early out e-mail', [('subject', 'Subject'), ('body', 'Body')]),
    'PayrollManagement.LoanEmailTemplate': ('Loan e-mail', [('subject', 'Subject'), ('body', 'Body')]),
    'PayrollManagement.AdvanceSalaryEmailTemplate': ('Advance salary e-mail', [('subject', 'Subject'), ('body', 'Body')]),
    'PayrollManagement.AirticketEmailTemplate': ('Air ticket e-mail', [('subject', 'Subject'), ('body', 'Body')]),
    'OrganisationManager.AssetEmailTemplate': ('Asset e-mail', [('subject', 'Subject'), ('body', 'Body')]),
}

# report / list columns whose values are names of these models (report centre: department column → Arabic name)
VALUE_COLUMNS = {
    'department': 'OrganisationManager.dept_master', 'dept_name': 'OrganisationManager.dept_master',
    'designation': 'OrganisationManager.desgntn_master', 'desgntn_job_title': 'OrganisationManager.desgntn_master',
    'category': 'OrganisationManager.ctgry_master', 'ctgry_title': 'OrganisationManager.ctgry_master',
    'branch': 'OrganisationManager.brnch_mstr', 'branch_name': 'OrganisationManager.brnch_mstr',
    'leave_type': 'calendars.leave_type', 'leave_type_name': 'calendars.leave_type',
    'component': 'PayrollManagement.SalaryComponent', 'component_name': 'PayrollManagement.SalaryComponent',
    'document_type': 'EmpManagement.document_type', 'doc_type': 'EmpManagement.document_type',
    'nationality': 'Core.Nationality', 'religion': 'Core.ReligionMaster', 'country': 'Core.cntry_mstr',
    'shift': 'calendars.Shift', 'shift_name': 'calendars.Shift',
    'location': 'OrgStructure.Location', 'division': 'OrgStructure.Division', 'section': 'OrgStructure.Section',
    'cost_center': 'OrgStructure.CostCenter', 'cost_centre': 'OrgStructure.CostCenter', 'grade': 'OrgStructure.Grade',
    'position': 'OrgStructure.JobPosition', 'employment_type': 'OrgStructure.EmploymentType',
    'asset_type': 'OrganisationManager.AssetType', 'expense_category': 'ExpenseManagement.ExpenseCategory',
}

_FIELDS = {}


def _ok(label):
    try:
        apps.get_model(label)
        return True
    except (LookupError, ValueError):
        return False


def entries():
    return {k: v for k, v in TRANSLATABLE.items() if _ok(k)}


def label_of(model):
    return model._meta.label if model is not None else ''


def fields_for(model_or_label):
    """[field, ...] translatable on this model ([] = not translatable)."""
    label = model_or_label if isinstance(model_or_label, str) else getattr(getattr(model_or_label, '_meta', None), 'label', '')
    if label in _FIELDS:
        return _FIELDS[label]
    spec = TRANSLATABLE.get(label)
    if spec is None and not isinstance(model_or_label, str):  # proxy / subclass of a registered model
        try:
            concrete = model_or_label._meta.concrete_model._meta.label
            spec = TRANSLATABLE.get(concrete)
        except Exception:
            spec = None
    out = [f for f, _ in spec[1]] if spec else []
    _FIELDS[label] = out
    return out


def concrete_label(model):
    try:
        return model._meta.concrete_model._meta.label
    except Exception:
        return label_of(model)


def model_for(label):
    try:
        return apps.get_model(label)
    except (LookupError, ValueError):
        return None
