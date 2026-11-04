# v1.62.11 (item 256): new module – a rejected payslip gives back everything it used up (as a deleted payslip does)
"""v1.62.11 (item 256) – a rejected payslip paid nothing, so it gives back what it used up, exactly as a deleted payslip does
(PayrollManagement/signals.py `_payslip_deleted_*`, payctl.payslip_deleted, AssetPlus unmark_payslip, cycles.release_orphans):

  loan instalments          LoanRepayment of the payslip taken back, loan balance back, a loan closed by it open again (v1.16.0 –
                            already done before 1.62.11, signals.reverse_loan_repayments)
  advances                  Deducted → Approved                          (consumed note of the payslip, v1.62.2 item 44)
  air-ticket encashments    PROCESSED → APPROVED, allocation amount back (consumed note)
  general requests          is_processed → False                         (consumed note)
  monthly inputs            the figure back (when still 0)               (consumed note)
  leave encashments         processed with the run → approved, not linked to the run
  arrear lines              (salary arrears, leave-pay recovery of PayrollPlus/recovery.py) included → waiting again
  holds / releases          a hold of the month waits again (pending), a release is paid again by the next run (payctl)
  asset recovery            deducted instalments → pending
  accruals                  nothing to do: entries of a rejected payslip do not count (accruals.entries)
  recovery instalments      v1.63.0 (item 28): RecoveryEntry of the payslip → given_back (balance back, a completed recovery runs again)

The next payslip of that month (another run, or the run recalculated) takes them again – exactly once: the consumed note keeps
"given_back", so a later delete of the rejected payslip gives nothing back a second time.

A rejected payslip approved again takes back what it gave, when it is still free (retake); when something was taken since by
another payslip the approval is refused (it would be paid twice) – problems().
"""
import logging
from decimal import Decimal

from django.apps import apps

from Translations.i18n import tr

log = logging.getLogger(__name__)
M = apps.get_model
ZERO = Decimal('0.00')


def _row(payslip):
    from PayrollManagement.signals import _v1622_consumed_key
    try:
        CO = M('ZeoApps', 'CompanyOption')
    except LookupError:
        return None, None
    return CO, CO.objects.filter(key=_v1622_consumed_key(payslip.pk)).first()


def give_back(payslip):
    """The payslip was rejected: give back what it used up. Returns the counts (the loans are done by the caller)."""
    from PayrollManagement.signals import _v1622_consumed_key, restore_consumed_items
    CO, row = _row(payslip)
    used = dict((row.value if row else None) or {})
    if used.get('given_back'):
        return {}
    LE = M('PayrollManagement', 'LeaveEncashment')
    enc = list(LE.objects.filter(payroll_run_id=payslip.payroll_run_id, employee_id=payslip.employee_id, status='processed')
               .values_list('pk', flat=True)) if payslip.payroll_run_id else []
    out = restore_consumed_items(payslip)   # advances, tickets, requests, inputs, leave encashments (deletes the note)
    note = dict(used, given_back=True, encashments=enc)
    # arrear lines (salary arrears and the leave-pay recovery lines) the payslip took
    try:
        from .models import ArrearLine
        ids = list(ArrearLine.objects.filter(included_payslip_id=payslip.pk, status='included').values_list('pk', flat=True))
        if ids:
            ArrearLine.objects.filter(pk__in=ids).update(status='pending', included_payslip_id=None)
            M('PayrollManagement', 'Payslip').objects.filter(pk=payslip.pk).update(arrears=ZERO)
        note['arrears'] = ids
        out['arrears'] = len(ids)
    except LookupError:
        pass
    # holds of the month and releases paid on it (payctl, item 228)
    try:
        from .models import SalaryHold
        from . import payctl
        note['holds'] = [[h.pk, str(h.held_amount)] for h in SalaryHold.objects.filter(payslip_id=payslip.pk).exclude(status='cancelled')]
        note['releases'] = list(SalaryHold.objects.filter(release_payslip_id=payslip.pk).values_list('pk', flat=True))
        out['holds'] = payctl.payslip_deleted(payslip)
    except LookupError:
        pass
    # asset damage / loss recovery instalments
    try:
        RI = M('AssetPlus', 'RecoveryInstalment')
        note['assets'] = list(RI.objects.filter(payslip_id=payslip.pk, status='deducted').values_list('pk', flat=True))
        if note['assets']:
            from AssetPlus.services import unmark_payslip
            out['assets'] = unmark_payslip(payslip.pk)
    except (LookupError, ImportError):
        pass
    try:   # v1.63.0 (item 28): recovery instalments the payslip took
        from . import recoveries as _v1630_rec
        note['recoveries'] = _v1630_rec.give_back_rejected(payslip.pk)
        out['recoveries'] = len(note['recoveries'])
    except LookupError:
        pass
    if CO is not None:
        CO.objects.update_or_create(key=_v1622_consumed_key(payslip.pk), defaults={'value': note})
    log.info('Payslip %s rejected: gave back %s', payslip.pk, out)
    return out


def _note(payslip):
    _CO, row = _row(payslip)
    v = dict((row.value if row else None) or {})
    return row, (v if v.get('given_back') else None)


def problems(payslip):
    """What the rejected payslip gave back and was taken since by another payslip (approving it again would pay it twice)."""
    _r, n = _note(payslip)
    if not n:
        return []
    out = []
    AV = M('PayrollManagement', 'AdvanceSalaryRequest')
    if n.get('advances') and AV.objects.filter(pk__in=n['advances']).exclude(status='Approved').exists():
        out.append(tr('advance salary'))
    if n.get('requests') and M('EmpManagement', 'GeneralRequest').objects.filter(pk__in=n['requests'], is_processed=True).exists():
        out.append(tr('general requests'))
    if any(M('PayrollManagement', 'AirTicketRequest').objects.filter(pk=t.get('id')).exclude(status='APPROVED').exists() for t in n.get('tickets') or []):
        out.append(tr('air ticket encashment'))
    if n.get('encashments') and M('PayrollManagement', 'LeaveEncashment').objects.filter(pk__in=n['encashments']).exclude(status='approved').exists():
        out.append(tr('leave encashment'))
    try:
        from .models import ArrearLine, SalaryHold
        if n.get('arrears') and ArrearLine.objects.filter(pk__in=n['arrears']).exclude(status='pending').exists():
            out.append(tr('arrears'))
        if n.get('holds') and SalaryHold.objects.filter(pk__in=[h[0] for h in n['holds']], payslip_id__isnull=False).exists():
            out.append(tr('held salary'))
        if n.get('releases') and SalaryHold.objects.filter(pk__in=n['releases'], release_payslip_id__isnull=False).exists():
            out.append(tr('held salary released'))
    except LookupError:
        pass
    if n.get('recoveries'):   # v1.63.0 (item 28)
        from . import recoveries as _v1630_rec
        if _v1630_rec.rejected_problems(n['recoveries']):
            out.append(tr('recovery instalments'))
    return out


def retake(payslip):
    """A rejected payslip is approved again: it takes back what it gave (problems() is empty – checked before the save)."""
    from django.utils import timezone
    row, n = _note(payslip)
    if not n:
        return {}
    out = {}
    out['advances'] = M('PayrollManagement', 'AdvanceSalaryRequest').objects.filter(pk__in=n.get('advances') or [], status='Approved').update(status='Deducted')
    out['requests'] = M('EmpManagement', 'GeneralRequest').objects.filter(pk__in=n.get('requests') or [], is_processed=False).update(is_processed=True)
    ATR, ALL = M('PayrollManagement', 'AirTicketRequest'), M('PayrollManagement', 'AirTicketAllocation')
    out['tickets'] = 0
    for t in n.get('tickets') or []:
        if ATR.objects.filter(pk=t.get('id'), status='APPROVED').update(status='PROCESSED'):
            ALL.objects.filter(pk=t.get('allocation')).update(remaining_amount=ZERO)
            out['tickets'] += 1
    ESS = M('PayrollManagement', 'EmployeeSalaryStructure')
    out['inputs'] = sum(ESS.objects.filter(pk=i.get('id'), amount=Decimal(str(i.get('amount') or '0'))).update(amount=ZERO) for i in n.get('inputs') or [])
    out['encashments'] = M('PayrollManagement', 'LeaveEncashment').objects.filter(pk__in=n.get('encashments') or [], status='approved',
                                                                                 payroll_run__isnull=True).update(
        status='processed', payroll_run_id=payslip.payroll_run_id, processed_at=timezone.now())
    try:
        from .models import ArrearLine, SalaryHold
        ids = list(ArrearLine.objects.filter(pk__in=n.get('arrears') or [], status='pending').values_list('pk', flat=True))
        if ids:
            ArrearLine.objects.filter(pk__in=ids).update(status='included', included_payslip_id=payslip.pk)
            tot = sum((Decimal(str(a)) for a in ArrearLine.objects.filter(pk__in=ids).values_list('amount', flat=True)), ZERO)
            M('PayrollManagement', 'Payslip').objects.filter(pk=payslip.pk).update(arrears=tot)
        out['arrears'] = len(ids)
        for pk, amt in n.get('holds') or []:
            SalaryHold.objects.filter(pk=pk, payslip_id__isnull=True).update(payslip_id=payslip.pk, held_amount=Decimal(str(amt)), status='held')
        for h in SalaryHold.objects.filter(pk__in=n.get('releases') or [], release_payslip_id__isnull=True, status='to_release'):
            h.release_payslip_id, h.released_amount, h.status = payslip.pk, h.held_amount, 'released'
            h.save(update_fields=['release_payslip_id', 'released_amount', 'status', 'updated_at'])
    except LookupError:
        pass
    try:
        if n.get('assets'):
            from AssetPlus.services import mark_deducted
            PC = M('PayrollManagement', 'PayslipComponent')
            amt = sum((Decimal(str(x)) for x in PC.objects.filter(payslip=payslip, component__formula__icontains='asset_recovery_amount')
                       .values_list('amount', flat=True)), ZERO)
            if amt > 0:
                mark_deducted(payslip, amt)
    except ImportError:
        pass
    if n.get('recoveries'):   # v1.63.0 (item 28): the instalments counted again
        from . import recoveries as _v1630_rec
        out['recoveries'] = _v1630_rec.retake_rejected(n['recoveries'])
    keep = {k: v for k, v in n.items() if k in ('advances', 'tickets', 'requests', 'inputs')}
    row.value = keep
    row.save(update_fields=['value'])
    log.info('Payslip %s approved again: took back %s', payslip.pk, out)
    return out
