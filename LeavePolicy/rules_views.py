"""v1.65.0 (items 82–97) – Leave › Setup › Leave rules.

GET  api/leave-rules/                         company settings + one row per leave type (its rules) + options
PUT  api/leave-rules/ {settings: {...}}       change the company settings (HR with leave setup rights)
POST api/leave-rules/ {leave_type_id, ...}    change the rules of one leave type
GET  api/leave-rules/types/                   what the request screens need per leave type (birth date, window) – any signed-in user
GET  api/leave-rules/eligibility/?leave_type= employees confirmed by HR for a type (item 91)
POST api/leave-rules/eligibility/ {employee_id, leave_type_id, confirmed, taken_before, note}
"""
from django.apps import apps
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from Translations.i18n import tr

from .models import LeaveEligibility, LeaveRuleSettings, LeaveTypeRule
from .views import READ, WRITE, _ctx, person

M = apps.get_model
SETTING_FIELDS = ('notice_leave', 'accrual_reduction', 'exit_under_6m', 'advance_when', 'advance_basis', 'catch_up', 'catch_up_max_months',
                  'nursing_enabled', 'nursing_minutes', 'nursing_months', 'extension_skips_notice')
RULE_FIELDS = ('birth_required', 'birth_window_months', 'birth_window_from', 'one_per_birth', 'sandwich', 'allow_projected', 'advance_salary',
               'document_above_days', 'hr_eligibility', 'min_service_years', 'nursing_breaks', 'notice_exempt',
               'negative_limit')   # v1.67.12 (effective, item 62)
BOOL = {'catch_up', 'nursing_enabled', 'extension_skips_notice', 'birth_required', 'one_per_birth', 'sandwich', 'allow_projected',
        'advance_salary', 'hr_eligibility', 'nursing_breaks', 'notice_exempt'}
# figures not verified in two sources: shown "to be confirmed" on the screen
TO_CONFIRM = {'accrual_reduction': 'Art. 33(2) / 51(4): unpaid leave and unpaid absence are not counted in the period of service – how this reduces annual leave days is to be confirmed.',
              'exit_under_6m': 'The law gives 2 days a month after 6 months and leave pay for the fractions of the last year; less than 6 months is not stated – to be confirmed.',
              'min_service_years': 'Hajj: up to 30 days unpaid, once with the same employer (advisers); a minimum service is not in the law found – to be confirmed.'}


def _b(v):
    return str(v).lower() in ('1', 'true', 'yes', 'on')


def _settings_row(s):
    return {k: getattr(s, k) for k in SETTING_FIELDS}


def _rule_row(lt, r):
    out = {'leave_type_id': lt.pk, 'name': lt.name, 'code': lt.code, 'category': lt.leave_category, 'type': lt.type, 'unit': lt.unit,
           'branch': str(lt.branch) if lt.branch_id else '', 'has_rule': r is not None}
    blank = LeaveTypeRule()
    for k in RULE_FIELDS:
        out[k] = getattr(r if r is not None else blank, k)
    out['nursing_default'] = lt.leave_category == 'maternity' and lt.type == 'paid'
    on = [k for k in ('birth_required', 'sandwich', 'allow_projected', 'advance_salary', 'hr_eligibility', 'nursing_breaks', 'notice_exempt')
          if out[k]]
    if out['document_above_days'] is not None:
        on.append('document_above_days')
    if out['min_service_years']:
        on.append('min_service_years')
    if out.get('negative_limit') is not None:   # v1.67.12 (effective, item 62)
        on.append('negative_limit')
    out['rules_on'] = on
    out['negative_allowed'] = bool(lt.negative)   # v1.67.12 (effective): the type allows a negative balance (Leave types)
    words = {'birth_required': tr('Birth date · {0} months', out['birth_window_months']), 'sandwich': tr('Sandwich rule'),
             'allow_projected': tr('Projected balance'), 'advance_salary': tr('Leave salary in advance'), 'hr_eligibility': tr('HR confirms eligibility'),
             'nursing_breaks': tr('Nursing breaks'), 'notice_exempt': tr('Allowed in the notice period'),
             'document_above_days': tr('Document above {0} days', f"{out['document_above_days']:g}" if out['document_above_days'] is not None else ''),
             'min_service_years': tr('{0} years of service', f"{out['min_service_years']:g}" if out['min_service_years'] else ''),
             'negative_limit': tr('At most {0} days below zero', f"{out['negative_limit']:g}" if out.get('negative_limit') is not None else '')}
    out['rules_text'] = [words[k] for k in on]
    out['category_label'] = tr(lt.get_leave_category_display())
    out['nursing_text'] = tr('Nursing breaks (law)')
    return out


class LeaveRulesView(APIView):
    permission_classes = [IsAuthenticated]

    def _can(self, request, codes):
        c = _ctx(request)
        return c.admin or bool(c.codes & codes)

    def get(self, request, pk=None, part=None):
        if not self._can(request, READ):
            return Response({'detail': tr('You may not see the leave rules.')}, status=403)
        s = LeaveRuleSettings.objects.order_by('id').first() or LeaveRuleSettings()
        LT = M('calendars', 'leave_type')
        rules = {r.leave_type_id: r for r in LeaveTypeRule.objects.all()}
        from .leave_rules import rule_for
        rows = []
        for lt in LT.objects.select_related('branch').order_by('branch_id', 'name'):
            r = rules.get(lt.pk) or rule_for(lt)
            rows.append(dict(_rule_row(lt, r), inherited=bool(r is not None and r.leave_type_id != lt.pk)))
        choices = {f: [{'value': v, 'label': tr(l)} for v, l in LeaveRuleSettings._meta.get_field(f).choices]
                   for f in ('notice_leave', 'accrual_reduction', 'exit_under_6m', 'advance_when', 'advance_basis')}
        choices['birth_window_from'] = [{'value': v, 'label': tr(l)} for v, l in LeaveTypeRule._meta.get_field('birth_window_from').choices]
        return Response({'settings': _settings_row(s), 'types': rows, 'choices': choices, 'can_edit': self._can(request, WRITE),
                         'to_confirm': {k: tr(v) for k, v in TO_CONFIRM.items()}})

    def put(self, request, pk=None, part=None):
        if not self._can(request, WRITE):
            return Response({'detail': tr('Only HR changes the leave settings.')}, status=403)
        s = LeaveRuleSettings.get()
        d = request.data.get('settings') if isinstance(request.data.get('settings'), dict) else request.data
        try:
            for k in SETTING_FIELDS:
                if k not in d:
                    continue
                v = d[k]
                if k in BOOL:
                    v = _b(v)
                elif k in ('catch_up_max_months', 'nursing_minutes', 'nursing_months'):
                    v = int(v)
                    lim = {'catch_up_max_months': (1, 24), 'nursing_minutes': (0, 120), 'nursing_months': (0, 24)}[k]
                    if not lim[0] <= v <= lim[1]:
                        return Response({k: [tr('Between {0} and {1}.', lim[0], lim[1])]}, status=400)
                else:
                    ok = {c[0] for c in LeaveRuleSettings._meta.get_field(k).choices}
                    if v not in ok:
                        return Response({k: [tr('Choose one of the options.')]}, status=400)
                setattr(s, k, v)
        except (TypeError, ValueError):
            return Response({'detail': tr('Give whole numbers.')}, status=400)
        s.save()
        return self.get(request)

    def patch(self, request, pk=None, part=None):
        """The shared form: PATCH settings/ = the company settings, PATCH <leave type id>/ = the rules of that type."""
        if part == 'settings':
            return self.put(request)
        return self.post(request, pk=pk)

    def post(self, request, pk=None, part=None):
        if not self._can(request, WRITE):
            return Response({'detail': tr('Only HR changes the leave settings.')}, status=403)
        d = request.data
        LT = M('calendars', 'leave_type')
        lt = LT.objects.filter(pk=pk or d.get('leave_type_id') or 0).first()
        if lt is None:
            return Response({'detail': tr('Choose the leave type.')}, status=400)
        r = LeaveTypeRule.objects.filter(leave_type_id=lt.pk).first() or LeaveTypeRule(leave_type_id=lt.pk)
        try:
            for k in RULE_FIELDS:
                if k not in d:
                    continue
                v = d[k]
                if k in BOOL:
                    v = _b(v)
                elif k in ('document_above_days', 'min_service_years', 'negative_limit'):   # v1.67.12 (effective): negative_limit
                    v = None if v in ('', None) else float(v)
                    if v is not None and v < 0:
                        return Response({k: [tr('Cannot be negative.')]}, status=400)
                elif k == 'birth_window_months':
                    v = int(v)
                    if not 1 <= v <= 24:
                        return Response({k: [tr('Between {0} and {1}.', 1, 24)]}, status=400)
                elif k == 'birth_window_from':
                    if v not in ('birth', 'before'):
                        return Response({k: [tr('Choose one of the options.')]}, status=400)
                setattr(r, k, v)
        except (TypeError, ValueError):
            return Response({'detail': tr('Give numbers.')}, status=400)
        if r.advance_salary and (lt.type == 'unpaid' or lt.unit == 'hours'):
            return Response({'advance_salary': [tr('Leave salary in advance is for paid leave counted in days.')]}, status=400)
        if r.birth_required and lt.unit == 'hours':
            return Response({'birth_required': [tr('The birth date rule is for leave counted in days.')]}, status=400)
        r.save()
        return Response(_rule_row(lt, r))


class TypeRulesView(APIView):
    """What the request screens need: {leave type id: {birth_required, birth_window_months, birth_window_from, extension}}."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from .leave_rules import rule_for
        LT = M('calendars', 'leave_type')
        out = {}
        for lt in LT.objects.all():
            r = rule_for(lt)
            out[lt.pk] = {'birth_required': bool(r and r.birth_required), 'birth_window_months': (r.birth_window_months if r else 6),
                          'birth_window_from': (r.birth_window_from if r else 'birth'),
                          'birth_optional': bool(lt.leave_category in ('maternity', 'paternity') and not (r and r.birth_required)),
                          'allow_projected': bool(r and r.allow_projected), 'hr_eligibility': bool(r and r.hr_eligibility),
                          'document_above_days': (r.document_above_days if r else None)}
        return Response(out)


class EligibilityView(APIView):
    """Item 91 – HR confirms an employee may take a leave type (e.g. Hajj leave, once in service). Nothing about religion is stored."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        c = _ctx(request)
        if not (c.admin or c.codes & READ):
            return Response({'detail': tr('You may not see the leave rules.')}, status=403)
        qs = LeaveEligibility.objects.all()
        if request.query_params.get('leave_type'):
            qs = qs.filter(leave_type_id=int(request.query_params['leave_type']))
        E = M('EmpManagement', 'emp_master')
        emps = {e.pk: e for e in E.objects.filter(pk__in=[x.employee_id for x in qs])}
        names = dict(M('calendars', 'leave_type').objects.values_list('id', 'name'))
        from HRActions.names import user_names
        who = user_names({x.confirmed_by_id for x in qs if x.confirmed_by_id})
        return Response([{'id': x.pk, 'employee_id': x.employee_id, 'employee': person(emps.get(x.employee_id)), 'leave_type_id': x.leave_type_id,
                          'leave_type': names.get(x.leave_type_id, ''), 'confirmed': x.confirmed, 'taken_before': x.taken_before, 'note': x.note,
                          'confirmed_by': who.get(x.confirmed_by_id, ''), 'confirmed_at': x.confirmed_at} for x in qs.order_by('-confirmed_at', '-id')])

    def post(self, request):
        c = _ctx(request)
        if not (c.admin or c.codes & WRITE):
            return Response({'detail': tr('Only HR changes the leave settings.')}, status=403)
        d = request.data
        E = M('EmpManagement', 'emp_master')
        e = E.objects.filter(pk=d.get('employee_id') or 0).first()
        lt = M('calendars', 'leave_type').objects.filter(pk=d.get('leave_type_id') or 0).first()
        if e is None or lt is None:
            return Response({'detail': tr('Choose the employee and the leave type.')}, status=400)
        if c.branches is not None and e.emp_branch_id_id not in c.branches:
            return Response({'detail': tr('You may not see this employee.')}, status=403)
        x, _ = LeaveEligibility.objects.get_or_create(employee_id=e.pk, leave_type_id=lt.pk)
        x.confirmed = _b(d.get('confirmed', True))
        x.taken_before = _b(d.get('taken_before', False))
        x.note = str(d.get('note') or '')[:255]
        x.confirmed_by_id, x.confirmed_at = request.user.pk, timezone.now()
        x.save()
        return Response({'id': x.pk, 'detail': tr('Saved.')}, status=201)
