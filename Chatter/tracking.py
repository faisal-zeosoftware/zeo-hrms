"""
Change history for every record of every company table.

A middleware keeps the current request; signals compare a record with its saved version and write
one RecordLog per create / change / delete with the user, time, IP address, screen (API path) and
the old and new value of every changed field. Linked records are logged by name, not by id.
Nothing is logged in the public schema, for this app's own tables or for technical tables.
"""
import datetime
import decimal
import logging
import threading

from django.apps import apps
from django.db import connection
from django.db.models.signals import post_delete, post_save, pre_save

logger = logging.getLogger(__name__)
_local = threading.local()

SKIP_APPS = {'migrations', 'Chatter', 'contenttypes', 'sessions', 'admin', 'auth', 'token_blacklist', 'django_celery_beat',
             'django_celery_results', 'tenants', 'Core', 'tenant_users', 'permissions'}
SKIP_FIELDS = {'password', 'last_login', 'created_at', 'updated_at', 'modified_at', 'created_on', 'updated_on',
               'date_joined', 'last_updated'}
SKIP_MODELS = {'DataTools.formsetting', 'NotificationManagement.notification'}


def current_request():
    return getattr(_local, 'request', None)


class CurrentRequestMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        _local.request = request
        try:
            return self.get_response(request)
        finally:
            _local.request = None


def model_key(model):
    return f'{model._meta.app_label}.{model._meta.model_name}'


from contextlib import contextmanager


@contextmanager
def paused():
    """v1.26.0 – no field-by-field history inside the block (bulk uploads, monthly accrual); the caller writes one summary
    note instead. Each save used to run an extra SELECT and write a history row (≈3 queries per record)."""
    prev = getattr(_local, 'paused', False)
    _local.paused = True
    try:
        yield
    finally:
        _local.paused = prev


def _tracked(sender):
    if getattr(_local, 'paused', False) or connection.schema_name == 'public':
        return False
    meta = sender._meta
    if meta.app_label in SKIP_APPS or model_key(sender) in SKIP_MODELS or meta.auto_created:
        return False
    return getattr(sender, 'zeo_track', True)


def _plain(v):
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (datetime.date, datetime.datetime, datetime.time)):
        return v.isoformat()
    from django.db.models.fields.files import FieldFile
    if isinstance(v, FieldFile):                          # file field (empty ones have no url – v1.10.0)
        return v.name or None
    return str(v)


def _fields(model):
    return [f for f in model._meta.concrete_fields
            if not f.primary_key and f.name not in SKIP_FIELDS and not getattr(f, 'auto_now', False)
            and not getattr(f, 'auto_now_add', False)]


def _snapshot(instance):
    snap = {}
    for f in _fields(type(instance)):
        snap[f.name] = getattr(instance, f.attname, None)
    return snap


def _display(field, raw, instance=None):
    """Readable value: linked records by their name, choices by their label."""
    if raw in (None, ''):
        return None
    if field.is_relation and field.many_to_one:
        # v1.16.0 (D15): during a payroll run the linked record the code just saved is already in memory (payslip,
        # component, employee …) – use it instead of reading it again (~25 queries per employee in a run)
        if instance is not None:
            try:
                from PayrollManagement.perf import active
                if active():
                    cached = field.get_cached_value(instance, None)
                    if cached is not None and cached.pk == raw:
                        return str(cached)
            except Exception:
                pass
        try:
            obj = field.related_model._default_manager.filter(pk=raw).first()
            return str(obj) if obj is not None else f'#{raw}'
        except Exception:
            return f'#{raw}'
    if field.choices:
        return str(dict(field.flatchoices).get(raw, raw))
    return _plain(raw)


def _label(field):
    v = str(field.verbose_name or field.name)
    return v[:1].upper() + v[1:].replace('_', ' ')


def _write(instance, action, changes):
    from .models import RecordLog
    req = current_request()
    user = getattr(req, 'user', None) if req is not None else None
    if user is not None and not getattr(user, 'is_authenticated', False):
        user = None
    try:
        label = str(instance)[:255]
    except Exception:
        label = ''
    meta = getattr(req, 'META', {}) if req is not None else {}
    ip = (meta.get('HTTP_X_FORWARDED_FOR') or meta.get('REMOTE_ADDR') or '').split(',')[0].strip()
    name = ''
    if user is not None:
        name = (getattr(user, 'get_full_name', lambda: '')() or getattr(user, 'username', '') or getattr(user, 'email', ''))
    from django.db import DatabaseError, transaction
    try:
        # v1.13.1: a savepoint, so a company schema that is still being created (history table not there yet)
        # never breaks the save that triggered the log line
        with transaction.atomic():
            RecordLog.objects.create(
                model=model_key(type(instance)), object_id=str(instance.pk), record_label=label, action=action,
                changes=changes, user=user, user_name=name[:150] or ('System' if user is None else ''),
                ip=ip[:64], path=(getattr(req, 'path', '') or '')[:255], method=getattr(req, 'method', '') or '',
                agent=(meta.get('HTTP_USER_AGENT') or '')[:255])
    except DatabaseError:
        logger.debug('record log skipped for %s (history table not available yet)', model_key(type(instance)))


def _pre_save(sender, instance, raw=False, **kwargs):
    if raw or not _tracked(sender):
        return
    instance._zeo_old = None
    if instance.pk is None:
        return
    try:
        old = sender._default_manager.filter(pk=instance.pk).first()
        instance._zeo_old = _snapshot(old) if old is not None else None
    except Exception:
        instance._zeo_old = None


def _post_save(sender, instance, created=False, raw=False, **kwargs):
    if raw or not _tracked(sender):
        return
    try:
        new = _snapshot(instance)
        old = getattr(instance, '_zeo_old', None)
        fields = {f.name: f for f in _fields(sender)}
        changes = {}
        if created or old is None:
            for k, v in new.items():
                if v not in (None, '', [], {}):
                    changes[k] = {'label': _label(fields[k]), 'old': None, 'new': _display(fields[k], v, instance)}
            action = 'created'
        else:
            for k, v in new.items():
                if _plain(old.get(k)) != _plain(v):
                    changes[k] = {'label': _label(fields[k]), 'old': _display(fields[k], old.get(k)), 'new': _display(fields[k], v, instance)}
            if not changes:
                return
            action = 'updated'
        _write(instance, action, changes)
    except Exception:
        logger.exception('change history not written for %s', sender)


def _post_delete(sender, instance, **kwargs):
    if not _tracked(sender):
        return
    try:
        _write(instance, 'deleted', {})
    except Exception:
        logger.exception('delete history not written for %s', sender)


_installed = False


def install():
    global _installed
    if _installed:
        return
    pre_save.connect(_pre_save, dispatch_uid='zeo_track_pre')
    post_save.connect(_post_save, dispatch_uid='zeo_track_post')
    post_delete.connect(_post_delete, dispatch_uid='zeo_track_delete')
    _installed = True
