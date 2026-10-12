"""v1.16.0 – AI settings API (base /ai/api/, company by ?schema= like every other call).

GET   settings/          company admin: the company's AI settings (the API key only as its last 4 characters)
PATCH settings/          company admin: mode, provider, api_url, model, api_key (write-only), clear_key, use_ask,
                         use_documents, monthly_limit; unknown keys → 400 naming the key
POST  settings/test/     company admin: test the connection with the saved values or the ones on the screen
GET   status/            any logged-in user: which mode Ask ZEO and documents use now, OCR available
POST  documents/read/    users who can add employee documents (and ESS users for their own uploads): multipart `file`
                         → suggestions for document type, number, issue and expiry date (nothing is saved)
"""
import logging
import re
import time

from django.core.cache import cache
from django.db import connection
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from Translations.i18n import tr
from . import service as S
from .docread import ocr_available
from .models import AICallLog, AISettings, PROVIDERS

log = logging.getLogger(__name__)

ADMIN_ONLY = 'Only the company administrator can see or change the AI settings.'
FIELDS = {'mode', 'provider', 'api_url', 'model', 'api_key', 'clear_key', 'use_ask', 'use_documents', 'monthly_limit'}
DOC_RATE = (30, 600)   # document readings per user per 10 minutes


def _ctx(request):
    from AccessControl.access import ctx
    return ctx(request)


def _tenant():
    s = getattr(connection, 'schema_name', 'public')
    return None if s == 'public' else s


def _need_company():
    if _tenant() is None:
        raise ValidationError({'detail': tr('Choose a company first (?schema=).')})


def _bool(v):
    if isinstance(v, bool):
        return v
    if str(v).strip().lower() in ('1', 'true', 'yes', 'on'):
        return True
    if str(v).strip().lower() in ('0', 'false', 'no', 'off', ''):
        return False
    raise ValueError


class _Admin(APIView):
    permission_classes = [IsAuthenticated]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        _need_company()
        try:
            admin = bool(_ctx(request).admin)
        except Exception:
            admin = bool(getattr(request.user, 'is_superuser', False))
        if not admin:
            raise PermissionDenied(tr(ADMIN_ONLY))


def data_sent_notice():
    return {
        'ask': tr('Ask ZEO: the question, the earlier questions of the conversation and the report rows the assistant looks up to answer '
                  '(at most 100 rows per look-up, as the asking user is allowed to see them – names, amounts, dates). Not whole tables, '
                  'no passwords, no files.'),
        'documents': tr('Documents: the text read from the uploaded document (at most 6,000 characters – names, numbers, dates of '
                        'passports, Emirates IDs, visas …) and its file name. The file itself is not sent.'),
        'general': tr('The provider receives this data under your contract with them. Check that it is allowed by your company\'s '
                      'data-protection rules (UAE PDPL) before you switch external AI on.'),
    }


def serialize(row):
    key = S.decrypt_key(row.api_key_enc)
    used = S.month_usage()
    calls = [{'at': c.at, 'user': c.username, 'feature': c.feature, 'feature_label': c.get_feature_display(), 'provider': c.provider,
              'sent': c.sent, 'ok': c.ok, 'result': c.result, 'request_bytes': c.request_bytes, 'response_bytes': c.response_bytes,
              'duration_ms': c.duration_ms} for c in AICallLog.objects.all()[:20]]
    from django.contrib.auth import get_user_model
    by = get_user_model().objects.filter(pk=row.updated_by_id).values_list('username', flat=True).first() if row.updated_by_id else None
    return {
        'mode': row.mode, 'provider': row.provider, 'api_url': row.api_url, 'model': row.model,
        'key_set': bool(row.api_key_enc), 'key_last4': row.key_last4, 'key_unreadable': bool(row.api_key_enc) and key is None,
        'use_ask': row.use_ask, 'use_documents': row.use_documents, 'monthly_limit': row.monthly_limit, 'used_this_month': used,
        'server_external_allowed': S.server_allows_external(),
        'server_note': '' if S.server_allows_external() else tr('External AI is switched off by the server administrator.'),
        'effective': {'ask': S.feature_mode('ask', row)[0], 'documents': S.feature_mode('documents', row)[0]},
        'ocr_available': ocr_available(),
        'providers': [{'code': c, 'label': l, 'default_url': S.DEFAULT_URLS.get(c, ''), 'default_model': S.DEFAULT_MODELS.get(c, '')} for c, l in PROVIDERS],
        'data_sent': data_sent_notice(),
        'recent_calls': calls,
        'updated_at': row.updated_at, 'updated_by': by,
    }


class SettingsView(_Admin):
    def get(self, request):
        return Response(serialize(AISettings.get()))

    def patch(self, request):
        d = request.data if isinstance(request.data, dict) else {}
        bad = sorted(set(d) - FIELDS)
        if bad:
            return Response({'detail': tr('Unknown setting: {0}.', ', '.join(bad))}, status=400)
        row = AISettings.get()
        errors = {}
        if 'mode' in d:
            if d['mode'] not in ('builtin', 'external'):
                errors['mode'] = tr('Choose "Built-in" or "External AI".')
            elif d['mode'] == 'external' and not S.server_allows_external():
                errors['mode'] = tr('External AI is switched off by the server administrator.')
            else:
                row.mode = d['mode']
        if 'provider' in d:
            if d['provider'] not in dict(PROVIDERS):
                errors['provider'] = tr('Choose Anthropic, OpenAI-compatible or Azure OpenAI.')
            else:
                row.provider = d['provider']
        if 'api_url' in d:
            u = str(d['api_url'] or '').strip()
            if len(u) > 500:
                errors['api_url'] = tr('The API address is too long (at most 500 characters).')
            elif u:
                msg = S.check_url(u)
                if msg:
                    errors['api_url'] = msg
            row.api_url = u
        if 'model' in d:
            m = str(d['model'] or '').strip()
            if len(m) > 120 or (m and not re.fullmatch(r'[\w.:/@\-]+', m)):
                errors['model'] = tr('The model name may contain letters, digits and . : / @ - _ (at most 120 characters).')
            row.model = m
        for f in ('use_ask', 'use_documents'):
            if f in d:
                try:
                    setattr(row, f, _bool(d[f]))
                except ValueError:
                    errors[f] = tr('Use true or false.')
        if 'monthly_limit' in d:
            try:
                n = int(d['monthly_limit'])
                if not 1 <= n <= 1000000:
                    raise ValueError
                row.monthly_limit = n
            except (TypeError, ValueError):
                errors['monthly_limit'] = tr('Enter a whole number of calls between 1 and 1,000,000.')
        if d.get('clear_key') in (True, 'true', '1', 1):
            row.api_key_enc, row.key_last4 = '', ''
        k = d.get('api_key')
        if k not in (None, ''):
            k = str(k).strip()
            if len(k) < 8 or len(k) > 500 or re.search(r'\s', k):
                errors['api_key'] = tr('The API key looks wrong: paste the whole key without spaces.')
            else:
                row.api_key_enc, row.key_last4 = S.encrypt_key(k), k[-4:]
        if row.mode == 'external' and not errors:
            if not row.api_key_enc:
                errors['api_key'] = tr('Enter the API key of the external AI.')
            if row.provider == 'azure' and not row.api_url:
                errors['api_url'] = tr('Enter the address of your Azure OpenAI deployment.')
            if not (row.model or S.DEFAULT_MODELS.get(row.provider)) and row.provider != 'azure':
                errors['model'] = tr('Enter the model name.')
        if errors:
            return Response({'detail': ' '.join(errors.values()), 'errors': errors}, status=400)
        row.updated_by_id = request.user.pk
        row.save()
        log.info('AI settings changed by %s: mode=%s provider=%s ask=%s documents=%s limit=%s key_changed=%s', request.user.pk, row.mode,
                 row.provider, row.use_ask, row.use_documents, row.monthly_limit, k not in (None, '') or bool(d.get('clear_key')))
        return Response(serialize(row))


class TestView(_Admin):
    def post(self, request):
        d = request.data if isinstance(request.data, dict) else {}
        row = AISettings.get()
        override = {k: d.get(k) for k in ('provider', 'api_url', 'model', 'api_key') if k in d}
        if override.get('provider') and override['provider'] not in dict(PROVIDERS):
            return Response({'ok': False, 'message': tr('Choose Anthropic, OpenAI-compatible or Azure OpenAI.')}, status=400)
        if override.get('api_url') is not None:
            override['api_url'] = str(override['api_url']).strip()
        return Response(S.test_connection(request, row, override))


class StatusView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        _need_company()
        row = AISettings.get()
        return Response({'ask': S.feature_mode('ask', row)[0], 'documents': S.feature_mode('documents', row)[0],
                         'ocr_available': ocr_available(), 'server_external_allowed': S.server_allows_external()})


def _throttled(user):
    k = f'zeo-docread:{user.pk}'
    hits = [h for h in (cache.get(k) or []) if h > time.time() - DOC_RATE[1]]
    if len(hits) >= DOC_RATE[0]:
        return True
    cache.set(k, hits + [time.time()], DOC_RATE[1])
    return False


class DocumentReadView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request):
        _need_company()
        c = _ctx(request)
        if not (c.admin or c.codes & {'add_emp_documents', 'change_emp_documents'} or c.emp is not None):
            raise PermissionDenied(tr('You do not have permission to add employee documents.'))
        f = request.FILES.get('file')
        if f is None:
            return Response({'detail': tr('Choose the file of the document first.')}, status=400)
        from .docread import MAX_BYTES, read_document
        if f.size > MAX_BYTES:
            return Response({'detail': tr('The file is larger than 10 MB. Upload a smaller scan or PDF.')}, status=400)
        if _throttled(request.user):
            return Response({'detail': tr('You have read many documents in a short time. Wait a few minutes.')}, status=429)
        from EmpManagement.models import document_type
        types = list(document_type.objects.filter(is_active=True).values('id', 'type_name'))
        started = time.time()
        res = read_document(request, f.name, f.read(), types)
        log.info('doc-read user=%s kind=%s source=%s mode=%s fields=%d secs=%.1f', request.user.pk, res.get('kind'), res.get('source'),
                 res.get('mode'), len(res['fields']), time.time() - started)
        return Response(res, status=status.HTTP_200_OK)
