"""v1.51.0 – self-service on the shared kiosk for staff without a phone or a ZEO login of their own: employee code (or
Emirates ID / passport / labour card number) + kiosk PIN → payslips, leave balance, leave requests, a question to HR.

* the kiosk must have "self_service" switched on; the session is a signed token for 5 minutes (renewed with every step)
  bound to the kiosk and the employee – nothing is kept in the browser of the shared tablet;
* 5 wrong PINs lock the employee's PIN for 15 minutes;
* a leave request goes through the normal leave request (rules, numbering, approvals) as the employee.
"""
from datetime import timedelta

from django.apps import apps
from django.contrib.auth.hashers import check_password
from django.core import signing
from django.db import connection
from django.utils import timezone

from . import models as M
from .services import Refused

SALT = 'zeo-kiosk-ess'
SESSION_SECONDS = 300
MAX_FAILS, LOCK_MINUTES = 5, 15


def find_employee(ident):
    """By employee code, or the Emirates ID / passport / labour card number of the employee's identity."""
    Emp = apps.get_model('EmpManagement', 'emp_master')
    ident = str(ident or '').strip()
    if not ident:
        return None
    e = Emp.objects.filter(emp_code__iexact=ident, is_active=True).first()
    if e is not None:
        return e
    try:
        Idt = apps.get_model('EmployeeProfile', 'EmployeeIdentity')
        digits = ident.replace('-', '').replace(' ', '')
        from django.db.models import Q
        row = Idt.objects.filter(Q(emirates_id__iexact=ident) | Q(emirates_id__iexact=digits) | Q(passport_no__iexact=ident) |
                                 Q(labour_card_no__iexact=ident)).first()
        if row is not None:
            return Emp.objects.filter(pk=row.employee_id, is_active=True).first()
    except LookupError:
        pass
    return None


def login(kiosk, ident, pin):
    if not kiosk.self_service:
        raise Refused('Self-service is not switched on for this kiosk.', 403)
    e = find_employee(ident)
    key = M.EmployeeKey.objects.filter(employee_id=e.pk).first() if e else None
    now = timezone.now()
    if key is not None and key.locked_until and key.locked_until > now:
        mins = int((key.locked_until - now).total_seconds() // 60) + 1
        raise Refused(f'Too many wrong PINs – try again in {mins} minute(s) or ask HR.', 403)
    if e is None or key is None or not key.pin_hash or not check_password(str(pin or ''), key.pin_hash):
        if key is not None:
            key.failed_pins = (key.failed_pins or 0) + 1
            if key.failed_pins >= MAX_FAILS:
                key.locked_until, key.failed_pins = now + timedelta(minutes=LOCK_MINUTES), 0
            key.save(update_fields=['failed_pins', 'locked_until', 'updated_at'])
        raise Refused('ID or PIN is wrong.', 403)
    if key.failed_pins or key.locked_until:
        key.failed_pins, key.locked_until = 0, None
        key.save(update_fields=['failed_pins', 'locked_until', 'updated_at'])
    return e, token(kiosk, e)


def token(kiosk, e):
    return signing.dumps({'k': kiosk.pk, 'e': e.pk, 's': getattr(connection, 'schema_name', '')}, salt=SALT)


def from_token(kiosk, tok):
    try:
        d = signing.loads(tok or '', salt=SALT, max_age=SESSION_SECONDS)
    except signing.BadSignature:
        raise Refused('Your session ended – enter your ID and PIN again.', 401)
    if d.get('k') != kiosk.pk or d.get('s') != getattr(connection, 'schema_name', ''):
        raise Refused('Your session ended – enter your ID and PIN again.', 401)
    e = apps.get_model('EmpManagement', 'emp_master').objects.filter(pk=d.get('e'), is_active=True).first()
    if e is None:
        raise Refused('Your session ended – enter your ID and PIN again.', 401)
    return e


def _name(e):
    return ' '.join(x for x in (e.emp_first_name, e.emp_last_name) if x)


def balances(e):
    from LeavePolicy import engine
    LT = apps.get_model('calendars', 'leave_type')
    p = engine.policy_for(e)
    out, types = [], []
    if p is None:
        return out, types
    names = dict(LT.objects.values_list('id', 'name'))
    LR = apps.get_model('calendars', 'employee_leave_request')
    for l in p.lines.all():
        if l.gender not in ('B', e.emp_gender or ''):
            continue
        types.append({'id': l.leave_type_id, 'name': names.get(l.leave_type_id, '?')})
        if l.accrual in ('per_event', 'none'):
            continue
        try:
            bal = float(engine.balance_row(e.pk, l.leave_type_id).balance or 0)
        except Exception:
            continue
        pending = sum(float(r.applied_days or r.number_of_days or 0) for r in LR.objects.filter(employee=e, leave_type_id=l.leave_type_id, status='pending'))
        out.append({'type': names.get(l.leave_type_id, '?'), 'balance': round(bal, 2), 'pending': round(pending, 2)})
    return out, types


def home(kiosk, e):
    from SelfService import services as SS
    slips = [SS.payslip_json(p) for p in SS.my_payslips(e)[:6]]
    bal, types = balances(e)
    LR = apps.get_model('calendars', 'employee_leave_request')
    reqs = [{'id': r.pk, 'number': r.document_number, 'type': r.leave_type.name, 'from': r.start_date, 'to': r.end_date, 'status': r.status,
             'days': float(r.applied_days or r.number_of_days or 0)} for r in LR.objects.filter(employee=e).select_related('leave_type').order_by('-id')[:5]]
    cats = []
    try:
        from ZeoHR.helpdesk import categories
        cats = [{'id': c.pk, 'name': c.name} for c in categories() if not c.redirect]
    except Exception:
        pass
    des = e.emp_desgntn_id.desgntn_job_title if e.emp_desgntn_id_id else ''
    if must_change_pin(e):   # v1.62.0 a start PIN from HR: the employee chooses their own before anything else
        return {'employee': {'name': _name(e), 'code': e.emp_code, 'designation': des}, 'must_change_pin': True,
                'session': token(kiosk, e), 'session_seconds': SESSION_SECONDS}
    return {'employee': {'name': _name(e), 'code': e.emp_code, 'designation': des}, 'payslips': slips, 'ytd': SS.ytd(e),
            'balances': bal, 'leave_types': types, 'requests': reqs, 'help_categories': cats, 'has_login': bool(e.users_id),
            'session': token(kiosk, e), 'session_seconds': SESSION_SECONDS}


def payslip(e, pk):
    from SelfService import services as SS
    p = SS.my_payslips(e).filter(pk=pk).first()
    if p is None:
        raise Refused('Payslip not found.', 404)
    out = SS.payslip_json(p, lines=True)
    out['lines'] = [l for l in out.get('lines', []) if l.get('amount')]   # nothing to read in a zero line
    return out


def request_leave(e, data):
    """Through the normal leave request API, as the employee (rules, document number, approvals and notices)."""
    if not e.users_id:
        raise Refused('You have no ZEO login yet – ask HR to enter the leave for you.', 403)
    from LeavePolicy.conflicts import check
    from django.utils.dateparse import parse_date
    LT = apps.get_model('calendars', 'leave_type')
    lt = LT.objects.filter(pk=data.get('leave_type')).first()
    a, b = parse_date(str(data.get('start_date') or '')), parse_date(str(data.get('end_date') or ''))
    if lt is None or a is None or b is None:
        raise Refused('Choose the leave type and the dates.')
    res = check(e, lt, a, b, False, created_by_id=e.users_id)
    if res['errors']:
        raise Refused(' '.join(res['errors']))
    from rest_framework.test import APIRequestFactory, force_authenticate
    from calendars.views import LeaveRequestviewset
    body = {'employee': e.pk, 'leave_type': lt.pk, 'start_date': str(a), 'end_date': str(b), 'reason': (data.get('reason') or 'Asked at the kiosk')[:500]}
    rf = APIRequestFactory().post('/calendars/emp-leave-request/', body, format='json')
    rf.tenant = getattr(connection, 'tenant', None)
    force_authenticate(rf, user=e.users)
    from rest_framework.permissions import IsAuthenticated
    # the kiosk checked the employee's ID and PIN: the employee asks for their own leave, as in Self service
    resp = LeaveRequestviewset.as_view({'post': 'create'}, permission_classes=[IsAuthenticated])(rf)
    if resp.status_code not in (200, 201):
        d = getattr(resp, 'data', None)
        msg = d.get('detail') if isinstance(d, dict) and d.get('detail') else (' '.join(str(x) for v in d.values() for x in (v if isinstance(v, list) else [v]))
                                                                                if isinstance(d, dict) else str(d))
        raise Refused(str(msg)[:400] or 'The leave request could not be sent.')
    return {'number': resp.data.get('document_number'), 'days': res['days'], 'warnings': res['warnings']}


def ask_hr(e, data):
    from ZeoHR.helpdesk import categories, open_ticket
    c = categories().filter(pk=data.get('category'), redirect='').first()
    if c is None:
        raise Refused('Choose what it is about.')
    try:
        o = open_ticket(e, c, data.get('subject') or c.name, data.get('description') or '', e.users if e.users_id else None, channel='kiosk')
    except Exception as exc:
        raise Refused(getattr(exc, 'message', None) or str(exc))
    return {'number': o.number, 'resolve_due': o.resolve_due}


# ------------------------------------------------------------------ v1.62.0 the employee's own PIN (start PIN from Give access › Kiosk only)
WEAK_PINS = {'0000', '1111', '1234', '4321', '2222', '3333', '4444', '5555', '6666', '7777', '8888', '9999', '123456', '654321', '000000',
             '111111', '121212', '112233', '1212', '1122'}


def must_change_pin(e):
    k = M.EmployeeKey.objects.filter(employee_id=e.pk).values_list('pin_must_change', flat=True).first()
    return bool(k)


def set_pin(e, pin, pin2=None):
    from django.contrib.auth.hashers import check_password, make_password
    pin = str(pin or '').strip()
    if not pin.isdigit() or not 4 <= len(pin) <= 6:
        raise Refused('The PIN is 4 to 6 digits.')
    if pin2 is not None and str(pin2).strip() != pin:
        raise Refused('The two PINs are not the same.')
    if pin in WEAK_PINS or len(set(pin)) == 1:
        raise Refused('This PIN is too easy to guess – choose another.')
    if e.emp_date_of_birth and pin in (e.emp_date_of_birth.strftime('%d%m'), e.emp_date_of_birth.strftime('%Y'), e.emp_date_of_birth.strftime('%m%d')):
        raise Refused('Do not use your date of birth.')
    key, _ = M.EmployeeKey.objects.get_or_create(employee_id=e.pk)
    if key.pin_hash and check_password(pin, key.pin_hash):
        raise Refused('Choose a new PIN – not the start PIN.')
    key.pin_hash, key.pin_must_change, key.failed_pins, key.locked_until = make_password(pin), False, 0, None
    key.save()
    return True
