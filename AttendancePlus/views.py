"""AttendancePlus API (v1.12.0) – mounted at /attendance-plus/api/ (ADMS device endpoints at /iclock/)."""
from datetime import timedelta

from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status as st, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from . import engine as E
from . import models as M
from . import serializers as SER
from . import services as S
from .access import (APPermission, branch_ok, c, can_see_employee, hr_employee_ids, hr_for_employee, is_hr, me,
                     team_ids, visible_employee_ids)


def refused(exc):
    return Response({'detail': str(exc)}, status=getattr(exc, 'code', 400))


def _emp(emp_id):
    from EmpManagement.models import emp_master
    return get_object_or_404(emp_master.objects.select_related('emp_branch_id', 'users'), pk=emp_id)


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


class APViewSet(viewsets.ModelViewSet):
    zeo_access = False
    zeo_scope = False
    permission_classes = [APPermission]
    filter_params = {}
    employee_field = None
    branch_field = None
    hr_only_read = False

    def get_queryset(self):
        qs = super().get_queryset()
        for param, lookup in self.filter_params.items():
            val = self.request.query_params.get(param)
            if val not in (None, ''):
                if val.lower() in ('true', 'false'):
                    val = val.lower() == 'true'
                qs = qs.filter(**{lookup: val.split(',') if lookup.endswith('__in') else val})
        if self.employee_field:
            ids = visible_employee_ids(self.request)
            if ids is not None:
                qs = qs.filter(**{f'{self.employee_field}__in': ids})
        elif self.branch_field:
            x = c(self.request)
            if not x.admin and x.branches is not None:
                qs = qs.filter(Q(**{f'{self.branch_field}__in': x.branches}) | Q(**{f'{self.branch_field}__isnull': True}))
        return qs

    def _check_branch(self, serializer):
        if self.branch_field:
            b = serializer.validated_data.get(self.branch_field, getattr(serializer.instance, self.branch_field, None))
            if not branch_ok(self.request, b):
                raise PermissionDenied('You can only set this up for your own branches.')

    def perform_create(self, serializer):
        self._check_branch(serializer)
        serializer.save()

    def perform_update(self, serializer):
        self._check_branch(serializer)
        serializer.save()

    def perform_destroy(self, instance):
        if self.branch_field and not branch_ok(self.request, getattr(instance, self.branch_field)):
            raise PermissionDenied('You can only delete records of your own branches.')
        instance.delete()


# ----------------------------------------------------------------------------- setup
class RuleViewSet(APViewSet):
    queryset = M.AttendanceRule.objects.all()
    serializer_class = SER.RuleSerializer
    filter_params = {'scope': 'scope', 'active': 'is_active', 'branch': 'branch_id'}
    branch_field = 'branch_id'

    def _check_branch(self, serializer):
        """Branch-restricted HR: branch rules of their branches and employee rules of their employees only;
        company / department / category rules need an HR user for all branches."""
        g = lambda k: serializer.validated_data.get(k, getattr(serializer.instance, k, None))  # noqa: E731
        scope = g('scope') or 'company'
        x = c(self.request)
        if x.admin or x.branches is None:
            return
        if scope == 'branch' and branch_ok(self.request, g('branch_id')):
            return
        if scope == 'employee' and g('employee_id') and hr_for_employee(self.request, g('employee_id')):
            return
        raise PermissionDenied('You can only set up rules for your own branches or their employees. '
                               'Company, department and category rules are set up by HR for all branches.')

    def perform_destroy(self, instance):
        x = c(self.request)
        if not (x.admin or x.branches is None or (instance.scope == 'branch' and branch_ok(self.request, instance.branch_id))
                or (instance.scope == 'employee' and instance.employee_id and hr_for_employee(self.request, instance.employee_id))):
            raise PermissionDenied('You can only delete rules of your own branches.')
        instance.delete()

    @action(detail=False, methods=['get'])
    def effective(self, request):
        """The rule and the shift that apply to an employee on a day (for the rules screen / ESS)."""
        emp_id = request.query_params.get('employee') or (me(request) and me(request).id)
        if not emp_id or not can_see_employee(request, emp_id):
            raise PermissionDenied('You can only look at your own or your team\'s rules.')
        e = _emp(emp_id)
        day = E.as_date(request.query_params.get('date') or E.today())
        r = E.rule_for(e)
        sh = E.shift_info(e, day, r)
        return Response({'rule': SER.RuleSerializer(r).data if r.id else {'name': r.name, 'id': None},
                         'shift': {k: (E.local(v).isoformat() if hasattr(v, 'tzinfo') and v else v) for k, v in (sh or {}).items()},
                         'methods': [{'value': k, 'label': v} for k, v in M.METHODS]})


class IPViewSet(APViewSet):
    queryset = M.IPRestriction.objects.all()
    serializer_class = SER.IPSerializer
    filter_params = {'branch': 'branch_id', 'active': 'is_active'}
    branch_field = 'branch_id'
    hr_only_read = True
    self_service_actions = {'my_ip'}

    @action(detail=False, methods=['get'])
    def my_ip(self, request):
        e = me(request)
        ip = S.client_ip(request)
        ok, msg = S.ip_allowed(e, ip) if e else (None, '')
        return Response({'ip': ip, 'allowed': ok, 'message': msg})


class DeviceViewSet(APViewSet):
    queryset = M.Device.objects.all()
    serializer_class = SER.DeviceSerializer
    filter_params = {'branch': 'branch_id', 'active': 'is_active', 'type': 'device_type'}
    branch_field = 'branch_id'
    hr_only_read = True

    @action(detail=True, methods=['post'])
    def new_key(self, request, pk=None):
        d = self.get_object()
        if not branch_ok(request, d.branch_id):
            raise PermissionDenied('You can only change devices of your own branches.')
        d.api_key = M.new_token()
        d.save(update_fields=['api_key'])
        return Response(SER.DeviceSerializer(d).data)


class KioskViewSet(APViewSet):
    queryset = M.Kiosk.objects.all()
    serializer_class = SER.KioskSerializer
    filter_params = {'branch': 'branch_id', 'active': 'is_active'}
    branch_field = 'branch_id'
    hr_only_read = True

    @action(detail=True, methods=['post'])
    def new_token(self, request, pk=None):
        k = self.get_object()
        if not branch_ok(request, k.branch_id):
            raise PermissionDenied('You can only change kiosks of your own branches.')
        k.token = M.new_token()
        k.save(update_fields=['token'])
        return Response(SER.KioskSerializer(k).data)


# ----------------------------------------------------------------------------- punches
class PunchViewSet(APViewSet):
    queryset = M.Punch.objects.select_related('device', 'kiosk').all()
    serializer_class = SER.PunchSerializer
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    filter_params = {'employee': 'employee_id', 'date': 'work_date', 'from': 'work_date__gte', 'to': 'work_date__lte',
                     'source': 'source', 'void': 'is_void', 'device': 'device_id', 'kiosk': 'kiosk_id'}
    employee_field = 'employee_id'
    self_service_actions = {'punch', 'my_status', 'my_qr', 'set_pin'}
    http_method_names = ['get', 'post', 'head', 'options']

    def create(self, request, *args, **kwargs):
        """HR manual punch for an employee (source = manual)."""
        emp_id = request.data.get('employee_id')
        if not emp_id or not hr_for_employee(request, emp_id):
            raise PermissionDenied('Only HR of the employee\'s branch can add punches for an employee.')
        try:
            ts = S._parse_dt(request.data.get('ts'))
        except S.Refused as exc:
            return refused(exc)
        if not ts:
            raise ValidationError({'ts': 'Give the punch date and time.'})
        kind = request.data.get('kind') or 'auto'
        if kind not in dict(M.PUNCH_KINDS):
            raise ValidationError({'kind': 'Unknown punch type.'})
        p, created = E.add_punch(_emp(emp_id), ts, kind, 'manual', user_id=request.user.id, note=request.data.get('note') or '',
                                 check=False)
        return Response(SER.PunchSerializer(p).data, status=201 if created else 200)

    @action(detail=False, methods=['post'])
    def punch(self, request):
        """Clock in / out / break / lunch for yourself (web, mobile, biometric selfie, site QR).
        HR (or an HR-registered shared device login) may punch for an employee of their branches."""
        x = c(request)
        target = request.data.get('employee') or request.data.get('employee_id')
        e = x.emp
        if target and (e is None or str(target) != str(e.id)):
            if not hr_for_employee(request, target):
                raise PermissionDenied('You can only punch for yourself.')
            e = _emp(target)
        if e is None:
            raise PermissionDenied('Your login is not linked to an employee.')
        kind = request.data.get('kind') or 'auto'
        if kind not in dict(M.PUNCH_KINDS):
            raise ValidationError({'kind': 'Unknown punch type.'})
        method = (request.data.get('source') or request.data.get('method') or 'web').lower()
        if method not in ('web', 'mobile', 'biometric', 'qr'):
            raise ValidationError({'source': 'Use web, mobile, biometric or qr here (kiosks and devices have their own endpoints).'})
        kiosk = None
        rule = E.rule_for(e)
        lat, lng = request.data.get('lat') or request.data.get('latitude'), request.data.get('lng') or request.data.get('longitude')
        photo = request.FILES.get('photo') or request.data.get('photo') or None
        try:
            from calendars.face_utils import convert_base64_to_file
            photo = convert_base64_to_file(photo, 'punch') if photo else None
        except Exception:
            pass
        verified = 'login'
        try:
            if method == 'qr':
                kiosk = S.kiosk_from_site_qr(request.data.get('site_qr'))
                verified = 'qr'
            if method == 'biometric':
                face = request.FILES.get('face_photo') or request.data.get('face_photo') or photo
                if not face:
                    raise S.Refused('Take a face photo to punch with face recognition.')
                from calendars import face_utils
                enc = face_utils.get_face_encoding(face_utils.convert_base64_to_file(face, 'face'))
                if not enc or not face_utils.verify_face(e.face_encoding, enc):
                    raise S.Refused('Your face did not match the registered face. Try again in good light or ask HR to register your face.', 403)
                verified = 'face'
            geo = S.check_punch(e, rule, method, ip=S.client_ip(request), lat=lat, lng=lng, photo=photo)
            p, created = E.add_punch(e, None, kind, method, kiosk=kiosk, device_ref=request.data.get('device_id') or '',
                                     ip=S.client_ip(request), lat=lat or None, lng=lng or None,
                                     location=request.data.get('location') or '', photo=photo, verified_by=verified,
                                     user_id=request.user.id)
            p.geofence_ok = geo
            p.save(update_fields=['geofence_ok'])
        except (S.Refused, E.PunchError) as exc:
            return refused(exc)
        day = M.AttendanceDay.objects.filter(employee_id=e.id, date=p.work_date).first()
        return Response({'detail': f"{dict(M.PUNCH_KINDS).get(p.resolved_kind or p.kind)} recorded at {E.local(p.ts):%H:%M}.",
                         'punch': SER.PunchSerializer(p).data, 'day': SER.DaySerializer(day).data if day else None},
                        status=201 if created else 200)

    @action(detail=False, methods=['get'])
    def my_status(self, request):
        """Current state for the punch buttons: what the next punch is, today's punches, breaks."""
        x = c(request)
        target = request.query_params.get('employee')
        e = x.emp
        if target and (e is None or str(target) != str(e.id)):
            if not can_see_employee(request, target):
                raise PermissionDenied('You can only see your own attendance.')
            e = _emp(target)
        if e is None:
            raise PermissionDenied('Your login is not linked to an employee.')
        rule = E.rule_for(e)
        day = E.work_date_for(e, E.now(), rule)
        ps = E.resolve_kinds(list(M.Punch.objects.filter(employee_id=e.id, work_date=day, is_void=False).order_by('ts')))
        on_break = None
        for p in ps:
            if p.resolved_kind in ('break_out', 'lunch_out'):
                on_break = p.resolved_kind
            elif p.resolved_kind in ('break_in', 'lunch_in', 'out'):
                on_break = None
        nk = E.next_kind(e.id, day)
        d = M.AttendanceDay.objects.filter(employee_id=e.id, date=day).first()
        sh = E.shift_info(e, day, rule)
        return Response({
            'employee_id': e.id, 'work_date': day, 'next': nk, 'clocked_in': nk == 'out', 'on_break': on_break,
            'punches': SER.PunchSerializer(ps, many=True).data, 'day': SER.DaySerializer(d).data if d else None,
            'shift': ({'name': sh.get('name'), 'start': E.local(sh['start']).strftime('%H:%M') if sh.get('start') else '',
                       'end': E.local(sh['end']).strftime('%H:%M') if sh.get('end') else '', 'off': sh.get('off')} if sh else None),
            'methods': rule.allowed_methods or [m for m in M.METHOD_KEYS if m not in ('import', 'correction', 'manual')],
            'require_gps': rule.require_gps or rule.require_geofence, 'require_selfie': rule.require_selfie, 'require_ip': rule.require_ip,
        })

    @action(detail=False, methods=['get'])
    def my_qr(self, request):
        """Badge QR text for the employee (yourself, or any employee for HR) – show it as a QR code."""
        target = request.query_params.get('employee')
        e = me(request)
        if target and (e is None or str(target) != str(e.id)):
            if not hr_for_employee(request, target):
                raise PermissionDenied('You can only see your own QR badge.')
            e = _emp(target)
        if e is None:
            raise PermissionDenied('Your login is not linked to an employee.')
        return Response({'employee_id': e.id, 'employee_code': e.emp_code, 'qr': S.employee_qr(e)})

    @action(detail=False, methods=['post'])
    def reset_qr(self, request):
        emp_id = request.data.get('employee')
        if not emp_id or not hr_for_employee(request, emp_id):
            raise PermissionDenied('Only HR can cancel QR badges.')
        k = S.employee_key(int(emp_id))
        k.qr_version += 1
        k.save(update_fields=['qr_version', 'updated_at'])
        return Response({'detail': 'The old QR badge no longer works. Print the new one.', 'qr': S.employee_qr(_emp(emp_id))})

    @action(detail=False, methods=['post'])
    def set_pin(self, request):
        target = request.data.get('employee')
        e = me(request)
        if target and (e is None or str(target) != str(e.id)):
            if not hr_for_employee(request, target):
                raise PermissionDenied('You can only set your own kiosk PIN.')
            e = _emp(target)
        if e is None:
            raise PermissionDenied('Your login is not linked to an employee.')
        try:
            S.set_pin(e.id, request.data.get('pin'))
        except S.Refused as exc:
            return refused(exc)
        return Response({'detail': 'Kiosk PIN saved.'})

    @action(detail=True, methods=['post'])
    def void(self, request, pk=None):
        p = self.get_object()
        if not hr_for_employee(request, p.employee_id):
            raise PermissionDenied('Only HR can remove punches.')
        p.is_void = True
        p.void_reason = (request.data.get('reason') or 'Removed by HR')[:255]
        p.save(update_fields=['is_void', 'void_reason'])
        E.recompute_day(p.employee_id, p.work_date)
        return Response(SER.PunchSerializer(p).data)

    @action(detail=False, methods=['post'], url_path='import')
    def import_file(self, request):
        """Bulk import punches from CSV / Excel (columns employee_code, date, time, type).
        ?preview=1 (default) only checks the file; commit=1 saves the valid rows."""
        if not is_hr(request):
            raise PermissionDenied('Only HR can import punches.')
        f = request.FILES.get('file')
        if not f:
            raise ValidationError({'file': 'Choose a CSV or Excel file with columns employee_code, date, time, type.'})
        try:
            rows = S.read_rows(f)
        except Exception:
            raise ValidationError({'file': 'The file could not be read. Save it as CSV (UTF-8) or .xlsx.'})
        commit = str(request.data.get('commit') or request.query_params.get('commit') or '').lower() in ('1', 'true', 'yes')
        res = S.import_punches(rows, commit=commit, user_id=request.user.id, allowed_ids=hr_employee_ids(request))
        tot = {k: sum(1 for r in res if r['status'] == k) for k in ('ok', 'duplicate', 'error', 'imported')}
        return Response({'committed': commit, 'totals': tot, 'rows': res})


# ----------------------------------------------------------------------------- daily results
class DayViewSet(APViewSet):
    queryset = M.AttendanceDay.objects.all()
    serializer_class = SER.DaySerializer
    filter_params = {'employee': 'employee_id', 'date': 'date', 'from': 'date__gte', 'to': 'date__lte', 'status': 'status__in',
                     'branch': 'branch_id', 'late': 'is_late', 'early': 'is_early', 'missing': 'missing_punch', 'night': 'is_night_shift'}
    employee_field = 'employee_id'
    http_method_names = ['get', 'post', 'head', 'options']
    self_service_actions = {'my_month'}

    def create(self, request, *args, **kwargs):
        raise PermissionDenied('Daily results are calculated from punches. Use Recalculate or add a punch / correction.')

    @action(detail=False, methods=['get'])
    def my_month(self, request):
        """ESS: one month with a row for every day (status, in / out, late, early, OT, break) and totals."""
        target = request.query_params.get('employee')
        e = me(request)
        if target and (e is None or str(target) != str(e.id)):
            if not can_see_employee(request, target):
                raise PermissionDenied('You can only see your own attendance.')
            e = _emp(target)
        if e is None:
            raise PermissionDenied('Your login is not linked to an employee.')
        t = E.today()
        y, mth = int(request.query_params.get('year') or t.year), int(request.query_params.get('month') or t.month)
        start = t.replace(year=y, month=mth, day=1)
        end = (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        have = {d.date: d for d in M.AttendanceDay.objects.filter(employee_id=e.id, date__range=(start, end))}
        last = min(end, t)
        d = start
        while d <= last:   # fill days not calculated yet
            if d not in have:
                have[d] = E.recompute_day(e, d, sync_calendar=False)
            d += timedelta(days=1)
        days = [SER.DaySerializer(have[k], context={}).data for k in sorted(have)]
        s = E.day_summary(e, start, end)
        ot = sum(x.ot_minutes + x.period_ot_minutes for x in have.values())
        return Response({'employee_id': e.id, 'employee_code': e.emp_code, 'year': y, 'month': mth, 'days': days,
                         'totals': dict(s, ot_minutes=ot)})

    @action(detail=False, methods=['get'])
    def board(self, request):
        """Daily board for HR / managers: who is in, out, late, absent, on leave or has a missing punch."""
        from EmpManagement.models import emp_master
        day = E.as_date(request.query_params.get('date') or E.today())
        ids = visible_employee_ids(request)
        if ids is not None and not is_hr(request) and not team_ids(request):
            raise PermissionDenied('The daily board is for HR and managers.')
        emps = emp_master.objects.filter(is_active=True)
        if ids is not None:
            emps = emps.filter(id__in=ids)
        b = request.query_params.get('branch')
        if b:
            emps = emps.filter(emp_branch_id=b)
        have = {d.employee_id: d for d in M.AttendanceDay.objects.filter(date=day, employee_id__in=emps.values('id'))}
        rows, counts = [], {}
        for e in emps.order_by('emp_code'):
            d = have.get(e.id) or E.recompute_day(e, day, sync_calendar=False)
            data = SER.DaySerializer(d, context={}).data
            state = d.status
            if state in ('present', 'half_day') and d.first_in and not d.last_out:
                state = 'in'
            elif state in ('present', 'half_day') and d.last_out:
                state = 'out'
            data['board_state'] = state
            counts[state] = counts.get(state, 0) + 1
            if d.is_late:
                counts['late'] = counts.get('late', 0) + 1
            rows.append(data)
        st_filter = request.query_params.get('state')
        if st_filter:
            rows = [r for r in rows if r['board_state'] == st_filter or (st_filter == 'late' and r['is_late'])]
        return Response({'date': day, 'counts': counts, 'rows': rows})

    @action(detail=False, methods=['get'])
    def missing(self, request):
        E.refresh_missing(visible_employee_ids(request))
        qs = self.get_queryset().filter(Q(missing_punch=True) | Q(status='missing_punch'))
        return Response(SER.DaySerializer(qs[:500], many=True).data)

    @action(detail=False, methods=['post'])
    def recompute(self, request):
        """HR: recalculate one employee (or all visible) for a date range (max 62 days)."""
        from EmpManagement.models import emp_master
        if not is_hr(request):
            raise PermissionDenied('Only HR can recalculate attendance.')
        a = E.as_date(request.data.get('from') or request.data.get('date') or E.today())
        b = E.as_date(request.data.get('to') or a)
        if b < a or (b - a).days > 62:
            raise ValidationError({'to': 'Pick a range of up to 62 days.'})
        if b > E.today():       # v1.12.0: days after today are calculated when they come (no empty future rows)
            b = E.today()
        emp_id = request.data.get('employee')
        ids = hr_employee_ids(request)
        if emp_id:
            if ids is not None and int(emp_id) not in ids:
                raise PermissionDenied('This employee is not in your branches.')
            emps = emp_master.objects.filter(pk=emp_id)
        else:
            emps = emp_master.objects.filter(is_active=True)
            if ids is not None:
                emps = emps.filter(id__in=ids)
        n = 0
        for e in emps:
            d = a
            while d <= b:
                E.recompute_day(e, d)
                d += timedelta(days=1)
                n += 1
        return Response({'detail': f'Recalculated {n} employee-days.', 'count': n})


# ----------------------------------------------------------------------------- corrections
class CorrectionViewSet(APViewSet):
    queryset = M.CorrectionRequest.objects.all()
    serializer_class = SER.CorrectionSerializer
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    filter_params = {'employee': 'employee_id', 'status': 'status__in', 'from': 'date__gte', 'to': 'date__lte', 'kind': 'kind',
                     'branch': 'branch_id'}
    employee_field = 'employee_id'
    self_service_actions = {'create', 'manager_approve', 'manager_reject', 'hr_approve', 'hr_reject', 'cancel'}
    http_method_names = ['get', 'post', 'head', 'options']

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx['team'] = team_ids(self.request) if not c(self.request).admin else None
        if c(self.request).admin:
            ctx['team'] = set(self.get_queryset().values_list('employee_id', flat=True))
        ctx['hr_ids'] = hr_employee_ids(self.request) if is_hr(self.request) else 'none'
        return ctx

    def get_queryset(self):
        qs = super().get_queryset()
        stage = self.request.query_params.get('stage')
        if stage == 'manager':
            x = c(self.request)
            qs = qs.filter(status='manager')
            if not x.admin:
                qs = qs.filter(employee_id__in=team_ids(self.request))
        elif stage == 'hr':
            qs = qs.filter(status='hr')
            ids = hr_employee_ids(self.request) if is_hr(self.request) else set()
            if ids is not None:
                qs = qs.filter(employee_id__in=ids)
        elif self.request.query_params.get('mine'):
            e = me(self.request)
            qs = qs.filter(employee_id=e.id if e else -1)
        return qs

    def create(self, request, *args, **kwargs):
        e = me(request)
        target = request.data.get('employee_id') or request.data.get('employee')
        by_hr = False
        if target and (e is None or str(target) != str(e.id)):
            if not hr_for_employee(request, target):
                raise PermissionDenied('You can only request corrections for yourself.')
            e, by_hr = _emp(target), True
        if e is None:
            raise PermissionDenied('Your login is not linked to an employee.')
        try:
            cr = S.new_correction(e, request.data, request.user, by_hr=by_hr, attachment=request.FILES.get('attachment'))
        except S.Refused as exc:
            return refused(exc)
        return Response(self.get_serializer(cr).data, status=201)

    def _obj(self, pk):
        return get_object_or_404(M.CorrectionRequest, pk=pk)

    def _decide(self, request, pk, stage, approve):
        cr = self._obj(pk)
        from EmpManagement.models import emp_master
        e = emp_master.objects.get(pk=cr.employee_id)
        x = c(request)
        if stage == 'manager':
            if not (x.admin or e.emp_reporting_manager_id == request.user.id):
                raise PermissionDenied('Only the employee\'s reporting manager can approve at this stage.')
            if x.emp and x.emp.id == e.id and not x.admin:
                raise PermissionDenied('You cannot approve your own correction.')
            fn = S.manager_decide
        else:
            if not hr_for_employee(request, e.id):
                raise PermissionDenied('Only HR of the employee\'s branch can give the final approval.')
            if x.emp and x.emp.id == e.id and not x.admin:
                raise PermissionDenied('You cannot approve your own correction.')
            fn = S.hr_decide
        note = request.data.get('note') or request.data.get('reason') or ''
        if not approve and len(str(note).strip()) < 3:
            raise ValidationError({'note': 'Write why the correction is rejected.'})
        try:
            cr = fn(cr, request.user, approve, note)
        except S.Refused as exc:
            return refused(exc)
        return Response(self.get_serializer(cr).data)

    @action(detail=True, methods=['post'])
    def manager_approve(self, request, pk=None):
        return self._decide(request, pk, 'manager', True)

    @action(detail=True, methods=['post'])
    def manager_reject(self, request, pk=None):
        return self._decide(request, pk, 'manager', False)

    @action(detail=True, methods=['post'])
    def hr_approve(self, request, pk=None):
        return self._decide(request, pk, 'hr', True)

    @action(detail=True, methods=['post'])
    def hr_reject(self, request, pk=None):
        return self._decide(request, pk, 'hr', False)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        cr = self._obj(pk)
        e = me(request)
        if not (e and e.id == cr.employee_id) and not hr_for_employee(request, cr.employee_id):
            raise PermissionDenied('You can only cancel your own requests.')
        if cr.status not in ('manager', 'hr'):
            return Response({'detail': 'Only requests waiting for approval can be cancelled.'}, status=400)
        cr.status = 'cancelled'
        cr.save(update_fields=['status', 'updated_at'])
        return Response(self.get_serializer(cr).data)


# ----------------------------------------------------------------------------- kiosk & devices (token auth)
class TokenView(APIView):
    zeo_access = False
    zeo_scope = False
    permission_classes = [AllowAny]
    authentication_classes = []
    parser_classes = [JSONParser, MultiPartParser, FormParser]


def _kiosk(request):
    tok = request.META.get('HTTP_X_KIOSK_TOKEN') or request.data.get('kiosk_token') if hasattr(request, 'data') else None
    tok = tok or request.query_params.get('token')
    k = M.Kiosk.objects.filter(token=tok, is_active=True).first() if tok else None
    if not k:
        raise S.Refused('This kiosk is not registered or was switched off. Ask HR for the kiosk link.', 401)
    M.Kiosk.objects.filter(pk=k.pk).update(last_seen=E.now())
    return k


class KioskInfoView(TokenView):
    def get(self, request):
        try:
            k = _kiosk(request)
        except S.Refused as exc:
            return refused(exc)
        out = {'name': k.name, 'allow_qr': k.allow_qr, 'allow_pin': k.allow_pin, 'allow_face': k.allow_face,
               'show_site_qr': k.show_site_qr, 'time': E.local(E.now()).strftime('%Y-%m-%d %H:%M:%S')}
        if k.show_site_qr:
            out['site_qr'], out['expires_in'] = S.site_qr(k)
        return Response(out)


class KioskPunchView(TokenView):
    def post(self, request):
        try:
            k = _kiosk(request)
            qr = request.data.get('qr') or request.data.get('barcode')
            if qr:
                if not k.allow_qr:
                    raise S.Refused('This kiosk does not accept badges / QR codes.', 403)
                e, how = S.employee_from_qr(qr), 'qr'
            elif request.data.get('employee_code') and request.data.get('pin'):
                if not k.allow_pin:
                    raise S.Refused('This kiosk does not accept PIN punches.', 403)
                e, how = S.employee_from_pin(request.data.get('employee_code'), request.data.get('pin')), 'pin'
            elif request.data.get('face_photo') and request.data.get('employee_code'):
                if not k.allow_face:
                    raise S.Refused('This kiosk does not use face recognition.', 403)
                from EmpManagement.models import emp_master
                from calendars import face_utils
                e = emp_master.objects.filter(emp_code__iexact=request.data.get('employee_code'), is_active=True).first()
                enc = face_utils.get_face_encoding(face_utils.convert_base64_to_file(request.data.get('face_photo'), 'face')) if e else None
                if not e or not enc or not face_utils.verify_face(e.face_encoding, enc):
                    raise S.Refused('Face not recognised. Try again or use your badge.', 403)
                how = 'face'
            else:
                raise S.Refused('Scan your badge / QR code, or enter your employee code and PIN.')
            if k.branch_id and e.emp_branch_id_id and k.branch_id != e.emp_branch_id_id:
                pass   # employees may punch at another branch's kiosk; the punch records the kiosk
            rule = E.rule_for(e)
            S.check_punch(e, rule, 'kiosk')
            kind = request.data.get('kind') or 'auto'
            if kind not in dict(M.PUNCH_KINDS):
                raise S.Refused('Unknown punch type.')
            p, created = E.add_punch(e, None, kind, 'kiosk', kiosk=k, device_ref=k.name, ip=S.client_ip(request), verified_by=how)
        except (S.Refused, E.PunchError) as exc:
            return refused(exc)
        name = ' '.join(x for x in [e.emp_first_name, e.emp_last_name] if x)
        return Response({'detail': f"{name}: {dict(M.PUNCH_KINDS).get(p.resolved_kind or p.kind)} at {E.local(p.ts):%H:%M}.",
                         'employee': name, 'kind': p.resolved_kind or p.kind, 'time': E.local(p.ts).strftime('%H:%M')},
                        status=201 if created else 200)


class DevicePushView(TokenView):
    """Generic JSON push: header X-Device-Key (or api_key), body {"punches": [{"pin"|"employee_code", "time", "type"}]}."""

    def post(self, request):
        key = request.META.get('HTTP_X_DEVICE_KEY') or request.data.get('api_key')
        d = M.Device.objects.filter(api_key=key, is_active=True).first() if key else None
        if not d:
            return Response({'detail': 'Unknown or inactive device key.'}, status=401)
        rows = []
        for r in request.data.get('punches') or []:
            rows.append((r.get('pin') or r.get('employee_code') or '', r.get('time') or r.get('timestamp') or '', r.get('type') or r.get('status') or ''))
        if not rows:
            return Response({'detail': 'Send a list "punches" with pin, time (YYYY-MM-DD HH:MM:SS) and type.'}, status=400)
        return Response(S.device_punches(d, rows, S.client_ip(request)))


# ---- ZKTeco ADMS (iclock) ------------------------------------------------------------
def _plain(text, code=200):
    return HttpResponse(text, content_type='text/plain', status=code)


class ADMSView(TokenView):
    parser_classes = []

    def _device(self, request):
        sn = request.query_params.get('SN') or request.query_params.get('sn')
        d = M.Device.objects.filter(serial_number=sn, is_active=True).first() if sn else None
        if d:
            S.device_seen(d, S.client_ip(request))
        return d


class ADMSCData(ADMSView):
    def get(self, request):
        d = self._device(request)
        if not d:
            return _plain('Device not registered', 401)
        stamp = d.last_stamp or 'None'
        return _plain('\n'.join([
            f'GET OPTION FROM: {d.serial_number}', f'ATTLOGStamp={stamp}', 'OPERLOGStamp=9999', 'ATTPHOTOStamp=None',
            'ErrorDelay=30', 'Delay=10', 'TransTimes=00:00;14:05', 'TransInterval=1', 'TransFlag=TransData AttLog',
            'TimeZone=4', 'Realtime=1', 'Encrypt=None', '']))

    def post(self, request):
        d = self._device(request)
        if not d:
            return _plain('Device not registered', 401)
        table = (request.query_params.get('table') or '').upper()
        body = request.body.decode('utf-8', errors='replace')
        if table != 'ATTLOG':
            return _plain('OK')
        res = S.device_punches(d, S.parse_attlog(body), S.client_ip(request))
        stamp = request.query_params.get('Stamp')
        if stamp:
            M.Device.objects.filter(pk=d.pk).update(last_stamp=stamp[:32])
        return _plain(f'OK: {res["received"]}')


class ADMSGetRequest(ADMSView):
    def get(self, request):
        d = self._device(request)
        return _plain('OK' if d else 'Device not registered', 200 if d else 401)


class ADMSDeviceCmd(ADMSView):
    def post(self, request):
        return _plain('OK')
