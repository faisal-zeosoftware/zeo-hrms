"""v1.33.0 – Shared masters of the ZEO business apps (the Odoo `product` / `res.partner` idea).

CRM uses them today (accounts, products, price books, tax codes, payment terms on quotations and sales orders); the Sales,
Inventory, Purchase and Accounting apps of later releases use the SAME tables, so nothing is re-keyed when they arrive.
Masters are company-wide (no branch): every branch sells the same products to the same accounts.
"""
from django.db import models

EMP = 'EmpManagement.emp_master'
MONEY = dict(max_digits=14, decimal_places=2)


class UOM(models.Model):
    """Unit of measure. A unit may be a multiple of a base unit (1 box = 12 pcs → base = pcs, factor = 12)."""
    code = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=60)
    category = models.CharField(max_length=10, default='unit', choices=(('unit', 'Units'), ('weight', 'Weight'), ('length', 'Length'), ('area', 'Area'),
                                                                        ('volume', 'Volume'), ('time', 'Time'), ('other', 'Other')))
    base = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='multiples', verbose_name='base unit',
                             help_text='Leave empty for a base unit (pcs, kg, m). A box of 12 pcs: base = pcs, factor = 12')
    factor = models.DecimalField('units of the base unit', max_digits=12, decimal_places=4, default=1)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['category', 'name']
        verbose_name = 'unit of measure'
        verbose_name_plural = 'units of measure'

    def __str__(self):
        return self.name

    def to_base(self, qty):
        return qty * self.factor if self.base_id else qty


class TaxCode(models.Model):
    code = models.CharField(max_length=15, unique=True)
    name = models.CharField(max_length=80)
    kind = models.CharField(max_length=8, default='vat', choices=(('vat', 'VAT'), ('gst', 'GST'), ('excise', 'Excise'), ('none', 'No tax')))
    rate = models.DecimalField('rate %', max_digits=6, decimal_places=3, default=0)
    inclusive = models.BooleanField('prices include tax', default=False)
    country = models.CharField(max_length=2, blank=True, help_text='ISO code of the country pack that seeded it; empty = made by the company')
    is_default = models.BooleanField('default for new products', default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['-is_default', 'rate', 'name']

    def __str__(self):
        return f'{self.name} ({self.rate:g}%)' if self.rate else self.name


class PaymentTerm(models.Model):
    code = models.CharField(max_length=15, unique=True)
    name = models.CharField(max_length=80)
    days = models.PositiveSmallIntegerField('due after (days)', default=30)
    kind = models.CharField(max_length=10, default='net', choices=(('net', 'Net days after invoice'), ('advance', 'Advance / before delivery'),
                                                                   ('eom', 'End of month + days'), ('cheques', 'Post-dated cheques')))
    instalments = models.PositiveSmallIntegerField('number of payments', default=1)
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['-is_default', 'days', 'name']

    def __str__(self):
        return self.name


class ProductCategory(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=80)
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='children')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'product categories'

    def __str__(self):
        return self.name


class Product(models.Model):
    """Goods or a service. Stock quantities are NOT kept here – the Inventory app adds its own stock tables per location."""
    number = models.CharField(max_length=20, unique=True, blank=True)
    code = models.CharField('SKU / internal code', max_length=40, blank=True)
    name = models.CharField(max_length=150)
    kind = models.CharField(max_length=8, default='goods', choices=(('goods', 'Goods'), ('service', 'Service'), ('bundle', 'Bundle / kit')))
    category = models.ForeignKey(ProductCategory, null=True, blank=True, on_delete=models.SET_NULL, related_name='products')
    uom = models.ForeignKey(UOM, null=True, blank=True, on_delete=models.SET_NULL, verbose_name='unit')
    barcode = models.CharField(max_length=40, blank=True)
    sale_price = models.DecimalField('list price', default=0, **MONEY)
    cost_price = models.DecimalField('cost', default=0, **MONEY)
    tax = models.ForeignKey(TaxCode, null=True, blank=True, on_delete=models.SET_NULL, verbose_name='sales tax')
    track_stock = models.BooleanField('track stock (Inventory app)', default=False)
    min_price = models.DecimalField('minimum selling price', null=True, blank=True, **MONEY, help_text='A quotation line below this needs approval')
    description = models.TextField(blank=True, help_text='Printed on quotations')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.code} {self.name}'.strip()


class PriceBook(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=80)
    currency = models.CharField(max_length=3, default='AED')
    kind = models.CharField(max_length=8, default='fixed', choices=(('fixed', 'Fixed prices per product'), ('markup', '% on the list price')))
    percent = models.DecimalField('% on the list price', max_digits=6, decimal_places=2, default=0, help_text='-10 = 10% below the list price (kind = % on the list price)')
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['-is_default', 'name']

    def __str__(self):
        return self.name

    def price_of(self, product, qty=1):
        """Unit price of the product in this book, or None when the book has no price for it."""
        if self.kind == 'markup':
            return (product.sale_price * (100 + self.percent) / 100).quantize(product.sale_price.__class__('0.01'))
        best = None
        for it in self.items.filter(product=product).order_by('-min_qty'):
            if qty >= it.min_qty:
                best = it.price
                break
        return best


class PriceBookItem(models.Model):
    price_book = models.ForeignKey(PriceBook, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='prices')
    min_qty = models.DecimalField('from quantity', max_digits=12, decimal_places=2, default=1)
    price = models.DecimalField(**MONEY)

    class Meta:
        ordering = ['product__name', 'min_qty']
        unique_together = [('price_book', 'product', 'min_qty')]


ACCOUNT_KINDS = (('customer', 'Customer'), ('prospect', 'Prospect'), ('supplier', 'Supplier'), ('both', 'Customer and supplier'), ('partner', 'Partner / broker'))


class Account(models.Model):
    """A company you deal with (Odoo: res.partner is_company). CRM contacts are the people in it."""
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
                               help_text='Branch that owns the account; empty = every branch')
    number = models.CharField(max_length=20, unique=True, blank=True)
    name = models.CharField(max_length=150)
    legal_name = models.CharField(max_length=200, blank=True)
    kind = models.CharField(max_length=10, choices=ACCOUNT_KINDS, default='customer')
    industry = models.CharField(max_length=80, blank=True)
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='subsidiaries', verbose_name='parent account')
    tax_id = models.CharField('tax registration no. (TRN / GSTIN)', max_length=30, blank=True)
    licence_no = models.CharField('trade licence no.', max_length=40, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    website = models.CharField(max_length=120, blank=True)
    billing_address = models.TextField(blank=True)
    shipping_address = models.TextField(blank=True)
    city = models.CharField(max_length=60, blank=True)
    country = models.CharField(max_length=60, blank=True)
    payment_term = models.ForeignKey(PaymentTerm, null=True, blank=True, on_delete=models.SET_NULL)
    price_book = models.ForeignKey(PriceBook, null=True, blank=True, on_delete=models.SET_NULL)
    currency = models.CharField(max_length=3, blank=True)
    credit_limit = models.DecimalField(null=True, blank=True, **MONEY)
    owner = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='account manager')
    tags = models.JSONField(default=list, blank=True, help_text='e.g. VIP, Government, Reseller')
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name
