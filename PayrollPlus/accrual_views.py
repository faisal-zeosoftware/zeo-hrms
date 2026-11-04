# v1.62.7 (item 18): new module – accrue now, pay later
"""v1.62.7 (item 18) – API of "Accrue now – pay later" (Payroll › Accounting › Accrue now – pay later), mounted at /payroll-plus/.

GET    api/accruals/                         the salary components that can be accrued, with their setting
GET    api/accruals/components/<id>/         the setting of one component               PUT {enabled, payout_month, include_payout_month,
                                                                                            pay_at_exit, accrual_account}
GET    api/accruals/balances/?from=&to=      per employee and component: opening, accrued, paid by payroll, paid at exit, balance
GET    api/accruals/entries/?employee=&component=   the movements of one balance
POST   api/accruals/opening/                 {employee, component, amount, as_of, note} – the go-live opening balance (0 removes it)
"""
from django.apps import apps
from django.utils.dateparse import parse_date
from rest_framework.response import Response

from Translations.i18n import tr

from . import accruals as A
from . import services as S
from .views import Base

M = apps.get_model
READ = {'view_payrollrun', 'change_payrollrun', 'view_salarycomponent', 'change_salarycomponent', 'view_payslip'}
SET = {'change_salarycomponent'}
OPEN = {'change_payrollrun', 'add_payrollrun', 'change_employeesalarystructure'}
MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']


def _refuse(ex):
    raise S.Refused(ex.message, status=ex.status, problems=ex.problems)


class _B(Base):
    def need(self, codes, msg):
        if not self.can(codes):
            raise S.Refused(tr(msg), status=403)

    def emp_ids(self):
        E = M('EmpManagement', 'emp_master')
        qs = E.objects.all()
        if self.c.branches is not None:
            qs = qs.filter(emp_branch_id__in=self.c.branches)
        return set(qs.values_list('pk', flat=True))


class AccrualsView(_B):
    def get(self, request):
        self.need(READ, 'You do not have the right to see accrued components.')
        SC = M('PayrollManagement', 'SalaryComponent')
        ESS = M('PayrollManagement', 'EmployeeSalaryStructure')
        sets = A.all_settings()
        pay = A.payout_ids()
        groups = {}
        for c in SC.objects.select_related('branch').order_by('name', 'branch_id'):
            s = sets.get(c.pk)
            typ = (s.previous_type if s and s.previous_type else c.component_type)
            if c.pk in pay or typ == 'deduction' or c.payroll_category in A.NOT_ACCRUABLE:
                continue
            k = A._gkey(c)
            g = groups.get(k)
            if g is None:
                g = groups[k] = A.setting_row(c, s)
                g.update({'branches': [], 'employees': 0, 'formula': c.formula or '', 'value_type': c.component_value_type,
                          'type_label': tr(dict(SC.COMPONENT_TYPES).get(typ, typ))})
            g['branches'].append(c.branch.branch_name if c.branch_id else tr('All branches'))
            g['employees'] += ESS.objects.filter(component=c, is_active=True, employee__is_active=True).count()
        rows = sorted(groups.values(), key=lambda r: (not r['enabled'], r['name'].lower()))
        return Response({'rows': rows, 'months': [{'value': i + 1, 'label': tr(m)} for i, m in enumerate(MONTHS)],
                         'default_account': A.DEFAULT_ACCOUNT, 'can_set': self.can(SET), 'can_opening': self.can(OPEN)})


class AccrualComponentView(_B):
    def _comp(self, pk):
        c = M('PayrollManagement', 'SalaryComponent').objects.filter(pk=pk).first()
        if c is None:
            raise S.Refused(tr('Not found.'), status=404)
        return c

    def get(self, request, pk):
        self.need(READ, 'You do not have the right to see accrued components.')
        return Response(A.setting_row(self._comp(pk)))

    def put(self, request, pk):
        self.need(SET, 'You do not have the right to change salary components.')
        try:
            return Response(A.save_setting(self._comp(pk), request.data, request.user))
        except A.Refused as ex:
            _refuse(ex)

    post = put


class AccrualBalancesView(_B):
    def get(self, request):
        self.need(READ, 'You do not have the right to see accrued components.')
        q = request.query_params
        d_from, d_to = parse_date(q.get('from') or '') or None, parse_date(q.get('to') or '') or None
        ids = self.emp_ids()
        rows = A.summary(d_from, d_to, ids)
        E = M('EmpManagement', 'emp_master')
        emps = {e.pk: e for e in E.objects.filter(pk__in={r['employee_id'] for r in rows}).select_related('emp_branch_id', 'emp_dept_id')}
        comp = q.get('component')
        out = []
        for r in rows:
            e = emps.get(r['employee_id'])
            if e is None or (comp and str(comp) not in (str(r['component_id']), r['group'])):
                continue
            out.append({**{k: (str(v) if k in ('opening', 'accrued', 'paid', 'paid_exit', 'adjust', 'balance') else v) for k, v in r.items()},
                        'emp_code': e.emp_code, 'employee': ' '.join(x for x in (e.emp_first_name, e.emp_last_name) if x),
                        'branch': e.emp_branch_id.branch_name if e.emp_branch_id_id else '', 'active': bool(e.is_active)})
        out.sort(key=lambda r: (r['component'].lower(), r['emp_code'] or ''))
        tot = {k: str(sum((A.q2(r[k]) for r in out), A.ZERO)) for k in ('opening', 'accrued', 'paid', 'paid_exit', 'balance')}
        return Response({'rows': out, 'totals': tot, 'from': d_from, 'to': d_to})


class AccrualEntriesView(_B):
    def get(self, request):
        self.need(READ, 'You do not have the right to see accrued components.')
        q = request.query_params
        try:
            emp = int(q.get('employee'))
            comp = M('PayrollManagement', 'SalaryComponent').objects.get(pk=int(q.get('component')))
        except Exception:
            raise S.Refused(tr('Choose the employee and the component.'))
        if emp not in self.emp_ids():
            raise S.Refused(tr('You do not have access to this employee.'), status=403)
        ids = [c.pk for c in A.twins(comp)]
        PR = M('PayrollManagement', 'PayrollRun')
        rows, bal = [], A.ZERO
        rej = set(M('PayrollManagement', 'Payslip').objects.filter(status__iexact='rejected').values_list('pk', flat=True))
        for x in A.AccrualEntry.objects.filter(employee_id=emp, component_id__in=ids).order_by('month', 'id'):
            counts = x.payslip_id not in rej
            if counts:
                bal += x.amount
            run = PR.objects.filter(pk=x.run_id).first() if x.run_id else None
            rows.append({'id': x.id, 'kind': x.kind, 'kind_label': tr(x.get_kind_display()), 'month': x.month, 'amount': str(x.amount),
                         'balance': str(A.q2(bal)), 'counts': counts, 'note': x.note, 'run': (run.name or f'{run.month:02d}/{run.year}') if run else '',
                         'run_id': x.run_id, 'settlement_id': x.settlement_id})
        return Response({'rows': rows, 'balance': str(A.q2(bal))})


class AccrualOpeningView(_B):
    def post(self, request):
        self.need(OPEN, 'You do not have the right to enter opening balances.')
        d = request.data
        problems = {}
        try:
            emp = int(d.get('employee'))
        except (TypeError, ValueError):
            emp = None
            problems['employee'] = tr('Choose the employee.')
        comp = M('PayrollManagement', 'SalaryComponent').objects.filter(pk=d.get('component') or 0).first() if str(d.get('component') or '').isdigit() else None
        if comp is None:
            problems['component'] = tr('Choose the component.')
        as_of = parse_date(str(d.get('as_of') or ''))
        if as_of is None:
            problems['as_of'] = tr('Enter the date of the balance.')
        try:
            amount = A.q2(d.get('amount'))
        except Exception:
            amount = None
            problems['amount'] = tr('Enter the amount.')
        if problems:
            raise S.Refused(tr('Check the highlighted fields.'), problems=problems)
        if emp not in self.emp_ids():
            raise S.Refused(tr('You do not have access to this employee.'), status=403)
        try:
            x = A.set_opening(emp, comp, amount, as_of, d.get('note') or '', request.user)
        except A.Refused as ex:
            _refuse(ex)
        return Response({'id': x.pk if x else None, 'balance': str(A.balance(emp, [c.pk for c in A.twins(comp)]))}, status=201 if x else 200)
