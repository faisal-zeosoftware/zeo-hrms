"""
v1.17.0 (dash17) – Home: one menu entry, the right dashboard for the user's role, and the "Needs your action" strip.

GET /dashboard/api/home/
    tabs     the views this user may open, in this order: hr (HR operations – company admins and HR users),
             management (company admins / view_ceo_dashboard), team (people report to the user), my (linked to an employee)
    default  the view the user lands on: admin / HR → hr, CEO right → management, manager → team, employee → my
    actions  only what this user must act on, each with its count and what a click opens (a drill-down list of the
             dashboard API, or a report of the report centre); the count is always the number of rows it opens:
               approvals   requests of every module waiting for this user's approval (or delegated to them)
               payslips    payslips waiting for this user's approval (payslip approval workflow)
               payroll     payroll runs processed and waiting for approval (users who manage payroll)
               documents   documents and visas expired or expiring within 30 days of the people the user manages
                           (HR: their branches – report "expiry-all"; a manager: their team)
               input       the user's own items needing input: overdue tasks / to-dos, goals to set, own documents to renew,
                           expense reports not submitted
               policies    (v1.17.1) company policies the user still has to acknowledge (My view)
"""
import logging

from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services as S

logger = logging.getLogger(__name__)

HR_CODES = {'view_emp_master', 'view_employee_leave_request', 'view_leaveapproval', 'view_attendance', 'view_payrollrun', 'view_payslip',
            'view_emp_documents', 'view_generalrequest', 'view_employeeresignation'}
TABS = [('hr', 'HR operations'), ('management', 'Management'), ('team', 'Team'), ('my', 'My dashboard')]


def tabs_for(request):
    from AccessControl.access import ctx
    from .ceo import can_view
    c = ctx(request)
    user = request.user
    allowed = []
    if c.admin or (HR_CODES & c.codes):
        allowed.append('hr')
    if can_view(c):
        allowed.append('management')
    if S.team_ids(user, depth=1):
        allowed.append('team')
    if c.emp is not None or not allowed:
        allowed.append('my')
    return c, allowed


def actions_for(request, c, allowed):
    user = request.user
    today = timezone.localdate()
    out = []

    # v1.17.0 (audit L4): every item belongs to the view where the user acts on it – the strip of a view shows its own items
    def pick(*prefs):
        return [next((v for v in prefs if v in allowed), allowed[0])]
    VIEWS = {'approvals': pick('team', 'hr', 'my', 'management'), 'payslips': pick('hr', 'management', 'my'),
             'payroll': [v for v in ('hr', 'management') if v in allowed] or pick('hr'), 'input': pick('my', 'team', 'hr', 'management')}

    def add(key, label, count, **kw):
        if count:
            out.append(dict({'key': key, 'label': label, 'count': count, 'views': kw.pop('views', None) or VIEWS.get(key) or [allowed[0]]}, **kw))

    def safe(fn):
        try:
            fn()
        except Exception:
            logger.exception('home action failed')

    def approvals():
        items = S.pending_approvals(user, request)   # v1.17.1: the request (rights of the newer modules)
        oldest = min((i['date'] for i in items if i['date']), default=None)
        add('approvals', 'Approvals waiting for you', len(items), tone='warn',
            sub_n=(today - oldest).days if oldest else None, sub='oldest {0} days',
            drill={'metric': 'approvals', 'title': 'Waiting for my approval'})
    safe(approvals)

    def payslips():
        add('payslips', 'Payslips to approve', len(S.payslip_approvals_for(user)), tone='warn',
            drill={'metric': 'payslip_approvals', 'title': 'Payslips waiting for your approval'})
    safe(payslips)

    def payroll():
        if c.admin or 'change_payrollrun' in c.codes:
            add('payroll', 'Payroll runs to approve', len(S.payroll_runs_to_approve(user, c.branches)), tone='warn',
                drill={'metric': 'payroll_to_approve', 'title': 'Payroll runs to approve'})
    safe(payroll)

    def documents():
        if 'hr' in allowed:
            from .overview import Calc, rpt
            from .reports import REPORTS
            spec = REPORTS.get('expiry-all')
            if spec and (c.admin or set(spec[5]) & c.codes):
                k = Calc(request).kpi('Documents and visas to renew', 'expiry-all', {'status': 'Expired|Within 30 days'}, timed=False)
                add('documents', 'Documents and visas to renew', k['value'], tone='bad', sub='expired or expiring within 30 days', views=['hr'],
                    link=k['link'], measure=k['measure'])
        if 'team' in allowed:
            r = S.drill(user, 'documents', {'scope': 'team', 'days': '30'})
            add('team_documents', 'Team documents to renew', len(r['rows']), tone='bad', sub='expired or expiring within 30 days', views=['team'],
                drill={'metric': 'documents', 'params': {'scope': 'team', 'days': 30}, 'title': 'Team documents to renew'})
    safe(documents)

    def mine():
        add('input', 'Your items needing input', len(S.my_input_rows(user, c.emp, today)),
            drill={'metric': 'my_input', 'title': 'Your items needing input'})
    safe(mine)

    def policies():   # v1.17.1: = drill my_policies state=open
        if c.emp is not None and 'my' in allowed:
            from .dash171 import my_policies
            rows = [p for p in my_policies(c.emp, user.pk, today) if p['state'] in ('to_acknowledge', 'overdue')]
            od = sum(1 for p in rows if p['state'] == 'overdue')
            add('policies', 'Policies to acknowledge', len(rows), tone='bad' if od else 'warn', views=['my'],
                sub='{0} overdue' if od else '', sub_n=od if od else None,
                drill={'metric': 'my_policies', 'params': {'state': 'open'}, 'title': 'Policies to acknowledge'})
    safe(policies)
    return out


class HomeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        c, allowed = tabs_for(request)
        labels = dict(TABS)
        name = ''
        if c.emp is not None:
            name = ' '.join(x for x in (getattr(c.emp, 'emp_first_name', '') or '', getattr(c.emp, 'emp_last_name', '') or '') if x).strip()
        out = {'tabs': [{'key': k, 'label': labels[k]} for k in allowed], 'default': allowed[0], 'is_admin': bool(c.admin),
               'has_employee': c.emp is not None, 'today': timezone.localdate(),
               'name': name or request.user.get_full_name() or request.user.username}
        if request.query_params.get('actions') != '0':
            out['actions'] = actions_for(request, c, allowed)
        try:   # links into the report centre in the request language (same as the dashboard cards)
            from .views import _localize_links
            data = {'cards': [{'route': '', 'kpis': [a for a in out.get('actions', []) if a.get('link')], 'items': []}]}
            _localize_links(data, request)
        except Exception:
            pass
        return Response(out)
