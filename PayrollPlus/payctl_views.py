# v1.62.11 (items 228, 231): new module – API of the payroll controls (PayrollPlus/payctl.py), mounted at /payroll-plus/
"""v1.62.11 (items 228, 231) – payroll controls API.

GET  PUT api/payctl/settings/                       pay-date shift, loan limit by gratuity (+ %)
GET  POST api/payctl/holds/?month=&status=          salary holds                     POST {employee, month YYYY-MM, kind full|amount|percent, value, reason}
POST api/payctl/holds/<id>/release/ | cancel/       {release_from YYYY-MM (empty = next run), note}
GET  POST api/payctl/locks/                         period locks                     POST {month YYYY-MM, branch (empty = all), note}
POST api/payctl/locks/<id>/unlock/                  {reason} – right "Can unlock a locked payroll period"
GET  POST api/payctl/revisions/?status=             salary revisions                 POST {employee, effective_from, reason, lines: [{component_id, new}]}
GET  api/payctl/revisions/salary/?employee=         the employee's current salary rows
POST api/payctl/revisions/<id>/approve/ | reject/ | cancel/   {note}
GET  api/payctl/pay-date/?date=&branches=1,2        the shifted payment date (preview)
GET  api/payctl/loan-limit/?employee=               the employee's loan limit by gratuity
GET  api/payctl/rehire/                             leavers; POST api/payctl/rehire/<employee id>/ {rehire_date, gratuity_continuity, note}
GET  api/payctl/import/template/                    CSV template; POST api/payctl/import/ (multipart file, apply=0|1)
"""
from django.apps import apps
from django.http import HttpResponse
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from Translations.i18n import tr

from . import payctl as PC
from . import services as S
from .views import Base

M = apps.get_model
RUN_READ = {'view_payrollrun', 'change_payrollrun', 'add_payrollrun', 'view_payslip', 'change_payslip'}
RUN_WRITE = {'change_payrollrun', 'add_payrollrun'}
SAL_READ = {'view_employeesalarystructure', 'change_employeesalarystructure', 'add_employeesalarystructure'}
SAL_WRITE = {'change_employeesalarystructure', 'add_employeesalarystructure'}
UNLOCK = {'unlock_periodlock'}
SET = {'change_payrollplussettings'}
EMP_WRITE = {'change_emp_master', 'add_emp_master'}


class _B(Base):
    def handle_exception(self, exc):
        if isinstance(exc, PC.Refused):   # the field messages at the top level: the shared form shows them next to the fields
            body = {**(exc.problems or {}), 'detail': exc.message}
            return Response(body, status=exc.status)
        from django.core.exceptions import ValidationError as DVE
        if isinstance(exc, DVE):
            msgs = exc.messages if hasattr(exc, 'messages') else [str(exc)]
            return Response({'detail': ' '.join(str(m) for m in msgs)}, status=400)
        return super().handle_exception(exc)

    def need(self, codes, msg):
        if not self.can(codes):
            raise PC.Refused(tr(msg), status=403)

    def branch_ok(self, bid):
        return self.c.branches is None or bid in self.c.branches

    def emps(self, ids):
        E = M('EmpManagement', 'emp_master')
        return {e.pk: e for e in E.objects.filter(pk__in=set(ids))}

    def emp_or_403(self, emp_id):
        try:
            emp_id = int(emp_id)
        except (TypeError, ValueError):
            raise PC.Refused(tr('Choose the employee.'), problems={'employee': tr('Choose the employee.')})
        if not self.emp_ok(emp_id):
            raise PC.Refused(tr('This employee is not in your branches.'), status=403)
        return emp_id


class SettingsView(_B):
    def get(self, request):
        self.need(RUN_READ | SET, 'You do not have the right to see the payroll controls.')
        return Response({**PC.settings(), 'can_edit': self.can(SET)})

    def put(self, request):
        self.need(SET, 'You do not have the right to change the payroll controls.')
        return Response({**PC.save_settings(request.data, request.user), 'can_edit': True})


# ------------------------------------------------------------------ holds
class HoldsView(_B):
    def get(self, request):
        self.need(RUN_READ, 'You do not have the right to see salary holds.')
        from .models import SalaryHold
        qs = SalaryHold.objects.all()
        m = PC._parse_month(request.query_params.get('month'))
        if m:
            qs = qs.filter(month=m)
        st = request.query_params.get('status')
        if st:
            qs = qs.filter(status=st)
        hs = list(qs[:2000])
        em = self.emps(h.employee_id for h in hs)
        rows = [PC.hold_row(h, em) for h in hs if self.branch_ok(getattr(em.get(h.employee_id), 'emp_branch_id_id', None))]
        return Response({'rows': rows, 'can_edit': self.can(RUN_WRITE),
                         'total_held': str(PC.q2(sum((PC.q2(r['held_amount']) for r in rows if r['status'] in ('held', 'to_release')), PC.ZERO)))})

    def post(self, request):
        self.need(RUN_WRITE, 'You do not have the right to hold salaries.')
        data = dict(request.data)
        data['employee'] = self.emp_or_403(request.data.get('employee'))
        h = PC.create_hold(data, request.user)
        return Response(PC.hold_row(h), status=201)


class HoldView(_B):
    def post(self, request, pk, action):
        self.need(RUN_WRITE, 'You do not have the right to hold salaries.')
        from .models import SalaryHold
        h = SalaryHold.objects.filter(pk=pk).first()
        if h is None:
            return Response({'detail': tr('Not found.')}, status=404)
        self.emp_or_403(h.employee_id)
        if action == 'release':
            h = PC.release_hold(h, request.data, request.user)
        elif action == 'cancel':
            h = PC.cancel_hold(h, request.user, request.data.get('note') or '')
        else:
            return Response({'detail': tr('Not found.')}, status=404)
        return Response(PC.hold_row(h))


# ------------------------------------------------------------------ period locks
class LocksView(_B):
    def get(self, request):
        self.need(RUN_READ, 'You do not have the right to see payroll period locks.')
        from .models import PeriodLock
        rows = [PC.lock_row(x) for x in PeriodLock.objects.all()[:500] if x.branch_id is None or self.branch_ok(x.branch_id)]
        Br = M('OrganisationManager', 'brnch_mstr')
        brs = Br.objects.order_by('branch_name')
        if self.c.branches is not None:
            brs = brs.filter(pk__in=self.c.branches)
        return Response({'rows': rows, 'can_lock': self.can(RUN_WRITE), 'can_unlock': self.can(UNLOCK),
                         'branches': [{'id': b.pk, 'name': b.branch_name} for b in brs]})

    def post(self, request):
        self.need(RUN_WRITE, 'You do not have the right to lock payroll periods.')
        b = request.data.get('branch')
        if b not in (None, '', 'null', 0, '0'):
            try:
                if not self.branch_ok(int(b)):
                    raise PC.Refused(tr('This branch is not yours.'), status=403)
            except (TypeError, ValueError):
                pass
        elif self.c.branches is not None:
            raise PC.Refused(tr('Choose one of your branches – only a company administrator locks every branch.'), problems={'branch': tr('Choose the branch.')})
        lk = PC.lock_period(request.data, request.user)
        return Response(PC.lock_row(lk), status=201)


class LockView(_B):
    def post(self, request, pk, action):
        from .models import PeriodLock
        lk = PeriodLock.objects.filter(pk=pk).first()
        if lk is None or action != 'unlock':
            return Response({'detail': tr('Not found.')}, status=404)
        self.need(UNLOCK, 'Only HR with the right "Can unlock a locked payroll period" can unlock it.')
        if lk.branch_id is not None and not self.branch_ok(lk.branch_id):
            raise PC.Refused(tr('This branch is not yours.'), status=403)
        lk = PC.unlock_period(lk, request.data.get('reason'), request.user)
        return Response(PC.lock_row(lk))


# ------------------------------------------------------------------ revisions
class RevisionsView(_B):
    def get(self, request):
        from .models import SalaryRevisionRequest
        A = M('EmpManagement', 'Approval')
        mine = set(A.objects.filter(status='Pending', approver=request.user, general_request__remarks__startswith=PC.MARK)
                   .values_list('general_request_id', flat=True))
        full = self.can(SAL_READ)
        if not full and not mine:
            raise PC.Refused(tr('You do not have the right to see salary revisions.'), status=403)
        qs = SalaryRevisionRequest.objects.all()
        if not full:
            qs = qs.filter(general_request_id__in=mine)
        st = request.query_params.get('status')
        if st:
            qs = qs.filter(status=st)
        rs = list(qs[:1000])
        em = self.emps(r.employee_id for r in rs)
        rows = [PC.rev_row(r, em, request.user) for r in rs
                if r.general_request_id in mine or self.branch_ok(getattr(em.get(r.employee_id), 'emp_branch_id_id', None))]
        return Response({'rows': rows, 'can_edit': self.can(SAL_WRITE), 'request_type': PC.REQUEST_TYPE})

    def post(self, request):
        self.need(SAL_WRITE, 'You do not have the right to revise salaries.')
        data = dict(request.data)
        data['employee'] = self.emp_or_403(request.data.get('employee'))
        data['lines'] = request.data.get('lines') or []
        r = PC.create_revision(data, request.user)
        return Response(PC.rev_row(r, None, request.user), status=201)


class RevisionSalaryView(_B):
    def get(self, request):
        self.need(SAL_READ, 'You do not have the right to see salaries.')
        eid = self.emp_or_403(request.query_params.get('employee'))
        e = PC._emp(eid)
        SC = M('PayrollManagement', 'SalaryComponent')
        comps = SC.objects.filter(component_value_type='fixed').exclude(code=PC.RELEASE_CODE)
        if e and e.emp_branch_id_id:
            from django.db.models import Q
            comps = comps.filter(Q(branch_id=e.emp_branch_id_id) | Q(branch__isnull=True))
        return Response({'employee': PC.emp_label(e), 'rows': PC.current_salary(e),
                         'components': [{'id': c.pk, 'name': c.name, 'code': c.code or '', 'type': c.component_type} for c in comps.order_by('name')]})


class RevisionView(_B):
    def post(self, request, pk, action):
        from .models import SalaryRevisionRequest
        r = SalaryRevisionRequest.objects.filter(pk=pk).first()
        if r is None:
            return Response({'detail': tr('Not found.')}, status=404)
        if action in ('approve', 'reject'):
            r = PC.decide_revision(r, action == 'approve', request.user, request.data.get('note') or '')
        elif action == 'cancel':
            self.need(SAL_WRITE, 'You do not have the right to revise salaries.')
            self.emp_or_403(r.employee_id)
            r = PC.cancel_revision(r, request.user)
        else:
            return Response({'detail': tr('Not found.')}, status=404)
        return Response(PC.rev_row(r, None, request.user))


# ------------------------------------------------------------------ pay date, loan limit
class PayDateView(_B):
    def get(self, request):
        self.need(RUN_READ, 'You do not have the right to see payroll runs.')
        d = PC._parse_day(request.query_params.get('date'))
        if d is None:
            raise PC.Refused(tr('Enter the date.'), problems={'date': tr('Enter the date.')})
        brs = [int(x) for x in str(request.query_params.get('branches') or '').split(',') if x.strip().isdigit()]
        new, moved = PC.shifted_pay_date(d, brs)
        return Response({'date': d.isoformat(), 'pay_date': new.isoformat(), 'moved': [{'date': x.isoformat(), 'why': w} for x, w in moved],
                         'on': PC.settings().get('pay_date_shift')})


class LoanLimitView(_B):
    def get(self, request):
        eid = self.emp_or_403(request.query_params.get('employee'))
        e = PC._emp(eid)
        own = getattr(e, 'users_id', None) == request.user.pk
        if not own:
            self.need({'view_loanapplication', 'add_loanapplication', 'change_loanapplication'} | RUN_READ, 'You do not have the right to see loans.')
        lim = PC.loan_limit(e)
        return Response({k: (str(v) if not isinstance(v, (bool, str, type(None), int, float)) else v) for k, v in lim.items()})


# ------------------------------------------------------------------ re-hire
class RehireView(_B):
    def get(self, request):
        self.need(EMP_WRITE | SAL_READ, 'You do not have the right to re-hire employees.')
        rows = [r for r in PC.leavers() if self.branch_ok(r['branch_id'])]
        return Response({'rows': rows, 'can_edit': self.can(EMP_WRITE)})

    def post(self, request, pk):
        self.need(EMP_WRITE, 'You do not have the right to re-hire employees.')
        eid = self.emp_or_403(pk)
        e = M('EmpManagement', 'emp_master').objects.filter(pk=eid).first()
        return Response(PC.rehire(e, request.data, request.user))


# ------------------------------------------------------------------ revision history import
class ImportView(_B):
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request, what=None):
        self.need(SAL_WRITE, 'You do not have the right to import salary history.')
        resp = HttpResponse(PC.template_csv(), content_type='text/csv')
        resp['Content-Disposition'] = 'attachment; filename="salary_revision_history_template.csv"'
        return resp

    def post(self, request, what=None):
        self.need(SAL_WRITE, 'You do not have the right to import salary history.')
        f = request.FILES.get('file')
        if f is None:
            raise PC.Refused(tr('Choose the file.'), problems={'file': tr('Choose the file.')})
        apply = str(request.data.get('apply')).lower() in ('1', 'true', 'yes')
        allowed = None
        if self.c.branches is not None:
            E = M('EmpManagement', 'emp_master')
            allowed = set(E.objects.filter(emp_branch_id__in=self.c.branches).values_list('pk', flat=True))
        res = PC.import_history(f, f.name, apply=apply, user=request.user, allowed=allowed)
        return Response(res)
