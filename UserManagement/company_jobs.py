"""v1.26.0 – company creation in the background.

Before: POST /users/api/company/ created the schema, ran every company migration and seeded the defaults inside the
request (6–7 minutes on the test server). gunicorn / nginx cut the request after 300 s, the screen said "server response
timeout" although the company was made, and the apps ticked on the form were never activated because that step came last.

Now the request only checks the form and answers 202 {job, schema} at once; a background thread
  1. creates the company (a copy of the ready template schema when `cloud_template` was run – seconds; otherwise the
     schema and its migrations),
  2. activates the apps ticked on the form (HR, Payroll, Projects, CRM, Property, Maintenance, HSE),
  3. loads the country defaults of the company country (UAE: WPS / gratuity / visa features, UAE leave types and
     policies, Housing / Transport / Food / Other allowance, payslip approval, public holidays, weekend),
  4. gives the creator access.
The screen polls GET /users/api/company-jobs/<job>/ and shows the steps.
"""
import logging
import threading
import uuid

from django.core.cache import cache
from django.core.files.base import ContentFile
from django.db import connection

log = logging.getLogger(__name__)

JOB = 'zeo:companyjob:{}'
STEPS = ['Creating the company', 'Setting up the apps', 'Loading the country defaults', 'Giving access', 'Finishing']


def _job(job, **kw):
    d = cache.get(JOB.format(job)) or {}
    d.update(kw)
    cache.set(JOB.format(job), d, 60 * 60 * 12)


def status(job):
    return cache.get(JOB.format(job))


def _detach_files(data):
    """Uploaded files are closed when the request ends – keep their bytes for the thread."""
    out = dict(data)
    for k, v in list(out.items()):
        if hasattr(v, 'read') and hasattr(v, 'name'):
            try:
                v.seek(0)
            except Exception:
                pass
            out[k] = ContentFile(v.read(), name=getattr(v, 'name', 'logo.png').split('/')[-1])
    return out


def _schema_for(name):
    import re
    from django.utils.text import slugify
    from .models import company as Company
    base = slugify(re.sub(r'[^a-zA-Z0-9]', '', name or '')) or 'company'
    schema, n = base, 1
    while Company.objects.filter(schema_name=schema).exists():
        n += 1
        schema = f'{base}{n}'
    return schema


def start(validated_data, apps, user, run_now=False):
    """Returns (job, schema). validated_data comes from CompanySerializer (already checked)."""
    job = uuid.uuid4().hex[:16]
    data = _detach_files(validated_data)
    schema = data.get('schema_name') or _schema_for(data.get('name', ''))
    data['schema_name'] = schema
    _job(job, state='running', step=STEPS[0], step_no=1, steps=len(STEPS), name=data.get('name', ''), schema=schema,
         company_id=None, apps=[], failed={}, defaults=[], error='', by=getattr(user, 'pk', None))
    args = (job, data, list(apps or []), getattr(user, 'pk', None))
    if run_now:
        _make(*args, own_thread=False)
    else:
        threading.Thread(target=_make, args=args, kwargs={'own_thread': True}, daemon=True).start()
    return job, schema


def _template_ready():
    try:
        from ZeoCloud.services import template_ready
        return template_ready()
    except Exception:
        return False


def _after_clone(comp):
    """A copy of the template keeps the template's branch name and codes – give them the company's."""
    from django_tenants.utils import schema_context
    from ZeoCloud.services import TEMPLATE
    old = TEMPLATE.upper().replace('_', '')
    new = comp.schema_name.upper()
    with schema_context(comp.schema_name):
        from django.apps import apps
        Branch = apps.get_model('OrganisationManager', 'brnch_mstr')
        b = Branch.objects.order_by('id').first()
        if b is not None:
            b.branch_name = comp.name[:100]
            b.branch_code = f'BR-{new}'[:50]
            if getattr(comp, 'country_id', None):
                b.br_country_id = comp.country_id
            b.save()
        LT = apps.get_model('calendars', 'leave_type')
        for t in LT.objects.all():
            code = t.code or ''
            for o in (old, TEMPLATE.upper()):
                if o in code:
                    t.code = code.replace(o, new)[:50]
                    t.save(update_fields=['code'])
                    break


def _make(job, data, apps, user_id, own_thread=True):
    from django.db import close_old_connections
    if own_thread:
        close_old_connections()
    schema = data['schema_name']
    try:
        from .models import company as Company, CustomUser
        connection.set_schema_to_public()
        user = CustomUser.objects.filter(pk=user_id).first() if user_id else None
        m2m = {k: data.pop(k) for k in list(data) if isinstance(data.get(k), (list, tuple))}
        comp = Company(**data)
        cloned = False
        if _template_ready():
            from django_tenants.clone import CloneSchema
            from ZeoCloud.services import TEMPLATE
            CloneSchema().clone_schema(TEMPLATE, schema, clone_mode='DATA')
            comp.auto_create_schema = False
            cloned = True
        comp.save()                                   # without the template: schema + company migrations
        for k, v in m2m.items():
            try:
                getattr(comp, k).set(v)
            except Exception:
                pass
        if cloned:
            try:
                _after_clone(comp)
            except Exception:
                log.exception('rename the template branch of %s', schema)
        _job(job, step=STEPS[1], step_no=2, company_id=comp.pk)

        from ZeoApps.services import setup_new_company
        done, failed = setup_new_company(comp, apps or ['hrms', 'payroll'], user)
        _job(job, step=STEPS[2], step_no=3, apps=done, failed=failed)

        defaults = []
        try:
            from CountryPack import services as CP
            CP.clear_cache(schema)
            res = CP.apply_defaults(user, schema_name=schema)
            defaults = [f"{a['kind']}: {a['name']}" for a in res.get('added', [])]
        except Exception as e:   # the company exists; the defaults can be loaded in Settings → Country settings
            log.exception('country defaults of %s', schema)
            failed['country defaults'] = str(e)[:200]
        try:   # v1.56.1: the standard approval chains (reporting manager) – every request of a new company was refused before
            from django_tenants.utils import schema_context
            from ZeoAdmin.approval_defaults import ensure as _chains
            with schema_context(schema):
                defaults += [f'Approval chain: {x}' for x in _chains(user=user)]
        except Exception as e:   # noqa: BLE001
            log.exception('approval chains of %s', schema)
            failed['approval chains'] = str(e)[:200]
        _job(job, step=STEPS[3], step_no=4, defaults=defaults, failed=failed)

        try:   # v1.28.1: the creator AND every system administrator can open the new company at once (before: only the
            # creator, so other admins / superusers did not see the company in "Choose a company" until allocated by hand)
            from ZeoCloud.services import grant_admin
            connection.set_schema_to_public()
            admins = list(CustomUser.objects.filter(is_superuser=True, is_active=True).exclude(is_ess=True))
            if user is not None and user not in admins:
                admins.append(user)
            for u in admins:
                try:
                    grant_admin(u, comp)
                except Exception:
                    log.exception('access of %s to %s', getattr(u, 'username', '?'), schema)
        except Exception:
            log.exception('access to %s', schema)
        _job(job, state='done', step='Done', step_no=len(STEPS), company_id=comp.pk)
    except Exception as e:
        log.exception('create company %s', schema)
        _job(job, state='failed', error=str(e)[:400])
        try:
            from django_tenants.utils import schema_exists
            from .models import company as Company
            connection.set_schema_to_public()
            c = Company.objects.filter(schema_name=schema).first()
            if c is not None:
                c.delete(force_drop=True)
            elif schema_exists(schema):
                with connection.cursor() as cur:
                    cur.execute(f'DROP SCHEMA "{schema}" CASCADE')
        except Exception:
            log.exception('clean-up after a failed company')
    finally:
        if own_thread:
            try:
                connection.set_schema_to_public()
            except Exception:
                pass
            close_old_connections()
