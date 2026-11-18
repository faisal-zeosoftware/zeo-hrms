"""v1.70.1 – the system folders follow their records: a new employee / project / partner … gets a folder, a renamed one renames it."""
import logging

from django.apps import apps
from django.db import connection
from django.db.models.signals import post_save

log = logging.getLogger(__name__)
_done = False


def _handler(key):
    def h(sender, instance, created=False, raw=False, **kw):
        if raw or getattr(connection, 'schema_name', 'public') == 'public':
            return
        try:
            from . import services as S
            from .models import DocFolder
            have = DocFolder.objects.filter(provider=key, record_id=instance.pk, kind='record').exists()
            if not have and not S.provider_root(key):
                return   # the app's root folder is made when the Document Center is first opened
            S.record_folder(key, instance, create=True)
        except Exception:   # noqa: BLE001  (table not there yet while a company is being made, …)
            log.debug('dms folder of %s %s', key, getattr(instance, 'pk', None), exc_info=True)
    h.__name__ = f'dms_folder_{key}'
    return h


_HANDLERS = {}


def connect():
    global _done
    if _done:
        return
    from .services import PROVIDERS
    for key, p in PROVIDERS.items():
        try:
            Model = apps.get_model(p[1])
        except (LookupError, ValueError):
            continue
        _HANDLERS[key] = _handler(key)
        post_save.connect(_HANDLERS[key], sender=Model, weak=False, dispatch_uid=f'zeodocs-{key}')
    _done = True
