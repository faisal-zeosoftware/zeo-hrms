"""
Organisation structure API (v1.12.0), mounted at /org-structure/api/.

Rights (the central AccessControl layer is switched off for these views because the tables keep integer ids; the same
rules are applied here):
  * settings: every user of the company reads them (screens need them); changing needs company admin,
    change_orgsettings or the form-designer rights (add_/change_emp_customfield);
  * masters (locations, divisions, sections, cost centres, grades, job positions, employment types): every user reads the
    rows of their branches; changing needs company admin, <verb>_<model> or <verb>_dept_master; branch users may only
    use their own branches (a master for "all branches" needs an unrestricted user);
  * employee assignment: HR (view_/change_emp_master or view_/change_employeeorg) for employees of their branches,
    employees read their own (my-org);
  * policy acknowledgements: HR with company-policy rights see who acknowledged and send reminders; employees see and
    acknowledge the policies that apply to them (my-policies);
  * country policies: read by everyone, changed / applied by company admin, change_countrypolicy or change_leavepolicy.
"""
import logging
from collections import defaultdict
from datetime import timedelta

from django.apps import apps
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError, Q
from django.http import FileResponse
from django.utils import timezone
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from . import seed, services as S
from .models import (FIELDS, BranchCountryPolicy, CostCenter, CountryPolicy, Division, DivisionDepartment, EmployeeOrg, EmploymentType,
                     Grade, JobPosition, Location, OrgSettings, PolicyAcknowledgement, PolicyVersion, Section)

log = logging.getLogger(__name__)
M = apps.get_model


def _schema(request):
    from django.db import connection
    if connection.schema_name != 'public':
        return connection.schema_name
    return request.GET.get('schema')


class Member(BasePermission):
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


class Base:
    zeo_access = False   # rights are checked here (module docstring)
    zeo_scope = False
    permission_classes = [Member]


def deny(msg, code=403):
    return Response({'detail': msg}, status=code)


def _ints(v):
    if v in (None, ''):
        return []
    if isinstance(v, str):
        v = [x for x in v.replace(';', ',').split(',') if x.strip()]
    if not isinstance(v, (list, tuple)):
        v = [v]
    out = []
    for x in v:
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            raise serializers.ValidationError(f'"{x}" is not a valid id.')
    return sorted(set(out))


# ------------------------------------------------------------------ settings
class SettingsView(Base, APIView):
    def get(self, request):
        d = S.settings_payload()
        d['can_change'] = S.is_hr_settings(request)
        return Response(d)

    def put(self, request):
        if not S.is_hr_settings(request):
            return deny('Only HR administrators can change the organisation settings.')
        s = OrgSettings.get()
        data = request.data if isinstance(request.data, dict) else {}
        for f in FIELDS:
            k = 'use_' + f
            if k in data:
                setattr(s, k, str(data[k]).lower() in ('true', '1', 'yes', 'on'))
        if isinstance(data.get('labels'), dict):
            lab = dict(s.labels or {})
            for f, v in data['labels'].items():
                if f in FIELDS:
                    v = str(v or '').strip()[:40]
                    if v:
                        lab[f] = v
                    else:
                        lab.pop(f, None)
            s.labels = lab
        if isinstance(data.get('mandatory'), dict):
            man = dict(s.mandatory or {})
            for f, v in data['mandatory'].items():
                if f in FIELDS:
                    man[f] = str(v).lower() in ('true', '1', 'yes', 'on')
            s.mandatory = man
        s.updated_by_id = request.user.pk
        s.save()
        if s.use_employment_types and not EmploymentType.objects.exists():
            seed.load_employment_types()
        if s.use_cost_centers and not CostCenter.objects.exists():
            S.import_expense_cost_centers()
        return self.get(request)

    post = put
    patch = put


# ------------------------------------------------------------------ masters
class MasterSerializer(serializers.ModelSerializer):
    branch_ids = serializers.JSONField(required=False)
    branch_names = serializers.SerializerMethodField()

    class Meta:
        fields = '__all__'
        read_only_fields = ['created_at', 'updated_at', 'created_by_id']

    def maps(self):
        if 'maps' not in self.context:
            self.context['maps'] = S.name_maps()
        return self.context['maps']

    def get_branch_names(self, o):
        b = self.maps()['branch']
        return ', '.join(b.get(int(i), f'#{i}') for i in (o.branch_ids or [])) or 'All branches'

    def validate_code(self, v):
        v = (v or '').strip()
        if not v:
            raise serializers.ValidationError('Enter a code.')
        qs = self.Meta.model.objects.filter(code__iexact=v)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(f'Code {v} is already used – choose another code.')
        return v

    def validate_name(self, v):
        v = (v or '').strip()
        if not v:
            raise serializers.ValidationError('Enter a name.')
        return v

    def validate_branch_ids(self, v):
        ids = _ints(v)
        known = set(self.maps()['branch'])
        bad = [i for i in ids if i not in known]
        if bad:
            raise serializers.ValidationError(f'Branch {bad[0]} does not exist.')
        allowed = self.context.get('allowed_branches')
        if allowed is not None:
            if not ids:
                raise serializers.ValidationError('Choose at least one of your branches (only unrestricted users can make it for all branches).')
            if any(i not in allowed for i in ids):
                raise serializers.ValidationError('You can only use your own branches.')
        return ids

    def _check_dept(self, v, required=False, label='Department'):
        if v in (None, ''):
            if required:
                raise serializers.ValidationError(f'Choose the {label.lower()}.')
            return None
        if int(v) not in self.maps()['department']:
            raise serializers.ValidationError(f'{label} {v} does not exist.')
        return int(v)

    def _check_emp(self, v, label='Employee'):
        if v in (None, ''):
            return None
        if not M('EmpManagement', 'emp_master').objects.filter(pk=v).exists():
            raise serializers.ValidationError(f'{label} {v} does not exist.')
        return int(v)

    def create(self, data):
        if 'branch_ids' not in data:
            if self.context.get('allowed_branches') is not None:
                raise serializers.ValidationError({'branch_ids': 'Choose at least one of your branches.'})
            data['branch_ids'] = []
        req = self.context.get('request')
        data['created_by_id'] = getattr(getattr(req, 'user', None), 'pk', None)
        return super().create(data)


class LocationSerializer(MasterSerializer):
    country_name = serializers.SerializerMethodField()
    employees = serializers.SerializerMethodField()

    class Meta(MasterSerializer.Meta):
        model = Location

    def get_country_name(self, o):
        if not o.country_id:
            return ''
        c = M('Core', 'cntry_mstr').objects.filter(pk=o.country_id).first()
        return c.country_name if c else ''

    def get_employees(self, o):
        return self.context.get('counts', {}).get(o.pk, 0)

    def validate_country_id(self, v):
        if v and not M('Core', 'cntry_mstr').objects.filter(pk=v).exists():
            raise serializers.ValidationError('Choose a country from the list.')
        return v

    def validate_latitude(self, v):
        if v is not None and not -90 <= float(v) <= 90:
            raise serializers.ValidationError('Latitude is between -90 and 90.')
        return v

    def validate_longitude(self, v):
        if v is not None and not -180 <= float(v) <= 180:
            raise serializers.ValidationError('Longitude is between -180 and 180.')
        return v

    def validate(self, d):
        lat = d.get('latitude', getattr(self.instance, 'latitude', None))
        lng = d.get('longitude', getattr(self.instance, 'longitude', None))
        if (lat is None) != (lng is None):
            raise serializers.ValidationError({'longitude': 'Enter both latitude and longitude, or neither.'})
        return d


class DivisionSerializer(MasterSerializer):
    department_ids = serializers.JSONField(required=False, write_only=True)
    departments = serializers.SerializerMethodField()
    department_list = serializers.SerializerMethodField()
    head_name = serializers.SerializerMethodField()
    employees = serializers.SerializerMethodField()

    class Meta(MasterSerializer.Meta):
        model = Division

    def get_departments(self, o):
        return [d.department_id for d in o.departments.all()]

    def to_representation(self, o):
        d = super().to_representation(o)
        d['department_ids'] = d.get('departments') or []   # the edit form works on department_ids
        return d

    def get_department_list(self, o):
        dm = self.maps()['department']
        return ', '.join(dm.get(d.department_id, f'#{d.department_id}') for d in o.departments.all())

    def get_head_name(self, o):
        e = M('EmpManagement', 'emp_master').objects.filter(pk=o.head_employee_id).first() if o.head_employee_id else None
        return S.emp_name(e)

    def get_employees(self, o):
        return self.context.get('counts', {}).get(o.pk, 0)

    def validate_head_employee_id(self, v):
        return self._check_emp(v, 'Head')

    def validate_department_ids(self, v):
        ids = _ints(v)
        dm = self.maps()['department']
        for i in ids:
            if i not in dm:
                raise serializers.ValidationError(f'Department {i} does not exist.')
            other = DivisionDepartment.objects.filter(department_id=i).exclude(division_id=getattr(self.instance, 'pk', None)).select_related('division').first()
            if other:
                raise serializers.ValidationError(f'Department {dm[i]} already belongs to division {other.division.name} – remove it there first.')
        return ids

    def _links(self, obj, ids):
        if ids is None:
            return
        DivisionDepartment.objects.filter(division=obj).exclude(department_id__in=ids).delete()
        have = set(DivisionDepartment.objects.filter(division=obj).values_list('department_id', flat=True))
        for i in ids:
            if i not in have:
                DivisionDepartment.objects.create(division=obj, department_id=i)

    def create(self, data):
        ids = data.pop('department_ids', None)
        obj = super().create(data)
        self._links(obj, ids)
        return obj

    def update(self, obj, data):
        ids = data.pop('department_ids', None)
        obj = super().update(obj, data)
        self._links(obj, ids)
        return obj


class SectionSerializer(MasterSerializer):
    department_name = serializers.SerializerMethodField()
    employees = serializers.SerializerMethodField()

    class Meta(MasterSerializer.Meta):
        model = Section

    def get_department_name(self, o):
        return self.maps()['department'].get(o.department_id, '')

    def get_employees(self, o):
        return self.context.get('counts', {}).get(o.pk, 0)

    def validate_department_id(self, v):
        return self._check_dept(v, required=True)

    def validate_head_employee_id(self, v):
        return self._check_emp(v, 'Head')

    def validate(self, d):
        dept = d.get('department_id', getattr(self.instance, 'department_id', None))
        if self.instance is not None and 'department_id' in d and d['department_id'] != self.instance.department_id:
            n = EmployeeOrg.objects.filter(section=self.instance).count()
            if n:
                raise serializers.ValidationError({'department_id': f'{n} employee(s) are in this section – move them before changing its department.'})
        if dept is None:
            raise serializers.ValidationError({'department_id': 'Choose the department.'})
        return d


class CostCenterSerializer(MasterSerializer):
    department_name = serializers.SerializerMethodField()
    manager_name = serializers.SerializerMethodField()
    employees = serializers.SerializerMethodField()

    class Meta(MasterSerializer.Meta):
        model = CostCenter
        read_only_fields = MasterSerializer.Meta.read_only_fields + ['expense_cost_center_id']

    def get_department_name(self, o):
        return self.maps()['department'].get(o.department_id, '') if o.department_id else ''

    def get_manager_name(self, o):
        e = M('EmpManagement', 'emp_master').objects.filter(pk=o.manager_id).first() if o.manager_id else None
        return S.emp_name(e)

    def get_employees(self, o):
        return self.context.get('counts', {}).get(o.pk, 0)

    def validate_department_id(self, v):
        return self._check_dept(v)

    def validate_manager_id(self, v):
        return self._check_emp(v, 'Manager')


class GradeSerializer(MasterSerializer):
    designation_ids = serializers.JSONField(required=False)
    designation_names = serializers.SerializerMethodField()
    employees = serializers.SerializerMethodField()

    class Meta(MasterSerializer.Meta):
        model = Grade

    def get_designation_names(self, o):
        dm = self.maps()['designation']
        return ', '.join(dm.get(int(i), f'#{i}') for i in (o.designation_ids or [])) or 'Any designation'

    def get_employees(self, o):
        return self.context.get('counts', {}).get(o.pk, 0)

    def validate_designation_ids(self, v):
        ids = _ints(v)
        dm = self.maps()['designation']
        bad = [i for i in ids if i not in dm]
        if bad:
            raise serializers.ValidationError(f'Designation {bad[0]} does not exist.')
        return ids

    def validate_level(self, v):
        if v is None or v < 1:
            raise serializers.ValidationError('Level is 1 or more (1 = lowest).')
        return v

    def validate_currency(self, v):
        v = (v or 'AED').strip().upper()
        if len(v) != 3 or not v.isalpha():
            raise serializers.ValidationError('Use a 3-letter currency code, e.g. AED.')
        return v

    def validate(self, d):
        lo = d.get('salary_min', getattr(self.instance, 'salary_min', None))
        hi = d.get('salary_max', getattr(self.instance, 'salary_max', None))
        for k, v in (('salary_min', lo), ('salary_max', hi)):
            if v is not None and v < 0:
                raise serializers.ValidationError({k: 'Salary cannot be negative.'})
        if lo is not None and hi is not None and lo > hi:
            raise serializers.ValidationError({'salary_max': 'The maximum salary must be at least the minimum.'})
        return d


class PositionSerializer(MasterSerializer):
    department_name = serializers.SerializerMethodField()
    designation_name = serializers.SerializerMethodField()
    grade_name = serializers.SerializerMethodField()
    reports_to_name = serializers.SerializerMethodField()
    filled = serializers.SerializerMethodField()
    vacancies = serializers.SerializerMethodField()
    open_requisitions = serializers.SerializerMethodField()

    class Meta(MasterSerializer.Meta):
        model = JobPosition

    def get_open_requisitions(self, o):
        if 'reqs' not in self.context:
            from .models import PositionRequisition
            links = dict(PositionRequisition.objects.values_list('requisition_id', 'position_id'))
            open_ids = set()
            try:
                MR = M('RecruitmentManagement', 'ManpowerRequisition')
                open_ids = set(MR.objects.filter(pk__in=list(links), status__in=['draft', 'pending', 'approved', 'on_hold']).values_list('pk', flat=True))
            except LookupError:
                pass
            cnt = defaultdict(int)
            for rid, pid in links.items():
                if rid in open_ids:
                    cnt[pid] += 1
            self.context['reqs'] = cnt
        return self.context['reqs'].get(o.pk, 0)

    def get_department_name(self, o):
        return self.maps()['department'].get(o.department_id, '') if o.department_id else ''

    def get_designation_name(self, o):
        return self.maps()['designation'].get(o.designation_id, '') if o.designation_id else ''

    def get_grade_name(self, o):
        return o.grade.name if o.grade_id else ''

    def get_reports_to_name(self, o):
        return o.reports_to.name if o.reports_to_id else ''

    def _filled(self, o):
        if 'filled' not in self.context:
            self.context['filled'] = S.filled_counts()
        return self.context['filled'].get(o.pk, 0)

    def get_filled(self, o):
        return self._filled(o)

    def get_vacancies(self, o):
        return max(0, o.headcount_budget - self._filled(o))

    def validate_department_id(self, v):
        return self._check_dept(v)

    def validate_designation_id(self, v):
        if v in (None, ''):
            return None
        if int(v) not in self.maps()['designation']:
            raise serializers.ValidationError(f'Designation {v} does not exist.')
        return int(v)

    def validate(self, d):
        g = d.get('grade', getattr(self.instance, 'grade', None))
        desg = d.get('designation_id', getattr(self.instance, 'designation_id', None))
        if g is not None and g.designation_ids and desg and desg not in [int(x) for x in g.designation_ids]:
            raise serializers.ValidationError({'grade': f'Grade {g.name} is not allowed for this designation.'})
        parent = d.get('reports_to', getattr(self.instance, 'reports_to', None))
        if parent is not None and self.instance is not None and S.would_loop_position(self.instance.pk, parent.pk):
            raise serializers.ValidationError({'reports_to': f'{parent.name} already reports (directly or indirectly) to this position – that would make a loop.'})
        hb = d.get('headcount_budget', getattr(self.instance, 'headcount_budget', 1))
        if hb is not None and hb < 0:
            raise serializers.ValidationError({'headcount_budget': 'Headcount cannot be negative.'})
        return d


class EmploymentTypeSerializer(MasterSerializer):
    employees = serializers.SerializerMethodField()

    class Meta(MasterSerializer.Meta):
        model = EmploymentType

    def get_employees(self, o):
        return self.context.get('counts', {}).get(o.pk, 0)

    def validate_probation_days(self, v):
        if v is not None and v > 365:
            raise serializers.ValidationError('Probation is at most 365 days.')
        return v


EMP_FIELD_OF = {Location: 'location', Division: 'division', Section: 'section', CostCenter: 'cost_center', Grade: 'grade',
                JobPosition: 'job_position', EmploymentType: 'employment_type'}


class MasterViewSet(Base, viewsets.ModelViewSet):
    model = None
    setting = None   # settings key the master belongs to

    def get_queryset(self):
        qs = self.model.objects.all()
        q = self.request.query_params
        if q.get('active') in ('true', '1'):
            qs = qs.filter(active=True)
        elif q.get('active') in ('false', '0'):
            qs = qs.filter(active=False)
        if q.get('department') and hasattr(self.model, 'department_id'):
            qs = qs.filter(Q(department_id=q['department']) | (Q(department_id__isnull=True) if self.model is not Section else Q(pk__in=[])))
        if q.get('designation') and self.model is Grade:
            try:
                d = int(q['designation'])
                qs = qs.filter(Q(designation_ids=[]) | Q(designation_ids__contains=[d]))
            except ValueError:
                pass
        if q.get('branch'):
            try:
                b = int(q['branch'])
                qs = qs.filter(Q(branch_ids=[]) | Q(branch_ids__contains=[b]))
            except ValueError:
                pass
        allowed = S.user_branches(self.request)
        if allowed is not None:
            cond = Q(branch_ids=[])
            for b in allowed:
                cond |= Q(branch_ids__contains=[b])
            qs = qs.filter(cond)
        if q.get('search'):
            qs = qs.filter(Q(code__icontains=q['search']) | Q(name__icontains=q['search']))
        return qs

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx['allowed_branches'] = S.user_branches(self.request)
        f = EMP_FIELD_OF.get(self.model)
        if f and self.request.method == 'GET':
            from django.db.models import Count
            E = M('EmpManagement', 'emp_master')
            act = E.objects.filter(Q(is_active=True) | Q(is_active__isnull=True)).values('id')
            ctx['counts'] = dict(EmployeeOrg.objects.filter(**{f + '__isnull': False}, employee_id__in=act).values(f + '_id')
                                 .annotate(n=Count('id')).values_list(f + '_id', 'n'))
        return ctx

    def _can(self, verb):
        return S.is_hr_master(self.request, self.model._meta.model_name, verb)

    def _check_obj_branch(self, obj):
        allowed = S.user_branches(self.request)
        if allowed is not None and (not obj.branch_ids or any(int(b) not in allowed for b in obj.branch_ids)):
            return False
        return True

    def create(self, request, *a, **kw):
        if not self._can('add'):
            return deny(f'You may not add {self.model._meta.verbose_name_plural}.')
        return super().create(request, *a, **kw)

    def update(self, request, *a, **kw):
        if not self._can('change'):
            return deny(f'You may not change {self.model._meta.verbose_name_plural}.')
        if not self._check_obj_branch(self.get_object()):
            return deny('This record is shared with branches you do not manage – ask a company administrator to change it.')
        return super().update(request, *a, **kw)

    def destroy(self, request, *a, **kw):
        if not self._can('delete'):
            return deny(f'You may not delete {self.model._meta.verbose_name_plural}.')
        obj = self.get_object()
        if not self._check_obj_branch(obj):
            return deny('This record is shared with branches you do not manage – ask a company administrator to delete it.')
        f = EMP_FIELD_OF.get(self.model)
        n = EmployeeOrg.objects.filter(**{f: obj}).count() if f else 0
        if n:
            return deny(f'{obj.name} is used by {n} employee(s) – make it inactive instead, or move the employees first.', 400)
        try:
            with transaction.atomic():
                if self.model is CostCenter and obj.expense_cost_center_id:
                    try:
                        E = M('ExpenseManagement', 'CostCenter')
                        if not M('ExpenseManagement', 'Expense').objects.filter(cost_center_id=obj.expense_cost_center_id).exists():
                            E.objects.filter(pk=obj.expense_cost_center_id).delete()
                        else:
                            E.objects.filter(pk=obj.expense_cost_center_id).update(active=False)
                    except Exception:
                        log.debug('expense cost centre cleanup failed', exc_info=True)
                obj.delete()
        except ProtectedError:
            return deny(f'{obj.name} is still used (for example by a job position) – make it inactive instead.', 400)
        return Response(status=204)

    @action(detail=False, methods=['get'], url_path='options')
    def options_list(self, request):
        """Active rows as {value, label} for drop-downs (filtered by ?department= / ?branch= / ?designation=)."""
        qs = self.get_queryset().filter(active=True)
        return Response([{'value': o.pk, 'label': o.name, 'code': o.code, **({'department_id': o.department_id} if hasattr(o, 'department_id') else {})} for o in qs])


class LocationViewSet(MasterViewSet):
    model = Location
    queryset = Location.objects.all()
    serializer_class = LocationSerializer


class DivisionViewSet(MasterViewSet):
    model = Division
    queryset = Division.objects.all()
    serializer_class = DivisionSerializer

    def get_queryset(self):
        return super().get_queryset().prefetch_related('departments')

    @action(detail=False, methods=['get'], url_path='for-department')
    def for_department(self, request):
        d = request.query_params.get('department')
        link = DivisionDepartment.objects.filter(department_id=d).select_related('division').first() if d else None
        return Response({'division_id': link.division_id if link else None, 'division': link.division.name if link else ''})


class SectionViewSet(MasterViewSet):
    model = Section
    queryset = Section.objects.all()
    serializer_class = SectionSerializer


class CostCenterViewSet(MasterViewSet):
    model = CostCenter
    queryset = CostCenter.objects.all()
    serializer_class = CostCenterSerializer

    def list(self, request, *a, **kw):
        if not CostCenter.objects.exists():
            S.import_expense_cost_centers()
        return super().list(request, *a, **kw)

    def perform_create(self, ser):
        S.sync_expense_cost_center(ser.save())

    def perform_update(self, ser):
        S.sync_expense_cost_center(ser.save())


class GradeViewSet(MasterViewSet):
    model = Grade
    queryset = Grade.objects.all()
    serializer_class = GradeSerializer


class PositionViewSet(MasterViewSet):
    model = JobPosition
    queryset = JobPosition.objects.all()
    serializer_class = PositionSerializer

    def get_queryset(self):
        qs = super().get_queryset().select_related('grade', 'reports_to')
        if self.request.query_params.get('vacant') in ('1', 'true'):
            filled = S.filled_counts()
            qs = [p for p in qs if p.headcount_budget > filled.get(p.pk, 0)]
            return JobPosition.objects.filter(pk__in=[p.pk for p in qs]).select_related('grade', 'reports_to')
        return qs

    @action(detail=True, methods=['post'], url_path='requisition')
    def requisition(self, request, pk=None):
        """Raise a draft manpower requisition in Recruitment for the position's vacancies."""
        data, code = _raise_requisition(request, self.get_object())
        return Response(data, status=code)


def _raise_requisition(request, p):
    """Draft manpower requisition (Recruitment) for the vacancies of a position. Returns (data, status)."""
    from .models import PositionRequisition
    if not S.has(request, 'add_manpowerrequisition'):
        return {'detail': 'You may not raise manpower requisitions.'}, 403
    try:
        MR = M('RecruitmentManagement', 'ManpowerRequisition')
    except LookupError:
        return {'detail': 'Recruitment is not installed.'}, 400
    if not p.department_id or not p.designation_id:
        return {'detail': 'Set the department and designation of the position first – the requisition needs them.'}, 400
    vac = p.headcount_budget - S.filled_counts().get(p.pk, 0)
    if vac <= 0:
        return {'detail': f'{p.name} has no vacancies ({p.headcount_budget} budgeted, all filled).'}, 400
    open_ids = list(p.requisitions.values_list('requisition_id', flat=True))
    if MR.objects.filter(pk__in=open_ids, status__in=['draft', 'pending', 'approved', 'on_hold']).exists():
        return {'detail': 'There is already an open requisition for this position – continue it in Recruitment.'}, 400
    me = S.my_employee(request)
    branch = int(p.branch_ids[0]) if p.branch_ids else (me.emp_branch_id_id if me else None)
    allowed = S.user_branches(request)
    if allowed is not None and branch not in allowed:
        return {'detail': 'The position belongs to a branch you do not manage.'}, 403
    boss = None
    if p.reports_to_id:
        h = EmployeeOrg.objects.filter(job_position_id=p.reports_to_id).first()
        boss = h.employee_id if h else None
    g = p.grade
    r = MR.objects.create(branch_id=branch, department_id=p.department_id, designation_id=p.designation_id, position_title=p.name[:150],
                          headcount=vac, requisition_type='new', min_salary=getattr(g, 'salary_min', None), max_salary=getattr(g, 'salary_max', None),
                          reporting_to_id=boss, requested_by=request.user,
                          justification=f'Budgeted job position {p.code} – {vac} of {p.headcount_budget} vacant.')
    PositionRequisition.objects.create(position=p, requisition_id=r.pk)
    return {'requisition_id': r.pk, 'document_number': r.document_number, 'headcount': vac}, 201


class EmploymentTypeViewSet(MasterViewSet):
    model = EmploymentType
    queryset = EmploymentType.objects.all()
    serializer_class = EmploymentTypeSerializer

    @action(detail=False, methods=['post'], url_path='load-standard')
    def load_standard(self, request):
        if not self._can('add'):
            return deny('You may not add employment types.')
        return Response({'created': seed.load_employment_types()})


# ------------------------------------------------------------------ employee assignment
def _emp_qs(request):
    E = M('EmpManagement', 'emp_master')
    qs = E.objects.select_related('emp_branch_id', 'emp_dept_id', 'emp_desgntn_id')
    allowed = S.user_branches(request)
    if allowed is not None:
        qs = qs.filter(emp_branch_id__in=allowed)
    return qs


def _visible_emp(request, emp_id, write=False):
    """(employee, error Response)"""
    E = M('EmpManagement', 'emp_master')
    emp = E.objects.select_related('emp_branch_id', 'emp_dept_id', 'emp_desgntn_id').filter(pk=emp_id).first() if str(emp_id).isdigit() else None
    if emp is None:
        return None, deny('Employee not found.', 404)
    me = S.my_employee(request)
    if S.is_hr_employees(request, write=write):
        allowed = S.user_branches(request)
        if allowed is None or emp.emp_branch_id_id in allowed:
            return emp, None
        return None, deny('This employee is in a branch you do not manage.')
    if not write and me is not None and me.pk == emp.pk:
        return emp, None
    return None, deny('You may only see your own organisation details.' if not write else 'You may not change employees’ organisation details.')


class EmployeeOrgView(Base, APIView):
    """GET ?employee=<id> | ?ids=1,2,3 (HR) → assignments; PUT/POST /employee-org/<id>/ → save one."""

    def get(self, request, emp_id=None):
        s = OrgSettings.get()
        fields = [f for f, _, _ in S.ORG_FIELDS]
        if emp_id is None and request.query_params.get('employee'):
            emp_id = request.query_params['employee']
        if emp_id is not None:
            emp, err = _visible_emp(request, emp_id)
            if err:
                return err
            o = EmployeeOrg.objects.filter(employee_id=emp.pk).first()
            d = S.assignment_json(o, emp, fields)
            d['active_fields'] = S.active_emp_fields(s)
            d['division_default'] = None
            link = DivisionDepartment.objects.filter(department_id=emp.emp_dept_id_id).select_related('division').first() if emp.emp_dept_id_id else None
            if link:
                d['division_default'] = {'id': link.division_id, 'name': link.division.name}
            return Response(d)
        if not S.is_hr_employees(request):
            return deny('You may only see your own organisation details.')
        qs = _emp_qs(request)
        ids = request.query_params.get('ids')
        if ids:
            qs = qs.filter(pk__in=_ints(ids))
        emps = list(qs.order_by('emp_code'))
        orgs = S.org_rows([e.pk for e in emps])
        return Response([S.assignment_json(orgs.get(e.pk), e, fields) for e in emps])

    def put(self, request, emp_id=None):
        emp_id = emp_id or (request.data or {}).get('employee')
        emp, err = _visible_emp(request, emp_id, write=True)
        if err:
            return err
        try:
            o = S.save_assignment(emp, request.data or {}, user_id=request.user.pk)
        except S.Problem as p:
            return Response(p.errors, status=400)
        return Response(S.assignment_json(o, emp))

    post = put
    patch = put


class MyOrgView(Base, APIView):
    """ESS: the user's own organisation details – only the fields that are switched on, read only."""

    def get(self, request):
        emp = S.my_employee(request)
        if emp is None:
            return Response({'active_fields': [], 'detail': 'Your login is not linked to an employee.'})
        s = OrgSettings.get()
        fields = S.active_emp_fields(s)
        o = EmployeeOrg.objects.select_related(*fields).filter(employee_id=emp.pk).first() if fields else None
        d = S.assignment_json(o, emp, fields)
        d['active_fields'] = fields
        d['labels'] = {f: s.label(S.FIELD_SETTING[f]) for f in fields}
        if not s.is_on('employment_types'):
            d.pop('contract_end_date', None)
        return Response(d)


class EmployeeOrgBulkView(Base, APIView):
    """POST [{employee: id, location: id, ...}, ...] → saved / errors per row (all-or-nothing per row)."""

    def post(self, request):
        if not S.is_hr_employees(request, write=True):
            return deny('You may not change employees’ organisation details.')
        rows = request.data if isinstance(request.data, list) else (request.data or {}).get('rows') or []
        out, ok = [], 0
        for i, r in enumerate(rows, 1):
            emp, err = _visible_emp(request, (r or {}).get('employee'), write=True)
            if err:
                out.append({'row': i, 'ok': False, 'errors': {'employee': err.data['detail']}})
                continue
            try:
                S.save_assignment(emp, r, user_id=request.user.pk)
                out.append({'row': i, 'ok': True, 'employee_id': emp.pk})
                ok += 1
            except S.Problem as p:
                out.append({'row': i, 'ok': False, 'errors': p.errors})
        return Response({'total': len(rows), 'ok': ok, 'failed': len(rows) - ok, 'results': out})


class EmployeeOrgImportView(Base, APIView):
    """POST {rows: [{employee_code, location_code, division_code, section_code, cost_center_code, grade_code,
    job_position_code, employment_type_code, contract_end_date}], dry_run: true|false} – codes, checked row by row."""
    COLUMNS = ['employee_code', 'location_code', 'division_code', 'section_code', 'cost_center_code', 'grade_code',
               'job_position_code', 'employment_type_code', 'contract_end_date']

    def get(self, request):
        return Response({'columns': self.COLUMNS, 'example': {'employee_code': 'EMP001', 'section_code': 'SEC-01', 'grade_code': 'G5'}})

    def post(self, request):
        if not S.is_hr_employees(request, write=True):
            return deny('You may not change employees’ organisation details.')
        data = request.data if isinstance(request.data, dict) else {'rows': request.data}
        rows = data.get('rows') or []
        dry = str(data.get('dry_run', request.query_params.get('dry_run', ''))).lower() in ('1', 'true', 'yes')
        E = M('EmpManagement', 'emp_master')
        out, ok = [], 0
        for i, r in enumerate(rows, 1):
            r = {str(k).strip().lower().replace(' ', '_'): v for k, v in (r or {}).items()}
            code = str(r.get('employee_code') or r.get('employee') or '').strip()
            emp = E.objects.filter(emp_code__iexact=code).first() if code else None
            if emp is None:
                out.append({'row': i, 'ok': False, 'errors': {'employee_code': f'No employee with code "{code}".' if code else 'Enter the employee code.'}})
                continue
            emp, err = _visible_emp(request, emp.pk, write=True)
            if err:
                out.append({'row': i, 'ok': False, 'errors': {'employee_code': err.data['detail']}})
                continue
            payload = {k: v for k, v in r.items() if k.endswith('_code') and k != 'employee_code' and v not in (None, '')}
            if r.get('contract_end_date') not in (None, ''):
                payload['contract_end_date'] = r['contract_end_date']
            try:
                if dry:
                    S.validate_assignment(emp, payload, EmployeeOrg.objects.filter(employee_id=emp.pk).first(), by_code=True)
                else:
                    S.save_assignment(emp, payload, user_id=request.user.pk, by_code=True)
                out.append({'row': i, 'ok': True, 'employee_id': emp.pk, 'employee': S.emp_name(emp)})
                ok += 1
            except S.Problem as p:
                out.append({'row': i, 'ok': False, 'errors': p.errors})
        return Response({'dry_run': dry, 'total': len(rows), 'ok': ok, 'failed': len(rows) - ok, 'results': out})


# ------------------------------------------------------------------ hierarchy
class HierarchyView(Base, APIView):
    """GET ?mode=positions|managers&root=<id> → tree; cycles are listed (and broken) instead of looping."""

    def get(self, request):
        if not S.is_hr_employees(request) and not S.has(request, 'view_jobposition'):
            return deny('You may not see the organisation hierarchy.')
        mode = request.query_params.get('mode', 'positions')
        root = request.query_params.get('root')
        if mode == 'managers':
            return Response(self._managers(request, root))
        return Response(self._positions(request, root))

    def _positions(self, request, root):
        s = OrgSettings.get()
        nodes = {p.pk: p for p in JobPosition.objects.filter(active=True).select_related('grade')}
        maps = S.name_maps()
        holders = defaultdict(list)
        E = M('EmpManagement', 'emp_master')
        act = E.objects.filter(Q(is_active=True) | Q(is_active__isnull=True))
        allowed = S.user_branches(request)
        if allowed is not None:
            act = act.filter(emp_branch_id__in=allowed)
        emps = {e.pk: e for e in act}
        for o in EmployeeOrg.objects.filter(job_position_id__in=list(nodes), employee_id__in=list(emps)):
            holders[o.job_position_id].append({'id': o.employee_id, 'name': S.emp_name(emps[o.employee_id])})
        cycles = S.position_cycles()
        broken = {c[0] for c in cycles}
        kids = defaultdict(list)
        tops = []
        for p in nodes.values():
            parent = p.reports_to_id if p.reports_to_id in nodes and p.pk not in broken else None
            (kids[parent] if parent else tops).append(p.pk)

        def build(pid, depth=0):
            p = nodes[pid]
            return {'id': p.pk, 'code': p.code, 'name': p.name, 'department': maps['department'].get(p.department_id, ''),
                    'designation': maps['designation'].get(p.designation_id, ''), 'grade': p.grade.name if p.grade_id else '',
                    'budget': p.headcount_budget, 'filled': len(holders[pid]), 'holders': holders[pid],
                    'children': [build(k, depth + 1) for k in sorted(kids[pid], key=lambda k: nodes[k].name)] if depth < 50 else []}
        start = [int(root)] if root and str(root).isdigit() and int(root) in nodes else sorted(tops, key=lambda k: nodes[k].name)
        return {'mode': 'positions', 'label': s.label('job_positions'), 'tree': [build(k) for k in start],
                'cycles': [[nodes[i].name for i in c if i in nodes] for c in cycles]}

    def _managers(self, request, root):
        E = M('EmpManagement', 'emp_master')
        qs = E.objects.filter(Q(is_active=True) | Q(is_active__isnull=True)).select_related('emp_desgntn_id', 'emp_dept_id')
        allowed = S.user_branches(request)
        if allowed is not None:
            qs = qs.filter(emp_branch_id__in=allowed)
        emps = {e.pk: e for e in qs}
        by_user = {e.users_id: e.pk for e in emps.values() if e.users_id}
        cycles = S.manager_cycles()
        broken = {c[0] for c in cycles}
        kids = defaultdict(list)
        tops = []
        for e in emps.values():
            m = by_user.get(e.emp_reporting_manager_id)
            if m and m != e.pk and e.pk not in broken:
                kids[m].append(e.pk)
            else:
                tops.append(e.pk)

        def build(eid, depth=0):
            e = emps[eid]
            return {'id': e.pk, 'name': S.emp_name(e), 'designation': getattr(e.emp_desgntn_id, 'desgntn_job_title', '') or '',
                    'department': getattr(e.emp_dept_id, 'dept_name', '') or '',
                    'children': [build(k, depth + 1) for k in sorted(kids[eid], key=lambda k: emps[k].emp_code)] if depth < 50 else []}
        start = [int(root)] if root and str(root).isdigit() and int(root) in emps else sorted(tops, key=lambda k: emps[k].emp_code)
        names = lambda c: [S.emp_name(emps[i]) if i in emps else f'#{i}' for i in c]
        return {'mode': 'managers', 'tree': [build(k) for k in start], 'cycles': [names(c) for c in cycles]}


class HierarchyCheckView(Base, APIView):
    """POST {employee, manager (user id)} or {position, reports_to} → {ok, detail}."""

    def post(self, request):
        d = request.data or {}
        if d.get('position') is not None:
            loop = S.would_loop_position(d.get('position'), d.get('reports_to'))
            return Response({'ok': not loop, 'detail': 'That would make the positions report to each other in a loop.' if loop else ''})
        try:
            loop = S.would_loop_manager(int(d.get('employee') or 0), d.get('manager'))
        except (TypeError, ValueError):
            return deny('Send employee (id) and manager (user id).', 400)
        return Response({'ok': not loop, 'detail': 'That manager already reports (directly or indirectly) to this employee – it would make a loop.' if loop else ''})


class NoManagerView(Base, APIView):
    """Active employees without a reporting manager (or with a manager whose login is not an active employee)."""

    def get(self, request):
        if not S.is_hr_employees(request):
            return deny('You may not see this list.')
        qs = _emp_qs(request).filter(Q(is_active=True) | Q(is_active__isnull=True))
        E = M('EmpManagement', 'emp_master')
        U = M('UserManagement', 'CustomUser')
        inactive_users = set(U.objects.filter(is_active=False).values_list('id', flat=True))
        left_emp_users = set(E.objects.filter(is_active=False, users__isnull=False).values_list('users_id', flat=True))
        rows = []
        for e in qs.order_by('emp_code'):
            why = ''
            if not e.emp_reporting_manager_id:
                why = 'No reporting manager'
            elif e.emp_reporting_manager_id == e.users_id:
                why = 'Reports to themself'
            elif e.emp_reporting_manager_id in inactive_users:
                why = 'Manager’s login is inactive'
            elif e.emp_reporting_manager_id in left_emp_users:
                why = 'Manager has left (inactive employee)'
            if why:
                rows.append({'id': e.pk, 'employee': S.emp_name(e), 'branch': getattr(e.emp_branch_id, 'branch_name', '') or '',
                             'department': getattr(e.emp_dept_id, 'dept_name', '') or '', 'designation': getattr(e.emp_desgntn_id, 'desgntn_job_title', '') or '',
                             'problem': why})
        return Response({'rows': rows, 'count': len(rows), 'cycles': S.manager_cycles()})


# ------------------------------------------------------------------ company policies
def _policies():
    return M('OrganisationManager', 'CompanyPolicy').objects.prefetch_related('branch', 'specific_users')


def _client_ip(request):
    x = request.META.get('HTTP_X_FORWARDED_FOR')
    return (x.split(',')[0].strip() if x else request.META.get('REMOTE_ADDR', ''))[:60]


class PolicyStatusView(Base, APIView):
    """HR: GET /policies/ → every policy with acknowledgement counts; GET /policies/<id>/ → who has / hasn't;
    PUT /policies/<id>/ {requires_ack, due_days}."""

    def get(self, request, pk=None):
        if not S.is_hr_policies(request):
            return deny('You may not see policy acknowledgements.')
        E = M('EmpManagement', 'emp_master')
        emps = list(_emp_qs(request).filter(Q(is_active=True) | Q(is_active__isnull=True)))
        if pk is not None:
            p = _policies().filter(pk=pk).first()
            if p is None:
                return deny('Policy not found.', 404)
            v = S.policy_version(p)
            acks = {a.employee_id: a for a in PolicyAcknowledgement.objects.filter(policy_id=p.pk, version=v.version)}
            older = set(PolicyAcknowledgement.objects.filter(policy_id=p.pk, version__lt=v.version).values_list('employee_id', flat=True))
            b = [x.pk for x in p.branch.all()]
            sp = [x.pk for x in p.specific_users.all()]
            rows = []
            for e in emps:
                if not S.policy_applies(p, e, branch_ids=b, specific=sp):
                    continue
                a = acks.get(e.pk)
                rows.append({'employee_id': e.pk, 'employee': S.emp_name(e), 'branch': getattr(e.emp_branch_id, 'branch_name', '') or '',
                             'department': getattr(e.emp_dept_id, 'dept_name', '') or '',
                             'status': 'Acknowledged' if a else ('Re-acknowledge (new version)' if e.pk in older else 'Pending'),
                             'acknowledged_at': a.acknowledged_at if a else None, 'ip': a.ip if a else ''})
            return Response({'id': p.pk, 'title': p.title, 'version': v.version, 'version_date': v.version_date, 'requires_ack': v.requires_ack,
                             'due_days': v.due_days, 'rows': rows, 'acknowledged': sum(1 for r in rows if r['status'] == 'Acknowledged'),
                             'pending': sum(1 for r in rows if r['status'] != 'Acknowledged')})
        out = []
        for p in _policies().order_by('title'):
            v = S.policy_version(p)
            b = [x.pk for x in p.branch.all()]
            allowed = S.user_branches(request)
            if allowed is not None and b and not set(b) & set(allowed):
                continue
            sp = [x.pk for x in p.specific_users.all()]
            ids = [e.pk for e in emps if S.policy_applies(p, e, branch_ids=b, specific=sp)]
            done = PolicyAcknowledgement.objects.filter(policy_id=p.pk, version=v.version, employee_id__in=ids).count()
            out.append({'id': p.pk, 'title': p.title, 'description': p.description or '', 'version': v.version, 'version_date': v.version_date,
                        'requires_ack': v.requires_ack, 'due_days': v.due_days, 'applies_to': len(ids), 'acknowledged': done,
                        'pending': len(ids) - done, 'percent': round(100 * done / len(ids), 1) if ids else 0,
                        'last_reminded_at': v.last_reminded_at})
        return Response(out)

    def put(self, request, pk=None):
        if not S.is_hr_policies(request, write=True):
            return deny('You may not change policy settings.')
        p = _policies().filter(pk=pk).first() if pk else None
        if p is None:
            return deny('Policy not found.', 404)
        v = S.policy_version(p)
        d = request.data or {}
        if 'requires_ack' in d:
            v.requires_ack = str(d['requires_ack']).lower() in ('true', '1', 'yes')
        if 'due_days' in d:
            try:
                v.due_days = max(0, min(365, int(d['due_days'])))
            except (TypeError, ValueError):
                return Response({'due_days': 'Enter a number of days.'}, status=400)
        if str(d.get('new_version', '')).lower() in ('true', '1'):
            v.version += 1
            v.version_date = timezone.now()
        v.save()
        return self.get(request, pk)

    patch = put


def remind_policy(p, actor=None):
    """Notify everyone who has not acknowledged the current version. Returns the number notified."""
    from django.db import connection
    v = S.policy_version(p)
    if not v.requires_ack:
        return 0
    E = M('EmpManagement', 'emp_master')
    done = set(PolicyAcknowledgement.objects.filter(policy_id=p.pk, version=v.version).values_list('employee_id', flat=True))
    pending = [e for e in S.applicable_employees(p, E.objects.filter(Q(is_active=True) | Q(is_active__isnull=True)).select_related('emp_branch_id'))
               if e.pk not in done and e.users_id]
    schema = connection.schema_name
    n = 0
    try:
        from django_tenants.utils import schema_context
        Inbox = M('UserManagement', 'UserNotificationInbox')
        with schema_context('public'):
            for e in pending:
                Inbox.objects.create(user_id=e.users_id, schema_name=schema, branch_id=e.emp_branch_id_id,
                                     branch_name=getattr(e.emp_branch_id, 'branch_name', None), notification_type='general',
                                     title='Please read and acknowledge a company policy',
                                     message=f'Please read "{p.title}" (version {v.version}) and confirm it under My policies.',
                                     source_model='CompanyPolicy', source_id=p.pk)
                n += 1
    except Exception:
        log.exception('policy reminder failed')
    v.last_reminded_at = timezone.now()
    v.save(update_fields=['last_reminded_at'])
    return n


class PolicyRemindView(Base, APIView):
    def post(self, request, pk):
        if not S.is_hr_policies(request, write=True):
            return deny('You may not send policy reminders.')
        p = _policies().filter(pk=pk).first()
        if p is None:
            return deny('Policy not found.', 404)
        return Response({'notified': remind_policy(p, request.user)})


class MyPoliciesView(Base, APIView):
    """ESS: the policies that apply to me with my acknowledgement of the current version."""

    def get(self, request):
        emp = S.my_employee(request)
        if emp is None:
            return Response([])
        out = []
        for p in _policies().order_by('title'):
            if not S.policy_applies(p, emp, user_id=request.user.pk):
                continue
            v = S.policy_version(p)
            a = PolicyAcknowledgement.objects.filter(policy_id=p.pk, employee_id=emp.pk, version=v.version).first()
            prev = PolicyAcknowledgement.objects.filter(policy_id=p.pk, employee_id=emp.pk, version__lt=v.version).exists()
            due = (v.version_date + timedelta(days=v.due_days)).date() if v.version_date else None
            out.append({'id': p.pk, 'title': p.title, 'description': p.description or '', 'version': v.version, 'version_date': v.version_date,
                        'requires_ack': v.requires_ack, 'acknowledged': bool(a), 'acknowledged_at': a.acknowledged_at if a else None,
                        'status': 'Acknowledged' if a else ('Not required' if not v.requires_ack else ('New version – please acknowledge again' if prev else 'Please acknowledge')),
                        'due_date': due, 'has_file': bool(p.policy_file)})
        return Response(out)


class MyPolicyAckView(Base, APIView):
    def post(self, request, pk):
        emp = S.my_employee(request)
        p = _policies().filter(pk=pk).first()
        if p is None:
            return deny('Policy not found.', 404)
        if emp is None or not S.policy_applies(p, emp, user_id=request.user.pk):
            return deny('This policy does not apply to you.')
        v = S.policy_version(p)
        try:
            a, created = PolicyAcknowledgement.objects.get_or_create(policy_id=p.pk, employee_id=emp.pk, version=v.version,
                                                                     defaults={'ip': _client_ip(request), 'user_id': request.user.pk})
        except IntegrityError:
            a, created = PolicyAcknowledgement.objects.get(policy_id=p.pk, employee_id=emp.pk, version=v.version), False
        if created:
            try:
                M('Chatter', 'Message').objects.create(model='EmpManagement.emp_master', object_id=str(emp.pk), author=request.user,
                                                       body=f'Acknowledged the company policy "{p.title}" (version {v.version}).')
            except Exception:
                pass
        return Response({'id': p.pk, 'version': v.version, 'acknowledged_at': a.acknowledged_at, 'already': not created})


class MyPolicyFileView(Base, APIView):
    def get(self, request, pk):
        emp = S.my_employee(request)
        p = _policies().filter(pk=pk).first()
        if p is None or not p.policy_file:
            return deny('The policy file was not found.', 404)
        if not (S.is_hr_policies(request) or (emp is not None and S.policy_applies(p, emp, user_id=request.user.pk))):
            return deny('This policy does not apply to you.')
        try:
            return FileResponse(p.policy_file.open('rb'), as_attachment=False, filename=p.policy_file.name.split('/')[-1])
        except Exception:
            return deny('The policy file could not be opened.', 404)


# ------------------------------------------------------------------ country policies
class CountryPolicySerializer(serializers.ModelSerializer):
    branches = serializers.SerializerMethodField()
    weekend_names = serializers.SerializerMethodField()

    class Meta:
        model = CountryPolicy
        fields = '__all__'
        read_only_fields = ['is_standard', 'updated_at']

    def get_branches(self, o):
        b = self.context.setdefault('bmap', dict(M('OrganisationManager', 'brnch_mstr').objects.values_list('id', 'branch_name')))
        return [{'id': x.branch_id, 'name': b.get(x.branch_id, f'#{x.branch_id}')} for x in o.branches.all()]

    def get_weekend_names(self, o):
        n = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        return ', '.join(n[d] for d in (o.weekend_days or []) if isinstance(d, int) and 0 <= d <= 6)

    def validate_code(self, v):
        v = (v or '').strip().upper()
        if not v:
            raise serializers.ValidationError('Enter a code.')
        return v

    def validate_country_code(self, v):
        v = (v or '').strip().upper()
        if len(v) != 2 or not v.isalpha():
            raise serializers.ValidationError('Use the 2-letter country code, e.g. AE.')
        return v

    def validate_weekend_days(self, v):
        if not isinstance(v, list) or any(not isinstance(d, int) or not 0 <= d <= 6 for d in v):
            raise serializers.ValidationError('Weekend days are numbers 0 (Monday) to 6 (Sunday).')
        if len(v) > 3:
            raise serializers.ValidationError('At most 3 weekend days.')
        return sorted(set(v))

    def validate_sick_leave(self, v):
        try:
            v = [[int(a), float(b)] for a, b in (v or [])]
        except (TypeError, ValueError):
            raise serializers.ValidationError('Sick leave slabs are pairs of [days, pay %].')
        if any(a <= 0 or not 0 <= b <= 100 for a, b in v):
            raise serializers.ValidationError('Days must be more than 0 and pay % between 0 and 100.')
        return v

    def _obj(self, v, name):
        if not isinstance(v, dict):
            raise serializers.ValidationError(f'{name} must be an object of settings.')
        return v

    def validate_annual_leave(self, v):
        v = self._obj(v, 'Annual leave')
        if 'days' in v and (not isinstance(v['days'], (int, float)) or v['days'] < 0 or v['days'] > 60):
            raise serializers.ValidationError('Annual leave days are between 0 and 60.')
        for s in v.get('steps') or []:
            if not isinstance(s, dict) or 'after_years' not in s or 'days' not in s:
                raise serializers.ValidationError('Each step needs after_years and days.')
        return v

    def validate_maternity(self, v):
        v = self._obj(v, 'Maternity')
        d = float(v.get('days') or 0)
        if float(v.get('full_pay_days') or 0) + float(v.get('half_pay_days') or 0) > d:
            raise serializers.ValidationError('Full-pay plus half-pay days cannot exceed the maternity days.')
        return v

    def validate_gratuity(self, v):
        v = self._obj(v, 'Gratuity')
        for s in v.get('slabs') or []:
            if not isinstance(s, dict) or 'days' not in s or 'from_year' not in s:
                raise serializers.ValidationError('Each gratuity slab needs from_year, to_year and days.')
        return v

    def validate_overtime(self, v):
        v = self._obj(v, 'Overtime')
        for k in ('normal', 'night', 'rest_day', 'holiday'):
            if k in v and (not isinstance(v[k], (int, float)) or not 100 <= v[k] <= 400):
                raise serializers.ValidationError(f'Overtime {k.replace("_", " ")} rate is a % of the hourly wage between 100 and 400.')
        return v

    def validate_notice_period(self, v):
        v = self._obj(v, 'Notice period')
        lo, hi = v.get('min_days'), v.get('max_days')
        if lo is not None and hi is not None and lo > hi:
            raise serializers.ValidationError('The minimum notice cannot be more than the maximum.')
        return v

    def validate(self, d):
        dh = d.get('daily_hours', getattr(self.instance, 'daily_hours', 8))
        wh = d.get('weekly_hours', getattr(self.instance, 'weekly_hours', 48))
        rh = d.get('ramadan_daily_hours', getattr(self.instance, 'ramadan_daily_hours', None))
        if dh is not None and not 0 < dh <= 12:
            raise serializers.ValidationError({'daily_hours': 'Daily hours are between 1 and 12.'})
        if wh is not None and not 0 < wh <= 72:
            raise serializers.ValidationError({'weekly_hours': 'Weekly hours are between 1 and 72.'})
        if rh is not None and dh is not None and rh > dh:
            raise serializers.ValidationError({'ramadan_daily_hours': 'Ramadan hours cannot be more than the normal daily hours.'})
        return d


class CountryPolicyViewSet(Base, viewsets.ModelViewSet):
    queryset = CountryPolicy.objects.prefetch_related('branches')
    serializer_class = CountryPolicySerializer

    def get_queryset(self):
        qs = super().get_queryset()
        if not CountryPolicy.objects.exists():
            seed.load_standard()
        q = self.request.query_params
        if q.get('country'):
            qs = qs.filter(country_code__iexact=q['country'])
        if q.get('active') in ('1', 'true'):
            qs = qs.filter(active=True)
        return qs

    def _w(self):
        return S.is_hr_country(self.request, write=True)

    def create(self, request, *a, **kw):
        if not self._w():
            return deny('You may not add country policies.')
        return super().create(request, *a, **kw)

    def update(self, request, *a, **kw):
        if not self._w():
            return deny('You may not change country policies.')
        return super().update(request, *a, **kw)

    def destroy(self, request, *a, **kw):
        if not self._w():
            return deny('You may not delete country policies.')
        obj = self.get_object()
        if obj.branches.exists():
            return deny(f'{obj.name} is applied to {obj.branches.count()} branch(es) – apply another policy to them first.', 400)
        return super().destroy(request, *a, **kw)

    @action(detail=False, methods=['post'], url_path='load-standard')
    def load_standard(self, request):
        if not self._w():
            return deny('You may not load country policies.')
        overwrite = str((request.data or {}).get('overwrite', '')).lower() in ('1', 'true')
        c, u = seed.load_standard(overwrite=overwrite)
        return Response({'created': c, 'updated': u})

    @action(detail=True, methods=['post'], url_path='apply')
    def apply(self, request, pk=None):
        """{branch_ids: [..]} or {branch_ids: 'all'} – the policy becomes the rule set of those branches."""
        if not self._w():
            return deny('You may not apply country policies.')
        obj = self.get_object()
        raw = (request.data or {}).get('branch_ids')
        Br = M('OrganisationManager', 'brnch_mstr')
        try:
            ids = list(Br.objects.values_list('id', flat=True)) if raw == 'all' else _ints(raw)
        except serializers.ValidationError as e:
            return Response({'branch_ids': e.detail}, status=400)
        if not ids:
            return Response({'branch_ids': 'Choose at least one branch.'}, status=400)
        known = set(Br.objects.filter(pk__in=ids).values_list('id', flat=True))
        bad = [i for i in ids if i not in known]
        if bad:
            return Response({'branch_ids': f'Branch {bad[0]} does not exist.'}, status=400)
        allowed = S.user_branches(request)
        if allowed is not None and any(i not in allowed for i in ids):
            return deny('You can only apply policies to your own branches.')
        if not obj.active:
            return deny('This policy is inactive – activate it before applying it.', 400)
        for b in ids:
            BranchCountryPolicy.objects.update_or_create(branch_id=b, defaults={'country_policy': obj, 'applied_by_id': request.user.pk})
        return Response({'applied': ids, 'policy': obj.name})

    @action(detail=False, methods=['get'], url_path='for-branch')
    def for_branch(self, request):
        b = request.query_params.get('branch')
        p = S.country_policy_for(int(b)) if b and b.isdigit() else None
        link = BranchCountryPolicy.objects.filter(branch_id=b).exists() if b and b.isdigit() else False
        return Response({'branch': b, 'source': 'applied' if link else ('country' if p else 'none'),
                         'policy': CountryPolicySerializer(p, context=self.get_serializer_context()).data if p else None})


class BranchCountryPolicyView(Base, APIView):
    """Every branch with its rule set (applied or by country)."""

    def get(self, request):
        out = []
        Br = M('OrganisationManager', 'brnch_mstr')
        links = {x.branch_id: x for x in BranchCountryPolicy.objects.select_related('country_policy')}
        allowed = S.user_branches(request)
        for b in Br.objects.order_by('branch_name'):
            if allowed is not None and b.pk not in allowed:
                continue
            p = S.country_policy_for(b.pk)
            out.append({'branch_id': b.pk, 'branch': b.branch_name, 'policy_id': p.pk if p else None, 'policy': p.name if p else '',
                        'source': 'Applied' if b.pk in links else ('By country' if p else 'None')})
        return Response(out)

    def delete(self, request):
        if not S.is_hr_country(request, write=True):
            return deny('You may not change country policies.')
        b = request.query_params.get('branch')
        allowed = S.user_branches(request)
        if allowed is not None and (not b or not b.isdigit() or int(b) not in allowed):
            return deny('You can only change your own branches.')
        BranchCountryPolicy.objects.filter(branch_id=b).delete()
        return Response(status=204)


# ------------------------------------------------------------------ headcount
class HeadcountView(Base, APIView):
    """GET ?group=division|section|cost_center|grade|location|job_position|employment_type → active employees per value."""

    def get(self, request):
        if not S.is_hr_employees(request):
            return deny('You may not see headcount.')
        from .reports import headcount_rows
        try:
            cols, rows = headcount_rows(_emp_qs(request), request.query_params.get('group', 'division'))
        except ValueError as e:
            return deny(str(e), 400)
        return Response({'columns': [{'key': k, 'label': l, 'type': t} for k, l, t in cols], 'rows': rows})
