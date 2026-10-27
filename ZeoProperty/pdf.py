"""v1.40.0 – printed documents of the Property app (reportlab, company letterhead from Company profile, Arabic shaped):
lease contract, sale agreement (SPA), invoice / billing record, receipt, owner statement, deposit statement (move-out),
condition report (inspection, with the move-in comparison), bank deposit slip, purchase order. Clauses come from the
Document templates (per country and language) when one exists; placeholders in {braces} are filled in."""
import io

from django.http import Http404, HttpResponse

from zeo.appkit.engine import d, emp_name
from zeo.appkit.engine import guard
from zeo.appkit.views import Base, get_reg
from ZeoCRM.pdf import _company, _font, _img, _profile, _sh

from .models import DocTemplate

BLUE = '#0f4c81'


def _style():
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph
    reg_f, bold_f = _font()

    def P(t, size=9.5, bold=False, color='#1f2430', align=0):
        return Paragraph(_sh(str(t if t is not None else '')).replace('\n', '<br/>'), ParagraphStyle('x', fontName=bold_f if bold else reg_f, fontSize=size, leading=size * 1.32,
                                                                                                    textColor=colors.HexColor(color), alignment=align))
    return P


def letterhead(P, title, number):
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, Spacer, Table, TableStyle
    comp, prof = _company(), _profile()
    name = getattr(prof, 'legal_name', '') or getattr(comp, 'name', '') or 'Company'
    left = [P(name, 14, True)]
    for line in ((getattr(prof, 'po_box', '') and f'P.O. Box {prof.po_box}'), getattr(prof, 'phone', ''), getattr(prof, 'email', ''), getattr(prof, 'website', ''),
                 (getattr(prof, 'tax_id', '') and f'TRN {prof.tax_id}'), getattr(prof, 'letterhead_header', '')):
        if line:
            left.append(P(line, 8.5, color='#5b6170'))
    right = [P(title.upper(), 16, True, BLUE, 2), P(number, 12, True, align=2)]
    logo = _img(getattr(comp, 'logo', None))
    if logo:
        right.insert(0, Image(logo, width=38 * mm, height=18 * mm, kind='proportional', hAlign='RIGHT'))
    t = Table([[left, right]], colWidths=[108 * mm, 70 * mm])
    t.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LINEBELOW', (0, 0), (-1, 0), 0.8, colors.HexColor(BLUE)), ('BOTTOMPADDING', (0, 0), (-1, 0), 8)]))
    return [t, Spacer(1, 5 * mm)], name, prof


def kv(P, pairs, cols=2):
    from reportlab.lib.units import mm
    from reportlab.platypus import Table, TableStyle
    pairs = [(k, v) for k, v in pairs if v not in (None, '', 'None')]
    rows, w = [], 178 / cols
    for i in range(0, len(pairs), cols):
        chunk = pairs[i:i + cols]
        row = []
        for k, v in chunk:
            row += [P(k, 8, color='#5b6170'), P(v, 9.5)]
        while len(row) < cols * 2:
            row += ['', '']
        rows.append(row)
    t = Table(rows, colWidths=[w * 0.38 * mm, w * 0.62 * mm] * cols)
    t.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('TOPPADDING', (0, 0), (-1, -1), 1.5), ('BOTTOMPADDING', (0, 0), (-1, -1), 1.5)]))
    return t


def grid(P, head, rows, widths, right=()):
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Table, TableStyle
    data = [[P(h, 8, True, '#ffffff', 2 if i in right else 0) for i, h in enumerate(head)]]
    for r in rows:
        data.append([P(c, 8.5, align=2 if i in right else 0) for i, c in enumerate(r)])
    t = Table(data, colWidths=[w * mm for w in widths], repeatRows=1)
    t.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), colors.HexColor(BLUE)), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                           ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f4f6fb')]), ('LINEBELOW', (0, -1), (-1, -1), 0.6, colors.HexColor('#c9cfdd')),
                           ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3)]))
    return t


def signatures(P, name, prof, other='Tenant', other_name=''):
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, Spacer, Table, TableStyle
    sig = [P('For ' + name, 9, True)]
    imgs = [Image(x, width=30 * mm, height=18 * mm, kind='proportional', hAlign='LEFT') for x in (_img(getattr(prof, 'signature', None)), _img(getattr(prof, 'stamp', None))) if x]
    if imgs:
        sig.append(Table([imgs], hAlign='LEFT'))
    sig.append(P(getattr(prof, 'signatory_name', '') or '', 9))
    if getattr(prof, 'signatory_designation', ''):
        sig.append(P(prof.signatory_designation, 8.5, color='#5b6170'))
    return Table([[sig, [P(other, 9, True), Spacer(1, 14 * mm), P(other_name or 'Name / signature / date', 8, color='#5b6170')]]], colWidths=[90 * mm, 88 * mm],
                 style=TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP')]))


def clauses(kind, ctx, lang='en'):
    from .complete import country
    qs = DocTemplate.objects.filter(kind=kind, is_active=True).exclude(review_status='to_review')
    t = qs.filter(country=country(), language=lang).first() or qs.filter(country='', language=lang).first() or qs.filter(language=lang).first() or qs.first()
    if t is None:
        return ''
    body = t.body
    for k, v in ctx.items():
        body = body.replace('{' + k + '}', str(v if v is not None else ''))
    return body


money = lambda v: f'{float(v or 0):,.2f}'  # noqa: E731


def build(kind, o, app):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Spacer
    from .complete import cinfo, compare
    from .app import cur
    P = _style()
    buf = io.BytesIO()
    c = cur()
    pdf = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=14 * mm, bottomMargin=16 * mm, title=f'{kind} {getattr(o, "number", "")}')
    S = lambda h=4: Spacer(1, h * mm)  # noqa: E731
    story = []
    number = getattr(o, 'number', '') or str(o.pk)
    ci = cinfo()

    if kind == 'lease':
        head, name, prof = letterhead(P, 'Lease contract', number)
        story += head
        u = o.unit
        story += [kv(P, [('Landlord / manager', name), ('Tenant', o.party.name + (f' ({o.party.company_name})' if o.party.company_name else '')),
                         ('Tenant ID', f'{o.party.id_type} {o.party.id_document}'.strip()), ('Phone / e-mail', ' · '.join(x for x in (o.party.phone, o.party.email) if x)),
                         ('Property', f'{u.building.name}, {u.building.address or u.building.city}'), ('Unit', u.unit_no + (f', floor {u.floor}' if u.floor else '') + (f' + {", ".join(x.unit_no for x in o.units.all())}' if o.units.exists() else '')),
                         ('Type / area', f'{u.unit_type.name if u.unit_type_id else ""} {u.area or ""} sq ft'.strip()), ('Furnishing', u.get_furnishing_display() if u.furnishing else ''),
                         ('Period', f'{d(o.start)} – {d(o.end)}'), ('Annual rent', f'{c} {money(o.annual_rent)}'), ('Payments', f'{o.payments} ({o.get_method_display()})'),
                         ('Security deposit', f'{c} {money(o.deposit)}'), ('Rent-free days', o.rent_free_days or ''), (ci['registration'], o.registration_ref),
                         ('Break option', d(o.break_date) if o.break_date else ''), ('Rent review', f'{d(o.review_date)} ({o.get_review_kind_display()})' if o.review_date and o.review_kind else ''),
                         ('Version', f'{o.version} ({o.get_change_display()})' if o.version > 1 else '')]), S()]
        if o.parties:
            from .models import Party
            rows = []
            for x in o.parties:
                p = Party.objects.filter(pk=x.get('party')).first()
                rows.append([p.name if p else '', dict(joint='Joint tenant', guarantor='Guarantor', occupant='Occupant', contact='Company contact').get(x.get('role'), x.get('role', '')), f'{x.get("share") or ""}'])
            story += [P('Parties', 9, True, BLUE), grid(P, ['Name', 'Role', 'Rent share %'], rows, [90, 50, 38], right=(2,)), S()]
        bills = [b for b in o.bills.exclude(status='void').exclude(is_credit=True).order_by('due_date')] if o.status != 'draft' else []
        if bills:
            story += [P('Payment schedule', 9, True, BLUE), grid(P, ['Due', 'Description', 'Amount', 'VAT', 'Total'], [[d(b.due_date), b.description or b.charge.name, money(b.amount), money(b.vat), money(b.total)] for b in bills],
                                                                [26, 92, 20, 18, 22], right=(2, 3, 4)), S()]
        elif o.schedule:
            story += [P('Payment schedule', 9, True, BLUE), grid(P, ['Due', 'Label', 'Amount'], [[d(x.get('due')), x.get('label', ''), money(x.get('amount'))] for x in o.schedule], [40, 98, 40], right=(2,)), S()]
        if o.charges:
            from .models import ChargeType
            story += [P('Other charges', 9, True, BLUE), grid(P, ['Charge', 'Amount', 'Per'], [[(ChargeType.objects.filter(pk=x.get('charge')).first() or ChargeType(name='')).name, money(x.get('amount')), x.get('per', '')] for x in o.charges], [98, 40, 40], right=(1,)), S()]
        body = clauses('lease', {'tenant': o.party.name, 'unit': u.label, 'building': u.building.name, 'start': d(o.start), 'end': d(o.end), 'rent': f'{c} {money(o.annual_rent)}',
                                 'deposit': f'{c} {money(o.deposit)}', 'company': name, 'registration': o.registration_ref, 'payments': o.payments, 'notice_days': ci['notice_days']})
        if body:
            story += [P('Terms and conditions', 9, True, BLUE), P(body, 8.5, color='#3a4050'), S(6)]
        if o.accepted_on:
            story += [P(f'Accepted online by the tenant on {d(o.accepted_on)} from {o.accepted_ip}', 8.5, color='#1f7a4d'), S()]
        story.append(signatures(P, name, prof, 'Tenant', o.party.name))

    elif kind == 'sale':
        head, name, prof = letterhead(P, 'Sale agreement', number)
        story += head
        u = o.unit
        story += [kv(P, [('Seller', name), ('Buyer', o.party.name), ('Buyer ID', f'{o.party.id_type} {o.party.id_document}'.strip()), ('Property', f'{u.building.name}, {u.building.address or u.building.city}'),
                         ('Unit', u.unit_no + (f', floor {u.floor}' if u.floor else '')), ('Area', f'{u.area or ""} sq ft' if u.area else ''), ('Price', f'{c} {money(o.price)}'),
                         ('Discount', f'{o.discount_percent}%' if o.discount_percent else ''), ('Signed on', d(o.signed_on) if o.signed_on else ''), ('Handover planned', d(o.handover_due) if o.handover_due else ''),
                         ('Completion', d(o.completion_on) if o.completion_on else ''), ('Status', o.get_status_display())]), S()]
        if o.co_buyers:
            from .models import Party
            story += [P('Co-buyers', 9, True, BLUE), grid(P, ['Name', 'Share %'], [[(Party.objects.filter(pk=x.get('party')).first() or Party(name='')).name, f'{x.get("share") or ""}'] for x in o.co_buyers], [138, 40], right=(1,)), S()]
        plan = o.plan or []
        if plan:
            story += [P('Payment plan', 9, True, BLUE), grid(P, ['Instalment', '%', 'Amount', 'Due'], [[x.get('label', ''), f'{x.get("percent") or ""}', money(x.get('amount') or (float(o.price) * float(x.get('percent') or 0) / 100)), d(x.get('due')) if x.get('due') else ''] for x in plan], [80, 20, 40, 38], right=(1, 2)), S()]
        body = clauses('spa', {'buyer': o.party.name, 'unit': u.label, 'building': u.building.name, 'price': f'{c} {money(o.price)}', 'company': name, 'handover': d(o.handover_due) if o.handover_due else ''})
        if body:
            story += [P('Terms and conditions', 9, True, BLUE), P(body, 8.5, color='#3a4050'), S(6)]
        story.append(signatures(P, name, prof, 'Buyer', o.party.name))

    elif kind == 'bill':
        head, name, prof = letterhead(P, 'Credit note' if o.is_credit else 'Invoice', number)
        story += head
        who = o.party or o.owner
        story += [kv(P, [('To', who.name if who else ''), ('Address', getattr(who, 'address', '')), ('Date', d(o.created_at)), ('Due date', d(o.due_date)), ('Unit', o.unit.label if o.unit_id else ''),
                         ('Lease / sale', (o.lease.number if o.lease_id else '') or (o.sale.number if o.sale_id else '')), ('Period', f'{d(o.period_from)} – {d(o.period_to)}' if o.period_from else ''),
                         ('Accounting ref.', o.external_ref), ('Credit note for', o.credit_of.number if o.credit_of_id else '')]), S()]
        story += [grid(P, ['Description', 'Amount', 'VAT', 'Total'], [[o.description or o.charge.name, money(o.amount), money(o.vat), money(o.total)]], [110, 22, 22, 24], right=(1, 2, 3)), S(2)]
        story += [kv(P, [('Paid', f'{c} {money(o.paid)}'), ('Balance', f'{c} {money(o.balance)}'), ('Status', o.get_status_display())], 3), S(6)]
        story.append(signatures(P, name, prof, 'Received by', ''))

    elif kind == 'receipt':
        head, name, prof = letterhead(P, 'Receipt', number)
        story += head
        who = o.party or o.owner
        story += [kv(P, [('Received from', who.name if who else ''), ('Date', d(o.received_on)), ('Amount', f'{c} {money(o.amount)}'), ('Method', o.get_method_display()), ('Reference', o.reference),
                         ('Cheque', f'{o.cheque.cheque_no} {o.cheque.bank}' if o.cheque_id else ''), ('Accounting ref.', o.external_ref)]), S()]
        allocs = list(o.allocations.select_related('bill__charge'))
        if allocs:
            story += [P('Applied to', 9, True, BLUE), grid(P, ['Billing record', 'Description', 'Amount'], [[a.bill.number, a.bill.description or a.bill.charge.name, money(a.amount)] for a in allocs], [34, 104, 40], right=(2,)), S()]
            if o.amount > o.allocated:
                story += [P(f'Unallocated: {c} {money(o.amount - o.allocated)}', 9), S()]
        body = clauses('receipt', {'name': who.name if who else '', 'amount': f'{c} {money(o.amount)}', 'company': name, 'date': d(o.received_on)})
        if body:
            story += [P(body, 8.5, color='#3a4050'), S(6)]
        story.append(signatures(P, name, prof, 'Payer', who.name if who else ''))

    elif kind == 'statement':
        head, name, prof = letterhead(P, 'Owner statement', number)
        story += head
        story += [kv(P, [('Owner', o.owner.name), ('Period', f'{d(o.period_from)} – {d(o.period_to)}'), ('Property', o.building.name if o.building_id else 'All properties'), ('Status', o.get_status_display())]), S()]
        story += [grid(P, ['Date', 'Unit', 'Line', 'Amount'], [[l.get('date', ''), l.get('unit', ''), l.get('text', ''), money(l.get('amount'))] for l in (o.lines or [])], [24, 34, 92, 28], right=(3,)), S(2)]
        story += [kv(P, [('Collected', f'{c} {money(o.collected)}'), ('Owner contributions', f'{c} {money(o.contributions)}'), ('Expenses', f'{c} {money(o.expenses)}'), ('Management fees', f'{c} {money(o.fees)}'),
                         ('Leasing / other fees', f'{c} {money(o.other_fees)}'), ('Net to owner', f'{c} {money(o.net)}')], 3), S(6)]
        body = clauses('statement', {'owner': o.owner.name, 'net': f'{c} {money(o.net)}', 'company': name})
        if body:
            story += [P(body, 8.5, color='#3a4050'), S(6)]
        story.append(signatures(P, name, prof, 'Owner', o.owner.name))

    elif kind == 'moveout':
        head, name, prof = letterhead(P, 'Deposit statement', number)
        story += head
        l = o.lease
        dep = sum((b.paid for b in l.bills.filter(charge__kind='deposit').exclude(status='void')), 0)
        story += [kv(P, [('Tenant', l.party.name), ('Unit', l.unit.label), ('Lease', l.number), ('Move-out date', d(o.move_out_on)), ('Inspection', d(o.inspection_on) if o.inspection_on else ''),
                         ('Keys returned', 'Yes' if o.keys_returned else 'No'), ('Status', o.get_status_display())]), S()]
        rows = [['Security deposit held', money(dep)]]
        for x in (o.deduction_lines or []):
            rows.append([f'Deduction – {x.get("item", "")}', '-' + money(x.get('amount'))])
        if not o.deduction_lines and o.deductions:
            rows.append(['Deductions' + (f' – {o.deduction_note}' if o.deduction_note else ''), '-' + money(o.deductions)])
        if o.unpaid:
            rows.append(['Unpaid bills at move-out', '-' + money(o.unpaid)])
        rows.append(['Refund to the tenant', money(o.refund)])
        story += [grid(P, ['Line', f'Amount {c}'], rows, [138, 40], right=(1,)), S()]
        refunds = list(o.refunds.all())
        if refunds:
            story += [P('Refund', 9, True, BLUE), grid(P, ['Refund', 'Status', 'Paid on', 'Reference', 'Amount'], [[r.number, r.get_status_display(), d(r.paid_on) if r.paid_on else '', r.reference, money(r.amount)] for r in refunds], [30, 36, 26, 56, 30], right=(4,)), S()]
        story.append(signatures(P, name, prof, 'Tenant', l.party.name))

    elif kind == 'inspection':
        head, name, prof = letterhead(P, 'Condition report', number)
        story += head
        story += [kv(P, [('Inspection', o.get_kind_display()), ('Unit', o.unit.label), ('Date', d(o.scheduled_on)), ('Inspector', emp_name(o.inspector) if o.inspector_id else ''),
                         ('Lease / sale', (o.lease.number if o.lease_id else '') or (o.sale.number if o.sale_id else '')), ('Tenant / buyer', o.tenant_name), ('Status', o.get_status_display()),
                         ('Compared with', o.compare_to.number if o.compare_to_id else '')]), S()]
        cond = dict(ok='OK', fair='Fair', poor='Poor', damaged='Damaged')
        story += [P('Findings', 9, True, BLUE), grid(P, ['Room', 'Item', 'Condition', 'Severity', 'Note', 'Cost'],
                                                     [[x.get('room', ''), x.get('item', ''), cond.get(x.get('condition'), x.get('condition', '')), (x.get('severity') or '').capitalize(), x.get('note', ''), money(x.get('cost')) if x.get('cost') else '']
                                                      for x in (o.items or [])], [30, 40, 20, 18, 50, 20], right=(5,)), S()]
        cmp = compare(o)
        if cmp:
            story += [P('Changes since the move-in inspection', 9, True, BLUE), grid(P, ['Room', 'Item', 'Move-in', 'Now', 'Cost'], [[x['room'], x['item'], cond.get(x['before'], x['before']), cond.get(x['after'], x['after']), money(x['cost']) if x['cost'] else ''] for x in cmp], [36, 60, 26, 26, 30], right=(4,)), S()]
        if o.meter_readings:
            story += [P('Meter readings', 9, True, BLUE), grid(P, ['Meter', 'Reading'], [[x.get('meter', ''), f'{x.get("reading") or ""}'] for x in o.meter_readings], [120, 58], right=(1,)), S()]
        if o.keys:
            story += [P('Keys and cards', 9, True, BLUE), grid(P, ['Item', 'Qty'], [[x.get('item', ''), f'{x.get("qty") or ""}'] for x in o.keys], [138, 40], right=(1,)), S()]
        story += [kv(P, [('Total cost of findings', f'{c} {money(o.total_cost)}'), ('Signed by inspector', d(o.inspector_signed_on) if o.inspector_signed_on else ''), ('Signed by tenant / buyer', d(o.tenant_signed_on) if o.tenant_signed_on else '')], 3), S(6)]
        story.append(signatures(P, name, prof, 'Tenant / buyer', o.tenant_name))

    elif kind == 'slip':
        head, name, prof = letterhead(P, 'Bank deposit slip', number)
        story += head
        story += [kv(P, [('Bank / account', o.bank), ('Date', d(o.deposited_on)), ('Cheques', o.count), ('Total', f'{c} {money(o.total)}'), ('Bank ref.', o.reference)]), S()]
        story += [grid(P, ['Cheque no.', 'Bank', 'Drawer', 'Date', 'Amount'], [[x.cheque_no, x.bank, x.drawer_name or x.party.name, d(x.cheque_date), money(x.amount)] for x in o.cheques.select_related('party')], [30, 40, 60, 24, 24], right=(4,)), S(6)]
        story.append(signatures(P, name, prof, 'Bank', ''))

    elif kind == 'purchase_order':
        head, name, prof = letterhead(P, 'Purchase order', number)
        story += head
        story += [kv(P, [('Vendor', o.vendor.name), ('Contact', ' · '.join(x for x in (o.vendor.contact, o.vendor.phone, o.vendor.email) if x)), ('Date', d(o.ordered_on) if o.ordered_on else ''), ('Needed by', d(o.needed_by) if o.needed_by else ''),
                         ('Property', (o.unit.label if o.unit_id else (o.building.name if o.building_id else ''))), ('Description', o.description), ('Status', o.get_status_display())]), S()]
        story += [grid(P, ['Item', 'Qty', 'Unit price', 'Total'], [[x.get('item', ''), f'{x.get("qty") or ""}', money(x.get('price')), money(float(x.get('qty') or 0) * float(x.get('price') or 0))] for x in (o.lines or [])], [104, 20, 26, 28], right=(1, 2, 3)), S(2)]
        story += [kv(P, [('Order total', f'{c} {money(o.total)}')], 1), S(6)]
        if o.note:
            story += [P(o.note, 8.5, color='#3a4050'), S()]
        story.append(signatures(P, name, prof, 'Vendor', o.vendor.name))
    else:
        raise Http404
    footer = getattr(prof, 'letterhead_footer', '')
    if footer:
        story += [S(6), P(footer, 7.5, color='#8a90a0', align=1)]
    pdf.build(story)
    return buf.getvalue()


class RecordPdfView(Base):
    """GET /zeo-property/api/r/<kind>/<pk>/pdf/ – the printed document of a record (the user must be able to open it)."""

    @guard
    def get(self, request, kind, pk):
        r = get_reg(self.app, kind, request)
        if not r.pdf:
            raise Http404
        obj = r.scope(request).filter(pk=pk).first()
        if obj is None:
            raise Http404
        data = build(kind, obj, self.app)
        resp = HttpResponse(data, content_type='application/pdf')
        resp['Content-Disposition'] = f'inline; filename="{getattr(obj, "number", kind)}.pdf"'
        return resp
