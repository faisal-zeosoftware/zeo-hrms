"""v1.73.9 – Document Center phase 4 API (zeo-apps/dms/…): checklists, upload requests and the external portal, templates and forms,
e-signature. The portal and the signing page for people outside the company are plain pages (no account, a long random link)."""
import json

from django.apps import apps
from django.http import FileResponse, Http404, HttpResponse
from django.utils import timezone
from django.utils.html import escape
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from Translations.i18n import tr

from . import models as Mo
from . import phase4 as P
from . import services as S
from .views import Member, _body, _doc, _folder, doc_row, refused, uname

M = apps.get_model


def _ip(request):
    return (request.META.get('HTTP_X_FORWARDED_FOR') or request.META.get('REMOTE_ADDR') or '').split(',')[0].strip()[:64]


def _admin(who):
    if not who.dms_admin:
        raise S.Refused(tr('Only a Document Center administrator changes this.'), 403)


def _json(d, k):
    v = d.get(k)
    if isinstance(v, str) and v[:1] in '[{':
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


# ================================================================== checklists
def cl_out(c):
    cats = dict(Mo.DocCategory.objects.values_list('pk', 'name'))
    return {'id': c.pk, 'name': c.name, 'provider': c.provider, 'provider_label': tr(dict(Mo.DocChecklist.PROVIDERS).get(c.provider, c.provider)),
            'description': c.description, 'branch_ids': c.branch_ids, 'department_ids': c.department_ids, 'active': c.active,
            'items': [{**i, 'category': cats.get(i.get('category_id'), '')} for i in c.items]}


class Checklists(APIView):
    permission_classes = [Member]

    @refused
    def get(self, request):
        qs = Mo.DocChecklist.objects.all()
        if request.query_params.get('provider'):
            qs = qs.filter(provider=request.query_params['provider'])
        return Response({'checklists': [cl_out(c) for c in qs],
                         'providers': [{'key': k, 'label': tr(v)} for k, v in Mo.DocChecklist.PROVIDERS if S.provider_active(k)]})

    @refused
    def post(self, request):
        who = S.Who(request)
        _admin(who)
        d = _body(request)
        d['items'] = _json(d, 'items')
        c = Mo.DocChecklist.objects.create(created_by_id=who.uid, **P.clean_checklist(d))
        return Response(cl_out(c), status=201)


class ChecklistOne(APIView):
    permission_classes = [Member]

    def _get(self, pk):
        c = Mo.DocChecklist.objects.filter(pk=pk).first()
        if c is None:
            raise Http404
        return c

    @refused
    def put(self, request, pk):
        who = S.Who(request)
        _admin(who)
        c = self._get(pk)
        d = _body(request)
        d['items'] = _json(d, 'items')
        for k, v in P.clean_checklist(d, c).items():
            setattr(c, k, v)
        c.save()
        return Response(cl_out(c))

    @refused
    def delete(self, request, pk):
        who = S.Who(request)
        _admin(who)
        c = self._get(pk)
        if c.requests.exists():
            c.active = False
            c.save(update_fields=['active'])
            return Response({'detail': tr('Used by upload requests – made inactive.'), 'inactive': True})
        c.delete()
        return Response(status=204)


class RecordChecklist(APIView):
    """GET ?provider=&record= – the checklist status of one record (the record's own view right)"""
    permission_classes = [Member]

    @refused
    def get(self, request):
        who = S.Who(request)
        prov, rid = request.query_params.get('provider'), request.query_params.get('record')
        if prov not in S.PROVIDERS or not str(rid or '').isdigit():
            raise S.Refused(tr('Choose the record.'))
        obj = P._record(prov, int(rid))
        if obj is None:
            raise Http404
        f = S.record_folder(prov, obj, create=False)
        if f is not None and 'view' not in S.folder_rights(f, who):
            raise S.Refused(tr('You do not have access to this folder.'), 403)
        if f is None and not (who.dms_admin or who.has(*S.PROVIDERS[prov][5])):
            raise S.Refused(tr('You do not have access to this folder.'), 403)
        return Response({'record': S.PROVIDERS[prov][3](obj), **P.record_status(prov, obj)})


class FolderChecklist(APIView):
    """GET – the checklist status of the record a folder belongs to (empty when the folder is not a record's)"""
    permission_classes = [Member]

    @refused
    def get(self, request, pk):
        who = S.Who(request)
        f = _folder(pk, who)
        if 'view' not in S.folder_rights(f, who):
            raise S.Refused(tr('You do not have access to this folder.'), 403)
        key, rec = S.provider_of(f)
        if rec is None or key not in S.PROVIDERS:
            return Response({'checklists': []})
        obj = P._record(key, rec.record_id)
        if obj is None:
            return Response({'checklists': []})
        return Response({'provider': key, 'record_id': obj.pk, 'record': S.PROVIDERS[key][3](obj), **P.record_status(key, obj)})


class ChecklistGaps(APIView):
    permission_classes = [Member]

    @refused
    def get(self, request):
        who = S.Who(request)
        prov = request.query_params.get('provider') or 'employee'
        if prov not in S.PROVIDERS:
            raise S.Refused(tr('Choose the kind of record.'))
        if not (who.dms_admin or who.has(*S.PROVIDERS[prov][5])):
            raise S.Refused(tr('You may not see these records.'), 403)
        rows = P.gaps(who, prov, request.query_params.get('checklist'))
        return Response({'rows': rows, 'count': len(rows)})


# ================================================================== upload requests (inside) and the portal (outside)
def ur_out(r, request=None):
    docs = {d.pk: d for d in Mo.Document.objects.filter(pk__in=[x['document_id'] for x in r.received or []])}
    return {'id': r.pk, 'folder': r.folder_id, 'folder_path': S.path_of(r.folder), 'recipient_name': r.recipient_name, 'recipient_email': r.recipient_email,
            'message': r.message, 'items': r.items, 'state': P.request_state(r), 'expires_at': r.expires_at, 'created_at': r.created_at,
            'created_by': uname(r.created_by_id), 'checklist': r.checklist_id, 'link': P.request_link(r),
            'received': [{**x, 'status': docs[x['document_id']].status if x['document_id'] in docs else 'deleted',
                          'title': docs[x['document_id']].title if x['document_id'] in docs else ''} for x in r.received or []]}


class UploadRequests(APIView):
    permission_classes = [Member]

    @refused
    def get(self, request):
        who = S.Who(request)
        qs = Mo.UploadRequest.objects.select_related('folder')
        if request.query_params.get('folder'):
            qs = qs.filter(folder_id=request.query_params['folder'])
        rows = [r for r in qs[:500] if who.dms_admin or r.created_by_id == who.uid or 'upload' in S.folder_rights(r.folder, who)]
        waiting = Mo.Document.objects.filter(status='submitted', tags__contains=['portal'], deleted_at__isnull=True).select_related('folder', 'category', 'current', 'published')
        review = [doc_row(d) for d in waiting[:300] if 'upload' in S.doc_rights(d, who)]
        return Response({'requests': [ur_out(r, request) for r in rows], 'to_review': review})

    @refused
    def post(self, request):
        who = S.Who(request)
        d = _body(request)
        f = _folder(d.get('folder') or 0, who)
        cl, items = None, _json(d, 'items') or []
        if d.get('checklist') and not items:   # the missing items of the record's checklist
            cl = Mo.DocChecklist.objects.filter(pk=d.get('checklist')).first()
            key, rec = S.provider_of(f)
            obj = P._record(key, rec.record_id) if rec is not None and key in S.PROVIDERS else None
            if cl is not None and obj is not None:
                st = P.record_status(key, obj, [cl])
                items = [{'key': i['key'], 'label': i['label'], 'category_id': i['category_id']} for c in st['checklists'] for i in c['items']
                         if i['required'] and i['state'] in ('missing', 'expired', 'no_expiry')]
            if not items:
                raise S.Refused(tr('Nothing is missing from the checklist.'))
        r = P.make_request(who, f, items, d.get('recipient_name'), d.get('recipient_email'), d.get('message'), d.get('days') or 14, cl, d.get('max_files') or 20)
        return Response(ur_out(r, request), status=201)


class UploadRequestOne(APIView):
    permission_classes = [Member]

    @refused
    def post(self, request, pk):
        """{action: revoke}"""
        who = S.Who(request)
        r = Mo.UploadRequest.objects.filter(pk=pk).select_related('folder').first()
        if r is None:
            raise Http404
        if (request.data.get('action') or '') != 'revoke':
            raise S.Refused(tr('Unknown action.'))
        P.revoke_request(who, r)
        return Response(ur_out(r, request))


class ReviewUpload(APIView):
    permission_classes = [Member]

    @refused
    def post(self, request, pk):
        who = S.Who(request)
        d, _r = _doc(pk, who)
        accept = str(request.data.get('decision') or '') == 'accept'
        if not accept and str(request.data.get('decision') or '') != 'reject':
            raise S.Refused(tr('Accept or reject.'))
        P.review_upload(who, d, accept, request.data.get('reason') or '')
        d.refresh_from_db()
        return Response(doc_row(d, who))


def _page(title, body, status=200):
    return HttpResponse(f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>{escape(title)}</title><style>body{{font-family:system-ui,sans-serif;background:#f3f5f4;color:#1d2a26;margin:0}}
main{{max-width:620px;margin:40px auto;background:#fff;border:1px solid #d7dfdb;border-radius:10px;padding:26px}}h1{{font-size:20px;margin:0 0 6px}}
p,li{{color:#4b5a55;line-height:1.5}}label{{display:block;font-weight:600;font-size:13px;margin:12px 0 4px}}
input[type=text],select,textarea{{width:100%;box-sizing:border-box;height:38px;border:1px solid #c9d3cf;border-radius:6px;padding:0 10px;font:inherit}}
textarea{{height:80px;padding:8px}}button,a.b{{display:inline-block;background:#1f6f5c;color:#fff;border:0;padding:10px 18px;border-radius:6px;font:inherit;cursor:pointer;text-decoration:none}}
button.g{{background:#fff;color:#8a1c1c;border:1px solid #e3b5b5}}.ok{{background:#e6f5ec;color:#16794a;padding:10px 12px;border-radius:6px}}
.err{{background:#fdecea;color:#b42318;padding:10px 12px;border-radius:6px}}.got{{color:#16794a;font-weight:600}}canvas{{border:1px dashed #9fb1aa;border-radius:6px;touch-action:none;width:100%;height:150px}}
</style></head><body><main>{body}</main></body></html>''', status=status)


def _closed(st):
    return {'revoked': tr('This link was withdrawn by the sender.'), 'expired': tr('This link has expired.'),
            'completed': tr('Everything asked for has been received – thank you.')}.get(st, tr('This link is no longer open.'))


class Portal(APIView):
    """the external upload page: GET the form, POST one file (multipart: item, file)"""
    permission_classes = [AllowAny]
    authentication_classes = []
    parser_classes = [MultiPartParser, FormParser]

    def _req(self, token):
        r = Mo.UploadRequest.objects.filter(token=token).select_related('folder').first()
        if r is None:
            raise Http404
        return r

    def _form(self, request, r, msg='', bad=False):
        from django.db import connection
        st = P.request_state(r)
        head = f'<h1>{escape(tr("Send your documents"))}</h1><p>{escape(tr("For {0}", r.recipient_name))}</p>'
        if r.message:
            head += f'<p>{escape(r.message)}</p>'
        note = f'<p class="{"err" if bad else "ok"}">{escape(msg)}</p>' if msg else ''
        if st != 'open':
            return _page(tr('Send your documents'), head + note + f'<p>{escape(_closed(st))}</p>')
        got = {x['key'] for x in r.received or []}
        lis = ''.join(f'<li>{escape(i["label"])}' + (f' – <span class="got">{escape(tr("received"))}</span>' if i['key'] in got else '') + '</li>' for i in r.items)
        opts = ''.join(f'<option value="{escape(i["key"])}">{escape(i["label"])}</option>' for i in r.items if i['key'] not in got) or \
            ''.join(f'<option value="{escape(i["key"])}">{escape(i["label"])}</option>' for i in r.items)
        exp = timezone.localtime(r.expires_at).strftime('%d %b %Y')
        form = (f'<form method="post" enctype="multipart/form-data" action="?schema={escape(connection.schema_name)}">'
                f'<label for="item">{escape(tr("Document"))}</label><select id="item" name="item">{opts}</select>'
                f'<label for="file">{escape(tr("File (PDF, photo or Office file, up to {0} MB)", P.PORTAL_MAX_MB))}</label><input id="file" type="file" name="file" required>'
                f'<p><button type="submit">{escape(tr("Send"))}</button></p></form>')
        return _page(tr('Send your documents'), head + note + f'<ul>{lis}</ul>' + form + f'<p style="font-size:13px">{escape(tr("This link works until {0}.", exp))}</p>')

    def get(self, request, token):
        return self._form(request, self._req(token))

    def post(self, request, token):
        r = self._req(token)
        up = request.FILES.get('file')
        if up is None:
            return self._form(request, r, tr('Choose a file.'), True)
        try:
            P.portal_upload(r, request.data.get('item') or '', up, _ip(request))
        except S.Refused as e:
            r.refresh_from_db()
            return self._form(request, r, str(e), True)
        r.refresh_from_db()
        return self._form(request, r, tr('Received – thank you. You can send the next one.'))


# ================================================================== templates and forms
def tpl_out(t):
    return {'id': t.pk, 'name': t.name, 'provider': t.provider, 'category': t.category_id, 'category_name': t.category.name if t.category_id else '',
            'body': t.body, 'questions': t.questions, 'published_body': t.published_body, 'published_questions': t.published_questions,
            'version': t.version, 'state': t.state, 'changed': t.state == 'published' and (t.body != t.published_body or t.questions != t.published_questions),
            'published_at': t.published_at, 'published_by': uname(t.published_by_id),
            'history': [{'version': h['version'], 'published_at': h.get('published_at'), 'published_by': uname(h.get('published_by'))} for h in t.history or []]}


class Templates(APIView):
    permission_classes = [Member]

    @refused
    def get(self, request):
        who = S.Who(request)
        qs = Mo.DocTemplate.objects.select_related('category')
        if not who.dms_admin:
            qs = qs.filter(state='published')
        return Response({'templates': [tpl_out(t) for t in qs], 'providers': [{'key': k, 'label': tr(v)} for k, v in Mo.DocChecklist.PROVIDERS if S.provider_active(k)],
                         'fields': ['company', 'today', 'record.name', 'employee.name', 'employee.first_name', 'employee.code', 'employee.designation',
                                    'employee.department', 'employee.branch', 'employee.nationality', 'employee.joined', 'employee.email', 'answer.<key>']})

    @refused
    def post(self, request):
        who = S.Who(request)
        _admin(who)
        d = _body(request)
        d['questions'] = _json(d, 'questions')
        v = P.clean_template(d)
        t = Mo.DocTemplate.objects.create(created_by_id=who.uid, **v)
        return Response(tpl_out(t), status=201)


class TemplateOne(APIView):
    permission_classes = [Member]

    def _get(self, pk):
        t = Mo.DocTemplate.objects.select_related('category').filter(pk=pk).first()
        if t is None:
            raise Http404
        return t

    @refused
    def put(self, request, pk):
        who = S.Who(request)
        _admin(who)
        t = self._get(pk)
        d = _body(request)
        d['questions'] = _json(d, 'questions')
        for k, v in P.clean_template(d, t).items():
            setattr(t, k, v)
        t.save()
        return Response(tpl_out(t))

    @refused
    def post(self, request, pk):
        """{action: publish | retire | preview | generate, record, answers, title}"""
        who = S.Who(request)
        t = self._get(pk)
        act = request.data.get('action')
        if act == 'publish':
            return Response(tpl_out(P.publish_template(who, t)))
        if act == 'retire':
            _admin(who)
            t.state = 'retired'
            t.save(update_fields=['state', 'updated_at'])
            return Response(tpl_out(t))
        if act == 'preview':
            _admin(who)
            obj = P._record(t.provider, request.data.get('record')) if t.provider and str(request.data.get('record') or '').isdigit() else None
            return Response({'html': P.render(t.body, P.context(t.provider, obj, request.data.get('answers') or {}))})
        if act == 'generate':
            doc = P.generate(who, t, t.provider, request.data.get('record'), request.data.get('answers') or {}, request.data.get('title') or '')
            return Response(doc_row(doc, who), status=201)
        raise S.Refused(tr('Unknown action.'))


class TemplateRecords(APIView):
    """GET ?provider=&q= – records to make a document for (the user's branches)"""
    permission_classes = [Member]

    @refused
    def get(self, request):
        who = S.Who(request)
        prov = request.query_params.get('provider')
        if prov not in S.PROVIDERS:
            return Response([])
        Model = S._model(prov)
        qs = Model.objects.all() if Model else []
        if prov == 'employee':
            qs = qs.filter(is_active=True)
        q = (request.query_params.get('q') or '').strip().lower()
        out = []
        for o in qs[:3000]:
            if not who.branch_ok(P._branch_of(prov, o)):
                continue
            label = S.PROVIDERS[prov][3](o)
            if q and q not in label.lower():
                continue
            out.append({'id': o.pk, 'name': label})
            if len(out) >= 200:
                break
        return Response(out)


# ================================================================== e-signature
def sr_out(sr, request=None, who=None):
    return {'id': sr.pk, 'document': {'id': sr.document_id, 'number': sr.document.number, 'title': sr.document.title}, 'version': sr.version.label,
            'state': P.sign_state(sr), 'sequential': sr.sequential, 'message': sr.message, 'expires_at': sr.expires_at, 'created_at': sr.created_at,
            'created_by': uname(sr.created_by_id), 'completed_at': sr.completed_at, 'signed_version': sr.signed_version.label if sr.signed_version_id else '',
            'mine': bool(who and (who.dms_admin or sr.created_by_id == who.uid)),
            'signers': [{'id': s.pk, 'order': s.order, 'name': s.name, 'email': s.email, 'internal': bool(s.user_id), 'state': s.state,
                         'signed_at': s.signed_at, 'typed_name': s.typed_name, 'decline_reason': s.decline_reason,
                         'link': P.sign_link(s) if (who and (who.dms_admin or sr.created_by_id == who.uid) and not s.user_id) else ''}
                        for s in sr.signers.all()]}


class SignRequests(APIView):
    permission_classes = [Member]

    @refused
    def get(self, request):
        who = S.Who(request)
        qs = Mo.SignRequest.objects.select_related('document', 'version', 'signed_version').prefetch_related('signers')
        if request.query_params.get('document'):
            qs = qs.filter(document_id=request.query_params['document'])
        rows = [sr for sr in qs[:500] if who.dms_admin or sr.created_by_id == who.uid or any(s.user_id == who.uid for s in sr.signers.all())
                or 'view' in S.doc_rights(sr.document, who)]
        mine = [s for s in Mo.Signer.objects.filter(user_id=who.uid, state='pending', request__state='out').select_related('request__document')]
        return Response({'requests': [sr_out(sr, request, who) for sr in rows],
                         'to_sign': [{'signer': s.pk, 'request': s.request_id, 'document': {'id': s.request.document_id, 'number': s.request.document.number,
                                                                                            'title': s.request.document.title}, 'message': s.request.message}
                                     for s in mine if P.sign_state(s.request) == 'out']})

    @refused
    def post(self, request):
        who = S.Who(request)
        d = _body(request)
        doc, _r = _doc(d.get('document') or 0, who)
        sr = P.sign_request(who, doc, _json(d, 'signers') or [], str(d.get('sequential', True)).lower() not in ('false', '0'), d.get('message'), d.get('days') or 14)
        return Response(sr_out(sr, request, who), status=201)


class SignRequestOne(APIView):
    permission_classes = [Member]

    @refused
    def post(self, request, pk):
        """{action: cancel}"""
        who = S.Who(request)
        sr = Mo.SignRequest.objects.filter(pk=pk).select_related('document', 'version').first()
        if sr is None:
            raise Http404
        if request.data.get('action') != 'cancel':
            raise S.Refused(tr('Unknown action.'))
        P.cancel(who, sr)
        sr.refresh_from_db()
        return Response(sr_out(sr, request, who))


class SignInternal(APIView):
    """a ZEO user signs (or declines) in the app: {decision: sign|decline, typed_name, image, reason}"""
    permission_classes = [Member]

    @refused
    def post(self, request, pk):
        who = S.Who(request)
        s = Mo.Signer.objects.filter(pk=pk).select_related('request__document').first()
        if s is None:
            raise Http404
        if request.data.get('decision') == 'decline':
            P.decline(s, request.data.get('reason'), _ip(request), who.uid)
        else:
            P.sign(s, request.data.get('typed_name'), request.data.get('image') or '', _ip(request), request.META.get('HTTP_USER_AGENT', ''), who.uid)
        sr = s.request
        sr.refresh_from_db()
        return Response(sr_out(sr, request, who))


class SignerFile(APIView):
    """the file to sign, for a ZEO signer"""
    permission_classes = [Member]

    def get(self, request, pk):
        who = S.Who(request)
        s = Mo.Signer.objects.filter(pk=pk, user_id=who.uid).select_related('request__version').first()
        if s is None:
            raise Http404
        v = s.request.version
        return FileResponse(v.file.open('rb'), as_attachment=False, filename=v.file_name, content_type=v.mime or 'application/octet-stream')


class Verify(APIView):
    permission_classes = [Member]
    parser_classes = [MultiPartParser, FormParser]

    @refused
    def post(self, request):
        up = request.FILES.get('file')
        if up is None:
            raise S.Refused(tr('Choose a file.'), problems={'file': tr('Required.')})
        out = P.verify(up)
        who = S.Who(request)
        if out.get('found'):
            d = Mo.Document.objects.get(pk=out['document']['id'])
            if 'view' not in S.doc_rights(d, who):
                out = {'sha256': out['sha256'], 'found': True, 'document': None, 'note': tr('The file is in the Document Center, in a folder you cannot open.')}
        return Response(out)


class PublicSign(APIView):
    """the signing page for a signer outside the company: GET the page, GET ?file=1 the file, POST sign / decline"""
    permission_classes = [AllowAny]
    authentication_classes = []
    parser_classes = [FormParser, MultiPartParser, JSONParser]

    def _s(self, token):
        s = Mo.Signer.objects.filter(token=token).select_related('request__document', 'request__version').first()
        if s is None or s.user_id:
            raise Http404
        return s

    def _page(self, request, s, msg='', bad=False):
        from django.db import connection
        sr = s.request
        d = sr.document
        q = f'?schema={escape(connection.schema_name)}'
        head = f'<h1>{escape(tr("Sign {0}", d.title))}</h1><p>{escape(d.number)} · {escape(tr("Version"))} {escape(sr.version.label)} · {escape(tr("For {0}", s.name))}</p>'
        if sr.message:
            head += f'<p>{escape(sr.message)}</p>'
        note = f'<p class="{"err" if bad else "ok"}">{escape(msg)}</p>' if msg else ''
        st = P.sign_state(sr)
        if s.state == 'signed':
            return _page(tr('Signed'), head + note + f'<p class="ok">{escape(tr("You signed on {0}. Thank you.", timezone.localtime(s.signed_at).strftime("%d %b %Y %H:%M")))}</p>')
        if st != 'out' or s.state == 'declined':
            return _page(tr('Closed'), head + note + f'<p>{escape(tr("This signature request is closed."))}</p>')
        if s.state == 'waiting':
            return _page(tr('Not yet'), head + f'<p>{escape(tr("Someone signs before you – this link opens for you when it is your turn."))}</p>')
        form = (f'<p><a class="b" href="{q}&amp;file=1" target="_blank" rel="noopener">{escape(tr("Open the document"))}</a></p>'
                f'<form method="post" action="{q}" onsubmit="document.getElementById(\'img\').value=window.zsig&&window.zsig()||\'\'">'
                f'<label for="tn">{escape(tr("Type your full name"))}</label><input id="tn" type="text" name="typed_name" required maxlength="200">'
                f'<label>{escape(tr("Draw your signature (optional)"))}</label><canvas id="c" width="560" height="150"></canvas>'
                f'<input type="hidden" name="image" id="img"><input type="hidden" name="decision" value="sign">'
                f'<p><label style="font-weight:400"><input type="checkbox" required> {escape(tr("I have read the document and I sign it electronically."))}</label></p>'
                f'<p><button type="submit">{escape(tr("Sign"))}</button></p></form>'
                f'<form method="post" action="{q}"><input type="hidden" name="decision" value="decline"><label for="rs">{escape(tr("Or decline – the reason"))}</label>'
                f'<textarea id="rs" name="reason" required maxlength="500"></textarea><p><button class="g" type="submit">{escape(tr("Decline"))}</button></p></form>'
                '<script>(function(){var c=document.getElementById("c"),x=c.getContext("2d"),d=false,used=false;x.lineWidth=2.2;x.lineCap="round";'
                'function p(e){var r=c.getBoundingClientRect(),t=e.touches?e.touches[0]:e;return[(t.clientX-r.left)*c.width/r.width,(t.clientY-r.top)*c.height/r.height]}'
                'function s(e){d=true;used=true;var q=p(e);x.beginPath();x.moveTo(q[0],q[1]);e.preventDefault()}'
                'function m(e){if(!d)return;var q=p(e);x.lineTo(q[0],q[1]);x.stroke();e.preventDefault()}function u(){d=false}'
                'c.addEventListener("mousedown",s);c.addEventListener("mousemove",m);window.addEventListener("mouseup",u);'
                'c.addEventListener("touchstart",s);c.addEventListener("touchmove",m);c.addEventListener("touchend",u);'
                'window.zsig=function(){return used?c.toDataURL("image/png"):""}})();</script>')
        exp = timezone.localtime(sr.expires_at).strftime('%d %b %Y')
        return _page(tr('Sign'), head + note + form + f'<p style="font-size:13px">{escape(tr("This link works until {0}.", exp))}</p>')

    def get(self, request, token):
        s = self._s(token)
        if request.GET.get('file') == '1':
            if P.sign_state(s.request) != 'out' and s.state != 'signed':
                raise Http404
            v = s.request.version
            Mo.Activity.objects.create(document=s.request.document, action='sign_file_opened', detail=s.name[:500], ip=_ip(request))
            resp = FileResponse(v.file.open('rb'), as_attachment=False, filename=v.file_name, content_type=v.mime or 'application/octet-stream')
            resp['X-Content-Type-Options'] = 'nosniff'
            resp['Cache-Control'] = 'no-store'
            return resp
        return self._page(request, s)

    def post(self, request, token):
        s = self._s(token)
        try:
            if request.data.get('decision') == 'decline':
                P.decline(s, request.data.get('reason'), _ip(request))
                s.refresh_from_db()
                return self._page(request, s, tr('You declined. The sender is told.'))
            P.sign(s, request.data.get('typed_name'), request.data.get('image') or '', _ip(request), request.META.get('HTTP_USER_AGENT', ''))
        except S.Refused as e:
            s.refresh_from_db()
            msg = str(e) + (' ' + ' '.join(str(v) for v in e.problems.values()) if e.problems else '')
            return self._page(request, s, msg, True)
        s.refresh_from_db()
        return self._page(request, s)


# ================================================================== v1.73.10 DMS phase 5: OCR and search in words
class OcrView(APIView):
    """GET – what was read and what waits; POST {limit} – read the waiting scans now (administrators); POST {version} – read one again"""
    permission_classes = [Member]

    @refused
    def get(self, request):
        from . import phase5 as P5
        who = S.Who(request)
        _admin(who)
        return Response(P5.status())

    @refused
    def post(self, request):
        from . import phase5 as P5
        who = S.Who(request)
        vid = request.data.get('version')
        if vid:
            v = Mo.Version.objects.filter(pk=vid).select_related('document').first()
            if v is None:
                raise Http404
            d, r = _doc(v.document_id, who)
            if not ({'edit', 'upload', 'admin'} & set(r)):
                raise S.Refused(tr('You may not change this document.'), 403)
            if not P5.ocr_available():
                raise S.Refused(tr('The server cannot read scans: Tesseract is not installed (apt install tesseract-ocr tesseract-ocr-ara).'))
            src = P5.ocr_version(v, force=True)
            S.log_act(who, 'ocr', f'{v.label}: {src}', document=d)
            v.refresh_from_db()
            return Response({'text_source': src, 'chars': len(v.text or ''), 'preview': (v.text or '')[:400]})
        _admin(who)
        if not P5.ocr_available():
            raise S.Refused(tr('The server cannot read scans: Tesseract is not installed (apt install tesseract-ocr tesseract-ocr-ara).'))
        try:
            limit = max(1, min(int(request.data.get('limit') or 50), 500))
        except (TypeError, ValueError):
            limit = 50
        done = P5.run_pending(limit)
        return Response({'done': done, **P5.status()})


class SmartSearch(APIView):
    """GET ?q=<words>&ai=1 – documents found from a question in plain words (+ the AI's answer when switched on)"""
    permission_classes = [Member]

    @refused
    def get(self, request):
        from . import phase5 as P5
        who = S.Who(request)
        q = ' '.join(str(request.query_params.get('q') or '').split())[:300]
        if len(q) < 2:
            raise S.Refused(tr('Type what you are looking for.'), problems={'q': tr('Required.')})
        out = P5.smart_search(who, q)
        out['ai_ready'] = P5.ai_ready()
        if str(request.query_params.get('ai') or '') in ('1', 'true'):
            out['answer'] = P5.ai_answer(request, who, q, out['results'])
        S.log_act(who, 'smart_search', q[:200])
        return Response(out)


class IndustryPacks(APIView):
    """v1.73.11 DMS phase 6 – GET: the industry packs and how much of each the company has; POST {pack}: install (adds only what is missing)"""
    permission_classes = [Member]

    @refused
    def get(self, request):
        from . import packs as PK
        who = S.Who(request)
        out = PK.status()
        for p in out:
            p['label'], p['description'] = tr(p['label']), tr(p['description'])
            for k in ('categories', 'checklists', 'templates'):
                p[k] = [tr(x) for x in p[k]]
        return Response({'packs': out, 'may_install': who.dms_admin})

    @refused
    def post(self, request):
        from . import packs as PK
        who = S.Who(request)
        added = PK.install(who, str(request.data.get('pack') or ''))
        n = sum(len(v) for v in added.values())
        return Response({'added': added, 'message': tr('Added {0} (categories {1}, checklists {2}, templates {3}).', n, len(added['categories']),
                                                       len(added['checklists']), len(added['templates'])) if n else tr('Nothing to add: the company has all of this pack.')})
