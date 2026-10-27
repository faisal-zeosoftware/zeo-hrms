"""v1.61.0 – legal hold: while an employee is under an active hold, ZEO refuses to delete or anonymise their records.

Guarded deletes (pre_delete, any screen, import undo or script): the employee record, employee documents and their custom
fields, family members and dependants, identity records, letter requests, HR tickets, disciplinary cases, resignations,
leave requests and payslips. Anonymisation and the retention list skip held employees.
"""
from django.apps import apps
from django.db.models.signals import pre_delete
from rest_framework.exceptions import PermissionDenied

GUARDED = {   # model label → how to find the employee id on a record
    'EmpManagement.emp_master': 'pk',
    'EmpManagement.Emp_Documents': 'emp_id_id',
    'EmpManagement.Doc_CustomFieldValue': 'emp_documents.emp_id_id',
    'EmpManagement.emp_family': 'emp_id_id',
    'EmpManagement.EmployeeResignation': 'employee_id',
    'EmployeeProfile.DependentExtra': 'employee_id',
    'EmployeeProfile.EmployeeIdentity': 'employee_id',
    'SelfService.LetterRequest': 'employee_id',
    'ZeoHR.HRTicket': 'employee_id',
    'ZeoHR.DisciplinaryCase': 'employee_id',
    'calendars.employee_leave_request': 'employee_id',
    'PayrollManagement.Payslip': 'employee_id',
}


class LegalHoldError(PermissionDenied):
    default_code = 'legal_hold'


def active(employee_id):
    if not employee_id:
        return None
    try:
        from .models import LegalHold
        return LegalHold.objects.filter(employee_id=employee_id, released_at__isnull=True).exclude(scope='company').first()
    except Exception:   # table not there yet (company being created)
        return None


def held_ids():
    from .models import LegalHold
    return set(LegalHold.objects.filter(released_at__isnull=True).exclude(employee_id=0).values_list('employee_id', flat=True))


def message(h):
    from Translations.i18n import tr
    ref = f' ({h.case_ref})' if h.case_ref else ''
    return tr('This employee is under a legal hold{0}: {1}. Their records cannot be deleted until the hold is released.', ref, h.reason)


def _emp_id(obj, how):
    try:
        v = obj
        for part in how.split('.'):
            v = getattr(v, part)
        return v
    except Exception:
        return None


def _guard(sender, instance, **kw):
    how = GUARDED.get(sender._meta.label)
    if how is None:
        return
    h = active(_emp_id(instance, how))
    if h is not None:
        raise LegalHoldError(message(h))


def install():
    for label in GUARDED:
        try:
            m = apps.get_model(label)
        except LookupError:
            continue
        pre_delete.connect(_guard, sender=m, dispatch_uid=f'legalhold-{label}')
