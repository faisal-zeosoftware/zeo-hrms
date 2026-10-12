"""
v1.18.0 – PayrollPlus API, mounted at /payroll-plus/.

GET  api/settings/                         the controls (encashment chain, segregation, deadlines)      PUT (company admin / HR manager)
GET  api/runs/<run_id>/control/            who prepared / approved / paid the run + its warnings (WPS deadline, cut-off)
GET  api/encashments/<id>/steps/           the approval steps of a leave encashment                     POST {step: hr|finance, note}
GET  api/settlements/?status=              final settlements (HR, Finance, management)
POST api/settlements/                      {eos} – start (calculate) the settlement of an end of service
GET  api/settlements/<id>/                 the statement (every component with its basis, the approval trail)
POST api/settlements/<id>/recalculate/     draft: calculate again from the current records ({notice_waived})
POST api/settlements/<id>/<action>/        submit (HR) → validate (Finance) → approve (management) → pay (Finance, payment_ref)
                                           | return (back to HR, note) | cancel
GET  api/settlements/eligible/             ends of service without a settlement yet
"""
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from django.apps import apps

from AccessControl.access import ctx, run_in_branches, deny_branch  # v1.22.0 branch rights
from Translations.i18n import tr

from . import services as S

M = apps.get_model
READ = {'view_exitsettlement', 'add_exitsettlement', 'change_exitsettlement', 'validate_exitsettlement', 'approve_exitsettlement', 'pay_exitsettlement',
        'view_endofservice', 'change_endofservice'}


class Base(APIView):
    permission_classes = [IsAuthenticated]
    zeo_access = False
    zeo_scope = False

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.c = ctx(request)

    def handle_exception(self, exc):
        if isinstance(exc, S.Refused):
            body = {'detail': exc.message}
            if exc.problems:
                body['errors'] = exc.problems
            return Response(body, status=exc.status)
        return super().handle_exception(exc)

    def can(self, codes):
        return self.c.admin or bool(set(codes) & self.c.codes)

    def emp_ok(self, emp_id):
        if self.c.branches is None:
            return True
        E = M('EmpManagement', 'emp_master')
        return E.objects.filter(pk=emp_id, emp_branch_id__in=self.c.branches).exists()


class SettingsView(Base):
    def get(self, request):
        s = S.settings()
        # v1.20.2: WPS establishment per branch (employer ID + bank routing code); empty = the company's
        Br = M('OrganisationManager', 'brnch_mstr'); BW = M('PayrollPlus', 'BranchWPS')
        bw = {x.branch_id: x for x in BW.objects.all()}
        brs = Br.objects.order_by('branch_name')
        if self.c.branches is not None:   # v1.22.0 branch rights: only the user's branches' WPS rows
            brs = brs.filter(pk__in=self.c.branches)
        wps = [{'branch': b.pk, 'branch_name': b.branch_name, 'employer_unique_id': getattr(bw.get(b.pk), 'employer_unique_id', ''),
                'bank_routing_code': getattr(bw.get(b.pk), 'bank_routing_code', '')} for b in brs]
        return Response({'branch_wps': wps, 'encashment_chain': s.encashment_chain, 'segregation': s.segregation, 'wps_late_days': s.wps_late_days,
                         'settlement_due_days': s.settlement_due_days, 'part_month': s.part_month, 'wps_period': s.wps_period, 'can_edit': self.can({'change_payrollplussettings'})})

    def put(self, request):
        # the payroll team must not be able to switch off the controls on its own work – only the company administrator or
        # a group given "Can change payroll plus settings"
        if not (self.c.admin or 'change_payrollplussettings' in self.c.codes):
            raise S.Refused(tr('Only the company administrator can change the payroll controls.'), status=403)
        s = S.settings()
        d = request.data
        for f in ('encashment_chain', 'segregation'):
            if f in d:
                setattr(s, f, str(d[f]).lower() in ('1', 'true', 'yes', 'on'))
        if 'wps_period' in d:   # v1.20.2
            if d['wps_period'] not in ('month', 'attendance'):
                raise S.Refused(tr('Choose the pay period of the WPS file.'), problems={'wps_period': 'Invalid.'})
            s.wps_period = d['wps_period']
        if 'part_month' in d:   # v1.20.2
            if d['part_month'] not in ('share', 'days'):
                raise S.Refused(tr('Choose how part months are paid.'), problems={'part_month': 'Invalid.'})
            s.part_month = d['part_month']
        for f in ('wps_late_days', 'settlement_due_days'):
            if f in d:
                try:
                    v = int(d[f])
                    if not 0 <= v <= 60:
                        raise ValueError
                    setattr(s, f, v)
                except (TypeError, ValueError):
                    raise S.Refused(tr('Enter a number of days between 0 and 60.'), problems={f: 'Invalid.'})
        if isinstance(d.get('branch_wps'), list):   # v1.20.2
            import re as _re
            BW = M('PayrollPlus', 'BranchWPS')
            if self.c.branches is not None:   # v1.22.0 branch rights: WPS rows of other branches cannot be written
                allowed = {str(b) for b in self.c.branches}
                if any(str(row.get('branch')) not in allowed for row in d['branch_wps'] if isinstance(row, dict)):
                    deny_branch()
            for row in d['branch_wps']:
                eid, rc = str(row.get('employer_unique_id') or '').strip(), str(row.get('bank_routing_code') or '').strip()
                if eid and not _re.fullmatch(r'\d{13}', eid) or rc and not _re.fullmatch(r'\d{9}', rc):
                    raise S.Refused(tr('The WPS employer ID has 13 digits and the routing code 9 digits.'), problems={'branch_wps': 'Invalid.'})
                if eid or rc:
                    BW.objects.update_or_create(branch_id=int(row['branch']), defaults={'employer_unique_id': eid, 'bank_routing_code': rc})
                else:
                    BW.objects.filter(branch_id=int(row['branch'])).delete()
        s.save()
        return self.get(request)

    patch = put


class RunControlView(Base):
    def get(self, request, run_id):
        PR = M('PayrollManagement', 'PayrollRun')
        run = PR.objects.filter(pk=run_id).first()
        if run is None or not self.can({'view_payrollrun', 'change_payrollrun', 'view_payslip'}) or not run_in_branches(self.c.branches, run):  # v1.22.0 branch rights
            raise S.Refused(tr('Not found.'), status=404)
        return Response(S.run_control_row(run))


class EncashmentStepsView(Base):
    def _get(self, pk):
        LE = M('PayrollManagement', 'LeaveEncashment')
        e = LE.objects.select_related('employee').filter(pk=pk).first()
        if e is None or not self.emp_ok(e.employee_id) or not self.can({'view_leaveencashment', 'change_leaveencashment', 'add_leaveencashment',
                                                                         'finance_check_leaveencashment', 'approve_leaveencashment_final'}):
            raise S.Refused(tr('Encashment not found.'), status=404)
        return e

    def get(self, request, pk):
        e = self._get(pk)
        return Response(S.encashment_steps_row(e.pk))

    def post(self, request, pk):
        e = self._get(pk)
        S.do_step(e, request.data.get('step'), request.user, self.c.codes, self.c.admin, request.data.get('note', ''))
        return Response(S.encashment_steps_row(e.pk))


class SettlementsView(Base):
    def get(self, request):
        if not self.can(READ):
            raise S.Refused(tr('You may not see final settlements.'), status=403)
        X = M('PayrollPlus', 'ExitSettlement')
        qs = X.objects.all()
        if request.GET.get('status'):
            qs = qs.filter(status__in=request.GET['status'].split(','))
        if self.c.branches is not None:   # v1.22.0 branch rights: filter before the 500-row limit
            E = M('EmpManagement', 'emp_master')
            qs = qs.filter(employee_id__in=E.objects.filter(emp_branch_id__in=self.c.branches).values('id'))
        rows = [S.settlement_row(x) for x in qs[:500] if self.emp_ok(x.employee_id)]
        return Response({'rows': rows, 'can_start': self.can({'add_exitsettlement', 'change_exitsettlement'})})

    def post(self, request):
        if not self.can({'add_exitsettlement', 'change_exitsettlement'}):
            raise S.Refused(tr('You may not start a final settlement.'), status=403)
        EOS = M('EmpManagement', 'EndOfService')
        eos = EOS.objects.select_related('resignation__employee').filter(pk=request.data.get('eos')).first()
        if eos is None or not self.emp_ok(eos.resignation.employee_id):
            raise S.Refused(tr('End of service not found.'), status=404)
        st = S.start(eos, request.user)
        return Response(S.settlement_row(st, full=True), status=201)


class EligibleView(Base):
    def get(self, request):
        if not self.can(READ):
            raise S.Refused(tr('You may not see final settlements.'), status=403)
        EOS = M('EmpManagement', 'EndOfService')
        X = M('PayrollPlus', 'ExitSettlement')
        have = set(X.objects.values_list('eos_id', flat=True))
        out = []
        for e in EOS.objects.select_related('resignation__employee').order_by('-last_working_date'):
            emp = e.resignation.employee
            if e.pk in have or not self.emp_ok(emp.pk):
                continue
            out.append({'eos': e.pk, 'employee_id': emp.pk, 'employee': f'{emp.emp_first_name} {emp.emp_last_name or ""}'.strip(), 'employee_code': emp.emp_code,
                        'last_working_date': e.last_working_date, 'exit_type': e.resignation.get_termination_type_display(), 'gratuity': float(e.gratuity_amount or 0)})
        return Response({'rows': out})


class SettlementView(Base):
    def _get(self, pk):
        X = M('PayrollPlus', 'ExitSettlement')
        st = X.objects.filter(pk=pk).first()
        if st is None or not self.emp_ok(st.employee_id) or not self.can(READ):
            raise S.Refused(tr('Final settlement not found.'), status=404)
        return st

    def get(self, request, pk, action=None):
        return Response(S.settlement_row(self._get(pk), full=True))

    def post(self, request, pk, action=None):
        st = self._get(pk)
        if action == 'recalculate':
            if st.status != 'draft':
                raise S.Refused(tr('Only a draft settlement can be calculated again – send it back to HR first.'), status=409)
            if not self.can({'add_exitsettlement', 'change_exitsettlement'}):
                raise S.Refused(tr('You may not change this settlement.'), status=403)
            if 'notice_waived' in request.data:
                st.notice_waived = str(request.data.get('notice_waived')).lower() in ('1', 'true', 'yes', 'on')
            S.compute(st, request.user)
        else:
            S.act(st, action, request.user, self.c.codes, self.c.admin, request.data)
        st.refresh_from_db()
        return Response(S.settlement_row(st, full=True))
