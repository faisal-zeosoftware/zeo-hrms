from datetime import date

from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from . import models as M
from . import serializers as S
from . import services
from .access import LPPermission, c, can_see_employee, employees_qs, is_hr, me, visible_employee_ids


def run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except DjangoValidationError as exc:
        raise ValidationError({'detail': exc.messages})


def _lm():
    from LearningManagement import models as m
    return m


def _emp(emp_id):
    from EmpManagement.models import emp_master
    return get_object_or_404(emp_master, pk=emp_id)


class LPViewSet(viewsets.ModelViewSet):
    """zeo_access / zeo_scope off: LPPermission + scope() below apply the same company / branch / own-row rules."""
    zeo_access = False
    zeo_scope = False
    permission_classes = [LPPermission]
    filter_params = {}
    employee_field = None          # rows belong to an employee (integer id) -> own / team / HR branches
    hr_only_list = False

    def get_queryset(self):
        qs = super().get_queryset()
        for param, lookup in self.filter_params.items():
            val = self.request.query_params.get(param)
            if val not in (None, ''):
                if val.lower() in ('true', 'false'):
                    val = val.lower() == 'true'
                qs = qs.filter(**{lookup: val.split(',') if lookup.endswith('__in') else val})
        return self.scope(qs)

    def scope(self, qs):
        if self.employee_field:
            ids = visible_employee_ids(self.request)
            if ids is not None:
                qs = qs.filter(**{f"{self.employee_field}__in": ids})
        elif self.hr_only_list and not is_hr(self.request):
            return qs.none()
        return qs


class CategoryViewSet(LPViewSet):
    queryset = M.TrainingCategory.objects.all()
    serializer_class = S.TrainingCategorySerializer
    filter_params = {'active': 'is_active'}


class ProviderViewSet(LPViewSet):
    queryset = M.Provider.objects.all()
    serializer_class = S.ProviderSerializer
    filter_params = {'active': 'is_active'}


class TrainerViewSet(LPViewSet):
    queryset = M.Trainer.objects.select_related('provider').all()
    serializer_class = S.TrainerSerializer
    filter_params = {'active': 'is_active', 'type': 'trainer_type', 'provider': 'provider_id'}


class VenueViewSet(LPViewSet):
    queryset = M.Venue.objects.all()
    serializer_class = S.VenueSerializer
    filter_params = {'active': 'is_active'}


class CourseExtraViewSet(LPViewSet):
    queryset = M.CourseExtra.objects.select_related('category', 'provider').all()
    serializer_class = S.CourseExtraSerializer
    filter_params = {'course': 'course_id', 'course_id': 'course_id', 'category': 'category_id', 'provider': 'provider_id'}


class SessionExtraViewSet(LPViewSet):
    queryset = M.SessionExtra.objects.select_related('trainer', 'provider', 'venue').all()
    serializer_class = S.SessionExtraSerializer
    filter_params = {'session': 'session_id', 'session_id': 'session_id', 'trainer': 'trainer_id'}


class CourseModuleViewSet(LPViewSet):
    queryset = M.CourseModule.objects.all()
    serializer_class = S.CourseModuleSerializer
    filter_params = {'course': 'course_id', 'course_id': 'course_id', 'active': 'is_active'}
    self_service_actions = {'open', 'complete', 'progress'}

    def _employee(self, request):
        emp = me(request)
        target = request.data.get('employee') or request.data.get('employee_id')
        if target and (emp is None or int(target) != emp.id):
            if not is_hr(request):
                raise PermissionDenied('You can only work on your own courses.')
            return _emp(target)
        if emp is None:
            raise PermissionDenied('Your login is not linked to an employee.')
        return emp

    def _out(self, res):
        p = res['progress']
        return {'progress': S.ModuleProgressSerializer(p).data, 'course': res['course'],
                'course_completed': res.get('course_completed', False)}

    @action(detail=True, methods=['post'])
    def open(self, request, pk=None):
        mod = self.get_object()
        emp = self._employee(request)
        p = run(services.open_module, mod, emp)
        return Response(self._out({'progress': p, 'course': services.course_progress(emp.id, mod.course_id)}))

    @action(detail=True, methods=['post'])
    def progress(self, request, pk=None):
        mod = self.get_object()
        return Response(self._out(run(services.set_progress, mod, self._employee(request), request.data.get('progress', 0))))

    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):
        mod = self.get_object()
        return Response(self._out(run(services.complete_module, mod, self._employee(request), request.data.get('score'))))


class ModuleProgressViewSet(LPViewSet):
    queryset = M.ModuleProgress.objects.select_related('module').all()
    serializer_class = S.ModuleProgressSerializer
    http_method_names = ['get', 'head', 'options']
    employee_field = 'employee_id'
    filter_params = {'employee': 'employee_id', 'module': 'module_id', 'course': 'module__course_id'}


class OnlineEnrolmentViewSet(LPViewSet):
    queryset = M.OnlineEnrolment.objects.all()
    serializer_class = S.OnlineEnrolmentSerializer
    http_method_names = ['get', 'post', 'delete', 'head', 'options']
    employee_field = 'employee_id'
    filter_params = {'employee': 'employee_id', 'course': 'course_id', 'status': 'status__in'}
    self_service_actions = {'create'}

    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        emp = _emp(ser.validated_data['employee_id'])
        own = me(request)
        if not is_hr(request) and not (own and own.id == emp.id) and emp.emp_reporting_manager_id != request.user.id:
            raise PermissionDenied('You can enrol yourself or your team only.')
        crs = get_object_or_404(_lm().Course, pk=ser.validated_data['course_id'])
        if not M.CourseModule.objects.filter(course_id=crs.id, is_active=True).exists():
            raise ValidationError({'detail': 'This course has no online modules yet.'})
        en = services.enrol(crs, emp, request.user)
        return Response(S.OnlineEnrolmentSerializer(en).data, status=201)


class SessionAttendanceViewSet(LPViewSet):
    queryset = M.SessionAttendance.objects.all()
    serializer_class = S.SessionAttendanceSerializer
    filter_params = {'session': 'session_id', 'session_id': 'session_id', 'nomination': 'nomination_id'}

    def scope(self, qs):
        if is_hr(self.request):
            return qs
        ids = visible_employee_ids(self.request) or set()
        noms = _lm().Nomination.objects.filter(employee_id__in=ids).values_list('id', flat=True)
        return qs.filter(nomination_id__in=list(noms))

    def perform_create(self, serializer):
        obj = serializer.save()
        services.sync_attendance(obj.session_id)

    def perform_update(self, serializer):
        obj = serializer.save()
        services.sync_attendance(obj.session_id)

    @action(detail=False, methods=['get'])
    def sheet(self, request):
        s = get_object_or_404(_lm().TrainingSession.objects.select_related('course'), pk=request.query_params.get('session'))
        data = services.attendance_sheet(s)
        if not is_hr(request):
            ids = visible_employee_ids(request) or set()
            data['rows'] = [r for r in data['rows'] if r['employee_id'] in ids]
        return Response(data)

    @action(detail=False, methods=['post'])
    def bulk(self, request):
        s = get_object_or_404(_lm().TrainingSession, pk=request.data.get('session'))
        res = run(services.save_attendance, s, request.data.get('rows') or [])
        return Response({'detail': f"{res['saved']} attendance mark(s) saved.", **res})


class TrainingBudgetViewSet(LPViewSet):
    queryset = M.TrainingBudget.objects.all()
    serializer_class = S.TrainingBudgetSerializer
    filter_params = {'year': 'year', 'department': 'department_id'}
    hr_only_list = True

    def perform_create(self, serializer):
        d = serializer.validated_data
        if M.TrainingBudget.objects.filter(year=d['year'], department_id=d.get('department_id')).exists():
            raise ValidationError({'detail': 'A budget for this year and department already exists.'})
        serializer.save()


class SessionCostViewSet(LPViewSet):
    queryset = M.SessionCost.objects.all()
    serializer_class = S.SessionCostSerializer
    filter_params = {'session': 'session_id', 'session_id': 'session_id', 'kind': 'kind__in'}
    hr_only_list = True


class SkillViewSet(LPViewSet):
    queryset = M.Skill.objects.all()
    serializer_class = S.SkillSerializer
    filter_params = {'active': 'is_active', 'category': 'category'}


class SkillLevelViewSet(LPViewSet):
    queryset = M.SkillLevel.objects.all()
    serializer_class = S.SkillLevelSerializer

    @action(detail=False, methods=['get'])
    def scale(self, request):
        return Response(services.skill_levels())


class CourseSkillViewSet(LPViewSet):
    queryset = M.CourseSkill.objects.select_related('skill').all()
    serializer_class = S.CourseSkillSerializer
    filter_params = {'course': 'course_id', 'course_id': 'course_id', 'skill': 'skill_id'}


class RoleSkillViewSet(LPViewSet):
    queryset = M.RoleSkill.objects.select_related('skill').all()
    serializer_class = S.RoleSkillSerializer
    filter_params = {'designation': 'designation_id', 'designation_id': 'designation_id', 'skill': 'skill_id'}


class EmployeeSkillViewSet(LPViewSet):
    """HR: anyone in their branches (verified); manager: team (verified); employee: own self-assessment (not verified)."""
    queryset = M.EmployeeSkill.objects.select_related('skill').all()
    serializer_class = S.EmployeeSkillSerializer
    employee_field = 'employee_id'
    filter_params = {'employee': 'employee_id', 'skill': 'skill_id', 'source': 'source__in'}
    self_service = True

    def _check(self, emp_id):
        if not can_see_employee(self.request, emp_id):
            raise PermissionDenied('You can only record skills of yourself or your team.')
        own = me(self.request)
        return None if (own and own.id == int(emp_id) and not is_hr(self.request)) else self.request.user.id

    def perform_create(self, serializer):
        serializer.save(verified_by=self._check(serializer.validated_data['employee_id']))

    def perform_update(self, serializer):
        emp_id = serializer.validated_data.get('employee_id', serializer.instance.employee_id)
        self._check(serializer.instance.employee_id)
        serializer.save(verified_by=self._check(emp_id))

    def perform_destroy(self, instance):
        self._check(instance.employee_id)
        instance.delete()


# ---------------------------------------------------------------------------- reports / pages
class LPView(APIView):
    zeo_access = False
    zeo_scope = False
    permission_classes = [LPPermission]


class CalendarView(LPView):
    """GET ?from=YYYY-MM-DD&to=YYYY-MM-DD[&course=] -> training sessions as calendar events."""

    def get(self, request):
        today = timezone.localdate()
        try:
            frm = date.fromisoformat(request.query_params.get('from') or today.replace(day=1).isoformat())
            to = date.fromisoformat(request.query_params.get('to') or (frm.replace(day=28)).isoformat())
        except ValueError:
            raise ValidationError({'detail': 'from / to must be YYYY-MM-DD.'})
        if to < frm:
            raise ValidationError({'detail': '"to" is before "from".'})
        hr = is_hr(request)
        ev = services.calendar_events(frm, to, employee=me(request), include_drafts=hr,
                                      include_cancelled=request.query_params.get('cancelled') == 'true',
                                      course=request.query_params.get('course'))
        return Response({'from': frm, 'to': to, 'events': ev})


class HistoryView(LPView):
    """GET ?employee=<id>: training history. Employee: own; manager: team; HR: all (their branches)."""

    def get(self, request):
        emp_id = request.query_params.get('employee')
        own = me(request)
        if not emp_id:
            if own is None:
                raise ValidationError({'detail': 'Pick an employee.'})
            emp_id = own.id
        if not can_see_employee(request, emp_id):
            raise PermissionDenied('You can only see your own training history (or your team\'s).')
        return Response(services.history(_emp(emp_id)))


class SkillMatrixView(LPView):
    """GET ?department=&designation=&skill=1,2&gaps=true -> employees x skills with level, required level and gap."""

    def get(self, request):
        qs = employees_qs(request)
        p = request.query_params
        if p.get('department'):
            qs = qs.filter(emp_dept_id__in=p['department'].split(','))
        if p.get('designation'):
            qs = qs.filter(emp_desgntn_id__in=p['designation'].split(','))
        if p.get('branch'):
            qs = qs.filter(emp_branch_id__in=p['branch'].split(','))
        if p.get('employee'):
            qs = qs.filter(id__in=p['employee'].split(','))
        qs = qs.exclude(is_active=False)
        skills = [int(x) for x in p['skill'].split(',') if x.isdigit()] if p.get('skill') else None
        return Response(services.skill_matrix(qs.order_by('emp_code'), skills, p.get('gaps') == 'true'))


class CourseProgressView(LPView):
    def get(self, request):
        crs = request.query_params.get('course')
        emp_id = request.query_params.get('employee') or (me(request).id if me(request) else None)
        if not crs or not emp_id:
            raise ValidationError({'detail': 'course and employee are required.'})
        if not can_see_employee(request, emp_id):
            raise PermissionDenied('Not your record.')
        return Response(services.course_progress(int(emp_id), int(crs)))


class MyLearningView(LPView):
    """ESS: my nominations, my online courses (modules + progress), my certificates, online courses I can start."""

    def get(self, request):
        m = _lm()
        emp_id = request.query_params.get('employee')
        emp = me(request)
        if emp_id:
            if not can_see_employee(request, emp_id):
                raise PermissionDenied('Not your record.')
            emp = _emp(emp_id)
        if emp is None:
            raise ValidationError({'detail': 'Your login is not linked to an employee.'})
        noms = []
        for n in m.Nomination.objects.filter(employee=emp).select_related('session__course').order_by('-session__start_date'):
            r = m.ParticipantResult.objects.filter(nomination=n).first()
            noms.append({'id': n.id, 'course': n.session.course.title, 'session': n.session.code, 'start_date': n.session.start_date,
                         'end_date': n.session.end_date, 'venue': n.session.venue, 'online_link': n.session.online_link,
                         'session_status': n.session.status, 'manager_status': n.manager_status, 'ld_status': n.ld_status,
                         'seat_status': n.seat_status, 'bond_required': n.bond_required, 'bond_accepted': bool(n.bond_accepted_on),
                         'passed': r.passed if r else None, 'attendance_percent': r.attendance_percent if r else None})
        online = []
        for en in M.OnlineEnrolment.objects.filter(employee_id=emp.id):
            crs = m.Course.objects.filter(pk=en.course_id).first()
            if crs:
                online.append({'id': en.id, 'course_id': crs.id, 'course': crs.title, 'code': crs.code, 'status': en.status,
                               'score': en.score, 'completed_at': en.completed_at, 'certificate_id': en.certificate_id,
                               'progress': services.course_progress(emp.id, crs.id)})
        enrolled = {o['course_id'] for o in online}
        with_modules = set(M.CourseModule.objects.filter(is_active=True).values_list('course_id', flat=True))
        available = [{'id': c_.id, 'code': c_.code, 'title': c_.title, 'duration_hours': c_.duration_hours}
                     for c_ in m.Course.objects.filter(id__in=with_modules - enrolled, is_active=True)]
        certs = [{'id': x.id, 'title': x.title, 'number': x.certificate_number, 'issued_on': x.issued_on, 'expiry_date': x.expiry_date,
                  'status': x.status, 'issued_by': x.issued_by, 'file': x.file.url if x.file else None}
                 for x in m.Certificate.objects.filter(employee=emp).select_related('course')]
        return Response({'employee': {'id': emp.id, 'name': services.emp_name(emp)}, 'nominations': noms, 'online': online,
                         'available': available, 'certificates': certs})


class BudgetView(LPView):
    """GET ?year=2026[&department=1,2] -> budget vs actual by department."""

    def get(self, request):
        if not is_hr(request):
            raise PermissionDenied('Training budget is for L&D / HR.')
        year = int(request.query_params.get('year') or timezone.localdate().year)
        deps = request.query_params.get('department')
        return Response(services.budget_vs_actual(year, deps.split(',') if deps else None))


class EmployeeCostView(LPView):
    def get(self, request):
        emp_id = request.query_params.get('employee') or (me(request).id if me(request) else None)
        if not emp_id:
            raise ValidationError({'detail': 'Pick an employee.'})
        if not can_see_employee(request, emp_id):
            raise PermissionDenied('Not your record.')
        y = request.query_params.get('year')
        return Response(services.employee_costs(_emp(emp_id), int(y) if y else None))


class CertificateDocView(LPView):
    """GET certificates/<id>/pdf/ -> PDF (reportlab); ?view=html -> printable HTML; ?download=1 -> attachment."""

    def get(self, request, pk):
        cert = get_object_or_404(_lm().Certificate.objects.select_related('employee', 'course'), pk=pk)
        if not can_see_employee(request, cert.employee_id):
            raise PermissionDenied('You can only open your own certificates.')
        name = f"certificate-{(cert.certificate_number or str(cert.id)).replace('/', '-')}"
        if request.query_params.get('view') == 'html':
            return HttpResponse(services.certificate_html(cert), content_type='text/html; charset=utf-8')
        resp = HttpResponse(services.certificate_pdf(cert), content_type='application/pdf')
        resp['Content-Disposition'] = f'{"attachment" if request.query_params.get("download") else "inline"}; filename="{name}.pdf"'
        return resp


class AlertsView(LPView):
    """POST {job: 'certificates' | 'nominations', days?} -> run the scheduled alert now (L&D / HR)."""

    def post(self, request):
        if not is_hr(request):
            raise PermissionDenied('Only L&D / HR can run alerts.')
        job = request.data.get('job', 'certificates')
        if job == 'nominations':
            n = services.escalate_nominations(int(request.data.get('days', 3)))
            return Response({'detail': f'{n} nomination(s) escalated.', 'escalated': n})
        out = services.certificate_expiry_alerts()
        return Response({'detail': f"{out['alerts']} certificate alert(s) sent, {out['needs_created']} renewal need(s) created.", **out})


class TrainerStatsView(LPView):
    def get(self, request, pk):
        return Response(services.trainer_stats(get_object_or_404(M.Trainer, pk=pk)))
