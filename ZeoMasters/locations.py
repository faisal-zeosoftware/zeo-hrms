"""v1.67.4 (masters) – one list of stores and locations (sites, buildings, rooms, stores / warehouses) shared by the apps.

Asset Management locations and stores are its first users, Maintenance locations the second; the Inventory app (1.77) keeps its
stock per store on the same records. Each app keeps its own record (floor plan, custodian, Property unit, stock of spare parts…)
with a `shared_location` link, and the fields both know are kept in step – the same way as the business partners and items:

  app record saved      → name, kind, parent, branch, address, active go to the shared location (made when there is none; an Asset
                          Management location and the Maintenance location it points to – `mnt_location_id` – share one)
  shared location saved → name, kind and address are copied to every app record of it (QuerySet.update – no loops); branch and
                          active stay the app's own (they decide who sees the record), so a change in Masters never hides an app record

The app codes stay in the apps (both number their locations LOC-…); the shared record has its own number (SL-00001).
Existing records are linked by `python manage.py link_locations` (dry run first).
"""
import logging
import threading

from django.apps import apps as django_apps
from django.db import transaction
from django.db.models.signals import post_save, pre_save

log = logging.getLogger(__name__)
_local = threading.local()


class LExt:
    """One kind of app record that is a place. fields: [(app field, shared field, 'both' | 'up')] – 'up' = app → shared only."""

    def __init__(self, app, title, model, reg, label, fields, kind=None, sibling=None, url=None, fk='shared_location'):
        self.app, self.title, self.model_path, self.reg, self.label = app, title, model, reg, label
        self.fields, self.kind, self.sibling, self.url, self.fk = fields, kind, sibling, url, fk

    @property
    def model(self):
        return django_apps.get_model(self.model_path)


LEXTS = []


def SL():
    return django_apps.get_model('ZeoMasters', 'Location')


def register(ext):
    LEXTS.append(ext)
    m = ext.model
    pre_save.connect(_stash, sender=m, dispatch_uid=f'loc_pre_{ext.model_path}')
    post_save.connect(lambda sender, instance, created, raw=False, ext=ext, **kw: (None if raw else ext_saved(ext, instance, created)),
                      sender=m, weak=False, dispatch_uid=f'loc_post_{ext.model_path}')
    return ext


def ext_of(model):
    for e in LEXTS:
        if e.model is model:
            return e
    return None


def _att(model, name):
    return model._meta.get_field(name).attname


def _stash(sender, instance, raw=False, **kw):
    if raw or getattr(_local, 'busy', False) or not instance.pk:
        return
    ext = ext_of(sender)
    if ext:
        instance._loc_old = sender.objects.filter(pk=instance.pk).values(ext.fk + '_id', 'parent_id').first()


def next_number():
    from .app import app
    return app.next_number('location', 'SL')


# ------------------------------------------------------------------ app record → shared location
def ext_saved(ext, o, created):
    if getattr(_local, 'busy', False):
        return
    try:
        with transaction.atomic():
            link(ext, o)
    except Exception:
        log.exception('shared location sync failed for %s %s', ext.model_path, o.pk)


def link(ext, o, depth=0):
    """Give the record its shared location (or update it) and return it."""
    S = SL()
    sid = getattr(o, ext.fk + '_id')
    s = S.objects.filter(pk=sid).first() if sid else None
    new_link = s is None
    if s is None and ext.sibling:
        s = ext.sibling(o)
    fresh = s is None
    if fresh:
        s = S(code=(getattr(o, 'code', '') or '')[:30], kind=(ext.kind(o) if ext.kind else 'room'))
    joining = new_link and not fresh   # the same place in another app (same name): it keeps its values, kind / address come from here
    for af, sf, way in ext.fields:
        v = getattr(o, _att(ext.model, af))
        if joining:
            if sf in ('kind', 'address') and v not in (None, ''):
                setattr(s, _att(S, sf), v)
            elif sf == 'branch' and s.branch_id is None and v:
                setattr(s, _att(S, sf), v)
            continue
        setattr(s, _att(S, sf), v)
    # the parent: the shared location of the record's parent (linked first when needed)
    par = o.parent if getattr(o, 'parent_id', None) else None
    if par is not None and depth < 12:
        ps = S.objects.filter(pk=getattr(par, ext.fk + '_id')).first() if getattr(par, ext.fk + '_id') else None
        if ps is None:
            ps = link(ext, par, depth + 1)
        if ps is not None and ps.pk != s.pk:
            s.parent = ps
    elif par is None and not joining:   # a top-level record; a sibling's location keeps its parent
        s.parent = None
    save_shared(s)
    if getattr(o, ext.fk + '_id') != s.pk:
        ext.model.objects.filter(pk=o.pk).update(**{ext.fk: s})
        setattr(o, ext.fk + '_id', s.pk)
    refresh_used_in(s)
    return s


def save_shared(s):
    _local.busy = True
    try:
        if not s.number:
            s.number = next_number()
        s.save()
        push(s)
    finally:
        _local.busy = False
    return s


def push(s):
    for ext in LEXTS:
        qs = ext.model.objects.filter(**{ext.fk + '_id': s.pk})
        if not qs.exists():
            continue
        vals = {}
        for af, sf, way in ext.fields:
            if way != 'both':
                continue
            vals[_att(ext.model, af)] = getattr(s, _att(SL(), sf))
        if vals:
            qs.update(**vals)


def refresh_used_in(s):
    rows, titles = [], []
    for ext in LEXTS:
        qs = ext.model.objects.filter(**{ext.fk + '_id': s.pk})
        for o in qs[:50]:
            row = {'app': ext.title, 'record': ext.label, 'number': getattr(o, 'code', '') or str(o.pk), 'name': str(getattr(o, 'name', o))[:120],
                   'slug': ext.app, 'kind': ext.reg, 'id': o.pk}
            if ext.url:
                row['url'] = ext.url(o)
            rows.append(row)
        if qs.exists() and ext.title not in titles:
            titles.append(ext.title)
    text = ', '.join(titles)[:200]
    if rows != (s.used_in or []) or text != s.apps_text:
        SL().objects.filter(pk=s.pk).update(used_in=rows, apps_text=text)
        s.used_in, s.apps_text = rows, text


def location_saved(sender, instance, created, raw=False, **kw):
    """A shared location saved from the Masters screen (not by the sync): its values go to the app records."""
    if raw or getattr(_local, 'busy', False):
        return
    try:
        _local.busy = True
        push(instance)
    except Exception:
        log.exception('shared location push failed for %s', instance.pk)
    finally:
        _local.busy = False
    refresh_used_in(instance)


# ------------------------------------------------------------------ the apps
def _asset_sibling(o):
    if not o.mnt_location_id:
        return None
    m = django_apps.get_model('ZeoMaintenance', 'Location').objects.filter(pk=o.mnt_location_id).only('name', 'shared_location').first()
    if m and m.shared_location_id and _same(m.name, o.name):   # joined only when it is the same name – nothing is joined on a guess
        return m.shared_location
    return None


def _same(a, b):
    return ' '.join(str(a or '').split()).lower() == ' '.join(str(b or '').split()).lower()


def _mnt_sibling(o):
    if not o.pk:
        return None
    for a in django_apps.get_model('ZeoAssets', 'Location').objects.filter(mnt_location_id=o.pk, shared_location__isnull=False).only('name', 'shared_location'):
        if _same(a.name, o.name):
            return a.shared_location
    return None


AST_LOCATION = register(LExt(
    'zeo-assets', 'Asset Management', 'ZeoAssets.Location', 'location', 'Location / store',
    fields=[('name', 'name', 'both'), ('kind', 'kind', 'both'), ('address', 'address', 'both'), ('branch', 'branch', 'up'),
            ('is_active', 'is_active', 'up')],
    kind=lambda o: o.kind, sibling=_asset_sibling))

MNT_LOCATION = register(LExt(
    'zeo-maintenance', 'Maintenance', 'ZeoMaintenance.Location', 'location', 'Location',
    fields=[('name', 'name', 'both'), ('branch', 'branch', 'up'), ('is_active', 'is_active', 'up')],
    kind=lambda o: 'room', sibling=_mnt_sibling))


# ------------------------------------------------------------------ forms of the apps
def attach(app):
    """The app's location form shows its shared store / location (read-only – it is kept in step automatically)."""
    for ext in LEXTS:
        if ext.app != app.slug or ext.reg not in app.regs:
            continue
        reg = app.regs[ext.reg]
        if ext.fk not in (reg.info or ()):
            reg.info = tuple(reg.info or ()) + (ext.fk,)
