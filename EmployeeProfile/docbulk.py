"""
v1.53.2 – bulk upload of employee document files (a zip of scans / PDFs) with folder mapping.

Layouts that are recognised (they can be mixed and nested):
  A  a folder per employee        EMP001/passport.pdf, EMP001/Emirates ID front.jpg
  B  a folder per document type   Passport/EMP001.pdf, Visa/EMP001 - Ahmed.pdf
  C  one folder, named files      EMP001_Passport_2027-05-31.pdf  (parts split on _ - . and spaces)
  D  a list in the zip            index.xlsx / index.csv / manifest.xlsx: File, Employee Code, Document Type,
                                  Document Number, Issue Date, Expiry Date – the list wins over every guess

How a file is matched:
  1. the list (D) when it names the file;
  2. employee: an employee code as a whole word anywhere in the path (folders or file name; the longest code wins) →
     else a number read from the document (passport, Emirates ID, UID, visa, labour card … numbers on the identity
     details or on a document of the employee) → else the name on the document (a suggestion, to check) → not matched;
  3. document type: words of the folders and the file name against the company's document types and their other names
     in English and Arabic (PP → Passport, EID / هوية → Emirates ID, Residence / Iqama → Visa, Work permit → Labour card,
     DL → Driving licence …) → else the kind of document read from its text (MRZ) → chosen by HR;
  4. number and dates: the list → the text of the document (MRZ, labels) → a date in the file name (one date = expiry,
     two dates = issue and expiry);
  5. front and back (or page 1 / page 2) of the same document of the same employee become one PDF;
  6. existing documents: the same file or the same number with the same expiry date → duplicate, skipped; the same
     number with a later expiry → the existing document gets the new file and dates; a new number for a passport, visa,
     Emirates ID, labour card, driving licence or insurance card → the new document is the current one and the old one
     stays as history (inactive); an older document than the current one → kept as history; a number that is on
     another employee's document → conflict.

Nothing is saved until HR presses Import; the import is listed in Import history, where it can be undone (the new
documents are deleted, the replaced ones are active again and the updated ones get back their file and dates).
"""
import csv
import hashlib
import io
import logging
import os
import re
import shutil
import threading
import time
import uuid
import zipfile
from datetime import date, datetime

from django.apps import apps
from django.conf import settings
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.db import connection, transaction

from Translations.i18n import tr

from . import services as S

log = logging.getLogger(__name__)
M = apps.get_model

ENDPOINT = '/employee-profile/api/doc-bulk/'
JOB = 'zeo-docbulk:{}'
TTL = 60 * 60 * 12
MAX_ZIP = 300 * 1024 * 1024
MAX_FILES = 2000
MAX_FILE = 10 * 1024 * 1024
DOC_EXT = ('.pdf', '.jpg', '.jpeg', '.png', '.tif', '.tiff', '.bmp', '.webp', '.gif', '.heic', '.heif', '.doc', '.docx', '.txt')
MERGEABLE = ('.pdf', '.jpg', '.jpeg', '.png', '.tif', '.tiff', '.bmp', '.webp', '.gif')
LIST_NAMES = ('index', 'manifest', 'documents', 'list', 'files')

# other names of the identity documents (normalised: lower case, letters and digits only; Arabic kept)
KIND_WORDS = {
    'passport': ['passport', 'passports', 'pp', 'ppt', 'passportcopy', 'جواز', 'جوازسفر', 'جوازالسفر'],
    'visa': ['visa', 'visas', 'residence', 'residencevisa', 'residency', 'residencepermit', 'iqama', 'entrypermit', 'employmentvisa',
             'اقامة', 'إقامة', 'الاقامة', 'الإقامة', 'تأشيرة', 'تاشيرة', 'فيزا'],
    'emirates_id': ['emiratesid', 'eid', 'eids', 'emiratesidcard', 'idcard', 'nationalid', 'emiratesidentity', 'هوية', 'الهوية', 'بطاقةالهوية'],
    'labour_card': ['labourcard', 'laborcard', 'labour', 'labor', 'workpermit', 'wp', 'lc', 'mohre', 'بطاقةعمل', 'تصريحعمل'],
    'driving_licence': ['drivinglicence', 'drivinglicense', 'licence', 'license', 'dl', 'رخصة', 'رخصةقيادة', 'رخصةالقيادة'],
    'insurance': ['insurance', 'medical', 'medicalinsurance', 'healthinsurance', 'insurancecard', 'healthcard', 'تأمين', 'تامين', 'التأمين'],
}
SIDE_RE = re.compile(r'(?:^|[\s_\-.(])(front|back|rear|side\s*[ab12]|page\s*\d|p\d|\d)\)?$', re.I)
DATE_PATTERNS = [
    (re.compile(r'(?<!\d)(20\d\d|19\d\d)[\-_. ]?(0[1-9]|1[0-2])[\-_. ]?(0[1-9]|[12]\d|3[01])(?!\d)'), ('y', 'm', 'd')),
    (re.compile(r'(?<!\d)(0[1-9]|[12]\d|3[01])[\-_. ](0[1-9]|1[0-2])[\-_. ](20\d\d|19\d\d)(?!\d)'), ('d', 'm', 'y')),
]
STATUS = {'ready': 'Ready', 'review': 'Needs review', 'unmatched': 'Employee not found', 'duplicate': 'Duplicate file',
          'conflict': 'Conflict', 'skipped': 'Skipped'}
ACTIONS = {'new': 'New document', 'replace': 'New current document – the old one is kept as history',
           'update': 'Updates the existing document (file and dates)', 'history': 'Older than the current one – kept as history'}


def _n(s):
    return re.sub(r'[\W_]+', '', str(s or '').lower())


def N(text, *args, auto=False):
    """A message kept in English with its values, said in the language of whoever looks at the rows."""
    return {'t': text, 'a': [str(x) for x in args], 'auto': auto}


def say(n):
    if isinstance(n, dict):
        return tr(n['t'], *[tr(x) for x in n.get('a') or []])
    return str(n)


def _job(_key, **kw):
    d = cache.get(JOB.format(_key)) or {}
    d.update(kw)
    cache.set(JOB.format(_key), d, TTL)
    return d


from contextlib import contextmanager


@contextmanager
def locked(job, wait=10):
    """One change at a time to the rows of an upload (they live in the cache)."""
    key = JOB.format(job) + ':lock'
    end = time.time() + wait
    got = cache.add(key, 1, 600)
    while not got and time.time() < end:
        time.sleep(0.05)
        got = cache.add(key, 1, 600)
    try:
        yield got
    finally:
        if got:
            cache.delete(key)


def get(job):
    return cache.get(JOB.format(job)) if re.fullmatch(r'[0-9a-f]{16}', str(job or '')) else None


def _root():
    """Where uploads wait for Import: not under media/ (nginx serves media/ without a login). ZEO_STAGING_ROOT may move it."""
    return os.path.join(getattr(settings, 'ZEO_STAGING_ROOT', None) or os.environ.get('ZEO_STAGING_ROOT') or os.path.join(settings.BASE_DIR, 'staging'), 'docbulk')


def staging(job, schema=None):
    return os.path.join(_root(), schema or connection.schema_name, job)


def _cleanup_old():
    root = _root()
    if not os.path.isdir(root):
        return
    limit = time.time() - 2 * 86400
    for sch in os.listdir(root):
        for j in os.listdir(os.path.join(root, sch)) if os.path.isdir(os.path.join(root, sch)) else []:
            p = os.path.join(root, sch, j)
            try:
                if os.path.getmtime(p) < limit:
                    shutil.rmtree(p, ignore_errors=True)
            except OSError:
                pass


# ------------------------------------------------------------------ what the company has
class Index:
    """Employees (the user's branches), document types and the numbers on identity details and documents."""

    def __init__(self, emp_ids=None):
        E = M('EmpManagement', 'emp_master')
        qs = E.objects.all() if emp_ids is None else E.objects.filter(pk__in=emp_ids)
        self.emps = {e.pk: e for e in qs.select_related('emp_branch_id')}
        self.codes = {}
        for e in self.emps.values():
            if e.emp_code:
                self.codes.setdefault(_n(e.emp_code), e.pk)
        self.max_code = max((len(k) for k in self.codes), default=0)
        self.names = {}
        for e in self.emps.values():
            w = frozenset(x for x in re.findall(r'\w+', S.full_name(e).lower()) if len(x) > 1)
            if w:
                self.names.setdefault(w, []).append(e.pk)
        self.numbers = {}
        Ident = M('EmployeeProfile', 'EmployeeIdentity')
        fields = ['passport_no', 'emirates_id', 'visa_uid', 'visa_number', 'visa_file_no', 'labour_card_no', 'work_permit_no',
                  'driving_licence_no', 'insurance_card_no']
        for row in Ident.objects.filter(employee_id__in=list(self.emps)).values('employee_id', *fields):
            for f in fields:
                if row[f] and len(_n(row[f])) >= 5:
                    self.numbers.setdefault(_n(row[f]), row['employee_id'])
        Doc = M('EmpManagement', 'Emp_Documents')
        for pk, emp, num in Doc.objects.filter(emp_id__in=list(self.emps)).values_list('pk', 'emp_id', 'emp_doc_number'):
            if num and len(_n(num)) >= 5:
                self.numbers.setdefault(_n(num), emp)
        DT = M('EmpManagement', 'document_type')
        self.types = {t.pk: t for t in DT.objects.all()}
        self.type_list = [{'id': t.pk, 'type_name': t.type_name} for t in self.types.values() if t.is_active]
        self.kind_of_type = {}
        self.words = {}   # normalised word → (type id or 'kind:<kind>', kind)
        for kind, *_ in S.DOC_KINDS:
            t = S.doc_type(kind, create=False)
            target = t.pk if t is not None else f'kind:{kind}'
            for t2 in S.doc_types_for(kind):
                self.kind_of_type[t2.pk] = kind
            if t is not None:
                self.kind_of_type[t.pk] = kind
            for w in KIND_WORDS[kind] + [_n(S.KIND_LABEL[kind])]:
                self.words.setdefault(_n(w) or w, (target, kind))
        for t in self.types.values():
            if t.is_active:
                self.words[_n(t.type_name)] = (t.pk, self.kind_of_type.get(t.pk))

    def type_target(self, kind):
        t = S.doc_type(kind, create=False)
        return t.pk if t is not None else f'kind:{kind}'

    def type_label(self, target):
        if isinstance(target, int) and target in self.types:
            return self.types[target].type_name
        if isinstance(target, str) and target.startswith('kind:'):
            return tr('{0} (new document type)', tr(S.KIND_LABEL[target[5:]]))   # said again in public_row
        return ''

    def emp_label(self, pk):
        e = self.emps.get(pk)
        return f'{e.emp_code} – {S.full_name(e)}' if e else ''


# ------------------------------------------------------------------ reading the path
def _tokens(part):
    return [t for t in re.split(r'[\s_\-.()\[\],+&]+', part) if t]


def _windows(tokens, size=3):
    for i in range(len(tokens)):
        for k in range(1, size + 1):
            if i + k <= len(tokens):
                yield i, k, _n(''.join(tokens[i:i + k]))


def path_dates(path):
    """Dates written in the path (file name first) and the path without them."""
    found, rest = [], path
    for rx, order in DATE_PATTERNS:
        for m in rx.finditer(rest):
            v = dict(zip(order, m.groups()))
            try:
                found.append(date(int(v['y']), int(v['m']), int(v['d'])))
            except ValueError:
                continue
        rest = rx.sub(' ', rest)
    return sorted(set(found)), rest


def match_path(path, ix):
    """{emp: (id, where), type: (target, kind, where), dates, number, others} from the folders and the file name."""
    dates, rest = path_dates(path)
    parts = [p for p in rest.replace('\\', '/').split('/') if p]
    if parts:
        parts[-1] = os.path.splitext(parts[-1])[0]
    emp_hits, type_hits, leftovers = [], [], []
    for depth, part in enumerate(parts):
        toks = _tokens(part)
        used = set()
        where = 'file name' if depth == len(parts) - 1 else 'folder'
        for i, k, w in _windows(toks, 3):
            if w in ix.codes and len(w) >= 2:
                emp_hits.append((len(w), depth, ix.codes[w], where))
                used.update(range(i, i + k))
        for i, k, w in _windows(toks, 4):
            if w in ix.words:
                type_hits.append((len(w), depth, ix.words[w], where))
                used.update(range(i, i + k))
        whole = _n(part)
        for word, tgt in ix.words.items():   # a longer type name inside the part ("EmiratesIDfront")
            if len(word) >= 6 and word in whole and not any(h[2] == tgt for h in type_hits):
                type_hits.append((len(word) - 1, depth, tgt, where))
        if depth == len(parts) - 1:
            leftovers = [toks[j] for j in range(len(toks)) if j not in used]
    out = {'dates': dates, 'emp': None, 'emps': [], 'type': None, 'number': None}
    if emp_hits:
        emp_hits.sort(key=lambda h: (-h[0], -h[1]))
        out['emp'] = (emp_hits[0][2], 'code in the ' + emp_hits[0][3])
        out['emps'] = sorted({h[2] for h in emp_hits})
    if type_hits:
        type_hits.sort(key=lambda h: (-h[1], -h[0]))   # the file name before the folders, then the longest word
        (tgt, kind), where = type_hits[0][2], type_hits[0][3]
        out['type'] = (tgt, kind, where)
    # "EMP001_Passport_N1234567": a word of the file name with letters and digits that is not a code, type or side word
    for t in leftovers:
        if len(t) >= 6 and re.search(r'\d', t) and re.fullmatch(r'[A-Za-z0-9]+', t) and not SIDE_RE.fullmatch(' ' + t):
            out['number'] = t.upper()
            break
    return out


def side_of(path):
    stem = os.path.splitext(os.path.basename(path))[0].strip()
    m = SIDE_RE.search(stem)
    if not m:
        return None, stem
    s = m.group(1).lower().replace(' ', '')
    order = {'front': 1, 'sidea': 1, 'side1': 1, 'page1': 1, 'p1': 1, '1': 1, 'back': 2, 'rear': 2, 'sideb': 2, 'side2': 2,
             'page2': 2, 'p2': 2, '2': 2}.get(s, 3 + (int(re.sub(r'\D', '', s) or 9)))
    return order, stem[:m.start()].strip(' _-.(')


# ------------------------------------------------------------------ the list in the zip
LIST_COLS = {
    'file': ('file', 'filename', 'filepath', 'path', 'document', 'scan'),
    'code': ('employeecode', 'empcode', 'code', 'employeeno', 'employeenumber', 'staffno', 'empno', 'employeeid'),
    'type': ('documenttype', 'type', 'doctype', 'document'),
    'number': ('documentnumber', 'number', 'docno', 'documentno', 'docnumber', 'no'),
    'issue': ('issuedate', 'issueddate', 'issued', 'dateofissue', 'issuedon'),
    'expiry': ('expirydate', 'expiry', 'expires', 'expirationdate', 'dateofexpiry', 'validuntil'),
}


def read_list(name, data):
    """[{file, code, type, number, issue, expiry}] from index.xlsx / .csv."""
    rows = []
    if name.lower().endswith('.csv'):
        txt = data.decode('utf-8-sig', 'replace')
        table = list(csv.reader(io.StringIO(txt)))
    else:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        table = [list(r) for r in ws.iter_rows(values_only=True)]
    if not table:
        return rows
    head = [_n(h) for h in table[0]]
    idx = {}
    for key, names in LIST_COLS.items():
        for i, h in enumerate(head):
            if h in names and i not in idx.values() and key not in idx:
                idx[key] = i
    if 'file' not in idx:
        return rows
    from EmpManagement.bulk_import import _date
    for r in table[1:]:
        def cell(k):
            i = idx.get(k)
            v = r[i] if i is not None and i < len(r) else None
            return v if v not in (None, '') else None
        if not cell('file'):
            continue
        d = {'file': str(cell('file')).strip().replace('\\', '/'), 'code': str(cell('code') or '').strip(), 'type': str(cell('type') or '').strip(),
             'number': str(cell('number') or '').strip(), 'errors': []}
        for k in ('issue', 'expiry'):
            try:
                v = _date(cell(k))
                d[k] = v.isoformat() if v else None
            except ValueError as e:
                d[k] = None
                d['errors'].append(str(e))
        rows.append(d)
    return rows


# ------------------------------------------------------------------ the job
def start(f, request, read=True, run_now=False):
    """f: an uploaded zip. Returns the job id. Raises ValueError with a message for the user."""
    if f.size > MAX_ZIP:
        raise ValueError(tr('The zip file is larger than 300 MB. Split it into smaller zip files.'))
    name = f.name or 'documents.zip'
    if not name.lower().endswith('.zip'):
        raise ValueError(tr('Upload a zip file with the document files (PDF or pictures).'))
    _cleanup_old()
    job = uuid.uuid4().hex[:16]
    folder = staging(job)
    os.makedirs(folder, exist_ok=True)
    zpath = os.path.join(folder, 'upload.zip')
    with open(zpath, 'wb') as out:
        for chunk in f.chunks():
            out.write(chunk)
    if not zipfile.is_zipfile(zpath):
        shutil.rmtree(folder, ignore_errors=True)
        raise ValueError(tr('The file is not a zip file, or it is damaged.'))
    user = request.user
    branches = S.user_branches(request)
    _job(job, state='running', step='Unpacking', processed=0, total=0, file_name=name[:200], by=getattr(user, 'pk', None),
         schema=connection.schema_name, read=bool(read), at=datetime.now().isoformat(timespec='seconds'), job=job, branches=branches)
    args = (job, connection.schema_name, branches, bool(read))
    if run_now:
        run(*args, own_thread=False)
    else:
        threading.Thread(target=run, args=args, kwargs={'own_thread': True}, daemon=True).start()
    return job


def _scope(branches):
    """Employee ids in the user's branches (None = every employee)."""
    if branches is None:
        return None
    return list(M('EmpManagement', 'emp_master').objects.filter(emp_branch_id__in=branches or []).values_list('pk', flat=True))


def run(job, schema, branches, read=True, own_thread=True):
    from django.db import close_old_connections
    if own_thread:
        close_old_connections()
    try:
        connection.set_schema(schema)
        folder = staging(job, schema)
        zpath = os.path.join(folder, 'upload.zip')
        files, lists, skipped = [], [], []
        with zipfile.ZipFile(zpath) as z:
            entries = [i for i in z.infolist() if not i.is_dir()]
            for info in entries:
                raw = info.filename
                try:   # names from Windows zips that are not marked UTF-8
                    path = raw if info.flag_bits & 0x800 else raw.encode('cp437').decode('utf-8')
                except (UnicodeEncodeError, UnicodeDecodeError):
                    path = raw
                path = path.replace('\\', '/').lstrip('/')
                base = os.path.basename(path)
                if not base or base.startswith(('.', '~$')) or '__MACOSX' in path or base.lower() in ('thumbs.db', 'desktop.ini'):
                    continue
                stem, ext = os.path.splitext(base.lower())
                if stem in LIST_NAMES and ext in ('.xlsx', '.csv'):
                    lists.append((path, z.read(info)))
                    continue
                if ext not in DOC_EXT:
                    skipped.append({'path': path, 'why': N('Not a document file (PDF, picture or Word).')})
                    continue
                if info.file_size > MAX_FILE:
                    skipped.append({'path': path, 'why': N('Larger than 10 MB.')})
                    continue
                if len(files) >= MAX_FILES:
                    skipped.append({'path': path, 'why': N('More than {0} files in one zip.', MAX_FILES)})
                    continue
                data = z.read(info)
                i = len(files)
                with open(os.path.join(folder, f'{i:05d}{ext}'), 'wb') as out:
                    out.write(data)
                files.append({'i': i, 'path': path, 'ext': ext, 'size': len(data), 'hash': hashlib.sha256(data).hexdigest()})
        os.remove(zpath)
        listed = []
        for p, data in lists:
            try:
                listed += read_list(p, data)
            except Exception as e:
                skipped.append({'path': p, 'why': N('The list could not be read: {0}', str(e)[:120])})
        _job(job, step='Reading the documents', total=len(files), processed=0, lists=[p for p, _ in lists])
        ix = Index(_scope(branches))
        by_file = {}
        for r in listed:
            by_file[_n(r['file'])] = r
            by_file.setdefault(_n(os.path.basename(r['file'])), r)
        drafts = []
        for n, fi in enumerate(files):
            lr = by_file.get(_n(fi['path'])) or by_file.get(_n(os.path.basename(fi['path'])))
            drafts.append(draft(fi, lr, ix, folder, read))
            if n % 3 == 0:
                _job(job, processed=n + 1)
        rows = group(drafts, ix, folder)
        evaluate_rows(rows, ix)
        _job(job, state='ready', step='Ready', processed=len(files), rows=rows, skipped=skipped, listed=len(listed),
             counts=counts(rows))
    except Exception as e:
        log.exception('document files upload')
        _job(job, state='failed', error=str(e)[:400])
    finally:
        if own_thread:
            try:
                connection.set_schema_to_public()
            except Exception:
                pass
            close_old_connections()


def counts(rows):
    c = {k: 0 for k in STATUS}
    for r in rows:
        c[r['status']] = c.get(r['status'], 0) + 1
    c['total'] = len(rows)
    return c


def draft(fi, lr, ix, folder, read):
    """One file → its first guesses."""
    m = match_path(fi['path'], ix)
    d = {'files': [fi], 'path': fi['path'], 'emp_id': None, 'match': '', 'type': None, 'kind': None, 'type_from': '',
         'number': None, 'issue': None, 'expiry': None, 'from': {}, 'notes': [], 'verified': False, 'name_on_doc': None, 'others': []}
    if m['emp']:
        d['emp_id'], d['match'] = m['emp'][0], m['emp'][1]
        if len(m['emps']) > 1:
            d['others'] = [x for x in m['emps'] if x != d['emp_id']]
    if m['type']:
        d['type'], d['kind'], d['type_from'] = m['type'][0], m['type'][1], m['type'][2]
    if len(m['dates']) == 1:
        d['expiry'], d['from']['expiry'] = m['dates'][0].isoformat(), 'file name'
    elif len(m['dates']) >= 2:
        d['issue'], d['expiry'] = m['dates'][0].isoformat(), m['dates'][-1].isoformat()
        d['from'].update(issue='file name', expiry='file name')
    if m['number']:
        d['number'], d['from']['number'] = m['number'], 'file name'
    rec = {}
    full_list = lr and lr.get('number') and lr.get('issue') and lr.get('expiry')
    if read and not full_list:
        try:
            from AISettings.docread import analyse, extract_text
            with open(os.path.join(folder, f"{fi['i']:05d}{fi['ext']}"), 'rb') as fh:
                data = fh.read()
            text, source = extract_text(fi['path'], data)
            res = analyse(os.path.basename(fi['path']), text, ix.type_list, source)
            rec = res.get('record') or {}
            d['read'] = source or ''
        except Exception:
            log.exception('doc bulk read %s', fi['path'])
            d['notes'].append(N('The document could not be read.'))
    if rec:
        d['verified'] = bool(rec.get('verified'))
        d['name_on_doc'] = rec.get('name')
        if rec.get('kind') and not d['type']:
            d['type'], d['kind'], d['type_from'] = ix.type_target(rec['kind']), rec['kind'], 'document'
        for k in ('number', 'issue', 'expiry'):
            if rec.get(k) and (not d[k] or d['from'].get(k) == 'file name' and k == 'number'):
                d[k], d['from'][k] = rec[k], 'document'
        if not d['emp_id']:
            for v in (rec.get('number'), rec.get('emirates_id'), rec.get('uid')):
                if v and _n(v) in ix.numbers:
                    d['emp_id'], d['match'] = ix.numbers[_n(v)], 'number on the document'
                    break
        if not d['emp_id'] and rec.get('name'):
            w = frozenset(x for x in re.findall(r'\w+', rec['name'].lower()) if len(x) > 1)
            hits = [pk for names, pks in ix.names.items() if len(names & w) >= 2 and (names <= w or w <= names) for pk in pks]
            if len(set(hits)) == 1:
                d['emp_id'], d['match'] = hits[0], 'name on the document'
    if lr:
        d['list'] = True
        if lr.get('code'):
            pk = ix.codes.get(_n(lr['code']))
            if pk:
                d['emp_id'], d['match'], d['others'] = pk, 'list in the zip', []
            else:
                d['emp_id'], d['match'] = None, ''
                d['notes'].append(N('The list gives employee code "{0}", which is not an employee you may add documents for.', lr['code']))
        if lr.get('type'):
            tgt = ix.words.get(_n(lr['type']))
            if tgt:
                d['type'], d['kind'], d['type_from'] = tgt[0], tgt[1], 'list in the zip'
            else:
                d['notes'].append(N('The list gives document type "{0}", which the company does not have.', lr['type']))
        for k in ('number', 'issue', 'expiry'):
            if lr.get(k):
                d[k], d['from'][k] = lr[k], 'list in the zip'
        d['notes'] += lr.get('errors') or []
    return d


def group(drafts, ix, folder):
    """Front and back (page 1, page 2 …) of one document of one employee → one row (merged into a PDF on import)."""
    rows, buckets = [], {}
    for d in drafts:
        side, stem = side_of(d['path'])
        ext = d['files'][0]['ext']
        if side is not None and d['emp_id'] and d['type'] and ext in MERGEABLE:
            key = (d['emp_id'], str(d['type']), os.path.dirname(d['path']).lower(), _n(stem))
            buckets.setdefault(key, []).append((side, d))
        else:
            rows.append(d)
    for key, items in buckets.items():
        if len(items) == 1:
            rows.append(items[0][1])
            continue
        items.sort(key=lambda x: (x[0], x[1]['path']))
        first = dict(items[0][1])
        first['files'] = [f for _, d in items for f in d['files']]
        first['notes'] = [n for _, d in items for n in d['notes']]
        for _, d in items[1:]:
            for k in ('number', 'issue', 'expiry'):
                better = d['from'].get(k) == 'document' and (d['verified'] or first['from'].get(k) != 'list in the zip')
                if d.get(k) and (not first.get(k) or (better and first['from'].get(k) == 'file name')):
                    first[k], first['from'][k] = d[k], d['from'].get(k)
            first['verified'] = first['verified'] or d['verified']
        first['merged'] = True
        rows.append(first)
    rows.sort(key=lambda d: d['path'].lower())
    for i, r in enumerate(rows):
        r['id'] = i + 1
    return rows


def _stored_hashes(emp_id, type_id=None):
    Doc = M('EmpManagement', 'Emp_Documents')
    out = {}
    qs = Doc.objects.filter(emp_id_id=emp_id).exclude(emp_doc_document='')
    if type_id:
        qs = qs.filter(document_type_id=type_id)
    for d in qs[:30]:
        try:
            with d.emp_doc_document.open('rb') as fh:
                out[hashlib.sha256(fh.read()).hexdigest()] = d
        except Exception:
            continue
    return out


def evaluate(r, ix, folder, others=None):
    """Status, action and notes of one row, from its employee, type, number and dates (also after HR edits it)."""
    Doc = M('EmpManagement', 'Emp_Documents')
    notes = [n for n in r.get('notes', []) if not (isinstance(n, dict) and n.get('auto'))]
    r['employee'] = ix.emp_label(r['emp_id']) if r.get('emp_id') else ''
    r['type_label'] = ix.type_label(r['type']) if r.get('type') else ''
    r['action'], r['existing'] = '', None
    if r.get('skip'):
        r['status'] = 'skipped'
        r['notes'] = notes
        return r
    st = 'ready'
    if not r.get('emp_id'):
        st = 'unmatched'
        notes.append(N('No employee code in the folder or file name, and no number or name on the document that matches an employee. Choose the employee.', auto=True))
    else:
        if r.get('match') == 'name on the document':
            st = 'review'
            notes.append(N('Matched by the name on the document – check the employee.', auto=True))
        if r.get('others'):
            st = 'review'
            notes.append(N('The path also names: {0}. Check the employee.', ', '.join(ix.emp_label(x) for x in r['others']), auto=True))
    if not r.get('type'):
        st = 'review' if st == 'ready' else st
        notes.append(N('Choose the document type.', auto=True))
    for k, label in (('number', 'document number'), ('issue', 'issue date'), ('expiry', 'expiry date')):
        if not r.get(k):
            st = 'review' if st == 'ready' else st
            notes.append(N('Enter the {0}.', label, auto=True))
    if r.get('issue') and r.get('expiry') and r['issue'] >= r['expiry']:
        st = 'review' if st == 'ready' else st
        notes.append(N('The issue date is not before the expiry date.', auto=True))
    if st != 'unmatched' and r.get('emp_id') and r.get('number'):
        num = str(r['number']).strip()
        hashes = {f['hash'] for f in r['files']}
        type_id = r['type'] if isinstance(r.get('type'), int) else None
        existing = Doc.objects.filter(emp_doc_number__iexact=num).select_related('emp_id', 'document_type').first()
        stored = _stored_hashes(r['emp_id'], type_id)
        same_file = next((d for h, d in stored.items() if h in hashes), None)
        twin = (others or {}).get(_n(num))
        if twin is not None and twin != r['id']:
            st = 'duplicate'
            notes.append(N('Another file in this zip has the same document number (row {0}).', twin, auto=True))
        elif same_file is not None:
            st = 'duplicate'
            notes.append(N('This file is already on the employee\'s document {0}.', same_file.emp_doc_number, auto=True))
        elif existing is not None:
            r['existing'] = existing.pk
            if existing.emp_id_id != r['emp_id']:
                st = 'conflict'
                notes.append(N('Number {0} is on a document of another employee ({1}).', num, f'{existing.emp_id.emp_code} – {S.full_name(existing.emp_id)}', auto=True))
            elif r.get('expiry') and existing.emp_doc_expiry_date and r['expiry'] == existing.emp_doc_expiry_date.isoformat():
                st = 'duplicate'
                notes.append(N('The employee already has this document (same number and expiry date).', auto=True))
            elif r.get('expiry') and existing.emp_doc_expiry_date and r['expiry'] > existing.emp_doc_expiry_date.isoformat():
                r['action'] = 'update'
            elif r.get('expiry'):
                st = 'conflict'
                notes.append(N('The employee\'s document with this number has a later expiry date ({0}).', existing.emp_doc_expiry_date.isoformat(), auto=True))
        elif r.get('type'):
            r['action'] = 'new'
            kind = r.get('kind') or (ix.kind_of_type.get(type_id) if type_id else None)
            if kind and type_id:
                cur = Doc.objects.filter(emp_id_id=r['emp_id'], document_type_id=type_id, is_active=True).order_by('-emp_doc_expiry_date').first()
                if cur is not None and r.get('expiry'):
                    if r['expiry'] >= cur.emp_doc_expiry_date.isoformat():
                        r['action'] = 'replace'
                        notes.append(N('The current document {0} (expiry {1}) will be kept as history.', cur.emp_doc_number, cur.emp_doc_expiry_date.isoformat(), auto=True))
                    else:
                        r['action'] = 'history'
    r['status'] = st
    r['notes'] = notes
    return r


def evaluate_all(st, ix):
    return evaluate_rows(st.get('rows') or [], ix)


def evaluate_rows(rows, ix):
    seen = {}
    for r in sorted(rows, key=lambda x: min(f['i'] for f in x['files'])):   # the first file of the zip with a number keeps it
        if r.get('number') and not r.get('skip') and r.get('emp_id'):
            seen.setdefault(_n(r['number']), r['id'])
    for r in rows:
        evaluate(r, ix, None, seen)
    return rows


# ------------------------------------------------------------------ edits by HR
def edit(st, changes, ix):
    """changes: [{id, emp_id | employee_code, type, number, issue, expiry, skip}] → the rows, evaluated again."""
    from EmpManagement.bulk_import import _date
    rows = {r['id']: r for r in st.get('rows') or []}
    errors = []
    for c in changes or []:
        r = rows.get(c.get('id'))
        if r is None:
            continue
        if 'employee_code' in c and c['employee_code'] not in (None, ''):
            pk = ix.codes.get(_n(c['employee_code']))
            if pk is None:
                errors.append({'id': r['id'], 'detail': tr('Employee code "{0}" was not found in your branches.', c['employee_code'])})
            else:
                r['emp_id'], r['match'], r['others'] = pk, 'chosen', []
        if 'emp_id' in c:
            pk = int(c['emp_id']) if str(c.get('emp_id') or '').isdigit() else None
            if pk is not None and pk not in ix.emps:
                errors.append({'id': r['id'], 'detail': tr('You may not add documents for this employee.')})
            else:
                r['emp_id'], r['match'], r['others'] = pk, ('chosen' if pk else ''), []
        if 'type' in c:
            t = c['type']
            if str(t).isdigit() and int(t) in ix.types:
                r['type'], r['kind'], r['type_from'] = int(t), ix.kind_of_type.get(int(t)), 'chosen'
            elif isinstance(t, str) and t.startswith('kind:') and t[5:] in S.KIND_LABEL:
                r['type'], r['kind'], r['type_from'] = t, t[5:], 'chosen'
            elif t in (None, ''):
                r['type'] = r['kind'] = None
        if 'number' in c:
            r['number'] = str(c['number'] or '').strip()[:50] or None
            r['from']['number'] = 'chosen'
        for k in ('issue', 'expiry'):
            if k in c:
                try:
                    v = _date(c[k])
                    r[k] = v.isoformat() if v else None
                    r['from'][k] = 'chosen'
                except ValueError:
                    errors.append({'id': r['id'], 'detail': tr('Enter a valid date (YYYY-MM-DD).')})
        if 'skip' in c:
            r['skip'] = bool(c['skip'])
    evaluate_all(st, ix)
    st['counts'] = counts(st['rows'])
    return errors


# ------------------------------------------------------------------ import
def _merge_pdf(parts):
    from PIL import Image, ImageOps
    from pypdf import PdfReader, PdfWriter
    w = PdfWriter()
    for ext, data in parts:
        if ext == '.pdf':
            for p in PdfReader(io.BytesIO(data)).pages:
                w.add_page(p)
        else:
            img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert('RGB')
            buf = io.BytesIO()
            img.save(buf, 'PDF', resolution=150)
            for p in PdfReader(io.BytesIO(buf.getvalue())).pages:
                w.add_page(p)
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


def _file_of(r, folder):
    parts = []
    for f in r['files']:
        with open(os.path.join(folder, f"{f['i']:05d}{f['ext']}"), 'rb') as fh:
            parts.append((f['ext'], fh.read()))
    base = os.path.basename(r['files'][0]['path'])
    if len(parts) == 1:
        return base, parts[0][1]
    stem = side_of(r['files'][0]['path'])[1] or os.path.splitext(base)[0]
    return f'{stem}.pdf', _merge_pdf(parts)


IDENT_KEEP = ('passport_no', 'passport_issue_date', 'passport_expiry_date', 'visa_number', 'visa_issue_date', 'visa_expiry_date',
              'emirates_id', 'emirates_id_issue_date', 'emirates_id_expiry_date', 'labour_card_no', 'labour_card_issue_date',
              'labour_card_expiry_date', 'driving_licence_no', 'driving_licence_issue_date', 'driving_licence_expiry_date',
              'insurance_card_no', 'insurance_issue_date', 'insurance_expiry_date', 'doc_links')


def _val(v):
    return v.isoformat() if isinstance(v, date) else v


def do_import(job, st, request, update_identity=True):
    """Import the ready rows. Returns the result for the screen."""
    from Chatter.tracking import paused
    from .models import DocumentImport, EmployeeIdentity
    Doc = M('EmpManagement', 'Emp_Documents')
    user = request.user
    folder = staging(job, st.get('schema'))
    ix = Index(_scope(S.user_branches(request)))
    st = get(job) or st
    evaluate_all(st, ix)
    rows = [r for r in st['rows'] if r['status'] == 'ready']
    created, replaced, changed, ident_before, log_rows, per_emp, failed = [], {}, {}, {}, [], {}, []
    with paused():
        for r in rows:
            try:
                with transaction.atomic():
                    name, data = _file_of(r, folder)
                    t = r['type']
                    if isinstance(t, str) and t.startswith('kind:'):
                        t = S.doc_type(t[5:], create=True).pk
                    emp = ix.emps[r['emp_id']]
                    iss, exp = date.fromisoformat(r['issue']), date.fromisoformat(r['expiry'])
                    if r['action'] == 'update':
                        d = Doc.objects.get(pk=r['existing'])
                        changed[str(d.pk)] = {'file': d.emp_doc_document.name or '', 'issue': _val(d.emp_doc_issued_date),
                                              'expiry': _val(d.emp_doc_expiry_date), 'active': d.is_active}
                        d.emp_doc_issued_date, d.emp_doc_expiry_date, d.is_active, d.updated_by = iss, exp, True, user
                        d.emp_doc_document.save(name, ContentFile(data), save=False)
                        d.save()
                    else:
                        d = Doc(emp_id=emp, document_type_id=t, emp_doc_number=str(r['number']).strip(), emp_doc_issued_date=iss,
                                emp_doc_expiry_date=exp, is_active=r['action'] != 'history', created_by=user, updated_by=user)
                        d.emp_doc_document.save(name, ContentFile(data), save=False)
                        d.save()
                        created.append(d.pk)
                        if r['action'] == 'replace':
                            old = Doc.objects.filter(emp_id_id=emp.pk, document_type_id=t, is_active=True).exclude(pk=d.pk)
                            for o in old:
                                replaced[str(d.pk)] = replaced.get(str(d.pk), []) + [o.pk]
                            old.update(is_active=False)
                    kind = r.get('kind') or ix.kind_of_type.get(t)
                    if update_identity and kind and r['action'] in ('new', 'replace', 'update'):
                        e = next(k for k in S.DOC_KINDS if k[0] == kind)
                        ident = EmployeeIdentity.objects.filter(employee_id=emp.pk).first() or EmployeeIdentity(employee_id=emp.pk)
                        cur_exp = getattr(ident, e[4])
                        if cur_exp is None or exp >= cur_exp:
                            if str(emp.pk) not in ident_before:
                                ident_before[str(emp.pk)] = {'new': ident.pk is None, **{f: _val(getattr(ident, f)) for f in IDENT_KEEP}}
                            setattr(ident, e[2], d.emp_doc_number[:ident._meta.get_field(e[2]).max_length])
                            setattr(ident, e[3], iss)
                            setattr(ident, e[4], exp)
                            ident.doc_links = dict(ident.doc_links or {}, **{kind: d.pk})
                            probs = dict(ident.doc_problems or {})
                            probs.pop(kind, None)
                            ident.doc_problems = probs
                            ident.updated_by_id = user.pk
                            ident.save()
                    per_emp.setdefault(emp.pk, []).append(f"{ix.type_label(t)} {d.emp_doc_number}")
                    r['result'] = 'imported'
                    log_rows.append({'file': r['path'], 'employee': r['employee'], 'type': ix.type_label(t), 'number': d.emp_doc_number,
                                     'action': r['action'], 'document_id': d.pk})
            except Exception as e:
                log.exception('doc bulk import row')
                r['result'] = 'failed'
                failed.append({'id': r['id'], 'path': r['path'], 'detail': str(e)[:200]})
    for emp_id, items in per_emp.items():
        S.note(ix.emps[emp_id], tr('Documents added from the upload {0}: {1}.', st.get('file_name', ''), ', '.join(items)), user)
    done = len(log_rows)
    batch_id = None
    from ZeoAdmin.models import ImportBatch
    if done:
        b = ImportBatch.objects.create(endpoint=ENDPOINT, label=tr('Employee document files'), file_name=st.get('file_name', '')[:200],
                                       model='EmpManagement.emp_documents', created_ids=created, rows=len(st['rows']), ok=done,
                                       failed=len(failed) + sum(1 for r in st['rows'] if r['status'] != 'ready'), user=user)
        batch_id = b.pk
    DocumentImport.objects.create(batch_id=batch_id, file_name=st.get('file_name', '')[:200], files=sum(len(r['files']) for r in st['rows']),
                                  created=len(created), updated=len(changed), skipped=len(st['rows']) - done, replaced=replaced,
                                  changed=changed, identity=ident_before, rows=log_rows[:5000], user_id=user.pk)
    left = [r for r in st['rows'] if r.get('result') != 'imported']
    st['rows'] = left
    st['counts'] = counts(left)
    st['imported'] = (st.get('imported') or 0) + done
    if not left:
        shutil.rmtree(folder, ignore_errors=True)
        st['state'] = 'done'
    _job(job, **st)
    return {'imported': done, 'created': len(created), 'updated': len(changed), 'replaced': sum(len(v) for v in replaced.values()),
            'failed': failed, 'batch': batch_id, 'left': len(left), 'counts': st['counts']}


# ------------------------------------------------------------------ undo (ZeoAdmin.imports.undo calls these)
def before_undo(batch):
    """Give the updated documents back their file and dates, and the identity details their values."""
    from .models import DocumentImport, EmployeeIdentity
    run_ = DocumentImport.objects.filter(batch_id=batch.pk).first()
    if run_ is None:
        return None
    Doc = M('EmpManagement', 'Emp_Documents')
    for pk, old in (run_.changed or {}).items():
        d = Doc.objects.filter(pk=pk).first()
        if d is None:
            continue
        new_file = d.emp_doc_document.name
        Doc.objects.filter(pk=pk).update(emp_doc_document=old.get('file') or '', emp_doc_issued_date=old['issue'], emp_doc_expiry_date=old['expiry'],
                                         is_active=old.get('active', True))
        if new_file and new_file != old.get('file'):
            try:
                d.emp_doc_document.storage.delete(new_file)
            except Exception:
                pass
    for emp_id, old in (run_.identity or {}).items():
        ident = EmployeeIdentity.objects.filter(employee_id=int(emp_id)).first()
        if ident is None:
            continue
        if old.get('new'):
            ident.delete()
            continue
        for f in IDENT_KEEP:
            v = old.get(f)
            fld = ident._meta.get_field(f)
            if v and fld.get_internal_type() == 'DateField':
                v = date.fromisoformat(v)
            setattr(ident, f, v if v is not None else (None if fld.null else ('' if f != 'doc_links' else {})))
        ident.save()
    files = {pk: n for pk, n in Doc.objects.filter(pk__in=batch.created_ids or []).values_list('pk', 'emp_doc_document')}
    return run_, files


def after_undo(batch, state, left):
    if not state:
        return
    run_, files = state
    Doc = M('EmpManagement', 'Emp_Documents')
    from django.core.files.storage import default_storage
    for pk, name in files.items():
        if pk not in (left or []) and name:
            try:
                default_storage.delete(name)
            except Exception:
                pass
    for new, olds in (run_.replaced or {}).items():
        if int(new) not in (left or []):
            Doc.objects.filter(pk__in=olds).update(is_active=True)


# ------------------------------------------------------------------ files for the screen
def template_xlsx():
    import openpyxl
    from openpyxl.styles import Font
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'index'
    ws.append(['File', 'Employee Code', 'Document Type', 'Document Number', 'Issue Date', 'Expiry Date'])
    ws.append(['EMP001/passport.pdf', 'EMP001', 'Passport', 'N1234567', '15/06/2022', '14/06/2032'])
    ws.append(['Visa/EMP002.pdf', 'EMP002', 'Visa', '784202412345678', '01/02/2025', '31/01/2027'])
    for c in ws[1]:
        c.font = Font(bold=True)
    for col, w in zip('ABCDEF', (34, 16, 20, 20, 14, 14)):
        ws.column_dimensions[col].width = w
    h = wb.create_sheet('How it works')
    for line in (
        'Put this sheet in the zip as index.xlsx (or index.csv). It is optional: without it, ZEO reads the folders, file names and documents.',
        'File: the path of the file inside the zip (folders with /) or only its name.',
        'Employee Code, Document Type, Document Number, Issue Date and Expiry Date: what the list gives wins over the guesses.',
        'Dates as dd/mm/yyyy (or Excel dates).',
        'Without a list: a folder per employee (EMP001/passport.pdf), a folder per type (Passport/EMP001.pdf) or names like EMP001_Passport_2032-06-14.pdf.',
        'Front and back: name them "... front" and "... back" (or 1 and 2) – they become one PDF.',
    ):
        h.append([line])
    h.column_dimensions['A'].width = 130
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def report_csv(st):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(['Row', 'File(s)', 'Employee', 'Matched by', 'Document type', 'Number', 'Issue date', 'Expiry date', 'Status', 'Action', 'Notes'])
    for r in st.get('rows') or []:
        p = public_row(r)
        w.writerow([r['id'], ' + '.join(f['path'] for f in r['files']), r.get('employee', ''), tr(MATCH.get(r.get('match') or '', r.get('match') or '')),
                    p['type_label'], r.get('number') or '', r.get('issue') or '', r.get('expiry') or '', p['status_label'], p['action_label'],
                    ' '.join(p['notes_all'])])
    for s in st.get('skipped') or []:
        w.writerow(['', s['path'], '', '', '', '', '', '', tr('Not imported'), '', say(s['why'])])
    return '﻿' + out.getvalue()


MATCH = {'code in the folder': 'Code in the folder', 'code in the file name': 'Code in the file name', 'number on the document': 'Number on the document',
         'name on the document': 'Name on the document', 'list in the zip': 'From the list', 'chosen': 'Chosen by you'}


def public_row(r):
    keys = ('id', 'path', 'emp_id', 'employee', 'match', 'type', 'kind', 'type_from', 'number', 'issue', 'expiry', 'from',
            'status', 'action', 'verified', 'name_on_doc', 'merged', 'skip', 'read')
    d = {k: r.get(k) for k in keys}
    t = r.get('type')
    d['type_label'] = tr('{0} (new document type)', tr(S.KIND_LABEL[t[5:]])) if isinstance(t, str) and t.startswith('kind:') else (r.get('type_label') or '')
    d['status_label'] = tr(STATUS.get(r.get('status') or '', '')) if r.get('status') else ''
    d['action_label'] = tr(ACTIONS[r['action']]) if r.get('action') else ''
    d['notes_all'] = [say(n) for n in r.get('notes') or []]
    d['files'] = [{'i': f['i'], 'path': f['path'], 'size': f['size'], 'ext': f['ext']} for f in r['files']]
    return d


def public(st, job):
    out = {k: st.get(k) for k in ('state', 'step', 'processed', 'total', 'file_name', 'error', 'counts', 'listed', 'imported', 'lists', 'read')}
    out['job'] = job
    out['skipped'] = [{'path': x['path'], 'why': say(x['why'])} for x in st.get('skipped') or []]
    out['rows'] = [public_row(r) for r in st.get('rows') or []]
    return out
