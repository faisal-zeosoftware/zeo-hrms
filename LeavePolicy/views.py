"""Leave policies, UAE setup, accrual / year end, ledger, planner and encashment approval (v1.10.0)."""
import calendar as cal
from datetime import date, datetime, timedelta

from django.apps import apps
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import serializers, status, viewsets
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import engine, setup
from .models import EmployeeLeavePolicy, LeaveLedger, LeavePolicy, LeavePolicyLine

M = apps.get_model
READ = {'view_leavepolicy', 'view_leave_type', 'view_leave_entitlement', 'view_emp_leave_balance', 'view_employee_leave_request', 'view_leavereport'}
WRITE = {'add_leavepolicy', 'change_leavepolicy', 'add_leave_type', 'change_leave_type', 'add_leave_entitlement', 'change_leave_entitlement'}
RUN = {'run_leave_accrual', 'change_leavepolicy', 'change_leave_entitlement'}
ENCASH = {'change_leaveencashment', 'add_leaveencashment'}


def _ctx(request):
    from AccessControl.access import ctx
    return ctx(request)


def can(request, codes):
    c = _ctx(request)
    return c.admin or bool(c.codes & codes)


class LeaveHR(BasePermission):
    message = 'You may not manage leave policies.'

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        return can(request, READ if request.method in ('GET', 'HEAD', 'OPTIONS') else WRITE)


def _type_names():
    return dict(M('calendars', 'leave_type').objects.values_list('id', 'name'))


def _emp(pk):
    return M('EmpManagement', 'emp_master').objects.filter(pk=pk).first()


def person(e):
    if e is None:
        return ''
    n = ' '.join(x for x in [e.emp_first_name, e.emp_middle_name, e.emp_last_name] if x and str(x).strip())
    return f'{n} ({e.emp_code})' if n else e.emp_code


# ------------------------------------------------------------------ policies
class LineSerializer(serializers.ModelSerializer):
    leave_type_name = serializers.SerializerMethodField()

    class Meta:
        model = LeavePolicyLine
        exclude = ['policy']

    def get_leave_type_name(self, o):
        return self.context.get('types', {}).get(o.leave_type_id, f'#{o.leave_type_id}')

    def validate_pay_slabs(self, v):
        try:
            v = [[float(a), float(b)] for a, b in (v or [])]
        except (TypeError, ValueError):
            raise serializers.ValidationError('Pay slabs are pairs of [days, pay %].')
        if any(a <= 0 or not 0 <= b <= 100 for a, b in v):
            raise serializers.ValidationError('Days must be more than 0 and pay % between 0 and 100.')
        return v

    def validate_service_steps(self, v):
        """v1.12.0: [{from_year, days, carry_forward_max}] – from service year 2 up, one step per year, sorted."""
        out = []
        for s in (v or []):
            if not isinstance(s, dict):
                raise serializers.ValidationError('Each step is {"from_year": …, "days": …}.')
            try:
                y, d = int(s.get('from_year')), float(s.get('days'))
            except (TypeError, ValueError):
                raise serializers.ValidationError('Each step needs a service year and days a year.')
            if y < 2:
                raise serializers.ValidationError('Steps start from service year 2 (year 1 uses the days a year of the line).')
            if d < 0 or d > 366:
                raise serializers.ValidationError(f'Service year {y}: days a year between 0 and 366.')
            cf = s.get('carry_forward_max')
            if cf in ('', None):
                cf = None
            else:
                try:
                    cf = float(cf)
                except (TypeError, ValueError):
                    raise serializers.ValidationError(f'Service year {y}: carry-forward limit must be a number.')
                if cf < 0:
                    raise serializers.ValidationError(f'Service year {y}: carry-forward limit cannot be negative.')
            out.append({'from_year': y, 'days': d, 'carry_forward_max': cf})
        years = [x['from_year'] for x in out]
        if len(years) != len(set(years)):
            raise serializers.ValidationError('Each service year once.')
        return sorted(out, key=lambda x: x['from_year'])

    def validate(self, data):
        prorate = data.get('prorate', getattr(self.instance, 'prorate', 'calendar'))
        accrual = data.get('accrual', getattr(self.instance, 'accrual', 'monthly'))
        if prorate == 'worked' and accrual != 'monthly':
            raise serializers.ValidationError({'prorate': 'Days worked needs leave earned monthly (yearly leave is given at the start of the year, before the days are worked).'})
        if data.get('service_steps') and accrual not in ('monthly', 'yearly'):
            raise serializers.ValidationError({'service_steps': 'Days by length of service need leave earned monthly or yearly.'})
        return data


class PolicySerializer(serializers.ModelSerializer):
    lines = LineSerializer(many=True, required=False)
    employees_count = serializers.SerializerMethodField()
    categories = serializers.SerializerMethodField()

    class Meta:
        model = LeavePolicy
        fields = '__all__'

    def get_employees_count(self, o):
        Emp = M('EmpManagement', 'emp_master')
        over = set(EmployeeLeavePolicy.objects.values_list('employee_id', flat=True))
        by_cat = Emp.objects.filter(is_active=True, emp_ctgry_id__in=o.category_ids or []).exclude(pk__in=over).count()
        return by_cat + o.employees.count()

    def get_categories(self, o):
        return list(M('OrganisationManager', 'ctgry_master').objects.filter(pk__in=o.category_ids or []).values_list('ctgry_title', flat=True))

    def validate(self, data):
        cats = data.get('category_ids')
        if cats is not None:
            others = LeavePolicy.objects.exclude(pk=getattr(self.instance, 'pk', None))
            taken = {c: p.name for p in others for c in (p.category_ids or [])}
            clash = [taken[c] for c in cats if c in taken]
            if clash:
                raise serializers.ValidationError({'category_ids': f'A category can be in one policy only; already in {", ".join(sorted(set(clash)))}.'})
        lines = self.initial_data.get('lines')
        if lines is not None:
            ids = [l.get('leave_type_id') for l in lines]
            if len(ids) != len(set(ids)):
                raise serializers.ValidationError({'lines': 'Each leave type once per policy.'})
        return data

    def to_internal_value(self, data):
        try:
            return super().to_internal_value(data)
        except serializers.ValidationError as e:
            det = e.detail if isinstance(e.detail, dict) else {}
            le = det.get('lines')
            if isinstance(le, dict):          # DRF gives {index: errors} for a nested list
                le = [le.get(str(i), le.get(i)) for i in range(len(data.get('lines') or []))] if isinstance(data, dict) else le
            if isinstance(le, list) and isinstance(data, dict) and isinstance(data.get('lines'), list):
                names = _type_names()
                msgs = []
                for raw, err in zip(data['lines'], le):
                    if err:
                        nm = names.get(raw.get('leave_type_id'), f"#{raw.get('leave_type_id')}")
                        for f, v in (err.items() if isinstance(err, dict) else [('', err)]):
                            for m in (v if isinstance(v, list) else [v]):
                                msgs.append(f'{nm}: {m}')
                if msgs:
                    raise serializers.ValidationError({'detail': ' '.join(msgs), 'lines': le})
            raise

    @transaction.atomic
    def _save_lines(self, policy, lines):
        if lines is None:
            return
        keep = []
        for l in lines:
            l = dict(l)
            l.pop('id', None)
            obj, _ = LeavePolicyLine.objects.update_or_create(policy=policy, leave_type_id=l.pop('leave_type_id'), defaults=l)
            keep.append(obj.pk)
        policy.lines.exclude(pk__in=keep).delete()

    def create(self, data):
        lines = data.pop('lines', None)
        if data.get('is_default'):
            LeavePolicy.objects.update(is_default=False)
        p = LeavePolicy.objects.create(**data)
        self._save_lines(p, lines)
        return p

    def update(self, inst, data):
        lines = data.pop('lines', None)
        if data.get('is_default'):
            LeavePolicy.objects.exclude(pk=inst.pk).update(is_default=False)
        for k, v in data.items():
            setattr(inst, k, v)
        inst.save()
        self._save_lines(inst, lines)
        return inst


class PolicyViewSet(viewsets.ModelViewSet):
    permission_classes = [LeaveHR]
    serializer_class = PolicySerializer
    queryset = LeavePolicy.objects.prefetch_related('lines', 'employees')

    def get_serializer_context(self):
        return {**super().get_serializer_context(), 'types': _type_names()}


class EmployeePolicySerializer(serializers.ModelSerializer):
    employee = serializers.SerializerMethodField()
    policy_name = serializers.CharField(source='policy.name', read_only=True)

    class Meta:
        model = EmployeeLeavePolicy
        fields = ['id', 'employee_id', 'employee', 'policy', 'policy_name', 'note', 'assigned_at']

    def get_employee(self, o):
        return person(_emp(o.employee_id))

    def validate_employee_id(self, v):
        if not _emp(v):
            raise serializers.ValidationError('Employee not found.')
        return v


class EmployeePolicyViewSet(viewsets.ModelViewSet):
    permission_classes = [LeaveHR]
    serializer_class = EmployeePolicySerializer
    queryset = EmployeeLeavePolicy.objects.select_related('policy')


# ------------------------------------------------------------------ an employee's effective policy
class EffectiveView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        c = _ctx(request)
        emp = _emp(request.query_params.get('employee')) if request.query_params.get('employee') else c.emp
        if emp is None:
            return Response({'detail': 'Employee not found.'}, status=404)
        if not (c.admin or (c.emp and c.emp.pk == emp.pk) or (c.codes & READ and (c.branches is None or emp.emp_branch_id_id in c.branches))):
            return Response({'detail': 'You may not see this employee.'}, status=403)
        p = engine.policy_for(emp)
        names = _type_names()
        out = {'employee': person(emp), 'employee_id': emp.pk, 'policy': p.name if p else None, 'policy_id': p.pk if p else None,
               'how': ('assigned to the employee' if EmployeeLeavePolicy.objects.filter(employee_id=emp.pk).exists() else
                       'by category' if p and emp.emp_ctgry_id_id in (p.category_ids or []) else 'default policy') if p else 'no policy',
               'service_months': engine.months_of_service(emp, date.today()), 'lines': []}
        for l in (p.lines.all() if p else []):
            bal = float(engine.balance_row(emp.pk, l.leave_type_id).balance or 0)
            taken = None
            if l.accrual in ('per_event', 'none'):   # no running balance: show what was taken this leave year
                ys, ye = engine.leave_year(p, date.today())
                taken, bal = float(engine.used_in(emp.pk, l.leave_type_id, ys, ye) or 0), None
            now_days, st = engine.step_on(l, emp, date.today())
            out['lines'].append({'leave_type': names.get(l.leave_type_id), 'leave_type_id': l.leave_type_id, 'days_per_year': now_days,
                                 'service_year': engine.service_year(emp, date.today()), 'step_from_year': st[0] if st else None,
                                 'accrual': l.get_accrual_display(), 'balance': bal, 'taken': taken, 'eligible': (l.gender in ('B', emp.emp_gender or '')) and
                                 engine.months_of_service(emp, date.today()) >= l.min_service_months})
        return Response(out)


# ------------------------------------------------------------------ UAE setup, accrual, year end, ledger start
class UAESetupView(APIView):
    permission_classes = [LeaveHR]

    def get(self, request):
        return Response(setup.preview())

    def post(self, request):
        return Response(setup.load(request.user))


class AccrualView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not can(request, RUN):
            return Response({'detail': 'You may not run leave accrual.'}, status=403)
        m = request.data.get('month')       # 'YYYY-MM' (the month to credit)
        on = None
        if m:
            try:
                y, mm = map(int, str(m).split('-')[:2])
                on = date(y, mm, cal.monthrange(y, mm)[1])
            except ValueError:
                return Response({'detail': 'Month as YYYY-MM.'}, status=400)
        return Response(engine.run_accrual(on, request.user.pk, preview=bool(request.data.get('preview'))))


class YearEndView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not can(request, RUN):
            return Response({'detail': 'You may not run the leave year end.'}, status=403)
        d = parse_date(str(request.data.get('date') or '')) if request.data.get('date') else None
        r = engine.run_year_end(d, request.user.pk, preview=bool(request.data.get('preview')))
        names = _type_names()
        for x in r['lines']:
            x['leave_type'] = names.get(x['leave_type_id'])
        return Response(r)


class LedgerStartView(APIView):
    """Start the ledger from today's balances (an opening line per employee and leave type where the ledger is empty)."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not can(request, RUN):
            return Response({'detail': 'You may not do this.'}, status=403)
        d = parse_date(str(request.data.get('date') or '')) or date.today()
        n = 0
        for b in M('calendars', 'emp_leave_balance').objects.all():
            if not LeaveLedger.objects.filter(employee_id=b.employee_id, leave_type_id=b.leave_type_id).exists() and b.balance:
                LeaveLedger.objects.create(employee_id=b.employee_id, leave_type_id=b.leave_type_id, date=d, kind='opening',
                                           days=float(b.balance), note='Balance when the ledger started', created_by_id=request.user.pk)
                n += 1
        return Response({'opening_lines': n})


class OpeningsView(APIView):
    """POST {"date": "YYYY-MM-DD", "rows": [{"employee_code", "leave_type", "days"}], "mode": "set"|"add"} – opening balances with a ledger line."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not can(request, RUN | {'add_emp_leave_balance', 'change_emp_leave_balance', 'import_emp_leave_balance'}):
            return Response({'detail': 'You may not load opening balances.'}, status=403)
        d = parse_date(str(request.data.get('date') or '')) or date.today()
        mode = request.data.get('mode') or 'set'
        Emp, LT = M('EmpManagement', 'emp_master'), M('calendars', 'leave_type')
        types = {t.name.lower(): t for t in LT.objects.all()} | {t.code.lower(): t for t in LT.objects.all()}
        done, errors = 0, []
        for i, r in enumerate(request.data.get('rows') or [], 1):
            e = Emp.objects.filter(emp_code__iexact=str(r.get('employee_code', '')).strip()).first()
            t = types.get(str(r.get('leave_type', '')).strip().lower())
            try:
                days = float(r.get('days'))
            except (TypeError, ValueError):
                days = None
            if not e or not t or days is None:
                errors.append(f'Row {i}: ' + ('employee not found' if not e else 'leave type not found' if not t else 'days must be a number'))
                continue
            cur = float(engine.balance_row(e.pk, t.pk).balance or 0)
            delta = days - cur if mode == 'set' else days
            if delta:
                engine.post(e.pk, t.pk, d, 'opening', delta, note=f'Opening balance ({mode} {days:g})', user_id=request.user.pk, move_balance=True)
            done += 1
        return Response({'loaded': done, 'errors': errors})


class LedgerView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        c = _ctx(request)
        emp = _emp(request.query_params.get('employee')) if request.query_params.get('employee') else c.emp
        if emp is None:
            return Response({'detail': 'Employee not found.'}, status=404)
        if not (c.admin or (c.emp and c.emp.pk == emp.pk) or (c.codes & READ and (c.branches is None or emp.emp_branch_id_id in c.branches))):
            return Response({'detail': 'You may not see this employee.'}, status=403)
        qs = LeaveLedger.objects.filter(employee_id=emp.pk)
        if request.query_params.get('leave_type'):
            qs = qs.filter(leave_type_id=request.query_params['leave_type'])
        names, run, rows = _type_names(), {}, []
        for l in qs.order_by('leave_type_id', 'date', 'id'):
            run[l.leave_type_id] = round(run.get(l.leave_type_id, 0) + l.days, 2)
            rows.append({'date': l.date, 'leave_type': names.get(l.leave_type_id), 'kind': l.get_kind_display(), 'days': l.days,
                         'balance': run[l.leave_type_id], 'note': l.note})
        return Response({'employee': person(emp), 'rows': rows})


# ------------------------------------------------------------------ leave planner
class PlannerView(APIView):
    """GET ?from=YYYY-MM-DD&to=…&branch=1,2&department=&leave_type=&status=approved,pending – who is away when."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        c = _ctx(request)
        q = request.query_params
        d0 = parse_date(q.get('from') or '') or date.today().replace(day=1)
        d1 = parse_date(q.get('to') or '') or (d0.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        if (d1 - d0).days > 120:
            return Response({'detail': 'Choose at most 4 months.'}, status=400)
        Emp = M('EmpManagement', 'emp_master')
        emps = Emp.objects.filter(is_active=True).select_related('emp_branch_id', 'emp_dept_id', 'emp_desgntn_id')
        team_only = not (c.admin or c.codes & READ)
        if team_only:
            from DashboardManagement.services import team_ids
            ids = set(team_ids(request.user)) | ({c.emp.pk} if c.emp else set())
            emps = emps.filter(pk__in=ids)
        if c.branches is not None and not team_only:
            emps = emps.filter(emp_branch_id__in=c.branches)
        branches = [int(x) for x in (q.get('branch') or '').split(',') if x.strip().isdigit()]
        if branches:
            emps = emps.filter(emp_branch_id__in=branches)
        if q.get('department'):
            emps = emps.filter(emp_dept_id=q['department'])
        statuses = [s for s in (q.get('status') or 'approved,pending').split(',') if s in ('approved', 'pending')]
        LR = M('calendars', 'employee_leave_request')
        reqs = LR.objects.filter(employee__in=emps, status__in=statuses, start_date__lte=d1, end_date__gte=d0).select_related('leave_type')
        if q.get('leave_type'):
            reqs = reqs.filter(leave_type_id=q['leave_type'])
        by_emp = {}
        for r in reqs:
            by_emp.setdefault(r.employee_id, []).append({'id': r.pk, 'number': r.document_number, 'type': r.leave_type.name, 'type_id': r.leave_type_id,
                                                         'category': r.leave_type.leave_category, 'from': max(r.start_date, d0), 'to': min(r.end_date, d1),
                                                         'start': r.start_date, 'end': r.end_date, 'days': float(r.approved_days or r.applied_days or r.number_of_days or 0),
                                                         'status': r.status, 'half': r.dis_half_day})
        hol = set()
        rows = []
        for e in emps.order_by('emp_dept_id__dept_name', 'emp_first_name'):
            rows.append({'id': e.pk, 'employee': person(e), 'department': e.emp_dept_id.dept_name if e.emp_dept_id_id else '',
                         'branch': e.emp_branch_id.branch_name if e.emp_branch_id_id else '', 'leaves': by_emp.get(e.pk, [])})
        # public holidays and weekends of the first employee's calendars (company calendars are usually shared)
        first = emps.first()
        wk = sorted(engine._weekend_days(first)) if first else []
        if first:
            hol = sorted(d for d in engine._holidays(first) if d0 <= d <= d1)
        away = {}
        for r in rows:
            for l in r['leaves']:
                d = l['from']
                while d <= l['to']:
                    away[d.isoformat()] = away.get(d.isoformat(), 0) + 1
                    d += timedelta(days=1)
        types = sorted({(l['type_id'], l['type'], l['category']) for r in rows for l in r['leaves']})
        return Response({'from': d0, 'to': d1, 'weekend': wk, 'holidays': hol, 'rows': rows, 'away': away,
                         'leave_types': [{'id': i, 'name': n, 'category': k} for i, n, k in types], 'headcount': len(rows)})


# ------------------------------------------------------------------ encashment approval
class EncashmentView(APIView):
    """GET ?status=pending – encashments with their checks; POST {id, action: approve|reject, remarks}."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not can(request, ENCASH | {'view_leaveencashment'}):
            return Response({'detail': 'You may not see encashments.'}, status=403)
        c = _ctx(request)
        LE = M('PayrollManagement', 'LeaveEncashment')
        qs = LE.objects.select_related('employee', 'leave_type', 'payroll_run').order_by('-id')
        if request.query_params.get('status'):
            qs = qs.filter(status__in=request.query_params['status'].split(','))
        if c.branches is not None:
            qs = qs.filter(employee__emp_branch_id__in=c.branches)
        out = []
        for e in qs[:500]:
            out.append({'id': e.pk, 'employee': person(e.employee), 'employee_id': e.employee_id, 'leave_type': e.leave_type.name,
                        'days': float(e.encashment_days), 'amount': float(e.encashment_amount), 'basic': float(e.basic_salary),
                        'balance_now': float(engine.balance_row(e.employee_id, e.leave_type_id).balance or 0), 'status': e.get_status_display(),
                        'requested': e.created_at.date() if getattr(e, 'created_at', None) else None, 'remarks': e.remarks or '',
                        'payroll_run': str(e.payroll_run) if e.payroll_run_id else '', 'problems': engine.check_encashment(e) if e.status in ('draft', 'pending') else []})
        return Response({'rows': out})

    def post(self, request):
        if not can(request, ENCASH):
            return Response({'detail': 'You may not approve encashments.'}, status=403)
        LE = M('PayrollManagement', 'LeaveEncashment')
        e = LE.objects.select_related('employee', 'leave_type').filter(pk=request.data.get('id')).first()
        if e is None:
            return Response({'detail': 'Encashment not found.'}, status=404)
        c = _ctx(request)
        if c.branches is not None and e.employee.emp_branch_id_id not in c.branches:
            return Response({'detail': 'Encashment not found.'}, status=404)
        if e.status not in ('draft', 'pending'):
            return Response({'detail': f'This encashment is already {e.get_status_display().lower()}.'}, status=400)
        act = request.data.get('action')
        if act == 'reject':
            e.status, e.remarks = 'rejected', (request.data.get('remarks') or e.remarks or '')[:500]
            e.save(update_fields=['status', 'remarks'])
            return Response({'status': e.get_status_display()})
        if act != 'approve':
            return Response({'detail': 'action is approve or reject.'}, status=400)
        errs = engine.check_encashment(e)
        if errs:
            return Response({'detail': ' '.join(errs), 'problems': errs}, status=400)
        with transaction.atomic():
            days = float(e.encashment_days)
            if not float(e.encashment_amount or 0):      # keep the amount of the company formula; else basic / 30 × days
                basic = engine.basic_of(e.employee) or float(e.basic_salary or 0)
                e.basic_salary = basic
                e.encashment_amount = round(basic / float(e.fixed_days or 30) * days, 2)
            e.status, e.approved_by, e.approved_at = 'approved', request.user, timezone.now()
            if request.data.get('remarks'):
                e.remarks = str(request.data['remarks'])[:500]
            e.save()
            engine.post(e.employee_id, e.leave_type_id, date.today(), 'encashed', -days, ref=e,
                        note=f'Encashed {days:g} days – AED {float(e.encashment_amount):,.2f} (paid with the next payroll)', user_id=request.user.pk, move_balance=True)
        return Response({'status': e.get_status_display(), 'amount': float(e.encashment_amount)})


# ------------------------------------------------------------------ v1.11.0: approvers by role, escalation, compensatory off
APPROVE_CODES = {'change_lvapprovalworkflow', 'add_lvapprovalworkflow', 'change_leaveapprovallevels', 'add_leaveapprovallevels',
                 'change_leave_escalation', 'add_leave_escalation', 'change_leavepolicy'}


def _uname(u):
    if u is None:
        return ''
    full = ' '.join(x for x in [getattr(u, 'first_name', ''), getattr(u, 'last_name', '')] if x)
    return f'{full} ({u.username})' if full else u.username


class ApproversView(APIView):
    """GET: who holds each role, leave workflows with their levels, users to choose from.
    POST {org_roles: [{role, branch_id, department_id, user_id|null}]} and/or {levels: [{level_id, role, escalate_role,
    approver_id, escalate_to_id, escalate_after_days, escalate_after_hours, escalate_after_minutes}]}"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not can(request, READ | APPROVE_CODES):
            return Response({'detail': 'You may not see the approvers.'}, status=403)
        from .models import LevelRole, OrgRole
        from .approvers import resolve
        U = M('UserManagement', 'CustomUser')
        users = {u.pk: u for u in U.objects.filter(is_active=True).order_by('username')}
        roles = [{'role': r.role, 'branch_id': r.branch_id, 'department_id': r.department_id, 'user_id': r.user_id,
                  'user': _uname(users.get(r.user_id)) or f'#{r.user_id} (inactive)'} for r in OrgRole.objects.all()]
        WF = M('calendars', 'LVApprovalWorkflow')
        lr = {x.level_id: x for x in LevelRole.objects.all()}
        wfs = []
        for w in WF.objects.select_related('request_type').prefetch_related('branch', 'leave_levels').order_by('request_type__name'):
            lv = []
            for l in w.leave_levels.all().order_by('level'):
                r = lr.get(l.pk)
                lv.append({'level_id': l.pk, 'level': l.level, 'role': r.role if r else 'user', 'escalate_role': r.escalate_role if r else '',
                           'approver_id': l.approver_id, 'approver': _uname(l.approver), 'escalate_to_id': l.escalate_to_id,
                           'escalate_after_days': l.escalate_after_days, 'escalate_after_hours': l.escalate_after_hours,
                           'escalate_after_minutes': l.escalate_after_minutes})
            wfs.append({'id': w.pk, 'leave_type': w.request_type.name, 'leave_type_id': w.request_type_id, 'approval_type': w.approval_type,
                        'approval_type_label': w.get_approval_type_display(), 'branches': [b.branch_name for b in w.branch.all()], 'levels': lv})
        # a sample: who would approve for each employee at level 1 of their annual leave workflow
        Br = M('OrganisationManager', 'brnch_mstr'); D = M('OrganisationManager', 'dept_master')
        return Response({'org_roles': roles, 'workflows': wfs, 'roles': LevelRole.ROLES, 'org_role_kinds': OrgRole.ROLES,
                         'users': [{'id': u.pk, 'name': _uname(u)} for u in users.values()],
                         'branches': [{'id': b.pk, 'name': b.branch_name} for b in Br.objects.all().order_by('branch_name')],
                         'departments': [{'id': d.pk, 'name': d.dept_name} for d in D.objects.all().order_by('dept_name')]})

    @transaction.atomic
    def post(self, request):
        if not can(request, APPROVE_CODES):
            return Response({'detail': 'You may not change the approvers.'}, status=403)
        from .models import LevelRole, OrgRole
        L = M('calendars', 'LeaveApprovalLevels')
        U = M('UserManagement', 'CustomUser')
        valid_roles = {k for k, _ in LevelRole.ROLES}
        for r in request.data.get('org_roles') or []:
            key = dict(role=r.get('role'), branch_id=r.get('branch_id') or None, department_id=r.get('department_id') or None)
            if key['role'] not in {k for k, _ in OrgRole.ROLES}:
                return Response({'detail': f'Unknown role {key["role"]}.'}, status=400)
            if key['role'] == 'branch_hr' and not key['branch_id']:
                return Response({'detail': 'Branch HR manager needs a branch.'}, status=400)
            if key['role'] == 'department_head' and not key['department_id']:
                return Response({'detail': 'Department head needs a department.'}, status=400)
            if not r.get('user_id'):
                OrgRole.objects.filter(**key).delete()
                continue
            if not U.objects.filter(pk=r['user_id'], is_active=True).exists():
                return Response({'detail': 'Choose an active user.'}, status=400)
            OrgRole.objects.update_or_create(**key, defaults={'user_id': r['user_id']})
        for x in request.data.get('levels') or []:
            lvl = L.objects.filter(pk=x.get('level_id')).first()
            if lvl is None:
                return Response({'detail': 'Approval level not found.'}, status=400)
            role = x.get('role') or 'user'
            esc = x.get('escalate_role') or ''
            if role not in valid_roles or (esc and esc not in valid_roles):
                return Response({'detail': 'Unknown approver role.'}, status=400)
            if role == 'user' and not (x.get('approver_id') or lvl.approver_id):
                return Response({'detail': f'Level {lvl.level}: choose the user or a role.'}, status=400)
            LevelRole.objects.update_or_create(level_id=lvl.pk, defaults={'role': role, 'escalate_role': esc})
            upd = {}
            if 'approver_id' in x:
                upd['approver_id'] = x['approver_id'] or None
            if 'escalate_to_id' in x:
                upd['escalate_to_id'] = x['escalate_to_id'] or None
            for k in ('escalate_after_days', 'escalate_after_hours', 'escalate_after_minutes'):
                if k in x:
                    upd[k] = max(int(x[k] or 0), 0)
            if upd:
                L.objects.filter(pk=lvl.pk).update(**upd)
        return Response({'ok': True})


class WhoApprovesView(APIView):
    """GET ?employee=&leave_type= – who the request would go to now, level by level (for checking the setup)."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not can(request, READ | APPROVE_CODES):
            return Response({'detail': 'You may not see the approvers.'}, status=403)
        from .approvers import resolve
        emp = _emp(request.query_params.get('employee'))
        LT = M('calendars', 'leave_type').objects.filter(pk=request.query_params.get('leave_type')).first()
        if emp is None or LT is None:
            return Response({'detail': 'Choose an employee and a leave type.'}, status=400)
        WF = M('calendars', 'LVApprovalWorkflow')
        w = WF.objects.filter(request_type=LT, branch__in=[emp.emp_branch_id]).first()
        if w is None:
            return Response({'employee': person(emp), 'workflow': None, 'steps': [], 'note': 'No workflow for this branch – the request is approved straight away.'})
        steps = []
        if w.approval_type == 'reporting_manager':
            u, how = resolve(None, emp)
            steps.append({'level': 1, 'approver': _uname(u), 'how': how})
        elif w.approval_type == 'multi_approval':
            for l in w.leave_levels.all().order_by('level'):
                u, how = resolve(l, emp)
                steps.append({'level': l.level, 'approver': _uname(u), 'how': how})
        return Response({'employee': person(emp), 'workflow': w.get_approval_type_display(), 'steps': steps})


class EscalationView(APIView):
    """GET: approvals due for escalation now (nothing changes). POST: escalate them now."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not can(request, READ | APPROVE_CODES):
            return Response({'detail': 'You may not see escalations.'}, status=403)
        from .approvers import escalate_due
        return Response({'due': escalate_due(dry=True)})

    def post(self, request):
        if not can(request, APPROVE_CODES):
            return Response({'detail': 'You may not run escalations.'}, status=403)
        from .approvers import escalate_due
        return Response({'escalated': escalate_due()})
