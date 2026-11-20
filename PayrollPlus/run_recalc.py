"""v1.67.24 (P-BUG-08 / P-BUG-12) – Recalculate a payroll run in place.

Before, the run page's Recalculate deleted the run and made a new one: the run got a new id (the page moved to a new address,
links in e-mails and to-dos broke), new document numbers, and its history (who prepared it, earlier calculations) was lost.

Now POST payroll/api/workspace/runs/<id>/recalculate/ keeps the run – same id, document numbers and preparer record:
  * only a run that is pending / processed and has no payslip sent for approval or decided (as the button already required);
  * the payslips are deleted (their signals give back loans, recoveries, expense reports, holds and monthly inputs as when the run
    was deleted), the AI audit of the run is removed (it described the old payslips – it must be run again);
  * the employees are chosen again with the run's own filters (branches, departments, categories) exactly as a new run would
    (joiners, leavers, employees paid by another run of the month), and the payroll is calculated again;
  * each recalculation is kept in the run's history (CompanyOption 'v16724_recalc:<run id>': when, who, payslips before / after,
    net before / after) – no new table; shown on the run page.
An off-cycle run (bonus, correction, arrears …) is refused here: it is edited on its own page.
"""
from decimal import Decimal

from django.apps import apps
from django.db import transaction
from django.utils import timezone

from .services import Refused

M = apps.get_model
KEY = 'v16724_recalc:{0}'


def _tr(s, *a):
    try:
        from Translations.i18n import tr
        return tr(s, *a)
    except Exception:   # noqa: BLE001
        return s.format(*a)


def history(run_id):
    CO = M('ZeoApps', 'CompanyOption')
    o = CO.objects.filter(key=KEY.format(run_id)).first()
    return list((o.value or {}).get('items') or []) if o else []


def _totals(run):
    P = M('PayrollManagement', 'Payslip')
    qs = P.objects.filter(payroll_run=run)
    net = sum((Decimal(str(x or 0)) for x in qs.values_list('net_salary', flat=True)), Decimal('0'))
    return qs.count(), net


def recalculate(run, user, background=False, token=None):
    """→ {'id', 'payslips', 'net', 'history'}; raises Refused with the reason."""
    P = M('PayrollManagement', 'Payslip')
    try:   # v1.75.1 not while it is being calculated
        from .run_background import is_calculating
        if is_calculating(run.pk):
            raise Refused(_tr('This payroll is still being calculated. Wait until it is finished.'), status=409)
    except ImportError:
        pass
    st = str(run.status or '').lower()
    if st not in ('pending', 'processed'):
        raise Refused(_tr('Only a run that is not approved or paid can be recalculated.'), status=409)
    try:
        from .models import OffCycleRun
        if OffCycleRun.objects.filter(payroll_run_id=run.pk).exists():
            raise Refused(_tr('An off-cycle run is changed on its own page (Payroll › Off-cycle runs).'), status=409)
    except ImportError:
        pass
    slips = P.objects.filter(payroll_run=run)
    # the same rule as the run page's button: nothing sent for approval yet (a payslip approved by itself when it was made – no
    # approval chain for its branch – is made again like the others)
    if slips.filter(confirm_status=True).exists() or slips.filter(status__in=['paid', 'Paid']).exists():
        raise Refused(_tr('Payslips were sent for approval – recalculating is no longer possible.'), status=409)
    from PayrollManagement.serializer import PayrollRunSerializer
    before_n, before_net = _totals(run)
    data = {'month': run.month, 'year': run.year, 'branch_ids': [b.pk for b in run.branch.all()],
            'department': [d.pk for d in run.department.all()], 'category': [c.pk for c in run.category.all()]}
    with transaction.atomic():
        for p in list(slips):   # one by one: each payslip's pre_delete signals give back what it took
            p.delete()
        ser = PayrollRunSerializer(instance=run, data=data, partial=True)
        if not ser.is_valid():
            msg = '; '.join(str(v[0] if isinstance(v, list) else v) for v in ser.errors.values()) or _tr('The payroll could not be recalculated.')
            raise Refused(msg, status=400)
        ids = getattr(ser, '_payroll_employee_ids', set())
        try:
            from ZeoAI.models import AuditRun
            AuditRun.objects.filter(payroll_run_id=run.pk).delete()
        except Exception:   # noqa: BLE001
            pass
        PR = M('PayrollManagement', 'PayrollRun')
        PR.objects.filter(pk=run.pk).update(status='pending')
        run.refresh_from_db()
        run.employees.clear()   # post_clear – the m2m handler only acts on post_add
        # v1.75.1: a large run is calculated again in the background (run_background) – progress on the page
        _bg = None
        try:
            from . import run_background as _bg
        except ImportError:
            _bg = None
        if _bg is not None and _bg.wanted(background, len(ids)):
            with _bg.deferred():
                run.employees.add(*sorted(ids))
            token = _bg.start(run, token, user, n_employees=len(ids))
        else:
            _bg = None
            run.employees.add(*sorted(ids))   # post_add → the payroll is calculated (PayrollManagement.signals.payrollrun_m2m_changed)
        RC = M('PayrollPlus', 'RunControl')
        RC.objects.filter(run_id=run.pk).update(prepared_by_id=getattr(user, 'pk', None), prepared_at=timezone.now(),
                                                approved_by_id=None, approved_at=None)
        if not RC.objects.filter(run_id=run.pk).exists():
            RC.objects.create(run_id=run.pk, prepared_by_id=getattr(user, 'pk', None))
        run.refresh_from_db()
        after_n, after_net = _totals(run)
        items = history(run.pk)
        items.append({'at': timezone.now().isoformat(), 'by': getattr(user, 'pk', None),
                      'by_name': (user.get_full_name() or user.username) if user is not None else '',
                      'payslips_before': before_n, 'payslips_after': after_n, 'net_before': str(before_net), 'net_after': str(after_net)})
        M('ZeoApps', 'CompanyOption').objects.update_or_create(key=KEY.format(run.pk), defaults={'value': {'items': items[-50:]}})
    out = {'id': run.pk, 'status': run.status, 'payslips': after_n, 'net': str(after_net), 'history': history_rows(run.pk)}
    if _bg is not None:
        out.update({'background': True, 'progress_token': token, 'payslips': 0})
    return out


def history_rows(run_id):
    out = []
    for h in reversed(history(run_id)):
        try:
            at = timezone.localtime(timezone.datetime.fromisoformat(h['at'])).strftime('%d/%m/%Y %H:%M')
        except Exception:   # noqa: BLE001
            at = h.get('at', '')
        out.append({'at': at, 'by': h.get('by_name', ''), 'payslips_before': h.get('payslips_before'), 'payslips_after': h.get('payslips_after'),
                    'net_before': h.get('net_before'), 'net_after': h.get('net_after')})
    return out
