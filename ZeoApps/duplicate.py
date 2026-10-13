"""v1.22.3 – duplicate a company (like Odoo's "Duplicate database").

The company's schema is copied with all its data (PostgreSQL clone of every table, sequence and row), then the new
company is registered with the same settings (country, currency, industry, address, logo, WPS details …) and the same
users get access. Uploaded files are shared with the original (the copy points to the same files).

`neutralize=True` makes a safe test copy: no e-mail leaves ZEO for that company (zeo/mailguard.py), the Projects
mailbox is not read, and outside AI is switched off.

Runs in a background thread; the progress is kept in the cache: start() → job id, status(job) → {state, step, …}.
"""
import logging
import re
import threading
import uuid

from django.core.cache import cache
from django.db import connection, transaction

log = logging.getLogger(__name__)
KEY = 'zeo:dup:{}'
STEPS = ['Copying the data', 'Registering the company', 'Giving access to the users', 'Finishing']


def schema_for(name):
    """A free, valid schema name from the company name (letters, digits, underscore; starts with a letter)."""
    from UserManagement.models import company
    from django_tenants.utils import schema_exists
    base = re.sub(r'[^a-z0-9]+', '_', (name or '').lower()).strip('_')
    base = re.sub(r'^(pg_|[0-9_]+)', '', base) or 'company'
    base = ('c_' + base if not base[0].isalpha() else base)[:40].rstrip('_')
    s, n = base, 2
    while company.objects.filter(schema_name=s).exists() or schema_exists(s):
        s = f'{base[:36]}_{n}'
        n += 1
    return s


def _set(job, **kw):
    d = cache.get(KEY.format(job)) or {}
    d.update(kw)
    cache.set(KEY.format(job), d, 60 * 60 * 6)


def status(job):
    return cache.get(KEY.format(job))


def start(source, name, neutralize, user):
    job = uuid.uuid4().hex[:16]
    schema = schema_for(name)
    _set(job, state='running', step=STEPS[0], step_no=1, steps=len(STEPS), source=source.schema_name, name=name,
         schema=schema, neutralize=bool(neutralize), by=user.pk, company_id=None, error='')
    t = threading.Thread(target=_run, args=(job, source.pk, name, schema, bool(neutralize), user.pk), daemon=True)
    t.start()
    return job, schema


def _db_of(comp):
    """v1.24.0: own database key of a company ('' = the main database)."""
    try:
        from ZeoCloud.models import AccountCompany
        link = AccountCompany.objects.filter(company=comp).first() if comp else None
        return (link.db_alias or '') if link else ''
    except Exception:
        return ''


def _run(job, source_id, name, schema, neutralize, user_id):
    from django.db import close_old_connections
    close_old_connections()
    try:
        from django_tenants.utils import schema_context
        from django_tenants.clone import CloneSchema
        from UserManagement.models import company, CustomUser, Domain
        connection.set_schema_to_public()
        src = company.objects.get(pk=source_id)
        # 1. data – v1.24.0: in the database the company lives in (the copy stays next to it)
        db_key = _db_of(src)
        if db_key:
            from ZeoCloud.dbrouting import pin
            with pin(db_key):
                CloneSchema().clone_schema(src.schema_name, schema, clone_mode='DATA')
        else:
            CloneSchema().clone_schema(src.schema_name, schema, clone_mode='DATA')
        # 2. the company row (no automatic schema: it exists already, and no "new company" defaults)
        _set(job, step=STEPS[1], step_no=2)
        skip = {'id', 'schema_name', 'name', 'created_on', 'paid_until'}
        data = {f.attname: getattr(src, f.attname) for f in company._meta.concrete_fields if f.name not in skip}
        new = company(schema_name=schema, name=name[:100], **data)
        new.auto_create_schema = False
        with transaction.atomic():
            new.save()
            if not Domain.objects.filter(tenant=new).exists():   # company.save() usually makes <schema>.localhost
                base = (src.domains.filter(is_primary=True).first() or src.domains.first())
                host = (base.domain.split('.', 1)[1] if base and '.' in base.domain else 'localhost')
                Domain.objects.create(domain=f'{schema}.{host}'[:253], tenant=new, is_primary=True)
        try:   # v1.23.0 ZEO Cloud: the copy belongs to the same account (an extra company when the plan says so)
            from ZeoCloud.models import AccountCompany
            sl = AccountCompany.objects.filter(company=src).select_related('account', 'account__plan').first()
            if sl is not None:
                from ZeoCloud.services import check_company_add, forget
                ok, extra, _m = check_company_add(sl.account)
                AccountCompany.objects.create(company=new, account=sl.account, extra=extra, db_alias=sl.db_alias)
                forget(schema)
                from ZeoCloud import dbrouting
                dbrouting.forget()
        except Exception:
            log.exception('cloud link of the copy')
        # 3. the same users
        _set(job, step=STEPS[2], step_no=3)
        for u in CustomUser.objects.filter(tenants=src):
            u.tenants.add(new)
        me = CustomUser.objects.filter(pk=user_id).first()
        if me is not None:
            me.tenants.add(new)
        # 4. neutralize, and note where the copy came from
        _set(job, step=STEPS[3], step_no=4)
        with schema_context(schema):
            from ZeoApps.models import CompanyOption
            CompanyOption.objects.update_or_create(key='duplicated_from', defaults={'value': {
                'schema': src.schema_name, 'name': src.name, 'by': user_id}})
            if neutralize:
                neutralize_company(user_id)
        from zeo.mailguard import forget
        forget(schema)
        _set(job, state='done', step='Done', step_no=len(STEPS), company_id=new.pk)
        log.info('company %s duplicated as %s (%s)', src.schema_name, schema, 'neutralized' if neutralize else 'live')
    except Exception as e:   # leave nothing half made
        log.exception('duplicate company failed')
        _set(job, state='failed', error=str(e)[:300])
        try:
            from UserManagement.models import company
            from django_tenants.utils import schema_exists
            connection.set_schema_to_public()
            c = company.objects.filter(schema_name=schema).first()
            src_key = _db_of(company.objects.filter(pk=source_id).first())
            if src_key:   # v1.24.0 the half-made copy is in the own database: drop it there, then the row
                from ZeoCloud.dbrouting import pin
                with pin(src_key):
                    with connection.cursor() as cur:
                        cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
                if c is not None:
                    c.auto_drop_schema = False
                    c.delete()
            elif c is not None:
                c.delete(force_drop=True)
            elif schema_exists(schema):
                with connection.cursor() as cur:
                    cur.execute(f'DROP SCHEMA "{schema}" CASCADE')
        except Exception:
            log.exception('clean-up after a failed duplicate')
    finally:
        try:
            connection.set_schema_to_public()
        except Exception:
            pass
        close_old_connections()


def neutralize_company(user_id=None):
    """In the current (copied) schema: no e-mails, no mailbox reading, no outside AI."""
    from ZeoApps.models import CompanyOption
    CompanyOption.objects.update_or_create(key='neutralized', defaults={'value': {'on': True, 'by': user_id}})
    try:
        from EmpManagement.models import EmailConfiguration
        EmailConfiguration.objects.update(is_active=False)
    except Exception:
        pass
    try:
        from ZeoProjects.models import ProjectsSettings
        ProjectsSettings.objects.update(mailbox_enabled=False)
    except Exception:
        pass
    try:
        from django.apps import apps
        for m in apps.get_app_config('AISettings').get_models():
            names = {f.name for f in m._meta.fields}
            flags = {f: False for f in ('use_ask', 'use_documents') if f in names}
            if flags and 'mode' in names:
                flags['mode'] = 'builtin'      # the free built-in AI keeps working
            if flags:
                m.objects.update(**flags)
    except Exception:
        pass
