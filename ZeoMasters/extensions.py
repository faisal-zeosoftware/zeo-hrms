"""v1.41.0 – the app records that belong to a business partner (phase 1: CRM and Property).

Each Ext says which app fields are master fields (copied both ways), which role the record gives the partner and how to find the
partner of a linked record of another app. `attach(app)` adds the partner picker, the shared-partner lock and the duplicate
warning to the app's own forms. Imported by ZeoMasters.apps.ready() (signals) and at the end of each app module (forms).
"""
from django.apps import apps as django_apps

from . import partners as PT
from .partners import PERSON, Ext, register

TENANT_ROLES = {'tenant': ['tenant'], 'buyer': ['buyer'], 'both': ['tenant', 'buyer']}
CONTACT_ROLES = {'prospect': ['prospect'], 'customer': ['customer'], 'tenant': ['tenant'], 'owner': ['owner'], 'buyer': ['buyer'],
                 'partner': ['broker'], 'other': ['other']}
ACCOUNT_ROLES = {'customer': ['customer'], 'prospect': ['prospect'], 'supplier': ['supplier'], 'both': ['customer', 'supplier'],
                 'partner': ['broker']}


def _model(path):
    return django_apps.get_model(path)


def _partner_of(model_path, pk):
    if not pk:
        return None
    o = _model(model_path).objects.filter(pk=pk).only('partner').first()
    return o.partner if (o and o.partner_id and not o.partner.merged_into_id) else None


# ---------------- CRM
def contact_to_partner(o, p):
    """The account of a CRM contact is the contact's company (Odoo: parent_id)."""
    if o.account_id and p.kind == 'individual':
        acc = _model('ZeoMasters.Account').objects.filter(pk=o.account_id).only('partner').first()
        if acc and acc.partner_id and p.parent_id != acc.partner_id and acc.partner_id != p.pk:
            p.parent_id = acc.partner_id
            if p.pk:
                return True
    return False


def contact_sibling(o):
    return _partner_of('ZeoProperty.Party', o.property_party_id) or (
        _partner_of('ZeoMasters.Account', o.account_id) if (o.kind == 'company' and o.account_id) else None)


def account_to_partner(o, p):
    if o.parent_id:
        par = _model('ZeoMasters.Account').objects.filter(pk=o.parent_id).only('partner').first()
        if par and par.partner_id and p.parent_id != par.partner_id and par.partner_id != p.pk:
            p.parent_id = par.partner_id
            return bool(p.pk)
    return False


CRM_ACCOUNT = register(Ext(
    'zeo-crm', 'CRM', 'ZeoMasters.Account', 'account', 'Account',
    fields=[('name', 'name', None), ('legal_name', 'legal_name', None), ('tax_id', 'tax_id', None), ('licence_no', 'licence_no', None),
            ('phone', 'phone', None), ('email', 'email', None), ('website', 'website', None), ('billing_address', 'address', None),
            ('city', 'city', None), ('country', 'country', None), ('payment_term', 'payment_term', None), ('price_book', 'price_book', None),
            ('currency', 'currency', None), ('credit_limit', 'credit_limit', None), ('tags', 'tags', None)],
    roles=lambda o: ACCOUNT_ROLES.get(o.kind, ['customer']), kind=lambda o: 'company', to_partner=account_to_partner,
    from_partner=lambda p: {'is_active': p.status != 'archived'} if p.status == 'archived' else {}))


CRM_CONTACT = register(Ext(
    'zeo-crm', 'CRM', 'ZeoCRM.Contact', 'contact', 'Contact',
    fields=[('kind', 'kind', PERSON), ('name', 'name', None), ('company_name', 'company_name', None), ('email', 'email', None),
            ('phone', 'mobile', None), ('job_title', 'job_title', None), ('consent', 'consent', None), ('tags', 'tags', None)],
    roles=lambda o: CONTACT_ROLES.get(o.contact_type, ['prospect']), sibling=contact_sibling, to_partner=contact_to_partner))

# ---------------- Property
def party_sibling(o):
    return _partner_of('ZeoCRM.Contact', o.crm_contact_id)


PROP_PARTY = register(Ext(
    'zeo-property', 'Property', 'ZeoProperty.Party', 'party', 'Tenant / buyer',
    fields=[('party_type', 'kind', PERSON), ('name', 'name', None), ('company_name', 'company_name', None), ('email', 'email', None),
            ('phone', 'mobile', None), ('id_type', 'id_type', None), ('id_document', 'id_number', None), ('id_expiry', 'id_expiry', None),
            ('nationality', 'nationality', None), ('phones', 'other_phones', None), ('address', 'address', None),
            ('addresses', 'addresses', None), ('portal_user', 'portal_user', None), ('consent', 'consent', None),
            ('consent_on', 'consent_on', None), ('retain_until', 'retain_until', None)],
    roles=lambda o: TENANT_ROLES.get(o.kind, ['tenant']), sibling=party_sibling))

PROP_OWNER = register(Ext(
    'zeo-property', 'Property', 'ZeoProperty.Owner', 'owner', 'Owner',
    fields=[('kind', 'kind', PERSON), ('name', 'name', None), ('email', 'email', None), ('phone', 'mobile', None), ('phones', 'other_phones', None),
            ('address', 'address', None), ('id_type', 'id_type', None), ('id_number', 'id_number', None), ('id_expiry', 'id_expiry', None),
            ('nationality', 'nationality', None), ('tax_id', 'tax_id', None), ('portal_user', 'portal_user', None)],
    roles=lambda o: ['owner']))

PROP_BROKER = register(Ext(
    'zeo-property', 'Property', 'ZeoProperty.Broker', 'broker', 'External broker',
    fields=[('name', 'name', None), ('company_name', 'company_name', None), ('email', 'email', None), ('phone', 'mobile', None)],
    roles=lambda o: ['broker'], kind=lambda o: 'individual'))

PROP_VENDOR = register(Ext(
    'zeo-property', 'Property', 'ZeoProperty.Vendor', 'vendor', 'Vendor',
    fields=[('name', 'name', None), ('email', 'email', None), ('phone', 'phone', None), ('licence_no', 'licence_no', None),
            ('licence_expiry', 'licence_expiry', None), ('tax_id', 'tax_id', None)],
    roles=lambda o: ['supplier'], kind=lambda o: 'company', active=lambda o: o.status == 'active'))


# ---------------- v1.42.0 phase 2: Maintenance, Projects, HSE, Asset Management
def prop_vendor_sibling(o):
    return _partner_of('ZeoMaintenance.Vendor', o.mnt_vendor_id)


def mnt_vendor_sibling(o):
    v = _model('ZeoProperty.Vendor').objects.filter(mnt_vendor_id=o.pk).only('partner').first() if o.pk else None
    return v.partner if (v and v.partner_id and not v.partner.merged_into_id) else None


PROP_VENDOR.sibling = prop_vendor_sibling

MNT_VENDOR = register(Ext(
    'zeo-maintenance', 'Maintenance', 'ZeoMaintenance.Vendor', 'vendor', 'Vendor',
    fields=[('name', 'name', None), ('email', 'email', None), ('phone', 'phone', None), ('licence_expiry', 'licence_expiry', None)],
    roles=lambda o: ['supplier'], kind=lambda o: 'company', sibling=mnt_vendor_sibling))


def pj_contact_to_partner(o, p):
    # a Projects contact is a person of the customer company
    cp = _model('ZeoProjects.Customer').objects.filter(pk=o.customer_id).only('partner').first()
    if cp and cp.partner_id and p.parent_id != cp.partner_id and cp.partner_id != p.pk:
        p.parent_id = cp.partner_id
        return bool(p.pk)
    return False


PJ_CUSTOMER = register(Ext(
    'zeo-projects', 'Projects', 'ZeoProjects.Customer', 'customer', 'Customer',
    fields=[('name', 'name', None), ('trn', 'tax_id', None), ('email', 'email', None), ('phone', 'phone', None), ('website', 'website', None),
            ('address', 'address', None), ('city', 'city', None), ('country', 'country', None)],
    roles=lambda o: ['customer'], kind=lambda o: 'company', url=lambda o: f'/main-sidebar/zeo-projects/customers/{o.pk}'))

PJ_CONTACT = register(Ext(
    'zeo-projects', 'Projects', 'ZeoProjects.Contact', 'contact', 'Customer contact',
    fields=[('name', 'name', None), ('email', 'email', None), ('phone', 'mobile', None), ('designation', 'job_title', None)],
    roles=lambda o: [], kind=lambda o: 'individual', to_partner=pj_contact_to_partner,
    url=lambda o: f'/main-sidebar/zeo-projects/customers/{o.customer_id}'))

HSE_CONTRACTOR = register(Ext(
    'zeo-hse', 'HSE', 'ZeoHSE.Contractor', 'contractor', 'Contractor',
    fields=[('name', 'name', None), ('phone', 'phone', None), ('email', 'email', None), ('trade_licence_no', 'licence_no', None),
            ('licence_expiry', 'licence_expiry', None)],
    roles=lambda o: ['contractor'], kind=lambda o: 'company', active=lambda o: o.is_active and o.status != 'suspended',
    url=lambda o: '/main-sidebar/zeo-hse/setup/contractors'))

# Asset Management: suppliers typed on records become references to a supplier partner
for _model_path, _reg, _label, _plural, _tf, _fk in (
        ('ZeoAssets.AssetCard', 'asset', 'Asset', 'Assets bought', 'supplier', 'supplier_partner'),
        ('ZeoAssets.CapitalCost', 'capital_cost', 'Capital cost', 'Capital costs', 'supplier', 'supplier_partner'),
        ('ZeoAssets.MaintenancePlan', 'plan', 'Maintenance plan', 'Maintenance plans', 'vendor', 'vendor_partner'),
        ('ZeoAssets.WorkOrder', 'work_order', 'Work order', 'Work orders', 'vendor', 'vendor_partner'),
        ('ZeoAssets.Part', 'part', 'Spare part', 'Spare parts supplied', 'supplier', 'supplier_partner')):
    register(Ext('zeo-assets', 'Asset Management', _model_path, _reg, _label, fields=[(_tf, 'name', None)], roles=lambda o: ['supplier'],
                 fk=_fk, ref=True, plural=_plural))


# v1.43.0: the preferred supplier of an item
register(Ext('zeo-masters', 'Masters', 'ZeoMasters.Product', 'item', 'Item', fields=[], roles=lambda o: ['supplier'],
             fk='preferred_supplier', ref=True, plural='Items supplied'))


# ------------------------------------------------------------------ forms of the apps
def _privileged(app, reg, request):
    from .app import app as mst
    # the app's manager role (Property manager, CRM manager…), a Masters manager or an administrator – not every register manager
    return app.is_admin(request) or app.is_manager(request) or mst.has(request, 'mst_manager')


def attach(app):
    """Partner picker, shared-partner lock and duplicate warning on every register of `app` that belongs to a partner."""
    from .app import partner_label, active_partners
    from .models import Partner, MasterSettings
    from zeo.appkit.engine import AppError

    app.option('partners', active_partners, partner_label, ('name', 'number', 'mobile', 'email', 'tax_id', 'id_number'), Partner)
    for ext in PT.EXTS:
        if ext.app != app.slug or ext.reg not in app.regs:
            continue
        reg = app.regs[ext.reg]
        if getattr(reg, '_mst', False):
            continue
        reg._mst = True
        if ext.fk not in reg.form:
            if ext.ref:   # the supplier picker goes next to the typed supplier name
                tf = ext.fields[0][0]
                form = list(reg.form)
                form.insert(form.index(tf) if tf in form else 0, ext.fk)
                reg.form = tuple(form)
            else:
                reg.form = (ext.fk,) + tuple(reg.form)
        orig_check, orig_defaults = reg.check, reg.defaults
        if ext.ref:
            def ref_check(o, request, old, ext=ext, orig_check=orig_check):
                pid = getattr(o, ext.fk + '_id')
                if pid and (old is None or getattr(old, ext.fk + '_id') != pid):
                    p = Partner.objects.filter(pk=pid).first()
                    if p and p.status == 'blocked':
                        raise AppError(f'{p.name} ({p.number}) is blocked: {p.blocked_reason}.', ext.fk)
                    if p:
                        setattr(o, ext.fields[0][0], PT._fit(ext.model, ext.fields[0][0], p.name))
                if orig_check:
                    orig_check(o, request, old)
            reg.check = ref_check
            continue

        def check(o, request, old, reg=reg, ext=ext, orig_check=orig_check):
            s = MasterSettings.get()
            force = str(request.data.get('force_duplicate', '')).lower() in ('1', 'true', 'yes')
            pid = getattr(o, ext.fk + '_id')
            if old is not None and pid and s.lock_shared and not _privileged(app, reg, request):
                p = Partner.objects.filter(pk=pid).first()
                if p and len(p.used_in or []) > 1:
                    for ef, pf, _ in ext.fields:
                        if pf not in PT.LOCKED:
                            continue
                        att = o._meta.get_field(ef).attname
                        if getattr(old, att) != getattr(o, att):
                            raise AppError(f'{p.name} ({p.number}) is used in {p.apps_text}: its {o._meta.get_field(ef).verbose_name} is changed '
                                           f'in Business partners by a manager.', ef)
            if old is None and pid:
                p = Partner.objects.filter(pk=pid).first()
                if p and p.status == 'blocked':
                    raise AppError(f'{p.name} ({p.number}) is blocked: {p.blocked_reason}.', 'partner')
            if old is None and not pid and s.duplicate_check and not force:
                m, why = PT.possible_match(ext, o)
                if m is not None and why in ('ID number', 'tax number'):
                    m = None   # same legal person / entity: linked automatically on save (history note on the partner)
                if m is not None:
                    raise AppError(f'{m.name} ({m.number}) is already a business partner with this {why} ({m.apps_text or "Masters"}). '
                                   'Use it, or save again to make a new partner.', 'partner',
                                   data={'duplicate': {'id': m.pk, 'number': m.number, 'name': m.name, 'partner': True}})
            if old is not None and pid and getattr(old, ext.fk + '_id') != pid:
                o._mst_relink = True
            if orig_check:
                orig_check(o, request, old)

        def defaults(o, request, ext=ext, orig_defaults=orig_defaults):
            if getattr(o, ext.fk + '_id'):   # an existing partner was chosen: its master fields fill the new record
                p = Partner.objects.filter(pk=getattr(o, ext.fk + '_id')).first()
                if p:
                    for ef, pf, tmap in ext.fields:
                        att = o._meta.get_field(ef).attname
                        if PT._empty(getattr(o, att)):
                            setattr(o, att, PT._fit(ext.model, ef, PT._from_partner_value(PT._pval(p, pf), tmap)))
            if orig_defaults:
                orig_defaults(o, request)

        reg.check, reg.defaults = check, defaults

    # a blocked partner gets no new business: new leases, quotations, purchase orders… that point at its app record are refused
    ext_models = {e.model: e for e in PT.EXTS}
    for reg in app.regs.values():
        if getattr(reg, '_mst_block', False):
            continue
        fks = [f for f in reg.model._meta.concrete_fields if getattr(f, 'related_model', None) in ext_models and not ext_models[f.related_model].ref]
        if not fks or reg.model in ext_models:
            continue
        reg._mst_block = True
        prev = reg.check

        def block_check(o, request, old, fks=fks, prev=prev, ext_models=ext_models):
            if old is None:
                for f in fks:
                    rid = getattr(o, f.attname)
                    if not rid:
                        continue
                    e = ext_models[f.related_model]
                    x = f.related_model.objects.filter(pk=rid).select_related(e.fk).first()
                    p = getattr(x, e.fk, None) if x else None
                    if p is not None and p.status == 'blocked':
                        raise AppError(f'{p.name} ({p.number}) is blocked in Business partners: {p.blocked_reason or "no reason given"}. '
                                       'No new business until a manager unblocks it.', f.name)
            if prev:
                prev(o, request, old)

        reg.check = block_check
    attach_credit(app)   # v1.44.0


# ------------------------------------------------------------------ v1.44.0 credit control on new business
def _crm_total(o):
    """The order total before the CRM saver works it out (lines: qty × price less discounts, plus the line tax)."""
    from decimal import Decimal
    from .models import TaxCode
    rates = dict(TaxCode.objects.values_list('pk', 'rate'))
    head = Decimal(str(o.discount_pct or 0)) / 100
    tot = Decimal('0')
    for it in o.items or []:
        try:
            net = Decimal(str(it.get('qty') or 0)) * Decimal(str(it.get('unit_price') or 0)) * (1 - Decimal(str(it.get('discount_pct') or 0)) / 100) * (1 - head)
            rate = Decimal(str(rates.get(it.get('tax')) or it.get('tax_rate') or 0))
            tot += net * (1 + rate / 100)
        except Exception:   # noqa: BLE001 – a bad line is reported by the CRM check
            continue
    return tot


def _p(x, fk='partner'):
    return getattr(x, fk) if (x is not None and getattr(x, fk + '_id', None)) else None


CREDIT_REGS = {
    ('zeo-property', 'lease'): lambda o: (_p(o.party) if o.party_id else None, 0),
    ('zeo-property', 'sale'): lambda o: (_p(o.party) if o.party_id else None, 0),
    ('zeo-property', 'bill'): lambda o: ((_p(o.party) if o.party_id else (_p(o.owner) if o.owner_id else None)),
                                         0 if o.is_credit else (o.amount or 0) + (o.vat or 0)),
    ('zeo-crm', 'sales_order'): lambda o: ((_p(o.account) if o.account_id else None) or (_p(o.contact) if o.contact_id else None), _crm_total(o)),
}


def credit_guard(partner, amount, request, privileged):
    """Raise the credit warning (or block) for new business of `partner`; 'save anyway' sends force_credit."""
    from zeo.appkit.engine import AppError
    from . import ledger
    prob = ledger.credit_problem(partner, amount)
    if not prob:
        return
    force = str(request.data.get('force_credit', '')).lower() in ('1', 'true', 'yes')
    can_force = prob['mode'] == 'warn' or privileged
    if force and can_force:
        return
    prob['can_force'] = can_force
    msg = ' '.join(prob['reasons']) + (' Save again to go on.' if can_force else ' A manager can go on, or release the credit hold.')
    raise AppError(msg, None, data={'credit': {k: (float(v) if hasattr(v, 'quantize') else v) for k, v in prob.items()}})


def attach_credit(app):
    for (slug, kind), fn in CREDIT_REGS.items():
        if slug != app.slug or kind not in app.regs:
            continue
        reg = app.regs[kind]
        if getattr(reg, '_credit', False):
            continue
        reg._credit = True
        prev = reg.check

        def credit_check(o, request, old, fn=fn, prev=prev, reg=reg):
            if prev:
                prev(o, request, old)
            if old is None:
                partner, amount = fn(o)
                if partner is not None:
                    credit_guard(partner, amount, request, _privileged(app, reg, request))

        reg.check = credit_check
