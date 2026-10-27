"""v1.62.0 – which company and record each uploaded file belongs to (README-UPDATE 83.1, gap 1 of the security review).

Since 1.56.1 /media/ opens files only for signed-in users – but any signed-in user of the server could open any file whose
address they knew. Now every file saved through a file field is written to ZeoCloud.MediaFile (path, company, record), and
/media/ opens it only for a user of that company who may open the record itself (the same rules as the screens: own
records for employees, branches for branch HR, the add-on apps' own rules, the record of a note / attachment).

* saving / deleting any record with a file field keeps the register up to date (signals);
* `python manage.py media_register` fills it for the files that already exist (run once after the update; it is safe to
  run again);
* a file that is in no record (older generated files, temporary exports) still opens for signed-in users – unless
  ZEO_MEDIA_STRICT=true, then only for the server administrator.
"""
import logging

from django.apps import apps
from django.core.cache import cache
from django.db import connection, transaction
from django.db.models import FileField
from django.db.models.signals import post_delete, post_save

log = logging.getLogger(__name__)
_fields = {}
SKIP_APPS = {'ZeoCloud', 'admin', 'sessions', 'contenttypes', 'auth'}


def file_fields(model):
    k = model._meta.label
    if k not in _fields:
        _fields[k] = [f.name for f in model._meta.get_fields() if isinstance(f, FileField) and not getattr(f, 'auto_created', False)
                      and getattr(f, 'concrete', True)]
    return _fields[k]


def _schema():
    return getattr(connection, 'schema_name', 'public') or 'public'


def _on_save(sender, instance, raw=False, **kw):
    if raw or sender._meta.app_label in SKIP_APPS:
        return
    names = file_fields(sender)
    if not names:
        return
    schema = _schema()
    if schema == 'public':
        return
    try:
        from django_tenants.utils import schema_context
        from .models import MediaFile
        label, pk = sender._meta.label, str(instance.pk)
        rows = []
        for n in names:
            v = getattr(instance, n, None)
            path = getattr(v, 'name', '') if v else ''
            with schema_context('public'):
                MediaFile.objects.filter(schema=schema, model=label, object_id=pk, field=n).exclude(path=path).delete()
            if path:
                rows.append(MediaFile(path=path[:500], schema=schema, model=label, object_id=pk, field=n))
        if rows:
            with schema_context('public'):
                MediaFile.objects.bulk_create(rows, ignore_conflicts=True)
    except Exception:
        log.exception('media register (save)')


def _on_delete(sender, instance, **kw):
    if sender._meta.app_label in SKIP_APPS or not file_fields(sender):
        return
    schema = _schema()
    if schema == 'public':
        return
    try:
        from django_tenants.utils import schema_context
        from .models import MediaFile
        with schema_context('public'):
            MediaFile.objects.filter(schema=schema, model=sender._meta.label, object_id=str(instance.pk)).delete()
    except Exception:
        log.exception('media register (delete)')


def install():
    post_save.connect(_on_save, dispatch_uid='zeo-media-register-save')
    post_delete.connect(_on_delete, dispatch_uid='zeo-media-register-delete')


def register_schema(schema):
    """Every file of every record of one company → MediaFile. Returns the number of files."""
    from django_tenants.utils import schema_context
    from .models import MediaFile
    n = 0
    with schema_context(schema):
        for m in apps.get_models():
            if m._meta.app_label in SKIP_APPS or m._meta.proxy or not m._meta.managed:
                continue
            names = file_fields(m)
            if not names:
                continue
            for n_ in names:
                try:
                    with transaction.atomic():
                        rows = [MediaFile(path=str(p)[:500], schema=schema, model=m._meta.label, object_id=str(pk), field=n_)
                                for pk, p in m._default_manager.exclude(**{n_: ''}).exclude(**{f'{n_}__isnull': True}).values_list('pk', n_)
                                if p]
                except Exception:   # a table that does not exist in this company (app not migrated there)
                    log.info('media register: %s skipped in %s', m._meta.label, schema)
                    continue
                if rows:
                    with schema_context('public'):
                        MediaFile.objects.bulk_create(rows, ignore_conflicts=True, batch_size=2000)
                    n += len(rows)
    return n


# ------------------------------------------------------------------ the check at /media/
class _Req:
    """What the access rules need from a request."""
    method = 'GET'

    def __init__(self, user, path=''):
        self.user = user
        self.GET = {}
        self.query_params = {}
        self.data = {}
        self.META = {}
        self.path = path


class _View:
    zeo_scope = True
    action = 'retrieve'


def record_ok(user, schema, label, object_id):
    """May the user open this record of that company?"""
    from django_tenants.utils import schema_context
    try:
        model = apps.get_model(label)
    except Exception:
        return False
    with schema_context(schema):
        r = _Req(user)
        try:
            if label == 'Chatter.Attachment':
                from Chatter.models import Attachment
                from Chatter.views import _att_access
                a = Attachment.objects.filter(pk=object_id).first()
                return bool(a is not None and _att_access(r, a))
            from AccessControl.access import ctx, scope_queryset
            if ctx(r).admin:
                return True
            qs = model._default_manager.filter(pk=object_id)
            from Chatter.views import MODEL_SCOPES
            own = MODEL_SCOPES.get(model._meta.label)
            if own is not None:
                return own(r, qs).exists()
            return scope_queryset(r, _View(), qs).exists()
        except Exception:
            log.exception('media record check %s %s', label, object_id)
            return False


def may_open(user, path):
    """True / False for a registered file; None when the file is in no record."""
    from django_tenants.utils import schema_context
    from .models import MediaFile
    k = f'zeo:mf:{user.pk}:{path}'
    v = cache.get(k)
    if v is not None:
        return None if v == 'n' else v == 'y'
    with schema_context('public'):
        rows = list(MediaFile.objects.filter(path=path).values_list('schema', 'model', 'object_id')[:20])
        mine = set(user.tenants.values_list('schema_name', flat=True)) if not user.is_superuser else None
    if not rows:
        cache.set(k, 'n', 60)
        return None
    ok = False
    for schema, label, oid in rows:
        if mine is not None and schema not in mine:
            continue
        if user.is_superuser or record_ok(user, schema, label, oid):
            ok = True
            break
    cache.set(k, 'y' if ok else 'x', 60)
    return ok
