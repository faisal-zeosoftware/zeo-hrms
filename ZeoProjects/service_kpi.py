"""v1.67.14 – service release 5: service KPIs and the SOP reports.

KPIs for a period (tickets created in it, unless said otherwise):
  first-time fix rate     tickets resolved with one finished visit ÷ tickets resolved with at least one visit
  response time           average hours from created to first response (first visit start / first reply)
  resolution time         average hours from created to resolved
  SLA met                 resolved tickets without a resolution breach ÷ resolved tickets
  PPM compliance          preventive visits resolved ÷ preventive visits due in the period (by planned date / created)
  utilisation             hours on site (finished visits) ÷ allocated hours, per technician
  customer rating         average of the visit ratings (1 – 5)
Reports (each also as CSV): technician productivity, repeat failures (same unit ≥ 2 tickets in 90 days), AMC profitability
(contract value per year vs labour cost and covered parts cost), parts used, service reports register, SLA by priority,
equipment failures by category."""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum

from . import services as S
from .models import AMCContract, CustomerEquipment, JobPart, ServiceVisit, SupportAllocation, Ticket, WorkLog

ZERO = Decimal('0')


def _h(delta):
    return round(delta.total_seconds() / 3600, 2) if delta is not None else None


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 2) if xs else None


def _pct(a, b):
    return round(a * 100 / b, 1) if b else None


def period(p):
    """(from, to) from query params 'from' / 'to' (dd/mm/yyyy or ISO); default = the last 90 days."""
    def d(v):
        if not v:
            return None
        try:
            return date.fromisoformat(v)
        except ValueError:
            dd, mm, yy = v.split('/')
            return date(int(yy), int(mm), int(dd))
    t = d(p.get('to')) or S.local_today()
    f = d(p.get('from')) or t - timedelta(days=89)
    return f, t


def kpis(f, t, team=None):
    tq = Ticket.objects.filter(created_at__date__gte=f, created_at__date__lte=t, customer__isnull=False)
    if team:
        tq = tq.filter(team_id=team)
    tickets = list(tq.select_related('equipment', 'equipment__category'))
    ids = [x.pk for x in tickets]
    done = [x for x in tickets if x.resolved_at]
    visits = defaultdict(int)
    for tid in ServiceVisit.objects.filter(ticket_id__in=ids, status='done').values_list('ticket_id', flat=True):
        visits[tid] += 1
    with_visit = [x for x in done if visits[x.pk]]
    ftf = [x for x in with_visit if visits[x.pk] == 1]
    resp = [_h(x.first_response_at - x.created_at) for x in tickets if x.first_response_at]
    reso = [_h(x.resolved_at - x.created_at) for x in done]
    sla_ok = [x for x in done if not x.resolution_breached]
    ppm = [x for x in tickets if x.ticket_kind == 'visit']
    ppm_done = [x for x in ppm if x.resolved_at]
    vq = ServiceVisit.objects.filter(status='done', finished_at__date__gte=f, finished_at__date__lte=t)
    ratings = [v for v in vq.values_list('rating', flat=True) if v]
    on_site = sum((v.hours for v in vq), ZERO)
    alloc = SupportAllocation.objects.filter(planned_date__gte=f, planned_date__lte=t).exclude(status='cancelled').aggregate(h=Sum('hours'))['h'] or ZERO
    parts_cost = sum((p.cost for p in JobPart.objects.filter(ticket_id__in=ids)), ZERO)
    return {'from': f.isoformat(), 'to': t.isoformat(), 'tickets': len(tickets), 'resolved': len(done), 'open': len(tickets) - len(done),
            'callouts': sum(1 for x in tickets if x.ticket_kind == 'callout'),
            'first_time_fix_pct': _pct(len(ftf), len(with_visit)), 'avg_response_hours': _avg(resp), 'avg_resolution_hours': _avg(reso),
            'sla_met_pct': _pct(len(sla_ok), len(done)), 'ppm_due': len(ppm), 'ppm_done': len(ppm_done), 'ppm_compliance_pct': _pct(len(ppm_done), len(ppm)),
            'visits': vq.count(), 'hours_on_site': float(round(on_site, 2)), 'hours_allocated': float(alloc),
            'utilisation_pct': _pct(float(on_site), float(alloc)), 'avg_rating': _avg(ratings), 'ratings': len(ratings),
            'parts_cost': float(round(parts_cost, 2))}


def technicians(f, t):
    rows = defaultdict(lambda: {'visits': 0, 'hours': ZERO, 'allocated': ZERO, 'completed': 0, 'ratings': [], 'jobs': 0})
    for v in ServiceVisit.objects.filter(status='done', finished_at__date__gte=f, finished_at__date__lte=t).select_related('employee'):
        r = rows[v.employee_id]
        r['name'] = S.emp_name(v.employee)
        r['visits'] += 1
        r['hours'] += v.hours
        r['completed'] += 1 if v.job_completed else 0
        if v.rating:
            r['ratings'].append(v.rating)
    for a in SupportAllocation.objects.filter(planned_date__gte=f, planned_date__lte=t).exclude(status='cancelled').select_related('employee'):
        r = rows[a.employee_id]
        r['name'] = S.emp_name(a.employee)
        r['allocated'] += Decimal(a.hours or 0)
        r['jobs'] += 1
    out = []
    for eid, r in rows.items():
        out.append({'employee': eid, 'name': r.get('name', ''), 'jobs': r['jobs'], 'visits': r['visits'], 'completed': r['completed'],
                    'hours_on_site': float(round(r['hours'], 2)), 'hours_allocated': float(r['allocated']),
                    'utilisation_pct': _pct(float(r['hours']), float(r['allocated'])), 'avg_rating': _avg(r['ratings'])})
    return sorted(out, key=lambda x: -x['visits'])


def repeat_failures(f, t, days=90):
    qs = Ticket.objects.filter(equipment__isnull=False, created_at__date__gte=f - timedelta(days=days), created_at__date__lte=t) \
        .exclude(ticket_kind='visit').select_related('equipment', 'equipment__category', 'customer', 'site').order_by('equipment_id', 'created_at')
    by = defaultdict(list)
    for x in qs:
        by[x.equipment_id].append(x)
    out = []
    for _eid, ts in by.items():
        hits = [b for a, b in zip(ts, ts[1:]) if (b.created_at - a.created_at).days <= days and b.created_at.date() >= f]
        if hits:
            x = ts[0].equipment
            out.append({'equipment': x.number, 'category': x.category.name, 'customer': ts[0].customer.name if ts[0].customer_id else '',
                        'site': ts[0].site.name if ts[0].site_id else '', 'tickets': len(ts), 'repeats': len(hits),
                        'last': ts[-1].number, 'last_subject': ts[-1].subject})
    return sorted(out, key=lambda r: -r['repeats'])


def amc_profit(f, t):
    out = []
    for c in AMCContract.objects.filter(start_date__lte=t, end_date__gte=f).select_related('customer', 'amc_type'):
        months = max(1, round(((c.end_date - c.start_date).days + 1) / 30.44))
        per_month = Decimal(c.value or 0) / months
        span = max(1, round(((min(t, c.end_date) - max(f, c.start_date)).days + 1) / 30.44))
        revenue = (per_month * span).quantize(Decimal('0.01'))
        labour = WorkLog.objects.filter(ticket__contract=c, date__gte=f, date__lte=t).aggregate(s=Sum('cost_amount'))['s'] or ZERO
        parts = sum((p.cost for p in JobPart.objects.filter(ticket__contract=c, covered=True, created_at__date__gte=f, created_at__date__lte=t)), ZERO)
        tickets = Ticket.objects.filter(contract=c, created_at__date__gte=f, created_at__date__lte=t).count()
        cost = Decimal(labour) + parts
        out.append({'contract': c.number, 'customer': c.customer.name, 'type': c.amc_type.name if c.amc_type_id else c.get_contract_type_display(),
                    'revenue': float(revenue), 'labour_cost': float(round(labour, 2)), 'parts_cost': float(round(parts, 2)), 'tickets': tickets,
                    'margin': float(round(revenue - cost, 2)), 'margin_pct': _pct(float(revenue - cost), float(revenue))})
    return sorted(out, key=lambda r: r['margin'])


def parts_used(f, t):
    return [{'date': p.created_at.date().isoformat(), 'ticket': p.ticket.number, 'customer': p.ticket.customer.name if p.ticket.customer_id else '',
             'code': p.code, 'name': p.name, 'qty': float(p.qty), 'cost': float(p.cost), 'price': float(p.price),
             'cover': 'Covered' if p.covered else ('Chargeable' if p.chargeable else '–'), 'billed': p.billed_doc.number if p.billed_doc_id else ''}
            for p in JobPart.objects.filter(created_at__date__gte=f, created_at__date__lte=t).select_related('ticket', 'ticket__customer', 'billed_doc')]


def service_reports(f, t):
    return [{'number': v.number, 'date': v.finished_at.date().isoformat() if v.finished_at else '', 'ticket': v.ticket.number,
             'customer': v.ticket.customer.name if v.ticket.customer_id else '', 'technician': S.emp_name(v.employee), 'hours': float(v.hours),
             'completed': 'Yes' if v.job_completed else 'No', 'rating': v.rating or '', 'sent_to': v.report_sent_to, 'id': v.pk}
            for v in ServiceVisit.objects.filter(status='done', finished_at__date__gte=f, finished_at__date__lte=t).select_related('ticket', 'ticket__customer', 'employee')]


def sla_by_priority(f, t):
    out = []
    for pr, label in (('critical', 'Critical'), ('high', 'High'), ('medium', 'Medium'), ('low', 'Low')):
        qs = Ticket.objects.filter(created_at__date__gte=f, created_at__date__lte=t, priority=pr, customer__isnull=False)
        n = qs.count()
        res = qs.filter(resolved_at__isnull=False)
        ok = res.filter(resolution_breached=False).count()
        out.append({'priority': label, 'tickets': n, 'resolved': res.count(), 'sla_met': ok, 'sla_met_pct': _pct(ok, res.count()),
                    'response_breached': qs.filter(response_breached=True).count()})
    return out


def failures_by_category(f, t):
    rows = Ticket.objects.filter(created_at__date__gte=f, created_at__date__lte=t, equipment__isnull=False).exclude(ticket_kind='visit') \
        .values('equipment__category__name').annotate(n=Count('id')).order_by('-n')
    units = dict(CustomerEquipment.objects.filter(is_active=True).values('category__name').annotate(n=Count('id')).values_list('category__name', 'n'))
    return [{'category': r['equipment__category__name'], 'tickets': r['n'], 'units': units.get(r['equipment__category__name'], 0),
             'per_unit': round(r['n'] / units[r['equipment__category__name']], 2) if units.get(r['equipment__category__name']) else None} for r in rows]


REPORTS = {'technicians': ('Technician productivity', technicians), 'repeat_failures': ('Repeat failures (90 days)', repeat_failures),
           'amc_profit': ('AMC profitability', amc_profit), 'parts_used': ('Parts used', parts_used),
           'service_reports': ('Service reports register', service_reports), 'sla': ('SLA by priority', sla_by_priority),
           'failures': ('Failures by equipment category', failures_by_category)}
