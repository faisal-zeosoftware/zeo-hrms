"""HSE API (/zeo-hse/api/..., ?schema=<company>). Rights: access.py. Rules: services.py."""
import json
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Count, Q
from django.http import FileResponse, Http404, HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from . import access as A
from . import links as L
from . import rows as RW
from . import services as S
from .models import (INDUSTRIES, ROLES, Action, CompetencyType, ApprovalRule, Area, Contractor, FormTemplate, GasTest, HazardLibrary, HSEFile,
                     HSESettings, Incident, IncidentCategory, Inspection, Isolation, Observation, Permit, PermitType, RiskAssessment,
                     RiskLine, Site, Term, ToolboxTalk)


def bad(msg, code=status.HTTP_400_BAD_REQUEST, field=None, data=None):
    body = {'detail': msg}
    if field:
        body[field] = [msg]
    if data:
        body.update(data)
    return Response(body, status=code)


def is_activated():
    return HSESettings.activated()


def guard(fn):
    def wrapper(self, request, *a, **k):
        try:
            if getattr(self, 'needs_activation', True) and not is_activated():
                raise S.HSEError('HSE is not activated yet. The HSE manager or an administrator activates it and chooses its industry.',
                                 status=409, data={'activated': False})
            return fn(self, request, *a, **k)
        except S.HSEError as e:
            return bad(e.message, e.status, e.field, e.data)
        except A.Denied as e:
            return bad(e.message, status.HTTP_403_FORBIDDEN)
        except Http404:
            return bad('Not found.', status.HTTP_404_NOT_FOUND)
    wrapper.__name__, wrapper.__doc__ = fn.__name__, fn.__doc__
    return wrapper


class Base(APIView):
    zeo_access = False   # rights are checked here (access.py)
    zeo_scope = False
    permission_classes = [A.CompanyMember]
    parser_classes = [JSONParser, MultiPartParser, FormParser]


class In:
    def __init__(self, request):
        data = request.data
        try:
            self.d = data.dict() if hasattr(data, 'dict') else dict(data)
        except Exception:
            self.d = {}
        self.files = request.FILES

    def has(self, k):
        return k in self.d

    def s(self, k, default=''):
        v = self.d.get(k, default)
        return '' if v is None else str(v).strip()

    def i(self, k, required=False, label=None):
        v = self.d.get(k)
        if v in (None, '', 'null'):
            if required:
                raise S.HSEError(f'{label or k.replace("_", " ").capitalize()} is required.', k)
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            raise S.HSEError(f'{label or k.replace("_", " ").capitalize()} must be a number.', k)

    def n(self, k, default=None):
        v = self.d.get(k)
        if v in (None, '', 'null'):
            return default
        try:
            return Decimal(str(v))
        except (InvalidOperation, ValueError):
            raise S.HSEError(f'{k.replace("_", " ").capitalize()} must be a number.', k)

    def dt(self, k, required=False, label=None):
        v = self.d.get(k)
        if v in (None, '', 'null'):
            if required:
                raise S.HSEError(f'{label or k.replace("_", " ").capitalize()} is required.', k)
            return None
        r = parse_date(str(v)[:10])
        if r is None:
            raise S.HSEError(f'{label or k} must be a date.', k)
        return r

    def dtm(self, k, required=False, label=None):
        v = self.d.get(k)
        if v in (None, '', 'null'):
            if required:
                raise S.HSEError(f'{label or k.replace("_", " ").capitalize()} is required.', k)
            return None
        r = parse_datetime(str(v))
        if r is None:
            dd = parse_date(str(v)[:10])
            r = datetime(dd.year, dd.month, dd.day) if dd else None
        if r is None:
            raise S.HSEError(f'{label or k} must be a date and time.', k)
        if timezone.is_naive(r):
            r = timezone.make_aware(r, timezone.get_current_timezone())
        return r

    def b(self, k, default=False):
        v = self.d.get(k)
        if v in (None, ''):
            return default
        if isinstance(v, bool):
            return v
        return str(v).lower() in ('1', 'true', 'yes', 'on')

    def j(self, k, default=None):
        v = self.d.get(k, default)
        if isinstance(v, str):
            try:
                return json.loads(v)
            except ValueError:
                raise S.HSEError(f'{k} is not valid.', k)
        return v

    def ids(self, k):
        v = self.j(k, [])
        if not isinstance(v, (list, tuple)):
            v = [v]
        try:
            return [int(x) for x in v if str(x).strip()]
        except (TypeError, ValueError):
            raise S.HSEError(f'{k} must be a list of ids.', k)

    def choice(self, k, choices, default=None):
        v = self.s(k)
        if not v:
            return default
        if v not in dict(choices):
            raise S.HSEError(f'"{v}" is not a valid {k.replace("_", " ")}.', k)
        return v

    def obj(self, k, model, required=False, label=None):
        pk = self.i(k, required, label)
        if pk is None:
            return None
        o = model.objects.filter(pk=pk).first()
        if o is None:
            raise S.HSEError(f'{label or k.replace("_", " ").capitalize()} not found.', k)
        return o

    def emp(self, k, required=False, label=None):
        from EmpManagement.models import emp_master
        return self.obj(k, emp_master, required, label)

    def filelist(self, k='files'):
        return self.files.getlist(k) if hasattr(self.files, 'getlist') else []


def get_or_404(model, pk, qs=None):
    o = (qs if qs is not None else model.objects).filter(pk=pk).first()
    if o is None:
        raise Http404
    return o


def page(request, qs, fn, default=100):
    q = request.query_params
    if str(q.get('branch') or '').isdigit():   # v1.22.0: Branch filter on every HSE list
        p = A.bpath(qs.model)
        if p:
            qs = qs.filter(**{p: int(q['branch'])})
    df = q.get('date_field')   # v1.22.0: dashboard figures open their records for the same period
    if df and df in {f.name for f in qs.model._meta.fields}:
        from django.db import models as DM
        suffix = '__date' if isinstance(qs.model._meta.get_field(df), DM.DateTimeField) else ''
        if parse_date(str(q.get('from') or '')):
            qs = qs.filter(**{f'{df}{suffix}__gte': parse_date(q['from'])})
        if parse_date(str(q.get('to') or '')):
            qs = qs.filter(**{f'{df}{suffix}__lte': parse_date(q['to'])})
    limit = max(1, min(1000, int(q.get('limit') or default)))
    offset = max(0, int(q.get('offset') or 0))
    return Response({'count': qs.count(), 'results': [fn(x) for x in qs[offset:offset + limit]]})


def ensure_pack():
    """v1.17.0: nothing is installed here any more – HSE is activated with its own industry choice (ActivateView)."""
    return HSESettings.get()


def company_industry():
    """(company master code, label, suggested HSE pack) of the current company."""
    from django.db import connection
    from UserManagement.models import company
    from .signals import industry_of
    t = company.objects.filter(schema_name=connection.schema_name).first()
    if t is None:
        return '', '', 'general'
    code = t.industry_type or ''
    return code, (t.get_industry_type_display() if code else ''), industry_of(t)


class ActivateView(APIView):
    """GET activate/ – activation state and the industry question; POST {industry} – activate HSE with that industry (once)."""
    zeo_access = False
    zeo_scope = False
    permission_classes = [A.CompanyMember]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    needs_activation = False

    @guard
    def get(self, request):
        from .packs import PACKS, summary
        s = HSESettings.get()
        code, label, suggested = company_industry()
        return Response(RW.jsonable({
            'activated': bool(s.activated_at), 'activated_at': s.activated_at, 'activated_by': S.user_name(s.activated_by),
            'industry': s.industry, 'industry_label': dict(INDUSTRIES)[s.industry], 'industry_source': s.industry_source,
            'company_industry': code, 'company_industry_label': label, 'suggested': suggested,
            'packs': [summary(c) for c in PACKS], 'can_activate': A.can_setup(request)}))

    @guard
    def post(self, request):
        A.need(A.can_setup(request), 'Only the HSE manager or an administrator can activate HSE.')
        from .packs import PACKS, install
        s = HSESettings.get()
        if s.activated_at:
            raise S.HSEError('HSE is already activated. Add another industry pack in Setup › Industry pack.')
        f = In(request)
        ind = f.s('industry') or 'general'
        if ind not in PACKS:
            raise S.HSEError('Choose the industry for HSE.', 'industry')
        with transaction.atomic():
            added = install(ind, request.user, primary=True)
            s = HSESettings.get()
            _, _, suggested = company_industry()
            s.industry = ind
            s.activated_at, s.activated_by = S.now(), request.user
            s.industry_source = 'company' if ind == suggested else 'chosen'
            s.save()
        S.event('settings', 0, f'HSE activated by {S.user_name(request.user)} – industry {dict(INDUSTRIES)[ind]}'
                + ('' if s.industry_source == 'company' else ' (different from the company master)') + f': {added}', request.user)
        r = self.get(request)
        r.data['added'] = added
        return r


def choices(c):
    return [{'value': k, 'label': v} for k, v in c]


# ------------------------------------------------------------------ lookups, settings, pack
class LookupsView(Base):
    needs_activation = False   # pages read it to show the activation step
    @guard
    def get(self, request):
        s = ensure_pack()
        from EmpManagement.models import emp_master
        staff = A.is_staff(request)
        pts = PermitType.objects.filter(is_active=True) if staff else S.usable(PermitType.objects.all())
        tpls = FormTemplate.objects.filter(is_active=True) if staff else S.usable(FormTemplate.objects.all())
        to_review = sum(m.objects.filter(review_status='to_review', is_active=True).count()
                        for m in (Term, FormTemplate, PermitType, ApprovalRule, HazardLibrary, IncidentCategory))
        return Response({
            'rights': A.rights(request), 'industry': s.industry, 'industry_label': dict(INDUSTRIES)[s.industry],
            'require_review': s.require_review, 'to_review': to_review,
            'terms': {t.key: t.label for t in Term.objects.filter(is_active=True)},
            'sites': [RW.site(x) for x in A.in_branches(Site.objects.filter(is_active=True), request).prefetch_related('areas').select_related('manager')],
            'contractors': [{'id': c.pk, 'name': c.name, 'status': c.status} for c in Contractor.objects.filter(is_active=True)],
            'permit_types': [{'id': p.pk, 'code': p.code, 'name': p.name, 'color': p.color, 'max_hours': p.max_hours,
                              'usable': (p.review_status == 'reviewed' or not s.require_review), 'requires_gas_test': p.requires_gas_test,
                              'requires_isolation': p.requires_isolation, 'requires_risk_assessment': p.requires_risk_assessment,
                              'requires_method_statement': p.requires_method_statement} for p in pts],
            'templates': [{'id': t.pk, 'kind': t.kind, 'name': t.name, 'code': t.code, 'frequency': t.frequency,
                           'usable': (t.review_status == 'reviewed' or not s.require_review)} for t in tpls],
            'categories': [{'id': c.pk, 'code': c.code, 'name': c.name, 'kind': c.kind, 'restricted': c.restricted}
                           for c in IncidentCategory.objects.filter(is_active=True)],
            'employees': [{'id': e.pk, 'name': S.emp_name(e), 'code': e.emp_code} for e in A.in_branches(emp_master.objects.filter(is_active=True), request, 'emp_branch_id').order_by('emp_first_name')[:3000]]
            if hasattr(emp_master, 'is_active') else [{'id': e.pk, 'name': S.emp_name(e), 'code': e.emp_code} for e in emp_master.objects.all()[:3000]],
            'matrix': {'likelihood': s.likelihood_labels, 'severity': s.severity_labels, 'bands': s.risk_bands},
            'roles': choices(ROLES), 'industries': choices(INDUSTRIES),
            'risk_kinds': choices(RiskAssessment.KINDS), 'permit_status': choices(Permit.STATUS), 'incident_status': choices(Incident.STATUS),
            'treatments': choices(Incident.TREATMENT), 'observation_kinds': choices(Observation.KINDS), 'action_status': choices(Action.STATUS),
            'priorities': choices(Action.PRIORITY), 'template_kinds': choices(FormTemplate.KINDS), 'isolation_kinds': choices(Isolation.KINDS),
            'contractor_status': choices(Contractor.STATUS), 'observation_categories': choices(Observation.CATEGORIES),
            'observed_risks': choices(Observation.RISK),
            'competency_types': [{'code': c.code, 'name': c.name} for c in CompetencyType.objects.filter(is_active=True)],
            'links': L.flags(),   # v1.17.0 – which other ZEO apps are linked
            'activated': bool(s.activated_at),
            'branches': branches(request),
        })


def branches(request=None):
    try:
        from OrganisationManager.models import brnch_mstr
        qs = brnch_mstr.objects.order_by('branch_name')
        b = A.branch_ids(request) if request is not None else None
        if b is not None:   # v1.22.0
            qs = qs.filter(pk__in=b)
        return [{'id': x.pk, 'name': x.branch_name} for x in qs]
    except Exception:
        return []


SETTINGS_TEXT = ('incident_prefix', 'permit_prefix', 'risk_prefix', 'action_prefix', 'inspection_prefix', 'observation_prefix', 'toolbox_prefix')


class SettingsView(Base):
    @guard
    def get(self, request):
        A.need(A.can_setup(request), 'Only the HSE manager can change HSE settings.')
        s = ensure_pack()
        r = {k: getattr(s, k) for k in SETTINGS_TEXT + ('rate_multiplier', 'hours_worked_month', 'require_review', 'action_reminder_days',
                                                      'block_expired_competency', 'significance_threshold', 'incompatible_groups',
                                                      'likelihood_labels', 'severity_labels', 'risk_bands', 'industry', 'packs_installed',
                                                      'fatigue_max_hours', 'links_off')}
        return Response(r)

    @guard
    def put(self, request):
        A.need(A.can_setup(request), 'Only the HSE manager can change HSE settings.')
        s, f = HSESettings.get(), In(request)
        for k in SETTINGS_TEXT:
            if f.has(k) and f.s(k):
                setattr(s, k, f.s(k)[:10])
        for k in ('block_expired_competency',):
            if f.has(k):
                setattr(s, k, f.b(k))
        if f.has('incompatible_groups'):
            s.incompatible_groups = f.j('incompatible_groups') or []
        if f.has('links_off'):
            s.links_off = [k for k in (f.j('links_off') or []) if k in L.LINKS]
        for k in ('rate_multiplier', 'hours_worked_month', 'action_reminder_days', 'significance_threshold', 'fatigue_max_hours'):
            if f.has(k):
                setattr(s, k, max(0, f.i(k) or 0))
        if f.has('require_review'):
            s.require_review = f.b('require_review')
        for k in ('likelihood_labels', 'severity_labels'):
            if f.has(k):
                v = f.j(k)
                if not isinstance(v, list) or len(v) != 5 or not all(str(x).strip() for x in v):
                    raise S.HSEError('Give 5 labels.', k)
                setattr(s, k, [str(x).strip()[:40] for x in v])
        if f.has('risk_bands'):
            bands = f.j('risk_bands')
            covered = set()
            for b in bands or []:
                lo, hi = int(b['min']), int(b['max'])
                if lo > hi or lo < 1 or hi > 25:
                    raise S.HSEError('Band ranges must be within 1–25.', 'risk_bands')
                covered |= set(range(lo, hi + 1))
            if covered != set(range(1, 26)):
                raise S.HSEError('The bands must cover every score from 1 to 25 exactly once.', 'risk_bands')
            s.risk_bands = bands
        s.save()
        return self.get(request)


class PackView(Base):
    """GET → industry, installed packs, review status per kind. POST {industry} → install that pack (adds only)."""

    @guard
    def get(self, request):
        from .packs import PACKS, summary
        s = ensure_pack()
        kinds = {'permit_types': PermitType, 'forms': FormTemplate, 'approvals': ApprovalRule, 'hazards': HazardLibrary,
                 'incident_categories': IncidentCategory, 'terms': Term}
        review = {k: {'to_review': m.objects.filter(review_status='to_review', is_active=True).count(),
                      'reviewed': m.objects.filter(review_status='reviewed', is_active=True).count()} for k, m in kinds.items()}
        code, clabel, suggested = company_industry()
        return Response({'industry': s.industry, 'industry_label': dict(INDUSTRIES)[s.industry], 'installed': s.packs_installed,
                         'industry_source': s.industry_source, 'company_industry_label': clabel, 'suggested': suggested,
                         'installed_at': RW.d(s.pack_installed_at), 'require_review': s.require_review, 'review': review,
                         'packs': [summary(c) for c in PACKS], 'can_setup': A.can_setup(request)})

    @guard
    def post(self, request):
        A.need(A.can_setup(request), 'Only the HSE manager can install industry packs.')
        from .packs import PACKS, install
        f = In(request)
        ind = f.s('industry')
        if ind not in PACKS:
            raise S.HSEError('Unknown industry.', 'industry')
        primary = f.b('primary')   # v1.17.0 – also make it HSE's own industry (add-only, nothing removed)
        added = install(ind, request.user, primary=primary)
        if primary:
            s = HSESettings.get()
            _, _, suggested = company_industry()
            s.industry, s.industry_source = ind, ('company' if ind == suggested else 'chosen')
            s.save()
        S.event('settings', 0, f'Industry pack "{ind}" installed by {S.user_name(request.user)}' + (' as HSE industry' if primary else '') + f': {added}', request.user)
        return Response({'added': added})


REVIEW_MODELS = {'permit_types': PermitType, 'forms': FormTemplate, 'approvals': ApprovalRule, 'hazards': HazardLibrary,
                 'incident_categories': IncidentCategory, 'terms': Term}


class ReviewView(Base):
    """POST {kind, ids: [...] | all: true, status: reviewed|to_review} – the HSE manager confirms pack content for this company."""

    @guard
    def post(self, request):
        A.need(A.is_manager(request), 'Only the HSE manager can review industry pack content.')
        f = In(request)
        m = REVIEW_MODELS.get(f.s('kind'))
        if m is None:
            raise S.HSEError('Unknown kind.', 'kind')
        st = f.choice('status', (('reviewed', ''), ('to_review', '')), 'reviewed')
        qs = m.objects.all() if f.b('all') else m.objects.filter(pk__in=f.ids('ids'))
        n = qs.update(review_status=st, reviewed_by=request.user if st == 'reviewed' else None, reviewed_at=S.now() if st == 'reviewed' else None)
        S.event('settings', 0, f'{n} {f.s("kind")} marked {st} by {S.user_name(request.user)}', request.user)
        return Response({'updated': n})


# ------------------------------------------------------------------ masters (configuration)
MASTER = {
    'sites': (Site, RW.site, ('code', 'name', 'kind', 'address'), ()),
    'contractors': (Contractor, RW.contractor, ('code', 'name', 'trade', 'contact_name', 'phone', 'email', 'trade_licence_no', 'notes'), ()),
    'templates': (FormTemplate, RW.template, ('code', 'name', 'description', 'frequency', 'kind'), ()),
    'permit-types': (PermitType, RW.permit_type, ('code', 'name', 'description', 'color'), ()),
    'approval-rules': (ApprovalRule, lambda x: {'id': x.pk, 'subject': x.subject, 'subject_label': x.get_subject_display(), 'key': x.key,
                                                'steps': x.steps, 'note': x.note, **RW.pack_fields(x)}, ('subject', 'key', 'note'), ()),
    'hazards': (HazardLibrary, lambda x: {'id': x.pk, 'category': x.category, 'activity': x.activity, 'hazard': x.hazard, 'consequence': x.consequence,
                                          'controls': x.controls, 'likelihood': x.likelihood, 'severity': x.severity, **RW.pack_fields(x)},
                ('category', 'activity', 'hazard', 'consequence', 'controls'), ()),
    'categories': (IncidentCategory, lambda x: {'id': x.pk, 'code': x.code, 'name': x.name, 'kind': x.kind, 'restricted': x.restricted, **RW.pack_fields(x)},
                   ('code', 'name', 'kind'), ()),
    'terms': (Term, lambda x: {'id': x.pk, 'key': x.key, 'label': x.label, **RW.pack_fields(x)}, ('key', 'label'), ()),
}


def save_master(kind, obj, f, request):
    model, row, texts, _ = MASTER[kind]
    for k in texts:
        if f.has(k):
            setattr(obj, k, f.s(k))
    if f.has('is_active'):
        obj.is_active = f.b('is_active')
    if kind == 'sites':
        if f.has('manager'):
            obj.manager = f.emp('manager')
        if f.has('branch'):
            from OrganisationManager.models import brnch_mstr
            obj.branch = f.obj('branch', brnch_mstr)
        if f.has('project_id'):
            obj.project_id = L.check_id('projects', f.i('project_id'), 'project_id')
        if f.has('cost_center_id'):
            obj.cost_center_id = L.check_id('cost-centers', f.i('cost_center_id'), 'cost_center_id')
        if not obj.code or not obj.name:
            raise S.HSEError('Code and name are required.', 'name')
    if kind == 'contractors':
        for k in ('licence_expiry', 'insurance_expiry'):
            if f.has(k):
                setattr(obj, k, f.dt(k))
        if f.has('status'):
            A.need(A.has_role(request, 'hse_manager') or A.has_role(request, 'hse_officer'), 'Only HSE can approve contractors.')
            obj.status = f.choice('status', Contractor.STATUS, obj.status)
        if not obj.code:
            obj.code = 'CON-' + str(Contractor.objects.count() + 1).zfill(4)
        if not obj.name:
            raise S.HSEError('Name is required.', 'name')
    if kind == 'templates':
        if f.has('questions'):
            qs = f.j('questions') or []
            keys = set()
            for q in qs:
                if not str(q.get('text', '')).strip():
                    raise S.HSEError('Every question needs a text.', 'questions')
                q['key'] = q.get('key') or ('q' + str(len(keys) + 1))
                if q['key'] in keys:
                    q['key'] = q['key'] + '_' + str(len(keys))
                keys.add(q['key'])
                q['type'] = q.get('type') if q.get('type') in ('yesno', 'text', 'number', 'date') else 'yesno'
            obj.questions = qs
            if obj.pk:
                obj.version += 1
        if obj.kind not in dict(FormTemplate.KINDS):
            raise S.HSEError('Choose the form type.', 'kind')
        if not obj.code or not obj.name:
            raise S.HSEError('Code and name are required.', 'name')
    if kind == 'permit-types':
        for k in ('requires_risk_assessment', 'requires_method_statement', 'requires_gas_test', 'requires_isolation', 'requires_contractor_approval'):
            if f.has(k):
                setattr(obj, k, f.b(k))
        for k in ('max_hours', 'gas_retest_hours'):
            if f.has(k):
                setattr(obj, k, max(0, f.i(k) or 0))
        if obj.max_hours < 1:
            raise S.HSEError('The permit must be valid for at least 1 hour.', 'max_hours')
        if f.has('checklist'):
            obj.checklist = f.obj('checklist', FormTemplate)
        for k in ('gas_limits', 'approval_steps', 'simops_conflicts', 'required_competencies'):
            if f.has(k):
                setattr(obj, k, f.j(k) or ([] if k != 'gas_limits' else {}))
        for s in obj.approval_steps or []:
            if s.get('role') not in A.ROLE_KEYS:
                raise S.HSEError(f'Unknown role "{s.get("role")}" in the approval steps.', 'approval_steps')
        if not obj.code or not obj.name:
            raise S.HSEError('Code and name are required.', 'name')
    if kind == 'approval-rules':
        if f.has('steps'):
            obj.steps = f.j('steps') or []
        for s in obj.steps or []:
            if s.get('role') not in A.ROLE_KEYS:
                raise S.HSEError(f'Unknown role "{s.get("role")}".', 'steps')
    if kind == 'hazards':
        for k in ('likelihood', 'severity'):
            if f.has(k):
                v = f.i(k) or 1
                if not 1 <= v <= 5:
                    raise S.HSEError('1 to 5.', k)
                setattr(obj, k, v)
    if kind == 'categories' and f.has('restricted'):
        obj.restricted = f.b('restricted')
    if kind in ('templates', 'permit-types', 'approval-rules', 'hazards', 'categories', 'terms') and not obj.pk:
        obj.review_status, obj.reviewed_by, obj.reviewed_at = 'reviewed', request.user, S.now()   # made by the company itself
    obj.save()
    return obj


class MasterListView(Base):
    @guard
    def get(self, request, kind):
        if kind not in MASTER:
            raise Http404
        ensure_pack()
        model, row, _, _ = MASTER[kind]
        A.need(A.can_view_all(request) or kind in ('sites', 'templates'), 'Only HSE can see this list.')   # v1.22.0
        qs = A.in_branches(model.objects.all(), request) if kind == 'sites' else model.objects.all()
        if request.query_params.get('active') == '1':
            qs = qs.filter(is_active=True)
        if kind == 'templates' and request.query_params.get('kind'):
            qs = qs.filter(kind=request.query_params['kind'])
        if kind == 'sites':
            qs = qs.prefetch_related('areas').select_related('manager')
        return Response([row(x) for x in qs])

    @guard
    def post(self, request, kind):
        if kind not in MASTER:
            raise Http404
        A.need(A.can_setup(request) or (kind == 'contractors' and A.is_staff(request)), 'Only the HSE manager can change the set-up.')
        model = MASTER[kind][0]
        with transaction.atomic():
            o = save_master(kind, model(), In(request), request)
        return Response(MASTER[kind][1](o), status=status.HTTP_201_CREATED)


class MasterDetailView(Base):
    @guard
    def get(self, request, kind, pk):
        if kind not in MASTER:
            raise Http404
        A.need(A.can_view_all(request) or kind in ('sites', 'templates'), 'Only HSE can see this record.')   # v1.22.0
        return Response(MASTER[kind][1](get_or_404(MASTER[kind][0], pk)))

    @guard
    def patch(self, request, kind, pk):
        if kind not in MASTER:
            raise Http404
        A.need(A.can_setup(request) or (kind == 'contractors' and A.is_staff(request)), 'Only the HSE manager can change the set-up.')
        o = get_or_404(MASTER[kind][0], pk)
        with transaction.atomic():
            o = save_master(kind, o, In(request), request)
        return Response(MASTER[kind][1](o))

    put = patch


class AreaView(Base):
    """POST {site, name} / PATCH areas/<id>/ {name, is_active}."""

    @guard
    def post(self, request):
        A.need(A.can_setup(request), 'Only the HSE manager can change the set-up.')
        f = In(request)
        site = f.obj('site', Site, True)
        if not f.s('name'):
            raise S.HSEError('Name is required.', 'name')
        a, _ = Area.objects.get_or_create(site=site, name=f.s('name')[:150])
        return Response(RW.site(site), status=status.HTTP_201_CREATED)

    @guard
    def patch(self, request, pk):
        A.need(A.can_setup(request), 'Only the HSE manager can change the set-up.')
        a = get_or_404(Area, pk)
        f = In(request)
        if f.has('name') and f.s('name'):
            a.name = f.s('name')[:150]
        if f.has('is_active'):
            a.is_active = f.b('is_active')
        a.save()
        return Response(RW.site(a.site))


# ------------------------------------------------------------------ risk assessments
@A.branch_scoped   # v1.22.0
def risk_scope(request):
    qs = RiskAssessment.objects.select_related('site', 'area', 'owner')
    if A.is_staff(request):
        return qs
    me = A.my_emp(request)
    return qs.filter(Q(status='approved') | Q(created_by=request.user) | Q(owner=me))


def save_risk_lines(ra, lines):
    if not isinstance(lines, list):
        raise S.HSEError('Lines must be a list.', 'lines')
    ra.lines.all().delete()
    from EmpManagement.models import emp_master
    for i, l in enumerate(lines, 1):
        if not str(l.get('hazard', '')).strip():
            raise S.HSEError(f'Line {i}: the hazard is required.', 'lines')
        vals = {}
        for k in ('likelihood', 'severity', 'residual_likelihood', 'residual_severity'):
            v = int(l.get(k) or 0)
            if not 1 <= v <= 5:
                raise S.HSEError(f'Line {i}: {k.replace("_", " ")} must be 1–5.', 'lines')
            vals[k] = v
        if vals['residual_likelihood'] * vals['residual_severity'] > vals['likelihood'] * vals['severity']:
            raise S.HSEError(f'Line {i}: the residual risk cannot be higher than the initial risk.', 'lines')
        RiskLine.objects.create(assessment=ra, sequence=i, step=str(l.get('step', ''))[:255], hazard=str(l['hazard'])[:255],
                                consequence=str(l.get('consequence', ''))[:255], existing_controls=l.get('existing_controls', '') or '',
                                additional_controls=l.get('additional_controls', '') or '',
                                action_owner=emp_master.objects.filter(pk=l.get('action_owner')).first() if l.get('action_owner') else None, **vals)


class RiskListView(Base):
    zeo_model = RiskAssessment   # v1.22.0: extra fields (form designer) and record panel for this screen
    @guard
    def get(self, request):
        q = request.query_params
        qs = risk_scope(request)
        if q.get('status'):
            qs = qs.filter(status=q['status'])
        if q.get('site'):
            qs = qs.filter(site_id=q['site'])
        if q.get('q'):
            qs = qs.filter(Q(title__icontains=q['q']) | Q(number__icontains=q['q']))
        return page(request, qs.prefetch_related('lines'), RW.risk)

    @guard
    def post(self, request):
        f = In(request)
        if not f.s('title'):
            raise S.HSEError('Title is required.', 'title')
        with transaction.atomic():
            ra = RiskAssessment.objects.create(number=S.next_number('risk'), kind=f.choice('kind', RiskAssessment.KINDS, 'tra'),
                                               title=f.s('title')[:255], site=f.obj('site', Site), area=f.obj('area', Area),
                                               activity=f.s('activity'), team=f.s('team')[:255], owner=f.emp('owner') or A.my_emp(request),
                                               review_date=f.dt('review_date'), created_by=request.user)
            if f.has('lines'):
                save_risk_lines(ra, f.j('lines'))
            S.event('risk', ra.pk, f'Created by {S.user_name(request.user)}', request.user)
        return Response(RW.risk(ra, True), status=status.HTTP_201_CREATED)


class RiskDetailView(Base):
    zeo_model = RiskAssessment   # v1.22.0: record panel (notes, activities, files, extra fields)
    @guard
    def get(self, request, pk):
        return Response(RW.risk(get_or_404(RiskAssessment, pk, risk_scope(request)), True))

    @guard
    def patch(self, request, pk):
        ra = get_or_404(RiskAssessment, pk, risk_scope(request))
        A.need(ra.created_by_id == request.user.pk or A.is_staff(request), 'Only the author or HSE can change it.')
        if ra.status not in ('draft', 'rejected'):
            raise S.HSEError('An approved or submitted risk assessment cannot be changed. Use "Revise" to make a new version.')
        f = In(request)
        with transaction.atomic():
            for k in ('title', 'activity', 'team'):
                if f.has(k):
                    setattr(ra, k, f.s(k))
            if f.has('kind'):
                ra.kind = f.choice('kind', RiskAssessment.KINDS, ra.kind)
            if f.has('site'):
                ra.site = f.obj('site', Site)
            if f.has('area'):
                ra.area = f.obj('area', Area)
            if f.has('owner'):
                ra.owner = f.emp('owner')
            if f.has('review_date'):
                ra.review_date = f.dt('review_date')
            ra.save()
            if f.has('lines'):
                save_risk_lines(ra, f.j('lines'))
        return Response(RW.risk(ra, True))


class RiskActionView(Base):
    """POST risks/<id>/<submit|approve|reject|revise>/"""

    @guard
    def post(self, request, pk, action):
        ra = get_or_404(RiskAssessment, pk, risk_scope(request))
        f = In(request)
        if action == 'submit':
            A.need(ra.created_by_id == request.user.pk or A.is_staff(request), 'Only the author or HSE can submit it.')
            S.submit_risk(ra, request)
        elif action in ('approve', 'reject'):
            S.decide_risk(ra, request, action == 'approve', f.s('note'))
        elif action == 'revise':
            if ra.status != 'approved':
                raise S.HSEError('Only an approved risk assessment can be revised.')
            if RiskAssessment.objects.filter(previous=ra).exclude(status__in=('rejected',)).exists():
                raise S.HSEError('A revision of this risk assessment is already in progress.')
            with transaction.atomic():
                new = RiskAssessment.objects.create(number=S.next_number('risk'), kind=ra.kind, title=ra.title, site=ra.site, area=ra.area,
                                                    activity=ra.activity, team=ra.team, owner=ra.owner, created_by=request.user, previous=ra)
                for l in ra.lines.all():
                    l.pk = None
                    l.assessment = new
                    l.save()
                S.event('risk', new.pk, f'Revision of {ra.number} ({f.s("reason") or "review"})', request.user)
                S.event('risk', ra.pk, f'Revision started: {new.number}', request.user)
            return Response(RW.risk(new, True), status=status.HTTP_201_CREATED)
        else:
            raise Http404
        return Response(RW.risk(RiskAssessment.objects.get(pk=ra.pk), True))


class HazardPickView(Base):
    @guard
    def get(self, request):
        q = request.query_params.get('q', '')
        qs = HazardLibrary.objects.filter(is_active=True)
        if q:
            qs = qs.filter(Q(activity__icontains=q) | Q(hazard__icontains=q) | Q(category__icontains=q))
        return Response([MASTER['hazards'][1](x) for x in qs[:100]])


# ------------------------------------------------------------------ permits
@A.branch_scoped   # v1.22.0
def permit_scope(request, board=False):
    qs = Permit.objects.select_related('permit_type', 'site', 'area', 'contractor', 'applicant', 'receiver', 'risk_assessment',
                                       'permit_type__checklist')
    if A.is_staff(request) or board:
        return qs
    me = A.my_emp(request)
    return qs.filter(Q(applicant=request.user) | Q(receiver=me) | Q(status__in=('active', 'suspended')))


class PermitListView(Base):
    zeo_model = Permit   # v1.22.0: extra fields (form designer) and record panel for this screen
    @guard
    def get(self, request):
        q = request.query_params
        qs = permit_scope(request)
        if q.get('view') == 'live':
            qs = qs.filter(status__in=('active', 'suspended', 'approved'))
        elif q.get('view') == 'mine':
            qs = qs.filter(applicant=request.user)
        elif q.get('view') == 'waiting':
            qs = qs.filter(status='submitted')
        if q.get('status'):
            qs = qs.filter(status=q['status'])
        if q.get('site'):
            qs = qs.filter(site_id=q['site'])
        if q.get('type'):
            qs = qs.filter(permit_type_id=q['type'])
        if q.get('q'):
            qs = qs.filter(Q(number__icontains=q['q']) | Q(description__icontains=q['q']) | Q(location__icontains=q['q']))
        return page(request, qs.order_by('-valid_from'), RW.permit)

    @guard
    def post(self, request):
        f = In(request)
        pt = f.obj('permit_type', PermitType, True, 'Permit type')
        if S.settings().require_review and pt.review_status != 'reviewed':
            raise S.HSEError('This permit type comes from the industry pack and has not been reviewed yet.', 'permit_type')
        site = f.obj('site', Site, True, 'Site')
        start, end = f.dtm('valid_from', True, 'Start'), f.dtm('valid_to', True, 'End')
        if end <= start:
            raise S.HSEError('The end is before the start.', 'valid_to')
        if not f.s('description'):
            raise S.HSEError('Describe the work.', 'description')
        area = f.obj('area', Area)
        if area and area.site_id != site.pk:
            raise S.HSEError('The area is not on this site.', 'area')
        ra = f.obj('risk_assessment', RiskAssessment)
        with transaction.atomic():
            p = Permit.objects.create(number=S.next_number('permit'), permit_type=pt, site=site, area=area, location=f.s('location')[:255],
                                      description=f.s('description'), equipment=f.s('equipment')[:255], contractor=f.obj('contractor', Contractor),
                                      applicant=request.user, receiver=f.emp('receiver'), crew_size=max(1, f.i('crew_size') or 1),
                                      valid_from=start, valid_to=end, risk_assessment=ra, method_statement=f.s('method_statement'),
                                      precautions=f.s('precautions'), asset_id=L.check_id('assets', f.i('asset_id'), 'asset_id'))
            if f.has('crew'):
                from EmpManagement.models import emp_master
                p.crew.set(emp_master.objects.filter(pk__in=f.ids('crew')))
            S.event('permit', p.pk, f'Requested by {S.user_name(request.user)}', request.user)
        return Response(RW.permit(p, True, request), status=status.HTTP_201_CREATED)


class PermitDetailView(Base):
    zeo_model = Permit   # v1.22.0: record panel (notes, activities, files, extra fields)
    @guard
    def get(self, request, pk):
        return Response(RW.jsonable(RW.permit(get_or_404(Permit, pk, permit_scope(request)), True, request)))

    @guard
    def patch(self, request, pk):
        p = get_or_404(Permit, pk, permit_scope(request))
        f = In(request)
        mine = p.applicant_id == request.user.pk or A.is_staff(request)
        if f.has('checklist_answers') or f.has('team_briefed'):
            A.need(mine or A.has_role(request, 'supervisor') or A.has_role(request, 'permit_issuer'), 'You cannot fill this checklist.')
            if p.status in ('closed', 'cancelled', 'expired', 'active'):
                raise S.HSEError('The checklist is locked once the permit is issued.')
            if f.has('checklist_answers'):
                ans = f.j('checklist_answers') or {}
                p.checklist_answers = {**(p.checklist_answers or {}), **{k: {'value': str((v or {}).get('value', ''))[:200], 'note': str((v or {}).get('note', ''))[:255],
                                                                               'by': S.user_name(request.user)} for k, v in ans.items()}}
            if f.has('team_briefed'):
                p.team_briefed = f.b('team_briefed')
        edit_keys = {'description', 'location', 'equipment', 'contractor', 'receiver', 'crew_size', 'crew', 'valid_from', 'valid_to', 'risk_assessment',
                     'method_statement', 'precautions', 'area', 'asset_id'}
        if edit_keys & set(f.d):
            A.need(mine, 'Only the applicant or HSE can change the permit.')
            if p.status not in ('draft', 'rejected'):
                raise S.HSEError('Only a draft or rejected permit can be changed – a change of scope needs a new approval.')
            for k in ('description', 'location', 'equipment', 'method_statement', 'precautions'):
                if f.has(k):
                    setattr(p, k, f.s(k))
            if f.has('contractor'):
                p.contractor = f.obj('contractor', Contractor)
            if f.has('receiver'):
                p.receiver = f.emp('receiver')
            if f.has('crew_size'):
                p.crew_size = max(1, f.i('crew_size') or 1)
            if f.has('risk_assessment'):
                p.risk_assessment = f.obj('risk_assessment', RiskAssessment)
            if f.has('area'):
                p.area = f.obj('area', Area)
            if f.has('asset_id'):
                p.asset_id = L.check_id('assets', f.i('asset_id'), 'asset_id')
            for k in ('valid_from', 'valid_to'):
                if f.has(k):
                    setattr(p, k, f.dtm(k, True))
            if f.has('crew'):
                from EmpManagement.models import emp_master
                p.crew.set(emp_master.objects.filter(pk__in=f.ids('crew')))
        p.save()
        return Response(RW.jsonable(RW.permit(p, True, request)))


class PermitActionView(Base):
    """POST permits/<id>/<submit|approve|reject|issue|suspend|close|cancel>/"""

    @guard
    def post(self, request, pk, action):
        p = get_or_404(Permit, pk, permit_scope(request))
        f = In(request)
        if action == 'submit':
            A.need(p.applicant_id == request.user.pk or A.is_staff(request), 'Only the applicant can submit.')
            S.submit_permit(p, request)
        elif action in ('approve', 'reject'):
            S.decide_permit(p, request, action == 'approve', f.s('note'))
        elif action == 'issue':
            S.issue_permit(p, request)
        elif action == 'suspend':
            S.suspend_permit(p, request, f.s('reason'))
        elif action == 'close':
            S.close_permit(p, request, f.s('note'), f.j('checks') or {})
        elif action == 'cancel':
            S.cancel_permit(p, request, f.s('reason'))
        else:
            raise Http404
        return Response(RW.jsonable(RW.permit(Permit.objects.get(pk=p.pk), True, request)))


class GasTestView(Base):
    @guard
    def post(self, request, pk):
        p = get_or_404(Permit, pk, permit_scope(request))
        f = In(request)
        data = {k: f.n(k) for k in ('o2', 'lel', 'h2s', 'co')}
        data.update({'tested_at': f.dtm('tested_at'), 'instrument': f.s('instrument'), 'note': f.s('note')})
        if all(data[k] is None for k in ('o2', 'lel', 'h2s', 'co')):
            raise S.HSEError('Enter the readings.', 'o2')
        S.add_gas_test(p, request, data)
        return Response(RW.jsonable(RW.permit(Permit.objects.get(pk=p.pk), True, request)), status=status.HTTP_201_CREATED)


class IsolationView(Base):
    """POST permits/<id>/isolations/ {point, kind, method, lock_no}; PATCH isolations/<iid>/ {action: verify|remove}."""

    @guard
    def post(self, request, pk):
        p = get_or_404(Permit, pk, permit_scope(request))
        A.need(any(A.has_role(request, k) for k in ('area_authority', 'permit_issuer', 'supervisor')), 'Only an authorised person can record isolations.')
        if p.status in ('closed', 'cancelled', 'expired'):
            raise S.HSEError('The permit is finished.')
        f = In(request)
        if not f.s('point'):
            raise S.HSEError('Isolation point is required.', 'point')
        Isolation.objects.create(permit=p, point=f.s('point')[:150], kind=f.choice('kind', Isolation.KINDS, 'electrical'), method=f.s('method')[:150],
                                 lock_no=f.s('lock_no')[:40], isolated_by=request.user, isolated_at=S.now())
        S.event('permit', p.pk, f'Isolation {f.s("point")} applied by {S.user_name(request.user)}', request.user)
        return Response(RW.jsonable(RW.permit(p, True, request)), status=status.HTTP_201_CREATED)

    @guard
    def patch(self, request, iid):
        i = get_or_404(Isolation, iid)
        p = get_or_404(Permit, i.permit_id, permit_scope(request))
        A.need(any(A.has_role(request, k) for k in ('area_authority', 'permit_issuer', 'supervisor')), 'Only an authorised person can do this.')
        act = In(request).s('action')
        if act == 'verify':
            if i.isolated_by_id == request.user.pk and not A.is_admin(request):
                raise S.HSEError('A second person must verify the isolation.')
            i.verified_by, i.verified_at = request.user, S.now()
            S.event('permit', p.pk, f'Isolation {i.point} verified by {S.user_name(request.user)}', request.user)
        elif act == 'remove':
            if p.status == 'active':
                raise S.HSEError('Isolations cannot be removed while work is in progress – suspend or close the permit first.')
            i.removed_at = S.now()
            S.event('permit', p.pk, f'Isolation {i.point} removed by {S.user_name(request.user)}', request.user)
        else:
            raise S.HSEError('Unknown action.')
        i.save()
        return Response(RW.jsonable(RW.permit(p, True, request)))


class PermitPdfView(Base):
    """Printable permit with QR code to the live permit."""

    @guard
    def get(self, request, pk):
        p = get_or_404(Permit, pk, permit_scope(request))
        from .pdf import permit_pdf
        resp = HttpResponse(permit_pdf(p, request), content_type='application/pdf')
        resp['Content-Disposition'] = f'attachment; filename="{p.number}.pdf"'
        return resp


# ------------------------------------------------------------------ incidents
@A.branch_scoped   # v1.22.0
def incident_scope(request):
    qs = Incident.objects.select_related('category', 'site', 'area', 'reported_by', 'investigator', 'injured_employee', 'permit', 'closed_by')
    if A.is_staff(request):
        return qs
    me = A.my_emp(request)
    return qs.filter(Q(reported_by=request.user) | Q(investigator=me))


class IncidentListView(Base):
    zeo_model = Incident   # v1.22.0: extra fields (form designer) and record panel for this screen
    @guard
    def get(self, request):
        q = request.query_params
        qs = incident_scope(request)
        if q.get('status') == 'open':
            qs = qs.exclude(status='closed')
        elif q.get('status'):
            qs = qs.filter(status=q['status'])
        if q.get('site'):
            qs = qs.filter(site_id=q['site'])
        if q.get('hipo') == '1':
            qs = qs.filter(high_potential=True)
        if q.get('q'):
            qs = qs.filter(Q(title__icontains=q['q']) | Q(number__icontains=q['q']))
        return page(request, qs, lambda x: RW.incident(x, False, request))

    @guard
    def post(self, request):
        """Anyone in the company reports an incident or near miss."""
        f = In(request)
        if not f.s('title') or not f.s('description'):
            raise S.HSEError('Say what happened (title and description).', 'title')
        occ = f.dtm('occurred_at', True, 'Date and time it happened')
        if occ > S.now() + timedelta(minutes=5):
            raise S.HSEError('The time it happened is in the future.', 'occurred_at')
        with transaction.atomic():
            i = Incident.objects.create(number=S.next_number('incident'), category=f.obj('category', IncidentCategory), title=f.s('title')[:255],
                                        description=f.s('description'), occurred_at=occ, reported_by=request.user, site=f.obj('site', Site),
                                        area=f.obj('area', Area), location=f.s('location')[:255], injured_employee=f.emp('injured_employee'),
                                        injured_name=f.s('injured_name')[:150], body_part=f.s('body_part')[:100],
                                        treatment=f.choice('treatment', Incident.TREATMENT, 'none'), immediate_actions=f.s('immediate_actions'),
                                        witnesses=f.s('witnesses'), permit=f.obj('permit', Permit), contractor=f.obj('contractor', Contractor),
                                        potential_severity=min(5, max(1, f.i('potential_severity') or 1)), risk_assessment=f.obj('risk_assessment', RiskAssessment),
                                        actual_severity=min(5, max(1, f.i('actual_severity') or 1)),
                                        asset_id=L.check_id('assets', f.i('asset_id'), 'asset_id'))
            i.high_potential = i.potential_severity >= 4
            i.save()
            for x in f.filelist():
                HSEFile.objects.create(model='incident', object_id=i.pk, file=x, name=x.name[:255], size=x.size or 0, uploaded_by=request.user)
            S.event('incident', i.pk, f'Reported by {S.user_name(request.user)}', request.user)
        sev = 'HIGH POTENTIAL ' if i.high_potential else ''
        S.tell_users(S.users_with_role('hse_officer') + (S.users_with_role('hse_manager') if i.high_potential or i.treatment in ('lost_time', 'fatality') else []),
                     f'{sev}Incident reported: {i.number} {i.title}', f'{i.title}\n{i.description[:1500]}\nSite: {i.site.name if i.site_id else "-"}',
                     'incident', i.pk, i.number, S.PAGE + f'incidents/{i.pk}', f'Classify incident {i.number}')
        L.incident_serious(i)
        L.link_leave(i)
        return Response(RW.jsonable(RW.incident(Incident.objects.get(pk=i.pk), True, request)), status=status.HTTP_201_CREATED)


class IncidentDetailView(Base):
    zeo_model = Incident   # v1.22.0: record panel (notes, activities, files, extra fields)
    @guard
    def get(self, request, pk):
        return Response(RW.jsonable(RW.incident(get_or_404(Incident, pk, incident_scope(request)), True, request)))

    @guard
    def patch(self, request, pk):
        """Investigation fields (HSE / investigator). Closed records are corrected with a logged reason only."""
        i = get_or_404(Incident, pk, incident_scope(request))
        me = A.my_emp(request)
        A.need(A.is_staff(request) or (me and i.investigator_id == me.pk), 'Only HSE or the investigator can update the investigation.')
        f = In(request)
        if i.status == 'closed':
            A.need(A.is_manager(request), 'A closed incident can be corrected only by the HSE manager.')
            if not f.s('correction_reason'):
                raise S.HSEError('Give the reason for correcting a closed record.', 'correction_reason')
        changed = []
        for k in ('findings', 'lessons_learned', 'witnesses', 'immediate_actions', 'location', 'body_part', 'authority_ref'):
            if f.has(k) and getattr(i, k) != f.s(k):
                setattr(i, k, f.s(k))
                changed.append(k)
        if f.has('root_cause'):
            i.root_cause = f.j('root_cause') or {}
            changed.append('root cause')
        for k in ('lost_days', 'restricted_days'):
            if f.has(k):
                setattr(i, k, max(0, f.i(k) or 0))
                changed.append(k)
        if f.has('estimated_loss'):
            i.estimated_loss = f.n('estimated_loss', Decimal(0))
        if f.has('asset_id') and i.asset_id != (f.i('asset_id') or None):
            i.asset_id = L.check_id('assets', f.i('asset_id'), 'asset_id')
            changed.append('equipment')
        if f.has('lost_days') and i.leave_ids:
            changed.append('lost days (typed over the leave count)')
        i.save()
        if changed:
            S.event('incident', i.pk, ('Correction: ' + f.s('correction_reason') + ' – ' if i.status == 'closed' else 'Updated: ') + ', '.join(changed), request.user)
        return Response(RW.jsonable(RW.incident(i, True, request)))


class IncidentActionView(Base):
    """POST incidents/<id>/<classify|close|approve|reject>/"""

    @guard
    def post(self, request, pk, action):
        i = get_or_404(Incident, pk, incident_scope(request))
        f = In(request)
        if action == 'classify':
            data = {}
            for k in ('actual_severity', 'potential_severity', 'lost_days', 'restricted_days'):
                if f.has(k):
                    data[k] = f.i(k)
            if f.has('treatment'):
                data['treatment'] = f.choice('treatment', Incident.TREATMENT, i.treatment)
            if f.has('reportable'):
                data['reportable'] = f.b('reportable')
            if f.has('authority_ref'):
                data['authority_ref'] = f.s('authority_ref')
            if f.has('report_due'):
                data['report_due'] = f.dt('report_due')
            if f.has('category'):
                data['category_id'] = f.i('category')
            if f.has('investigator'):
                data['investigator_id'] = f.i('investigator')
            S.classify_incident(i, request, data)
        elif action == 'close':
            S.start_incident_closure(i, request)
        elif action in ('approve', 'reject'):
            S.decide_incident_closure(i, request, action == 'approve', f.s('note'))
        elif action == 'asset-damage':     # v1.17.0 links
            A.need(A.is_staff(request), 'Only HSE can report asset damage from an incident.')
            L.report_asset_damage(i, request, f.s('severity') or 'minor', f.n('repair_cost', Decimal(0)), f.s('note'))
        elif action == 'safety-alert':
            A.need(A.has_role(request, 'hse_manager') or A.has_role(request, 'hse_officer'), 'Only HSE publishes safety alerts.')
            L.publish_alert(i, request, f.s('title'), f.s('message'))
        elif action == 'link-leave':
            A.need(A.is_staff(request), 'Only HSE can do this.')
            if not L.on('leave'):
                raise S.HSEError('Leave is not linked.')
            if not L.link_leave(i, notify=False):
                raise S.HSEError('No approved sick or injury leave of the injured employee was found within 7 days of the incident.')
        else:
            raise Http404
        return Response(RW.jsonable(RW.incident(Incident.objects.get(pk=i.pk), True, request)))


# ------------------------------------------------------------------ inspections, observations, toolbox talks
@A.branch_scoped   # v1.22.0
def staff_or_own(request, qs, field):
    if A.is_staff(request):
        return qs
    me = A.my_emp(request)
    return qs.filter(**{field: me}) if field.endswith(('inspector', 'presenter')) else qs.filter(**{field: request.user})


class InspectionListView(Base):
    zeo_model = Inspection   # v1.22.0: extra fields (form designer) and record panel for this screen
    @guard
    def get(self, request):
        q = request.query_params
        qs = staff_or_own(request, Inspection.objects.select_related('template', 'site', 'area', 'inspector'), 'inspector')
        if q.get('status'):
            qs = qs.filter(status=q['status'])
        if q.get('site'):
            qs = qs.filter(site_id=q['site'])
        return page(request, qs, RW.inspection)

    @guard
    def post(self, request):
        f = In(request)
        tpl = f.obj('template', FormTemplate, True, 'Checklist')
        if S.settings().require_review and tpl.review_status != 'reviewed':
            raise S.HSEError('This checklist comes from the industry pack and has not been reviewed yet.', 'template')
        x = Inspection.objects.create(number=S.next_number('inspection'), template=tpl, site=f.obj('site', Site), area=f.obj('area', Area),
                                      subject=f.s('subject')[:200], inspector=f.emp('inspector') or A.my_emp(request), date=f.dt('date') or S.today(),
                                      created_by=request.user, asset_id=L.check_id('assets', f.i('asset_id'), 'asset_id'))
        S.event('inspection', x.pk, f'Started by {S.user_name(request.user)}', request.user)
        return Response(RW.inspection(x, True), status=status.HTTP_201_CREATED)


class InspectionDetailView(Base):
    zeo_model = Inspection   # v1.22.0: record panel (notes, activities, files, extra fields)
    @guard
    def get(self, request, pk):
        qs = staff_or_own(request, Inspection.objects.all(), 'inspector')   # v1.22.0: only inspections the user may see
        return Response(RW.jsonable(RW.inspection(get_or_404(Inspection, pk, qs), True)))

    @guard
    def patch(self, request, pk):
        x = get_or_404(Inspection, pk)
        me = A.my_emp(request)
        A.need(A.is_staff(request) or (me and x.inspector_id == me.pk), 'Only the inspector or HSE can fill it.')
        if x.status == 'completed':
            raise S.HSEError('A completed inspection cannot be changed.')
        f = In(request)
        if f.has('answers'):
            ans = f.j('answers') or {}
            x.answers = {**(x.answers or {}), **{k: {'value': str((v or {}).get('value', ''))[:500], 'note': str((v or {}).get('note', ''))[:500]}
                                                 for k, v in ans.items()}}
        for k in ('subject',):
            if f.has(k):
                setattr(x, k, f.s(k))
        x.save()
        if f.b('complete'):
            S.complete_inspection(x, request)
        return Response(RW.jsonable(RW.inspection(x, True)))


class ObservationListView(Base):
    zeo_model = Observation   # v1.22.0: extra fields (form designer) and record panel for this screen
    @guard
    def get(self, request):
        q = request.query_params
        qs = staff_or_own(request, Observation.objects.select_related('site', 'area', 'reported_by'), 'reported_by')
        if q.get('status'):
            qs = qs.filter(status=q['status'])
        if q.get('kind'):
            qs = qs.filter(kind=q['kind'])
        if q.get('category'):
            qs = qs.filter(category=q['category'])
        return page(request, qs, lambda o: RW.observation(o, request))

    @guard
    def post(self, request):
        f = In(request)
        if not f.s('description'):
            raise S.HSEError('Describe what you saw.', 'description')
        o = Observation.objects.create(number=S.next_number('observation'), kind=f.choice('kind', Observation.KINDS, 'unsafe_condition'),
                                       description=f.s('description'), site=f.obj('site', Site), area=f.obj('area', Area),
                                       location=f.s('location')[:255], immediate_action=f.s('immediate_action'), reported_by=request.user,
                                       category=f.choice('category', Observation.CATEGORIES, 'other'), task=f.s('task')[:200],
                                       observed_risk=f.choice('observed_risk', Observation.RISK, 'low'), confidential=f.b('confidential'))
        for x in f.filelist():
            HSEFile.objects.create(model='observation', object_id=o.pk, file=x, name=x.name[:255], size=x.size or 0, uploaded_by=request.user)
        S.event('observation', o.pk, f'Reported by {S.user_name(request.user)}', request.user)
        if o.observed_risk == 'high':
            S.tell_users(S.users_with_role('hse_manager'), f'SERIOUS HAZARD reported: {o.number}', o.description, 'observation', o.pk, o.number,
                         S.PAGE + 'observations', f'Serious hazard {o.number} – escalate?')
        if o.kind in ('unsafe_act', 'unsafe_condition', 'near_miss'):
            S.tell_users(S.users_with_role('hse_officer'), f'Observation {o.number}: {o.get_kind_display()}', o.description, 'observation', o.pk,
                         o.number, S.PAGE + 'observations', f'Review observation {o.number}')
        return Response(RW.observation(o, request), status=status.HTTP_201_CREATED)


class ObservationDetailView(Base):
    zeo_model = Observation   # v1.22.0: record panel (notes, activities, files, extra fields)
    @guard
    def patch(self, request, pk):
        A.need(A.is_staff(request), 'Only HSE can update observations.')
        o = get_or_404(Observation, pk)
        f = In(request)
        if f.has('status'):
            o.status = f.choice('status', Observation.STATUS, o.status)
            S.close_todos('observation', o.pk)
        o.save()
        S.event('observation', o.pk, f'Status {o.get_status_display()}', request.user)
        return Response(RW.observation(o, request))


class ObservationEscalateView(Base):
    """Serious hazard → incident workflow (near miss)."""

    @guard
    def post(self, request, pk):
        from .modules import observation_escalate
        o = get_or_404(Observation, pk)
        i = observation_escalate(o, request, In(request))
        return Response({'observation': RW.observation(o, request), 'incident': {'id': i.pk, 'number': i.number}})


class ObservationTrendView(Base):
    """BBS trends by category, kind, site and month – never by person."""

    @guard
    def get(self, request):
        A.need(A.can_view_all(request), 'Only HSE sees observation trends.')
        q = request.query_params
        qs = Observation.objects.all()
        if q.get('from'):
            qs = qs.filter(reported_at__date__gte=q['from'])
        if q.get('to'):
            qs = qs.filter(reported_at__date__lte=q['to'])
        cats, kinds = dict(Observation.CATEGORIES), dict(Observation.KINDS)
        from django.db.models.functions import TruncMonth
        return Response({
            'by_category': [{'label': cats.get(x['category'], x['category']), 'n': x['n']} for x in qs.values('category').annotate(n=Count('id')).order_by('-n')],
            'by_kind': [{'label': kinds.get(x['kind'], x['kind']), 'n': x['n']} for x in qs.values('kind').annotate(n=Count('id')).order_by('-n')],
            'by_site': [{'label': x['site__name'] or '-', 'n': x['n']} for x in qs.values('site__name').annotate(n=Count('id')).order_by('-n')],
            'by_month': [{'label': x['m'].strftime('%b %Y'), 'n': x['n']} for x in qs.annotate(m=TruncMonth('reported_at')).values('m').annotate(n=Count('id')).order_by('m')],
            'safe_share': round(qs.filter(kind='safe_act').count() * 100 / qs.count(), 1) if qs.count() else None,
            'total': qs.count(),
        })


class ToolboxListView(Base):
    zeo_model = ToolboxTalk   # v1.22.0: extra fields (form designer) and record panel for this screen
    @guard
    def get(self, request):
        qs = staff_or_own(request, ToolboxTalk.objects.select_related('site', 'presenter').prefetch_related('attendees'), 'presenter')
        return page(request, qs, RW.talk)

    @guard
    def post(self, request):
        f = In(request)
        tpl = f.obj('template', FormTemplate)
        topic = f.s('topic') or (tpl.name if tpl else '')
        if not topic:
            raise S.HSEError('Topic is required.', 'topic')
        from EmpManagement.models import emp_master
        t = ToolboxTalk.objects.create(number=S.next_number('toolbox'), topic=topic[:200], template=tpl, site=f.obj('site', Site),
                                       date=f.dt('date') or S.today(), presenter=f.emp('presenter') or A.my_emp(request),
                                       other_attendees=f.s('other_attendees'), key_points=f.s('key_points'), created_by=request.user)
        t.attendees.set(emp_master.objects.filter(pk__in=f.ids('attendees')))
        return Response(RW.talk(t, True), status=status.HTTP_201_CREATED)


# ------------------------------------------------------------------ actions (CAPA)
@A.branch_scoped   # v1.22.0
def action_scope(request):
    qs = Action.objects.select_related('owner', 'verifier', 'site')
    if A.is_staff(request):
        return qs
    me = A.my_emp(request)
    return qs.filter(Q(owner=me) | Q(verifier=me) | Q(created_by=request.user))


SOURCE_MODEL = {'incident': Incident, 'inspection': Inspection, 'permit': Permit, 'risk': RiskAssessment, 'observation': Observation}


class ActionListView(Base):
    zeo_model = Action   # v1.22.0: extra fields (form designer) and record panel for this screen
    @guard
    def get(self, request):
        q = request.query_params
        qs = action_scope(request)
        v = q.get('view')
        if v == 'open':
            qs = qs.filter(status__in=('open', 'in_progress', 'rejected'))
        elif v == 'overdue':
            qs = qs.filter(status__in=('open', 'in_progress', 'rejected'), due_date__lt=S.today())
        elif v == 'verify':
            qs = qs.filter(status='done')
        elif v == 'mine':
            qs = qs.filter(owner=A.my_emp(request))
        if q.get('source'):
            qs = qs.filter(source=q['source'], source_id=q.get('source_id') or None)
        return page(request, qs, RW.action)

    @guard
    def post(self, request):
        f = In(request)
        A.need(A.is_staff(request) or A.has_role(request, 'site_manager'), 'Only HSE or managers raise actions.')
        src = f.choice('source', Action.SOURCES, 'manual')
        sid = f.i('source_id')
        ref = ''
        site = f.obj('site', Site)
        if src != 'manual':
            o = get_or_404(SOURCE_MODEL[src], sid)
            ref = o.number
            site = site or getattr(o, 'site', None)
            if src == 'observation' and o.status == 'open':
                o.status = 'actioned'
                o.save(update_fields=['status'])
        a = S.create_action(source=src, source_id=sid, source_ref=ref, title=f.s('title'), description=f.s('description'),
                            owner=f.emp('owner'), due_date=f.dt('due_date'), priority=f.choice('priority', Action.PRIORITY, 'medium'),
                            site=site, verifier=f.emp('verifier'), user=request.user, kind=f.s('kind') or 'corrective')
        return Response(RW.action(a, True), status=status.HTTP_201_CREATED)


class ActionDetailView(Base):
    zeo_model = Action   # v1.22.0: record panel (notes, activities, files, extra fields)
    @guard
    def get(self, request, pk):
        return Response(RW.action(get_or_404(Action, pk, action_scope(request)), True))

    @guard
    def patch(self, request, pk):
        a = get_or_404(Action, pk, action_scope(request))
        A.need(A.is_staff(request), 'Only HSE can change the action.')
        f = In(request)
        changes = []
        if f.has('owner'):
            a.owner = f.emp('owner', True)
            changes.append('owner')
        if f.has('due_date'):
            nd = f.dt('due_date', True)
            if nd != a.due_date:
                if not f.s('reason'):
                    raise S.HSEError('Give the reason for moving the due date.', 'reason')
                changes.append(f'due date {a.due_date} → {nd} ({f.s("reason")})')
                a.due_date = nd
        if f.has('verifier'):
            a.verifier = f.emp('verifier')
            changes.append('verifier')
        if f.has('priority'):
            a.priority = f.choice('priority', Action.PRIORITY, a.priority)
        if f.has('status') and f.s('status') == 'cancelled':
            if not f.s('reason'):
                raise S.HSEError('Give the reason for cancelling.', 'reason')
            a.status = 'cancelled'
            changes.append(f'cancelled ({f.s("reason")})')
            S.close_todos('action', a.pk)
        a.save()
        if changes:
            S.event('action', a.pk, 'Changed: ' + '; '.join(changes), request.user)
        return Response(RW.action(a, True))


class ActionStepView(Base):
    """POST actions/<id>/<start|done|verify>/"""

    @guard
    def post(self, request, pk, step):
        a = get_or_404(Action, pk, action_scope(request))
        f = In(request)
        if step == 'start':
            if a.status in ('open', 'rejected'):
                a.status = 'in_progress'
                a.save()
                S.event('action', a.pk, 'Started', request.user)
        elif step == 'done':
            S.action_done(a, request, f.s('evidence'))
        elif step == 'verify':
            S.action_verify(a, request, f.b('effective'), f.s('note'))
        else:
            raise Http404
        return Response(RW.action(Action.objects.get(pk=a.pk), True))


# ------------------------------------------------------------------ files
FILE_MODELS = {'incident': (Incident, incident_scope), 'permit': (Permit, permit_scope), 'risk': (RiskAssessment, risk_scope),
               'inspection': (Inspection, lambda r: staff_or_own(r, Inspection.objects.all(), 'inspector')), 'action': (Action, action_scope),
               'observation': (Observation, lambda r: staff_or_own(r, Observation.objects.all(), 'reported_by')),
               'toolbox': (ToolboxTalk, lambda r: staff_or_own(r, ToolboxTalk.objects.all(), 'presenter'))}   # v1.22.0: every file needs its record's rights


class FileListView(Base):
    """POST files/?model=permit&id=5 (multipart files[], note)."""

    @guard
    def post(self, request):
        m, oid = request.query_params.get('model'), request.query_params.get('id')
        if m not in FILE_MODELS:
            raise Http404
        model, scope = FILE_MODELS[m]
        get_or_404(model, oid, scope(request) if scope else None)
        f = In(request)
        xs = f.filelist()
        if not xs:
            raise S.HSEError('Choose a file.', 'files')
        for x in xs:
            if (x.size or 0) > 20 * 1024 * 1024:
                raise S.HSEError(f'{x.name} is larger than 20 MB.', 'files')
        for x in xs:
            HSEFile.objects.create(model=m, object_id=int(oid), file=x, name=x.name[:255], size=x.size or 0, note=f.s('note')[:255], uploaded_by=request.user)
        S.event(m, int(oid), f'{len(xs)} file(s) added: ' + ', '.join(x.name for x in xs), request.user)
        return Response(RW.files(m, int(oid)), status=status.HTTP_201_CREATED)


class FileView(Base):
    @guard
    def get(self, request, pk):
        x = get_or_404(HSEFile, pk)
        if x.model in FILE_MODELS:
            model, scope = FILE_MODELS[x.model]
            get_or_404(model, x.object_id, scope(request) if scope else None)
        else:
            from .registers import file_allowed
            known, ok = file_allowed(request, x)
            A.need(known and ok, 'You cannot open this file.')
        return FileResponse(x.file.open('rb'), as_attachment=True, filename=x.name)


# ------------------------------------------------------------------ dashboard
class DashboardView(Base):
    @guard
    def get(self, request):
        ensure_pack()
        A.need(A.can_view_all(request), 'The HSE figures are for HSE staff and management.')   # v1.22.0
        q = request.query_params
        if str(q.get('branch') or '').isdigit():   # v1.22.0 Branch picked on the dashboard
            allowed = A.branch_ids(request)
            request._zeo_dash_branch = int(q['branch']) if allowed is None or int(q['branch']) in allowed else -1
        t = S.today()
        to = parse_date(q.get('to') or '') or t
        frm = parse_date(q.get('from') or '') or to.replace(day=1)
        s = S.settings()
        inc = A.in_branches(Incident.objects.all(), request).filter(occurred_at__date__gte=frm, occurred_at__date__lte=to)
        site_obj = None
        if q.get('site'):
            inc = inc.filter(site_id=q['site'])
            site_obj = Site.objects.filter(pk=q['site']).first()
        project_sites = None
        if q.get('project') and L.on('projects'):
            project_sites = L.project_site_ids(int(q['project']))
            inc = inc.filter(site_id__in=project_sites)
        recordable = inc.filter(treatment__in=('medical', 'restricted', 'lost_time', 'fatality')).count()
        lti = inc.filter(treatment__in=('lost_time', 'fatality')).count()
        if project_sites is not None and site_obj is None:
            hours, hours_source = L.project_hours(frm, to, project_sites)
        else:
            hours, hours_source = L.hours_worked(frm, to, site_obj)   # attendance when linked, else the monthly hours in settings
        rate = lambda n: round(n * s.rate_multiplier / hours, 2) if hours else None
        acts = A.in_branches(Action.objects.all(), request).all()
        open_acts = acts.filter(status__in=('open', 'in_progress', 'rejected'))
        due = acts.filter(due_date__gte=frm, due_date__lte=to)
        closed_on_time = sum(1 for a in due.filter(status='closed') if a.done_at and a.done_at.date() <= a.due_date)
        scores = [float(x) for x in A.in_branches(Inspection.objects.all(), request).filter(date__range=(frm, to), status='completed', score__isnull=False).values_list('score', flat=True)]
        by_cat = list(inc.values('category__name').annotate(n=Count('id')).order_by('-n'))
        weeks = []
        start = frm - timedelta(days=frm.weekday())
        while start <= to and len(weeks) < 26:
            end = start + timedelta(days=6)
            weeks.append({'week': start.isoformat(), 'incidents': A.in_branches(Incident.objects.all(), request).filter(occurred_at__date__range=(start, end)).count(),
                          'observations': A.in_branches(Observation.objects.all(), request).filter(reported_at__date__range=(start, end)).count()})
            start += timedelta(days=7)
        return Response(RW.jsonable({
            'from': frm, 'to': to, 'industry': s.industry, 'industry_label': dict(INDUSTRIES)[s.industry],
            'days_since_lti': (lambda x: (t - timezone.localtime(x.occurred_at).date()).days if x else None)(
                A.in_branches(Incident.objects.all(), request).filter(treatment__in=('lost_time', 'fatality')).order_by('-occurred_at').first()),
            'incidents': inc.count(), 'near_misses': inc.filter(category__kind='near_miss').count(), 'recordable': recordable, 'lti': lti,
            'lost_days': sum(inc.values_list('lost_days', flat=True)), 'high_potential': inc.filter(high_potential=True).count(),
            'open_incidents': A.in_branches(Incident.objects.all(), request).exclude(status='closed').count(),
            'trir': rate(recordable), 'ltifr': rate(lti), 'hours': hours, 'hours_source': hours_source, 'multiplier': s.rate_multiplier,
            'project': int(q['project']) if project_sites is not None else None,
            'live_permits': A.in_branches(Permit.objects.all(), request).filter(status='active').count(), 'suspended_permits': A.in_branches(Permit.objects.all(), request).filter(status='suspended').count(),
            'waiting_permits': A.in_branches(Permit.objects.all(), request).filter(status='submitted').count(),
            'open_actions': open_acts.count(), 'overdue_actions': open_acts.filter(due_date__lt=t).count(),
            'to_verify': acts.filter(status='done').count(),
            'action_closure_pct': round(closed_on_time * 100 / due.count(), 1) if due.count() else None,
            'inspections': A.in_branches(Inspection.objects.all(), request).filter(date__range=(frm, to), status='completed').count(),
            'avg_score': round(sum(scores) / len(scores), 1) if scores else None,
            'observations': A.in_branches(Observation.objects.all(), request).filter(reported_at__date__range=(frm, to)).count(),
            'toolbox_talks': A.in_branches(ToolboxTalk.objects.all(), request).filter(date__range=(frm, to)).count(),
            'risk_reviews_due': A.in_branches(RiskAssessment.objects.all(), request).filter(status='approved', review_date__lt=t).count(),
            'by_category': [{'label': x['category__name'] or 'Not classified', 'n': x['n']} for x in by_cat],
            'weekly': weeks,
            'recent_incidents': [RW.incident(i, False, request) for i in incident_scope(request)[:8]],
            'live': [RW.permit(p) for p in A.in_branches(Permit.objects.all(), request).filter(status__in=('active', 'suspended')).select_related('permit_type', 'site', 'area', 'contractor', 'applicant', 'receiver')[:20]],
            'overdue': [RW.action(a) for a in open_acts.filter(due_date__lt=t).select_related('owner', 'verifier', 'site')[:10]],
            'rights': A.rights(request),
        }))


class MyHSEView(Base):
    """What waits for me: approvals by my roles, my actions, my permits, my incidents to investigate."""

    @guard
    def get(self, request):
        from .models import ApprovalStep
        mine_roles = A.roles(request)
        pend = {}
        for st in ApprovalStep.objects.filter(status='pending', role__in=mine_roles).order_by('model', 'object_id', 'sequence'):
            key = (st.model, st.object_id)
            if key not in pend:
                first = ApprovalStep.objects.filter(model=st.model, object_id=st.object_id, status='pending').order_by('sequence').first()
                if first and first.pk == st.pk:
                    pend[key] = st
        approvals = []
        for (m, oid), st in pend.items():
            from .models import Audit, ChangeRequest, Claim
            mdl = {'permit': Permit, 'risk': RiskAssessment, 'incident': Incident, 'moc': ChangeRequest, 'audit': Audit, 'claim': Claim}.get(m)
            o = mdl.objects.filter(pk=oid).first() if mdl else None
            if o:
                approvals.append({'model': m, 'id': oid, 'number': o.number, 'title': getattr(o, 'title', '') or getattr(o, 'description', '')[:80] or getattr(o, 'insurer', ''),
                                  'step': st.label})
        me = A.my_emp(request)
        from .models import Chemical, HealthSurveillance, HSEDocument, Obligation
        other = []
        if me is not None:
            for x in HSEDocument.objects.filter(status='review', approver=me):
                other.append({'tag': 'Approve', 'kind': 'documents', 'id': x.pk, 'number': x.number, 'title': x.title})
            for x in HSEDocument.objects.filter(status='published', requires_ack=True, restricted=False).exclude(acks__employee=me)[:20]:
                other.append({'tag': 'Read', 'kind': 'documents', 'id': x.pk, 'number': x.number, 'title': x.title})
            for x in Obligation.objects.filter(reviewer=me, assessments__state='pending').distinct():
                other.append({'tag': 'Approve', 'kind': 'obligations', 'id': x.pk, 'number': x.ref, 'title': x.title})
            for x in Obligation.objects.filter(owner=me, is_active=True, due_date__lte=S.today() + timedelta(days=14)).exclude(status='na'):
                other.append({'tag': 'Assess', 'kind': 'obligations', 'id': x.pk, 'number': x.ref, 'title': x.title})
        if A.has_role(request, 'hse_officer') or A.has_role(request, 'hse_manager'):
            for x in Chemical.objects.filter(status='review'):
                other.append({'tag': 'Review', 'kind': 'chemicals', 'id': x.pk, 'number': x.number, 'title': x.name})
        if A.has_role(request, 'occ_health'):
            for x in HealthSurveillance.objects.filter(completed_on__isnull=True, due_date__lte=S.today() + timedelta(days=14)).select_related('employee')[:30]:
                other.append({'tag': 'Health', 'kind': 'health', 'id': x.pk, 'number': x.number, 'title': f'{S.emp_name(x.employee)} – {x.get_assessment_type_display()} due {x.due_date}'})
        if A.is_manager(request) and L.on('handover'):
            from .models import Handover
            for h in Handover.objects.filter(status='open').select_related('employee')[:30]:
                other.append({'tag': 'Handover', 'kind': 'handover', 'id': h.pk, 'number': h.employee.emp_code, 'route': ['links', 'handovers'],
                              'title': f'Reassign the HSE work of {S.emp_name(h.employee)} ({h.get_reason_display().lower()})'})
        return Response(RW.jsonable({
            'other': other,
            'approvals': approvals,
            'to_issue': [RW.permit(p) for p in Permit.objects.filter(status='approved').select_related('permit_type', 'site', 'area', 'contractor', 'applicant', 'receiver')]
            if A.has_role(request, 'permit_issuer') else [],
            'actions': [RW.action(a) for a in Action.objects.filter(owner=me, status__in=('open', 'in_progress', 'rejected')).select_related('owner', 'verifier', 'site')] if me else [],
            'verify': [RW.action(a) for a in Action.objects.filter(status='done').filter(Q(verifier=me) | Q(verifier__isnull=True) if A.is_staff(request) else Q(verifier=me)).select_related('owner', 'verifier', 'site')] if me or A.is_staff(request) else [],
            'permits': [RW.permit(p) for p in Permit.objects.filter(applicant=request.user).exclude(status__in=('closed', 'cancelled', 'expired')).select_related('permit_type', 'site', 'area', 'contractor', 'applicant', 'receiver')],
            'investigations': [RW.incident(i, False, request) for i in Incident.objects.filter(investigator=me).exclude(status='closed').select_related('category', 'site', 'area', 'reported_by')] if me else [],
            'rights': A.rights(request),
        }))



# ------------------------------------------------------------------ v1.17.0 links with the other ZEO apps
class LinksView(Base):
    """GET links/ – which links are possible and on; PUT links/ {off: [keys], fatigue_max_hours} (HSE manager)."""

    @guard
    def get(self, request):
        A.need(A.is_staff(request) or A.can_setup(request), 'Only HSE can see this.')
        rows, extra = L.status()
        s = S.settings()
        return Response({'links': rows, 'asset_plus': extra['asset_plus'], 'zeo_projects': extra['zeo_projects'], 'fatigue_max_hours': s.fatigue_max_hours,
                         'can_change': A.can_setup(request)})

    @guard
    def put(self, request):
        A.need(A.can_setup(request), 'Only the HSE manager can change the links.')
        f, s = In(request), HSESettings.get()
        if f.has('off'):
            s.links_off = [k for k in (f.j('off') or []) if k in L.LINKS]
        if f.has('fatigue_max_hours'):
            s.fatigue_max_hours = max(0, min(24, f.i('fatigue_max_hours') or 0))
        s.save()
        L.forget_tables()
        done = L.daily() if f.b('sync') else {}
        r = self.get(request)
        r.data['synced'] = done
        return r


class MusterView(Base):
    @guard
    def get(self, request):
        A.need(A.is_staff(request) or A.has_role(request, 'site_manager'), 'Only HSE and site managers can open the muster list.')
        site = get_or_404(Site, request.query_params['site']) if request.query_params.get('site') else None
        return Response(L.muster(site))


class HandoverView(Base):
    """GET links/handovers/?status=open|all; POST links/handovers/<id>/ {to_employee, note} or {nothing: true}."""

    @guard
    def get(self, request, pk=None):
        A.need(A.is_manager(request) or A.is_admin(request), 'Only the HSE manager handles handovers.')
        from .models import Handover
        qs = Handover.objects.select_related('employee', 'done_by')
        if request.query_params.get('status', 'open') == 'open':
            qs = qs.filter(status='open')
        return Response({'on': L.on('handover'), 'results': [L.handover_row(h) for h in qs[:200]]})

    @guard
    def post(self, request, pk):
        A.need(A.is_manager(request) or A.is_admin(request), 'Only the HSE manager handles handovers.')
        from .models import Handover
        h = get_or_404(Handover, pk)
        f = In(request)
        if f.b('nothing'):
            if h.status != 'open':
                raise S.HSEError('Already done.')
            h.status, h.done_by, h.done_at, h.note = 'done', request.user, S.now(), ('Closed without reassignment. ' + f.s('note'))[:4000]
            h.save()
            S.close_todos('handover', h.pk)
        else:
            L.reassign(h, f.emp('to_employee'), request, f.s('note'))
        return Response(L.handover_row(Handover.objects.get(pk=h.pk)))


class CostReportView(Base):
    @guard
    def get(self, request):
        A.need(A.is_manager(request) or A.has_role(request, 'claims_officer') or A.has_role(request, 'senior_mgmt') or A.is_admin(request),
               'Only the HSE manager, claims officer and senior management see HSE costs.')
        q = request.query_params
        t = S.today()
        to = parse_date(q.get('to') or '') or t
        frm = parse_date(q.get('from') or '') or t.replace(month=1, day=1)
        return Response(L.cost_report(frm, to))


class ProjectHSEView(Base):
    """GET project/<id>/ – the HSE tab inside a project (Projects app). 404 when Projects is not linked."""

    @guard
    def get(self, request, pid):
        if not L.on('projects'):
            raise Http404
        A.need(L.can_see_project(request, pid), 'You are not on this project.')
        return Response(RW.jsonable(L.project_summary(pid, request)))


# ------------------------------------------------------------------ v1.22.0 record panel (Chatter): who may open which HSE record
try:
    from Chatter.views import register_scope as _rs
    _rs(RiskAssessment, lambda request, qs: qs.filter(pk__in=risk_scope(request).values('pk')))
    _rs(Permit, lambda request, qs: qs.filter(pk__in=permit_scope(request).values('pk')))
    _rs(Incident, lambda request, qs: qs.filter(pk__in=incident_scope(request).values('pk')))
    _rs(Inspection, lambda request, qs: qs.filter(pk__in=staff_or_own(request, Inspection.objects.all(), 'inspector').values('pk')))
    _rs(Observation, lambda request, qs: qs.filter(pk__in=staff_or_own(request, Observation.objects.all(), 'reported_by').values('pk')))
    _rs(ToolboxTalk, lambda request, qs: qs.filter(pk__in=staff_or_own(request, ToolboxTalk.objects.all(), 'presenter').values('pk')))
    _rs(Action, lambda request, qs: qs.filter(pk__in=action_scope(request).values('pk')))
except Exception:   # Chatter not installed
    pass
