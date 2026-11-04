# v1.62.11 (item 18b): new module – API of Payroll › Monthly inputs and of the bulk opening balances (accrue now – pay later)
"""v1.62.11 (item 18b) – mounted at /payroll-plus/.

GET  api/monthly-inputs/?month=YYYY-MM&branch=<id>     grid: columns (input components), rows (employees, figures, state), month state
POST api/monthly-inputs/                               {month, branch, rows: [{employee, values: {CODE: number}}]} – all or nothing
POST api/monthly-inputs/preview/                       {month, rows: [{employee, values}]} → {employee id: {lines, total}}
GET  api/monthly-inputs/template/?month=&branch=       Excel template (employee_code, input_code, value)
POST api/monthly-inputs/import/                        multipart file, month, branch, apply=0|1 – dry run / apply
GET  api/monthly-inputs/components/                    every component that can be a monthly input, with its flag
GET|PUT api/monthly-inputs/components/<id>/            {monthly_input: true|false|null (automatic)}
GET  api/accruals/opening/template/                    Excel template of the opening balances
POST api/accruals/opening/import/                      multipart file, apply=0|1
GET|PUT api/accruals/opening/grid/<component id>/      the opening balances of one component; PUT {rows: [{employee, amount, as_of}]}
"""
from django.apps import apps
from django.http import HttpResponse
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from Translations.i18n import tr

from . import accrual_openings as AO
from . import accruals as A
from . import monthly_inputs as MI
from . import services as S
from .views import Base

M = apps.get_model
READ = {'view_payrollrun', 'change_payrollrun', 'add_payrollrun', 'view_employeesalarystructure', 'change_employeesalarystructure'}
WRITE = {'change_employeesalarystructure', 'change_payrollrun', 'add_payrollrun'}
FLAG = {'change_salarycomponent'}
OPEN = {'change_payrollrun', 'add_payrollrun', 'change_employeesalarystructure'}   # as accrual_views.OPEN (1.62.7)
XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def _re(ex):
    raise S.Refused(ex.message, status=ex.status, problems=ex.problems)


class _B(Base):
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def need(self, codes, msg):
        if not self.can(codes):
            raise S.Refused(tr(msg), status=403)

    def allowed(self):
        if self.c.branches is None:
            return None
        E = M('EmpManagement', 'emp_master')
        return set(E.objects.filter(emp_branch_id__in=self.c.branches).values_list('pk', flat=True))

    def branches(self):
        B = M('OrganisationManager', 'brnch_mstr')
        qs = B.objects.all().order_by('branch_name')
        if self.c.branches is not None:
            qs = qs.filter(pk__in=self.c.branches)
        return [{'id': b.pk, 'name': b.branch_name} for b in qs]

    def branch(self, v):
        bs = self.branches()
        try:
            bid = int(v)
        except (TypeError, ValueError):
            bid = bs[0]['id'] if bs else None
        if bid is None or bid not in {b['id'] for b in bs}:
            raise S.Refused(tr('Choose a branch you have access to.'), status=403 if bid else 400)
        return bid

    def month(self, v, branch_id=None):
        m = MI.parse_month(v)
        if m is None:
            if branch_id is None:
                raise S.Refused(tr('Choose the payroll month.'), problems={'month': tr('Choose the payroll month.')})
            return MI.parse_month(MI.months_offered(branch_id)[1])
        return m


class MonthlyInputsView(_B):
    def get(self, request):
        self.need(READ, 'You do not have the right to see monthly inputs.')
        q = request.query_params
        bid = self.branch(q.get('branch'))
        m = self.month(q.get('month'), bid)
        months, default = MI.months_offered(bid)
        out = MI.grid(m, bid, self.allowed())
        out.update({'branches': self.branches(), 'branch': bid, 'months': months, 'default_month': default,
                    'can_edit': self.can(WRITE), 'can_flag': self.can(FLAG)})
        return Response(out)

    def post(self, request):
        self.need(WRITE, 'You do not have the right to enter monthly inputs.')
        d = request.data
        bid = self.branch(d.get('branch'))
        m = self.month(d.get('month'))
        rows = d.get('rows') or []
        if not isinstance(rows, list) or not rows:
            raise S.Refused(tr('Nothing to save.'))
        try:
            return Response(MI.save_grid(m, bid, rows, self.allowed(), request.user))
        except MI.Refused as ex:
            _re(ex)


class MonthlyInputsPreviewView(_B):
    def post(self, request):
        self.need(READ, 'You do not have the right to see monthly inputs.')
        d = request.data
        m = self.month(d.get('month'))
        E = M('EmpManagement', 'emp_master')
        allowed = self.allowed()
        out = {}
        from PayrollManagement.perf import payroll_cache
        with payroll_cache():
            for r in (d.get('rows') or [])[:500]:
                e = E.objects.filter(pk=r.get('employee') if str(r.get('employee') or '').isdigit() else 0).first()
                if e is None or (allowed is not None and e.pk not in allowed):
                    continue
                try:
                    out[str(e.pk)] = MI.preview(m, e, r.get('values') or {})
                except Exception as ex:   # a broken formula of one employee never stops the others
                    out[str(e.pk)] = {'lines': [], 'total': '0.00', 'error': str(ex)[:200]}
        return Response(out)


class MonthlyInputsTemplateView(_B):
    def get(self, request):
        self.need(WRITE, 'You do not have the right to enter monthly inputs.')
        q = request.query_params
        bid = self.branch(q.get('branch'))
        m = self.month(q.get('month'), bid)
        resp = HttpResponse(MI.template_xlsx(m, bid, self.allowed()), content_type=XLSX)
        resp['Content-Disposition'] = f'attachment; filename="monthly_inputs_{m:%Y_%m}.xlsx"'
        return resp


class MonthlyInputsImportView(_B):
    def post(self, request):
        self.need(WRITE, 'You do not have the right to enter monthly inputs.')
        f = request.FILES.get('file')
        if f is None:
            raise S.Refused(tr('Choose the file.'), problems={'file': tr('Choose the file.')})
        d = request.data
        m = self.month(d.get('month'))
        bid = self.branch(d.get('branch')) if d.get('branch') not in (None, '', 'all') else None
        apply = str(d.get('apply')).lower() in ('1', 'true', 'yes')
        try:
            return Response(MI.import_file(f, f.name, m, bid, apply=apply, allowed=self.allowed(), user=request.user))
        except MI.Refused as ex:
            _re(ex)


class MonthlyInputComponentsView(_B):
    def get(self, request):
        self.need(READ | {'view_salarycomponent', 'change_salarycomponent'}, 'You do not have the right to see salary components.')
        return Response({'rows': MI.candidates(), 'can_flag': self.can(FLAG)})


class MonthlyInputComponentView(_B):
    def _comp(self, pk):
        c = M('PayrollManagement', 'SalaryComponent').objects.filter(pk=pk).first()
        if c is None:
            raise S.Refused(tr('Not found.'), status=404)
        return c

    def get(self, request, pk):
        self.need(READ | {'view_salarycomponent', 'change_salarycomponent'}, 'You do not have the right to see salary components.')
        return Response(MI.component_flag(self._comp(pk)))

    def put(self, request, pk):
        self.need(FLAG, 'You do not have the right to change salary components.')
        v = request.data.get('monthly_input')
        on = None if v in (None, '', 'auto', 'null') else str(v).lower() in ('1', 'true', 'yes', 'on')
        try:
            return Response(MI.save_flag(self._comp(pk), on, request.user))
        except MI.Refused as ex:
            _re(ex)

    post = put


# ------------------------------------------------------------------ opening balances in bulk (accrue now – pay later)
class OpeningTemplateView(_B):
    def get(self, request):
        self.need(OPEN, 'You do not have the right to enter opening balances.')
        resp = HttpResponse(AO.template_xlsx(), content_type=XLSX)
        resp['Content-Disposition'] = 'attachment; filename="accrued_opening_balances.xlsx"'
        return resp


class OpeningImportView(_B):
    def post(self, request):
        self.need(OPEN, 'You do not have the right to enter opening balances.')
        f = request.FILES.get('file')
        if f is None:
            raise S.Refused(tr('Choose the file.'), problems={'file': tr('Choose the file.')})
        apply = str(request.data.get('apply')).lower() in ('1', 'true', 'yes')
        try:
            return Response(AO.import_file(f, f.name, apply=apply, allowed=self.allowed(), user=request.user))
        except A.Refused as ex:
            _re(ex)


class OpeningGridView(_B):
    def _comp(self, pk):
        c = M('PayrollManagement', 'SalaryComponent').objects.filter(pk=pk).first()
        if c is None:
            raise S.Refused(tr('Not found.'), status=404)
        return c

    def get(self, request, pk):
        self.need(OPEN | {'view_payrollrun', 'view_salarycomponent', 'change_salarycomponent', 'view_payslip'}, 'You do not have the right to see accrued components.')
        return Response(AO.grid(self._comp(pk), self.allowed()))

    def put(self, request, pk):
        self.need(OPEN, 'You do not have the right to enter opening balances.')
        rows = request.data.get('rows') or []
        try:
            return Response(AO.save_grid(self._comp(pk), rows, self.allowed(), request.user))
        except A.Refused as ex:
            _re(ex)

    post = put
