"""v1.52.0 – imports: the column mapping of a file layout is remembered per screen, and a committed import can be undone
(the records it created are deleted; a record that is used elsewhere is kept and listed)."""
import hashlib
import logging

from django.apps import apps
from django.db import transaction
from django.db.models import ProtectedError
from django.utils import timezone

log = logging.getLogger(__name__)


def headers_key(headers):
    norm = sorted(str(h).strip().lower() for h in headers or [] if str(h).strip())
    return hashlib.sha256('|'.join(norm).encode()).hexdigest()


def find_mapping(endpoint, headers):
    from .models import ImportMapping
    m = ImportMapping.objects.filter(endpoint=endpoint, headers_key=headers_key(headers)).first()
    return m


def save_mapping(endpoint, headers, mapping, user=None, name=''):
    from .models import ImportMapping
    if not headers or not isinstance(mapping, dict):
        return None
    m, _ = ImportMapping.objects.get_or_create(endpoint=endpoint, headers_key=headers_key(headers),
                                               defaults={'headers': list(headers), 'name': name or endpoint.strip('/').split('/')[-1]})
    m.headers, m.mapping, m.used = list(headers), {str(k): str(v) for k, v in mapping.items() if v}, (m.used or 0) + 1
    if user is not None and getattr(user, 'pk', None):
        m.user = user
    if name:
        m.name = name[:120]
    m.save()
    return m


def remember(request, endpoint, model, res, data):
    """After a committed import: the batch (created ids) and the mapping. Returns the batch id."""
    from .models import ImportBatch
    ids = [r['id'] for r in res.get('results', []) if r.get('ok') and r.get('id') is not None]
    key = f'{model._meta.app_label}.{model._meta.model_name}' if model is not None else ''
    if data.get('headers') and isinstance(data.get('mapping'), dict):
        try:
            save_mapping(endpoint, data.get('headers'), data.get('mapping'), request.user)
        except Exception:
            log.exception('import mapping')
    if not res.get('ok'):
        return None
    b = ImportBatch.objects.create(endpoint=endpoint, label=str(data.get('label') or '')[:120], file_name=str(data.get('file_name') or '')[:200],
                                   model=key, created_ids=ids, rows=res.get('total', 0), ok=res.get('ok', 0), failed=res.get('failed', 0),
                                   user=request.user if getattr(request.user, 'pk', None) else None)
    return b.pk


def undo(batch, user):
    """Delete the records of the import. Returns {deleted, kept: [{id, why}]}."""
    if batch.status == 'undone':
        raise ValueError('This import is already undone.')
    M = apps.get_model(*batch.model.split('.')) if batch.model else None
    if M is None:
        raise ValueError('The records of this import cannot be found.')
    deleted, kept = 0, []
    from Chatter.tracking import paused
    left = []
    hook = state = None
    if batch.endpoint == '/employee-profile/api/doc-bulk/':   # v1.53.2 document files: restore what the import changed
        from EmployeeProfile import docbulk as hook
        state = hook.before_undo(batch)
    with paused():
        for pk in batch.created_ids or []:
            obj = M.objects.filter(pk=pk).first()
            if obj is None:
                continue
            try:
                with transaction.atomic():
                    obj.delete()
                deleted += 1
            except ProtectedError as e:
                kept.append({'id': pk, 'why': 'used by other records: ' + ', '.join(sorted({str(o._meta.verbose_name) for o in list(e.protected_objects)[:5]}))})
                left.append(pk)
            except Exception as e:
                kept.append({'id': pk, 'why': str(e)[:200]})
                left.append(pk)
    if hook is not None:
        hook.after_undo(batch, state, left)
    batch.status = 'partly' if left else 'undone'
    batch.undone_by, batch.undone_at = user, timezone.now()
    batch.undo_note = f'{deleted} deleted' + (f', {len(left)} kept' if left else '')
    batch.created_ids = left
    batch.save()
    try:
        from Chatter.models import Message
        from Chatter.tracking import model_key
        Message.objects.create(model=model_key(M), object_id='import', author=user, body=f'Import undone: {deleted} records deleted' + (f', {len(left)} kept.' if left else '.'))
    except Exception:
        pass
    return {'deleted': deleted, 'kept': kept}


def batch_json(b):
    return {'id': b.id, 'endpoint': b.endpoint, 'label': b.label, 'file_name': b.file_name, 'model': b.model, 'rows': b.rows, 'ok': b.ok,
            'failed': b.failed, 'records': len(b.created_ids or []), 'status': b.status, 'status_label': b.get_status_display(),
            'undo_note': b.undo_note, 'user': (b.user.get_full_name() or b.user.username) if b.user_id else '', 'at': b.at,
            'undone_at': b.undone_at}
