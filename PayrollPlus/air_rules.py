"""v1.62.3 (items 101, 102, 103, 105, 124) – the rules of an air ticket allocation in one place, used by the request API
(PayrollManagement serializer), the payroll cash payout (PayrollManagement.signals), the allocation job (PayrollManagement.tasks)
and the trip approval (ExpenseManagement). No model, no migration.

request_problem(...)   why a ticket / cash payout request cannot use an allocation (None = it can)
payout_problem(t)      why payroll must not pay an approved cash payout request (None = pay it)
employee_left(e, day)  the employee has left / is leaving (inactive, approved resignation / termination, leaving status)
policy_countries(e)    countries an employee matches a policy on (employee country, nationality, else the branch country)
allocation_due(...)    the policy frequency: may the job allocate the next ticket of the policy on this day?
"""
from datetime import date
from decimal import Decimal

from django.apps import apps

from Translations.i18n import tr

M = apps.get_model
ZERO = Decimal('0.00')
USED_REQ = ('APPROVED', 'PROCESSED')
LIVE_REQ = ('PENDING', 'APPROVED', 'PROCESSED')   # v1.62.3 (item 101): a request still using the allocation (not rejected)


def _day(d):
    if d is None:
        return None
    return d.date() if hasattr(d, 'date') and callable(d.date) else d


def add_years(d, n):
    try:
        return d.replace(year=d.year + n)
    except ValueError:   # 29 February
        return d.replace(year=d.year + n, day=28)


def _ref(r):
    return r.document_number or f'#{r.pk}'


def request_problem(employee_id, allocation, on_date=None, exclude_pk=None, request_type=None):
    """v1.62.3 (items 101, 102, 103): a ticket or cash payout request may only use an approved, active, not expired allocation of
    the same employee with an amount left, that has no other ticket / cash payout request (pending, approved or paid)."""
    if allocation is None:
        return tr('Choose the air ticket allocation.')
    if employee_id and allocation.employee_id != int(employee_id):   # item 102
        return tr('This air ticket allocation belongs to another employee.')
    if allocation.status == 'REJECTED':
        return tr('This air ticket allocation was rejected.')
    if allocation.status != 'APPROVED':   # item 103
        return tr('This air ticket allocation is not approved yet.')
    if not allocation.is_active:
        return tr('This air ticket allocation is inactive.')
    on = _day(on_date) or date.today()
    if allocation.expiry_date and allocation.expiry_date < on:   # item 103
        return tr('This air ticket allocation expired on {0}.', allocation.expiry_date.strftime('%d/%m/%Y'))
    left = allocation.remaining_amount if allocation.remaining_amount is not None else allocation.amount
    if (left or ZERO) <= 0:
        return tr('Nothing is left on this air ticket allocation.')
    R = M('PayrollManagement', 'AirTicketRequest')
    other = R.objects.filter(allocation=allocation, status__in=LIVE_REQ)
    if exclude_pk:
        other = other.exclude(pk=exclude_pk)
    # v1.66.0 (item 120): partial use – on a family policy with partial use a further ticket request is fine while every other request
    # of the allocation is a ticket with its travellers recorded and something is left (the others of the family travel later)
    from . import air_policy as _AP
    if request_type and other.exists() and _AP.partial_request_ok(allocation, list(other), request_type):
        return None
    other = other.order_by('pk').first()
    if other is not None:   # item 101
        return tr('This air ticket allocation is already used by request {0}.', _ref(other))
    return None


def payout_problem(ticket):
    """v1.62.3 (items 101, 102, 103): payroll pays an approved cash payout only on the employee's own approved, active allocation
    that had not expired on the request date and whose ticket was not taken. (Another cash payout of the same allocation is
    handled by the payroll itself: paid once.) Not paid → the request stays approved and is paid once the allocation is fixed."""
    a = getattr(ticket, 'allocation', None)
    if a is None:
        return 'no allocation'
    if a.employee_id != ticket.employee_id:
        return 'allocation of another employee'
    if a.status != 'APPROVED' or not a.is_active:
        return f'allocation {a.status.lower()}' + ('' if a.is_active else ', inactive')
    on = _day(ticket.request_date)
    if a.expiry_date and on and a.expiry_date < on:
        return f'allocation expired on {a.expiry_date:%d/%m/%Y}'
    R = M('PayrollManagement', 'AirTicketRequest')
    from . import air_policy as _AP
    if _AP.is_partial(a.policy_id):   # v1.66.0 (item 120): partial use – the rest of the family's tickets may be paid in cash
        if (a.remaining_amount if a.remaining_amount is not None else a.amount or ZERO) <= 0:
            return 'nothing left on the allocation'
        if any(not _AP.travel(r.pk) for r in R.objects.filter(allocation=a, request_type='TICKET', status__in=USED_REQ).exclude(pk=ticket.pk)):
            return 'ticket of the allocation already approved'
    elif R.objects.filter(allocation=a, request_type='TICKET', status__in=USED_REQ).exclude(pk=ticket.pk).exists():
        return 'ticket of the allocation already approved'
    why = _AP.payout_problem(ticket)   # v1.66.0 (item 119): "Pay in the next payroll" of the cash rule (setting next_payroll)
    if why:
        return why
    return None


def employee_left(emp, day=None):
    """v1.62.3 (item 124): inactive, an approved resignation / termination, or an employment status of a leaver."""
    if not getattr(emp, 'is_active', True) or getattr(emp, 'emp_status', True) is False:
        return True
    try:
        from PayrollManagement.signals import last_working_day
        if last_working_day(emp) is not None:
            return True
    except ImportError:
        pass
    try:
        EI = M('EmployeeProfile', 'EmploymentInfo')
        if EI.objects.filter(employee_id=emp.pk, status__in=('left', 'terminated', 'absconded', 'retired')).exists():
            return True
    except LookupError:
        pass
    return False


def policy_countries(emp):
    """v1.62.3 (item 124): the country on the employee, else the country of the nationality; the branch country always counts too
    (a policy made for the country of work applies to everyone working there)."""
    ids = set()
    try:
        from PayrollPlus.services import _home_countries
        ids.update(_home_countries(emp))
    except Exception:
        if emp.emp_country_id_id:
            ids.add(emp.emp_country_id_id)
    br = getattr(emp, 'emp_branch_id', None)
    if br is not None and getattr(br, 'br_country_id', None):
        ids.add(br.br_country_id)
    return ids


def policy_matches(p, emp):
    """Department / designation / category / branch – a policy that lists none of them applies to all."""
    for rel, val in (('eligible_departments', emp.emp_dept_id_id), ('eligible_designations', emp.emp_desgntn_id_id),
                     ('eligible_categories', emp.emp_ctgry_id_id), ('branch', emp.emp_branch_id_id)):
        ids = set(getattr(p, rel).values_list('id', flat=True))
        if ids and val not in ids:
            return False
    return True


def allocation_due(emp, policy, today):
    """v1.62.3 (item 105): the next allocation of a policy only when the policy frequency has passed since the last one
    (every 2 years = 2 years after the last allocation, not every calendar year)."""
    A = M('PayrollManagement', 'AirTicketAllocation')
    last = A.objects.filter(employee=emp, policy=policy).order_by('-allocated_date', '-pk').first()
    if last is None or not last.allocated_date:
        return True
    return add_years(last.allocated_date, max(int(policy.frequency_years or 1), 1)) <= today
