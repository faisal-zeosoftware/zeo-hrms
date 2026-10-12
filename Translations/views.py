"""v1.15.0 – /translations/api/ : translations of master records (department, designation, leave type …).

GET  registry/                              translatable models, their fields, API endpoints and the company's languages
GET  record/?model=<app.Model>&id=<pk>      one record: original + translation per field and language (also ?endpoint=/organisation/api/Department/)
PUT  record/  {model|endpoint, id, translations: {field: {lang: value}}}   set (empty value = remove) – change right on the model
GET  ?model=&lang=&record_id=&q=&page=      translations list (paged)
POST {model, record_id, field, lang, value} set one translation
DELETE <id>/                                remove one translation
"""
import logging

from django.db.models import Q
from django.urls import Resolver404, resolve
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import registry as R
from . import services as S
from .i18n import active_languages, norm, tr
from .models import FieldTranslation

log = logging.getLogger(__name__)
_ENDPOINTS = None


def _view_model(cls):
    qs = getattr(cls, 'queryset', None)
    if qs is not None:
        return getattr(qs, 'model', None)
    sc = getattr(cls, 'serializer_class', None)
    return getattr(getattr(sc, 'Meta', None), 'model', None)


def endpoints():
    """{model label: [list endpoint, ...]} of the translatable models (from the URL patterns, once)."""
    global _ENDPOINTS
    if _ENDPOINTS is not None:
        return _ENDPOINTS
    from django.urls import get_resolver
    from django.urls.resolvers import URLPattern, URLResolver
    out = {}

    def walk(patterns, prefix):
        for p in patterns:
            if isinstance(p, URLResolver):
                walk(p.url_patterns, prefix + str(p.pattern))
            elif isinstance(p, URLPattern):
                cb = p.callback
                cls = getattr(cb, 'cls', None) or getattr(cb, 'view_class', None)
                actions = getattr(cb, 'actions', None) or {}
                if cls is None or actions.get('get') != 'list':
                    continue
                m = _view_model(cls)
                if m is None or not R.fields_for(m):
                    continue
                path = (prefix + str(p.pattern)).replace('^', '').replace('$', '')
                if '(' in path or '<' in path or 'bulk' in path.lower():
                    continue
                out.setdefault(R.concrete_label(m), []).append('/' + path.lstrip('/'))
    try:
        walk(get_resolver().url_patterns, '')
    except Exception:
        log.exception('translation endpoints scan failed')
    _ENDPOINTS = out
    return out


def model_from(data):
    """The model of ?model= / ?endpoint= (None if not translatable)."""
    label = (data.get('model') or '').strip()
    if label:
        m = R.model_for(label)
        return m if m is not None and R.fields_for(m) else None
    ep = (data.get('endpoint') or '').strip()
    if ep:
        path = '/' + ep.split('?')[0].lstrip('/')
        if not path.endswith('/'):
            path += '/'
        try:
            match = resolve(path)
        except Resolver404:
            return None
        cls = getattr(match.func, 'cls', None) or getattr(match.func, 'view_class', None)
        m = _view_model(cls) if cls else None
        return m if m is not None and R.fields_for(m) else None
    return None


def _err(text, code=status.HTTP_400_BAD_REQUEST, *args):
    return Response({'detail': tr(text, *args)}, status=code)


class RegistryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        eps = endpoints()
        models = []
        for label, (title, fields) in R.entries().items():
            models.append({'model': label, 'label': title, 'fields': [{'field': f, 'label': fl} for f, fl in fields],
                           'endpoints': eps.get(label, [])})
        return Response({'models': models, 'languages': active_languages()})


class RecordView(APIView):
    permission_classes = [IsAuthenticated]

    def _obj(self, request, data):
        model = model_from(data)
        if model is None:
            return None, None, _err('This record type cannot be translated.', status.HTTP_404_NOT_FOUND)
        rid = data.get('id') or data.get('record_id')
        try:
            obj = model.objects.filter(pk=int(rid)).first()
        except (TypeError, ValueError):
            obj = None
        if obj is None:
            return None, None, _err('The record was not found. It may have been deleted.', status.HTTP_404_NOT_FOUND)
        if not S.table_ready():
            return None, None, _err('Translations are not set up for this company yet. Run the database update (migrate_schemas).')
        return model, obj, None

    def get(self, request):
        model, obj, err = self._obj(request, request.query_params)
        if err:
            return err
        if not S.can_view(request, model):
            return _err('You do not have the right to see this record.', status.HTTP_403_FORBIDDEN)
        return Response(S.record_payload(model, obj, request))

    def put(self, request):
        data = request.data if hasattr(request.data, 'get') else {}
        model, obj, err = self._obj(request, data)
        if err:
            return err
        if not S.can_edit(request, model):
            return _err('Only users who may change this record can translate it.', status.HTTP_403_FORBIDDEN)
        tr_in = data.get('translations')
        if not isinstance(tr_in, dict):
            return _err('Send the translations as {"field": {"language": "text"}}.')
        fields = R.fields_for(model)
        codes = {l['code'] for l in active_languages()}
        problems = []
        for f, per in tr_in.items():
            if f not in fields:
                problems.append(tr('{0} cannot be translated.', f))
                continue
            if not isinstance(per, dict):
                problems.append(tr('{0}: send one text per language.', f))
                continue
            for lang in per:
                if norm(lang) not in codes:
                    problems.append(tr('{0} is not an active language of the company.', lang))
            mf = model._meta.get_field(f)
            limit = getattr(mf, 'max_length', None)
            for lang, val in per.items():
                if val is not None and not isinstance(val, str):
                    problems.append(tr('{0}: the translation must be text.', f))
                elif limit and val and len(val.strip()) > max(limit * 2, 200):
                    problems.append(tr('{0}: the translation is too long (at most {1} characters).', f, max(limit * 2, 200)))
        if problems:
            return Response({'detail': ' '.join(problems)}, status=status.HTTP_400_BAD_REQUEST)
        done = {}
        for f, per in tr_in.items():
            for lang, val in per.items():
                res = S.save_translation(model, obj, f, lang, val or '', user=request.user)
                if res:
                    done[f'{f}.{norm(lang)}'] = res
        try:  # change history on the record (Chatter note) – best effort
            if done:
                from Chatter.models import Message
                from Chatter.tracking import model_key
                Message.objects.create(model=model_key(model), object_id=str(obj.pk), author=request.user,
                                       body='Translations changed: ' + ', '.join(f'{k} {v}' for k, v in done.items()))
        except Exception:
            pass
        out = S.record_payload(model, obj, request)
        out['changed'] = done
        return Response(out)


class TranslationListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        q = request.query_params
        from AccessControl.access import ctx
        c = ctx(request)
        qs = FieldTranslation.objects.all() if S.table_ready() else FieldTranslation.objects.none()
        if q.get('model'):
            qs = qs.filter(model=q['model'])
        if q.get('record_id'):
            try:
                qs = qs.filter(record_id=int(q['record_id']))
            except ValueError:
                return _err('record_id must be a number.')
        if q.get('lang'):
            qs = qs.filter(lang=norm(q['lang']))
        if q.get('field'):
            qs = qs.filter(field=q['field'])
        if q.get('q'):
            qs = qs.filter(Q(value__icontains=q['q']) | Q(source__icontains=q['q']))
        if not c.admin:  # only the models the user may see
            allowed = [lbl for lbl in R.entries() if R.model_for(lbl) is not None and S.can_view(request, R.model_for(lbl))]
            qs = qs.filter(model__in=allowed)
        qs = qs.order_by('model', 'record_id', 'field', 'lang')
        try:
            page = max(1, int(q.get('page') or 1))
            size = min(500, max(1, int(q.get('page_size') or 100)))
        except ValueError:
            page, size = 1, 100
        total = qs.count()
        rows = [{'id': r.id, 'model': r.model, 'record_id': r.record_id, 'field': r.field, 'lang': r.lang, 'value': r.value,
                 'source': r.source, 'is_standard': r.is_standard, 'updated_at': r.updated_at}
                for r in qs[(page - 1) * size: page * size]]
        return Response({'count': total, 'page': page, 'page_size': size, 'results': rows})

    def post(self, request):
        d = request.data if hasattr(request.data, 'get') else {}
        model = model_from(d)
        if model is None:
            return _err('This record type cannot be translated.', status.HTTP_404_NOT_FOUND)
        field = d.get('field') or ''
        if field not in R.fields_for(model):
            return _err('{0} cannot be translated.', status.HTTP_400_BAD_REQUEST, field)
        lang = norm(d.get('lang'))
        if lang not in {l['code'] for l in active_languages()}:
            return _err('{0} is not an active language of the company.', status.HTTP_400_BAD_REQUEST, d.get('lang') or '')
        try:
            obj = model.objects.filter(pk=int(d.get('record_id'))).first()
        except (TypeError, ValueError):
            obj = None
        if obj is None:
            return _err('The record was not found. It may have been deleted.', status.HTTP_404_NOT_FOUND)
        if not S.can_edit(request, model):
            return _err('Only users who may change this record can translate it.', status.HTTP_403_FORBIDDEN)
        res = S.save_translation(model, obj, field, lang, d.get('value') or '', user=request.user)
        return Response({'result': res, **S.record_payload(model, obj, request)}, status=status.HTTP_200_OK)


class TranslationDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, pk):
        row = FieldTranslation.objects.filter(pk=pk).first() if S.table_ready() else None
        if row is None:
            return _err('The translation was not found.', status.HTTP_404_NOT_FOUND)
        model = R.model_for(row.model)
        if model is None or not S.can_edit(request, model):
            return _err('Only users who may change this record can translate it.', status.HTTP_403_FORBIDDEN)
        row.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
