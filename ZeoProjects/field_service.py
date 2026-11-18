"""v1.67.14 – service releases 3 and 4: the technician's job on the phone and the service report; parts, vans and job billing.

Release 3 – technician flow and service report
  * Start job (time and location from the phone) → checklist of the equipment's kind (from the industry pack) → work done,
    findings, recommendation → parts used → customer name, signature and rating → Finish job.
  * Finishing logs the hours on the ticket (on site, approved), resolves the ticket when the job is completed, moves the
    equipment's next preventive date, and makes the service report (number SR-…, PDF) – e-mailed to the customer when the
    setting is on.
  * Preventive visits: a daily job plans a "Preventive visit" ticket for every unit under an active AMC whose next preventive
    date falls within the planning horizon (Projects › Settings › Field service).
Release 4 – parts, vans and job billing
  * Parts used on a ticket come from Asset Management stock – the technician's van (a store of kind "Vehicle" with the
    technician as person in charge) or any store – and are issued from it; removing the part returns it.
  * Each part is covered by the AMC (parts included, or up to the yearly parts limit, or warranty) or chargeable (cost + markup).
    The AMC coverage panel now counts the parts value used in the contract year.
  * "Bill this job": a PI for the customer with the chargeable parts and the billable hours (contract rate, else the default
    labour rate), through the normal billing (PI → tax invoice → payments → accounting).
"""
import base64
import io
import logging
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from . import services as S
from .models import (CustomerEquipment, JobPart, ServiceVisit, SupportAllocation, Ticket, WorkLog)

log = logging.getLogger(__name__)
ZERO = Decimal('0')
C2 = Decimal('0.01')
JOB_PAGE = '/main-sidebar/zeo-projects/my-support-jobs?job='


class FieldError(S.ProjectsError):
    pass


def q2(v):
    return Decimal(str(v or 0)).quantize(C2)


def _dec(v):
    try:
        return None if v in (None, '') else Decimal(str(v)).quantize(Decimal('0.000001'))
    except Exception:   # noqa: BLE001
        return None


# ------------------------------------------------------------------ release 3: the visit
def template_checklist(t):
    cat = t.equipment.category if t.equipment_id else None
    items = list(cat.checklist or []) if cat else []
    if not items:
        from .packs import GENERAL_CHECKLIST, edited_checklists
        # v1.67.23 (pending 271): a checklist emptied on screen means no checklist for that kind of equipment
        items = [] if cat is not None and cat.pk in edited_checklists() else GENERAL_CHECKLIST
    return [{'item': i, 'result': '', 'note': ''} for i in items]


def visit_for(al, create=True):
    v = ServiceVisit.objects.filter(allocation=al).first()
    if v is None and create:
        v = ServiceVisit.objects.create(ticket=al.ticket, allocation=al, employee=al.employee, checklist=template_checklist(al.ticket))
    return v


def _open(v):
    if v.status != 'open':
        raise FieldError('This job is finished – the service report is final.')


def when(value):
    """v1.67.15: the time a support manager gives for a visit recorded after the fact (paper job sheet, phone offline) –
    ISO date-time; local time when it has no zone. Never in the future."""
    if value in (None, ''):
        return None
    from datetime import datetime
    from zoneinfo import ZoneInfo
    try:
        at = datetime.fromisoformat(str(value).strip().replace('Z', '+00:00'))
    except ValueError:
        raise FieldError('Enter the time as date and time (YYYY-MM-DD HH:MM).', 'at')
    if at.tzinfo is None:
        at = at.replace(tzinfo=ZoneInfo(S.settings().timezone or 'Asia/Dubai'))
    if at > S.now():
        raise FieldError('The time cannot be in the future.', 'at')
    return at


def _local_day(at):
    from zoneinfo import ZoneInfo
    return at.astimezone(ZoneInfo(S.settings().timezone or 'Asia/Dubai')).date()


def start(v, user=None, lat=None, lng=None, at=None):
    _open(v)
    if v.started_at:
        return v
    v.started_at, v.start_lat, v.start_lng = at or S.now(), _dec(lat), _dec(lng)
    if not v.number:
        v.number = S.next_number('srv')
    v.save()
    t = v.ticket
    if v.allocation_id and v.allocation.status == 'planned':
        v.allocation.status = 'in_progress'
        v.allocation.save(update_fields=['status'])
    S.mark_first_response(t, v.started_at)
    if t.stage in ('new', 'assigned', 'reopened', 'waiting_internal'):
        try:
            S.set_stage(t, 'in_progress', user)
        except S.ProjectsError:
            pass
    t.save()
    where = f' at {v.start_lat}, {v.start_lng}' if v.start_lat is not None else ''
    S.event(t, 'visit', f'{S.emp_name(v.employee)} started the job{where} ({v.number}).', user)
    return v


def save(v, data, user=None):
    _open(v)
    if 'checklist' in data and isinstance(data['checklist'], list):
        out = []
        for i, row in enumerate(data['checklist'][:80]):
            res = str(row.get('result') or '')
            out.append({'item': str(row.get('item') or f'Item {i + 1}')[:200], 'result': res if res in ('ok', 'issue', 'na') else '',
                        'note': str(row.get('note') or '')[:300]})
        v.checklist = out
    for f in ('work_done', 'findings', 'recommendation'):
        if f in data:
            setattr(v, f, str(data[f] or '')[:4000])
    if 'customer_name' in data:
        v.customer_name = str(data['customer_name'] or '')[:150]
    if 'rating' in data:
        r = data.get('rating')
        v.rating = int(r) if str(r or '').isdigit() and 1 <= int(r) <= 5 else None
    if 'customer_signature' in data:
        sig = str(data['customer_signature'] or '')
        if sig and not sig.startswith('data:image/png;base64,'):
            raise FieldError('The signature must be drawn on the screen.', 'customer_signature')
        if len(sig) > 400_000:
            raise FieldError('The signature is too large – clear it and sign again.', 'customer_signature')
        v.customer_signature = sig
    v.save()
    return v


@transaction.atomic
def finish(v, data, user=None, lat=None, lng=None, at=None):
    _open(v)
    save(v, data, user)
    if not v.started_at:
        raise FieldError('Start the job first.')
    if at is not None and at <= v.started_at:
        raise FieldError('The finish time must be after the start time.', 'at')
    if not (v.work_done or '').strip():
        raise FieldError('Write the work done.', 'work_done')
    if any(not r.get('result') for r in v.checklist or []):
        raise FieldError('Tick every checklist line (OK, issue or not applicable).', 'checklist')
    completed = bool(data.get('job_completed', True))
    v.finished_at, v.end_lat, v.end_lng, v.status, v.job_completed = at or S.now(), _dec(lat), _dec(lng), 'done', completed
    v.save()
    t = v.ticket
    hours = max(Decimal('0.25'), v.hours)
    try:
        w = S.log_work(t, v.employee, hours=min(hours, Decimal('24')), day=_local_day(at) if at else S.local_today(),
                       description=f'{v.number}: {v.work_done}'[:2000],
                       onsite=True, user=user, auto_approve=True, manager=True)
        WorkLog.objects.filter(pk=w.pk).update(visit=v)
    except S.ProjectsError as ex:
        S.event(t, 'visit', f'Hours not logged for {v.number}: {ex}', user, public=False)
    if v.allocation_id:
        v.allocation.status = 'done'
        v.allocation.save(update_fields=['status'])
    if v.rating:
        t.satisfaction = v.rating
        t.save(update_fields=['satisfaction'])
    if completed and t.stage not in ('resolved', 'closed'):
        try:
            S.set_stage(t, 'resolved', user, note=v.work_done, force=True)
        except S.ProjectsError as ex:
            S.event(t, 'visit', f'Ticket not resolved: {ex}', user, public=False)
    if t.ticket_kind == 'visit' and t.equipment_id:
        x = t.equipment
        x.last_pm_date = S.local_today()
        months = x.category.pm_months if x.category_id else None
        x.next_pm_date = S.add_months(x.last_pm_date, months) if months else None
        x.save(update_fields=['last_pm_date', 'next_pm_date'])
    S.event(t, 'visit', f'{S.emp_name(v.employee)} finished the job – service report {v.number}' + ('' if completed else ' (work not completed)') + '.', user)
    S.close_todos(v.allocation if v.allocation_id else t, 'Support job')
    transaction.on_commit(lambda: _send_report(v.pk))
    return v


def _send_report(pk):
    v = ServiceVisit.objects.select_related('ticket', 'ticket__customer').get(pk=pk)
    if not S.settings().report_to_customer:
        return
    to = report_emails(v.ticket)
    if not to:
        return
    try:
        pdf = report_pdf(v)
        S.send_mail(to, f'Service report {v.number} – {v.ticket.number}',
                    f'Dear customer,\n\nPlease find the service report {v.number} for {v.ticket.number} "{v.ticket.subject}" attached.\n\n'
                    f'Work done: {v.work_done}\n\nThank you.\n', attachments=[(f'{v.number}.pdf', pdf, 'application/pdf')])
        ServiceVisit.objects.filter(pk=pk).update(report_sent_to=', '.join(to)[:255], report_sent_at=S.now())
    except Exception:   # noqa: BLE001 – the job stays finished; the report can be downloaded and sent again
        log.exception('service report %s not sent', v.number)


def report_emails(t):
    """The ticket's contacts, else the customer's own e-mail address."""
    to = S.customer_emails(t)
    if not to and t.customer_id and t.customer.email:
        to = [t.customer.email]
    return to


def resend_report(v, to=None):
    if v.status != 'done':
        raise FieldError('Finish the job first.')
    to = [x.strip() for x in (to or '').replace(';', ',').split(',') if x.strip()] or report_emails(v.ticket)
    if not to:
        raise FieldError('The customer has no e-mail address – enter one.', 'to')
    S.send_mail(to, f'Service report {v.number} – {v.ticket.number}', f'Please find the service report {v.number} attached.\n',
                attachments=[(f'{v.number}.pdf', report_pdf(v), 'application/pdf')])
    v.report_sent_to, v.report_sent_at = ', '.join(to)[:255], S.now()
    v.save(update_fields=['report_sent_to', 'report_sent_at'])
    return v


def report_pdf(v):
    """The service report: company letterhead, customer / site / equipment, times, checklist, work, parts, signature."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from .billing_pdf import _company, _esc, _font, _img, _profile, _sh
    reg, bold = _font()
    st = ParagraphStyle('n', fontName=reg, fontSize=9, leading=12)
    sb = ParagraphStyle('b', fontName=bold, fontSize=9, leading=12)
    h1 = ParagraphStyle('h', fontName=bold, fontSize=15, leading=19)
    t = v.ticket
    co, prof = _company(), _profile()
    P = lambda x, s=st: Paragraph(_esc(_sh(x)).replace('\n', '<br/>'), s)
    out = []
    logo = _img(getattr(prof, 'letterhead', None)) or _img(getattr(prof, 'logo', None)) if prof else None
    head = [[P(getattr(co, 'name', '') or 'Service company', sb), P('Service report', h1)]]
    if logo:
        try:
            head[0][0] = Image(logo, width=45 * mm, height=18 * mm, kind='proportional')
        except Exception:   # noqa: BLE001
            pass
    out.append(Table(head, colWidths=[95 * mm, 85 * mm]))
    out.append(Spacer(1, 4 * mm))
    fmt = lambda d: d.astimezone(S.now().tzinfo).strftime('%d/%m/%Y %H:%M') if d else '–'
    x = t.equipment
    info = [['Report', v.number, 'Ticket', t.number],
            ['Customer', t.customer.name if t.customer_id else 'Internal', 'Site', t.site.name if t.site_id else '–'],
            ['Equipment', f'{x.number} {x.category.name}' if x else '–', 'Serial / model', f"{x.serial_no or ''} {x.model_no or ''}".strip() if x else '–'],
            ['Technician', S.emp_name(v.employee), 'Kind of work', t.get_ticket_kind_display()],
            ['Started', fmt(v.started_at), 'Finished', fmt(v.finished_at)],
            ['Time on site', f'{v.hours} h', 'Contract', t.contract.number if t.contract_id else '–']]
    tb = Table([[P(a, sb), P(b), P(c, sb), P(d)] for a, b, c, d in info], colWidths=[28 * mm, 62 * mm, 28 * mm, 62 * mm])
    tb.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#d0d4de')), ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#f4f5f9')),
                            ('BACKGROUND', (2, 0), (2, -1), colors.HexColor('#f4f5f9')), ('VALIGN', (0, 0), (-1, -1), 'TOP')]))
    out += [tb, Spacer(1, 4 * mm), P(f'Request: {t.subject}', sb)]
    if v.checklist:
        rows = [[P('Checklist', sb), P('Result', sb), P('Note', sb)]] + [
            [P(r['item']), P({'ok': 'OK', 'issue': 'Issue', 'na': 'N/A'}.get(r.get('result'), '–')), P(r.get('note') or '')] for r in v.checklist]
        ct = Table(rows, colWidths=[90 * mm, 22 * mm, 68 * mm], repeatRows=1)
        ct.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#d0d4de')), ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#eef0f6'))]))
        out += [Spacer(1, 3 * mm), ct]
    for label, txt in (('Work done', v.work_done), ('Findings', v.findings), ('Recommendation', v.recommendation)):
        if txt:
            out += [Spacer(1, 3 * mm), P(label, sb), P(txt)]
    parts = list(v.parts.all()) or list(JobPart.objects.filter(ticket=t, visit__isnull=True))
    if parts:
        rows = [[P('Part', sb), P('Qty', sb), P('Cover', sb)]] + [
            [P(f'{p.code} {p.name}'.strip()), P(f'{p.qty:g} {p.unit}'), P('Covered' if p.covered else ('Chargeable' if p.chargeable else '–'))] for p in parts]
        pt = Table(rows, colWidths=[120 * mm, 25 * mm, 35 * mm], repeatRows=1)
        pt.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#d0d4de')), ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#eef0f6'))]))
        out += [Spacer(1, 3 * mm), P('Parts used', sb), pt]
    sig = None
    if v.customer_signature.startswith('data:image/png;base64,'):
        try:
            sig = Image(io.BytesIO(base64.b64decode(v.customer_signature.split(',', 1)[1])), width=55 * mm, height=22 * mm, kind='proportional')
        except Exception:   # noqa: BLE001
            sig = None
    out += [Spacer(1, 5 * mm), Table([[P('Customer', sb), P('Signature', sb), P('Rating', sb)],
                                      [P(v.customer_name or '–'), sig or P('–'), P(f'{v.rating} / 5' if v.rating else '–')]],
                                     colWidths=[60 * mm, 70 * mm, 50 * mm])]
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=12 * mm, bottomMargin=12 * mm,
                      title=f'Service report {v.number}').build(out)
    return buf.getvalue()


def visit_row(v, full=False):
    r = {'id': v.pk, 'number': v.number, 'ticket': v.ticket_id, 'ticket_number': v.ticket.number, 'employee': v.employee_id,
         'employee_name': S.emp_name(v.employee), 'status': v.status, 'started_at': v.started_at.isoformat() if v.started_at else None,
         'finished_at': v.finished_at.isoformat() if v.finished_at else None, 'hours': str(v.hours), 'rating': v.rating,
         'job_completed': v.job_completed, 'report_sent_to': v.report_sent_to,
         'start_lat': str(v.start_lat) if v.start_lat is not None else None, 'start_lng': str(v.start_lng) if v.start_lng is not None else None}
    if full:
        r.update(checklist=v.checklist, work_done=v.work_done, findings=v.findings, recommendation=v.recommendation, customer_name=v.customer_name,
                 customer_signature=v.customer_signature, parts=[part_row(p) for p in JobPart.objects.filter(ticket=v.ticket).order_by('id')])
    return r


# ------------------------------------------------------------------ release 3: preventive visits
def plan_ppm(user=None, horizon=None, apply=True):
    """Preventive-visit tickets for units under an active AMC whose next preventive date is within the horizon. Returns
    the tickets made (or that would be made)."""
    days = horizon if horizon is not None else S.settings().ppm_horizon_days
    until = S.local_today() + timedelta(days=days)
    made = []
    qs = CustomerEquipment.objects.select_related('category', 'customer', 'site').filter(is_active=True, category__pm_months__isnull=False)
    for x in qs:
        if x.status not in ('in_service', 'under_repair'):
            continue
        due = x.next_pm_date or (S.add_months(x.last_pm_date, x.category.pm_months) if x.last_pm_date else S.local_today())
        if due > until:
            continue
        if Ticket.objects.filter(equipment=x, ticket_kind='visit').exclude(stage__in=('resolved', 'closed', 'cancelled')).exists():
            continue
        cons = S.equipment_contracts(x)
        if not cons:
            continue
        made.append({'equipment': x.number, 'customer': x.customer.name, 'due': due.isoformat(), 'contract': cons[0].number})
        if apply:
            t = S.create_ticket(subject=f'Preventive visit – {x.category.name} {x.number}', description=f'Planned preventive visit due {due:%d/%m/%Y}.',
                                user=user, customer=x.customer, contract=cons[0], site=x.site, equipment=x, ticket_kind='visit', priority='low')
            if not x.next_pm_date:
                CustomerEquipment.objects.filter(pk=x.pk).update(next_pm_date=due)
            made[-1]['ticket'] = t.number
    return made


# ------------------------------------------------------------------ release 4: parts and vans
def assets_on():
    try:
        from ZeoApps.services import is_active
        return is_active('assets')
    except Exception:   # noqa: BLE001
        return False


def vans(emp=None):
    if not assets_on():
        return []
    from ZeoAssets.models import Location
    qs = Location.objects.filter(kind='vehicle', is_active=True)
    if emp is not None:
        qs = qs.filter(custodian=emp)
    return list(qs.select_related('custodian'))


def van_stock(emp=None):
    if not assets_on():
        return []
    from ZeoAssets.models import Stock
    out = []
    for loc in vans(emp):
        rows = Stock.objects.filter(location=loc, qty__gt=0).select_related('part').order_by('part__code')
        out.append({'id': loc.pk, 'code': loc.code, 'name': loc.name, 'custodian': loc.custodian_id, 'custodian_name': S.emp_name(loc.custodian) if loc.custodian_id else '',
                    'lines': [{'part': s.part_id, 'code': s.part.code, 'name': s.part.name, 'unit': s.part.unit, 'qty': str(s.qty),
                               'value': str(q2(Decimal(s.qty) * Decimal(s.part.unit_cost or 0))), 'min': str(s.part.min_qty)} for s in rows]})
    return out


def stores():
    if not assets_on():
        return []
    from ZeoAssets.models import Location
    return [{'id': l.pk, 'code': l.code, 'name': l.name, 'kind': l.kind} for l in Location.objects.filter(is_active=True, kind__in=('store', 'vehicle'))]


def parts_for_pick(location_id=None, q=''):
    if not assets_on():
        return []
    from ZeoAssets.models import Part, Stock
    if location_id:
        rows = Stock.objects.filter(location_id=location_id, qty__gt=0).select_related('part')
        if q:
            rows = rows.filter(part__name__icontains=q) | rows.filter(part__code__icontains=q)
        return [{'id': s.part_id, 'code': s.part.code, 'name': s.part.name, 'unit': s.part.unit, 'qty': str(s.qty), 'unit_cost': str(s.part.unit_cost)}
                for s in rows[:100]]
    ps = Part.objects.filter(is_active=True)
    if q:
        ps = ps.filter(name__icontains=q) | ps.filter(code__icontains=q)
    return [{'id': p.pk, 'code': p.code, 'name': p.name, 'unit': p.unit, 'qty': str(p.on_hand), 'unit_cost': str(p.unit_cost)} for p in ps[:100]]


@transaction.atomic
def transfer_to_van(part_id, qty, from_id, to_id, user=None, note=''):
    if not assets_on():
        raise FieldError('Asset Management is not active – vans and stock live there.')
    from ZeoAssets.maint import post_move
    from ZeoAssets.models import Location, Part, StockMove
    from zeo.appkit.engine import AppError
    part = Part.objects.filter(pk=part_id).first()
    a, b = Location.objects.filter(pk=from_id).first(), Location.objects.filter(pk=to_id).first()
    if not (part and a and b):
        raise FieldError('Choose the part and both stores.')
    try:
        return post_move(StockMove(kind='transfer', part=part, qty=Decimal(str(qty)), from_location=a, to_location=b, date=S.local_today(),
                                   note=(note or 'Van restock')[:255]), user)
    except AppError as ex:
        raise FieldError(str(ex))


def _cover(t, cost):
    """(covered, chargeable) for a part of this cost on the ticket."""
    if not t.customer_id:
        return False, False
    if t.coverage_kind == 'warranty':
        return True, False
    c = t.contract
    if c is None or t.billable_status == 'chargeable' or t.parts_chargeable:
        return False, True
    mode = (c.amc_type.parts if c.amc_type_id else 'included')
    if mode == 'included':
        return True, False
    if mode == 'limit':
        if c.parts_limit is None:
            return True, False
        return (True, False) if parts_used_value(c) + cost <= Decimal(c.parts_limit) else (False, True)
    return False, True


def parts_used_value(c):
    start = S.contract_year_start(c)
    qs = JobPart.objects.filter(ticket__contract=c, covered=True)
    if start:
        qs = qs.filter(created_at__date__gte=start)
    total = ZERO
    for p in qs:
        total += p.cost
    return q2(total)


@transaction.atomic
def add_part(t, data, user=None, visit=None):
    qty = Decimal(str(data.get('qty') or '1'))
    if qty <= 0:
        raise FieldError('The quantity must be more than zero.', 'qty')
    part_id, loc_id = data.get('part') or None, data.get('location') or None
    s = S.settings()
    p = JobPart(ticket=t, visit=visit, qty=qty, created_by=user if getattr(user, 'pk', None) else None)
    if part_id:
        if not assets_on():
            raise FieldError('Asset Management is not active – enter the part by name.', 'part')
        from ZeoAssets.models import Location, Part, StockMove
        from ZeoAssets.maint import post_move
        from zeo.appkit.engine import AppError
        part = Part.objects.filter(pk=part_id).first()
        if part is None:
            raise FieldError('Part not found.', 'part')
        p.part_id, p.code, p.name, p.unit, p.unit_cost = part.pk, part.code, part.name, part.unit, part.unit_cost
        if loc_id:
            loc = Location.objects.filter(pk=loc_id).first()
            if loc is None:
                raise FieldError('Store not found.', 'location')
            try:
                m = post_move(StockMove(kind='issue', part=part, qty=qty, from_location=loc, date=S.local_today(), reference=t.number[:80],
                                        note=f'Ticket {t.number}'[:255]), user)
            except AppError as ex:
                raise FieldError(str(ex), 'qty')
            p.location_id, p.stock_move_id, p.unit_cost = loc.pk, m.pk, m.unit_cost
    else:
        name = str(data.get('name') or '').strip()
        if not name:
            raise FieldError('Choose a part or enter its name.', 'name')
        p.name, p.code, p.unit = name[:150], str(data.get('code') or '')[:40], str(data.get('unit') or 'pcs')[:20]
        p.unit_cost = Decimal(str(data.get('unit_cost') or '0'))
    cost = q2(qty * Decimal(p.unit_cost or 0))
    p.covered, p.chargeable = _cover(t, cost)
    if 'chargeable' in data and data['chargeable'] is not None:   # the manager can decide otherwise
        p.chargeable = bool(data['chargeable'])
        p.covered = not p.chargeable and bool(t.customer_id)
    price = data.get('unit_price')
    p.unit_price = q2(Decimal(str(price))) if price not in (None, '') else q2(Decimal(p.unit_cost or 0) * (1 + Decimal(s.parts_markup or 0) / 100))
    p.save()
    S.event(t, 'parts', f'Part used: {qty:g} × {p.code} {p.name}'.replace('  ', ' ') + (' (covered)' if p.covered else ' (chargeable)' if p.chargeable else ''),
            user, public=False)
    return p


@transaction.atomic
def remove_part(p, user=None):
    if p.billed_doc_id:
        raise FieldError(f'Already billed on {p.billed_doc.number} – cancel that first.')
    if p.stock_move_id and p.location_id and assets_on():
        from ZeoAssets.maint import post_move
        from ZeoAssets.models import Location, Part, StockMove
        post_move(StockMove(kind='return', part=Part.objects.get(pk=p.part_id), qty=p.qty, to_location=Location.objects.get(pk=p.location_id),
                            date=S.local_today(), reference=p.ticket.number[:80], note=f'Removed from ticket {p.ticket.number}'[:255]), user)
    S.event(p.ticket, 'parts', f'Part removed: {p.qty:g} × {p.code} {p.name}'.replace('  ', ' '), user, public=False)
    p.delete()


def part_row(p):
    return {'id': p.pk, 'ticket': p.ticket_id, 'visit': p.visit_id, 'part': p.part_id, 'location': p.location_id, 'code': p.code, 'name': p.name,
            'unit': p.unit, 'qty': str(p.qty), 'unit_cost': str(q2(p.unit_cost)), 'cost': str(p.cost), 'unit_price': str(p.unit_price),
            'price': str(p.price), 'covered': p.covered, 'chargeable': p.chargeable, 'billed': p.billed_doc.number if p.billed_doc_id else ''}


# ------------------------------------------------------------------ release 4: billing a job
def billable_of(t):
    """Chargeable parts and billable hours of a ticket that are not billed yet."""
    rate = (t.contract.hourly_rate_outside if t.contract_id and t.contract.hourly_rate_outside else None) or S.settings().labour_rate
    parts = list(JobPart.objects.filter(ticket=t, chargeable=True, billed_doc__isnull=True))
    hours = list(WorkLog.objects.filter(ticket=t, billable=True, billed_doc__isnull=True, status='approved'))
    return parts, hours, Decimal(rate or 0)


def bill_job(t, user=None):
    if not t.customer_id:
        raise FieldError('An internal ticket is not billed.')
    parts, hours, rate = billable_of(t)
    if not parts and not hours:
        raise FieldError('Nothing chargeable on this ticket (no chargeable parts or billable hours).')
    vat = str(S.settings().default_vat_rate)
    lines = [{'description': f'{t.number} – {p.code} {p.name}'.replace('  ', ' ')[:255], 'quantity': str(p.qty), 'unit_price': str(p.unit_price),
              'vat_type': 'standard', 'vat_rate': vat} for p in parts]
    total_h = sum((Decimal(w.hours) for w in hours), ZERO)
    if total_h:
        lines.append({'description': f'{t.number} – labour {total_h:g} h ({t.subject})'[:255], 'quantity': str(total_h), 'unit_price': str(q2(rate)),
                      'vat_type': 'standard', 'vat_rate': vat})
    from . import billing as B
    doc = B.pi_create_customer(t.customer_id, user=user, lines=lines, client_ref=t.number)
    JobPart.objects.filter(pk__in=[p.pk for p in parts]).update(billed_doc=doc)
    WorkLog.objects.filter(pk__in=[w.pk for w in hours]).update(billed_doc=doc)
    S.event(t, 'billing', f'Billed on {doc.number}: {len(parts)} part line(s), {total_h:g} h.', user, public=False)
    return doc
