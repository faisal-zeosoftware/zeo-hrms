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


TARGET_RELATIONS = ('specific_employees', 'branches', 'department', 'designation', 'category')


def announcement_targets():
    """v1.16.0 (D15): {announcement id: {relation: set of target ids}} – read once (one query per relation table); a list of
    employees passes it to announcements_for for every employee."""
    from .models import Announcement
    out = {a: {r: set() for r in TARGET_RELATIONS} for a in Announcement.objects.values_list('id', flat=True)}
    for rel in TARGET_RELATIONS:
        f = Announcement._meta.get_field(rel)
        through = f.remote_field.through
        for a_id, x_id in through.objects.values_list(f.m2m_column_name(), f.m2m_reverse_name()):
            if a_id in out:
                out[a_id][rel].add(x_id)
    return out


def announcements_for(employee, include_expired=False, now=None, targets=None):
    from .models import Announcement
    now = now or timezone.now()
    # v1.16.0 (D15): the rule below was one SQL statement joining the five target tables with counts – 0.4 s per employee
    # (the employee list took ~25 s for 56 employees). Same rule, worked out from the target ids:
    #   direct   = the employee is one of the specific employees
    #   targeted = no specific employees, and for every target (branches, departments, designations, categories)
    #              the target is empty or holds the employee's value
    targets = announcement_targets() if targets is None else targets
    dims = (('branches', employee.emp_branch_id_id), ('department', employee.emp_dept_id_id),
            ('designation', employee.emp_desgntn_id_id), ('category', employee.emp_ctgry_id_id))
    ids = set()
    for a_id, t in targets.items():
        if employee.pk in t['specific_employees']:
            ids.add(a_id)
        elif not t['specific_employees'] and all(not t[rel] or (value and value in t[rel]) for rel, value in dims):
            ids.add(a_id)
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
