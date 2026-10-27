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
