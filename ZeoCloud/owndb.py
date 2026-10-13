"""v1.24.0 – own databases for Premium accounts and the Move tool.

- create_own_db(account)        a new PostgreSQL database for the account: the shared tables (structure), the masters
                                (countries, currencies …), the account's users and the migration history.
- move(company, target)         one company between the main database ('') and an own database (key), either way:
                                copy (pg_dump / pg_restore), checks (tables, rows, sequences), switch, old copy kept 7 days.
- move_back(job)                roll back a move: the company goes back where it came from, with its current data.
- sync_shared(key)              the shared rows an own database needs (masters, content types, users) – after updates.
- migrate_all()                 shared and company migrations in every database (cloud_migrate).

Shared tables (users, companies, accounts …) are always read and written in the main database (dbrouting). An own
database keeps a copy only of the rows its company tables point to, so that their foreign keys hold.
"""
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
from datetime import timedelta

from django.conf import settings
from django.db import connections, close_old_connections
from django.utils import timezone

from . import dbrouting
from .models import Account, AccountCompany, DatabaseServer, MoveJob, PlatformAdmin

log = logging.getLogger(__name__)
KEEP_DAYS = 7
CONTROL = 'control'
SAFE = re.compile(r'^[a-z_][a-z0-9_]{0,62}$')


class MoveError(Exception):
    pass


# ---------------------------------------------------------------- connections
def conn(key):
    """Connection on the public schema of a database: '' / 'control' = main, otherwise the own database."""
    if not key or key == CONTROL:
        c = connections[CONTROL]
        c.set_schema_to_public()
        return c
    return dbrouting.ensure_alias(key)


def _params(key):
    main = connections['default']._zeo_main
    if not key or key == CONTROL:
        p = dict(main)
    else:
        p = dbrouting.params_of(key, main)
        if not p:
            raise MoveError(f'Database {key} is not registered.')
    return p


def _pg_args(p):
    a = []
    if p.get('HOST'):
        a += ['-h', str(p['HOST'])]
    if p.get('PORT'):
        a += ['-p', str(p['PORT'])]
    if p.get('USER'):
        a += ['-U', str(p['USER'])]
    return a


def _pg_env(p):
    e = dict(os.environ)
    if p.get('PASSWORD'):
        e['PGPASSWORD'] = str(p['PASSWORD'])
    return e


def _run(cmd, p, stdin=None, timeout=3600):
    r = subprocess.run(cmd, env=_pg_env(p), stdin=stdin, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise MoveError(f'{os.path.basename(cmd[0])} failed: {(r.stderr or r.stdout).strip()[:800]}')
    return r


def _tool(name):
    for d in (getattr(settings, 'ZEO_PG_BIN', ''), '/usr/lib/postgresql/16/bin', '/usr/bin', '/usr/local/bin'):
        if d and os.path.exists(os.path.join(d, name)):
            return os.path.join(d, name)
    return name


def schema_exists(key, schema):
    with conn(key).cursor() as cur:
        cur.execute('SELECT 1 FROM pg_namespace WHERE nspname = %s', [schema])
        return cur.fetchone() is not None


def where_is(schema):
    """'' (main) or the own database key the registry gives for a schema."""
    link = AccountCompany.objects.filter(company__schema_name=schema).first()
    return (link.db_alias or '') if link else ''


# ---------------------------------------------------------------- shared rows
def _replica_tables(initial=False):
    """Shared tables that company tables point to: masters and content types (all rows). The migration history only
    when the database is made (its tables are a copy of the main database's at that moment); later the own database
    keeps its own history (cloud_migrate)."""
    from django.apps import apps
    out = ['django_content_type'] + (['django_migrations'] if initial else [])
    for m in apps.get_app_config('Core').get_models():
        out.append(m._meta.db_table)
    return out


def _copy_rows(src, dst, table, where='', params=None):
    """Insert or update every row of a table from src into dst (same columns, same ids)."""
    with src.cursor() as cur:
        cur.execute('SELECT a.attname FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace '
                    "WHERE n.nspname = 'public' AND c.relname = %s AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum", [table])
        cols = [r[0] for r in cur.fetchall()]
        if not cols:
            return 0
        cur.execute(f'SELECT {", ".join(chr(34) + c + chr(34) for c in cols)} FROM public."{table}" {where}', params or [])
        rows = cur.fetchall()
    if not rows:
        return 0
    with dst.cursor() as cur:
        cur.execute('SELECT a.attname FROM pg_index i JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) '
                    'WHERE i.indrelid = %s::regclass AND i.indisprimary', [f'public."{table}"'])
        pk = [r[0] for r in cur.fetchall()]
        qc = ', '.join(f'"{c}"' for c in cols)
        upd = ', '.join(f'"{c}" = EXCLUDED."{c}"' for c in cols if c not in pk)
        conflict = f'ON CONFLICT ({", ".join(chr(34) + c + chr(34) for c in pk)}) DO ' + (f'UPDATE SET {upd}' if upd else 'NOTHING') if pk else ''
        from psycopg2.extras import execute_values
        execute_values(cur, f'INSERT INTO public."{table}" ({qc}) VALUES %s {conflict}', rows, page_size=500)
        if pk == ['id']:   # numbering of the copy follows (django_migrations gets new rows in the own database)
            cur.execute(f"SELECT setval(pg_get_serial_sequence('public.\"{table}\"', 'id'), GREATEST((SELECT max(id) FROM public.\"{table}\"), 1))")
    return len(rows)


def users_for(key):
    """Ids of the users an own database needs: members of its companies, the owners, Adept staff."""
    from UserManagement.models import CustomUser
    comp_ids = list(AccountCompany.objects.filter(db_alias=key).values_list('company_id', flat=True))
    ids = set(CustomUser.objects.using(CONTROL).filter(tenants__id__in=comp_ids).values_list('id', flat=True)) if comp_ids else set()
    srv = DatabaseServer.objects.filter(key=key).select_related('account').first()
    if srv and srv.account and srv.account.owner_id:
        ids.add(srv.account.owner_id)
    ids |= set(PlatformAdmin.objects.values_list('user_id', flat=True))
    return sorted(ids)


def sync_shared(key, users=True, initial=False):
    """Bring the copies of the shared rows in an own database up to date. Returns {table: rows}."""
    src, dst = conn(CONTROL), conn(key)
    done = {}
    for t in _replica_tables(initial):
        try:
            done[t] = _copy_rows(src, dst, t)
        except Exception as e:          # a table missing on one side (before migrate) – report, go on
            log.warning('sync %s.%s: %s', key, t, e)
            done[t] = f'error: {e}'[:120]
    if users:
        ids = users_for(key)
        done['UserManagement_customuser'] = _copy_rows(src, dst, 'UserManagement_customuser', 'WHERE id = ANY(%s)', [ids]) if ids else 0
    return done


def push_user(user_id, keys=None):
    """A user was saved in the main database: refresh the copy in every own database that has (or needs) it.
    keys: own databases that need the user now (a company there was just given to the user)."""
    for srv in DatabaseServer.objects.filter(state='ready'):
        try:
            if (keys and srv.key in keys) or user_id in users_for(srv.key):
                _copy_rows(conn(CONTROL), conn(srv.key), 'UserManagement_customuser', 'WHERE id = %s', [user_id])
        except Exception:
            log.exception('push user %s to %s', user_id, srv.key)


def push_row(model, pk):
    t = model._meta.db_table
    for srv in DatabaseServer.objects.filter(state='ready'):
        try:
            _copy_rows(conn(CONTROL), conn(srv.key), t, 'WHERE id = %s', [pk])
        except Exception:
            log.exception('push %s %s to %s', t, pk, srv.key)


# ---------------------------------------------------------------- own database
def db_name_for(account):
    base = re.sub(r'[^a-z0-9_]', '_', str(connections[CONTROL].settings_dict['NAME']).lower())
    return f'{base}_acc{account.id}'[:63]


def create_own_db(account, by=None, note=''):
    """Create (or finish creating) the own database of an account. Returns the DatabaseServer."""
    from . import services as S
    key = f'acc{account.id}'
    srv, _ = DatabaseServer.objects.get_or_create(key=key, defaults={'account': account, 'name': db_name_for(account),
                                                                     'label': account.name, 'state': 'creating', 'note': note})
    if srv.state == 'ready':
        return srv
    srv.state = 'creating'
    srv.save(update_fields=['state'])
    dbrouting.forget()
    try:
        main = _params(CONTROL)
        with conn(CONTROL).cursor() as cur:
            cur.execute('SELECT 1 FROM pg_database WHERE datname = %s', [srv.name])
            if not cur.fetchone():
                cur.execute(f'CREATE DATABASE "{srv.name}"')
        own = dict(_params(key))
        psql, pg_dump = _tool('psql'), _tool('pg_dump')
        # structure of the shared tables (no rows – shared data stays in the main database)
        _run([psql, *_pg_args(own), '-d', srv.name, '-v', 'ON_ERROR_STOP=1', '-q', '-c', 'DROP SCHEMA IF EXISTS public CASCADE'], own)
        with tempfile.NamedTemporaryFile(suffix='.sql') as f:
            _run([pg_dump, *_pg_args(main), '-d', main['NAME'], '--schema-only', '--no-owner', '--no-privileges',
                  '-n', 'public', *(['-n', 'extensions'] if schema_exists('', 'extensions') else []), '-f', f.name], main)
            _run([psql, *_pg_args(own), '-d', srv.name, '-v', 'ON_ERROR_STOP=1', '-q', '-f', f.name], own)
        srv.state = 'ready'
        srv.save(update_fields=['state'])
        dbrouting.forget()
        sync_shared(key, initial=True)
        account.hosting, account.db_alias = 'own', key
        account.save(update_fields=['hosting', 'db_alias'])
        S.event(account, 'database', f'Own database {srv.name} created', by=by)
        return srv
    except Exception as e:
        srv.state, srv.note = 'failed', f'{e}'[:2000]
        srv.save(update_fields=['state', 'note'])
        dbrouting.forget()
        raise


# ---------------------------------------------------------------- move
def _tables_and_rows(key, schema):
    """{table: rows} and {sequence: last value} of one schema – the checks before the switch."""
    rows, seqs = {}, {}
    with conn(key).cursor() as cur:
        cur.execute("SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %s AND c.relkind IN ('r', 'p') ORDER BY 1", [schema])
        for (t,) in cur.fetchall():
            cur.execute(f'SELECT count(*) FROM "{schema}"."{t}"')
            rows[t] = cur.fetchone()[0]
        cur.execute("SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %s AND c.relkind = 'S' ORDER BY 1", [schema])
        for (s,) in cur.fetchall():
            cur.execute(f'SELECT last_value, is_called FROM "{schema}"."{s}"')
            seqs[s] = tuple(cur.fetchone())
    return rows, seqs


def _say(job, step, line=None):
    job.step = step[:120]
    if line or step:
        job.log = (job.log + f'{timezone.localtime():%H:%M:%S} {line or step}\n')[-20000:]
    job.save(update_fields=['step', 'log'])


def start_move(company, target, by=None, settle=None, run_now=False, account=None):
    """Queue (and start in a thread) the move of one company to target ('' = main database, or an own database key)."""
    link = AccountCompany.objects.select_related('account').filter(company=company).first()
    acc = account or (link.account if link else None)
    source = (link.db_alias or '') if link else ''
    if source == (target or ''):
        raise MoveError('The company is already there.')
    if MoveJob.objects.filter(schema=company.schema_name, state__in=('waiting', 'running')).exists():
        raise MoveError('This company is already being moved.')
    job = MoveJob.objects.create(account=acc, company=company, schema=company.schema_name, source=source, target=target or '',
                                 created_by=by if getattr(by, 'pk', None) else None)
    if run_now:
        run_move(job.id, settle=settle, own_thread=False)
        job.refresh_from_db()
    else:
        threading.Thread(target=run_move, args=(job.id,), kwargs={'settle': settle}, daemon=True).start()
    return job


def run_move(job_id, settle=None, own_thread=True):
    from . import services as S
    if own_thread:
        close_old_connections()
    job = MoveJob.objects.get(pk=job_id)
    schema, src, dst = job.schema, job.source, job.target
    settle = getattr(settings, 'ZEO_MOVE_SETTLE', 12) if settle is None else settle
    link = AccountCompany.objects.filter(company__schema_name=schema).first()
    restored = renamed = False
    job.state, job.started_at = 'running', timezone.now()
    job.save(update_fields=['state', 'started_at'])
    try:
        if not SAFE.match(schema):
            raise MoveError('Unusual schema name – move it by hand.')
        if not link:
            raise MoveError('The company is not linked to an account.')
        if (link.db_alias or '') != src:
            raise MoveError('The company is not where the job expects it.')
        for k in (src, dst):
            if k and not DatabaseServer.objects.filter(key=k, state='ready').exists():
                raise MoveError(f'Database {k} is not ready.')
        if not schema_exists(src, schema):
            raise MoveError('The company data is not in the source database.')
        if schema_exists(dst, schema):
            raise MoveError('The target database already has a schema with this name.')

        _say(job, 'Closing the company for the move')
        link.moving = True
        link.save(update_fields=['moving'])
        S.forget(schema)
        if settle:
            time.sleep(settle)          # every process sees "moving" (503) before the copy starts

        if dst:
            _say(job, 'Copying users and masters', f'shared rows → {dst}')
            sync_shared(dst)
            ids = set(users_for(dst))
            from UserManagement.models import CustomUser
            ids |= set(CustomUser.objects.using(CONTROL).filter(tenants=link.company_id).values_list('id', flat=True))
            if ids:
                _copy_rows(conn(CONTROL), conn(dst), 'UserManagement_customuser', 'WHERE id = ANY(%s)', [sorted(ids)])

        _say(job, 'Copying the data')
        ps, pd = _params(src), _params(dst)
        with tempfile.NamedTemporaryFile(suffix='.dump') as f:
            _run([_tool('pg_dump'), *_pg_args(ps), '-d', ps['NAME'], '-Fc', '-n', schema, '--no-owner', '--no-privileges', '-f', f.name], ps)
            restored = True
            _run([_tool('pg_restore'), *_pg_args(pd), '-d', pd['NAME'], '--no-owner', '--no-privileges', '--exit-on-error', f.name], pd)

        _say(job, 'Checking tables, rows and numbering')
        r1, s1 = _tables_and_rows(src, schema)
        r2, s2 = _tables_and_rows(dst, schema)
        if set(r1) != set(r2):
            raise MoveError(f'Tables differ: {sorted(set(r1) ^ set(r2))[:10]}')
        bad = [t for t in r1 if r1[t] != r2[t]]
        if bad:
            raise MoveError('Row counts differ: ' + ', '.join(f'{t} {r1[t]}→{r2[t]}' for t in bad[:10]))
        badseq = [s for s in s1 if s1[s] != s2.get(s)]
        if badseq:
            raise MoveError(f'Numbering differs: {badseq[:10]}')
        job.tables, job.rows = len(r1), sum(r1.values())
        _say(job, 'Switching to the new database', f'{job.tables} tables, {job.rows} rows, {len(s1)} sequences – same in both')

        old = f'zmv_{timezone.localdate():%Y%m%d}_{schema}'[:63]
        n = 1
        while schema_exists(src, old):
            n += 1
            old = f'zmv{n}_{timezone.localdate():%Y%m%d}_{schema}'[:63]
        with conn(src).cursor() as cur:
            cur.execute(f'ALTER SCHEMA "{schema}" RENAME TO "{old}"')
        renamed = True
        link.db_alias = dst
        link.save(update_fields=['db_alias'])
        dbrouting.forget()
        S.forget(schema)
        if settle:
            time.sleep(settle)          # every process now finds the company in the new database
        link.moving = False
        link.save(update_fields=['moving'])
        job.old_copy, job.keep_until = old, timezone.localdate() + timedelta(days=KEEP_DAYS)
        job.state, job.finished_at = 'done', timezone.now()
        job.save(update_fields=['old_copy', 'keep_until', 'state', 'finished_at', 'tables', 'rows'])
        _say(job, 'Done', f'old copy kept as {old} in {src or "the main database"} until {job.keep_until}')
        if link.account_id:
            S.event(link.account, 'move', f'{link.company.name} moved to {dst or "the shared cloud"}', by=job.created_by,
                    job=job.id, tables=job.tables, rows=job.rows)
        if dst:
            sync_shared(dst)            # users of this company are now needed there for good
    except Exception as e:
        log.exception('move %s', schema)
        try:
            if renamed:
                # the switch failed half way: undo the rename, the company stays where it was
                with conn(src).cursor() as cur:
                    cur.execute(f'ALTER SCHEMA "{old}" RENAME TO "{schema}"')
                if link:
                    link.db_alias = src
                    link.save(update_fields=['db_alias'])
            if restored and schema_exists(dst, schema):
                with conn(dst).cursor() as cur:
                    cur.execute(f'DROP SCHEMA "{schema}" CASCADE')
        except Exception:
            log.exception('move clean-up %s', schema)
        if link:
            link.moving = False
            link.save(update_fields=['moving'])
        dbrouting.forget()
        S.forget(schema)
        job.state, job.error, job.finished_at = 'failed', str(e)[:2000], timezone.now()
        job.save(update_fields=['state', 'error', 'finished_at'])
        _say(job, 'Failed – the company stays where it was', str(e)[:300])
    finally:
        if own_thread:
            close_old_connections()


def move_back(job, by=None, settle=None, run_now=False):
    """Roll back a finished move: the company goes back to where it was, with its current data."""
    if job.state != 'done':
        raise MoveError('Only a finished move can be moved back.')
    if not job.company:
        raise MoveError('The company no longer exists.')
    new = start_move(job.company, job.source, by=by, settle=settle, run_now=run_now, account=job.account)
    if new.state in ('waiting', 'running', 'done'):
        job.state = 'rolled_back'
        job.save(update_fields=['state'])
    return new


def drop_old_copies(today=None):
    """Old copies older than 7 days are removed (cloud_daily)."""
    today = today or timezone.localdate()
    out = []
    for job in MoveJob.objects.filter(state__in=('done', 'rolled_back')).exclude(old_copy='').filter(keep_until__lt=today):
        try:
            if schema_exists(job.source, job.old_copy):
                with conn(job.source).cursor() as cur:
                    cur.execute(f'DROP SCHEMA "{job.old_copy}" CASCADE')
            out.append(job.old_copy)
            job.old_copy = ''
            job.save(update_fields=['old_copy'])
        except Exception:
            log.exception('drop old copy %s', job.old_copy)
    return out


def retire_empty():
    """An own database without companies (the account left Premium) is retired: no longer used; dropped by cloud_daily
    once the old copies in it are past their 7 days."""
    for srv in DatabaseServer.objects.filter(state='ready'):
        if not AccountCompany.objects.filter(db_alias=srv.key).exists() and srv.account and srv.account.db_alias != srv.key:
            srv.state = 'retired'
            srv.save(update_fields=['state'])
    dbrouting.forget()


def drop_retired(today=None):
    """Drop retired own databases whose old copies have all expired (cloud_daily)."""
    today = today or timezone.localdate()
    out = []
    for srv in DatabaseServer.objects.filter(state='retired'):
        if MoveJob.objects.filter(source=srv.key, keep_until__gte=today).exclude(old_copy='').exists():
            continue
        if AccountCompany.objects.filter(db_alias=srv.key).exists():
            continue
        try:
            if srv.key in connections.settings:
                connections[srv.key].close()
            with conn(CONTROL).cursor() as cur:
                cur.execute('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()', [srv.name])
                cur.execute(f'DROP DATABASE IF EXISTS "{srv.name}"')
            MoveJob.objects.filter(source=srv.key).exclude(old_copy='').update(old_copy='')
            srv.delete()
            out.append(srv.name)
        except Exception:
            log.exception('drop retired database %s', srv.name)
    return out


# ---------------------------------------------------------------- plan changes
def change_hosting(account, to_own, by=None, settle=None, run_now=False):
    """Premium ↔ shared: create the own database if needed and move every company of the account, one after the other.

    Returns the ids of the companies that will move."""
    target = create_own_db(account, by=by).key if to_own else ''
    ids = [l.company_id for l in AccountCompany.objects.filter(account=account, archived=False) if (l.db_alias or '') != target]
    if not to_own:
        account.hosting, account.db_alias = 'shared', ''
        account.save(update_fields=['hosting', 'db_alias'])
    if run_now:
        _move_many(ids, target, getattr(by, 'pk', None), settle, own_thread=False)
    else:
        threading.Thread(target=_move_many, args=(ids, target, getattr(by, 'pk', None), settle), daemon=True).start()
    return ids


def _move_many(company_ids, target, by_id, settle, own_thread=True):
    from UserManagement.models import company as Company, CustomUser
    if own_thread:
        close_old_connections()
    by = CustomUser.objects.filter(pk=by_id).first() if by_id else None
    for cid in company_ids:
        comp = Company.objects.filter(pk=cid).first()
        if comp:
            try:
                start_move(comp, target, by=by, settle=settle, run_now=True)
            except MoveError:
                log.warning('move %s skipped', cid)
    if not target:
        retire_empty()
    if own_thread:
        close_old_connections()


# ---------------------------------------------------------------- updates
def migrate_all(stdout=None):
    """Shared migrations in every own database, company migrations in every company wherever it lives, shared rows."""
    from django.core.management import call_command
    out = {}
    for srv in DatabaseServer.objects.filter(state='ready'):
        with dbrouting.pin(srv.key):
            call_command('migrate_schemas', '--shared', interactive=False, verbosity=0)
        out[srv.key] = 'shared ok'
    call_command('migrate_schemas', '--tenant', interactive=False, verbosity=0, executor='standard')
    for srv in DatabaseServer.objects.filter(state='ready'):
        sync_shared(srv.key)
    return out
