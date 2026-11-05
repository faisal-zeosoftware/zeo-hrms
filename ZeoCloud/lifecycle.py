"""v1.67.1 (companies) – Make inactive / Make active, Delete (kept 30 days), restore and the final removal of companies and
own databases (user decisions of 5 November 2026).

Who: the owner of the company's ZEO Cloud account (Account.owner) or a company administrator of the company (a user of it
whose rights there are administrator – ZeoCloud.rights.is_admin, the same check as `_zeo_is_admin`). Restore: a server
administrator only (`python manage.py restore_company <schema>` / `restore_database <key>`, or the Adept console).

* Inactive company – nobody can open it (UserManagement.middleware.SchemaMiddleware answers 403 for every company request,
  the scheduled jobs skip it); it is left out of Choose a company, the company menu, the sign-in answer and every company
  list; its owner and administrators still see it on Choose a company marked "Inactive" with Make active. An own database
  made inactive makes all its companies inactive the same way (DatabaseServer.inactive).
* Deleted company – a backup first (zeo_backup: the schema and the company's files; the person deleting downloads it, one
  copy stays on the server), then at once: inactive and hidden from everyone (no list, no restore button for users); every
  user unlinked (logins, invited rights, pending invitations, branch access – recorded in CompanyLife.links for a restore);
  nothing blocked: its name, licence place and plan counts are free at once (a new company may take the same name; the
  schema and domain stay reserved for the restore, so a new company with the same name gets the next internal name).
* After 30 days the daily job (cloud_daily / ZeoCloud.tasks.run_daily) removes it: the schema, the company row, its
  domains, links and its files. The backup folder stays KEEP_BACKUP_DAYS (ZEO_DELETED_BACKUP_KEEP_DAYS, default 90).
"""
import logging
import os
import shutil
import threading
import time
from datetime import timedelta

from django.conf import settings
from django.db import connection
from django.utils import timezone

log = logging.getLogger(__name__)
KEEP_DAYS = 30               # a deleted company / database can be restored for 30 days, then the daily job removes it
TOKEN_SALT = 'zeo-deleted-backup'
TOKEN_HOURS = 24             # the download link of the backup works for the person deleting, for 24 hours


class LifeError(Exception):
    def __init__(self, message, status=400, **extra):
        super().__init__(message)
        self.message, self.status, self.extra = message, status, extra


def tr(text, *args):
    from .services import tr as _tr
    return _tr(text, *args)


def keep_backup_days():
    """How long the server keeps the backup of a deleted company / database. There is no stored backup retention on the
    server (the nightly zeo_backup gets --keep on its command line), so 90 days unless ZEO_DELETED_BACKUP_KEEP_DAYS is set."""
    v = os.environ.get('ZEO_DELETED_BACKUP_KEEP_DAYS') or getattr(settings, 'ZEO_DELETED_BACKUP_KEEP_DAYS', '')
    try:
        return max(KEEP_DAYS, int(v)) if str(v).strip() else 90
    except (TypeError, ValueError):
        return 90


def backup_root():
    root = os.environ.get('ZEO_BACKUP_DIR') or getattr(settings, 'ZEO_BACKUP_DIR', '') or os.path.join(settings.BASE_DIR, 'backups')
    return os.path.join(root, 'deleted')


# ================================================================ state of every company (cached a few seconds)
_lock = threading.Lock()
_cache = {'until': 0.0, 'map': {}}
TTL = 5.0


def _load():
    from .models import AccountCompany, CompanyLife, DatabaseServer
    out = {}
    for schema, state in CompanyLife.objects.filter(company__isnull=False).exclude(state='active').values_list('schema_name', 'state'):
        out[schema] = state
    off_db = set(DatabaseServer.objects.filter(inactive=True).values_list('key', flat=True))
    if off_db:
        for schema in AccountCompany.objects.filter(db_alias__in=off_db).values_list('company__schema_name', flat=True):
            out.setdefault(schema, 'inactive')
    return out


def state_map():
    """{schema: 'inactive' | 'deleted'} – only the companies that are not active."""
    now = time.time()
    if _cache['until'] > now:
        return _cache['map']
    with _lock:
        if _cache['until'] > now:
            return _cache['map']
        try:
            m = _load()
        except Exception:   # before the migration / no table yet: everything active
            m = {}
        _cache['map'], _cache['until'] = m, now + TTL
        return m


def forget():
    _cache['until'] = 0.0
    try:
        from django.core.cache import cache
        cache.delete('zeo:life:v')
    except Exception:
        pass


def state_of(schema):
    return state_map().get(schema or '', 'active')


def is_live(schema):
    return state_of(schema) == 'active'


def off_schemas():
    """'public' and every company that is not active – the scheduled jobs leave them out (`.exclude(schema_name__in=…)`)."""
    return ['public', *state_map().keys()]


def deleted_ids():
    from .models import CompanyLife
    return set(CompanyLife.objects.filter(state='deleted', company__isnull=False).values_list('company_id', flat=True))


def live(qs):
    """A company queryset without the inactive and deleted companies."""
    off = [s for s in state_map().keys()]
    return qs.exclude(schema_name__in=off) if off else qs


def not_deleted(qs):
    off = [s for s, st in state_map().items() if st == 'deleted']
    return qs.exclude(schema_name__in=off) if off else qs


# ================================================================ who may
def owner_of(comp):
    from .models import AccountCompany
    l = AccountCompany.objects.select_related('account').filter(company=comp).first()
    return l.account.owner_id if l and l.account else None


def is_company_admin(user, comp):
    """A user of the company whose rights there are administrator (invited rights included), or a system administrator
    who is a user of it."""
    if user is None or not getattr(user, 'is_authenticated', False) or comp is None:
        return False
    from .models import CompanyAccess
    if not user.tenants.filter(pk=comp.pk).exists() and not CompanyAccess.objects.filter(user=user, company=comp).exists():
        return False
    if user.is_superuser:
        return True
    try:
        from .rights import is_admin
        return bool(is_admin(user, comp.schema_name))
    except Exception:
        log.exception('admin check')
        return False


def can_manage(user, comp):
    """Owner of the company's account, or a company administrator of it. A deleted company: nobody (server admin restores)."""
    if user is None or comp is None or not getattr(user, 'is_authenticated', False):
        return False
    if state_of(comp.schema_name) == 'deleted':
        return False
    own = owner_of(comp)
    if own and own == user.pk:
        return True
    return is_company_admin(user, comp)


# ================================================================ history
def life_of(comp, create=True):
    from .models import CompanyLife
    life = CompanyLife.objects.filter(company=comp).first()
    if life is None and create:
        life = CompanyLife.objects.create(company=comp, schema_name=comp.schema_name, name=comp.name[:150], state='active')
    return life


def _by(user):
    return user if getattr(user, 'pk', None) else None


def _hist(life, action, by=None, note='', **data):
    h = list(life.history or [])
    row = {'at': timezone.now().isoformat(timespec='seconds'), 'action': action, 'by': getattr(by, 'username', '') or (by if isinstance(by, str) else ''),
           'by_id': getattr(by, 'pk', None), 'note': note}
    row.update(data)
    h.append(row)
    life.history = h[-200:]


def _event(comp, kind, text, by=None, **data):
    """The account history too, when the company belongs to a ZEO Cloud account."""
    try:
        from .models import AccountCompany
        from .services import event
        l = AccountCompany.objects.select_related('account').filter(company=comp).first()
        if l is not None:
            event(l.account, kind, text, _by(by), **data)
    except Exception:
        log.exception('company event')


def history_json(life):
    return list(reversed(life.history or [])) if life else []


# ================================================================ make inactive / make active
def set_active(comp, active, by=None, via=''):
    """Make a company active or inactive (no deletion). Returns the CompanyLife."""
    life = life_of(comp)
    if life.state == 'deleted':
        raise LifeError(tr('This company was deleted.'), 404)
    want = 'active' if active else 'inactive'
    if life.state == want:
        return life
    life.state, life.changed_at, life.changed_by = want, timezone.now(), _by(by)
    life.name = comp.name[:150]
    _hist(life, 'made_active' if active else 'made_inactive', by, via)
    life.save()
    forget()
    _event(comp, 'company', f'{"Made active" if active else "Made inactive"} – {comp.name}' + (f' ({via})' if via else ''), by)
    try:
        from .services import forget as cforget
        cforget(comp.schema_name)
    except Exception:
        pass
    return life


# ================================================================ what a deletion removes (the confirm window)
COUNTS = [
    ('employees', 'Employees', 'EmpManagement.emp_master'),
    ('branches', 'Branches', 'OrganisationManager.brnch_mstr'),
    ('payroll_runs', 'Payroll runs', 'PayrollManagement.PayrollRun'),
    ('payslips', 'Payslips', 'PayrollManagement.Payslip'),
    ('leave_requests', 'Leave requests', 'calendars.employee_leave_request'),
    ('loans', 'Loans', 'PayrollManagement.LoanApplication'),
    ('documents', 'Employee documents', 'EmpManagement.Emp_Documents'),
]


def counts(comp):
    """[{key, label, count}] – what the company holds, for the confirm window."""
    from django.apps import apps
    from django_tenants.utils import schema_context
    from .models import CompanyAccess, MediaFile, UserInvite
    out = []
    try:
        with schema_context(comp.schema_name):
            for key, label, model in COUNTS:
                try:
                    out.append({'key': key, 'label': label, 'count': apps.get_model(model).objects.count()})
                except Exception:
                    continue
    except Exception:
        log.exception('counts of %s', comp.schema_name)
    from UserManagement.models import CustomUser
    users = set(CustomUser.objects.filter(tenants=comp).values_list('id', flat=True))
    users |= set(CompanyAccess.objects.filter(company=comp).values_list('user_id', flat=True))
    out.append({'key': 'users', 'label': 'Users who lose access', 'count': len(users)})
    out.append({'key': 'invites', 'label': 'Pending invitations', 'count': UserInvite.objects.filter(company=comp, state='pending').count()})
    out.append({'key': 'files', 'label': 'Uploaded files', 'count': MediaFile.objects.filter(schema=comp.schema_name).count()})
    return out


# ================================================================ backup
def make_backup(tag, schema='', db_key=''):
    """zeo_backup of one company (its schema and files) or of a whole own database. Returns the folder."""
    from io import StringIO
    from django.core.management import call_command
    root = backup_root()
    os.makedirs(root, exist_ok=True)
    out = StringIO()
    args = dict(dir=root, keep=0, no_media=True, company_files=True, no_status=True, tag=tag, stdout=out)
    if schema:
        args['schema'] = schema
    if db_key:
        args['db'] = db_key
    try:
        call_command('zeo_backup', **args)
    except Exception as e:
        raise LifeError(tr('The backup could not be made, so nothing was deleted: {0}', str(e)[:300]), 500)
    finally:
        try:
            connection.set_schema_to_public()
        except Exception:
            pass
    text = out.getvalue()
    folder = ''
    for line in text.splitlines():
        if line.startswith('Backup made and checked: '):
            folder = line[len('Backup made and checked: '):].rsplit(' (', 1)[0].strip()
    if not folder or not os.path.isdir(folder):
        raise LifeError(tr('The backup could not be made, so nothing was deleted.'), 500)
    return folder


def download_token(folder, user, name):
    from django.core import signing
    return signing.dumps({'f': os.path.basename(folder), 'u': getattr(user, 'pk', None), 'n': name[:80]}, salt=TOKEN_SALT)


def read_token(token):
    from django.core import signing
    try:
        return signing.loads(token, salt=TOKEN_SALT, max_age=TOKEN_HOURS * 3600)
    except signing.BadSignature:
        return None


def backup_zip(folder, out_path, title=''):
    """The backup as one .zip for the download: plain files (an encrypted server copy is decrypted with ZEO_BACKUP_KEY),
    the manifest and a short how-to."""
    import json
    import zipfile
    key = os.environ.get('ZEO_BACKUP_KEY') or getattr(settings, 'ZEO_BACKUP_KEY', '')
    m = json.load(open(os.path.join(folder, 'manifest.json')))
    with zipfile.ZipFile(out_path, 'w', compression=zipfile.ZIP_DEFLATED, allowZip64=True) as z:
        for f in m.get('files', []):
            src = os.path.join(folder, f['name'])
            if f['name'].endswith('.enc'):
                if not key:
                    z.write(src, f['name'])
                    continue
                from ZeoAdmin.backupcrypt import decrypt_stream
                plain = src[:-4] + '.plain'
                with open(plain, 'wb') as fo:
                    decrypt_stream(src, key, fo)
                try:
                    z.write(plain, f['name'][:-4])
                finally:
                    os.remove(plain)
            else:
                z.write(src, f['name'])
        z.write(os.path.join(folder, 'manifest.json'), 'manifest.json')
        z.writestr('READ-ME.txt', (
            f'ZEO backup made before {title or "the company"} was deleted.\n\n'
            'db-*.dump      PostgreSQL custom-format dump (pg_restore --no-owner -d <database> db-default.dump)\n'
            'files.tar.gz   the uploaded files of the company (tar xzf files.tar.gz -C <ZEO folder>)\n'
            'manifest.json  ZEO version, time and the SHA-256 of every file\n\n'
            'The server keeps its own copy. Within 30 days a server administrator can also restore the company directly.\n'))
    return out_path


# ================================================================ unlink / relink the users
def _branch_access_rows(schema):
    from django.apps import apps
    from django_tenants.utils import schema_context
    rows = []
    try:
        with schema_context(schema):
            UBA = apps.get_model('OrganisationManager', 'UserBranchAccess')
            for a in UBA.objects.all():
                rows.append({'user': a.user_id, 'branches': list(a.branch.values_list('id', flat=True))})
    except Exception:
        log.exception('branch access of %s', schema)
    return rows


def unlink_users(comp):
    """Every user linked to the company is unlinked; returns what was linked (put back by relink_users)."""
    from django.apps import apps
    from django_tenants.utils import schema_context
    from UserManagement.models import CustomUser
    from .models import CompanyAccess, UserInvite
    links = {
        'tenants': sorted(CustomUser.objects.filter(tenants=comp).values_list('id', flat=True)),
        'company_access': [{'user': a.user_id, 'admin': a.admin, 'groups': list(a.groups or []), 'invite': a.invite_id}
                           for a in CompanyAccess.objects.filter(company=comp)],
        'invites': list(UserInvite.objects.filter(company=comp, state='pending').values_list('id', flat=True)),
        'branch_access': _branch_access_rows(comp.schema_name),
    }
    # branch access (company schema) – the rows go; the schema itself stays until the removal
    try:
        with schema_context(comp.schema_name):
            apps.get_model('OrganisationManager', 'UserBranchAccess').objects.all().delete()
    except Exception:
        log.exception('branch access of %s', comp.schema_name)
    UserInvite.objects.filter(pk__in=links['invites']).update(state='cancelled', note='Company deleted', answered_at=timezone.now())
    CompanyAccess.objects.filter(company=comp).delete()
    for u in CustomUser.objects.filter(pk__in=links['tenants']):
        u.tenants.remove(comp)          # the login stays (also for a user whose only company this was)
    try:
        from .rights import forget as rforget
        rforget()
    except Exception:
        pass
    return links


def relink_users(comp, links):
    """Put back what unlink_users removed. Returns the number of users linked again."""
    from django.apps import apps
    from django_tenants.utils import schema_context
    from UserManagement.models import CustomUser
    from .models import CompanyAccess, UserInvite
    links = links or {}
    n = 0
    for u in CustomUser.objects.filter(pk__in=links.get('tenants') or []):
        u.tenants.add(comp)
        n += 1
    for a in links.get('company_access') or []:
        if CustomUser.objects.filter(pk=a.get('user')).exists():
            CompanyAccess.objects.update_or_create(user_id=a['user'], company=comp, defaults={
                'admin': bool(a.get('admin')), 'groups': a.get('groups') or [],
                'invite': UserInvite.objects.filter(pk=a.get('invite')).first() if a.get('invite') else None})
    UserInvite.objects.filter(pk__in=links.get('invites') or [], state='cancelled', note='Company deleted').update(state='pending', note='', answered_at=None)
    rows = links.get('branch_access') or []
    if rows:
        try:
            with schema_context(comp.schema_name):
                UBA = apps.get_model('OrganisationManager', 'UserBranchAccess')
                B = apps.get_model('OrganisationManager', 'brnch_mstr')
                for r in rows:
                    if not CustomUser.objects.filter(pk=r.get('user')).exists():
                        continue
                    a = UBA.objects.create(user_id=r['user'])
                    a.branch.set(B.objects.filter(pk__in=r.get('branches') or []))
        except Exception:
            log.exception('branch access back into %s', comp.schema_name)
    try:
        from .rights import forget as rforget
        rforget()
    except Exception:
        pass
    return n


# ================================================================ delete / restore / remove a company
def _mark_deleted(comp, by, links, backup='', db_key=''):
    life = life_of(comp)
    now = timezone.now()
    life.state, life.changed_at, life.changed_by = 'deleted', now, _by(by)
    life.deleted_at, life.purge_after = now, timezone.localdate() + timedelta(days=KEEP_DAYS)
    life.name, life.schema_name = comp.name[:150], comp.schema_name
    life.links, life.backup, life.db_key = links, backup, db_key or ''
    _hist(life, 'deleted', by, f'with the database {db_key}' if db_key else '', backup=os.path.basename(backup) if backup else '',
          users=len(links.get('tenants') or []), purge_after=life.purge_after.isoformat())
    life.save()
    forget()
    try:
        from .services import forget as cforget
        cforget(comp.schema_name)
    except Exception:
        pass
    return life


def check_delete(user, comp, typed):
    if comp is None or comp.schema_name in ('public',):
        raise LifeError(tr('Not found.'), 404)
    if state_of(comp.schema_name) == 'deleted':
        raise LifeError(tr('Not found.'), 404)
    if not can_manage(user, comp):
        raise LifeError(tr('Only the owner or a company administrator can delete the company.'), 403)
    if str(typed or '').strip() != comp.name.strip():
        raise LifeError(tr('Type the company name exactly as shown to delete it.'), 400, field='confirm')
    from .models import AccountCompany
    l = AccountCompany.objects.filter(company=comp).first()
    if l is not None and l.moving:
        raise LifeError(tr('{0} is being moved. Try again when the move has finished.', comp.name), 409)


def delete_company(user, comp, typed):
    """Backup, then the company is deleted (kept 30 days). Returns {life, backup, token, file_name}."""
    check_delete(user, comp, typed)
    from .models import AccountCompany
    l = AccountCompany.objects.filter(company=comp).first()
    folder = make_backup(f'company-{comp.schema_name}', schema=comp.schema_name, db_key=(l.db_alias if l else '') or '')
    links = unlink_users(comp)
    life = _mark_deleted(comp, user, links, backup=folder)
    _event(comp, 'company', f'Company deleted – {comp.name} (kept until {life.purge_after}; backup {os.path.basename(folder)})', user)
    try:
        from django.core.cache import cache
        if l is not None:
            cache.delete(f'zeo:cloud:usage:{l.account_id}')
    except Exception:
        pass
    name = f'zeo-backup-{comp.schema_name}-{os.path.basename(folder)[len("backup-"):]}.zip'
    return {'life': life, 'backup': folder, 'token': download_token(folder, user, name), 'file_name': name}


def _restored_name(comp, life):
    """The name back; ' (restored)' added when another company of the same account (or of the users it is given back to)
    has taken it meanwhile."""
    from UserManagement.models import company as Company
    from .models import AccountCompany
    name = life.name or comp.name
    l = AccountCompany.objects.select_related('account').filter(company=comp).first()
    if l is not None:
        ids = set(AccountCompany.objects.filter(account=l.account).values_list('company_id', flat=True))
    else:
        from UserManagement.models import CustomUser
        ids = set(CustomUser.objects.filter(pk__in=(life.links or {}).get('tenants') or []).values_list('tenants__id', flat=True))
    ids.discard(comp.pk)
    ids.discard(None)
    taken = {norm(n) for n in not_deleted(Company.objects.filter(pk__in=ids)).values_list('name', flat=True)}
    if norm(name) not in taken:
        return name
    base = f'{name[:88]} (restored)'
    cand, n = base, 2
    while norm(cand) in taken:
        cand = f'{name[:86]} (restored {n})'
        n += 1
    return cand


def restore_company(comp_or_schema, by='server administrator'):
    """Server administrator: a deleted company back within 30 days (active, users linked again). Returns the CompanyLife."""
    from UserManagement.models import company as Company
    from .models import CompanyLife, DatabaseServer
    comp = comp_or_schema if not isinstance(comp_or_schema, str) else Company.objects.filter(schema_name=comp_or_schema).first()
    life = CompanyLife.objects.filter(company=comp).first() if comp is not None else None
    if comp is None or life is None or life.state != 'deleted':
        raise LifeError(tr('There is no deleted company {0} to restore.', comp_or_schema if isinstance(comp_or_schema, str) else getattr(comp, 'schema_name', '')), 404)
    if life.db_key:
        srv = DatabaseServer.objects.filter(key=life.db_key).first()
        if srv is not None and srv.state == 'retired':
            raise LifeError(tr('The company was deleted with the database {0} – restore the database ({1}).', srv.label or srv.name,
                               f'restore_database {srv.key}'), 409)
    if not _schema_there(comp.schema_name):
        raise LifeError(tr('The data of {0} is no longer on the server.', comp.schema_name), 410)
    name = _restored_name(comp, life)
    if name != comp.name:
        comp.name = name
        comp.save(update_fields=['name'])
    n = relink_users(comp, life.links)
    life.state, life.changed_at, life.changed_by = 'active', timezone.now(), by if getattr(by, 'pk', None) else None
    life.deleted_at, life.purge_after, life.db_key = None, None, ''
    life.name = comp.name[:150]
    _hist(life, 'restored', by, '', name=comp.name, users=n)
    life.save()
    forget()
    _event(comp, 'company', f'Company restored – {comp.name} ({n} users linked again)', by if getattr(by, 'pk', None) else None)
    try:
        from .services import forget as cforget
        cforget(comp.schema_name)
        from .models import AccountCompany
        l = AccountCompany.objects.filter(company=comp).first()
        if l is not None:
            from django.core.cache import cache
            cache.delete(f'zeo:cloud:usage:{l.account_id}')
    except Exception:
        pass
    return life


def _schema_there(schema):
    from . import owndb
    from .models import DatabaseServer
    try:
        key = owndb.where_is(schema)
        if key and not DatabaseServer.objects.filter(key=key, state='ready').exists():
            return False
        return owndb.schema_exists(key, schema)
    except Exception:
        return False


def purge_company(life, today=None):
    """The final removal of a deleted company: its schema, the company row, domains, links and files. Returns a summary."""
    from django.apps import apps
    from UserManagement.models import Domain, company as Company
    from . import owndb
    from .models import AccountCompany, CompanyAccess, MediaFile, UserInvite
    comp = life.company
    schema = life.schema_name
    done = {'schema': schema, 'name': life.name}
    if comp is None or schema in ('public', ''):
        return done
    if Company.objects.filter(schema_name=schema).exclude(pk=comp.pk).exists():   # never another company's schema
        raise LifeError(f'schema {schema} belongs to another company', 409)
    key = (AccountCompany.objects.filter(company=comp).values_list('db_alias', flat=True).first() or '')
    from .models import DatabaseServer
    srv = DatabaseServer.objects.filter(key=key).first() if key else None
    # 1. the schema (in the database it lives in; an own database being removed is dropped as a whole afterwards – never
    #    look for the schema in the main database when its own database is not ready)
    try:
        if key and (srv is None or srv.state != 'ready'):
            done['schema_dropped'] = 'with the database'
        elif owndb.schema_exists(key, schema):
            with owndb.conn(key).cursor() as cur:
                cur.execute(f'DROP SCHEMA "{schema}" CASCADE')
            done['schema_dropped'] = True
    except Exception:
        log.exception('drop schema %s', schema)
        raise
    # 1b. old copies left by moves of this company (kept 7 days in the database it moved from)
    from .models import MoveJob
    for job in MoveJob.objects.filter(company=comp).exclude(old_copy=''):
        try:
            src = job.source or ''
            if src and not DatabaseServer.objects.filter(key=src, state='ready').exists():
                continue   # that own database is dropped as a whole
            if job.old_copy != schema and not Company.objects.filter(schema_name=job.old_copy).exists() and owndb.schema_exists(src, job.old_copy):
                with owndb.conn(src).cursor() as cur:
                    cur.execute(f'DROP SCHEMA "{job.old_copy}" CASCADE')
            job.old_copy = ''
            job.save(update_fields=['old_copy'])
        except Exception:
            log.exception('old copy %s of %s', job.old_copy, schema)
    # 2. its files: registered for this company only, its logo, a media/<schema>/ folder
    media = os.path.realpath(str(getattr(settings, 'MEDIA_ROOT', '') or ''))
    files = 0
    if media and os.path.isdir(media):
        paths = set(MediaFile.objects.filter(schema=schema).values_list('path', flat=True))
        shared = set(MediaFile.objects.filter(path__in=paths).exclude(schema=schema).values_list('path', flat=True))
        if comp.logo:
            paths.add(comp.logo.name)
        for pth in paths - shared:
            full = os.path.realpath(os.path.join(media, pth))
            if full.startswith(media + os.sep) and os.path.isfile(full):
                try:
                    os.remove(full)
                    files += 1
                except OSError:
                    log.warning('could not remove %s', full)
        d = os.path.realpath(os.path.join(media, schema))
        if d.startswith(media + os.sep) and os.path.isdir(d) and d != media:
            shutil.rmtree(d, ignore_errors=True)
            done['folder'] = True
    done['files'] = files
    MediaFile.objects.filter(schema=schema).delete()
    # 3. links and rows that name the company
    CompanyAccess.objects.filter(company=comp).delete()
    UserInvite.objects.filter(company=comp).delete()
    Domain.objects.filter(tenant=comp).delete()
    for label, flt in (('LanguagePack.CompanyLanguageSettings', 'schema_name'), ('UserManagement.UserNotificationInbox', 'schema_name'),
                       ('ZeoCloud.SharedRowOwner', 'schema_name')):
        try:
            m = apps.get_model(label)
            if any(f.name == flt for f in m._meta.fields):
                m.objects.filter(**{flt: schema}).delete()
        except Exception:
            pass
    try:
        apps.get_model('ZeoMobile', 'MobileDevice').objects.filter(schema=schema).update(schema='')
    except Exception:
        pass
    from UserManagement.models import CustomUser
    for u in CustomUser.objects.filter(tenants=comp):
        u.tenants.remove(comp)
    acc_id = AccountCompany.objects.filter(company=comp).values_list('account_id', flat=True).first()
    if acc_id:
        try:
            from .models import Account
            from .services import event
            event(Account.objects.get(pk=acc_id), 'company', f'Deleted company removed after {KEEP_DAYS} days – {life.name} ({schema})')
        except Exception:
            pass
    AccountCompany.objects.filter(company=comp).delete()
    # 4. the company row (the schema is gone already: no drop by django-tenants)
    life.company = None
    life.state, life.purged_at = 'purged', timezone.now()
    _hist(life, 'removed', 'daily job', f'{KEEP_DAYS} days after the deletion', files=files)
    life.save()
    comp.auto_drop_schema = False
    Company.objects.filter(pk=comp.pk).delete()
    forget()
    return done


def purge_due(today=None):
    """Daily: deleted companies past their 30 days are removed; old deletion backups go after keep_backup_days()."""
    from .models import CompanyLife
    today = today or timezone.localdate()
    out = []
    for life in CompanyLife.objects.filter(state='deleted', purge_after__lt=today).select_related('company'):
        try:
            out.append(purge_company(life, today))
        except Exception:
            log.exception('remove deleted company %s', life.schema_name)
    out_b = drop_old_backups(today)
    return {'companies': out, 'backups_removed': out_b}


def drop_old_backups(today=None):
    from datetime import datetime
    today = today or timezone.localdate()
    root = backup_root()
    removed = []
    if not os.path.isdir(root):
        return removed
    limit = today - timedelta(days=keep_backup_days())
    for n in sorted(os.listdir(root)):
        if not n.startswith('backup-'):
            continue
        try:
            made = datetime.strptime(n[len('backup-'):len('backup-') + 15], '%Y%m%d-%H%M%S').date()
        except ValueError:
            continue
        if made < limit and os.path.exists(os.path.join(root, n, 'manifest.json')):
            shutil.rmtree(os.path.join(root, n), ignore_errors=True)
            removed.append(n)
    return removed


# ================================================================ own databases
def db_title(srv):
    return (srv.label or srv.name or srv.key).strip()


def db_companies(key, include_deleted=False):
    from .models import AccountCompany
    links = AccountCompany.objects.select_related('company').filter(db_alias=key)
    m = state_map()
    return [l.company for l in links if include_deleted or m.get(l.company.schema_name) != 'deleted']


def set_db_active(account, key, active, by=None):
    from .models import DatabaseServer
    from .services import event
    srv = DatabaseServer.objects.filter(account=account, key=key).exclude(state='retired').first()
    if srv is None:
        raise LifeError(tr('Not found.'), 404)
    if not active and key == account.db_alias:
        raise LifeError(tr('This is the default database – make another one the default first.'), 409)
    if srv.inactive == (not active):
        return srv
    srv.inactive = not active
    srv.save(update_fields=['inactive'])
    forget()
    event(account, 'database', f'Database {db_title(srv)} made {"active" if active else "inactive"} '
                               f'({len(db_companies(key))} companies {"open again" if active else "closed"})', _by(by))
    for c in db_companies(key):
        try:
            from .services import forget as cforget
            cforget(c.schema_name)
        except Exception:
            pass
    return srv


def check_db_delete(account, key, typed):
    from .models import DatabaseServer, MoveJob
    from . import owndb
    srv = DatabaseServer.objects.filter(account=account, key=key).exclude(state='retired').first()
    if srv is None:
        raise LifeError(tr('Not found.'), 404)
    if key == account.db_alias:
        raise LifeError(tr('This is the default database – make another one the default first.'), 409)
    shown = {x.strip() for x in (srv.label, srv.name, db_title(srv)) if x and x.strip()}   # the screen shows the label (or the name)
    if str(typed or '').strip() not in shown:
        raise LifeError(tr('Type the database name exactly as shown to delete it.'), 400, field='confirm')
    if MoveJob.objects.filter(target=key, state__in=('waiting', 'running')).exists() or MoveJob.objects.filter(
            source=key, state__in=('waiting', 'running')).exists() or owndb.dup_running(key):
        raise LifeError(tr('A copy or a move of this database is still running.'), 409)
    return srv


def delete_database(account, key, by, typed):
    """Backup of the whole own database, every company in it deleted (B), the database retired (dropped after 30 days)."""
    from . import dbrouting, owndb
    from .services import event
    srv = check_db_delete(account, key, typed)
    comps = db_companies(key)
    folder = make_backup(f'database-{key}', db_key=key) if srv.state == 'ready' else ''
    for comp in comps:
        links = unlink_users(comp)
        _mark_deleted(comp, by, links, backup=folder, db_key=key)
    srv.state, srv.retired_on, srv.retired_by, srv.backup = 'retired', timezone.localdate(), _by(by), folder
    srv.save(update_fields=['state', 'retired_on', 'retired_by', 'backup'])
    dbrouting.forget()
    owndb.forget_db(key)
    forget()
    event(account, 'database', f'Database deleted – {db_title(srv)} with {len(comps)} companies '
                               f'(deleted after {KEEP_DAYS} days; backup {os.path.basename(folder) if folder else "none"})', _by(by))
    try:
        from django.core.cache import cache
        cache.delete(f'zeo:cloud:usage:{account.pk}')
    except Exception:
        pass
    name = f'zeo-backup-database-{key}-{os.path.basename(folder)[len("backup-"):]}.zip' if folder else ''
    return {'server': srv, 'companies': [c.name for c in comps], 'backup': folder,
            'token': download_token(folder, by, name) if folder else '', 'file_name': name}


def restore_database(key, by='server administrator'):
    """Server administrator: a deleted (retired) own database back within 30 days, with the companies deleted with it."""
    from . import dbrouting, owndb
    from .models import CompanyLife, DatabaseServer
    from .services import event
    srv = DatabaseServer.objects.filter(key=key, state='retired').first()
    if srv is None:
        raise LifeError(tr('There is no deleted database {0} to restore.', key), 404)
    with owndb.conn('control').cursor() as cur:
        cur.execute('SELECT 1 FROM pg_database WHERE datname = %s', [srv.name])
        if not cur.fetchone():
            raise LifeError(tr('The database {0} is no longer on the server.', srv.name), 410)
    srv.state, srv.retired_on, srv.retired_by = 'ready', None, None
    srv.save(update_fields=['state', 'retired_on', 'retired_by'])
    dbrouting.forget()
    owndb.forget_db(key)
    restored = []
    for life in CompanyLife.objects.filter(state='deleted', db_key=key).select_related('company'):
        if life.company is None:
            continue
        life.db_key = ''
        life.save(update_fields=['db_key'])
        try:
            restored.append(restore_company(life.company, by).company.name)
        except LifeError as e:
            log.warning('restore %s: %s', life.schema_name, e.message)
    if srv.account:
        event(srv.account, 'database', f'Database restored – {db_title(srv)} ({len(restored)} companies)', by if getattr(by, 'pk', None) else None)
    return {'server': srv, 'companies': restored}


def deleted_list():
    """For the console and the commands: deleted companies and databases that can still be restored."""
    from .models import CompanyLife, DatabaseServer
    comps = [{'schema_name': l.schema_name, 'name': l.name, 'company_id': l.company_id, 'deleted_at': l.deleted_at.isoformat() if l.deleted_at else None,
              'restore_until': l.purge_after.isoformat() if l.purge_after else None, 'db_key': l.db_key,
              'by': (l.changed_by.username if l.changed_by else ''), 'users': len((l.links or {}).get('tenants') or []),
              'backup': os.path.basename(l.backup or '')}
             for l in CompanyLife.objects.filter(state='deleted').select_related('changed_by').order_by('purge_after')]
    dbs = [{'key': d.key, 'label': db_title(d), 'name': d.name, 'account': d.account.name if d.account else '',
            'retired_on': d.retired_on.isoformat() if d.retired_on else None,
            'delete_on': (d.retired_on + timedelta(days=KEEP_DAYS + 1)).isoformat() if d.retired_on else None,
            'companies': [l.name for l in CompanyLife.objects.filter(state='deleted', db_key=d.key)]}
           for d in DatabaseServer.objects.filter(state='retired').select_related('account').order_by('retired_on')]
    return {'companies': comps, 'databases': dbs, 'keep_days': KEEP_DAYS}


# ================================================================ D. company names: unique within one user / account
def norm(name):
    return ' '.join(str(name or '').split()).casefold()


def scope_ids(user=None, account=None):
    """Company ids a new name is compared with: the user's companies, the companies of the ZeoCloud accounts the user owns,
    and (for a company made in an account) that account's companies and its owner's companies."""
    from .models import AccountCompany
    ids = set()
    if user is not None and getattr(user, 'pk', None):
        ids |= set(user.tenants.values_list('id', flat=True))
        ids |= set(AccountCompany.objects.filter(account__owner_id=user.pk).values_list('company_id', flat=True))
    if account is not None:
        ids |= set(AccountCompany.objects.filter(account=account).values_list('company_id', flat=True))
        if account.owner_id:
            from UserManagement.models import CustomUser
            o = CustomUser.objects.filter(pk=account.owner_id).first()
            if o is not None:
                ids |= set(o.tenants.values_list('id', flat=True))
    return ids


def name_clash(name, user=None, account=None, exclude_id=None):
    """The company of the same user / account that already has this name (ignoring case and spacing), or None.
    Deleted companies do not count; the template and the public company never."""
    from UserManagement.models import company as Company
    ids = scope_ids(user, account)
    if exclude_id:
        ids.discard(exclude_id)
    if not ids:
        return None
    want = norm(name)
    qs = not_deleted(Company.objects.filter(pk__in=ids).exclude(schema_name__in=('public', 'zeo_template')))
    for c in qs.only('id', 'name', 'schema_name'):
        if norm(c.name) == want:
            return c
    return None
