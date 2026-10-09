"""v1.13.0 – daily probation reminder (celery beat entry in INTEGRATION.md).

For every company: open probations ending in 30, 14, 7 or 1 days, today, or overdue (once a week) → notification to
the reporting manager and to the HR users of the employee's branch (company admins and users with change_emp_master
whose branch access includes the branch). Employees on notice whose last working day has passed get their final status.
"""
import logging
from datetime import date, timedelta

from celery import shared_task

log = logging.getLogger(__name__)
REMIND_DAYS = (30, 14, 7, 1, 0)


def _hr_users(branch_id):
    from django.apps import apps
    from tenant_users.tenants.models import UserTenantPermissions
    UBA = apps.get_model('OrganisationManager', 'UserBranchAccess')
    out = set()
    for utp in UserTenantPermissions.objects.prefetch_related('groups__permissions'):
        if utp.is_superuser:
            out.add(utp.profile_id)
            continue
        codes = {p.codename for g in utp.groups.all() for p in g.permissions.all()}
        if 'change_emp_master' not in codes:
            continue
        branches = set(b for b in UBA.objects.filter(user_id=utp.profile_id).values_list('branch__id', flat=True) if b)
        if not branches or branch_id in branches:
            out.add(utp.profile_id)
    return out


def remind_company(today=None):
    from django.apps import apps
    from django.db import connection
    from django_tenants.utils import schema_context
    from . import services as S
    from .models import EmploymentInfo
    today = today or date.today()
    Emp = apps.get_model('EmpManagement', 'emp_master')
    schema = connection.schema_name
    # employees without a row yet (created before the app was installed)
    for e in Emp.objects.exclude(id__in=EmploymentInfo.objects.values('employee_id')).select_related('emp_branch_id'):
        S.ensure_info(e, today)
    for i in EmploymentInfo.objects.filter(status='on_notice', date_of_leaving__lt=today):
        e = Emp.objects.filter(pk=i.employee_id).first()
        if e is not None:
            S.refresh_probation(e, i, today)
    sent = 0
    qs = EmploymentInfo.objects.filter(probation_status__in=('on_probation', 'extended'), probation_end_date__isnull=False,
                                       probation_end_date__lte=today + timedelta(days=30))
    for i in qs:
        days = (i.probation_end_date - today).days
        weekly_overdue = days < 0 and (i.last_reminded_on is None or (today - i.last_reminded_on).days >= 7)
        if days not in REMIND_DAYS and not weekly_overdue:
            continue
        if i.last_reminded_on == today:
            continue
        e = Emp.objects.select_related('emp_branch_id').filter(pk=i.employee_id).first()
        if e is None or e.is_active is False:
            continue
        users = _hr_users(e.emp_branch_id_id)
        if e.emp_reporting_manager_id:
            users.add(e.emp_reporting_manager_id)
        when = 'today' if days == 0 else (f'in {days} day' + ('s' if days != 1 else '')) if days > 0 else f'{-days} days ago'
        title = f'Probation ends {when}: {S.full_name(e)} ({e.emp_code})'
        msg = (f'The probation of {S.full_name(e)} ({e.emp_code}) ends {when} ({i.probation_end_date:%d/%m/%Y}). '
               f'Confirm, extend (UAE maximum 6 months in total) or end it on the Employment tab of the employee.')
        try:
            Inbox = apps.get_model('UserManagement', 'UserNotificationInbox')
            with schema_context('public'):
                for uid in users:
                    Inbox.objects.create(user_id=uid, schema_name=schema, branch_id=e.emp_branch_id_id,
                                         branch_name=getattr(e.emp_branch_id, 'branch_name', None), notification_type='general',
                                         title=title[:255], message=msg, source_model='emp_master', source_id=e.pk)
                    sent += 1
        except Exception:
            log.exception('probation reminder failed')
        EmploymentInfo.objects.filter(pk=i.pk).update(last_reminded_on=today)
    return sent


@shared_task
def probation_reminders():
    from django_tenants.utils import get_tenant_model, schema_context
    from . import services as S
    out = {}
    for t in get_tenant_model().objects.exclude(schema_name='public'):
        with schema_context(t.schema_name):
            if not S.table_ready():
                continue
            try:
                out[t.schema_name] = remind_company()
            except Exception:
                log.exception('probation reminders for %s', t.schema_name)
    return out
