from rest_framework import serializers

from . import models as M
from . import services as S
from . import engine as E


def _emp_map(ctx, ids):
    cache = ctx.setdefault('_emps', {})
    missing = [i for i in ids if i and i not in cache]
    if missing:
        from EmpManagement.models import emp_master
        for e in emp_master.objects.filter(id__in=missing).values('id', 'emp_code', 'emp_first_name', 'emp_last_name', 'emp_branch_id'):
            cache[e['id']] = e
    return cache


def _branch_name(ctx, bid):
    cache = ctx.setdefault('_branches', None)
    if cache is None:
        from OrganisationManager.models import brnch_mstr
        cache = ctx['_branches'] = {b.id: b.branch_name for b in brnch_mstr.objects.all()}
    return cache.get(bid, '') if bid else 'All branches'


class EmpMixin:
    def to_representation(self, obj):
        d = super().to_representation(obj)
        eid = getattr(obj, 'employee_id', None)
        if eid is not None:
            e = _emp_map(self.context, [eid]).get(eid)
            d['employee_code'] = e['emp_code'] if e else ''
            d['employee_name'] = ' '.join(x for x in [e and e['emp_first_name'], e and e['emp_last_name']] if x) if e else ''
            d['employee_display'] = f"{d['employee_name']} ({d['employee_code']})" if e else str(eid)
        if hasattr(obj, 'branch_id'):
            d['branch_display'] = _branch_name(self.context, obj.branch_id)
        return d


class RuleSerializer(EmpMixin, serializers.ModelSerializer):
    class Meta:
        model = M.AttendanceRule
        fields = '__all__'

    def validate(self, data):
        g = lambda k: data.get(k, getattr(self.instance, k, None) if self.instance else None)  # noqa: E731
        scope = g('scope') or 'company'
        need = {'branch': 'branch_id', 'department': 'department_id', 'category': 'category_id', 'employee': 'employee_id'}.get(scope)
        if need and not g(need):
            raise serializers.ValidationError({need: f'Pick the {scope} this rule applies to.'})
        full, half = g('min_hours_full_day') or 0, g('min_hours_half_day') or 0
        if full and half and half > full:
            raise serializers.ValidationError({'min_hours_half_day': 'The half-day minimum must be lower than the full-day minimum.'})
        mx = g('max_hours') or 0
        if mx and full and mx < full:
            raise serializers.ValidationError({'max_hours': 'Maximum hours must be more than the full-day minimum.'})
        bad = [m for m in (g('allowed_methods') or []) if m not in M.METHOD_KEYS]
        if bad:
            raise serializers.ValidationError({'allowed_methods': f'Unknown method(s): {", ".join(bad)}.'})
        if (g('rounding_mode') or 'none') != 'none' and not g('rounding_minutes'):
            raise serializers.ValidationError({'rounding_minutes': 'Give the rounding step in minutes (e.g. 5 or 15).'})
        dc = g('day_change_hour')
        if dc is not None and dc > 12:
            raise serializers.ValidationError({'day_change_hour': 'Use an hour between 0 and 12.'})
        for k in ('exempt_category_ids', 'exempt_employee_ids'):
            v = g(k)
            if v and (not isinstance(v, list) or any(not str(x).isdigit() for x in v)):
                raise serializers.ValidationError({k: 'Use a list of ids.'})
        return data

    def to_representation(self, obj):
        d = super().to_representation(obj)
        d['scope_label'] = obj.get_scope_display()
        return d


class IPSerializer(EmpMixin, serializers.ModelSerializer):
    class Meta:
        model = M.IPRestriction
        fields = '__all__'

    def validate_cidrs(self, v):
        nets, bad = S.parse_cidrs(v)
        if bad:
            raise serializers.ValidationError(f'Not a valid IP address or network: {", ".join(bad)}. Use e.g. 10.0.0.0/24 or 94.200.1.5.')
        if not nets:
            raise serializers.ValidationError('Add at least one IP address or network.')
        return v


class DeviceSerializer(EmpMixin, serializers.ModelSerializer):
    class Meta:
        model = M.Device
        fields = '__all__'
        read_only_fields = ('api_key', 'last_seen', 'last_ip', 'last_stamp', 'punches_received')

    def validate_serial_number(self, v):
        v = (v or '').strip()
        if not v.replace('-', '').isalnum():
            raise serializers.ValidationError('Use the serial number printed on the device (letters and digits).')
        return v


class KioskSerializer(EmpMixin, serializers.ModelSerializer):
    class Meta:
        model = M.Kiosk
        fields = '__all__'
        read_only_fields = ('token', 'last_seen')


class PunchSerializer(EmpMixin, serializers.ModelSerializer):
    class Meta:
        model = M.Punch
        fields = '__all__'
        read_only_fields = [f.name for f in M.Punch._meta.fields if f.name not in ('employee_id', 'ts', 'kind', 'note', 'lat', 'lng', 'location')]

    def to_representation(self, obj):
        d = super().to_representation(obj)
        d['kind_label'] = dict(M.PUNCH_KINDS).get(obj.resolved_kind or obj.kind, obj.kind)
        d['source_label'] = obj.get_source_display()
        d['local_time'] = E.local(obj.ts).strftime('%Y-%m-%d %H:%M:%S') if obj.ts else None
        d['device_display'] = str(obj.device) if obj.device_id else (str(obj.kiosk) if obj.kiosk_id else obj.device_ref)
        return d


def hm(m):
    m = int(m or 0)
    return f'{m // 60}:{m % 60:02d}'


class DaySerializer(EmpMixin, serializers.ModelSerializer):
    class Meta:
        model = M.AttendanceDay
        fields = '__all__'

    def to_representation(self, obj):
        d = super().to_representation(obj)
        d['status_label'] = obj.get_status_display()
        for k in ('first_in', 'last_out', 'shift_start', 'shift_end'):
            v = getattr(obj, k)
            d[k + '_local'] = E.local(v).strftime('%H:%M') if v else ''
        d['worked'] = hm(obj.worked_minutes)
        d['ot_total_minutes'] = obj.ot_minutes + obj.period_ot_minutes
        d['ot'] = hm(obj.ot_minutes + obj.period_ot_minutes)
        d['late'] = hm(obj.late_minutes) if obj.late_minutes else ''
        d['early'] = hm(obj.early_minutes) if obj.early_minutes else ''
        d['break'] = hm(obj.break_minutes) if obj.break_minutes else ''
        return d


class CorrectionSerializer(EmpMixin, serializers.ModelSerializer):
    class Meta:
        model = M.CorrectionRequest
        fields = '__all__'
        read_only_fields = [f.name for f in M.CorrectionRequest._meta.fields if f.name not in ('reason', 'attachment')]

    def to_representation(self, obj):
        d = super().to_representation(obj)
        d['kind_label'] = obj.get_kind_display()
        d['status_label'] = obj.get_status_display()
        d['proposed_in_local'] = E.local(obj.proposed_in).strftime('%Y-%m-%d %H:%M') if obj.proposed_in else ''
        d['proposed_out_local'] = E.local(obj.proposed_out).strftime('%Y-%m-%d %H:%M') if obj.proposed_out else ''
        req = self.context.get('request')
        d['can_manager_act'] = bool(req and obj.status == 'manager' and self.context.get('team') is not None and obj.employee_id in self.context['team'])
        d['can_hr_act'] = bool(req and obj.status == 'hr' and self.context.get('hr_ids', 'none') != 'none'
                               and (self.context['hr_ids'] is None or obj.employee_id in self.context['hr_ids']))
        return d
