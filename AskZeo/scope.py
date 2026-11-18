"""Ask ZEO – the user's own rights, branches and company (AccessControl.access.ctx), used by every answer."""
from django.apps import apps
from django.db import connection
from django.db.models import Q

M = apps.get_model
RELEASED = ('Approved', 'approved', 'paid', 'Paid')
MS = '/main-sidebar/'


class NoCompany(Exception):
    pass


def ctx(request):
    from AccessControl.access import ctx as _ctx
    if getattr(connection, 'schema_name', 'public') in ('', 'public'):
        raise NoCompany()
    return _ctx(request)


def can(c, codes):
    return bool(c.admin or (set(codes) & c.codes))


HR_CODES = {'view_emp_master', 'change_emp_master', 'add_emp_master', 'view_payslip', 'view_payrollrun', 'change_payrollrun'}


def is_hr(c):
    # v1.73.2: "back office" is anybody with any right (a sales agent with one CRM right counted as HR: asked about a colleague, Ask
    # ZEO named them and showed the HR examples). HR = administrator or the employee / payroll rights.
    return bool(c.admin or (HR_CODES & set(getattr(c, 'codes', ()) or ())))


def emp_name(e):
    if not e:
        return ''
    return ' '.join(x for x in (e.emp_first_name, e.emp_last_name) if x) or e.emp_code


def emp_label(e):
    return f'{emp_name(e)} ({e.emp_code})' if e else ''


def emp_url(e):
    return f'{MS}sub-sidebar/employee-details/{e.pk}/details'


def employees(c):
    """Employees this user may see: all (admin), their branches (HR), only themselves (self service)."""
    E = M('EmpManagement', 'emp_master')
    qs = E.objects.all()
    if c.admin:
        return qs
    if not c.backoffice:
        return qs.filter(pk=c.emp.pk) if c.emp else qs.none()
    if c.branches is None:
        return qs
    return qs.filter(Q(emp_branch_id__in=c.branches) | Q(pk=c.emp.pk if c.emp else 0))


def payslips(c):
    P = M('PayrollManagement', 'Payslip')
    qs = P.objects.select_related('payroll_run', 'employee')
    if not c.admin and 'view_payslip' not in c.codes:
        return qs.filter(employee=c.emp, status__in=RELEASED) if c.emp else qs.none()
    return qs.filter(employee__in=employees(c))


def find_employee(c, text):
    """The employee named in the question (code or name), among those the user may see. None when not one."""
    import re
    from .words import norm_ar
    t = ' ' + norm_ar(text).lower() + ' '
    qs = employees(c).only('id', 'emp_code', 'emp_first_name', 'emp_last_name', 'emp_branch_id')
    best = []
    for e in qs[:5000]:
        code = (e.emp_code or '').lower()
        if code and re.search(r'(?<![a-z0-9])' + re.escape(code) + r'(?![a-z0-9])', t):
            return e
        full = emp_name(e).lower()
        first = (e.emp_first_name or '').lower()
        if full and len(full) > 3 and full in t:
            best.append((3, e))
        elif first and len(first) > 2 and re.search(r'(?<![a-z؀-ۿ])' + re.escape(first) + r'(?![a-z؀-ۿ])', t):
            best.append((1, e))
    if not best:
        return None
    best.sort(key=lambda x: -x[0])
    if len(best) > 1 and best[0][0] == best[1][0]:
        return None   # two people with that name – ask for the code
    return best[0][1]
