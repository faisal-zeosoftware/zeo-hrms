"""Ask ZEO – every change: preview → confirm → applied (recorded "via Ask ZEO") → undo.

Two kinds of plan
* records  [{op: update|create, model: 'App.Model', id, values: {field: value}, label}]  – data and setup changes, made with the
           ORM inside one transaction (the model's own save(), signals and change history run as usual). Undo puts the
           saved "before" values back / deletes what was created.
* api      [{method, path, data, label, undo: {method, path, data} | None}]  – the product's own APIs called as the user
           (requests, payroll steps); `{id}` in an undo path is the id the call answered with.

Safeguards: a change is only shown as a preview; nothing happens before the user confirms it. Changes of setup or company
data (group 'setup') need the company switch "Ask ZEO may change setup" (Settings › Ask ZEO, default off). The user's own
requests and the guided payroll steps (group 'work') are the same actions the user may do on the screens.
"""
import datetime
import decimal
import logging

from django.apps import apps
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.utils import timezone

from Translations.i18n import tr

from . import apicall
from .models import AskSettings, Change

log = logging.getLogger(__name__)
SETUP_KINDS = {'records', 'setup', 'bulk', 'data_fix'}


class Refused(Exception):
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status = status


def _plain(v):
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return str(v)
    if hasattr(v, 'pk'):
        return v.pk
    return v


def _snapshot(obj, fields):
    out = {}
    for f in fields:
        fld = obj._meta.get_field(f)
        out[f] = _plain(getattr(obj, fld.attname))
    return out


def preview(request, conversation, kind, title, plan, summary='', group='setup', undoable=True):
    """Stores the proposed change; nothing is changed yet. Returns the Change."""
    before, after = [], []
    if plan.get('records'):
        for op in plan['records']:
            Mdl = apps.get_model(op['model'])
            if op['op'] == 'update':
                obj = Mdl.objects.get(pk=op['id'])
                before.append({'model': op['model'], 'id': op['id'], 'label': op.get('label') or str(obj)[:120], 'values': _snapshot(obj, op['values'])})
                after.append({'model': op['model'], 'id': op['id'], 'label': op.get('label') or str(obj)[:120],
                              'values': {f: _plain(v) for f, v in op['values'].items()}})
            else:
                after.append({'model': op['model'], 'id': None, 'label': op.get('label') or '', 'values': {f: _plain(v) for f, v in op['values'].items()}})
    plan = dict(plan, group=group)
    import json
    from django.core.serializers.json import DjangoJSONEncoder
    plan = json.loads(json.dumps(plan, cls=DjangoJSONEncoder))   # dates and amounts kept as text; the model's full_clean() turns them back
    return Change.objects.create(user=request.user, conversation=conversation, kind=kind, title=title[:255], summary=summary, plan=plan,
                                 before=before, after=after, undoable=undoable)


def card(ch, lang=None):
    """The preview card shown in the chat."""
    rows = []
    for i, a in enumerate(ch.after or []):
        b = (ch.before[i]['values'] if i < len(ch.before or []) else {})
        for f, v in a['values'].items():
            rows.append({'record': a.get('label') or '', 'field': f, 'before': b.get(f, '–') if b else '–', 'after': v})
    for s in (ch.plan.get('api') or []):
        verb = {'post': 'Make', 'put': 'Change', 'patch': 'Change', 'delete': 'Remove'}.get(s['method'].lower(), s['method'].upper())   # 1.74.0: words, not HTTP verbs
        rows.append({'record': s.get('label') or s['path'], 'field': tr(verb, lang=lang), 'before': '', 'after': s.get('show') or ''})
    s = AskSettings.get()
    blocked = ch.plan.get('group') == 'setup' and not s.may_change_setup and not ch.plan.get('via')   # v1.73.17
    return {'type': 'change', 'id': ch.pk, 'title': ch.title, 'summary': ch.summary, 'state': ch.state, 'rows': rows[:200], 'more': max(0, len(rows) - 200),
            'blocked': blocked, 'undoable': ch.undoable,
            'blocked_text': tr('Ask ZEO may not change setup in this company. An administrator can switch this on in Settings › Ask ZEO.', lang=lang) if blocked else ''}


def by_api_key(request):
    """v1.73.17: the call is made with an API key (an AI tool, a script) – not a person signed in."""
    auth = getattr(request, 'auth', None)
    return isinstance(auth, dict) and bool(auth.get('api_key'))


def _check_owner(request, ch):
    if (ch.plan or {}).get('via'):   # v1.73.17 a draft from an AI tool (API key): a company administrator approves it, never a key
        from AccessControl.access import ctx
        if by_api_key(request):
            raise Refused(tr('An API key cannot approve a change. A company administrator approves it in Settings › Ask ZEO.'), 403)
        if not ctx(request).admin:
            raise Refused(tr('A change proposed by an AI tool is approved by a company administrator.'), 403)
        return
    if ch.user_id != request.user.pk:
        raise Refused(tr('This change was proposed to another user.'), 403)


@transaction.atomic
def _apply_records(ch):
    after = []
    for i, op in enumerate(ch.plan['records']):
        Mdl = apps.get_model(op['model'])
        if op['op'] == 'update':
            obj = Mdl.objects.select_for_update().get(pk=op['id'])
            now = _snapshot(obj, op['values'])
            if now != ch.before[i]['values']:
                raise Refused(tr('{0} was changed by somebody else after the preview. Ask again for a new preview.', ch.before[i]['label']), 409)
            for f, v in op['values'].items():
                setattr(obj, Mdl._meta.get_field(f).attname, v)
            obj.full_clean(exclude=[f.name for f in Mdl._meta.fields if f.name not in op['values']])
            obj.save()
            after.append({'model': op['model'], 'id': obj.pk, 'label': ch.after[i]['label'], 'values': _snapshot(obj, op['values'])})
        else:
            obj = Mdl(**{Mdl._meta.get_field(f).attname: v for f, v in op['values'].items()})
            obj.full_clean()
            obj.save()
            after.append({'model': op['model'], 'id': obj.pk, 'label': ch.after[i]['label'] or str(obj)[:120], 'values': _snapshot(obj, op['values'])})
    return after


def _fill(v, ids):
    if isinstance(v, str):
        for k, x in ids.items():
            v = v.replace('{' + k + '}', str(x))
        return v
    if isinstance(v, dict):
        return {k: _fill(x, ids) for k, x in v.items()}
    if isinstance(v, list):
        return [_fill(x, ids) for x in v]
    return v


def confirm(request, ch):
    from django.core.exceptions import ValidationError
    _check_owner(request, ch)
    if ch.state != 'preview':
        raise Refused(tr('This change was already {0}.', tr(ch.get_state_display()).lower()))
    # v1.73.17 an AI tool's draft is approved by a company administrator in person – the company switch is for Ask ZEO's own users
    if ch.plan.get('group') == 'setup' and not ch.plan.get('via') and not AskSettings.get().may_change_setup:
        raise Refused(tr('Ask ZEO may not change setup in this company. An administrator can switch this on in Settings › Ask ZEO.'), 403)
    if (ch.plan or {}).get('via'):   # v1.73.17 who approved the AI tool's draft
        ch.plan = dict(ch.plan, via=dict(ch.plan['via'], approved_by=request.user.username, approved_at=timezone.now().isoformat()))
    try:
        if ch.plan.get('records'):
            ch.after = _apply_records(ch)
        if ch.plan.get('custom'):
            if ch.plan['custom'] == 'map_upload':   # 1.72.0: a mapped client file → data workbook upload + check
                from .aiwork import run_map_upload as run_custom
            else:
                from .guided import run_custom
            try:
                ch.result = run_custom(request, ch)
            except apicall.ApiError as e:
                ch.result = {'error': e.detail}
                raise Refused(e.detail, e.status if e.status < 500 else 400)
        elif ch.plan.get('api'):
            done, ids = [], {}
            import contextlib
            whole = transaction.atomic() if ch.plan.get('atomic') else contextlib.nullcontext()   # 1.72.0: all steps or none (bulk)
            with whole:
                _run_steps(request, ch, done, ids)
            ch.result = {'done': done, 'ids': ids}
    except Refused:
        if ch.plan.get('atomic') and isinstance(ch.result, dict):
            ch.result = dict(ch.result, done=[], rolled_back=True)   # nothing of it stayed
        ch.state, ch.error = 'failed', ch.result.get('error', '') if isinstance(ch.result, dict) else ''
        ch.save()
        raise
    except ValidationError as e:
        ch.state, ch.error = 'failed', '; '.join(f'{k}: {" ".join(map(str, v))}' for k, v in getattr(e, 'message_dict', {'': e.messages}).items())
        ch.save()
        raise Refused(ch.error)
    ch.state, ch.applied_at = 'applied', timezone.now()
    ch.save()
    return ch


def _run_steps(request, ch, done, ids):
    for i, s in enumerate(ch.plan['api']):
        try:
            files = None
            if s.get('files'):   # uploaded files sent with the call (multipart), e.g. the leave document
                from django.core.files.uploadedfile import SimpleUploadedFile
                from .models import Upload
                files = {}
                for field, uid in s['files'].items():
                    u = Upload.objects.get(pk=uid, user=request.user)
                    with u.file.open('rb') as fh:
                        files[field] = SimpleUploadedFile(u.name, fh.read())
            out = apicall.call(request, s['method'], _fill(s['path'], ids), _fill(s.get('data'), ids), files=files)
        except apicall.ApiError as e:
            ch.result = {'done': done, 'failed_step': s.get('label') or s['path'], 'error': e.detail}
            raise Refused(tr('{0}: {1}', s.get('label') or s['path'], e.detail), e.status if e.status < 500 else 400)
        if isinstance(out, dict) and out.get('id') is not None:
            ids[f'id{i}'] = out['id']
            ids.setdefault('id', out['id'])
        done.append({'label': s.get('label') or s['path'], 'answer': _short(out), 'ids': dict(ids)})


def _short(out):
    if isinstance(out, dict):
        return {k: v for k, v in list(out.items())[:12] if isinstance(v, (str, int, float, bool)) or v is None}
    return {'items': len(out)} if isinstance(out, list) else {}


def reject(request, ch):
    """v1.73.17: a company administrator rejects an AI tool's draft (kept in the log as cancelled)."""
    _check_owner(request, ch)
    if ch.state != 'preview':
        raise Refused(tr('This change was already {0}.', tr(ch.get_state_display()).lower()))
    ch.plan = dict(ch.plan, via=dict(ch.plan['via'], rejected_by=request.user.username, rejected_at=timezone.now().isoformat()))
    ch.state, ch.error = 'cancelled', tr('Rejected by {0}.', request.user.username)
    ch.save(update_fields=['plan', 'state', 'error'])
    return ch


def undo(request, ch):
    from AccessControl.access import ctx
    c = ctx(request)
    if by_api_key(request):   # v1.73.17 undo is done by a person
        raise Refused(tr('An API key cannot undo a change. A company administrator undoes it in Settings › Ask ZEO.'), 403)
    if ch.user_id != request.user.pk and not c.admin:
        raise Refused(tr('Only the user who made the change or an administrator can undo it.'), 403)
    if ch.state != 'applied':
        raise Refused(tr('Only an applied change can be undone.'))
    if not ch.undoable:
        raise Refused(tr('This change cannot be undone from Ask ZEO – it is done in its own screen.'))
    try:
        with transaction.atomic():
            for i, op in reversed(list(enumerate(ch.plan.get('records') or []))):
                Mdl = apps.get_model(op['model'])
                a = ch.after[i]
                if op['op'] == 'update':
                    obj = Mdl.objects.select_for_update().get(pk=a['id'])
                    if _snapshot(obj, op['values']) != a['values']:
                        raise Refused(tr('{0} was changed again after Ask ZEO changed it – undo it on its own screen.', a['label']), 409)
                    for f, v in ch.before[i]['values'].items():
                        setattr(obj, Mdl._meta.get_field(f).attname, v)
                    obj.save()
                else:
                    Mdl.objects.filter(pk=a['id']).delete()
        if ch.plan.get('api'):
            ids = (ch.result or {}).get('ids') or {}
            done = (ch.result or {}).get('done') or []
            for i, s in reversed(list(enumerate(ch.plan['api']))):
                u = s.get('undo')
                if u and 'by_status' in u:   # 1.72.0: the undo depends on what the step's answer said (e.g. revision waiting or applied)
                    st = ((done[i] if i < len(done) else {}).get('answer') or {}).get('status')
                    u = u['by_status'].get(st)
                if not u:
                    continue
                try:
                    apicall.call(request, u['method'], _fill(u['path'], ids), _fill(u.get('data'), ids))
                except apicall.ApiError as e:
                    raise Refused(tr('{0}: {1}', s.get('label') or s['path'], e.detail), e.status if e.status < 500 else 400)
    except ObjectDoesNotExist:
        raise Refused(tr('The record no longer exists.'), 409)
    ch.state, ch.undone_at, ch.undone_by = 'undone', timezone.now(), request.user
    ch.save()
    return ch


def as_json(ch, lang=None):
    return {'id': ch.pk, 'title': ch.title, 'summary': ch.summary, 'kind': ch.kind, 'state': ch.state, 'state_label': tr(ch.get_state_display(), lang=lang),
            'user': getattr(ch.user, 'username', '') if ch.user_id else '', 'created_at': ch.created_at.isoformat(),
            'applied_at': ch.applied_at.isoformat() if ch.applied_at else None, 'undone_at': ch.undone_at.isoformat() if ch.undone_at else None,
            'undone_by': getattr(ch.undone_by, 'username', '') if ch.undone_by_id else '', 'undoable': ch.undoable and ch.state == 'applied',
            'error': ch.error, 'before': ch.before, 'after': ch.after, **_via(ch)}


def _via(ch):
    """v1.73.17: made via Ask ZEO, or proposed by an AI tool with an API key (and who approved / rejected it)."""
    v = (ch.plan or {}).get('via') or {}
    if not v:
        return {'via': 'Ask ZEO'}
    return {'via': f"API key {v.get('key')}", 'api_key': v.get('key', ''), 'scope': v.get('scope', ''), 'request': v.get('request', ''),
            'approved_by': v.get('approved_by', ''), 'rejected_by': v.get('rejected_by', '')}
