"""Reading a document: its text (PDF, scan / photo by OCR, Excel, CSV, Word, e-mail, pasted text), then its fields –
by the company's AI when it is switched on for documents (Settings › AI settings), else by the built-in reader."""
import base64
import csv
import email
import io
import json
import logging
import re
from datetime import date, datetime
from email import policy

log = logging.getLogger(__name__)
MAX_TEXT = 24000
HINT = {'sales_quote': 'a customer enquiry / request for quotation sent TO us – the partner is the CUSTOMER',
        'sales_order': "a customer's purchase order sent TO us – the partner is the CUSTOMER; reference = the customer's PO number",
        'purchase_quote': "a supplier's quotation / offer sent TO us – the partner is the SUPPLIER; reference = the supplier's quote number",
        'purchase_request': 'an internal purchase request or a list of items we need – the partner (if any) is a SUPPLIER',
        'price_list': "a supplier's PRICE LIST sent TO us – the partner is the SUPPLIER; there may be no quantities: put the minimum / "
                      "'from' quantity of a price break in min_qty and leave qty empty; valid_from / valid_until are the list's validity"}


# ------------------------------------------------------------------ text of a file
def _cell(v):
    if v is None:
        return ''
    if isinstance(v, (datetime, date)):
        return v.strftime('%d/%m/%Y')
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def sheet_rows(name, data):
    """rows of an Excel / CSV file (lists of cell texts) – None for other files"""
    low = (name or '').lower()
    if low.endswith(('.xlsx', '.xlsm')):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
        rows = []
        for ws in wb.worksheets[:3]:
            for r in ws.iter_rows(values_only=True):
                cells = [_cell(v) for v in r]
                if any(cells):
                    rows.append(cells)
        return rows
    if low.endswith('.xls'):
        try:
            import xlrd
            wb = xlrd.open_workbook(file_contents=data)
            return [[_cell(c) for c in ws.row_values(i)] for ws in wb.sheets()[:3] for i in range(ws.nrows) if any(ws.row_values(i))]
        except Exception:   # noqa: BLE001
            return None
    if low.endswith(('.csv', '.tsv')):
        txt = data.decode('utf-8-sig', 'replace')
        dialect = csv.excel_tab if low.endswith('.tsv') or txt.count('\t') > txt.count(',') else csv.excel
        return [[c.strip() for c in r] for r in csv.reader(io.StringIO(txt), dialect) if any(c.strip() for c in r)]
    return None


def _eml(data):
    m = email.message_from_bytes(data, policy=policy.default)
    head = '\n'.join(f'{k}: {m.get(k, "")}' for k in ('From', 'To', 'Subject', 'Date') if m.get(k))
    body = m.get_body(preferencelist=('plain', 'html'))
    txt = body.get_content() if body is not None else ''
    if body is not None and body.get_content_type() == 'text/html':
        txt = re.sub(r'<[^>]+>', ' ', txt)
    atts = []
    for part in m.iter_attachments():
        fn = part.get_filename() or ''
        try:
            atts.append((fn, part.get_content() if isinstance(part.get_content(), bytes) else part.get_content().encode()))
        except Exception:   # noqa: BLE001
            pass
    return head + '\n\n' + txt, atts


def file_text(name, data):
    """(text, rows or None, how) – how: pdf, ocr, sheet, word, email, text, '' (nothing readable)"""
    low = (name or '').lower()
    rows = sheet_rows(name, data)
    if rows is not None:
        return '\n'.join(' | '.join(c for c in r) for r in rows)[:MAX_TEXT], rows, 'sheet'
    if low.endswith('.docx'):
        from docx import Document
        d = Document(io.BytesIO(data))
        parts = [p.text for p in d.paragraphs if p.text.strip()]
        for t in d.tables:
            for r in t.rows:
                parts.append(' | '.join(c.text.strip() for c in r.cells))
        return '\n'.join(parts)[:MAX_TEXT], None, 'word'
    if low.endswith('.eml'):
        txt, atts = _eml(data)
        for fn, b in atts[:3]:
            t2, _, _ = file_text(fn, b)
            if t2:
                txt += f'\n\n--- attachment {fn} ---\n{t2}'
        return txt[:MAX_TEXT], None, 'email'
    if low.endswith(('.txt', '.text', '.md')):
        return data.decode('utf-8', 'replace')[:MAX_TEXT], None, 'text'
    if data[:5] == b'%PDF-':
        t = _pdftotext(data)   # keeps the columns of a table on one line
        if len(re.sub(r'\s', '', t)) >= 20:
            return t[:MAX_TEXT], _vertical_rows(t), 'pdf'
    from AISettings.docread import extract_text
    t, src = extract_text(name, data)
    return (t or '')[:MAX_TEXT], _vertical_rows(t or ''), ('pdf' if src == 'text' else src)


def _pdftotext(data):
    import shutil
    import subprocess
    import tempfile
    exe = shutil.which('pdftotext')
    if not exe:
        return ''
    with tempfile.NamedTemporaryFile(suffix='.pdf') as f:
        f.write(data)
        f.flush()
        try:
            r = subprocess.run([exe, '-layout', '-l', '10', f.name, '-'], capture_output=True, timeout=60)
            return r.stdout.decode('utf-8', 'replace')
        except Exception:   # noqa: BLE001
            return ''


def _vertical_rows(text):
    """a table written one cell per line (some PDF readers): the header cells, then the cells of each row, are put back into rows"""
    ls = [x.strip() for x in text.splitlines()]
    for i in range(len(ls)):
        n = 0
        while i + n < len(ls) and n < 9 and _head_map_single(ls[i + n]):
            n += 1
        if n >= 3 and _head_map(ls[i:i + n]):
            rows = [ls[i:i + n]]
            j = i + n
            while j + n <= len(ls):
                row = ls[j:j + n]
                if re.search(r'(?i)\b(sub ?total|total|vat)\b', row[0]):
                    break
                rows.append(row)
                j += n
            return rows if len(rows) > 1 else None
    return None


def _head_map_single(c):
    c0 = re.sub(r'\s+', ' ', (c or '').strip().lower()).strip(':')
    return any(c0 in w or c0 == x for w in HEAD.values() for x in w) and len(c0) < 25


def mask(text):
    """bank account and card numbers never go to the AI"""
    t = re.sub(r'\bAE\d{2}(?:\s?\d){19}\b', '[IBAN]', text or '')
    t = re.sub(r'\b[A-Z]{2}\d{2}[A-Z0-9 ]{11,30}\b', lambda m: '[IBAN]' if len(re.sub(r'\s', '', m.group(0))) >= 15 else m.group(0), t)
    t = re.sub(r'\b(?:\d[ -]?){13,19}\b', lambda m: '[CARD]' if _luhn(re.sub(r'\D', '', m.group(0))) else m.group(0), t)
    return t


def _luhn(d):
    if not 13 <= len(d) <= 19:
        return False
    s = 0
    for i, ch in enumerate(reversed(d)):
        n = int(ch) * (2 if i % 2 else 1)
        s += n - 9 if n > 9 else n
    return s % 10 == 0


# ------------------------------------------------------------------ the company's AI
SCHEMA = '''{"partner": {"name": "", "email": "", "phone": "", "trn": "", "address": "", "contact_person": ""},
 "reference": "", "date": "YYYY-MM-DD", "valid_until": "YYYY-MM-DD", "delivery_date": "YYYY-MM-DD", "currency": "AED",
 "payment_terms": "", "notes": "",
 "valid_from": "YYYY-MM-DD",
 "lines": [{"code": "", "description": "", "qty": 0, "min_qty": null, "unit": "", "price": null, "discount_pct": 0, "vat_pct": null, "amount": null}],
 "totals": {"net": null, "vat": null, "total": null}, "lead_days": null}'''


def ai_ready(request):
    try:
        from AISettings.service import feature_mode
        return feature_mode('documents')[0] == 'external'
    except Exception:   # noqa: BLE001
        return False


def ai_extract(request, kind, text, name='', data=None):
    """the fields as JSON, from the text – or, when there is no text, from the scan / photo itself (Anthropic)"""
    from AISettings.service import chat, settings_row
    system = ('You read business documents for a UAE ERP and return ONLY JSON in this form:\n' + SCHEMA +
              '\nRules: one entry in "lines" per item line of the document, in order; "code" is the item / part / SKU / model number as written '
              '(empty if none); "qty" a number; "price" the unit price before discount and VAT (null if the document has none); '
              'numbers without thousands separators; dates as YYYY-MM-DD (UAE documents write dates day first); '
              'do not invent anything that is not in the document; leave a field empty when unsure. '
              'Our own company details (the "to" / "bill to us" side) are NOT the partner.')
    content = [{'type': 'text', 'text': f'Document type: {HINT.get(kind, kind)}.\nFile: {name}\n\n' + mask(text)}]
    how = 'ai'
    if not text.strip() and data:
        if settings_row().provider != 'anthropic':
            raise ValueError('The document has no text and the AI provider cannot read scans.')
        low = (name or '').lower()
        if data[:5] == b'%PDF-':
            content = [{'type': 'document', 'source': {'type': 'base64', 'media_type': 'application/pdf', 'data': base64.b64encode(data).decode()}},
                       {'type': 'text', 'text': f'Document type: {HINT.get(kind, kind)}. Read it.'}]
        else:
            mt = 'image/png' if data[:8] == b'\x89PNG\r\n\x1a\n' else ('image/webp' if low.endswith('.webp') else 'image/jpeg')
            content = [{'type': 'image', 'source': {'type': 'base64', 'media_type': mt, 'data': base64.b64encode(data).decode()}},
                       {'type': 'text', 'text': f'Document type: {HINT.get(kind, kind)}. Read it.'}]
        how = 'ai+vision'
    r = chat(request, 'documents', system, [{'role': 'user', 'content': content}], max_tokens=4000)
    t = '\n'.join(b.get('text', '') for b in (r or {}).get('content') or [] if b.get('type') == 'text')
    m = re.search(r'```(?:json)?\s*(\{.*\})\s*```', t, re.S) or re.search(r'(\{.*\})', t, re.S)
    if not m:
        raise ValueError('The AI did not answer in the expected form.')
    return json.loads(m.group(1)), how


# ------------------------------------------------------------------ the built-in reader
NUM = r'-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?'
HEAD = {'code': ('code', 'item code', 'item no', 'item #', 'sku', 'part', 'part no', 'part number', 'model', 'ref', 'article', 'product code', 'material'),
        'description': ('description', 'item', 'product', 'item description', 'particulars', 'details', 'name', 'material description'),
        'qty': ('qty', 'quantity', 'qnty', 'qty.', 'no. of units', 'units', 'pcs'),
        'unit': ('unit', 'uom', 'u/m', 'um'),
        'price': ('price', 'unit price', 'rate', 'unit rate', 'u/price', 'unit cost', 'price/unit', 'cost'),
        'discount_pct': ('disc', 'discount', 'disc %', 'discount %'),
        'min_qty': ('moq', 'min qty', 'min. qty', 'minimum qty', 'minimum', 'from qty', 'qty from', 'break qty', 'price break', 'min order'),
        'amount': ('amount', 'total', 'line total', 'net amount', 'value', 'net')}


def _num(s):
    if s is None:
        return None
    s = str(s).replace('AED', '').replace('aed', '').strip()
    m = re.search(NUM, s)
    return float(m.group(0).replace(',', '')) if m else None


def _head_map(row):
    out = {}
    for i, c in enumerate(row):
        c0 = re.sub(r'\s+', ' ', (c or '').strip().lower()).strip(':')
        if not c0:
            continue
        for key, words in HEAD.items():
            if key not in out and (c0 in words or any(c0.startswith(w + ' ') or c0 == w for w in words)):
                out[key] = i
                break
    return out if (('qty' in out or 'price' in out) and ('description' in out or 'code' in out)) else None


def _table_lines(rows):
    hm, start = None, 0
    for i, r in enumerate(rows[:40]):
        hm = _head_map(r)
        if hm:
            start = i + 1
            break
    if not hm:
        return []
    lines = []
    for r in rows[start:]:
        g = lambda k: r[hm[k]] if k in hm and hm[k] < len(r) else ''   # noqa: E731
        qty = _num(g('qty')) if 'qty' in hm else (1.0 if _num(g('price')) is not None else None)   # a price list: no quantities
        desc, code = g('description'), g('code')
        if qty is None or qty <= 0 or not (desc or code):
            if re.search(r'\b(sub ?total|total|vat|grand)\b', ' '.join(r).lower()):
                break
            continue
        lines.append({'code': code, 'description': desc or code, 'qty': qty, 'unit': g('unit'), 'price': _num(g('price')),
                      'discount_pct': _num(g('discount_pct')) or 0, 'amount': _num(g('amount')), 'min_qty': _num(g('min_qty'))})
    return lines


UNITS = r'(?:nos?|no\.|pcs?|pieces?|units?|ea|each|sets?|boxes|box|rolls?|coils?|cyl(?:inders?)?|kg|kgs|m|mtrs?|meters?|ltrs?|litres?|liters?|pairs?|lengths?|bags?|cartons?|ctns?|drums?|tins?|packs?|pkts?)'
SKIP = re.compile(r'(?i)^\s*(?:[a-z .]{0,25}\b(?:date|ref(?:erence)?|no\.?|number|po|lpo|quotation|quote|invoice|delivery|validity|valid|payment|terms|attn|attention|subject|tel|phone|fax|mob(?:ile)?|e-?mail|trn|vat|total|sub ?total|grand total|discount|from|to|cc|regards|dear|hello|hi|thanks?|thank you)\b[^:]{0,20}:|.*\b(?:sub ?total|grand total|total before|total after|vat \d|amount in words)\b)')
TAIL = re.compile(r'^(?P<body>.+?)\s+(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>' + UNITS + r'\.?)?\s+(?:AED\s*)?(?P<price>\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)(?:\s+(?:AED\s*)?(?P<amount>\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?))?\s*$', re.I)
CODE = re.compile(r'^(?=[A-Za-z0-9./\-]*\d)(?=[A-Za-z0-9./\-]*[A-Za-z])[A-Za-z0-9][A-Za-z0-9./\-]{2,}$')


def _split_code(body):
    body = body.strip(' =:-|–')
    tok = body.split()
    if len(tok) > 1 and CODE.match(tok[0]):
        return tok[0], ' '.join(tok[1:]).strip(' =:-|–')
    return '', body


def _text_lines(text):
    """'5 x Daikin split AC 2 ton', 'Digital thermostat: 5 nos', '3 coils copper pipe 3/8 inch',
    'AC-SPL-1.5 Split AC 1.5 ton 4 Nos 2,850.00 11,400.00' (qty, unit, price, amount from the right)"""
    rows = [re.split(r'\t|\s{2,}|\s\|\s', ln.strip()) for ln in text.splitlines() if ln.strip()]
    lines = _table_lines(rows)
    if lines:
        return lines
    for raw in text.splitlines():
        s = re.sub(r'\s+', ' ', raw.strip(' -•*\t>'))
        if not s or len(s) > 220 or SKIP.match(s) or '@' in s or not re.search(r'[A-Za-z]{3,}', s):
            continue
        s = re.sub(r'^\d{1,3}[.)]\s+', '', s)   # "1. ", "2) " line numbers
        m = re.match(r'(?i)^(\d+(?:\.\d+)?)\s*(?:x|×|\*)\s*(.{3,})$', s) or re.match(r'(?i)^(\d+(?:\.\d+)?)\s*(' + UNITS + r')\.?\s+(?:of\s+)?(.{3,})$', s)
        if m:
            g = m.groups()
            code, desc = _split_code(g[-1].strip(' .,'))
            lines.append({'code': code, 'description': desc, 'qty': float(g[0]), 'unit': g[1] if len(g) == 3 else '', 'price': None, 'discount_pct': 0})
            continue
        m = re.match(r'(?i)^(.{3,}?)\s*(?:[:\-–=]|x|×)\s*(\d+(?:\.\d+)?)\s*(' + UNITS + r')?\.?$', s)
        if m:
            code, desc = _split_code(m.group(1).strip())
            lines.append({'code': code, 'description': desc, 'qty': float(m.group(2)), 'unit': m.group(3) or '', 'price': None, 'discount_pct': 0})
            continue
        m = TAIL.match(s)
        if m:
            qty, price = float(m.group('qty')), _num(m.group('price'))
            amount = _num(m.group('amount')) if m.group('amount') else None
            if amount is not None and price and abs(qty * price - amount) > max(1, amount * 0.02):
                continue   # the numbers are not qty × price = amount: not an item line
            code, desc = _split_code(m.group('body').strip(' |:'))
            if qty > 0 and desc and qty < 1000000:
                lines.append({'code': code, 'description': desc, 'qty': qty, 'unit': m.group('unit') or '', 'price': price, 'discount_pct': 0, 'amount': amount})
    return lines


def _date(s):
    for f in ('%d/%m/%Y', '%d-%m-%Y', '%d.%m.%Y', '%Y-%m-%d', '%d/%m/%y', '%d %b %Y', '%d %B %Y', '%d-%b-%Y', '%b %d, %Y', '%B %d, %Y'):
        try:
            return datetime.strptime(s.strip(), f).date().isoformat()
        except ValueError:
            pass
    return ''


def builtin_extract(kind, text, rows=None):
    t = text or ''
    out = {'partner': {}, 'lines': _table_lines(rows) if rows else _text_lines(t)}
    m = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', t)
    if m:
        out['partner']['email'] = m.group(0)
    m = re.search(r'(?i)\bfrom:\s*"?([^"<\n]+?)"?\s*<', t) or re.search(r'(?im)^\s*(?:customer|client|supplier|vendor|company|from)\s*(?:name)?\s*[:\-]\s*(.+)$', t)
    if m:
        out['partner']['name'] = m.group(1).strip()[:120]
    m = re.search(r'(?i)\bTRN\s*(?:no\.?|number)?\s*[:\-]?\s*(\d{15})', t) or re.search(r'\b(100\d{12})\b', t)
    if m:
        out['partner']['trn'] = m.group(1)
    m = re.search(r'(?i)(?:tel|phone|mob(?:ile)?)\.?\s*[:\-]?\s*(\+?[\d][\d\s()-]{7,}\d)', t)
    if m:
        out['partner']['phone'] = re.sub(r'[^\d+]', '', m.group(1))
    ref_words = {'sales_order': r'(?:P\.?O\.?|purchase order|LPO|order)', 'purchase_quote': r'(?:quotation|quote|offer|ref)',
                 'sales_quote': r'(?:RFQ|enquiry|inquiry|ref)', 'purchase_request': r'(?:request|PR|ref)'}.get(kind, r'ref')
    m = re.search(rf'(?i)\b{ref_words}\s*(?:no\.?|number|#|ref\.?)?\s*[:\-#]\s*((?=[\w/\-.]*\d)[A-Z0-9][\w/\-.]{{1,40}})', t) or \
        re.search(r'(?i)\b(?:ref(?:erence)?|po|lpo|quotation|quote|enquiry|rfq)\s*(?:no\.?|number|#)?\s*[:\-#]\s*((?=[\w/\-.]*\d)[A-Z0-9][\w/\-.]{1,40})', t)
    if m:
        out['reference'] = m.group(1).strip('.')
    m = re.search(r'(?i)\bdate\s*[:\-]?\s*(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}|\d{1,2}[ \-][A-Za-z]{3,9}[ \-]\d{4})', t)
    if m:
        out['date'] = _date(m.group(1))
    m = re.search(r'(?i)(?:valid|effective|prices?)\s+from\s*[:\-]?\s*(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4})', t)
    if m:
        out['valid_from'] = _date(m.group(1))
    m = re.search(r'(?i)valid(?:ity)?\s*(?:until|till|to|up to|upto)\s*[:\-]?\s*(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4})', t) or \
        re.search(r'(?i)valid(?:ity)?\s*[:\-]\s*(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4})', t)
    if m:
        out['valid_until'] = _date(m.group(1))
    m = re.search(r'(?i)\b(USD|EUR|GBP|SAR|INR|AED|OMR|QAR|KWD|BHD)\b', t)
    out['currency'] = m.group(1).upper() if m else ''
    m = re.search(r'(?i)delivery\s*[:\-]?\s*(\d{1,3})\s*(?:working\s+)?days', t)
    if m:
        out['lead_days'] = int(m.group(1))
    return out


def read(request, kind, name, data=None, text=''):
    """(text, extracted fields, reader used, note)"""
    rows, how = None, 'text'
    if data is not None:
        text, rows, how = file_text(name, data)
    note = ''
    if ai_ready(request):
        try:
            ex, used = ai_extract(request, kind, text, name, data if not text.strip() else None)
            if not ex.get('lines') and rows:
                ex['lines'] = _table_lines(rows)
            return text, ex, used, note
        except Exception as e:   # noqa: BLE001
            log.warning('doc capture: AI reading failed: %s', e)
            note = f'The AI could not read it ({str(e)[:120]}) – the built-in reader was used.'
    elif not text.strip():
        return text, {'partner': {}, 'lines': []}, 'built-in', ('Nothing readable in the file. Switch on the company AI for documents (Settings › AI settings) to read '
                                                              'scans and photos, or paste the text.')
    return text, builtin_extract(kind, text, rows), 'built-in', note or ('' if ai_ready(request) else
                                                                           'Read by the built-in reader. With the company AI switched on for documents, free-form e-mails and scans are read better.')
