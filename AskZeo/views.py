"""Ask ZEO – API (prefix /ask-zeo/).

GET    api/home/                         settings the page needs, example questions (English and Arabic), my conversations
POST   api/ask/                          {question, conversation?, uploads?: [ids], suite?, branch?} → the answer
GET    api/conversations/                my conversations;  GET / DELETE api/conversations/<id>/
POST   api/uploads/                      multipart "file" (≤ 15 MB: xlsx, xls, csv, pdf, docx, txt, png, jpg) → {id, name, kind}
POST   api/changes/<id>/confirm/         apply a previewed change;  POST api/changes/<id>/undo/   put it back
GET    api/changes/                      changes made via Ask ZEO (mine; administrators: everybody's)
POST   api/guided/payroll/               {year, month} → the steps;  {year, month, step, data?} → the preview (Change) of that step
POST   api/forms/<form>/                 {values} → the preview (Change) of a guided form (leave)
GET    api/history/                      who asked what (administrators: the whole company; others: their own)
GET    api/usage/                        questions and AI use of the company this month (administrators)
GET / PATCH api/settings/                the company switches (administrators)
1.72.0
GET / POST api/documents/                company documents Ask ZEO answers from (POST multipart: HR / administrators)
GET / PATCH / DELETE api/documents/<id>/
GET    api/uploads/<id>/file/            my uploaded file (or a file Ask ZEO made for me) back
POST   api/summary/                      the monthly summary e-mail now (administrators; send=true sends it)
"""
import logging
import os
import re

from django.db.models import Count, Q, Sum
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from Translations.i18n import current_lang, tr

from . import changes, engine, guided, ready
from . import scope as S
from .models import AskSettings, AskUsage, Change, Conversation, Message, Upload
from .words import has_arabic

log = logging.getLogger(__name__)
MAX_UPLOAD = 15 * 1024 * 1024
EXT = {'.xlsx': 'spreadsheet', '.xls': 'spreadsheet', '.csv': 'spreadsheet', '.pdf': 'document', '.docx': 'document', '.txt': 'document',
       '.png': 'image', '.jpg': 'image', '.jpeg': 'image',
       '.mp4': 'video', '.mov': 'video', '.webm': 'video'}   # v1.73.4 (A10): an SOP filmed – its on-screen text is read
MAX_VIDEO = 40 * 1024 * 1024
RATE = (60, 600)   # questions per user per 10 minutes – .env ZEO_ASKZEO_RATE=60/600 changes it


def _rate():
    try:
        n, secs = os.environ.get('ZEO_ASKZEO_RATE', '').split('/')
        return max(1, int(n)), max(10, int(secs))
    except ValueError:
        return RATE


def _throttled(user):
    import time
    from django.core.cache import cache
    from django.db import connection
    n, secs = _rate()
    k = f'askzeo:{connection.schema_name}:{user.pk}'
    now = time.time()
    hits = [h for h in (cache.get(k) or []) if h > now - secs]
    if len(hits) >= n:
        return True
    cache.set(k, hits + [now], secs)
    return False


class Base(APIView):
    permission_classes = [IsAuthenticated]

    def initial(self, request, *a, **kw):
        super().initial(request, *a, **kw)
        from rest_framework.exceptions import PermissionDenied, ValidationError
        try:
            self.c = S.ctx(request)
        except S.NoCompany:
            raise ValidationError({'detail': tr('Open a company first.')})
        from django.db import connection
        u = request.user
        try:   # the login must belong to this company (like every other company screen)
            if not (u.is_superuser or u.tenants.filter(schema_name=connection.schema_name).exists()):
                raise PermissionDenied(tr('You do not have access to this company.'))
        except AttributeError:
            pass
        self.s = AskSettings.get()
        if not self.s.enabled and not self.c.admin:
            raise PermissionDenied(tr('Ask ZEO is switched off in this company.'))
        if not self.s.employees_may_ask and not self.c.backoffice:
            raise PermissionDenied(tr('Ask ZEO is open to HR and managers only in this company.'))
        self.lang = current_lang(request) or 'en'

    def finalize_response(self, request, response, *a, **kw):
        """1.72.0 fix: the product's response translation treats a "message" key as an error text and translated every value inside the
        answer (source, intent, card types) for Arabic users. Ask ZEO answers are already in the user's language – keep them as they are."""
        import copy
        keep = None
        data = getattr(response, 'data', None)
        if isinstance(data, dict) and isinstance(data.get('message'), dict) and getattr(response, 'status_code', 500) < 400:
            keep = copy.deepcopy(data['message'])
        response = super().finalize_response(request, response, *a, **kw)
        if keep is not None and isinstance(getattr(response, 'data', None), dict):
            response.data['message'] = keep
        return response

    def admin_only(self):
        from rest_framework.exceptions import PermissionDenied
        if not self.c.admin:
            raise PermissionDenied(tr('Only a company administrator can do this.'))


def _conv_json(cv):
    return {'id': cv.pk, 'title': cv.title, 'updated_at': cv.updated_at.isoformat(), 'created_at': cv.created_at.isoformat()}


def _msg_json(m):
    return {'id': m.pk, 'role': m.role, 'text': m.text, 'intent': m.intent, 'source': m.source, 'source_label': tr(dict(Message._meta.get_field('source').choices).get(m.source, '')) if m.source else '',
            'mode': m.mode, 'created_at': m.created_at.isoformat(), **(m.data or {})}


def _fresh_cards(cards, lang):
    """Change cards show the change's state now (applied / undone), not the state when the answer was given."""
    out = []
    for cd in cards or []:
        if isinstance(cd, dict) and cd.get('type') == 'change' and cd.get('id'):
            ch = Change.objects.filter(pk=cd['id']).first()
            if ch is not None:
                cd = changes.card(ch, lang)
        out.append(cd)
    return out


class HomeView(Base):
    def get(self, request):
        from . import ai
        convs = Conversation.objects.filter(user=request.user)[:30]
        return Response({'enabled': self.s.enabled, 'mode': 'ai' if ai.ready(request) else 'builtin', 'may_change_setup': self.s.may_change_setup,
                         'is_admin': self.c.admin, 'is_hr': S.is_hr(self.c), 'examples': engine.examples(self.c, self.lang),
                         'conversations': [_conv_json(x) for x in convs], 'max_upload_mb': MAX_UPLOAD // (1024 * 1024),
                         'upload_types': sorted(EXT)})


class AskView(Base):
    def post(self, request):
        q = str(request.data.get('question') or '').strip()
        if not q:
            return Response({'question': tr('Ask a question.')}, status=400)
        if len(q) > 2000:
            return Response({'question': tr('Ask in at most 2000 characters.')}, status=400)
        if _throttled(request.user):
            return Response({'detail': tr('You have asked many questions in a short time. Wait a few minutes.')}, status=429)
        cid = request.data.get('conversation')
        conv = None
        if cid not in (None, '', 0, '0'):
            if not str(cid).isdigit():
                return Response({'conversation': tr('Not found.')}, status=400)
            conv = Conversation.objects.filter(pk=int(cid), user=request.user).first()
            if conv is None:
                return Response({'conversation': tr('Not found.')}, status=404)
        ids = request.data.get('uploads') or []
        if not isinstance(ids, list) or any(not str(x).isdigit() for x in ids):
            return Response({'uploads': tr('Give the uploaded file ids as a list.')}, status=400)
        ups = list(Upload.objects.filter(pk__in=[int(x) for x in ids], user=request.user))
        if len(ups) != len(ids):
            return Response({'uploads': tr('Not found.')}, status=404)
        lang = 'ar' if has_arabic(q) else self.lang
        if conv is None:
            conv = Conversation.objects.create(user=request.user, title=q[:200], suite=str(request.data.get('suite') or '')[:20])
        for u in ups:
            if u.conversation_id is None:
                u.conversation = conv
                u.save(update_fields=['conversation'])
        Message.objects.create(conversation=conv, role='user', text=q, language=lang, data={'uploads': [{'id': u.pk, 'name': u.name} for u in ups]} if ups else {})
        branch = ','.join(re.findall(r'\d+', str(request.data.get('branch') or '')))
        try:
            a = engine.answer(request, conv, q, ups, str(request.data.get('suite') or '')[:20], branch, lang)
        except S.NoCompany:
            return Response({'detail': tr('Open a company first.')}, status=400)
        except Exception:
            log.exception('Ask ZEO failed')
            a = {'text': tr('Something went wrong while answering. The question is saved – try again or ask it another way.', lang=lang), 'source': 'check', 'intent': 'error'}
        data = {k: a[k] for k in ('cards', 'links', 'follow', 'figures', 'badges', 'ai_usage', 'cached') if a.get(k)}
        m = Message.objects.create(conversation=conv, role='assistant', text=a.get('text') or '', intent=a.get('intent') or '', source=a.get('source') or '',
                                   mode=a.get('mode') or 'builtin', language=lang, data=data)
        if not a.get('usage_logged'):
            AskUsage.objects.create(user=request.user, feature=a.get('intent') or 'ask', mode=a.get('mode') or 'builtin')
        conv.save(update_fields=['updated_at'])
        return Response({'conversation': _conv_json(conv), 'message': _msg_json(m)})


class ConversationsView(Base):
    def get(self, request):
        return Response([_conv_json(x) for x in Conversation.objects.filter(user=request.user)[:100]])


class ConversationView(Base):
    def _get(self, request, pk):
        return Conversation.objects.filter(pk=pk, user=request.user).first()

    def get(self, request, pk):
        cv = self._get(request, pk)
        if cv is None:
            return Response({'detail': tr('Not found.')}, status=404)
        msgs = []
        for m in cv.messages.all():
            j = _msg_json(m)
            if j.get('cards'):
                j['cards'] = _fresh_cards(j['cards'], self.lang)
            msgs.append(j)
        return Response({**_conv_json(cv), 'messages': msgs})

    def delete(self, request, pk):
        cv = self._get(request, pk)
        if cv is None:
            return Response({'detail': tr('Not found.')}, status=404)
        cv.delete()
        return Response(status=204)


class UploadView(Base):
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        f = request.FILES.get('file')
        if f is None:
            return Response({'file': tr('Choose a file.')}, status=400)
        ext = os.path.splitext(f.name or '')[1].lower()
        if ext not in EXT:
            return Response({'file': tr('This kind of file cannot be used. Use {0}.', ', '.join(sorted(EXT)))}, status=400)
        lim = MAX_VIDEO if EXT.get(ext) == 'video' else MAX_UPLOAD
        if f.size > lim:
            return Response({'file': tr('The file is larger than {0} MB.', lim // (1024 * 1024))}, status=400)
        kind = EXT[ext]
        if ext == '.xlsx':
            try:
                import openpyxl
                wb = openpyxl.load_workbook(f, read_only=True)
                if any(n.strip().lower() == 'index' for n in wb.sheetnames):
                    kind = 'workbook'
                wb.close()
                f.seek(0)
            except Exception:
                return Response({'file': tr('The Excel file cannot be read.')}, status=400)
        u = Upload.objects.create(user=request.user, file=f, name=os.path.basename(f.name)[:255], size=f.size, kind=kind)
        return Response({'id': u.pk, 'name': u.name, 'size': u.size, 'kind': u.kind}, status=201)


class ChangesView(Base):
    def get(self, request):
        qs = Change.objects.exclude(state='preview').select_related('user', 'undone_by')
        if not (self.c.admin or 'view_all_askzeo_changes' in self.c.codes):
            qs = qs.filter(user=request.user)
        return Response([changes.as_json(x, self.lang) for x in qs[:200]])


class ChangeView(Base):
    def post(self, request, pk, action):
        ch = Change.objects.filter(pk=pk).first()
        if ch is None or (ch.user_id != request.user.pk and not self.c.admin):
            return Response({'detail': tr('Not found.')}, status=404)
        try:
            if action == 'confirm':
                changes.confirm(request, ch)
            elif action == 'undo':
                changes.undo(request, ch)
            elif action == 'reject' and (ch.plan or {}).get('via'):   # v1.73.17 an AI tool's draft
                changes.reject(request, ch)
            elif action == 'cancel':
                if changes.by_api_key(request):   # v1.73.17 a key withdraws its drafts at /api/v1/ai/changes/<id>/withdraw/
                    return Response({'detail': tr('Not found.')}, status=404)
                if ch.state != 'preview' or ch.user_id != request.user.pk:
                    return Response({'detail': tr('Only a change that is still a preview can be cancelled.')}, status=400)
                ch.state = 'cancelled'
                ch.save(update_fields=['state'])
            else:
                return Response({'detail': tr('Not found.')}, status=404)
        except changes.Refused as e:
            body = {'detail': str(e), 'change': changes.as_json(Change.objects.get(pk=ch.pk), self.lang)}
            if getattr(e, 'fields', None):
                body['fields'] = e.fields
            return Response(body, status=e.status)
        ch.refresh_from_db()
        out = changes.as_json(ch, self.lang)
        out['card'] = changes.card(ch, self.lang)
        if ch.plan.get('step') and ch.plan.get('ym'):
            y, m = ch.plan['ym']
            out['guided'] = guided.state(request, self.c, y, m, self.lang)
        return Response(out)


class GuidedPayrollView(Base):
    def post(self, request):
        if not S.can(self.c, {'view_payrollrun', 'add_payrollrun', 'change_payrollrun'}):
            return Response({'detail': tr('The guided payroll is for payroll users.')}, status=403)
        try:
            y, m = int(request.data.get('year')), int(request.data.get('month'))
            if not (2000 <= y <= 2100 and 1 <= m <= 12):
                raise ValueError
        except (TypeError, ValueError):
            return Response({'month': tr('Give the year and the month (1–12).')}, status=400)
        step = request.data.get('step')
        if not step:
            return Response(guided.state(request, self.c, y, m, self.lang))
        cid = request.data.get('conversation')
        conv = Conversation.objects.filter(pk=cid, user=request.user).first() if str(cid or '').isdigit() else None
        try:
            ch = guided.plan(request, self.c, conv, y, m, str(step), request.data.get('data') or {}, self.lang)
        except changes.Refused as e:
            return Response({'detail': str(e)}, status=e.status)
        return Response({'card': changes.card(ch, self.lang)})


class FormView(Base):
    def get(self, request, form):
        if form != 'leave':
            return Response({'detail': tr('Not found.')}, status=404)
        return Response(ready.leave_form(request, self.c, self.lang))

    def post(self, request, form):
        cid = request.data.get('conversation')
        conv = Conversation.objects.filter(pk=cid, user=request.user).first() if str(cid or '').isdigit() else None
        values = request.data.get('values')
        if not isinstance(values, dict):
            return Response({'values': tr('Fill in the form.')}, status=400)
        try:
            ch = ready.submit_form(request, self.c, conv, form, values, self.lang)
        except changes.Refused as e:
            return Response({'detail': str(e), 'fields': getattr(e, 'fields', {})}, status=e.status)
        return Response({'card': changes.card(ch, self.lang)})


class HistoryView(Base):
    def get(self, request):
        qs = Message.objects.filter(role='user').select_related('conversation__user')
        if not self.c.admin:
            qs = qs.filter(conversation__user=request.user)
        u = request.query_params.get('user')
        if u and str(u).isdigit():
            qs = qs.filter(conversation__user_id=int(u))
        rows = []
        for m in qs.order_by('-created_at')[:300]:
            ans = Message.objects.filter(conversation_id=m.conversation_id, role='assistant', created_at__gte=m.created_at).order_by('created_at').first()
            rows.append({'at': m.created_at.isoformat(), 'user': m.conversation.user.username, 'question': m.text, 'intent': ans.intent if ans else '',
                         'source': ans.source if ans else '', 'mode': ans.mode if ans else '', 'conversation': m.conversation_id})
        ch = Change.objects.exclude(state='preview').select_related('user', 'undone_by')
        if not self.c.admin:
            ch = ch.filter(user=request.user)
        # v1.73.17 drafts of AI tools (API keys) waiting for a company administrator
        waiting = Change.objects.filter(state='preview', plan__has_key='via').select_related('user') if self.c.admin else Change.objects.none()
        return Response({'questions': rows, 'changes': [changes.as_json(x, self.lang) for x in ch[:200]],
                         'waiting': [dict(changes.as_json(x, self.lang), rows=changes.card(x, self.lang).get('rows') or [],
                                          blocked=changes.card(x, self.lang).get('blocked')) for x in waiting[:100]]})


class UsageView(Base):
    def get(self, request):
        self.admin_only()
        start = timezone.localtime().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        qs = AskUsage.objects.filter(at__gte=start)
        agg = qs.aggregate(calls=Sum('calls'), tin=Sum('tokens_in'), tout=Sum('tokens_out'), cost=Sum('cost_usd'))
        by = list(qs.values('feature', 'mode').annotate(n=Count('id'), calls=Sum('calls'), cost=Sum('cost_usd')).order_by('-n'))
        users = list(qs.values('user__username').annotate(n=Count('id')).order_by('-n')[:20])
        return Response({'since': start.date().isoformat(), 'questions': qs.count(), 'ai_questions': qs.filter(mode='ai').count(),
                         'ai_calls': agg['calls'] or 0, 'tokens_in': agg['tin'] or 0, 'tokens_out': agg['tout'] or 0,
                         'cost_usd': str(round(agg['cost'] or 0, 4)), 'budget_usd': str(self.s.monthly_budget_usd),
                         'by_feature': [{**x, 'cost': str(x['cost'] or 0)} for x in by], 'by_user': users})


SETTINGS_FIELDS = {'enabled': bool, 'may_change_setup': bool, 'employees_may_ask': bool, 'hide_salary': bool, 'mask_names': bool,
                   'monthly_budget_usd': 'money', 'model_ask': 'text', 'model_small': 'text', 'price_in_per_mtok': 'money', 'price_out_per_mtok': 'money',
                   'summary_email': bool, 'summary_to': 'emails'}


def _settings_json(s):
    return {'enabled': s.enabled, 'may_change_setup': s.may_change_setup, 'employees_may_ask': s.employees_may_ask, 'hide_salary': s.hide_salary,
            'mask_names': s.mask_names, 'monthly_budget_usd': f'{s.monthly_budget_usd:.2f}', 'model_ask': s.model_ask, 'model_small': s.model_small,
            'price_in_per_mtok': f'{s.price_in_per_mtok:.3f}', 'price_out_per_mtok': f'{s.price_out_per_mtok:.3f}', 'summary_email': s.summary_email,
            'summary_to': s.summary_to, 'updated_at': s.updated_at.isoformat() if s.updated_at else None,
            'updated_by': getattr(s.updated_by, 'username', '') if s.updated_by_id else ''}


class SettingsView(Base):
    def get(self, request):
        self.admin_only()
        return Response(_settings_json(self.s))

    def patch(self, request):
        self.admin_only()
        from decimal import Decimal, InvalidOperation
        d = request.data if isinstance(request.data, dict) else {}
        err = {}
        unknown = [k for k in d if k not in SETTINGS_FIELDS]
        if unknown:
            return Response({'detail': tr('Unknown setting(s): {0}', ', '.join(unknown))}, status=400)
        for k, typ in SETTINGS_FIELDS.items():
            if k not in d:
                continue
            v = d[k]
            if typ is bool:
                if not isinstance(v, bool):
                    err[k] = tr('Give true or false.')
                    continue
            elif typ == 'money':
                try:
                    v = Decimal(str(v))
                    if v < 0 or v > Decimal('99999'):
                        raise InvalidOperation
                except (InvalidOperation, ValueError):
                    err[k] = tr('Give an amount from 0 to 99,999.')
                    continue
            elif typ == 'text':
                v = str(v or '').strip()
                if len(v) > 120 or (v and not re.fullmatch(r'[\w.:\-/@]+', v)):
                    err[k] = tr('Letters, digits and . - _ : / only, at most 120.')
                    continue
            elif typ == 'emails':
                v = ', '.join(x.strip() for x in str(v or '').split(',') if x.strip())
                bad = [x for x in v.split(', ') if x and not re.fullmatch(r'[^@\s,]+@[^@\s,]+\.[^@\s,]+', x)]
                if bad or len(v) > 2000:
                    err[k] = tr('Not an e-mail address: {0}', ', '.join(bad[:3]) or '…')
                    continue
            setattr(self.s, k, v)
        if err:
            return Response(err, status=400)
        self.s.updated_by = request.user
        self.s.save()
        return Response(_settings_json(self.s))


# ---- 1.72.0 ---------------------------------------------------------------------------------------------------------------
DOC_EXT = ('.pdf', '.docx', '.txt', '.md')


def _doc_json(d, lang=None):
    from .models import DOC_ACCESS, DOC_KINDS
    return {'id': d.pk, 'title': d.title, 'kind': d.kind, 'kind_label': tr(dict(DOC_KINDS).get(d.kind, d.kind), lang=lang), 'file_name': d.file_name,
            'access': d.access, 'access_label': tr(dict(DOC_ACCESS).get(d.access, d.access), lang=lang),
            'branches': [{'id': b.pk, 'name': str(b)} for b in d.branches.all()], 'active': d.active, 'chars': d.chars,
            'sections': d.parts.count(), 'uploaded_by': getattr(d.uploaded_by, 'username', '') if d.uploaded_by_id else '',
            'created_at': d.created_at.isoformat()}


class DocumentsView(Base):
    """GET api/documents/ – the documents I may read (HR / administrators: all, with access);  POST multipart {file, title, kind?, access?,
    branches?: "1,2"} – HR and administrators add one; it is cut into sections and searched by Ask ZEO."""
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def _may_manage(self):
        from rest_framework.exceptions import PermissionDenied
        if not (self.c.admin or S.is_hr(self.c)):
            raise PermissionDenied(tr('Only HR or a company administrator can manage company documents.'))

    def get(self, request):
        from . import docs
        qs = docs.visible(self.c).prefetch_related('branches').select_related('uploaded_by')
        return Response([_doc_json(d, self.lang) for d in qs])

    def post(self, request):
        self._may_manage()
        from django.db import transaction
        from . import docs
        from .models import DOC_ACCESS, DOC_KINDS, CompanyDoc, DocPart
        f = request.FILES.get('file')
        err = {}
        if f is None:
            err['file'] = tr('Choose a file.')
        else:
            ext = os.path.splitext(f.name or '')[1].lower()
            if ext not in DOC_EXT:
                err['file'] = tr('This kind of file cannot be used. Use {0}.', ', '.join(DOC_EXT))
            elif f.size > MAX_UPLOAD:
                err['file'] = tr('The file is larger than {0} MB.', MAX_UPLOAD // (1024 * 1024))
        title = (request.data.get('title') or '').strip()
        if not title and f is not None:
            title = os.path.splitext(os.path.basename(f.name))[0][:200]
        if len(title) > 200:
            err['title'] = tr('At most {0} characters.', 200)
        kind = request.data.get('kind') or 'policy'
        if kind not in dict(DOC_KINDS):
            err['kind'] = tr('Choose one of: {0}', ', '.join(dict(DOC_KINDS)))
        access = request.data.get('access') or 'all'
        if access not in dict(DOC_ACCESS):
            err['access'] = tr('Choose one of: {0}', ', '.join(dict(DOC_ACCESS)))
        branch_ids = []
        raw = request.data.get('branches') or ''
        if raw:
            try:
                branch_ids = sorted({int(x) for x in str(raw).replace(' ', '').split(',') if x})
            except ValueError:
                err['branches'] = tr('Give branch numbers separated by commas.')
            else:
                from django.apps import apps as A
                B = A.get_model('OrganisationManager', 'brnch_mstr')
                found = set(B.objects.filter(pk__in=branch_ids).values_list('pk', flat=True))
                if found != set(branch_ids):
                    err['branches'] = tr('Unknown branch: {0}', ', '.join(str(x) for x in branch_ids if x not in found))
        if err:
            return Response(err, status=400)
        data = f.read()
        try:
            text = docs.extract(f.name, data) or ''
        except Exception:
            log.info('Ask ZEO document read', exc_info=True)
            text = ''
        if len(text.strip()) < 20:
            return Response({'file': tr('No text could be read from this file (a scanned PDF needs to be typed or saved as text first).')}, status=400)
        f.seek(0)
        with transaction.atomic():
            d = CompanyDoc.objects.create(title=title, kind=kind, file=f, file_name=os.path.basename(f.name)[:255], access=access,
                                          chars=len(text), uploaded_by=request.user)
            if branch_ids:
                d.branches.set(branch_ids)
            DocPart.objects.bulk_create([DocPart(doc=d, order=i, title=(t or '')[:200], text=x) for i, (t, x) in enumerate(docs.split(text))])
        return Response(_doc_json(d, self.lang), status=201)


class DocumentView(DocumentsView):
    """PATCH api/documents/<id>/ {title?, kind?, access?, active?};  DELETE – HR and administrators."""

    def get(self, request, pk):
        from . import docs
        d = docs.visible(self.c).filter(pk=pk).first()
        if d is None:
            return Response({'detail': tr('Not found.')}, status=404)
        out = _doc_json(d, self.lang)
        out['parts'] = [{'order': p.order, 'title': p.title, 'text': p.text[:400]} for p in d.parts.all()[:200]]
        return Response(out)

    def patch(self, request, pk):
        self._may_manage()
        from .models import DOC_ACCESS, DOC_KINDS, CompanyDoc
        d = CompanyDoc.objects.filter(pk=pk).first()
        if d is None:
            return Response({'detail': tr('Not found.')}, status=404)
        v, err = request.data if isinstance(request.data, dict) else {}, {}
        if 'title' in v:
            t = str(v['title'] or '').strip()
            if not t or len(t) > 200:
                err['title'] = tr('Give a title of at most {0} characters.', 200)
            d.title = t
        if 'kind' in v:
            if v['kind'] not in dict(DOC_KINDS):
                err['kind'] = tr('Choose one of: {0}', ', '.join(dict(DOC_KINDS)))
            d.kind = v['kind']
        if 'access' in v:
            if v['access'] not in dict(DOC_ACCESS):
                err['access'] = tr('Choose one of: {0}', ', '.join(dict(DOC_ACCESS)))
            d.access = v['access']
        if 'active' in v:
            if not isinstance(v['active'], bool):
                err['active'] = tr('Give true or false.')
            d.active = v['active'] is True
        if err:
            return Response(err, status=400)
        d.save()
        return Response(_doc_json(d, self.lang))

    def delete(self, request, pk):
        self._may_manage()
        from .models import CompanyDoc
        d = CompanyDoc.objects.filter(pk=pk).first()
        if d is None:
            return Response({'detail': tr('Not found.')}, status=404)
        try:
            d.file.delete(save=False)
        except Exception:
            pass
        d.delete()
        return Response(status=204)


class UploadFileView(Base):
    """GET api/uploads/<id>/file/ – my own uploaded file back (or a file Ask ZEO made for me, e.g. a Word guide)."""

    def get(self, request, pk):
        from django.http import FileResponse
        u = Upload.objects.filter(pk=pk, user=request.user).first()
        if u is None or not u.file:
            return Response({'detail': tr('Not found.')}, status=404)
        return FileResponse(u.file.open('rb'), as_attachment=True, filename=u.name)


class SummaryView(Base):
    """POST api/summary/ {send?: false} – administrators: the monthly summary e-mail now (preview; send=true sends it)."""

    def post(self, request):
        self.admin_only()
        from . import summary
        send = request.data.get('send') is True if isinstance(request.data, dict) else False
        subject, text, mode = summary.build(request.user)
        to = summary.recipients(self.s, request.user)
        sent = False
        if send:
            if not to:
                return Response({'detail': tr('Give the e-mail addresses in "Send the summary to".')}, status=400)
            from django.core.mail import send_mail
            send_mail(subject, text, None, to, fail_silently=False)
            sent = True
        return Response({'subject': subject, 'text': text, 'mode': mode, 'to': to, 'sent': sent})


class TestCompanyView(Base):
    """v1.73.5 – Ask ZEO makes a module in the company's test company (Settings › Test company) before the live company.
    POST {package} – the module, its scripts (switched off) and its print layout, by ZEO Developer's package import, in the test
    company's schema. DELETE <module key> – removes them there again (Undo). Company administrators only."""

    def _test(self):
        from ZeoAdmin.models import TestCompany
        return TestCompany.objects.filter(state='done').exclude(schema_name='').order_by('-at').first()

    def post(self, request):
        if not self.c.admin:
            return Response({'detail': tr('Only a company administrator can do this.')}, status=403)
        t = self._test()
        if t is None:
            return Response({'detail': tr('This company has no test company (Settings › Test company).')}, status=404)
        from django_tenants.utils import schema_context
        from ZeoDeveloper import packages as P
        try:
            with schema_context(t.schema_name):
                summary = P.apply((request.data or {}).get('package') or {}, request.user, source='ask-zeo', name='From Ask ZEO')
        except P.PackageError as e:
            return Response({'detail': e.message}, status=e.status)
        return Response({'id': t.pk, 'test_company': t.name, 'schema': t.schema_name, 'summary': summary}, status=201)

    def delete(self, request, key):
        if not self.c.admin:
            return Response({'detail': tr('Only a company administrator can do this.')}, status=403)
        t = self._test()
        if t is None:
            return Response(status=204)
        from django_tenants.utils import schema_context
        with schema_context(t.schema_name):
            from ZeoDeveloper.models import PrintLayout, Script
            from ZeoFlow.models import CustomModule, CustomRecord
            m = CustomModule.objects.filter(key=key).first()
            if m is not None and CustomRecord.objects.filter(module=m).exists():
                return Response({'detail': tr('The module has records in the test company – delete them there first.')}, status=409)
            Script.objects.filter(module_key=key).delete()
            PrintLayout.objects.filter(module_key=key).delete()
            CustomModule.objects.filter(key=key).delete()
        return Response(status=204)
