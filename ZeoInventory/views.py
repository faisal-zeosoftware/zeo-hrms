"""v1.75.0 Inventory screens that are not a plain register: scan, labels (PDF with Code 128 and QR) and the delivery note PDF."""
from io import BytesIO

from django.http import HttpResponse
from rest_framework.response import Response

from zeo.appkit.engine import AppError, guard, today
from zeo.appkit.views import Base

from . import services as S
from .models import Bin, StockDoc, StockMove

SIZES = {'small': (50, 25), 'medium': (70, 35), 'large': (100, 50)}


def _can(app, request):
    return app.is_admin(request) or app.any(request, 'inv_manager', 'inv_storekeeper', 'inv_accountant', 'inv_viewer')


class ScanView(Base):
    """GET ?code=… [&warehouse=ID] – what a scanned code is (item, packaging, lot, serial, bin, GS1) with its stock."""
    @guard
    def get(self, request):
        if not _can(self.app, request):
            raise AppError('You have no access to Inventory.', status=403)
        q = request.query_params
        wh = None
        if str(q.get('warehouse', '')).isdigit():
            from .models import Warehouse
            wh = Warehouse.objects.filter(pk=int(q['warehouse'])).first()
        try:
            r = S.scan(q.get('code', ''), wh)
        except S.InvError as e:
            raise AppError(e.message, 'code', status=404)
        out = {'kind': r['kind'], 'code': r['code']}
        if r['kind'] == 'bin':
            b = r['bin']
            out['bin'] = {'id': b.pk, 'code': b.code, 'warehouse': b.warehouse.code}
            out['stock'] = [{'item': str(x['product__name']), 'lot': x['lot'], 'qty': float(x['s'])} for x in
                            StockMove.objects.filter(bin=b).values('product__name', 'lot').annotate(s=__import__('django.db.models', fromlist=['Sum']).Sum('qty')) if x['s']]
            return Response(out)
        p = r['product']
        out.update({'item': {'id': p.pk, 'number': p.number, 'name': p.name, 'unit': p.uom.code if p.uom_id else ''}, 'unit': r['uom'].code if r.get('uom') else '',
                    'factor': float(r['factor']), 'lot': r.get('lot', ''), 'serial': r.get('serial', ''),
                    'expiry': r['expiry'].isoformat() if r.get('expiry') else None, 'on_hand': float(S.on_hand(p, wh)),
                    'packages': S.packs_text(p, S.on_hand(p, wh))})
        return Response(out)


def label_pdf(cards, size):
    """cards: [{'code', 'title', 'line', 'qr'}] → an A4 sheet of labels, each with a QR (opens the record in ZEO) and a Code 128 barcode"""
    from reportlab.graphics import renderPDF
    from reportlab.graphics.barcode import code128, qr
    from reportlab.graphics.shapes import Drawing
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    w, h = (x * mm for x in SIZES.get(size, SIZES['medium']))
    pw, ph = A4
    margin, gap = 8 * mm, 3 * mm
    cols = max(1, int((pw - 2 * margin + gap) // (w + gap)))
    rows = max(1, int((ph - 2 * margin + gap) // (h + gap)))
    buf = BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4)
    pdf.setTitle('Stock labels')
    for i, c in enumerate(cards):
        k = i % (cols * rows)
        if i and k == 0:
            pdf.showPage()
        x = margin + (k % cols) * (w + gap)
        y = ph - margin - (k // cols + 1) * h - (k // cols) * gap
        pdf.setLineWidth(0.3)
        pdf.setStrokeGray(0.75)
        pdf.roundRect(x, y, w, h, 2 * mm)
        pad = 2 * mm
        qs = h - 2 * pad
        q = qr.QrCodeWidget(c['qr'])
        b = q.getBounds()
        dr = Drawing(qs, qs, transform=[qs / (b[2] - b[0]), 0, 0, qs / (b[3] - b[1]), 0, 0])
        dr.add(q)
        renderPDF.draw(dr, pdf, x + pad, y + pad)
        tx, tw = x + qs + 2 * pad, w - qs - 3 * pad
        fs = max(5, h / mm / 5)
        pdf.setFillGray(0)
        pdf.setFont('Helvetica-Bold', fs * 1.2)
        pdf.drawString(tx, y + h - pad - fs * 1.2, c['code'][:24])
        pdf.setFont('Helvetica', fs * 0.85)
        for n, txt in enumerate((c.get('title') or '', c.get('line') or '')):
            while txt and pdf.stringWidth(txt, 'Helvetica', fs * 0.85) > tw:
                txt = txt[:-1]
            pdf.drawString(tx, y + h - pad - fs * (2.4 + 1.1 * n), txt)
        bar = code128.Code128(c['code'], barHeight=h * 0.26, barWidth=0.6)
        scale = min(1.0, tw / max(bar.width, 1))
        pdf.saveState()
        pdf.translate(tx, y + pad)
        pdf.scale(scale, 1)
        bar.drawOn(pdf, 0, 0)
        pdf.restoreState()
    pdf.save()
    return buf.getvalue()


class LabelsView(Base):
    """GET ?kind=item|lot|bin|doc &ids=1,2 [&size=] – item labels (one per unit / carton of a receipt with kind=doc), lot / serial labels, bin labels."""
    @guard
    def get(self, request):
        if not _can(self.app, request):
            raise AppError('You have no access to Inventory.', status=403)
        q = request.query_params
        kind = q.get('kind', 'item')
        ids = [int(x) for x in (q.get('ids') or '').split(',') if x.strip().isdigit()]
        page = self.app.page
        cards = []
        from django.apps import apps
        P = apps.get_model('ZeoMasters', 'Product')
        if kind == 'item':
            for p in P.objects.filter(pk__in=ids):
                code = p.barcode or p.number or p.code
                cards.append({'code': code, 'title': p.name, 'line': p.number, 'qr': f'ZEO:ITEM:{p.number or p.pk}'})
        elif kind == 'bin':
            for b in Bin.objects.filter(pk__in=ids).select_related('warehouse'):
                cards.append({'code': b.code, 'title': f'{b.warehouse.code} {b.name or ""}'.strip(), 'line': b.get_kind_display() if hasattr(b, 'get_kind_display') else '',
                              'qr': f'ZEO:BIN:{b.code}'})
        elif kind == 'lot':
            for m in StockMove.objects.filter(pk__in=ids).select_related('product'):
                code = m.serial or m.lot
                cards.append({'code': code, 'title': m.product.name, 'line': f'Exp {m.expiry:%d/%m/%Y}' if m.expiry else m.product.number,
                              'qr': f'ZEO:{"SN" if m.serial else "LOT"}:{code}'})
        elif kind == 'doc':   # one label per unit (serials) or per package of a receipt
            for doc in StockDoc.objects.filter(pk__in=ids):
                for ln in doc.lines.select_related('product', 'uom'):
                    n = 1 if ln.serial else int(abs(ln.qty or 0)) or 1
                    code = ln.serial or ln.lot or ln.product.barcode or ln.product.number
                    for _ in range(min(n, 500)):
                        cards.append({'code': code, 'title': ln.product.name, 'line': f'{doc.number} {ln.uom.code if ln.uom_id else ""}',
                                      'qr': f'ZEO:{"SN" if ln.serial else "LOT" if ln.lot else "ITEM"}:{code}'})
        if not cards:
            raise AppError('Nothing to print – choose the records.')
        size = q.get('size') if q.get('size') in SIZES else 'medium'
        r = HttpResponse(label_pdf(cards[:3000], size), content_type='application/pdf')
        r['Content-Disposition'] = f'inline; filename="stock-labels-{today():%Y%m%d}.pdf"'
        return r


class NoteView(Base):
    """GET – the delivery note (or any stock document) as a PDF, English and Arabic headings, with lots / serials and packages."""
    @guard
    def get(self, request, pk):
        if not _can(self.app, request):
            raise AppError('You have no access to Inventory.', status=403)
        doc = self.app.regs['doc'].scope(request).filter(pk=pk).first()
        if doc is None:
            raise AppError('Not found.', status=404)
        from django.utils.html import escape as e
        title = {'delivery': ('Delivery note', 'إشعار تسليم'), 'dropship': ('Delivery note (drop ship)', 'إشعار تسليم'), 'receipt': ('Goods receipt', 'إيصال استلام بضاعة'),
                 'transfer': ('Transfer note', 'إشعار تحويل'), 'count': ('Stock count sheet', 'ورقة جرد المخزون'),
                 'adjustment': ('Stock adjustment voucher', 'سند تسوية المخزون'), 'scrap': ('Scrap / write-off voucher', 'سند إتلاف'),
                 'consumption': ('Material issue voucher', 'سند صرف مواد'), 'return_in': ('Customer return note', 'إشعار مرتجع من العميل'),
                 'return_out': ('Return to supplier note', 'إشعار مرتجع إلى المورد'), 'opening': ('Opening stock', 'رصيد افتتاحي للمخزون')}.get(doc.kind, (doc.get_kind_display(), ''))
        sheet = doc.kind == 'count' and doc.status == 'draft'   # a count sheet: the counted column is left to fill in
        def qty_cell(ln):
            if sheet:
                return '<td class="n">______</td><td></td>'
            return f'<td class="n">{ln.qty.normalize():f} {e(ln.uom.code if ln.uom_id else "")}</td><td>{e(S.packs_text(ln.product, ln.base_qty or ln.qty))}</td>'
        rows = ''.join(f'<tr><td>{i}</td><td>{e(ln.product.number or "")}</td><td>{e(ln.product.name)}</td><td>{e(ln.lot or ln.serial)}</td>{qty_cell(ln)}</tr>'
                       for i, ln in enumerate(doc.lines.select_related('product', 'uom', 'product__uom'), 1))
        info = [('Number / الرقم', doc.number), ('Date / التاريخ', f'{doc.date:%d/%m/%Y}'), ('Customer / العميل' if doc.kind != 'receipt' else 'Supplier / المورد', str(doc.partner or '')),
                ('Ship to / عنوان التسليم', doc.ship_to), ('Contact / جهة الاتصال', f'{doc.site_contact} {doc.site_phone}'.strip()),
                ('Requested / التاريخ المطلوب', f'{doc.requested_date:%d/%m/%Y}' if doc.requested_date else ''), ('Method / طريقة التسليم', doc.get_ship_method_display() if doc.ship_method else ''),
                ('Driver / السائق', f'{doc.driver} {doc.vehicle}'.strip()), ('Reference / المرجع', doc.reference)]
        head = ''.join(f'<tr><th>{e(a)}</th><td>{e(b or "")}</td></tr>' for a, b in info if b)
        pod = ''
        if doc.delivery_status:
            pod = (f'<p class="pod">{e(doc.get_delivery_status_display())} – {e(doc.pod_name)} {doc.delivered_at:%d/%m/%Y %H:%M}</p>' if doc.delivered_at else '')
            if doc.pod_signature.startswith('data:image'):
                pod += f'<img class="sig" src="{doc.pod_signature}"/>'
        html = f'''<html><head><meta charset="utf-8"><style>
            @page {{ size: A4; margin: 16mm; }} body {{ font-family: 'DejaVu Sans', sans-serif; font-size: 10pt; color: #1f2933; }}
            h1 {{ font-size: 16pt; margin: 0 0 2mm; }} .ar {{ direction: rtl; font-size: 13pt; color: #52606d; }}
            table {{ border-collapse: collapse; width: 100%; margin-top: 4mm; }} th, td {{ border-bottom: 0.3pt solid #cbd2d9; padding: 1.5mm 2mm; text-align: left; vertical-align: top; }}
            .info th {{ width: 45mm; color: #52606d; font-weight: normal; }} .lines th {{ background: #f0f4f8; }} td.n {{ text-align: right; white-space: nowrap; }}
            .sign {{ margin-top: 18mm; display: flex; justify-content: space-between; }} .sign div {{ width: 70mm; border-top: 0.4pt solid #52606d; padding-top: 1mm; }}
            .sig {{ height: 18mm; }} </style></head><body>
            <h1>{e(title[0])}</h1><div class="ar">{e(title[1])}</div>
            <table class="info">{head}</table>
            <table class="lines"><tr><th>#</th><th>Item no.</th><th>Item / الصنف</th><th>Lot / serial</th><th>Quantity / الكمية</th><th>Packages</th></tr>{rows}</table>
            {pod}<div class="sign"><div>Delivered by / سلمه</div><div>Received by / استلمه</div></div></body></html>'''
        from weasyprint import HTML
        r = HttpResponse(HTML(string=html).write_pdf(), content_type='application/pdf')
        r['Content-Disposition'] = f'inline; filename="{doc.number}.pdf"'
        return r



class OpeningImportView(Base):
    """POST (multipart) file=.xlsx, warehouse, date, commit=0|1 – opening stock from Excel: item (number / code / barcode), quantity, unit cost,
    lot, serial, expiry (dd/mm/yyyy or a date), bin. Without commit: a preview with the error list; with commit: a draft opening stock document."""
    @guard
    def post(self, request):
        if not (self.app.is_admin(request) or self.app.any(request, 'inv_manager')):
            raise AppError('Only the inventory manager imports opening stock.', status=403)
        import datetime as _dt
        from decimal import Decimal, InvalidOperation
        from django.apps import apps
        from django.db.models import Q
        import openpyxl
        from .models import Warehouse
        f = request.FILES.get('file')
        if f is None:
            raise AppError('Choose the Excel file.', 'file')
        wh = Warehouse.objects.filter(pk=int(request.data.get('warehouse') or 0)).first()
        if wh is None:
            raise AppError('Choose the warehouse.', 'warehouse')
        try:
            on = _dt.date.fromisoformat(str(request.data.get('date') or today()))
        except ValueError:
            raise AppError('The date is not valid.', 'date')
        wb = openpyxl.load_workbook(f, data_only=True, read_only=True)
        ws = wb.active
        it = ws.iter_rows(values_only=True)
        head = [str(x or '').strip().lower() for x in next(it, [])]
        col = {k: next((i for i, h in enumerate(head) if h in names), None) for k, names in (
            ('item', ('item', 'item number', 'item code', 'code', 'number', 'barcode')), ('qty', ('qty', 'quantity')), ('cost', ('unit cost', 'cost', 'unit_cost')),
            ('lot', ('lot', 'batch')), ('serial', ('serial', 'serial number')), ('expiry', ('expiry', 'expiry date')), ('bin', ('bin',)))}
        if col['item'] is None or col['qty'] is None:
            raise AppError('The first row must name the columns: Item, Quantity, Unit cost (and Lot, Serial, Expiry, Bin when used).', 'file')
        P = apps.get_model('ZeoMasters', 'Product')
        rows, errors = [], []
        for n, r in enumerate(it, 2):
            if not r or all(x in (None, '') for x in r):
                continue
            g = lambda k: r[col[k]] if col[k] is not None and col[k] < len(r) else None   # noqa: E731
            code = str(g('item') or '').strip()
            p = P.objects.filter(Q(number__iexact=code) | Q(code__iexact=code) | Q(barcode=code)).first() if code else None
            err = []
            if p is None:
                err.append(f'item "{code}" not found')
            try:
                q = Decimal(str(g('qty')))
                if q <= 0:
                    err.append('quantity must be more than 0')
            except (InvalidOperation, TypeError):
                q = None
                err.append('quantity is not a number')
            try:
                c = Decimal(str(g('cost'))) if g('cost') not in (None, '') else None
            except InvalidOperation:
                c = None
                err.append('unit cost is not a number')
            exp = g('expiry')
            if isinstance(exp, _dt.datetime):
                exp = exp.date()
            elif exp not in (None, ''):
                try:
                    exp = _dt.datetime.strptime(str(exp).strip(), '%d/%m/%Y').date()
                except ValueError:
                    err.append('expiry is not dd/mm/yyyy')
                    exp = None
            else:
                exp = None
            b = Bin.objects.filter(warehouse=wh, code__iexact=str(g('bin') or '').strip()).first() if g('bin') else None
            if g('bin') and b is None:
                err.append(f'bin {g("bin")} is not in {wh.code}')
            if p is not None:
                tr = S.tracking_of(p)
                if tr == 'lot' and not g('lot'):
                    err.append('lot needed')
                if tr == 'serial' and (not g('serial') or (q is not None and q != 1)):
                    err.append('one serial number per row (quantity 1)')
                if c is None and S.method_of(p) != 'standard':
                    err.append('unit cost needed')
            row = {'row': n, 'item': str(p) if p else code, 'qty': float(q) if q is not None else None, 'unit_cost': float(c) if c is not None else None,
                   'lot': str(g('lot') or ''), 'serial': str(g('serial') or ''), 'expiry': exp.isoformat() if exp else None, 'bin': b.code if b else '',
                   'error': '; '.join(err)}
            rows.append((row, p, q, c, exp, b))
            if err:
                errors.append(row)
        out = {'rows': [x[0] for x in rows], 'errors': errors, 'count': len(rows)}
        if str(request.data.get('commit') or '') not in ('1', 'true') or errors or not rows:
            out['committed'] = False
            return Response(out)
        from .app import PREFIX
        from .models import OperationType, StockDoc, StockDocLine
        op = OperationType.objects.filter(warehouse=wh, kind='opening', is_active=True).first()
        d = StockDoc.objects.create(kind='opening', date=on, document_date=on, warehouse=wh, operation=op, reference=f'Import {f.name}'[:80], branch_id=wh.branch_id,
                                    created_by=request.user if getattr(request.user, 'is_authenticated', False) else None)
        d.number = self.app.next_number(f'op_{op.pk}', op.code) if op is not None and op.code else self.app.next_number('doc_opening', PREFIX['opening'])
        d.save(update_fields=['number'])
        for row, p, q, c, exp, b in rows:
            StockDocLine.objects.create(doc=d, product=p, uom=p.uom, qty=q, unit_cost=c, lot=row['lot'], serial=row['serial'], expiry=exp, bin=b)
        out.update({'committed': True, 'doc': d.pk, 'number': d.number})
        return Response(out)
