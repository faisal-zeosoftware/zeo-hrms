"""v1.70.1 – Document Center API (zeo-apps/dms/…)."""
from zeo.tz import today as _zt_today   # v1.73.16 the user's / company's day, not the server's UTC day
import json
import mimetypes
import os
from datetime import timedelta

from django.apps import apps
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponse
from django.utils import timezone
from django.utils.html import escape
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from Translations.i18n import tr

from . import fields as F   # v1.70.7 designer fields
from . import linked as L   # v1.70.8 the files of the other apps
from . import models as Mo
from . import services as S

M = apps.get_model


class Member(BasePermission):
    message = 'Please log in.'

    def has_permission(self, request, view):
        from django.db import connection
        u = request.user
        if not u or not u.is_authenticated:
            return False
        sch = getattr(connection, 'schema_name', 'public')
        if sch == 'public':
            self.message = 'Choose a company.'
            return False
        return bool(u.is_superuser or u.tenants.filter(schema_name=sch).exists())


def refused(fn):
    def w(self, request, *a, **kw):
        try:
            return fn(self, request, *a, **kw)
        except S.Refused as e:
            return Response({'detail': str(e), 'problems': e.problems}, status=e.status)
    w.__name__ = fn.__name__
    return w


def _body(request):
    d = request.data
    if hasattr(d, 'dict'):
        d = d.dict()
    d = dict(d)
    for k in ('fields', 'tags', 'rows', 'users', 'groups', 'branches', 'departments', 'options', 'rules', 'ids'):
        if isinstance(d.get(k), str) and d[k][:1] in '[{':
            try:
                d[k] = json.loads(d[k])
            except ValueError:
                pass
    return d


# ------------------------------------------------------------------ names
_names = {}


def uname(uid):
    if not uid:
        return ''
    if uid not in _names:
        u = M('UserManagement', 'CustomUser').objects.filter(pk=uid).first()
        _names[uid] = ((u.get_full_name() if hasattr(u, 'get_full_name') else '') or getattr(u, 'username', '') or getattr(u, 'email', '')) if u else f'#{uid}'
        if len(_names) > 5000:
            _names.clear()
    return _names[uid]


def _who_label(g):
    if g.who == 'everyone':
        return tr('Everyone')
    if g.who == 'user':
        return uname(g.who_id)
    if g.who == 'group':
        from django.contrib.auth.models import Group
        x = Group.objects.filter(pk=g.who_id).first()
        return x.name if x else f'#{g.who_id}'
    if g.who == 'branch':
        x = M('OrganisationManager', 'brnch_mstr').objects.filter(pk=g.who_id).first()
        return x.branch_name if x else f'#{g.who_id}'
    x = M('OrganisationManager', 'dept_master').objects.filter(pk=g.who_id).first()
    return x.dept_name if x else f'#{g.who_id}'


def _preset(rights):
    rs = set(rights)
    for k in ('manager', 'approver', 'reviewer', 'contributor', 'viewer'):
        if rs == set(Mo.PRESETS[k]) | {'view'}:
            return k
    return 'custom'


def grants_out(target):
    return [{'id': g.id, 'who': g.who, 'who_id': g.who_id, 'label': _who_label(g), 'rights': g.rights, 'preset': _preset(g.rights)} for g in target.grants.all()]


def folder_out(f, who, counts=True):
    r = S.folder_rights(f, who)
    out = {'id': f.id, 'name': f.name, 'parent': f.parent_id, 'kind': f.kind, 'provider': f.provider or '', 'record_id': f.record_id, 'private': f.private,
           'inherit': f.inherit, 'owner': uname(f.owner_id), 'mine': bool(f.owner_id and f.owner_id == who.uid), 'rights': sorted(r, key=Mo.RIGHTS.index),
           'system': f.kind != 'user', 'deleted': bool(f.deleted_at)}
    if f.kind == 'root' and not f.provider and f.name == S.PERSONAL:
        out['personal_root'] = True
    if counts:
        out['has_children'] = f.children.filter(deleted_at__isnull=True).exists() or (f.kind == 'root' and bool(f.provider))
    return out


def ver_out(v):
    return {'id': v.id, 'label': v.label, 'major': v.major, 'minor': v.minor, 'file_name': v.file_name, 'size': v.size, 'mime': v.mime,
            'state': v.state, 'note': v.change_note, 'author': uname(v.author_id), 'created_at': v.created_at, 'submitted_at': v.submitted_at,
            'approved_at': v.approved_at, 'published_at': v.published_at, 'restored_from': v.restored_from.label if v.restored_from_id else '',
            'readable': bool(v.text), 'has_file': bool(v.file), 'sha256': v.sha256[:12],
            'text_source': getattr(v, 'text_source', ''), 'text_chars': len(v.text or '')}   # v1.73.10


def doc_row(d, who=None):
    v = d.current
    return {'id': d.id, 'number': d.number, 'title': d.title, 'folder': d.folder_id, 'folder_path': S.path_of(d.folder), 'category': d.category.name if d.category_id else '',
            'category_id': d.category_id, 'status': d.status, 'status_label': tr(d.get_status_display()), 'confidentiality': d.confidentiality,
            'version': (d.published or v).label if (d.published_id or v) else '', 'draft': v.label if v and d.published_id and v.pk != d.published_id else '',
            'file_name': v.file_name if v else '', 'size': v.size if v else 0, 'expiry_date': d.expiry_date, 'tags': d.tags, 'owner': uname(d.owner_id),
            'updated_at': d.updated_at, 'locked_by': uname(d.locked_by_id), 'controlled': d.controlled, 'deleted_at': d.deleted_at,
            'expired': bool(d.expiry_date and d.expiry_date < _zt_today()),
            'expiring': bool(d.expiry_date and _zt_today() <= d.expiry_date <= _zt_today() + timedelta(days=30)),
            'legal_hold': d.legal_hold, 'retention_until': d.retention_until, 'renewal_of': d.renewal_of_id,   # v1.73.6
            'days_left': (d.expiry_date - _zt_today()).days if d.expiry_date else None}


def _folder(pk, who=None):
    f = Mo.DocFolder.objects.filter(pk=pk).select_related('parent').first()
    if f is None or (f.deleted_at and not (who and who.dms_admin)):
        raise Http404
    return f


def _doc(pk, who, right='view', trashed=False):
    d = Mo.Document.objects.select_related('folder', 'category', 'current', 'published').prefetch_related('grants').filter(pk=pk).first()
    if d is None or (d.deleted_at and not trashed):
        raise Http404
    r = S.doc_rights(d, who)
    if right and right not in r and not (trashed and d.deleted_by_id == who.uid):
        raise S.Refused(tr('You do not have access to this document.'), 403)
    return d, r


# ------------------------------------------------------------------ tree and folders
class Tree(APIView):
    permission_classes = [Member]

    @refused
    def get(self, request):
        who = S.Who(request)
        pid = request.GET.get('parent')
        if not pid:
            roots = S.ensure_roots()
            S.personal_folder(who)
            out = []
            for f in Mo.DocFolder.objects.filter(parent__isnull=True, kind='root', deleted_at__isnull=True).order_by('name'):
                if f.provider and not S.provider_active(f.provider):
                    continue
                if f.name == S.PERSONAL or 'view' in S.folder_rights(f, who) or _visible_below(f, who):
                    out.append(folder_out(f, who))
            order = {S.GENERAL: 0, S.PERSONAL: 1}
            out.sort(key=lambda x: (order.get(x['name'], 2), x['name'].lower()))
            return Response({'folders': out, 'dms_admin': who.dms_admin})
        f = _folder(pid, who)
        if f.kind == 'root' and f.provider:
            S.sync_provider(f.provider)
        out = []
        kids = f.children.filter(deleted_at__isnull=True).order_by('name')
        if f.kind == 'root' and f.name == S.PERSONAL and not f.provider:
            kids = kids if who.dms_admin else kids.filter(Q(owner_id=who.uid) | Q(private=False))
        for c in kids[:3000]:
            if 'view' in S.folder_rights(c, who) or (c.kind == 'root' and _visible_below(c, who)):
                out.append(folder_out(c, who, counts=c.kind != 'record'))
        return Response({'folders': out})


def _visible_below(f, who):
    """a root the user has no right on but with something below they may see (an employee's own folder)"""
    if who.dms_admin:
        return True
    if who.emp_id:   # an employee sees the way down to their own folder (HR › Employees › them)
        own = Mo.DocFolder.objects.filter(provider='employee', kind='record', record_id=who.emp_id, deleted_at__isnull=True).first()
        if own is not None and f.pk in {x.pk for x in S.ancestors(own)}:
            return True
    return Mo.DocGrant.objects.filter(folder__isnull=False).filter(
        Q(who='everyone') | Q(who='user', who_id=who.uid) | Q(who='group', who_id__in=who.groups or [0])).exists() and any(
        'view' in S.folder_rights(x, who) for x in S.descendants(f)[:500])


class Folders(APIView):
    permission_classes = [Member]
    zeo_model = Mo.DocFolder   # v1.70.7: file fields of the folder fields

    @refused
    def post(self, request):
        who = S.Who(request)
        d = _body(request)
        parent = _folder(d.get('parent'), who) if str(d.get('parent') or '').isdigit() else None
        f = S.create_folder(who, parent, d.get('name'), private=str(d.get('private')).lower() in ('1', 'true'))
        if 'fields' in d:   # v1.70.7 folder fields
            try:
                S.set_folder_fields(who, f, d.get('fields') or {}, create=True)
            except S.Refused:
                f.delete()
                raise
        out = folder_out(f, who)
        out['fields'] = f.fields
        return Response(out, status=201)


class FolderOne(APIView):
    permission_classes = [Member]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    @refused
    def get(self, request, pk):
        who = S.Who(request)
        f = _folder(pk, who)
        r = S.folder_rights(f, who)
        own_emp = S.provider_of(f)[0] == 'employee' and f.kind == 'record' and f.record_id == who.emp_id
        if 'view' not in r and not own_emp:
            raise S.Refused(tr('You do not have access to this folder.'), 403)
        if f.kind == 'root' and f.provider:
            S.sync_provider(f.provider)
        crumbs = [{'id': x.id, 'name': x.name} for x in reversed(S.ancestors(f))]
        qs = f.documents.filter(deleted_at__isnull=True).select_related('folder', 'category', 'current', 'published').prefetch_related('grants')
        if request.GET.get('status'):
            qs = qs.filter(status=request.GET['status'])
        docs = [doc_row(d) for d in S.visible_documents(who, qs.order_by('title')[:2000])]
        out = folder_out(f, who)
        out.update({'path': crumbs, 'documents': docs, 'grants': grants_out(f) if 'admin' in r else None, 'hr_documents': _hr_docs(f, who),
                    'fields': f.fields or {}, 'field_defs': [x for x in F.folder_fields() if not x['hidden']],   # v1.70.7 folder fields
                    'linked': L.for_folder(f, who)})   # v1.70.8 the files attached in the other apps (read-only, by source)
        return Response(out)

    @refused
    def patch(self, request, pk):
        who = S.Who(request)
        f = _folder(pk, who)
        d = _body(request)
        if 'name' in d:
            S.rename_folder(who, f, d['name'])
        if 'private' in d and f.kind == 'user':
            S.need(S.folder_rights(f, who), 'admin')
            f.private = str(d['private']).lower() in ('1', 'true')
            f.save(update_fields=['private'])
        if 'fields' in d:   # v1.70.7 folder fields
            S.set_folder_fields(who, f, d.get('fields') or {})
        out = folder_out(f, who)
        out['fields'] = f.fields or {}
        return Response(out)

    @refused
    def delete(self, request, pk):
        who = S.Who(request)
        S.delete_folder(who, _folder(pk, who))
        return Response(status=204)


def _hr_docs(f, who):
    """an employee's folder also shows the documents kept on the employee record (read-only here)"""
    if f.provider != 'employee' or f.kind != 'record':
        return None
    try:
        D = M('EmpManagement', 'Emp_Documents')
    except LookupError:
        return None
    out = []
    for x in D.objects.filter(emp_id=f.record_id, is_active=True).select_related('document_type').order_by('-emp_doc_expiry_date'):
        out.append({'id': x.id, 'type': getattr(x.document_type, 'type_name', '') or str(x.document_type or ''), 'number': x.emp_doc_number,
                    'issued': x.emp_doc_issued_date, 'expiry': x.emp_doc_expiry_date, 'has_file': bool(x.emp_doc_document),
                    'expired': bool(x.emp_doc_expiry_date and x.emp_doc_expiry_date < _zt_today())})
    return out


class HrDocFile(APIView):
    permission_classes = [Member]

    @refused
    def get(self, request, pk):
        who = S.Who(request)
        x = M('EmpManagement', 'Emp_Documents').objects.filter(pk=pk).first()
        if x is None or not x.emp_doc_document:
            raise Http404
        f = Mo.DocFolder.objects.filter(provider='employee', kind='record', record_id=x.emp_id_id).first()
        if f is None or 'download' not in S.folder_rights(f, who):
            raise S.Refused(tr('You do not have access to this document.'), 403)
        name = os.path.basename(x.emp_doc_document.name)
        return FileResponse(x.emp_doc_document.open('rb'), as_attachment=request.GET.get('dl') == '1', filename=name)


class FolderMove(APIView):
    permission_classes = [Member]

    @refused
    def post(self, request, pk):
        who = S.Who(request)
        f = _folder(pk, who)
        S.move_folder(who, f, _folder(_body(request).get('target'), who))
        return Response(folder_out(f, who))


class FolderRights(APIView):
    permission_classes = [Member]

    @refused
    def put(self, request, pk):
        who = S.Who(request)
        f = _folder(pk, who)
        d = _body(request)
        S.set_grants(who, f, d.get('rows') or [], inherit=d.get('inherit'))
        return Response({'grants': grants_out(f), 'inherit': Mo.DocFolder.objects.get(pk=f.pk).inherit})


class FolderZip(APIView):
    permission_classes = [Member]
    parser_classes = [MultiPartParser, FormParser]

    @refused
    def get(self, request, pk):
        who = S.Who(request)
        f = _folder(pk, who)
        buf, n = S.zip_folder(who, f)
        if not n:
            raise S.Refused(tr('There is nothing in this folder you may download.'), 404)
        resp = HttpResponse(buf.getvalue(), content_type='application/zip')
        resp['Content-Disposition'] = f'attachment; filename="{_ascii(f.name)}.zip"'
        return resp

    @refused
    def post(self, request, pk):
        who = S.Who(request)
        f = _folder(pk, who)
        up = request.FILES.get('file')
        if up is None:
            raise S.Refused(tr('Choose a zip file.'), problems={'file': tr('Required.')})
        return Response(S.upload_zip(who, f, up))


def _ascii(s):
    return ''.join(ch if ch.isascii() and ch not in '"\\' else '_' for ch in s)[:80] or 'documents'


# ------------------------------------------------------------------ documents
class Documents(APIView):
    permission_classes = [Member]
    zeo_model = Mo.Document   # v1.70.7: file fields are kept as the document's attachments (Chatter)
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @refused
    def post(self, request):
        who = S.Who(request)
        d = _body(request)
        up = request.FILES.get('file')
        if up is None:
            raise S.Refused(tr('Choose a file.'), problems={'file': tr('Required.')})
        if not str(d.get('folder') or '').isdigit():
            raise S.Refused(tr('Choose the folder.'), problems={'folder': tr('Required.')})
        doc, dup = S.register(who, _folder(d['folder'], who), up, d)
        out = doc_row(doc)
        if dup is not None and 'view' in S.doc_rights(dup, who):
            out['duplicate_of'] = {'id': dup.id, 'number': dup.number, 'title': dup.title}
        elif dup is not None:
            out['duplicate_of'] = {'id': None, 'number': '', 'title': ''}
        return Response(out, status=201)


class DocOne(APIView):
    permission_classes = [Member]

    @refused
    def get(self, request, pk):
        who = S.Who(request)
        d, r = _doc(pk, who, trashed=True)
        S.seen(who, d)
        out = doc_row(d)
        vs = list(d.versions.select_related('restored_from').order_by('-major', '-minor', '-id'))
        cur = d.current
        appr = []
        if cur is not None:
            for a in cur.approvals.order_by('stage', 'id'):
                appr.append({'id': a.id, 'stage': a.stage, 'stage_name': a.stage_name, 'kind': a.kind, 'mode': a.mode, 'approver': uname(a.approver_id),
                             'mine': a.approver_id == who.uid and a.decision == 'pending', 'decision': a.decision, 'comment': a.comment, 'due_date': a.due_date,
                             'decided_at': a.decided_at, 'overdue': bool(a.decision == 'pending' and a.due_date and a.due_date < _zt_today())})
        dist = None
        if 'share' in r:
            dist = [{'user': uname(x.user_id), 'version': x.version.label, 'require_ack': x.require_ack, 'sent_at': x.sent_at, 'viewed_at': x.viewed_at,
                     'acknowledged_at': x.acknowledged_at, 'sent_by': uname(x.sent_by_id)} for x in d.distributions.select_related('version').order_by('-sent_at')[:1000]]
        mine = d.distributions.filter(user_id=who.uid).order_by('-sent_at').first()
        links = None
        if 'share' in r:
            links = [{'id': x.id, 'recipient': x.recipient, 'expires_at': x.expires_at, 'downloads': x.downloads, 'max_downloads': x.max_downloads,
                      'allow_download': x.allow_download, 'state': S.link_state(x), 'by': uname(x.created_by_id), 'created_at': x.created_at,
                      'token': x.token if S.link_state(x) == 'active' else ''} for x in d.links.order_by('-created_at')[:200]]
        act = [{'action': a.action, 'detail': a.detail, 'user': uname(a.user_id), 'at': a.at} for a in d.activity.order_by('-at')[:300]] \
            if ({'audit', 'admin', 'edit'} & r or d.owner_id == who.uid) else None
        out.update({
            'rights': sorted(r, key=Mo.RIGHTS.index), 'remarks': d.remarks, 'fields': d.fields, 'category_fields': F.cat_fields(d.category),
            'field_defs': [x for x in F.for_document(d.category) if not x['hidden']],   # v1.70.7: the category's fields, then those of every document
            'received_date': d.received_date, 'effective_date': d.effective_date, 'retention_until': d.retention_until,
            'department_id': d.department_id, 'branch_id': d.branch_id, 'employee_id': d.employee_id, 'project_id': d.project_id, 'partner_id': d.partner_id,
            'owner_id': d.owner_id, 'created_by': uname(d.created_by_id), 'created_at': d.created_at, 'locked_by_me': d.locked_by_id == who.uid,
            'current': ver_out(cur) if cur else None, 'published': ver_out(d.published) if d.published_id else None,
            'versions': [ver_out(v) for v in vs], 'approvals': appr, 'workflow': d.category.workflow.name if d.category_id and d.category.workflow_id else '',
            'distribution': dist, 'my_distribution': {'require_ack': mine.require_ack, 'acknowledged_at': mine.acknowledged_at, 'version': mine.version.label} if mine else None,
            'links': links, 'activity': act, 'grants': grants_out(d) if 'admin' in r else None})
        from . import lifecycle as LC   # v1.73.6 expiry, renewal, retention, legal hold, archive
        ren = d.renewals.filter(deleted_at__isnull=True).order_by('-id').first()
        out.update({'renewal_of_doc': {'id': d.renewal_of_id, 'number': d.renewal_of.number, 'title': d.renewal_of.title} if d.renewal_of_id else None,
                    'renewed_by': {'id': ren.id, 'number': ren.number, 'status': ren.status} if ren else None,
                    'renewal_owner': uname(LC.renewal_owner(d)), 'legal_hold_reason': d.legal_hold_reason, 'legal_hold_by': uname(d.legal_hold_by_id),
                    'legal_hold_at': d.legal_hold_at, 'held': LC.is_held(d), 'archive_location': d.archive_location, 'archived_at': d.archived_at,
                    'archived_by': uname(d.archived_by_id), 'retention_reviewed_at': d.retention_reviewed_at,
                    'alerts': [{'kind': a.kind, 'days': a.days, 'to': uname(a.to_user_id), 'sent_at': a.sent_at} for a in d.expiry_alerts.order_by('-sent_at')[:20]],
                    'disposal': next(({'method': x.method, 'certificate': x.certificate, 'by': uname(x.authorised_by_id), 'at': x.authorised_at, 'early': x.early,
                                       'reason': x.reason, 'evidence': bool(x.evidence)} for x in d.disposals.order_by('-id')[:1]), None)})
        return Response(out)

    @refused
    def patch(self, request, pk):
        who = S.Who(request)
        d, _ = _doc(pk, who)
        S.update_meta(who, d, _body(request))
        return Response(doc_row(d))


class DocVersions(APIView):
    permission_classes = [Member]
    parser_classes = [MultiPartParser, FormParser]

    @refused
    def post(self, request, pk):
        who = S.Who(request)
        d, _ = _doc(pk, who)
        up = request.FILES.get('file')
        if up is None:
            raise S.Refused(tr('Choose a file.'), problems={'file': tr('Required.')})
        b = _body(request)
        v = S.new_revision(who, d, up, b.get('note') or '', major=str(b.get('major')).lower() in ('1', 'true'))
        return Response(ver_out(v), status=201)


class VersionFile(APIView):
    permission_classes = [Member]

    @refused
    def get(self, request, pk):
        who = S.Who(request)
        v = Mo.Version.objects.select_related('document').filter(pk=pk).first()
        if v is None:
            raise Http404
        d, r = _doc(v.document_id, who)
        dl = request.GET.get('dl') == '1'
        S.need(r, 'download' if dl else 'view')
        # readers of the published version only see that one; drafts are for the people who work on it
        if v.pk != d.published_id and not ({'edit', 'upload', 'review', 'approve', 'admin'} & r) and \
                not Mo.Approval.objects.filter(version=v, approver_id=who.uid).exists():
            raise S.Refused(tr('Only the version in force can be opened.'), 403)
        if not v.file:
            raise S.Refused(tr('The file of this version was disposed of.'), 410)
        S.log_act(who, 'downloaded' if dl else 'viewed', v.label, document=d)
        S.seen(who, d)
        resp = FileResponse(v.file.open('rb'), as_attachment=dl, filename=v.file_name, content_type=v.mime or mimetypes.guess_type(v.file_name)[0] or 'application/octet-stream')
        resp['X-Content-Type-Options'] = 'nosniff'
        return resp


class DocCompare(APIView):
    permission_classes = [Member]

    @refused
    def get(self, request, pk):
        who = S.Who(request)
        d, r = _doc(pk, who)
        S.need(r, 'view')
        a = d.versions.filter(pk=request.GET.get('a') or 0).first()
        b = d.versions.filter(pk=request.GET.get('b') or 0).first()
        if a is None or b is None:
            raise S.Refused(tr('Choose two versions.'))
        out = S.compare(a, b)
        out.update({'a': ver_out(a), 'b': ver_out(b), 'same_file': a.sha256 == b.sha256})
        return Response(out)


class DocAction(APIView):
    permission_classes = [Member]

    @refused
    def post(self, request, pk):
        who = S.Who(request)
        b = _body(request)
        act = b.get('action')
        d, r = _doc(pk, who, right=None, trashed=act == 'untrash')
        if 'view' not in r and act not in ('untrash',) and not (act == 'acknowledge'):
            raise S.Refused(tr('You do not have access to this document.'), 403)
        if act == 'submit':
            S.submit(who, d, b.get('note') or '')
        elif act == 'withdraw':
            S.withdraw(who, d)
        elif act == 'publish':
            S.publish(who, d)
        elif act in ('lock', 'unlock'):
            S.lock(who, d, act == 'lock')
        elif act == 'trash':
            S.trash(who, d)
        elif act == 'untrash':
            S.untrash(who, d)
        elif act == 'archive':   # v1.73.6 with the place of the paper original
            from .lifecycle import archive
            archive(who, d, b.get('location') or '')
        elif act in ('hold', 'release'):   # v1.73.6 legal hold
            from .lifecycle import set_hold
            set_hold(who, d, act == 'hold', b.get('reason') or '')
        elif act == 'review':   # v1.73.6 retention review
            from .lifecycle import review
            review(who, d, b.get('decision') or '', b.get('note') or '')
        elif act == 'dispose':   # v1.73.6 through the disposal register (POST documents/<id>/dispose/ with the evidence file)
            from .lifecycle import dispose
            dispose(who, d, b.get('method') or 'deleted', b.get('certificate') or '', b.get('reason') or '')
        elif act in ('obsolete', 'reactivate'):
            S.set_status(who, d, {'obsolete': 'obsolete', 'reactivate': 'published'}[act])
        elif act == 'acknowledge':
            S.acknowledge(who, d)
        elif act == 'move':
            S.move_document(who, d, _folder(b.get('target'), who))
        elif act == 'restore':
            old = d.versions.filter(pk=b.get('version') or 0).first()
            if old is None:
                raise S.Refused(tr('Choose the version to restore.'))
            S.restore_version(who, d, old, b.get('note') or '')
        else:
            raise S.Refused(tr('Unknown action.'))
        d.refresh_from_db()
        return Response(doc_row(d))


class DocRights(APIView):
    permission_classes = [Member]

    @refused
    def put(self, request, pk):
        who = S.Who(request)
        d, _ = _doc(pk, who)
        S.set_grants(who, d, _body(request).get('rows') or [])
        return Response({'grants': grants_out(d)})


class DocDistribute(APIView):
    permission_classes = [Member]

    @refused
    def post(self, request, pk):
        who = S.Who(request)
        d, _ = _doc(pk, who)
        b = _body(request)
        n = S.distribute(who, d, users=b.get('users') or [], groups=b.get('groups') or [], branches=b.get('branches') or [],
                         departments=b.get('departments') or [], require_ack=str(b.get('require_ack')).lower() in ('1', 'true'))
        return Response({'sent': n})


class DocLinks(APIView):
    permission_classes = [Member]

    @refused
    def post(self, request, pk):
        who = S.Who(request)
        d, _ = _doc(pk, who)
        b = _body(request)
        lk = S.make_link(who, d, days=b.get('days') or 7, recipient=b.get('recipient') or '', max_downloads=b.get('max_downloads'),
                         allow_download=str(b.get('allow_download', 'true')).lower() in ('1', 'true'))
        return Response({'id': lk.id, 'token': lk.token, 'expires_at': lk.expires_at}, status=201)


class LinkRevoke(APIView):
    permission_classes = [Member]

    @refused
    def post(self, request, pk):
        who = S.Who(request)
        lk = Mo.ShareLink.objects.select_related('document').filter(pk=pk).first()
        if lk is None:
            raise Http404
        S.revoke_link(who, lk)
        return Response({'state': S.link_state(lk)})


class Decide(APIView):
    permission_classes = [Member]

    @refused
    def post(self, request, pk):
        who = S.Who(request)
        a = Mo.Approval.objects.select_related('version__document__category__workflow').filter(pk=pk).first()
        if a is None:
            raise Http404
        b = _body(request)
        S.decide(who, a, b.get('decision'), b.get('comment') or '')
        return Response({'ok': True})


class Inbox(APIView):
    """what waits for the user: approvals, documents to read / acknowledge, documents of theirs expiring, checked out by them"""
    permission_classes = [Member]

    @refused
    def get(self, request):
        who = S.Who(request)
        today = _zt_today()
        ap = []
        for a in Mo.Approval.objects.filter(approver_id=who.uid, decision='pending', version__document__deleted_at__isnull=True) \
                .select_related('version__document__folder', 'version__document__category', 'version__document__current', 'version__document__published').order_by('due_date'):
            d = a.version.document
            row = doc_row(d)
            row.update({'approval': a.id, 'stage_name': a.stage_name, 'kind': a.kind, 'due_date': a.due_date, 'version_label': a.version.label,
                        'author': uname(a.version.author_id), 'overdue': bool(a.due_date and a.due_date < today)})
            ap.append(row)
        rd = []
        for x in Mo.Distribution.objects.filter(user_id=who.uid, document__deleted_at__isnull=True).filter(
                Q(require_ack=True, acknowledged_at__isnull=True) | Q(require_ack=False, viewed_at__isnull=True)).select_related(
                'document__folder', 'document__category', 'document__current', 'document__published', 'version').order_by('-sent_at')[:300]:
            row = doc_row(x.document)
            row.update({'require_ack': x.require_ack, 'sent_at': x.sent_at, 'sent_by': uname(x.sent_by_id), 'version_label': x.version.label})
            rd.append(row)
        mine = Mo.Document.objects.filter(deleted_at__isnull=True).filter(Q(owner_id=who.uid) | Q(created_by_id=who.uid)).select_related(
            'folder', 'category', 'current', 'published')
        exp = [doc_row(d) for d in mine.filter(expiry_date__isnull=False, expiry_date__lte=today + timedelta(days=30)).exclude(
            status__in=('archived', 'obsolete', 'disposed')).order_by('expiry_date')[:200]]
        back = [doc_row(d) for d in mine.filter(status__in=('revision_required', 'rejected', 'approved'))[:200]]
        locked = [doc_row(d) for d in Mo.Document.objects.filter(locked_by_id=who.uid, deleted_at__isnull=True).select_related('folder', 'category', 'current', 'published')[:200]]
        return Response({'approvals': ap, 'to_read': rd, 'expiring': exp, 'returned': back, 'checked_out': locked})


class Search(APIView):
    permission_classes = [Member]

    @refused
    def get(self, request):
        who = S.Who(request)
        docs = S.search(who, request.GET)
        if request.GET.get('export') == 'xlsx':   # v1.70.7: the found documents with their fields
            return _export(docs, request)
        rows = []
        for d in docs:
            r = doc_row(d)
            r['fields'] = d.fields or {}
            rows.append(r)
        out = {'documents': rows, 'count': len(rows)}
        if request.GET.get('q') and not any(k.startswith('f.') for k in request.GET):   # v1.70.8: the files attached in the other apps too
            out['linked'] = L.search(who, request.GET.get('q'))
        return Response(out)


def _export(docs, request):
    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.styles import Font
    cols, seen = [], set()
    for d in F.doc_fields():
        if d['key'] not in seen and F.visible_in(d, 'export'):
            seen.add(d['key'])
            cols.append(d)
    for doc in docs:
        for d in F.cat_fields(doc.category):
            if d['key'] not in seen and F.visible_in(d, 'export'):
                seen.add(d['key'])
                cols.append(d)
    wb = Workbook()
    ws = wb.active
    ws.title = 'Documents'
    head = [tr(x) for x in ('Number', 'Title', 'Folder', 'Category', 'Status', 'Version', 'Confidentiality', 'Owner', 'Effective', 'Expiry', 'Updated')] + [c['label'] for c in cols]
    ws.append(head)
    for c in ws[1]:
        c.font = Font(bold=True)
    for doc in docs:
        r = doc_row(doc)
        ws.append([r['number'], r['title'], r['folder_path'], r['category'], r['status_label'], r['version'], tr(doc.get_confidentiality_display()), r['owner'],
                   doc.effective_date, doc.expiry_date, timezone.localtime(doc.updated_at).replace(tzinfo=None) if doc.updated_at else None]
                  + [F.text(c, (doc.fields or {}).get(c['key'])) for c in cols])
    for col, w in zip('ABCDEFGHIJK', (18, 40, 40, 20, 16, 9, 14, 22, 12, 12, 18)):
        ws.column_dimensions[col].width = w
    for row in ws.iter_rows(min_row=2):
        for c in row[8:10]:
            c.number_format = 'DD/MM/YYYY'
        row[10].number_format = 'DD/MM/YYYY HH:MM'
    buf = BytesIO()
    wb.save(buf)
    resp = HttpResponse(buf.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = f'attachment; filename="documents-{_zt_today().isoformat()}.xlsx"'
    return resp


class Trash(APIView):
    permission_classes = [Member]

    @refused
    def get(self, request):
        who = S.Who(request)
        qs = Mo.Document.objects.filter(deleted_at__isnull=False).select_related('folder', 'category', 'current', 'published')
        if not who.dms_admin:
            qs = qs.filter(deleted_by_id=who.uid)
        rows = []
        for d in qs.order_by('-deleted_at')[:500]:
            row = doc_row(d)
            row.update({'deleted_by': uname(d.deleted_by_id), 'purge_on': (d.deleted_at + timedelta(days=30)).date()})
            rows.append(row)
        return Response({'documents': rows})


# ------------------------------------------------------------------ settings (Document Center administrators)
def _admin(request):
    who = S.Who(request)
    if not who.dms_admin:
        raise S.Refused(tr('Only a Document Center administrator may change these settings.'), 403)
    return who


def cat_out(c):
    return {'id': c.id, 'name': c.name, 'code': c.code, 'parent': c.parent_id, 'description': c.description, 'fields': [F.norm(f, i) for i, f in enumerate(c.fields or [])], 'number_pattern': c.number_pattern,
            'number_reset': c.number_reset, 'workflow': c.workflow_id, 'workflow_name': c.workflow.name if c.workflow_id else '',
            'default_confidentiality': c.default_confidentiality, 'expiry_required': c.expiry_required, 'retention_years': c.retention_years, 'active': c.active,
            'documents': c.documents.filter(deleted_at__isnull=True).count(),
            # v1.73.6 expiry, renewal, retention
            'alert_days': c.alert_days or list(Mo.ALERT_DAYS), 'renewal_owner': c.renewal_owner, 'renewal_user_id': c.renewal_user_id,
            'renewal_user': uname(c.renewal_user_id), 'escalate_days': c.escalate_days, 'escalate_user_id': c.escalate_user_id,
            'escalate_user': uname(c.escalate_user_id), 'retention_start': c.retention_start, 'retention_action': c.retention_action}


def _clean_cat(b, c=None):
    name = ' '.join(str(b.get('name') or (c.name if c else '')).split())[:120]
    code = ''.join(ch for ch in str(b.get('code') or (c.code if c else '')).upper() if ch.isalnum())[:10]
    probs = {}
    if not name:
        probs['name'] = tr('Required.')
    if not code:
        probs['code'] = tr('Letters and digits only.')
    if code and Mo.DocCategory.objects.filter(code=code).exclude(pk=getattr(c, 'pk', None)).exists():
        probs['code'] = tr('Already used.')
    pat = str(b.get('number_pattern') if 'number_pattern' in b else (c.number_pattern if c else '{dept}-{cat}-{yyyy}-{seq:4}'))[:80]
    if '{seq' not in pat:
        probs['number_pattern'] = tr('The pattern needs {seq} (the running number).')
    import re
    bad = [t for t in re.findall(r'\{([^}]*)\}', pat) if t.split(':')[0] not in ('company', 'branch', 'dept', 'cat', 'yyyy', 'yy', 'mm', 'seq')]
    if bad:
        probs['number_pattern'] = tr('Unknown part: {0}', ', '.join('{' + x + '}' for x in bad))
    fields = b.get('fields') if 'fields' in b else (c.fields if c else [])
    clean_f, seen = [], {d['key'] for d in F.doc_fields()}   # v1.70.7: the designer's 20 types; not the name of a field of every document
    for i, f in enumerate(fields or []):
        if not ' '.join(str(f.get('label') or '').split()):
            continue
        try:
            d = F.clean_def(f, i, seen)
        except F.Refused as e:
            probs['fields'] = str(e)
            continue
        seen.add(d['key'])
        clean_f.append(d)
    wf = b.get('workflow') if 'workflow' in b else (c.workflow_id if c else None)
    if wf not in (None, '') and not Mo.DocWorkflow.objects.filter(pk=wf).exists():
        probs['workflow'] = tr('Not found.')
    conf = b.get('default_confidentiality') or (c.default_confidentiality if c else 'internal')
    if conf not in dict(Mo.CONFIDENTIALITY):
        probs['default_confidentiality'] = tr('Invalid.')
    reset = b.get('number_reset') or (c.number_reset if c else 'yearly')
    if reset not in ('yearly', 'monthly', 'never'):
        probs['number_reset'] = tr('Invalid.')
    try:
        ret = int(b.get('retention_years') if b.get('retention_years') not in (None, '') else (c.retention_years if c else 0) or 0)
        if not 0 <= ret <= 100:
            raise ValueError
    except (TypeError, ValueError):
        probs['retention_years'] = tr('0 to 100 years.')
        ret = 0
    # v1.73.6 expiry alerts, renewal owner, escalation, retention start and action
    days = b.get('alert_days') if 'alert_days' in b else (c.alert_days if c else [])
    if isinstance(days, str):
        days = [x.strip() for x in days.replace(';', ',').split(',') if x.strip()]
    try:
        days = sorted({int(x) for x in (days or [])}, reverse=True)
        if any(not 0 <= x <= 730 for x in days) or len(days) > 10:
            raise ValueError
    except (TypeError, ValueError):
        probs['alert_days'] = tr('Days from 0 to 730, at most 10, e.g. 90, 60, 30, 15, 7.')
        days = []
    ro = b.get('renewal_owner') or (c.renewal_owner if c else 'owner')
    if ro not in ('owner', 'user'):
        probs['renewal_owner'] = tr('Invalid.')
    ru = S._int(b.get('renewal_user') if 'renewal_user' in b else (c.renewal_user_id if c else None))
    if ro == 'user' and not ru:
        probs['renewal_user'] = tr('Choose the user who renews.')
    try:
        esc = int(b.get('escalate_days') if b.get('escalate_days') not in (None, '') else (c.escalate_days if c else 7))
        if not 0 <= esc <= 365:
            raise ValueError
    except (TypeError, ValueError):
        probs['escalate_days'] = tr('0 to 365 days.')
        esc = 7
    eu = S._int(b.get('escalate_user') if 'escalate_user' in b else (c.escalate_user_id if c else None))
    rs = b.get('retention_start') or (c.retention_start if c else 'effective')
    if rs not in dict(Mo.RETENTION_START):
        probs['retention_start'] = tr('Invalid.')
    ra = b.get('retention_action') or (c.retention_action if c else 'review')
    if ra not in dict(Mo.RETENTION_ACTION):
        probs['retention_action'] = tr('Invalid.')
    if probs:
        raise S.Refused(tr('Check the highlighted fields.'), problems=probs)
    return {'alert_days': days, 'renewal_owner': ro, 'renewal_user_id': ru if ro == 'user' else None, 'escalate_days': esc, 'escalate_user_id': eu,
            'retention_start': rs, 'retention_action': ra,
            'name': name, 'code': code, 'description': str(b.get('description') if 'description' in b else (c.description if c else ''))[:1000],
            'fields': clean_f, 'number_pattern': pat, 'number_reset': reset, 'workflow_id': int(wf) if wf not in (None, '') else None,
            'default_confidentiality': conf, 'expiry_required': str(b.get('expiry_required') if 'expiry_required' in b else (c.expiry_required if c else False)).lower() in ('1', 'true'),
            'retention_years': ret, 'active': str(b.get('active') if 'active' in b else (c.active if c else True)).lower() in ('1', 'true')}


class Categories(APIView):
    permission_classes = [Member]

    @refused
    def get(self, request):
        S.Who(request)
        qs = Mo.DocCategory.objects.select_related('workflow').order_by('name')
        if request.GET.get('all') != '1':
            qs = qs.filter(active=True)
        return Response([cat_out(c) for c in qs])

    @refused
    def post(self, request):
        who = _admin(request)
        c = Mo.DocCategory.objects.create(**_clean_cat(_body(request)))
        S.log_act(who, 'category_created', f'{c.code} {c.name}')
        return Response(cat_out(c), status=201)


class CategoryOne(APIView):
    permission_classes = [Member]

    @refused
    def patch(self, request, pk):
        who = _admin(request)
        c = Mo.DocCategory.objects.filter(pk=pk).first()
        if c is None:
            raise Http404
        old_ret = (c.retention_years, c.retention_start)
        for k, v in _clean_cat(_body(request), c).items():
            setattr(c, k, v)
        c.save()
        if (c.retention_years, c.retention_start) != old_ret:   # v1.73.6: the documents' retention dates follow the category
            for d in Mo.Document.objects.filter(category=c).exclude(status='disposed').select_related('category'):
                LC.refresh_retention(d)
        # documents of the category follow its workflow from their next version
        Mo.Document.objects.filter(category=c, status='draft', published__isnull=True).update(controlled=bool(c.workflow_id))
        S.log_act(who, 'category_changed', f'{c.code} {c.name}')
        return Response(cat_out(c))

    @refused
    def delete(self, request, pk):
        who = _admin(request)
        c = Mo.DocCategory.objects.filter(pk=pk).first()
        if c is None:
            raise Http404
        if c.documents.exists():
            c.active = False
            c.save(update_fields=['active'])
            return Response({'deactivated': True})
        c.delete()
        S.log_act(who, 'category_deleted', f'{c.code} {c.name}')
        return Response(status=204)


def wf_out(w):
    return {'id': w.id, 'name': w.name, 'stages': w.stages, 'active': w.active, 'categories': list(w.categories.values_list('name', flat=True))}


def _clean_wf(b, w=None):
    name = ' '.join(str(b.get('name') or (w.name if w else '')).split())[:120]
    probs = {}
    if not name:
        probs['name'] = tr('Required.')
    elif Mo.DocWorkflow.objects.filter(name__iexact=name).exclude(pk=getattr(w, 'pk', None)).exists():
        probs['name'] = tr('Already used.')
    stages = []
    for i, s in enumerate(b.get('stages') if 'stages' in b else (w.stages if w else []) or []):
        nm = ' '.join(str(s.get('name') or '').split())[:120] or f'{tr("Stage")} {i + 1}'
        users = [int(x) for x in s.get('users') or [] if str(x).isdigit()]
        groups = [int(x) for x in s.get('groups') or [] if str(x).isdigit()]
        role = s.get('role') if s.get('role') in ('owner_manager', 'dept_head') else ''
        if not (users or groups or role):
            probs['stages'] = tr('Stage "{0}" needs its approvers (users, a group or a role).', nm)
        try:
            due = max(1, min(60, int(s.get('due_days') or 3)))
        except (TypeError, ValueError):
            due = 3
        stages.append({'name': nm, 'kind': 'review' if s.get('kind') == 'review' else 'approve', 'users': users, 'groups': groups, 'role': role,
                       'mode': 'all' if s.get('mode') == 'all' else 'any', 'due_days': due})
    if not stages:
        probs['stages'] = tr('Add at least one stage.')
    if probs:
        raise S.Refused(tr('Check the highlighted fields.'), problems=probs)
    return {'name': name, 'stages': stages, 'active': str(b.get('active') if 'active' in b else (w.active if w else True)).lower() in ('1', 'true')}


class Workflows(APIView):
    permission_classes = [Member]

    @refused
    def get(self, request):
        S.Who(request)
        return Response([wf_out(w) for w in Mo.DocWorkflow.objects.order_by('name')])

    @refused
    def post(self, request):
        who = _admin(request)
        w = Mo.DocWorkflow.objects.create(**_clean_wf(_body(request)))
        S.log_act(who, 'workflow_created', w.name)
        return Response(wf_out(w), status=201)


class WorkflowOne(APIView):
    permission_classes = [Member]

    @refused
    def patch(self, request, pk):
        who = _admin(request)
        w = Mo.DocWorkflow.objects.filter(pk=pk).first()
        if w is None:
            raise Http404
        for k, v in _clean_wf(_body(request), w).items():
            setattr(w, k, v)
        w.save()
        S.log_act(who, 'workflow_changed', w.name)
        return Response(wf_out(w))

    @refused
    def delete(self, request, pk):
        who = _admin(request)
        w = Mo.DocWorkflow.objects.filter(pk=pk).first()
        if w is None:
            raise Http404
        if w.categories.exists():
            raise S.Refused(tr('Categories use this workflow – change them first.'), 409)
        w.delete()
        S.log_act(who, 'workflow_deleted', w.name)
        return Response(status=204)


class Lookups(APIView):
    """the people, groups, branches, departments and categories the screens choose from"""
    permission_classes = [Member]

    @refused
    def get(self, request):
        from django.contrib.auth.models import Group
        who = S.Who(request)
        U = M('UserManagement', 'CustomUser')
        from django.db import connection
        users = U.objects.filter(is_active=True, tenants__schema_name=connection.schema_name).distinct().order_by('username')[:3000]
        return Response({
            'users': [{'id': u.id, 'name': (u.get_full_name() or u.username or u.email)} for u in users],
            'groups': [{'id': g.id, 'name': g.name} for g in Group.objects.order_by('name')],
            'branches': [{'id': b.id, 'name': b.branch_name} for b in M('OrganisationManager', 'brnch_mstr').objects.order_by('branch_name')],
            'departments': [{'id': d.id, 'name': d.dept_name} for d in M('OrganisationManager', 'dept_master').objects.order_by('dept_name')],
            'categories': [cat_out(c) for c in Mo.DocCategory.objects.filter(active=True).select_related('workflow').order_by('name')],
            'rights': [{'key': k, 'label': tr(Mo.RIGHT_LABELS.get(k, k.capitalize()))} for k in Mo.RIGHTS],
            'presets': [{'key': k, 'label': tr(k.capitalize()), 'rights': v} for k, v in Mo.PRESETS.items()],
            'statuses': [{'key': k, 'label': tr(v)} for k, v in Mo.STATUS],
            'confidentiality': [{'key': k, 'label': tr(v)} for k, v in Mo.CONFIDENTIALITY],
            'dms_admin': who.dms_admin, 'me': who.uid,
            'doc_fields': F.doc_fields(), 'folder_fields': F.folder_fields(),   # v1.70.7
            # v1.73.6 DMS phase 3
            'retention_start': [{'key': k, 'label': tr(v)} for k, v in Mo.RETENTION_START],
            'retention_action': [{'key': k, 'label': tr(v)} for k, v in Mo.RETENTION_ACTION],
            'disposal_methods': [{'key': k, 'label': tr(v)} for k, v in Mo.DisposalRecord.METHODS],
            'channels': [{'key': k, 'label': tr(v)} for k, v in Mo.Correspondence.CHANNELS],
            'corr_states': [{'key': k, 'label': tr(v)} for k, v in Mo.Correspondence.STATES],
            'corr_manage': LC.corr_may(who, 'manage'), 'corr_view': LC.corr_may(who, 'view') or who.has('view_correspondence')
            or Mo.Correspondence.objects.filter(Q(assigned_to_id=who.uid) | Q(approver_id=who.uid)).exists(),   # letters given to them
            'alert_days': list(Mo.ALERT_DAYS),
        })


class Report(APIView):
    """counts for the overview: by status, by category, expiring, waiting approvals, unacknowledged"""
    permission_classes = [Member]

    @refused
    def get(self, request):
        who = S.Who(request)
        today = _zt_today()
        docs = S.visible_documents(who, Mo.Document.objects.filter(deleted_at__isnull=True))
        by_status, by_cat = {}, {}
        for d in docs:
            by_status[d.status] = by_status.get(d.status, 0) + 1
            k = d.category.name if d.category_id else tr('No category')
            by_cat[k] = by_cat.get(k, 0) + 1
        ids = [d.id for d in docs]
        return Response({
            'total': len(docs), 'by_status': by_status, 'by_category': by_cat,
            'expired': sum(1 for d in docs if d.expiry_date and d.expiry_date < today and d.status not in ('archived', 'obsolete', 'disposed')),
            'expiring_30': sum(1 for d in docs if d.expiry_date and today <= d.expiry_date <= today + timedelta(days=30)),
            'waiting_approval': Mo.Approval.objects.filter(version__document_id__in=ids, decision='pending').values('version_id').distinct().count(),
            'overdue_approval': Mo.Approval.objects.filter(version__document_id__in=ids, decision='pending', due_date__lt=today).count(),
            'unacknowledged': Mo.Distribution.objects.filter(document_id__in=ids, require_ack=True, acknowledged_at__isnull=True).count(),
        })


# ------------------------------------------------------------------ public share link (no login)
def _link(token):
    lk = Mo.ShareLink.objects.select_related('document__published', 'document__current').filter(token=token).first()
    if lk is None:
        raise Http404
    return lk


def _page(title, body):
    return HttpResponse(f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>{escape(title)}</title><style>body{{font-family:system-ui,sans-serif;background:#f3f5f4;color:#1d2a26;margin:0}}
main{{max-width:560px;margin:48px auto;background:#fff;border:1px solid #d7dfdb;border-radius:10px;padding:28px}}h1{{font-size:20px;margin:0 0 6px}}
p{{color:#4b5a55;line-height:1.5}}a.b{{display:inline-block;background:#1f6f5c;color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none}}</style></head>
<body><main>{body}</main></body></html>''')


class PublicLink(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, token):
        lk = _link(token)
        st = S.link_state(lk)
        if st != 'active':
            msg = {'revoked': tr('This link was withdrawn by the sender.'), 'expired': tr('This link has expired.'),
                   'used': tr('This link was used the number of times allowed.'), 'withdrawn': tr('This document is no longer available.')}[st]
            return _page(tr('Link not available'), f'<h1>{escape(tr("Link not available"))}</h1><p>{escape(msg)}</p>')
        d = lk.document
        v = lk.version or d.published or d.current
        Mo.Activity.objects.create(document=d, action='link_opened', detail=lk.recipient[:200],
                                   ip=(request.META.get('HTTP_X_FORWARDED_FOR') or request.META.get('REMOTE_ADDR') or '').split(',')[0][:64])
        from django.db import connection
        q = f'?schema={escape(connection.schema_name)}'
        btn = f'<p><a class="b" href="file/{q}">{escape(tr("Download"))}</a></p>' if lk.allow_download else \
            f'<p><a class="b" href="file/{q}&amp;view=1">{escape(tr("Open"))}</a></p>'
        exp = timezone.localtime(lk.expires_at).strftime('%d %b %Y %H:%M')
        return _page(d.title, f'<h1>{escape(d.title)}</h1><p>{escape(d.number)} · {escape(tr("Version"))} {escape(v.label)} · {escape(v.file_name)}</p>'
                     f'{btn}<p style="font-size:13px">{escape(tr("This link works until {0}.", exp))}</p>')


class PublicLinkFile(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, token):
        lk = _link(token)
        if S.link_state(lk) != 'active':
            raise Http404
        d = lk.document
        v = lk.version or d.published or d.current
        if v is None or not v.file:
            raise Http404
        from django.db.models import F
        Mo.ShareLink.objects.filter(pk=lk.pk).update(downloads=F('downloads') + 1)
        Mo.Activity.objects.create(document=d, action='link_downloaded', detail=lk.recipient[:200],
                                   ip=(request.META.get('HTTP_X_FORWARDED_FOR') or request.META.get('REMOTE_ADDR') or '').split(',')[0][:64])
        resp = FileResponse(v.file.open('rb'), as_attachment=lk.allow_download and request.GET.get('view') != '1', filename=v.file_name,
                            content_type=v.mime or 'application/octet-stream')
        resp['X-Content-Type-Options'] = 'nosniff'
        resp['Cache-Control'] = 'no-store'
        return resp


# ------------------------------------------------------------------ v1.70.7: fields of every document / folder (form designer)
def _scope(scope):
    if scope not in ('document', 'folder'):
        raise Http404
    return scope


def _strip_values(scope, key):
    """deleting a field deletes its values (as the form designer does)"""
    Model = Mo.Document if scope == 'document' else Mo.DocFolder
    for o in Model.objects.filter(fields__has_key=key).only('id', 'fields'):
        o.fields = {k: v for k, v in (o.fields or {}).items() if k != key}
        o.save(update_fields=['fields'])


def _save_def(who, scope, body, f=None):
    taken = set(Mo.DocField.objects.filter(scope=scope).exclude(pk=getattr(f, 'pk', None)).values_list('key', flat=True))
    if scope == 'document':   # nor the name of a category's own field
        for c in Mo.DocCategory.objects.all():
            taken |= {F.norm(x, i)['key'] for i, x in enumerate(c.fields or [])}
    raw = dict(body)
    if f is not None:
        raw.setdefault('key', f.key)
        for k in ('label', 'field_type', 'options', 'required', 'section', 'order', 'help_text', 'placeholder', 'default', 'hidden', 'read_only', 'rules'):
            raw.setdefault(k, getattr(f, k))
    elif not raw.get('order'):
        last = Mo.DocField.objects.filter(scope=scope).order_by('-order').first()
        raw['order'] = (last.order if last else 0) + 10
    try:
        d = F.clean_def(raw, 0, taken)
    except F.Refused as e:
        raise S.Refused(str(e), problems=e.problems)
    if f is not None and d['key'] != f.key:
        d['key'] = f.key   # the name stays: renaming the label keeps the values
    if f is not None and d['field_type'] != f.field_type:
        Model = Mo.Document if scope == 'document' else Mo.DocFolder
        bad = []
        from Chatter.views import clean_value
        for o in Model.objects.filter(fields__has_key=f.key).only('id', 'fields')[:5000]:
            v = (o.fields or {}).get(f.key)
            nv, e = clean_value(F._F(d, required=False), F.text(F.field_out(f), v) if d['field_type'] not in ('file', 'employee') else v)
            if e:
                bad.append(e)
        if bad:
            raise S.Refused(tr('{0} saved value(s) do not fit the new type – e.g. {1}', len(bad), bad[0]), problems={'field_type': tr('Invalid.')})
    if f is None:
        f = Mo.DocField(scope=scope, created_by_id=who.uid)
    for k, v in d.items():
        setattr(f, k, v)
    f.save()
    S.log_act(who, 'field_saved', f'{scope}: {f.label}')
    return f


class FieldDefs(APIView):
    """GET the fields of every document / every folder; POST add one (Document Center administrators)"""
    permission_classes = [Member]

    @refused
    def get(self, request, scope):
        S.Who(request)
        return Response([F.field_out(f) for f in Mo.DocField.objects.filter(scope=_scope(scope))])

    @refused
    def post(self, request, scope):
        who = _admin(request)
        return Response(F.field_out(_save_def(who, _scope(scope), _body(request))), status=201)


class FieldDefOne(APIView):
    permission_classes = [Member]

    @refused
    def patch(self, request, scope, pk):
        who = _admin(request)
        f = Mo.DocField.objects.filter(pk=pk, scope=_scope(scope)).first()
        if f is None:
            raise Http404
        return Response(F.field_out(_save_def(who, scope, _body(request), f)))

    @refused
    def delete(self, request, scope, pk):
        who = _admin(request)
        f = Mo.DocField.objects.filter(pk=pk, scope=_scope(scope)).first()
        if f is None:
            raise Http404
        _strip_values(scope, f.key)
        S.log_act(who, 'field_deleted', f'{scope}: {f.label}')
        f.delete()
        return Response(status=204)


class FieldOrder(APIView):
    """POST {ids: [...]} – the order of the fields"""
    permission_classes = [Member]

    @refused
    def post(self, request, scope):
        _admin(request)
        ids = [int(x) for x in (_body(request).get('ids') or []) if str(x).isdigit()]
        for i, pk in enumerate(ids):
            Mo.DocField.objects.filter(pk=pk, scope=_scope(scope)).update(order=(i + 1) * 10)
        return Response([F.field_out(f) for f in Mo.DocField.objects.filter(scope=scope)])


# ------------------------------------------------------------------ v1.70.8: the files attached in the other apps
def _linked(request, ref):
    who = S.Who(request)
    try:
        src, row, folder = L.resolve(ref, who)
    except LookupError:
        raise Http404
    return who, src, row, folder


class LinkedFile(APIView):
    """GET linked/<source:pk>/file/ – open (inline) or download (?download=1) a file of another app, with that app's rights"""
    permission_classes = [Member]

    @refused
    def get(self, request, ref):
        who, src, row, folder = _linked(request, ref)
        f = src.fileobj(row)
        try:
            fh = f.open('rb')
        except (OSError, ValueError):
            raise S.Refused(tr('The file is missing on the server.'), 404)
        name = src.name(row)
        mime = mimetypes.guess_type(name)[0] or 'application/octet-stream'
        S.log_act(who, 'linked_viewed', f'{src.key}: {name}', folder=folder)
        resp = FileResponse(fh, content_type=mime, as_attachment=request.GET.get('download') == '1', filename=_ascii(name) or 'file')
        resp['X-Content-Type-Options'] = 'nosniff'
        return resp


class LinkedCopy(APIView):
    """POST linked/<source:pk>/copy/ {title?, category?} – a managed document made from it in the record's folder (the source stays)"""
    permission_classes = [Member]

    @refused
    def post(self, request, ref):
        from django.core.files import File
        who, src, row, folder = _linked(request, ref)
        if folder is None:
            raise S.Refused(tr('The record has no folder in the Document Center yet – open its folder first.'))
        S.need(S.folder_rights(folder, who), 'upload', tr('You may not upload into this folder.'))
        d = _body(request)
        name = src.name(row)
        f = src.fileobj(row)
        try:
            fh = f.open('rb')
        except (OSError, ValueError):
            raise S.Refused(tr('The file is missing on the server.'), 404)
        try:
            up = File(fh, name=name)
            item = src.item(row)
            data = {'title': d.get('title') or os.path.splitext(name)[0], 'category': d.get('category') or '', 'fields': d.get('fields') or {},
                    'remarks': tr('Copied from {0} – {1}', item['group'], item['from'] or name)[:4000], 'change_note': tr('Copied from {0}', item['group'])}
            doc, dup = S.register(who, folder, up, data)
        finally:
            fh.close()
        S.log_act(who, 'linked_copied', f'{src.key}:{row.pk} → {doc.number}', document=doc)
        out = doc_row(doc)
        if dup is not None and dup.pk != doc.pk:
            out['duplicate_of'] = {'id': dup.id, 'number': dup.number, 'title': dup.title} if 'view' in S.doc_rights(dup, who) else {'id': None, 'number': '', 'title': ''}
        return Response(out, status=201)



# ================================================================== v1.73.6 – DMS phase 3: renewal, disposal, registers, correspondence
from . import lifecycle as LC  # noqa: E402


class DocRenew(APIView):
    """POST file + expiry_date (+ title, effective_date, remarks) – the renewed copy, linked to this document"""
    permission_classes = [Member]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @refused
    def post(self, request, pk):
        who = S.Who(request)
        d, _ = _doc(pk, who)
        up = request.FILES.get('file')
        if up is None:
            raise S.Refused(tr('Choose the file of the renewed document.'), problems={'file': tr('Required.')})
        new = LC.renew(who, d, up, _body(request))
        return Response(doc_row(new), status=201)


class DocDispose(APIView):
    """POST method, certificate, reason (+ evidence file) – disposal through the register"""
    permission_classes = [Member]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @refused
    def post(self, request, pk):
        who = S.Who(request)
        d, _ = _doc(pk, who)
        b = _body(request)
        rec = LC.dispose(who, d, b.get('method') or 'deleted', b.get('certificate') or '', b.get('reason') or '', request.FILES.get('evidence'))
        d.refresh_from_db()
        return Response({'document': doc_row(d), 'register_id': rec.pk}, status=201)


def _admin_or(request, right):
    who = S.Who(request)
    if not (who.dms_admin or who.has(right)):
        raise S.Refused(tr('Only a Document Center administrator may see this register.'), 403)
    return who


class Expiring(APIView):
    """GET ?days=90 – the expiry and renewal register: documents expiring within the days (and expired ones not renewed)"""
    permission_classes = [Member]

    @refused
    def get(self, request):
        who = S.Who(request)
        days = int(request.GET.get('days') or 90)
        today = _zt_today()
        qs = S.visible_documents(who, Mo.Document.objects.filter(deleted_at__isnull=True, expiry_date__isnull=False, expiry_date__lte=today + timedelta(days=days),
                                                                  status__in=LC.ACTIVE).select_related('category', 'folder'))   # a list
        rows = []
        for d in qs:
            ren = d.renewals.filter(deleted_at__isnull=True).order_by('-id').first()
            row = doc_row(d)
            row.update({'renewal_owner': uname(LC.renewal_owner(d)), 'renewed_by': {'id': ren.id, 'number': ren.number, 'status': ren.status} if ren else None,
                        'last_alert': next(({'kind': a.kind, 'days': a.days, 'at': a.sent_at} for a in d.expiry_alerts.order_by('-sent_at')[:1]), None)})
            rows.append(row)
        rows.sort(key=lambda r: (r['expiry_date'] or today))
        return Response({'documents': rows, 'days': days})


class RetentionDue(APIView):
    """GET – retention reached (to review), legal holds, the archive register and the disposal register (administrators, Dispose right)"""
    permission_classes = [Member]

    @refused
    def get(self, request):
        who = _admin_or(request, 'manage_dms')
        today = _zt_today()
        base = Mo.Document.objects.filter(deleted_at__isnull=True).select_related('category', 'folder')
        due = [dict(doc_row(d), held=LC.is_held(d), action=(d.category.retention_action if d.category_id else 'review'))
               for d in base.filter(retention_until__lte=today).exclude(status='disposed').order_by('retention_until')[:500]]
        holds = [dict(doc_row(d), reason=d.legal_hold_reason, by=uname(d.legal_hold_by_id), at=d.legal_hold_at) for d in base.filter(legal_hold=True)[:500]]
        arch = [dict(doc_row(d), location=d.archive_location, archived_at=d.archived_at, archived_by=uname(d.archived_by_id))
                for d in base.filter(status='archived').order_by('-archived_at')[:500]]
        disp = [{'id': r.id, 'number': r.number, 'title': r.title, 'category': r.category, 'retention_until': r.retention_until, 'early': r.early, 'reason': r.reason,
                 'method': r.method, 'method_label': tr(r.get_method_display()), 'certificate': r.certificate, 'evidence': r.evidence_name,
                 'by': uname(r.authorised_by_id), 'at': r.authorised_at} for r in Mo.DisposalRecord.objects.all()[:1000]]
        return Response({'due': due, 'holds': holds, 'archive': arch, 'disposals': disp})


class DisposalEvidence(APIView):
    permission_classes = [Member]

    @refused
    def get(self, request, pk):
        _admin_or(request, 'manage_dms')
        r = Mo.DisposalRecord.objects.filter(pk=pk).first()
        if r is None or not r.evidence:
            raise S.Refused(tr('Not found.'), 404)
        from django.http import FileResponse
        return FileResponse(r.evidence.open('rb'), as_attachment=True, filename=r.evidence_name or 'evidence')


def corr_out(c):
    return {'id': c.id, 'direction': c.direction, 'number': c.number, 'date': c.date, 'subject': c.subject, 'party': c.party, 'partner_id': c.partner_id,
            'their_reference': c.their_reference, 'channel': c.channel, 'channel_label': tr(c.get_channel_display()),
            'document': {'id': c.document_id, 'number': c.document.number, 'title': c.document.title} if c.document_id else None,
            'reply_to': {'id': c.reply_to_id, 'number': c.reply_to.number} if c.reply_to_id else None,
            'replies': [{'id': x.id, 'number': x.number, 'status': x.status} for x in c.replies.all()[:20]],
            'deadline': c.deadline, 'overdue': bool(c.deadline and c.deadline < _zt_today() and c.status not in ('replied', 'closed', 'dispatched')),
            'assigned_to_id': c.assigned_to_id, 'assigned_to': uname(c.assigned_to_id), 'branch_id': c.branch_id, 'department_id': c.department_id,
            'confidentiality': c.confidentiality, 'status': c.status, 'status_label': tr(c.get_status_display()), 'approver_id': c.approver_id,
            'approver': uname(c.approver_id), 'decided_at': c.decided_at, 'decision_note': c.decision_note, 'dispatch_method': c.dispatch_method,
            'tracking': c.tracking, 'dispatched_at': c.dispatched_at, 'remarks': c.remarks, 'created_by': uname(c.created_by_id), 'created_at': c.created_at,
            'closed_at': c.closed_at}


class CorrList(APIView):
    """GET ?direction=in|out&status&q&overdue=1 – the incoming / outgoing register; POST – register a letter"""
    permission_classes = [Member]

    @refused
    def get(self, request):
        who = S.Who(request)
        qs = LC.corr_visible(who).select_related('document', 'reply_to')
        g = request.GET
        if g.get('direction') in ('in', 'out'):
            qs = qs.filter(direction=g['direction'])
        if g.get('status'):
            qs = qs.filter(status=g['status'])
        if g.get('q'):
            from django.db.models import Q
            q = g['q']
            qs = qs.filter(Q(number__icontains=q) | Q(subject__icontains=q) | Q(party__icontains=q) | Q(their_reference__icontains=q))
        rows = [corr_out(c) for c in qs[:1000]]
        if g.get('overdue') in ('1', 'true'):
            rows = [r for r in rows if r['overdue']]
        return Response({'letters': rows, 'may_manage': LC.corr_may(who, 'manage')})

    @refused
    def post(self, request):
        who = S.Who(request)
        return Response(corr_out(LC.corr_save(who, _body(request))), status=201)


class CorrOne(APIView):
    permission_classes = [Member]

    def _get(self, who, pk):
        c = LC.corr_visible(who).filter(pk=pk).first()
        if c is None:
            raise S.Refused(tr('Not found.'), 404)
        return c

    @refused
    def get(self, request, pk):
        return Response(corr_out(self._get(S.Who(request), pk)))

    @refused
    def patch(self, request, pk):
        who = S.Who(request)
        return Response(corr_out(LC.corr_save(who, _body(request), self._get(who, pk))))

    @refused
    def delete(self, request, pk):
        who = S.Who(request)
        c = self._get(who, pk)
        if not LC.corr_may(who, 'manage'):
            raise S.Refused(tr('You may not register correspondence.'), 403)
        if c.status not in ('registered', 'draft'):
            raise S.Refused(tr('Only a letter just registered (or a draft) can be deleted – close it instead.'), 409)
        LC.corr_close_todos(c)
        c.delete()
        return Response(status=204)


class CorrAction(APIView):
    permission_classes = [Member]

    @refused
    def post(self, request, pk):
        who = S.Who(request)
        c = LC.corr_visible(who).filter(pk=pk).first()
        if c is None:
            raise S.Refused(tr('Not found.'), 404)
        b = _body(request)
        return Response(corr_out(LC.corr_action(who, c, b.get('action') or '', b)))
