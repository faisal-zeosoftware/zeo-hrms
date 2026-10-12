"""The kit engine: App definition, rights, errors, request parsing, numbering, history, notifications and the
generic register API. An app module builds one App(...) and calls app.urls() in its urls.py."""
import json
import logging
import time
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO

from django.apps import apps as djapps
from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError, connection, models as M, transaction
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponse
from django.urls import path
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

log = logging.getLogger(__name__)
REGISTRY = {}          # app label → App (every kit app imported in this process)
OPTION_PROVIDERS = {}  # 'property.units' → (app label, fn() → (qs, label fn, search fields))


# ------------------------------------------------------------------ errors and rights
class AppError(Exception):
    def __init__(self, message, field=None, status=400, data=None):
        super().__init__(message)
        self.message, self.field, self.status, self.data = message, field, status, data


class Denied(Exception):
    def __init__(self, message='You do not have permission to do this.'):
        self.message = message


def need(cond, message):
    if not cond:
        raise Denied(message)


def ctx(request):
    try:
        from AccessControl.access import ctx as c
        return c(request)
    except Exception:
        return None


class CompanyMember(BasePermission):
    message = 'You do not have access to this company.'

    def has_permission(self, request, view):
        u = request.user
        if not u or not u.is_authenticated:
            self.message = 'Please log in.'
            return False
        sch = connection.schema_name if connection.schema_name != 'public' else request.GET.get('schema')
        if not sch or sch == 'public':
            self.message = 'Choose a company.'
            return False
        return bool(u.is_superuser or u.tenants.filter(schema_name=sch).exists())


# ------------------------------------------------------------------ v1.22.0 branches (Settings › Branch permissions)
def branch_ids(request):
    """The branches the user works in: None = every branch (company administrator); a list otherwise
    (Settings › Branch permissions, or the employee's own branch)."""
    if request is None:
        return None
    c = ctx(request)
    if c is None or c.admin:
        return None
    return list(c.branches or [])


def has_branch(model):
    try:
        f = model._meta.get_field('branch')
        return f.related_model.__name__ == 'brnch_mstr'
    except Exception:
        return False


def branch_q(model, branches, path='branch'):
    """Rows of these branches plus rows without a branch (shared by every branch)."""
    return Q(**{f'{path}__in': branches}) | Q(**{f'{path}__isnull': True})


def in_branches(qs, request, path='branch'):
    """Limit any queryset of a model with a branch to the user's branches (admins: everything).
    On a dashboard (request._zeo_dash_branch) also the Branch picked on the dashboard."""
    model = qs.model
    if path == 'branch' and not has_branch(model):
        return qs
    pick = getattr(request, '_zeo_dash_branch', None) if request is not None else None
    if pick:
        qs = qs.filter(**{path: pick})
    b = branch_ids(request)
    if b is None:
        return qs
    return qs.filter(branch_q(model, b, path))


def dash_period(request):
    """(from, to) of a dashboard: ?from=&to= (default: this month)."""
    t = today()
    q = getattr(request, 'query_params', {}) or {}
    frm = parse_date(str(q.get('from') or '')[:10]) or t.replace(day=1)
    to = parse_date(str(q.get('to') or '')[:10]) or (add_months(t.replace(day=1), 1) - timedelta(days=1))
    return (frm, to) if frm <= to else (to, frm)


# integer links to records of another kit app → (app label, model) whose branch is copied
_INT_LINKS = {'property_unit_id': ('ZeoProperty', 'Unit'), 'property_building_id': ('ZeoProperty', 'Building'),
              'unit_id': ('ZeoProperty', 'Unit'), 'crm_contact_id': ('ZeoCRM', 'Contact'), 'crm_opportunity_id': ('ZeoCRM', 'Opportunity')}


def derive_branch(obj):
    """Copy the branch from the record this one belongs to (unit → building, lease → unit, work order → location …).
    The model may list the fields to look at in BRANCH_FROM; otherwise every foreign key in order."""
    names = getattr(type(obj), 'BRANCH_FROM', None)
    fields = []
    for f in obj._meta.fields:
        if f.name == 'branch':
            continue
        if isinstance(f, M.ForeignKey) and has_branch(f.related_model):
            fields.append(f.name)
        elif f.name in _INT_LINKS:
            fields.append(f.name)
    if names:
        fields = [n for n in names if n in fields] + [n for n in fields if n not in names]
    for n in fields:
        try:
            if n in _INT_LINKS and not isinstance(obj._meta.get_field(n), M.ForeignKey):
                pk = getattr(obj, n)
                if not pk:
                    continue
                lbl, mdl = _INT_LINKS[n]
                if not djapps.is_installed(lbl):
                    continue
                Mdl = djapps.get_model(lbl, mdl)
                b = Mdl.objects.filter(pk=pk).values_list('branch_id', flat=True).first()
            else:
                rel = getattr(obj, n, None)
                b = getattr(rel, 'branch_id', None) if rel is not None else None
            if b:
                return b
        except Exception:
            continue
    return None


_SIGNALLED = set()


def _branch_signal(model):
    """Records made by links, portals and packs (not through a form) still get the branch of their parent record."""
    if model in _SIGNALLED:
        return
    from django.db.models.signals import pre_save

    def fill(sender, instance, raw=False, **kw):
        if raw or instance.branch_id:
            return
        try:
            instance.branch_id = derive_branch(instance)
        except Exception:
            pass
    pre_save.connect(fill, sender=model, weak=False, dispatch_uid=f'zeo-branch-{model._meta.label}')
    _SIGNALLED.add(model)


def now():
    return timezone.now()


def company_tz():
    tz = 'Asia/Dubai'
    try:
        from ZeoProjects.models import ProjectsSettings
        tz = ProjectsSettings.get().timezone or tz
    except Exception:
        pass
    return tz


def today():
    try:
        from zoneinfo import ZoneInfo
        return timezone.now().astimezone(ZoneInfo(company_tz())).date()
    except Exception:
        return timezone.now().date()


def local(dt):
    try:
        from zoneinfo import ZoneInfo
        return dt.astimezone(ZoneInfo(company_tz()))
    except Exception:
        return dt


def add_months(d, n):
    import calendar
    y, m = divmod(d.month - 1 + n, 12)
    y += d.year
    m += 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def emp_name(e):
    if e is None:
        return ''
    return ' '.join(x for x in (e.emp_first_name, e.emp_last_name) if x and str(x).strip()) or e.emp_code


def user_name(u):
    if u is None:
        return ''
    try:
        from EmpManagement.models import emp_master
        e = emp_master.objects.filter(users=u).first()
        if e:
            return emp_name(e)
    except Exception:
        pass
    return (u.get_full_name() if hasattr(u, 'get_full_name') else '') or u.username


def d(v):
    if v is None:
        return None
    return v.isoformat() if hasattr(v, 'isoformat') else v


def num(v):
    return float(v) if v is not None else None


def jsonable(o):
    if isinstance(o, dict):
        return {k: jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonable(v) for v in o]
    if isinstance(o, Decimal):
        return float(o)
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    return o


# ------------------------------------------------------------------ request data
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
                raise AppError(f'{label or k.replace("_", " ").capitalize()} is required.', k)
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            raise AppError(f'{label or k} must be a whole number.', k)

    def n(self, k, default=None):
        v = self.d.get(k)
        if v in (None, '', 'null'):
            return default
        try:
            return Decimal(str(v))
        except (InvalidOperation, ValueError):
            raise AppError(f'{k.replace("_", " ").capitalize()} must be a number.', k)

    def b(self, k):
        v = self.d.get(k)
        return v is True or str(v).lower() in ('1', 'true', 'yes', 'on')

    def dt(self, k, required=False, label=None):
        v = self.d.get(k)
        if v in (None, '', 'null'):
            if required:
                raise AppError(f'{label or k.replace("_", " ").capitalize()} is required.', k)
            return None
        r = parse_date(str(v)[:10])
        if r is None:
            raise AppError(f'{label or k} is not a valid date.', k)
        return r

    def dtm(self, k, required=False, label=None):
        v = self.d.get(k)
        if v in (None, '', 'null'):
            if required:
                raise AppError(f'{label or k} is required.', k)
            return None
        r = parse_datetime(str(v))
        if r is None:
            dd = parse_date(str(v)[:10])
            if dd is None:
                raise AppError(f'{label or k} is not a valid date and time.', k)
            r = datetime.combine(dd, datetime.min.time())
        if timezone.is_naive(r):
            from zoneinfo import ZoneInfo
            r = r.replace(tzinfo=ZoneInfo(company_tz()))
        return r

    def j(self, k):
        v = self.d.get(k)
        if isinstance(v, str):
            try:
                return json.loads(v) if v.strip() else None
            except ValueError:
                raise AppError(f'{k} is not valid.', k)
        return v

    def ids(self, k):
        v = self.j(k) if isinstance(self.d.get(k), str) else self.d.get(k)
        if isinstance(v, (int, str)) and str(v).isdigit():
            v = [v]
        return [int(x) for x in (v or []) if str(x).isdigit()]

    def choice(self, k, choices, default=None):
        v = self.s(k)
        if not v:
            return default
        if v not in dict(choices):
            raise AppError(f'"{v}" is not a valid {k.replace("_", " ")}.', k)
        return v

    def emp(self, k):
        pk = self.i(k)
        if pk is None:
            return None
        from EmpManagement.models import emp_master
        e = emp_master.objects.filter(pk=pk).first()
        if e is None:
            raise AppError('Choose an existing employee.', k)
        return e

    def obj(self, k, model, required=False, label=None):
        pk = self.i(k, required, label)
        if pk is None:
            return None
        x = model.objects.filter(pk=pk).first()
        if x is None:
            raise AppError(f'{label or k.replace("_", " ").capitalize()} not found.', k)
        return x

    def filelist(self, k='files'):
        try:
            return self.files.getlist(k)
        except Exception:
            return []


def bad(msg, code=status.HTTP_400_BAD_REQUEST, field=None, data=None):
    body = {'detail': msg}
    if field:
        body[field] = [msg]
    if data:
        body.update(data)
    return Response(body, status=code)


def guard(fn):
    def wrapper(self, request, *a, **k):
        try:
            if getattr(self, 'needs_activation', True) and self.app is not None and not self.app.activated():
                raise AppError(f'{self.app.title} is not activated yet. A manager or administrator activates it and chooses its industry.',
                               status=409, data={'activated': False})
            return fn(self, request, *a, **k)
        except AppError as e:
            return bad(e.message, e.status, e.field, e.data)
        except Denied as e:
            return bad(e.message, status.HTTP_403_FORBIDDEN)
        except Http404:
            return bad('Not found.', status.HTTP_404_NOT_FOUND)
        except IntegrityError as e:
            log.warning('integrity: %s', e)
            return bad('A record with the same code / number already exists, or the record is still in use.')
    wrapper.__name__, wrapper.__doc__ = fn.__name__, fn.__doc__
    return wrapper


# ------------------------------------------------------------------ links to other kit apps
_TABLES = {}


def tables():
    sch = getattr(connection, 'schema_name', 'public')
    hit = _TABLES.get(sch)
    if hit and time.time() - hit[0] < 120:
        return hit[1]
    try:
        names = {t.lower() for t in connection.introspection.table_names()}
    except Exception:
        names = set()
    _TABLES[sch] = (time.time(), names)
    return names


def forget_tables():
    _TABLES.clear()


def other_app(label):
    """The other kit app when it is installed, migrated and activated in this company, else None."""
    a = REGISTRY.get(label)
    if a is None or not djapps.is_installed(label) and label not in {c.label for c in djapps.get_app_configs()}:
        return None
    if a.settings_model._meta.db_table.lower() not in tables():
        return None
    return a if a.activated() else None


def linked(app, label):
    """Is the link from `app` to the app `label` on (installed, activated, not switched off here)?"""
    if other_app(label) is None:
        return False
    try:
        return label not in (app.settings().links_off or [])
    except Exception:
        return False


def provider(name):
    p = OPTION_PROVIDERS.get(name)
    if p is None or other_app(p[0]) is None:
        return None
    return p[1]()


def link_options(name, q='', ids=None, limit=500, request=None):
    got = provider(name)
    if not got:
        return []
    qs, lbl, search = got
    qs = in_branches(qs, request)   # v1.22.0
    if ids:
        qs = qs.model.objects.filter(pk__in=ids)
    elif q:
        cond = Q()
        for s_ in search:
            cond |= Q(**{s_ + '__icontains': q})
        qs = qs.filter(cond)
    return [{'id': x.pk, 'label': lbl(x)} for x in qs[:limit]]


def link_label(name, pk):
    if not pk:
        return ''
    got = provider(name)
    if not got:
        return f'#{pk}'
    x = got[0].model.objects.filter(pk=pk).first()
    return got[1](x) if x else f'#{pk} (removed)'


def link_exists(name, pk):
    got = provider(name)
    return bool(got and got[0].model.objects.filter(pk=pk).exists())


def call(app, label, name, *a, **kw):
    """Ask the other app `label` to do `name` – only when the link from `app` is on. Returns None when it is not."""
    if not linked(app, label):
        return None
    fn = REGISTRY[label].hooks.get(name)
    if fn is None:
        return None
    try:
        with transaction.atomic():
            return fn(*a, **kw)
    except AppError:
        raise
    except Exception:
        log.exception('%s → %s.%s failed', app.label, label, name)
        return None


def safe(fn):
    """A link must never stop the record being saved (here or in the other app)."""
    def wrap(*a, **kw):
        try:
            with transaction.atomic():
                return fn(*a, **kw)
        except (AppError, Denied):
            raise
        except Exception:
            log.exception('link %s failed', fn.__name__)
            return None
    wrap.__name__ = fn.__name__
    return wrap


# ------------------------------------------------------------------ the App
class App:
    def __init__(self, *, label, slug, title, settings_model, sequence_model, event_model, file_model, roles, role_prefix,
                 manager_role, packs, pack_labels, pack_descriptions, industry_from_company, pack_models, prefixes=None,
                 links=(), page=None):
        self.label, self.slug, self.title = label, slug, title
        self.settings_model, self.sequence_model, self.event_model, self.file_model = settings_model, sequence_model, event_model, file_model
        self.roles, self.role_prefix, self.manager_role = roles, role_prefix, manager_role
        self.packs, self.pack_labels, self.pack_descriptions = packs, pack_labels, pack_descriptions
        self.industry_from_company, self.pack_models = industry_from_company, pack_models
        self.prefixes = prefixes or {}
        self.links = links                      # [(label, title, what it does)]
        self.page = page or f'/main-sidebar/{slug}/'
        self.regs = {}
        self.options = {}                       # name → (qs fn, label fn, search fields)
        self.model_options = {}                 # model class name → option name
        self.dashboard = None                   # fn(request) → dict
        self.my_work = None                     # fn(request) → list of {title, rows}
        self.settings_fields = ()               # app-specific settings a manager may change
        self.daily = None                       # fn() → dict, run by the kit's daily command
        self.hooks = {}                         # name → fn: what other kit apps may ask this app to do
        REGISTRY[label] = self

    # settings / activation
    def settings(self):
        return self.settings_model.get()

    def activated(self):
        try:
            return self.settings_model.objects.filter(activated_at__isnull=False).exists()
        except Exception:
            return False

    # rights
    def code(self, role):
        return role if role.startswith(self.role_prefix) else self.role_prefix + role

    def is_admin(self, request):
        c = ctx(request)
        return bool(c and c.admin)

    def has(self, request, role):
        c = ctx(request)
        return bool(c and (c.admin or self.code(role) in c.codes))

    def any(self, request, *roles):
        return any(self.has(request, r) for r in roles)

    def is_manager(self, request):
        return self.has(request, self.manager_role)

    def my_emp(self, request):
        c = ctx(request)
        return c.emp if c else None

    def rights(self, request):
        r = {k: self.has(request, k) for k, _ in self.roles}
        r.update({'admin': self.is_admin(request), 'manager': self.is_manager(request),
                  'any_role': any(r.values()), 'user_id': request.user.pk, 'employee_id': getattr(self.my_emp(request), 'pk', None)})
        return r

    def users_with_role(self, role):
        from django.contrib.auth.models import Group
        from tenant_users.tenants.models import UserTenantPermissions
        groups = Group.objects.filter(permissions__codename=self.code(role), permissions__content_type__app_label=self.label)
        out = [u.profile for u in _company_perms().filter(groups__in=groups).select_related('profile').distinct()
               if u.profile and u.profile.is_active]
        if not out:
            out = [u.profile for u in _company_perms().filter(is_superuser=True).select_related('profile')
                   if u.profile and u.profile.is_active]
        return out

    # numbering, history, files
    def next_number(self, kind, default_prefix):
        with transaction.atomic():
            seq, _ = self.sequence_model.objects.select_for_update().get_or_create(kind=kind)
            seq.last += 1
            seq.save()
        prefix = self.prefixes.get(kind, default_prefix)
        return f'{prefix}-{seq.last:05d}'

    def event(self, model, obj_id, text, user=None):
        self.event_model.objects.create(model=model, object_id=obj_id, text=text[:4000], user=user if (user and user.is_authenticated) else None)

    def history(self, model, obj_id):
        return [{'text': e.text, 'by': user_name(e.user), 'at': d(e.at)} for e in self.event_model.objects.filter(model=model, object_id=obj_id).select_related('user')[:200]]

    def files(self, model, obj_id):
        return [{'id': f.pk, 'name': f.name, 'size': f.size, 'note': f.note, 'portal': f.portal, 'by': user_name(f.uploaded_by), 'at': d(f.uploaded_at)}
                for f in self.file_model.objects.filter(model=model, object_id=obj_id).select_related('uploaded_by')]

    def term(self, key, default):
        try:
            return (self.settings().terms or {}).get(key) or default
        except Exception:
            return default

    # notifications (e-mail through the company's mail settings + My To-do)
    def send_mail(self, to, subject, body):
        to = [x for x in dict.fromkeys(t.strip() for t in (to or []) if t and '@' in t)]
        if not to:
            return False
        try:
            from django.conf import settings as dj
            from django.core.mail import EmailMessage, get_connection
            conn, sender = None, dj.DEFAULT_FROM_EMAIL
            try:
                from EmpManagement.models import EmailConfiguration
                cfg = EmailConfiguration.objects.filter(is_active=True).first()
                if cfg and cfg.email_host_user:
                    conn = get_connection(host=cfg.email_host, port=cfg.email_port, username=cfg.email_host_user,
                                          password=cfg.email_host_password, use_tls=cfg.email_use_tls, fail_silently=False)
                    sender = cfg.email_host_user
            except Exception:
                conn = None
            EmailMessage(subject=subject[:200], body=body, from_email=sender, to=to, connection=conn).send(fail_silently=False)
            return True
        except Exception:
            log.exception('%s e-mail failed', self.label)
            return False

    def todo(self, user, summary, model, obj_id, label, page, note=''):
        if user is None:
            return
        try:
            from Chatter.models import Activity
            key = f'{self.label}.{model}'
            if Activity.objects.filter(model=key, object_id=str(obj_id), assigned_to=user, summary=summary[:255], state='open').exists():
                return
            Activity.objects.create(model=key, object_id=str(obj_id), record_label=label[:255], page=page[:255], activity_type='todo',
                                    summary=summary[:255], note=note, due_date=today(), assigned_to=user)
        except Exception:
            log.exception('to-do failed')

    def close_todos(self, model, obj_id, startswith=None):
        try:
            from Chatter.models import Activity
            qs = Activity.objects.filter(model=f'{self.label}.{model}', object_id=str(obj_id), state='open')
            if startswith:
                qs = qs.filter(summary__startswith=startswith)
            qs.update(state='done', done_at=now())
        except Exception:
            log.exception('to-do close failed')

    def tell(self, users, subject, text, model, obj_id, label, page, summary=None):
        seen = set()
        for u in users:
            if u is None or u.pk in seen:
                continue
            seen.add(u.pk)
            self.send_mail([u.email], subject, text)
            if summary:
                self.todo(u, summary, model, obj_id, label, page, text)

    def tell_emp(self, e, subject, text, model, obj_id, label, page, summary=None):
        if e is None:
            return
        self.send_mail([e.emp_company_email or e.emp_personal_email or (getattr(e.users, 'email', '') if e.users_id else '')], subject, text)
        if summary and e.users_id:
            self.todo(e.users, summary, model, obj_id, label, page, text)

    # registers
    def reg(self, r):
        r.app = self
        r.cols, r.form, r.info, r.filters = tuple(r.cols), tuple(r.form), tuple(r.info), tuple(r.filters)
        try:                                   # v1.22.0: the record panel uses the register's own rights
            from Chatter.views import register_scope
            register_scope(r.model, lambda request, qs, r=r: qs.filter(pk__in=r.scope(request).values('pk')))
        except Exception:
            pass
        if has_branch(r.model):                # v1.22.0: every register with a branch shows, takes and filters it
            _branch_signal(r.model)
            if 'branch' not in r.cols:
                r.cols = tuple(r.cols) + ('branch',)
            if 'branch' not in r.form and 'branch' not in r.readonly:
                r.form = tuple(r.form) + ('branch',)
            if 'branch' not in r.filters:
                r.filters = tuple(r.filters) + ('branch',)
        self.regs[r.kind] = r
        self.model_options.setdefault(r.model.__name__, r.kind)
        return r

    def provide(self, name, qs_fn, label_fn, search):
        """Offer a list of records to the other kit apps (e.g. 'property.units'); they see it only while linked."""
        OPTION_PROVIDERS[name] = (self.label, lambda: (qs_fn(), label_fn, search))

    def option(self, name, qs_fn, label_fn, search, model=None):
        self.options[name] = (qs_fn, label_fn, search)
        if model is not None:
            self.model_options[model.__name__] = name

    # packs
    def install(self, code, user=None, primary=False):
        from .packs import install
        return install(self, code, user, primary)

    def urls(self):
        from . import views as V
        a = self
        return [
            path('api/app/', V.AppInfoView.as_view(app=a)),
            path('api/activate/', V.ActivateView.as_view(app=a)),
            path('api/pack/', V.PackView.as_view(app=a)),
            path('api/pack/review/', V.ReviewView.as_view(app=a)),
            path('api/links/', V.LinksView.as_view(app=a)),
            path('api/dashboard/', V.DashboardView.as_view(app=a)),
            path('api/my/', V.MyWorkView.as_view(app=a)),
            path('api/registers/', V.RegistersView.as_view(app=a)),
            path('api/options/<str:name>/', V.OptionsView.as_view(app=a)),
            path('api/files/<int:pk>/', V.FileView.as_view(app=a)),
            path('api/r/<str:kind>/', V.RegisterListView.as_view(app=a)),
            path('api/r/<str:kind>/export/', V.RegisterExportView.as_view(app=a)),
            path('api/r/<str:kind>/<int:pk>/', V.RegisterDetailView.as_view(app=a)),
            path('api/r/<str:kind>/<int:pk>/do/<str:action>/', V.RegisterActionView.as_view(app=a)),
            path('api/r/<str:kind>/<int:pk>/files/', V.RegisterFileView.as_view(app=a)),
        ]


def company_industry():
    """(company master code, its label) of the current company."""
    try:
        from UserManagement.models import company
        t = company.objects.filter(schema_name=connection.schema_name).first()
        if t is None:
            return '', ''
        code = t.industry_type or ''
        return code, (t.get_industry_type_display() if code else '')
    except Exception:
        return '', ''


# ------------------------------------------------------------------ registers
class Act:
    def __init__(self, name, label, run, allowed=None, fields=(), style='', confirm=''):
        self.name, self.label, self.run, self.allowed, self.fields, self.style, self.confirm = name, label, run, allowed, fields, style, confirm


def st(*s):
    return lambda o, *_: o.status in s


class Reg:
    """One register: model, list columns, form fields, who manages / views, what a normal user sees, workflow actions."""

    def __init__(self, kind, model, label, plural, group, *, cols, form, number=None, manage=(), view=(), create='manage', own=(),
                 public=None, restricted=(), search=('number',), filters=('status',), edit_when=None, actions=(), children=(), extra=None,
                 saver=None, check=None, child_of=None, title='number', subtitle='', help='', defaults=None, files=True, links=None,
                 can_restricted=None, review=False, ordering=None, scope_fn=None, readonly=(), deletable=False, export_extra=(), own_edit=False, info=()):
        self.kind, self.model, self.label, self.plural, self.group = kind, model, label, plural, group
        self.cols, self.form, self.number, self.manage, self.view, self.create, self.own = cols, form, number, manage, view, create, own
        self.public, self.restricted, self.search, self.filters = public, restricted, search, filters
        self.edit_when, self.actions, self.children, self.extra = edit_when, list(actions), children, extra
        self.saver, self.check, self.child_of, self.title, self.subtitle = saver, check, child_of, title, subtitle
        self.help, self.defaults, self.files, self.links = help, defaults, files, links or {}
        self.can_restricted, self.review, self.ordering, self.scope_fn, self.readonly = can_restricted, review, ordering, scope_fn, readonly
        self.deletable, self.export_extra, self.own_edit = deletable, export_extra, own_edit
        self.info = info                         # read-only fields shown on the record (set by workflow)
        self.app = None

    def manages(self, request):
        return self.app.is_admin(request) or self.app.any(request, *self.manage)

    def views_all(self, request):
        return self.manages(request) or self.app.any(request, *self.view)

    def can_create(self, request):
        return self.create == 'all' or self.manages(request) or (self.create not in ('manage', 'all', 'none') and self.app.any(request, *self.create))

    def can_open(self, request):
        return self.views_all(request) or self.can_create(request) or bool(self.own) or self.public is not None

    def scope(self, request):
        qs = self.model.objects.all()
        if self.ordering:
            qs = qs.order_by(*self.ordering)
        if self.scope_fn:
            qs = self.scope_fn(qs, request)
        qs = in_branches(qs, request)          # v1.22.0: only the user's branches (and records shared by every branch)
        if self.views_all(request):
            return qs
        me = self.app.my_emp(request)
        q = Q(pk__in=[])
        for field, typ in self.own:
            if typ == 'user':
                q |= Q(**{field: request.user})
            elif me is not None:
                q |= Q(**{field: me})
        if self.public is not None:
            q |= self.public(request)
        return qs.filter(q)

    def is_own(self, obj, request):
        me = self.app.my_emp(request)
        for field, typ in self.own:
            v = getattr(obj, field + '_id', None)
            if (typ == 'user' and v == request.user.pk) or (typ == 'emp' and me is not None and v == me.pk):
                return True
        return False

    def can_edit(self, obj, request):
        if self.edit_when and not self.edit_when(obj):
            return False
        return self.manages(request) or (self.is_own(obj, request) and (self.create == 'all' or self.own_edit))

    def show_restricted(self, request, obj=None):
        return self.manages(request) if self.can_restricted is None else self.can_restricted(request, obj)

    def link_off(self, name):
        if name not in self.links:
            return False
        p = OPTION_PROVIDERS.get(self.links[name])
        return p is None or not linked(self.app, p[0])


def field_of(model, name):
    return model._meta.get_field(name)


def ftype(f):
    if isinstance(f, M.ManyToManyField):
        return 'm2m'
    if isinstance(f, M.ForeignKey):
        return 'fk'
    if f.choices:
        return 'choice'
    if isinstance(f, M.BooleanField):
        return 'bool3' if f.null else 'bool'
    if isinstance(f, M.TextField):
        return 'textarea'
    if isinstance(f, M.DecimalField):
        return 'decimal'
    if isinstance(f, (M.IntegerField, M.PositiveSmallIntegerField, M.PositiveIntegerField)):
        return 'int'
    if isinstance(f, M.DateTimeField):
        return 'datetime'
    if isinstance(f, M.DateField):
        return 'date'
    if isinstance(f, M.JSONField):
        return 'json'
    return 'text'


def label_of(f):
    v = str(f.verbose_name)
    return v[:1].upper() + v[1:]


def opt_label(app, model_name, obj):
    if obj is None:
        return ''
    k = app.model_options.get(model_name)
    if k and k in app.options:
        return app.options[k][1](obj)
    if model_name == 'emp_master':
        return f'{emp_name(obj)} ({obj.emp_code})'
    if model_name == 'brnch_mstr':
        return obj.branch_name
    if model_name in ('CustomUser', 'User'):
        return user_name(obj)
    for a in ('number', 'code', 'name', 'title'):
        if getattr(obj, a, None):
            return str(getattr(obj, a))
    return str(obj)


def fmeta(r, name):
    f = field_of(r.model, name)
    if name in r.links:
        return {'name': name, 'label': label_of(f), 'type': 'fk', 'options': 'link:' + r.links[name], 'help': str(f.help_text or ''),
                'restricted': False, 'required': False, 'link': OPTION_PROVIDERS.get(r.links[name], ('',))[0]}
    t = ftype(f)
    m = {'name': name, 'label': label_of(f), 'type': t, 'help': str(f.help_text or ''), 'restricted': name in r.restricted}
    if t in ('fk', 'm2m'):
        rm = f.related_model.__name__
        m['options'] = 'employees' if rm == 'emp_master' else ('users' if rm in ('CustomUser', 'User') else
                                                              ('branches' if rm == 'brnch_mstr' else r.app.model_options.get(rm, '')))
    if t == 'choice':
        m['choices'] = [{'value': k, 'label': str(v)} for k, v in f.choices if k != '']
    m['required'] = bool(not f.blank and t not in ('bool', 'bool3', 'json', 'm2m') and f.default is M.NOT_PROVIDED)
    m['readonly'] = name in r.readonly
    return m


def value(r, obj, name, hide=False):
    f = field_of(r.model, name)
    if name in r.links:
        pk = getattr(obj, name)
        return pk, (link_label(r.links[name], pk) if pk else '')
    t = ftype(f)
    if hide and name in r.restricted:
        return None, '(restricted)'
    if t == 'm2m':
        xs = list(getattr(obj, name).all()) if obj.pk else []
        return [x.pk for x in xs], ', '.join(opt_label(r.app, f.related_model.__name__, x) for x in xs)
    if t == 'fk':
        x = getattr(obj, name)
        return (x.pk if x else None), opt_label(r.app, f.related_model.__name__, x)
    v = getattr(obj, name)
    if t == 'choice':
        return v, str(dict(f.choices).get(v, v or ''))
    if isinstance(v, Decimal):
        return float(v), None
    if isinstance(v, (date, datetime)):
        return v.isoformat(), None
    return v, None


def ser(r, obj, request, cols=None):
    hide = not r.show_restricted(request, obj)
    out = {'id': obj.pk, 'kind': r.kind}
    for n in cols or r.cols:
        if r.link_off(n):
            continue
        v, lbl = value(r, obj, n, hide)
        out[n] = v
        if lbl is not None:
            out[n + '_label'] = lbl
    t, tl = value(r, obj, r.title)
    out['_title'] = str(tl if tl else (t if t is not None else obj.pk))
    if r.subtitle:
        sv, sl = value(r, obj, r.subtitle, hide)
        out['_subtitle'] = sl if sl is not None else (str(sv) if sv is not None else '')
    else:
        out['_subtitle'] = ''
    if hasattr(obj, 'status') and 'status' not in out:
        out['status'] = obj.status
    if hasattr(obj, 'get_status_display'):
        out['status_label'] = obj.get_status_display()
    if hasattr(obj, 'review_status'):
        out['review_status'] = obj.review_status
        out['pack'] = getattr(obj, 'pack', '')
    return out


def detail(r, obj, request):
    out = ser(r, obj, request, tuple(dict.fromkeys(r.cols + r.form + r.info)))
    out['info'] = [fmeta(r, n) for n in r.info if not r.link_off(n)]
    out['can_edit'] = r.can_edit(obj, request)
    out['actions'] = [{'name': a.name, 'label': a.label, 'style': a.style, 'confirm': a.confirm,
                       'fields': [{'name': n, 'label': l, 'type': t, 'required': req} for n, l, t, req in a.fields]}
                      for a in r.actions if action_ok(r, a, obj, request)]
    out['children'] = []
    for label, ck, fk in r.children:
        cr = r.app.regs[ck]
        if not cr.can_open(request) and not r.manages(request):
            continue
        qs = cr.model.objects.filter(**{fk: obj})
        if not r.manages(request):
            qs = qs.filter(pk__in=cr.scope(request).values('pk'))
        out['children'].append({'label': label, 'kind': ck, 'fk': fk, 'cols': [fmeta(cr, c) for c in cr.cols if not cr.link_off(c)],
                                'rows': [ser(cr, x, request) for x in qs[:300]],
                                'can_add': (cr.can_create(request) or r.can_edit(obj, request)) and cr.create != 'none'})
    out['history'] = r.app.history(r.kind, obj.pk)
    out['files'] = r.app.files(r.kind, obj.pk) if r.files else []
    out['restricted_hidden'] = not r.show_restricted(request, obj) and bool(r.restricted)
    if r.extra:
        out.update(r.extra(obj, request))
    return jsonable(out)


def assign(r, obj, f, request, creating):
    hide = not r.show_restricted(request, obj)
    m2m = {}
    for n in r.form:
        if n not in f.d or (hide and n in r.restricted) or n in r.readonly:
            continue
        fld = field_of(r.model, n)
        if not creating and r.child_of == n:
            continue
        if n in r.links:
            if not r.link_off(n):
                pk = f.i(n)
                if pk and not link_exists(r.links[n], pk):
                    raise AppError('Choose an existing record.', n)
                setattr(obj, n, pk)
            continue
        t = ftype(fld)
        if t == 'fk':
            setattr(obj, n, f.obj(n, fld.related_model, False, label_of(fld)))
        elif t == 'm2m':
            m2m[n] = f.ids(n)
        elif t == 'choice':
            v = f.s(n)
            if v and v not in dict(fld.choices):
                raise AppError(f'"{v}" is not a valid {label_of(fld).lower()}.', n)
            setattr(obj, n, v if v else (fld.default if fld.default is not M.NOT_PROVIDED else ''))
        elif t == 'bool':
            setattr(obj, n, f.b(n))
        elif t == 'bool3':
            setattr(obj, n, None if f.d.get(n) in (None, '') else f.b(n))
        elif t == 'int':
            v = f.i(n)
            setattr(obj, n, v if v is not None else (fld.default if fld.default is not M.NOT_PROVIDED else None))
        elif t == 'decimal':
            v = f.n(n)
            setattr(obj, n, v if v is not None else (fld.default if fld.default is not M.NOT_PROVIDED else None))
        elif t == 'date':
            setattr(obj, n, f.dt(n))
        elif t == 'datetime':
            setattr(obj, n, f.dtm(n))
        elif t == 'json':
            v = f.j(n)
            setattr(obj, n, v if v is not None else (fld.default() if callable(fld.default) else {}))
        else:
            setattr(obj, n, f.s(n)[:fld.max_length] if getattr(fld, 'max_length', None) else f.s(n))
    return m2m


def validate(r, obj):
    for n in r.form:
        if r.link_off(n) or n in r.links:
            continue
        fm = fmeta(r, n)
        if fm['required'] and fm['type'] != 'm2m':
            v = getattr(obj, n + '_id' if fm['type'] == 'fk' else n, None)
            if v in (None, ''):
                raise AppError(f'{fm["label"]} is required.', n)


def save(r, obj, f, request, creating):
    old = r.model.objects.get(pk=obj.pk) if obj.pk else None
    with transaction.atomic():
        m2m = assign(r, obj, f, request, creating)
        if creating:
            if r.number:
                obj.number = r.app.next_number(r.number[0], r.number[1])
            if r.defaults:
                try:
                    r.defaults(obj, request)
                except ObjectDoesNotExist:
                    validate(r, obj)     # a required choice is missing: say which
                    raise
            if r.review and hasattr(obj, 'review_status'):
                obj.review_status = 'reviewed'   # made by the company itself
        if has_branch(r.model):                # v1.22.0
            if creating and not obj.branch_id:
                obj.branch_id = derive_branch(obj) or default_branch(request)
            b = branch_ids(request)
            if b is not None and obj.branch_id and obj.branch_id not in b and (creating or obj.branch_id != getattr(old, 'branch_id', None)):
                raise AppError('Choose one of your branches.', 'branch', status=403)
        validate(r, obj)
        if r.check:
            r.check(obj, request, old)
        if r.saver:
            res = r.saver(obj, request, creating, old)
            obj = res if isinstance(res, r.model) else obj
        else:
            obj.save()
        for n, ids in m2m.items():
            rel = field_of(r.model, n).related_model
            getattr(obj, n).set(rel.objects.filter(pk__in=ids))
        if creating:
            r.app.event(r.kind, obj.pk, f'Created by {user_name(request.user)}', request.user)
            for x in f.filelist('files'):
                r.app.file_model.objects.create(model=r.kind, object_id=obj.pk, file=x, name=x.name[:255], size=x.size or 0, uploaded_by=request.user)
        elif old is not None:
            changed = []
            for n in r.form:
                fld = field_of(r.model, n)
                if isinstance(fld, M.ManyToManyField):
                    continue
                if getattr(old, fld.attname) != getattr(obj, fld.attname):
                    changed.append(label_of(fld) + (' (restricted)' if n in r.restricted else ''))
            if changed:
                r.app.event(r.kind, obj.pk, 'Changed: ' + ', '.join(changed), request.user)
    return obj


def default_branch(request):
    """New records: the user's only branch, or the branch of their employee record (None when it is open)."""
    b = branch_ids(request)
    if b and len(b) == 1:
        return b[0]
    c = ctx(request)
    e = getattr(c, 'emp', None) if c else None
    eb = getattr(e, 'emp_branch_id_id', None) if e is not None else None
    if eb and (b is None or eb in b):
        return eb
    return b[0] if b else None


def action_ok(r, a, obj, request):
    """An action with its own rule decides alone (state AND rights); without one only managers may run it."""
    return bool(a.allowed(obj, request)) if a.allowed is not None else r.manages(request)


def run_action(r, obj, request, name, data):
    """Run a workflow action from code (used by links and portals); same checks as the API."""
    a = next((x for x in r.actions if x.name == name), None)
    if a is None:
        raise AppError('Unknown action.')
    if not action_ok(r, a, obj, request):
        raise AppError(f'"{a.label}" is not possible now.')
    return a.run(obj, request, data)



def _company_perms():   # v1.22.0: only the users of the open company (see AccessControl.access.company_perms)
    from AccessControl.access import company_perms
    return company_perms()
