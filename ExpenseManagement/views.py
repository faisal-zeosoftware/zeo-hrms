"""
Expense management API (mounted at /expense/).

Rights (the central AccessControl layer is switched off for these views – the same rules are applied here,
because the tables keep employee ids instead of foreign keys):
  * every user of the company may read categories, policies and cost centres; changing them needs
    add_ / change_ / delete_<model> (or company admin);
  * employees see and change only their own expenses, reports, trips and advances while they are drafts;
  * approvers see the reports / trips that wait (or waited) for them and act only on the step assigned to them;
  * HR / finance with view_<model> see all rows of their branches; reimbursing needs reimburse_expensereport
    or change_expensereport, the accounting export export_expensereport or view_expensereport, advances
    are approved / paid with change_expenseadvance.
"""
from datetime import date

from django.db import connection
from django.db.models import Q, Sum
from django.http import HttpResponse
from django.utils.dateparse import parse_date
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services as S
from .models import (ZERO, CostCenter, Expense, ExpenseAdvance, ExpenseApproval, ExpenseCategory, ExpensePolicy, ExpensePolicyLimit,
                     ExpenseReport, ExpenseSettings, Trip)


# ------------------------------------------------------------------ access helpers
def _ctx(request):
    from AccessControl.access import ctx
    return ctx(request)


def can(request, *codes):
    try:
        c = _ctx(request)
        return c.admin or any(x in c.codes for x in codes)
    except Exception:
        return False


def is_admin(request):
    try:
        return _ctx(request).admin
    except Exception:
        return False


def branches(request):
    try:
        c = _ctx(request)
        return None if c.admin else c.branches
    except Exception:
        return []


def my_emp(request):
    try:
        return _ctx(request).emp
    except Exception:
        return S.employee_of_user(request.user)


def _schema(request):
    if connection.schema_name != 'public':
        return connection.schema_name
    return request.GET.get('schema')


class CompanyMember(BasePermission):
    """Logged in and a user of the company in ?schema=."""
    message = 'You do not have access to this company.'

    def has_permission(self, request, view):
        u = request.user
        if not u or not u.is_authenticated:
            self.message = 'Please log in.'
            return False
        sch = _schema(request)
        if not sch or sch == 'public':
            self.message = 'Choose a company.'
            return False
        return u.is_superuser or u.tenants.filter(schema_name=sch).exists()


class SetupRights(CompanyMember):
    """Reading for all users of the company; changing needs add_/change_/delete_<model>."""

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            return True
        model = view.queryset.model._meta.model_name
        verb = {'POST': 'add', 'DELETE': 'delete'}.get(request.method, 'change')
        self.message = f'You may not {verb} {view.queryset.model._meta.verbose_name}.'
        return can(request, f'{verb}_{model}')


def err(e):
    return Response({'detail': e.message, 'problems': e.problems}, status=e.status)


class Base:
    zeo_access = False     # rights are checked here (see the module docstring)
    zeo_scope = False


def _names_ctx(emp_ids):
    from django.apps import apps
    E = apps.get_model('EmpManagement', 'emp_master')
    return {e.pk: S.person(e) for e in E.objects.filter(pk__in={i for i in emp_ids if i})}


class NamesMixin:
    def emp_name(self, obj):
        names = self.context.setdefault('emp_names', {})
        if obj.employee_id not in names:
            names.update(_names_ctx([obj.employee_id]))
        return names.get(obj.employee_id, '')


# ------------------------------------------------------------------ setup: categories, policies, cost centres, settings
class CategorySerializer(serializers.ModelSerializer):
    kind_label = serializers.CharField(source='get_kind_display', read_only=True)

    class Meta:
        model = ExpenseCategory
        fields = '__all__'


class CategoryViewSet(Base, viewsets.ModelViewSet):
    queryset = ExpenseCategory.objects.all()
    serializer_class = CategorySerializer
    permission_classes = [SetupRights]

    def get_queryset(self):
        qs = super().get_queryset()
        a = self.request.query_params.get('active')
        if a in ('true', '1'):
            qs = qs.filter(active=True)
        return qs

    def destroy(self, request, *a, **k):
        if self.get_object().expenses.exists():
            return Response({'detail': 'The category is used by expenses – set it inactive instead.'}, status=400)
        return super().destroy(request, *a, **k)


class LimitSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)

    class Meta:
        model = ExpensePolicyLimit
        fields = '__all__'
        extra_kwargs = {'policy': {'required': False}}
        validators = []      # nested under a policy the policy is not posted; checked below instead

    def validate(self, data):
        pol = data.get('policy') or getattr(self.instance, 'policy', None)
        cat = data.get('category') or getattr(self.instance, 'category', None)
        if pol is not None and cat is not None:
            dup = ExpensePolicyLimit.objects.filter(policy=pol, category=cat)
            if self.instance is not None:
                dup = dup.exclude(pk=self.instance.pk)
            if dup.exists():
                raise serializers.ValidationError(f'The policy already has a limit for {cat.name}.')
        return data


class PolicySerializer(serializers.ModelSerializer):
    limits = LimitSerializer(many=True, required=False)

    class Meta:
        model = ExpensePolicy
        fields = '__all__'

    def validate_approval_roles(self, v):
        ok = set(S.ROLE_LABELS)
        bad = [r for r in (v or []) if r not in ok]
        if bad:
            raise serializers.ValidationError(f'Unknown role(s): {", ".join(bad)}. Use {", ".join(sorted(ok))}.')
        return v

    def _limits(self, pol, rows):
        if rows is None:
            return
        pol.limits.all().delete()
        for r in rows:
            r.pop('policy', None)
            ExpensePolicyLimit.objects.create(policy=pol, **r)

    def create(self, data):
        rows = data.pop('limits', None)
        pol = super().create(data)
        self._limits(pol, rows)
        self._one_default(pol)
        return pol

    def update(self, inst, data):
        rows = data.pop('limits', None)
        pol = super().update(inst, data)
        self._limits(pol, rows)
        self._one_default(pol)
        return pol

    @staticmethod
    def _one_default(pol):
        if pol.is_default:
            ExpensePolicy.objects.exclude(pk=pol.pk).filter(is_default=True).update(is_default=False)


class PolicyViewSet(Base, viewsets.ModelViewSet):
    queryset = ExpensePolicy.objects.prefetch_related('limits__category')
    serializer_class = PolicySerializer
    permission_classes = [SetupRights]

    @action(detail=False, methods=['get'])
    def mine(self, request):
        """The policy of the logged-in employee (or ?employee=<id> for HR) with mileage / per diem rates."""
        emp = my_emp(request)
        if request.query_params.get('employee') and can(request, 'view_expense', 'add_expense'):
            emp = S.employee(request.query_params['employee'])
        pol = S.policy_for(emp)
        return Response(PolicySerializer(pol).data if pol else {})


class LimitRights(CompanyMember):
    """Limits belong to the policy: changing them needs the policy (or limit) rights."""

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            return True
        verb = {'POST': 'add', 'DELETE': 'delete'}.get(request.method, 'change')
        self.message = 'You may not change expense policies.'
        return can(request, f'{verb}_expensepolicylimit', 'change_expensepolicy')


class LimitViewSet(Base, viewsets.ModelViewSet):
    """Limits one by one (child table of a policy page): ?policy=<id>."""
    queryset = ExpensePolicyLimit.objects.select_related('category')
    serializer_class = LimitSerializer
    permission_classes = [LimitRights]

    def get_queryset(self):
        qs = super().get_queryset()
        p = self.request.query_params.get('policy')
        return qs.filter(policy_id=p) if p else qs


class CostCenterSerializer(serializers.ModelSerializer):
    department_name = serializers.SerializerMethodField()

    class Meta:
        model = CostCenter
        fields = '__all__'

    def get_department_name(self, o):
        from django.apps import apps
        d = apps.get_model('OrganisationManager', 'dept_master').objects.filter(pk=o.department_id).first() if o.department_id else None
        return d.dept_name if d else ''


class CostCenterViewSet(Base, viewsets.ModelViewSet):
    queryset = CostCenter.objects.all()
    serializer_class = CostCenterSerializer
    permission_classes = [SetupRights]

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.query_params.get('active') in ('true', '1'):
            qs = qs.filter(active=True)
        return qs


class SettingsView(Base, APIView):
    permission_classes = [CompanyMember]

    def get(self, request):
        s = ExpenseSettings.get()
        return Response({'payable_account': s.payable_account, 'advance_account': s.advance_account, 'default_expense_account': s.default_expense_account})

    def put(self, request):
        if not can(request, 'change_expensesettings', 'change_expensepolicy'):
            return Response({'detail': 'You may not change the expense settings.'}, status=403)
        s = ExpenseSettings.get()
        for k in ('payable_account', 'advance_account', 'default_expense_account'):
            if request.data.get(k):
                setattr(s, k, str(request.data[k])[:50])
        s.save()
        return self.get(request)

    post = put


# ------------------------------------------------------------------ scoped records
class ScopedViewSet(Base, viewsets.ModelViewSet):
    """Own rows; rows waiting for me (approvers); all rows of my branches with view_<model>."""
    permission_classes = [CompanyMember]
    approver_path = None      # ORM path to ExpenseApproval.approver_user_id

    def model_name(self):
        return self.queryset.model._meta.model_name

    def hr(self, verb='view'):
        return can(self.request, f'{verb}_{self.model_name()}')

    def get_queryset(self):
        qs = super().get_queryset()
        r, p = self.request, self.request.query_params
        emp = my_emp(r)
        if p.get('mine') in ('1', 'true') or not (self.hr() or is_admin(r)):
            cond = Q(employee_id=emp.pk) if emp else Q(pk__in=[])
            if self.approver_path and p.get('mine') not in ('1', 'true'):
                cond |= Q(**{self.approver_path: r.user.pk})
            qs = qs.filter(cond).distinct()
        else:
            b = branches(r)
            if b is not None:
                qs = qs.filter(Q(branch_id__in=b) | Q(branch_id__isnull=True))
        if p.get('status'):
            qs = qs.filter(status__in=p['status'].split(','))
        if p.get('employee'):
            qs = qs.filter(employee_id=p['employee'])
        return qs

    def get_serializer_context(self):
        c = super().get_serializer_context()
        c['emp_names'] = {}
        return c

    def list(self, request, *a, **k):
        qs = self.filter_queryset(self.get_queryset())[:1000]
        rows = list(qs)
        ctx = self.get_serializer_context()
        ctx['emp_names'] = _names_ctx([x.employee_id for x in rows])
        return Response(self.get_serializer_class()(rows, many=True, context=ctx).data)

    def target_employee(self, data):
        """Employee the new record is for: own, or (HR with add_<model>) any employee of their branches."""
        emp = my_emp(self.request)
        want = data.get('employee_id') or data.get('employee')
        if want and (emp is None or str(want) != str(emp.pk)):
            if not self.hr('add'):
                raise S.ExpenseError('You can only create records for yourself.', status=403)
            other = S.employee(want)
            if other is None:
                raise S.ExpenseError('Employee not found.')
            b = branches(self.request)
            if b is not None and other.emp_branch_id_id not in b:
                raise S.ExpenseError('You can only work with employees of your branches.', status=403)
            return other
        if emp is None:
            raise S.ExpenseError('Your login is not linked to an employee.', status=403)
        return emp

    def can_edit(self, obj, states):
        r = self.request
        emp = my_emp(r)
        own = emp is not None and obj.employee_id == emp.pk
        if obj.status not in states:
            raise S.ExpenseError(f'It is {obj.get_status_display().lower()} and cannot be changed now.')
        if own or is_admin(r) or self.hr('change'):
            return True
        raise S.ExpenseError('You can only change your own records.', status=403)

    def handle_exception(self, exc):
        if isinstance(exc, S.ExpenseError):
            return err(exc)
        return super().handle_exception(exc)


def _approval_rows(qs):
    from django.apps import apps
    U = apps.get_model('UserManagement', 'CustomUser')
    rows = list(qs)
    users = {u.pk: u for u in U.objects.filter(pk__in={x.approver_user_id for x in rows} | {x.acted_by_user_id for x in rows if x.acted_by_user_id})}
    out = []
    for a in rows:
        role = a.role.split('>')
        out.append({'id': a.pk, 'round': a.round, 'level': a.level, 'role': a.role,
                    'role_label': S.ROLE_LABELS.get(role[0], role[0]) + (f' (→ {S.ROLE_LABELS.get(role[1], role[1])})' if len(role) > 1 else ''),
                    'approver_user_id': a.approver_user_id, 'approver': S.user_name(a.approver_user_id) if a.approver_user_id in users else '',
                    'status': a.status, 'note': a.note, 'acted_at': a.acted_at,
                    'acted_by': S.user_name(a.acted_by_user_id) if a.acted_by_user_id else ''})
    return out


# ------------------------------------------------------------------ expenses
class ExpenseSerializer(NamesMixin, serializers.ModelSerializer):
    employee_name = serializers.SerializerMethodField()
    category_name = serializers.CharField(source='category.name', read_only=True)
    category_kind = serializers.CharField(source='category.kind', read_only=True)
    report_number = serializers.SerializerMethodField()
    report_status = serializers.SerializerMethodField()
    cost_center_name = serializers.SerializerMethodField()
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    has_block = serializers.SerializerMethodField()
    employee_id = serializers.IntegerField(required=False)

    class Meta:
        model = Expense
        fields = '__all__'
        read_only_fields = ('branch_id', 'amount_aed', 'status', 'policy_violations', 'created_by_id')
        extra_kwargs = {'date': {'required': False}}     # today when not given

    def get_employee_name(self, o):
        return self.emp_name(o)

    def get_report_number(self, o):
        return o.report.number if o.report_id else ''

    def get_report_status(self, o):
        return o.report.status if o.report_id else ''

    def get_cost_center_name(self, o):
        return str(o.cost_center) if o.cost_center_id else ''

    def get_has_block(self, o):
        return S.has_block(o.policy_violations)


class ExpenseViewSet(ScopedViewSet):
    queryset = Expense.objects.select_related('category', 'report', 'cost_center')
    serializer_class = ExpenseSerializer
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    approver_path = 'report__approvals__approver_user_id'

    def get_queryset(self):
        qs = super().get_queryset()
        p = self.request.query_params
        if p.get('unreported') in ('1', 'true'):
            qs = qs.filter(status='unreported', report__isnull=True)
        if p.get('report'):
            qs = qs.filter(report_id=p['report'])
        if p.get('from'):
            qs = qs.filter(date__gte=p['from'])
        if p.get('to'):
            qs = qs.filter(date__lte=p['to'])
        if p.get('category'):
            qs = qs.filter(category_id=p['category'])
        return qs

    def _build(self, ser, inst=None):
        data = dict(ser.validated_data)
        data.pop('employee_id', None)
        rep = data.get('report', inst.report if inst else None)
        if inst is None:
            emp = self.target_employee(self.request.data)
            inst = Expense(employee_id=emp.pk, created_by_id=self.request.user.pk)
        for k, v in data.items():
            setattr(inst, k, v)
        if rep is not None:
            if rep.employee_id != inst.employee_id or rep.status not in ('draft', 'sent_back'):
                raise S.ExpenseError('The expense can only go into a draft or sent-back report of the same employee.')
        if not inst.date:
            inst.date = date.today()
        S.prepare_expense(inst)
        if rep is not None:
            inst.policy_violations = S.check_expense(inst, rep.policy or S.policy_for(S.employee(inst.employee_id)), rep)
        return inst

    def create(self, request, *a, **k):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        inst = self._build(ser)
        inst.save()
        if inst.report_id:
            S.recalc_report(inst.report)
        return Response(self.get_serializer(inst).data, status=201)

    def update(self, request, *a, partial=False, **k):
        inst = self.get_object()
        self.can_edit(inst, ('unreported',))
        ser = self.get_serializer(inst, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        old_report = inst.report
        inst = self._build(ser, inst)
        inst.save()
        for r in {old_report, inst.report} - {None}:
            S.recalc_report(r)
        return Response(self.get_serializer(inst).data)

    def partial_update(self, request, *a, **k):
        return self.update(request, *a, **k)

    def destroy(self, request, *a, **k):
        inst = self.get_object()
        self.can_edit(inst, ('unreported',))
        rep = inst.report
        if inst.receipt:
            inst.receipt.delete(save=False)
        inst.delete()
        if rep:
            S.recalc_report(rep)
        return Response(status=204)

    @action(detail=False, methods=['post'])
    def check(self, request):
        """Live policy check of a form that is not saved yet: amounts in AED and violations."""
        data = request.data.copy() if hasattr(request.data, 'copy') else dict(request.data)
        inst = None
        if data.get('id'):
            inst = self.get_queryset().filter(pk=data.get('id')).first()
        ser = self.get_serializer(inst, data=data, partial=True)
        ser.is_valid(raise_exception=True)
        if not ser.validated_data.get('category') and not inst:
            return Response({'amount': None, 'amount_aed': None, 'violations': []})
        try:
            obj = self._build(ser, Expense.objects.select_related('category', 'report').get(pk=inst.pk) if inst else None)
        except S.ExpenseError as e:
            return Response({'amount': None, 'amount_aed': None, 'violations': [{'code': 'input', 'level': 'block', 'message': e.message}]})
        if not obj.receipt and (request.data.get('has_receipt') in ('1', 'true', True)):
            obj.policy_violations = [v for v in obj.policy_violations if v['code'] != 'receipt']
        pol = S.policy_for(S.employee(obj.employee_id))
        return Response({'amount': obj.amount, 'currency': obj.currency, 'exchange_rate': obj.exchange_rate, 'amount_aed': obj.amount_aed,
                         'violations': obj.policy_violations, 'blocked': S.has_block(obj.policy_violations),
                         'mileage_rate': pol.mileage_rate if pol else None, 'per_diem_rate': pol.per_diem_rate if pol else None})


# ------------------------------------------------------------------ reports
class ReportSerializer(NamesMixin, serializers.ModelSerializer):
    employee_name = serializers.SerializerMethodField()
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    trip_number = serializers.SerializerMethodField()
    expense_count = serializers.SerializerMethodField()
    waiting_for = serializers.SerializerMethodField()
    employee_id = serializers.IntegerField(required=False)
    expense_ids = serializers.ListField(child=serializers.IntegerField(), write_only=True, required=False)

    class Meta:
        model = ExpenseReport
        fields = '__all__'
        read_only_fields = ('number', 'branch_id', 'department_id', 'status', 'total', 'advance_applied', 'amount_to_reimburse',
                            'reimburse_via', 'reimbursed_on', 'reimbursement_reference', 'payroll_run_id', 'round', 'submitted_at',
                            'approved_at', 'created_by_id', 'policy')

    def get_employee_name(self, o):
        return self.emp_name(o)

    def get_trip_number(self, o):
        return o.trip.number if o.trip_id else ''

    def get_expense_count(self, o):
        return o.expenses.count()

    def get_waiting_for(self, o):
        s = S.current_step(o) if o.status == 'submitted' else None
        return S.user_name(s.approver_user_id) if s else ''


class ReportViewSet(ScopedViewSet):
    queryset = ExpenseReport.objects.select_related('trip', 'policy')
    serializer_class = ReportSerializer
    approver_path = 'approvals__approver_user_id'

    def get_queryset(self):
        qs = super().get_queryset()
        p = self.request.query_params
        if p.get('waiting') == 'me':
            qs = qs.filter(status='submitted', approvals__status='pending', approvals__approver_user_id=self.request.user.pk).distinct()
        if p.get('reimburse') in ('1', 'true'):
            qs = qs.filter(status='approved')
        return qs

    def flags(self, r):
        req = self.request
        emp = my_emp(req)
        own = emp is not None and r.employee_id == emp.pk
        step = S.current_step(r) if r.status == 'submitted' else None
        mine = step is not None and step.approver_user_id == req.user.pk
        fin = can(req, 'reimburse_expensereport', 'change_expensereport')
        return {'is_own': own,
                'can_edit': r.status in ('draft', 'sent_back') and (own or is_admin(req) or self.hr('change')),
                'can_submit': r.status in ('draft', 'sent_back') and (own or is_admin(req) or self.hr('change')),
                'can_approve': step is not None and not own and (mine or is_admin(req)),
                'can_reimburse': r.status == 'approved' and fin and not (r.reimburse_via == 'payroll' and r.payroll_run_id is None and r.reimbursement_reference),
                'payroll_pending': r.status == 'approved' and r.reimburse_via == 'payroll' and r.payroll_run_id is None}

    def detail_data(self, r):
        d = self.get_serializer(r).data
        ctx = self.get_serializer_context()
        d['lines'] = ExpenseSerializer(r.expenses.select_related('category', 'cost_center', 'report').order_by('date', 'id'), many=True, context=ctx).data
        d['approvals'] = _approval_rows(r.approvals.all())
        d['advances'] = [{'advance': a.advance.number, 'advance_id': a.advance_id, 'amount': a.amount, 'advance_amount': a.advance.amount}
                         for a in r.advance_lines.select_related('advance')]
        d['outstanding_advances'] = S.money(sum((S.advance_balance(a) for a in ExpenseAdvance.objects.filter(employee_id=r.employee_id, status='paid')), ZERO))
        d.update(self.flags(r))
        return d

    def retrieve(self, request, *a, **k):
        return Response(self.detail_data(self.get_object()))

    def create(self, request, *a, **k):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        emp = self.target_employee(request.data)
        data = dict(ser.validated_data)
        ids = data.pop('expense_ids', None)
        data.pop('employee_id', None)
        if data.get('trip') and data['trip'].employee_id != emp.pk:
            raise S.ExpenseError('The trip belongs to another employee.')
        r = ExpenseReport.objects.create(number=S.next_number(ExpenseReport, 'ER'), employee_id=emp.pk, branch_id=emp.emp_branch_id_id,
                                         department_id=emp.emp_dept_id_id, created_by_id=request.user.pk, policy=S.policy_for(emp), **data)
        if ids:
            S.add_expenses(r, ids)
        return Response(self.detail_data(r), status=201)

    def update(self, request, *a, **k):
        r = self.get_object()
        self.can_edit(r, ('draft', 'sent_back'))
        ser = self.get_serializer(r, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        data = dict(ser.validated_data)
        ids = data.pop('expense_ids', None)
        data.pop('employee_id', None)
        for k2, v in data.items():
            setattr(r, k2, v)
        r.save()
        if ids:
            S.add_expenses(r, ids)
        S.recalc_report(r)
        return Response(self.detail_data(r))

    def partial_update(self, request, *a, **k):
        return self.update(request, *a, **k)

    def destroy(self, request, *a, **k):
        r = self.get_object()
        self.can_edit(r, ('draft',))
        r.expenses.update(report=None, status='unreported')
        r.delete()
        return Response(status=204)

    @action(detail=True, methods=['post'])
    def add_expenses(self, request, pk=None):
        r = self.get_object()
        self.can_edit(r, ('draft', 'sent_back'))
        S.add_expenses(r, request.data.get('expense_ids') or [])
        return Response(self.detail_data(r))

    @action(detail=True, methods=['post'])
    def remove_expense(self, request, pk=None):
        r = self.get_object()
        self.can_edit(r, ('draft', 'sent_back'))
        e = r.expenses.filter(pk=request.data.get('expense_id')).first()
        if e is None:
            raise S.ExpenseError('The expense is not in this report.')
        S.remove_expense(r, e)
        return Response(self.detail_data(r))

    @action(detail=True, methods=['post'])
    def submit(self, request, pk=None):
        r = self.get_object()
        self.can_edit(r, ('draft', 'sent_back'))
        return Response(self.detail_data(S.submit_report(r, request.user)))

    def _act(self, request, act):
        r = self.get_object()
        return Response(self.detail_data(S.act_report(r, request.user, act, request.data.get('note') or request.data.get('reason') or '',
                                                      is_admin=is_admin(request))))

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        return self._act(request, 'approve')

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        return self._act(request, 'reject')

    @action(detail=True, methods=['post'])
    def send_back(self, request, pk=None):
        return self._act(request, 'send_back')

    @action(detail=True, methods=['post'])
    def reimburse(self, request, pk=None):
        if not can(request, 'reimburse_expensereport', 'change_expensereport'):
            return Response({'detail': 'You may not reimburse expense reports.'}, status=403)
        r = self.get_object()
        on = parse_date(str(request.data.get('date') or '')) if request.data.get('date') else None
        return Response(self.detail_data(S.reimburse(r, request.data.get('via') or 'direct', request.data.get('reference') or '', on, request.user)))


# ------------------------------------------------------------------ trips
class TripSerializer(NamesMixin, serializers.ModelSerializer):
    employee_name = serializers.SerializerMethodField()
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    approvals = serializers.SerializerMethodField()
    employee_id = serializers.IntegerField(required=False)
    waiting_for = serializers.SerializerMethodField()

    class Meta:
        model = Trip
        fields = '__all__'
        read_only_fields = ('number', 'branch_id', 'department_id', 'status', 'created_by_id')

    def get_employee_name(self, o):
        return self.emp_name(o)

    def get_approvals(self, o):
        return _approval_rows(o.approvals.all())

    def get_waiting_for(self, o):
        s = S.current_step(o) if o.status == 'submitted' else None
        return S.user_name(s.approver_user_id) if s else ''


class TripViewSet(ScopedViewSet):
    queryset = Trip.objects.all()
    serializer_class = TripSerializer
    approver_path = 'approvals__approver_user_id'

    def create(self, request, *a, **k):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        emp = self.target_employee(request.data)
        data = dict(ser.validated_data)
        data.pop('employee_id', None)
        t = Trip.objects.create(number=S.next_number(Trip, 'TR'), employee_id=emp.pk, branch_id=emp.emp_branch_id_id,
                                department_id=emp.emp_dept_id_id, created_by_id=request.user.pk, **data)
        return Response(self.get_serializer(t).data, status=201)

    def update(self, request, *a, **k):
        t = self.get_object()
        self.can_edit(t, ('draft', 'rejected'))
        ser = self.get_serializer(t, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        data = dict(ser.validated_data)
        data.pop('employee_id', None)
        for k2, v in data.items():
            setattr(t, k2, v)
        t.save()
        return Response(self.get_serializer(t).data)

    def partial_update(self, request, *a, **k):
        return self.update(request, *a, **k)

    def destroy(self, request, *a, **k):
        t = self.get_object()
        self.can_edit(t, ('draft',))
        t.delete()
        return Response(status=204)

    @action(detail=True, methods=['post'])
    def submit(self, request, pk=None):
        t = self.get_object()
        self.can_edit(t, ('draft', 'rejected'))
        return Response(self.get_serializer(S.submit_trip(t, request.user)).data)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        return Response(self.get_serializer(S.act_trip(self.get_object(), request.user, 'approve', request.data.get('note', ''), is_admin(request))).data)

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        return Response(self.get_serializer(S.act_trip(self.get_object(), request.user, 'reject',
                                                       request.data.get('note') or request.data.get('reason') or '', is_admin(request))).data)

    @action(detail=True, methods=['post'])
    def close(self, request, pk=None):
        t = self.get_object()
        if t.status != 'approved':
            raise S.ExpenseError('Only an approved trip can be closed.')
        self.can_edit(t, ('approved',))
        t.status = 'closed'
        t.save(update_fields=['status'])
        return Response(self.get_serializer(t).data)


# ------------------------------------------------------------------ advances
class AdvanceSerializer(NamesMixin, serializers.ModelSerializer):
    employee_name = serializers.SerializerMethodField()
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    balance = serializers.SerializerMethodField()
    trip_number = serializers.SerializerMethodField()
    employee_id = serializers.IntegerField(required=False)

    class Meta:
        model = ExpenseAdvance
        fields = '__all__'
        read_only_fields = ('number', 'branch_id', 'status', 'reference', 'approved_by_id', 'paid_on', 'created_by_id')

    def get_employee_name(self, o):
        return self.emp_name(o)

    def get_balance(self, o):
        return S.advance_balance(o) if o.status in ('paid', 'settled') else o.amount

    def get_trip_number(self, o):
        return o.trip.number if o.trip_id else ''


class AdvanceViewSet(ScopedViewSet):
    queryset = ExpenseAdvance.objects.select_related('trip')
    serializer_class = AdvanceSerializer

    def get_queryset(self):
        r = self.request
        if self.hr() or is_admin(r) or r.query_params.get('mine') in ('1', 'true'):
            return super().get_queryset()
        # employees see their own advances, reporting managers also those of their team
        from django.apps import apps
        emp = my_emp(r)
        team = list(apps.get_model('EmpManagement', 'emp_master').objects.filter(emp_reporting_manager=r.user).values_list('pk', flat=True))
        qs = ExpenseAdvance.objects.select_related('trip').filter(employee_id__in=team + ([emp.pk] if emp else []))
        if r.query_params.get('status'):
            qs = qs.filter(status__in=r.query_params['status'].split(','))
        if r.query_params.get('employee'):
            qs = qs.filter(employee_id=r.query_params['employee'])
        return qs

    def create(self, request, *a, **k):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        emp = self.target_employee(request.data)
        data = dict(ser.validated_data)
        data.pop('employee_id', None)
        if data.get('amount') is None or data['amount'] <= 0:
            raise S.ExpenseError('The amount must be more than zero.')
        if data.get('trip') and data['trip'].employee_id != emp.pk:
            raise S.ExpenseError('The trip belongs to another employee.')
        adv = ExpenseAdvance.objects.create(number=S.next_number(ExpenseAdvance, 'ADV'), employee_id=emp.pk, branch_id=emp.emp_branch_id_id,
                                            created_by_id=request.user.pk, **data)
        return Response(self.get_serializer(adv).data, status=201)

    def update(self, request, *a, **k):
        adv = self.get_object()
        self.can_edit(adv, ('requested',))
        ser = self.get_serializer(adv, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        data = dict(ser.validated_data)
        data.pop('employee_id', None)
        for k2, v in data.items():
            setattr(adv, k2, v)
        adv.save()
        return Response(self.get_serializer(adv).data)

    def partial_update(self, request, *a, **k):
        return self.update(request, *a, **k)

    def destroy(self, request, *a, **k):
        adv = self.get_object()
        self.can_edit(adv, ('requested',))
        adv.delete()
        return Response(status=204)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        adv = self.get_object()
        from LeavePolicy.approvers import holder
        mgr = holder('reporting_manager', S.employee(adv.employee_id))
        if not (can(request, 'change_expenseadvance') or (mgr is not None and mgr.pk == request.user.pk)):
            return Response({'detail': 'You may not approve this advance.'}, status=403)
        return Response(self.get_serializer(S.approve_advance(adv, request.user)).data)

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        adv = self.get_object()
        if not can(request, 'change_expenseadvance'):
            from LeavePolicy.approvers import holder
            mgr = holder('reporting_manager', S.employee(adv.employee_id))
            if mgr is None or mgr.pk != request.user.pk:
                return Response({'detail': 'You may not reject this advance.'}, status=403)
        if adv.status not in ('requested', 'approved'):
            raise S.ExpenseError('Only a requested or approved advance can be rejected.')
        adv.status = 'rejected'
        adv.save(update_fields=['status'])
        return Response(self.get_serializer(adv).data)

    @action(detail=True, methods=['post'])
    def pay(self, request, pk=None):
        if not can(request, 'change_expenseadvance'):
            return Response({'detail': 'You may not pay advances.'}, status=403)
        adv = self.get_object()
        on = parse_date(str(request.data.get('date'))) if request.data.get('date') else None
        return Response(self.get_serializer(S.pay_advance(adv, request.user, request.data.get('reference') or '', on)).data)


# ------------------------------------------------------------------ home, export, analytics
class HomeView(Base, APIView):
    """Dashboard of the logged-in user (Zoho Expense home)."""
    permission_classes = [CompanyMember]

    def get(self, request):
        emp = my_emp(request)
        eid = emp.pk if emp else -1
        today = date.today()
        mine = Expense.objects.filter(employee_id=eid)
        unrep = mine.filter(status='unreported', report__isnull=True)
        reps = ExpenseReport.objects.filter(employee_id=eid)
        awaiting = reps.filter(status='submitted')
        to_pay = reps.filter(status='approved')
        advs = list(ExpenseAdvance.objects.filter(employee_id=eid, status__in=('requested', 'approved', 'paid')))

        def agg(qs, f):
            return S.money(qs.aggregate(s=Sum(f))['s'] or ZERO)
        month = mine.filter(date__year=today.year, date__month=today.month).exclude(status='rejected')
        cats = [{'label': x['category__name'], 'amount': S.money(x['s'])} for x in
                month.values('category__name').annotate(s=Sum('amount_aed')).order_by('-s')]
        waiting = ExpenseReport.objects.filter(status='submitted', approvals__status='pending', approvals__approver_user_id=request.user.pk).distinct()
        wnames = _names_ctx([r.employee_id for r in waiting])
        trips_waiting = Trip.objects.filter(status='submitted', approvals__status='pending', approvals__approver_user_id=request.user.pk).distinct()
        recent = ExpenseSerializer(mine.select_related('category', 'report', 'cost_center')[:8], many=True,
                                   context={'request': request, 'emp_names': {}}).data
        data = {
            'employee': S.person(emp), 'employee_id': emp.pk if emp else None,
            'unreported': {'count': unrep.count(), 'amount': agg(unrep, 'amount_aed')},
            'awaiting_approval': {'count': awaiting.count(), 'amount': agg(awaiting, 'total')},
            'awaiting_reimbursement': {'count': to_pay.count(), 'amount': agg(to_pay, 'amount_to_reimburse')},
            'advances': {'count': len(advs), 'outstanding': S.money(sum((S.advance_balance(a) if a.status == 'paid' else a.amount for a in advs), ZERO))},
            'recent': recent,
            'spend_by_category': cats, 'month_total': agg(month, 'amount_aed'),
            'approvals': [{'id': r.pk, 'number': r.number, 'title': r.title, 'employee': wnames.get(r.employee_id, ''), 'total': r.total,
                           'submitted_at': r.submitted_at, 'type': 'report'} for r in waiting[:20]]
                         + [{'id': t.pk, 'number': t.number, 'title': t.purpose, 'employee': S.person(S.employee(t.employee_id)),
                             'total': t.estimated_cost, 'submitted_at': t.created_at, 'type': 'trip'} for t in trips_waiting[:20]],
            'is_finance': can(request, 'reimburse_expensereport', 'change_expensereport'),
            'is_hr': can(request, 'view_expensereport'),
        }
        if data['is_finance'] or data['is_hr']:
            allr = ExpenseReport.objects.all()
            b = branches(request)
            if b is not None:
                allr = allr.filter(Q(branch_id__in=b) | Q(branch_id__isnull=True))
            ready = allr.filter(status='approved')
            data['company'] = {'to_reimburse': {'count': ready.count(), 'amount': agg(ready, 'amount_to_reimburse')},
                               'in_approval': {'count': allr.filter(status='submitted').count(), 'amount': agg(allr.filter(status='submitted'), 'total')}}
        return Response(data)


def _range(request):
    p = request.query_params
    f = parse_date(p.get('from') or '') if p.get('from') else None
    t = parse_date(p.get('to') or '') if p.get('to') else None
    return f, t


class ExportView(Base, APIView):
    """Accounting entries of approved reports: ?from=&to=[&as=csv]."""
    permission_classes = [CompanyMember]

    def get(self, request):
        if not can(request, 'export_expensereport', 'view_expensereport', 'reimburse_expensereport'):
            return Response({'detail': 'You may not export expense entries.'}, status=403)
        f, t = _range(request)
        data = S.journal(f, t, branches(request))
        if request.query_params.get('as') == 'csv':
            resp = HttpResponse(S.journal_csv(data), content_type='text/csv')
            resp['Content-Disposition'] = f'attachment; filename="expense-journal-{f or "start"}-{t or date.today()}.csv"'
            return resp
        return Response(data)


class AnalyticsView(Base, APIView):
    """Spend by category / department / employee / month (?from=&to=&status=). HR: their branches; others: own."""
    permission_classes = [CompanyMember]

    def get(self, request):
        f, t = _range(request)
        qs = Expense.objects.all()
        if can(request, 'view_expense', 'view_expensereport') and request.query_params.get('mine') not in ('1', 'true'):
            b = branches(request)
            if b is not None:
                qs = qs.filter(Q(branch_id__in=b) | Q(branch_id__isnull=True))
        else:
            emp = my_emp(request)
            qs = qs.filter(employee_id=emp.pk if emp else -1)
        st = request.query_params.get('status') or 'submitted,approved,reimbursed'
        qs = qs.filter(status__in=st.split(','))
        if f:
            qs = qs.filter(date__gte=f)
        if t:
            qs = qs.filter(date__lte=t)
        return Response(S.analytics(qs))
