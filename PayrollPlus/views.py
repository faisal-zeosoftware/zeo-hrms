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
from rest_framework.decorators import action
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
                         'settlement_due_days': s.settlement_due_days, 'part_month': s.part_month, 'wps_period': s.wps_period,
                         'payslip_language': s.payslip_language,
                         'pay_weekly': s.pay_weekly, 'pay_biweekly': s.pay_biweekly, 'pay_semimonthly': s.pay_semimonthly,   # v1.55.0
                         'can_edit': self.can({'change_payrollplussettings'})})

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
        # v1.55.0: weekly / bi-weekly / semi-monthly pay – off until switched on; cannot be switched off while a calendar uses it
        for freq, f in (('weekly', 'pay_weekly'), ('biweekly', 'pay_biweekly'), ('semimonthly', 'pay_semimonthly')):
            if f in d:
                on = str(d[f]).lower() in ('1', 'true', 'yes', 'on')
                if not on and getattr(s, f):
                    from .models import PayCalendar
                    used = list(PayCalendar.objects.filter(active=True, frequency=freq).values_list('name', flat=True))
                    if used:
                        raise S.Refused(tr('This pay frequency is used by the calendar {0} – deactivate the calendar first.', ', '.join(used)),
                                        problems={f: 'In use.'})
                setattr(s, f, on)
        if 'wps_period' in d:   # v1.20.2
            if d['wps_period'] not in ('month', 'attendance'):
                raise S.Refused(tr('Choose the pay period of the WPS file.'), problems={'wps_period': 'Invalid.'})
            s.wps_period = d['wps_period']
        if 'payslip_language' in d:   # v1.50.0
            if d['payslip_language'] not in ('reader', 'en', 'en_ar'):
                raise S.Refused(tr('Choose the language of the payslip.'), problems={'payslip_language': 'Invalid.'})
            s.payslip_language = d['payslip_language']
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
    from .models import ExitSettlement as zeo_model   # v1.62.0 EOS: a data load can date the start of a settlement

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
    from .models import ExitSettlement as zeo_model   # v1.62.0 EOS: data loads stamp the business date of a step

    def _get(self, pk):
        X = M('PayrollPlus', 'ExitSettlement')
        st = X.objects.filter(pk=pk).first()
        if st is None or not self.emp_ok(st.employee_id) or not self.can(READ):
            raise S.Refused(tr('Final settlement not found.'), status=404)
        return st

    def get(self, request, pk, action=None, line_id=None):
        st = self._get(pk)
        if action == 'pdf':   # v1.62.0 EOS: the statement on the letterhead
            return _pdf_response(_case_of(st, self.c, request.user))
        return Response(S.settlement_row(st, full=True))

    def post(self, request, pk, action=None, line_id=None):
        st = self._get(pk)
        hr = {'add_exitsettlement', 'change_exitsettlement'}
        if action == 'recalculate':
            if st.status != 'draft':
                raise S.Refused(tr('Only a draft settlement can be calculated again – send it back to HR first.'), status=409)
            if not self.can(hr):
                raise S.Refused(tr('You may not change this settlement.'), status=403)
            if 'notice_waived' in request.data:
                was = st.notice_waived
                st.notice_waived = str(request.data.get('notice_waived')).lower() in ('1', 'true', 'yes', 'on')
                if was != st.notice_waived:   # v1.62.0 EOS
                    S.log_step(st, 'notice_waived', request.user, tr('Notice not served: waived') if st.notice_waived else tr('Notice not served: deducted'))
            S.compute(st, request.user)
        elif action in ('lines', 'assets'):   # v1.62.0 EOS: HR's other lines / asset decisions
            if not self.can(hr):
                raise S.Refused(tr('You may not change this settlement.'), status=403)
            from . import leavers as L
            if action == 'lines':
                L.add_line(st, request.data, request.user)
            else:
                L.decide_asset(st, request.data, request.user)
        elif action == 'pdf':
            raise S.Refused(tr('Unknown action.'), status=405)
        else:
            S.act(st, action, request.user, self.c.codes, self.c.admin, request.data)
        st.refresh_from_db()
        return Response(S.settlement_row(st, full=True))

    def delete(self, request, pk, action=None, line_id=None):   # v1.62.0 EOS: DELETE settlements/<id>/lines/<line id>/
        st = self._get(pk)
        if action != 'lines' or line_id is None:
            raise S.Refused(tr('Unknown action.'), status=405)
        if not self.can({'add_exitsettlement', 'change_exitsettlement'}):
            raise S.Refused(tr('You may not change this settlement.'), status=403)
        from . import leavers as L
        L.remove_line(st, line_id, request.user)
        st.refresh_from_db()
        return Response(S.settlement_row(st, full=True))


# ---------------------------------------------------------------- v1.62.0 EOS – Leavers workspace
def _case_of(st, c, user):
    from . import leavers as L
    eos = M('EmpManagement', 'EndOfService').objects.select_related('resignation__employee').get(pk=st.eos_id)
    return L.case(eos.resignation, c, user)


def _pdf_response(case):
    from django.http import HttpResponse
    from . import eos_pdf
    if not case.get('settlement'):
        raise S.Refused(tr('Calculate the settlement first.'), status=409)
    data = None
    try:   # v1.62.0 print – the company's default design (Settings › Print templates); the statement below when there is none
        from PrintTemplates.hooks import settlement_pdf
        data = settlement_pdf(case)
    except ImportError:
        pass
    data = data or eos_pdf.build(case)
    r = HttpResponse(data, content_type='application/pdf')
    nm = f"Final settlement {case['settlement'].get('number') or case['employee_code']} {case['employee']}".replace('/', '-')
    r['Content-Disposition'] = f'inline; filename="{nm}.pdf"'
    return r


class LeaversView(Base):
    """GET api/leavers/?stage=&branch=&q=   every leaver with the step of its settlement   POST api/leavers/  new leaver (termination …)"""
    from EmpManagement.models import EmployeeResignation as zeo_model   # v1.62.0 EOS

    def get(self, request):
        if not self.can(READ | {'view_approved_resignations', 'view_employeeresignation'}):
            raise S.Refused(tr('You may not see the leavers.'), status=403)
        from . import leavers as L
        rows, counts = L.leaver_rows(self.c, request.GET.get('stage', ''), request.GET.get('branch', ''), (request.GET.get('q') or '').strip())
        Br = M('OrganisationManager', 'brnch_mstr')
        brs = Br.objects.order_by('branch_name')
        if self.c.branches is not None:
            brs = brs.filter(pk__in=self.c.branches)
        hr = self.can({'add_exitsettlement', 'change_exitsettlement'})
        return Response({'rows': rows, 'counts': counts, 'stages': [{'key': k, 'label': tr(v)} for k, v in L.STAGES],
                         'branches': [{'id': b.pk, 'name': b.branch_name} for b in brs], 'can_new': hr,
                         'exit_types': [{'key': k, 'label': tr(v)} for k, v in L.EXIT_TYPES if k in L.NEW_TYPES],
                         'due_days': L.due_days(), 'steps': [tr(x) for x in L.STEPS]})

    def post(self, request):
        if not self.can({'add_exitsettlement', 'change_exitsettlement'}):
            raise S.Refused(tr('You may not record a leaver.'), status=403)
        from . import leavers as L
        res = L.new_leaver(request.data, request.user, self.c)
        return Response(L.case(res, self.c, request.user), status=201)


class LeaverView(Base):
    """GET api/leavers/<resignation id>/   the case;   POST …/calculate/   calculate the settlement   GET …/pdf/"""
    from EmpManagement.models import EmployeeResignation as zeo_model   # v1.62.0 EOS

    def _res(self, pk):
        R = M('EmpManagement', 'EmployeeResignation')
        res = R.objects.select_related('employee').filter(pk=pk, status__iexact='approved').first()
        if res is None or not self.emp_ok(res.employee_id) or not self.can(READ | {'view_approved_resignations', 'view_employeeresignation'}):
            raise S.Refused(tr('Leaver not found.'), status=404)
        return res

    def get(self, request, pk, action=None):
        from . import leavers as L
        res = self._res(pk)
        case = L.case(res, self.c, request.user)
        if action == 'pdf':
            if not self.can(READ):
                raise S.Refused(tr('Leaver not found.'), status=404)
            return _pdf_response(case)
        return Response(case)

    def post(self, request, pk, action=None):
        from . import leavers as L
        res = self._res(pk)
        if action != 'calculate':
            raise S.Refused(tr('Unknown action.'), status=405)
        if not self.can({'add_exitsettlement', 'change_exitsettlement'}):
            raise S.Refused(tr('You may not start a final settlement.'), status=403)
        L.calculate(res, request.user)
        return Response(L.case(res, self.c, request.user))


class MyLeaverView(Base):
    """GET api/leavers/mine/ (and …/mine/pdf/) – the employee's own final settlement, read only, once approved."""
    def get(self, request, action=None):
        from . import leavers as L
        emp = self.c.emp
        R = M('EmpManagement', 'EmployeeResignation')
        res = R.objects.filter(employee=emp, status__iexact='approved').order_by('-id').first() if emp else None
        eos = M('EmpManagement', 'EndOfService').objects.filter(resignation=res).first() if res else None
        st = M('PayrollPlus', 'ExitSettlement').objects.filter(eos_id=eos.pk).first() if eos else None
        if st is None or st.status not in ('approved', 'paid'):
            raise S.Refused(tr('Your final settlement is not ready yet.'), status=404)
        case = L.case(res, self.c, request.user)
        case['rights'] = {k: False for k in case['rights']}
        case['rights']['pdf'] = True
        case['assets'] = [{k: v for k, v in a.items() if k not in ('note',)} for a in case['assets']]
        if action == 'pdf':
            return _pdf_response(case)
        return Response(case)


# ---------------------------------------------------------------- v1.31.0 statutory schemes
from rest_framework import serializers, viewsets   # noqa: E402
from .models import StatutoryScheme   # noqa: E402


class StatutorySerializer(serializers.ModelSerializer):
    class Meta:
        model = StatutoryScheme
        fields = '__all__'
        read_only_fields = ['updated_at', 'updated_by_id', 'is_standard']

    def validate_code(self, v):
        import re
        v = (v or '').strip().lower()
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,19}', v):
            raise serializers.ValidationError(tr('Code: letters, digits and _ only (it becomes the formula name stat_<code>_emp).'))
        qs = StatutoryScheme.objects.filter(code=v)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(tr('Code {0} is already used.', v))
        return v

    def validate_slabs(self, v):
        if v in (None, ''):
            return []
        if not isinstance(v, list) or any(not isinstance(x, dict) for x in v):
            raise serializers.ValidationError(tr('Slabs must be a list of rows.'))
        return v


STAT_CODES = ('change_salarycomponent', 'add_salarycomponent', 'change_payrollplussettings', 'view_salarycomponent', 'view_payrollrun')


class StatutoryViewSet(viewsets.ModelViewSet):
    """Payroll → Statutory schemes. GET list (+ pack, verify note); POST / PUT / DELETE (payroll setup rights);
    POST seed/ (load the pack's schemes), POST apply/ (salary components), GET preview/?employee=."""
    permission_classes = [IsAuthenticated]
    zeo_access = False
    zeo_scope = False
    queryset = StatutoryScheme.objects.all()
    serializer_class = StatutorySerializer

    def _c(self):
        return ctx(self.request)

    def _can_change(self):
        c = self._c()
        return c.admin or bool({'change_salarycomponent', 'add_salarycomponent', 'change_payrollplussettings'} & c.codes)

    def _can_view(self):
        c = self._c()
        return c.admin or bool(set(STAT_CODES) & c.codes)

    def list(self, request, *a, **kw):
        if not self._can_view():
            return Response({'detail': tr('You may not see the payroll setup.')}, status=403)
        from CountryPack import services as cp
        from CountryPack.packs import statutory_for, VERIFY
        p = cp.current_pack()
        rows = self.get_serializer(self.get_queryset(), many=True).data
        return Response({'rows': rows, 'pack': p['pack'], 'pack_name': p.get('name', ''), 'currency': (p.get('currency') or {}).get('code', ''),
                         'available': [{'code': d['code'], 'name': d['name']} for d in statutory_for(p['pack'])], 'verify': VERIFY,
                         'can_change': self._can_change(), 'kinds': StatutoryScheme.KINDS, 'bases': StatutoryScheme.BASES, 'applies': StatutoryScheme.APPLIES})

    def create(self, request, *a, **kw):
        if not self._can_change():
            return Response({'detail': tr('You may not change the payroll setup.')}, status=403)
        return super().create(request, *a, **kw)

    def perform_create(self, serializer):
        serializer.save(updated_by_id=self.request.user.pk)

    def perform_update(self, serializer):
        serializer.save(updated_by_id=self.request.user.pk)

    def update(self, request, *a, **kw):
        if not self._can_change():
            return Response({'detail': tr('You may not change the payroll setup.')}, status=403)
        return super().update(request, *a, **kw)

    def destroy(self, request, *a, **kw):
        if not self._can_change():
            return Response({'detail': tr('You may not change the payroll setup.')}, status=403)
        return super().destroy(request, *a, **kw)

    @action(detail=False, methods=['post'])
    def seed(self, request):
        if not self._can_change():
            return Response({'detail': tr('You may not change the payroll setup.')}, status=403)
        from CountryPack import services as cp
        from . import statutory as st
        pack = (request.data or {}).get('pack') or cp.current_pack()['pack']
        added, kept = st.seed(pack, request.user)
        return Response({'added': added, 'kept': kept, 'pack': pack})

    @action(detail=False, methods=['post'])
    def apply(self, request):
        if not self._can_change():
            return Response({'detail': tr('You may not change the payroll setup.')}, status=403)
        from . import statutory as st
        added, kept = st.apply_components(request.user)
        return Response({'added': added, 'kept': kept})

    @action(detail=False, methods=['get'])
    def preview(self, request):
        if not self._can_view():
            return Response({'detail': tr('You may not see the payroll setup.')}, status=403)
        from . import statutory as st
        eid = request.query_params.get('employee')
        if not str(eid).isdigit():
            return Response({'detail': tr('Choose an employee.')}, status=400)
        c = self._c()
        if c.branches is not None:
            E = M('EmpManagement', 'emp_master')
            if not E.objects.filter(pk=int(eid), emp_branch_id__in=c.branches).exists():
                return Response({'detail': tr('You can only work with employees of your own branches.')}, status=403)
        on = _day(request.query_params.get('date'))
        if on is False:
            return Response({'detail': tr('Give the date as YYYY-MM-DD.')}, status=400)
        return Response(st.preview(int(eid), on))

    # ---------------------------------------------------------------- v1.59.0 rates from a date
    @action(detail=True, methods=['get', 'post', 'delete'], url_path='versions')
    def versions(self, request, pk=None):
        """GET the rate history of a scheme; POST {effective_from, rate fields…, law_reference, notes} new rates from a date;
        DELETE ?version=<id> removes one."""
        if not self._can_view():
            return Response({'detail': tr('You may not see the payroll setup.')}, status=403)
        sc = self.get_object()
        if request.method == 'GET':
            return Response(scheme_history(sc))
        if not self._can_change():
            return Response({'detail': tr('You may not change the payroll setup.')}, status=403)
        V = M('PayrollPlus', 'StatutorySchemeVersion')
        if request.method == 'DELETE':
            vid = request.query_params.get('version')
            n = V.objects.filter(pk=int(vid), scheme=sc).delete()[0] if str(vid).isdigit() else 0
            if not n:
                return Response({'detail': tr('Rates not found.')}, status=404)
            return Response(scheme_history(sc))
        ser = SchemeVersionSerializer(data=request.data or {}, context={'scheme': sc})
        ser.is_valid(raise_exception=True)
        v = ser.save(scheme=sc, created_by_id=request.user.pk)
        out = scheme_history(sc)
        out['note'] = runs_from(v.effective_from)
        return Response(out, status=201)

    @action(detail=False, methods=['get'], url_path='in-force')
    def in_force(self, request):
        """GET ?date= – every active scheme with the rates in force on that day."""
        if not self._can_view():
            return Response({'detail': tr('You may not see the payroll setup.')}, status=403)
        from . import statutory as st
        on = _day(request.query_params.get('date'))
        if on is False:
            return Response({'detail': tr('Give the date as YYYY-MM-DD.')}, status=400)
        from .legal import _on
        on = _on(on)
        out = []
        for x in st.schemes_on(on):
            v = x._version
            out.append({'id': x.pk, 'code': x.code, 'name': x.name, 'kind': x.kind,
                        'employee_rate': str(x.employee_rate), 'employer_rate': str(x.employer_rate),
                        'employee_fixed': str(x.employee_fixed), 'employer_fixed': str(x.employer_fixed),
                        'min_base': str(x.min_base) if x.min_base is not None else None, 'max_base': str(x.max_base) if x.max_base is not None else None,
                        'source': 'version' if v else 'scheme', 'rates_from': v.effective_from.isoformat() if v else (x.effective_from.isoformat() if x.effective_from else None),
                        'law_reference': (v.law_reference if v else x.law_reference) or ''})
        return Response({'date': on.isoformat(), 'rows': out})


def _day(v):
    """None (not given), a date, or False (bad)."""
    if v in (None, ''):
        return None
    from datetime import date as _dd
    try:
        return _dd.fromisoformat(str(v)[:10])
    except ValueError:
        return False


def runs_from(d):
    """A note when payroll runs of that month or later exist already – they keep their payslips until re-run."""
    try:
        PR = M('PayrollManagement', 'PayrollRun')
        n = sum(1 for r in PR.objects.only('year', 'month') if (int(r.year) * 12 + int(r.month)) >= d.year * 12 + d.month)
    except Exception:
        n = 0
    if not n:
        return ''
    return tr('{0} payroll run(s) from that month exist already – their payslips keep the old figures until you re-run them.', n)


class SchemeVersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = M('PayrollPlus', 'StatutorySchemeVersion')
        exclude = ['scheme', 'created_by_id', 'created_at']

    def validate_slabs(self, v):
        if v in (None, ''):
            return []
        if not isinstance(v, list) or any(not isinstance(x, dict) for x in v):
            raise serializers.ValidationError(tr('Slabs must be a list of rows.'))
        return v

    def validate(self, d):
        sc = self.context['scheme']
        if not d.get('effective_from'):
            raise serializers.ValidationError({'effective_from': tr('Give the date the new rates start.')})
        for k in ('employee_rate', 'employer_rate', 'cess_rate'):
            if d.get(k) is not None and not (0 <= d[k] <= 100):
                raise serializers.ValidationError({k: tr('A rate is a percent between 0 and 100.')})
        lo, hi = d.get('min_base'), d.get('max_base')
        if lo is not None and hi is not None and lo > hi:
            raise serializers.ValidationError({'max_base': tr('The ceiling cannot be lower than the minimum base.')})
        V = M('PayrollPlus', 'StatutorySchemeVersion')
        if V.objects.filter(scheme=sc, effective_from=d['effective_from']).exists():
            raise serializers.ValidationError({'effective_from': tr('{0} has rates from {1} already – delete them first.', sc.name, d['effective_from'].isoformat())})
        if sc.effective_from and d['effective_from'] <= sc.effective_from:
            raise serializers.ValidationError({'effective_from': tr('New rates must start after the scheme’s own date ({0}).', sc.effective_from.isoformat())})
        return d


def _vrow(o, fields):
    out = {}
    for f in fields:
        v = getattr(o, f)
        out[f] = v if (v is None or isinstance(v, (list, dict))) else str(v)
    return out


def scheme_history(sc):
    """The scheme's own rates (from its effective date) and the dated versions, oldest first, with the one in force today."""
    from .models import RATE_FIELDS
    from .legal import scheme_version
    cur = scheme_version(sc)
    rows = [dict(_vrow(sc, RATE_FIELDS), id=None, effective_from=sc.effective_from.isoformat() if sc.effective_from else None,
                 law_reference=sc.law_reference, notes='', source='scheme', in_force=cur is None)]
    for v in sc.versions.order_by('effective_from'):
        rows.append(dict(_vrow(v, RATE_FIELDS), id=v.pk, effective_from=v.effective_from.isoformat(), law_reference=v.law_reference,
                         notes=v.notes, source='version', in_force=bool(cur and cur.pk == v.pk)))
    return {'scheme': {'id': sc.pk, 'code': sc.code, 'name': sc.name, 'kind': sc.kind}, 'rows': rows}


class LegalRatesView(APIView):
    """v1.59.0 – Payroll › Legal rates. GET ?date= every known figure with the value in force on the day and its history;
    POST {key, value, effective_from, law_reference, notes}; DELETE ?id=. Same rights as the statutory schemes."""
    permission_classes = [IsAuthenticated]
    zeo_access = False
    zeo_scope = False

    def _can(self, change):
        c = ctx(self.request)
        return c.admin or bool(set({'change_salarycomponent', 'add_salarycomponent', 'change_payrollplussettings'} if change else STAT_CODES) & c.codes)

    def _out(self, on):
        from .legal import KNOWN_RATES, _on, plain
        LR = M('PayrollPlus', 'LegalRate')
        on = _on(on)
        hist = {}
        for r in LR.objects.order_by('key', 'effective_from'):
            hist.setdefault(r.key, []).append(r)
        keys = list(KNOWN_RATES) + sorted(k for k in hist if k not in KNOWN_RATES)
        rows = []
        for k in keys:
            meta = KNOWN_RATES.get(k, {})
            h = hist.get(k, [])
            cur = None
            for r in h:
                if r.effective_from <= on:
                    cur = r
            rows.append({'key': k, 'label': tr(meta.get('label', k)), 'country': meta.get('country', h[0].country if h else ''),
                         'built_in': str(meta['default']) if meta else None,
                         'value': str(plain(cur.value)) if cur else (str(meta['default']) if meta else None),
                         'source': 'rate' if cur else 'built-in', 'from': cur.effective_from.isoformat() if cur else None,
                         'law_reference': cur.law_reference if cur else '',
                         'history': [{'id': r.pk, 'value': str(plain(r.value)), 'effective_from': r.effective_from.isoformat(),
                                      'law_reference': r.law_reference, 'notes': r.notes, 'in_force': bool(cur and cur.pk == r.pk)} for r in h]})
        return {'date': on.isoformat(), 'rows': rows, 'can_change': self._can(True)}

    def get(self, request):
        if not self._can(False):
            return Response({'detail': tr('You may not see the payroll setup.')}, status=403)
        on = _day(request.query_params.get('date'))
        if on is False:
            return Response({'detail': tr('Give the date as YYYY-MM-DD.')}, status=400)
        return Response(self._out(on))

    def post(self, request):
        if not self._can(True):
            return Response({'detail': tr('You may not change the payroll setup.')}, status=403)
        from .legal import KNOWN_RATES
        import re
        d = request.data or {}
        key = str(d.get('key') or '').strip().lower()
        if not re.fullmatch(r'[a-z][a-z0-9_]{1,59}', key):
            return Response({'key': tr('Choose the figure (key: letters, digits and _).')}, status=400)
        if key not in KNOWN_RATES:
            return Response({'key': tr('{0} is not a figure ZEO uses.', key)}, status=400)
        eff = _day(d.get('effective_from'))
        if not eff:
            return Response({'effective_from': tr('Give the date the new value starts.')}, status=400)
        try:
            val = Decimal(str(d.get('value')))
        except Exception:
            return Response({'value': tr('Give the value as a number.')}, status=400)
        if val < 0:
            return Response({'value': tr('The value cannot be negative.')}, status=400)
        if key.endswith(('_rate', '_new', '_old', '_employer')) and val > 1:
            return Response({'value': tr('Rates are fractions: 0.11 = 11 %.')}, status=400)
        LR = M('PayrollPlus', 'LegalRate')
        if LR.objects.filter(key=key, effective_from=eff).exists():
            return Response({'effective_from': tr('{0} has a value from {1} already – delete it first.', key, eff.isoformat())}, status=400)
        LR.objects.create(key=key, country=KNOWN_RATES[key]['country'], value=val, effective_from=eff,
                          law_reference=str(d.get('law_reference') or '')[:400], notes=str(d.get('notes') or ''), created_by_id=request.user.pk)
        out = self._out(None)
        out['note'] = runs_from(eff)
        return Response(out, status=201)

    def delete(self, request):
        if not self._can(True):
            return Response({'detail': tr('You may not change the payroll setup.')}, status=403)
        i = request.query_params.get('id')
        n = M('PayrollPlus', 'LegalRate').objects.filter(pk=int(i)).delete()[0] if str(i).isdigit() else 0
        if not n:
            return Response({'detail': tr('Value not found.')}, status=404)
        return Response(self._out(None))


# ------------------------------------------------------------------ v1.49.0 provisions and loans
PROV_READ = {'view_payrollrun', 'add_payrollrun', 'change_payrollrun', 'view_payslip'}
PROV_EDIT = {'add_payrollrun', 'change_payrollrun'}
LOAN_READ = {'view_loanapplication', 'change_loanapplication', 'add_loanapplication'}
LOAN_EDIT = {'change_loanapplication'}


class ProvisionsView(Base):
    """GET the months; POST {month: 'YYYY-MM-DD'} make / remake the month's provisions."""
    def get(self, request):
        from .models import ProvisionRun
        from .provisions import run_json
        if not self.can(PROV_READ):
            raise S.Refused(tr('Only payroll sees the provisions.'), status=403)
        return Response([run_json(r) for r in ProvisionRun.objects.all()[:36]])

    def post(self, request):
        from datetime import date
        from django.utils.dateparse import parse_date
        from .provisions import make_run, run_json, last_month_end
        if not self.can(PROV_EDIT):
            raise S.Refused(tr('Only payroll makes the provisions.'), status=403)
        d = parse_date(str(request.data.get('month') or '')) or last_month_end()
        if d > date.today():
            raise S.Refused(tr('Choose a month that has ended.'))
        run = make_run(d, request.user.pk)
        return Response(run_json(run, lines=True, emp_ok=self.emp_ok), status=201)


class ProvisionView(Base):
    def get(self, request, pk):
        import csv
        import io
        from django.http import HttpResponse
        from .models import ProvisionRun
        from .provisions import run_json
        if not self.can(PROV_READ):
            raise S.Refused(tr('Only payroll sees the provisions.'), status=403)
        run = ProvisionRun.objects.filter(pk=pk).first()
        if run is None:
            raise S.Refused(tr('Not found.'), status=404)
        out = run_json(run, lines=True, emp_ok=self.emp_ok)
        if request.query_params.get('export') == 'csv':
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(['Code', 'Employee', 'Department', 'Branch'] + [f'{k} {x}' for k in ('gratuity', 'leave', 'air_ticket') for x in ('opening', 'closing', 'movement')])
            for r in out['rows']:
                w.writerow([r['code'], r['name'], r['department'], r['branch']] + [(r.get(k) or {}).get(x, '') for k in ('gratuity', 'leave', 'air_ticket') for x in ('opening', 'closing', 'movement')])
            resp = HttpResponse(buf.getvalue(), content_type='text/csv')
            resp['Content-Disposition'] = f'attachment; filename="provisions-{run.month:%Y-%m}.csv"'
            return resp
        out['accounting'] = _accounting_on()
        return Response(out)

    def post(self, request, pk, action):
        from .models import ProvisionRun
        from .provisions import post, run_json
        if not self.can(PROV_EDIT):
            raise S.Refused(tr('Only payroll changes the provisions.'), status=403)
        run = ProvisionRun.objects.filter(pk=pk).first()
        if run is None:
            raise S.Refused(tr('Not found.'), status=404)
        if action == 'final':
            if run.status != 'draft':
                raise S.Refused(tr('This month is already {0}.', run.get_status_display().lower()))
            run.status = 'final'
            run.save(update_fields=['status'])
        elif action == 'post':
            if run.status == 'posted':
                raise S.Refused(tr('Already posted.'))
            if not _accounting_on():
                raise S.Refused(tr('The Accounting integration is not set up – export the CSV for your accounting system instead.'))
            post(run, request.user)
        else:
            raise S.Refused(tr('Unknown action.'), status=404)
        return Response(run_json(run, lines=True, emp_ok=self.emp_ok))


def _accounting_on():
    try:
        from ZeoAccounting.services import settings as acc
        s = acc()
        return bool(getattr(s, 'enabled', False) or getattr(s, 'is_active', False) or getattr(s, 'connector', ''))
    except Exception:
        return False


class LoansView(Base):
    """GET ?mine=1 | ?employee= | (HR) every running loan with its balance, instalment and months left."""
    def get(self, request):
        from .loans import ACTIVE, summary
        LA = M('PayrollManagement', 'LoanApplication')
        qs = LA.objects.select_related('employee', 'loan_type')
        if request.query_params.get('mine') or not self.can(LOAN_READ):
            emp = self.c.emp
            qs = qs.filter(employee=emp) if emp else qs.none()
        elif request.query_params.get('employee'):
            qs = qs.filter(employee_id=request.query_params['employee'])
        st = request.query_params.get('status') or 'running'
        if st == 'running':
            qs = qs.filter(status__in=ACTIVE)
        elif st != 'all':
            qs = qs.filter(status=st)
        rows = []
        for l in qs.order_by('-applied_on')[:300]:
            if not self.emp_ok(l.employee_id) and not (self.c.emp and l.employee_id == self.c.emp.pk):
                continue
            s_ = summary(l)
            s_.pop('schedule', None)
            s_.pop('events', None)
            rows.append(s_)
        return Response({'rows': rows, 'can_edit': self.can(LOAN_EDIT)})


class LoanView(Base):
    """GET the loan with its schedule and changes; POST <action>: topup {amount, months} | reschedule {emi | months} |
    settle {note} | pause {start, resume, note} | resume."""
    def _loan(self, pk, edit=False):
        LA = M('PayrollManagement', 'LoanApplication')
        l = LA.objects.select_related('employee', 'loan_type').filter(pk=pk).first()
        if l is None:
            raise S.Refused(tr('Loan not found.'), status=404)
        mine = bool(self.c.emp and l.employee_id == self.c.emp.pk)
        if edit:
            if not self.can(LOAN_EDIT) or not self.emp_ok(l.employee_id):
                raise S.Refused(tr('Only payroll changes loans.'), status=403)
        elif not (mine or (self.can(LOAN_READ) and self.emp_ok(l.employee_id))):
            raise S.Refused(tr('Loan not found.'), status=404)
        return l

    def get(self, request, pk):
        from .loans import summary
        out = summary(self._loan(pk))
        out['can_edit'] = self.can(LOAN_EDIT)
        return Response(out)

    def post(self, request, pk, action):
        from django.utils.dateparse import parse_date
        from . import loans as L
        l = self._loan(pk, edit=True)
        d = request.data
        try:
            if action == 'topup':
                L.topup(l, d.get('amount'), d.get('months'), request.user, d.get('note') or '')
            elif action == 'reschedule':
                L.reschedule(l, d.get('emi'), d.get('months'), request.user, d.get('note') or '')
            elif action == 'settle':
                L.settle(l, request.user, d.get('note') or '', at_exit=bool(d.get('at_exit')))
            elif action == 'pause':
                L.pause(l, parse_date(str(d.get('start') or '')), parse_date(str(d.get('resume') or '')), request.user, d.get('note') or '')
            elif action == 'resume':
                L.resume(l, request.user, d.get('note') or '')
            else:
                raise S.Refused(tr('Unknown action.'), status=404)
        except L.LoanError as e:
            raise S.Refused(tr(e.message), status=e.status)
        out = L.summary(l)
        out['can_edit'] = True
        return Response(out)


# ------------------------------------------------------------------ v1.50.0 payroll what-if and cost allocation
WHATIF_READ = {'add_payrollrun', 'change_payrollrun'}
WHATIF_APPLY = {'change_employeesalarystructure'}
ALLOC_READ = {'view_payrollrun', 'add_payrollrun', 'change_payrollrun'}
ALLOC_EDIT = {'add_payrollrun', 'change_payrollrun'}


def _month(v, default=None):
    from datetime import date
    s = str(v or '')
    try:
        y, m = int(s[:4]), int(s[5:7])
        return date(y, m, 1)
    except (ValueError, TypeError):
        return default


class _V150(Base):
    def handle_exception(self, exc):
        from .whatif import WhatIfError
        from .costalloc import AllocError
        if isinstance(exc, (WhatIfError, AllocError)):
            return Response({'detail': exc.message}, status=exc.status)
        return super().handle_exception(exc)

    def visible(self):
        if self.c.branches is None:
            return None
        E = M('EmpManagement', 'emp_master')
        return list(E.objects.filter(emp_branch_id__in=self.c.branches).values_list('pk', flat=True))


class WhatIfsView(_V150):
    """GET the scenarios (+ options); POST {name, month, rules, employee_ids} makes one and calculates it."""
    def get(self, request):
        from .models import WhatIfScenario
        from .whatif import scenario_json
        if not self.can(WHATIF_READ):
            raise S.Refused(tr('Only payroll makes trial payrolls.'), status=403)
        SC = M('PayrollManagement', 'SalaryComponent')
        D = M('OrganisationManager', 'dept_master'); B = M('OrganisationManager', 'brnch_mstr'); G = M('OrganisationManager', 'desgntn_master')
        E = M('EmpManagement', 'emp_master')
        brs = B.objects.all()
        emps = E.objects.filter(is_active=True)
        if self.c.branches is not None:
            brs, emps = brs.filter(pk__in=self.c.branches), emps.filter(emp_branch_id__in=self.c.branches)
        opts = {'components': [{'id': c.pk, 'name': c.name, 'type': c.component_type, 'formula': c.component_value_type == 'formula'}
                               for c in SC.objects.order_by('component_type', 'name')],
                'department': [{'id': x.pk, 'name': x.dept_name} for x in D.objects.order_by('dept_name')],
                'branch': [{'id': x.pk, 'name': x.branch_name} for x in brs.order_by('branch_name')],
                'designation': [{'id': x.pk, 'name': x.desgntn_job_title} for x in G.objects.order_by('desgntn_job_title')],
                'employees': [{'id': e.pk, 'name': f'{e.emp_code} {e.emp_first_name} {e.emp_last_name or ""}'.strip()} for e in emps.order_by('emp_code')[:2000]]}
        return Response({'rows': [scenario_json(x) for x in WhatIfScenario.objects.all()[:100]], 'options': opts,
                         'can_apply': self.can(WHATIF_APPLY)})

    def post(self, request):
        from datetime import date
        from .models import WhatIfScenario
        from .whatif import check_rules, scenario_json, simulate
        if not self.can(WHATIF_READ):
            raise S.Refused(tr('Only payroll makes trial payrolls.'), status=403)
        d = request.data
        name = (d.get('name') or '').strip()[:120]
        if not name:
            raise S.Refused(tr('Give the scenario a name.'))
        rules = check_rules(d.get('rules'))
        if not rules:
            raise S.Refused(tr('Add at least one change.'))
        t = date.today()
        sc = WhatIfScenario.objects.create(name=name, month=_month(d.get('month'), t.replace(day=1)), rules=rules,
                                           employee_ids=[int(x) for x in (d.get('employee_ids') or []) if str(x).isdigit()],
                                           effective_from=_month(d.get('effective_from')), created_by_id=request.user.pk)
        simulate(sc, self.visible())
        return Response(scenario_json(sc, full=True), status=201)


class WhatIfView(_V150):
    def _get(self, pk):
        from .models import WhatIfScenario
        if not self.can(WHATIF_READ):
            raise S.Refused(tr('Only payroll makes trial payrolls.'), status=403)
        sc = WhatIfScenario.objects.filter(pk=pk).first()
        if sc is None:
            raise S.Refused(tr('Not found.'), status=404)
        return sc

    def get(self, request, pk):
        import csv
        import io
        from django.http import HttpResponse
        from .whatif import scenario_json
        sc = self._get(pk)
        if request.query_params.get('export') == 'csv':
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(['Code', 'Employee', 'Department', 'Branch', 'Gross now', 'Gross new', 'Net now', 'Net new', 'Difference', '%'])
            for r in (sc.result or {}).get('rows', []):
                w.writerow([r['code'], r['name'], r['department'], r['branch'], r['base_gross'], r['new_gross'], r['base_net'], r['new_net'], r['diff'], r['pct']])
            resp = HttpResponse(buf.getvalue(), content_type='text/csv')
            resp['Content-Disposition'] = f'attachment; filename="what-if-{sc.pk}.csv"'
            return resp
        return Response(scenario_json(sc, full=True))

    def put(self, request, pk):
        from .whatif import check_rules, scenario_json, simulate
        sc = self._get(pk)
        if sc.status == 'applied':
            raise S.Refused(tr('An applied scenario cannot be changed – copy it into a new one.'))
        d = request.data
        if 'name' in d:
            sc.name = (d.get('name') or sc.name).strip()[:120]
        if 'month' in d:
            sc.month = _month(d.get('month'), sc.month)
        if 'effective_from' in d:
            sc.effective_from = _month(d.get('effective_from'))
        if 'rules' in d:
            sc.rules = check_rules(d.get('rules'))
        if 'employee_ids' in d:
            sc.employee_ids = [int(x) for x in (d.get('employee_ids') or []) if str(x).isdigit()]
        sc.save()
        simulate(sc, self.visible())
        return Response(scenario_json(sc, full=True))

    def delete(self, request, pk):
        sc = self._get(pk)
        if sc.status == 'applied':
            raise S.Refused(tr('An applied scenario is kept as the record of the change.'))
        sc.delete()
        return Response(status=204)

    def post(self, request, pk, action):
        from .whatif import apply, scenario_json, simulate
        sc = self._get(pk)
        if action == 'calculate':
            simulate(sc, self.visible())
        elif action == 'apply':
            if not self.can(WHATIF_APPLY):
                raise S.Refused(tr('Only a user who may change salaries applies a scenario.'), status=403)
            n = apply(sc, request.user, self.visible())
            out = scenario_json(sc, full=True)
            out['applied_rows'] = n
            return Response(out)
        else:
            raise S.Refused(tr('Unknown action.'), status=404)
        return Response(scenario_json(sc, full=True))


class CostAllocRulesView(_V150):
    """GET the allocation rules (+ options); POST {employee_id, basis, lines, valid_from, valid_until, note} (id = update)."""
    def get(self, request):
        from .models import CostAllocation
        from .costalloc import options, rule_json
        if not self.can(ALLOC_READ):
            raise S.Refused(tr('Only payroll sees the cost allocation.'), status=403)
        rows = [rule_json(r) for r in CostAllocation.objects.all()[:1000] if self.emp_ok(r.employee_id)]
        return Response({'rows': rows, 'options': options(), 'can_edit': self.can(ALLOC_EDIT)})

    def post(self, request):
        from django.utils.dateparse import parse_date
        from .models import CostAllocation
        from .costalloc import check_lines, rule_json
        if not self.can(ALLOC_EDIT):
            raise S.Refused(tr('Only payroll changes the cost allocation.'), status=403)
        d = request.data
        E = M('EmpManagement', 'emp_master')
        e = E.objects.filter(pk=d.get('employee_id')).first()
        if e is None or not self.emp_ok(e.pk):
            raise S.Refused(tr('Choose the employee.'))
        basis = d.get('basis') if d.get('basis') in ('percent', 'timesheet') else 'percent'
        lines = check_lines(basis, d.get('lines') if basis == 'percent' else [])
        r = CostAllocation.objects.filter(pk=d.get('id')).first() if d.get('id') else CostAllocation(employee_id=e.pk)
        if r is None:
            raise S.Refused(tr('Not found.'), status=404)
        r.employee_id, r.basis, r.lines = e.pk, basis, lines
        r.valid_from = parse_date(str(d.get('valid_from') or '')) or None
        r.valid_until = parse_date(str(d.get('valid_until') or '')) or None
        if r.valid_from and r.valid_until and r.valid_until < r.valid_from:
            raise S.Refused(tr('"Until" is before "from".'))
        r.note = (d.get('note') or '')[:255]
        r.updated_by_id = request.user.pk
        r.save()
        return Response(rule_json(r), status=201)


class CostAllocRuleView(_V150):
    def delete(self, request, pk):
        from .models import CostAllocation
        if not self.can(ALLOC_EDIT):
            raise S.Refused(tr('Only payroll changes the cost allocation.'), status=403)
        CostAllocation.objects.filter(pk=pk).delete()
        return Response(status=204)


class CostAllocMonthView(_V150):
    """GET ?month=YYYY-MM [&export=csv] the allocation of the month; POST {month} posts it to accounting."""
    def get(self, request):
        import csv
        import io
        from datetime import date
        from django.http import HttpResponse
        from .costalloc import compute
        if not self.can(ALLOC_READ):
            raise S.Refused(tr('Only payroll sees the cost allocation.'), status=403)
        t = date.today()
        m = _month(request.query_params.get('month'), (t.replace(day=1) - __import__('datetime').timedelta(days=1)).replace(day=1))
        res = compute(m, self.emp_ok)
        if request.query_params.get('export') == 'csv':
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(['Code', 'Employee', 'Department', 'Cost', 'Allocated to', 'Type', '%', 'Hours', 'Amount'])
            for r in res['rows']:
                for sp in r['splits']:
                    w.writerow([r['code'], r['name'], r['department'], r['cost'], sp['name'], sp['kind'], sp['percent'], sp['hours'], sp['amount']])
            resp = HttpResponse(buf.getvalue(), content_type='text/csv')
            resp['Content-Disposition'] = f'attachment; filename="cost-allocation-{m:%Y-%m}.csv"'
            return resp
        res['accounting'] = _accounting_on()
        return Response(res)

    def post(self, request):
        from .costalloc import post
        if not self.can(ALLOC_EDIT):
            raise S.Refused(tr('Only payroll posts the cost allocation.'), status=403)
        if not _accounting_on():
            raise S.Refused(tr('The Accounting integration is not set up – export the CSV for your accounting system instead.'))
        m = _month(request.data.get('month'))
        if m is None:
            raise S.Refused(tr('Choose the month.'))
        b = post(m, request.user, self.emp_ok)
        return Response({'journal': b.number, 'status': b.status, 'lines': b.lines_count, 'problems': b.problems})


# ------------------------------------------------------------------ v1.55.0 pay calendars, off-cycle runs, arrears
from datetime import timedelta
from decimal import Decimal
PAY_READ = {'view_payrollrun', 'add_payrollrun', 'change_payrollrun'}
PAY_WRITE = {'add_payrollrun', 'change_payrollrun'}


class _V155(Base):
    def need(self, write=False):
        if not self.can(PAY_WRITE if write else PAY_READ):
            raise S.Refused(tr('Only payroll users can do this.'), status=403)

    def emps(self):
        E = M('EmpManagement', 'emp_master')
        from django.db.models import Q
        qs = E.objects.filter(Q(is_active=True) | Q(is_active__isnull=True))
        if self.c.branches is not None:
            qs = qs.filter(emp_branch_id__in=self.c.branches)
        return qs

    def check_emps(self, ids):
        allowed = set(self.emps().filter(pk__in=ids).values_list('pk', flat=True))
        bad = [i for i in ids if int(i) not in allowed]
        if bad:
            raise S.Refused(tr('Some employees are not in your branches.'), status=403)


def _run_state(run_id):
    R = M('PayrollManagement', 'PayrollRun')
    r = R.objects.filter(pk=run_id).first()
    if r is None:
        return None
    P = M('PayrollManagement', 'Payslip')
    from django.db.models import Count, Sum
    agg = P.objects.filter(payroll_run=r).aggregate(n=Count('id'), net=Sum('net_salary'), gross=Sum('gross_salary'))
    return {'id': r.pk, 'name': r.name, 'status': r.status, 'payslips': agg['n'] or 0, 'net': str(agg['net'] or 0), 'gross': str(agg['gross'] or 0)}


def calendar_json(cal, with_periods=True):
    from . import cycles as C
    from .models import CalendarRun
    d = {'id': cal.pk, 'name': cal.name, 'frequency': cal.frequency, 'frequency_label': tr(cal.get_frequency_display()), 'first_start': cal.first_start,
         'pay_days_after': cal.pay_days_after, 'employee_ids': cal.employee_ids, 'category_ids': cal.category_ids, 'active': cal.active,
         'members': len(C.member_ids(cal)), 'factor': str(C.factor(cal.frequency).quantize(Decimal('0.0001')))}
    if with_periods:
        runs = {r.period_start: r for r in CalendarRun.objects.filter(calendar=cal)}
        out = []
        for a, b in C.periods(cal):
            r = runs.get(a)
            out.append({'start': a, 'end': b, 'pay_date': b + timedelta(days=cal.pay_days_after), 'closes_month': C.closes_month(cal, a, b),
                        'run': _run_state(r.payroll_run_id) if r else None})
        d['periods'] = out
    return d


class CalendarsView(_V155):
    def get(self, request):
        from .models import PayCalendar
        self.need()
        Cat = M('OrganisationManager', 'ctgry_master')
        on = S.settings().pay_frequencies()   # v1.55.0: only the frequencies switched on in Payroll controls
        return Response({'enabled': bool(on), 'can_settings': self.c.admin or 'change_payrollplussettings' in self.c.codes,
                         'rows': [calendar_json(c) for c in PayCalendar.objects.all()] if on else [],
                         'frequencies': [{'key': k, 'label': tr(v)} for k, v in PayCalendar.FREQ if k in on],
                         'categories': [{'id': x.pk, 'name': x.ctgry_title} for x in Cat.objects.order_by('ctgry_title')] if hasattr(Cat, 'ctgry_title') else
                                       [{'id': x.pk, 'name': str(x)} for x in Cat.objects.all()],
                         'employees': [{'id': e.pk, 'name': f'{e.emp_code} {e.emp_first_name or ""} {e.emp_last_name or ""}'.strip()} for e in self.emps().order_by('emp_code')[:3000]]})

    def post(self, request):
        from datetime import date as _d
        from .models import PayCalendar
        self.need(True)
        d = request.data
        cal = PayCalendar.objects.filter(pk=d.get('id')).first() if d.get('id') else PayCalendar(created_by_id=request.user.pk)
        if d.get('id') and cal is None:
            raise S.Refused(tr('Not found.'), status=404)
        name = (d.get('name') or cal.name or '').strip()[:80]
        if not name:
            raise S.Refused(tr('Give the calendar a name.'))
        freq = d.get('frequency') or cal.frequency
        if freq not in dict(PayCalendar.FREQ):
            raise S.Refused(tr('Choose how often the employees are paid.'))
        if freq not in S.settings().pay_frequencies():   # v1.55.0
            raise S.Refused(tr('Switch this pay frequency on first: Settings → Payroll controls.'), problems={'frequency': 'Not switched on.'})
        try:
            first = _d.fromisoformat(str(d.get('first_start') or cal.first_start or _d.today())[:10])
        except ValueError:
            raise S.Refused(tr('Enter a valid date (YYYY-MM-DD).'))
        emp_ids = [int(x) for x in (d.get('employee_ids') if isinstance(d.get('employee_ids'), list) else cal.employee_ids or []) if str(x).isdigit()]
        self.check_emps(emp_ids)
        cats = [int(x) for x in (d.get('category_ids') if isinstance(d.get('category_ids'), list) else cal.category_ids or []) if str(x).isdigit()]
        if cal.pk and cal.frequency != freq and cal.runs.exists():
            raise S.Refused(tr('This calendar has payroll runs – make a new calendar for another frequency.'))
        others = PayCalendar.objects.filter(active=True).exclude(pk=cal.pk)
        from . import cycles as C
        taken = {e: o.name for o in others for e in C.member_ids(o)}
        clash = sorted({taken[e] for e in emp_ids if e in taken})
        if clash:
            raise S.Refused(tr('Some employees are already on the calendar {0}.', ', '.join(clash)))
        cal.name, cal.frequency, cal.first_start, cal.employee_ids, cal.category_ids = name, freq, first, emp_ids, cats
        cal.pay_days_after = max(0, min(int(d.get('pay_days_after', cal.pay_days_after or 3) or 0), 31))
        if 'active' in d:
            cal.active = bool(d['active'])
        cal.save()
        return Response(calendar_json(cal), status=201)


class CalendarView(_V155):
    def post(self, request, pk):
        """{action: 'run', start} – make and calculate the run of a period."""
        from datetime import date as _d
        from . import cycles as C
        from .models import PayCalendar
        self.need(True)
        cal = PayCalendar.objects.filter(pk=pk).first()
        if cal is None:
            raise S.Refused(tr('Not found.'), status=404)
        if cal.frequency not in S.settings().pay_frequencies():   # v1.55.0
            raise S.Refused(tr('Switch this pay frequency on first: Settings → Payroll controls.'))
        try:
            start = _d.fromisoformat(str(request.data.get('start'))[:10])
        except ValueError:
            raise S.Refused(tr('Enter a valid date (YYYY-MM-DD).'))
        mem = C.member_ids(cal)
        if self.c.branches is not None and self.emps().filter(pk__in=mem).count() != len(mem):
            raise S.Refused(tr('Some employees of this calendar are not in your branches – ask someone who manages all of them.'), status=403)
        try:
            cr = C.run_period(cal, start, request.user)
        except C.CycleError as e:
            raise S.Refused(str(e))
        return Response({'run': _run_state(cr.payroll_run_id), 'calendar': calendar_json(cal)}, status=201)

    def delete(self, request, pk):
        from .models import PayCalendar
        self.need(True)
        cal = PayCalendar.objects.filter(pk=pk).first()
        if cal is None:
            raise S.Refused(tr('Not found.'), status=404)
        if cal.runs.exists():
            cal.active = False
            cal.save(update_fields=['active', 'updated_at'])
            return Response({'deactivated': True})
        cal.delete()
        return Response(status=204)


def offcycle_json(o):
    return {'id': o.pk, 'kind': o.kind, 'kind_label': tr(o.get_kind_display()), 'title': o.title, 'pay_date': o.pay_date, 'lines': o.lines,
            'run': _run_state(o.payroll_run_id), 'created_at': o.created_at}


class OffCycleView(_V155):
    def get(self, request):
        from .models import ArrearLine, OffCycleRun
        self.need()
        SC = M('PayrollManagement', 'SalaryComponent')
        return Response({'rows': [offcycle_json(o) for o in OffCycleRun.objects.all()[:200]],
                         'kinds': [{'key': k, 'label': tr(v)} for k, v in OffCycleRun.KINDS],
                         'components': [{'id': c.pk, 'name': c.name, 'type': c.component_type} for c in SC.objects.order_by('component_type', 'name')],
                         'employees': [{'id': e.pk, 'name': f'{e.emp_code} {e.emp_first_name or ""} {e.emp_last_name or ""}'.strip()} for e in self.emps().order_by('emp_code')[:3000]],
                         'arrears_waiting': ArrearLine.objects.filter(status='pending').count()})

    def post(self, request):
        from datetime import date as _d
        from . import cycles as C
        self.need(True)
        d = request.data
        kind = d.get('kind') or 'other'
        from .models import OffCycleRun
        if kind not in dict(OffCycleRun.KINDS):
            raise S.Refused(tr('Choose the kind of run.'))
        try:
            pay = _d.fromisoformat(str(d.get('pay_date') or _d.today())[:10])
        except ValueError:
            raise S.Refused(tr('Enter a valid date (YYYY-MM-DD).'))
        lines = d.get('lines') if isinstance(d.get('lines'), list) else []
        ids = [int(x.get('employee_id')) for x in lines if str(x.get('employee_id', '')).isdigit()]
        if kind == 'arrears' and not lines:
            from .models import ArrearLine
            ids = list(ArrearLine.objects.filter(status='pending').values_list('employee_id', flat=True).distinct())
        self.check_emps(ids)
        title = (d.get('title') or '').strip() or f'{tr(dict(OffCycleRun.KINDS)[kind])} {pay:%b %Y}'
        try:
            o = C.off_cycle(kind, title, pay, lines, request.user)
        except C.CycleError as e:
            raise S.Refused(str(e))
        return Response(offcycle_json(o), status=201)


class ArrearsView(_V155):
    def get(self, request):
        from . import cycles as C
        from .models import ArrearLine
        self.need()
        C.release_orphans()
        E = M('EmpManagement', 'emp_master')
        SC = M('PayrollManagement', 'SalaryComponent')
        mine = set(self.emps().values_list('pk', flat=True))
        rows = [x for x in ArrearLine.objects.exclude(status='cancelled').order_by('-created_at')[:2000] if x.employee_id in mine]
        names = {e.pk: f'{e.emp_code} {e.emp_first_name or ""} {e.emp_last_name or ""}'.strip() for e in E.objects.filter(pk__in={x.employee_id for x in rows})}
        comps = {c.pk: c.name for c in SC.objects.filter(pk__in={x.component_id for x in rows})}
        H = M('PayrollManagement', 'SalaryRevisionHistory')
        recent = []
        for h in H.objects.filter(employee_id__in=mine).select_related('employee', 'component').order_by('-date_updated')[:200]:
            last = (h.revisions or [{}])[-1]
            recent.append({'employee_id': h.employee_id, 'employee': f'{h.employee.emp_code} {h.employee.emp_first_name or ""}'.strip(),
                           'component_id': h.component_id, 'component': h.component.name, 'old': last.get('old_amount'), 'new': last.get('new_amount'),
                           'revised_on': last.get('revised_on'), 'effective_period': last.get('effective_period')})
        return Response({'lines': [{'id': x.pk, 'employee_id': x.employee_id, 'employee': names.get(x.employee_id, ''), 'component': comps.get(x.component_id, ''),
                                    'month': x.month, 'old': str(x.old_amount), 'new': str(x.new_amount), 'amount': str(x.amount), 'status': x.status,
                                    'status_label': tr(x.get_status_display()), 'payslip': x.included_payslip_id, 'effective_from': x.effective_from} for x in rows],
                         'revisions': recent})

    def post(self, request):
        """{action: preview | save, employee_ids, effective_from, component_ids?} or {action: cancel, ids}."""
        from datetime import date as _d
        from . import cycles as C
        from .models import ArrearLine
        self.need(True)
        d = request.data
        if d.get('action') == 'cancel':
            ids = [int(x) for x in d.get('ids') or [] if str(x).isdigit()]
            mine = set(self.emps().values_list('pk', flat=True))
            n = ArrearLine.objects.filter(pk__in=ids, status='pending', employee_id__in=mine).update(status='cancelled')
            return Response({'cancelled': n})
        try:
            eff = _d.fromisoformat(str(d.get('effective_from'))[:10])
        except ValueError:
            raise S.Refused(tr('Enter the date the salary change is effective from.'))
        ids = [int(x) for x in d.get('employee_ids') or [] if str(x).isdigit()]
        if not ids:
            raise S.Refused(tr('Choose the employees.'))
        self.check_emps(ids)
        comps = [int(x) for x in d.get('component_ids') or [] if str(x).isdigit()] or None
        return Response(C.calculate(ids, eff, comps, request.user, save=d.get('action') == 'save'))
