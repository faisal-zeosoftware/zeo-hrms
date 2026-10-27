"""v1.62.0 print – API /print-templates/api/…

Settings › Print templates (company administrators and the right manage_printtemplate):
  GET  meta/                                document types, looks, fonts, branches, rights
  GET  templates/?doc_type=                 samples and the company's copies of a type (with the defaults)
  POST templates/ {copy_from, name}         copy a template
  GET/PATCH/DELETE templates/<id>/          read / save (a new version) / delete an own copy
  GET  templates/<id>/thumb/                PNG of the first page (made-up data, the company's branding)
  GET  templates/<id>/versions/             versions;  POST templates/<id>/restore/ {version}
  POST templates/<id>/reset/                a sample back to ZEO's design
  POST preview/ {template_id | unsaved template, record_id}   PNG pages (never an error status for a template mistake)
  GET  records/?doc_type=&q=                records to preview with
  GET/POST defaults/ ; DELETE defaults/<id>/
  GET/PATCH branding/ ; POST/DELETE branding/file/?kind=logo|stamp|signature
Everyone who may print the document:
  GET  print/<doc_type>/<pk>/               the PDF with the company's default design (ESS: own documents only)
"""
import logging

from django.http import Http404, HttpResponse
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import contexts as C
from . import designs as D
from . import services as S
from .engine import FONT_CHOICES, PrintError, available
from .models import PrintBranding, PrintDefault, PrintTemplate

log = logging.getLogger(__name__)
MANAGE = 'manage_printtemplate'


def _tr(msg):
    try:
        from Translations.i18n import tr
        return tr(msg)
    except Exception:   # noqa: BLE001
        return msg


def bad(msg, code=400, **extra):
    return Response({'detail': _tr(msg), **extra}, status=code)


class Base(APIView):
    permission_classes = [IsAuthenticated]
    zeo_access = False   # rights are checked here
    zeo_scope = False

    def initial(self, request, *a, **k):
        super().initial(request, *a, **k)
        from AccessControl.access import ctx
        self.c = ctx(request)
        S.ensure_seeded()

    @property
    def manager(self):
        return bool(self.c.admin or MANAGE in self.c.codes)

    def need_manage(self):
        if not self.manager:
            self.permission_denied(self.request, message=_tr('Only company administrators and users with the right "Design the printed documents" change print templates.'))

    def uid(self):
        return getattr(self.request.user, 'pk', None)


def tpl_json(t, defaults=None, full=False):
    d = {'id': t.pk, 'doc_type': t.doc_type, 'name': t.name, 'description': t.description, 'is_sample': t.is_sample, 'sample_key': t.sample_key,
         'language': t.language, 'mode': t.mode, 'version': t.version, 'updated_at': t.updated_at, 'copied_from': t.copied_from_id,
         'look': (t.settings or {}).get('look', t.sample_key), 'defaults': [
             {'id': r.pk, 'branch_id': r.branch_id, 'language': r.language} for r in (defaults or []) if r.template_id == t.pk]}
    if full:
        html, css = S.sources(t)
        d.update(settings=t.settings, html=t.html if t.mode == 'html' else html, css=t.css if t.mode == 'html' else S.editable_css(t),
                 blocks=D.block_catalogue(t.doc_type), placeholders=S.placeholders(t.doc_type, 'en' if t.language == 'bi' else t.language))
    return d


class MetaView(Base):
    def get(self, request):
        from django.apps import apps
        counts = {}
        for t in PrintTemplate.objects.values('doc_type', 'is_sample'):
            k = counts.setdefault(t['doc_type'], {'samples': 0, 'own': 0})
            k['samples' if t['is_sample'] else 'own'] += 1
        defs = {}
        for r in PrintDefault.objects.select_related('template'):
            if r.branch_id is None and r.language == '':
                defs[r.doc_type] = {'id': r.template_id, 'name': r.template.name}
        Br = apps.get_model('OrganisationManager', 'brnch_mstr')
        return Response({
            'doc_types': [{'key': k, 'label': en, 'label_ar': ar, 'family': fam, 'icon': icon, **counts.get(k, {'samples': 0, 'own': 0}),
                           'default': defs.get(k)} for k, en, ar, fam, perms, icon in C.DOC_TYPES],
            'looks': [{'key': k, 'name': v['name'], 'description': v['description']} for k, v in D.LOOKS.items()],
            'fonts': FONT_CHOICES, 'headers': [{'key': 'letterhead', 'label': 'Letterhead'}, {'key': 'band', 'label': 'Colour band'},
                                               {'key': 'line', 'label': 'Compact line'}, {'key': 'bilingual', 'label': 'Bilingual (English | Arabic)'}],
            'branches': [{'id': b.pk, 'name': b.branch_name} for b in Br.objects.order_by('branch_name')],
            'can_manage': self.manager, 'engine': available(),
        })


class TemplatesView(Base):
    def get(self, request):
        self.need_manage()
        dt = request.query_params.get('doc_type') or ''
        qs = PrintTemplate.objects.all()
        if dt:
            qs = qs.filter(doc_type=dt)
        defaults = list(PrintDefault.objects.filter(doc_type=dt) if dt else PrintDefault.objects.all())
        return Response([tpl_json(t, defaults) for t in qs.order_by('-is_sample', 'id')])

    def post(self, request):
        self.need_manage()
        src = PrintTemplate.objects.filter(pk=request.data.get('copy_from')).first()
        if src is None:
            return bad('Choose the template to copy.')
        t = S.copy(src, self.uid(), (request.data.get('name') or '').strip() or None)
        return Response(tpl_json(t, full=True), status=201)


class TemplateView(Base):
    def obj(self, pk):
        self.need_manage()
        t = PrintTemplate.objects.filter(pk=pk).first()
        if t is None:
            raise Http404
        return t

    def get(self, request, pk):
        t = self.obj(pk)
        return Response(tpl_json(t, list(PrintDefault.objects.filter(template=t)), full=True))

    def patch(self, request, pk):
        t = self.obj(pk)
        if t.is_sample:
            return bad('The samples of ZEO are read only – make a copy and edit the copy.', 409)
        try:
            S.save(t, request.data, self.uid())
        except PrintError as e:
            return bad(e.message, line=e.line)
        return Response(tpl_json(t, list(PrintDefault.objects.filter(template=t)), full=True))

    put = patch

    def delete(self, request, pk):
        t = self.obj(pk)
        if t.is_sample:
            return bad('The samples of ZEO cannot be deleted.', 409)
        t.delete()
        return Response(status=204)


class ThumbView(Base):
    def get(self, request, pk):
        self.need_manage()
        t = PrintTemplate.objects.filter(pk=pk).first()
        if t is None:
            raise Http404
        try:
            png = S.thumb(t, int(request.query_params.get('w') or 420))
        except Exception:   # noqa: BLE001
            log.exception('thumbnail')
            png = S._error_png('The thumbnail could not be made.', 420)
        r = HttpResponse(png, content_type='image/png')
        r['Cache-Control'] = 'private, max-age=300'
        return r


class VersionsView(Base):
    def get(self, request, pk):
        self.need_manage()
        t = PrintTemplate.objects.filter(pk=pk).first()
        if t is None:
            raise Http404
        from django.contrib.auth import get_user_model
        U = get_user_model()
        names = {u.pk: (u.get_full_name() or u.username) for u in U.objects.filter(pk__in=[v.saved_by_id for v in t.versions.all() if v.saved_by_id])}
        return Response([{'version': v.version, 'name': v.name, 'mode': v.mode, 'note': v.note, 'saved_at': v.saved_at,
                          'saved_by': names.get(v.saved_by_id, ''), 'current': v.version == t.version} for v in t.versions.all()])


class RestoreView(Base):
    def post(self, request, pk):
        self.need_manage()
        t = PrintTemplate.objects.filter(pk=pk).first()
        if t is None:
            raise Http404
        try:
            S.restore(t, int(request.data.get('version') or 0), self.uid())
        except (PrintError, ValueError) as e:
            return bad(getattr(e, 'message', str(e)))
        return Response(tpl_json(t, full=True))


class ResetView(Base):
    def post(self, request, pk):
        self.need_manage()
        t = PrintTemplate.objects.filter(pk=pk).first()
        if t is None:
            raise Http404
        try:
            S.reset_sample(t)
        except PrintError as e:
            return bad(e.message)
        return Response(tpl_json(t, full=True))


def _record(doc_type, c, rid):
    qs = C.queryset(doc_type, c)
    return qs.filter(pk=rid).first() if qs is not None and rid else None


class PreviewView(Base):
    def post(self, request):
        self.need_manage()
        d = request.data
        if d.get('template_id'):
            t = PrintTemplate.objects.filter(pk=d['template_id']).first()
            if t is None:
                raise Http404
            src = t if not any(k in d for k in ('settings', 'html', 'css', 'mode', 'language')) else {
                'doc_type': t.doc_type, 'mode': d.get('mode', t.mode), 'settings': d.get('settings', t.settings), 'html': d.get('html', t.html),
                'css': d.get('css', t.css), 'language': d.get('language', t.language)}
            doc_type = t.doc_type
        else:
            doc_type = d.get('doc_type')
            if doc_type not in C.BY_KEY:
                return bad('Choose a document type.')
            src = {'doc_type': doc_type, 'mode': d.get('mode', 'blocks'), 'settings': d.get('settings') or {}, 'html': d.get('html', ''),
                   'css': d.get('css', ''), 'language': d.get('language', 'en')}
        obj = None
        if d.get('record_id'):
            obj = _record(doc_type, self.c, d.get('record_id'))
            if obj is None:
                return bad('That record does not exist or you may not see it.', 404)
        try:
            width = max(300, min(1400, int(d.get('width') or 900)))
        except (TypeError, ValueError):
            width = 900
        if d.get('format') == 'pdf':
            try:
                data = S.pdf(src, S.context_for(doc_type, src, obj, request))
            except PrintError as e:
                return Response({'ok': False, 'error': e.message, 'line': e.line})
            r = HttpResponse(data, content_type='application/pdf')
            r['Content-Disposition'] = 'inline; filename="preview.pdf"'
            return r
        return Response(S.preview(src, obj, request, width))


class RecordsView(Base):
    def get(self, request):
        self.need_manage()
        dt = request.query_params.get('doc_type')
        if dt not in C.BY_KEY:
            return bad('Choose a document type.')
        return Response(C.records(dt, self.c, (request.query_params.get('q') or '').strip()))


class DefaultsView(Base):
    def get(self, request):
        self.need_manage()
        return Response([{'id': r.pk, 'doc_type': r.doc_type, 'branch_id': r.branch_id, 'language': r.language, 'template_id': r.template_id,
                          'template': r.template.name} for r in PrintDefault.objects.select_related('template').order_by('doc_type', 'branch_id')])

    def post(self, request):
        self.need_manage()
        t = PrintTemplate.objects.filter(pk=request.data.get('template_id')).first()
        if t is None:
            return bad('Choose a template.')
        bid = request.data.get('branch_id') or None
        try:
            r = S.set_default(request.data.get('doc_type') or t.doc_type, t, int(bid) if bid else None, request.data.get('language') or '', self.uid())
        except (PrintError, ValueError) as e:
            return bad(getattr(e, 'message', str(e)))
        return Response({'id': r.pk, 'doc_type': r.doc_type, 'branch_id': r.branch_id, 'language': r.language, 'template_id': r.template_id}, status=201)


class DefaultView(Base):
    def delete(self, request, pk):
        self.need_manage()
        PrintDefault.objects.filter(pk=pk).delete()
        return Response(status=204)


BRAND_FIELDS = ('logo_source', 'logo_position', 'primary_color', 'accent_color', 'text_color', 'font', 'font_arabic', 'header_text', 'footer_text',
                'address', 'trn', 'show_stamp', 'show_signature', 'show_watermark', 'watermark_text')


def brand_json(b):
    out = {f: getattr(b, f) for f in BRAND_FIELDS}
    for f in ('logo', 'stamp', 'signature'):
        out[f] = bool(getattr(b, f) and getattr(b, f).name)
    out['updated_at'] = b.updated_at
    return out


class BrandingView(Base):
    def get(self, request):
        self.need_manage()
        return Response(brand_json(PrintBranding.get()))

    def patch(self, request):
        self.need_manage()
        import re
        b = PrintBranding.get()
        for f in BRAND_FIELDS:
            if f not in request.data:
                continue
            v = request.data[f]
            if f.endswith('_color') and not re.fullmatch(r'#[0-9a-fA-F]{6}', str(v or '')):
                return bad('Colours are written #RRGGBB.')
            if f in ('font', 'font_arabic') and v not in {x['key'] for x in FONT_CHOICES}:
                return bad('Choose one of the fonts of the list.')
            if f == 'logo_position' and v not in ('left', 'center', 'right'):
                return bad('Logo position: left, center or right.')
            if f == 'logo_source' and v not in ('auto', 'company', 'own'):
                return bad('Logo: auto, company or own.')
            if f.startswith('show_'):
                v = bool(v)
            setattr(b, f, v)
        b.updated_by_id = self.uid()
        b.save()
        return Response(brand_json(b))

    put = patch


class BrandingFileView(Base):
    KINDS = ('logo', 'stamp', 'signature')

    def post(self, request):
        """multipart: kind=logo|stamp|signature, file=<image>. Stored as this company's file (print_templates/<schema>/…)."""
        self.need_manage()
        kind = request.data.get('kind')
        f = request.FILES.get('file')
        if kind not in self.KINDS or f is None:
            return bad('Send kind (logo, stamp or signature) and the image file.')
        if f.size > 2 * 1024 * 1024:
            return bad('The image may be 2 MB at most.')
        try:
            from PIL import Image
            img = Image.open(f)
            img.verify()
            fmt = (img.format or '').lower()
        except Exception:   # noqa: BLE001
            return bad('Upload a PNG or JPEG image.')
        if fmt not in ('png', 'jpeg'):
            return bad('Upload a PNG or JPEG image.')
        f.seek(0)
        from django.db import connection
        b = PrintBranding.get()
        old = getattr(b, kind)
        if old and old.name:
            old.delete(save=False)
        getattr(b, kind).save(f'{connection.schema_name}/{kind}.{"jpg" if fmt == "jpeg" else "png"}', f, save=False)
        if kind == 'logo':
            b.logo_source = 'own'
        b.updated_by_id = self.uid()
        b.save()
        return Response(brand_json(b))

    def delete(self, request):
        self.need_manage()
        kind = request.query_params.get('kind')
        if kind not in self.KINDS:
            return bad('kind: logo, stamp or signature.')
        b = PrintBranding.get()
        f = getattr(b, kind)
        if f and f.name:
            f.delete(save=False)
        if kind == 'logo' and b.logo_source == 'own':
            b.logo_source = 'auto'
        b.save()
        return Response(brand_json(b))


class BrandingImageView(Base):
    """GET branding/file/<kind>/ – the uploaded image (for the settings screen)."""

    def get(self, request, kind):
        self.need_manage()
        if kind not in BrandingFileView.KINDS:
            raise Http404
        f = getattr(PrintBranding.get(), kind)
        if not f or not f.name:
            raise Http404
        import mimetypes
        return HttpResponse(f.open('rb').read(), content_type=mimetypes.guess_type(f.name)[0] or 'image/png')


class PrintView(Base):
    """GET print/<doc_type>/<pk>/?template=<id>&lang=en|ar – the document as a PDF with the company's design."""

    def get(self, request, doc_type, pk):
        if doc_type not in C.BY_KEY:
            raise Http404
        obj = _record(doc_type, self.c, pk)
        if obj is None:
            return bad('The document does not exist or you may not print it.', 404)
        t = None
        if request.query_params.get('template'):
            if not self.manager:
                return bad('Only the people who design the documents can print with another template.', 403)
            t = PrintTemplate.objects.filter(pk=request.query_params['template'], doc_type=doc_type).first()
        lang = request.query_params.get('lang') or None
        if t is None:
            t = S.default_for(doc_type, C.branch_of(doc_type, obj), lang) or S.default_for(doc_type, C.branch_of(doc_type, obj))
        if t is None:   # no default chosen yet: the Classic sample of the language
            t = PrintTemplate.objects.filter(doc_type=doc_type, is_sample=True, sample_key='arabic' if lang == 'ar' else 'classic').first() \
                or PrintTemplate.objects.filter(doc_type=doc_type, is_sample=True, sample_key='classic').first()
        if not available():
            return bad('The PDF engine (WeasyPrint) is not installed on the server.', 503)
        try:
            data = S.pdf(t, S.context_for(doc_type, t, obj, request))
        except PrintError as e:
            return bad(e.message, 422, line=e.line)
        r = HttpResponse(data, content_type='application/pdf')
        name = f'{C.BY_KEY[doc_type][1]} {pk}'.replace('/', '-')
        r['Content-Disposition'] = f'{"attachment" if request.query_params.get("download") else "inline"}; filename="{name}.pdf"'
        return r
