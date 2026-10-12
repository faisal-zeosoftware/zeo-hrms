"""Projects staff API (/zeo-projects/api/...). Rights: see access.py. Customer portal: portal.py."""
from rest_framework.exceptions import APIException
import json
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Count, Q
from django.http import FileResponse, Http404
from django.utils.dateparse import parse_date
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from . import access as A
from . import rows as RW
from . import services as S
from .models import (BILLABLE, BILLING_FREQ, BILLING_METHODS, CHANNELS, CONTRACT_STATUS, CONTRACT_TYPES, CR_STATUS, DEV_STATUS,
                     DONE_STAGES, INVOICE_STATUS, LEVEL, MILESTONE_STATUS, OPEN_STAGES, PRIORITIES, PROJECT_TYPES, RENEWAL_STATUS,
                     RISK_KIND, RISK_STATUS, SUPPORT_TIMING, TICKET_STAGES, AMCBilling, AMCContract, ChangeRequest, Contact, Customer,
                     DevRequest, IssueCategory, Milestone, ProjectExpense, ProjectProfile, ProjectRisk, ProjectsSettings,
                     ServiceProduct, SLAPolicy, SLATarget, SupportTeam, TaskDetail, Ticket, TicketFile, WorkLog, PlanTemplate)


# ------------------------------------------------------------------ plumbing
def bad(msg, code=status.HTTP_400_BAD_REQUEST, field=None):
    body = {'detail': msg}
    if field:
        body[field] = [msg]
    return Response(body, status=code)


def guard(fn):
    def wrapper(self, request, *a, **k):
        try:
            return fn(self, request, *a, **k)
        except S.ProjectsError as e:
            return bad(e.message, e.status, e.field)
        except A.Denied as e:
            return bad(e.message, status.HTTP_403_FORBIDDEN)
        except Http404:
            return bad('Not found.', status.HTTP_404_NOT_FOUND)
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


class ProjectsOff(APIException):
    status_code = 409
    default_detail = 'Projects is not active in this company. A company administrator activates it in Settings › Apps.'
    default_code = 'not_active'


def projects_active():
    try:
        from ZeoApps.services import is_active
        return is_active('projects')
    except Exception:
        return True


class Base(APIView):
    zeo_access = False   # rights are checked in this module (access.py)
    zeo_scope = False
    permission_classes = [A.CompanyMember]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    needs_activation = True

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        # v1.22.0: a switched-off Projects app answers 409 (as CRM, Property, Maintenance and HSE do); its data is kept
        if self.needs_activation and not projects_active():
            raise ProjectsOff()


class In:
    """Typed reading of JSON or multipart input (arrays / objects may arrive as JSON text in multipart)."""

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
        if v in (None, '', 'null', 'None'):
            if required:
                raise S.ProjectsError(f'{label or k.replace("_", " ").capitalize()} is required.', k)
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            raise S.ProjectsError(f'{label or k.replace("_", " ").capitalize()} must be a number.', k)

    def n(self, k, default=None, label=None):
        v = self.d.get(k)
        if v in (None, '', 'null'):
            return default
        try:
            r = Decimal(str(v))
        except (InvalidOperation, ValueError):
            raise S.ProjectsError(f'{label or k.replace("_", " ").capitalize()} must be a number.', k)
        if r < 0:
            raise S.ProjectsError(f'{label or k.replace("_", " ").capitalize()} cannot be negative.', k)
        return r

    def dt(self, k, required=False, label=None):
        v = self.d.get(k)
        if v in (None, '', 'null'):
            if required:
                raise S.ProjectsError(f'{label or k.replace("_", " ").capitalize()} is required.', k)
            return None
        r = parse_date(str(v)[:10]) if v else None
        if r is None:
            raise S.ProjectsError(f'{label or k.replace("_", " ").capitalize()} must be a date (YYYY-MM-DD).', k)
        return r

    def b(self, k, default=False):
        v = self.d.get(k)
        if v in (None, ''):
            return default
        if isinstance(v, bool):
            return v
        return str(v).lower() in ('1', 'true', 'yes', 'on')

    def ids(self, k):
        v = self.d.get(k)
        if v in (None, ''):
            return []
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except ValueError:
                v = [x for x in v.split(',') if x.strip()]
        if not isinstance(v, (list, tuple)):
            v = [v]
        try:
            return [int(x) for x in v if str(x).strip()]
        except (TypeError, ValueError):
            raise S.ProjectsError(f'{k} must be a list of ids.', k)

    def choice(self, k, choices, default=None):
        v = self.s(k)
        if not v:
            return default
        if v not in dict(choices):
            raise S.ProjectsError(f'{k.replace("_", " ").capitalize()}: "{v}" is not a valid choice.', k)
        return v

    def obj(self, k, model, required=False, label=None):
        pk = self.i(k, required, label)
        if pk is None:
            return None
        o = model.objects.filter(pk=pk).first()
        if o is None:
            raise S.ProjectsError(f'{label or k.replace("_", " ").capitalize()} not found.', k)
        return o

    def filelist(self, k='files'):
        return self.files.getlist(k) if hasattr(self.files, 'getlist') else []


def get_or_404(model, pk, request=None):
    qs = A.in_branches(model.objects.all(), request) if request is not None else model.objects   # v1.22.0
    o = qs.filter(pk=pk).first()
    if o is None:
        raise Http404
    return o


def emp(pk):
    from EmpManagement.models import emp_master
    return emp_master.objects.filter(pk=pk).first() if pk else None


def page(request, qs, row, default_limit=50):
    q = request.query_params
    qs = A.branch_filter(qs, request)   # v1.22.0: Branch filter on every Projects list
    try:
        limit = max(1, min(1000, int(q.get('limit') or default_limit)))
        offset = max(0, int(q.get('offset') or 0))
    except ValueError:
        limit, offset = default_limit, 0
    total = qs.count()
    return Response({'count': total, 'results': [row(x) for x in qs[offset:offset + limit]]})


def choices(c):
    return [{'value': k, 'label': v} for k, v in c]


# ------------------------------------------------------------------ lookups, settings
def _branches(request):
    from OrganisationManager.models import brnch_mstr
    qs = brnch_mstr.objects.order_by('branch_name')
    b = A.branch_ids(request)
    if b is not None:
        qs = qs.filter(pk__in=b)
    return [{'id': x.pk, 'name': x.branch_name} for x in qs]


class LookupsView(Base):
    """GET → everything the screens need for pickers, plus the user's rights."""

    @guard
    def get(self, request):
        from EmpManagement.models import emp_master
        s = S.settings()
        emps = A.in_branches(emp_master.objects.filter(is_active=True), request).only('id', 'emp_first_name', 'emp_last_name', 'emp_code').order_by('emp_first_name')
        return Response({
            'rights': A.rights(request),
            'currency': s.currency,
            'customers': [{'id': c.id, 'name': c.name, 'code': c.code} for c in A.in_branches(Customer.objects.filter(is_active=True), request)],
            'products': [{'id': p.id, 'name': p.name, 'category': p.category} for p in ServiceProduct.objects.filter(is_active=True)],
            'plan_templates': [{'id': t.id, 'name': t.name, 'product': t.product_id, 'project_type': t.project_type}
                               for t in PlanTemplate.objects.filter(is_active=True)],
            'teams': [{'id': t.id, 'name': t.name} for t in SupportTeam.objects.filter(is_active=True)],
            'categories': [{'id': c.id, 'name': c.name, 'needs_development': c.needs_development} for c in IssueCategory.objects.filter(is_active=True)],
            'sla_policies': [{'id': p.id, 'name': p.name} for p in SLAPolicy.objects.filter(is_active=True)],
            'employees': [{'id': e.id, 'name': S.emp_name(e), 'code': e.emp_code} for e in emps],
            'branches': _branches(request),   # v1.22.0 the user's branches
            'stages': choices(TICKET_STAGES), 'open_stages': list(OPEN_STAGES), 'priorities': choices(PRIORITIES),
            'channels': choices(CHANNELS), 'billable': choices(BILLABLE), 'contract_types': choices(CONTRACT_TYPES),
            'billing_frequencies': choices(BILLING_FREQ), 'contract_status': choices(CONTRACT_STATUS),
            'renewal_status': choices(RENEWAL_STATUS), 'support_timing': choices(SUPPORT_TIMING),
            'invoice_status': choices(INVOICE_STATUS), 'project_types': choices(PROJECT_TYPES),
            'billing_methods': choices(BILLING_METHODS), 'milestone_status': choices(MILESTONE_STATUS),
            'cr_status': choices(CR_STATUS), 'dev_status': choices(DEV_STATUS), 'risk_kinds': choices(RISK_KIND),
            'levels': choices(LEVEL), 'risk_status': choices(RISK_STATUS),
            'product_categories': choices(ServiceProduct.CATEGORIES), 'expense_categories': choices(ProjectExpense.CATEGORIES),
            'stage_flow': {k: list(v) for k, v in S.ALLOWED.items()},
        })


SETTINGS_FIELDS = ('ticket_prefix', 'amc_prefix', 'dev_prefix', 'cr_prefix', 'project_prefix', 'customer_prefix', 'currency',
                   'timezone', 'mailbox_host', 'mailbox_user', 'mailbox_folder', 'portal_url', 'weekly_report_emails')
SETTINGS_INTS = ('number_padding', 'after_expiry_reminder_days', 'expiring_window_days', 'waiting_customer_reminder_days',
                 'auto_close_resolved_days', 'escalation_minutes', 'mailbox_port')
SETTINGS_BOOLS = ('notify_customer', 'portal_enabled', 'portal_show_hours', 'portal_show_invoices', 'mailbox_enabled', 'mailbox_ssl', 'weekly_report_enabled',
                  'approval_not_covered')


def settings_row(s):
    r = {f: getattr(s, f) for f in SETTINGS_FIELDS + SETTINGS_INTS + SETTINGS_BOOLS}
    r.update({'work_days': s.work_days or [0, 1, 2, 3, 4], 'work_start': s.work_start.strftime('%H:%M') if hasattr(s.work_start, 'strftime') else str(s.work_start)[:5],
              'work_end': s.work_end.strftime('%H:%M') if hasattr(s.work_end, 'strftime') else str(s.work_end)[:5],
              'holiday_calendar_ids': s.holiday_calendar_ids or [], 'renewal_reminder_days': s.renewal_reminder_days or [90, 60, 30, 0],
              'default_team': s.default_team_id, 'default_sla': s.default_sla_id, 'mailbox_password_set': bool(s.mailbox_password),
              'mailbox_last_check': RW.d(s.mailbox_last_check), 'mailbox_last_error': s.mailbox_last_error,
              'approval_hours_limit': RW.num(s.approval_hours_limit)})
    try:
        from calendars.models import holiday_calendar
        r['holiday_calendars'] = [{'id': c.id, 'name': f'{c.calendar_title} {c.year}'} for c in holiday_calendar.objects.order_by('-year')]
    except Exception:
        r['holiday_calendars'] = []
    return r


class SettingsView(Base):
    @guard
    def get(self, request):
        A.need(request, 'setup')
        return Response(settings_row(S.settings()))

    @guard
    def put(self, request):
        A.need(request, 'setup')
        s, f = S.settings(), In(request)
        for k in SETTINGS_FIELDS:
            if f.has(k):
                setattr(s, k, f.s(k))
        for k in SETTINGS_INTS:
            if f.has(k):
                setattr(s, k, f.i(k) or 0)
        for k in SETTINGS_BOOLS:
            if f.has(k):
                setattr(s, k, f.b(k))
        if f.has('approval_hours_limit'):
            v = f.n('approval_hours_limit', Decimal(0))
            if v < 0 or v > 1000:
                raise S.ProjectsError('The hours limit must be between 0 and 1000.', 'approval_hours_limit')
            s.approval_hours_limit = v
        if f.has('mailbox_password') and f.s('mailbox_password'):
            s.mailbox_password = f.s('mailbox_password')
        if f.has('work_days'):
            s.work_days = [x for x in f.ids('work_days') if 0 <= x <= 6]
        if f.has('holiday_calendar_ids'):
            s.holiday_calendar_ids = f.ids('holiday_calendar_ids')
        if f.has('renewal_reminder_days'):
            s.renewal_reminder_days = sorted({x for x in f.ids('renewal_reminder_days') if 0 <= x <= 365}, reverse=True)
        for k in ('work_start', 'work_end'):
            if f.has(k):
                v = f.s(k)
                try:
                    h, m = v.split(':')[:2]
                    setattr(s, k, f'{int(h):02d}:{int(m):02d}')
                except ValueError:
                    raise S.ProjectsError('Use HH:MM for working hours.', k)
        if f.has('default_team'):
            s.default_team = f.obj('default_team', SupportTeam)
        if f.has('default_sla'):
            s.default_sla = f.obj('default_sla', SLAPolicy)
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(s.timezone)
        except Exception:
            raise S.ProjectsError('Unknown time zone, e.g. Asia/Dubai.', 'timezone')
        s.save()
        return Response(settings_row(ProjectsSettings.get()))


# ------------------------------------------------------------------ simple masters
def team_row(t):
    return {'id': t.id, 'name': t.name, 'lead': RW.emp_ref(t.lead), 'members': [RW.emp_ref(m) for m in t.members.all()],
            'email_alias': t.email_alias, 'auto_assign': t.auto_assign, 'is_active': t.is_active,
            'open_tickets': t.tickets.filter(stage__in=OPEN_STAGES).count()}


def product_row(p):
    return {'id': p.id, 'name': p.name, 'category': p.category, 'category_label': p.get_category_display(),
            'default_team': p.default_team_id, 'is_active': p.is_active}


def category_row(c):
    return {'id': c.id, 'name': c.name, 'default_team': c.default_team_id, 'needs_development': c.needs_development,
            'default_billable': c.default_billable, 'is_active': c.is_active}


def sla_row(p):
    return {'id': p.id, 'name': p.name, 'description': p.description, 'is_default': p.is_default, 'is_active': p.is_active,
            'targets': [{'priority': t.priority, 'response_minutes': t.response_minutes, 'resolution_minutes': t.resolution_minutes,
                         'business_hours': t.business_hours, 'response_text': S.sla.human(t.response_minutes),
                         'resolution_text': S.sla.human(t.resolution_minutes)}
                        for t in sorted(p.targets.all(), key=lambda x: S.PRIORITY_ORDER.get(x.priority, 9))]}


MASTERS = {
    'products': (ServiceProduct, product_row),
    'teams': (SupportTeam, team_row),
    'categories': (IssueCategory, category_row),
    'sla-policies': (SLAPolicy, sla_row),
}


def save_master(kind, o, f):
    if f.has('name'):
        name = f.s('name')
        if not name:
            raise S.ProjectsError('Name is required.', 'name')
        model = type(o)
        if model.objects.filter(name__iexact=name).exclude(pk=o.pk).exists():
            raise S.ProjectsError(f'"{name}" already exists.', 'name')
        o.name = name
    if f.has('is_active'):
        o.is_active = f.b('is_active', True)
    if kind == 'products':
        if f.has('category'):
            o.category = f.choice('category', ServiceProduct.CATEGORIES, 'other')
        if f.has('default_team'):
            o.default_team = f.obj('default_team', SupportTeam)
    elif kind == 'teams':
        if f.has('lead'):
            o.lead = emp(f.i('lead'))
        if f.has('email_alias'):
            o.email_alias = f.s('email_alias').lower()
        if f.has('auto_assign'):
            o.auto_assign = f.b('auto_assign', True)
    elif kind == 'categories':
        if f.has('default_team'):
            o.default_team = f.obj('default_team', SupportTeam)
        if f.has('needs_development'):
            o.needs_development = f.b('needs_development')
        if f.has('default_billable'):
            o.default_billable = f.choice('default_billable', BILLABLE, '')
    elif kind == 'sla-policies':
        if f.has('description'):
            o.description = f.s('description')
        if f.has('is_default'):
            o.is_default = f.b('is_default')
    if not o.name:
        raise S.ProjectsError('Name is required.', 'name')
    with transaction.atomic():
        o.save()
        if kind == 'teams' and f.has('members'):
            from EmpManagement.models import emp_master
            o.members.set(emp_master.objects.filter(pk__in=f.ids('members')))
        if kind == 'sla-policies':
            if o.is_default:
                SLAPolicy.objects.exclude(pk=o.pk).update(is_default=False)
            if f.has('targets'):
                raw = f.d.get('targets')
                if isinstance(raw, str):
                    raw = json.loads(raw or '[]')
                for t in raw or []:
                    pr = t.get('priority')
                    if pr not in S.PRIORITY_ORDER:
                        raise S.ProjectsError('Unknown priority in targets.', 'targets')
                    try:
                        resp, res = int(t.get('response_minutes')), int(t.get('resolution_minutes'))
                    except (TypeError, ValueError):
                        raise S.ProjectsError(f'{pr}: response and resolution must be minutes.', 'targets')
                    if resp <= 0 or res <= 0 or res < resp:
                        raise S.ProjectsError(f'{pr}: targets must be above 0 and resolution not shorter than response.', 'targets')
                    SLATarget.objects.update_or_create(policy=o, priority=pr, defaults={
                        'response_minutes': resp, 'resolution_minutes': res, 'business_hours': bool(t.get('business_hours', True))})
    return o


class MasterListView(Base):
    @guard
    def get(self, request, kind):
        if kind not in MASTERS:
            raise Http404
        model, row = MASTERS[kind]
        qs = model.objects.all()
        if request.query_params.get('active') == '1':
            qs = qs.filter(is_active=True)
        return Response([row(o) for o in qs])

    @guard
    def post(self, request, kind):
        if kind not in MASTERS:
            raise Http404
        A.need(request, 'setup')
        model, row = MASTERS[kind]
        return Response(row(save_master(kind, model(), In(request))), status=status.HTTP_201_CREATED)


class MasterDetailView(Base):
    @guard
    def patch(self, request, kind, pk):
        if kind not in MASTERS:
            raise Http404
        A.need(request, 'setup')
        model, row = MASTERS[kind]
        return Response(row(save_master(kind, get_or_404(model, pk), In(request))))

    put = patch

    @guard
    def delete(self, request, kind, pk):
        if kind not in MASTERS:
            raise Http404
        A.need(request, 'setup')
        model, row = MASTERS[kind]
        o = get_or_404(model, pk)
        used = {'products': lambda: o.tickets.exists() or o.contracts.exists(), 'teams': lambda: o.tickets.exists(),
                'categories': lambda: o.tickets.exists(), 'sla-policies': lambda: o.contracts.exists()}[kind]()
        if used:
            o.is_active = False
            o.save(update_fields=['is_active'])
            return Response({'detail': 'In use – switched off instead of deleted.', 'deactivated': True})
        o.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------ customers and contacts
def save_customer(c, f, user, request=None):
    for k in ('name', 'trn', 'email', 'phone', 'website', 'address', 'city', 'country', 'notes', 'email_domains'):
        if f.has(k):
            setattr(c, k, f.s(k))
    if not c.name:
        raise S.ProjectsError('Customer name is required.', 'name')
    if Customer.objects.filter(name__iexact=c.name).exclude(pk=c.pk).exists():
        raise S.ProjectsError(f'A customer named "{c.name}" already exists.', 'name')
    if f.has('code') and f.s('code'):
        c.code = f.s('code').upper()
    if not c.code:
        c.code = S.next_number('customer')
    if Customer.objects.filter(code__iexact=c.code).exclude(pk=c.pk).exists():
        raise S.ProjectsError(f'Code {c.code} is used by another customer.', 'code')
    if f.has('account_manager'):
        c.account_manager = emp(f.i('account_manager'))
    if f.has('default_sla'):
        c.default_sla = f.obj('default_sla', SLAPolicy)
    from OrganisationManager.models import brnch_mstr
    allowed = A.branch_ids(request) if request is not None else None
    if f.has('branch'):
        bid = f.i('branch')
        if bid and allowed is not None and bid not in allowed:   # v1.22.0
            raise S.ProjectsError('Choose one of your branches.', 'branch')
        c.branch = brnch_mstr.objects.filter(pk=bid).first() if bid else None
    if not c.pk and not c.branch_id and allowed:   # v1.22.0: branch users' customers belong to their branch
        from zeo.appkit.engine import default_branch
        bid = default_branch(request)
        c.branch = brnch_mstr.objects.filter(pk=bid).first() if bid else None
    if f.has('is_active'):
        c.is_active = f.b('is_active', True)
    if not c.pk:
        c.created_by = user
    c.save()
    return c


class CustomerListView(Base):
    zeo_model = Customer   # v1.22.0: extra fields (form designer) and record panel for this screen
    @guard
    def get(self, request):
        A.need(request, 'customer_view')
        q = request.query_params
        qs = A.in_branches(Customer.objects.all(), request).select_related('account_manager')
        if q.get('q'):
            s = q['q']
            qs = qs.filter(Q(name__icontains=s) | Q(code__icontains=s) | Q(email__icontains=s) | Q(contacts__name__icontains=s)).distinct()
        if q.get('active', '1') == '1':
            qs = qs.filter(is_active=True)
        return page(request, qs, lambda c: RW.customer_row(c, counts=True), 100)

    @guard
    def post(self, request):
        A.need(request, 'customer_edit')
        f = In(request)
        with transaction.atomic():
            c = save_customer(Customer(), f, request.user, request)
            if f.s('contact_name'):
                Contact.objects.create(customer=c, name=f.s('contact_name'), email=f.s('contact_email'), phone=f.s('contact_phone'),
                                       is_primary=True)
        return Response(RW.customer_row(c, True), status=status.HTTP_201_CREATED)


class CustomerDetailView(Base):
    zeo_model = Customer   # v1.22.0: record panel (notes, activities, files, extra fields)
    @guard
    def get(self, request, pk):
        A.need(request, 'customer_view')
        c = get_or_404(Customer, pk, request)
        r = RW.customer_row(c, counts=True)
        r['contact_list'] = [RW.contact_row(x) for x in c.contacts.select_related('portal_user')]
        r['contract_list'] = [RW.contract_row(x) for x in c.contracts.select_related('sla_policy', 'account_manager', 'previous', 'renewal_owner').prefetch_related('products')]
        r['ticket_list'] = [RW.ticket_row(t) for t in A.ticket_scope(request, c.tickets.all()).select_related(
            'customer', 'contact', 'product', 'category', 'contract', 'team', 'assigned_to', 'requester_employee')[:50]]
        from ProjectManagement.models import Project
        visible = set(A.visible_project_ids(request))
        r['project_list'] = [{'id': p.project_id, 'number': p.number, 'title': Project.objects.filter(pk=p.project_id).values_list('title', flat=True).first()}
                             for p in c.projects.all() if p.project_id in visible]
        return Response(r)

    @guard
    def patch(self, request, pk):
        A.need(request, 'customer_edit')
        return Response(RW.customer_row(save_customer(get_or_404(Customer, pk, request), In(request), request.user, request), True))

    put = patch


def save_contact(c, f):
    if f.has('customer') and not c.pk:
        c.customer = f.obj('customer', Customer, required=True)
    for k in ('name', 'email', 'phone', 'designation'):
        if f.has(k):
            setattr(c, k, f.s(k))
    if not c.name:
        raise S.ProjectsError('Contact name is required.', 'name')
    for k in ('is_primary', 'is_active', 'portal_can_approve'):
        if f.has(k):
            setattr(c, k, f.b(k))
    if f.has('portal_role'):
        c.portal_role = f.choice('portal_role', Contact.PORTAL_ROLES, 'user')
    if c.email and Contact.objects.filter(customer=c.customer, email__iexact=c.email).exclude(pk=c.pk).exists():
        raise S.ProjectsError('Another contact of this customer has this e-mail.', 'email')
    c.save()
    if c.is_primary:
        Contact.objects.filter(customer=c.customer).exclude(pk=c.pk).update(is_primary=False)
    if not c.is_active and c.portal_user_id and c.portal_user.is_active:
        c.portal_user.is_active = False
        c.portal_user.save(update_fields=['is_active'])
    return c


class ContactListView(Base):
    @guard
    def get(self, request):
        A.need(request, 'customer_view')
        qs = A.in_branches(Contact.objects.all(), request).select_related('customer', 'portal_user')
        if request.query_params.get('customer'):
            qs = qs.filter(customer_id=request.query_params['customer'])
        if request.query_params.get('active', '1') == '1':
            qs = qs.filter(is_active=True)
        return Response([RW.contact_row(c) for c in qs[:500]])

    @guard
    def post(self, request):
        A.need(request, 'customer_edit')
        return Response(RW.contact_row(save_contact(Contact(), In(request))), status=status.HTTP_201_CREATED)


class ContactDetailView(Base):
    @guard
    def patch(self, request, pk):
        A.need(request, 'customer_edit')
        return Response(RW.contact_row(save_contact(get_or_404(Contact, pk, request), In(request))))

    put = patch

    @guard
    def delete(self, request, pk):
        A.need(request, 'customer_edit')
        c = get_or_404(Contact, pk, request)
        if c.tickets.exists() or c.portal_user_id:
            c.is_active = False
            save_contact(c, In(request))
            return Response({'detail': 'In use – switched off instead of deleted.', 'deactivated': True})
        c.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ContactPortalView(Base):
    """POST {action: invite | reset | disable} → creates / resets / disables the contact's portal login.
    The new password is returned once (and e-mailed to the contact)."""

    @guard
    def post(self, request, pk):
        A.need(request, 'contact_portal')
        from . import portal_users
        c = get_or_404(Contact, pk, request)
        action = In(request).s('action') or 'invite'
        sch = A.schema_of(request)
        if action == 'disable':
            portal_users.disable(c)
            return Response(RW.contact_row(c))
        username, password = portal_users.invite(c, sch, reset=(action == 'reset'))
        r = RW.contact_row(Contact.objects.get(pk=c.pk))
        r.update({'username': username, 'password': password, 'company': sch})
        return Response(r)


# ------------------------------------------------------------------ tickets
TICKET_RELATED = ('customer', 'contact', 'product', 'category', 'contract', 'team', 'assigned_to', 'requester_employee', 'sla_policy')


def filter_tickets(request, qs):
    q = request.query_params
    if q.get('q'):
        s = q['q']
        qs = qs.filter(Q(number__icontains=s) | Q(subject__icontains=s) | Q(customer__name__icontains=s) | Q(contact__name__icontains=s))
    if q.get('stage'):
        qs = qs.filter(stage__in=q['stage'].split(','))
    if q.get('open') == '1':
        qs = qs.filter(stage__in=OPEN_STAGES)
    if q.get('open') == '0':
        qs = qs.filter(stage__in=DONE_STAGES)
    if q.get('priority'):
        qs = qs.filter(priority__in=q['priority'].split(','))
    for k in ('customer', 'team', 'contract', 'channel', 'billable_status', 'product', 'category'):
        if q.get(k):
            qs = qs.filter(**{k: q[k]})
    if q.get('assigned') == 'me':
        e = A.my_emp(request)
        qs = qs.filter(assigned_to=e) if e else qs.none()
    elif q.get('assigned') == 'none':
        qs = qs.filter(assigned_to__isnull=True)
    elif q.get('assigned'):
        qs = qs.filter(assigned_to_id=q['assigned'])
    if q.get('requested') == 'me':
        e = A.my_emp(request)
        qs = qs.filter(Q(requester_employee=e) | Q(created_by=request.user, customer__isnull=True)) if e else qs.filter(created_by=request.user)
    if q.get('approval') == 'pending':
        qs = qs.filter(approval_status='pending')
    if q.get('breached') == '1':
        qs = qs.filter(Q(resolution_breached=True) | Q(response_breached=True))
    if q.get('from'):
        qs = qs.filter(created_at__date__gte=q['from'])
    if q.get('to'):
        qs = qs.filter(created_at__date__lte=q['to'])
    order = q.get('order') or '-created_at'
    allowed = {'-created_at', 'created_at', 'resolution_due', '-resolution_due', 'priority', 'number', '-number', '-updated_at'}
    return qs.order_by(order if order in allowed else '-created_at', '-id')


class TicketListView(Base):
    zeo_model = Ticket   # v1.22.0: extra fields (form designer) and record panel for this screen
    @guard
    def get(self, request):
        qs = filter_tickets(request, A.ticket_scope(request).select_related(*TICKET_RELATED))
        return page(request, qs, RW.ticket_row)

    @guard
    def post(self, request):
        """Support staff create a ticket for a customer, or any employee raises an internal helpdesk ticket (no customer)."""
        f = In(request)
        customer = f.obj('customer', Customer)
        me = A.my_emp(request)
        if customer is not None:
            A.need(request, 'ticket_add')
        elif me is None and not A.is_admin(request):
            raise S.ProjectsError('Your user is not linked to an employee.')
        staff = A.can(request, 'ticket_add')
        t = S.create_ticket(
            subject=f.s('subject'), description=f.s('description'), user=request.user,
            channel=f.choice('channel', CHANNELS, 'manual' if customer else 'internal') if staff else 'internal',
            customer=customer, contact=f.obj('contact', Contact),
            employee=(emp(f.i('requester_employee')) if staff and f.has('requester_employee') else (None if customer else me)),
            product=f.obj('product', ServiceProduct), category=f.obj('category', IssueCategory),
            priority=f.choice('priority', PRIORITIES, 'medium'),
            team=f.obj('team', SupportTeam) if staff else None,
            assigned_to=emp(f.i('assigned_to')) if staff and A.can(request, 'ticket_manage') else None,
            contract=f.obj('contract', AMCContract) if staff else None,
            billable_status=f.choice('billable_status', BILLABLE) if staff else None,
            files=f.filelist(), approved_by=request.user if A.can(request, 'ticket_manage') else None)
        return Response(RW.ticket_detail(t), status=status.HTTP_201_CREATED)


def ticket_for(request, pk):
    t = A.ticket_scope(request, Ticket.objects.select_related(*TICKET_RELATED)).filter(pk=pk).first()
    if t is None:
        raise Http404
    return t


def need_work(request, t):
    if not A.can_work_ticket(request, t):
        raise A.Denied('Only the assigned engineer, the team or a support manager can change this ticket.')


class TicketDetailView(Base):
    zeo_model = Ticket   # v1.22.0: record panel (notes, activities, files, extra fields)
    @guard
    def get(self, request, pk):
        t = ticket_for(request, pk)
        worker = A.can_work_ticket(request, t) or A.can(request, 'ticket_all')
        r = RW.ticket_detail(t, staff=True, show_internal=worker)
        r['can_work'] = A.can_work_ticket(request, t)
        r['can_manage'] = A.can(request, 'ticket_manage')
        r['allowed_stages'] = list(S.ALLOWED.get(t.stage, ())) if r['can_work'] else []
        if not worker:
            r.pop('worklogs', None)
        return Response(r)

    @guard
    def patch(self, request, pk):
        t = ticket_for(request, pk)
        need_work(request, t)
        S.update_ticket(t, In(request).d, request.user)
        return Response(RW.ticket_detail(Ticket.objects.get(pk=t.pk)))


class TicketAssignView(Base):
    @guard
    def post(self, request, pk):
        t = ticket_for(request, pk)
        f = In(request)
        target = emp(f.i('employee'))
        me = A.my_emp(request)
        # engineers may take a ticket of their team; anything else needs the manager right
        taking = target is not None and me is not None and target.pk == me.pk and A.can_work_ticket(request, t)
        if not (taking or A.can(request, 'ticket_manage')):
            raise A.Denied('Only a support manager can assign tickets to others.')
        team = f.obj('team', SupportTeam) if f.has('team') else None
        S.assign(t, target, team, request.user)
        return Response(RW.ticket_detail(Ticket.objects.get(pk=t.pk)))


class TicketStageView(Base):
    @guard
    def post(self, request, pk):
        t = ticket_for(request, pk)
        need_work(request, t)
        f = In(request)
        stage = f.s('stage')
        if stage == 'closed' and t.stage != 'resolved' and not A.can(request, 'ticket_manage'):
            raise A.Denied('Only a support manager can close a ticket that is not resolved.')
        if stage == 'in_progress':
            S.work_allowed(t, A.can(request, 'ticket_manage'))
        S.set_stage(t, stage, request.user, f.s('note'))
        return Response(RW.ticket_detail(Ticket.objects.get(pk=t.pk)))


class TicketMessageView(Base):
    @guard
    def post(self, request, pk):
        t = ticket_for(request, pk)
        f = In(request)
        worker = A.can_work_ticket(request, t)
        me = A.my_emp(request)
        requester = me is not None and t.requester_employee_id == me.pk
        if not (worker or requester or A.can(request, 'ticket_all')):
            raise A.Denied('You cannot write on this ticket.')
        internal = f.b('internal') and (worker or A.can(request, 'ticket_all'))
        if requester and not worker:
            # the employee who raised an internal ticket answers like a customer
            S.add_message(t, f.s('body'), user=request.user, internal=False, via='staff', files=f.filelist())
            if t.stage == 'waiting_customer':
                S.set_stage(t, 'in_progress', request.user, by_customer=True)
        else:
            S.add_message(t, f.s('body'), user=request.user, internal=internal, files=f.filelist())
        return Response(RW.ticket_detail(Ticket.objects.get(pk=t.pk)))


class TicketWorkLogView(Base):
    @guard
    def post(self, request, pk):
        t = ticket_for(request, pk)
        need_work(request, t)
        f = In(request)
        who = A.my_emp(request)
        if f.has('employee') and A.can(request, 'ticket_manage'):
            who = emp(f.i('employee')) or who
        w = S.log_work(t, who, hours=f.n('hours', label='Hours'), day=f.dt('date'), description=f.s('description'),
                       onsite=f.b('onsite'), billable=f.b('billable') if f.has('billable') else None,
                       counts_against_amc=f.b('counts_against_amc') if f.has('counts_against_amc') else None,
                       travel_cost=f.n('travel_cost', 0), other_charges=f.n('other_charges', 0), user=request.user,
                       auto_approve=A.can(request, 'worklog_approve'), manager=A.can(request, 'ticket_manage'))
        return Response(RW.worklog_row(w), status=status.HTTP_201_CREATED)


class TicketApprovalView(Base):
    """POST {action: request|approve|reject, hours, note, assigned_to} – v1.15.0 work approval."""

    @guard
    def post(self, request, pk):
        t = ticket_for(request, pk)
        f = In(request)
        action = f.s('action')
        if action == 'request':
            need_work(request, t)
            S.request_more_time(t, request.user, f.n('hours', label='Hours'), f.s('note'))
        elif action in ('approve', 'reject'):
            A.need(request, 'ticket_manage', 'Only a support manager can approve work.')
            S.decide_approval(t, request.user, action == 'approve', hours=f.n('hours', None) if f.has('hours') else None,
                              note=f.s('note'), assign_to=emp(f.i('assigned_to')) if f.i('assigned_to') else None)
        else:
            raise S.ProjectsError('Unknown action.', 'action')
        return Response(RW.ticket_detail(Ticket.objects.get(pk=t.pk)))


class TicketDevRequestView(Base):
    @guard
    def post(self, request, pk):
        t = ticket_for(request, pk)
        need_work(request, t)
        f = In(request)
        d = S.raise_dev_request(t, request.user, title=f.s('title') or t.subject, issue=f.s('issue') or t.description,
                                proposed_solution=f.s('proposed_solution'), estimated_hours=f.n('estimated_hours', 0),
                                estimated_cost=f.n('estimated_cost', 0), chargeable=f.b('chargeable') if f.has('chargeable') else None,
                                needs_customer_approval=f.b('needs_customer_approval') if f.has('needs_customer_approval') else None)
        return Response(RW.dev_row(d), status=status.HTTP_201_CREATED)


class FileDownloadView(Base):
    @guard
    def get(self, request, pk):
        f = get_or_404(TicketFile, pk)
        t = ticket_for(request, f.ticket_id)
        if f.is_internal and not (A.can_work_ticket(request, t) or A.can(request, 'ticket_all')):
            raise Http404
        return FileResponse(f.file.open('rb'), as_attachment=True, filename=f.name)


# ------------------------------------------------------------------ work logs
class WorkLogListView(Base):
    @guard
    def get(self, request):
        q = request.query_params
        qs = A.in_branches(WorkLog.objects.all(), request).select_related('ticket', 'ticket__customer', 'employee')
        if not A.can(request, 'worklog_approve'):
            e = A.my_emp(request)
            qs = qs.filter(employee=e) if e else qs.none()
        if q.get('status'):
            qs = qs.filter(status=q['status'])
        if q.get('employee'):
            qs = qs.filter(employee_id=q['employee'])
        if q.get('from'):
            qs = qs.filter(date__gte=q['from'])
        if q.get('to'):
            qs = qs.filter(date__lte=q['to'])
        return page(request, qs, RW.worklog_row, 100)


class WorkLogDecideView(Base):
    @guard
    def post(self, request):
        A.need(request, 'worklog_approve')
        f = In(request)
        n = S.decide_worklogs(f.ids('ids'), request.user, f.b('approve', True), f.s('reason'))
        return Response({'updated': n})


class WorkLogDetailView(Base):
    @guard
    def delete(self, request, pk):
        w = get_or_404(WorkLog, pk, request)
        me = A.my_emp(request)
        if w.status == 'approved' and not A.can(request, 'worklog_approve'):
            raise A.Denied('An approved work log can only be removed by an approver.')
        if not (A.can(request, 'worklog_approve') or (me and w.employee_id == me.pk)):
            raise A.Denied('You can only remove your own work logs.')
        w.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------ AMC contracts
def save_contract(c, f, user):
    creating = not c.pk
    if creating or f.has('customer'):
        cust = f.obj('customer', Customer, required=creating)
        if cust is not None:
            if c.pk and c.customer_id != cust.pk and c.tickets.exists():
                raise S.ProjectsError('The customer cannot change once tickets are linked.', 'customer')
            c.customer = cust
    for k in ('title', 'exclusions', 'notes', 'currency'):
        if f.has(k):
            setattr(c, k, f.s(k))
    if not c.title:
        c.title = f'Annual maintenance – {c.customer.name}'
    if f.has('contract_type'):
        c.contract_type = f.choice('contract_type', CONTRACT_TYPES, 'comprehensive')
    if f.has('billing_frequency'):
        c.billing_frequency = f.choice('billing_frequency', BILLING_FREQ, 'annual')
    if f.has('support_timing'):
        c.support_timing = f.choice('support_timing', SUPPORT_TIMING, 'business')
    for k in ('start_date', 'end_date'):
        if f.has(k) or creating:
            v = f.dt(k, required=creating)
            if v:
                setattr(c, k, v)
    if c.end_date < c.start_date:
        raise S.ProjectsError('The end date is before the start date.', 'end_date')
    for k in ('value', 'included_hours', 'onsite_hours', 'hourly_rate_outside'):
        if f.has(k):
            setattr(c, k, f.n(k, Decimal(0)))
    if f.has('unlimited_hours'):
        c.unlimited_hours = f.b('unlimited_hours')
    if f.has('sla_policy'):
        c.sla_policy = f.obj('sla_policy', SLAPolicy)
    if f.has('account_manager'):
        c.account_manager = emp(f.i('account_manager'))
    if creating:
        c.number = S.next_number('amc')
        c.created_by = user
        c.status = 'draft'
    c.save()
    if f.has('products'):
        c.products.set(ServiceProduct.objects.filter(pk__in=f.ids('products')))
    if c.status != 'draft' and any(f.has(k) for k in ('value', 'billing_frequency', 'start_date', 'end_date')):
        S.build_billing(c)
    return c


class ContractListView(Base):
    zeo_model = AMCContract   # v1.22.0: extra fields (form designer) and record panel for this screen
    @guard
    def get(self, request):
        A.need(request, 'amc_view')
        q = request.query_params
        qs = A.in_branches(AMCContract.objects.all(), request).select_related('customer', 'sla_policy', 'account_manager', 'previous', 'renewal_owner').prefetch_related('products')
        for c in qs.exclude(status__in=('draft', 'cancelled')):
            S.refresh_contract_status(c)
        if q.get('q'):
            s = q['q']
            qs = qs.filter(Q(number__icontains=s) | Q(title__icontains=s) | Q(customer__name__icontains=s))
        if q.get('status'):
            qs = qs.filter(status__in=q['status'].split(','))
        if q.get('customer'):
            qs = qs.filter(customer_id=q['customer'])
        if q.get('renewal_status'):
            qs = qs.filter(renewal_status__in=q['renewal_status'].split(','))
        if q.get('expiring'):
            try:
                days = int(q['expiring'])
            except ValueError:
                days = 30
            today = S.local_today()
            qs = qs.filter(end_date__gte=today, end_date__lte=today + timedelta(days=days)).exclude(status__in=('draft', 'cancelled', 'renewed'))
        return page(request, qs, lambda c: RW.contract_row(c, usage=q.get('usage') == '1'), 100)

    @guard
    def post(self, request):
        A.need(request, 'amc_edit')
        f = In(request)
        with transaction.atomic():
            c = save_contract(AMCContract(), f, request.user)
            if f.b('activate'):
                S.activate_contract(c, request.user)
        return Response(RW.contract_row(c, True), status=status.HTTP_201_CREATED)


class ContractDetailView(Base):
    zeo_model = AMCContract   # v1.22.0: record panel (notes, activities, files, extra fields)
    @guard
    def get(self, request, pk):
        A.need(request, 'amc_view')
        c = get_or_404(AMCContract, pk, request)
        S.refresh_contract_status(c)
        r = RW.contract_row(c, usage=True)
        r['billing'] = [RW.billing_row(b) for b in c.billing.all()] if A.can(request, 'billing_view') or A.can(request, 'amc_edit') else []
        r['tickets'] = [RW.ticket_row(t) for t in c.tickets.select_related(*TICKET_RELATED)[:100]]
        r['renewals'] = [{'id': x.id, 'number': x.number, 'status': x.status, 'start_date': RW.d(x.start_date), 'end_date': RW.d(x.end_date)}
                         for x in c.renewals.all()]
        chain, p = [], c.previous
        while p is not None and len(chain) < 20:
            chain.append({'id': p.id, 'number': p.number, 'start_date': RW.d(p.start_date), 'end_date': RW.d(p.end_date), 'value': RW.num(p.value)})
            p = p.previous
        r['history'] = chain
        r['tickets_by_product'] = {(w['product__name'] or 'Not set'): w['n']
                                   for w in c.tickets.values('product__name').annotate(n=Count('id'))}
        return Response(r)

    @guard
    def patch(self, request, pk):
        A.need(request, 'amc_edit')
        c = get_or_404(AMCContract, pk, request)
        if c.status in ('expired', 'renewed', 'cancelled') and not A.is_admin(request):
            raise S.ProjectsError('A closed contract cannot be changed.')
        return Response(RW.contract_row(save_contract(c, In(request), request.user), True))

    put = patch


class ContractActionView(Base):
    """POST action: activate | cancel | renew | renewal (follow-up fields) | rebuild-billing."""

    @guard
    def post(self, request, pk, action):
        c = get_or_404(AMCContract, pk, request)
        f = In(request)
        if action == 'activate':
            A.need(request, 'amc_edit')
            S.activate_contract(c, request.user)
        elif action == 'cancel':
            A.need(request, 'amc_edit')
            c.status = 'cancelled'
            c.notes = (c.notes + '\nCancelled: ' + f.s('reason')).strip()
            c.save(update_fields=['status', 'notes'])
            c.billing.filter(status='planned').update(status='cancelled')
        elif action == 'renew':
            A.need(request, 'amc_renew')
            n = S.renew_contract(c, request.user, start_date=f.dt('start_date'), end_date=f.dt('end_date'), value=f.n('value'),
                                 included_hours=f.n('included_hours'))
            if f.b('activate'):
                S.activate_contract(n, request.user)
            return Response(RW.contract_row(n, True), status=status.HTTP_201_CREATED)
        elif action == 'renewal':
            A.need(request, 'amc_renew')
            if f.has('renewal_status'):
                c.renewal_status = f.choice('renewal_status', RENEWAL_STATUS, c.renewal_status)
            for k in ('renewal_quote_ref', 'renewal_note', 'lost_reason'):
                if f.has(k):
                    setattr(c, k, f.s(k))
            if f.has('renewal_value'):
                c.renewal_value = f.n('renewal_value')
            if f.has('renewal_owner'):
                c.renewal_owner = emp(f.i('renewal_owner'))
            if c.renewal_status == 'lost' and not c.lost_reason:
                raise S.ProjectsError('Give the reason the renewal was lost.', 'lost_reason')
            c.save()
            if c.renewal_status in ('won', 'lost'):
                S.close_todos(c)
        elif action == 'rebuild-billing':
            A.need(request, 'billing_edit')
            S.build_billing(c)
        else:
            raise Http404
        return Response(RW.contract_row(AMCContract.objects.get(pk=c.pk), True))


class BillingListView(Base):
    """Finance: AMC instalments and project milestones to invoice / invoiced / paid."""

    @guard
    def get(self, request):
        A.need(request, 'billing_view')
        q = request.query_params
        st = q.get('status')
        amc = A.in_branches(AMCBilling.objects.all(), request).select_related('contract', 'contract__customer').exclude(contract__status__in=('draft', 'cancelled'))
        ms = Milestone.objects.filter(amount__gt=0, project_id__in=A.visible_project_ids(request))   # v1.22.0: only projects the user may see
        if st:
            amc = amc.filter(status__in=st.split(','))
            ms = ms.filter(invoice_status__in=st.split(','))
        if q.get('due_by'):
            amc = amc.filter(due_date__lte=q['due_by'])
        from ProjectManagement.models import Project
        titles = dict(Project.objects.values_list('id', 'title'))
        profs = {p.project_id: p for p in ProjectProfile.objects.select_related('customer')}
        out_ms = []
        for m in ms:
            r = RW.milestone_row(m)
            p = profs.get(m.project_id)
            r.update({'project_title': titles.get(m.project_id, ''), 'customer_name': p.customer.name if p and p.customer_id else '',
                      'currency': S.settings().currency})
            out_ms.append(r)
        return Response({'amc': [RW.billing_row(b) for b in amc[:500]], 'milestones': out_ms})


class BillingDetailView(Base):
    @guard
    def patch(self, request, pk):
        A.need(request, 'billing_edit')
        b = get_or_404(AMCBilling, pk)
        f = In(request)
        if f.has('status'):
            b.status = f.choice('status', INVOICE_STATUS, b.status)
        for k in ('invoice_no', 'note'):
            if f.has(k):
                setattr(b, k, f.s(k))
        for k in ('invoice_date', 'paid_date', 'due_date'):
            if f.has(k):
                setattr(b, k, f.dt(k))
        if f.has('amount'):
            b.amount = f.n('amount', Decimal(0))
        if b.status in ('invoiced', 'paid') and not b.invoice_no:
            raise S.ProjectsError('Enter the invoice number.', 'invoice_no')
        if b.status == 'paid' and not b.paid_date:
            b.paid_date = S.local_today()
        b.save()
        return Response(RW.billing_row(b))


# ------------------------------------------------------------------ development requests
class DevRequestListView(Base):
    zeo_model = DevRequest   # v1.22.0: extra fields (form designer) and record panel for this screen
    @guard
    def get(self, request):
        q = request.query_params
        qs = A.in_branches(DevRequest.objects.all(), request).select_related('ticket', 'customer', 'requested_by', 'tech_by', 'commercial_by', 'assigned_to')
        if not A.can(request, 'dev_view'):
            e = A.my_emp(request)
            qs = qs.filter(Q(requested_by=request.user) | Q(assigned_to=e)) if e else qs.filter(requested_by=request.user)
        if q.get('status'):
            qs = qs.filter(status__in=q['status'].split(','))
        if q.get('waiting') == 'me':
            steps = []
            if A.can(request, 'dev_tech'):
                steps.append('submitted')
            if A.can(request, 'dev_commercial'):
                steps.append('tech_approved')
            qs = qs.filter(status__in=steps)
        if q.get('q'):
            qs = qs.filter(Q(number__icontains=q['q']) | Q(title__icontains=q['q']) | Q(customer__name__icontains=q['q']))
        return page(request, qs, RW.dev_row, 100)


class DevRequestDetailView(Base):
    zeo_model = DevRequest   # v1.22.0: record panel (notes, activities, files, extra fields)
    @guard
    def get(self, request, pk):
        d = get_or_404(DevRequest, pk, request)
        e = A.my_emp(request)
        if not (A.can(request, 'dev_view') or d.requested_by_id == request.user.id or (e and d.assigned_to_id == e.pk)):
            raise Http404
        r = RW.dev_row(d)
        r['can_tech'] = A.can(request, 'dev_tech') and d.status == 'submitted'
        r['can_commercial'] = A.can(request, 'dev_commercial') and d.status == 'tech_approved'
        r['can_record_customer'] = (A.can(request, 'dev_commercial') or A.can(request, 'ticket_manage')) and d.status == 'commercial_approved'
        r['can_progress'] = bool(A.can(request, 'ticket_manage') or (e and d.assigned_to_id == e.pk) or A.can(request, 'dev_tech'))
        r['next_status'] = list(S.DEV_FLOW.get(d.status, ()))
        return Response(r)

    @guard
    def patch(self, request, pk):
        d = get_or_404(DevRequest, pk, request)
        if not (A.can(request, 'dev_tech') or A.can(request, 'ticket_manage')):
            raise A.Denied()
        f = In(request)
        for k in ('title', 'issue', 'proposed_solution'):
            if f.has(k) and d.status in ('submitted', 'tech_approved'):
                setattr(d, k, f.s(k))
        for k in ('estimated_hours', 'estimated_cost'):
            if f.has(k) and d.status in ('submitted', 'tech_approved'):
                setattr(d, k, f.n(k, Decimal(0)))
        if f.has('assigned_to'):
            d.assigned_to = emp(f.i('assigned_to'))
            if d.task_id and d.assigned_to:
                from ProjectManagement.models import Task
                task = Task.objects.filter(pk=d.task_id).first()
                if task:
                    task.task_members.add(d.assigned_to)
                    task.project.members.add(d.assigned_to)
        if f.has('project_id') and d.status in ('submitted', 'tech_approved', 'commercial_approved'):
            d.project_id = f.i('project_id')
        d.save()
        return Response(RW.dev_row(d))


class DevRequestDecideView(Base):
    @guard
    def post(self, request, pk):
        d = get_or_404(DevRequest, pk, request)
        f = In(request)
        step = f.s('step')
        if step == 'tech':
            A.need(request, 'dev_tech')
        elif step == 'commercial':
            A.need(request, 'dev_commercial')
        elif step == 'customer':
            if not (A.can(request, 'dev_commercial') or A.can(request, 'ticket_manage')):
                raise A.Denied('Only a support manager or the finance team can record the customer decision.')
        S.decide_dev(d, request.user, step, f.b('approve', True), f.s('note'), f.n('quoted_amount'), f.s('customer_name'))
        return Response(RW.dev_row(DevRequest.objects.get(pk=d.pk)))


class DevRequestProgressView(Base):
    @guard
    def post(self, request, pk):
        d = get_or_404(DevRequest, pk, request)
        e = A.my_emp(request)
        if not (A.can(request, 'ticket_manage') or A.can(request, 'dev_tech') or (e and d.assigned_to_id == e.pk)):
            raise A.Denied('Only the developer, a technical reviewer or a support manager can move this request.')
        f = In(request)
        S.dev_progress(d, request.user, f.s('status'), f.s('note'))
        return Response(RW.dev_row(DevRequest.objects.get(pk=d.pk)))


# ------------------------------------------------------------------ reports
class ReportListView(Base):
    """GET → reports this user may open."""

    @guard
    def get(self, request):
        from . import reports as REP
        return Response({'reports': REP.catalogue(request), 'rights': A.rights(request)})


class ReportView(Base):
    """GET /reports/<key>/?from=&to=&customer=&team=&employee=&project=&status=&group=&window="""

    @guard
    def get(self, request, key):
        from . import reports as REP
        bq = request.query_params.get('branch')   # v1.22.0 Branch filter of the report
        if bq and str(bq).isdigit():
            allowed = A.branch_ids(request)
            request._zeo_dash_branch = int(bq) if allowed is None or int(bq) in allowed else -1
        return Response(RW.jsonable(REP.run(request, key)))


class WeeklySummaryView(Base):
    """GET → preview of the weekly e-mail; POST → send it now (managers)."""

    @guard
    def get(self, request):
        from . import reports as REP
        A.need(request, 'dashboard')
        return Response({'text': REP.weekly_summary(request)})

    @guard
    def post(self, request):
        from . import reports as REP
        A.need(request, 'ticket_manage')
        n = REP.send_weekly(force=True)
        if not n:
            raise S.ProjectsError('Nobody to send to: add e-mail addresses in Setup → Weekly summary.')
        return Response({'sent_to': n})



# ------------------------------------------------------------------ v1.15.0 notification settings
class NotificationSettingsView(Base):
    """GET/PUT ?scope=company | customer&customer=<id> | me
    PUT {rows: [{event, custom, email, dashboard}]}"""

    def _target(self, request, write=False):
        q = request.query_params
        scope = q.get('scope') or 'company'
        if scope == 'me':
            return None, request.user
        if scope == 'customer':
            c = Customer.objects.filter(pk=q.get('customer')).first()
            if c is None:
                raise Http404
            A.need(request, 'customer_edit' if write else 'customer_view')
            return c, None
        if write:
            A.need(request, 'setup')
        return None, None

    @guard
    def get(self, request):
        from . import notify as N
        c, u = self._target(request)
        return Response({'rows': N.matrix(c, u), 'scope': request.query_params.get('scope') or 'company'})

    @guard
    def put(self, request):
        from . import notify as N
        c, u = self._target(request, write=True)
        rows = request.data.get('rows') if isinstance(request.data, dict) else None
        if not isinstance(rows, list):
            raise S.ProjectsError('rows must be a list.', 'rows')
        return Response({'rows': N.save(rows, c, u)})



# ------------------------------------------------------------------ v1.15.0 work history files and e-mail
class _Q:
    def __init__(self, data):
        self.query_params = data


def history_filters(request, data):
    from . import reports as REP
    shim = _Q({k: (data.get(k) or '') for k in ('from', 'to', 'customer', 'contract', 'project', 'employee')})
    return REP.P(shim)


class WorkHistoryFileView(Base):
    """GET ?file=pdf|xlsx&from=&to=&customer=&contract=&project=&employee="""

    @guard
    def get(self, request):
        from django.http import HttpResponse
        from . import history as H
        data = H.collect(request, history_filters(request, request.query_params))
        fmt = request.query_params.get('file') or 'pdf'   # not ?format= (DRF content negotiation)
        name = 'Work_history_' + ''.join(ch if ch.isalnum() else '_' for ch in data['subject'])[:50] + f"_{data['to']}"
        if fmt == 'xlsx':
            resp = HttpResponse(H.build_xlsx(data), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            resp['Content-Disposition'] = f'attachment; filename="{name}.xlsx"'
        else:
            resp = HttpResponse(H.build_pdf(data), content_type='application/pdf')
            resp['Content-Disposition'] = f'attachment; filename="{name}.pdf"'
        return resp


class WorkHistorySendView(Base):
    """POST {from, to, customer, contract, project, employee, emails: "a@x, b@y", message} → PDF + Excel by e-mail."""

    @guard
    def post(self, request):
        from . import history as H
        if not (any(A.can(request, k) for k in ('ticket_manage', 'project_edit', 'customer_edit', 'amc_edit')) or A.is_admin(request)):
            raise A.Denied('You do not have the right to send reports to customers.')
        data_in = request.data if isinstance(request.data, dict) else {}
        raw = data_in.get('emails') or ''
        emails = [x.strip() for x in (raw if isinstance(raw, list) else str(raw).replace(';', ',').split(',')) if '@' in str(x)]
        if not emails:
            raise S.ProjectsError('Enter at least one e-mail address.', 'emails')
        if len(emails) > 20:
            raise S.ProjectsError('At most 20 addresses.', 'emails')
        data = H.collect(request, history_filters(request, data_in))
        name = 'Work_history_' + ''.join(ch if ch.isalnum() else '_' for ch in data['subject'])[:50] + f"_{data['to']}"
        body = ((data_in.get('message') or '').strip() + '\n\n' if data_in.get('message') else '') + \
            (f"Please find attached the work history for {data['subject']}, {data['from']} to {data['to']}: "
             f"{data['total_hours']:g} hours in {len(data['entries'])} time entries and {len(data['tickets'])} tickets.\n")
        ok = S.send_mail(emails, f"Work history – {data['subject']} ({data['from']} to {data['to']})", body,
                         reply_to=getattr(request.user, 'email', None) or None,
                         attachments=[(name + '.pdf', H.build_pdf(data), 'application/pdf'),
                                      (name + '.xlsx', H.build_xlsx(data),
                                       'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')])
        if not ok:
            raise S.ProjectsError('The e-mail could not be sent. Check Settings → E-mail configuration.')
        return Response({'sent_to': emails})


# ------------------------------------------------------------------ v1.22.0 record panel (Chatter): who may open which Projects record
try:
    from Chatter.views import register_scope as _rs
    _rs(Ticket, lambda request, qs: qs.filter(pk__in=A.ticket_scope(request).values('pk')))
    _rs(Customer, lambda request, qs: A.in_branches(qs, request) if A.can(request, 'customer_view') else qs.none())
    _rs(AMCContract, lambda request, qs: A.in_branches(qs, request) if A.can(request, 'amc_view') else qs.none())
    _rs(DevRequest, lambda request, qs: A.in_branches(qs, request) if (A.can(request, 'dev_view') or A.can(request, 'dev_tech')
                                                                         or A.can(request, 'dev_commercial')) else qs.none())
except Exception:
    pass
