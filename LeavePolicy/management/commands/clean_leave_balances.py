"""v1.67.5 (bugs) L2 / L5 – clean up leave balances that show a leave type twice for one employee, and the empty balances
on a type for the other gender (maternity leave for a man).

Why they exist: before 1.67.5 a new employee got a balance row for EVERY leave type of the company – also the copies of the
other branches (Annual Leave of the head office and Annual Leave of the Dubai branch …), and for maternity leave whatever the
gender. Leave balance then listed Annual, Sick, Casual, Compensatory, Maternity, Parental … twice. 1.67.5 makes no such rows
(calendars/utils.is_leave_type_applicable) and does not show the empty ones (calendars/utils.BalanceView).

What this command does, per employee:
1. a balance on another branch's copy of a type the employee's branch has (or on a second type of the same name for the same
   branch, made twice) is MERGED into the employee's own type: the days are moved with two ledger lines (− on the old type,
   + on the kept type, as an employee transfer does), so the history stays and the balance equals its ledger;
2. the balance row that is then empty is removed (its ledger lines are kept, they add up to 0);
3. an EMPTY balance on a type for the other gender is removed; one that holds days is only listed (HR decides).
Not moved: leavers (their final settlement used these figures) and balances with a pending request – they are listed.
Leave types themselves are never deleted; types made twice are listed so HR can delete the unused one.

Dry run by default: the work is done inside a transaction that is rolled back, so the figures are exactly what --apply does.

  manage.py clean_leave_balances --schema gdis               # dry run, nothing kept
  manage.py clean_leave_balances --schema gdis --apply       # writes
  manage.py clean_leave_balances --schema gdis --report /tmp/leave_clean.json
"""
import json
from datetime import date

from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum
from django_tenants.utils import schema_context

M = apps.get_model


class _DryRun(Exception):
    pass


def target_type(emp, lt, types):
    """The type the employee should use instead of `lt`, or None when `lt` is right for the employee."""
    name = (lt.name or '').strip().lower()
    b = emp.emp_branch_id_id
    if lt.branch_id and b and lt.branch_id != b:   # another branch's copy – the employee's branch copy (same code, else same name)
        own = [t for t in types if t.branch_id == b and (t.code == lt.code or (t.name or '').strip().lower() == name)]
        if own:
            return sorted(own, key=lambda t: (t.code != lt.code, t.pk))[0]
    same = [t for t in types if t.branch_id == lt.branch_id and (t.name or '').strip().lower() == name and t.pk < lt.pk]
    return sorted(same, key=lambda t: t.pk)[0] if same else None   # a type made twice: the oldest


def work(on=None, user_id=None):
    from calendars.utils import leave_gender
    from LeavePolicy import engine
    on = on or date.today()
    engine._run.types = None
    B, LR = M('calendars', 'emp_leave_balance'), M('calendars', 'employee_leave_request')
    _, _, _, Ledger = engine._models()
    types = list(M('calendars', 'leave_type').objects.all().order_by('pk'))
    by_id = {t.pk: t for t in types}
    genders = {t.pk: leave_gender(t) for t in types}
    out = {'merged': [], 'removed_empty': [], 'removed_gender': [], 'kept': [], 'duplicate_types': []}
    seen = {}
    for t in types:   # types made twice (same name, same branch scope)
        k = (t.branch_id, (t.name or '').strip().lower())
        if k in seen:
            out['duplicate_types'].append({'id': t.pk, 'name': t.name, 'code': t.code, 'branch_id': t.branch_id, 'first_id': seen[k],
                                           'requests': LR.objects.filter(leave_type_id=t.pk).count()})
        else:
            seen[k] = t.pk
    rows = list(B.objects.select_related('employee').order_by('employee_id', 'leave_type_id'))
    for b in rows:
        e, lt = b.employee, by_id.get(b.leave_type_id)
        if e is None or lt is None:
            continue
        base = {'employee_id': e.pk, 'code': e.emp_code, 'leave_type_id': lt.pk, 'leave_type': f'{lt.name} ({lt.code})'}
        days = round(float(b.balance or 0), 2)
        tgt = target_type(e, lt, types)
        if tgt is not None:
            if LR.objects.filter(employee=e, leave_type=lt, status='pending').exists():
                out['kept'].append({**base, 'days': days, 'why': 'a request on this type waits for approval'})
                continue
            if days and not e.is_active:
                out['kept'].append({**base, 'days': days, 'why': 'leaver – the final settlement was made on these figures'})
                continue
            if days:
                note_to = f'{tgt.name} ({tgt.code})'
                engine.post(e.pk, lt.pk, on, 'adjustment', -days, note=f'Merged into {note_to} (clean_leave_balances, 1.67.5)'[:255],
                            user_id=user_id, move_balance=True)
                engine.post(e.pk, tgt.pk, on, 'adjustment', days, note=f'From {base["leave_type"]} (clean_leave_balances, 1.67.5)'[:255],
                            user_id=user_id, move_balance=True)
                out['merged'].append({**base, 'days': days, 'into_id': tgt.pk, 'into': note_to})
            b.refresh_from_db()
            led = round(float(Ledger.objects.filter(employee_id=e.pk, leave_type_id=lt.pk).aggregate(s=Sum('days'))['s'] or 0), 2)
            if not float(b.balance or 0) and abs(led) < 0.005:
                b.delete()
                out['removed_empty'].append({**base, 'instead': f'{tgt.name} ({tgt.code})'})
            else:
                out['kept'].append({**base, 'days': round(float(b.balance or 0), 2), 'why': 'the balance and its ledger do not add up to 0'})
            continue
        g = genders.get(lt.pk)
        if g and (e.emp_gender or '').upper()[:1] != g:
            if days:
                out['kept'].append({**base, 'days': days, 'why': f'holds days on a type for {"women" if g == "F" else "men"} only – HR decides'})
            else:
                b.delete()
                out['removed_gender'].append({**base, 'gender': e.emp_gender or ''})
    out['mismatches'] = len(engine.ledger_mismatches())
    return out


def run(apply=False, user_id=None):
    if apply:
        with transaction.atomic():
            return work(user_id=user_id)
    res = None
    try:
        with transaction.atomic():
            res = work(user_id=user_id)
            raise _DryRun()
    except _DryRun:
        pass
    return res


class Command(BaseCommand):
    help = 'Merge duplicate leave balances (other branch copies / types made twice) and remove empty wrong-gender balances. Dry run unless --apply.'

    def add_arguments(self, parser):
        parser.add_argument('--schema', required=True)
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--report')

    def handle(self, *args, **o):
        with schema_context(o['schema']):
            res = run(apply=o['apply'])
        w = self.stdout.write
        w(('APPLIED' if o['apply'] else 'DRY RUN – nothing is kept') + f" ({o['schema']})")
        for r in res['merged']:
            w(f"  merge  {r['code']}: {r['days']:g} days {r['leave_type']} → {r['into']}")
        for r in res['removed_empty']:
            w(f"  remove {r['code']}: empty {r['leave_type']} (uses {r['instead']})")
        for r in res['removed_gender']:
            w(f"  remove {r['code']}: empty {r['leave_type']} (other gender)")
        for r in res['kept']:
            w(f"  keep   {r['code']}: {r['leave_type']} {r.get('days', 0):g} days – {r['why']}")
        for r in res['duplicate_types']:
            w(f"  type made twice: {r['name']} ({r['code']}, id {r['id']}, first id {r['first_id']}, {r['requests']} request(s)) – delete it in Leave types when unused")
        w(f"merged {len(res['merged'])} · removed {len(res['removed_empty'])} duplicate + {len(res['removed_gender'])} other-gender empty rows · "
          f"kept {len(res['kept'])} · types made twice {len(res['duplicate_types'])} · ledger mismatches {res['mismatches']}")
        if o.get('report'):
            json.dump({'schema': o['schema'], 'applied': o['apply'], **res}, open(o['report'], 'w'), indent=1, default=str)
