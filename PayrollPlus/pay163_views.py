# v1.63.0 (items 27, 28): new module – API of the salary split and the recovery schedules, mounted at /payroll-plus/
"""v1.63.0 (items 27, 28).

Recoveries (item 28)
GET  POST api/recoveries/?status=&employee=            list / create {employee, component, total, start_month YYYY-MM, mode months|amount,
                                                        months, per_month, monthly_cap, exit_action deduct|write_off, reason}
POST api/recoveries/preview/                            the schedule of the form (nothing saved)
GET  api/recoveries/<id>/                               the recovery with its schedule, entries and history
POST api/recoveries/<id>/<action>/                      pause | resume | settle {mode paid|next_payroll, paid_on, reference} |
                                                        write-off {reason} | cancel | change {monthly_cap, exit_action} | approve | reject {note}
GET  api/recoveries/components/                         the deduction components (types of recovery)
GET  api/recoveries/import/template/                    Excel template; POST api/recoveries/import/ (multipart file, apply=0|1)
Salary split (item 27)
GET  POST api/pay-splits/?employee=                     the splits (history) / a new dated split {employee, effective_from, method, basis
                                                        amount|percent, value, bank_name, iban, account_number, routing_code, note}
POST api/pay-splits/<id>/cancel/                        cancel an entry
GET  api/pay-splits/methods/                            the methods of the second part
GET  PUT api/pay-splits/settings/                       {wps_split_edr: portion|full} (to be confirmed)
POST api/pay-splits/iban-check/                         {iban} → {ok, problem}
GET  api/pay-splits/import/template/                    Excel template; POST api/pay-splits/import/ (multipart file, apply=0|1)
"""
from django.apps import apps
from django.http import HttpResponse
from rest_framework.response import Response

from Translations.i18n import tr

from . import paysplit as PS
from . import recoveries as RC
from .payctl_views import RUN_READ, RUN_WRITE, SAL_READ, SET, _B

M = apps.get_model
REC_READ = RUN_READ | SAL_READ
REC_WRITE = RUN_WRITE | {'change_employeesalarystructure'}
SPLIT_READ = REC_READ | {'view_emp_master', 'change_emp_master', 'view_employeebankdetail', 'change_employeebankdetail'}
SPLIT_WRITE = {'change_emp_master', 'change_employeebankdetail', 'add_employeebankdetail', 'change_payrollrun'}
XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


class _V(_B):
    def handle_exception(self, exc):
        if isinstance(exc, (RC.Refused, PS.Refused)):
            return Response({**(exc.problems or {}), 'detail': exc.message}, status=exc.status)
        return super().handle_exception(exc)

    def allowed(self):
        if self.c.branches is None:
            return None
        return set(M('EmpManagement', 'emp_master').objects.filter(emp_branch_id__in=self.c.branches).values_list('pk', flat=True))


# ------------------------------------------------------------------ recoveries (item 28)
class RecoveriesView(_V):
    def get(self, request):
        self.need(REC_READ, 'You do not have the right to see recoveries.')
        from .models import Recovery
        qs = Recovery.objects.all()
        st = request.query_params.get('status')
        if st == 'open':
            qs = qs.filter(status__in=('pending', 'active', 'paused'))
        elif st:
            qs = qs.filter(status=st)
        emp = request.query_params.get('employee')
        if str(emp or '').isdigit():
            qs = qs.filter(employee_id=int(emp))
        rs = list(qs[:2000])
        em = self.emps(r.employee_id for r in rs)
        comps = {c.pk: c for c in M('PayrollManagement', 'SalaryComponent').objects.filter(pk__in={r.component_id for r in rs})}
        rows = [RC.row(r, em, comps, request.user) for r in rs if self.branch_ok(getattr(em.get(r.employee_id), 'emp_branch_id_id', None))]
        tot = {k: str(sum((RC.q2(x[k]) for x in rows if x['status'] in ('active', 'paused', 'pending')), RC.ZERO)) for k in ('total', 'recovered', 'balance')}
        return Response({'rows': rows, 'totals': tot, 'can_edit': self.can(REC_WRITE)})

    def post(self, request):
        self.need(REC_WRITE, 'You do not have the right to create recoveries.')
        data = dict(request.data)
        data['employee'] = self.emp_or_403(request.data.get('employee'))
        r = RC.create(data, request.user)
        return Response(RC.row(r, user=request.user, full=True), status=201)


class RecoveryPreviewView(_V):
    def post(self, request):
        self.need(REC_READ, 'You do not have the right to see recoveries.')
        data = dict(request.data)
        if data.get('employee'):
            data['employee'] = self.emp_or_403(data.get('employee'))
        return Response(RC.preview(data))


class RecoveryComponentsView(_V):
    def get(self, request):
        self.need(REC_READ, 'You do not have the right to see recoveries.')
        SC = M('PayrollManagement', 'SalaryComponent')
        rows = [{'id': c.pk, 'name': c.name, 'code': c.code or '', 'branch_id': c.branch_id,
                 'label': f'{c.name} ({c.code})' if c.code else c.name}
                for c in SC.objects.filter(component_type='deduction', show_in_payslip=True).order_by('name', 'pk')
                if c.branch_id is None or self.branch_ok(c.branch_id)]
        return Response(rows)


class RecoveryView(_V):
    def _get(self, pk):
        from .models import Recovery
        r = Recovery.objects.filter(pk=pk).first()
        if r is None:
            raise RC.Refused(tr('Not found.'), status=404)
        self.emp_or_403(r.employee_id)
        return r

    def get(self, request, pk):
        self.need(REC_READ, 'You do not have the right to see recoveries.')
        return Response(RC.row(self._get(pk), user=request.user, full=True))

    def post(self, request, pk, action):
        r = self._get(pk)
        d = request.data or {}
        if action in ('approve', 'reject'):
            r = RC.decide(r, action == 'approve', request.user, d.get('note') or '')
            return Response(RC.row(r, user=request.user, full=True))
        self.need(REC_WRITE, 'You do not have the right to change recoveries.')
        fn = {'pause': lambda: RC.pause(r, request.user, d.get('note') or ''), 'resume': lambda: RC.resume(r, request.user, d.get('note') or ''),
              'settle': lambda: RC.settle_early(r, d, request.user), 'write-off': lambda: RC.write_off(r, d.get('reason'), request.user),
              'cancel': lambda: RC.cancel(r, request.user, d.get('note') or ''), 'change': lambda: RC.change(r, d, request.user)}.get(action)
        if fn is None:
            return Response({'detail': tr('Not found.')}, status=404)
        r = fn()
        r.refresh_from_db()
        return Response(RC.row(r, user=request.user, full=True))


class RecoveryImportView(_V):
    def get(self, request, what=None):
        self.need(REC_READ, 'You do not have the right to see recoveries.')
        resp = HttpResponse(RC.template_xlsx(), content_type=XLSX)
        resp['Content-Disposition'] = 'attachment; filename="recoveries_template.xlsx"'
        return resp

    def post(self, request, what=None):
        self.need(REC_WRITE, 'You do not have the right to create recoveries.')
        f = request.FILES.get('file')
        if f is None:
            raise RC.Refused(tr('Choose the file.'), problems={'file': tr('Choose the file.')})
        apply = str(request.data.get('apply') or '0') in ('1', 'true', 'True')
        return Response(RC.import_rows(f, f.name, apply=apply, user=request.user, allowed=self.allowed()))


# ------------------------------------------------------------------ salary split (item 27)
class SplitsView(_V):
    def get(self, request):
        self.need(SPLIT_READ, 'You do not have the right to see salary splits.')
        from .models import PaySplit
        qs = PaySplit.objects.all()
        emp = request.query_params.get('employee')
        if str(emp or '').isdigit():
            self.emp_or_403(int(emp))
            qs = qs.filter(employee_id=int(emp))
        if request.query_params.get('active') == '1':
            qs = qs.filter(status='active')
        ss = list(qs[:3000])
        em = self.emps(s.employee_id for s in ss)
        rows = [PS.row(s, em) for s in ss if self.branch_ok(getattr(em.get(s.employee_id), 'emp_branch_id_id', None))]
        out = {'rows': rows, 'can_edit': self.can(SPLIT_WRITE), 'settings': PS.settings(), 'methods': PS.method_choices()}
        if str(emp or '').isdigit():
            out['main_method'] = PS.main_method(int(emp))
            out['main_method_label'] = PS.method_name(out['main_method'])
        return Response(out)

    def post(self, request):
        self.need(SPLIT_WRITE, 'You do not have the right to change salary splits.')
        emp = self.emp_or_403(request.data.get('employee'))
        s = PS.create(emp, request.data, request.user)
        return Response(PS.row(s), status=201)


class SplitView(_V):
    def post(self, request, pk, action):
        self.need(SPLIT_WRITE, 'You do not have the right to change salary splits.')
        from .models import PaySplit
        s = PaySplit.objects.filter(pk=pk).first()
        if s is None or action != 'cancel':
            return Response({'detail': tr('Not found.')}, status=404)
        self.emp_or_403(s.employee_id)
        return Response(PS.row(PS.cancel(s, request.user, (request.data or {}).get('note') or '')))


class SplitMethodsView(_V):
    def get(self, request):
        self.need(SPLIT_READ, 'You do not have the right to see salary splits.')
        return Response(PS.method_choices())


class SplitSettingsView(_V):
    def get(self, request):
        self.need(SPLIT_READ | SET, 'You do not have the right to see salary splits.')
        return Response({**PS.settings(), 'can_edit': self.can(SET)})

    def put(self, request):
        self.need(SET, 'You do not have the right to change the payroll controls.')
        return Response({**PS.save_settings(request.data), 'can_edit': True})


class IbanCheckView(_V):
    def post(self, request):
        why = PS.iban_problem((request.data or {}).get('iban'))
        return Response({'ok': not why, 'problem': why})


class SplitImportView(_V):
    def get(self, request, what=None):
        self.need(SPLIT_READ, 'You do not have the right to see salary splits.')
        resp = HttpResponse(PS.template_xlsx(), content_type=XLSX)
        resp['Content-Disposition'] = 'attachment; filename="salary_splits_template.xlsx"'
        return resp

    def post(self, request, what=None):
        self.need(SPLIT_WRITE, 'You do not have the right to change salary splits.')
        f = request.FILES.get('file')
        if f is None:
            raise PS.Refused(tr('Choose the file.'), problems={'file': tr('Choose the file.')})
        apply = str(request.data.get('apply') or '0') in ('1', 'true', 'True')
        return Response(PS.import_rows(f, f.name, apply=apply, user=request.user, allowed=self.allowed()))
