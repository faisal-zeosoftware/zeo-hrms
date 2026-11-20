"""Accounting packs: the UAE chart of accounts (codes that the other ZEO apps already post to: Inventory 1400 / 2150 / 5000 …, Payroll
accruals, Property VAT output 2210), the UAE VAT codes and the standard journals. IFRS presentation; corporate-tax treatment per account."""


def a(code, name, group, **kw):
    return {'code': code, 'name': name, 'group': group, **kw}


UAE_COA = [
    a('1000', 'Cash in hand', 'cash', control='bank'), a('1010', 'Bank – current account', 'bank', control='bank', reconcile=True),
    a('1015', 'Card and online receipts (clearing)', 'bank', control='bank'),
    a('1200', 'Trade receivables', 'receivable', control='receivable', reconcile=True), a('1210', 'Other receivables', 'current_asset'),
    a('1250', 'Post-dated cheques received', 'current_asset'), a('1300', 'Prepayments', 'prepayment'), a('1310', 'Deposits paid', 'current_asset'),
    a('1320', 'Advances to suppliers', 'current_asset'), a('1330', 'Staff advances and loans', 'current_asset'),
    a('1350', 'VAT recoverable (input)', 'current_asset'),
    a('1400', 'Inventory', 'inventory'), a('1410', 'Stock in transit', 'inventory'), a('1450', 'Work in progress', 'inventory'),
    a('1490', 'Inventory provision', 'inventory'),
    a('1500', 'Property, plant and equipment', 'fixed_asset'), a('1510', 'Furniture and fixtures', 'fixed_asset'), a('1520', 'Vehicles', 'fixed_asset'),
    a('1530', 'Computers and IT equipment', 'fixed_asset'), a('1590', 'Accumulated depreciation', 'accum_depr'),
    a('1600', 'Intangible assets', 'non_current_asset'), a('1700', 'Right-of-use assets (leases)', 'non_current_asset'),
    a('1999', 'Suspense', 'current_asset'),
    a('2100', 'Trade payables', 'payable', control='payable', reconcile=True), a('2150', 'Goods received not invoiced (GRNI)', 'current_liability'),
    a('2155', 'Shipment clearing', 'current_liability'), a('2160', 'Landed cost clearing', 'current_liability'),
    a('2165', 'Landed cost accrual (estimates)', 'current_liability'), a('2170', 'Consignment payable', 'current_liability'),
    a('2190', 'Commissions payable', 'current_liability'), a('2200', 'Accrued expenses', 'current_liability'),
    a('2210', 'VAT output', 'tax'), a('2220', 'VAT payable to the FTA', 'tax'), a('2230', 'Corporate tax payable', 'tax'),
    a('2250', 'Post-dated cheques issued', 'current_liability'), a('2300', 'Salaries payable', 'current_liability'),
    a('2310', 'Leave salary and air ticket provision', 'current_liability'), a('2330', 'Pension contributions payable (GPSSA / ADPF)', 'current_liability'),
    a('2400', 'Customer deposits and advances', 'current_liability'), a('2500', 'Amounts due to owners', 'current_liability'),
    a('2510', 'Brokers payable', 'current_liability'), a('2600', 'Provision for end-of-service benefits (gratuity)', 'non_current_liability'),
    a('2700', 'Bank loans', 'non_current_liability'), a('2750', 'Lease liabilities', 'non_current_liability'),
    a('3000', 'Share capital', 'equity'), a('3100', 'Owners\' current account', 'equity'), a('3150', 'Statutory reserve', 'equity'),
    a('3200', 'Retained earnings', 'retained'), a('3900', 'Opening balance equity', 'equity'),
    a('4000', 'Sales revenue', 'revenue'), a('4050', 'Service revenue', 'revenue'), a('4100', 'Sales returns and allowances', 'revenue'),
    a('4900', 'Other income', 'other_income'), a('4910', 'Exchange gain', 'other_income'), a('4920', 'Dividend income', 'other_income', ct_treatment='exempt_income'),
    a('5000', 'Cost of goods sold', 'cost_of_sales'), a('5100', 'Purchases', 'cost_of_sales'), a('5110', 'Change in inventory', 'cost_of_sales'),
    a('5810', 'Stock adjustment', 'cost_of_sales'), a('5820', 'Count variance', 'cost_of_sales'), a('5830', 'Scrap and write-off', 'cost_of_sales'),
    a('5840', 'Purchase price variance', 'cost_of_sales'), a('5845', 'Cost variance', 'cost_of_sales'), a('5850', 'Inventory revaluation', 'cost_of_sales'),
    a('5860', 'Inventory write-down (NRV)', 'cost_of_sales'), a('5870', 'Internal consumption', 'cost_of_sales'),
    a('6000', 'Salaries and wages', 'expense'), a('6100', 'Allowances', 'expense'), a('6150', 'End-of-service benefits', 'expense'),
    a('6160', 'Leave salary and air tickets', 'expense'), a('6190', 'Other staff costs', 'expense'), a('6200', 'Rent', 'expense'),
    a('6300', 'Commission', 'expense'), a('6400', 'Repairs and maintenance', 'expense'), a('6500', 'Utilities', 'expense'),
    a('6550', 'Telephone and internet', 'expense'), a('6600', 'Depreciation', 'depreciation'), a('6650', 'Licences and government fees', 'expense'),
    a('6700', 'Bank charges', 'expense'), a('6750', 'Professional fees', 'expense'), a('6800', 'Entertainment', 'expense', ct_treatment='half'),
    a('6850', 'Fines and penalties', 'other_expense', ct_treatment='non_deductible'), a('6860', 'Donations', 'other_expense', ct_treatment='non_deductible'),
    a('6900', 'Finance cost (interest)', 'finance_cost'), a('6910', 'Exchange loss', 'other_expense'), a('6990', 'Rounding difference', 'other_expense'),
    a('7000', 'Other operating expenses', 'expense'), a('8900', 'Corporate tax expense', 'income_tax', ct_treatment='ct_expense'),
]

UAE_TAX = [
    {'code': 'SR', 'name': 'Standard rated 5 %', 'use': 'sale', 'treatment': 'SR', 'rate': 5, 'account': '@account:2210'},
    {'code': 'ZR', 'name': 'Zero rated (exports …)', 'use': 'sale', 'treatment': 'ZR', 'rate': 0, 'account': '@account:2210'},
    {'code': 'EX', 'name': 'Exempt', 'use': 'sale', 'treatment': 'EX', 'rate': 0},
    {'code': 'OS', 'name': 'Out of scope', 'use': 'sale', 'treatment': 'OS', 'rate': 0},
    {'code': 'SR-P', 'name': 'Standard rated purchase 5 %', 'use': 'purchase', 'treatment': 'SR', 'rate': 5, 'account': '@account:1350'},
    {'code': 'ZR-P', 'name': 'Zero rated purchase', 'use': 'purchase', 'treatment': 'ZR', 'rate': 0},
    {'code': 'EX-P', 'name': 'Exempt purchase', 'use': 'purchase', 'treatment': 'EX', 'rate': 0},
    {'code': 'RC', 'name': 'Reverse charge – imported services 5 %', 'use': 'purchase', 'treatment': 'RC', 'rate': 5, 'account': '@account:1350',
     'rc_account': '@account:2210'},
    {'code': 'IM', 'name': 'Imported goods (customs) 5 %', 'use': 'purchase', 'treatment': 'IM', 'rate': 5, 'account': '@account:1350', 'rc_account': '@account:2210'},
    {'code': 'BL', 'name': 'Blocked input tax (entertainment, motor vehicles …)', 'use': 'purchase', 'treatment': 'BL', 'rate': 5, 'recoverable': 0},
]

JOURNALS = [
    {'code': 'GJ', 'name': 'General journal', 'kind': 'general'}, {'code': 'SAL', 'name': 'Sales', 'kind': 'sale'},
    {'code': 'PUR', 'name': 'Purchases', 'kind': 'purchase'}, {'code': 'BNK', 'name': 'Bank', 'kind': 'bank', 'account': '@account:1010', 'pdc_account': '@account:1250'},
    {'code': 'CSH', 'name': 'Cash', 'kind': 'cash', 'account': '@account:1000'}, {'code': 'OPN', 'name': 'Opening balances', 'kind': 'opening'},
    {'code': 'FX', 'name': 'Exchange revaluation', 'kind': 'fx'}, {'code': 'YE', 'name': 'Year end', 'kind': 'closing'},
    {'code': 'ZEO', 'name': 'From other ZEO apps (payroll, inventory …)', 'kind': 'outbox'},
]

PACKS = {
    'uae': {'label': 'UAE – IFRS chart, VAT 5 %', 'description': 'A chart of accounts for a UAE company (the codes the other ZEO apps post to), the VAT codes for VAT 201 and the standard journals.',
            'highlights': [f'{len(UAE_COA)} accounts', f'{len(UAE_TAX)} VAT codes', f'{len(JOURNALS)} journals'],
            'rows': {'account': UAE_COA, 'tax': UAE_TAX, 'journal': JOURNALS}},
    'general': {'label': 'General (no VAT)', 'description': 'The same chart of accounts and journals without VAT codes – for a company not registered for VAT.',
                'highlights': [f'{len(UAE_COA)} accounts', f'{len(JOURNALS)} journals'],
                'rows': {'account': UAE_COA, 'journal': JOURNALS}},
}
