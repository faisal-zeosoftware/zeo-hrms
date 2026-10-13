"""v1.23.0 – ZEO Cloud API: sign-up, the owner's subscription and security, creating companies, the Adept control console.

    public   GET  /cloud/api/signup/plans/                 plans and apps for the sign-up page
             POST /cloud/api/signup/                       new owner + account (14-day trial) + first company → {job}
             GET  /cloud/api/jobs/<job>/                   progress of a company being made
    users    GET  /cloud/api/status/?schema=               banner, plan and app licences of the open company
             GET  /cloud/api/accounts/<id>/subscription/   owner: plan, limits, usage, apps, extra companies, invoices
             POST /cloud/api/accounts/<id>/companies/      owner: create a company within the plan → {job}
             POST /cloud/api/accounts/<id>/request/        owner: request an upgrade / app / more employees
             GET|PUT /cloud/api/accounts/<id>/security/    owner: only company e-mails
             POST /cloud/api/notices/seen/
    Adept    /cloud/api/console/…                          customers, plans, app licences, invoices, actions, history
"""
import logging
import re
from datetime import date, timedelta

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.core.exceptions import ValidationError as DjValidationError
from django.db.models import Q
from django.utils import timezone
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services as S
from .models import APP_KEYS, AccessNotice, Account, AccountCompany, AccountEvent, AppLicence, Invoice, Plan, PlatformAdmin

log = logging.getLogger(__name__)
tr = S.tr


def _d(v):
    if not v:
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _iso(d):
    return d.isoformat() if d else None


def _num(v):
    return float(v) if v is not None else None


def plan_json(p):
    return {'id': p.id, 'code': p.code, 'name': p.name, 'included_companies': p.included_companies, 'max_companies': p.max_companies,
            'extra_companies': p.extra_companies, 'max_employees': p.max_employees, 'max_users': p.max_users, 'storage_gb': p.storage_gb,
            'group_features': p.group_features, 'hosting': p.hosting, 'hosting_label': p.get_hosting_display(),
            'price_year': _num(p.price_year), 'extra_company_price': _num(p.extra_company_price), 'app_price': _num(p.app_price), 'active': p.active}


APPS_INFO = [
    ('payroll', 'Payroll', 'Salaries, payslips, WPS, gratuity, loans and final settlement'),
    ('projects', 'Projects', 'Projects, tasks, timesheets, tickets with SLA, AMC'),
    ('crm', 'CRM', 'Contacts, pipelines, opportunities, activities'),
    ('property', 'Property', 'Buildings, units, leases, billing, cheques'),
    ('maintenance', 'Maintenance', 'Work orders, inspections, preventive plans'),
    ('hse', 'HSE', 'Risk, permits, incidents, inspections, audits'),
]


def account_json(acc, full=False, user=None):
    st = S.status_of(acc)
    out = {'id': acc.id, 'name': acc.name, 'owner_email': acc.owner_email, 'owner_name': acc.owner_name, 'owner_id': acc.owner_id, 'plan': plan_json(acc.plan),
           'status': st['status'], 'read_only': st['read_only'], 'days_left': st['days_left'], 'until': st['until'],
           'state': acc.state, 'trial_ends': _iso(acc.trial_ends), 'renews_on': _iso(acc.renews_on), 'grace_days': acc.grace_days,
           'hosting': acc.hosting, 'hosting_label': acc.get_hosting_display(), 'created_at': acc.created_at.isoformat()}
    if full:
        out.update(trn=acc.trn, country=acc.country, notes=acc.notes, allowed_email_domain=acc.allowed_email_domain,
                   extra_companies=acc.extra_companies, max_employees=acc.max_employees, max_users=acc.max_users, storage_gb=acc.storage_gb,
                   limits=S.limits(acc), usage=S.usage(acc), apps=S.apps_for(acc),
                   companies=[{'id': l.company_id, 'name': l.company.name, 'schema_name': l.company.schema_name, 'extra': l.extra,
                               'archived': l.archived, 'where': 'Own database' if l.db_alias else 'Shared cloud', 'db_alias': l.db_alias,
                               'apps': _apps_of(l.company.schema_name)} for l in S.company_links(acc, include_archived=True)],
                   invoices=[invoice_json(i) for i in acc.invoices.all()[:50]],
                   database=_account_db(acc),
                   history=[{'at': e.at.isoformat(), 'kind': e.kind, 'text': e.text, 'by': e.by.username if e.by else ''}
                            for e in acc.events.select_related('by')[:100]])
    return out


def _account_db(acc):
    """v1.24.0: the own database of the account, if any."""
    from .models import DatabaseServer
    srv = DatabaseServer.objects.filter(account=acc).exclude(state='retired').first()
    return {'key': srv.key, 'name': srv.name, 'state': srv.state, 'note': srv.note[:300]} if srv else None


def _apps_of(schema):
    from django_tenants.utils import schema_context
    try:
        with schema_context(schema):
            from ZeoApps.services import APPS, is_active
            return [a[0] for a in APPS if is_active(a[0])]
    except Exception:
        return []


def invoice_json(i):
    return {'id': i.id, 'number': i.number, 'issued_on': _iso(i.issued_on), 'period_from': _iso(i.period_from), 'period_to': _iso(i.period_to),
            'amount': _num(i.amount), 'currency': i.currency, 'state': i.state, 'paid_on': _iso(i.paid_on), 'odoo_ref': i.odoo_ref, 'note': i.note}


# ------------------------------------------------------------------ permissions
class Base(APIView):
    permission_classes = [IsAuthenticated]

    def account(self, request, pk, owner=True):
        acc = Account.objects.select_related('plan').filter(pk=pk).first()
        if acc is None:
            return None, Response({'detail': tr('Not found.')}, status=404)
        if S.is_platform_admin(request.user):
            return acc, None
        if owner and acc.owner_id != request.user.pk:
            return None, Response({'detail': tr('Only the account owner can do this.')}, status=403)
        if not owner and not request.user.tenants.filter(cloud__account=acc).exists() and acc.owner_id != request.user.pk:
            return None, Response({'detail': tr('Not found.')}, status=404)
        return acc, None


class ConsoleBase(APIView):
    permission_classes = [IsAuthenticated]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not S.is_platform_admin(request.user):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied(tr('The control console is for Adept staff only.'))


# ------------------------------------------------------------------ company list (UserManagement.MyCompaniesView adds this)
def my_companies_extra(request, res):
    u = request.user
    res['cloud'] = S.cloud_on()
    if not res['cloud']:   # a customer's own server: the list stays as in 1.22.3
        res.update(platform_admin=False, accounts=[], notices=[])
        return res
    res['platform_admin'] = S.is_platform_admin(u)
    links = {l.company_id: l for l in AccountCompany.objects.select_related('account', 'account__plan')
             .filter(company_id__in=[c['id'] for c in res['companies']])}
    accounts = {}
    keep = []
    for c in res['companies']:
        l = links.get(c['id'])
        if l is None:
            c['account'] = None
            keep.append(c)
            continue
        acc = l.account
        st = S.status_of(acc)
        if st['blocked'] and not res['platform_admin']:
            continue   # cancelled: sign-in to its companies is closed
        own = acc.owner_id == u.pk
        c['account'] = acc.id
        c['extra'] = l.extra
        c['where'] = ('Own database' if l.db_alias else 'Shared cloud') if own or res['platform_admin'] else ''
        c['read_only'] = st['read_only']
        c['moving'] = l.moving   # v1.24.0
        keep.append(c)
        if acc.id not in accounts:
            ok, extra, msg = S.check_company_add(acc) if own else (False, False, '')
            accounts[acc.id] = {'id': acc.id, 'name': acc.name, 'role': 'owner' if own else 'member', 'plan': acc.plan.name,
                                'plan_code': acc.plan.code, 'status': st['status'], 'read_only': st['read_only'], 'days_left': st['days_left'],
                                'group_features': acc.plan.group_features, 'can_create': bool(own and ok), 'next_is_extra': bool(extra),
                                'create_note': msg, 'banner': S.banner_of(acc, owner=own)}
    res['companies'] = keep
    # own account first, then by name
    res['accounts'] = sorted(accounts.values(), key=lambda a: (a['role'] != 'owner', a['name'].lower()))
    owned = Account.objects.filter(owner=u).exclude(state='cancelled')
    for acc in owned:   # an owner whose account has no company yet still sees it
        if acc.id not in accounts:
            ok, extra, msg = S.check_company_add(acc)
            res['accounts'].insert(0, {'id': acc.id, 'name': acc.name, 'role': 'owner', 'plan': acc.plan.name, 'plan_code': acc.plan.code,
                                       'status': S.status_of(acc)['status'], 'read_only': False, 'days_left': None,
                                       'group_features': acc.plan.group_features, 'can_create': ok, 'next_is_extra': extra, 'create_note': msg,
                                       'banner': S.banner_of(acc, owner=True)})
    if S.cloud_on() and not u.is_superuser:
        res['can_create'] = False   # in ZEO Cloud a company is created in an account (owner) or by Adept
    if not (res.get('user') or {}).get('name'):   # logins have no name field: the owner name of the account
        own = Account.objects.filter(owner=u).exclude(owner_name='').first()
        if own:
            res['user']['name'] = own.owner_name
    res['notices'] = [{'id': n.id, 'text': n.text, 'at': n.at.isoformat()} for n in AccessNotice.objects.filter(user=u, seen=False)[:5]]


def duplicate_check(u, src):
    """None = go on; a Response = refused."""
    if src is None or not S.cloud_on():
        return None
    link = AccountCompany.objects.select_related('account', 'account__plan').filter(company=src).first()
    if link is None:
        return None
    acc = link.account
    if acc.owner_id != u.pk and not S.is_platform_admin(u):
        return Response({'detail': tr('Only the account owner can duplicate a company.')}, status=403)
    ok, extra, msg = S.check_company_add(acc)
    if not ok:
        return Response({'detail': msg}, status=409)
    return None


class NoticesSeenView(Base):
    def post(self, request):
        AccessNotice.objects.filter(user=request.user, seen=False).update(seen=True)
        return Response({'ok': True})


# ------------------------------------------------------------------ sign-up (public)
class SignupPlansView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        S.ensure_plans()
        from UserManagement.models import company as C
        return Response({'plans': [plan_json(p) for p in Plan.objects.filter(active=True)],
                         'apps': [{'key': k, 'title': t, 'description': d} for k, t, d in APPS_INFO],
                         'industries': [{'code': k, 'label': str(v)} for k, v in C.INDUSTRY_CHOICES],
                         'trial_days': 14, 'cloud': S.cloud_on()})


EMAIL = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


class SignupView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        if not S.cloud_on():
            return Response({'detail': tr('Sign-up is not available on this server.')}, status=404)
        ip = request.META.get('HTTP_X_FORWARDED_FOR', request.META.get('REMOTE_ADDR', '')).split(',')[0].strip()
        k = f'zeo:signup:{ip}'
        n = cache.get(k, 0)
        if n >= 5:
            return Response({'detail': tr('Too many sign-ups from this address. Try again in an hour.')}, status=429)
        d = request.data
        errs = {}
        company = ' '.join(str(d.get('company') or '').split())[:100]
        name = ' '.join(str(d.get('name') or '').split())[:150]
        email = str(d.get('email') or '').strip().lower()
        pw = str(d.get('password') or '')
        apps = [a for a in (d.get('apps') or []) if a in APP_KEYS]
        plan = Plan.objects.filter(code=str(d.get('plan') or ''), active=True).first()
        if len(company) < 2:
            errs['company'] = tr('Enter the company name.')
        if not name:
            errs['name'] = tr('Enter your name.')
        if not EMAIL.match(email):
            errs['email'] = tr('Enter a valid e-mail address.')
        if plan is None:
            errs['plan'] = tr('Choose a plan.')
        if not apps:
            errs['apps'] = tr('Select at least one app.')
        from UserManagement.models import CustomUser, company as C
        if email and CustomUser.objects.filter(Q(email__iexact=email) | Q(username__iexact=email)).exists():
            errs['email'] = tr('This e-mail already has a ZEO login. Sign in and create the company from your company list.')
        if company and C.objects.filter(name__iexact=company).exists():
            errs['company'] = tr('A company with this name exists already.')
        try:
            validate_password(pw)
        except DjValidationError as e:
            errs['password'] = ' '.join(str(m) for m in e.messages)
        if errs:
            return Response({'detail': next(iter(errs.values())), 'errors': errs}, status=400)
        cache.set(k, n + 1, 3600)
        user = CustomUser.objects.create_user(username=email, password=pw, email=email)
        acc = S.create_account(company, email, plan, by=None, owner=user, apps=apps)
        acc.owner_name = name
        acc.save(update_fields=['owner_name'])
        job, schema = S.start_company(acc, company, str(d.get('industry') or 'OTH')[:10], apps, user)
        return Response({'job': job, 'schema': schema, 'account': acc.id, 'username': user.username}, status=202)


class JobView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, job):
        st = S.job_status(job)
        if not st:
            return Response({'detail': tr('Not found.')}, status=404)
        return Response({k: st.get(k) for k in ('state', 'step', 'step_no', 'steps', 'name', 'schema', 'company_id', 'error', 'failed')})


# ------------------------------------------------------------------ the open company: banner and licences
class StatusView(Base):
    def get(self, request):
        schema = request.GET.get('schema') or ''
        u = request.user
        link = S.link_of_schema(schema)
        out = {'cloud': S.cloud_on(), 'linked': False, 'platform_admin': S.is_platform_admin(u)}
        if link is None or not u.tenants.filter(schema_name=schema).exists() and not out['platform_admin']:
            return Response(out)
        acc = link.account
        own = acc.owner_id == u.pk
        st = S.status_of(acc)
        out.update(linked=True, owner=own, account={'id': acc.id, 'name': acc.name, 'plan': acc.plan.name, 'plan_code': acc.plan.code,
                                                    'group_features': acc.plan.group_features},
                   status=st['status'], read_only=st['read_only'], days_left=st['days_left'], until=st['until'],
                   banner=S.banner_of(acc, owner=own), apps=S.apps_for(acc, schema), extra=link.extra)
        return Response(out)


# ------------------------------------------------------------------ owner
class SubscriptionView(Base):
    def get(self, request, pk):
        acc, err = self.account(request, pk)
        if err:
            return err
        out = account_json(acc, full=True)
        out['banner'] = S.banner_of(acc, owner=True)
        out['next_company'] = dict(zip(('ok', 'extra', 'note'), S.check_company_add(acc)))
        out.pop('notes', None)
        return Response(out)


class AccountCompanyCreateView(Base):
    def post(self, request, pk):
        acc, err = self.account(request, pk)
        if err:
            return err
        ok, extra, msg = S.check_company_add(acc)
        if not ok:
            return Response({'detail': msg}, status=409)
        d = request.data
        name = ' '.join(str(d.get('name') or '').split())[:100]
        if len(name) < 2:
            return Response({'detail': tr('Enter the company name.')}, status=400)
        from UserManagement.models import company as C
        if C.objects.filter(name__iexact=name).exists():
            return Response({'detail': tr('A company with this name exists already.')}, status=400)
        apps = [a for a in (d.get('apps') or []) if a in APP_KEYS]
        if not apps:
            return Response({'detail': tr('Select at least one app.')}, status=400)
        for a in apps:   # only licensed apps with a free place
            ok2, m2 = S.can_activate(acc, a, None)
            if not ok2:
                return Response({'detail': m2}, status=409)
        if extra and not S.is_platform_admin(request.user) and acc.extra_companies < len([l for l in S.company_links(acc) if l.extra]) + 1:
            acc.extra_companies = len([l for l in S.company_links(acc) if l.extra]) + 1   # the owner accepted the extra charge
            acc.save(update_fields=['extra_companies'])
            S.event(acc, 'extra', f'Extra company accepted by the owner – {name}', request.user)
        job, schema = S.start_company(acc, name, str(d.get('industry') or 'OTH')[:10], apps, request.user, extra=extra, country_id=d.get('country'))
        return Response({'job': job, 'schema': schema, 'extra': extra}, status=202)


class RequestView(Base):
    def post(self, request, pk):
        acc, err = self.account(request, pk)
        if err:
            return err
        text = str(request.data.get('text') or '').strip()[:400]
        what = str(request.data.get('what') or 'upgrade')[:40]
        if not text:
            return Response({'detail': tr('Write what you need.')}, status=400)
        S.event(acc, 'request', f'Request ({what}): {text}', request.user)
        to = getattr(settings, 'ZEO_CLOUD_ADMIN_EMAIL', '') or ''
        if to:
            from django.core.mail import send_mail
            send_mail(f'ZEO Cloud request – {acc.name}', f'{acc.name} ({acc.owner_email}) asks: {what}\n\n{text}', None, [to], fail_silently=True)
        return Response({'ok': True})


class SecurityView(Base):
    def get(self, request, pk):
        acc, err = self.account(request, pk)
        if err:
            return err
        return Response({'allowed_email_domain': acc.allowed_email_domain})

    def put(self, request, pk):
        acc, err = self.account(request, pk)
        if err:
            return err
        dom = str(request.data.get('allowed_email_domain') or '').strip().lower().lstrip('@')
        if dom and not S.VALID_DOMAIN.match(dom):
            return Response({'detail': tr('Enter a domain like sahragroup.ae.')}, status=400)
        if dom != acc.allowed_email_domain:
            S.event(acc, 'security', f'Only company e-mails: {"@" + dom if dom else "any e-mail"}', request.user)
        acc.allowed_email_domain = dom
        acc.save(update_fields=['allowed_email_domain'])
        return Response({'allowed_email_domain': dom})


# ------------------------------------------------------------------ Adept console
class ConsoleMeView(Base):
    def get(self, request):
        return Response({'platform_admin': S.is_platform_admin(request.user), 'cloud': S.cloud_on()})


class ConsoleCustomersView(ConsoleBase):
    def get(self, request):
        qs = Account.objects.select_related('plan').order_by('name')
        q = (request.GET.get('q') or '').strip()
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(owner_email__icontains=q) | Q(companies__company__name__icontains=q)).distinct()
        if request.GET.get('plan'):
            qs = qs.filter(plan__code=request.GET['plan'])
        rows = []
        for acc in qs[:500]:
            j = account_json(acc)
            j['usage'] = S.usage(acc)
            j['limits'] = S.limits(acc)
            rows.append(j)
        st = request.GET.get('status')
        if st:
            rows = [r for r in rows if r['status'] == st]
        if request.GET.get('renewal') == '30':
            soon = (timezone.localdate() + timedelta(days=30)).isoformat()
            rows = [r for r in rows if (r['renews_on'] or r['trial_ends'] or '9999') <= soon]
        counts = {}
        for r in rows:
            counts[r['status']] = counts.get(r['status'], 0) + 1
        return Response({'customers': rows, 'counts': counts, 'total': len(rows)})

    def post(self, request):
        d = request.data
        plan = Plan.objects.filter(code=str(d.get('plan') or '')).first()
        name = ' '.join(str(d.get('name') or '').split())
        email = str(d.get('owner_email') or '').strip().lower()
        if not name or plan is None or not EMAIL.match(email):
            return Response({'detail': tr('Enter the legal name, a valid owner e-mail and the plan.')}, status=400)
        from UserManagement.models import CustomUser
        owner = CustomUser.objects.filter(Q(email__iexact=email) | Q(username__iexact=email)).first()
        invited = False
        if owner is None:
            import secrets
            owner = CustomUser.objects.create_user(username=email, password=secrets.token_urlsafe(16), email=email, must_change_password=True)
            invited = True
        start = _d(d.get('start')) if d.get('start_mode') == 'paid' else None
        acc = S.create_account(name, email, plan, by=request.user, owner=owner, trial_days=int(d.get('trial_days') or 14), trn=d.get('trn') or '',
                               country=d.get('country') or 'United Arab Emirates', apps=d.get('apps') or [],
                               limits={k: d.get(k) for k in ('max_employees', 'max_users', 'storage_gb')}, start=start)
        if d.get('owner_name'):
            acc.owner_name = str(d['owner_name'])[:150]
            acc.save(update_fields=['owner_name'])
        out = {'account': account_json(acc, full=True), 'invited': invited}
        comp = d.get('company') or {}
        if comp.get('name'):
            job, schema = S.start_company(acc, comp['name'], comp.get('industry') or 'OTH', d.get('apps') or [], request.user, grant_creator=False)
            out.update(job=job, schema=schema)
        if invited:
            _invite(owner, acc)
        return Response(out, status=201)


def _invite(user, acc):
    """E-mail the new owner how to set a password (the reset-password flow of the sign-in page)."""
    try:
        from django.core.mail import send_mail
        send_mail(tr('Your ZEO account {0}', acc.name),
                  tr('Adept created the ZEO account {0} for you. Open ZEO, choose "Forgot password" and enter {1} to set your password.', acc.name, user.email),
                  None, [user.email], fail_silently=True)
    except Exception:
        log.exception('invite')


class ConsoleCustomerView(ConsoleBase):
    FIELDS = ('name', 'trn', 'country', 'notes', 'grace_days', 'extra_companies', 'max_employees', 'max_users', 'storage_gb', 'allowed_email_domain')

    def get(self, request, pk):
        acc = Account.objects.select_related('plan').filter(pk=pk).first()
        if acc is None:
            return Response({'detail': tr('Not found.')}, status=404)
        return Response(account_json(acc, full=True))

    def patch(self, request, pk):
        acc = Account.objects.select_related('plan').filter(pk=pk).first()
        if acc is None:
            return Response({'detail': tr('Not found.')}, status=404)
        d = request.data
        changes = []
        for f in self.FIELDS:
            if f in d:
                v = d[f]
                if f in ('max_employees', 'max_users', 'storage_gb'):
                    v = int(v) if v not in (None, '') else None
                elif f in ('grace_days', 'extra_companies'):
                    v = int(v or 0)
                if getattr(acc, f) != v:
                    changes.append(f'{f}: {getattr(acc, f)} → {v}')
                    setattr(acc, f, v)
        for f in ('renews_on', 'trial_ends'):
            if f in d:
                v = _d(d[f])
                if getattr(acc, f) != v:
                    changes.append(f'{f}: {getattr(acc, f)} → {v}')
                    setattr(acc, f, v)
        if 'plan' in d:   # same hosting only – a change between shared and own database goes through Change plan (moves)
            p = Plan.objects.filter(code=d['plan']).first()
            if p and p.id != acc.plan_id:
                if p.hosting != acc.plan.hosting and acc.companies.exists():
                    return Response({'detail': tr('This plan change moves companies between the shared cloud and an own database – use Change plan.')}, status=409)
                n = len(S.company_links(acc))
                if p.max_companies is not None and n > p.max_companies:
                    return Response({'detail': tr('Blocked: the account has {0} companies and {1} allows {2}.', n, p.name, p.max_companies)}, status=409)
                changes.append(f'plan: {acc.plan.name} → {p.name}')
                acc.plan, acc.hosting = p, p.hosting
        acc.save()
        if changes:
            S.event(acc, 'change', '; '.join(changes)[:500], request.user)
        cache.delete(f'zeo:cloud:usage:{acc.pk}')
        return Response(account_json(acc, full=True))


class ConsoleActionView(ConsoleBase):
    def post(self, request, pk):
        acc = Account.objects.select_related('plan').filter(pk=pk).first()
        if acc is None:
            return Response({'detail': tr('Not found.')}, status=404)
        a = request.data.get('action')
        if a == 'suspend':
            acc.state = 'suspended'
            text = 'Suspended (read-only)'
        elif a == 'reactivate':
            acc.state = 'active'
            if not acc.renews_on or acc.renews_on < timezone.localdate():
                acc.renews_on = timezone.localdate() + timedelta(days=int(request.data.get('days') or 30))
            text = f'Reactivated until {acc.renews_on}'
        elif a == 'cancel':
            acc.state = 'cancelled'
            text = 'Cancelled – data kept 90 days'
        elif a == 'extend':
            to = _d(request.data.get('until'))
            if acc.state == 'trial':
                acc.trial_ends = to or (acc.trial_ends or timezone.localdate()) + timedelta(days=int(request.data.get('days') or 14))
                text = f'Trial extended to {acc.trial_ends}'
            else:
                acc.renews_on = to or (acc.renews_on or timezone.localdate()) + timedelta(days=int(request.data.get('days') or 30))
                text = f'Renewal extended to {acc.renews_on}'
        elif a == 'activate':   # trial → active without an invoice (Adept's decision)
            acc.state = 'active'
            acc.renews_on = _d(request.data.get('until')) or timezone.localdate() + timedelta(days=365)
            acc.apps.filter(trial=True).update(trial=False, valid_until=None)
            text = f'Activated until {acc.renews_on}'
        else:
            return Response({'detail': tr('Unknown action.')}, status=400)
        acc.save()
        S.event(acc, a, text, request.user)
        for l in S.company_links(acc, include_archived=True):
            S.forget(l.company.schema_name)
        return Response(account_json(acc, full=True))


class ConsoleAppsView(ConsoleBase):
    def put(self, request, pk):
        acc = Account.objects.filter(pk=pk).first()
        if acc is None:
            return Response({'detail': tr('Not found.')}, status=404)
        d = request.data
        app = d.get('app')
        if app not in APP_KEYS:
            return Response({'detail': tr('Unknown app.')}, status=400)
        lic = AppLicence.objects.filter(account=acc, app=app).first() or AppLicence(account=acc, app=app)
        lic.licensed = bool(d.get('licensed', True))
        lic.trial = bool(d.get('trial', False))
        lic.companies = int(d['companies']) if d.get('companies') not in (None, '') else None
        lic.users = int(d['users']) if d.get('users') not in (None, '') else None
        lic.valid_until = _d(d.get('valid_until'))
        if lic.trial and not lic.valid_until:
            lic.valid_until = timezone.localdate() + timedelta(days=int(d.get('trial_days') or 30))
        lic.price = d.get('price') if d.get('price') not in (None, '') else None
        lic.save()
        S.event(acc, 'app', f'{S.APP_TITLES.get(app, app)}: ' + ('not licensed' if not lic.licensed else
                ('trial until ' + str(lic.valid_until) if lic.trial else 'licensed') + (f', {lic.companies} companies' if lic.companies else ', all companies')
                + (f', {lic.users} users' if lic.users else '')), request.user)
        cache.delete_many([f'zeo:cloud:lic:{acc.pk}:{app}'])
        return Response(account_json(acc, full=True))


class ConsoleInvoicesView(ConsoleBase):
    def post(self, request, pk):
        acc = Account.objects.filter(pk=pk).first()
        if acc is None:
            return Response({'detail': tr('Not found.')}, status=404)
        d = request.data
        num = str(d.get('number') or '').strip()
        if not num:
            return Response({'detail': tr('Enter the invoice number (from Odoo).')}, status=400)
        inv = Invoice.objects.create(account=acc, number=num[:40], amount=d.get('amount') or 0, currency=(d.get('currency') or 'AED')[:3],
                                     period_from=_d(d.get('period_from')), period_to=_d(d.get('period_to')), odoo_ref=str(d.get('odoo_ref') or '')[:60],
                                     note=str(d.get('note') or '')[:255])
        S.event(acc, 'invoice', f'Invoice {inv.number} added ({inv.amount} {inv.currency})', request.user)
        if d.get('paid'):
            S.mark_paid(inv, request.user)
        return Response(account_json(Account.objects.get(pk=acc.pk), full=True), status=201)


class ConsoleInvoicePaidView(ConsoleBase):
    def post(self, request, pk):
        inv = Invoice.objects.select_related('account').filter(pk=pk).first()
        if inv is None:
            return Response({'detail': tr('Not found.')}, status=404)
        acc = S.mark_paid(inv, request.user)
        for l in S.company_links(acc, include_archived=True):
            S.forget(l.company.schema_name)
        return Response(account_json(Account.objects.get(pk=acc.pk), full=True))


class ConsoleCompaniesView(ConsoleBase):
    """POST {name, industry, apps} – Adept creates a company for the account; POST {link: company_id} – link an existing one."""
    def post(self, request, pk):
        acc = Account.objects.select_related('plan').filter(pk=pk).first()
        if acc is None:
            return Response({'detail': tr('Not found.')}, status=404)
        d = request.data
        if d.get('link'):
            from UserManagement.models import company as C
            comp = C.objects.filter(pk=d['link']).first()
            if comp is None or AccountCompany.objects.filter(company=comp).exists():
                return Response({'detail': tr('This company is already in an account.')}, status=409)
            ok, extra, msg = S.check_company_add(acc)
            AccountCompany.objects.create(company=comp, account=acc, extra=extra)
            if acc.owner:
                S.grant_admin(acc.owner, comp)
            S.forget(comp.schema_name)
            S.event(acc, 'company', f'Existing company linked – {comp.name}', request.user)
            return Response(account_json(acc, full=True))
        if d.get('archive') is not None:
            l = AccountCompany.objects.filter(account=acc, company_id=d.get('company')).first()
            if l is None:
                return Response({'detail': tr('Not found.')}, status=404)
            l.archived = bool(d['archive'])
            l.save(update_fields=['archived'])
            S.event(acc, 'company', f'{"Archived" if l.archived else "Restored"} – {l.company.name}', request.user)
            return Response(account_json(acc, full=True))
        ok, extra, msg = S.check_company_add(acc)
        if not ok:
            return Response({'detail': msg}, status=409)
        name = ' '.join(str(d.get('name') or '').split())[:100]
        if len(name) < 2:
            return Response({'detail': tr('Enter the company name.')}, status=400)
        job, schema = S.start_company(acc, name, d.get('industry') or 'OTH', d.get('apps') or [], request.user, extra=extra, grant_creator=False)
        return Response({'job': job, 'schema': schema, 'extra': extra}, status=202)


class ConsoleUnlinkedView(ConsoleBase):
    def get(self, request):
        from UserManagement.models import company as C
        qs = C.objects.exclude(schema_name__in=('public', S.TEMPLATE)).exclude(cloud__isnull=False).order_by('name')
        return Response([{'id': c.id, 'name': c.name, 'schema_name': c.schema_name} for c in qs[:500]])


class ConsolePlansView(ConsoleBase):
    def get(self, request):
        S.ensure_plans()
        return Response({'plans': [plan_json(p) for p in Plan.objects.all()]})

    def put(self, request):
        d = request.data
        p = Plan.objects.filter(code=d.get('code')).first()
        if p is None:
            return Response({'detail': tr('Not found.')}, status=404)
        for f in ('name', 'included_companies', 'max_companies', 'max_employees', 'max_users', 'storage_gb', 'price_year', 'extra_company_price', 'app_price'):
            if f in d:
                v = d[f]
                setattr(p, f, None if v in ('', None) else v)
        for f in ('extra_companies', 'group_features', 'active'):
            if f in d:
                setattr(p, f, bool(d[f]))
        p.save()
        return Response({'plans': [plan_json(x) for x in Plan.objects.all()]})


# ------------------------------------------------------------------ v1.24.0 own databases and the Move tool (console)
def move_json(j):
    return {'id': j.id, 'schema': j.schema, 'company': j.company.name if j.company else j.schema, 'company_id': j.company_id,
            'source': j.source, 'target': j.target, 'from': 'Own database' if j.source else 'Shared cloud', 'to': 'Own database' if j.target else 'Shared cloud',
            'state': j.state, 'step': j.step, 'tables': j.tables, 'rows': j.rows, 'old_copy': j.old_copy,
            'keep_until': _iso(j.keep_until), 'error': j.error, 'log': j.log,
            'started_at': j.started_at.isoformat() if j.started_at else None,
            'finished_at': j.finished_at.isoformat() if j.finished_at else None,
            'by': j.created_by.username if j.created_by else '', 'can_back': j.state == 'done' and bool(j.company_id)}


def _db_size(srv):
    from . import owndb
    try:
        with owndb.conn(srv.key).cursor() as cur:
            cur.execute('SELECT pg_database_size(current_database())')
            return round(cur.fetchone()[0] / 1024 / 1024, 1)
    except Exception:
        return None


def server_json(srv, size=False):
    return {'key': srv.key, 'name': srv.name, 'label': srv.label, 'host': srv.host or '(main server)', 'state': srv.state,
            'account': srv.account.name if srv.account else '', 'account_id': srv.account_id, 'note': srv.note[:300],
            'companies': AccountCompany.objects.filter(db_alias=srv.key).count(), 'created_at': srv.created_at.isoformat(),
            'size_mb': _db_size(srv) if size and srv.state == 'ready' else None}


class ConsoleDatabasesView(ConsoleBase):
    def get(self, request):
        from .models import DatabaseServer
        from django.db import connections
        main = connections['control'].settings_dict['NAME']
        with connections['control'].cursor() as cur:
            cur.execute('SELECT pg_database_size(current_database())')
            main_mb = round(cur.fetchone()[0] / 1024 / 1024, 1)
        shared = AccountCompany.objects.filter(db_alias='').count()
        return Response({'main': {'name': main, 'size_mb': main_mb, 'companies': shared},
                         'databases': [server_json(s, size=True) for s in DatabaseServer.objects.select_related('account')]})


class ConsoleMovesView(ConsoleBase):
    """GET – moves of the account; POST {company, to: 'own'|'shared'} – move one company."""
    def get(self, request, pk):
        from .models import MoveJob
        return Response([move_json(j) for j in MoveJob.objects.filter(account_id=pk).select_related('company', 'created_by')[:50]])

    def post(self, request, pk):
        from . import owndb
        acc = Account.objects.select_related('plan').filter(pk=pk).first()
        if acc is None:
            return Response({'detail': tr('Not found.')}, status=404)
        link = AccountCompany.objects.select_related('company').filter(account=acc, company_id=request.data.get('company')).first()
        if link is None:
            return Response({'detail': tr('Not found.')}, status=404)
        to = request.data.get('to')
        try:
            if to == 'own':
                if acc.plan.hosting != 'own':
                    return Response({'detail': tr('Own databases are part of Premium – change the plan first.')}, status=409)
                target = owndb.create_own_db(acc, by=request.user).key
            elif to == 'shared':
                target = ''
            else:
                return Response({'detail': tr('Choose where the company goes.')}, status=400)
            job = owndb.start_move(link.company, target, by=request.user, account=acc)
        except owndb.MoveError as e:
            return Response({'detail': tr(str(e))}, status=409)
        S.event(acc, 'move', f'Move started – {link.company.name} to {target or "the shared cloud"}', request.user, job=job.id)
        return Response(move_json(job), status=202)


class ConsoleMoveView(ConsoleBase):
    """GET – one move (progress); POST {back: true} – roll it back (the company returns with its current data)."""
    def get(self, request, pk):
        from .models import MoveJob
        j = MoveJob.objects.select_related('company', 'created_by').filter(pk=pk).first()
        if j is None:
            return Response({'detail': tr('Not found.')}, status=404)
        return Response(move_json(j))

    def post(self, request, pk):
        from . import owndb
        from .models import MoveJob
        j = MoveJob.objects.select_related('company', 'account').filter(pk=pk).first()
        if j is None:
            return Response({'detail': tr('Not found.')}, status=404)
        try:
            new = owndb.move_back(j, by=request.user)
        except owndb.MoveError as e:
            return Response({'detail': tr(str(e))}, status=409)
        if j.account:
            S.event(j.account, 'move', f'Move rolled back – {j.schema}', request.user, job=new.id)
        return Response(move_json(new), status=202)


class ConsoleChangePlanView(ConsoleBase):
    """POST {plan} – any plan change; to or from Premium moves every company (own database ↔ shared cloud)."""
    def post(self, request, pk):
        from . import owndb
        acc = Account.objects.select_related('plan').filter(pk=pk).first()
        if acc is None:
            return Response({'detail': tr('Not found.')}, status=404)
        p = Plan.objects.filter(code=request.data.get('plan')).first()
        if p is None:
            return Response({'detail': tr('Choose a plan.')}, status=400)
        if p.hosting == 'enterprise':
            return Response({'detail': tr('Enterprise runs on its own servers – Adept sets it up; it is not a plan change here.')}, status=409)
        links = S.company_links(acc)
        if p.max_companies is not None and len(links) > p.max_companies:
            return Response({'detail': tr('Blocked: the account has {0} companies and {1} allows {2}.', len(links), p.name, p.max_companies)}, status=409)
        from .models import MoveJob
        if MoveJob.objects.filter(account=acc, state__in=('waiting', 'running')).exists():
            return Response({'detail': tr('A move is still running for this account.')}, status=409)
        old = acc.plan
        where = [bool(l.db_alias) for l in links]
        to_own = p.hosting == 'own' and (not acc.db_alias or not all(where))
        from_own = p.hosting != 'own' and (any(where) or acc.hosting == 'own')
        acc.plan = p
        if not (to_own or from_own):
            acc.hosting = 'own' if acc.db_alias else p.hosting
        acc.save()
        moving = []
        try:
            if to_own or from_own:
                moving = owndb.change_hosting(acc, to_own, by=request.user)
        except Exception as e:
            acc.plan = old
            acc.save(update_fields=['plan'])
            return Response({'detail': tr('The own database could not be created: {0}', str(e)[:200])}, status=500)
        S.event(acc, 'plan', f'Plan {old.name} → {p.name}' + (f'; {len(moving)} companies moving to ' + ('the own database' if to_own else 'the shared cloud') if moving else ''), request.user)
        cache.delete(f'zeo:cloud:usage:{acc.pk}')
        out = account_json(acc, full=True)
        out['moving'] = moving
        return Response(out)
