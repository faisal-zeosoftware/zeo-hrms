"""v1.67.26 (pending item 1.62.12, item 237) – custom modules and Blueprint (no programming).

Settings › Automation › Custom modules (company administrators) designs a register:
  * its fields – text, long text, number, amount, date, choice (A|B|C), yes / no, employee, e-mail, phone; required or not; shown in
    the list or not;
  * its numbering (prefix + running number) and who may add records (every employee / HR / administrators) and see them (own records –
    a manager also sees the team's –, everyone, HR only);
  * its Blueprint: the states a record goes through (Draft › Submitted › Approved …), where a new record starts, and the moves between
    states. Each move says from which states it can be made, who may make it (the person who made the record, the line manager, HR,
    an administrator, or the members of a permission group), the fields that must be filled before it, and whether a note is needed.
A record can only change state through a move of its Blueprint; every move is kept in the record's history (who, when, note).
The records live in one table (CustomRecord.data, JSON) – no table per module, nothing to migrate when a module changes.

API  (company administrators)  GET/POST flow/api/custom-modules/   GET/PUT/DELETE flow/api/custom-modules/<key>/
     (users)                   GET flow/api/custom/                (the modules this user may use)
                               GET/POST flow/api/custom/<key>/records/   GET/PATCH/DELETE flow/api/custom/<key>/records/<id>/
                               POST flow/api/custom/<key>/records/<id>/move/ {transition, note}
"""
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from .models import CustomModule, CustomRecord

TYPES = ('text', 'textarea', 'number', 'money', 'date', 'choice', 'yesno', 'employee', 'email', 'phone')
# v1.74.0 ZEO Developer (D1): calculated fields, rate-table lookups, child tables, links, QR / barcode – checked and calculated by
# ZeoDeveloper.fields when the app is installed (a module without them works exactly as before)
DEV_TYPES = ('formula', 'lookup', 'table', 'link', 'qr', 'barcode')


def _dev():
    from django.apps import apps
    if not apps.is_installed('ZeoDeveloper'):
        return None
    from ZeoDeveloper import fields as dev_fields
    return dev_fields
WHO = ('owner', 'manager', 'hr', 'admin')
KEY_RE = re.compile(r'^[a-z][a-z0-9_]{0,39}$')


class CustomError(Exception):
    def __init__(self, msg, field=None, status=400):
        super().__init__(msg)
        self.field, self.status = field, status


def _tr(s, *a):
    try:
        from Translations.i18n import tr
        return tr(s, *a)
    except Exception:   # noqa: BLE001
        return s.format(*a)


# ------------------------------------------------------------------ rights
def ctx(request):
    from AccessControl.access import ctx as _c
    return _c(request)


def is_admin(request):
    return bool(ctx(request).admin)


def is_hr(request):
    c = ctx(request)
    return bool(c.admin or {'change_emp_master', 'add_emp_master'} & set(c.codes))


def my_emp(request):
    from EmpManagement.models import emp_master
    return emp_master.objects.filter(users=request.user).first()


def in_group(request, name):
    return request.user.groups.filter(name__iexact=name).exists() if name else False


def can_create(m, request):
    return m.active and (m.who_create == 'all' or (m.who_create == 'hr' and is_hr(request)) or is_admin(request))


def scope(m, request):
    qs = CustomRecord.objects.filter(module=m)
    if is_admin(request) or m.who_view == 'all' or (m.who_view in ('own', 'hr') and is_hr(request)):
        return qs
    if m.who_view == 'hr':
        return qs.none()
    from django.db.models import Q
    from EmpManagement.models import emp_master
    team = list(emp_master.objects.filter(emp_reporting_manager=request.user).values_list('pk', flat=True))
    return qs.filter(Q(owner_id=request.user.pk) | Q(employee_id__in=team))


# ------------------------------------------------------------------ the design
def clean_fields(fields):
    if not isinstance(fields, list) or not fields:
        raise CustomError(_tr('Add at least one field.'), 'fields')
    out, seen = [], set()
    for i, f in enumerate(fields, 1):
        if not isinstance(f, dict):
            raise CustomError(_tr('Field {0} is not valid.', i), 'fields')
        label = str(f.get('label') or '').strip()
        key = str(f.get('key') or '').strip().lower() or re.sub(r'[^a-z0-9]+', '_', label.lower()).strip('_')[:40]
        if not label:
            raise CustomError(_tr('Field {0}: give it a label.', i), 'fields')
        if not KEY_RE.match(key):
            raise CustomError(_tr('Field {0}: the key starts with a letter and holds only small letters, digits and _.', i), 'fields')
        if key in seen:
            raise CustomError(_tr('Field {0}: the key "{1}" is used twice.', i, key), 'fields')
        t = f.get('type') or 'text'
        if t not in TYPES and not (t in DEV_TYPES and _dev() is not None):
            raise CustomError(_tr('Field {0}: choose a type from the list.', i), 'fields')
        opts = str(f.get('options') or '').strip()
        if t == 'choice' and len([o for o in opts.split('|') if o.strip()]) < 2:
            raise CustomError(_tr('Field {0}: a choice needs at least two options (A|B).', i), 'fields')
        seen.add(key)
        out.append({'key': key, 'label': label[:80], 'type': t, 'options': opts[:1000 if t == 'formula' else 500], 'required': bool(f.get('required')),
                    'in_list': bool(f.get('in_list', True)), 'help': str(f.get('help') or '')[:200]})
        dv = _dev()
        if dv is not None and (t in DEV_TYPES or f.get('show_if') or f.get('required_if')):   # v1.74.0
            try:
                out[-1].update(dv.clean_extra(out[-1], i, f, None))
            except dv.FieldError as e:
                raise CustomError(str(e), 'fields')
    dv = _dev()
    if dv is not None and any(x['type'] in DEV_TYPES or x.get('show_if') or x.get('required_if') for x in out):   # v1.74.0
        probs = dv.check_module(out)
        if probs:
            raise CustomError(' '.join(probs[:3]), 'fields')
    return out


def clean_blueprint(bp, field_keys):
    if not bp:
        bp = {}
    states = bp.get('states') or [{'key': 'open', 'label': 'Open'}, {'key': 'closed', 'label': 'Closed'}]
    st, seen = [], set()
    for i, s in enumerate(states, 1):
        k = str(s.get('key') or '').strip().lower() or re.sub(r'[^a-z0-9]+', '_', str(s.get('label') or '').lower()).strip('_')
        if not KEY_RE.match(k or '-'):
            raise CustomError(_tr('State {0}: the key starts with a letter and holds only small letters, digits and _.', i), 'blueprint')
        if k in seen:
            raise CustomError(_tr('State {0}: "{1}" is used twice.', i, k), 'blueprint')
        if not str(s.get('label') or '').strip():
            raise CustomError(_tr('State {0}: give it a name.', i), 'blueprint')
        seen.add(k)
        st.append({'key': k, 'label': str(s['label']).strip()[:40], 'color': str(s.get('color') or '')[:12], 'final': bool(s.get('final'))})
    start = bp.get('start') or st[0]['key']
    if start not in seen:
        raise CustomError(_tr('The start state must be one of the states.'), 'blueprint')
    tr_, tkeys = [], set()
    for i, t in enumerate(bp.get('transitions') or [], 1):
        label = str(t.get('label') or '').strip()
        k = str(t.get('key') or '').strip().lower() or re.sub(r'[^a-z0-9]+', '_', label.lower()).strip('_')
        if not label or not KEY_RE.match(k or '-'):
            raise CustomError(_tr('Move {0}: give it a name (e.g. Approve).', i), 'blueprint')
        if k in tkeys:
            raise CustomError(_tr('Move {0}: "{1}" is used twice.', i, k), 'blueprint')
        frm = [x for x in (t.get('from') or []) if x]
        if not frm or any(x not in seen for x in frm):
            raise CustomError(_tr('Move {0} ({1}): choose the states it starts from.', i, label), 'blueprint')
        if t.get('to') not in seen:
            raise CustomError(_tr('Move {0} ({1}): choose the state it ends in.', i, label), 'blueprint')
        who = str(t.get('who') or 'hr')
        if who not in WHO and not who.startswith('group:'):
            raise CustomError(_tr('Move {0} ({1}): choose who may make it.', i, label), 'blueprint')
        req = [x for x in (t.get('require') or []) if x]
        if any(x not in field_keys for x in req):
            raise CustomError(_tr('Move {0} ({1}): a required field is not a field of the module.', i, label), 'blueprint')
        tkeys.add(k)
        tr_.append({'key': k, 'label': label[:40], 'from': frm, 'to': t['to'], 'who': who, 'require': req, 'note': bool(t.get('note'))})
    reach = {start}
    changed = True
    while changed:   # every state can be reached from the start (else nobody can ever get a record there)
        changed = False
        for t in tr_:
            if set(t['from']) & reach and t['to'] not in reach:
                reach.add(t['to'])
                changed = True
    lost = [s['label'] for s in st if s['key'] not in reach]
    if lost:
        raise CustomError(_tr('No move leads to: {0}.', ', '.join(lost)), 'blueprint')
    return {'states': st, 'start': start, 'transitions': tr_}


def save_module(data, user, m=None):
    name, plural = str(data.get('name') or '').strip(), str(data.get('plural') or '').strip()
    if not name:
        raise CustomError(_tr('Give the module a name.'), 'name')
    key = str(data.get('key') or (m.key if m else '') or re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_'))[:40]
    if not KEY_RE.match(key):
        raise CustomError(_tr('Key: small letters, digits and _, starting with a letter.'), 'key')
    if CustomModule.objects.filter(key=key).exclude(pk=getattr(m, 'pk', None)).exists():
        raise CustomError(_tr('Another module has the key "{0}".', key), 'key')
    fields = clean_fields(data.get('fields') if 'fields' in data else (m.fields if m else None))
    bp = clean_blueprint(data.get('blueprint') if 'blueprint' in data else (m.blueprint if m else None), {f['key'] for f in fields})
    prefix = str(data.get('prefix') or (m.prefix if m else '') or 'REC').strip().upper()
    if not re.fullmatch(r'[A-Z][A-Z0-9-]{0,9}', prefix):
        raise CustomError(_tr('Number prefix: capital letters and digits, up to 10.'), 'prefix')
    wc, wv = data.get('who_create', getattr(m, 'who_create', 'all')), data.get('who_view', getattr(m, 'who_view', 'own'))
    if wc not in ('all', 'hr', 'admin') or wv not in ('own', 'all', 'hr'):
        raise CustomError(_tr('Choose who may add and who may see records.'), 'who_create')
    if m is not None and m.records.exists():   # a state in use cannot disappear
        used = set(m.records.values_list('state', flat=True))
        gone = used - {s['key'] for s in bp['states']}
        if gone:
            raise CustomError(_tr('Records are in the state(s) {0} – keep them or move the records first.', ', '.join(sorted(gone))), 'blueprint')
    m = m or CustomModule(created_by_id=user.pk)
    m.key, m.name, m.plural, m.prefix = key, name[:80], (plural or name + 's')[:80], prefix
    m.icon = str(data.get('icon') or m.icon or 'widgets')[:40]
    m.description = str(data.get('description') or '')[:300]
    m.fields, m.blueprint, m.who_create, m.who_view = fields, bp, wc, wv
    if 'active' in data:
        m.active = str(data['active']).lower() in ('1', 'true', 'yes')
    m.save()
    return m


def module_json(m, request=None, full=True):
    out = {'key': m.key, 'name': m.name, 'plural': m.plural, 'icon': m.icon, 'description': m.description, 'prefix': m.prefix, 'active': m.active,
           'who_create': m.who_create, 'who_view': m.who_view, 'records': m.records.count()}
    if full:
        out.update({'fields': m.fields, 'blueprint': m.blueprint})
    if request is not None:
        out['can_create'] = can_create(m, request)
    return out


# ------------------------------------------------------------------ records
def _value(f, v):
    t, label = f['type'], f['label']
    if t in DEV_TYPES:   # v1.74.0 ZEO Developer field types
        dv = _dev()
        try:
            return dv.value(f, v) if dv is not None else None
        except dv.FieldError as e:
            raise CustomError(str(e), e.field or f['key'])
    if v in (None, '') or (t == 'yesno' and v is None):
        return None
    s = str(v).strip()
    if t in ('number', 'money'):
        try:
            n = Decimal(s.replace(',', ''))
        except (InvalidOperation, ValueError):
            raise CustomError(_tr('{0}: enter a number.', label), f['key'])
        # v1.74.0 fix: str(Decimal('60').normalize()) gave "6E+1" – numbers are stored as plain digits
        return str(n.quantize(Decimal('0.01'))) if t == 'money' else (format(n.normalize(), 'f') if n == n.to_integral() else str(n))
    if t == 'date':
        try:
            return date.fromisoformat(s[:10]).isoformat()
        except ValueError:
            raise CustomError(_tr('{0}: enter a date.', label), f['key'])
    if t == 'choice':
        opts = [o.strip() for o in f['options'].split('|') if o.strip()]
        if s not in opts:
            raise CustomError(_tr('{0}: choose one of the list.', label), f['key'])
        return s
    if t == 'yesno':
        return str(v).lower() in ('1', 'true', 'yes', 'on')
    if t == 'employee':
        from EmpManagement.models import emp_master
        try:
            e = emp_master.objects.filter(pk=int(s)).first()
        except ValueError:
            e = None
        if e is None:
            raise CustomError(_tr('{0}: choose an employee.', label), f['key'])
        return e.pk
    if t == 'email' and not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', s):
        raise CustomError(_tr('{0}: enter a valid e-mail address.', label), f['key'])
    if t == 'phone' and not re.fullmatch(r'\+?[0-9 ()-]{6,20}', s):
        raise CustomError(_tr('{0}: enter a phone number.', label), f['key'])
    return s[:4000 if t == 'textarea' else 300]


def clean_data(m, data, old=None):
    out = dict(old or {})
    for f in m.fields:
        if f['key'] in (data or {}):
            out[f['key']] = _value(f, data[f['key']])
    unknown = [k for k in (data or {}) if k not in {f['key'] for f in m.fields}]
    if unknown:
        raise CustomError(_tr('Unknown field: {0}.', ', '.join(unknown)), unknown[0])
    for f in m.fields:
        if f['type'] in ('formula', 'lookup', 'qr', 'barcode'):   # v1.74.0 calculated – filled by compute() below
            out[f['key']] = (old or {}).get(f['key'])
            continue
        if f['required'] and out.get(f['key']) in (None, '', []):
            raise CustomError(_tr('{0} is required.', f['label']), f['key'])
    return out


def compute(m, r):
    """v1.74.0 ZEO Developer: calculated fields, lookups, QR codes and conditions of the record (nothing to do without them)."""
    dv = _dev()
    if dv is None:
        return r
    try:
        dv.compute(m, r.data, r.number, r.state)
    except dv.FieldError as e:
        raise CustomError(str(e), e.field)
    return r


def _hook(event, r, request, **kw):
    """v1.74.0 ZEO Developer: scripts that run on create / update / move (their failures never undo the user's save)."""
    try:
        from django.apps import apps
        if apps.is_installed('ZeoDeveloper'):
            from ZeoDeveloper.hooks import record_event
            record_event(event, r, request, **kw)
    except Exception:   # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception('ZEO Developer hook %s', event)


def create(m, data, request):
    if not can_create(m, request):
        raise CustomError(_tr('You may not add {0}.', m.plural.lower()), status=403)
    e = my_emp(request)
    with transaction.atomic():
        m = CustomModule.objects.select_for_update().get(pk=m.pk)
        m.seq += 1
        m.save(update_fields=['seq'])
        r = CustomRecord(module=m, number=f'{m.prefix}-{m.seq:05d}', data=clean_data(m, data), state=m.blueprint.get('start') or '',
                         owner_id=request.user.pk, employee_id=getattr(e, 'pk', None), branch_id=getattr(e, 'emp_branch_id_id', None))
        compute(m, r)   # v1.74.0
        r.history = [{'at': timezone.now().isoformat(), 'by': request.user.pk, 'by_name': _uname(request.user), 'action': 'created', 'to': r.state}]
        r.save()
    _hook('create', r, request)   # v1.74.0
    return r


def _uname(u):
    return (u.get_full_name() or u.username) if u else ''


def can_edit(r, request):
    st = next((s for s in r.module.blueprint.get('states', []) if s['key'] == r.state), {})
    if st.get('final'):
        return is_admin(request)
    return is_admin(request) or is_hr(request) or (r.owner_id == request.user.pk and r.state == r.module.blueprint.get('start'))


def update(r, data, request):
    if not can_edit(r, request):
        raise CustomError(_tr('This record can no longer be changed by you.'), status=403)
    old = dict(r.data)
    r.data = clean_data(r.module, data, old)
    compute(r.module, r)   # v1.74.0
    changed = [f['label'] for f in r.module.fields if old.get(f['key']) != r.data.get(f['key'])]
    if changed:
        r.history = (r.history or []) + [{'at': timezone.now().isoformat(), 'by': request.user.pk, 'by_name': _uname(request.user), 'action': 'changed',
                                           'note': ', '.join(changed)}]
    r.save()
    if changed:
        _hook('update', r, request, old=old, changed=[f['key'] for f in r.module.fields if old.get(f['key']) != r.data.get(f['key'])])   # v1.74.0
    return r


def may_move(r, t, request):
    who = t['who']
    if is_admin(request):
        return True
    if who == 'owner':
        return r.owner_id == request.user.pk
    if who == 'manager':
        from EmpManagement.models import emp_master
        return bool(r.employee_id and emp_master.objects.filter(pk=r.employee_id, emp_reporting_manager=request.user).exists())
    if who == 'hr':
        return is_hr(request)
    if who.startswith('group:'):
        return in_group(request, who[6:])
    return False


def moves(r, request):
    return [t for t in r.module.blueprint.get('transitions', []) if r.state in t['from'] and may_move(r, t, request)]


def move(r, key, note, request):
    t = next((x for x in r.module.blueprint.get('transitions', []) if x['key'] == key), None)
    if t is None:
        raise CustomError(_tr('Unknown move.'), 'transition')
    if r.state not in t['from']:
        lbl = {s['key']: s['label'] for s in r.module.blueprint.get('states', [])}
        raise CustomError(_tr('"{0}" is not possible from {1}.', t['label'], lbl.get(r.state, r.state)), 'transition', 409)
    if not may_move(r, t, request):
        raise CustomError(_tr('You may not "{0}" this record.', t['label']), 'transition', 403)
    miss = [f['label'] for f in r.module.fields if f['key'] in t['require'] and r.data.get(f['key']) in (None, '')]
    if miss:
        raise CustomError(_tr('Fill in first: {0}.', ', '.join(miss)), t['require'][0])
    if t['note'] and not (note or '').strip():
        raise CustomError(_tr('Write a note for "{0}".', t['label']), 'note')
    old = r.state
    r.state = t['to']
    r.history = (r.history or []) + [{'at': timezone.now().isoformat(), 'by': request.user.pk, 'by_name': _uname(request.user), 'action': t['label'],
                                       'from': old, 'to': r.state, 'note': (note or '').strip()[:500]}]
    r.save(update_fields=['state', 'history', 'updated_at'])
    _hook('move', r, request, transition=t['key'], old_state=old)   # v1.74.0
    try:   # tell the person who made it
        if r.owner_id and r.owner_id != request.user.pk:
            from UserManagement.models import CustomUser
            from zeo.module_helpers import notify
            lbl = {s['key']: s['label'] for s in r.module.blueprint.get('states', [])}
            notify(user=CustomUser.objects.filter(pk=r.owner_id).first(), title=f'{r.module.name} {r.number}',
                   message=_tr('{0}: {1} → {2}', t['label'], lbl.get(old, old), lbl.get(r.state, r.state)), notification_type='general')
    except Exception:   # noqa: BLE001
        pass
    return r


def record_json(r, request, full=True):
    m = r.module
    lbl = {s['key']: s for s in m.blueprint.get('states', [])}
    out = {'id': r.pk, 'number': r.number, 'state': r.state, 'state_label': lbl.get(r.state, {}).get('label', r.state),
           'state_color': lbl.get(r.state, {}).get('color', ''), 'data': r.data, 'created_at': r.created_at, 'updated_at': r.updated_at}
    emp_ids = {r.employee_id} | {r.data.get(f['key']) for f in m.fields if f['type'] == 'employee'}
    from EmpManagement.models import emp_master
    names = {e.pk: ' '.join(x for x in (e.emp_first_name, e.emp_last_name) if x) + f' ({e.emp_code})'
             for e in emp_master.objects.filter(pk__in=[x for x in emp_ids if x])}
    out['owner'] = names.get(r.employee_id, '')
    out['display'] = {f['key']: names.get(r.data.get(f['key']), '') for f in m.fields if f['type'] == 'employee'}
    dv = _dev()
    if dv is not None and any(f['type'] == 'link' for f in m.fields):   # v1.74.0 labels of linked records
        out['display'].update(dv.displays(m, r.data))
    if full and dv is not None:   # v1.74.0 ZEO Developer: script buttons and print layouts of the module
        try:
            from ZeoDeveloper.hooks import record_extras
            out.update(record_extras(r, request))
        except Exception:   # noqa: BLE001
            pass
    if full:
        out.update({'history': r.history, 'moves': [{'key': t['key'], 'label': t['label'], 'note': t['note'], 'to': t['to']} for t in moves(r, request)],
                    'can_edit': can_edit(r, request), 'can_delete': is_admin(request) or (r.owner_id == request.user.pk and r.state == m.blueprint.get('start'))})
    return out
