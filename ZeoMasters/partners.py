"""v1.41.0 – business partners: one master record per person / company, each app keeps its own part (Odoo res.partner, SAP BP).

An app record that belongs to a partner (CRM contact / account, Property tenant / owner / broker / vendor, … – an "extension")
has a `partner` link. The master fields are kept in both places so every existing screen, PDF, portal and report keeps working:

  app record saved   → the fields the user changed go to the partner (a new partner is made, or an existing one is used
                       when the sibling record already has one or the ID / tax number is the same)
  partner saved      → its master fields are copied to every app record of the partner (QuerySet.update – no signals, no loops)

Duplicates: phones, e-mail, ID and tax numbers are normalised into `search_keys`; same ID / tax no. = 100, same e-mail /
mobile = 60. Saving warns (the screen offers "use this partner" / "save anyway"); the list of possible duplicates is reviewed
by a person, and merging re-points every record to the partner that is kept (the oldest number by default).

Other apps add themselves with `register(Ext(...))` (phase 2: Projects, Maintenance, HSE, Asset Management, employees).
"""
import logging
import re
import threading

from django.apps import apps as django_apps
from django.db import transaction
from django.db.models.signals import post_save, pre_save

log = logging.getLogger(__name__)
_local = threading.local()

ROLE_LABELS = {
    'customer': 'Customer', 'prospect': 'Prospect', 'supplier': 'Supplier', 'tenant': 'Tenant', 'buyer': 'Buyer', 'owner': 'Owner',
    'broker': 'Broker', 'contractor': 'Contractor', 'student': 'Student', 'guardian': 'Guardian', 'patient': 'Patient',
    'insurer': 'Insurer', 'employee': 'Employee', 'other': 'Other',
}
ROLE_CHOICES = '|'.join(f'{k}={v}' for k, v in ROLE_LABELS.items())

# master fields an app record may carry (partner field names); everything else belongs to the app
MASTER_FIELDS = ('kind', 'name', 'first_name', 'middle_name', 'last_name', 'arabic_name', 'legal_name', 'company_name', 'job_title',
                 'gender', 'date_of_birth', 'nationality', 'id_type', 'id_number', 'id_expiry', 'tax_id', 'licence_no', 'licence_expiry',
                 'mobile', 'phone', 'email', 'other_phones', 'website', 'address', 'city', 'country', 'addresses', 'payment_term',
                 'price_book', 'currency', 'credit_limit', 'portal_user', 'consent', 'consent_on', 'retain_until', 'tags')
# fields that identify the party – changing them on a shared partner needs a manager (settings.lock_shared)
LOCKED = ('kind', 'name', 'arabic_name', 'legal_name', 'mobile', 'phone', 'email', 'id_type', 'id_number', 'tax_id', 'licence_no')

PERSON = {'person': 'individual', 'company': 'company'}
PERSON_BACK = {'individual': 'person', 'company': 'company'}


class Ext:
    """One kind of app record that belongs to a partner."""

    def __init__(self, app, title, model, reg, label, fields, roles, kind=None, sibling=None, to_partner=None, from_partner=None,
                 active=None, fk='partner', ref=False, url=None, plural=None):
        self.app, self.title, self.model_path, self.reg, self.label = app, title, model, reg, label
        self.fk = fk                    # v1.42.0: the link field (partner / supplier_partner / vendor_partner)
        self.ref = ref                  # v1.42.0: a reference (a part's supplier) – never makes a partner; one summary row in "Used in"
        self.url = url                  # v1.42.0: fn(record) → page of the record when the app is not a kit app (Projects, HSE)
        self.plural = plural or label + 's'
        self.fields = fields            # [(app field, partner field, {app value: partner value} or None)]
        self.roles = roles              # fn(record) → [role codes]
        self.kind = kind                # fn(record) → 'individual' / 'company' when not in fields
        self.sibling = sibling          # fn(record) → Partner of a linked record of another app (CRM contact ↔ tenant)
        self.to_partner = to_partner    # fn(record, partner) – extra fields (e.g. account → company of a contact)
        self.from_partner = from_partner  # fn(partner) → {app field: value} – extra fields copied back
        self.active = active or (lambda o: getattr(o, 'is_active', True) and getattr(o, 'status', 'active') not in ('inactive',))

    @property
    def model(self):
        return django_apps.get_model(self.model_path)


EXTS = []


def register(ext):
    EXTS.append(ext)
    m = ext.model
    pre_save.connect(_stash, sender=m, dispatch_uid=f'mst_pre_{ext.model_path}')
    post_save.connect(lambda sender, instance, created, raw=False, ext=ext, **kw: (None if raw else ext_saved(ext, instance, created)),
                      sender=m, weak=False, dispatch_uid=f'mst_post_{ext.model_path}')
    return ext


def ext_of(model):
    for e in EXTS:
        if e.model is model:
            return e
    return None


# ------------------------------------------------------------------ normalising
def digits(v):
    return re.sub(r'\D', '', str(v or ''))


def phone_key(v):
    d = digits(v)
    return d[-9:] if len(d) >= 7 else ''


def alnum(v):
    return re.sub(r'[^A-Z0-9]', '', str(v or '').upper())


def keys_of(p):
    """Normalised keys of a partner: m: phone (last 9 digits), e: e-mail, i: ID number, t: tax no., l: trade licence."""
    out = []
    phones = [p.mobile, p.phone] + [(x or {}).get('number', '') if isinstance(x, dict) else x for x in (p.other_phones or [])]
    for ph in phones:
        k = phone_key(ph)
        if k:
            out.append('m:' + k)
    if p.email:
        out.append('e:' + p.email.strip().lower())
    if alnum(p.id_number) and len(alnum(p.id_number)) >= 5:
        out.append('i:' + alnum(p.id_number))
    if alnum(p.tax_id) and len(alnum(p.tax_id)) >= 5:
        out.append('t:' + alnum(p.tax_id))
    if p.kind == 'company' and alnum(p.licence_no) and len(alnum(p.licence_no)) >= 4:
        out.append('l:' + alnum(p.licence_no))
    return list(dict.fromkeys(out))


def role_codes(p):
    out = []
    for r in p.roles or []:
        c = r.get('role') if isinstance(r, dict) else r
        if c and c not in out:
            out.append(c)
    return out


def set_roles(p, codes):
    """Roles chosen by hand on the partner (the apps' roles come from their records – see refresh_used_in)."""
    p.roles = [{'role': c} for c in dict.fromkeys(c for c in codes if c)]


def split_name(name):
    parts = (name or '').split()
    if len(parts) <= 1:
        return (parts[0] if parts else ''), '', ''
    if len(parts) == 2:
        return parts[0], '', parts[1]
    return parts[0], ' '.join(parts[1:-1]), parts[-1]


# ------------------------------------------------------------------ helpers
def Partner():
    return django_apps.get_model('ZeoMasters', 'Partner')


def next_number():
    from .app import app
    return app.next_number('partner', 'BP')


def _fit(model, field, v):
    """A partner value made to fit the app field (length, choices)."""
    try:
        f = model._meta.get_field(field)
    except Exception:
        return v
    if v is None:
        return None if f.null else ('' if f.get_internal_type() in ('CharField', 'TextField', 'EmailField') else v)
    ml = getattr(f, 'max_length', None)
    if ml and isinstance(v, str):
        v = v[:ml]
    return v


def _pval(p, pf):
    if pf in ('payment_term', 'price_book', 'portal_user'):
        return getattr(p, pf + '_id')
    return getattr(p, pf)


def _ext_val(o, ef):
    f = o._meta.get_field(ef)
    return getattr(o, f.attname)


def _to_partner_value(v, tmap):
    if tmap and v in tmap:
        return tmap[v]
    return v


def _from_partner_value(v, tmap):
    if tmap:
        back = {pv: ev for ev, pv in tmap.items()}
        return back.get(v, v)
    return v


def _empty(v):
    return v in (None, '', [], {})


def _stash(sender, instance, raw=False, **kw):
    """Previous values of the master fields (to send only what the user changed)."""
    if raw or getattr(_local, 'busy', False) or not instance.pk:
        return
    ext = ext_of(sender)
    if not ext:
        return
    try:
        old = sender.objects.filter(pk=instance.pk).values(*[sender._meta.get_field(ef).attname for ef, _, _ in ext.fields], ext.fk + '_id').first()
        if old is not None:
            old['partner_id'] = old.pop(ext.fk + '_id')
    except Exception:
        old = None
    instance._mst_old = old


# ------------------------------------------------------------------ app record → partner
def ext_saved(ext, o, created):
    if getattr(_local, 'busy', False) or getattr(o, '_mst_skip', False):
        return
    try:
        with transaction.atomic():
            _ext_saved(ext, o, created)
    except Exception:
        log.exception('business partner sync failed for %s %s', ext.model_path, o.pk)


def _ext_saved(ext, o, created):
    P = Partner()
    pid = getattr(o, ext.fk + '_id')
    p = P.objects.filter(pk=pid).first() if pid else None
    old = getattr(o, '_mst_old', None) or {}
    changed = False
    if p is None and ext.ref:
        prev = old.get('partner_id')
        if prev:
            x = P.objects.filter(pk=prev).first()
            if x:
                refresh_used_in(x)
        return
    if ext.ref:   # a reference: the record shows the partner's name; it never changes the partner
        tf = ext.fields[0][0] if ext.fields else None
        if tf and getattr(o, tf) != _fit(ext.model, tf, p.name):
            ext.model.objects.filter(pk=o.pk).update(**{tf: _fit(ext.model, tf, p.name)})
        refresh_used_in(p)
        prev = old.get('partner_id')
        if prev and prev != p.pk:
            x = P.objects.filter(pk=prev).first()
            if x:
                refresh_used_in(x)
        return
    if p is None:
        p = (ext.sibling(o) if ext.sibling else None)
        if p is None:
            p = strong_match(ext, o)
            if p is not None:
                _note(p, f'Linked {ext.title} {ext.label.lower()} {getattr(o, "number", "") or o.pk} ({o}) – same ID / tax number')
        if p is None:
            p = P(number=next_number())
            for ef, pf, tmap in ext.fields:
                v = _to_partner_value(_ext_val(o, ef), tmap)
                if not _empty(v):
                    setattr(p, pf if pf not in ('payment_term', 'price_book', 'portal_user') else pf + '_id', v)
            if ext.kind and not any(pf == 'kind' for _, pf, _ in ext.fields):
                p.kind = ext.kind(o)
            if p.kind == 'individual' and not (p.first_name or p.last_name):
                p.first_name, p.middle_name, p.last_name = (x[:80] for x in split_name(p.name))
            p.name = (p.name or o.__str__() or '–')[:200]
        else:
            _fill_from(ext, o, p)       # the partner keeps its values; empty ones are filled, other phones kept
        changed = True
        ext.model.objects.filter(pk=o.pk).update(**{ext.fk: p if p.pk else None})
    elif old.get('partner_id') and old.get('partner_id') != p.pk:
        # moved to another partner (picked on the form): the record takes that partner's master fields
        prev = P.objects.filter(pk=old['partner_id']).first()
        changed = True
        if prev:
            refresh_used_in(prev)
    else:
        for ef, pf, tmap in ext.fields:
            att = ext.model._meta.get_field(ef).attname
            new = _ext_val(o, ef)
            if att in old and old[att] == new:
                continue
            if att not in old and not created:
                continue
            v = _to_partner_value(new, tmap)
            if pf in ('payment_term', 'price_book', 'portal_user'):
                setattr(p, pf + '_id', v)
            else:
                setattr(p, pf, _fit(P, pf, v) if v is not None else (None if P._meta.get_field(pf).null else _fit(P, pf, '')))
            changed = True
    if ext.to_partner:
        changed = bool(ext.to_partner(o, p)) or changed
    if changed or not p.pk:
        save_partner(p)
        if getattr(o, ext.fk + '_id') != p.pk:
            setattr(o, ext.fk + '_id', p.pk)
            ext.model.objects.filter(pk=o.pk).update(**{ext.fk: p})
            push(p)
        refresh_used_in(p)
    else:
        refresh_used_in(p)


def _fill_from(ext, o, p):
    for ef, pf, tmap in ext.fields:
        v = _to_partner_value(_ext_val(o, ef), tmap)
        if _empty(v):
            continue
        cur = _pval(p, pf)
        if _empty(cur):
            setattr(p, pf if pf not in ('payment_term', 'price_book', 'portal_user') else pf + '_id', v)
        elif pf in ('mobile', 'phone') and phone_key(cur) != phone_key(v):
            others = list(p.other_phones or [])
            if not any(phone_key((x or {}).get('number') if isinstance(x, dict) else x) == phone_key(v) for x in others):
                others.append({'label': ext.title, 'number': v})
                p.other_phones = others
        elif pf == 'email' and str(cur).lower() != str(v).lower() and str(v).lower() not in (p.notes or '').lower():
            p.notes = ((p.notes or '') + f'\nOther e-mail ({ext.title}): {v}').strip()


def _note(p, text):
    try:
        from .app import app
        app.event('partner', p.pk, text)
    except Exception:
        pass


def strong_match(ext, o):
    """An existing partner with the same ID number or tax no. (never by name / phone / e-mail alone)."""
    P = Partner()
    keys = []
    for ef, pf, tmap in ext.fields:
        if pf == 'id_number' and len(alnum(_ext_val(o, ef))) >= 5:
            keys.append('i:' + alnum(_ext_val(o, ef)))
        if pf == 'tax_id' and len(alnum(_ext_val(o, ef))) >= 5:
            keys.append('t:' + alnum(_ext_val(o, ef)))
    for k in keys:
        x = P.objects.filter(search_keys__contains=k + ' ', merged_into__isnull=True).order_by('pk').first()
        if x:
            return x
    return None


def possible_match(ext, o):
    """Best partner sharing an ID / tax no. / e-mail / mobile with the app record being created (for the warning on save)."""
    P = Partner()
    probe = P()
    for ef, pf, tmap in ext.fields:
        if pf in ('mobile', 'phone', 'email', 'id_number', 'tax_id', 'licence_no', 'kind', 'other_phones'):
            v = _to_partner_value(_ext_val(o, ef), tmap)
            if not _empty(v):
                setattr(probe, pf, v)
    best, score, why = None, 0, ''
    for k in keys_of(probe):
        s = 100 if k[0] in 'it' else 80 if k[0] == 'l' else 60
        x = P.objects.filter(search_keys__contains=k + ' ', merged_into__isnull=True).order_by('pk').first()
        if x and s > score:
            best, score, why = x, s, {'m': 'phone', 'e': 'e-mail', 'i': 'ID number', 't': 'tax number', 'l': 'trade licence'}[k[0]]
    return best, why


# ------------------------------------------------------------------ partner → app records
def save_partner(p):
    """Save a partner and copy its master fields to every app record; refresh keys, 'used in' and duplicates."""
    P = Partner()
    _local.busy = True
    try:
        if not p.number:
            p.number = next_number()
        p.search_keys = (' '.join(keys_of(p)) + ' ')[:400]
        p.save()
        push(p)
        refresh_used_in(p)
    finally:
        _local.busy = False
    try:
        scan_duplicates(p)
    except Exception:
        log.exception('duplicate scan failed for partner %s', p.pk)
    return p


def push(p):
    for ext in EXTS:
        m = ext.model
        qs = m.objects.filter(**{ext.fk + '_id': p.pk})
        if not qs.exists():
            continue
        vals = {}
        for ef, pf, tmap in ext.fields:
            v = _from_partner_value(_pval(p, pf), tmap)
            f = m._meta.get_field(ef)
            vals[f.attname] = _fit(m, ef, v)
        if ext.from_partner:
            vals.update(ext.from_partner(p) or {})
        if vals:
            qs.update(**vals)


def refresh_used_in(p):
    P = Partner()
    rows, titles, app_roles = [], [], []
    for ext in EXTS:
        qs = ext.model.objects.filter(**{ext.fk + '_id': p.pk})
        if ext.ref:   # a reference: one summary row (e.g. "Asset Management · Spare parts – 12")
            n = qs.count()
            if not n:
                continue
            rows.append({'app': ext.title, 'record': ext.plural, 'number': str(n), 'name': f'{n} record(s)',
                         'roles': ', '.join(ROLE_LABELS.get(r, r) for r in (ext.roles(None) or [])), 'active': 'Yes', 'slug': '', 'kind': '', 'id': None})
            app_roles += [r for r in (ext.roles(None) or []) if r not in app_roles]
        else:
            for o in qs[:50]:
                row = {'app': ext.title, 'record': ext.label, 'number': getattr(o, 'number', '') or getattr(o, 'code', '') or str(o.pk), 'name': str(o)[:120],
                       'roles': ', '.join(ROLE_LABELS.get(r, r) for r in (ext.roles(o) or [])), 'active': 'Yes' if ext.active(o) else 'No',
                       'slug': ext.app, 'kind': ext.reg, 'id': o.pk}
                if ext.url:
                    row['url'] = ext.url(o)
                rows.append(row)
                if ext.active(o):
                    app_roles += [r for r in (ext.roles(o) or []) if r not in app_roles]
            if not qs.exists():
                continue
        if ext.title not in titles:
            titles.append(ext.title)
    if p.employee_id:   # v1.42.0 employee link (commission, expense, settlement payee)
        e = django_apps.get_model('EmpManagement', 'emp_master').objects.filter(pk=p.employee_id).only('pk', 'emp_code', 'emp_first_name', 'emp_last_name').first()
        if e:
            rows.append({'app': 'HRMS', 'record': 'Employee', 'number': e.emp_code or str(e.pk),
                         'name': f'{e.emp_first_name or ""} {e.emp_last_name or ""}'.strip(), 'roles': 'Employee', 'active': 'Yes',
                         'slug': '', 'kind': '', 'id': e.pk, 'url': f'/main-sidebar/sub-sidebar/employee-details/{e.pk}/details'})
            if 'employee' not in app_roles:
                app_roles.append('employee')
            if 'HRMS' not in titles:
                titles.append('HRMS')
    text = ', '.join(titles)[:200]
    codes = app_roles + [c for c in role_codes(p) if c not in app_roles]
    rtext = ', '.join(ROLE_LABELS.get(c, c.title()) for c in codes)[:200]
    if rows != (p.used_in or []) or text != p.apps_text or rtext != p.roles_text:
        P.objects.filter(pk=p.pk).update(used_in=rows, apps_text=text, roles_text=rtext)
        p.used_in, p.apps_text, p.roles_text = rows, text, rtext


def partner_saved(sender, instance, created, raw=False, **kw):
    """A partner saved from the Masters screen (not by the sync itself)."""
    if raw or getattr(_local, 'busy', False):
        return
    save_partner(instance)


# ------------------------------------------------------------------ duplicates and merge
def scan_duplicates(p):
    P, D = Partner(), django_apps.get_model('ZeoMasters', 'DuplicatePair')
    if p.merged_into_id or p.status == 'archived':
        return 0
    made = 0
    for k in keys_of(p):
        score = 100 if k[0] in 'it' else 80 if k[0] == 'l' else 60
        why = {'m': 'same phone', 'e': 'same e-mail', 'i': 'same ID number', 't': 'same tax number', 'l': 'same trade licence'}[k[0]]
        for x in P.objects.filter(search_keys__contains=k + ' ', merged_into__isnull=True).exclude(pk=p.pk).exclude(status='archived')[:20]:
            a, b = (x, p) if x.pk < p.pk else (p, x)
            if p.parent_id == x.pk or x.parent_id == p.pk:   # a company and its own contact person share the office phone
                continue
            row = D.objects.filter(partner=a, other=b).first()
            if row is None:
                from .app import app
                D.objects.create(number=app.next_number('duplicate', 'DUP'), partner=a, other=b, reason=why, score=score)
                made += 1
            elif row.status == 'open' and score > row.score:
                row.score, row.reason = score, why
                row.save(update_fields=['score', 'reason'])
    return made


def merge(keep, drop, user=None):
    """Re-point every record of `drop` to `keep` (all apps), fill keep's empty fields, archive drop. Cannot be undone automatically."""
    P = Partner()
    if keep.pk == drop.pk:
        raise ValueError('Choose two different partners.')
    with transaction.atomic():
        _local.busy = True
        try:
            for rel in P._meta.related_objects:
                if rel.one_to_many or rel.one_to_one:
                    fk = rel.field
                    if fk.model is P and fk.name in ('merged_into',):
                        continue
                    try:
                        fk.model.objects.filter(**{fk.name: drop}).update(**{fk.name: keep})
                    except Exception:
                        log.exception('merge: could not move %s.%s', fk.model.__name__, fk.name)
            for ext in EXTS:   # v1.42.0: links without a reverse name (Asset Management references)
                ext.model.objects.filter(**{ext.fk: drop}).update(**{ext.fk: keep})
            for f in P._meta.concrete_fields:
                if f.name in ('id', 'number', 'created_at', 'updated_at', 'merged_into', 'used_in', 'search_keys', 'roles', 'roles_text',
                              'apps_text', 'status', 'notes'):
                    continue
                if _empty(getattr(keep, f.attname)) and not _empty(getattr(drop, f.attname)):
                    setattr(keep, f.attname, getattr(drop, f.attname))
            others = list(keep.other_phones or [])
            for ph in [drop.mobile, drop.phone]:
                if ph and phone_key(ph) not in (phone_key(keep.mobile), phone_key(keep.phone)) and \
                        not any(phone_key((x or {}).get('number') if isinstance(x, dict) else x) == phone_key(ph) for x in others):
                    others.append({'label': f'From {drop.number}', 'number': ph})
            keep.other_phones = others
            set_roles(keep, role_codes(keep) + role_codes(drop))
            keep.notes = ((keep.notes or '') + f'\nMerged {drop.number} ({drop.name}).' + (f'\n{drop.notes}' if drop.notes else '')).strip()
            drop.merged_into, drop.status = keep, 'archived'
            drop.search_keys = ''
            drop.save()
        finally:
            _local.busy = False
        save_partner(keep)
        D = django_apps.get_model('ZeoMasters', 'DuplicatePair')
        from django.utils import timezone
        D.objects.filter(status='open').filter(partner=drop).update(status='merged', decided_by=user, decided_at=timezone.now())
        D.objects.filter(status='open').filter(other=drop).update(status='merged', decided_by=user, decided_at=timezone.now())
    return keep


# ------------------------------------------------------------------ linking the records that exist (migration / command)
def link_all(stdout=None):
    """Give every app record a partner: siblings first (CRM ↔ Property links), then same ID / tax no.; nothing is merged on a
    guess – records that only share a phone / e-mail get their own partner and appear in Possible duplicates."""
    n = 0
    for ext in EXTS:
        if ext.ref:
            n += link_refs(ext)
            continue
        m = ext.model
        for o in m.objects.filter(**{ext.fk + '__isnull': True}).order_by('pk'):
            o._mst_old = None
            _local.busy = False
            ext_saved(ext, o, True)
            n += 1
    if stdout:
        stdout.write(f'linked {n} app records to business partners')
    return n


def link_refs(ext):
    """v1.42.0: supplier names typed on records (spare parts, work orders…) → the supplier partner of that name; a name used for
    the first time becomes a company partner with the supplier role (tag "from supplier names"). Exact name, case ignored."""
    P = Partner()
    if not ext.fields:
        return 0
    tf = ext.fields[0][0]   # the text field
    n = 0
    names = {}
    for o in ext.model.objects.filter(**{ext.fk + '__isnull': True}).exclude(**{tf: ''}).only('pk', tf):
        nm = ' '.join(str(getattr(o, tf) or '').split())
        if not nm:
            continue
        key = nm.lower()
        if key not in names:
            x = P.objects.filter(name__iexact=nm, merged_into__isnull=True).order_by('pk').first()
            if x is None:
                x = P(number=next_number(), kind='company', name=nm[:200], tags=['from supplier names'])
                _local.busy = True
                try:
                    x.search_keys = ''
                    x.save()
                finally:
                    _local.busy = False
            names[key] = x
        ext.model.objects.filter(pk=o.pk).update(**{ext.fk: names[key]})
        n += 1
    for x in names.values():
        refresh_used_in(x)
    return n


# ------------------------------------------------------------------ v1.42.0 employees
def _emp_values(e):
    name = ' '.join(x for x in (e.emp_first_name, getattr(e, 'emp_middle_name', ''), e.emp_last_name) if x).strip() or e.emp_code or str(e.pk)
    return {'kind': 'individual', 'name': name[:200], 'first_name': (e.emp_first_name or '')[:80], 'last_name': (e.emp_last_name or '')[:80],
            'mobile': (e.emp_mobile_number_1 or '')[:30], 'email': (getattr(e, 'emp_personal_email', '') or getattr(e, 'emp_company_email', '') or '')[:254]}


def partner_for_employee(e, create=True):
    """The partner of an employee (made when an app needs to pay or invoice the employee: commission, expense, final settlement)."""
    P = Partner()
    p = P.objects.filter(employee_id=e.pk, merged_into__isnull=True).order_by('pk').first()
    if p is None and create:
        p = P(number=next_number(), employee_id=e.pk, **_emp_values(e))
        save_partner(p)
    return p


def employee_saved(sender, instance, created, raw=False, **kw):
    """An employee's name / mobile / e-mail changed: the partner of the employee follows (only when one exists)."""
    if raw or getattr(_local, 'busy', False):
        return
    try:
        P = Partner()
        for p in P.objects.filter(employee_id=instance.pk, merged_into__isnull=True):
            v = _emp_values(instance)
            if any(getattr(p, k) != val for k, val in v.items() if val):
                for k, val in v.items():
                    if val:
                        setattr(p, k, val)
                save_partner(p)
    except Exception:
        log.exception('employee → partner sync failed for %s', instance.pk)
