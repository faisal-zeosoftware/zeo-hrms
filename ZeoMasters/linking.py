"""v1.67.4 (masters) – one-time linking of the records that exist to the shared masters, dry run first.

    python manage.py link_partners  [--schema gdis] [--apply] [--no-create]
    python manage.py link_items     [--schema gdis] [--apply] [--no-create]
    python manage.py link_locations [--schema gdis] [--apply]

Without --apply nothing is kept: the work is done inside a transaction that is rolled back, so the figures are exactly what --apply
will do. Safe to run again (only records without a link are handled).

Partners – each app record / typed name without a partner: same tax no. (TRN) or ID number → same e-mail or phone → same name
(exact, case and spaces ignored) → a new partner (company, tagged "from link_partners"; --no-create leaves it unlinked).
Items – same code → same name (exact) → a new item; cost lines of work orders are only matched by name (their descriptions are
free text – no item is made from them).
Locations – an Asset Management location and the Maintenance location it points to share one shared location when they have the
same name; every other location gets its own.

Linking never changes the app record: the partner / item keeps its values and only its empty fields are filled from the record.
"""
from collections import OrderedDict

from django.apps import apps as django_apps
from django.db import transaction

from . import items as IT
from . import locations as LOC
from . import partners as PT


def _norm(v):
    return ' '.join(str(v or '').split())


def _row(name):
    return OrderedDict([('records', name), ('without_link', 0), ('by_tax_no', 0), ('by_contact', 0), ('by_code', 0), ('by_name', 0),
                        ('created', 0), ('left', 0)])


def _keep_partner(p):
    """Save a partner without copying it to the app records (linking never changes an app record)."""
    PT._local.busy = True
    try:
        if not p.number:
            p.number = PT.next_number()
        p.search_keys = (' '.join(PT.keys_of(p)) + ' ')[:400]
        p.save()
    finally:
        PT._local.busy = False


def _contact_match(ext, o):
    P = PT.Partner()
    probe = P()
    for ef, pf, tmap in ext.fields:
        if pf in ('mobile', 'phone', 'email', 'other_phones'):
            v = PT._ext_val(o, ef)
            if not PT._empty(v):
                setattr(probe, pf, v)
    for k in PT.keys_of(probe):
        if k[0] in 'me':
            x = P.objects.filter(search_keys__contains=k + ' ', merged_into__isnull=True).exclude(status='archived').order_by('pk').first()
            if x:
                return x
    return None


def _name_of(ext, o):
    for ef, pf, _ in ext.fields:
        if pf == 'name':
            return _norm(PT._ext_val(o, ef))
    return _norm(str(o))


def link_partners(create=True):
    P = PT.Partner()
    out = []
    for ext in PT.EXTS:
        if not ext.fields:   # the preferred supplier of an item is picked by hand
            continue
        m = ext.model
        r = _row(f'{ext.title} · {ext.plural if ext.ref else ext.label}')
        qs = m.objects.filter(**{ext.fk + '__isnull': True}).order_by('pk')
        touched = {}
        for o in qs:
            r['without_link'] += 1
            if ext.ref:
                tf = ext.fields[0][0]
                nm = _norm(getattr(o, tf))
                if not nm:
                    r['left'] += 1
                    continue
                p = PT.by_name(nm)
                if p is not None:
                    r['by_name'] += 1
                elif create:
                    p = P(number=PT.next_number(), kind='company', name=nm[:200], tags=['from link_partners'])
                    _keep_partner(p)
                    r['created'] += 1
                else:
                    r['left'] += 1
                    continue
                m.objects.filter(pk=o.pk).update(**{ext.fk: p})
                touched[p.pk] = p
                continue
            p = PT.strong_match(ext, o)
            how = 'by_tax_no' if p else ''
            if p is None:
                p = _contact_match(ext, o)
                how = 'by_contact' if p else ''
            if p is None:
                nm = _name_of(ext, o)
                p = PT.by_name(nm) if nm else None
                how = 'by_name' if p else ''
            if p is not None:
                PT._fill_from(ext, o, p)
                _keep_partner(p)
                m.objects.filter(pk=o.pk).update(**{ext.fk: p})
                PT._note(p, f'Linked {ext.title} {ext.label.lower()} {getattr(o, "number", "") or getattr(o, "code", "") or o.pk} ({o}) – {how.replace("_", " ")} (link_partners)')
                touched[p.pk] = p
                r[how] += 1
            elif create:
                o._mst_old = None
                PT._local.busy = False
                PT.ext_saved(ext, o, True)   # a new partner from the record (the 1.41 engine)
                r['created'] += 1
            else:
                r['left'] += 1
        for p in touched.values():
            PT.refresh_used_in(p)
        if r['without_link']:
            out.append(r)
    return out


def link_items(create=True):
    I = IT.Item()
    out = []
    # the HR asset types (a core table) are linked through their AssetPlus side record – the one asset numbering makes
    try:
        from AssetPlus.services import type_config
        AT = django_apps.get_model('OrganisationManager', 'AssetType')
        Cfg = django_apps.get_model('AssetPlus', 'AssetTypeConfig')
        have = set(Cfg.objects.values_list('asset_type_id', flat=True))
        IT._local.busy = True   # the side records are made first; their items below
        try:
            for at in AT.objects.exclude(pk__in=have):
                type_config(at.pk)
        finally:
            IT._local.busy = False
    except (LookupError, ImportError):
        pass
    for ext in IT.IEXTS:
        m = ext.model
        qs = m.objects.filter(**{ext.fk + '__isnull': True}).order_by('pk')
        if ext.ref and not ext.fill:   # asset models are picked by hand
            continue
        r = _row(f'{ext.title} · {ext.plural if ext.ref else ext.label}')
        touched = {}
        for o in qs:
            r['without_link'] += 1
            if ext.ref:   # cost lines: by name only, never made
                nm = _norm(getattr(o, 'description', ''))
                it = I.objects.filter(name__iexact=nm, is_active=True).order_by('pk').first() if nm else None
                if it is None:
                    r['left'] += 1
                    continue
                m.objects.filter(pk=o.pk).update(**{ext.fk: it})
                touched[it.pk] = it
                r['by_name'] += 1
                continue
            taken = m.objects.exclude(**{ext.fk + '__isnull': True}).values(ext.fk)   # one item per record of an app
            it, how = None, ''
            code = _norm(getattr(o, ext.match_code, '')) if ext.match_code else ''
            if code:
                it = I.objects.filter(code__iexact=code).exclude(pk__in=taken).order_by('pk').first()
                how = 'by_code' if it else ''
            if it is None:
                nm = _norm(getattr(o, 'name', ''))
                it = I.objects.filter(name__iexact=nm).exclude(pk__in=taken).order_by('pk').first() if nm else None
                how = 'by_name' if it else ''
            if it is not None:
                fill = {}
                for f in ext.fields:
                    v = IT._to_item(o, f)
                    if not IT._empty(v) and IT._empty(getattr(it, IT._att(I, f[1]))):
                        fill[IT._att(I, f[1])] = v
                if fill:
                    I.objects.filter(pk=it.pk).update(**fill)
                m.objects.filter(pk=o.pk).update(**{ext.fk: it})
                touched[it.pk] = it
                r[how] += 1
            elif create:
                o._itm_old = None
                IT.ext_saved(ext, o, True)   # a new item from the record (the 1.43 engine)
                r['created'] += 1
            else:
                r['left'] += 1
        for it in touched.values():
            IT.refresh_used_in(I.objects.get(pk=it.pk))
        if r['without_link']:
            out.append(r)
    return out


def link_locations():
    out, differ = [], []
    for ext in LOC.LEXTS:
        r = OrderedDict([('records', f'{ext.title} · {ext.label}'), ('without_link', 0), ('joined', 0), ('created', 0)])
        for o in ext.model.objects.filter(**{ext.fk + '__isnull': True}).order_by('pk'):
            o.refresh_from_db()
            if getattr(o, ext.fk + '_id'):   # linked meanwhile as the parent of another record
                r['without_link'] += 1
                r['created'] += 1
                continue
            r['without_link'] += 1
            sib = ext.sibling(o) if ext.sibling else None
            LOC.link(ext, o)
            r['joined' if sib is not None else 'created'] += 1
        if r['without_link']:
            out.append(r)
    try:
        A = django_apps.get_model('ZeoAssets', 'Location')
        Mn = django_apps.get_model('ZeoMaintenance', 'Location')
        for a in A.objects.exclude(mnt_location_id=None).only('code', 'name', 'mnt_location_id', 'shared_location'):
            mn = Mn.objects.filter(pk=a.mnt_location_id).only('code', 'name', 'shared_location').first()
            if mn and a.shared_location_id != mn.shared_location_id:
                differ.append(f'{a.code} "{a.name}" (Asset Management) ↔ {mn.code} "{mn.name}" (Maintenance)')
    except LookupError:
        pass
    return out, differ


def run(fn, apply, *a):
    """Run a linker; without `apply` everything is rolled back (dry run)."""
    with transaction.atomic():
        res = fn(*a)
        if not apply:
            transaction.set_rollback(True)
    return res


def table(rows):
    if not rows:
        return '  nothing to link'
    keys = [k for k in rows[0].keys() if k == 'records' or any(r[k] for r in rows)]
    w = max(len(r['records']) for r in rows)
    lines = ['  ' + 'records'.ljust(w) + '  ' + '  '.join(k.replace('_', ' ') for k in keys[1:])]
    for r in rows:
        lines.append('  ' + r['records'].ljust(w) + '  ' + '  '.join(str(r[k]).rjust(len(k)) for k in keys[1:]))
    tot = {k: sum(r[k] for r in rows) for k in keys[1:]}
    lines.append('  ' + 'total'.ljust(w) + '  ' + '  '.join(str(tot[k]).rjust(len(k)) for k in keys[1:]))
    return '\n'.join(lines)
