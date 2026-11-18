"""v1.70.1 – Document Center services: rights, the folder tree, numbering, versions, workflow, distribution, share links, search."""
from zeo.tz import today as _zt_today   # v1.73.16 the user's / company's day, not the server's UTC day
import hashlib
import io
import logging
import mimetypes
import os
import re
import secrets
import zipfile
from datetime import date, timedelta

from django.apps import apps
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from Translations.i18n import tr

from . import models as Mo

log = logging.getLogger(__name__)
M = apps.get_model
ALL = set(Mo.RIGHTS)
MAX_FILE = 100 * 1024 * 1024
BLOCKED_EXT = {'.exe', '.bat', '.cmd', '.com', '.msi', '.ps1', '.sh', '.js', '.vbs', '.scr', '.jar', '.dll', '.html', '.htm', '.svg'}


def en(text, *a):
    """a text stored for later (to-dos, the log): in English – translated when it is shown"""
    return tr(text, *a, lang='en')


class Refused(Exception):
    def __init__(self, message, status=400, problems=None):
        super().__init__(message)
        self.status, self.problems = status, problems or {}


# ================================================================== who the user is
class Who:
    """the request's user for the Document Center: id, admin, permission codes, groups, branches, employee"""

    def __init__(self, request=None, user=None):
        from AccessControl.access import ctx
        self.request = request
        if request is not None:
            c = ctx(request)
            self.user, self.admin, self.codes, self.branches, self.emp = c.user, c.admin, set(c.codes), c.branches, c.emp
        else:   # jobs / tests
            self.user, self.admin, self.codes, self.branches, self.emp = user, True, set(), None, None
        self.uid = getattr(self.user, 'pk', None)
        self.dms_admin = self.admin or 'manage_dms' in self.codes
        self.groups = set()
        try:
            from tenant_users.tenants.models import UserTenantPermissions as UTP
            utp = UTP.objects.filter(profile_id=self.uid).first()
            if utp:
                self.groups = set(utp.groups.values_list('id', flat=True))
        except Exception:   # noqa: BLE001
            pass
        self.emp_id = getattr(self.emp, 'pk', None)
        self.dept_id = getattr(self.emp, 'emp_dept_id_id', None)
        self.branch_id = getattr(self.emp, 'emp_branch_id_id', None)
        self._fr = {}

    def has(self, *codes):
        return self.admin or any(c in self.codes for c in codes)

    def branch_ok(self, branch_id):
        return self.branches is None or branch_id in (None, 0) or branch_id in set(self.branches)


# ================================================================== providers: the system folders made from the apps' records
def _name_emp(e):
    return f"{e.emp_code or e.pk} – {' '.join(x for x in [e.emp_first_name, e.emp_last_name] if x)}"


PROVIDERS = {
    # key: (app, model, path, label(obj), branch attr, view codes, write codes)
    'employee': ('hrms', 'EmpManagement.emp_master', ['HR', 'Employees'], _name_emp, 'emp_branch_id_id',
                 ('view_emp_documents', 'view_emp_master'), ('add_emp_documents', 'change_emp_documents')),
    'project': ('projects', 'ProjectManagement.Project', ['Projects'], lambda o: o.title, None, ('view_project',), ('change_project', 'add_project')),
    'partner': (None, 'ZeoMasters.Partner', ['Business partners'], lambda o: f'{o.number} – {o.name}' if o.number else o.name, None,
                ('view_partner',), ('change_partner', 'add_partner')),
    'building': ('property', 'ZeoProperty.Building', ['Property', 'Buildings'], lambda o: f'{o.number} – {o.name}' if o.number else o.name, 'branch_id',
                 ('view_building',), ('change_building',)),
    'asset': ('assets', 'ZeoAssets.AssetCard', ['Assets'], lambda o: f'{o.code} – {o.name}' if o.code else o.name, 'branch_id',
              ('view_assetcard',), ('change_assetcard',)),
    'hse_site': ('hse', 'ZeoHSE.Site', ['HSE', 'Sites'], lambda o: f'{o.code} – {o.name}', 'branch_id', ('view_site',), ('change_site',)),
    'crm_account': ('crm', 'ZeoCRM.Contact', ['CRM', 'Accounts'], lambda o: f'{o.number} – {o.name}' if o.number else o.name, 'branch_id',
                    ('view_contact',), ('change_contact',)),
}
GENERAL, PERSONAL = 'General', 'My folders'


def provider_active(key):
    app = PROVIDERS[key][0]
    if app is None:
        return True
    try:
        from ZeoApps.services import is_active
        return bool(is_active(app))
    except Exception:   # noqa: BLE001
        return app == 'hrms'


def _model(key):
    try:
        return M(PROVIDERS[key][1])
    except (LookupError, ValueError):
        return None


def _root(name, parent=None):
    F = Mo.DocFolder
    f = F.objects.filter(name=name, parent=parent, kind='root', deleted_at__isnull=True).first()
    if f is None:
        f = F.objects.create(name=name, parent=parent, kind='root')
    return f


def ensure_roots():
    """General, My folders and the root folders of the active apps"""
    out = {'general': _root(GENERAL), 'personal': _root(PERSONAL)}
    for key, p in PROVIDERS.items():
        if not provider_active(key) or _model(key) is None:
            continue
        parent = None
        for n in p[2]:
            parent = _root(n, parent)
        if not parent.provider:
            Mo.DocFolder.objects.filter(pk=parent.pk).update(provider=key)
            parent.provider = key
        out[key] = parent
    return out


def provider_root(key):
    return Mo.DocFolder.objects.filter(kind='root', provider=key, deleted_at__isnull=True).first()


def record_folder(key, obj, create=True):
    """the folder of one record (made when missing; its name follows the record)"""
    F = Mo.DocFolder
    f = F.objects.filter(provider=key, record_id=obj.pk, kind='record').first()
    name = (PROVIDERS[key][3](obj) or str(obj.pk))[:200]
    br = getattr(obj, PROVIDERS[key][4], None) if PROVIDERS[key][4] else None
    if f is None and create:
        root = provider_root(key) or ensure_roots().get(key)
        if root is None:
            return None
        f = F.objects.create(name=name, parent=root, kind='record', provider=key, record_id=obj.pk, branch_id=br)
    elif f is not None and (f.name != name or f.branch_id != br):
        F.objects.filter(pk=f.pk).update(name=name, branch_id=br)
        f.name, f.branch_id = name, br
    return f


def sync_provider(key):
    """make the missing record folders of an app (when its root folder is opened)"""
    Model = _model(key)
    if Model is None or not provider_active(key):
        return 0
    have = set(Mo.DocFolder.objects.filter(provider=key, kind='record').values_list('record_id', flat=True))
    root = provider_root(key) or ensure_roots().get(key)
    n = 0
    qs = Model.objects.all()
    if key == 'employee':
        qs = qs.filter(Q(is_active=True) | Q(pk__in=Mo.Document.objects.filter(employee_id__isnull=False).values('employee_id')))
    new = []
    for o in qs.exclude(pk__in=have)[:5000]:
        br = getattr(o, PROVIDERS[key][4], None) if PROVIDERS[key][4] else None
        new.append(Mo.DocFolder(name=(PROVIDERS[key][3](o) or str(o.pk))[:200], parent=root, kind='record', provider=key, record_id=o.pk, branch_id=br))
        n += 1
    Mo.DocFolder.objects.bulk_create(new, batch_size=500)
    return n


def personal_folder(who):
    """the user's own private folder under My folders"""
    root = _root(PERSONAL)
    F = Mo.DocFolder
    f = F.objects.filter(parent=root, owner_id=who.uid, private=True, deleted_at__isnull=True).first()
    if f is None:
        nm = (getattr(who.user, 'get_full_name', lambda: '')() or getattr(who.user, 'username', '') or f'User {who.uid}')[:200]
        f = F.objects.create(name=nm, parent=root, kind='user', owner_id=who.uid, private=True, created_by_id=who.uid)
    return f


# ================================================================== rights
def ancestors(folder):
    out, f, seen = [], folder, set()
    while f is not None and f.pk not in seen:
        seen.add(f.pk)
        out.append(f)
        f = f.parent
    return out   # folder first, root last


def provider_of(folder):
    for f in ancestors(folder):
        if f.provider:
            return f.provider, (f if f.kind == 'record' else None)
    return None, None


def _grant_matches(g, who):
    if g.who == 'everyone':
        return True
    if g.who == 'user':
        return g.who_id == who.uid
    if g.who == 'group':
        return g.who_id in who.groups
    if g.who == 'branch':
        return g.who_id == who.branch_id or (who.branches is not None and g.who_id in set(who.branches))
    if g.who == 'department':
        return g.who_id == who.dept_id
    return False


def folder_rights(folder, who):
    """the set of rights of the user on a folder (inherited from the folders above while they inherit)"""
    if folder is None:
        return set()
    if folder.pk in who._fr:
        return who._fr[folder.pk]
    if who.dms_admin:
        who._fr[folder.pk] = set(ALL)
        return who._fr[folder.pk]
    rights = set()
    chain = ancestors(folder)
    private = next((f for f in chain if f.private), None)
    for f in chain:
        if f.owner_id and f.owner_id == who.uid:
            rights |= set(Mo.PRESETS['manager']) | {'dispose'}
        for g in f.grants.all():
            if _grant_matches(g, who):
                rights |= set(g.rights)
        if not f.inherit:
            break
    if private is not None and private.owner_id != who.uid and not rights:
        who._fr[folder.pk] = set()
        return set()
    # the app's own rights on its system folders
    key, rec = provider_of(folder)
    if key and private is None:
        p = PROVIDERS[key]
        if who.has(*p[5]):
            ok = True
            if rec is not None and p[4]:
                ok = who.branch_ok(rec.branch_id)
            if ok:
                rights |= {'view', 'download', 'print'}
                if who.has(*p[6]):
                    rights |= {'create', 'upload', 'edit', 'share', 'archive'}
        if key == 'employee' and rec is not None and rec.record_id == who.emp_id:
            rights |= {'view', 'download', 'print'}   # employees see their own folder
    # General: the rights of the old Document library carry on
    top = chain[-1]
    if top.kind == 'root' and top.name == GENERAL and not top.provider:
        if who.has('view_folder', 'view_document'):
            rights |= {'view', 'download', 'print'}
        if who.has('add_folder', 'add_document'):
            rights |= {'create', 'upload', 'edit'}
        if who.has('delete_document', 'delete_folder'):
            rights |= {'archive'}
    who._fr[folder.pk] = rights
    return rights


def doc_rights(doc, who):
    if who.dms_admin:
        return set(ALL)
    r = set(folder_rights(doc.folder, who))
    own = doc.owner_id == who.uid or doc.created_by_id == who.uid
    if own:
        r |= {'view', 'download', 'print', 'edit', 'upload', 'share', 'archive'}
    for g in doc.grants.all():
        if _grant_matches(g, who):
            r |= set(g.rights)
    listed = any(_grant_matches(g, who) for g in doc.grants.all())
    if doc.confidentiality == 'restricted' and not (own or listed):
        r = set()
    elif doc.confidentiality == 'confidential' and not (own or listed or {'review', 'approve', 'admin'} & r):
        r = set()
    # a recipient of a distribution reads the version sent to them
    if 'view' not in r and doc.distributions.filter(user_id=who.uid).exists():
        r |= {'view', 'download', 'print'}
    # a reviewer / approver of a waiting step reads the document
    if 'view' not in r and Mo.Approval.objects.filter(version__document=doc, approver_id=who.uid).exists():
        r |= {'view', 'download', 'print'}
    if doc.deleted_at is not None and 'admin' not in r and not own:
        r = set()
    return r


def need(rights, right, message=None):
    if right not in rights:
        raise Refused(message or tr('You do not have the "{0}" right here.', tr(Mo.RIGHT_LABELS.get(right, right))), 403)


# ================================================================== activity
def log_act(who, action, detail='', document=None, folder=None):
    try:
        ip = ''
        if who is not None and who.request is not None:
            ip = (who.request.META.get('HTTP_X_FORWARDED_FOR') or who.request.META.get('REMOTE_ADDR') or '').split(',')[0].strip()[:64]
        Mo.Activity.objects.create(document=document, folder=folder, user_id=getattr(who, 'uid', None), action=action, detail=str(detail)[:500], ip=ip)
    except Exception:   # noqa: BLE001
        log.exception('dms activity')


# ================================================================== folders
def clean_name(v, field='name', limit=200):
    v = ' '.join(str(v or '').split())
    if not v:
        raise Refused(tr('Enter the name.'), problems={field: tr('Required.')})
    if any(ch in v for ch in '/\\:*?"<>|'):
        raise Refused(tr('A name cannot contain / \\ : * ? " < > |'), problems={field: tr('Invalid.')})
    return v[:limit]


def create_folder(who, parent, name, private=False):
    if parent is None:
        raise Refused(tr('Choose where the folder goes.'))
    rights = folder_rights(parent, who)
    if parent.kind == 'root' and parent.name == PERSONAL and not parent.provider:
        parent = personal_folder(who)
        rights = folder_rights(parent, who)
    need(rights, 'create', tr('You may not create folders here.'))
    name = clean_name(name)
    if Mo.DocFolder.objects.filter(parent=parent, name__iexact=name, deleted_at__isnull=True).exists():
        raise Refused(tr('A folder "{0}" is already here.', name), problems={'name': tr('Already used.')})
    f = Mo.DocFolder.objects.create(name=name, parent=parent, kind='user', owner_id=who.uid, created_by_id=who.uid, private=bool(private),
                                    branch_id=parent.branch_id)
    log_act(who, 'folder_created', f'{path_of(f)}', folder=f)
    return f


def path_of(folder):
    return ' › '.join(reversed([f.name for f in ancestors(folder)]))


def rename_folder(who, f, name):
    if f.kind != 'user':
        raise Refused(tr('System folders take the name of their record.'))
    need(folder_rights(f, who), 'edit')
    name = clean_name(name)
    if Mo.DocFolder.objects.filter(parent=f.parent, name__iexact=name, deleted_at__isnull=True).exclude(pk=f.pk).exists():
        raise Refused(tr('A folder "{0}" is already here.', name), problems={'name': tr('Already used.')})
    old = f.name
    f.name = name
    f.save(update_fields=['name'])
    log_act(who, 'folder_renamed', f'{old} → {name}', folder=f)


def descendants(f):
    out, stack = [], [f]
    while stack:
        x = stack.pop()
        out.append(x)
        stack.extend(x.children.all())
    return out


def move_folder(who, f, target):
    if f.kind != 'user':
        raise Refused(tr('System folders cannot be moved.'))
    need(folder_rights(f, who), 'edit')
    need(folder_rights(target, who), 'create', tr('You may not create folders in the target folder.'))
    if target.pk in {x.pk for x in descendants(f)}:
        raise Refused(tr('A folder cannot go inside itself.'))
    if Mo.DocFolder.objects.filter(parent=target, name__iexact=f.name, deleted_at__isnull=True).exclude(pk=f.pk).exists():
        raise Refused(tr('A folder "{0}" is already there.', f.name))
    old = path_of(f)
    f.parent = target
    f.save(update_fields=['parent'])
    log_act(who, 'folder_moved', f'{old} → {path_of(f)}', folder=f)


def delete_folder(who, f):
    """to the trash with everything in it (restorable)"""
    if f.kind != 'user':
        raise Refused(tr('System folders cannot be deleted.'))
    need(folder_rights(f, who), 'archive', tr('You may not delete this folder.'))
    now = timezone.now()
    ids = [x.pk for x in descendants(f)]
    if Mo.Document.objects.filter(folder_id__in=ids, deleted_at__isnull=True, status__in=('submitted', 'under_review', 'pending_approval')).exists():
        raise Refused(tr('Documents in this folder are waiting for approval – finish or withdraw them first.'))
    held = _held(Mo.Document.objects.filter(folder_id__in=ids, deleted_at__isnull=True))
    if held:
        raise Refused(tr('{0} document(s) in this folder are under legal hold.', held))
    Mo.DocFolder.objects.filter(pk__in=ids, deleted_at__isnull=True).update(deleted_at=now, deleted_by_id=who.uid)
    Mo.Document.objects.filter(folder_id__in=ids, deleted_at__isnull=True).update(deleted_at=now, deleted_by_id=who.uid)
    log_act(who, 'folder_deleted', path_of(f), folder=f)


def _held(docs):
    """documents under a legal hold: their own (v1.73.6) or their employee's"""
    try:
        from ZeoAdmin.legalhold import held_ids
        ids = set(held_ids())
    except Exception:   # noqa: BLE001
        ids = set()
    return sum(1 for d in docs if getattr(d, 'legal_hold', False) or (d.employee_id and d.employee_id in ids))


def set_grants(who, target, rows, inherit=None):
    """replace the rights on a folder (or a document): rows = [{who, who_id, rights | preset}]"""
    is_doc = isinstance(target, Mo.Document)
    need(doc_rights(target, who) if is_doc else folder_rights(target, who), 'admin', tr('Only a manager of this folder may change its rights.'))
    clean = []
    for r in rows or []:
        w = r.get('who')
        if w not in dict(Mo.DocGrant.WHO):
            raise Refused(tr('Choose who gets the rights.'))
        wid = r.get('who_id')
        if w != 'everyone' and not str(wid or '').isdigit():
            raise Refused(tr('Choose the user, group, branch or department.'))
        rights = list(Mo.PRESETS.get(r.get('preset') or '', ())) or [x for x in (r.get('rights') or []) if x in ALL]
        if 'dispose' in rights and not who.dms_admin:
            raise Refused(tr('Only a Document Center administrator may give the "Dispose" right.'), 403)
        if not rights:
            raise Refused(tr('Give at least one right.'))
        if 'view' not in rights:
            rights.append('view')
        clean.append((w, int(wid) if w != 'everyone' else None, sorted(set(rights), key=Mo.RIGHTS.index)))
    with transaction.atomic():
        qs = target.grants.all()
        qs.delete()
        for w, wid, rights in clean:
            Mo.DocGrant.objects.create(**({'document': target} if is_doc else {'folder': target}), who=w, who_id=wid, rights=rights,
                                       created_by_id=who.uid)
        if inherit is not None and not is_doc:
            target.inherit = bool(inherit)
            target.save(update_fields=['inherit'])
    who._fr.clear()
    log_act(who, 'rights_changed', '; '.join(f'{w}:{wid or "*"}={"/".join(r)}' for w, wid, r in clean) or 'no rights',
            document=target if is_doc else None, folder=None if is_doc else target)


# ================================================================== numbering
def next_number(cat, doc, on=None):
    on = on or _zt_today()
    pat = (cat.number_pattern if cat else '') or '{cat}-{yyyy}-{seq:4}'
    dept = ''
    if doc.department_id:
        d = M('OrganisationManager', 'dept_master').objects.filter(pk=doc.department_id).first()
        dept = re.sub(r'[^A-Z0-9]', '', (getattr(d, 'dept_name', '') or '').upper())[:4]
    br = ''
    if doc.branch_id:
        b = M('OrganisationManager', 'brnch_mstr').objects.filter(pk=doc.branch_id).first()
        br = (getattr(b, 'branch_code', '') or '')[:10]
    comp = ''
    try:
        from SelfService.letters import company_name
        comp = ''.join(w[0] for w in (company_name() or '').split()[:3]).upper()
    except Exception:   # noqa: BLE001
        pass
    vals = {'company': comp or 'CO', 'branch': br or 'BR', 'dept': dept or 'GEN', 'cat': (cat.code if cat else 'DOC').upper(),
            'yyyy': f'{on.year}', 'yy': f'{on.year % 100:02d}', 'mm': f'{on.month:02d}'}
    reset = cat.number_reset if cat else 'yearly'
    scope = re.sub(r'\{seq(:\d+)?\}', '', pat)
    for k, v in vals.items():
        scope = scope.replace('{' + k + '}', v)
    key = f"{cat.pk if cat else 0}|{scope}|{on.year if reset in ('yearly', 'monthly') else ''}|{on.month if reset == 'monthly' else ''}"[:160]
    with transaction.atomic():
        c, _ = Mo.DocCounter.objects.select_for_update().get_or_create(key=key)
        c.value += 1
        c.save(update_fields=['value'])
        n = c.value
    out = pat
    for k, v in vals.items():
        out = out.replace('{' + k + '}', v)
    out = re.sub(r'\{seq(?::(\d+))?\}', lambda m: str(n).zfill(int(m.group(1) or 4)), out)
    return out[:60]


# ================================================================== files and versions
def read_text(fileobj, name):
    """text of a PDF, Word, text or CSV file (for search and comparison); '' when it cannot be read"""
    ext = os.path.splitext(name or '')[1].lower()
    try:
        fileobj.seek(0)
        data = fileobj.read()
        fileobj.seek(0)
    except Exception:   # noqa: BLE001
        return ''
    try:
        if ext == '.pdf':
            import pypdfium2 as pdfium
            doc = pdfium.PdfDocument(data)
            try:
                return '\n'.join(doc[i].get_textpage().get_text_range() for i in range(min(len(doc), 200)))[:200000]
            finally:
                doc.close()
        if ext == '.docx':
            import docx
            d = docx.Document(io.BytesIO(data))
            return '\n'.join(p.text for p in d.paragraphs)[:200000]
        if ext in ('.txt', '.csv', '.md', '.json', '.xml', '.log'):
            for enc in ('utf-8', 'cp1256', 'latin-1'):
                try:
                    return data.decode(enc)[:200000]
                except UnicodeDecodeError:
                    continue
    except Exception:   # noqa: BLE001
        log.info('text of %s could not be read', name, exc_info=True)
    return ''


def check_file(upload):
    name = os.path.basename(getattr(upload, 'name', '') or 'file')
    ext = os.path.splitext(name)[1].lower()
    if ext in BLOCKED_EXT:
        raise Refused(tr('Files of type {0} cannot be uploaded.', ext), problems={'file': tr('Not allowed.')})
    size = getattr(upload, 'size', None) or 0
    if size > MAX_FILE:
        raise Refused(tr('A file can be at most {0} MB.', MAX_FILE // (1024 * 1024)), problems={'file': tr('Too large.')})
    if size == 0:
        raise Refused(tr('The file is empty.'), problems={'file': tr('Empty.')})
    return name


def _sha(upload):
    h = hashlib.sha256()
    upload.seek(0)
    for chunk in iter(lambda: upload.read(1024 * 1024), b''):
        h.update(chunk)
    upload.seek(0)
    return h.hexdigest()


def add_version(who, doc, upload, note='', major=False, restored_from=None):
    """a new version of the document from an uploaded file (1.0 for the first, then 1.1 or 2.0)"""
    name = check_file(upload)
    last = doc.versions.order_by('-major', '-minor', '-id').first()
    if last is None:
        mj, mn = 1, 0
    elif major:
        mj, mn = last.major + 1, 0
    else:
        mj, mn = last.major, last.minor + 1
    v = Mo.Version(document=doc, major=mj, minor=mn, file_name=name[:255], size=getattr(upload, 'size', 0) or 0,
                   mime=(mimetypes.guess_type(name)[0] or '')[:120], sha256=_sha(upload), text=read_text(upload, name), change_note=(note or '')[:500],
                   author_id=who.uid, restored_from=restored_from, state='draft')
    v.file.save(name, upload, save=False)
    v.save()
    doc.current = v
    if not doc.controlled:   # a plain document: the new version is in force at once, the one before is superseded
        now = timezone.now()
        if doc.published_id:
            Mo.Version.objects.filter(pk=doc.published_id).update(state='superseded')
        v.state, v.published_at = 'published', now
        v.save(update_fields=['state', 'published_at'])
        doc.published, doc.status = v, 'published'
    doc.save(update_fields=['current', 'published', 'status', 'updated_at'])
    log_act(who, 'version_added' if last else 'uploaded', f'{v.label} {name}' + (f' – {note}' if note else ''), document=doc)
    try:   # v1.73.10 a scan or photo is read (OCR) so its words can be searched
        from .phase5 import after_version
        after_version(v)
    except Exception:   # noqa: BLE001
        log.exception('text of version %s', v.pk)
    return v


def register(who, folder, upload, data):
    """a new document in a folder with its first version"""
    rights = folder_rights(folder, who)
    need(rights, 'upload', tr('You may not upload into this folder.'))
    if folder.kind == 'root' and folder.provider:
        raise Refused(tr('Open the folder of a record to add its documents.'))
    if folder.kind == 'root' and folder.name == PERSONAL:
        folder = personal_folder(who)
    cat = None
    if data.get('category'):
        cat = Mo.DocCategory.objects.filter(pk=data.get('category'), active=True).first()
        if cat is None:
            raise Refused(tr('Choose an active category.'), problems={'category': tr('Not found.')})
    name = check_file(upload)
    title = ' '.join(str(data.get('title') or os.path.splitext(name)[0]).split())[:255]
    if not title:
        raise Refused(tr('Enter the title.'), problems={'title': tr('Required.')})
    conf = data.get('confidentiality') or (cat.default_confidentiality if cat else 'internal')
    if conf not in dict(Mo.CONFIDENTIALITY):
        raise Refused(tr('Choose the confidentiality.'), problems={'confidentiality': tr('Invalid.')})
    expiry = _date(data.get('expiry_date'), 'expiry_date')
    if cat and cat.expiry_required and not expiry:
        raise Refused(tr('This category needs an expiry date.'), problems={'expiry_date': tr('Required.')})
    effective = _date(data.get('effective_date'), 'effective_date')
    if expiry and effective and expiry < effective:
        raise Refused(tr('The expiry date is before the effective date.'), problems={'expiry_date': tr('Before the effective date.')})
    fields = _check_fields(cat, data.get('fields') or {}, who=who, create=True)
    key, rec = provider_of(folder)
    doc = Mo.Document(title=title, folder=folder, category=cat, confidentiality=conf, tags=_tags(data.get('tags')), remarks=str(data.get('remarks') or '')[:4000],
                      fields=fields, received_date=_date(data.get('received_date'), 'received_date'), effective_date=effective, expiry_date=expiry,
                      department_id=_int(data.get('department')), branch_id=_int(data.get('branch')) or folder.branch_id, owner_id=_int(data.get('owner')) or who.uid,
                      created_by_id=who.uid, controlled=bool(cat and cat.workflow_id), status='draft')
    if rec is not None and key in ('employee', 'project', 'partner'):
        setattr(doc, f'{key}_id', rec.record_id)
    for k in ('employee', 'project', 'partner'):
        if data.get(k) and str(data.get(k)).isdigit():
            setattr(doc, f'{k}_id', int(data.get(k)))
    with transaction.atomic():
        doc.save()
        doc.number = next_number(cat, doc)
        doc.save(update_fields=['number'])
        add_version(who, doc, upload, data.get('change_note') or '')
        from .lifecycle import refresh_retention
        refresh_retention(doc)   # v1.73.6: from the category's start event
    dup = Mo.Version.objects.filter(sha256=doc.current.sha256, document__deleted_at__isnull=True).exclude(document=doc).select_related('document').first()
    return doc, (dup.document if dup else None)


def _date(v, field):
    if v in (None, ''):
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        raise Refused(tr('Enter a valid date.'), problems={field: tr('Invalid date.')})


def _int(v):
    return int(v) if str(v or '').isdigit() else None


def _tags(v):
    if isinstance(v, str):
        v = v.split(',')
    return sorted({' '.join(str(x).split())[:40] for x in (v or []) if str(x).strip()})[:30]


def _check_fields(cat, values, current=None, who=None, create=False):
    """v1.70.7: the category's own fields and the fields of every document, with the form designer's types and checks"""
    from . import fields as F
    try:
        return F.check(F.for_document(cat), values, current, admin=bool(who and who.dms_admin), create=create)
    except F.Refused as e:
        raise Refused(str(e), problems=e.problems)


def set_folder_fields(who, folder, values, create=False):
    """v1.70.7: values of the folder fields (edit right on the folder)"""
    from . import fields as F
    if not create:
        need(folder_rights(folder, who), 'edit', tr('You may not change this folder.'))
    try:
        v = F.check(F.folder_fields(), values, folder.fields, admin=who.dms_admin, create=create)
    except F.Refused as e:
        raise Refused(str(e), problems=e.problems)
    if v != (folder.fields or {}):
        folder.fields = v
        folder.save(update_fields=['fields'])
        log_act(who, 'details_changed', tr('Folder fields'), folder=folder)
    return v


def update_meta(who, doc, data):
    r = doc_rights(doc, who)
    need(r, 'edit')
    if doc.controlled and (doc.published_id or doc.status in ('approved', 'pending_approval', 'under_review', 'submitted')):
        # a controlled document in force or in review: its register details are fixed – a change is a new revision
        allowed = {'tags', 'remarks'}
    else:
        allowed = None
    ch = []
    for k in ('title', 'confidentiality', 'remarks', 'tags', 'received_date', 'effective_date', 'expiry_date', 'owner', 'department', 'fields'):
        if k not in data or (allowed is not None and k not in allowed):
            continue
        if k == 'title':
            v = ' '.join(str(data[k] or '').split())[:255]
            if not v:
                raise Refused(tr('Enter the title.'), problems={'title': tr('Required.')})
        elif k == 'confidentiality':
            v = data[k]
            if v not in dict(Mo.CONFIDENTIALITY):
                raise Refused(tr('Choose the confidentiality.'))
        elif k == 'tags':
            v = _tags(data[k])
        elif k in ('received_date', 'effective_date', 'expiry_date'):
            v = _date(data[k], k)
        elif k in ('owner', 'department'):
            v = _int(data[k])
            k = f'{k}_id'
        elif k == 'fields':
            v = _check_fields(doc.category, data[k] or {}, current=doc.fields, who=who)
        else:
            v = str(data[k] or '')[:4000]
        if getattr(doc, k) != v:
            ch.append(k.replace('_id', ''))
            setattr(doc, k, v)
    if doc.expiry_date and doc.effective_date and doc.expiry_date < doc.effective_date:
        raise Refused(tr('The expiry date is before the effective date.'))
    doc.save()
    from .lifecycle import refresh_retention
    refresh_retention(doc)   # v1.73.6
    if ch:
        log_act(who, 'details_changed', ', '.join(ch), document=doc)
    return ch


def new_revision(who, doc, upload, note='', major=False):
    r = doc_rights(doc, who)
    need(r, 'upload', tr('You may not add a version of this document.'))
    if doc.locked_by_id and doc.locked_by_id != who.uid:
        raise Refused(tr('The document is checked out by someone else.'), 409)
    if doc.status in ('archived', 'obsolete', 'disposed') or doc.deleted_at:
        raise Refused(tr('An archived document cannot get a new version.'))
    if doc.controlled and doc.current and doc.current.state in ('in_review',):
        raise Refused(tr('A version is in review – wait for the decision or withdraw it first.'), 409)
    if doc.controlled and doc.current and doc.current.state in ('draft', 'returned') and doc.current_id != doc.published_id:
        # the draft revision is replaced by the new file (the same number)
        old = doc.current
        v = add_version(who, doc, upload, note or old.change_note, major=False)
        Mo.Version.objects.filter(pk=v.pk).update(major=old.major, minor=old.minor)
        old.delete()
        v.refresh_from_db()
        doc.refresh_from_db()
        return v
    v = add_version(who, doc, upload, note, major=major)
    if doc.controlled:
        doc.status = 'draft' if not doc.published_id else doc.status
        doc.save(update_fields=['status'])
    return v


def restore_version(who, doc, old, note=''):
    r = doc_rights(doc, who)
    need(r, 'edit', tr('You may not restore a version of this document.'))
    if old.document_id != doc.pk:
        raise Refused(tr('Not a version of this document.'))
    if doc.current_id == old.pk:
        raise Refused(tr('This is already the newest version.'))
    old.file.open('rb')
    try:
        data = ContentFile(old.file.read(), name=old.file_name)
    finally:
        old.file.close()
    v = add_version(who, doc, data, note or en('Restored from version {0}', old.label), major=False, restored_from=old)
    log_act(who, 'restored', f'{old.label} → {v.label}', document=doc)
    return v


def compare(a, b):
    """lines removed and added between two versions (text read from the files)"""
    import difflib
    x, y = (a.text or '').splitlines(), (b.text or '').splitlines()
    if not x and not y:
        return {'comparable': False, 'lines': []}
    out = []
    for ln in difflib.unified_diff(x, y, a.label, b.label, lineterm='', n=1):
        if ln.startswith(('---', '+++')):
            continue
        out.append({'kind': 'add' if ln.startswith('+') else 'del' if ln.startswith('-') else 'hunk' if ln.startswith('@@') else 'same', 'text': ln[1:] if ln[:1] in '+- ' else ln})
        if len(out) > 2000:
            break
    return {'comparable': True, 'lines': out, 'same': not any(l['kind'] in ('add', 'del') for l in out)}


def lock(who, doc, on=True):
    r = doc_rights(doc, who)
    need(r, 'edit')
    if on:
        if doc.locked_by_id and doc.locked_by_id != who.uid:
            raise Refused(tr('The document is already checked out by someone else.'), 409)
        doc.locked_by_id, doc.locked_at = who.uid, timezone.now()
    else:
        if doc.locked_by_id and doc.locked_by_id != who.uid and 'admin' not in r:
            raise Refused(tr('Only the person who checked it out (or a manager) can release it.'), 403)
        doc.locked_by_id, doc.locked_at = None, None
    doc.save(update_fields=['locked_by_id', 'locked_at'])
    log_act(who, 'checked_out' if on else 'checked_in', '', document=doc)


def move_document(who, doc, target):
    need(doc_rights(doc, who), 'edit')
    need(folder_rights(target, who), 'upload', tr('You may not put documents in the target folder.'))
    old = path_of(doc.folder)
    doc.folder = target
    doc.save(update_fields=['folder', 'updated_at'])
    log_act(who, 'moved', f'{old} → {path_of(target)}', document=doc)


def trash(who, doc):
    r = doc_rights(doc, who)
    need(r, 'archive', tr('You may not delete this document.'))
    if doc.status in ('submitted', 'under_review', 'pending_approval'):
        raise Refused(tr('The document is waiting for approval – withdraw it first.'))
    if _held([doc]):
        raise Refused(tr('The document is under legal hold.'), 403)
    doc.deleted_at, doc.deleted_by_id = timezone.now(), who.uid
    doc.save(update_fields=['deleted_at', 'deleted_by_id'])
    log_act(who, 'deleted', '', document=doc)


def untrash(who, doc):
    if not (who.dms_admin or doc.deleted_by_id == who.uid or 'admin' in folder_rights(doc.folder, who)):
        raise Refused(tr('You may not restore this document.'), 403)
    if doc.folder.deleted_at:
        Mo.DocFolder.objects.filter(pk__in=[f.pk for f in ancestors(doc.folder) if f.deleted_at]).update(deleted_at=None, deleted_by_id=None)
    doc.deleted_at, doc.deleted_by_id = None, None
    doc.save(update_fields=['deleted_at', 'deleted_by_id'])
    log_act(who, 'restored_from_trash', '', document=doc)


def set_status(who, doc, status):
    """archive / obsolete / dispose / back to published"""
    r = doc_rights(doc, who)
    if status == 'disposed':
        need(r, 'dispose', tr('Only a user with the "Dispose" right may dispose of documents.'))
        if _held([doc]):
            raise Refused(tr('The document is under legal hold.'), 403)
    else:
        need(r, 'archive')
    if status not in ('archived', 'obsolete', 'disposed', 'published'):
        raise Refused(tr('Unknown status.'))
    if status == 'published' and not doc.published_id:
        raise Refused(tr('The document has no published version.'))
    old = doc.status
    doc.status = status
    doc.archived_at = timezone.now() if status in ('archived', 'obsolete', 'disposed') else None
    doc.save(update_fields=['status', 'archived_at'])
    if status in ('archived', 'published'):
        from .lifecycle import refresh_retention
        refresh_retention(doc)   # v1.73.6: retention may start when archived
    if status == 'disposed':
        for v in doc.versions.all():   # the files go; the register and the audit trail stay as evidence
            try:
                v.file.delete(save=False)
            except Exception:   # noqa: BLE001
                pass
            Mo.Version.objects.filter(pk=v.pk).update(file='', text='')
    log_act(who, 'status', f'{old} → {status}', document=doc)


# ================================================================== workflow
def _users_of(stage, doc):
    U = M('UserManagement', 'CustomUser')
    ids = [int(x) for x in stage.get('users') or [] if str(x).isdigit()]
    if stage.get('groups'):
        try:
            from tenant_users.tenants.models import UserTenantPermissions as UTP
            ids += list(UTP.objects.filter(groups__id__in=stage['groups']).values_list('profile_id', flat=True))
        except Exception:   # noqa: BLE001
            pass
    role = stage.get('role') or ''
    if role == 'owner_manager' and doc.owner_id:
        e = M('EmpManagement', 'emp_master').objects.filter(users_id=doc.owner_id).first()
        if e and e.emp_reporting_manager_id:
            ids.append(e.emp_reporting_manager_id)
    if role == 'dept_head' and doc.department_id:
        try:
            R = M('LeavePolicy', 'OrgRole')
            ids += [x for x in R.objects.filter(role='department_head', department_id=doc.department_id).values_list('user_id', flat=True) if x]
        except Exception:   # noqa: BLE001
            pass
    out = []
    for i in ids:
        if i not in out and U.objects.filter(pk=i, is_active=True).exists():
            out.append(i)
    return out


def submit(who, doc, note=''):
    """send the newest version for review / approval (its category's workflow)"""
    r = doc_rights(doc, who)
    need(r, 'edit', tr('You may not submit this document.'))
    if not doc.controlled or not (doc.category and doc.category.workflow_id):
        raise Refused(tr('This document has no approval workflow – it is in force as soon as it is uploaded.'))
    v = doc.current
    if v is None or v.state not in ('draft', 'returned'):
        raise Refused(tr('There is no draft version to submit.'))
    wf = doc.category.workflow
    stages = [s for s in (wf.stages or []) if s]
    if not stages:
        raise Refused(tr('The workflow "{0}" has no stages.', wf.name))
    plan = []
    for i, s in enumerate(stages):
        users = [u for u in _users_of(s, doc) if u != who.uid or len(_users_of(s, doc)) == 1]
        if not users:
            raise Refused(tr('Nobody can decide the stage "{0}" of this document – set its approvers in the workflow.', s.get('name') or i + 1))
        plan.append((i, s, users))
    with transaction.atomic():
        v.approvals.all().delete()
        for i, s, users in plan:
            due = _zt_today() + timedelta(days=int(s.get('due_days') or 3)) if i == 0 else None
            for u in users:
                Mo.Approval.objects.create(version=v, stage=i, stage_name=(s.get('name') or '')[:120], kind=s.get('kind') or 'approve',
                                           mode=s.get('mode') or 'any', approver_id=u, decision='pending' if i == 0 else 'waiting', due_date=due)
        v.state, v.submitted_at = 'in_review', timezone.now()
        v.save(update_fields=['state', 'submitted_at'])
        doc.status = 'under_review' if plan[0][1].get('kind') == 'review' else 'pending_approval'
        doc.save(update_fields=['status'])
    log_act(who, 'submitted', f'{v.label}' + (f' – {note}' if note else ''), document=doc)
    _tell_pending(doc, v, 0)
    return v


def withdraw(who, doc):
    v = doc.current
    if v is None or v.state != 'in_review':
        raise Refused(tr('Nothing is in review.'))
    if not (who.uid in (doc.owner_id, doc.created_by_id, v.author_id) or who.dms_admin):
        raise Refused(tr('Only the owner or the author can withdraw it.'), 403)
    v.approvals.exclude(decision__in=('approved', 'rejected', 'returned')).update(decision='skipped')
    v.state = 'draft'
    v.save(update_fields=['state'])
    doc.status = 'published' if doc.published_id else 'draft'
    doc.save(update_fields=['status'])
    _close_todos(doc)
    log_act(who, 'withdrawn', v.label, document=doc)


def decide(who, approval, decision, comment=''):
    v = approval.version
    doc = v.document
    if approval.approver_id != who.uid and not who.dms_admin:
        raise Refused(tr('This step is not yours.'), 403)
    if approval.decision != 'pending':
        raise Refused(tr('This step is already decided or not open yet.'), 409)
    if decision not in ('approved', 'rejected', 'returned'):
        raise Refused(tr('Choose approve, reject or return for correction.'))
    if decision in ('rejected', 'returned') and not (comment or '').strip():
        raise Refused(tr('Give the reason.'), problems={'comment': tr('Required.')})
    now = timezone.now()
    with transaction.atomic():
        approval.decision, approval.comment, approval.decided_at = decision, (comment or '')[:4000], now
        approval.save(update_fields=['decision', 'comment', 'decided_at'])
        same = v.approvals.filter(stage=approval.stage)
        if decision in ('rejected', 'returned'):
            v.approvals.filter(decision__in=('pending', 'waiting')).update(decision='skipped')
            v.state = 'rejected' if decision == 'rejected' else 'returned'
            v.save(update_fields=['state'])
            doc.status = 'rejected' if decision == 'rejected' else 'revision_required'
            if doc.published_id:   # a refused revision leaves the version in force
                doc.status = 'published'
            doc.save(update_fields=['status'])
            log_act(who, decision, f'{v.label} – {approval.stage_name}: {comment}', document=doc)
            _close_todos(doc)
            _tell_author(doc, v, en('{0} {1} was {2}: {3}', doc.number, v.label, decision, comment))
            return
        done = (approval.mode == 'any') or not same.filter(decision='pending').exists()
        if done:
            same.filter(decision='pending').update(decision='skipped')
            nxt = v.approvals.filter(stage__gt=approval.stage).order_by('stage').first()
            if nxt is not None:
                st = v.approvals.filter(stage=nxt.stage)
                due_days = 3
                try:
                    due_days = int((doc.category.workflow.stages[nxt.stage] or {}).get('due_days') or 3)
                except Exception:   # noqa: BLE001
                    pass
                st.update(decision='pending', due_date=_zt_today() + timedelta(days=due_days))
                doc.status = 'under_review' if nxt.kind == 'review' else 'pending_approval'
                doc.save(update_fields=['status'])
                _tell_pending(doc, v, nxt.stage)
            else:
                v.state, v.approved_at = 'approved', now
                v.save(update_fields=['state', 'approved_at'])
                doc.status = 'approved'
                doc.save(update_fields=['status'])
                _tell_author(doc, v, en('{0} {1} is approved – it can be published.', doc.number, v.label))
    log_act(who, 'approved' if approval.kind == 'approve' else 'reviewed', f'{v.label} – {approval.stage_name}' + (f': {comment}' if comment else ''), document=doc)
    _close_todos(doc, who.uid)


def publish(who, doc):
    r = doc_rights(doc, who)
    need(r, 'publish', tr('You may not publish documents here.'))
    v = doc.current
    if v is None:
        raise Refused(tr('The document has no version.'))
    if doc.controlled and v.state != 'approved':
        raise Refused(tr('Only an approved version can be published.'))
    now = timezone.now()
    with transaction.atomic():
        if doc.published_id and doc.published_id != v.pk:
            Mo.Version.objects.filter(pk=doc.published_id).update(state='superseded')
        v.state, v.published_at = 'published', now
        v.save(update_fields=['state', 'published_at'])
        doc.published, doc.status = v, 'published'
        doc.save(update_fields=['published', 'status'])
    log_act(who, 'published', v.label, document=doc)
    if doc.renewal_of_id:   # v1.73.6: the renewal is in force – the document it renews is superseded
        from .lifecycle import superseded_by_renewal
        superseded_by_renewal(who, doc)
    # the people the earlier versions went to get the new one
    users = set(doc.distributions.exclude(version=v).values_list('user_id', flat=True))
    if users:
        distribute(who, doc, users=list(users), require_ack=doc.distributions.filter(require_ack=True).exists(), via='new version')
    return v


# ================================================================== distribution and links
def distribute(who, doc, users=(), groups=(), branches=(), departments=(), require_ack=False, via=''):
    r = doc_rights(doc, who)
    need(r, 'share', tr('You may not distribute this document.'))
    v = doc.published or doc.current
    if v is None:
        raise Refused(tr('The document has no version.'))
    if doc.controlled and not doc.published_id:
        raise Refused(tr('Only a published document can be distributed.'))
    ids = set(int(x) for x in users if str(x).isdigit())
    E = M('EmpManagement', 'emp_master')
    if groups:
        try:
            from tenant_users.tenants.models import UserTenantPermissions as UTP
            ids |= set(UTP.objects.filter(groups__id__in=list(groups)).values_list('profile_id', flat=True))
        except Exception:   # noqa: BLE001
            pass
    if branches:
        ids |= set(x for x in E.objects.filter(emp_branch_id__in=list(branches), is_active=True).values_list('users_id', flat=True) if x)
    if departments:
        ids |= set(x for x in E.objects.filter(emp_dept_id__in=list(departments), is_active=True).values_list('users_id', flat=True) if x)
    if not ids:
        raise Refused(tr('Choose who receives it.'))
    n = 0
    for u in ids:
        obj, created = Mo.Distribution.objects.get_or_create(version=v, user_id=u, defaults={'document': doc, 'require_ack': bool(require_ack),
                                                                                             'sent_by_id': who.uid, 'via': via[:40]})
        if created:
            n += 1
            _todo(u, en('Read {0} {1} ({2})', doc.number, doc.title, v.label) if not require_ack else en('Read and acknowledge {0} {1} ({2})', doc.number, doc.title, v.label), doc)
    log_act(who, 'distributed', en('{0} recipient(s){1}', n, en(', acknowledgement asked') if require_ack else ''), document=doc)
    return n


def acknowledge(who, doc):
    d = Mo.Distribution.objects.filter(document=doc, user_id=who.uid).order_by('-sent_at').first()
    if d is None:
        raise Refused(tr('This document was not sent to you.'), 404)
    now = timezone.now()
    d.viewed_at = d.viewed_at or now
    d.acknowledged_at = d.acknowledged_at or now
    d.save(update_fields=['viewed_at', 'acknowledged_at'])
    _close_todos(doc, who.uid)
    log_act(who, 'acknowledged', d.version.label, document=doc)


def seen(who, doc):
    Mo.Distribution.objects.filter(document=doc, user_id=who.uid, viewed_at__isnull=True).update(viewed_at=timezone.now())


def make_link(who, doc, days=7, recipient='', max_downloads=None, allow_download=True):
    r = doc_rights(doc, who)
    need(r, 'share', tr('You may not share this document.'))
    if doc.confidentiality in ('confidential', 'restricted', 'internal') and not who.dms_admin:
        raise Refused(tr('Only public documents can be shared outside the company (this one is {0}).', tr(doc.get_confidentiality_display())), 403)
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 0
    if not 1 <= days <= 90:
        raise Refused(tr('A link is valid for 1 to 90 days.'), problems={'days': tr('Invalid.')})
    if doc.controlled and not doc.published_id:
        raise Refused(tr('Only a published document can be shared.'))
    lk = Mo.ShareLink.objects.create(document=doc, version=None, token=secrets.token_urlsafe(32), recipient=str(recipient or '')[:200],
                                     expires_at=timezone.now() + timedelta(days=days), max_downloads=_int(max_downloads) or None,
                                     allow_download=bool(allow_download), created_by_id=who.uid)
    log_act(who, 'link_created', f'{days} day(s)' + (f' – {recipient}' if recipient else ''), document=doc)
    return lk


def revoke_link(who, lk):
    need(doc_rights(lk.document, who), 'share')
    lk.revoked_at = timezone.now()
    lk.save(update_fields=['revoked_at'])
    log_act(who, 'link_revoked', lk.recipient, document=lk.document)


def link_state(lk):
    if lk.revoked_at:
        return 'revoked'
    if lk.expires_at <= timezone.now():
        return 'expired'
    if lk.max_downloads and lk.downloads >= lk.max_downloads:
        return 'used'
    if lk.document.deleted_at or lk.document.status in ('archived', 'obsolete', 'disposed'):
        return 'withdrawn'
    return 'active'


# ================================================================== search
def visible_documents(who, qs=None):
    """the documents the user may view (rights are checked per folder, then per document)"""
    qs = (qs if qs is not None else Mo.Document.objects.all()).select_related('folder', 'category', 'current', 'published').prefetch_related('grants')
    if who.dms_admin:
        return list(qs)
    out = []
    for d in qs:
        if 'view' in doc_rights(d, who):
            out.append(d)
    return out


def search(who, p):
    qs = Mo.Document.objects.filter(deleted_at__isnull=True)
    q = ' '.join(str(p.get('q') or '').split())
    if q:
        cond = Q(number__icontains=q) | Q(title__icontains=q) | Q(remarks__icontains=q) | Q(tags__icontains=q) | Q(current__text__icontains=q) \
            | Q(current__file_name__icontains=q) | Q(fields__icontains=q)
        qs = qs.filter(cond)
    for k, f in (('category', 'category_id'), ('department', 'department_id'), ('branch', 'branch_id'), ('employee', 'employee_id'),
                 ('project', 'project_id'), ('partner', 'partner_id'), ('owner', 'owner_id'), ('status', 'status'), ('confidentiality', 'confidentiality')):
        v = p.get(k)
        if v not in (None, ''):
            qs = qs.filter(**{f: v}) if k in ('status', 'confidentiality') else qs.filter(**{f: _int(v) or -1})
    if p.get('folder') and str(p.get('folder')).isdigit():
        root = Mo.DocFolder.objects.filter(pk=int(p['folder'])).first()
        if root is not None:
            qs = qs.filter(folder_id__in=[x.pk for x in descendants(root)])
    for k, f in (('from', 'created_at__date__gte'), ('to', 'created_at__date__lte'), ('expiry_from', 'expiry_date__gte'), ('expiry_to', 'expiry_date__lte')):
        if p.get(k):
            qs = qs.filter(**{f: _date(p.get(k), k)})
    if p.get('tag'):
        qs = qs.filter(tags__icontains=str(p['tag']))
    want = {k[2:]: v for k, v in p.items() if k.startswith('f.') and str(v).strip() != ''}   # v1.70.7: f.<field>=value
    if want:
        from . import fields as F
        defs = {d['key']: d for d in F.doc_fields()}
        for c in Mo.DocCategory.objects.all():
            for d in F.cat_fields(c):
                defs.setdefault(d['key'], d)
        for k in want:
            if k in defs:
                qs = qs.filter(fields__has_key=k)
        docs = [d for d in qs.order_by('-updated_at')[:5000]
                if all(k in defs and F.matches(defs[k], (d.fields or {}).get(k), v) for k, v in want.items())]
        return visible_documents(who, Mo.Document.objects.filter(pk__in=[d.pk for d in docs[:2000]]).order_by('-updated_at'))[:500]
    return visible_documents(who, qs.order_by('-updated_at')[:2000])[:500]


# ================================================================== zip in / out
def upload_zip(who, folder, upload):
    """a zip: its folders become subfolders, its files documents (plain, not controlled)"""
    need(folder_rights(folder, who), 'upload', tr('You may not upload into this folder.'))
    need(folder_rights(folder, who), 'create', tr('You may not create folders here.'))
    try:
        z = zipfile.ZipFile(upload)
    except zipfile.BadZipFile:
        raise Refused(tr('This is not a zip file.'), problems={'file': tr('Invalid.')})
    total = sum(i.file_size for i in z.infolist())
    if total > 500 * 1024 * 1024 or len(z.infolist()) > 2000:
        raise Refused(tr('The zip is too large (at most 500 MB unpacked and 2,000 files).'))
    made, skipped, folders = 0, [], 0
    cache = {'': folder}
    for info in z.infolist():
        if info.is_dir() or '__MACOSX' in info.filename or os.path.basename(info.filename).startswith('.'):
            continue
        parts = [p for p in info.filename.replace('\\', '/').split('/') if p and p not in ('.', '..')]
        if not parts:
            continue
        cur, key = folder, ''
        for p in parts[:-1]:
            key = f'{key}/{p}'
            if key not in cache:
                nm = clean_name(re.sub(r'[/\\:*?"<>|]', '-', p))
                f = Mo.DocFolder.objects.filter(parent=cur, name__iexact=nm, deleted_at__isnull=True).first()
                if f is None:
                    f = Mo.DocFolder.objects.create(name=nm, parent=cur, kind='user', owner_id=who.uid, created_by_id=who.uid, branch_id=cur.branch_id)
                    folders += 1
                cache[key] = f
            cur = cache[key]
        data = ContentFile(z.read(info), name=parts[-1])
        try:
            register(who, cur, data, {'title': os.path.splitext(parts[-1])[0]})
            made += 1
        except Refused as e:
            skipped.append({'file': '/'.join(parts), 'reason': str(e)})
    log_act(who, 'zip_uploaded', en('{0} file(s), {1} folder(s)', made, folders), folder=folder)
    return {'documents': made, 'folders': folders, 'skipped': skipped}


def zip_folder(who, folder):
    need(folder_rights(folder, who), 'download', tr('You may not download this folder.'))
    buf = io.BytesIO()
    n = 0
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in descendants(folder):
            if f.deleted_at:
                continue
            rel = path_of(f)[len(path_of(folder)):].strip(' ›').replace(' › ', '/')
            for d in f.documents.filter(deleted_at__isnull=True).exclude(status='disposed'):
                if 'download' not in doc_rights(d, who):
                    continue
                v = d.published or d.current
                if v is None or not v.file:
                    continue
                try:
                    v.file.open('rb')
                    z.writestr('/'.join(x for x in [rel, f'{d.number} {v.file_name}'.strip()] if x), v.file.read())
                    n += 1
                finally:
                    v.file.close()
    log_act(who, 'zip_downloaded', en('{0} file(s)', n), folder=folder)
    buf.seek(0)
    return buf, n


# ================================================================== notifications
def _todo(user_id, summary, doc):
    try:
        from Chatter.models import Activity as CA
        U = M('UserManagement', 'CustomUser')
        u = U.objects.filter(pk=user_id).first()
        if u is None:
            return
        if CA.objects.filter(model='ZeoDocs.document', object_id=str(doc.pk), assigned_to=u, summary=summary[:255], state='open').exists():
            return
        CA.objects.create(model='ZeoDocs.document', object_id=str(doc.pk), record_label=f'{doc.number} {doc.title}'[:255],
                          page=f'/main-sidebar/documents?doc={doc.pk}'[:255], activity_type='todo', summary=summary[:255], due_date=_zt_today(),
                          assigned_to=u)
    except Exception:   # noqa: BLE001
        log.exception('dms to-do')


def _close_todos(doc, user_id=None):
    try:
        from Chatter.models import Activity as CA
        qs = CA.objects.filter(model='ZeoDocs.document', object_id=str(doc.pk), state='open')
        if user_id:
            qs = qs.filter(assigned_to_id=user_id)
        qs.update(state='done', done_at=timezone.now())
    except Exception:   # noqa: BLE001
        log.exception('dms to-do close')


def _tell_pending(doc, v, stage):
    for a in v.approvals.filter(stage=stage, decision='pending'):
        _todo(a.approver_id, en('{0} {1} ({2}) is waiting for your {3}', doc.number, doc.title, v.label, 'review' if a.kind == 'review' else 'approval'), doc)


def _tell_author(doc, v, text):
    for u in {x for x in (doc.owner_id, v.author_id) if x}:
        _todo(u, text, doc)
