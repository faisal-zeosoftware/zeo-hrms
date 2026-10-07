"""Business logic for the appraisal cycle (kept out of views so it can be tested and reused)."""
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from zeo.module_helpers import notify
from .models import (DEFAULT_BANDS, AppraisalOutcome, CalibrationLog, GoalSheet, IncrementBand,
                     PerformanceImprovementPlan)


@transaction.atomic
def launch_cycle(cycle, user=None):
    if cycle.status != 'draft':
        raise ValidationError("Only a draft cycle can be launched.")
    if not cycle.increment_bands.exists():
        IncrementBand.objects.bulk_create([
            IncrementBand(cycle=cycle, rating=r, increment_percent=p, bonus_months=b, guideline_percent=g)
            for r, p, b, g in DEFAULT_BANDS])
    created = 0
    for emp in cycle.eligible_employees():
        _, was_created = GoalSheet.objects.get_or_create(
            cycle=cycle, employee=emp, defaults={'manager': emp.emp_reporting_manager})
        created += int(was_created)
    cycle.status = 'active'
    cycle.launched_on = timezone.now()
    cycle.save(update_fields=['status', 'launched_on'])
    return created


def _transition(sheet, allowed_from, to_status):
    if sheet.status not in allowed_from:
        raise ValidationError(f"Not allowed while the sheet is '{sheet.get_status_display()}'.")
    sheet.status = to_status


def submit_goals(sheet):
    _transition(sheet, ('draft',), 'submitted')
    sheet.validate_goals()
    sheet.return_reason = None
    sheet.save()
    notify(user=sheet.manager, title="Goals submitted",
           message=f"{sheet.employee} submitted goals for {sheet.cycle}.", notification_type='performance')


def approve_goals(sheet):
    _transition(sheet, ('submitted',), 'approved')
    sheet.validate_goals()
    sheet.save()
    notify(employee=sheet.employee, title="Goals approved",
           message=f"Your goals for {sheet.cycle} were approved.", notification_type='performance')


def return_goals(sheet, reason):
    _transition(sheet, ('submitted', 'self_submitted'), 'draft' if sheet.status == 'submitted' else 'approved')
    sheet.return_reason = reason
    sheet.save()
    notify(employee=sheet.employee, title="Sent back",
           message=f"Your {sheet.cycle} form was sent back: {reason}", notification_type='performance')


def submit_self(sheet):
    _transition(sheet, ('approved',), 'self_submitted')
    missing = sheet.goals.filter(self_rating__isnull=True).count()
    if missing:
        raise ValidationError(f"Rate every goal before submitting ({missing} not rated).")
    sheet.compute_scores()
    sheet.save()
    notify(user=sheet.manager, title="Self appraisal submitted",
           message=f"{sheet.employee} submitted the self appraisal for {sheet.cycle}.", notification_type='performance')


def submit_review(sheet, user=None):
    _transition(sheet, ('self_submitted',), 'reviewed')
    missing = sheet.goals.filter(manager_rating__isnull=True).count()
    if missing:
        raise ValidationError(f"Rate every goal before submitting the review ({missing} not rated).")
    sheet.compute_scores()
    sheet.final_rating = sheet.proposed_rating
    sheet.save()
    _create_training_need(sheet)
    notify(employee=sheet.employee, title="Manager review completed",
           message=f"Your manager completed the {sheet.cycle} review.", notification_type='performance')


def _create_training_need(sheet):
    """Development needs flow to Learning Management when that app is installed."""
    if not sheet.development_needs or not apps.is_installed('LearningManagement'):
        return
    TrainingNeed = apps.get_model('LearningManagement', 'TrainingNeed')
    Course = apps.get_model('LearningManagement', 'Course')
    text = sheet.development_needs.strip()
    # suggest a catalogue course when the need names one (e.g. "UAE Corporate Tax Essentials")
    course = next((c for c in Course.objects.filter(is_active=True).order_by('-id')
                   if c.title.lower() in text.lower() or text.lower() in c.title.lower()), None)
    TrainingNeed.objects.get_or_create(
        employee=sheet.employee, source='appraisal', source_reference=f"{sheet.cycle.name} (sheet {sheet.id})",
        defaults={'need': text[:250], 'course': course,
                  'priority': 'high' if (sheet.final_rating or 3) <= 2 else 'medium'})


@transaction.atomic
def calibrate(sheet, new_rating, reason, user=None):
    if sheet.status not in ('reviewed', 'calibrated'):
        raise ValidationError("Only reviewed sheets can be calibrated.")
    if sheet.cycle.status == 'closed':
        raise ValidationError("The cycle is closed.")
    new_rating = int(new_rating)
    if new_rating not in range(1, 6):
        raise ValidationError("Rating must be 1 to 5.")
    if not reason:
        raise ValidationError("A reason is required for every calibration change.")
    CalibrationLog.objects.create(sheet=sheet, old_rating=sheet.final_rating, new_rating=new_rating, reason=reason, changed_by=user)
    sheet.final_rating = new_rating
    sheet.save(update_fields=['final_rating', 'updated_at'])


def distribution(cycle):
    sheets = cycle.goal_sheets.exclude(final_rating__isnull=True).select_related('employee__emp_dept_id')
    total = sheets.count()
    bands = {b.rating: b for b in cycle.increment_bands.all()}
    dist = []
    for r in range(5, 0, -1):
        n = sheets.filter(final_rating=r).count()
        dist.append({'rating': r, 'count': n, 'percent': round(n * 100 / total, 1) if total else 0,
                     'guideline_percent': float(bands[r].guideline_percent) if r in bands else None})
    by_dept = {}
    for s in sheets:
        d = str(s.employee.emp_dept_id) if s.employee.emp_dept_id else 'Unassigned'
        row = by_dept.setdefault(d, {'department': d, 'count': 0, 'sum': 0, **{str(i): 0 for i in range(1, 6)}})
        row['count'] += 1
        row['sum'] += s.final_rating
        row[str(s.final_rating)] += 1
    depts = []
    for row in by_dept.values():
        row['average'] = round(row.pop('sum') / row['count'], 2)
        depts.append(row)
    status_counts = {k: cycle.goal_sheets.filter(status=k).count() for k, _ in GoalSheet.STATUS_CHOICES}
    return {'cycle': cycle.name, 'rated': total, 'sheets': cycle.goal_sheets.count(), 'distribution': dist,
            'departments': sorted(depts, key=lambda x: x['department']), 'status_counts': status_counts}


@transaction.atomic
def lock_ratings(cycle):
    pending = cycle.goal_sheets.exclude(status__in=('reviewed', 'calibrated', 'acknowledged')).count()
    if pending:
        raise ValidationError(f"{pending} goal sheet(s) are not reviewed yet.")
    cycle.goal_sheets.filter(status='reviewed').update(status='calibrated')
    cycle.status = 'calibration'
    cycle.save(update_fields=['status'])


def _basic_line(employee):
    from PayrollManagement.models import EmployeeSalaryStructure
    return (EmployeeSalaryStructure.objects.filter(employee=employee, is_active=True, component__payroll_category='basic')
            .select_related('component').first())


@transaction.atomic
def generate_outcomes(cycle):
    if cycle.status not in ('calibration', 'closed'):
        raise ValidationError("Lock the ratings (calibration) before generating outcomes.")
    bands = {b.rating: b for b in cycle.increment_bands.all()}
    made = 0
    for sheet in cycle.goal_sheets.filter(final_rating__isnull=False).select_related('employee'):
        band = bands.get(sheet.final_rating)
        line = _basic_line(sheet.employee)
        basic = line.amount if line and line.amount else Decimal('0')
        outcome, created = AppraisalOutcome.objects.get_or_create(sheet=sheet, defaults={'final_rating': sheet.final_rating})
        if outcome.status == 'pushed':
            continue
        outcome.final_rating = sheet.final_rating
        outcome.current_basic = basic
        outcome.increment_percent = band.increment_percent if band else Decimal('0')
        outcome.bonus_amount = (basic * (band.bonus_months if band else Decimal('0'))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        outcome.effective_date = cycle.increment_effective_date
        outcome.recalc()
        outcome.save()
        made += int(created)
        if sheet.final_rating <= 2 and not sheet.pips.exists():
            start = timezone.localdate()
            PerformanceImprovementPlan.objects.create(
                employee=sheet.employee, sheet=sheet, start_date=start, end_date=start + timedelta(days=90),
                objectives=f"Improve performance after {cycle.name} rating {sheet.final_rating}. Agree measurable targets with the manager.",
                owner=sheet.manager)
    return made


@transaction.atomic
def push_to_payroll(outcome, user=None):
    """Writes the new basic into EmployeeSalaryStructure; the existing pre_save
    signal records it in SalaryRevisionHistory."""
    if outcome.status == 'pushed':
        raise ValidationError("Already pushed to payroll.")
    if outcome.status != 'approved':
        raise ValidationError("Approve the outcome before pushing it to payroll.")
    line = _basic_line(outcome.sheet.employee)
    if outcome.increment_percent and outcome.increment_percent > 0:
        if not line:
            raise ValidationError("Employee has no active Basic salary component.")
        line._revised_by = user
        line._remarks = f"Appraisal {outcome.sheet.cycle.name}: rating {outcome.final_rating}, +{outcome.increment_percent}%"
        eff = outcome.effective_date or timezone.localdate()
        line._effective_period = eff.strftime('%B%Y')
        line.amount = outcome.new_basic
        line.save()
    outcome.status = 'pushed'
    outcome.pushed_on = timezone.now()
    outcome.pushed_by = user
    outcome.save()
    notify(employee=outcome.sheet.employee, title="Appraisal outcome",
           message=f"Your {outcome.sheet.cycle.name} outcome is final: rating {outcome.final_rating}, increment {outcome.increment_percent}%.",
           notification_type='performance')
