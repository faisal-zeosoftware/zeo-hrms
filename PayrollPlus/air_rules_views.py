"""v1.66.0 (items 118–123, decision 261) – Air tickets › Setup › Air ticket settings (PayrollPlus.air_policy).

GET  api/air-tickets/rules/                       company settings, policies with their rules, the fare table, overlaps, choices
PUT  api/air-tickets/rules/                       {settings} save the company settings (air ticket setup right)
PUT  api/air-tickets/rules/policy/<id>/           save the rules of one policy
POST api/air-tickets/rules/fares/                 add / change a fare ({id} changes)        DELETE api/air-tickets/rules/fares/<id>/
GET  api/air-tickets/rules/check/?employee_id=&on= what the rules give one employee on a day (policy, value, travellers, accrual)
GET  api/air-tickets/rules/provision/?as_of=      the air ticket provision report (liability, posted, to post)
GET  api/air-tickets/rules/travel/?request_id=    the travellers of a ticket request        POST {request_id, travellers: [...]}
POST api/air-tickets/rules/pay-outside/           {request_id, reference} an approved cash payout paid outside payroll
"""
from datetime import date

from django.apps import apps
from django.db import transaction
from django.utils import timezone
from rest_framework.response import Response

from Translations.i18n import tr

from . import air_policy as AP
from . import services as S
from .air_tickets import AtBase, POLICY_READ, ALLOC_READ, REQ_READ, emp_name

M = apps.get_model
SETUP = {'change_airticketpolicy', 'add_airticketpolicy'}
REQ_EDIT = {'change_airticketrequest'}
PROV_READ = ALLOC_READ | {'view_payrollrun', 'change_payrollrun'}


def _refused(ex):
    raise S.Refused(ex.message, problems=ex.problems)


def _pol_json(p):
    return {'id': p.pk, 'name': p.name, 'country': p.country.country_name if p.country_id else '', 'country_id': p.country_id,
            'amount': str(AP.q2(p.amount)), 'allowance_type': p.allowance_type, 'travel_class': p.travel_class,
            'frequency_years': p.frequency_years, 'allowed_in_probation': bool(p.allowed_in_probation), 'is_active': bool(p.is_active),
            'limits': AP.specificity(p),
            'cash_rules': [{'years': r.required_service_years, 'next_payroll': bool(r.apply_in_next_payroll)} for r in p.rules.all()
                           if r.rule_type == 'ENCASHMENT'],
            'rules': AP.policy_rules(p.pk)}


class RulesView(AtBase):
    def get(self, request):
        self.need(POLICY_READ | ALLOC_READ | SETUP)
        P = M('PayrollManagement', 'AirTicketPolicy')
        C = M('Core', 'cntry_mstr')
        pols = list(P.objects.select_related('country').prefetch_related('rules', 'eligible_departments', 'eligible_designations',
                                                                          'eligible_categories', 'branch').order_by('-is_active', 'name'))
        cids = {p.country_id for p in pols}
        countries = [{'id': c.pk, 'name': c.country_name} for c in C.objects.order_by('country_name')]
        names = {c['id']: c['name'] for c in countries}
        fares = [{**f, 'country': names.get(f.get('country_id'), '')} for f in AP.fares()]
        return Response({'settings': AP.settings(), 'defaults': AP.DEFAULTS, 'policy_defaults': AP.POLICY_DEFAULTS,
                         'policies': [_pol_json(p) for p in pols], 'fares': fares, 'overlaps': AP.overlap_list(),
                         'countries': countries, 'policy_countries': sorted(cids - {None}),
                         'can_edit': self.can(SETUP), 'can_requests': self.can(REQ_EDIT)})

    def put(self, request):
        self.need(SETUP)
        try:
            AP.save_settings(request.data.get('settings') or request.data, request.user)
        except AP.Refused as ex:
            _refused(ex)
        return self.get(request)


class PolicyRulesView(AtBase):
    def put(self, request, pk):
        self.need(SETUP)
        try:
            AP.save_policy_rules(pk, request.data or {}, request.user)
        except AP.Refused as ex:
            _refused(ex)
        P = M('PayrollManagement', 'AirTicketPolicy')
        return Response({'policy': _pol_json(P.objects.select_related('country').get(pk=pk))})


class FaresView(AtBase):
    def post(self, request):
        self.need(SETUP)
        try:
            row = AP.save_fare(request.data or {}, request.user)
        except AP.Refused as ex:
            _refused(ex)
        return Response({'fare': row, 'fares': AP.fares()})

    def delete(self, request, pk):
        self.need(SETUP)
        try:
            AP.delete_fare(pk)
        except AP.Refused as ex:
            _refused(ex)
        return Response({'fares': AP.fares()})


def _on(request, field='on'):
    v = request.query_params.get(field)
    try:
        return date.fromisoformat(v[:10]) if v else timezone.localdate()
    except ValueError:
        raise S.Refused(tr('Enter a valid date.'), problems={field: tr('Enter a valid date.')})


class CheckView(AtBase):
    def get(self, request):
        self.need(POLICY_READ | ALLOC_READ | SETUP)
        E = M('EmpManagement', 'emp_master')
        qs = E.objects.filter(is_active=True)
        if self.c.branches is not None:
            qs = qs.filter(emp_branch_id__in=self.c.branches)
        eid = request.query_params.get('employee_id')
        if not eid:
            return Response({'employees': [{'id': e.pk, 'code': e.emp_code, 'name': emp_name(e)} for e in qs.order_by('emp_code')[:2000]]})
        e = qs.select_related('emp_branch_id', 'emp_nationality').filter(pk=eid).first()
        if e is None:
            raise S.Refused(tr('Employee not found.'), status=404)
        return Response(AP.employee_check(e, _on(request)))


class ProvisionReportView(AtBase):
    def get(self, request):
        self.need(PROV_READ)
        as_of = _on(request, 'as_of')
        out = AP.provision_report(as_of)
        if self.c.branches is not None:
            E = M('EmpManagement', 'emp_master')
            ok = set(E.objects.filter(emp_branch_id__in=self.c.branches).values_list('pk', flat=True))
            out['rows'] = [r for r in out['rows'] if r['employee_id'] in ok]
            out['totals'] = {k: str(AP.q2(sum((AP.D(r[k]) for r in out['rows']), AP.ZERO))) for k in ('liability', 'posted', 'to_post')}
        return Response(out)


def _request(self, rid):
    R = M('PayrollManagement', 'AirTicketRequest')
    r = R.objects.select_related('employee', 'allocation', 'allocation__policy').filter(pk=rid).first()
    if r is None or not self.emp_ok(r.employee_id):
        raise S.Refused(tr('Air ticket request not found.'), status=404)
    return r


class TravelView(AtBase):
    def get(self, request):
        self.need(REQ_READ)
        R = M('PayrollManagement', 'AirTicketRequest')
        rid = request.query_params.get('request_id')
        if not rid:   # the ticket requests whose policy allows partial use
            qs = self.in_branches(R.objects.filter(request_type='TICKET', status__in=('PENDING', 'APPROVED', 'PROCESSED'))
                                  .select_related('employee', 'allocation', 'allocation__policy'), 'employee__emp_branch_id', request)
            rows = []
            for r in qs.order_by('-request_date', '-id')[:300]:
                if r.allocation_id and AP.is_partial(r.allocation.policy_id):
                    t = AP.travel(r.pk) or {}
                    rows.append({'id': r.pk, 'number': r.document_number or f'#{r.pk}', 'employee': emp_name(r.employee), 'code': r.employee.emp_code,
                                 'policy': r.allocation.policy.name, 'status': r.status, 'request_date': r.request_date,
                                 'travellers': t.get('names') or [], 'amount': t.get('amount') or '', 'applied': bool(t.get('applied')),
                                 'left': str(AP.q2(r.allocation.remaining_amount))})
            return Response({'rows': rows})
        r = _request(self, rid)
        a = r.allocation
        day = r.departure_date or r.request_date or timezone.localdate()
        cover = AP.value(r.employee, a.policy, day)[2] if a else []   # the travellers the policy covers, each with the fare
        return Response({'request': {'id': r.pk, 'number': r.document_number or f'#{r.pk}', 'employee': emp_name(r.employee), 'status': r.status,
                                     'policy': a.policy.name if a else '', 'partial': bool(a and AP.is_partial(a.policy_id)),
                                     'left': str(AP.q2(a.remaining_amount)) if a else '0.00'},
                         'covered': cover, 'travelled': sorted(AP.travelled_keys(a, exclude_req=r.pk)) if a else [],
                         'travel': AP.travel(r.pk)})

    def post(self, request):
        self.need(REQ_EDIT)
        r = _request(self, request.data.get('request_id'))
        try:
            with transaction.atomic():
                t = AP.set_travel(r, request.data.get('travellers') or [], request.user)
        except AP.Refused as ex:
            _refused(ex)
        r.allocation.refresh_from_db()
        return Response({'travel': t, 'left': str(AP.q2(r.allocation.remaining_amount))})


class PayOutsideView(AtBase):
    def post(self, request):
        self.need(REQ_EDIT)
        r = _request(self, request.data.get('request_id'))
        try:
            with transaction.atomic():
                amt = AP.pay_outside(r, str(request.data.get('reference') or '')[:60], request.user)
        except AP.Refused as ex:
            _refused(ex)
        return Response({'status': 'PROCESSED', 'amount': str(AP.q2(amt))})
