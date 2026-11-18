"""v1.73.8 – Leave › Setup › Leave setup: one page per leave type (basics, entitlement by policy, who & when, special rules),
the setup check of the type, and leave grants.

GET  api/setup/                     the leave types with a one-line summary and the problems found (check my setup)
GET  api/setup/<lt>/                the leave type, its rules, one row per policy (line or none) and the options
PUT  api/setup/<lt>/  {lines: [{policy_id, on, …line fields, more}], policies: [{id, year_basis}]}
                                    saves the policy lines of this type (the basics go to calendars/api/leave-type/<lt>/ and the
                                    special rules to api/leave-rules/<lt>/ – each field has one home)
GET  api/grants/?leave_type=        leave grants and anniversary credits (ledger lines)
POST api/grants/ {employees: [ids], leave_type, days, date, reason}
"""
from zeo.tz import today as _zt_today   # v1.73.16 the user's / company's day, not the server's UTC day
from datetime import date

from django.apps import apps
from django.db import transaction
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from Translations.i18n import tr

from . import engine, more
from .models import LeaveLedger, LeavePolicy, LeavePolicyLine, LeaveTypeRule
from .views import READ, WRITE, LineSerializer, _ctx, person

M = apps.get_model
LINE_FIELDS = ('days_per_year', 'accrual', 'first_year_uae', 'count_days', 'gender', 'min_service_months', 'after_probation', 'notice_days',
               'max_per_request', 'max_per_year', 'max_times_in_service', 'requires_document', 'allow_negative', 'carry_forward_max',
               'carry_forward_expiry_months', 'excess_action', 'encashable', 'encash_max_per_year', 'prorate', 'service_steps', 'pay_slabs',
               'law_reference', 'notes', 'more')


def _can(request, codes):
    c = _ctx(request)
    return c.admin or bool(c.codes & codes)


def problems(lt, lines, rule=None):
    """What looks wrong in the setup of one leave type (texts)."""
    out = []
    from .categories import system_of
    cat = system_of(lt.leave_category)
    if not lines:
        out.append(tr('No leave policy has {0}: nobody gets a balance, limits or rules for it.', lt.name))
    for l in lines:
        pn = l.policy.name
        m = more.of(l)
        if l.accrual in ('monthly', 'yearly') and not l.days_per_year and not m.get('anniversary'):
            out.append(tr('{0}: {1} is earned {2} but the days a year are 0.', pn, lt.name, l.get_accrual_display().split(' (')[0].lower()))
        if cat == 'maternity' and l.gender == 'M' or cat == 'paternity' and l.gender == 'F':
            out.append(tr('{0}: {1} is set for the wrong gender.', pn, lt.name))
        if m.get('exceed') == 'lop' and (lt.type == 'unpaid' or l.accrual in ('per_event', 'none')):
            out.append(tr('{0}: "over the balance → unpaid" needs a paid type with a balance.', pn))
        if m.get('balance_cap') is not None and l.days_per_year and m['balance_cap'] < l.days_per_year and l.accrual == 'yearly':
            out.append(tr('{0}: the balance cap ({1:g}) is below the days a year ({2:g}) given at once.', pn, m['balance_cap'], l.days_per_year))
        if m.get('rh_dates') and not m.get('times'):
            out.append(tr('{0}: restricted holidays without "how often" – every listed day can be taken.', pn))
        if m.get('max_consecutive') and l.max_per_request and m['max_consecutive'] < l.max_per_request:
            out.append(tr('{0}: "in a row" ({1:g}) is below "per request" ({2:g}).', pn, m['max_consecutive'], l.max_per_request))
        if m.get('past_days') is not None and m.get('future_days') == 0 and m['past_days'] == 0:
            out.append(tr('{0}: only today can be asked (past 0 and ahead 0 days).', pn))
        if lt.pk in (m.get('no_combine') or []):
            out.append(tr('{0}: {1} cannot be "not next to" itself – use "at least N days between".', pn, lt.name))
    return out


def copy_of(lt_id, line_type_ids):
    """the leave type in a policy that this branch copy takes its lines from (engine.twin_type: same code, else same name, another
    branch) – None when the type has lines of its own or is no copy"""
    if lt_id in line_type_ids:
        return None
    by_id, _, _ = engine._type_index()
    me = by_id.get(lt_id)
    if not me or me[0] is None:
        return None
    for j in sorted(line_type_ids):
        o = by_id.get(j)
        if o and o[0] is not None and o[0] != me[0] and ((me[1] and o[1] == me[1]) or (me[2] and o[2] == me[2])):
            return j
    return None


def _line_row(l):
    d = LineSerializer(l).data
    d.pop('id', None)
    return d


def _summary(lt, lines):
    if not lines:
        return tr('Not in any policy')
    parts = []
    for l in lines:
        if l.accrual in ('monthly', 'yearly'):
            parts.append(tr('{0}: {1:g} days a year', l.policy.name, l.days_per_year))
        else:
            parts.append(tr('{0}: {1}', l.policy.name, tr(l.get_accrual_display().split(' (')[0])))
    return ' · '.join(parts)


class SetupListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _can(request, READ):
            return Response({'detail': tr('Only HR sees the leave setup.')}, status=403)
        LT = M('calendars', 'leave_type')
        by_type = {}
        for l in LeavePolicyLine.objects.select_related('policy').filter(policy__active=True):
            by_type.setdefault(l.leave_type_id, []).append(l)
        rules = {r.leave_type_id: r for r in LeaveTypeRule.objects.all()}
        out = []
        types = list(LT.objects.select_related('branch').order_by('name'))
        names = {t.pk: (t.name, str(t.branch) if t.branch_id else '') for t in types}
        for lt in types:
            ls = by_type.get(lt.pk, [])
            src = copy_of(lt.pk, set(by_type))
            if src:   # v1.62.1 a branch copy: the policy lines of its type apply to it
                sn, sb = names.get(src, ('', ''))
                out.append({'id': lt.pk, 'name': lt.name, 'code': lt.code, 'category': lt.leave_category, 'type': lt.type, 'unit': lt.unit,
                            'branch': str(lt.branch) if lt.branch_id else '', 'policies': 0, 'copy_of': src,
                            'summary': tr('Branch copy – uses the policy lines of {0}{1}', sn, f' ({sb})' if sb else ''), 'more_rules': [], 'problems': []})
                continue
            out.append({'id': lt.pk, 'name': lt.name, 'code': lt.code, 'category': lt.leave_category, 'type': lt.type, 'unit': lt.unit,
                        'branch': str(lt.branch) if lt.branch_id else '', 'policies': len(ls), 'summary': _summary(lt, ls),
                        'more_rules': sorted({k for l in ls for k in more.of(l)}), 'problems': problems(lt, ls, rules.get(lt.pk))})
        return Response({'types': out, 'problems': sum(len(t['problems']) for t in out)})


class SetupView(APIView):
    permission_classes = [IsAuthenticated]

    def _lt(self, pk):
        return M('calendars', 'leave_type').objects.filter(pk=pk).first()

    def get(self, request, pk):
        if not _can(request, READ):
            return Response({'detail': tr('Only HR sees the leave setup.')}, status=403)
        lt = self._lt(pk)
        if lt is None:
            return Response({'detail': tr('Not found.')}, status=404)
        from .rules_views import _rule_row
        rule = LeaveTypeRule.objects.filter(leave_type_id=lt.pk).first()
        lines = {l.policy_id: l for l in LeavePolicyLine.objects.select_related('policy').filter(leave_type_id=lt.pk)}
        rows = []
        for p in LeavePolicy.objects.all():
            l = lines.get(p.pk)
            row = {'policy_id': p.pk, 'policy': p.name, 'policy_active': p.active, 'year_basis': p.year_basis, 'leave_year_start': p.leave_year_start,
                   'on': l is not None}
            row.update(_line_row(l) if l is not None else {k: v for k, v in _line_row(LeavePolicyLine(policy=p, leave_type_id=lt.pk)).items()})
            row['more'] = more.of(l) if l is not None else {}
            rows.append(row)
        try:
            locs = [{'id': x.pk, 'name': x.name} for x in M('OrgStructure', 'Location').objects.all()]
        except LookupError:
            locs = []
        LT = M('calendars', 'leave_type')
        opts = {'types': [{'id': x.pk, 'name': x.name} for x in LT.objects.exclude(pk=lt.pk).order_by('name')],
                'designations': [{'id': x.pk, 'name': x.desgntn_job_title} for x in M('OrganisationManager', 'desgntn_master').objects.all()],
                'departments': [{'id': x.pk, 'name': x.dept_name} for x in M('OrganisationManager', 'dept_master').objects.all()],
                'locations': locs}
        src = copy_of(lt.pk, set(LeavePolicyLine.objects.filter(policy__active=True).values_list('leave_type_id', flat=True)))
        srct = M('calendars', 'leave_type').objects.filter(pk=src).select_related('branch').first() if src else None
        return Response({'copy_of': {'id': srct.pk, 'name': srct.name, 'branch': str(srct.branch) if srct.branch_id else ''} if srct else None,
                         'type': {'id': lt.pk, 'name': lt.name, 'code': lt.code, 'leave_category': lt.leave_category, 'type': lt.type, 'unit': lt.unit,
                                  'negative': lt.negative, 'description': lt.description, 'allow_half_day': lt.allow_half_day,
                                  'include_weekend': lt.include_weekend, 'include_holiday': lt.include_holiday, 'valid_from': lt.valid_from,
                                  'valid_to': lt.valid_to, 'branch': lt.branch_id},
                         'rule': _rule_row(lt, rule), 'lines': rows, 'options': opts,
                         'problems': [] if (srct and not lines) else problems(lt, [l for l in lines.values()], rule)})

    @transaction.atomic
    def put(self, request, pk):
        if not _can(request, WRITE):
            return Response({'detail': tr('Only HR changes the leave setup.')}, status=403)
        lt = self._lt(pk)
        if lt is None:
            return Response({'detail': tr('Not found.')}, status=404)
        errors = {}
        for p in request.data.get('policies') or []:
            yb = p.get('year_basis')
            if yb in ('month', 'joining'):
                LeavePolicy.objects.filter(pk=p.get('id')).update(year_basis=yb)
        for row in request.data.get('lines') or []:
            p = LeavePolicy.objects.filter(pk=row.get('policy_id') or 0).first()
            if p is None:
                continue
            line = LeavePolicyLine.objects.filter(policy=p, leave_type_id=lt.pk).first()
            if not row.get('on'):
                if line is not None:
                    line.delete()
                continue
            data = {k: row[k] for k in LINE_FIELDS if k in row}
            data['leave_type_id'] = lt.pk
            s = LineSerializer(line, data=data, partial=line is not None)
            if not s.is_valid():
                errors[p.name] = s.errors
                continue
            s.save(policy=p)
        if errors:
            transaction.set_rollback(True)
            def flat(e):
                if isinstance(e, dict):
                    return ' '.join(flat(v) for v in e.values())
                if isinstance(e, (list, tuple)):
                    return ' '.join(flat(v) for v in e)
                return str(e)
            why = '; '.join(f'{k} – {flat(v)}' for k, v in errors.items())
            return Response({'lines': errors, 'detail': tr('Some policies were not saved: {0}', why)}, status=400)
        return self.get(request, pk)


class GrantsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _can(request, READ):
            return Response({'detail': tr('Only HR sees leave grants.')}, status=403)
        c = _ctx(request)
        qs = LeaveLedger.objects.filter(kind__in=('grant', 'anniversary')).order_by('-date', '-pk')
        if request.query_params.get('leave_type'):
            qs = qs.filter(leave_type_id=request.query_params['leave_type'])
        E = M('EmpManagement', 'emp_master')
        rows = list(qs[:500])
        emps = {e.pk: e for e in E.objects.filter(pk__in={r.employee_id for r in rows})}
        if c.branches is not None and not c.admin:
            rows = [r for r in rows if emps.get(r.employee_id) and emps[r.employee_id].emp_branch_id_id in c.branches]
        names = dict(M('calendars', 'leave_type').objects.values_list('id', 'name'))
        return Response([{'id': r.pk, 'date': r.date, 'kind': r.kind, 'kind_label': tr(r.get_kind_display()), 'employee_id': r.employee_id,
                          'employee': person(emps.get(r.employee_id)), 'leave_type_id': r.leave_type_id, 'leave_type': names.get(r.leave_type_id, ''),
                          'days': r.days, 'note': r.note} for r in rows])

    def post(self, request):
        if not _can(request, WRITE | {'add_emp_leave_balance', 'change_emp_leave_balance'}):
            return Response({'detail': tr('Only HR gives leave grants.')}, status=403)
        c = _ctx(request)
        d = request.data
        lt = M('calendars', 'leave_type').objects.filter(pk=d.get('leave_type') or 0).first()
        bad = {}
        if lt is None:
            bad['leave_type'] = [tr('Choose the leave type.')]
        try:
            days = float(d.get('days'))
            if days == 0 or abs(days) > 366:
                raise ValueError
        except (TypeError, ValueError):
            bad['days'] = [tr('Give the days (not 0, at most 366).')]
        try:
            on = date.fromisoformat(str(d.get('date') or _zt_today().isoformat())[:10])
        except ValueError:
            bad['date'] = [tr('Give a date.')]
        if not (d.get('reason') or '').strip():
            bad['reason'] = [tr('Give the reason.')]
        E = M('EmpManagement', 'emp_master')
        ids = [int(x) for x in (d.get('employees') or []) if str(x).isdigit()]
        emps = list(E.objects.filter(pk__in=ids, is_active=True))
        if c.branches is not None and not c.admin:
            emps = [e for e in emps if e.emp_branch_id_id in c.branches]
        if not emps:
            bad['employees'] = [tr('Choose the employees.')]
        if bad:
            return Response(bad, status=400)
        rows = more.grant([e.pk for e in emps], lt.pk, days, on, d['reason'].strip(), user_id=request.user.pk)
        return Response({'granted': len(rows), 'ids': rows, 'detail': tr('{0:g} days of {1} given to {2} employee(s).', days, lt.name, len(rows))}, status=201)
