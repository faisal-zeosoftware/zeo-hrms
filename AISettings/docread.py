"""v1.16.0 – "Fill from document": read an uploaded employee document and suggest its type, number and dates.

Built-in, free and on the server (default):
  1. text of text-based PDFs (pypdf);
  2. OCR with the Tesseract program when it is installed on the server (apt install tesseract-ocr; optional) for
     photos / scans (JPG, PNG, …) and PDFs without text (pages rendered with pdftoppm when present, else the images
     embedded in the PDF);
  3. the machine-readable zone (MRZ) of passports, ID cards (Emirates ID back side) and visas, verified with its check digits;
  4. patterns: Emirates ID number 784-YYYY-NNNNNNN-C (check digit), visa file / UID numbers, work permit / labour card
     numbers, labelled issue / expiry / birth dates;
  5. keywords in the file name and the text for the document type, matched to the company's document types.
Optional external AI (Settings → AI settings → Documents, only when allowed by the server and the company): only the
extracted TEXT (at most 6,000 characters, never the file) is sent; values verified by MRZ check digits are kept.

Nothing is saved here – the screen shows suggestions and the user decides what to fill.
"""
import io
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from datetime import date

from Translations.i18n import tr

log = logging.getLogger(__name__)

MAX_BYTES = 10 * 1024 * 1024
MAX_PAGES = 3
OCR_TIMEOUT = 40
IMAGE_EXT = ('.jpg', '.jpeg', '.png', '.tif', '.tiff', '.bmp', '.webp', '.gif', '.heic', '.heif')

MONTHS = {m: i + 1 for i, m in enumerate(['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'])}

# document kinds: label, words in the text / file name (weights), words in the company's document type names
KINDS = {
    'passport': ('Passport', [('passport', 3), ('passeport', 3), ('place of birth', 1), ('p<', 2), ('جواز', 3)], ['passport']),
    'emirates_id': ('Emirates ID', [('emirates id', 4), ('identity card', 3), ('id card', 2), ('federal authority for identity', 4),
                                    ('resident identity', 3), ('eid', 2), ('emiratesid', 4), ('emirates_id', 4), ('بطاقة الهوية', 4), ('هوية', 2)],
                    ['emirates id', 'emirates-id', 'eid', 'national id', 'identity', 'id card']),
    'visa': ('Residence visa', [('residence', 3), ('visa', 3), ('entry permit', 3), ('residency', 3), ('file no', 2), ('u.i.d', 2),
                                ('uid no', 2), ('إقامة', 3), ('تأشيرة', 3)], ['visa', 'residen', 'iqama', 'entry permit']),
    'labour_card': ('Labour card / work permit', [('labour card', 4), ('labor card', 4), ('labour_card', 4), ('work permit', 4),
                                                   ('work_permit', 4), ('mohre', 3), ('human resources', 2), ('person code', 3),
                                                   ('بطاقة عمل', 4), ('تصريح عمل', 4)], ['labour', 'labor', 'work permit']),
    'driving_licence': ('Driving licence', [('driving licen', 4), ('driving_licen', 4), ('driver', 2), ('traffic', 2), ('رخصة قيادة', 4)],
                        ['driving', 'licence', 'license']),
    'insurance': ('Health insurance card', [('insurance', 3), ('daman', 2), ('policy no', 2), ('member id', 2), ('تأمين', 3)],
                  ['insurance', 'medical']),
}

LABELS = {
    'issue': r'(?:date\s*of\s*issue|issue\s*date|issuing\s*date|date\s*issued|issued\s*on|issued|تاريخ\s*الإصدار)',
    'expiry': r'(?:date\s*of\s*expiry|expiry\s*date|expiration\s*date|date\s*of\s*expiration|expires\s*on|expiry|valid\s*until|valid\s*till|valid\s*up\s*to|تاريخ\s*الانتهاء)',
    'birth': r'(?:date\s*of\s*birth|birth\s*date|d\.?o\.?b\.?|تاريخ\s*الميلاد)',
}
DATE_RE = (r'(\d{1,2})\s*[/\-.]\s*(\d{1,2})\s*[/\-.]\s*(\d{4})'               # 31/12/2030
           r'|(\d{4})\s*[/\-.]\s*(\d{1,2})\s*[/\-.]\s*(\d{1,2})'               # 2030-12-31
           r'|(\d{1,2})[\s\-/]*([A-Za-z]{3})[A-Za-z]*\.?[\s\-/,]*(\d{4})')      # 31 DEC 2030 / 31-Dec-2030


# ------------------------------------------------------------------ tools available on this server
def ocr_available():
    return bool(shutil.which('tesseract'))


def _ocr_image(img_bytes):
    """Text of an image with the Tesseract program ('' when it is not installed or fails)."""
    exe = shutil.which('tesseract')
    if not exe:
        return ''
    try:
        from PIL import Image, ImageOps
        try:
            import pillow_heif   # noqa: F401 – HEIC photos from phones, when installed
            pillow_heif.register_heif_opener()
        except Exception:
            pass
        im = Image.open(io.BytesIO(img_bytes))
        im = ImageOps.exif_transpose(im).convert('L')
        if im.width < 1600:   # small photos read better enlarged
            f = 1600 / max(im.width, 1)
            im = im.resize((int(im.width * f), int(im.height * f)))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'page.png')
            im.save(p)
            r = subprocess.run([exe, p, 'stdout', '--psm', '3'], capture_output=True, timeout=OCR_TIMEOUT)
            return r.stdout.decode('utf-8', 'replace')
    except Exception as exc:
        log.warning('OCR failed: %s', exc)
        return ''


def _pdf_text(data):
    try:
        from pypdf import PdfReader
        rd = PdfReader(io.BytesIO(data))
        return '\n'.join((p.extract_text() or '') for p in rd.pages[:MAX_PAGES])
    except Exception as exc:
        log.info('PDF text not readable: %s', exc)
        return ''


def _pdf_ocr(data):
    """OCR of a scanned PDF: pages rendered with pdftoppm (poppler) when present, else the images inside the PDF."""
    if not ocr_available():
        return ''
    out = []
    exe = shutil.which('pdftoppm')
    if exe:
        try:
            with tempfile.TemporaryDirectory() as d:
                src = os.path.join(d, 'in.pdf')
                with open(src, 'wb') as fh:
                    fh.write(data)
                subprocess.run([exe, '-r', '250', '-l', str(MAX_PAGES), '-png', src, os.path.join(d, 'p')], capture_output=True, timeout=OCR_TIMEOUT)
                for name in sorted(os.listdir(d)):
                    if name.endswith('.png'):
                        with open(os.path.join(d, name), 'rb') as fh:
                            out.append(_ocr_image(fh.read()))
            return '\n'.join(out)
        except Exception as exc:
            log.info('pdftoppm failed: %s', exc)
    try:
        from pypdf import PdfReader
        for p in PdfReader(io.BytesIO(data)).pages[:MAX_PAGES]:
            for img in p.images[:2]:
                out.append(_ocr_image(img.data))
    except Exception as exc:
        log.info('PDF images not readable: %s', exc)
    return '\n'.join(out)


def extract_text(name, data):
    """(text, source) – source 'text' (PDF text), 'ocr' or '' (nothing readable)."""
    low = (name or '').lower()
    if data[:5] == b'%PDF-' or low.endswith('.pdf'):
        t = _pdf_text(data)
        if len(re.sub(r'\s', '', t)) >= 20:
            return t, 'text'
        t = _pdf_ocr(data)
        return (t, 'ocr') if t.strip() else ('', '')
    if low.endswith(IMAGE_EXT) or data[:3] == b'\xff\xd8\xff' or data[:8] == b'\x89PNG\r\n\x1a\n':
        t = _ocr_image(data)
        return (t, 'ocr') if t.strip() else ('', '')
    if low.endswith('.txt'):
        return data[:200000].decode('utf-8', 'replace'), 'text'
    return '', ''


# ------------------------------------------------------------------ MRZ (ICAO 9303)
def _cd(s):
    """ICAO check digit (weights 7, 3, 1)."""
    total = 0
    for i, ch in enumerate(s):
        v = int(ch) if ch.isdigit() else (ord(ch) - 55 if 'A' <= ch <= 'Z' else 0)
        total += v * (7, 3, 1)[i % 3]
    return str(total % 10)


_NUM_FIX = str.maketrans({'O': '0', 'Q': '0', 'D': '0', 'I': '1', 'L': '1', 'Z': '2', 'S': '5', 'B': '8', 'G': '6', 'T': '7'})


def _num(s):
    return s.translate(_NUM_FIX)


def _yymmdd(s, birth=False):
    if not re.fullmatch(r'\d{6}', s):
        return None
    yy, mm, dd = int(s[:2]), int(s[2:4]), int(s[4:])
    cur = date.today().year % 100
    year = (1900 + yy if yy > cur else 2000 + yy) if birth else (2000 + yy if yy < 70 else 1900 + yy)
    try:
        return date(year, mm, dd)
    except ValueError:
        return None


def _names(s):
    parts = s.strip('<').split('<<', 1)
    sur = parts[0].replace('<', ' ').strip()
    given = parts[1].replace('<', ' ').strip() if len(parts) > 1 else ''
    return re.sub(r'\s+', ' ', f'{given} {sur}').strip().title()


def mrz_lines(text):
    out = []
    for raw in text.splitlines():
        s = re.sub(r'\s+', '', raw.upper()).replace('«', '<').replace('‹', '<').replace('(', '<').replace('{', '<')
        s = re.sub(r'[^A-Z0-9<]', '', s)
        if re.search(r'[a-z]', raw) or not 28 <= len(s) <= 48:
            continue
        if s.count('<') >= 1 or sum(ch.isdigit() for ch in s) >= 12:
            out.append(s)
    return out


_D = '[0-9OQDIZSBGT]'
TD3_L2 = re.compile(rf'(?P<num>[A-Z0-9<]{{9}})(?P<ncd>{_D}|<)[^A-Z]{{0,2}}?(?P<nat>[A-Z<]{{3}})(?P<dob>{_D}{{6}})(?P<dcd>{_D})'
                    rf'(?P<sex>[MFX<])(?P<exp>{_D}{{6}})(?P<ecd>{_D})')


def parse_mrz(text):
    """Fields of the first MRZ found, with check-digit results. None when there is no MRZ."""
    lines = mrz_lines(text)
    # TD3 (passport) / MRV-A, MRV-B (visa): two lines of 44 / 36 starting with P or V
    for i in range(len(lines) - 1):
        l1, l2 = lines[i], lines[i + 1]
        if l1[:1] in ('P', 'V') and len(l1) >= 36 and len(l2) >= 28:
            n = 44 if len(l1) >= 40 and len(l2) >= 40 else 36
            l1 = (l1 + '<' * n)[:n]
            # fields by structure, so one extra / missing character read by OCR does not shift everything
            m = TD3_L2.match(l2)
            if not m:
                continue
            num, num_cd = m['num'], _num(m['ncd'])
            dob, dob_cd = _num(m['dob']), _num(m['dcd'])
            exp, exp_cd = _num(m['exp']), _num(m['ecd'])
            opt = l2[m.end():m.end() + 14]
            checks = {'number': _cd(num) == num_cd, 'birth': _cd(dob) == dob_cd, 'expiry': _cd(exp) == exp_cd}
            if not checks['number']:   # OCR may read 0 as O inside an all-digit number
                alt = _num(num)
                if _cd(alt) == num_cd:
                    num, checks['number'] = alt, True
            return {'format': 'TD3' if n == 44 else 'MRV-B', 'kind': 'passport' if l1[0] == 'P' else 'visa',
                    'country': l1[2:5].replace('<', ''), 'name': _names(l1[5:]), 'number': num.replace('<', ''),
                    'nationality': m['nat'].replace('<', ''), 'birth': _yymmdd(dob, True), 'sex': m['sex'].replace('<', ''),
                    'expiry': _yymmdd(exp), 'optional': opt.replace('<', ''), 'checks': checks}
    # TD1 (ID card, e.g. the back of the Emirates ID): three lines of 30
    for i in range(len(lines) - 2):
        l1, l2, l3 = (x[:30].ljust(30, '<') for x in lines[i:i + 3])
        if l1[:1] in ('I', 'A', 'C') and re.match(r'[0-9OQDILZSBGT]{6}', l2):
            num, num_cd = l1[5:14], _num(l1[14])
            dob, dob_cd = _num(l2[0:6]), _num(l2[6])
            exp, exp_cd = _num(l2[8:14]), _num(l2[14])
            checks = {'number': _cd(num) == num_cd, 'birth': _cd(dob) == dob_cd, 'expiry': _cd(exp) == exp_cd}
            opt = _num(l1[15:30].replace('<', ''))
            return {'format': 'TD1', 'kind': 'emirates_id' if l1[2:5] == 'ARE' else 'id_card', 'country': l1[2:5].replace('<', ''),
                    'name': _names(l3), 'number': num.replace('<', ''), 'nationality': l2[15:18].replace('<', ''),
                    'birth': _yymmdd(dob, True), 'sex': l2[7].replace('<', ''), 'expiry': _yymmdd(exp), 'optional': opt, 'checks': checks}
    return None


# ------------------------------------------------------------------ patterns in the text
def luhn_ok(digits):
    total = 0
    for i, ch in enumerate(reversed(digits)):
        v = int(ch)
        if i % 2 == 1:
            v *= 2
            if v > 9:
                v -= 9
        total += v
    return total % 10 == 0


def emirates_id(text):
    """'784-1990-1234567-1' when an Emirates ID number is in the text (check digit verified first)."""
    found = []
    for m in re.finditer(r'(?<!\d)784[\s\-]?\d{4}[\s\-]?\d{7}[\s\-]?\d(?!\d)', text):
        d = re.sub(r'\D', '', m.group())
        found.append((luhn_ok(d), f'{d[:3]}-{d[3:7]}-{d[7:14]}-{d[14]}'))
    found.sort(key=lambda x: not x[0])
    return found[0] if found else None


def _date(m):
    g = m.groups()
    try:
        if g[0]:
            return date(int(g[2]), int(g[1]), int(g[0]))
        if g[3]:
            return date(int(g[3]), int(g[4]), int(g[5]))
        mon = MONTHS.get(g[7].lower()[:3])
        return date(int(g[8]), mon, int(g[6])) if mon else None
    except (ValueError, TypeError):
        return None


def labelled_date(text, which):
    """The first date after a label such as 'Date of expiry' (same line or the next one)."""
    for m in re.finditer(LABELS[which], text, re.I):
        tail = text[m.end():m.end() + 60]
        if which == 'issue' and re.match(r'\s*(?:place|by|authority|country)', tail, re.I):
            continue   # "Place of issue" / "Issued by"
        d = re.search(DATE_RE, tail)
        if d:
            v = _date(d)
            if v:
                return v
    return None


def all_dates(text):
    out = []
    for m in re.finditer(DATE_RE, text):
        v = _date(m)
        if v and 1920 <= v.year <= 2100:
            out.append(v)
    return out


NUMBER_LABELS = [
    ('visa', r'(?:file\s*(?:no|number|#)\.?|رقم\s*الملف)\s*[:\-]?\s*(\d{3}\s*/\s*\d{4}\s*/\s*\d{1,9}(?:\s*/\s*\d{1,9})?)'),
    ('visa', r'(?:entry\s*permit\s*(?:no|number)\.?)\s*[:\-]?\s*([0-9/ ]{6,24}\d)'),
    ('labour_card', r'(?:work\s*permit\s*(?:no|number)|labou?r\s*card\s*(?:no|number)|permit\s*(?:no|number)|card\s*(?:no|number))\.?\s*[:\-]?\s*([A-Z0-9]{5,20})'),
    ('passport', r'(?:passport\s*(?:no|number|#)|document\s*(?:no|number))\.?\s*[:\-]?\s*([A-Z0-9]{6,12})'),
    ('driving_licence', r'(?:licen[cs]e\s*(?:no|number))\.?\s*[:\-]?\s*([A-Z0-9\-]{4,20})'),
    ('insurance', r'(?:member\s*id|card\s*(?:no|number)|policy\s*(?:no|number))\.?\s*[:\-]?\s*([A-Z0-9\-/]{4,30})'),
]


def labelled_number(text, kind):
    for k, pat in NUMBER_LABELS:
        if k != kind:
            continue
        m = re.search(pat, text, re.I)
        if m:
            return re.sub(r'\s+', '', m.group(1)).strip('-/')
    return None


def other_numbers(text):
    uid = re.search(r'(?:u\.?\s*i\.?\s*d\.?|unified\s*(?:no|number))\s*(?:no|number)?\.?\s*[:\-]?\s*(\d{6,12})', text, re.I)
    pc = re.search(r'person\s*code\s*[:\-]?\s*(\d{10,16})', text, re.I)
    return {'uid': uid.group(1) if uid else None, 'person_code': pc.group(1) if pc else None}


def holder_name(text):
    m = re.search(r'(?:^|\n)\s*(?:full\s*)?name\s*[:\-]\s*([A-Za-z][A-Za-z.\'\-]*(?: [A-Za-z][A-Za-z.\'\-]*){0,5})', text, re.I)
    if not m:
        return None
    words = []
    for w in m.group(1).split(' '):   # stop at the next label on the same line ("… Passport No")
        if w.lower() in ('passport', 'nationality', 'sex', 'date', 'profession', 'employer', 'sponsor', 'id', 'no', 'gender'):
            break
        words.append(w)
    return ' '.join(words).strip().title() or None


def guess_kind(name, text):
    """(kind, score per kind) from the words of the file name and the text."""
    hay_name = re.sub(r'[_\-.]+', ' ', (name or '').lower())
    hay = (text or '').lower()
    score = {}
    for k, (_, words, _t) in KINDS.items():
        s = 0
        for w, wt in words:
            w2 = w.replace('_', ' ')
            if re.search(r'(?<![a-z])' + re.escape(w2), hay_name):
                s += wt
            if w in hay:
                s += wt
        score[k] = s
    best = max(score, key=score.get)
    return (best if score[best] > 0 else None), score


def match_type(kind, types):
    """The company's document type that fits the kind: [{id, type_name}] → {id, name} or None."""
    if not kind or kind not in KINDS:
        return None
    pats = KINDS[kind][2]
    for t in types:
        n = (t.get('type_name') or '').lower()
        if any(p in n for p in pats):
            return {'id': t['id'], 'name': t['type_name']}
    return None


# ------------------------------------------------------------------ main
def _field(key, label, value, confidence, source, note=''):
    if isinstance(value, date):
        value = value.isoformat()
    return {'key': key, 'label': label, 'value': value, 'confidence': confidence, 'source': source, 'note': note}


def analyse(name, text, types, source=''):
    """Suggestions from the file name and its text. Pure function – no network, no saving."""
    mrz = parse_mrz(text) if text else None
    kind, scores = guess_kind(name, text)
    eid = emirates_id(text) if text else None
    if mrz and mrz['kind'] in KINDS:
        kind = mrz['kind']
    elif eid and eid[0] and scores.get(kind or '', 0) < 4 and kind not in ('visa', 'labour_card'):
        kind = 'emirates_id'
    fields, notes = [], []
    number = issue = expiry = birth = None
    conf = {}
    src = 'MRZ' if mrz else ('text' if source == 'text' else 'OCR' if source == 'ocr' else 'file name')

    if mrz:
        ok = mrz['checks']
        if kind == 'emirates_id':
            d = re.sub(r'\D', '', mrz['optional'])
            if len(d) == 15 and d.startswith('784'):
                number, conf['number'] = f'{d[:3]}-{d[3:7]}-{d[7:14]}-{d[14]}', ('high' if luhn_ok(d) else 'medium')
        if not number:
            number, conf['number'] = mrz['number'], 'high' if ok['number'] else 'low'
        expiry, conf['expiry'] = mrz['expiry'], 'high' if ok['expiry'] else 'low'
        birth = mrz['birth'] if ok['birth'] else None
        if not all(ok.values()):
            notes.append(tr('Some check digits of the machine-readable zone do not match – compare the values with the document.'))
    if not number and eid and kind == 'emirates_id':
        number, conf['number'] = eid[1], 'high' if eid[0] else 'low'
    if not number and kind:
        n = labelled_number(text or '', kind)
        if n:
            number, conf['number'] = n, 'medium'
    if text:
        if not expiry:
            expiry = labelled_date(text, 'expiry')
            conf['expiry'] = 'medium'
        issue = labelled_date(text, 'issue')
        conf['issue'] = 'medium'
        birth = birth or labelled_date(text, 'birth')
        if not expiry or not issue:   # unlabelled dates: earliest future = expiry, latest past (not the birth date) = issue
            ds = sorted(set(all_dates(text)) - {birth, issue, expiry})
            today = date.today()
            if not expiry:
                fut = [d for d in ds if d > today]
                if fut:
                    expiry, conf['expiry'] = fut[-1], 'low'
            if not issue:
                past = [d for d in ds if d <= today and (not birth or d.year - birth.year >= 15)]
                if past:
                    issue, conf['issue'] = past[-1], 'low'

    t = match_type(kind, types)
    if t:
        fields.append(_field('document_type', 'Document type', t['id'], 'high' if mrz or (scores.get(kind, 0) >= 4) else 'medium', src, t['name']))
    elif kind:
        notes.append(tr('The document looks like a {0}, but the company has no document type with that name. '
                        'Add it in Settings → Document types or choose the type yourself.', tr(KINDS[kind][0]).lower()))
    if number:
        fields.append(_field('emp_doc_number', 'Document number', number, conf.get('number', 'medium'), src))
    if issue:
        fields.append(_field('emp_doc_issued_date', 'Issue date', issue, conf.get('issue', 'medium'), src))
    if expiry:
        fields.append(_field('emp_doc_expiry_date', 'Expiry date', expiry, conf.get('expiry', 'medium'), src))
    if issue and expiry and issue >= expiry:
        notes.append(tr('The issue date found is not before the expiry date – check both dates.'))
    info = []
    nm = (mrz or {}).get('name') or holder_name(text or '')
    if nm:
        info.append({'label': 'Name on the document', 'value': nm})
    if mrz and mrz.get('nationality'):
        info.append({'label': 'Nationality', 'value': mrz['nationality']})
    if birth:
        info.append({'label': 'Date of birth', 'value': birth.isoformat()})
    if mrz and mrz.get('sex'):
        info.append({'label': 'Sex', 'value': mrz['sex']})
    if eid and kind != 'emirates_id':
        info.append({'label': 'Emirates ID number', 'value': eid[1]})
    on = other_numbers(text or '')
    if on['uid']:
        info.append({'label': 'UID number', 'value': on['uid']})
    if on['person_code']:
        info.append({'label': 'Person code', 'value': on['person_code']})
    if mrz:
        info.append({'label': 'Machine-readable zone', 'value': f"{mrz['format']} – check digits {'all correct' if all(mrz['checks'].values()) else 'not all correct'}"})
    return {'kind': kind, 'kind_label': KINDS[kind][0] if kind in KINDS else '', 'fields': fields, 'info': info, 'notes': notes,
            'mrz': bool(mrz), 'mrz_ok': bool(mrz and all(mrz['checks'].values()))}


EXTERNAL_PROMPT = ('You read the text of an identity or employment document of an employee in the UAE. Answer ONLY with a JSON object '
                   'with these keys (null when not in the text): kind (one of passport, emirates_id, visa, labour_card, driving_licence, '
                   'insurance, other), document_number, issue_date (YYYY-MM-DD), expiry_date (YYYY-MM-DD), holder_name, nationality, '
                   'date_of_birth (YYYY-MM-DD). Do not guess values that are not in the text.')


def external_read(request, name, text):
    """The external AI's reading of the extracted text (JSON). Raises ExternalUnavailable."""
    from .service import ExternalUnavailable, chat
    r = chat(request, 'documents', EXTERNAL_PROMPT, [{'role': 'user', 'content': f'File name: {name}\n\nText:\n{text[:6000]}'}], None, 400)
    raw = ' '.join(b.get('text', '') for b in r.get('content') or [] if b.get('type') == 'text')
    m = re.search(r'\{.*\}', raw, re.S)
    try:
        d = json.loads(m.group()) if m else None
    except ValueError:
        d = None
    if not isinstance(d, dict):
        raise ExternalUnavailable(tr('The AI answer could not be read.'))
    return d


def _iso(v):
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def merge_external(res, ext, types):
    """External values fill what the built-in reading did not find or found with low confidence; MRZ-verified values stay."""
    by = {f['key']: f for f in res['fields']}
    kind = ext.get('kind') if ext.get('kind') in KINDS else None
    if kind and 'document_type' not in by:
        t = match_type(kind, types)
        if t:
            res['fields'].insert(0, _field('document_type', 'Document type', t['id'], 'medium', 'external AI', t['name']))
        res['kind'], res['kind_label'] = kind, KINDS[kind][0]
    for key, label, val in (('emp_doc_number', 'Document number', (str(ext.get('document_number') or '').strip() or None)),
                            ('emp_doc_issued_date', 'Issue date', _iso(ext.get('issue_date'))),
                            ('emp_doc_expiry_date', 'Expiry date', _iso(ext.get('expiry_date')))):
        if not val:
            continue
        cur = by.get(key)
        if cur is None:
            res['fields'].append(_field(key, label, val, 'medium', 'external AI'))
        elif cur['confidence'] == 'low':
            cur.update(_field(key, label, val, 'medium', 'external AI'))
    if ext.get('holder_name') and not any(i['label'] == 'Name on the document' for i in res['info']):
        res['info'].insert(0, {'label': 'Name on the document', 'value': str(ext['holder_name'])[:80]})
    order = ['document_type', 'emp_doc_number', 'emp_doc_issued_date', 'emp_doc_expiry_date']
    res['fields'].sort(key=lambda f: order.index(f['key']) if f['key'] in order else 9)
    return res


def read_document(request, name, data, types):
    """Everything the screen needs: suggestions, how they were found and whether OCR / external AI were used."""
    from .service import ExternalUnavailable, feature_mode
    text, source = extract_text(name, data)
    res = analyse(name, text, types, source)
    res.update({'source': source or 'file name', 'ocr_available': ocr_available(), 'mode': 'builtin', 'chars': len(text)})
    if not text:
        low = (name or '').lower()
        if (low.endswith(IMAGE_EXT) or not low.endswith('.pdf')) and not ocr_available():
            res['notes'].append(tr('Text could not be read from this file: the server has no OCR program (Tesseract). '
                                   'Only the file name was used. Upload a PDF with text, or ask the administrator to install tesseract-ocr.'))
        else:
            res['notes'].append(tr('No text could be read from this file. Only the file name was used.'))
    mode, _why = feature_mode('documents')
    if mode == 'external' and text.strip():
        try:
            res = merge_external(res, external_read(request, name, text), types)
            res['mode'] = 'external'
        except ExternalUnavailable as exc:
            res['notes'].append(tr('The external AI could not be used ({0}); these suggestions come from the built-in reader.', str(exc)))
            res['fallback'] = str(exc)
    if not res['fields']:
        res['notes'].append(tr('No values were found. Fill the fields yourself.'))
    return res
