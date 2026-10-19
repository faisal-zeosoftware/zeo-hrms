"""Release 2 screens (v1.34.0): maintenance calendar, helpdesk mailbox, field (mobile) summary, Excel import of assets and parts."""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO

from django.db import transaction
from django.http import HttpResponse
from django.utils.dateparse import parse_date
from rest_framework.response import Response

from zeo.appkit.engine import AppError, In, guard, jsonable, need, user_name
from zeo.appkit.views import Base

from . import maint as MT
from .models import AssetCard, Category, Location, Part, StockMove, Ticket, WorkOrder


def can(app, request, *roles):
    return app.is_admin(request) or app.any(request, *roles)


class CalendarView(Base):
    """GET ?from=&to= – work orders by planned date and the coming plan dates."""

    @guard
    def get(self, request):
        q = request.query_params
        t = MT.today()
        frm = parse_date(q.get('from') or '') or t.replace(day=1)
        to = parse_date(q.get('to') or '') or (MT.add_months(frm, 1) - timedelta(days=1))
        if (to - frm).days > 400:
            raise AppError('Choose a period of up to a year.')
        a = self.app
        need(a.regs['work_order'].can_open(request), 'You have no access to work orders.')
        wos = a.regs['work_order'].scope(request)
        plans = a.regs['plan'].scope(request) if a.regs['plan'].can_open(request) else a.regs['plan'].model.objects.none()
        if q.get('technician', '').isdigit():
            wos = wos.filter(technician_id=int(q['technician']))
            plans = plans.filter(technician_id=int(q['technician']))
        return Response(jsonable({'from': frm, 'to': to, 'items': MT.calendar_items(frm, to, wos, plans)}))


class MailboxView(Base):
    """POST {password} – set the helpdesk mailbox password (never sent back); POST {test: true} – read the mailbox now."""

    @guard
    def post(self, request):
        a = self.app
        need(can(a, request, 'ast_manager'), 'Only the asset manager can change the mailbox.')
        f = In(request)
        s = a.settings()
        if f.has('password'):
            s.mail_password = f.s('password')[:200]
            s.save(update_fields=['mail_password'])
            a.event('settings', 0, f'Helpdesk mailbox password changed by {user_name(request.user)}', request.user)
        out = {'password_set': bool(s.mail_password)}
        if f.b('test'):
            from .mailin import check_mailbox
            try:
                out['read'] = check_mailbox()
                out['message'] = f'Mailbox read: {out["read"]} new ticket(s) or replies.'
            except Exception as e:
                raise AppError(f'The mailbox could not be read: {e}')
        return Response(out)

    @guard
    def get(self, request):
        s = self.app.settings()
        return Response({'password_set': bool(s.mail_password), 'enabled': s.mail_enabled, 'host': s.mail_host, 'user': s.mail_user})


class FieldView(Base):
    """GET – what the field (mobile) screen shows: my open work orders and tickets, counts I do, quick figures."""

    @guard
    def get(self, request):
        a = self.app
        e = a.my_emp(request)
        out = {'employee': e.pk if e else None, 'rights': a.rights(request), 'work_orders': [], 'tickets': [], 'counts': [], 'my_assets': []}
        if e is not None:
            out['work_orders'] = [{'id': o.pk, 'number': o.number, 'title': o.title, 'asset': f'{o.card.code} {o.card.name}', 'status': o.status,
                                   'status_label': o.get_status_display(), 'priority': o.get_priority_display(), 'date': o.scheduled_on}
                                  for o in WorkOrder.objects.filter(technician=e, status__in=MT.OPEN_WO).select_related('card')[:50]]
            out['tickets'] = [{'id': t.pk, 'number': t.number, 'subject': t.subject, 'status': t.status, 'status_label': t.get_status_display(),
                               'mine': t.reporter_id == e.pk} for t in Ticket.objects.filter(reporter=e).exclude(status__in=('closed', 'cancelled'))[:30]]
            from .models import Audit
            out['counts'] = [{'id': x.pk, 'number': x.number, 'name': x.name, 'found': x.found, 'expected': x.expected}
                             for x in Audit.objects.filter(auditors=e, status='counting')[:20]]
            out['my_assets'] = [{'id': c.pk, 'code': c.code, 'name': c.name, 'due_back': c.due_back} for c in AssetCard.objects.filter(holder_employee=e)[:50]]
        return Response(jsonable(out))


# ------------------------------------------------------------------ Excel import (bulk upload)
def _wb(request):
    up = request.FILES.get('file')
    if up is None:
        raise AppError('Choose the Excel file.', 'file')
    from openpyxl import load_workbook
    try:
        wb = load_workbook(up, read_only=True, data_only=True)
    except Exception:
        raise AppError('This is not an Excel (.xlsx) file.', 'file')
    rows = list(wb.active.iter_rows(values_only=True))
    if not rows:
        raise AppError('The sheet is empty.')
    head = [str(h or '').strip().lower() for h in rows[0]]
    return [dict(zip(head, r)) for r in rows[1:] if any(v not in (None, '') for v in r)]


def _num(v, field, line):
    if v in (None, ''):
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        raise AppError(f'Line {line}: {field} is not a number.')


def _date(v, field, line):
    if v in (None, ''):
        return None
    if isinstance(v, date):
        return v if not hasattr(v, 'date') else v.date()
    d = parse_date(str(v)[:10])
    if d is None:
        raise AppError(f'Line {line}: {field} is not a date (use YYYY-MM-DD).')
    return d


TEMPLATES = {
    'assets': ['name', 'category code', 'serial number', 'model', 'manufacturer', 'purchase date', 'cost', 'supplier', 'invoice no', 'warranty until',
               'location code', 'criticality', 'rfid tag', 'notes'],
    'parts': ['code', 'name', 'group', 'unit', 'manufacturer part no', 'supplier', 'minimum stock', 'reorder quantity', 'store code', 'opening quantity', 'unit cost'],
}


class ImportView(Base):
    """GET ?template=assets|parts – the Excel template; POST multipart {file, kind, dry_run} – check or import.
    Assets are registered like the New asset form (HR asset made too); parts with an opening stock in a store."""

    @guard
    def get(self, request, kind):
        need(kind in TEMPLATES, 'Unknown import.')
        from openpyxl import Workbook
        wb = Workbook()
        wb.active.append(TEMPLATES[kind])
        buf = BytesIO()
        wb.save(buf)
        r = HttpResponse(buf.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        r['Content-Disposition'] = f'attachment; filename="asset-management-{kind}-template.xlsx"'
        return r

    @guard
    def post(self, request, kind):
        a = self.app
        need(kind in TEMPLATES, 'Unknown import.')
        need(can(a, request, 'ast_manager', 'ast_coordinator') if kind == 'assets' else can(a, request, 'ast_manager', 'ast_storekeeper'),
             'You cannot import these records.')
        rows = _wb(request)
        dry = In(request).b('dry_run')
        errors, done = [], 0
        with transaction.atomic():
            for i, r in enumerate(rows, start=2):
                try:
                    with transaction.atomic():
                        (self.asset if kind == 'assets' else self.part)(r, i, request)
                    done += 1
                except AppError as e:
                    errors.append({'line': i, 'error': e.message})
            if dry:
                transaction.set_rollback(True)
        if not dry:
            a.event('settings', 0, f'Excel import of {kind}: {done} line(s) by {user_name(request.user)}', request.user)
        return Response({'checked' if dry else 'imported': done, 'errors': errors})

    def asset(self, r, line, request):
        from .app import card_check, card_defaults, card_saver
        cat = Category.objects.filter(code__iexact=str(r.get('category code') or '').strip()).first()
        if cat is None:
            raise AppError(f'Line {line}: category code "{r.get("category code")}" not found.')
        if not r.get('name'):
            raise AppError(f'Line {line}: the name is empty.')
        loc = Location.objects.filter(code__iexact=str(r.get('location code') or '').strip()).first() if r.get('location code') else None
        c = AssetCard(name=str(r['name'])[:100], category=cat, serial_number=str(r.get('serial number') or '')[:100], model_no=str(r.get('model') or '')[:100],
                      manufacturer=str(r.get('manufacturer') or '')[:100], purchase_date=_date(r.get('purchase date'), 'purchase date', line) or MT.today(),
                      purchase_cost=_num(r.get('cost'), 'cost', line) or 0, supplier=str(r.get('supplier') or '')[:150], invoice_no=str(r.get('invoice no') or '')[:60],
                      warranty_end=_date(r.get('warranty until'), 'warranty until', line), location=loc, rfid_tag=str(r.get('rfid tag') or '')[:80],
                      criticality=str(r.get('criticality') or 'medium').lower() if str(r.get('criticality') or 'medium').lower() in ('low', 'medium', 'high', 'critical') else 'medium',
                      notes=str(r.get('notes') or ''), branch_id=getattr(loc, 'branch_id', None))
        card_defaults(c, type('Req', (), {'data': {}})())
        try:
            card_check(c, request, None)
            card_saver(c, request, True, None)
        except AppError as e:
            raise AppError(f'Line {line}: {e.message}')

    def part(self, r, line, request):
        code = str(r.get('code') or '').strip()
        if not r.get('name'):
            raise AppError(f'Line {line}: the name is empty.')
        p = Part.objects.filter(code__iexact=code).first() if code else None
        if p is None:
            from .app import app
            p = Part(code=code or app.next_number('part', 'SP'))
        p.name = str(r['name'])[:150]
        p.group, p.unit = str(r.get('group') or p.group or '')[:80], str(r.get('unit') or p.unit or 'pcs')[:20]
        p.manufacturer_no, p.supplier = str(r.get('manufacturer part no') or p.manufacturer_no or '')[:80], str(r.get('supplier') or p.supplier or '')[:150]
        p.min_qty = _num(r.get('minimum stock'), 'minimum stock', line) or p.min_qty or 0
        p.reorder_qty = _num(r.get('reorder quantity'), 'reorder quantity', line) or p.reorder_qty or 0
        p.save()
        qty = _num(r.get('opening quantity'), 'opening quantity', line)
        if qty:
            store = Location.objects.filter(code__iexact=str(r.get('store code') or '').strip()).first()
            if store is None:
                raise AppError(f'Line {line}: store code "{r.get("store code")}" not found.')
            MT.post_move(StockMove(kind='receipt', part=p, qty=qty, to_location=store, unit_cost=_num(r.get('unit cost'), 'unit cost', line) or 0,
                                   reference='Opening stock (import)', branch_id=store.branch_id, date=MT.today()), request.user)
