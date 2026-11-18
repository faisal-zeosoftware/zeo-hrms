"""v1.73.10 – Document Center phase 5: reading scanned documents (OCR) and searching in plain words (AI search).

* OCR – a scan or photo (PDF without text, JPG, PNG, TIFF, HEIC …) is read with the free Tesseract program on the server (English
  and Arabic when their language data is installed) so its words are searchable and comparable like any text. A small file is read
  when it is uploaded; bigger ones (and the documents of before 1.73.10) by the daily job or "Read scanned documents now".
  Version.text_source says where the text came from: text (in the file), ocr, none (nothing readable), '' (not tried yet).
* Search in words – "Khalid's passport", "expired trade licences", "جواز سفر خالد", "contracts 2025": the question's words are matched
  to categories, employees, statuses and years (filters) and to titles, numbers, tags, fields and the file text (ranked), with the
  sentence where they were found. When the company's AI is switched on for Documents, it also answers the question from the best
  documents' text, citing their numbers – only snippets the user may open are sent, ID numbers hidden.
"""
from zeo.tz import today as _zt_today   # v1.73.16 the user's / company's day, not the server's UTC day
import logging
import re
from collections import Counter
from datetime import date, timedelta

from django.apps import apps
from django.db.models import Q
from django.utils import timezone

from Translations.i18n import tr

from . import models as Mo
from . import services as S

log = logging.getLogger(__name__)
M = apps.get_model
OCR_EXT = ('.pdf', '.jpg', '.jpeg', '.png', '.tif', '.tiff', '.bmp', '.webp', '.gif', '.heic', '.heif')
INLINE_MAX = 3 * 1024 * 1024     # read at upload up to 3 MB (a phone photo, a few scanned pages); bigger: the daily job
OCR_PAGES = 20


# ================================================================== OCR
def langs():
    try:
        import subprocess
        import shutil
        exe = shutil.which('tesseract')
        if not exe:
            return ''
        out = subprocess.run([exe, '--list-langs'], capture_output=True, timeout=10).stdout.decode()
        have = {x.strip() for x in out.splitlines()[1:]}
        return '+'.join(x for x in ('eng', 'ara') if x in have)
    except Exception:   # noqa: BLE001
        return ''


def ocr_available():
    from AISettings.docread import ocr_available as oa
    return oa()


def readable(v):
    return (v.file_name or '').lower().endswith(OCR_EXT)


def _tess(png_path, lang, tsv=False):
    import shutil
    import subprocess
    exe = shutil.which('tesseract')
    if not exe:
        return ''
    r = subprocess.run([exe, png_path, 'stdout'] + (['-l', lang] if lang else []) + (['tsv'] if tsv else []), capture_output=True, timeout=90)
    return r.stdout.decode('utf-8', 'replace')


def _arabic_words(png_path, min_conf=75):
    """the Arabic words read with good confidence, line by line (an English page read as Arabic gives only low-confidence noise)"""
    lines = {}
    for row in _tess(png_path, 'ara', tsv=True).splitlines()[1:]:
        c = row.split('\t')
        if len(c) < 12 or not c[11].strip():
            continue
        try:
            conf = float(c[10])
        except ValueError:
            continue
        if conf >= min_conf and re.search(r'[\u0600-\u06FF]', c[11]):
            lines.setdefault((c[2], c[3], c[4]), []).append(c[11])
    return '\n'.join(' '.join(w) for _, w in sorted(lines.items()))


def _page(im, have):
    """one picture: English (as it is, then enlarged when it reads little) + the Arabic words"""
    import os
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, 'p.png')
        im.save(p)
        t = _tess(p, 'eng' if 'eng' in have else None)
        if len(re.sub(r'\W', '', t)) < 8 and im.width < 1600:
            f = 1600 / max(im.width, 1)
            im.resize((int(im.width * f), int(im.height * f))).save(p)
            t2 = _tess(p, 'eng' if 'eng' in have else None)
            if len(re.sub(r'\W', '', t2)) > len(re.sub(r'\W', '', t)):
                t = t2
            im.save(p)
        if 'ara' in have:
            ar = _arabic_words(p)
            if ar:
                t = t.rstrip() + '\n' + ar
        return t.strip()


def ocr_bytes(name, data):
    """English first (codes and numbers read best alone), then the Arabic words read with good confidence"""
    import io
    from PIL import Image, ImageOps
    have = langs().split('+') if langs() else []
    low = (name or '').lower()
    out = []
    if data[:5] == b'%PDF-' or low.endswith('.pdf'):
        try:
            import pypdfium2 as pdfium
            doc = pdfium.PdfDocument(data)
            try:
                for i in range(min(len(doc), OCR_PAGES)):
                    im = doc[i].render(scale=250 / 72).to_pil().convert('L')
                    out.append(_page(im, have))
            finally:
                doc.close()
        except Exception:   # noqa: BLE001 – no renderer: the images inside the PDF
            from AISettings import docread as R
            return R._pdf_ocr(data, lang='eng' if 'eng' in have else None, max_pages=OCR_PAGES) or ''
        return '\n'.join(x for x in out if x)
    try:
        try:
            import pillow_heif   # noqa: F401 – HEIC photos from phones, when installed
            pillow_heif.register_heif_opener()
        except Exception:   # noqa: BLE001
            pass
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert('L')
    except Exception:   # noqa: BLE001
        return ''
    return _page(im, have)


def ocr_version(v, force=False):
    """read the text of a version; returns the source ('text' / 'ocr' / 'none' / '' when it was not tried)"""
    if v.text and not force:
        if not v.text_source:
            Mo.Version.objects.filter(pk=v.pk).update(text_source='text')
        return 'text'
    if not v.file or not readable(v):
        Mo.Version.objects.filter(pk=v.pk).update(text_source='none')
        return 'none'
    if not ocr_available():
        return ''
    try:
        with v.file.open('rb') as fh:
            data = fh.read()
    except Exception:   # noqa: BLE001 – the file is gone
        Mo.Version.objects.filter(pk=v.pk).update(text_source='none', ocr_at=timezone.now())
        return 'none'
    t = ''
    try:
        t = ocr_bytes(v.file_name, data) or ''
    except Exception:   # noqa: BLE001
        log.exception('OCR of version %s', v.pk)
    t = re.sub(r'[ \t]+', ' ', t).strip()[:200000]
    src = 'ocr' if len(re.sub(r'\W', '', t)) >= 3 else 'none'
    Mo.Version.objects.filter(pk=v.pk).update(text=t if src == 'ocr' else '', text_source=src, ocr_at=timezone.now())
    v.text, v.text_source = (t if src == 'ocr' else ''), src
    return src


def after_version(v):
    """called when a version is added: mark text from the file, read a small scan now"""
    if v.text:
        Mo.Version.objects.filter(pk=v.pk).update(text_source='text')
        return 'text'
    if not readable(v):
        Mo.Version.objects.filter(pk=v.pk).update(text_source='none')
        return 'none'
    if (v.size or 0) <= INLINE_MAX:
        return ocr_version(v)
    return ''   # the daily job reads it


def pending():
    return Mo.Version.objects.filter(text_source='', document__deleted_at__isnull=True).exclude(file='')


def run_pending(limit=100):
    out = Counter()
    for v in pending().order_by('-id')[:limit]:
        out[ocr_version(v) or 'skipped'] += 1
    return dict(out)


def status():
    base = Mo.Version.objects.filter(document__deleted_at__isnull=True)
    c = Counter(base.values_list('text_source', flat=True))
    return {'text': c.get('text', 0), 'ocr': c.get('ocr', 0), 'none': c.get('none', 0), 'waiting': pending().count(),
            'ocr_available': ocr_available(), 'languages': langs()}


# ================================================================== search in words
AR_NORM = str.maketrans({'أ': 'ا', 'إ': 'ا', 'آ': 'ا', 'ة': 'ه', 'ى': 'ي', 'ؤ': 'و', 'ئ': 'ي', 'ـ': ''})
DIAC = re.compile(r'[ً-ْٰ]')
STOP = {'the', 'a', 'an', 'of', 'for', 'in', 'on', 'to', 'and', 'or', 'with', 'my', 'me', 'show', 'find', 'where', 'is', 'are', 'all', 'any', 'which',
        'what', 'who', 'documents', 'document', 'file', 'files', 'copy', 'please', 'get', 'give', 'list', 'whose', 'that', 'this', 'from', 'by', 'about',
        'do', 'we', 'have', 'our', 'his', 'her', 'their', 's', 'i',
        'في', 'من', 'على', 'الى', 'إلى', 'عن', 'مع', 'او', 'و', 'ال', 'هذا', 'هذه', 'كل', 'اين', 'ابحث', 'اعرض', 'مستند', 'مستندات', 'ملف'}
EXPIRED = {'expired', 'منتهي', 'منتهية', 'منتهيه'}
EXPIRING = {'expiring', 'expire', 'expires', 'renewal', 'due', 'تنتهي', 'قريبا'}
DRAFT = {'draft', 'drafts', 'مسوده', 'مسودة'}


def norm(s):
    s = DIAC.sub('', str(s or '')).translate(AR_NORM).lower()
    return re.sub(r"[^\w؀-ۿ]+", ' ', s).strip()


def words(q):
    out = []
    for w in norm(q).split():
        if w.endswith("'s"):
            w = w[:-2]
        if w and w not in STOP and len(w) > 1:
            out.append(w)
    return out


LOOK = {'0': '[0o]', 'o': '[0o]', '1': '[1il]', 'i': '[1il]', 'l': '[1il]'}


def loose(t):
    """a code with digits as a scan may read it (0 / O, 1 / I / l) – a regular expression, or None for a plain word"""
    if not re.search(r'\d', t) or not re.fullmatch(r'[a-z0-9]+', t):
        return None
    return ''.join(LOOK.get(ch, re.escape(ch)) for ch in t)


def hits(t, text):
    rx = loose(t)
    return len(re.findall(rx, text)) if rx else text.count(t)


def _snippet(text, terms, width=110):
    if not text:
        return ''
    low = norm(text)
    pos = -1
    for t in terms:
        rx = loose(t)
        m = re.search(rx, low) if rx else None
        pos = m.start() if m else low.find(t)
        if pos >= 0:
            break
    if pos < 0:
        return ''
    # the normalised text has the same length only for single letters changed; map by ratio as a good-enough window
    ratio = len(text) / max(len(low), 1)
    p = int(pos * ratio)
    a, b = max(0, p - width), min(len(text), p + width)
    return ('… ' if a else '') + ' '.join(text[a:b].split()) + (' …' if b < len(text) else '')


def understand(q):
    """the filters the words name: categories, employees, status, year"""
    ws = words(q)
    wset = set(ws)
    f, used = {}, set()
    cats = []
    for c in Mo.DocCategory.objects.filter(active=True):
        cw = [x for x in words(c.name) if x not in STOP]
        if cw and all(any(x == w or (len(x) > 3 and w.startswith(x[:-1])) for w in ws) for x in cw):
            cats.append(c)
            used.update(w for w in ws for x in cw if x == w or (len(x) > 3 and w.startswith(x[:-1])))
    if cats:
        f['categories'] = cats
    E = M('EmpManagement', 'emp_master')
    emps = []
    if ws:
        cand = Q()
        for w in ws:
            if len(w) >= 3:
                cand |= Q(emp_first_name__iexact=w) | Q(emp_last_name__iexact=w) | Q(emp_code__iexact=w)
        if cand:
            emps = list(E.objects.filter(cand)[:20])
    if emps:
        f['employees'] = emps
        for e in emps:
            used.update(w for w in ws if w in {norm(e.emp_first_name), norm(e.emp_last_name), norm(e.emp_code)})
    if wset & EXPIRED:
        f['expired'] = True
        used |= wset & EXPIRED
    elif wset & EXPIRING:
        f['expiring'] = True
        used |= wset & EXPIRING
    if wset & DRAFT:
        f['draft'] = True
        used |= wset & DRAFT
    years = [int(w) for w in ws if re.fullmatch(r'(19|20)\d\d', w)]
    if years:
        f['year'] = years[0]
        used.add(str(years[0]))
    f['terms'] = [w for w in ws if w not in used]
    return f


def smart_search(who, q, limit=20):
    f = understand(q)
    terms = f['terms']
    qs = Mo.Document.objects.filter(deleted_at__isnull=True).select_related('category', 'current', 'folder')
    if f.get('categories'):
        ids = set()
        for c in f['categories']:
            ids.update(_cat_tree(c.pk))
        qs = qs.filter(category_id__in=ids)
    if f.get('employees'):
        eids = [e.pk for e in f['employees']]
        fids = list(Mo.DocFolder.objects.filter(provider='employee', record_id__in=eids).values_list('pk', flat=True))
        allf = set()
        for x in Mo.DocFolder.objects.filter(pk__in=fids):
            allf.update(y.pk for y in S.descendants(x))
        qs = qs.filter(Q(employee_id__in=eids) | Q(folder_id__in=allf))
    today = _zt_today()
    if f.get('expired'):
        qs = qs.filter(Q(expiry_date__lt=today) | Q(status='expired'))
    if f.get('expiring'):
        qs = qs.filter(expiry_date__gte=today, expiry_date__lte=today + timedelta(days=90))
    if f.get('draft'):
        qs = qs.filter(status__in=('draft', 'submitted', 'under_review', 'pending_approval'))
    if f.get('year'):
        y = f['year']
        qs = qs.filter(Q(effective_date__year=y) | Q(created_at__year=y) | Q(expiry_date__year=y) | Q(title__icontains=str(y)))
    if terms:
        cond = Q()
        for t in terms:
            cond |= Q(title__icontains=t) | Q(number__icontains=t) | Q(tags__icontains=t) | Q(remarks__icontains=t) | Q(fields__icontains=t) \
                | Q(current__text__icontains=t) | Q(current__file_name__icontains=t)
            if loose(t):   # a scan may read 0 as O and 1 as I or l
                cond |= Q(current__text__iregex=loose(t))
        if not any(k in f for k in ('categories', 'employees', 'expired', 'expiring', 'draft', 'year')):
            qs = qs.filter(cond)
    docs = S.visible_documents(who, qs.order_by('-updated_at')[:1500])
    rows = []
    for d in docs:
        v = d.current
        text = norm(v.text) if v and v.text else ''
        title, num = norm(d.title), norm(d.number)
        score = 0.0
        for t in terms:
            score += 5 if t in title else 0
            score += 6 if t in num else 0
            score += 3 if any(t in norm(x) for x in (d.tags or [])) else 0
            score += 2 if t in norm(d.fields) else 0
            score += min(hits(t, text), 5)
        if terms and len(terms) > 1 and ' '.join(terms) in text:
            score += 4
        if terms and score == 0 and not any(k in f for k in ('categories', 'employees')):
            continue
        score += 3 if f.get('categories') else 0
        score += 3 if f.get('employees') else 0
        rows.append((score, d))
    rows.sort(key=lambda x: (-x[0], -(x[1].updated_at.timestamp() if x[1].updated_at else 0)))
    out = []
    for score, d in rows[:limit]:
        v = d.current
        out.append({'id': d.pk, 'number': d.number, 'title': d.title, 'category': d.category.name if d.category_id else '', 'status': d.status,
                    'folder_path': S.path_of(d.folder), 'expiry_date': d.expiry_date, 'score': round(score, 1),
                    'text_source': v.text_source if v else '', 'snippet': _snippet(v.text if v else '', terms or [norm(d.title)])})
    understood = {'categories': [c.name for c in f.get('categories', [])],
                  'employees': [f'{e.emp_first_name or ""} {e.emp_last_name or ""} ({e.emp_code})'.strip() for e in f.get('employees', [])],
                  'expired': bool(f.get('expired')), 'expiring': bool(f.get('expiring')), 'draft': bool(f.get('draft')), 'year': f.get('year'), 'words': terms}
    return {'results': out, 'count': len(rows), 'understood': understood}


def _cat_tree(cid):
    ids, todo = {cid}, [cid]
    while todo:
        kids = list(Mo.DocCategory.objects.filter(parent_id__in=todo).values_list('pk', flat=True))
        todo = [k for k in kids if k not in ids]
        ids.update(todo)
    return ids


def ai_ready():
    try:
        from AISettings.service import feature_mode
        return feature_mode('documents')[0] == 'external'
    except Exception:   # noqa: BLE001
        return False


def ai_answer(request, who, q, results):
    """the company's AI answers from the best documents (ID numbers hidden); None when it is not switched on or fails"""
    if not ai_ready() or not results:
        return None
    from AISettings.service import ExternalUnavailable, chat
    from AskZeo.privacy import Shield
    sh = Shield(hide_salary=True)
    parts = []
    for r in results[:6]:
        d = Mo.Document.objects.select_related('current').get(pk=r['id'])
        body = (d.current.text if d.current else '')[:1500]
        parts.append(f"[{d.number}] {d.title} – {r['category']}\n{sh.text(body)}")
    system = ('You answer questions about the company\'s documents. Use ONLY the documents given. Cite the document number in square brackets '
              'after each fact, e.g. [HR-2026-0001]. If the documents do not answer, say so in one sentence. Answer in the language of the question, briefly.')
    try:
        r = chat(request, 'documents', system, [{'role': 'user', 'content': f'Question: {q}\n\nDocuments:\n\n' + '\n\n'.join(parts)}], max_tokens=600)
    except ExternalUnavailable as e:
        return {'text': '', 'note': str(e)}
    except Exception:   # noqa: BLE001
        log.exception('documents AI answer')
        return None
    t = '\n'.join(b.get('text', '') for b in (r or {}).get('content') or [] if b.get('type') == 'text').strip()
    try:
        t = sh.restore(t)
    except Exception:   # noqa: BLE001
        pass
    cited = [x['number'] for x in results if x['number'] and f"[{x['number']}]" in t]
    return {'text': t, 'cited': cited}
