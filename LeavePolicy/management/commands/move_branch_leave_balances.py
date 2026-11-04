"""v1.62.4 (item 73 = pending item 20) – leave days of employees that sit on another branch's copy of a leave type are moved to
the copy of the employee's own branch, with two ledger lines (− on the old type, + on the employee's type).

Before 1.62.1 the monthly / yearly accrual credited the head-office leave types (Annual Leave AL-GDI of the head office …) to
the Dubai staff: they cannot request those types, but the days were counted in the balance grid, the leave liability and the
end of service. Only active employees are moved (a leaver's final settlement is already made on the old figures – listed, not
moved). Dry run by default; --apply writes. --report <file.json> keeps what was found / moved.

  manage.py move_branch_leave_balances --schema gdis                 # dry run
  manage.py move_branch_leave_balances --schema gdis --apply
"""
import json
from datetime import date

from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import transaction
from django_tenants.utils import schema_context

M = apps.get_model


def plan(on=None):
    """[{employee, code, from_type, to_type, days, action}] – action 'move', or why not ('leaver', 'no copy', 'pending requests')."""
    from LeavePolicy import engine
    engine._run.types = None
    out = []
    B = M('calendars', 'emp_leave_balance')
    LR = M('calendars', 'employee_leave_request')
    names = {i: (n, c, b) for i, n, c, b in M('calendars', 'leave_type').objects.values_list('id', 'name', 'code', 'branch_id')}
    brn = dict(M('OrganisationManager', 'brnch_mstr').objects.values_list('id', 'branch_name'))
    qs = B.objects.filter(leave_type__branch_id__isnull=False, employee__emp_branch_id__isnull=False).exclude(balance=0).exclude(balance__isnull=True) \
        .select_related('employee').order_by('employee_id', 'leave_type_id')
    for b in qs:
        e = b.employee
        if names[b.leave_type_id][2] == e.emp_branch_id_id:
            continue
        tw = engine.twin_type(b.leave_type_id, e.emp_branch_id_id)
        row = {'employee_id': e.pk, 'code': e.emp_code, 'name': e.emp_first_name, 'active': e.is_active,
               'from_type_id': b.leave_type_id, 'from_type': f'{names[b.leave_type_id][0]} ({names[b.leave_type_id][1]}, {brn.get(names[b.leave_type_id][2], "")})',
               'to_type_id': tw, 'to_type': f'{names[tw][0]} ({names[tw][1]}, {brn.get(names[tw][2], "")})' if tw else '',
               'days': round(float(b.balance or 0), 2)}
        if not e.is_active:
            row['action'] = 'not moved – leaver (final settlement made on these figures)'
        elif tw is None:
            row['action'] = "not moved – the employee's branch has no copy of this leave type"
        elif LR.objects.filter(employee=e, leave_type_id=b.leave_type_id, status='pending').exists():
            row['action'] = 'not moved – a request on this type waits for approval'
        else:
            row['action'] = 'move'
        out.append(row)
    return out


@transaction.atomic
def apply(rows, on=None, user_id=None):
    from LeavePolicy import engine
    on = on or date.today()
    done = []
    for r in rows:
        if r['action'] != 'move':
            continue
        b = engine.balance_row(r['employee_id'], r['from_type_id'])
        days = round(float(b.balance or 0), 2)     # read again inside the transaction
        if not days:
            continue
        # v1.62.4 (item 73): the same two lines as an employee transfer (HRActions/transfer.py)
        engine.post(r['employee_id'], r['from_type_id'], on, 'adjustment', -days,
                    note=f"Moved to {r['to_type']} (leave type of the employee's branch)", user_id=user_id, move_balance=True)
        engine.post(r['employee_id'], r['to_type_id'], on, 'adjustment', days,
                    note=f"From {r['from_type']} (leave type of the employee's branch)", user_id=user_id, move_balance=True)
        done.append({**r, 'days': days, 'moved_on': on.isoformat()})
    return done


class Command(BaseCommand):
    help = "Move leave days that sit on another branch's leave type to the employee's own branch copy (dry run unless --apply)."

    def add_arguments(self, parser):
        parser.add_argument('--schema', required=True)
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--report')

    def handle(self, *args, **o):
        with schema_context(o['schema']):
            rows = plan()
            moved = apply(rows) if o['apply'] else []
            from LeavePolicy.engine import ledger_mismatches
            bad = ledger_mismatches()
        for r in rows:
            self.stdout.write(f"{r['code']} {r['name']}: {r['days']:g} days {r['from_type']} → {r['to_type'] or '-'}: {r['action']}")
        tot = {}
        for r in (moved if o['apply'] else [x for x in rows if x['action'] == 'move']):
            tot[r['from_type']] = round(tot.get(r['from_type'], 0) + r['days'], 2)
        self.stdout.write(('MOVED ' if o['apply'] else 'DRY RUN – would move ') + f"{len(moved) if o['apply'] else sum(1 for x in rows if x['action'] == 'move')} balance(s): {tot}")
        self.stdout.write(f'ledger mismatches: {len(bad)}')
        if o.get('report'):
            json.dump({'schema': o['schema'], 'applied': o['apply'], 'rows': rows, 'moved': moved, 'totals': tot, 'mismatches': bad},
                      open(o['report'], 'w'), indent=1, default=str)
