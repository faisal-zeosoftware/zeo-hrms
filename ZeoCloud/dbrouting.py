"""v1.24.0 – which database each company lives in, and the router for shared tables.

The registry is read from the main database (ZeoCloud_accountcompany.db_alias + ZeoCloud_databaseserver) through the
'control' connection and kept for a few seconds in each process. A move waits longer than that before it copies.
"""
import contextlib
import os
import threading
import time

from django.conf import settings

TTL = 8           # seconds a process keeps the registry
_lock = threading.Lock()
_cache = {'at': 0.0, 'schemas': {}, 'servers': {}}


def _load():
    from django.db import connections
    schemas, servers = {}, {}
    try:
        c = connections['control']
        c.set_schema_to_public()
        with c.cursor() as cur:
            cur.execute('SELECT key, host, port, name, "user", password_env FROM "ZeoCloud_databaseserver" '
                        "WHERE state IN ('ready', 'creating')")
            for key, host, port, name, user, penv in cur.fetchall():
                servers[key] = {'HOST': host, 'PORT': port, 'NAME': name, 'USER': user, 'PASSWORD_ENV': penv}
            cur.execute('SELECT c.schema_name, a.db_alias FROM "ZeoCloud_accountcompany" a '
                        'JOIN "UserManagement_company" c ON c.id = a.company_id WHERE a.db_alias <> \'\'')
            schemas = {s: k for s, k in cur.fetchall() if k in servers}
    except Exception:
        # tables not there yet (before migrate) – everything is in the main database
        return {}, {}
    return schemas, servers


def refresh(force=False):
    now = time.monotonic()
    if not force and now - _cache['at'] < TTL:
        return _cache
    with _lock:
        if force or now - _cache['at'] >= TTL:
            s, v = _load()
            _cache.update(at=time.monotonic(), schemas=s, servers=v)
    return _cache


def forget():
    _cache['at'] = 0.0


def db_of_schema(schema):
    if not schema or schema == 'public':
        return None
    return refresh()['schemas'].get(schema)


def params_of(key, main):
    """NAME / HOST / PORT / USER / PASSWORD of an own database; empty fields = the main database's."""
    v = refresh()['servers'].get(key) or refresh(force=True)['servers'].get(key)
    if not v:
        return None
    pw = os.environ.get(v['PASSWORD_ENV'], '') if v['PASSWORD_ENV'] else main.get('PASSWORD')
    return {'NAME': v['NAME'], 'HOST': v['HOST'] or main.get('HOST'), 'PORT': v['PORT'] or main.get('PORT'),
            'USER': v['USER'] or main.get('USER'), 'PASSWORD': pw}


def ensure_alias(key):
    """A separate connection named after the own database (public schema) – copies of shared rows, checks."""
    from django.db import connections
    if key in connections.settings:
        return connections[key]
    base = dict(connections['control'].settings_dict)
    base.update(params_of(key, connections['default']._zeo_main) or {})
    base['ENGINE'] = 'django_tenants.postgresql_backend'
    base.pop('SCHEMA', None)
    connections.settings[key] = base
    c = connections[key]
    c.set_schema_to_public()
    return c


@contextlib.contextmanager
def pin(key):
    """Point 'default' at one database for the public schema too (migrations of an own database, copies)."""
    from django.db import connection
    old_pin, old_want = connection._zeo_pin, connection._zeo_want
    old_schema = connection.schema_name
    connection._zeo_pin = None if key in (None, 'control') else key
    connection.set_schema_to_public()
    try:
        yield connection
    finally:
        connection._zeo_pin = old_pin
        if old_schema:
            connection.set_schema(old_schema)
        else:
            connection._zeo_want = old_want


# ---------------------------------------------------------------- router
_SHARED_ONLY = None


def shared_only(app_label):
    global _SHARED_ONLY
    if _SHARED_ONLY is None:
        from django.apps import apps
        tenant = set(settings.TENANT_APPS)
        out = set()
        for a in apps.get_app_configs():
            if a.name in settings.SHARED_APPS and a.name not in tenant:
                out.add(a.label)
        _SHARED_ONLY = out
    return app_label in _SHARED_ONLY


def _on_own_db():
    from django.db import connections
    c = connections['default']
    if getattr(c, '_zeo_pin', None):
        return False      # pinned (migrations, copies): everything stays in that database
    return getattr(c, '_zeo_want', 'control') != 'control'


class ControlRouter:
    """While the connection is in an own database, shared tables (users, companies, accounts …) go to the main one."""

    def db_for_read(self, model, **hints):
        if _on_own_db() and shared_only(model._meta.app_label):
            return 'control'
        return None

    db_for_write = db_for_read

    def allow_relation(self, obj1, obj2, **hints):
        dbs = {obj1._state.db, obj2._state.db}
        if dbs <= {'default', 'control', None}:
            return True
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if db != 'default':
            return False
        return None
