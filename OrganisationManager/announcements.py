"""v1.13.0 – which announcements an employee receives.

An announcement reaches an employee when
  * the employee is one of its specific employees, or
  * it has no specific employees and the employee matches every target it has
    (branches, departments, designations, categories – an empty target means "all").
An announcement without any target goes to the whole company.
Scheduled announcements appear from `schedule_at`; expired ones (`expires_at`) are hidden.
"""
from django.db.models import Count, Q
from django.utils import timezone


def announcements_for(employee, include_expired=False, now=None):
    from .models import Announcement
    now = now or timezone.now()
    qs = Announcement.objects.annotate(
        n_emp=Count('specific_employees', distinct=True), n_br=Count('branches', distinct=True),
        n_dep=Count('department', distinct=True), n_des=Count('designation', distinct=True),
        n_cat=Count('category', distinct=True))
    direct = Q(specific_employees=employee)

    def dim(n, field, value):
        return Q(**{n: 0}) | (Q(**{field: value}) if value else Q(pk__in=[]))

    targeted = (Q(n_emp=0) & dim('n_br', 'branches', employee.emp_branch_id_id) & dim('n_dep', 'department', employee.emp_dept_id_id)
                & dim('n_des', 'designation', employee.emp_desgntn_id_id) & dim('n_cat', 'category', employee.emp_ctgry_id_id))
    ids = set(qs.filter(direct).values_list('id', flat=True)) | set(qs.filter(targeted).values_list('id', flat=True))
    out = Announcement.objects.filter(id__in=ids)
    out = out.filter(Q(schedule_at__isnull=True) | Q(schedule_at__lte=now))
    if not include_expired:
        out = out.filter(Q(expires_at__isnull=True) | Q(expires_at__gte=now))
    return out.order_by('-is_sticky', '-created_at')


def audience(announcement):
    """Active employees an announcement reaches (for read counts)."""
    from EmpManagement.models import emp_master
    emps = emp_master.objects.filter(is_active=True)
    spec = list(announcement.specific_employees.values_list('id', flat=True))
    if spec:
        return emps.filter(id__in=spec)
    for rel, field in (('branches', 'emp_branch_id'), ('department', 'emp_dept_id'), ('designation', 'emp_desgntn_id'), ('category', 'emp_ctgry_id')):
        ids = list(getattr(announcement, rel).values_list('id', flat=True))
        if ids:
            emps = emps.filter(**{f'{field}__in': ids})
    return emps
