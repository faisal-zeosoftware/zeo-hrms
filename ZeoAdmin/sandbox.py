"""v1.53.0 – a test company with the setup of this company, not its data (roadmap 67).

Settings → Test company: a new company "<name> – test" is made like any new company (the same country and apps), the chosen
setup is copied into it, and it is made safe – no e-mail leaves ZEO for it, the Projects mailbox is not read and outside AI
is off (the same as a neutralized duplicate, zeo/mailguard.py). The administrators of the company get access.

What can be copied:
  org        branches, departments, designations and categories
  leave_types, leave_policies, salary_components, letter_templates, form_designs   (ZeoGroup.operations, the group copy)
  (permission groups are shared by every company of the server, so they need no copy)
  automations  automation rules (copied switched off)
No employee, payroll, leave, attendance, request or other record is copied. Delete the test company when done (or make a
new one). Use a full duplicate (Choose a company → Duplicate) when the data is needed too.
"""
import logging
import threading

from django.apps import apps
from django.db import connection, transaction
from django_tenants.utils import schema_context

log = logging.getLogger(__name__)
KINDS = [
    ('org', 'Branches, departments, designations and categories'),
    ('leave_types', 'Leave types'),
    ('leave_policies', 'Leave policies'),
    ('salary_components', 'Salary components'),
    ('letter_templates', 'Letter templates'),
    ('form_designs', 'Form designs and extra fields'),
    ('automations', 'Automation rules (copied switched off)'),
]
ORG = [('OrganisationManager', 'brnch_mstr', 'branch_code', 'branch_name'), ('OrganisationManager', 'dept_master', 'dept_code', 'dept_name'),   # departments etc. have no branch link
       ('OrganisationManager', 'desgntn_master', 'desgntn_code', 'desgntn_job_title'), ('OrganisationManager', 'ctgry_master', 'ctgry_code', 'ctgry_title')]


def _plain(obj):
    from ZeoGroup.operations import _plain as p
    return p(obj)


def _first(model, *names):
    have = {f.name for f in model._meta.concrete_fields}
    return next((n for n in names if n in have), None)


def read(kinds):
    from ZeoGroup.operations import _read
    data = _read([k for k in kinds if k in ('leave_types', 'leave_policies', 'salary_components', 'letter_templates', 'form_designs')])
    if 'org' in kinds:
        data['_org'] = {}
        for app, name, code_f, name_f in ORG:
            m = apps.get_model(app, name)
            rows = []
            for o in m.objects.all().order_by('id'):
                d = _plain(o)
                # links: branch by its code (departments …), every other link (employees, users) is dropped
                for f in m._meta.concrete_fields:
                    if f.is_relation and f.related_model is not None and f.related_model._meta.model_name == 'brnch_mstr':
                        b = getattr(o, f.name, None)
                        d['_branch'] = (f.attname, getattr(b, 'branch_code', None) if b else None)
                rows.append(d)
            data['_org'][name] = rows
    if 'automations' in kinds:
        W = _model('ZeoFlow', 'Workflow')
        if W is not None:
            data['_flows'] = [_plain(w) for w in W.objects.all()]
    return data


def _model(app, name):
    try:
        return apps.get_model(app, name)
    except LookupError:
        return None


def write(data, kinds):
    from ZeoGroup.operations import _write
    res = _write(data, [k for k in kinds if k in ('leave_types', 'leave_policies', 'salary_components', 'letter_templates', 'form_designs')], False)
    if 'org' in kinds:
        n = 0
        Branch = apps.get_model('OrganisationManager', 'brnch_mstr')
        for app, name, code_f, name_f in ORG:
            m = apps.get_model(app, name)
            have = list(m.objects.all())
            for d in data.get('_org', {}).get(name, []):
                d = dict(d)
                br = d.pop('_branch', None)
                code, nm = (d.get(code_f) or '').strip().lower(), (d.get(name_f) or '').strip().lower()
                if any((code and (getattr(o, code_f, '') or '').strip().lower() == code) or (nm and (getattr(o, name_f, '') or '').strip().lower() == nm) for o in have):
                    continue
                if br:
                    b = Branch.objects.filter(branch_code=br[1]).first() if br[1] else None
                    if b is None and name != 'brnch_mstr':
                        b = Branch.objects.order_by('id').first()
                    d[br[0]] = b.pk if b else None
                for f in m._meta.concrete_fields:   # required links that cannot be mapped: leave the row out
                    if f.is_relation and not f.null and f.attname not in d:
                        d = None
                        break
                if d is None:
                    continue
                try:
                    with transaction.atomic():
                        o = m(**d)
                        o.save()
                    have.append(o)
                    n += 1
                except Exception:
                    log.exception('copy %s to the test company', name)
        res['org'] = {'added': n}
    if 'automations' in kinds:
        W = _model('ZeoFlow', 'Workflow')
        n = 0
        if W is not None:
            names = set(W.objects.values_list('name', flat=True))
            for d in data.get('_flows') or []:
                d = dict(d)
                if d.get('name') in names:
                    continue
                d['state'] = 'inactive'   # switched off in the test company until someone turns it on
                for k in ('runs_count', 'errors_count'):
                    if k in d:
                        d[k] = 0
                for k in ('last_run_at', 'published_at'):
                    if k in d:
                        d[k] = None
                try:
                    with transaction.atomic():
                        W.objects.create(**d)
                    n += 1
                except Exception:
                    log.exception('copy automation to the test company')
        res['automations'] = {'added': n}
    return res


def start(src, user, kinds, name=None):
    from .models import TestCompany
    kinds = [k for k, _ in KINDS if k in (kinds or [])]
    if not kinds:
        raise ValueError('Choose what to copy.')
    name = (name or f'{src.name} – test')[:100]
    from UserManagement.models import company
    if company.objects.filter(name__iexact=name).exists():
        raise ValueError('A company with this name exists already – give the test company another name.')
    t = TestCompany.objects.create(name=name, kinds=kinds, by=user, step='Making the company')
    threading.Thread(target=_run, args=(connection.schema_name, t.pk, src.pk, name, kinds, user.pk), daemon=True).start()
    return t


def _set(source_schema, pk, **kw):
    from .models import TestCompany
    with schema_context(source_schema):
        TestCompany.objects.filter(pk=pk).update(**kw)


def _run(source_schema, pk, src_id, name, kinds, user_id):
    from django.db import close_old_connections
    close_old_connections()
    schema = ''
    try:
        from UserManagement.company_jobs import start as make, status
        from UserManagement.models import company, CustomUser
        from ZeoApps.duplicate import neutralize_company, schema_for
        connection.set_schema_to_public()
        src = company.objects.get(pk=src_id)
        user = CustomUser.objects.filter(pk=user_id).first()
        with schema_context(source_schema):
            from ZeoApps.services import active_keys
            keys = list(active_keys() or [])
            data = read(kinds)
        schema = schema_for(name)
        _set(source_schema, pk, schema_name=schema, step='Making the company')
        vd = {'name': name, 'schema_name': schema, 'country': src.country}
        for f in ('currency', 'state', 'industry_type', 'city', 'timezone', 'financial_year'):
            v = getattr(src, f, None)
            if v not in (None, ''):
                vd[f] = v
        job, schema = make(vd, keys or ['hrms', 'payroll'], user, run_now=True)
        st = status(job) or {}
        if st.get('state') != 'done':
            raise RuntimeError(st.get('error') or 'The company could not be made.')
        connection.set_schema_to_public()
        new = company.objects.get(pk=st['company_id'])
        try:   # ZEO Cloud: the test company belongs to the same account
            from ZeoCloud.models import AccountCompany
            sl = AccountCompany.objects.filter(company=src).select_related('account').first()
            if sl is not None and not AccountCompany.objects.filter(company=new).exists():
                AccountCompany.objects.create(company=new, account=sl.account, extra=False, db_alias=sl.db_alias)
        except Exception:
            log.exception('cloud link of the test company')
        for u in CustomUser.objects.filter(tenants=src, is_active=True):   # the administrators of the company
            try:
                from django_tenants.utils import schema_context as sc
                from tenant_users.permissions.models import UserTenantPermissions
                with sc(src.schema_name):
                    adm = UserTenantPermissions.objects.filter(profile=u, is_superuser=True).exists()
                if adm or u.is_superuser:
                    from ZeoCloud.services import grant_admin
                    grant_admin(u, new)
            except Exception:
                log.exception('access to the test company')
        _set(source_schema, pk, company_id=new.pk, step='Copying the setup')
        with schema_context(schema):
            res = write(data, kinds)
            from ZeoApps.models import CompanyOption
            CompanyOption.objects.update_or_create(key='test_company_of', defaults={'value': {'schema': src.schema_name, 'name': src.name, 'by': user_id}})
            neutralize_company(user_id)
        from zeo.mailguard import forget
        forget(schema)
        _set(source_schema, pk, state='done', step='Ready', result=res)
    except Exception as e:
        log.exception('test company failed')
        _set(source_schema, pk, state='failed', error=str(e)[:500], step='Failed')
    finally:
        try:
            connection.set_schema_to_public()
        except Exception:
            pass
        close_old_connections()


def delete(t, user):
    """Delete a test company made here (only a company marked as a test company of this one)."""
    from UserManagement.models import company
    if t.state not in ('done', 'failed'):
        raise ValueError('Wait until the test company is ready.')
    src_schema = connection.schema_name
    c = company.objects.filter(pk=t.company_id).first() if t.company_id else None
    if c is not None:
        with schema_context(c.schema_name):
            from ZeoApps.models import CompanyOption
            o = CompanyOption.objects.filter(key='test_company_of').first()
            ok = bool(o and (o.value or {}).get('schema') == src_schema)
        if not ok:
            raise ValueError('That company is not a test company of this company.')
        connection.set_schema_to_public()
        try:
            c.delete(force_drop=True)
        finally:
            connection.set_schema_to_public()
    with schema_context(src_schema):
        t.state, t.step = 'deleted', 'Deleted'
        t.save(update_fields=['state', 'step'])
