"""v1.22.1 – document numbers of the other transaction screens from Settings › Document numbering.

Leave, loan, advance salary, air ticket, general, document, resignation, asset and late-in / early-out requests and
payroll runs always took their number from the branch's Document Numbering setup. The screens below numbered
themselves (e.g. ER-2026-0001) or had no number at all. They are now document types of the same setup:

- when the record's branch has a setup row for the type, the number comes from it (prefix, suffix, length, counter,
  date range – the same rules as a leave request);
- when it has none yet, the record keeps its own numbering, as before (new branches get a row for every type;
  existing branches get theirs with `python manage.py fix_document_prefixes --add-missing`).

The number is set when the record is first saved (pre_save), so every path – screen, self service, import, API,
automatic jobs – gets one.
"""
import logging

from django.apps import apps
from django.db.models.signals import pre_save
from django.utils import timezone

log = logging.getLogger(__name__)

# model → (document type, number field, own prefix used when the branch has no setup row)
SCREENS = {
    'ExpenseManagement.Trip': ('expense_trip', 'number', 'TR'),
    'ExpenseManagement.ExpenseReport': ('expense_report', 'number', 'ER'),
    'ExpenseManagement.ExpenseAdvance': ('expense_advance', 'number', 'ADV'),
    'SelfService.ProfileChangeRequest': ('profile_change_request', 'number', 'PCR'),
    'SelfService.LetterRequest': ('letter_request', 'number', 'LTR'),
    'SelfService.Grievance': ('grievance', 'number', 'GRV'),
    'AssetPlus.AssetReturn': ('asset_return', 'number', 'RET'),
    'AssetPlus.AssetTransfer': ('asset_transfer', 'number', 'TRF'),
    'AssetPlus.MaintenanceRecord': ('asset_maintenance', 'number', 'MNT'),
    'AssetPlus.AssetDamage': ('asset_damage', 'number', 'DMG'),
    'AssetPlus.AssetLoss': ('asset_loss', 'number', 'LOS'),
    'AssetPlus.AssetDisposal': ('asset_disposal', 'number', 'DSP'),
    'RecruitmentManagement.ManpowerRequisition': ('manpower_requisition', 'document_number', 'MRF'),
    'RecruitmentManagement.Offer': ('job_offer', 'document_number', 'OFR'),
    'AttendancePlus.CorrectionRequest': ('attendance_correction', 'number', 'ACR'),
    'HRActions.EmployeeTransfer': ('employee_transfer', 'number', 'ETR'),
    'PayrollPlus.ExitSettlement': ('exit_settlement', 'number', 'FS'),
    'ShiftPlanner.ShiftRequest': ('shift_request', 'number', 'SHR'),
}

# the labels shown in Settings › Document numbering (added to DocumentNumbering.DOCUMENT_TYPES)
TYPE_LABELS = [
    ('expense_trip', 'Business Trip'), ('expense_report', 'Expense Report'), ('expense_advance', 'Expense Advance'),
    ('profile_change_request', 'Profile Change Request'), ('letter_request', 'HR Letter Request'), ('grievance', 'Grievance'),
    ('asset_return', 'Asset Return'), ('asset_transfer', 'Asset Transfer'), ('asset_maintenance', 'Asset Maintenance'),
    ('asset_damage', 'Asset Damage'), ('asset_loss', 'Asset Loss'), ('asset_disposal', 'Asset Disposal'),
    ('manpower_requisition', 'Manpower Requisition'), ('job_offer', 'Job Offer'),
    ('attendance_correction', 'Attendance Correction'), ('employee_transfer', 'Employee Transfer'),
    ('exit_settlement', 'Final Settlement'), ('shift_request', 'Shift Request'),
]


def branch_of(obj):
    """The branch (brnch_mstr) a record belongs to: its own branch, the employee's, or the asset's."""
    B = apps.get_model('OrganisationManager', 'brnch_mstr')
    for name in ('branch', 'from_branch'):
        b = getattr(obj, name, None) if name in {f.name for f in obj._meta.fields} else None
        if b is not None and not isinstance(b, int):
            return b
    for name in ('branch_id', 'from_branch_id'):
        v = getattr(obj, name, None)
        if isinstance(v, int) and v:
            return B.objects.filter(pk=v).first()
    emp = getattr(obj, 'employee', None) if 'employee' in {f.name for f in obj._meta.fields} else None
    if emp is not None and not isinstance(emp, int):
        return getattr(emp, 'emp_branch_id', None)
    eid = getattr(obj, 'employee_id', None)
    if eid:
        E = apps.get_model('EmpManagement', 'emp_master')
        e = E.objects.filter(pk=eid).select_related('emp_branch_id').first()
        if e is not None:
            return e.emp_branch_id
    aid = getattr(obj, 'asset_id', None)
    if aid:
        try:
            P = apps.get_model('AssetPlus', 'AssetProfile')
            bid = P.objects.filter(asset_id=aid).values_list('branch_id', flat=True).first()
            if bid:
                return B.objects.filter(pk=bid).first()
        except LookupError:
            pass
    return None


def own_number(model, field, prefix):
    """The numbering the screen had before: PREFIX-YEAR-0001."""
    year = timezone.now().year
    base = f'{prefix}-{year}-'
    n = model.objects.filter(**{f'{field}__startswith': base}).count() + 1
    while model.objects.filter(**{field: f'{base}{n:04d}'}).exists():
        n += 1
    return f'{base}{n:04d}'


def _assign(sender, instance, **kwargs):
    if instance.pk is not None or kwargs.get('raw'):
        return
    spec = SCREENS.get(f'{sender._meta.app_label}.{sender.__name__}')
    if not spec:
        return
    doc_type, field, prefix = spec
    current = getattr(instance, field, '') or ''
    DN = apps.get_model('OrganisationManager', 'DocumentNumbering')
    branch = None
    try:
        branch = branch_of(instance)
    except Exception:
        log.exception('document number: branch of %s', sender.__name__)
    max_len = sender._meta.get_field(field).max_length or 50
    if not current or current.startswith(f'{prefix}-'):   # v1.56.0 the numbering pattern of the setup guide, when switched on
        try:
            from ZeoAdmin.numbering import next_number as _pattern_number
            num = _pattern_number(doc_type, branch, taken=lambda n: sender.objects.filter(**{field: n}).exists())
        except ImportError:
            num = None
        if num and len(num) <= max_len:
            setattr(instance, field, num)
            return
    if branch is not None and DN.objects.filter(branch_id=branch, type=doc_type).exists():
        # the screen's own number (if it made one) is replaced by the setup's
        if not current or current.startswith(f'{prefix}-'):
            num = DN.next_for(branch, doc_type, taken=lambda n: sender.objects.filter(**{field: n}).exists())
            if num and len(num) <= max_len:
                setattr(instance, field, num)
                return
            if num:
                log.warning('document number %s longer than %s characters – own numbering used', num, max_len)
    if not current:
        setattr(instance, field, own_number(sender, field, prefix))


def connect():
    for label in SCREENS:
        try:
            model = apps.get_model(label)
        except LookupError:
            continue
        pre_save.connect(_assign, sender=model, dispatch_uid=f'zeo-docnum-{label}')
