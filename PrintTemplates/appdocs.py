"""v1.67.6 (setup) item 6 – print templates for every app, not only HR and Payroll.

Settings › Print templates offered only HR / Payroll documents (payslip, letters …): a company with Projects, CRM, Property,
Maintenance, Asset Management or HSE found nothing for its own documents and every document said "Old layout (no default
yet)". Each app now has its documents here, with the same five looks (classic, modern, compact, bilingual; Arabic for the
customer-facing ones), a default design chosen for it (the Classic sample) and a real record context – the template engine,
the editor, the branding and the defaults are the existing ones.

A document is described by APP_DOCS: the model, the number / date / status fields, the detail rows, the party (customer,
tenant …), optional lines with totals, notes and terms. One generic block set ("record") draws every one of them.
"""
from datetime import date, datetime
from decimal import Decimal

from django.apps import apps
from django.utils import timezone

# key, English, Arabic, family, view rights, icon, app, model, spec
APP_DOCS = [
    ('support_ticket', 'Support ticket', 'تذكرة دعم', 'form', {'view_ticket', 'change_ticket'}, 'support_agent', 'projects', 'ZeoProjects.Ticket', {
        'number': 'number', 'date': 'created_at', 'status': 'stage',
        'party': ('Customer|العميل', ['customer', 'requester_name', 'requester_email']),
        'fields': [('Subject|الموضوع', 'subject'), ('Priority|الأولوية', 'priority'), ('Category|الفئة', 'category'), ('Team|الفريق', 'team'),
                   ('Assigned to|المسؤول', 'assigned_to'), ('Contract|العقد', 'contract'), ('Response due|موعد الرد', 'response_due'),
                   ('Resolution due|موعد الحل', 'resolution_due')],
        'notes': 'description'}),
    ('amc_contract', 'AMC contract', 'عقد صيانة سنوي', 'form', {'view_amccontract', 'change_amccontract'}, 'handshake', 'projects', 'ZeoProjects.AMCContract', {
        'number': 'number', 'date': 'start_date', 'status': 'status',
        'party': ('Customer|العميل', ['customer']),
        'fields': [('Title|العنوان', 'title'), ('Contract type|نوع العقد', 'contract_type'), ('Start date|تاريخ البدء', 'start_date'),
                   ('End date|تاريخ الانتهاء', 'end_date'), ('Billing|الفوترة', 'billing_frequency'), ('Included hours|الساعات المشمولة', 'included_hours'),
                   ('Support timing|أوقات الدعم', 'support_timing'), ('Account manager|مدير الحساب', 'account_manager')],
        'totals': [('Contract value|قيمة العقد', 'value', 'net')], 'notes': 'exclusions', 'terms': 'notes'}),
    ('quotation', 'Quotation', 'عرض سعر', 'form', {'view_quotation', 'change_quotation'}, 'request_quote', 'crm', 'ZeoCRM.Quotation', {
        'number': 'number', 'date': 'date', 'status': 'status',
        'party': ('Customer|العميل', ['account', 'contact']),
        'fields': [('Customer reference|مرجع العميل', 'reference'), ('Sales agent|مندوب المبيعات', 'owner'), ('Payment term|شروط الدفع', 'payment_term'),
                   ('Currency|العملة', 'currency')],
        'lines': ('lines', [('Description|الوصف', 'description'), ('Qty|الكمية', 'qty', 'num'), ('Unit price|سعر الوحدة', 'unit_price', 'money'),
                            ('Disc. %|خصم %', 'discount_pct', 'num'), ('VAT|الضريبة', 'tax_amount', 'money'), ('Total|الإجمالي', 'total', 'money')]),
        'totals': [('Subtotal|المجموع الفرعي', 'subtotal'), ('Discount|الخصم', 'discount_total'), ('VAT|ضريبة القيمة المضافة', 'tax_total'),
                   ('Total|الإجمالي', 'total', 'net')],
        'notes': 'notes', 'terms': 'terms'}),
    ('sales_order', 'Sales order', 'أمر بيع', 'form', {'view_salesorder', 'change_salesorder'}, 'shopping_cart', 'crm', 'ZeoCRM.SalesOrder', {
        'number': 'number', 'date': 'date', 'status': 'status',
        'party': ('Customer|العميل', ['account', 'contact']),
        'fields': [('Customer reference|مرجع العميل', 'reference'), ('Sales agent|مندوب المبيعات', 'owner'), ('Payment term|شروط الدفع', 'payment_term'),
                   ('Currency|العملة', 'currency')],
        'lines': ('lines', [('Description|الوصف', 'description'), ('Qty|الكمية', 'qty', 'num'), ('Unit price|سعر الوحدة', 'unit_price', 'money'),
                            ('Disc. %|خصم %', 'discount_pct', 'num'), ('VAT|الضريبة', 'tax_amount', 'money'), ('Total|الإجمالي', 'total', 'money')]),
        'totals': [('Subtotal|المجموع الفرعي', 'subtotal'), ('Discount|الخصم', 'discount_total'), ('VAT|ضريبة القيمة المضافة', 'tax_total'),
                   ('Total|الإجمالي', 'total', 'net')],
        'notes': 'notes', 'terms': 'terms'}),
    ('lease_contract', 'Lease contract', 'عقد إيجار', 'form', {'view_lease', 'change_lease'}, 'apartment', 'property', 'ZeoProperty.Lease', {
        'number': 'number', 'date': 'start', 'status': 'status',
        'party': ('Tenant|المستأجر', ['party']),
        'fields': [('Unit|الوحدة', 'unit'), ('Start|البداية', 'start'), ('End|النهاية', 'end'), ('Payments|عدد الدفعات', 'payments'),
                   ('Payment method|طريقة الدفع', 'method'), ('Registration (Ejari / Tawtheeq)|التسجيل', 'registration_ref'), ('Agent|الوكيل', 'agent')],
        'totals': [('Security deposit|التأمين', 'deposit'), ('Annual rent|الإيجار السنوي', 'annual_rent', 'net')], 'notes': 'notes'}),
    ('property_bill', 'Tenant invoice', 'فاتورة المستأجر', 'form', {'view_bill', 'change_bill'}, 'receipt', 'property', 'ZeoProperty.Bill', {
        'number': 'number', 'date': 'due_date', 'status': 'status',
        'party': ('Bill to|إلى', ['party', 'owner']),
        'fields': [('Description|الوصف', 'description'), ('Unit|الوحدة', 'unit'), ('Lease|العقد', 'lease'), ('Period from|من', 'period_from'),
                   ('Period to|إلى', 'period_to'), ('Due date|تاريخ الاستحقاق', 'due_date')],
        'totals': [('Amount|المبلغ', 'amount'), ('VAT|ضريبة القيمة المضافة', 'vat'), ('Paid|المدفوع', 'paid'), ('Total|الإجمالي', 'total', 'net')]}),
    ('property_receipt', 'Receipt voucher', 'سند قبض', 'form', {'view_receipt', 'change_receipt'}, 'payments', 'property', 'ZeoProperty.Receipt', {
        'number': 'number', 'date': 'received_on', 'status': 'status',
        'party': ('Received from|استلمنا من', ['party', 'owner']),
        'fields': [('Received on|تاريخ الاستلام', 'received_on'), ('Method|الطريقة', 'method'), ('Reference|المرجع', 'reference'),
                   ('Cheque|الشيك', 'cheque'), ('Note|ملاحظة', 'note')],
        'totals': [('Allocated|المخصص', 'allocated'), ('Amount received|المبلغ المستلم', 'amount', 'net')], 'words': 'amount'}),
    ('work_order', 'Work order', 'أمر عمل', 'form', {'view_workorder', 'change_workorder'}, 'build', 'maintenance', 'ZeoMaintenance.WorkOrder', {
        'number': 'number', 'date': 'created_at', 'status': 'status',
        'party': ('Requested by|مقدم الطلب', ['requester', 'requester_email', 'requester_phone']),
        'fields': [('Title|العنوان', 'title'), ('Category|الفئة', 'category'), ('Priority|الأولوية', 'priority'), ('Location|الموقع', 'location'),
                   ('Asset|الأصل', 'asset'), ('Technician|الفني', 'technician'), ('Vendor|المورد', 'vendor'), ('Who pays|على حساب', 'who_pays')],
        'lines': ('costs', [('Kind|النوع', 'kind'), ('Description|الوصف', 'description'), ('Qty|الكمية', 'qty', 'num'), ('Rate|السعر', 'rate', 'money'),
                                ('Amount|المبلغ', 'amount', 'money')]),
        'totals': [('Estimate|التقدير', 'estimate'), ('Cost|التكلفة', 'cost', 'net')], 'notes': 'description', 'terms': 'work_note'}),
    ('asset_work_order', 'Asset work order', 'أمر عمل للأصل', 'form', {'view_workorder', 'change_workorder'}, 'handyman', 'assets', 'ZeoAssets.WorkOrder', {
        'number': 'number', 'date': 'scheduled_on', 'status': 'status',
        'party': ('Asset|الأصل', ['card']),
        'fields': [('Title|العنوان', 'title'), ('Kind|النوع', 'kind'), ('Priority|الأولوية', 'priority'), ('Team|الفريق', 'team'),
                   ('Technician|الفني', 'technician'), ('Scheduled on|موعد التنفيذ', 'scheduled_on'), ('Due date|تاريخ الاستحقاق', 'due_date'),
                   ('Labour hours|ساعات العمل', 'labour_hours')],
        'totals': [('Labour|العمالة', 'labour_cost'), ('Parts|قطع الغيار', 'parts_cost'), ('Other|أخرى', 'other_cost')],
        'notes': 'description', 'terms': 'work_note'}),
    ('permit_to_work', 'Permit to work', 'تصريح عمل', 'form', {'view_permit', 'change_permit'}, 'assignment_turned_in', 'hse', 'ZeoHSE.Permit', {
        'number': 'number', 'date': 'valid_from', 'status': 'status',
        'party': ('Applicant|مقدم الطلب', ['applicant', 'contractor']),
        'fields': [('Permit type|نوع التصريح', 'permit_type'), ('Site|الموقع', 'site'), ('Area|المنطقة', 'area'), ('Location|المكان', 'location'),
                   ('Equipment|المعدات', 'equipment'), ('Crew size|عدد الطاقم', 'crew_size'), ('Valid from|صالح من', 'valid_from'),
                   ('Valid to|صالح حتى', 'valid_to'), ('Receiver|المستلم', 'receiver'), ('Issued by|صادر عن', 'issued_by')],
        'notes': 'description', 'terms': 'precautions'}),
    ('incident_report', 'Incident report', 'تقرير حادث', 'form', {'view_incident', 'change_incident'}, 'report', 'hse', 'ZeoHSE.Incident', {
        'number': 'number', 'date': 'occurred_at', 'status': 'status',
        'party': ('Reported by|أبلغ عنه', ['reported_by']),
        'fields': [('Title|العنوان', 'title'), ('Category|الفئة', 'category'), ('Occurred at|وقت الحدوث', 'occurred_at'), ('Site|الموقع', 'site'),
                   ('Location|المكان', 'location'), ('Injured person|المصاب', 'injured_name'), ('Body part|الجزء المصاب', 'body_part'),
                   ('Actual severity|الخطورة الفعلية', 'actual_severity'), ('Potential severity|الخطورة المحتملة', 'potential_severity'),
                   ('Investigator|المحقق', 'investigator')],
        'notes': 'description', 'terms': 'immediate_actions'}),
    ('development_plan', 'Development plan', 'خطة التطوير', 'form', {'view_developmentplan', 'change_developmentplan'}, 'trending_up', 'people', 'ZeoPeople.DevelopmentPlan', {
        'number': 'number', 'date': 'created_at', 'status': 'status', 'party': ('Employee|الموظف', ['employee']),
        'fields': [('Title|العنوان', 'title'), ('Start|البداية', 'start'), ('Target date|التاريخ المستهدف', 'target_date'), ('Mentor / coach|المرشد', 'mentor'),
                   ('Progress %|نسبة الإنجاز', 'progress')],
        'notes': 'notes'}),
]
BY_KEY = {d[0]: d for d in APP_DOCS}
CUSTOMER_FACING = ('quotation', 'sales_order', 'lease_contract', 'property_bill', 'property_receipt', 'amc_contract')
# the app of every print document (the HR / Payroll ones of 1.62 included)
DOC_APP = {'payslip': 'payroll', 'final_settlement': 'payroll', 'loan_agreement': 'payroll', 'salary_certificate': 'hrms',
           'experience_letter': 'hrms', 'noc': 'hrms', 'salary_transfer': 'hrms', 'offer_letter': 'hrms', 'warning_letter': 'hrms',
           'expense_claim': 'hrms', 'leave_approval': 'hrms', 'asset_handover': 'hrms', **{d[0]: d[6] for d in APP_DOCS}}


def doc_for_model(model):
    """The print document of a model (None when it has none)."""
    lab = f'{model._meta.app_label}.{model.__name__}'
    return next((d[0] for d in APP_DOCS if d[7] == lab), None)


def app_active(doc_type):
    try:
        from ZeoApps.services import is_active
        return is_active(DOC_APP.get(doc_type, 'hrms'))
    except Exception:
        return True


def M(label):
    return apps.get_model(label)


def _lab(spec, lang):
    en, _, ar = spec.partition('|')
    return ar if lang == 'ar' else (f'{en} / {ar}' if lang == 'bi' else en)


def _fmt(v, kind='', lang='en'):
    from .contexts import d as fdate, money
    if v is None or v == '':
        return ''
    if kind == 'money' or isinstance(v, Decimal) and kind != 'num':
        return money(v)
    # v1.67.8 (fixes) item 9: full dates dd/mm/yyyy on every app document (was "08 Nov 2026", and the document date kept only "08")
    if isinstance(v, datetime):
        v = timezone.localtime(v) if timezone.is_aware(v) else v
        return v.strftime('%d/%m/%Y %H:%M')
    if isinstance(v, date):
        return v.strftime('%d/%m/%Y')
    if isinstance(v, bool):
        return ('نعم' if v else 'لا') if lang == 'ar' else ('Yes' if v else 'No')
    if isinstance(v, Decimal):
        return f'{v.normalize():f}'
    return str(v)


def _value(obj, path, kind='', lang='en'):
    cur = obj
    for part in path.split('.'):
        if cur is None:
            return ''
        disp = getattr(cur, f'get_{part}_display', None)
        cur = disp() if callable(disp) and part == path.split('.')[-1] else getattr(cur, part, None)
    if hasattr(cur, 'all') and callable(cur.all):
        return ', '.join(str(x) for x in cur.all()[:10])
    return _fmt(cur, kind, lang)


def _doc_date(obj, path):
    """v1.67.8 (fixes) item 9: the document date as dd/mm/yyyy (a date-time: its day in company time). Before, the formatted
    value was cut at the first space – "08 Nov 2026" became "08"."""
    cur = obj
    for part in (path or '').split('.'):
        cur = getattr(cur, part, None) if cur is not None else None
    if isinstance(cur, datetime):
        cur = (timezone.localtime(cur) if timezone.is_aware(cur) else cur).date()
    if isinstance(cur, date):
        return cur.strftime('%d/%m/%Y')
    return str(cur or '')


def record_ctx(doc_type, obj, lang='en'):
    """The context of a record of an app document (company, branding, doc, rec)."""
    from .contexts import base, signers, words
    key, en, ar, fam, perms, icon, app, label, spec = BY_KEY[doc_type]
    branch = getattr(obj, 'branch', None) if 'branch' in {f.name for f in obj._meta.fields} else None
    c = base(doc_type, lang, None, branch)
    c['doc'].update(number=_value(obj, spec.get('number', 'number')), date=_doc_date(obj, spec.get('date')) if spec.get('date') else '',
                    status=_value(obj, spec.get('status', 'status'), '', lang))
    party_lab, party_paths = spec.get('party', ('', []))
    party = [x for x in (_value(obj, p, '', lang) for p in party_paths) if x]
    rows = [{'label': _lab(lb, lang), 'value': _value(obj, p, '', lang)} for lb, p in spec.get('fields', [])]
    rec = {'party_label': _lab(party_lab, lang) if party_lab else '', 'party': party, 'fields': [r for r in rows if r['value']],
           'columns': [], 'rows': [], 'totals': [], 'notes': '', 'terms': '', 'words': ''}
    if spec.get('lines'):
        rel, cols = spec['lines']
        rec['columns'] = [{'label': _lab(cl[0], lang), 'num': len(cl) > 2 and cl[2] in ('num', 'money')} for cl in cols]
        mgr = getattr(obj, rel, None)
        items = list(mgr.all()[:200]) if mgr is not None and hasattr(mgr, 'all') else []
        isnum = [len(cl) > 2 and cl[2] in ('num', 'money') for cl in cols]
        rec['rows'] = [[{'v': _value(it, cl[1], cl[2] if len(cl) > 2 else '', lang), 'num': n} for cl, n in zip(cols, isnum)] for it in items]
        if not items and getattr(obj, 'items', None) and isinstance(obj.items, list):   # CRM: the lines kept as JSON until saved again
            rec['rows'] = [[{'v': _fmt(it.get(cl[1]), cl[2] if len(cl) > 2 else '', lang), 'num': n} for cl, n in zip(cols, isnum)]
                           for it in obj.items[:200] if isinstance(it, dict)]
    for t in spec.get('totals', []):
        rec['totals'].append({'label': _lab(t[0], lang), 'value': _value(obj, t[1], 'money', lang), 'net': len(t) > 2})
    rec['totals'] = [t for t in rec['totals'] if t['value']]
    for k in ('notes', 'terms'):
        if spec.get(k):
            rec[k] = _value(obj, spec[k], '', lang)
    if spec.get('words'):
        try:
            rec['words'] = words(getattr(obj, spec['words']) or 0, 'ar' if lang == 'ar' else 'en')
        except Exception:   # noqa: BLE001
            rec['words'] = ''
    c['rec'] = rec
    c['signers'] = signers(c, lang, ('Prepared by', 'أعده', '', '', False), ('Approved by', 'اعتمده', c['company'].get('signatory_name', ''), '', True),
                           ('Received by', 'استلمه', '', '', False))
    return c


SAMPLES = {
    'support_ticket': {'party': ['Al Noor Trading LLC', 'Omar Haddad', 'omar@alnoor.example.ae'],
                       'fields': ['Printer offline on the 2nd floor', 'High', 'Hardware', 'Service desk', 'Rami Saleh', 'AMC-00012', '08/11/2026 10:30', '09/11/2026 09:00'],
                       'notes': 'The office printer shows "offline" since this morning. Restarted twice.'},
    'amc_contract': {'party': ['Al Noor Trading LLC'], 'fields': ['Annual support – ERP', 'Comprehensive', '01/01/2026', '31/12/2026', 'Quarterly', '120', 'Sun–Thu 8:00–18:00', 'Rami Saleh'],
                     'totals': [('Contract value|قيمة العقد', '24,000.00', True)], 'notes': 'Hardware replacement parts are not included.'},
    'quotation': {'party': ['Al Noor Trading LLC', 'Omar Haddad'], 'fields': ['PO-7781', 'Layla Hassan', '30 days', 'AED'],
                  'rows': [['ERP implementation – phase 1', '1', '45,000.00', '0', '2,250.00', '47,250.00'],
                           ['User training (days)', '3', '2,500.00', '10', '337.50', '7,087.50'],
                           ['Annual support', '1', '12,000.00', '0', '600.00', '12,600.00']],
                  'totals': [('Subtotal|المجموع الفرعي', '64,500.00', False), ('Discount|الخصم', '750.00', False), ('VAT|ضريبة القيمة المضافة', '3,187.50', False),
                             ('Total|الإجمالي', '66,937.50', True)],
                  'notes': 'Prices valid for 30 days.', 'terms': '50% on order, 50% on go-live.'},
    'lease_contract': {'party': ['Mohammed Al Suwaidi'], 'fields': ['Tower A – 1204', '01/01/2026', '31/12/2026', '4', 'Cheques', 'EJ-2026-118844', 'Hessa Al Ali'],
                       'totals': [('Security deposit|التأمين', '4,250.00', False), ('Annual rent|الإيجار السنوي', '85,000.00', True)]},
    'property_bill': {'party': ['Mohammed Al Suwaidi'], 'fields': ['Rent – instalment 2 of 4', 'Tower A – 1204', 'LSE-00031', '01/04/2026', '30/06/2026', '01/04/2026'],
                      'totals': [('Amount|المبلغ', '21,250.00', False), ('VAT|ضريبة القيمة المضافة', '0.00', False), ('Total|الإجمالي', '21,250.00', True)]},
    'property_receipt': {'party': ['Mohammed Al Suwaidi'], 'fields': ['01/04/2026', 'Cheque', 'CHQ 000452 – Emirates NBD', 'CHQ-00093'],
                         'totals': [('Amount received|المبلغ المستلم', '21,250.00', True)], 'words': True},
    'work_order': {'party': ['Front desk – Tower A', 'desk@example.ae'], 'fields': ['AC not cooling – unit 1204', 'HVAC', 'High', 'Tower A – 12th floor', 'Split AC 2.5 ton', 'Imran Khan', 'CoolTech LLC', 'Owner'],
                   'rows': [['Labour', 'Gas top-up and cleaning', '2', '150.00', '300.00'], ['Material', 'Refrigerant R410A', '1', '220.00', '220.00']],
                   'totals': [('Estimate|التقدير', '600.00', False), ('Cost|التكلفة', '520.00', True)], 'notes': 'Tenant reports warm air since yesterday.'},
    'permit_to_work': {'party': ['Imran Khan', 'CoolTech LLC'], 'fields': ['Hot work', 'Main plant', 'Workshop', 'Bay 3', 'Welding set', '3', '08/11/2026 07:00', '08/11/2026 17:00', 'Site supervisor', 'HSE officer'],
                       'notes': 'Welding of the support bracket.', 'terms': 'Fire watch for 30 minutes after the work; extinguisher at the bay.'},
    'incident_report': {'party': ['Site supervisor'], 'fields': ['Slip near the loading bay', 'Near miss', '07/11/2026 14:20', 'Main plant', 'Loading bay', '', '', '1', '3', 'HSE officer'],
                        'notes': 'Oil on the floor near bay 2; nobody was hurt.', 'terms': 'Area cordoned off and cleaned.'},
}

# v1.67.8 (fixes) item 9: the sample dates above are dd/mm/yyyy (were "08 Nov 2026")


def sample_ctx(doc_type, lang='en'):
    """Made-up data for the gallery and previews without a record (the company's own letterhead and branding)."""
    from .contexts import base, signers, words
    key, en, ar, fam, perms, icon, app, label, spec = BY_KEY[doc_type]
    c = base(doc_type, lang)
    if not c['company'].get('name'):
        c['company'].update(name='Golden Dune Integrated Services LLC', initials='GD')
    smp = SAMPLES.get(doc_type) or SAMPLES.get({'sales_order': 'quotation', 'asset_work_order': 'work_order'}.get(doc_type, ''), {})
    c['doc'].update(number=f'{doc_type[:3].upper()}-00042', date=_fmt(date.today(), '', lang), status='')
    fields = spec.get('fields', [])
    vals = smp.get('fields', [])
    rec = {'party_label': _lab(spec.get('party', ('',))[0], lang) if spec.get('party') else '', 'party': smp.get('party', []),
           'fields': [{'label': _lab(f[0], lang), 'value': v} for f, v in zip(fields, vals) if v],
           'columns': [{'label': _lab(cl[0], lang), 'num': len(cl) > 2 and cl[2] in ('num', 'money')} for cl in (spec.get('lines') or ('', []))[1]],
           'rows': [[{'v': v, 'num': i > 1 or (i == 1 and not v[:1].isalpha())} for i, v in enumerate(r)] for r in smp.get('rows', [])] if spec.get('lines') else [],
           'totals': [{'label': _lab(a, lang), 'value': v, 'net': n} for a, v, n in smp.get('totals', [])],
           'notes': smp.get('notes', ''), 'terms': smp.get('terms', ''), 'words': words(21250, 'ar' if lang == 'ar' else 'en') if smp.get('words') else ''}
    c['rec'] = rec
    c['signers'] = signers(c, lang, ('Prepared by', 'أعده', '', '', False), ('Approved by', 'اعتمده', c['company'].get('signatory_name', '') or 'Sara Al Hashimi', '', True),
                           ('Received by', 'استلمه', '', '', False))
    return c


def queryset(doc_type, c):
    """Records the user may print: company administrators and users with the app rights of the document."""
    key, en, ar, fam, perms, icon, app, label, spec = BY_KEY[doc_type]
    try:
        qs = M(label).objects.all()
    except LookupError:
        return None
    if not app_active(doc_type):   # v1.67.7 (admin): nothing of an app that is off
        return qs.none()
    if not (c.admin or (perms & c.codes) or any(x.startswith(app_prefix(app)) for x in c.codes)):
        return qs.none()
    if c.branches is not None and not c.admin and 'branch' in {f.name for f in qs.model._meta.fields}:
        from django.db.models import Q
        qs = qs.filter(Q(branch_id__in=c.branches) | Q(branch__isnull=True))
    return qs


def may_print(doc_type, c):
    """v1.67.7 (admin): the Print button – the document's app is active and the user may print it (company administrator,
    the document's rights or a right of its app; the same rule as `queryset`)."""
    if doc_type not in BY_KEY or not app_active(doc_type):
        return False
    key, en, ar, fam, perms, icon, app, label, spec = BY_KEY[doc_type]
    return bool(c.admin or (perms & c.codes) or any(x.startswith(app_prefix(app)) for x in c.codes))


def printable(request):
    """v1.67.7 (admin): the app documents this user may print in the open company."""
    from AccessControl.access import ctx
    c = ctx(request)
    return [d[0] for d in APP_DOCS if may_print(d[0], c)]


def print_doc_for(model, request):
    """v1.67.7 (admin): the document key of a model when the user may print it, else None."""
    dt = doc_for_model(model)
    if not dt:
        return None
    from AccessControl.access import ctx
    return dt if may_print(dt, ctx(request)) else None


def app_prefix(app):
    return {'projects': 'zp_', 'crm': 'crm_', 'property': 'prop_', 'maintenance': 'mnt_', 'assets': 'am_', 'hse': 'hse_', 'people': 'ppl_'}.get(app, '~')


def label_of(doc_type, o):
    spec = BY_KEY[doc_type][8]
    num = _value(o, spec.get('number', 'number')) or f'#{o.pk}'
    party = next((x for x in (_value(o, p) for p in spec.get('party', ('', []))[1]) if x), '')
    title = next((_value(o, p) for p in ('title', 'subject', 'description') if hasattr(o, p)), '')
    return ' – '.join(x for x in [num, party, title] if x)[:160]


# ------------------------------------------------------------------ the design: one block set for every app document
def _sec(title_en, title_ar, inner):
    return (f'<section class="blk"><h3 class="sec-title">[[{title_en}|{title_ar}]]</h3>{inner}</section>')


PARTY = ('{% if rec.party %}<section class="blk"><h3 class="sec-title">{{ rec.party_label }}</h3><div class="addressee">'
         '{% for p in rec.party %}<div{% if forloop.first %} style="font-weight:600"{% endif %}>{{ p }}</div>{% endfor %}</div></section>{% endif %}')
DETAILS = ('{% if rec.fields %}<section class="blk"><h3 class="sec-title">[[Details|التفاصيل]]</h3><table class="kv cols2"><tbody>'
           '{% for f in rec.fields %}{% if forloop.counter0|divisibleby:2 %}<tr>{% endif %}<th>{{ f.label }}</th><td>{{ f.value }}</td>'
           '{% if not forloop.counter0|divisibleby:2 or forloop.last %}{% if forloop.last and forloop.counter0|divisibleby:2 %}<th></th><td></td>{% endif %}</tr>{% endif %}'
           '{% endfor %}</tbody></table></section>{% endif %}')
LINES = ('{% if rec.rows %}<section class="blk"><table class="lines"><thead><tr>{% for col in rec.columns %}<th{% if col.num %} class="num"{% endif %}>'
         '{{ col.label }}</th>{% endfor %}</tr></thead><tbody>{% for r in rec.rows %}<tr>{% for v in r %}<td{% if v.num %} class="num"{% endif %}>'
         '{{ v.v }}</td>{% endfor %}</tr>{% endfor %}</tbody></table></section>{% endif %}')
TOTALS = ('{% if rec.totals %}<section class="blk"><table class="tiles"><tr>{% for t in rec.totals %}<td class="tile-c"><div class="tile{% if t.net %} net{% endif %}">'
          '<div class="t-l">{{ t.label }}</div><div class="t-v">{{ t.value }}</div></div></td>{% endfor %}</tr></table></section>{% endif %}'
          '{% if rec.words %}<div class="blk words"><b>[[Amount in words|المبلغ كتابةً]]:</b> {{ rec.words }}</div>{% endif %}')
NOTES = ('{% if rec.notes %}<div class="blk notes"><b>[[Notes|ملاحظات]]</b><p>{{ rec.notes|linebreaksbr }}</p></div>{% endif %}')
TERMS = ('{% if rec.terms %}<div class="blk notes"><b>[[Terms and remarks|الشروط والملاحظات]]</b><p>{{ rec.terms|linebreaksbr }}</p></div>{% endif %}')
RECORD = {
    'party': ('Customer / party', PARTY), 'details': ('Details', DETAILS), 'lines': ('Lines', LINES), 'totals': ('Totals', TOTALS),
    'notes': ('Notes', NOTES), 'terms': ('Terms and remarks', TERMS),
}
ORDERS = {
    'classic': ['header', 'title', 'party', 'details', 'lines', 'totals', 'notes', 'terms', 'signatures', 'footer'],
    'modern': ['header', 'party', 'totals', 'details', 'lines', 'notes', 'terms', 'signatures', 'footer'],
    'compact': ['header', 'party', 'details', 'lines', 'totals', 'notes', 'signatures', 'footer'],
    'bilingual': ['header', 'title', 'party', 'details', 'lines', 'totals', 'notes', 'terms', 'signatures', 'footer'],
}
