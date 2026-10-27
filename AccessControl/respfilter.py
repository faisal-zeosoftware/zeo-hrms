"""v1.62.0 – what an API answer may show (README-UPDATE 83.2 and 83.6).

1. Hidden fields (gap 6 of the security review): an employee who reads their own record through the employee API no longer
   receives the fields HR set to "Not shown in self service" (Employees › Self-service settings), the groups that are hidden
   (e.g. bank, family, salary) or the face-recognition data – before, the screens hid them but the API still sent them.
2. Masking (Settings › Sign-in security › "Mask sensitive numbers", off by default): IBAN / account numbers, Emirates ID and
   passport numbers are shown as ••••1234 to users without the rights "Can see full bank details" / "Can see full Emirates ID
   and passport numbers"; salaries are shown as •••• to HR users without "Can see salaries when they are masked" (employees
   still see their own pay). A masked value sent back by a form is never saved (ZeoCloud.hardening).
Company administrators always see everything.
"""
import logging
import re

from django.db import connection

log = logging.getLogger(__name__)
MASK = '••••'
KEEP_SYSTEM = {'users', 'is_ess', 'created_at', 'updated_at', 'created_by', 'updated_by', 'is_active', 'id'}
ALWAYS_HIDDEN = {'face_encoding'}
BANK = re.compile(r'(^|_)(iban|iban_number|iban_no|account_number|account_no|bank_account|bank_account_no|bank_account_number|acc_no|accountno)$', re.I)
IDENT = re.compile(r'^(emp_)?(emirates_id|emirates_id_no|emirates_id_number|eid|eid_no|eid_number|passport_no|passport_number|passport)$', re.I)
SALARY = {'basic_salary', 'gross_salary', 'net_salary', 'total_salary', 'current_salary', 'last_month_salary', 'final_month_salary',
          'salary', 'basic', 'gross', 'net', 'net_pay', 'gross_pay', 'total_earnings', 'total_deductions', 'monthly_salary', 'salary_amount',
          'expected_salary', 'emp_jh_leaving_salary_permonth'}


def mask_value(v, keep=4):
    if v in (None, ''):
        return v
    s = str(v)
    if MASK in s:
        return s
    if keep and len(s) > keep:
        return MASK + s[-keep:]
    return MASK


def _policy(schema):
    from django.core.cache import cache
    k = f'zeo:maskpol:{schema}'
    v = cache.get(k)
    if v is None:
        from django_tenants.utils import schema_context
        from ZeoCloud.models import SecurityPolicy
        with schema_context('public'):
            v = bool(SecurityPolicy.objects.filter(schema=schema).values_list('mask_sensitive', flat=True).first())
        cache.set(k, v, 30)
    return v


def forget(schema):
    from django.core.cache import cache
    cache.delete(f'zeo:maskpol:{schema}')


def _hide_employee(d, pm):
    from .ess_guard import HIDDEN, NESTED, emp_field_policy, field_policy
    for k in list(d.keys()):
        if k in ALWAYS_HIDDEN:
            d.pop(k, None)
            continue
        if k in KEEP_SYSTEM:
            continue
        if k in NESTED:
            if field_policy(NESTED[k][0], '', pm) == HIDDEN:
                d.pop(k, None)
            continue
        if emp_field_policy(k, pm) == HIDDEN:
            d.pop(k, None)
    if field_policy('salary', '', pm) == HIDDEN:
        for k in list(d.keys()):
            if k in SALARY or k.startswith('salary') or k in ('emp_salary', 'salary_structure', 'payslips', 'employee_salary'):
                d.pop(k, None)


def _walk_emp(data, pm):
    if isinstance(data, list):
        for x in data:
            _walk_emp(x, pm)
    elif isinstance(data, dict):
        if 'emp_code' in data and ('emp_first_name' in data or 'emp_branch_id' in data):
            _hide_employee(data, pm)
        for k in ('results', 'data', 'rows'):
            if isinstance(data.get(k), (list, dict)):
                _walk_emp(data[k], pm)


def _mask(data, bank, ident, salary, depth=0):
    if depth > 12:
        return data
    if isinstance(data, list):
        return [_mask(x, bank, ident, salary, depth + 1) for x in data]
    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            ks = str(k)
            if isinstance(v, (dict, list)):
                out[k] = _mask(v, bank, ident, salary, depth + 1)
            elif bank and BANK.search(ks) and v not in (None, ''):
                out[k] = mask_value(v)
            elif ident and IDENT.search(ks) and v not in (None, '') and not isinstance(v, bool):
                out[k] = mask_value(v)
            elif salary and ks.lower() in SALARY and v not in (None, '') and not isinstance(v, bool):
                out[k] = MASK
            else:
                out[k] = v
        return out
    return data


def apply(request, view, response):
    """Filter response.data in place (DRF Response)."""
    data = getattr(response, 'data', None)
    if not isinstance(data, (dict, list)) or getattr(response, 'status_code', 200) >= 400:
        return response
    schema = getattr(connection, 'schema_name', 'public')
    if not schema or schema == 'public':
        return response
    user = getattr(request, 'user', None)
    if not getattr(user, 'is_authenticated', False) or getattr(user, 'is_superuser', False):
        return response
    from .access import ctx, view_model
    c = ctx(request)
    if c.admin:
        return response
    try:
        from EmpManagement.models import emp_master
        if view_model(view) is emp_master and 'view_emp_master' not in c.codes:
            from .ess_guard import policy_map
            _walk_emp(data, policy_map())
    except Exception:
        log.exception('hidden fields')
    try:
        if _policy(schema):
            bank = 'see_bank_details' not in c.codes
            ident = 'see_identity_numbers' not in c.codes
            salary = c.backoffice and 'see_salaries' not in c.codes
            if bank or ident or salary:
                response.data = _mask(data, bank, ident, salary)
    except Exception:
        log.exception('masking')
    return response


def install():
    from rest_framework.views import APIView
    if getattr(APIView, '_zeo_respfilter', False):
        return
    orig = APIView.finalize_response

    def finalize_response(self, request, response, *args, **kwargs):
        response = orig(self, request, response, *args, **kwargs)
        if getattr(self, 'zeo_filter', True) is not False:
            try:
                apply(request, self, response)
            except Exception:
                log.exception('response filter')
        return response
    APIView.finalize_response = finalize_response
    APIView._zeo_respfilter = True
