"""v1.43.0 – the item master (Odoo product.product): one item per good, service, spare part, consumable, asset model, charge or fee.

The apps keep their own part of an item and an `item` link: Asset Management spare parts (stock per store, fits assets), Property
charge types (refundable, kind), Projects service products (support team), HSE chemicals (SDS, hazards, storage). The fields both
sides know are kept in step:

  app record saved → the changed fields go to the item (a new item is made, or an existing one with the same code / name is used)
  item saved       → its fields are copied to every app record of the item (QuerySet.update – no signals, no loops)

A field may flow one way only ('up': the spare part's average cost comes from its stock moves and is shown on the item).
"""
import logging
import threading

from django.apps import apps as django_apps
from django.db import transaction
from django.db.models.signals import post_save, pre_save

log = logging.getLogger(__name__)
_local = threading.local()

KIND_LABELS = {'goods': 'Goods', 'service': 'Service', 'bundle': 'Bundle', 'spare_part': 'Spare part', 'consumable': 'Consumable',
               'asset_model': 'Asset model', 'charge': 'Charge', 'fee': 'Fee'}


def Item():
    return django_apps.get_model('ZeoMasters', 'Product')


def UOM():
    return django_apps.get_model('ZeoMasters', 'UOM')


def uom_id(text):
    """A unit typed in an app ('pcs', 'L', 'kg') → the unit of measure (made when new)."""
    t = (text or '').strip()
    if not t:
        return None
    U = UOM()
    u = U.objects.filter(code__iexact=t).first() or U.objects.filter(name__iexact=t).first()
    if u is None:
        u = U.objects.create(code=t[:10], name=t[:60])
    return u.pk


def uom_text(pk):
    if not pk:
        return ''
    u = UOM().objects.filter(pk=pk).first()
    return u.code if u else ''


class IExt:
    """One kind of app record that belongs to an item. fields: [(app field, item field, to_item, from_item, direction)] where
    to_item / from_item are callables (or None) and direction is 'both' or 'up' (app → item only)."""

    def __init__(self, app, title, model, reg, label, fields, kind, url=None, fk='item', ref=False, plural=None, from_item=None,
                 match_code=None):
        self.app, self.title, self.model_path, self.reg, self.label = app, title, model, reg, label
        self.fields, self.kind, self.url, self.fk, self.ref = fields, kind, url, fk, ref
        self.plural = plural or label + 's'
        self.from_item = from_item          # fn(item) → {app field: value} – extra values copied back (e.g. consumable flag)
        self.match_code = match_code        # the app field holding a code to match an existing item

    @property
    def model(self):
        return django_apps.get_model(self.model_path)


IEXTS = []


def register(ext):
    IEXTS.append(ext)
    m = ext.model
    pre_save.connect(_stash, sender=m, dispatch_uid=f'itm_pre_{ext.model_path}')
    post_save.connect(lambda sender, instance, created, raw=False, ext=ext, **kw: (None if raw else ext_saved(ext, instance, created)),
                      sender=m, weak=False, dispatch_uid=f'itm_post_{ext.model_path}')
    return ext


def ext_of(model):
    for e in IEXTS:
        if e.model is model:
            return e
    return None


def _att(model, name):
    return model._meta.get_field(name).attname


def _fit(model, field, v):
    f = model._meta.get_field(field)
    if v is None:
        return None if f.null else ('' if f.get_internal_type() in ('CharField', 'TextField') else (f.default if f.default is not None else v))
    ml = getattr(f, 'max_length', None)
    if ml and isinstance(v, str):
        v = v[:ml]
    return v


def _empty(v):
    return v in (None, '', [], {})


def _stash(sender, instance, raw=False, **kw):
    if raw or getattr(_local, 'busy', False) or not instance.pk:
        return
    ext = ext_of(sender)
    if not ext:
        return
    try:
        atts = [_att(sender, f[0]) for f in ext.fields] + [ext.fk + '_id']
        instance._itm_old = sender.objects.filter(pk=instance.pk).values(*atts).first()
    except Exception:
        instance._itm_old = None


# ------------------------------------------------------------------ app record → item
def ext_saved(ext, o, created):
    if getattr(_local, 'busy', False):
        return
    try:
        with transaction.atomic():
            _ext_saved(ext, o, created)
    except Exception:
        log.exception('item sync failed for %s %s', ext.model_path, o.pk)


def _to_item(o, f):
    ef, itf, to_item = f[0], f[1], f[2]
    v = getattr(o, _att(o.__class__, ef))
    return to_item(v) if to_item else v


def _ext_saved(ext, o, created):
    I = Item()
    iid = getattr(o, ext.fk + '_id')
    it = I.objects.filter(pk=iid).first() if iid else None
    old = getattr(o, '_itm_old', None) or {}
    if ext.ref:
        prev = old.get(ext.fk + '_id')
        for x in {it, I.objects.filter(pk=prev).first() if prev and prev != iid else None}:
            if x is not None:
                refresh_used_in(x)
        return
    changed = False
    if it is None:
        it = match(ext, o)
        if it is None:
            from .app import app
            it = I(number=app.next_number('item', 'ITM'), kind=ext.kind(o))
            for f in ext.fields:
                v = _to_item(o, f)
                if not _empty(v):
                    setattr(it, _att(I, f[1]), v)
            it.name = (it.name or str(o))[:150]
        else:   # an existing item: it keeps its values; empty ones are filled
            for f in ext.fields:
                v = _to_item(o, f)
                if not _empty(v) and _empty(getattr(it, _att(I, f[1]))):
                    setattr(it, _att(I, f[1]), v)
        changed = True
    else:
        for f in ext.fields:
            att = _att(o.__class__, f[0])
            if att in old and old[att] == getattr(o, att):
                continue
            if att not in old and not created:
                continue
            setattr(it, _att(I, f[1]), _fit(I, f[1], _to_item(o, f)))
            changed = True
        k = ext.kind(o)
        if k and it.kind != k and it.kind in ('spare_part', 'consumable') and k in ('spare_part', 'consumable'):
            it.kind, changed = k, True
    if changed:
        save_item(it)
    if getattr(o, ext.fk + '_id') != it.pk:
        setattr(o, ext.fk + '_id', it.pk)
        ext.model.objects.filter(pk=o.pk).update(**{ext.fk: it})
        push(it)
    refresh_used_in(it)


def match(ext, o):
    """An existing item for a new app record: same code, else same name and a kind of the same family."""
    I = Item()
    k = ext.kind(o)
    family = {'spare_part': ('spare_part', 'consumable', 'goods'), 'consumable': ('consumable', 'spare_part', 'goods'),
              'charge': ('charge', 'fee'), 'service': ('service', 'fee'), 'goods': ('goods', 'spare_part', 'consumable')}.get(k, (k,))
    # one item per record of an app (two charge types never share an item); other apps may share it
    taken = ext.model.objects.exclude(pk=o.pk).exclude(**{ext.fk + '__isnull': True}).values(ext.fk)
    if ext.match_code:
        code = (getattr(o, ext.match_code) or '').strip()
        if code:
            x = I.objects.filter(code__iexact=code, kind__in=family).exclude(pk__in=taken).order_by('pk').first()
            if x:
                return x
    name = (getattr(o, 'name', '') or '').strip()
    if name:
        return I.objects.filter(name__iexact=name, kind__in=family).exclude(pk__in=taken).order_by('pk').first()
    return None


# ------------------------------------------------------------------ item → app records
def save_item(it):
    _local.busy = True
    try:
        if not it.number:
            from .app import app
            it.number = app.next_number('item', 'ITM')
        it.save()
        push(it)
        refresh_used_in(it)
    finally:
        _local.busy = False
    return it


def push(it):
    I = Item()
    for ext in IEXTS:
        if ext.ref:
            continue
        m = ext.model
        qs = m.objects.filter(**{ext.fk + '_id': it.pk})
        if not qs.exists():
            continue
        vals = {}
        ids = list(qs.values_list('pk', flat=True))
        for f in ext.fields:
            if len(f) > 4 and f[4] == 'up':
                continue
            v = getattr(it, _att(I, f[1]))
            if f[3]:
                v = f[3](v)
            v = _fit(m, f[0], v)
            mf = m._meta.get_field(f[0])
            if mf.unique and (len(ids) > 1 or _empty(v) or m.objects.filter(**{f[0]: v}).exclude(pk__in=ids).exists()):
                continue   # a unique code is never copied where it would clash (or be emptied)
            vals[mf.attname] = v
        if ext.from_item:
            vals.update(ext.from_item(it) or {})
        if vals:
            qs.update(**vals)


def refresh_used_in(it):
    I = Item()
    rows, titles = [], []
    for ext in IEXTS:
        qs = ext.model.objects.filter(**{ext.fk + '_id': it.pk})
        if ext.ref:
            n = qs.count()
            if n:
                rows.append({'app': ext.title, 'record': ext.plural, 'number': str(n), 'name': f'{n} record(s)', 'slug': '', 'kind': '', 'id': None})
        else:
            for o in qs[:50]:
                row = {'app': ext.title, 'record': ext.label, 'number': getattr(o, 'code', '') or getattr(o, 'number', '') or str(o.pk),
                       'name': str(o)[:120], 'slug': ext.app, 'kind': ext.reg, 'id': o.pk}
                if ext.url:
                    row['url'] = ext.url(o)
                rows.append(row)
        if qs.exists() and ext.title not in titles:
            titles.append(ext.title)
    text = ', '.join(titles)[:200]
    if rows != (it.used_in or []) or text != it.apps_text:
        I.objects.filter(pk=it.pk).update(used_in=rows, apps_text=text)
        it.used_in, it.apps_text = rows, text


def item_saved(sender, instance, created, raw=False, **kw):
    """An item saved from the Items / Products screens (not by the sync)."""
    if raw or getattr(_local, 'busy', False):
        return
    try:
        _local.busy = True
        push(instance)
        refresh_used_in(instance)
    except Exception:
        log.exception('item push failed for %s', instance.pk)
    finally:
        _local.busy = False


def link_all(stdout=None):
    n = 0
    for ext in IEXTS:
        if ext.ref:
            continue
        for o in ext.model.objects.filter(**{ext.fk + '__isnull': True}).order_by('pk'):
            o._itm_old = None
            ext_saved(ext, o, True)
            n += 1
    if stdout:
        stdout.write(f'linked {n} app records to items')
    return n


def merge(keep, drop):
    """Move every record of `drop` to `keep` (all apps, price books, quotation lines) and deactivate `drop`."""
    I = Item()
    with transaction.atomic():
        for rel in I._meta.related_objects:
            if rel.one_to_many or rel.one_to_one:
                try:
                    rel.field.model.objects.filter(**{rel.field.name: drop}).update(**{rel.field.name: keep})
                except Exception:
                    log.exception('item merge: could not move %s.%s', rel.field.model.__name__, rel.field.name)
        for ext in IEXTS:
            ext.model.objects.filter(**{ext.fk: drop}).update(**{ext.fk: keep})
        for f in I._meta.concrete_fields:
            if f.name in ('id', 'number', 'created_at', 'updated_at', 'used_in', 'apps_text', 'is_active', 'description'):
                continue
            if _empty(getattr(keep, f.attname)) and not _empty(getattr(drop, f.attname)):
                setattr(keep, f.attname, getattr(drop, f.attname))
        drop.is_active = False
        drop.description = ((drop.description or '') + f'\nMerged into {keep.number} {keep.name}.').strip()
        _local.busy = True
        try:
            drop.save()
        finally:
            _local.busy = False
        save_item(keep)
        refresh_used_in(drop)
    return keep
