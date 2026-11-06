"""v1.62.1 (user request 2 Nov) – required setup before HR transactions.

A company created from 1.62.1 on (SetupGuide.gate_on, set by ZeoApps.services.setup_new_company) finishes the required steps
of the setup guide before leave, attendance, overtime, payroll, loans, advances, air
tickets, requests, resignations and asset requests can be saved. Setup screens (masters, employees, users, settings, the
guide itself) and every read stay open.

Later on 2 Nov the rule became stricter: all 15 steps – a step counts when it is Completed, and a step that can be
skipped (numbering, shifts, modules, communication) also counts when the administrator chose Skip; a step never touched
still blocks.

Companies that existed before 1.62.1 have gate_on = False (migration ZeoAdmin 0006) and work as before. A duplicate of a
company copies its guide row, so a set-up company stays set up. The company administrator can switch the rule off in the
setup guide ("Allow transactions before the setup is finished"); Adept can do it with `manage.py setup_gate`.

The check is SetupGateMiddleware: POST / PUT / PATCH on the transaction paths below answer
409 {code: 'setup_required', detail, steps: [...]}. The state is cached per schema for 20 s and forgotten when a step is
marked or the switch changes.
"""
import json
import re

from django.core.cache import cache
from django.db import connection
from django.http import HttpResponse

from Translations.i18n import tr

CACHE = 'zeo:setupgate:{}'
TTL = 20

# POST / PUT / PATCH on these paths are HR transactions (reads and DELETE stay open). Setup lists such as
# …-approval-levels, …-email-template, …-escalation, leave types and policies are not matched.
TRANSACTION_PATHS = [
    # leave and leave approvals
    r'/calendars/api/emp-leave-request/', r'/employee/api/emp-leave-request/', r'/calendars/api/leave-approvals/',
    r'/calendars/api/compensatory-leave-request/', r'/calendars/api/employee-leave-rejoins/',
    r'/calendars/api/leave-encashment-tranaction/', r'/leave-policy/api/requests/', r'/leave-policy/api/encashments/',
    r'/payroll/api/emp-leave-encashment/', r'/payroll-plus/api/encashments/',
    r'/calendars/api/lateinearly-request/', r'/calendars/api/lateineralyout-approval/',
    # attendance marking, punches and their import, overtime
    r'/calendars/api/attendance/', r'/calendars/api/import-attendance/', r'/calendars/api/Emp-bulkuplod-attendance/',
    r'/calendars/api/Emp-overtime/', r'/calendars/api/Emp-bulkupload-overtime/',
    r'/attendance-plus/api/punches/', r'/attendance-plus/api/corrections/', r'/attendance-plus/api/kiosk/punch/',
    r'/attendance-plus/api/overtime/', r'/attendance-plus/api/late-early/', r'/employee-profile/api/record/\d+/attendance/',
    r'/attendance-plus/api/ess-punch/$',   # v1.67.5 (bugs) ESS1 the self-service barcode / face punch
    r'/api/v1/attendance/punches/',
    # payroll runs and payslips, final settlement
    r'/payroll/api/PayrollRun/', r'/payroll/api/payslip/', r'/payroll/api/PayslipConfirmedViewSet/', r'/payroll/api/approval-payroll/',
    r'/payroll-plus/api/runs/', r'/payroll-plus/api/workspace/runs/', r'/payroll-plus/api/workspace/approvals/',
    r'/payroll-plus/api/off-cycle/', r'/payroll-plus/api/arrears/', r'/payroll-plus/api/settlements/',
    # loans and advance salary
    r'/payroll/api/loan-application/', r'/payroll/api/loan-approval/', r'/payroll/api/loan-repayment/',
    r'/payroll-plus/api/loans/', r'/payroll-plus/api/loan-desk/requests/', r'/payroll-plus/api/loan-desk/approvals/',
    r'/payroll-plus/api/loan-desk/repayments/',
    r'/payroll/api/advance-salary-request/', r'/payroll/api/approval-salaryrequest/', r'/expense/api/advances/',
    # air tickets
    r'/payroll/api/airticket-request/', r'/payroll/api/airticket-approval/',
    r'/payroll-plus/api/air-tickets/requests/', r'/payroll-plus/api/air-tickets/approvals/', r'/payroll-plus/api/air-tickets/mine/',
    # general and document requests, resignations, asset requests (and the mobile app's requests / approvals)
    r'/employee/api/general-request/', r'/employee/api/request-approvals/', r'/employee/api/Doc-request/',
    r'/employee/api/Doc-request-approval/', r'/hr-actions/api/requests/',
    r'/employee/api/employee-resignation/', r'/employee/api/resign-approval/', r'/hr-actions/api/resignations/',
    r'/payroll-plus/api/leavers/mine/',
    r'/organisation/api/asset-Request/', r'/organisation/api/asset-request-approvals/',
    r'/asset-plus/api/desk/requests/', r'/asset-plus/api/desk/approvals/',
    r'/mobile-co/api/requests/', r'/mobile-co/api/approvals/',
    # expense claims and shift swap requests
    r'/expense/api/expenses/', r'/expense/api/reports/', r'/shift-planner/api/requests/',
]
TX = re.compile('^(?:' + '|'.join(TRANSACTION_PATHS) + ')')
METHODS = ('POST', 'PUT', 'PATCH')


def required():
    """v1.62.1 (user request 2 Nov, later the same day): every step of the guide, in order: [(key, number, name, can skip)].
    A step counts when it is Completed; a step that can be skipped also counts when the administrator chose Skip."""
    from . import setup_guide as SG
    return [(k, no, SG.NAMES[k], can_skip) for k, no, _t, _s, can_skip, _o in SG.STEPS]


def step_ok(status, can_skip):
    return status == 'done' or (can_skip and status == 'skipped')


def _compute():
    from django.apps import apps
    G = apps.get_model('ZeoAdmin', 'SetupGuide')
    g = G.objects.order_by('id').first()
    if g is None or not g.gate_on or not _hr_on():   # v1.67.3 (apps) a company without HR: no HR setup gate
        return {'enforced': False, 'allow': bool(g and g.gate_allow), 'open': [], 'blocked': False}
    steps = g.steps or {}
    open_ = [[k, no] for k, no, _n, can_skip in required() if not step_ok((steps.get(k) or {}).get('status'), can_skip)]
    return {'enforced': True, 'allow': bool(g.gate_allow), 'open': open_, 'blocked': bool(open_) and not g.gate_allow}


def _hr_on():
    """v1.67.3 (apps) the gate is about HR transactions – it never blocks a company that does not use HR."""
    try:
        from ZeoApps.services import is_active
        return bool(is_active('hrms'))
    except Exception:
        return True


def state(schema=None):
    """{enforced, allow, open: [[step key, number]], blocked} of the current (or the named) schema; cached for TTL s."""
    schema = schema or getattr(connection, 'schema_name', 'public')
    if schema == 'public':
        return {'enforced': False, 'allow': False, 'open': [], 'blocked': False}
    key = CACHE.format(schema)
    st = cache.get(key)
    if st is None:
        if schema != connection.schema_name:
            from django_tenants.utils import schema_context
            with schema_context(schema):
                st = _compute()
        else:
            st = _compute()
        cache.set(key, st, TTL)
    return st


def forget(schema=None):
    cache.delete(CACHE.format(schema or getattr(connection, 'schema_name', 'public')))


def blocked():
    try:
        return state()['blocked']
    except Exception:   # never stop the company because the check failed (e.g. a schema not migrated yet)
        return False


def open_names(st=None):
    from . import setup_guide as SG
    st = st or state()
    return [tr(SG.NAMES[k]) for k, _no in st['open']]


def message(st=None):
    st = st or state()
    names = open_names(st)
    n = len(names)
    if n == 1:
        return tr('Finish the setup first – 1 step is still open ({0}).', ', '.join(names))
    return tr('Finish the setup first – {0} steps are still open ({1}).', n, ', '.join(names))


def employee_message():
    return tr('Your company is still being set up – requests open when HR finishes the setup.')


def info(admin=False):
    """What the screens need: the banner on Home, the notice on self service and the switch in the setup guide."""
    st = state()
    return {'enforced': st['enforced'], 'allow': st['allow'], 'blocked': st['blocked'], 'open': open_names(st),
            'open_steps': [k for k, _no in st['open']], 'count': len(st['open']), 'total': len(required()),
            'message': message(st) if st['blocked'] else '', 'employee_message': employee_message() if st['blocked'] else '',
            'link': '/main-sidebar/settings/setup-guide', 'admin': bool(admin)}


def refused():
    st = state()
    body = {'code': 'setup_required', 'detail': message(st), 'employee_detail': employee_message(),
            'steps': open_names(st), 'link': '/main-sidebar/settings/setup-guide'}
    return HttpResponse(json.dumps(body), status=409, content_type='application/json')


# ------------------------------------------------------------------ company creation, duplicate, switch
def enforce_new():
    """Company creation (in the new company's schema): HR transactions wait for the required steps."""
    from django.apps import apps
    G = apps.get_model('ZeoAdmin', 'SetupGuide')
    g = G.get()
    g.gate_on = True
    g.save(update_fields=['gate_on', 'updated_at'])
    forget()


def follow(source_schema):
    """A company made as a copy of another (test company): the rule only when the source itself is still waiting."""
    src = state(source_schema)
    from django.apps import apps
    G = apps.get_model('ZeoAdmin', 'SetupGuide')
    g = G.get()
    g.gate_on = bool(src['blocked'])
    g.save(update_fields=['gate_on', 'updated_at'])
    forget()


def set_allow(on, user=None):
    from django.apps import apps
    from django.utils import timezone
    G = apps.get_model('ZeoAdmin', 'SetupGuide')
    g = G.get()
    g.gate_allow, g.gate_by_id, g.gate_at = bool(on), getattr(user, 'pk', None), timezone.now()
    g.save(update_fields=['gate_allow', 'gate_by_id', 'gate_at', 'updated_at'])
    forget()
    return state()


def _member(request, schema):
    """Only a signed-in user of this company (or one of its API keys) is told about the open steps; anyone else gets the
    view's own answer (401 / 403). Plain token check, without the session side effects of the full sign-in check."""
    try:
        h = request.META.get('HTTP_AUTHORIZATION', '')
        if h.startswith('Bearer '):
            from rest_framework_simplejwt.authentication import JWTAuthentication
            res = JWTAuthentication().authenticate(request)
            if not res:
                return False
            u = res[0]
            return bool(u.is_active and (u.is_superuser or u.tenants.filter(schema_name=schema).exists()))
        raw = h[len('Api-Key '):].strip() if h.startswith('Api-Key ') else request.META.get('HTTP_X_API_KEY', '').strip()
        if raw:
            from .apikeys import hash_key
            from .models import ApiKey
            return ApiKey.objects.filter(key_hash=hash_key(raw), active=True).exists()
    except Exception:
        return False
    return False


class SetupGateMiddleware:
    """409 setup_required on the HR transaction paths while a new company has not finished the required setup steps."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        schema = getattr(connection, 'schema_name', 'public')
        if request.method in METHODS and TX.match(request.path) and schema != 'public' and blocked() and _member(request, schema):
            return refused()
        return self.get_response(request)
