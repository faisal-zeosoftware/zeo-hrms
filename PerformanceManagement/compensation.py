"""v1.67.26 (pending item 1.62.12, item 234) – compensation cycle with increment letters.

Performance › Compensation cycles (HR with the right to change appraisal outcomes; approval by a company administrator or a user
with the right to approve outcomes – never the preparer):
  1. New cycle: name, effective date, budget % of the basic salaries, the employees (branches, departments, joined before), and an
     appraisal cycle when the increments follow its final ratings and increment bands.
  2. Fill: one line per employee with the current basic and gross salary, the rating and the proposed % (the band of the rating;
     0 % without an appraisal cycle). HR changes the final % (or the new basic) line by line and gives a reason. The page shows the
     cost a month and against the budget.
  3. Send for approval → Approve (another person) or Return.
  4. Apply: each employee's basic salary line takes the new amount from the effective date (the salary history keeps the old one –
     the payroll of earlier months keeps the old basic, as the appraisal outcomes do since 1.62.2).
  5. Increment letters: the "Increment letter" template (HR letters › Letter templates) for every employee whose salary changed, as
     a mail merge – one PDF each, kept with the employee's letters, optionally e-mailed, all in one ZIP.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from .models import CompensationCycle, CompensationLine

Q2 = Decimal('0.01')


class CompError(Exception):
    def __init__(self, msg, status=400, field=None):
        super().__init__(msg)
        self.status, self.field = status, field


def _tr(s, *a):
    try:
        from Translations.i18n import tr
        return tr(s, *a)
    except Exception:   # noqa: BLE001
        return s.format(*a)


def _figures(e):
    try:
        from SelfService import profile as P
        f = P.salary_figures(e)
        return Decimal(str(f['basic'] or 0)), Decimal(str(f['gross'] or 0))
    except Exception:   # noqa: BLE001
        return Decimal(0), Decimal(0)


def _employees(c):
    from EmpManagement.models import emp_master
    qs = emp_master.objects.filter(is_active=True)
    if c.branches.exists():
        qs = qs.filter(emp_branch_id__in=c.branches.all())
    if c.departments.exists():
        qs = qs.filter(emp_dept_id__in=c.departments.all())
    if c.joined_before:
        qs = qs.filter(emp_joined_date__lt=c.joined_before)
    return qs.order_by('emp_code')


@transaction.atomic
def fill(c):
    """Make (or refresh) the lines: current salary, rating and proposed %. Lines HR already changed keep their final %."""
    if c.status != 'draft':
        raise CompError(_tr('Only a draft cycle can be filled.'), 409)
    ratings, bands = {}, {}
    if c.appraisal_cycle_id:
        for s in c.appraisal_cycle.goal_sheets.filter(final_rating__isnull=False):
            ratings[s.employee_id] = s.final_rating
        bands = {b.rating: b for b in c.appraisal_cycle.increment_bands.all()}
    keep = {ln.employee_id: ln for ln in c.lines.all()}
    n = 0
    seen = set()
    for e in _employees(c):
        basic, gross = _figures(e)
        if basic <= 0:
            continue
        r = ratings.get(e.pk)
        band = bands.get(r)
        pct = band.increment_percent if band else Decimal(0)
        bonus = (basic * (band.bonus_months if band else Decimal(0))).quantize(Q2, ROUND_HALF_UP)
        ln = keep.get(e.pk) or CompensationLine(cycle=c, employee=e, final_percent=pct)
        changed_by_hr = ln.pk and ln.final_percent != ln.proposed_percent
        ln.rating, ln.current_basic, ln.current_gross, ln.proposed_percent, ln.bonus_amount = r, basic, gross, pct, bonus
        if not changed_by_hr:
            ln.final_percent = pct
        ln.new_basic = (basic * (1 + ln.final_percent / 100)).quantize(Q2, ROUND_HALF_UP)
        ln.save()
        seen.add(e.pk)
        n += 1
    c.lines.exclude(employee_id__in=seen).delete()
    return n


def set_line(c, line_id, data):
    if c.status != 'draft':
        raise CompError(_tr('The cycle is sent for approval – return it to change a line.'), 409)
    ln = c.lines.filter(pk=line_id).first()
    if ln is None:
        raise CompError(_tr('Not found.'), 404)
    try:
        if data.get('new_basic') not in (None, ''):
            nb = Decimal(str(data['new_basic'])).quantize(Q2, ROUND_HALF_UP)
            if nb < ln.current_basic:
                raise CompError(_tr('The new basic is lower than today\'s ({0}) – a salary decrease is not done in a compensation cycle.', ln.current_basic), 400, 'new_basic')
            ln.new_basic = nb
            ln.final_percent = ((nb / ln.current_basic - 1) * 100).quantize(Q2, ROUND_HALF_UP) if ln.current_basic else Decimal(0)
        elif data.get('final_percent') not in (None, ''):
            p = Decimal(str(data['final_percent'])).quantize(Q2, ROUND_HALF_UP)
            if p < 0 or p > 100:
                raise CompError(_tr('Increment %: from 0 to 100.'), 400, 'final_percent')
            ln.final_percent = p
            ln.new_basic = (ln.current_basic * (1 + p / 100)).quantize(Q2, ROUND_HALF_UP)
        if 'bonus_amount' in data and data['bonus_amount'] not in (None, ''):
            b = Decimal(str(data['bonus_amount'])).quantize(Q2, ROUND_HALF_UP)
            if b < 0:
                raise CompError(_tr('Bonus: 0 or more.'), 400, 'bonus_amount')
            ln.bonus_amount = b
    except (ArithmeticError, ValueError):
        raise CompError(_tr('Enter a number.'), 400)
    if 'reason' in data:
        ln.reason = str(data['reason'] or '')[:255]
    if ln.final_percent != ln.proposed_percent and not ln.reason.strip():
        raise CompError(_tr('Give a reason when the increment differs from the proposal ({0} %).', ln.proposed_percent), 400, 'reason')
    ln.save()
    return ln


def totals(c):
    lines = list(c.lines.all())
    base = sum((ln.current_basic for ln in lines), Decimal(0))
    cost = sum((ln.new_basic - ln.current_basic for ln in lines), Decimal(0))
    budget = (base * c.budget_percent / 100).quantize(Q2, ROUND_HALF_UP)
    return {'employees': len(lines), 'raised': sum(1 for ln in lines if ln.new_basic > ln.current_basic), 'basic_total': str(base.quantize(Q2)),
            'cost_month': str(cost.quantize(Q2)), 'cost_year': str((cost * 12).quantize(Q2)), 'budget_month': str(budget),
            'used_percent': str((cost * 100 / base).quantize(Q2)) if base else '0.00', 'over_budget': cost > budget,
            'bonus_total': str(sum((ln.bonus_amount for ln in lines), Decimal(0)).quantize(Q2))}


def submit(c, user):
    if c.status != 'draft':
        raise CompError(_tr('Only a draft cycle is sent for approval.'), 409)
    if not c.lines.exists():
        raise CompError(_tr('Fill the cycle first – it has no employees.'))
    c.status, c.prepared_by = 'review', user
    c.save(update_fields=['status', 'prepared_by'])
    return c


def decide(c, user, approve, note=''):
    if c.status != 'review':
        raise CompError(_tr('The cycle is not waiting for approval.'), 409)
    if c.prepared_by_id and c.prepared_by_id == user.pk:
        raise CompError(_tr('The person who prepared the cycle cannot approve it – another approver decides (segregation of duties).'), 403)
    if approve:
        c.status, c.approved_by, c.approved_at = 'approved', user, timezone.now()
    else:
        if not (note or '').strip():
            raise CompError(_tr('Say why the cycle is returned.'), 400, 'note')
        c.status = 'draft'
        c.notes = ((c.notes + '\n') if c.notes else '') + _tr('Returned by {0}: {1}', user.get_full_name() or user.username, note.strip())
    c.save()
    return c


@transaction.atomic
def apply(c, user):
    """The new basic of every raised employee from the effective date (salary history keeps the old amount)."""
    if c.status != 'approved':
        raise CompError(_tr('Approve the cycle before applying it.'), 409)
    from .services import _basic_line
    done, missing = 0, []
    for ln in c.lines.select_related('employee').filter(applied=False):
        if ln.new_basic <= ln.current_basic:
            continue
        line = _basic_line(ln.employee)
        if line is None:
            missing.append(ln.employee.emp_code)
            continue
        line._revised_by = user
        line._remarks = f'{c.name}: +{ln.final_percent}%' + (f' – {ln.reason}' if ln.reason else '')
        line._effective_period = c.effective_date.strftime('%B%Y')
        line.amount = ln.new_basic
        line.valid_from = c.effective_date
        line.save()
        ln.applied = True
        ln.save(update_fields=['applied'])
        done += 1
    if missing:
        raise CompError(_tr('No basic salary line for: {0} – add it on Employee salary first.', ', '.join(missing)), 400)
    c.status, c.applied_at = 'applied', timezone.now()
    c.save(update_fields=['status', 'applied_at'])
    return done


def letters(c, user, language='en', email=False):
    """Increment letters for the raised employees (mail merge with their figures)."""
    if c.status not in ('approved', 'applied'):
        raise CompError(_tr('Letters are made once the cycle is approved.'), 409)
    from SelfService import mailmerge as MM
    lines = [ln for ln in c.lines.select_related('employee', 'employee__emp_branch_id') if ln.new_basic > ln.current_basic]
    if not lines:
        raise CompError(_tr('Nobody in the cycle has an increment.'))
    cur = 'AED'
    try:
        from SelfService.letters import company
        co = company()
        cur = (getattr(co.currency, 'currency_code', None) or 'AED') if getattr(co, 'currency_id', None) else 'AED'
    except Exception:   # noqa: BLE001
        pass
    extras = {}
    for ln in lines:
        inc = ln.new_basic - ln.current_basic
        extras[ln.employee_id] = {'old_basic': f'{ln.current_basic:,.2f}', 'new_basic': f'{ln.new_basic:,.2f}', 'increment_percent': f'{ln.final_percent.normalize():f}',
                                  'increment_amount': f'{inc:,.2f}', 'new_gross': f'{(ln.current_gross + inc):,.2f}', 'currency': cur,
                                  'effective_date': c.effective_date.strftime('%d/%m/%Y'), 'bonus_amount': f'{ln.bonus_amount:,.2f}',
                                  'cycle_name': c.name, 'rating': str(ln.rating or '')}
    out, failed, sent = MM.run('increment', language, [ln.employee for ln in lines], user, '', extras, email)
    by_emp = {lr.employee_id: lr.pk for lr in out}
    for ln in lines:
        if ln.employee_id in by_emp:
            CompensationLine.objects.filter(pk=ln.pk).update(letter_id=by_emp[ln.employee_id])
    c.letters_at = timezone.now()
    c.save(update_fields=['letters_at'])
    return out, failed, sent


def cycle_json(c, lines=True):
    out = {'id': c.pk, 'name': c.name, 'effective_date': c.effective_date, 'budget_percent': str(c.budget_percent), 'status': c.status,
           'status_label': dict(CompensationCycle.STATUS).get(c.status), 'appraisal_cycle': c.appraisal_cycle_id,
           'appraisal_cycle_name': c.appraisal_cycle.name if c.appraisal_cycle_id else '', 'branches': list(c.branches.values_list('pk', flat=True)),
           'departments': list(c.departments.values_list('pk', flat=True)), 'joined_before': c.joined_before, 'notes': c.notes,
           'prepared_by': (c.prepared_by.get_full_name() or c.prepared_by.username) if c.prepared_by_id else '',
           'approved_by': (c.approved_by.get_full_name() or c.approved_by.username) if c.approved_by_id else '', 'approved_at': c.approved_at,
           'applied_at': c.applied_at, 'letters_at': c.letters_at, 'totals': totals(c)}
    if lines:
        out['lines'] = [{'id': ln.pk, 'employee_id': ln.employee_id, 'code': ln.employee.emp_code,
                         'name': ' '.join(x for x in (ln.employee.emp_first_name, ln.employee.emp_last_name) if x),
                         'rating': ln.rating, 'current_basic': str(ln.current_basic), 'current_gross': str(ln.current_gross),
                         'proposed_percent': str(ln.proposed_percent), 'final_percent': str(ln.final_percent), 'new_basic': str(ln.new_basic),
                         'bonus_amount': str(ln.bonus_amount), 'reason': ln.reason, 'applied': ln.applied, 'letter_id': ln.letter_id}
                        for ln in c.lines.select_related('employee')]
    return out
