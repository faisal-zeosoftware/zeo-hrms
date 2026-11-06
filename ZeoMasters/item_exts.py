"""v1.43.0 – app records that belong to an item (Asset Management spare parts, Property charge types, Projects service products,
HSE chemicals) and the asset-model picker on assets. Imported by ZeoMasters.apps.ready(); `attach(app)` at the end of each kit app."""
from . import items as IT
from .items import IExt, register, uom_id, uom_text

AST_PART = register(IExt(
    'zeo-assets', 'Asset Management', 'ZeoAssets.Part', 'part', 'Spare part',
    fields=[('code', 'code', None, None, 'both'), ('name', 'name', None, None, 'both'), ('unit', 'uom', uom_id, uom_text, 'both'),
            ('manufacturer_no', 'manufacturer_no', None, None, 'both'), ('supplier_partner', 'preferred_supplier', None, None, 'both'),
            ('min_qty', 'min_qty', None, None, 'both'), ('reorder_qty', 'reorder_qty', None, None, 'both'),
            ('unit_cost', 'cost_price', None, None, 'up')],      # average cost comes from the stock moves
    kind=lambda o: 'consumable' if o.is_consumable else 'spare_part', match_code='code',
    from_item=lambda it: {'is_consumable': it.kind == 'consumable'} if it.kind in ('consumable', 'spare_part') else {}))

PROP_CHARGE = register(IExt(
    'zeo-property', 'Property', 'ZeoProperty.ChargeType', 'charge', 'Charge type',
    fields=[('code', 'code', None, None, 'both'), ('name', 'name', None, None, 'both')],
    kind=lambda o: 'charge', match_code='code'))

PJ_SERVICE = register(IExt(
    'zeo-projects', 'Projects', 'ZeoProjects.ServiceProduct', 'service', 'Service product',
    fields=[('name', 'name', None, None, 'both')],
    kind=lambda o: 'service', url=lambda o: '/main-sidebar/zeo-projects/setup'))

HSE_CHEMICAL = register(IExt(
    'zeo-hse', 'HSE', 'ZeoHSE.Chemical', 'chemical', 'Chemical (SDS)',
    fields=[('name', 'name', None, None, 'both'), ('product_code', 'supplier_code', None, None, 'both'), ('unit', 'uom', uom_id, uom_text, 'both')],
    kind=lambda o: 'consumable', url=lambda o: f'/main-sidebar/zeo-hse/r/chemicals/{o.pk}'))

AST_MODEL = register(IExt(
    'zeo-assets', 'Asset Management', 'ZeoAssets.AssetCard', 'asset', 'Asset', fields=[], kind=lambda o: 'asset_model',
    fk='model_item', ref=True, plural='Assets of this model'))


# v1.67.4 (masters): the items of the other current apps. Existing records are linked by `python manage.py link_items` (dry run first);
# a new or changed record links itself on save like a spare part.
COST_KIND = {'labour': 'service', 'material': 'spare_part', 'vendor': 'service', 'other': 'goods'}

EXP_CATEGORY = register(IExt(
    'expenses', 'Expenses', 'ExpenseManagement.ExpenseCategory', 'category', 'Expense category',
    fields=[('code', 'code', None, None, 'both'), ('name', 'name', None, None, 'both'), ('gl_account', 'expense_account', None, None, 'both')],
    kind=lambda o: 'service', match_code='code', auto=False, new={'is_sold': False, 'is_purchased': True},
    url=lambda o: '/main-sidebar/expense-options'))

HR_ASSET_TYPE = register(IExt(
    'asset-plus', 'HR assets', 'AssetPlus.AssetTypeConfig', 'asset_type', 'Asset type', fields=[],
    kind=lambda o: 'asset_model', auto=False, new={'is_sold': False}, url=lambda o: '/main-sidebar/asset-options/asset-type'))

MNT_COST = register(IExt(
    'zeo-maintenance', 'Maintenance', 'ZeoMaintenance.CostLine', 'cost', 'Cost line', fields=[],
    kind=lambda o: COST_KIND.get(o.kind, 'goods') if o is not None else 'goods', ref=True, plural='Work order cost lines', auto=False,
    fill={'description': lambda it: it.name, 'rate': lambda it: it.cost_price or it.purchase_price or 0}))


def asset_type_saved(sender, instance, created, raw=False, **kw):
    """v1.67.4 (masters) an HR asset type (core table OrganisationManager.AssetType) is saved: its side record in AssetPlus
    (AssetTypeConfig, the same one asset numbering makes) gets its item; a rename renames the item (one way – the core table
    is never written from the item)."""
    if raw:
        return
    try:
        from django.apps import apps as _apps
        from AssetPlus.services import type_config
        cfg = type_config(instance.pk)
        cfg.refresh_from_db(fields=['item'])
        if not cfg.item_id:
            cfg.save()   # the item link is made by the item sync (post_save of AssetTypeConfig)
        else:
            it = _apps.get_model('ZeoMasters', 'Product').objects.filter(pk=cfg.item_id).first()
            if it and instance.name and it.name != instance.name[:150]:
                it.name = instance.name[:150]
                IT.save_item(it)
    except Exception:   # noqa: BLE001 – never stops saving the asset type
        IT.log.exception('HR asset type → item failed for %s', instance.pk)


def attach(app):
    """Item picker on the app's own forms; choosing an item fills the record's name, code, unit…"""
    from .models import Product
    from .app import item_label
    app.option('items', lambda: Product.objects.filter(is_active=True), item_label, ('name', 'code', 'number', 'barcode', 'manufacturer_no'), Product)
    for ext in IT.IEXTS:
        if ext.app != app.slug or ext.reg not in app.regs:
            continue
        reg = app.regs[ext.reg]
        if getattr(reg, '_itm', False):
            continue
        reg._itm = True
        if ext.fk not in reg.form:
            reg.form = (ext.fk,) + tuple(reg.form)
        if ext.ref:
            if ext.fill:   # v1.67.4 (masters): picking an item fills the empty fields of the line (description, rate)
                prev = reg.defaults

                def ref_defaults(o, request, ext=ext, prev=prev):
                    iid = getattr(o, ext.fk + '_id')
                    it = Product.objects.filter(pk=iid).first() if iid else None
                    if it is not None:
                        for af, fn in ext.fill.items():
                            if IT._empty(getattr(o, af)) or getattr(o, af) == 0:
                                setattr(o, af, IT._fit(ext.model, af, fn(it)))
                    if prev:
                        prev(o, request)
                reg.defaults = ref_defaults
            continue
        orig = reg.defaults

        def defaults(o, request, ext=ext, orig=orig):
            iid = getattr(o, ext.fk + '_id')
            if iid:
                it = Product.objects.filter(pk=iid).first()
                if it:
                    for f in ext.fields:
                        if len(f) > 4 and f[4] == 'up':
                            continue
                        att = IT._att(ext.model, f[0])
                        if IT._empty(getattr(o, att)) or (f[0] == 'unit' and getattr(o, att) == 'pcs'):
                            v = getattr(it, IT._att(Product, f[1]))
                            setattr(o, att, IT._fit(ext.model, f[0], f[3](v) if f[3] else v))
            if orig:
                orig(o, request)
        reg.defaults = defaults
