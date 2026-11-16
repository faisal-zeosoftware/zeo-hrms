"""v1.67.14 – service releases 3 – 5: API of the technician's job (visit, checklist, parts, signature, service report), ticket
parts and job billing, vans and van stock, preventive-visit planning, service KPIs and reports."""
import csv
import io

from django.http import Http404, HttpResponse
from rest_framework import status
from rest_framework.response import Response

from . import access as A
from . import field_service as FS
from . import services as S
from . import service_kpi as K
from .models import JobPart, ServiceVisit, SupportAllocation, Ticket
from .support_planner import can_open_job
from .views import Base, In, guard


def _alloc(request, pk, edit=False):
    a = SupportAllocation.objects.select_related('ticket', 'employee').filter(pk=pk).first()
    if a is None or not can_open_job(request, a):
        raise Http404
    if edit and a.access_until is not None and not A.can(request, 'ticket_manage'):
        raise FS.FieldError('The job is closed – it is read-only history now.')
    if edit and not A.can(request, 'ticket_manage'):
        e = A.my_emp(request)
        if e is None or e.pk != a.employee_id:
            raise FS.FieldError('Only the allocated person works on the job.')
    return a


def _ticket(request, pk):
    t = Ticket.objects.filter(pk=pk).first()
    if t is None or not A.ticket_scope(request).filter(pk=t.pk).exists():
        raise Http404
    return t


def _related(pk):
    """v1.67.14: records a job action changes – for the data workbook's @date (history loaded with its real days)."""
    from .models import WorkLog
    a = SupportAllocation.objects.filter(pk=pk).first()
    if a is None:
        return []
    out = [(SupportAllocation, a.pk), (Ticket, a.ticket_id)]
    v = ServiceVisit.objects.filter(allocation=a).first()
    if v is not None:
        out.append((ServiceVisit, v.pk))
    out += [(WorkLog, w) for w in WorkLog.objects.filter(ticket_id=a.ticket_id).values_list('pk', flat=True)]
    parts = list(JobPart.objects.filter(ticket_id=a.ticket_id).values_list('pk', 'stock_move_id'))
    out += [(JobPart, p) for p, _m in parts]
    moves = [m for _p, m in parts if m]
    if moves and FS.assets_on():   # v1.67.15: the stock issue of a part gets the job's day too
        from ZeoAssets.models import StockMove
        out += [(StockMove, m) for m in moves]
    return out


class JobVisitView(Base):
    """GET / POST support-planner/<pk>/visit/ – the service report of the job: start {lat, lng} | save {...} | finish {..., lat, lng}
    | resend {to}."""
    zeo_related = staticmethod(_related)

    @guard
    def get(self, request, pk):
        a = _alloc(request, pk)
        v = FS.visit_for(a, create=a.access_until is None)
        return Response(FS.visit_row(v, True) if v else None)

    @guard
    def post(self, request, pk):
        a = _alloc(request, pk, edit=True)
        v = FS.visit_for(a)
        d = request.data
        act = d.get('action') or 'save'
        at = None
        if d.get('at') not in (None, ''):   # v1.67.15: a visit recorded after the fact – support managers only
            A.need(request, 'ticket_manage', 'Only a support manager records a visit with its own times.')
            at = FS.when(d.get('at'))
        if act == 'start':
            FS.start(v, request.user, d.get('lat'), d.get('lng'), at=at)
        elif act == 'save':
            FS.save(v, d, request.user)
        elif act == 'finish':
            FS.finish(v, d, request.user, d.get('lat'), d.get('lng'), at=at)
        elif act == 'resend':
            FS.resend_report(v, d.get('to'))
        else:
            raise FS.FieldError('Unknown action.')
        return Response(FS.visit_row(ServiceVisit.objects.get(pk=v.pk), True))


class JobPartsView(Base):
    """POST support-planner/<pk>/parts/ {part, location, qty | name, unit_cost} – a part used on the job (from the van);
    DELETE support-planner/<pk>/parts/<part_pk>/."""
    zeo_related = staticmethod(_related)

    @guard
    def post(self, request, pk):
        a = _alloc(request, pk, edit=True)
        v = FS.visit_for(a)
        FS.add_part(a.ticket, request.data, request.user, visit=v)
        return Response(FS.visit_row(ServiceVisit.objects.get(pk=v.pk), True), status=status.HTTP_201_CREATED)

    @guard
    def delete(self, request, pk, part_pk):
        a = _alloc(request, pk, edit=True)
        p = JobPart.objects.filter(pk=part_pk, ticket=a.ticket).first()
        if p is None:
            raise Http404
        FS.remove_part(p, request.user)
        v = FS.visit_for(a)
        return Response(FS.visit_row(v, True))


class VisitPdfView(Base):
    @guard
    def get(self, request, pk):
        v = ServiceVisit.objects.select_related('ticket').filter(pk=pk).first()
        if v is None:
            raise Http404
        ok = A.ticket_scope(request).filter(pk=v.ticket_id).exists() or (v.allocation_id and can_open_job(request, v.allocation))
        if not ok:
            raise Http404
        r = HttpResponse(FS.report_pdf(v), content_type='application/pdf')
        r['Content-Disposition'] = f'inline; filename="{v.number or "service-report"}.pdf"'
        return r


class TicketFieldView(Base):
    """GET tickets/<pk>/field/ – visits (service reports), parts used, what is still chargeable; POST {action: part | bill}."""

    @guard
    def get(self, request, pk):
        t = _ticket(request, pk)
        parts, hours, rate = FS.billable_of(t)
        return Response({'visits': [FS.visit_row(v) for v in t.visits.all().select_related('employee', 'ticket')],
                         'parts': [FS.part_row(p) for p in JobPart.objects.filter(ticket=t).select_related('billed_doc')],
                         'chargeable': {'parts': len(parts), 'parts_value': str(sum((p.price for p in parts), FS.ZERO)),
                                        'hours': str(sum((h.hours for h in hours), FS.ZERO)), 'rate': str(rate)},
                         'can_bill': A.can(request, 'billing_edit') and bool(t.customer_id), 'can_edit': A.can_work_ticket(request, t),
                         'assets_on': FS.assets_on()})

    @guard
    def post(self, request, pk):
        t = _ticket(request, pk)
        act = request.data.get('action')
        if act == 'part':
            if not A.can_work_ticket(request, t):
                raise A.Denied('Only the people working on the ticket add parts.')
            FS.add_part(t, request.data, request.user)
        elif act == 'remove':
            if not A.can_work_ticket(request, t):
                raise A.Denied('Only the people working on the ticket remove parts.')
            p = JobPart.objects.filter(pk=request.data.get('id'), ticket=t).first()
            if p is None:
                raise Http404
            FS.remove_part(p, request.user)
        elif act == 'bill':
            A.need(request, 'billing_edit')
            doc = FS.bill_job(t, request.user)
            return Response({'pi': doc.pk, 'number': doc.number}, status=status.HTTP_201_CREATED)
        else:
            raise FS.FieldError('Unknown action.')
        return self.get(request, pk)


class VansView(Base):
    """GET field/vans/?all=1 – my van(s) and their stock (managers: every van); POST {part, qty, from, to, note} – restock a van."""

    @guard
    def get(self, request):
        manager = A.can(request, 'ticket_manage')
        emp = None if (manager and request.query_params.get('all')) else A.my_emp(request)
        if emp is None and not manager:
            return Response({'vans': [], 'stores': [], 'assets_on': FS.assets_on(), 'manager': False})
        return Response({'vans': FS.van_stock(emp), 'stores': FS.stores() if manager else [], 'assets_on': FS.assets_on(), 'manager': manager})

    @guard
    def post(self, request):
        A.need(request, 'ticket_manage', 'Only support managers restock vans.')
        d = request.data
        FS.transfer_to_van(d.get('part'), d.get('qty'), d.get('from'), d.get('to'), request.user, d.get('note') or '')
        return Response({'vans': FS.van_stock(None), 'stores': FS.stores(), 'assets_on': True, 'manager': True}, status=status.HTTP_201_CREATED)


class PartsPickView(Base):
    """GET field/parts/?location=&q= – parts to pick (in stock at that store / van)."""

    @guard
    def get(self, request):
        loc = request.query_params.get('location')
        return Response({'parts': FS.parts_for_pick(int(loc) if str(loc or '').isdigit() else None, request.query_params.get('q', '')),
                         'stores': FS.stores(), 'my_vans': [{'id': l.pk, 'name': l.name} for l in FS.vans(A.my_emp(request))] if A.my_emp(request) else []})


class PpmView(Base):
    """GET field/ppm/ – the preventive visits that would be planned now; POST – plan them (support managers)."""

    @guard
    def get(self, request):
        A.need(request, 'ticket_manage')
        return Response({'plan': FS.plan_ppm(request.user, apply=False), 'horizon': S.settings().ppm_horizon_days})

    @guard
    def post(self, request):
        A.need(request, 'ticket_manage')
        made = FS.plan_ppm(request.user, apply=True)
        return Response({'made': made}, status=status.HTTP_201_CREATED)


class ServiceKpiView(Base):
    """GET service-kpi/?from=&to=&team= – the KPI tiles and the small tables."""

    @guard
    def get(self, request):
        A.need(request, 'kpi_view', 'Service KPIs are for support managers and Management.')   # v1.67.17 (checklist 47)
        f, t = K.period(request.query_params)
        team = request.query_params.get('team')
        return Response({'kpis': K.kpis(f, t, int(team) if str(team or '').isdigit() else None), 'sla': K.sla_by_priority(f, t),
                         'technicians': K.technicians(f, t)[:15], 'repeat_failures': K.repeat_failures(f, t)[:10],
                         'failures': K.failures_by_category(f, t)[:10],
                         'reports': [{'key': k, 'label': v[0]} for k, v in K.REPORTS.items()]})


class ServiceReportView(Base):
    """GET service-kpi/report/<key>/?from=&to=&as=csv  ('format' is taken by the API framework)"""

    @guard
    def get(self, request, key):
        A.need(request, 'kpi_view', 'Service reports are for support managers and Management.')   # v1.67.17 (checklist 47)
        if key not in K.REPORTS:
            raise Http404
        f, t = K.period(request.query_params)
        label, fn = K.REPORTS[key]
        rows = fn(f, t)
        if request.query_params.get('as') == 'csv':
            buf = io.StringIO()
            if rows:
                w = csv.DictWriter(buf, fieldnames=[k for k in rows[0] if k != 'id'], extrasaction='ignore')
                w.writeheader()
                w.writerows(rows)
            else:   # v1.67.14 (test): an empty period gave an empty file – say so
                csv.writer(buf).writerow([f'{label}: nothing from {f.isoformat()} to {t.isoformat()}.'])
            r = HttpResponse('﻿' + buf.getvalue(), content_type='text/csv; charset=utf-8')
            r['Content-Disposition'] = f'attachment; filename="{key}_{f:%Y%m%d}_{t:%Y%m%d}.csv"'
            return r
        return Response({'label': label, 'from': f.isoformat(), 'to': t.isoformat(), 'rows': rows})
