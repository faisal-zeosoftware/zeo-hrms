"""v1.62.0 – export control (README-UPDATE 83.6): every export written down, a right to export, a row limit, a watermark.

An export is an Excel / CSV answer of the API, or a PDF asked for as a list export (?file=pdf, ?format=pdf, /export…).
For each company (Settings › Sign-in security › Exports):
  * every export → ZeoAdmin.ExportLog (who, which screen, how many rows) – shown on the security dashboard;
  * "Exports need a right": HR / back-office users need the right "Can export lists" (Permission groups); employees still
    export their own data (My payslips …);
  * "Largest export": more rows than the limit need the right "Can export large lists" – a refused export is a security
    event and the administrators are told;
  * "Watermark on PDFs": every PDF made by ZEO (lists, payslips, letters …) carries the user and the time on each page.
Company administrators are never refused. Uploaded files under /media/ are not touched.
"""
import csv
import io
import logging
import re

from django.db import connection
from django.http import JsonResponse
from django.utils import timezone

log = logging.getLogger(__name__)
XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
EXPORT_Q = re.compile(r'(^|&)(file|format|export)=(xlsx|csv|pdf|excel)(&|$)', re.I)
SKIP = ('/media/', '/_protected_media/', '/admin/', '/static/', '/users/', '/cloud/', '/licence/')


def kind_of(request, response):
    ct = (response.get('Content-Type') or '').lower()
    if ct.startswith(XLSX) or ct.startswith('application/vnd.ms-excel'):
        return 'xlsx'
    if ct.startswith('text/csv') or ct.startswith('application/csv'):
        return 'csv'
    if ct.startswith('application/pdf'):
        q = request.META.get('QUERY_STRING', '')
        return 'pdf-list' if (EXPORT_Q.search(q) or re.search(r'/export(/|$|-)', request.path)) else 'pdf'
    return None


def count_rows(kind, content):
    try:
        if kind == 'csv':
            text = content.decode('utf-8-sig', 'replace')
            n = sum(1 for _ in csv.reader(io.StringIO(text)))
            return max(0, n - 1)
        if kind == 'xlsx':
            from openpyxl import load_workbook
            wb = load_workbook(io.BytesIO(content), read_only=True)
            n = 0
            for ws in wb.worksheets[:5]:
                n += max(0, (ws.max_row or 0) - 1)
            wb.close()
            return n
    except Exception:
        log.info('export rows not counted', exc_info=True)
    return None


def _policy(schema):
    from django.core.cache import cache
    k = f'zeo:exppol:{schema}'
    v = cache.get(k)
    if v is None:
        from django_tenants.utils import schema_context
        from ZeoCloud.models import SecurityPolicy
        with schema_context('public'):
            p = SecurityPolicy.objects.filter(schema=schema).values('export_control', 'export_row_limit', 'export_watermark').first()
        v = p or {'export_control': False, 'export_row_limit': 0, 'export_watermark': False}
        cache.set(k, v, 30)
    return v


def forget(schema):
    from django.core.cache import cache
    cache.delete(f'zeo:exppol:{schema}')


def _rights(request):
    try:
        from AccessControl.access import ctx
        c = ctx(request)
        return c.admin, c.backoffice, c.codes
    except Exception:
        return False, False, set()


def watermark(content, text):
    """The PDF with `text` across every page (light grey, diagonal) – the original when anything fails."""
    try:
        from pypdf import PdfReader, PdfWriter
        from reportlab.lib.colors import Color
        from reportlab.pdfgen import canvas
        src = PdfReader(io.BytesIO(content))
        out = PdfWriter()
        stamps = {}
        for page in src.pages:
            w, h = float(page.mediabox.width), float(page.mediabox.height)
            key = (round(w), round(h))
            if key not in stamps:
                buf = io.BytesIO()
                c = canvas.Canvas(buf, pagesize=(w, h))
                c.setFillColor(Color(0.45, 0.45, 0.5, alpha=0.16))
                c.setFont('Helvetica', 11)
                c.saveState()
                c.translate(w / 2, h / 2)
                c.rotate(32)
                for i in range(-6, 7):
                    c.drawCentredString(0, i * 70, text)
                c.restoreState()
                c.setFillColor(Color(0.3, 0.3, 0.35, alpha=0.6))
                c.setFont('Helvetica', 7)
                c.drawString(18, 10, text)
                c.save()
                stamps[key] = PdfReader(io.BytesIO(buf.getvalue())).pages[0]
            page.merge_page(stamps[key])
            out.add_page(page)
        b = io.BytesIO()
        out.write(b)
        return b.getvalue(), True
    except Exception:
        log.exception('watermark')
        return content, False


def _log(request, kind, rows, size, refused='', wm=False):
    try:
        from ZeoAdmin.models import ExportLog
        from ZeoCloud.netguard import client_ip
        u = getattr(request, 'user', None)
        ExportLog.objects.create(user=u if getattr(u, 'is_authenticated', False) else None, path=(request.get_full_path() or '')[:300],
                                 kind=kind[:10], rows=rows, bytes=size, refused=refused[:200], watermarked=wm, ip=client_ip(request))
    except Exception:
        log.exception('export log')


class ExportGuardMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        try:
            schema = getattr(connection, 'schema_name', 'public')
            if not schema or schema == 'public' or request.path.startswith(SKIP) or response.status_code != 200:
                return response
            kind = kind_of(request, response)
            if kind is None:
                return response
            user = getattr(request, 'user', None)
            if not getattr(user, 'is_authenticated', False):
                return response
            streaming = getattr(response, 'streaming', False)
            content = b'' if streaming else response.content
            pol = _policy(schema)
            admin, backoffice, codes = _rights(request)
            if kind == 'pdf':   # a document, not a list: only the watermark
                if pol['export_watermark'] and not streaming:
                    response = self._stamp(request, response, content)
                return response
            rows = None if streaming else count_rows(kind if kind != 'pdf-list' else 'pdf', content)
            refused = ''
            if not admin:
                if pol['export_control'] and backoffice and 'export_lists' not in codes:
                    refused = 'needs the right "Can export lists"'
                elif pol['export_row_limit'] and rows is not None and rows > pol['export_row_limit'] and 'export_large' not in codes:
                    refused = f'{rows} rows – more than the limit of {pol["export_row_limit"]}'
            if refused:
                _log(request, kind[:10], rows, len(content), refused)
                try:
                    from ZeoCloud.netguard import client_ip
                    from ZeoCloud.security import event
                    event('export_refused', user=user, schema=schema, ip=client_ip(request),
                          detail=f'Export of {request.path} refused: {refused}.', notify='limit' in refused)
                except Exception:
                    pass
                from Translations.i18n import tr
                msg = (tr('Exports need the right "Can export lists" – ask your administrator.') if 'right' in refused else
                       tr('This export has {0} rows – more than the limit of {1}. Filter the list, or ask for the right "Can export large lists".',
                          rows, pol['export_row_limit']))
                return JsonResponse({'detail': msg, 'code': 'export_refused'}, status=403)
            wm = False
            if kind == 'pdf-list' and pol['export_watermark'] and not streaming:
                response = self._stamp(request, response, content)
                wm = True
            _log(request, 'pdf' if kind == 'pdf-list' else kind, rows, len(content), '', wm)
        except Exception:
            log.exception('export guard')
        return response

    @staticmethod
    def _stamp(request, response, content):
        from ZeoCloud.netguard import client_ip
        u = getattr(request, 'user', None)
        who = (getattr(u, 'email', '') or getattr(u, 'username', '') or getattr(u, 'name', '') or '').strip()
        when = timezone.localtime().strftime('%d %b %Y %H:%M')
        text = f'ZEO - {who} - {when} - {client_ip(request)}'
        new, ok = watermark(content, text)
        if ok:
            response.content = new
            response['Content-Length'] = str(len(new))
        return response
