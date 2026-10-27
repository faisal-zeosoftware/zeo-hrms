"""v1.52.0 – ZEO administration API (/zeo-admin/api/…)."""
import json
from datetime import date

from django.contrib.auth import get_user_model
from django.db import connection
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import models as M

U = get_user_model()


def bad(msg, code=400):
    return Response({'detail': msg}, status=code)


class Base(APIView):
    permission_classes = [IsAuthenticated]

    def initial(self, request, *a, **k):
        super().initial(request, *a, **k)
        from AccessControl.access import ctx
        self.c = ctx(request)

    @property
    def admin(self):
        return bool(self.c and self.c.admin)

    def need_admin(self):
        if not self.admin:
            raise PermissionError('Company administrators only.')

    def handle_exception(self, exc):
        if isinstance(exc, PermissionError):
            return bad(str(exc), 403)
        return super().handle_exception(exc)


def company_users():
    schema = getattr(connection, 'schema_name', '')
    return U.objects.filter(tenants__schema_name=schema, is_active=True).distinct()


def uname(u):
    return (u.get_full_name() or u.username or u.email) if u else ''


# ------------------------------------------------------------------ scheduled reports (22)
class SchedulesView(Base):
    def get(self, request):
        from DashboardManagement.reports import REPORTS, _allowed
        from .schedules import json_row
        qs = M.ReportSchedule.objects.select_related('owner')
        if not self.admin:
            qs = qs.filter(owner=request.user)
        reports = [{'key': k, 'title': v[0], 'section': v[1], 'period': v[4] is not None} for k, v in REPORTS.items() if _allowed(self.c, v[5])]
        try:   # v1.53.1 report names in the user's language
            from Translations.reports import localize_list
            reports = localize_list(reports, request)
        except Exception:
            pass
        rows = [json_row(s) for s in qs]
        names = {r['key']: r['title'] for r in reports}
        for r in rows:
            r['report_title'] = names.get(r['report'], r['report_title'])
        return Response({'rows': rows, 'reports': sorted(reports, key=lambda r: (r['section'], r['title'])),
                         'can_any_address': self.admin, 'my_email': request.user.email})

    def post(self, request):
        from DashboardManagement.reports import REPORTS, _allowed
        from .schedules import check, json_row, next_run
        d = request.data
        s = M.ReportSchedule.objects.filter(pk=d.get('id')).first() if d.get('id') else M.ReportSchedule(owner=request.user)
        if s is None or (s.pk and s.owner_id != request.user.pk and not self.admin):
            return bad('Not found.', 404)
        spec = REPORTS.get(d.get('report') or s.report)
        if spec is None or not _allowed(self.c, spec[5]):
            return bad('Choose a report you may run.')
        s.name = (d.get('name') or spec[0])[:120]
        s.report = d.get('report') or s.report
        s.params = d.get('params') if isinstance(d.get('params'), dict) else (s.params or {})
        for f, choices in (('period', M.ReportSchedule.PERIODS), ('frequency', M.ReportSchedule.FREQ), ('fmt', M.ReportSchedule.FORMATS)):
            if d.get(f) in dict(choices):
                setattr(s, f, d[f])
        try:
            s.weekday = min(max(int(d.get('weekday', s.weekday) or 0), 0), 6)
            s.day = min(max(int(d.get('day', s.day) or 1), 1), 31)
            s.hour = min(max(int(d.get('hour', s.hour) or 0), 0), 23)
        except (TypeError, ValueError):
            return bad('Give the day and the hour as numbers.')
        rec = d.get('recipients', s.recipients)
        if isinstance(rec, str):
            rec = [x.strip() for x in rec.replace(';', ',').split(',') if x.strip()]
        s.recipients = list(dict.fromkeys(rec or []))
        if not self.admin and any(r.lower() != (request.user.email or '').lower() for r in s.recipients):
            return bad('You can schedule a report to your own e-mail; an administrator can add other addresses.', 403)
        if 'active' in d:
            s.active = str(d['active']).lower() in ('1', 'true')
        p = check(s)
        if p:
            return bad(p)
        s.next_run = next_run(s)
        s.save()
        return Response(json_row(s), status=201)


class ScheduleView(Base):
    def _get(self, request, pk):
        s = M.ReportSchedule.objects.select_related('owner').filter(pk=pk).first()
        if s is None or (s.owner_id != request.user.pk and not self.admin):
            raise PermissionError('Not found.')
        return s

    def delete(self, request, pk):
        self._get(request, pk).delete()
        return Response(status=204)

    def post(self, request, pk, action=None):
        from .schedules import json_row, send
        s = self._get(request, pk)
        if action == 'send':
            st = send(s)
            return Response({**json_row(s), 'result': st}, status=200 if not st.startswith('failed') else 400)
        return bad('Unknown action.')


# ------------------------------------------------------------------ API keys and webhooks (23)
def key_json(k):
    return {'id': k.pk, 'name': k.name, 'prefix': f'zeo_{k.prefix}_…', 'user': uname(k.user), 'user_id': k.user_id, 'read_only': k.read_only,
            'allowed_ips': k.allowed_ips, 'expires_at': k.expires_at, 'active': k.active, 'last_used': k.last_used, 'last_ip': k.last_ip,
            'uses': k.uses, 'created_at': k.created_at, 'created_by': uname(k.created_by), 'scopes': k.scopes or [], 'v1_only': k.v1_only}


def _scopes(d, read_only):
    """v1.61.0 (scopes, problem) from the request."""
    from .api_v1 import SCOPE_KEYS
    raw = d.get('scopes') or []
    if isinstance(raw, str):
        raw = [x.strip() for x in raw.split(',') if x.strip()]
    bad_ = [x for x in raw if x not in SCOPE_KEYS]
    if bad_:
        return None, 'Unknown scope: ' + ', '.join(bad_)
    if read_only and any(x.endswith(':write') for x in raw):
        return None, 'A read-only key cannot have a write scope – untick "read only" or remove the write scope.'
    return sorted(set(raw), key=SCOPE_KEYS.index), None


class ApiKeysView(Base):
    def get(self, request):
        self.need_admin()
        return Response({'rows': [key_json(k) for k in M.ApiKey.objects.select_related('user', 'created_by')],
                         'users': [{'id': u.pk, 'name': uname(u), 'email': u.email} for u in company_users().order_by('email')[:1000]],
                         'schema': getattr(connection, 'schema_name', ''),
                         'scopes': [{'scope': s, 'meaning': m} for s, m in __import__('ZeoAdmin.api_v1', fromlist=['SCOPES']).SCOPES]})

    def post(self, request):
        from .apikeys import hash_key, make_key
        self.need_admin()
        d = request.data
        name = (d.get('name') or '').strip()[:120]
        u = company_users().filter(pk=d.get('user_id')).first()
        if not name or u is None:
            return bad('Give the key a name and choose the user it acts as.')
        if u.is_superuser and not request.user.is_superuser:
            return bad('A key cannot act as the platform administrator.', 403)
        ips = [x.strip() for x in (d.get('allowed_ips') or []) if str(x).strip()] if isinstance(d.get('allowed_ips'), list) else \
            [x.strip() for x in str(d.get('allowed_ips') or '').replace(';', ',').split(',') if x.strip()]
        ro = str(d.get('read_only', True)).lower() not in ('0', 'false')
        scopes, prob = _scopes(d, ro)
        if prob:
            return bad(prob)
        prefix, raw = make_key()
        k = M.ApiKey.objects.create(name=name, prefix=prefix, key_hash=hash_key(raw), user=u, read_only=ro,
                                    allowed_ips=ips, expires_at=parse_date(str(d.get('expires_at') or '')) or None, created_by=request.user,
                                    scopes=scopes, v1_only=str(d.get('v1_only', False)).lower() in ('1', 'true'))
        return Response({**key_json(k), 'key': raw, 'note': 'Copy the key now – it is not shown again.'}, status=201)


class ApiKeyView(Base):
    def patch(self, request, pk):
        self.need_admin()
        k = M.ApiKey.objects.filter(pk=pk).first()
        if k is None:
            return bad('Not found.', 404)
        if 'active' in request.data:
            k.active = str(request.data['active']).lower() in ('1', 'true')
        if 'read_only' in request.data:
            k.read_only = str(request.data['read_only']).lower() in ('1', 'true')
        if 'v1_only' in request.data:   # v1.61.0
            k.v1_only = str(request.data['v1_only']).lower() in ('1', 'true')
        if 'scopes' in request.data or 'read_only' in request.data:
            sc, prob = _scopes({'scopes': request.data.get('scopes', k.scopes)}, k.read_only)
            if prob:
                return bad(prob)
            k.scopes = sc
        k.save()
        return Response(key_json(k))

    def delete(self, request, pk):
        self.need_admin()
        M.ApiKey.objects.filter(pk=pk).delete()
        return Response(status=204)


def hook_json(h):
    from .webhooks import model_label
    return {'id': h.pk, 'name': h.name, 'url': h.url, 'models': [{'key': k, 'label': model_label(k)} for k in h.models_list or []],
            'models_list': h.models_list, 'events': h.events, 'active': h.active, 'has_secret': bool(h.secret), 'last_status': h.last_status,
            'last_at': h.last_at, 'failures': h.failures}


class WebhooksView(Base):
    def get(self, request):
        from .webhooks import CHOICES, EVENTS
        self.need_admin()
        return Response({'rows': [hook_json(h) for h in M.Webhook.objects.all()], 'choices': [{'key': k, 'label': l} for k, l in CHOICES], 'events': EVENTS})

    def post(self, request):
        import secrets
        from .webhooks import EVENTS, forget, put_secret, valid_model
        self.need_admin()
        d = request.data
        h = M.Webhook.objects.filter(pk=d.get('id')).first() if d.get('id') else M.Webhook()
        if h is None:
            return bad('Not found.', 404)
        h.name = (d.get('name') or h.name or '').strip()[:120]
        h.url = (d.get('url') or h.url or '').strip()
        if not h.name or not h.url.startswith(('https://', 'http://')):
            return bad('Give the name and the URL (https://…).')
        ms = [m for m in (d.get('models_list') or h.models_list or []) if valid_model(m)]
        evs = [e for e in (d.get('events') or h.events or []) if e in EVENTS]
        if not ms or not evs:
            return bad('Choose at least one record type and one event.')
        h.models_list, h.events = ms, evs
        if 'active' in d:
            h.active = str(d['active']).lower() in ('1', 'true')
            if h.active:
                h.failures = 0
        new_secret = None
        if not h.secret or d.get('new_secret'):
            new_secret = secrets.token_urlsafe(32)
            h.secret = put_secret(new_secret)
        h.save()
        forget()
        out = hook_json(h)
        if new_secret:
            out['secret'] = new_secret
            out['note'] = 'Keep the signing secret – it checks the X-ZEO-Signature header; it is not shown again.'
        return Response(out, status=201)


class WebhookView(Base):
    def get(self, request, pk, action=None):
        self.need_admin()
        h = M.Webhook.objects.filter(pk=pk).first()
        if h is None:
            return bad('Not found.', 404)
        return Response({'webhook': hook_json(h), 'deliveries': [{'id': x.pk, 'event': x.event, 'status': x.status, 'code': x.code, 'response': x.response,
                                                                   'tries': x.tries, 'next_try': x.next_try, 'at': x.at}
                                                                  for x in h.deliveries.all()[:50]]})

    def post(self, request, pk, action=None):
        from .webhooks import deliver, test
        self.need_admin()
        h = M.Webhook.objects.filter(pk=pk).first()
        if h is None:
            return bad('Not found.', 404)
        if action == 'test':
            x = test(h)
            return Response({'status': x.status, 'code': x.code, 'response': x.response})
        if action == 'retry':
            x = deliver(int(request.data.get('delivery')))
            return Response({'status': x.status if x else 'not found'})
        return bad('Unknown action.')

    def delete(self, request, pk, action=None):
        from .webhooks import forget
        self.need_admin()
        M.Webhook.objects.filter(pk=pk).delete()
        forget()
        return Response(status=204)


# ------------------------------------------------------------------ imports (63)
class ImportMappingView(Base):
    """GET ?endpoint=&headers=[json] → the remembered mapping of this file layout (or null)."""
    def get(self, request):
        from .imports import find_mapping
        try:
            headers = json.loads(request.query_params.get('headers') or '[]')
        except ValueError:
            headers = []
        m = find_mapping(request.query_params.get('endpoint') or '', headers)
        return Response({'mapping': m.mapping if m else None, 'name': m.name if m else '', 'used': m.used if m else 0,
                         'updated_at': m.updated_at if m else None})


class ImportsView(Base):
    def get(self, request):
        from .imports import batch_json
        qs = M.ImportBatch.objects.select_related('user')
        if not self.admin:
            qs = qs.filter(user=request.user)
        if request.query_params.get('endpoint'):
            qs = qs.filter(endpoint=request.query_params['endpoint'])
        return Response([batch_json(b) for b in qs[:200]])


class ImportUndoView(Base):
    def post(self, request, pk):
        from .imports import batch_json, undo
        b = M.ImportBatch.objects.filter(pk=pk).first()
        if b is None or (b.user_id != request.user.pk and not self.admin):
            return bad('Not found.', 404)
        try:
            res = undo(b, request.user)
        except ValueError as e:
            return bad(str(e))
        return Response({**batch_json(b), **res})


# ------------------------------------------------------------------ out of office (64)
def ooo_json(o):
    t = date.today()
    return {'id': o.pk, 'user': uname(o.user), 'user_id': o.user_id, 'delegate': uname(o.delegate), 'delegate_id': o.delegate_id,
            'start_date': o.start_date, 'end_date': o.end_date, 'note': o.note, 'include_pending': o.include_pending, 'active': o.active,
            'moved': o.moved, 'state': 'ended' if (not o.active or o.end_date < t) else ('now' if o.start_date <= t else 'planned')}


class OutOfOfficeView(Base):
    def get(self, request):
        from .delegation import approval_models
        mine = M.OutOfOffice.objects.filter(user=request.user).select_related('user', 'delegate')[:20]
        for_me = M.OutOfOffice.objects.filter(delegate=request.user, active=True, end_date__gte=date.today()).select_related('user', 'delegate')
        out = {'mine': [ooo_json(o) for o in mine], 'for_me': [ooo_json(o) for o in for_me],
               'users': [{'id': u.pk, 'name': uname(u)} for u in company_users().exclude(pk=request.user.pk).filter(is_active=True).order_by('username', 'email')[:1000]],
               'kinds': [label for key, label, _ in approval_models()] + ['Automation approvals']}
        if self.admin and request.query_params.get('all'):
            out['all'] = [ooo_json(o) for o in M.OutOfOffice.objects.filter(end_date__gte=date.today(), active=True).select_related('user', 'delegate')]
        return Response(out)

    def post(self, request):
        from .delegation import check, start
        d = request.data
        user = request.user
        if d.get('user_id') and str(d.get('user_id')) != str(request.user.pk):   # an administrator sets it for someone who is away
            self.need_admin()
            user = company_users().filter(pk=d.get('user_id')).first()
            if user is None:
                return bad('User not found.', 404)
        delegate = company_users().filter(pk=d.get('delegate_id')).first()
        a, b = parse_date(str(d.get('start_date') or '')), parse_date(str(d.get('end_date') or ''))
        p = check(user, delegate, a, b)
        if p:
            return bad(p)
        o = M.OutOfOffice.objects.create(user=user, delegate=delegate, start_date=a, end_date=b, note=(d.get('note') or '')[:255],
                                         include_pending=str(d.get('include_pending', True)).lower() not in ('0', 'false'), created_by=request.user)
        n = start(o)
        return Response({**ooo_json(o), 'moved_now': n}, status=201)

    def delete(self, request, pk=None):
        o = M.OutOfOffice.objects.filter(pk=pk).first()
        if o is None or (o.user_id != request.user.pk and not self.admin):
            return bad('Not found.', 404)
        if o.start_date > date.today():
            o.delete()
        else:
            o.active, o.end_date = False, min(o.end_date, date.today())
            o.save(update_fields=['active', 'end_date'])
        return Response(status=204)


# ------------------------------------------------------------------ health (69)
class HealthView(Base):
    def get(self, request):
        from .health import run
        self.need_admin()
        return Response(run(test_mail=request.query_params.get('test_mail') == '1'))


# ------------------------------------------------------------------ v1.53.0 data retention and privacy (68)
def _emp_name(e):
    return ' '.join(x for x in [e.emp_first_name, e.emp_last_name] if x) or e.emp_code


class RetentionView(Base):
    def get(self, request):
        from . import privacy as P
        self.need_admin()
        if request.query_params.get('q') is not None:   # employee search for the export
            from django.apps import apps
            from django.db.models import Q
            t = request.query_params['q'].strip()
            E = apps.get_model('EmpManagement', 'emp_master')
            qs = E.objects.filter(Q(emp_code__icontains=t) | Q(emp_first_name__icontains=t) | Q(emp_last_name__icontains=t)) if t else E.objects.none()
            return Response({'employees': [{'id': e.pk, 'code': e.emp_code, 'name': _emp_name(e), 'active': e.is_active} for e in qs.order_by('emp_code')[:20]]})
        p = P.policy()
        rows = [{'id': e.pk, 'code': e.emp_code, 'name': _emp_name(e), 'left_on': d} for e, d in P.due(p)[:500]]
        return Response({'policy': {'anonymise_after_years': p.anonymise_after_years, 'automatic': p.automatic, 'delete_documents': p.delete_documents,
                                    'delete_attachments': p.delete_attachments, 'last_run': p.last_run, 'last_result': p.last_result},
                         'due': rows,
                         'done': [{'employee_id': a.employee_id, 'code': a.emp_code, 'left_on': a.left_on, 'removed': a.removed, 'automatic': a.automatic,
                                   'by': uname(a.by), 'at': a.at} for a in M.AnonymisedEmployee.objects.select_related('by')[:100]],
                         'exports': [{'employee_id': x.employee_id, 'code': x.emp_code, 'records': x.records, 'files': x.files, 'by': uname(x.by),
                                      'self_service': x.self_service, 'at': x.at} for x in M.PrivacyExport.objects.select_related('by')[:50]]})

    def put(self, request):
        from . import privacy as P
        self.need_admin()
        p, d = P.policy(), request.data
        try:
            y = int(d.get('anonymise_after_years', p.anonymise_after_years) or 0)
        except (TypeError, ValueError):
            return bad('Give the number of years.')
        if not 0 <= y <= 50:
            return bad('Between 0 (never) and 50 years.')
        p.anonymise_after_years = y
        for f in ('automatic', 'delete_documents', 'delete_attachments'):
            if f in d:
                setattr(p, f, str(d[f]).lower() in ('1', 'true'))
        p.updated_by = request.user
        p.save()
        return self.get(request)


class LegalHoldsView(Base):
    """v1.61.0 GET the holds (active first, then released); POST {employee_id, reason, case_ref} place a hold;
    POST {id, action: 'release', note} release it. Company administrators only."""
    def _rows(self):
        from django.apps import apps
        E = apps.get_model('EmpManagement', 'emp_master')
        hs = list(M.LegalHold.objects.select_related('placed_by', 'released_by').order_by('released_at', '-placed_at')[:300])
        names = {e.pk: _emp_name(e) for e in E.objects.filter(pk__in=[h.employee_id for h in hs])}
        return [{'id': h.id, 'scope': h.scope, 'case_model': h.case_model, 'case_id': h.case_id, 'case_label': h.case_label,
                 'employee_id': h.employee_id, 'code': h.emp_code, 'name': names.get(h.employee_id, ''), 'reason': h.reason,
                 'case_ref': h.case_ref, 'placed_by': uname(h.placed_by), 'placed_at': h.placed_at, 'active': h.released_at is None,
                 'released_by': uname(h.released_by), 'released_at': h.released_at, 'release_note': h.release_note} for h in hs]

    def get(self, request):
        self.need_admin()
        return Response({'rows': self._rows()})

    def post(self, request):
        from django.apps import apps
        from django.utils import timezone
        from Translations.i18n import tr
        self.need_admin()
        d = request.data
        if d.get('action') == 'release':
            h = M.LegalHold.objects.filter(pk=d.get('id'), released_at__isnull=True).first()
            if h is None:
                return bad(tr('Active hold not found.'), 404)
            note = str(d.get('note') or '').strip()
            if not note:
                return bad(tr('Say why the hold is released (e.g. the case is closed).'))
            h.released_by, h.released_at, h.release_note = request.user, timezone.now(), note[:300]
            h.save(update_fields=['released_by', 'released_at', 'release_note'])
            return Response({'rows': self._rows()})
        E = apps.get_model('EmpManagement', 'emp_master')
        reason = str(d.get('reason') or '').strip()
        scope = d.get('scope') or 'employee'
        if scope in ('case', 'company'):   # v1.62.0 a held case (its employee too) or the whole company (no history cleaning)
            if not reason:
                return bad(tr('Give the reason for the hold.'))
            emp_id, label, code = 0, '', ''
            if scope == 'case':
                CASES = {'ZeoHR.DisciplinaryCase': 'employee_id', 'ZeoHR.HRTicket': 'employee_id', 'EmpManagement.EmployeeResignation': 'employee_id'}
                cm = d.get('case_model') or 'ZeoHR.DisciplinaryCase'
                if cm not in CASES:
                    return bad(tr('Choose a disciplinary case, an HR ticket or a resignation.'))
                obj = apps.get_model(cm).objects.filter(pk=d.get('case_id')).first() if str(d.get('case_id') or '').isdigit() else None
                if obj is None:
                    return bad(tr('Case not found.'))
                emp_id = getattr(obj, CASES[cm], 0) or 0
                e = E.objects.filter(pk=emp_id).first()
                code = getattr(e, 'emp_code', '') or ''
                label = str(getattr(obj, 'number', '') or getattr(obj, 'document_number', '') or obj)[:200]
            elif M.LegalHold.objects.filter(scope='company', released_at__isnull=True).exists():
                return bad(tr('The company is under a legal hold already.'))
            M.LegalHold.objects.create(employee_id=emp_id, emp_code=code, reason=reason[:300], case_ref=str(d.get('case_ref') or '')[:120],
                                       placed_by=request.user, scope=scope, case_model=d.get('case_model', '') if scope == 'case' else '',
                                       case_id=str(d.get('case_id') or '') if scope == 'case' else '', case_label=label)
            return Response({'rows': self._rows()}, status=201)
        e = E.objects.filter(pk=d.get('employee_id')).first() if str(d.get('employee_id') or '').isdigit() else None
        if e is None:
            return bad(tr('Choose the employee.'))
        if not reason:
            return bad(tr('Give the reason for the hold.'))
        if M.LegalHold.objects.filter(employee_id=e.pk, released_at__isnull=True, scope='employee').exists():
            return bad(tr('{0} is under a legal hold already.', e.emp_code))
        if M.AnonymisedEmployee.objects.filter(employee_id=e.pk).exists():
            return bad(tr('{0} was anonymised already – there is nothing left to hold.', e.emp_code))
        M.LegalHold.objects.create(employee_id=e.pk, emp_code=e.emp_code or '', reason=reason[:300], case_ref=str(d.get('case_ref') or '')[:120],
                                   placed_by=request.user)
        return Response({'rows': self._rows()}, status=201)


class AnonymiseView(Base):
    """POST {employee_ids: [..]} – anonymise these employees who left (whether due or not; HR decides)."""
    def post(self, request):
        from django.apps import apps
        from . import privacy as P
        self.need_admin()
        ids = [int(x) for x in request.data.get('employee_ids') or [] if str(x).isdigit()]
        if not ids:
            return bad('Choose the employees.')
        E = apps.get_model('EmpManagement', 'emp_master')
        done, failed = [], []
        p = P.policy()
        for e in E.objects.filter(pk__in=ids):
            try:
                P.anonymise(e, request.user, p)
                done.append(e.emp_code)
            except ValueError as ex:
                failed.append(f'{e.emp_code}: {ex}')
        return Response({'anonymised': done, 'failed': failed})


class PrivacyExportView(Base):
    """GET <emp_id>/ (administrators) or me/ (the employee's own data) → a zip file."""
    def get(self, request, emp_id=None):
        from django.apps import apps
        from django.http import HttpResponse
        from . import privacy as P
        E = apps.get_model('EmpManagement', 'emp_master')
        if emp_id is None:
            e = E.objects.filter(users=request.user).order_by('-is_active', 'id').first()
            if e is None:
                return bad('Your login is not linked to an employee.', 404)
            mine = True
        else:
            self.need_admin()
            e = E.objects.filter(pk=emp_id).first()
            if e is None:
                return bad('Employee not found.', 404)
            mine = False
        content, name, n, f = P.export(e, request.user, self_service=mine)
        resp = HttpResponse(content, content_type='application/zip')
        resp['Content-Disposition'] = f'attachment; filename="{name}"'
        resp['X-Records'] = str(n)
        resp['X-Files'] = str(f)
        return resp


# ------------------------------------------------------------------ v1.53.0 test company (67)
def test_json(t):
    return {'id': t.pk, 'name': t.name, 'schema_name': t.schema_name, 'company_id': t.company_id, 'kinds': t.kinds, 'state': t.state,
            'state_label': t.get_state_display(), 'step': t.step, 'result': t.result, 'error': t.error, 'by': uname(t.by), 'at': t.at}


class TestCompanyView(Base):
    def get(self, request, pk=None):
        from .sandbox import KINDS
        self.need_admin()
        return Response({'kinds': [{'key': k, 'label': l} for k, l in KINDS],
                         'rows': [test_json(t) for t in M.TestCompany.objects.exclude(state='deleted').select_related('by')[:20]]})

    def post(self, request, pk=None):
        from UserManagement.models import company
        from .sandbox import start
        self.need_admin()
        if M.TestCompany.objects.filter(state='running').exists():
            return bad('A test company is being made – wait until it is ready.')
        try:
            from ZeoApps.models import CompanyOption
            if CompanyOption.objects.filter(key__in=('test_company_of', 'neutralized')).exists():
                return bad('This is a test company itself – make test companies from the real company.')
        except Exception:
            pass
        src = company.objects.filter(schema_name=connection.schema_name).first()
        try:
            t = start(src, request.user, request.data.get('kinds') or [], (request.data.get('name') or '').strip() or None)
        except ValueError as e:
            return bad(str(e))
        return Response(test_json(t), status=202)

    def delete(self, request, pk=None):
        from .sandbox import delete
        self.need_admin()
        t = M.TestCompany.objects.filter(pk=pk).first()
        if t is None:
            return bad('Not found.', 404)
        try:
            delete(t, request.user)
        except ValueError as e:
            return bad(str(e))
        return Response(status=204)


# ------------------------------------------------------------------ v1.56.0 setup guide (77)
class SetupGuideView(Base):
    """GET the steps with their progress; POST {action: opened} when the guide opened by itself the first time."""
    def get(self, request):
        from . import setup_guide as SG
        if not self.admin:
            return Response({'steps': [], 'can_use': False})
        out = SG.overview(full=request.query_params.get('full') == '1')
        out['can_use'] = True
        return Response(out)

    def post(self, request):
        self.need_admin()
        g = M.SetupGuide.get()
        if request.data.get('action') == 'opened' and not g.opened_at:
            g.opened_at = timezone.now()
            g.save(update_fields=['opened_at', 'updated_at'])
        return Response({'ok': True})


class SetupGuideStepView(Base):
    def get(self, request, key):
        from . import setup_guide as SG
        self.need_admin()
        try:
            return Response(SG.step(key))
        except SG.GuideError as e:
            return Response({'detail': str(e), 'problems': e.problems}, status=e.status)

    def post(self, request, key):
        from . import setup_guide as SG
        self.need_admin()
        origin = request.headers.get('Origin') or ''
        try:
            msg, st = SG.post(key, request.data or {}, request.user, base_url=origin)
        except SG.GuideError as e:
            return Response({'detail': str(e), 'problems': e.problems}, status=e.status)
        return Response({'message': msg, 'step': st, 'overview': SG.overview()})


class SetupGuideHomeView(Base):
    """The Home checklist after go-live (company administrators)."""
    def get(self, request):
        from . import setup_guide as SG
        if not self.admin:
            return Response({'items': [], 'live': False})
        g = M.SetupGuide.get()
        ov = SG.overview()
        from django.apps import apps as _apps
        new_company = not _apps.get_model('EmpManagement', 'emp_master').objects.exists()
        return Response({'items': SG.home_items(), 'live': bool(g.live_at), 'done': ov['done'], 'total': ov['total'],
                         'auto_open': not g.live_at and not g.opened_at and new_company,
                         # an existing company that never opened the guide is not reminded of it on Home
                         'show': bool(g.live_at or g.opened_at or new_company)})


class SetupGuideMatrixView(Base):
    def get(self, request):
        from . import category_matrix as CM
        self.need_admin()
        return Response({'rows': CM.rows(), 'options': CM.options(), 'exceptions': CM.exceptions()})

    def post(self, request):
        from . import category_matrix as CM
        self.need_admin()
        try:
            n = CM.save(request.data or {}, request.user)
        except CM.MatrixError as e:
            return bad(str(e))
        return Response({'changed': n, 'rows': CM.rows(), 'options': CM.options(), 'exceptions': CM.exceptions()})


class SetupGuideTemplateView(Base):
    def get(self, request, kind):
        from django.http import HttpResponse
        from . import guide_templates as GT
        self.need_admin()
        try:
            data = GT.download(kind)
        except GT.TemplateError as e:
            return bad(str(e), 404)
        r = HttpResponse(data, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        r['Content-Disposition'] = f'attachment; filename="zeo-setup-{kind}.xlsx"'
        return r

    def post(self, request, kind):
        from . import guide_templates as GT
        self.need_admin()
        f = request.FILES.get('file')
        if f is None:
            return bad(tr_('Choose the Excel file.'))
        if f.size > 10 * 1024 * 1024:
            return bad(tr_('The file is larger than 10 MB.'))
        try:
            return Response(GT.upload(kind, f, request.user))
        except GT.TemplateError as e:
            return bad(str(e))


def tr_(s, *a):
    from Translations.i18n import tr
    return tr(s, *a)


class SignInLinkView(APIView):
    """v1.56.0 – /users/sign-in-link/ (no login): GET ?t= who the link is for; POST {t, password} choose the password."""
    permission_classes = []
    authentication_classes = []

    def get(self, request):
        from .invites import check
        hit = check(request.query_params.get('t', ''))
        if hit is None:
            return bad(tr_('This link has expired or was already used – ask HR for a new one.'), 410)
        schema, inv, u = hit
        return Response({'username': u.username, 'expires_at': inv.expires_at})

    def post(self, request):
        from .invites import use
        pw = request.data.get('password') or ''
        if pw != (request.data.get('confirm') or pw):
            return bad(tr_('The two passwords are not the same.'))
        try:
            u = use(request.data.get('t', ''), pw)
        except ValueError as e:
            return bad(str(e))
        return Response({'ok': True, 'username': u.username})


class MediaCookieView(APIView):
    """v1.56.1 – /users/media-cookie/: the signed-in user gets the cookie that opens uploaded files (and its value as
    `token` for the phone app, which adds ?zm= to file addresses)."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from zeo import media_auth
        r = Response({'ok': True, 'max_age': media_auth.MAX_AGE})
        r.data['token'] = media_auth.set_cookie(r, request, request.user)
        return r

    get = post


# ------------------------------------------------------------------ v1.62.0 log and history cleaning (83.8)
class HistoryView(Base):
    """GET the categories (rows, MB, oldest, due now, legal holds), the periods and the last cleanings; PUT {days: {key: n},
    nightly}; POST {action: 'preview' | 'clean', categories: [...], confirm: <company name>}. Administrators (or the right
    "Can clean logs and history")."""
    def _need(self):
        if not (self.admin or 'clean_history' in self.c.codes):
            raise PermissionError(tr_('Company administrators only (or the right "Can clean logs and history").'))

    def get(self, request):
        from . import history as H
        self._need()
        out = H.overview()
        out['runs'] = [H.run_json(r) for r in M.HistoryRun.objects.select_related('by')[:30]]
        return Response(out)

    def put(self, request):
        from . import history as H
        self._need()
        p = H.policy()
        days = dict(p.days or {})
        test = H.is_test_company()
        try:
            for k, v in (request.data.get('days') or {}).items():
                days[k] = H.check_days(k, v, test)
        except (H.Refused, TypeError, ValueError) as e:
            return bad(tr_(str(e)) if isinstance(e, H.Refused) else tr_('Give whole numbers of days.'))
        p.days = days
        if 'nightly' in request.data:
            p.nightly = str(request.data['nightly']).lower() in ('1', 'true')
        p.updated_by = request.user
        p.save()
        return self.get(request)

    def post(self, request):
        from UserManagement.models import company
        from . import history as H
        self._need()
        keys = [k for k in (request.data.get('categories') or []) if k in H.CAT]
        if not keys:
            return bad(tr_('Choose the categories.'))
        act = request.data.get('action') or 'preview'
        if act == 'preview':
            p, hold = H.policy(), H.holds()
            return Response({'preview': [H.measure(k, p, hold) for k in keys]})
        if act != 'clean':
            return bad(tr_('Unknown action.'))
        if not self.admin:
            return bad(tr_('Only a company administrator cleans.'), 403)
        name = getattr(company.objects.filter(schema_name=connection.schema_name).first(), 'name', '')
        if ' '.join(str(request.data.get('confirm') or '').split()).lower() != ' '.join(name.split()).lower():
            return bad(tr_('Type the company name ({0}) to confirm.', name))
        run = H.clean(keys, 'manual', request.user)
        return Response({'run': H.run_json(run)})


class HistoryArchiveView(Base):
    def get(self, request, pk):
        import os
        from django.http import FileResponse
        self.need_admin()
        r = M.HistoryRun.objects.filter(pk=pk).first()
        if r is None or not r.archive or not os.path.isfile(r.archive):
            return bad(tr_('The archive file is not on the server (moved or deleted).'), 404)
        return FileResponse(open(r.archive, 'rb'), as_attachment=True, filename=os.path.basename(r.archive))


class MyHistoryView(APIView):
    """POST: clear my recently viewed records (the app clears its own search history in the browser)."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from . import history as H
        n = H.clear_my_recent(request.user)
        return Response({'cleared': n})


# ------------------------------------------------------------------ v1.62.0 bank-detail changes (83.6)
class BankChangesView(Base):
    def _may_see(self):
        return self.admin or bool({'approve_bank_change', 'change_employeebankdetail', 'view_employeebankdetail'} & self.c.codes)

    def get(self, request):
        from .bankguard import change_json, policy_on
        if not self._may_see():
            return bad(tr_('You may not see bank-detail changes.'), 403)
        decider = self.admin or 'approve_bank_change' in self.c.codes
        qs = M.BankChange.objects.select_related('requested_by', 'decided_by')
        st = request.query_params.get('state')
        if st:
            qs = qs.filter(state=st)
        rows = []
        for c in qs[:300]:
            j = change_json(c)
            if not decider:
                j.pop('old_full'), j.pop('new_full')
            j['can_decide'] = decider and c.state == 'pending' and c.requested_by_id != request.user.pk
            rows.append(j)
        return Response({'rows': rows, 'on': policy_on(), 'pending': M.BankChange.objects.filter(state='pending').count(), 'decider': decider})

    def post(self, request):
        from .bankguard import change_json, decide
        c = M.BankChange.objects.filter(pk=request.data.get('id')).first()
        if c is None:
            return bad(tr_('Change not found.'), 404)
        act = request.data.get('action')
        if act not in ('approve', 'reject'):
            return bad(tr_('Approve or reject.'))
        try:
            decide(c, request.user, act == 'approve', request.data.get('note', ''))
        except ValueError as e:
            return bad(tr_(str(e)), 403 if 'right' in str(e) or 'second person' in str(e) else 400)
        except Exception as e:
            return bad(tr_('The change could not be applied: {0}', e))
        return Response(change_json(c))


# ------------------------------------------------------------------ v1.62.0 give access (83.5)
class AccessView(Base):
    """GET ?filter=no_login|not_activated&q= – employees and their login; POST {employee_ids, method, password} – give access."""
    def _need(self):
        if not (self.admin or {'give_access', 'change_emp_master'} & self.c.codes):
            raise PermissionError(tr_('HR users with the right "Can give employees access" only.'))

    def get(self, request):
        from . import giveaccess as G
        self._need()
        p = G.policy()
        return Response({'rows': G.rows(request.query_params.get('filter', ''), (request.query_params.get('q') or '').strip()),
                         'messaging': G.messaging_on(), 'app_url': bool(G.app_url()),
                         'policy': {'signin_id': p.signin_id, 'link_hours': p.link_hours, 'hr_types_password': p.hr_types_password,
                                    'default_password_on': bool(p.default_password_on and p.default_password_hash),
                                    'default_password_days': p.default_password_days},
                         'grants': [{'code': g.emp_code, 'method': g.get_method_display(), 'detail': g.detail, 'by': uname(g.by), 'at': g.at}
                                    for g in M.AccessGrant.objects.select_related('by')[:100]]})

    def post(self, request):
        from . import giveaccess as G
        self._need()
        ids = [int(x) for x in (request.data.get('employee_ids') or []) if str(x).isdigit()]
        if not ids:
            return bad(tr_('Choose the employees.'))
        if len(ids) > 2000:
            return bad(tr_('At most 2,000 employees at a time.'))
        method = request.data.get('method') or 'auto'
        if method not in G.METHODS:
            return bad(tr_('Choose how the password is set.'))
        return Response(G.give_many(ids, method, request.user, str(request.data.get('password') or '')))


class AccessSlipsView(Base):
    """GET the PDF of the slips made by the last Give access call (the key it answered; valid 15 minutes, once)."""
    def get(self, request, key):
        from django.core.cache import cache
        from django.http import HttpResponse
        from . import giveaccess as G
        d = cache.get(f'zeo:slips:{key}')
        if not d or d.get('schema') != connection.schema_name or d.get('by') != request.user.pk:
            return bad(tr_('The slips are no longer available – give access again to print new ones.'), 404)
        cache.delete(f'zeo:slips:{key}')
        pdf = G.slip_pdf(d['slips'])
        r = HttpResponse(pdf, content_type='application/pdf')
        r['Content-Disposition'] = 'inline; filename="zeo-activation-slips.pdf"'
        r['Cache-Control'] = 'no-store'
        return r


class ExportLogView(Base):
    def get(self, request):
        self.need_admin()
        rows = [{'user': uname(e.user), 'path': e.path, 'kind': e.kind, 'rows': e.rows, 'bytes': e.bytes, 'refused': e.refused,
                 'watermarked': e.watermarked, 'ip': e.ip, 'at': e.at} for e in M.ExportLog.objects.select_related('user')[:500]]
        return Response({'rows': rows})


class ExportCheckView(Base):
    """v1.62.0 POST {screen, kind, rows} – the app asks before it makes an export file in the browser (lists, reports):
    the company's export right and row limit, written to the export log; answers the watermark text for PDFs."""
    def post(self, request):
        from django.utils import timezone as tz
        from zeo.exportguard import _policy
        from ZeoCloud.netguard import client_ip
        kind = str(request.data.get('kind') or '')[:10]
        try:
            rows = int(request.data.get('rows'))
        except (TypeError, ValueError):
            rows = None
        rows = None if rows is not None and rows < 0 else rows
        screen = str(request.data.get('screen') or '')[:300]
        pol = _policy(connection.schema_name)
        refused = ''
        if not self.admin:
            if pol['export_control'] and self.c.backoffice and 'export_lists' not in self.c.codes:
                refused = 'needs the right "Can export lists"'
            elif pol['export_row_limit'] and rows is not None and rows > pol['export_row_limit'] and 'export_large' not in self.c.codes:
                refused = f'{rows} rows – more than the limit of {pol["export_row_limit"]}'
        M.ExportLog.objects.create(user=request.user, path=screen, kind=kind, rows=rows, refused=refused, watermarked=bool(pol['export_watermark'] and kind == 'pdf'),
                                   ip=client_ip(request))
        if refused:
            try:
                from ZeoCloud.security import event
                event('export_refused', user=request.user, schema=connection.schema_name, ip=client_ip(request),
                      detail=f'Export of "{screen}" refused: {refused}.', notify='limit' in refused)
            except Exception:
                pass
            from Translations.i18n import tr
            msg = (tr('Exports need the right "Can export lists" – ask your administrator.') if 'right' in refused else
                   tr('This export has {0} rows – more than the limit of {1}. Filter the list, or ask for the right "Can export large lists".', rows, pol['export_row_limit']))
            return Response({'detail': msg, 'code': 'export_refused'}, status=403)
        u = request.user
        label = f'ZEO - {u.email or u.username} - {tz.localtime():%d %b %Y %H:%M} - {client_ip(request)}'
        return Response({'ok': True, 'watermark': bool(pol['export_watermark']), 'label': label})
