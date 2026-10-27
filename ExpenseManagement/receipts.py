"""v1.50.0 – read a receipt photo / PDF on this server (Tesseract OCR, free; nothing is sent outside): total amount, date,
VAT, the seller's tax number (TRN / VAT no.), currency and merchant. The employee checks the filled fields before saving;
the expense keeps what the receipt said so the approver sees when the claimed amount differs from the receipt."""
import re
from datetime import date
from decimal import Decimal, InvalidOperation

CURRENCIES = ('AED', 'SAR', 'QAR', 'OMR', 'KWD', 'BHD', 'USD', 'EUR', 'GBP', 'INR')
CUR_WORDS = {'DHS': 'AED', 'DH': 'AED', 'DIRHAM': 'AED', 'RIYAL': 'SAR', 'SR': 'SAR', 'QR': 'QAR', 'RO': 'OMR', 'KD': 'KWD', 'BD': 'BHD',
             'RS': 'INR', '₹': 'INR', '$': 'USD', '€': 'EUR', '£': 'GBP', 'د.إ': 'AED', 'ر.س': 'SAR'}
AMOUNT = r'(\d{1,3}(?:[,\s]\d{3})*(?:[.,]\d{2,3})|\d+[.,]\d{2,3}|\d+)'
TOTAL_WORDS = [r'grand\s*total', r'total\s*(?:amount|due|payable)?\s*(?:incl\.?|including|inc\.?)?\s*(?:vat|tax)?', r'amount\s*(?:due|payable|paid)',
               r'net\s*(?:amount|payable|total)', r'balance\s*due', r'الإجمالي', r'المجموع', r'المبلغ\s*الإجمالي']
VAT_WORDS = r'(?:vat|tax|ضريبة)'
SKIP_MERCHANT = re.compile(r'(tax\s*invoice|simplified|invoice|receipt|cash\s*memo|bill|tel|phone|www\.|@|trn|vat|date|time|^\W*$)', re.I)


def _num(s):
    s = (s or '').strip().replace(' ', '')
    if re.search(r',\d{2}$', s) and '.' not in s:   # 12,50 = 12.50
        s = s.replace(',', '.')
    s = s.replace(',', '')
    try:
        v = Decimal(s)
        return v if Decimal('0') < v < Decimal('10000000') else None
    except InvalidOperation:
        return None


def _amounts(line):
    return [v for v in (_num(m) for m in re.findall(AMOUNT, line)) if v is not None]


def total(lines):
    """The total of the receipt: the last amount on a 'total' line (grand total first), else the biggest amount."""
    for w in TOTAL_WORDS:
        best = None
        for ln in lines:
            if re.search(w, ln, re.I) and not re.search(r'sub\s*-?\s*total|before|excl|without', ln, re.I):
                a = _amounts(ln)
                if a:
                    best = a[-1]
        if best is not None:
            return best
    every = [a for ln in lines if not re.search(r'trn|tel|phone|mob|card|no\.|#', ln, re.I) for a in _amounts(ln) if a != a.to_integral() or a < 100000]
    return max(every) if every else None


def vat(lines, tot):
    for ln in lines:
        if re.search(VAT_WORDS, ln, re.I) and not re.search(r'trn|reg|no\.|number|incl|total', ln, re.I):
            a = [x for x in _amounts(ln) if x not in (Decimal('5'), Decimal('15'), Decimal('5.00'), Decimal('15.00'))]
            if a and (tot is None or a[-1] < tot):
                return a[-1]
    return None


def tax_number(text):
    m = re.search(r'(?:TRN|VAT\s*(?:Reg(?:istration)?\.?\s*)?(?:No|Number|#)?|Tax\s*Reg(?:istration)?\.?\s*No\.?|الرقم\s*الضريبي)\s*[:.\-]?\s*(\d[\d\s]{11,18}\d)',
                  text, re.I)
    if m:
        return re.sub(r'\s', '', m.group(1))[:20]
    m = re.search(r'\b(1\d{14}|3\d{14})\b', text)   # UAE TRN 100… / KSA VAT 3…
    return m.group(1) if m else ''


def currency(text, default=''):
    up = text.upper()
    for c in CURRENCIES:
        if re.search(rf'\b{c}\b', up):
            return c
    for w, c in CUR_WORDS.items():
        if (w.isalpha() and re.search(rf'\b{w}\b', up)) or (not w.isalpha() and w in text):
            return c
    return default


def receipt_date(text):
    from AISettings.docread import all_dates
    today = date.today()
    ds = [x for x in all_dates(text) if x <= today and (today - x).days < 800]
    if ds:
        return ds[0]
    m = re.search(r'\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2})\b', text)   # 03/10/26
    if m:
        try:
            v = date(2000 + int(m.group(3)), int(m.group(2)), int(m.group(1)))
            return v if v <= today else None
        except ValueError:
            return None
    return None


def merchant(lines):
    for ln in lines[:8]:
        t = ln.strip(' -*=:|')
        if len(re.sub(r'[^A-Za-z؀-ۿ]', '', t)) >= 3 and not SKIP_MERCHANT.search(t) and not re.search(r'\d{4,}', t):
            return t[:80]
    return ''


def parse(text, default_currency=''):
    lines = [re.sub(r'\s+', ' ', ln).strip() for ln in (text or '').splitlines() if ln.strip()]
    tot = total(lines)
    v = vat(lines, tot)
    return {'amount': str(tot) if tot is not None else '', 'vat': str(v) if v is not None else '', 'tax_number': tax_number(text or ''),
            'currency': currency(text or '', default_currency), 'date': (receipt_date(text or '') or '') and receipt_date(text or '').isoformat(),
            'merchant': merchant(lines)}


def read(name, data, default_currency=''):
    from AISettings.docread import IMAGE_EXT, _ocr_image, extract_text, ocr_available
    low = (name or '').lower()
    if low.endswith(IMAGE_EXT) or data[:3] == b'\xff\xd8\xff' or data[:8] == b'\x89PNG\r\n\x1a\n':
        text = _ocr_image(data, psm='4')   # a receipt is one column of lines with the amounts on the right
        source = 'ocr' if text.strip() else ''
    else:
        text, source = extract_text(name, data)
    out = parse(text, default_currency)
    out.update({'source': source, 'ocr': ocr_available(), 'text': (text or '')[:3000],
                'found': [k for k in ('amount', 'date', 'vat', 'tax_number', 'merchant') if out.get(k)]})
    return out
