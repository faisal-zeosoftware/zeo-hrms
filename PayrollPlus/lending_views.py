# v1.67.11 (loans): new module
"""v1.67.11 (loans) – API of Payroll › Loan and advance settings and of the advance payout.

GET  api/lending/settings/                     the company's deduction limit, advance limits, settlement recovery, accounts (+ the law)
PUT  api/lending/settings/                     {deduction: {…}, loans: {…}, advances: {…}, settlement: {…}, accounts: {…}}
GET  api/lending/check/?kind=loan&employee=&loan_type=&amount=&months=     what would stop the loan (+ basic, wage, profit, instalment)
GET  api/lending/check/?kind=advance&employee=&amount=&instalments=        what would stop the advance
POST api/lending/advances/<id>/payout/         {date, ref} – the advance is paid out (journal)
"""
from django.apps import apps
from django.utils.dateparse import parse_date
from rest_framework.response import Response

from Translations.i18n import tr

from . import lending as LN
from . import services as S
from .views import Base

M = apps.get_model
SET_READ = {'change_payrollplussettings', 'view_payrollrun', 'change_payrollrun', 'add_payrollrun', 'change_loanapplication', 'view_loanapplication'}
LAW = [
    {'topic': 'deduction_limit', 'text': 'Decree-Law 33/2021 Art. 25(2): the deductions of a month may not exceed 50 % of the wage.'},
    {'topic': 'loan_interest', 'text': 'Decree-Law 33/2021 Art. 25(1)(a): a loan to the worker is repaid from the wage within that limit, '
                                       'with the worker\'s written consent and without interest.'},
    {'topic': 'settlement', 'text': 'Decree-Law 33/2021 Art. 51(7) and Cabinet Resolution 1/2022 Art. 29: amounts the worker owes may be '
                                    'deducted from the end-of-service benefits; no percentage is set – the recovery limit is the company\'s choice.'},
]


class SettingsView(Base):
    def get(self, request):
        if not self.can(SET_READ):
            raise S.Refused(tr('You may not see the loan and advance settings.'), status=403)
        return Response({**LN.settings(), 'uae': LN.uae(), 'law': [{**x, 'text': tr(x['text'])} for x in LAW],
                         'can_edit': bool(self.c.admin or 'change_payrollplussettings' in self.c.codes)})

    def put(self, request):
        if not (self.c.admin or 'change_payrollplussettings' in self.c.codes):
            raise S.Refused(tr('Only the company administrator can change the loan and advance settings.'), status=403)
        try:
            LN.save_settings(request.data or {})
        except LN.LendError as e:
            raise S.Refused(e.message, problems=e.problems)
        return self.get(request)

    patch = put


class CheckView(Base):
    def get(self, request):
        q = request.query_params
        E = M('EmpManagement', 'emp_master')
        emp = E.objects.filter(pk=q.get('employee') or 0).first()
        mine = bool(self.c.emp and emp is not None and emp.pk == self.c.emp.pk)
        if emp is None or not (mine or (self.can({'add_loanapplication', 'change_loanapplication', 'add_advancesalaryrequest',
                                                   'change_advancesalaryrequest'}) and self.emp_ok(emp.pk))):
            raise S.Refused(tr('Employee not found.'), status=404)
        basic, wage = LN.basic_wage(emp)
        out = {'basic': str(basic), 'wage': str(wage), 'leaving': LN.leaving(emp), 'service_months': LN.service_months(emp, LN.timezone.localdate())}
        if q.get('kind') == 'advance':
            out['problems'] = LN.check_advance(emp, q.get('amount') or 0, q.get('instalments') or 1)
            out['settings'] = LN.settings()['advances']
            return Response(out)
        lt = M('PayrollManagement', 'LoanType').objects.filter(pk=q.get('loan_type') or 0).first()
        if lt is None:
            raise S.Refused(tr('Choose the loan type.'), status=400)
        months = int(q.get('months') or lt.repayment_period or 1)
        amount = LN.q2(q.get('amount') or 0)
        r = LN.rule_of(lt.pk)
        pr = LN.profit(r, amount, months)
        out.update({'problems': LN.check_loan(emp, lt, amount, months, q.get('emi'), guarantor_id=q.get('guarantor')), 'rule': LN.rule_dict(lt.pk),
                    'max_amount': str(LN.q2(lt.max_amount)), 'max_months': lt.repayment_period, 'profit': str(pr),
                    'total': str(amount + pr), 'instalment': str(LN.q2((amount + pr) / months)) if months else '0.00'})
        return Response(out)


class AdvanceView(Base):
    def post(self, request, pk, action):
        AS = M('PayrollManagement', 'AdvanceSalaryRequest')
        a = AS.objects.select_related('employee').filter(pk=pk).first()
        if a is None or not self.emp_ok(a.employee_id):
            raise S.Refused(tr('Advance not found.'), status=404)
        if not self.can({'change_advancesalaryrequest'}):
            raise S.Refused(tr('Only payroll pays out advances.'), status=403)
        if action != 'payout':
            raise S.Refused(tr('Unknown action.'), status=404)
        try:
            LN.payout_advance(a, parse_date(str(request.data.get('date') or '')), request.data.get('ref') or '', request.user)
        except LN.LendError as e:
            raise S.Refused(e.message, status=e.status, problems=e.problems)
        return Response({'id': a.pk, 'payout': LN.adv_terms(a.pk).payout})
