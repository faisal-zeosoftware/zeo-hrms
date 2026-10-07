import re
from decimal import Decimal

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from zeo.module_helpers import is_tenant_admin, notify
from .models import (DEFAULT_ONBOARDING_TASKS, DEFAULT_VISA_STEPS, NATIONAL_STEPS, Application, ApplicationStageHistory,
                     ManpowerRequisition, OnboardingTask, RequisitionApproval, RequisitionApprovalLevel, VisaStep)


# ---------------- requisition approvals ----------------
@transaction.atomic
def submit_requisition(req, user=None):
    if req.status not in ('draft', 'rejected'):
        raise ValidationError("Only draft or rejected requisitions can be submitted.")
    req.approvals.all().delete()
    levels = list(RequisitionApprovalLevel.objects.all())
    created = []
    for lv in levels:
        needed = lv.only_above_budget is None or (req.max_salary or Decimal('0')) > lv.only_above_budget
        created.append(RequisitionApproval.objects.create(
            requisition=req, level=lv.level, role=lv.role, approver=lv.approver, status='pending' if needed else 'skipped'))
    req.submitted_on = timezone.now()
    if not any(a.status == 'pending' for a in created):
        req.status, req.approved_on = 'approved', timezone.now()
        req.save()
        return req
    req.status = 'pending'
    req.save()
    _notify_next(req)
    return req


def current_approval(req):
    return req.approvals.filter(status='pending').order_by('level').first()


def _notify_next(req):
    step = current_approval(req)
    if step and step.approver:
        notify(user=step.approver, title="Manpower requisition", notification_type='recruitment',
               message=f"{req.document_number} ({req.position_title}) is waiting for your approval as {step.role}.")


@transaction.atomic
def act_on_requisition(req, user, decision, comments=''):
    if req.status != 'pending':
        raise ValidationError("This requisition is not pending approval.")
    step = current_approval(req)
    if step is None:
        raise ValidationError("No pending approval step.")
    if step.approver_id and step.approver_id != user.id and not is_tenant_admin(user):
        raise ValidationError(f"Waiting for {step.role} ({step.approver}).")
    step.status = 'approved' if decision == 'approve' else 'rejected'
    step.comments, step.acted_by, step.acted_on = comments, user, timezone.now()
    step.save()
    if decision != 'approve':
        req.status = 'rejected'
        req.save()
        notify(user=req.requested_by, title="Requisition rejected", notification_type='recruitment',
               message=f"{req.document_number} was rejected by {step.role}: {comments}")
        return req
    if current_approval(req) is None:
        req.status, req.approved_on = 'approved', timezone.now()
        req.save()
        notify(user=req.requested_by, title="Requisition approved", notification_type='recruitment',
               message=f"{req.document_number} ({req.position_title}) is approved.")
    else:
        _notify_next(req)
    return req


def opening_from_requisition(req):
    from .models import JobOpening
    if req.status != 'approved':
        raise ValidationError("The requisition must be approved first.")
    return JobOpening.objects.create(
        requisition=req, title=req.position_title, branch=req.branch, department=req.department,
        designation=req.designation, openings=req.headcount, employment_type=req.employment_type,
        location=req.work_location, min_salary=req.min_salary, max_salary=req.max_salary,
        description=req.justification, is_emiratisation=req.is_emiratisation,
        channels=['Careers page', 'Nafis portal'] if req.is_emiratisation else ['Careers page', 'LinkedIn'],
        hiring_manager=req.requested_by)


# ---------------- pipeline ----------------
def move_stage(application, to_stage, user=None, note=''):
    valid = dict(Application.STAGES)
    if to_stage not in valid:
        raise ValidationError(f"Unknown stage '{to_stage}'.")
    if application.stage in ('hired',) and to_stage != 'hired':
        raise ValidationError("A hired candidate cannot be moved back.")
    if to_stage == 'rejected' and not note:
        raise ValidationError("Give a reason when rejecting a candidate.")
    if to_stage == 'hired' and not (hasattr(application, 'offer') and application.offer.status in ('accepted', 'joined')):
        raise ValidationError("Only candidates with an accepted offer can be marked as hired.")
    old = application.stage
    if old == to_stage:
        return application
    application.stage = to_stage
    if to_stage == 'rejected':
        application.rejection_reason = note
    application.save()
    ApplicationStageHistory.objects.create(application=application, from_stage=old, to_stage=to_stage, note=note or None, changed_by=user)
    job = application.job
    if to_stage == 'interview' and job.status == 'published':
        job.status = 'interviewing'
        job.save(update_fields=['status'])
    return application


# ---------------- offers ----------------
def _set_offer(offer, allowed, to, **extra):
    if offer.status not in allowed:
        raise ValidationError(f"Not allowed while the offer is '{offer.get_status_display()}'.")
    offer.status = to
    for k, v in extra.items():
        setattr(offer, k, v)
    offer.save()
    return offer


def approve_offer(offer, user):
    if offer.status == 'draft':
        offer.status = 'pending_approval'
    return _set_offer(offer, ('pending_approval',), 'approved', approved_by=user, approved_on=timezone.now())


def send_offer(offer, user=None):
    _set_offer(offer, ('approved',), 'sent', sent_on=timezone.now())
    move_stage(offer.application, 'offer', user, f"Offer {offer.document_number} sent")
    return offer


@transaction.atomic
def accept_offer(offer, user=None):
    _set_offer(offer, ('sent',), 'accepted', responded_on=timezone.now())
    cand = offer.application.candidate
    steps = NATIONAL_STEPS if cand.visa_status in ('uae_national', 'gcc_national') else DEFAULT_VISA_STEPS
    if not offer.visa_steps.exists():
        VisaStep.objects.bulk_create([VisaStep(offer=offer, sequence=i + 1, name=n,
                                               status='done' if i == 0 else 'pending',
                                               step_date=timezone.localdate() if i == 0 else None)
                                      for i, n in enumerate(steps)])
    if not offer.onboarding_tasks.exists():
        OnboardingTask.objects.bulk_create([OnboardingTask(offer=offer, team=t, title=title, due_date=offer.joining_date)
                                            for t, title in DEFAULT_ONBOARDING_TASKS])
    job = offer.application.job
    hired = _accepted_offer_count(job)
    if hired >= job.openings and job.status not in ('filled', 'closed'):
        job.status = 'filled'
        job.save(update_fields=['status'])
    return offer


def _accepted_offer_count(job):
    from .models import Offer
    return Offer.objects.filter(application__job=job, status__in=('accepted', 'joined')).count()


def decline_offer(offer, reason='', user=None):
    _set_offer(offer, ('sent', 'approved'), 'declined', responded_on=timezone.now(), decline_reason=reason)
    move_stage(offer.application, 'rejected', user, f"Offer declined: {reason or 'no reason given'}")
    return offer


def _next_emp_code():
    from EmpManagement.models import emp_master
    best_prefix, best_num, width = 'EMP', 0, 4
    for code in emp_master.objects.values_list('emp_code', flat=True):
        m = re.match(r'^([A-Za-z\-]*)(\d+)$', code or '')
        if m and int(m.group(2)) >= best_num:
            best_prefix, best_num, width = m.group(1) or 'EMP', int(m.group(2)), len(m.group(2))
    return f"{best_prefix}{best_num + 1:0{width}d}"


@transaction.atomic
def convert_to_employee(offer, user=None, emp_code=None, require_visa=True):
    from EmpManagement.models import emp_master
    if offer.employee_id:
        raise ValidationError("This offer is already converted to an employee.")
    if offer.status != 'accepted':
        raise ValidationError("Only accepted offers can be converted.")
    if require_visa and offer.visa_steps.filter(status__in=('pending', 'in_progress')).exists():
        raise ValidationError("Visa / onboarding steps are still open. Mark them done or not applicable first.")
    cand = offer.application.candidate
    code = (emp_code or '').strip() or _next_emp_code()
    if emp_master.objects.filter(emp_code=code).exists():
        raise ValidationError(f"Employee code {code} already exists.")
    emp = emp_master(
        emp_code=code, emp_first_name=cand.first_name, emp_last_name=cand.last_name, emp_gender=cand.gender,
        emp_date_of_birth=cand.date_of_birth, emp_personal_email=cand.email, emp_mobile_number_1=cand.phone,
        emp_joined_date=offer.joining_date, emp_nationality=cand.nationality, emp_reporting_manager=offer.reporting_manager,
        emp_branch_id=offer.branch, emp_dept_id=offer.department, emp_desgntn_id=offer.designation,
        emp_ctgry_id=offer.category, work_location=offer.branch, visa_location=offer.branch,
        emp_city=cand.current_location)
    emp.save(authenticated_user=user)
    _create_salary_lines(emp, offer, user)
    offer.employee = emp
    offer.status = 'joined'
    offer.save(update_fields=['employee', 'status'])
    move_stage(offer.application, 'hired', user, f"Joined as {emp.emp_code}")
    _assign_induction(emp)
    notify(user=offer.reporting_manager, title="New joiner", notification_type='recruitment',
           message=f"{cand.full_name} joins as {offer.position_title} on {offer.joining_date} ({emp.emp_code}).")
    return emp


def _create_salary_lines(emp, offer, user):
    try:
        from PayrollManagement.models import EmployeeSalaryStructure, SalaryComponent
    except Exception:
        return
    mapping = [('basic', offer.basic_salary), ('hra', offer.housing_allowance), ('housing_allowance', offer.housing_allowance),
               ('transport_allowance', offer.transport_allowance), ('other_allowance', offer.other_allowance)]
    done_amounts = set()
    for category, amount in mapping:
        if not amount or category in done_amounts:
            continue
        comp = (SalaryComponent.objects.filter(payroll_category=category, branch=offer.branch).first()
                or SalaryComponent.objects.filter(payroll_category=category).first())
        if not comp:
            continue
        EmployeeSalaryStructure.objects.update_or_create(
            employee=emp, component=comp, defaults={'amount': amount, 'is_active': True, 'valid_from': offer.joining_date})
        done_amounts.add(category)
        if category == 'hra':
            done_amounts.add('housing_allowance')


def _assign_induction(emp):
    if not apps.is_installed('LearningManagement'):
        return
    Course = apps.get_model('LearningManagement', 'Course')
    TrainingNeed = apps.get_model('LearningManagement', 'TrainingNeed')
    for course in Course.objects.filter(is_active=True, auto_assign_new_joiners=True):
        TrainingNeed.objects.get_or_create(employee=emp, course=course, source='new_joiner',
                                           defaults={'need': f"Induction: {course.title}", 'priority': 'mandatory'})
