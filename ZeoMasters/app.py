"""v1.41.0 – ZEO Masters: business partners (individuals and companies), their bank accounts and the possible-duplicates list.

Not an app the user switches on: it sits under every business app (CRM, Property, …, later Accounting). Its pages are opened
from each app's menu (Business partners, Possible duplicates) and keep that app's menu.

Rights
  view    every user with a role in any business app (they pick partners on their own forms), the Masters roles, admins
  edit    Masters manager, the manager of any business app, admins
  merge   Masters manager, admins
"""
from django.db.models import Q

from zeo.appkit.engine import Act, App, AppError, Reg, ctx, need, now, user_name

from . import partners as PT
from .models import (MASTER_ROLES, UOM, DuplicatePair, MasterEvent, MasterFile, MasterSequence, MasterSettings, Partner, PartnerBank, Product,
                     ProductCategory, TaxCode)
from . import items as ITM
from . import accounting as ACC   # v1.44.0
from . import ledger as LG   # v1.44.0


class MastersApp(App):
    """Roles of the other kit apps count here ('any_app', 'app_manager')."""

    def code(self, role):
        return role

    def has(self, request, role):
        c = ctx(request)
        if not c:
            return False
        if c.admin:
            return True
        if role == 'any_app':
            return any(code in c.codes for code in _all_codes()) or 'mst_viewer' in c.codes or 'mst_manager' in c.codes
        if role == 'app_manager':
            return any(code in c.codes for code in _manager_codes())
        return role in c.codes


def _apps():
    from zeo.appkit.engine import REGISTRY
    return [a for a in REGISTRY.values() if a.label != 'ZeoMasters']


def _all_codes():
    out = set()
    for a in _apps():
        out.update(k for k, _ in a.roles)
    out.update(_other_app_codes()[0])
    return out


def _manager_codes():
    return {a.manager_role for a in _apps() if a.manager_role} | _other_app_codes()[1]


def _other_app_codes():
    """v1.42.0: apps not built on the kit – HSE roles and the Projects customer rights also see / manage partners."""
    view, manage = {'view_customer', 'add_customer', 'change_customer'}, {'change_customer'}
    try:
        from ZeoHSE.models import ROLES as HSE_ROLES
        view.update(k if k.startswith('hse_') else 'hse_' + k for k, _ in HSE_ROLES)
        manage.add('hse_manager')
    except Exception:
        pass
    return view, manage


app = MastersApp(
    label='ZeoMasters', slug='zeo-masters', title='Masters', settings_model=MasterSettings, sequence_model=MasterSequence,
    event_model=MasterEvent, file_model=MasterFile, roles=MASTER_ROLES, role_prefix='mst_', manager_role='mst_manager',
    packs={'general': {'label': 'General', 'description': 'No pack items'}}, pack_labels={'general': 'General'},
    pack_descriptions={'general': 'No pack items'}, industry_from_company={}, pack_models={},
    page='/main-sidebar/zeo-masters/',
)
app.settings_fields = ('duplicate_check', 'lock_shared', 'credit_control', 'credit_overdue_days')   # v1.44.0 credit control

EDIT = ('mst_manager', 'app_manager')
VIEW = ('any_app',)


def can_merge(request):
    return app.has(request, 'mst_manager')


# ------------------------------------------------------------------ options (pickers)
def partner_label(x):
    extra = x.mobile or x.email or x.tax_id
    return f'{x.name} ({x.number})' + (f' · {extra}' if extra else '')


def active_partners():
    return Partner.objects.filter(merged_into__isnull=True).exclude(status='archived')


app.option('partners', active_partners, partner_label, ('name', 'number', 'mobile', 'email', 'tax_id', 'id_number', 'search_keys'), Partner)
app.option('companies', lambda: active_partners().filter(kind='company'), partner_label, ('name', 'number', 'tax_id'))
app.provide('masters.partners', active_partners, partner_label, ('name', 'number', 'mobile', 'email', 'tax_id'))


# ------------------------------------------------------------------ checks
def partner_check(o, request, old):
    s = app.settings()
    if o.kind == 'individual':
        if not o.name.strip():
            o.name = ' '.join(x for x in (o.first_name, o.middle_name, o.last_name) if x).strip()
        if not (o.first_name or o.last_name) and o.name:
            o.first_name, o.middle_name, o.last_name = (x[:80] for x in PT.split_name(o.name))
    else:
        o.first_name = o.middle_name = o.last_name = ''
        o.gender, o.date_of_birth = '', None
    need(o.name.strip(), 'Enter the name.')
    if o.parent_id:
        need(o.parent_id != o.pk, 'A partner cannot be its own company.')
        need(o.parent.kind == 'company', 'The company of a contact must be a company partner.')
    if o.email:
        o.email = o.email.strip().lower()
    codes = PT.role_codes(o)
    bad = [c for c in codes if c not in PT.ROLE_LABELS]
    if bad:
        raise AppError(f'Unknown role: {", ".join(bad)}.', 'roles')
    PT.set_roles(o, codes)
    if o.status == 'blocked' and not o.blocked_reason:
        raise AppError('Say why the partner is blocked.', 'blocked_reason')
    # v1.44.0 tax number by country, e-invoice defaults
    o.country_code = (o.country_code or '').strip().upper()[:2]
    bad = ACC.clean_tax_id(o)
    if bad and (old is None or old.tax_id != o.tax_id or (old.country_code or '') != o.country_code):
        raise AppError(bad, 'tax_id')
    ACC.einvoice_defaults(o)
    force = str(request.data.get('force_duplicate', '')).lower() in ('1', 'true', 'yes')
    keys = PT.keys_of(o)
    if s.duplicate_check and not force and keys and (old is None or PT.keys_of(old) != keys):
        q = Q()
        for k in keys:
            q |= Q(search_keys__contains=k + ' ')
        dup = Partner.objects.filter(q, merged_into__isnull=True).exclude(pk=o.pk or 0).exclude(status='archived').order_by('pk').first()
        if dup:
            raise AppError(f'{dup.name} ({dup.number}) already has this phone, e-mail, ID or tax number. Open it, or save again to keep both '
                           '(it will be listed under Possible duplicates).', 'email',
                           data={'duplicate': {'id': dup.pk, 'number': dup.number, 'name': dup.name}})


def partner_saver(o, request, creating, old):
    if creating and not o.number:
        o.number = PT.next_number()
    PT.save_partner(o)
    if old is not None and old.status != o.status:
        app.event('partner', o.pk, f'Status: {old.get_status_display()} → {o.get_status_display()}' + (f' ({o.blocked_reason})' if o.blocked_reason else ''),
                  request.user)
    return o


def bank_check(o, request, old):
    from EmployeeProfile.validators import clean_iban_any
    if o.iban:
        try:
            o.iban = clean_iban_any(o.iban)
        except ValueError as e:
            raise AppError(str(e), 'iban')
    need(o.iban or o.account_no, 'Enter the IBAN or the account number.')
    if o.is_default:
        PartnerBank.objects.filter(partner_id=o.partner_id).exclude(pk=o.pk or 0).update(is_default=False)


# ------------------------------------------------------------------ actions
def a_block(o, request, f):
    o.status, o.blocked_reason = 'blocked', f.s('reason')
    PT.save_partner(o)
    app.event('partner', o.pk, f'Blocked: {o.blocked_reason}', request.user)
    return o


def a_unblock(o, request, f):
    o.status, o.blocked_reason = 'active', ''
    PT.save_partner(o)
    app.event('partner', o.pk, 'Unblocked' + (f': {f.s("note")}' if f.s('note') else ''), request.user)
    return o


def a_archive(o, request, f):
    o.status = 'archived'
    PT.save_partner(o)
    app.event('partner', o.pk, 'Archived' + (f': {f.s("note")}' if f.s('note') else ''), request.user)
    return o


def a_merge_into(o, request, f):
    """Merge THIS partner into another one (this one is archived)."""
    need(can_merge(request), 'Only a Masters manager or an administrator can merge partners.')
    keep = f.obj('into', Partner, True, 'Keep partner')
    need(keep.pk != o.pk, 'Choose another partner.')
    need(not keep.merged_into_id, f'{keep.number} was itself merged – choose the partner it was merged into.')
    PT.merge(keep, o, request.user)
    app.event('partner', keep.pk, f'{o.number} ({o.name}) merged into this partner by {user_name(request.user)}', request.user)
    app.event('partner', o.pk, f'Merged into {keep.number} ({keep.name})', request.user)
    return keep


def _decide(o, request, keep, drop):
    need(can_merge(request), 'Only a Masters manager or an administrator can merge partners.')
    need(o.status == 'open', 'This pair was already decided.')
    PT.merge(keep, drop, request.user)
    o.status, o.decided_by, o.decided_at = 'merged', request.user, now()
    o.save()
    app.event('duplicate', o.pk, f'Merged: kept {keep.number}, archived {drop.number}', request.user)
    app.event('partner', keep.pk, f'{drop.number} ({drop.name}) merged into this partner (possible duplicate {o.number})', request.user)
    return o


def a_keep_first(o, request, f):
    return _decide(o, request, o.partner, o.other)


def a_keep_second(o, request, f):
    return _decide(o, request, o.other, o.partner)


def a_dismiss(o, request, f):
    need(app.any(request, *EDIT), 'Only a manager can decide on duplicates.')
    o.status, o.decided_by, o.decided_at = 'dismissed', request.user, now()
    o.save()
    app.event('duplicate', o.pk, 'Not a duplicate' + (f': {f.s("note")}' if f.s('note') else ''), request.user)
    return o


def a_rescan(o, request, f):
    n = PT.scan_duplicates(o)
    app.event('partner', o.pk, f'Duplicate check: {n} new possible duplicate(s)', request.user)
    return o


def pair_extra(o, request):
    show_id = app.any(request, *EDIT)   # the ID number is restricted

    def side(p):
        return {'number': p.number, 'name': p.name, 'kind': p.get_kind_display(), 'mobile': p.mobile, 'email': p.email,
                'id_number': p.id_number if show_id else ('(restricted)' if p.id_number else ''),
                'tax_id': p.tax_id, 'roles': p.roles_text, 'used_in': p.apps_text, 'created': p.created_at.date().isoformat()}
    return {'compare': [side(o.partner), side(o.other)]}


# ------------------------------------------------------------------ registers
PARTNER_FORM = ('kind', 'name', 'first_name', 'middle_name', 'last_name', 'arabic_name', 'legal_name', 'company_name', 'parent', 'contact_type',
                'job_title', 'gender', 'date_of_birth', 'nationality', 'id_type', 'id_number', 'id_expiry', 'tax_id', 'licence_no', 'licence_expiry',
                'registration_authority', 'mobile', 'phone', 'email', 'other_phones', 'website', 'address', 'city', 'country', 'addresses',
                'street', 'building_no', 'additional_no', 'district', 'postal_code', 'state', 'country_code',
                'preferred_language', 'preferred_channel', 'roles', 'tags', 'payment_term', 'price_book', 'currency', 'credit_limit', 'employee',
                'einvoice_channel', 'einvoice_id', 'einvoice_scheme', 'buyer_id_type', 'buyer_id',
                'accounting_ref', 'consent', 'consent_on', 'retain_until', 'notes')

FINANCE = ('mst_manager', 'mst_viewer', 'app_manager')   # v1.44.0: who sees balances and statements


def can_finance(request):
    return app.any(request, *FINANCE)


def a_credit_hold(o, request, f):
    need(f.s('reason'), 'Say why.')
    o.credit_hold, o.credit_hold_reason = True, f.s('reason')[:200]
    Partner.objects.filter(pk=o.pk).update(credit_hold=True, credit_hold_reason=o.credit_hold_reason, updated_at=now())
    app.event('partner', o.pk, f'Put on credit hold by {user_name(request.user)}: {o.credit_hold_reason}', request.user)
    return o


def a_credit_release(o, request, f):
    o.credit_hold, o.credit_hold_reason = False, ''
    Partner.objects.filter(pk=o.pk).update(credit_hold=False, credit_hold_reason='', updated_at=now())
    app.event('partner', o.pk, f'Credit hold released by {user_name(request.user)}' + (f': {f.s("note")}' if f.s('note') else ''), request.user)
    return o


def partner_extra(o, request):
    out = {'einvoice': {'country': ACC.country_code_of(o), 'missing': ACC.einvoice_missing(o)}}
    if can_finance(request) and not o.merged_into_id:
        b = LG.balances(o)
        out['balances'] = {k: (float(v) if hasattr(v, 'quantize') else v) for k, v in b.items() if k not in ('partner', 'ageing')}
        out['balances']['ageing'] = {k: float(v) for k, v in b['ageing'].items()}
        out['balances']['company'] = b['partner'].name if b['partner'].pk != o.pk else ''
        out['statement'] = b['partner'].pk
    return out

preg = app.reg(Reg(
    'partner', Partner, 'Business partner', 'Business partners', 'Partners',
    cols=('number', 'name', 'kind', 'roles_text', 'mobile', 'email', 'tax_id', 'apps_text', 'balance_receivable', 'balance_overdue', 'status'),
    form=PARTNER_FORM, number=None, manage=EDIT, view=VIEW, create=('any_app',),
    restricted=('id_number', 'date_of_birth', 'balance_receivable', 'balance_payable', 'balance_overdue'),
    can_restricted=lambda request, obj=None: app.any(request, *EDIT),
    extra=partner_extra,
    search=('number', 'name', 'arabic_name', 'legal_name', 'mobile', 'phone', 'email', 'tax_id', 'licence_no', 'search_keys', 'used_in'),
    filters=('kind', 'status', 'contact_type', 'nationality', 'country', 'credit_hold', 'einvoice_channel'),
    title='name', subtitle='number', check=partner_check, saver=partner_saver,
    tables={'roles': [('role', 'Role', 'choice', PT.ROLE_CHOICES)],
            'other_phones': [('label', 'Label', 'text'), ('number', 'Number', 'text')],
            'addresses': [('label', 'Label', 'text'), ('address', 'Address', 'text'), ('country', 'Country', 'text')],
            'used_in': [('app', 'App', 'text'), ('record', 'Record', 'text'), ('number', 'Number', 'text'), ('name', 'Name', 'text'),
                        ('roles', 'Role', 'text'), ('active', 'Active', 'text')]},
    info=('used_in', 'status', 'blocked_reason', 'credit_hold', 'credit_hold_reason', 'merged_into', 'updated_at'),
    children=(('People and addresses', 'partner', 'parent'), ('Bank accounts', 'bank', 'partner')),
    actions=[
        Act('block', 'Block', a_block, lambda o, r: o.status == 'active' and app.any(r, *EDIT), style='danger',
            fields=(('reason', 'Why (shown in every app)', 'text', True),)),
        Act('unblock', 'Unblock', a_unblock, lambda o, r: o.status != 'active' and not o.merged_into_id and app.any(r, *EDIT),
            fields=(('note', 'Note', 'text', False),)),
        Act('archive', 'Archive', a_archive, lambda o, r: o.status == 'active' and app.any(r, *EDIT), fields=(('note', 'Note', 'text', False),),
            confirm='Archived partners are hidden from pickers; their records stay.'),
        Act('merge', 'Merge into another partner', a_merge_into, lambda o, r: not o.merged_into_id and can_merge(r), style='danger',
            fields=(('into', 'Keep this partner (the other one is archived)', 'opt:partners', True),),
            confirm='Every lease, contact, invoice and document of this partner moves to the partner you keep. This cannot be undone automatically.'),
        Act('rescan', 'Check for duplicates', a_rescan, lambda o, r: app.any(r, *EDIT)),
        Act('credit_hold', 'Put on credit hold', a_credit_hold, lambda o, r: not o.credit_hold and not o.merged_into_id and app.any(r, *EDIT), style='danger',
            fields=(('reason', 'Why (shown when new business is made)', 'text', True),)),
        Act('credit_release', 'Release credit hold', a_credit_release, lambda o, r: o.credit_hold and app.any(r, *EDIT), fields=(('note', 'Note', 'text', False),)),
    ],
    help='People and companies the business deals with – one record each, used by every app (Odoo: contacts / res.partner). '
         'Each app keeps its own details (lease, pipeline, SLA…) in its own record.'))

app.reg(Reg('bank', PartnerBank, 'Bank account', 'Bank accounts', 'Partners',
            cols=('partner', 'bank', 'account_name', 'iban', 'swift', 'currency', 'is_default'),
            form=('partner', 'bank', 'account_name', 'account_no', 'iban', 'swift', 'currency', 'is_default'),
            manage=EDIT, view=('mst_manager', 'app_manager'), create='manage', child_of='partner', check=bank_check,
            search=('bank', 'iban', 'account_no', 'partner__name', 'partner__number'), filters=('currency',), title='bank', subtitle='partner',
            help='Payouts, refunds and supplier payments. Seen and changed by managers only.'))

app.reg(Reg('duplicate', DuplicatePair, 'Possible duplicate', 'Possible duplicates', 'Partners',
            cols=('number', 'partner', 'other', 'reason', 'score', 'status'),
            form=(), readonly=('partner', 'other', 'reason', 'score'), manage=EDIT, view=VIEW, create='none',
            search=('number', 'partner__name', 'other__name', 'partner__number', 'other__number', 'reason'), filters=('status', 'reason'),
            title='number', subtitle='reason', extra=pair_extra, edit_when=lambda o: False, info=('partner', 'other', 'reason', 'score', 'status', 'decided_by', 'decided_at'),
            actions=[
                Act('keep_first', 'Merge – keep the first', a_keep_first, lambda o, r: o.status == 'open' and can_merge(r), style='primary',
                    confirm='The second partner is archived and all its records move to the first. This cannot be undone automatically.'),
                Act('keep_second', 'Merge – keep the second', a_keep_second, lambda o, r: o.status == 'open' and can_merge(r),
                    confirm='The first partner is archived and all its records move to the second. This cannot be undone automatically.'),
                Act('dismiss', 'Not a duplicate', a_dismiss, lambda o, r: o.status == 'open' and app.any(r, *EDIT), fields=(('note', 'Why', 'text', False),)),
            ],
            help='Partners that share a phone, e-mail, ID or tax number. A person decides – nothing is merged on a guess.'))


# ------------------------------------------------------------------ v1.43.0 items (the item master)
def item_label(x):
    return f'{x.name} ({x.code or x.number})' + (f' · {x.get_kind_display()}' if x.kind not in ('goods',) else '')


app.option('items', lambda: Product.objects.filter(is_active=True), item_label, ('name', 'code', 'number', 'barcode', 'manufacturer_no'), Product)
app.option('uoms', lambda: UOM.objects.filter(is_active=True), lambda x: f'{x.name} ({x.code})', ('code', 'name'), UOM)
app.option('item-categories', lambda: ProductCategory.objects.filter(is_active=True), lambda x: x.name, ('code', 'name'), ProductCategory)
app.option('tax-codes', lambda: TaxCode.objects.filter(is_active=True), str, ('code', 'name'), TaxCode)


def item_check(o, request, old):
    force = str(request.data.get('force_duplicate', '')).lower() in ('1', 'true', 'yes')
    if not force:
        for f, label in (('code', 'code'), ('barcode', 'barcode')):
            v = (getattr(o, f) or '').strip()
            if v and (old is None or getattr(old, f) != v):
                dup = Product.objects.filter(**{f + '__iexact': v}).exclude(pk=o.pk or 0).first()
                if dup:
                    raise AppError(f'{dup.name} ({dup.number or dup.code}) already has this {label}. Open it, or save again to keep both.', f,
                                   data={'duplicate': {'id': dup.pk, 'number': dup.number or dup.code, 'name': dup.name}})
    if o.kind == 'service':
        o.track_stock = False


def item_defaults(o, request):
    if not o.tax_id:
        o.tax = TaxCode.objects.filter(is_default=True, is_active=True).first()
    if not o.uom_id and o.kind in ('goods', 'spare_part', 'consumable', 'asset_model'):
        o.uom = UOM.objects.filter(code__iexact='pcs').first() or UOM.objects.filter(base__isnull=True).first()


def item_saver(o, request, creating, old):
    if creating and not o.number:
        o.number = app.next_number('item', 'ITM')
    ITM.save_item(o)
    return o


def a_item_merge(o, request, f):
    need(app.any(request, *EDIT), 'Only a manager can merge items.')
    keep = f.obj('into', Product, True, 'Keep item')
    need(keep.pk != o.pk, 'Choose another item.')
    ITM.merge(keep, o)
    app.event('item', keep.pk, f'{o.number or o.code} ({o.name}) merged into this item by {user_name(request.user)}', request.user)
    app.event('item', o.pk, f'Merged into {keep.number or keep.code} ({keep.name})', request.user)
    return keep


ITEM_FORM = ('kind', 'code', 'name', 'arabic_name', 'category', 'uom', 'barcode', 'is_sold', 'is_purchased', 'sale_price', 'min_price', 'tax',
             'cost_price', 'purchase_price', 'preferred_supplier', 'supplier_code', 'lead_time_days', 'manufacturer', 'manufacturer_no',
             'track_stock', 'min_qty', 'reorder_qty', 'valuation', 'standard_cost', 'hs_code', 'country_of_origin', 'weight_kg',
             'income_account', 'expense_account', 'stock_account', 'tax_category', 'exemption_reason', 'tags', 'description', 'is_active')

app.reg(Reg('item', Product, 'Item', 'Items', 'Items',
            cols=('number', 'code', 'name', 'kind', 'category', 'uom', 'sale_price', 'preferred_supplier', 'apps_text', 'is_active'),
            form=ITEM_FORM, manage=EDIT, view=VIEW, create=('any_app',), restricted=('cost_price', 'purchase_price', 'standard_cost', 'stock_value'),
            can_restricted=lambda request, obj=None: app.any(request, *EDIT),
            search=('number', 'code', 'name', 'arabic_name', 'barcode', 'manufacturer_no', 'supplier_code'),
            filters=('kind', 'category', 'is_sold', 'is_purchased', 'track_stock', 'is_active'),
            title='name', subtitle='code', check=item_check, defaults=item_defaults, saver=item_saver,
            tables={'used_in': [('app', 'App', 'text'), ('record', 'Record', 'text'), ('number', 'Number', 'text'), ('name', 'Name', 'text')]},
            info=('used_in', 'stock_qty', 'stock_value', 'updated_at'),
            actions=[Act('merge', 'Merge into another item', a_item_merge, lambda o, r: o.is_active and app.any(r, *EDIT), style='danger',
                         fields=(('into', 'Keep this item (this one is deactivated)', 'opt:items', True),),
                         confirm='Every spare part, charge type, quotation line and price of this item moves to the item you keep.')],
            help='Goods, services, spare parts, consumables, asset models, charges and fees – one list for every app (Odoo: products). '
                 'Stock stays in the apps that keep stores; costs are restricted to managers.'))
app.reg(Reg('uom', UOM, 'Unit of measure', 'Units of measure', 'Items', cols=('code', 'name', 'category', 'base', 'factor', 'unece_code', 'is_active'),
            form=('code', 'name', 'category', 'base', 'factor', 'unece_code', 'is_active'), manage=EDIT, view=VIEW, search=('code', 'name'),
            filters=('category', 'is_active'), title='name', subtitle='code', files=False))
app.reg(Reg('item_category', ProductCategory, 'Item category', 'Item categories', 'Items', cols=('code', 'name', 'parent', 'is_active'),
            form=('code', 'name', 'parent', 'is_active'), manage=EDIT, view=VIEW, search=('code', 'name'), filters=('is_active',), title='name', files=False))
app.reg(Reg('tax_code', TaxCode, 'Tax code', 'Tax codes', 'Items', cols=('code', 'name', 'kind', 'rate', 'inclusive', 'is_default', 'is_active'),
            form=('code', 'name', 'kind', 'rate', 'inclusive', 'is_default', 'is_active'), manage=EDIT, view=VIEW, search=('code', 'name'),
            filters=('kind', 'is_active'), title='name', subtitle='code', files=False))


# ------------------------------------------------------------------ dashboard / my work / daily
def dashboard(request):
    qs = active_partners()
    return {'cards': [
        {'label': 'Business partners', 'value': qs.count()},
        {'label': 'Companies', 'value': qs.filter(kind='company').count()},
        {'label': 'Individuals', 'value': qs.filter(kind='individual').count()},
        {'label': 'Blocked', 'value': qs.filter(status='blocked').count()},
        {'label': 'Possible duplicates', 'value': DuplicatePair.objects.filter(status='open').count()},
    ]}


def my_work(request):
    if not app.any(request, *EDIT):
        return []
    rows = [{'title': f'{x.partner.name} ↔ {x.other.name}', 'sub': x.reason, 'kind': 'duplicate', 'id': x.pk}
            for x in DuplicatePair.objects.filter(status='open').select_related('partner', 'other')[:20]]
    return [{'title': 'Possible duplicates to review', 'rows': rows}] if rows else []


def daily():
    n = PT.link_all()
    m = ITM.link_all()   # v1.43.0
    b = LG.refresh_all()   # v1.44.0 balances
    v = ACC.valuation()    # v1.44.0 stock value of items
    return {'linked': n, 'items_linked': m, 'balances': b, 'stock_value': float(v['total'])}


app.dashboard, app.my_work, app.daily = dashboard, my_work, daily
