"""v1.73.9 – Document Center phase 4: required-document checklists, the external upload portal, controlled templates and forms,
e-signature.

* Checklists: per kind of record (employee, project, partner …) the documents it must have, one category per item; a record's
  status (received, expired, to review, missing) and the gaps of all records.
* External portal: a link (no account) through which an outsider uploads the documents asked for into the record's folder; each
  upload is "Submitted" until someone with the upload right accepts (published) or rejects it.
* Templates and forms: HTML with merge fields and questions; only a published template makes documents (a PDF registered in the
  record's folder with the answers as its fields); every published version is kept.
* E-signature: internal and external signers, in order or all at once, typed name + drawn signature, IP and time; when all have
  signed, the file + a signature certificate page become a new version (its SHA-256 can be verified later).
"""
from zeo.tz import today as _zt_today   # v1.73.16 the user's / company's day, not the server's UTC day
import base64
import hashlib
import io
import logging
import re
import secrets
from datetime import timedelta
from html import escape

from django.apps import apps
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, transaction
from django.utils import timezone

from Translations.i18n import tr

from . import models as Mo
from . import services as S
from .services import Refused

log = logging.getLogger(__name__)
M = apps.get_model
GONE = ('rejected', 'superseded', 'expired', 'archived', 'obsolete', 'disposed')
PORTAL_MAX_MB = 20
PORTAL_EXT = {'.pdf', '.jpg', '.jpeg', '.png', '.heic', '.webp', '.doc', '.docx', '.xls', '.xlsx', '.txt', '.zip'}


def _key(label, used):
    k = re.sub(r'[^a-z0-9]+', '_', (label or '').lower()).strip('_')[:30] or 'item'
    base, n = k, 2
    while k in used:
        k, n = f'{base}_{n}', n + 1
    used.add(k)
    return k


def _system():
    return S.Who(user=None)


# ================================================================== checklists
def clean_checklist(data, current=None):
    probs = {}
    name = ' '.join(str(data.get('name') or '').split())[:120]
    if not name:
        probs['name'] = tr('Required.')
    provider = data.get('provider') or 'employee'
    if provider not in dict(Mo.DocChecklist.PROVIDERS):
        probs['provider'] = tr('Choose one of the options.')
    items, used = [], set()
    cats = set(Mo.DocCategory.objects.filter(active=True).values_list('pk', flat=True))
    for i, it in enumerate(data.get('items') or []):
        label = ' '.join(str((it or {}).get('label') or '').split())[:120]
        try:
            cid = int(it.get('category_id'))
        except (TypeError, ValueError):
            cid = None
        if not label and not cid:
            continue
        if cid not in cats:
            probs[f'items.{i}'] = tr('Choose an active category.')
            continue
        if not label:
            label = Mo.DocCategory.objects.get(pk=cid).name
        k = it.get('key') if it.get('key') and it.get('key') not in used else _key(label, used)
        used.add(k)
        items.append({'key': k, 'label': label, 'category_id': cid, 'required': it.get('required') is not False,
                      'needs_expiry': bool(it.get('needs_expiry')), 'note': str(it.get('note') or '')[:255]})
    if not items and 'items' not in probs:
        probs['items'] = tr('Add at least one document.')
    if name and provider and Mo.DocChecklist.objects.filter(provider=provider, name__iexact=name).exclude(pk=getattr(current, 'pk', None)).exists():
        probs['name'] = tr('A checklist with this name exists.')
    if probs:
        raise Refused(tr('Check the marked fields.'), problems=probs)
    ints = lambda v: [int(x) for x in (v or []) if str(x).isdigit()]  # noqa: E731
    return {'name': name, 'provider': provider, 'description': str(data.get('description') or '')[:255], 'items': items,
            'branch_ids': ints(data.get('branch_ids')), 'department_ids': ints(data.get('department_ids')) if provider == 'employee' else [],
            'active': data.get('active') is not False}


def _record(provider, record_id):
    Model = S._model(provider)
    return Model.objects.filter(pk=record_id).first() if Model else None


def _branch_of(provider, obj):
    a = S.PROVIDERS[provider][4]
    return getattr(obj, a, None) if a else None


def checklists_for(provider, obj):
    out = []
    br = _branch_of(provider, obj)
    dept = getattr(obj, 'emp_dept_id_id', None) if provider == 'employee' else None
    for c in Mo.DocChecklist.objects.filter(provider=provider, active=True):
        if c.branch_ids and br not in c.branch_ids:
            continue
        if c.department_ids and dept not in c.department_ids:
            continue
        out.append(c)
    return out


def _cat_ids(cid):
    """the category and its subcategories"""
    ids, todo = {cid}, [cid]
    while todo:
        kids = list(Mo.DocCategory.objects.filter(parent_id__in=todo).values_list('pk', flat=True))
        todo = [k for k in kids if k not in ids]
        ids.update(todo)
    return ids


def _folder_docs(folder):
    if folder is None:
        return []
    ids = [f.pk for f in S.descendants(folder) if not f.deleted_at]
    return list(Mo.Document.objects.filter(folder_id__in=ids, deleted_at__isnull=True).exclude(status__in=GONE).select_related('category'))


def record_status(provider, obj, checklists=None):
    today = _zt_today()
    folder = S.record_folder(provider, obj, create=False)
    docs = _folder_docs(folder)
    out = []
    for c in (checklists if checklists is not None else checklists_for(provider, obj)):
        rows = []
        for it in c.items:
            cids = _cat_ids(it['category_id'])
            mine = sorted([d for d in docs if d.category_id in cids], key=lambda d: (d.status != 'published', -(d.pk or 0)))
            d = mine[0] if mine else None
            if d is None:
                st = 'missing'
            elif d.status in ('submitted', 'draft', 'under_review', 'pending_approval', 'revision_required') and 'portal' in (d.tags or []):
                st = 'to_review'
            elif d.expiry_date and d.expiry_date < today:
                st = 'expired'
            elif it.get('needs_expiry') and not d.expiry_date:
                st = 'no_expiry'
            else:
                st = 'ok'
            rows.append({**it, 'state': st, 'document': {'id': d.pk, 'number': d.number, 'title': d.title, 'status': d.status,
                                                          'expiry_date': d.expiry_date} if d else None})
        req = [r for r in rows if r['required']]
        done = [r for r in req if r['state'] == 'ok']
        out.append({'checklist': {'id': c.pk, 'name': c.name}, 'items': rows, 'required': len(req), 'done': len(done),
                    'percent': round(100 * len(done) / len(req)) if req else 100})
    return {'folder': folder.pk if folder else None, 'checklists': out}


def gaps(who, provider, checklist_id=None, limit=2000):
    """records of the kind with a required item not received (or expired / to review), the user's branches only"""
    Model = S._model(provider)
    if Model is None:
        return []
    qs = Model.objects.all()
    if provider == 'employee':
        qs = qs.filter(is_active=True)
    lists = [c for c in Mo.DocChecklist.objects.filter(provider=provider, active=True) if not checklist_id or c.pk == int(checklist_id)]
    out = []
    for obj in qs[:limit]:
        br = _branch_of(provider, obj)
        if not who.branch_ok(br):
            continue
        mine = [c for c in lists if (not c.branch_ids or br in c.branch_ids) and
                (not c.department_ids or getattr(obj, 'emp_dept_id_id', None) in c.department_ids)]
        if not mine:
            continue
        st = record_status(provider, obj, mine)
        for c in st['checklists']:
            bad = [i for i in c['items'] if i['required'] and i['state'] != 'ok']
            if bad:
                out.append({'provider': provider, 'record_id': obj.pk, 'record': S.PROVIDERS[provider][3](obj), 'folder': st['folder'],
                            'checklist': c['checklist'], 'percent': c['percent'],
                            'missing': [{'key': i['key'], 'label': i['label'], 'state': i['state'], 'category_id': i['category_id']} for i in bad]})
    return out


# ================================================================== the external upload portal
def request_state(r):
    if r.state in ('revoked', 'completed'):
        return r.state
    if r.expires_at <= timezone.now():
        return 'expired'
    return 'open'


def make_request(who, folder, items, recipient_name, recipient_email='', message='', days=14, checklist=None, max_files=20):
    S.need(S.folder_rights(folder, who), 'upload', tr('You may not upload into this folder.'))
    if folder.kind == 'root':
        raise Refused(tr('Open the folder of a record (or your own folder) to ask for documents.'))
    probs = {}
    name = ' '.join(str(recipient_name or '').split())[:200]
    if not name:
        probs['recipient_name'] = tr('Required.')
    email = str(recipient_email or '').strip()[:200]
    if email and not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email):
        probs['recipient_email'] = tr('Enter a valid e-mail.')
    try:
        days = int(days or 14)
        if not 1 <= days <= 90:
            raise ValueError
    except (TypeError, ValueError):
        probs['days'] = tr('Between {0} and {1}.', 1, 90)
    cats = set(Mo.DocCategory.objects.filter(active=True).values_list('pk', flat=True))
    clean, used = [], set()
    for it in items or []:
        label = ' '.join(str((it or {}).get('label') or '').split())[:120]
        cid = it.get('category_id')
        cid = int(cid) if str(cid or '').isdigit() and int(cid) in cats else None
        if not label and cid:
            label = Mo.DocCategory.objects.get(pk=cid).name
        if not label:
            continue
        k = it.get('key') if it.get('key') and it.get('key') not in used else _key(label, used)
        used.add(k)
        clean.append({'key': k, 'label': label, 'category_id': cid})
    if not clean:
        probs['items'] = tr('Add at least one document.')
    if probs:
        raise Refused(tr('Check the marked fields.'), problems=probs)
    r = Mo.UploadRequest.objects.create(token=secrets.token_urlsafe(32), folder=folder, checklist=checklist, items=clean, recipient_name=name,
                                        recipient_email=email, message=str(message or '')[:2000], expires_at=timezone.now() + timedelta(days=days),
                                        max_files=max(1, min(int(max_files or 20), 50)), created_by_id=who.uid)
    S.log_act(who, 'upload_requested', f'{name}: ' + ', '.join(i['label'] for i in clean), folder=folder)
    return r


def request_link(r, request=None):
    path = f'/zeo-apps/dms/p/{r.token}/?schema={connection.schema_name}'
    return request.build_absolute_uri(path) if request is not None else path


def revoke_request(who, r):
    S.need(S.folder_rights(r.folder, who), 'upload', tr('You may not change this request.'))
    r.state = 'revoked'
    r.save(update_fields=['state'])
    S.log_act(who, 'upload_request_revoked', r.recipient_name, folder=r.folder)


def portal_upload(r, key, upload, ip=''):
    if request_state(r) != 'open':
        raise Refused(tr('This link is no longer open.'), 403)
    it = next((x for x in r.items if x['key'] == key), None)
    if it is None:
        raise Refused(tr('Choose the document you are sending.'), problems={'item': tr('Required.')})
    if len(r.received or []) >= r.max_files:
        raise Refused(tr('The most files this link takes have been sent.'), 403)
    name = S.check_file(upload)
    ext = ('.' + name.rsplit('.', 1)[-1].lower()) if '.' in name else ''
    if ext not in PORTAL_EXT:
        raise Refused(tr('Send a PDF, a photo or an Office file.'), problems={'file': tr('Not allowed.')})
    if (getattr(upload, 'size', 0) or 0) > PORTAL_MAX_MB * 1024 * 1024:
        raise Refused(tr('A file can be at most {0} MB.', PORTAL_MAX_MB), problems={'file': tr('Too large.')})
    who = _system()
    who.uid = r.created_by_id
    with transaction.atomic():
        doc, _dup = S.register(who, r.folder, upload, {'title': f"{it['label']} – {r.recipient_name}"[:255], 'category': it.get('category_id') or '',
                                                       'tags': ['portal'], 'remarks': tr('Sent through the upload link by {0}', r.recipient_name),
                                                       'owner': r.created_by_id})
        Mo.Document.objects.filter(pk=doc.pk).update(status='submitted')   # waits for review before it counts
        rec = list(r.received or []) + [{'key': key, 'document_id': doc.pk, 'file_name': name, 'at': timezone.now().isoformat()}]
        got = {x['key'] for x in rec}
        upd = {'received': rec}
        if all(i['key'] in got for i in r.items):
            upd.update(state='completed', completed_at=timezone.now())
        Mo.UploadRequest.objects.filter(pk=r.pk).update(**upd)
        Mo.Activity.objects.create(document=doc, folder=r.folder, user_id=None, action='portal_upload', detail=f'{r.recipient_name}: {it["label"]}'[:500], ip=ip[:64])
    if r.created_by_id:
        doc.refresh_from_db()
        S._todo(r.created_by_id, S.en('{0} sent {1} – accept or reject it', r.recipient_name, it['label']), doc)
    return doc


def review_upload(who, doc, accept, reason=''):
    S.need(S.doc_rights(doc, who), 'upload', tr('You may not review this document.'))
    if doc.status != 'submitted' or 'portal' not in (doc.tags or []):
        raise Refused(tr('Only a document sent through an upload link and waiting for review can be accepted or rejected.'))
    if accept:
        if doc.controlled:
            doc.status = 'draft'   # goes through its workflow like any controlled document
        else:
            doc.status = 'published' if doc.published_id else 'draft'
        doc.save(update_fields=['status', 'updated_at'])
        S.log_act(who, 'portal_accepted', '', document=doc)
    else:
        if not str(reason or '').strip():
            raise Refused(tr('Give the reason.'), problems={'reason': tr('Required.')})
        doc.status = 'rejected'
        doc.remarks = (doc.remarks + '\n' if doc.remarks else '') + tr('Rejected: {0}', reason)
        doc.save(update_fields=['status', 'remarks', 'updated_at'])
        S.log_act(who, 'portal_rejected', reason[:400], document=doc)
        for r in Mo.UploadRequest.objects.filter(state='completed', received__contains=[{'document_id': doc.pk}]):
            Mo.UploadRequest.objects.filter(pk=r.pk).update(state='open', completed_at=None)   # the item is asked again
    S._close_todos(doc)
    return doc


# ================================================================== templates and forms
MERGE = re.compile(r'\{\{\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*\}\}')
QTYPES = ('text', 'textarea', 'number', 'date', 'select')


def clean_template(data, current=None):
    probs = {}
    name = ' '.join(str(data.get('name') or '').split())[:160]
    if not name:
        probs['name'] = tr('Required.')
    elif Mo.DocTemplate.objects.filter(name__iexact=name).exclude(pk=getattr(current, 'pk', None)).exists():
        probs['name'] = tr('A template with this name exists.')
    provider = data.get('provider') or ''
    if provider and provider not in S.PROVIDERS:
        probs['provider'] = tr('Choose one of the options.')
    cat = None
    if data.get('category'):
        cat = Mo.DocCategory.objects.filter(pk=data.get('category'), active=True).first()
        if cat is None:
            probs['category'] = tr('Choose an active category.')
    body = str(data.get('body') or '')
    if re.search(r'<\s*script|on\w+\s*=|javascript:', body, re.I):
        probs['body'] = tr('Scripts are not allowed in a template.')
    qs, used = [], set()
    for i, q in enumerate(data.get('questions') or []):
        label = ' '.join(str((q or {}).get('label') or '').split())[:120]
        if not label:
            continue
        ft = q.get('field_type') if q.get('field_type') in QTYPES else 'text'
        opts = [str(o)[:80] for o in (q.get('options') or []) if str(o).strip()] if ft == 'select' else []
        if ft == 'select' and not opts:
            probs[f'questions.{i}'] = tr('A list needs its options.')
        k = re.sub(r'[^a-z0-9_]', '', str(q.get('key') or '').lower()) or _key(label, set(used))
        if k in used:
            k = _key(label, used)
        used.add(k)
        qs.append({'key': k, 'label': label, 'field_type': ft, 'options': opts, 'required': bool(q.get('required'))})
    known = {'company', 'today', 'record', 'employee', 'answer'}
    for f in MERGE.findall(body):
        if f.split('.')[0] not in known:
            probs['body'] = tr('Unknown merge field {{ {0} }}.', f)
            break
        if f.startswith('answer.') and f.split('.', 1)[1] not in used:
            probs['body'] = tr('{{ {0} }} has no question with the key {1}.', f, f.split('.', 1)[1])
            break
    if probs:
        raise Refused(tr('Check the marked fields.'), problems=probs)
    return {'name': name, 'provider': provider, 'category': cat, 'body': body, 'questions': qs}


def context(provider, obj, answers=None):
    from django.db import connection as cn
    comp = getattr(getattr(cn, 'tenant', None), 'name', '') or ''
    today = _zt_today()
    ctx = {'company': comp, 'today': today.strftime('%d/%m/%Y'), 'answer': {k: v for k, v in (answers or {}).items()}}
    if obj is not None:
        ctx['record'] = {'name': S.PROVIDERS[provider][3](obj) if provider in S.PROVIDERS else str(obj), 'id': obj.pk}
    if provider == 'employee' and obj is not None:
        e = obj
        full = ' '.join(x for x in [e.emp_first_name, e.emp_middle_name, e.emp_last_name] if x)
        ctx['employee'] = {'name': full, 'first_name': e.emp_first_name or '', 'code': e.emp_code or '',
                           'designation': str(getattr(e, 'emp_desgntn_id', '') or ''), 'department': str(getattr(e, 'emp_dept_id', '') or ''),
                           'branch': str(getattr(e, 'emp_branch_id', '') or ''), 'nationality': str(getattr(e, 'emp_nationality', '') or ''),
                           'joined': e.emp_joined_date.strftime('%d/%m/%Y') if e.emp_joined_date else '', 'email': e.emp_company_email or e.emp_personal_email or ''}
    return ctx


def render(body, ctx):
    def val(m):
        cur = ctx
        for part in m.group(1).split('.'):
            cur = cur.get(part, '') if isinstance(cur, dict) else ''
        return escape(str(cur if cur is not None else ''))
    return MERGE.sub(val, body or '')


def publish_template(who, t):
    if not who.dms_admin:
        raise Refused(tr('Only a Document Center administrator publishes templates.'), 403)
    if not (t.body or '').strip():
        raise Refused(tr('The template is empty.'), problems={'body': tr('Required.')})
    now = timezone.now()
    t.version += 1
    t.published_body, t.published_questions, t.state, t.published_at, t.published_by_id = t.body, t.questions, 'published', now, who.uid
    t.history = list(t.history or []) + [{'version': t.version, 'body': t.body, 'questions': t.questions, 'published_at': now.isoformat(), 'published_by': who.uid}]
    t.save()
    return t


def _pdf(html, title):
    from weasyprint import HTML
    page = (f'<!doctype html><html><head><meta charset="utf-8"><title>{escape(title)}</title><style>@page{{size:A4;margin:22mm 18mm}}'
            f'body{{font-family:"DejaVu Sans",sans-serif;font-size:11pt;line-height:1.5;color:#1d2033}}</style></head><body>{html}</body></html>')
    return HTML(string=page).write_pdf()


def generate(who, t, provider='', record_id=None, answers=None, title=''):
    if t.state != 'published' or not t.version:
        raise Refused(tr('Only a published template makes documents – publish it first.'))
    obj, folder = None, None
    if t.provider:
        if not record_id:
            raise Refused(tr('Choose the record.'), problems={'record': tr('Required.')})
        obj = _record(t.provider, record_id)
        if obj is None:
            raise Refused(tr('Choose the record.'), problems={'record': tr('Not found.')})
        folder = S.record_folder(t.provider, obj)
    else:
        folder = S.personal_folder(who)
    probs, ans = {}, {}
    for q in t.published_questions or []:
        v = (answers or {}).get(q['key'])
        v = '' if v is None else str(v).strip()
        if q.get('required') and not v:
            probs[q['key']] = tr('Required.')
        if v and q['field_type'] == 'number':
            try:
                float(v)
            except ValueError:
                probs[q['key']] = tr('A number.')
        if v and q['field_type'] == 'select' and v not in q.get('options', []):
            probs[q['key']] = tr('Choose one of the options.')
        ans[q['key']] = v[:2000]
    if probs:
        raise Refused(tr('Check the marked fields.'), problems=probs)
    html = render(t.published_body, context(t.provider, obj, ans))
    name = (title or t.name)[:200]
    pdf = _pdf(html, name)
    fname = re.sub(r'[^\w\- ]+', '', name).strip().replace(' ', '_')[:80] or 'document'
    upload = SimpleUploadedFile(f'{fname}.pdf', pdf, content_type='application/pdf')
    data = {'title': name, 'category': t.category_id or '', 'change_note': tr('Made from the template {0} (version {1})', t.name, t.version)}
    doc, _ = S.register(who, folder, upload, data)
    Mo.Document.objects.filter(pk=doc.pk).update(remarks=tr('Made from the template {0} (version {1})', t.name, t.version))
    S.log_act(who, 'generated', f'{t.name} v{t.version}', document=doc)
    return doc


# ================================================================== e-signature
def sign_request(who, doc, signers, sequential=True, message='', days=14):
    r = S.doc_rights(doc, who)
    if not ({'share', 'edit', 'admin'} & set(r)):
        raise Refused(tr('You may not send this document for signature.'), 403)
    v = doc.published or doc.current
    if v is None or not v.file:
        raise Refused(tr('The document has no file to sign.'))
    if doc.sign_requests.filter(state='out').exists():
        raise Refused(tr('The document is already out for signature – cancel that request first.'))
    probs, rows = {}, []
    U = M('UserManagement', 'CustomUser')
    for i, s in enumerate(signers or []):
        uid = s.get('user_id')
        if uid:
            u = U.objects.filter(pk=uid, is_active=True).first()
            if u is None:
                probs[f'signers.{i}'] = tr('Not found.')
                continue
            rows.append({'user_id': u.pk, 'name': (u.get_full_name() or u.username or u.email)[:200], 'email': (u.email or '')[:200]})
        else:
            name = ' '.join(str(s.get('name') or '').split())[:200]
            email = str(s.get('email') or '').strip()[:200]
            if not name:
                probs[f'signers.{i}'] = tr('Give the name.')
                continue
            if email and not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email):
                probs[f'signers.{i}'] = tr('Enter a valid e-mail.')
                continue
            rows.append({'user_id': None, 'name': name, 'email': email})
    if not rows and 'signers' not in probs:
        probs['signers'] = tr('Add at least one signer.')
    try:
        days = int(days or 14)
        if not 1 <= days <= 90:
            raise ValueError
    except (TypeError, ValueError):
        probs['days'] = tr('Between {0} and {1}.', 1, 90)
    if probs:
        raise Refused(tr('Check the marked fields.'), problems=probs)
    with transaction.atomic():
        sr = Mo.SignRequest.objects.create(document=doc, version=v, message=str(message or '')[:2000], sequential=bool(sequential),
                                           expires_at=timezone.now() + timedelta(days=days), created_by_id=who.uid)
        for n, s in enumerate(rows, 1):
            Mo.Signer.objects.create(request=sr, order=n, token=secrets.token_urlsafe(32), state='pending' if (n == 1 or not sequential) else 'waiting', **s)
        S.log_act(who, 'sign_requested', ', '.join(s['name'] for s in rows), document=doc)
    _tell_signers(sr)
    return sr


def _tell_signers(sr):
    for s in sr.signers.filter(state='pending', user_id__isnull=False):
        S._todo(s.user_id, S.en('Sign {0} {1}', sr.document.number, sr.document.title), sr.document)


def sign_state(sr):
    if sr.state == 'out' and sr.expires_at <= timezone.now():
        return 'expired'
    return sr.state


def sign_link(s, request=None):
    path = f'/zeo-apps/dms/sign/{s.token}/?schema={connection.schema_name}'
    return request.build_absolute_uri(path) if request is not None else path


PNG = re.compile(r'^data:image/png;base64,([A-Za-z0-9+/=]+)$')


def sign(s, typed_name, image='', ip='', ua='', user_id=None):
    sr = s.request
    if sign_state(sr) != 'out':
        raise Refused(tr('This signature request is closed.'), 403)
    if s.state != 'pending':
        raise Refused(tr('It is not your turn to sign yet.') if s.state == 'waiting' else tr('Already done.'), 403)
    if s.user_id and user_id != s.user_id:
        raise Refused(tr('Only {0} signs here.', s.name), 403)
    probs = {}
    tn = ' '.join(str(typed_name or '').split())[:200]
    if len(tn) < 2:
        probs['typed_name'] = tr('Type your full name.')
    img = str(image or '')
    if img:
        m = PNG.match(img)
        if not m or len(img) > 400_000:
            probs['image'] = tr('Draw the signature again.')
        else:
            try:
                base64.b64decode(m.group(1), validate=True)
            except Exception:   # noqa: BLE001
                probs['image'] = tr('Draw the signature again.')
    if probs:
        raise Refused(tr('Check the marked fields.'), problems=probs)
    with transaction.atomic():
        Mo.Signer.objects.filter(pk=s.pk).update(state='signed', typed_name=tn, image=img, signed_at=timezone.now(), ip=(ip or '')[:64], user_agent=(ua or '')[:255])
        Mo.Activity.objects.create(document=sr.document, user_id=user_id, action='signed', detail=f'{s.name} ({tn})'[:500], ip=(ip or '')[:64])
        if s.user_id:
            S._close_todos(sr.document, s.user_id)
        nxt = sr.signers.filter(state='waiting').order_by('order').first()
        if nxt is not None and sr.sequential:
            Mo.Signer.objects.filter(pk=nxt.pk).update(state='pending')
        if not sr.signers.exclude(state='signed').exists():
            finish(sr)
    _tell_signers(sr)
    s.refresh_from_db()
    return s


def decline(s, reason, ip='', user_id=None):
    sr = s.request
    if sign_state(sr) != 'out' or s.state != 'pending':
        raise Refused(tr('This signature request is closed.'), 403)
    if s.user_id and user_id != s.user_id:
        raise Refused(tr('Only {0} signs here.', s.name), 403)
    if not str(reason or '').strip():
        raise Refused(tr('Give the reason.'), problems={'reason': tr('Required.')})
    with transaction.atomic():
        Mo.Signer.objects.filter(pk=s.pk).update(state='declined', decline_reason=str(reason)[:500], signed_at=timezone.now(), ip=(ip or '')[:64])
        Mo.SignRequest.objects.filter(pk=sr.pk).update(state='declined', completed_at=timezone.now())
        Mo.Activity.objects.create(document=sr.document, user_id=user_id, action='sign_declined', detail=f'{s.name}: {reason}'[:500], ip=(ip or '')[:64])
        S._close_todos(sr.document)
    if sr.created_by_id:
        S._todo(sr.created_by_id, S.en('{0} declined to sign {1}: {2}', s.name, sr.document.number, str(reason)[:120]), sr.document)


def cancel(who, sr):
    if not (who.dms_admin or sr.created_by_id == who.uid):
        raise Refused(tr('Only the sender cancels the signature request.'), 403)
    if sr.state != 'out':
        raise Refused(tr('This signature request is closed.'))
    Mo.SignRequest.objects.filter(pk=sr.pk).update(state='cancelled', completed_at=timezone.now())
    S._close_todos(sr.document)
    S.log_act(who, 'sign_cancelled', '', document=sr.document)


def _certificate(sr, signers, file_sha):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    y = h - 25 * mm
    c.setFont('Helvetica-Bold', 15)
    c.drawString(20 * mm, y, 'Signature certificate')
    y -= 9 * mm
    c.setFont('Helvetica', 9.5)
    d = sr.document
    for line in (f'Document: {d.number} {d.title}'[:110], f'Version signed: {sr.version.label}  ·  file {sr.version.file_name}'[:110],
                 f'SHA-256 of the file signed: {file_sha}', f'Sent: {timezone.localtime(sr.created_at):%d/%m/%Y %H:%M}  ·  '
                 f'Completed: {timezone.localtime(sr.completed_at or timezone.now()):%d/%m/%Y %H:%M}'):
        c.drawString(20 * mm, y, line)
        y -= 5.5 * mm
    y -= 4 * mm
    for s in signers:
        if y < 50 * mm:
            c.showPage()
            y = h - 25 * mm
            c.setFont('Helvetica', 9.5)
        c.setFont('Helvetica-Bold', 11)
        c.drawString(20 * mm, y, f'{s.order}. {s.name}' + (f'  <{s.email}>' if s.email else ''))
        c.setFont('Helvetica', 9.5)
        y -= 5.5 * mm
        c.drawString(24 * mm, y, f'Typed name: {s.typed_name}  ·  signed {timezone.localtime(s.signed_at):%d/%m/%Y %H:%M:%S}  ·  IP {s.ip or "-"}  ·  '
                                 + ('ZEO user' if s.user_id else 'link sent by e-mail / message'))
        y -= 4 * mm
        m = PNG.match(s.image or '')
        if m:
            try:
                img = ImageReader(io.BytesIO(base64.b64decode(m.group(1))))
                c.drawImage(img, 24 * mm, y - 22 * mm, width=60 * mm, height=20 * mm, preserveAspectRatio=True, mask='auto')
            except Exception:   # noqa: BLE001
                pass
            y -= 24 * mm
        else:
            c.setFont('Helvetica-Oblique', 16)
            c.drawString(24 * mm, y - 9 * mm, s.typed_name)
            c.setFont('Helvetica', 9.5)
            y -= 13 * mm
        y -= 4 * mm
    c.setFont('Helvetica', 8)
    c.drawString(20 * mm, 15 * mm, 'Each signer agreed to sign electronically. Verify a copy in ZEO: Document Center › Signatures › Verify a file.')
    c.save()
    return buf.getvalue()


def finish(sr):
    """everyone signed: the file + the certificate page → a new version (in force when the signed version was)"""
    sr.refresh_from_db()
    v = sr.version
    signers = list(sr.signers.order_by('order'))
    with v.file.open('rb') as fh:
        raw = fh.read()
    file_sha = hashlib.sha256(raw).hexdigest()
    Mo.SignRequest.objects.filter(pk=sr.pk).update(state='completed', completed_at=timezone.now())
    sr.refresh_from_db()
    cert = _certificate(sr, signers, file_sha)
    out = cert
    if (v.file_name or '').lower().endswith('.pdf'):
        try:
            from pypdf import PdfReader, PdfWriter
            wr = PdfWriter()
            for p in PdfReader(io.BytesIO(raw)).pages:
                wr.add_page(p)
            for p in PdfReader(io.BytesIO(cert)).pages:
                wr.add_page(p)
            b = io.BytesIO()
            wr.write(b)
            out = b.getvalue()
        except Exception:   # noqa: BLE001 – an unreadable PDF: the certificate on its own (it names the file's SHA-256)
            log.exception('signed copy of %s', v.pk)
    base = (v.file_name or 'document').rsplit('.', 1)[0]
    up = SimpleUploadedFile(f'{base}-signed.pdf', out, content_type='application/pdf')
    doc = sr.document
    who = _system()
    who.uid = sr.created_by_id
    was_published = doc.published_id == v.pk
    nv = S.add_version(who, doc, up, tr('Signed by {0}', ', '.join(s.name for s in signers)))
    if doc.controlled and was_published:   # signing changes no content: the signed copy is in force at once
        now = timezone.now()
        Mo.Version.objects.filter(pk=v.pk).update(state='superseded')
        Mo.Version.objects.filter(pk=nv.pk).update(state='published', published_at=now)
        Mo.Document.objects.filter(pk=doc.pk).update(published=nv, status='published')
    Mo.SignRequest.objects.filter(pk=sr.pk).update(signed_version=nv)
    Mo.Activity.objects.create(document=doc, action='signed_copy', detail=f'{nv.label} sha256 {nv.sha256}'[:500])
    if sr.created_by_id:
        S._todo(sr.created_by_id, S.en('{0} {1} is signed by everyone', doc.number, doc.title), doc)
    return nv


def verify(upload):
    h = hashlib.sha256()
    for chunk in upload.chunks():
        h.update(chunk)
    sha = h.hexdigest()
    v = Mo.Version.objects.filter(sha256=sha).select_related('document').first()
    if v is None:
        return {'sha256': sha, 'found': False}
    sr = Mo.SignRequest.objects.filter(signed_version=v).first() or Mo.SignRequest.objects.filter(version=v).first()
    return {'sha256': sha, 'found': True, 'document': {'id': v.document_id, 'number': v.document.number, 'title': v.document.title}, 'version': v.label,
            'signed_copy': bool(sr and sr.signed_version_id == v.pk),
            'signers': [{'name': s.name, 'typed_name': s.typed_name, 'signed_at': s.signed_at, 'state': s.state}
                        for s in (sr.signers.all() if sr else [])]}
