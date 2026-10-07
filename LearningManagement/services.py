from dateutil.relativedelta import relativedelta
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Avg, Count, Q
from django.utils import timezone

from zeo.module_helpers import notify
from .models import Certificate, Course, Nomination, ParticipantResult, TrainingBond, TrainingNeed, TrainingSession


def leave_clash(employee, session):
    if not apps.is_installed('calendars'):
        return None
    try:
        Leave = apps.get_model('calendars', 'employee_leave_request')
    except LookupError:
        return None
    clash = Leave.objects.filter(employee=employee, status='approved', start_date__lte=session.end_date,
                                 end_date__gte=session.start_date).first()
    return clash


@transaction.atomic
def nominate(session, employee, nominated_by='manager', reason='', need=None, user=None, allow_clash=False, is_ld=False):
    if session.status not in ('published', 'draft'):
        raise ValidationError("Nominations are closed for this session.")
    # L&D / HR may add late or retroactive nominations (e.g. recording attendance after a session)
    if session.nomination_deadline and timezone.localdate() > session.nomination_deadline and not is_ld:
        raise ValidationError("The nomination deadline has passed.")
    if Nomination.objects.filter(session=session, employee=employee).exists():
        raise ValidationError(f"{employee} is already nominated for this session.")
    clash = leave_clash(employee, session)
    if clash and not allow_clash:
        raise ValidationError(f"{employee} has approved leave from {clash.start_date} to {clash.end_date}.")
    nom = Nomination.objects.create(
        session=session, employee=employee, nominated_by=nominated_by, reason=reason, need=need, created_by=user,
        bond_required=session.bond_required,
        manager_status='approved' if nominated_by in ('manager', 'hr', 'auto') else 'pending')
    if need:
        need.status = 'nominated'
        need.save(update_fields=['status'])
    if nom.manager_status == 'pending':
        notify(user=employee.emp_reporting_manager, title="Training nomination", notification_type='learning',
               message=f"{employee} asked to attend {session.course.title} ({session.code}).")
    return nom


def _try_confirm(nom):
    if nom.manager_status != 'approved' or nom.ld_status != 'approved':
        return
    if nom.bond_required and not nom.bond_accepted_on:
        nom.seat_status = 'pending'
        return
    taken = nom.session.nominations.filter(seat_status='confirmed').exclude(pk=nom.pk).count()
    nom.seat_status = 'confirmed' if taken < nom.session.seats else 'waitlist'


@transaction.atomic
def approve(nom, level, user=None):
    if level == 'manager':
        nom.manager_status = 'approved'
    elif level == 'ld':
        if nom.manager_status != 'approved':
            raise ValidationError("Manager approval is needed first.")
        nom.ld_status = 'approved'
    else:
        raise ValidationError("level must be 'manager' or 'ld'.")
    _try_confirm(nom)
    nom.save()
    if nom.bond_required and nom.ld_status == 'approved' and not hasattr(nom, 'bond'):
        TrainingBond.objects.create(employee=nom.employee, course=nom.session.course, session=nom.session, nomination=nom,
                                    amount=nom.session.effective_cost or 0, start_date=nom.session.end_date)
        notify(employee=nom.employee, title="Training bond", notification_type='learning',
               message=f"Please accept the training bond for {nom.session.course.title} to confirm your seat.")
    if nom.seat_status == 'confirmed':
        notify(employee=nom.employee, title="Training confirmed", notification_type='learning',
               message=f"Your seat in {nom.session.course.title} on {nom.session.start_date} is confirmed.")
    return nom


def reject(nom, level, reason, user=None):
    if not reason:
        raise ValidationError("A reason is required.")
    setattr(nom, 'manager_status' if level == 'manager' else 'ld_status', 'rejected')
    nom.seat_status, nom.rejection_reason = 'cancelled', reason
    nom.save()
    _promote_waitlist(nom.session)
    return nom


def _promote_waitlist(session):
    free = session.seats - session.nominations.filter(seat_status='confirmed').count()
    for w in session.nominations.filter(seat_status='waitlist').order_by('created_at')[:max(free, 0)]:
        w.seat_status = 'confirmed'
        w.save(update_fields=['seat_status'])


@transaction.atomic
def accept_bond(bond, user=None):
    if bond.status != 'pending':
        raise ValidationError("This bond is not pending acceptance.")
    bond.status, bond.accepted_on = 'active', timezone.now()
    bond.save()
    if bond.nomination:
        bond.nomination.bond_accepted_on = bond.accepted_on
        _try_confirm(bond.nomination)
        bond.nomination.save()
    return bond


@transaction.atomic
def close_session(session, user=None):
    if session.status in ('completed', 'cancelled'):
        raise ValidationError("Session already closed.")
    issued = failed = 0
    course = session.course
    for nom in session.nominations.filter(seat_status='confirmed').select_related('employee'):
        result, _ = ParticipantResult.objects.get_or_create(nomination=nom)
        result.evaluate()
        result.save()
        if result.passed:
            expiry = session.end_date + relativedelta(months=course.certificate_validity_months) if course.certificate_validity_months else None
            Certificate.objects.get_or_create(
                employee=nom.employee, session=session,
                defaults={'course': course, 'title': course.title, 'issued_on': session.end_date, 'expiry_date': expiry,
                          'issued_by': course.provider or 'Internal', 'certificate_number': f"{session.code}-{nom.employee.emp_code}"})
            TrainingNeed.objects.filter(employee=nom.employee, course=course).exclude(status__in=('completed', 'cancelled')).update(status='completed')
            issued += 1
        else:
            TrainingNeed.objects.get_or_create(
                employee=nom.employee, course=course, status='identified',
                defaults={'need': f"Repeat: {course.title}", 'source': 'compliance', 'priority': 'high',
                          'source_reference': f"Not passed in {session.code}"})
            failed += 1
    session.status = 'completed'
    session.save(update_fields=['status'])
    return {'certificates_issued': issued, 'not_passed': failed}


def expiry_scan(days=60):
    """Creates compliance needs for certificates expiring within `days` (run daily via celery beat or manually)."""
    limit = timezone.localdate() + relativedelta(days=days)
    created = 0
    for cert in Certificate.objects.filter(expiry_date__isnull=False, expiry_date__lte=limit, course__isnull=False).select_related('employee', 'course'):
        newer = Certificate.objects.filter(employee=cert.employee, course=cert.course, issued_on__gt=cert.issued_on).exists()
        if newer:
            continue
        _, was = TrainingNeed.objects.get_or_create(
            employee=cert.employee, course=cert.course, source='compliance', status='identified',
            defaults={'need': f"Renew: {cert.title} (expires {cert.expiry_date})", 'priority': 'high',
                      'target_date': cert.expiry_date})
        created += int(was)
    return created


def summary():
    today = timezone.localdate()
    certs = Certificate.objects.all()
    # renewed (superseded) certificates are history, not compliance items
    renewed_ids = [c.id for c in certs.filter(course__isnull=False).select_related('course') if c.superseded]
    certs = certs.exclude(id__in=renewed_ids)
    results = ParticipantResult.objects.all()
    return {
        'needs_open': TrainingNeed.objects.exclude(status__in=('completed', 'cancelled')).count(),
        'needs_by_source': list(TrainingNeed.objects.values('source').annotate(count=Count('id')).order_by('-count')),
        'sessions_upcoming': TrainingSession.objects.filter(start_date__gte=today, status__in=('published', 'draft')).count(),
        'sessions_completed': TrainingSession.objects.filter(status='completed').count(),
        'certificates_valid': certs.filter(Q(expiry_date__isnull=True) | Q(expiry_date__gt=today + relativedelta(days=60))).count(),
        'certificates_expiring': certs.filter(expiry_date__gte=today, expiry_date__lte=today + relativedelta(days=60)).count(),
        'certificates_expired': certs.filter(expiry_date__lt=today).count(),
        'bonds_active': TrainingBond.objects.filter(status='active').count(),
        'pass_rate': round(results.filter(passed=True).count() * 100 / results.exclude(passed__isnull=True).count(), 1) if results.exclude(passed__isnull=True).exists() else None,
        'avg_feedback': results.aggregate(v=Avg('feedback_rating'))['v'],
        'training_cost': float(sum(((s.effective_cost or 0) * s.confirmed_count() for s in
                                    TrainingSession.objects.filter(status='completed').select_related('course')), 0)),
    }
