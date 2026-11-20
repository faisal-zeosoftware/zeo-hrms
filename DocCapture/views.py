import json
import logging
import mimetypes

from django.core.files.base import ContentFile
from django.db.models import Q
from django.http import HttpResponse
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import create as C
from . import matcher as M
from . import reader as R
from .models import KINDS, CaptureJob

log = logging.getLogger(__name__)
MAX_FILE = 15 * 1024 * 1024
EXT = ('.pdf', '.png', '.jpg', '.jpeg', '.webp', '.xlsx', '.xlsm', '.xls', '.csv', '.tsv', '.docx', '.eml', '.txt')
TARGET_LABELS = {'quote': 'Sales quotation', 'order': 'Sales order (confirmed)', 'po': 'Purchase order (draft)', 'rfq_quote': 'Supplier quote on an RFQ',
                 'request': 'Purchase request', 'rfq': 'Request for quotation (RFQ)', 'price_list': "The supplier's price list (Purchase › Supplier prices)"}


def err(msg, code=400, field=None):
    return Response({'detail': msg, **({field: [msg]} if field else {})}, status=code)


def job_json(j):
    return {'id': j.id, 'kind': j.kind, 'kind_label': j.get_kind_display(), 'source_name': j.source_name, 'has_file': bool(j.file),
            'is_image': (j.source_name or '').lower().endswith(('.png', '.jpg', '.jpeg', '.webp')), 'is_pdf': (j.source_name or '').lower().endswith('.pdf'),
            'text': j.text[:20000], 'reader': j.reader, 'proposal': j.proposal, 'status': j.status, 'target_number': j.target_number,
            'target_link': j.target_link, 'created_at': j.created_at, 'note': (j.extracted or {}).get('_note', '')}


def mine(request):
    qs = CaptureJob.objects.all()
    return qs if request.user.is_superuser else qs.filter(created_by=request.user)


class MetaView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from .pricelist import feature_on
        out = {'kinds': [], 'ai': R.ai_ready(request), 'accept': ','.join(EXT), 'can_update_prices': C.may_update_prices(request), 'supplier_prices_on': feature_on()}
        for k, label in KINDS:
            targets = []
            for t in C.TARGETS[k]:
                app, ok = C.rights(request, k, t)
                if ok:
                    targets.append({'value': t, 'label': TARGET_LABELS[t]})
            out['kinds'].append({'value': k, 'label': label, 'targets': targets, 'allowed': bool(targets)})
        try:
            from ZeoPurchase.models import Rfq
            out['rfqs'] = [{'id': r.pk, 'label': f'{r.number} – {r.date:%d/%m/%Y} ({len(r.lines or [])} items)'}
                           for r in Rfq.objects.exclude(status__in=('awarded', 'cancelled', 'closed')).order_by('-date')[:50]]
        except Exception:   # noqa: BLE001
            out['rfqs'] = []
        out['recent'] = [{'id': j.id, 'kind': j.get_kind_display(), 'source': j.source_name, 'status': j.status, 'number': j.target_number,
                          'link': j.target_link, 'at': j.created_at} for j in mine(request)[:12]]
        return Response(out)


class ReadView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request):
        kind = request.data.get('kind') or ''
        if kind not in dict(KINDS):
            return err('Choose what the document is.', field='kind')
        if not any(C.rights(request, kind, t)[1] for t in C.TARGETS[kind]):
            return err('You may not create these documents.', 403)
        f = request.FILES.get('file')
        text = (request.data.get('text') or '').strip()
        if not f and not text:
            return err('Upload the document or paste its text.', field='file')
        data, name = None, 'pasted text'
        if f:
            name = f.name[:200]
            if not name.lower().endswith(EXT):
                return err('Upload a PDF, a photo (JPG, PNG), Excel, CSV, Word, an e-mail (.eml) or a text file.', field='file')
            if f.size > MAX_FILE:
                return err('The file is larger than 15 MB.', field='file')
            data = f.read()
        try:
            txt, ex, how, note = R.read(request, kind, name, data, text)
        except Exception as e:   # noqa: BLE001
            log.exception('doc capture read failed')
            return err(f'The document could not be read: {str(e)[:200]}')
        prop = M.propose(kind, ex)
        if how.startswith('ai'):
            M.ai_pick(request, kind, prop)
            M.mark_images(prop['lines'])
        j = CaptureJob(kind=kind, source_name=name, text=txt or text, reader=how, extracted={**ex, '_note': note}, proposal=prop, created_by=request.user)
        if data is not None:
            j.file.save(name, ContentFile(data), save=False)
        j.save()
        return Response(job_json(j))


class JobView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        j = mine(request).filter(pk=pk).first()
        return Response(job_json(j)) if j else err('Not found.', 404)


class FileView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        j = mine(request).filter(pk=pk).first()
        if j is None or not j.file:
            return err('Not found.', 404)
        ct = mimetypes.guess_type(j.source_name)[0] or 'application/octet-stream'
        r = HttpResponse(j.file.open('rb').read(), content_type=ct)
        r['Content-Disposition'] = f'inline; filename="{j.source_name}"'
        return r


class RematchView(APIView):
    """the user changed the customer / supplier: match the items and prices again for that partner"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        j = mine(request).filter(pk=pk).first()
        if j is None:
            return err('Not found.', 404)
        pid = request.data.get('partner_id')
        prop = j.proposal or {}
        for ln in prop.get('lines') or []:
            if not ln.get('changed'):
                ln['item'] = M.match_item(ln, int(pid) if pid else None, j.kind)
            ln['notes'] = M.check_line(j.kind, ln, int(pid) if pid else None)
        M.mark_images(prop.get('lines') or [])
        return Response({'lines': prop.get('lines') or []})


class CreateView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]

    def post(self, request, pk):
        j = mine(request).filter(pk=pk).first()
        if j is None:
            return err('Not found.', 404)
        if j.status == 'created':
            return err(f'Already created as {j.target_number}.', 409)
        payload = request.data or {}
        try:
            res = C.create(request, j, payload)
        except C.CaptureError as e:
            return err(e.message, field=e.field)
        except Exception as e:   # noqa: BLE001 – the apps' own errors (SalesError, PurchaseError, AppError …)
            msg = getattr(e, 'message', None) or str(e)
            log.warning('doc capture create failed: %s', msg)
            return err(str(msg)[:300])
        pid = payload.get('partner_id')
        if not pid and res.get('target', '').startswith(('sales', 'purchase')):
            pid = None
        learnt = M.learn(request.user, j.kind, (j.proposal or {}).get('partner_text'), int(pid) if pid else None, payload.get('lines') or [])
        j.status, j.target, j.target_id, j.target_number, j.target_link = 'created', res['target'], res['id'], res['number'] or '', res['link']
        j.save(update_fields=['status', 'target', 'target_id', 'target_number', 'target_link'])
        res['learnt'] = learnt
        return Response(res)


class SearchView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, what):
        q = (request.query_params.get('q') or '').strip()
        kind = request.query_params.get('kind') or 'sales_quote'
        if what == 'partners':
            from ZeoMasters.models import Partner
            qs = Partner.objects.exclude(status='archived')
            if q:
                qs = qs.filter(Q(name__icontains=q) | Q(number__icontains=q) | Q(email__icontains=q) | Q(tax_id__icontains=q))
            return Response([M._pinfo(p) for p in qs.order_by('name')[:20]])
        from ZeoMasters.models import Product
        qs = Product.objects.filter(is_active=True)
        qs = qs.filter(is_sold=True) if kind in M.SALES else qs.filter(is_purchased=True)
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q) | Q(number__icontains=q) | Q(barcode__icontains=q) | Q(manufacturer_no__icontains=q))
        rows = [M._iinfo(p) for p in qs.select_related('uom').order_by('name')[:25]]
        from .images import item_images
        pics = item_images([r['id'] for r in rows])
        for r in rows:
            r['has_image'] = r['id'] in pics
        return Response(rows)


class ItemImageView(APIView):
    """the item master picture as a small JPEG (review screen thumbnails)"""
    permission_classes = [IsAuthenticated]

    def get(self, request, pid):
        from .images import item_images, thumb
        f = item_images([pid]).get(int(pid))
        if f is None:
            return err('No picture.', 404)
        r = HttpResponse(thumb(f, int(request.query_params.get('size') or 240)), content_type='image/jpeg')
        r['Cache-Control'] = 'private, max-age=600'
        return r


class PhotoView(APIView):
    """a photo of one line's item → the items that look like it, with a percentage (kept on the job: it can become the item's picture)"""
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, pk):
        from .images import photo_match
        j = mine(request).filter(pk=pk).first()
        if j is None:
            return err('Not found.', 404)
        f = request.FILES.get('file')
        if not f or not f.name.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
            return err('Upload a photo (JPG, PNG or WEBP).', field='file')
        if f.size > 10 * 1024 * 1024:
            return err('The photo is larger than 10 MB.', field='file')
        data = f.read()
        n = int(request.data.get('n') or 0)
        line = next((l for l in (j.proposal or {}).get('lines') or [] if l.get('n') == n), None)
        hint = [line['item']['id']] if line and line.get('item', {}).get('id') else []
        try:
            cands, note = photo_match(request, data, j.kind, hint)
        except Exception as e:   # noqa: BLE001
            log.exception('photo match failed')
            return err(f'The photo could not be compared: {str(e)[:150]}')
        from django.core.files.storage import default_storage
        path = default_storage.save(f'doc_capture/photos/{j.pk}_{n}_{f.name[-60:]}', ContentFile(data))
        ex = dict(j.extracted or {})
        ex.setdefault('_photos', {})[str(n)] = {'path': path, 'name': f.name[:120]}
        j.extracted = ex
        j.save(update_fields=['extracted'])
        return Response({'n': n, 'candidates': cands, 'note': note})
