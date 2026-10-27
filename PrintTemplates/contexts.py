"""v1.62.0 print – the document types and their data (the template context).

Every context is plain data (dicts, lists, text) – never a model – so a template can only show what is listed here.
Common to every document:
  company.*  name, name_ar, initials, logo, address, address_ar, po_box, phone, email, website, trn, licence_no, header_text,
             footer_text, signature, stamp, signatory_name, signatory_title
  brand.*    primary, accent, logo_position, watermark, watermark_text, show_stamp, show_signature
  doc.*      title, title_ar, subtitle, number, date, status, verification_code, generated
  employee.* name, name_ar, code, title, designation, department, branch, nationality, joined, email, passport_no,
             emirates_id_masked, labour_card_no
  signers    [{label, name, note, image, stamp}]
  lang, dir
Per document: payslip / ytd / leave / bank, settlement, letter / salary / bank / travel, offer, case, loan, claim, handover.
"""
import logging
import re
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.apps import apps
from django.db.models import Q, Sum
from django.utils import timezone

log = logging.getLogger(__name__)
M = apps.get_model

AR_MONTHS = ['يناير', 'فبراير', 'مارس', 'أبريل', 'مايو', 'يونيو', 'يوليو', 'أغسطس', 'سبتمبر', 'أكتوبر', 'نوفمبر', 'ديسمبر']
EN_MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']

# key, English, Arabic, family, rights that let a back-office user print it (the employee always prints his own)
DOC_TYPES = [
    ('payslip', 'Payslip', 'قسيمة الراتب', 'form', {'view_payslip', 'change_payslip', 'add_payrollrun', 'view_payrollrun'}, 'receipt_long'),
    ('final_settlement', 'Final settlement statement', 'كشف التسوية النهائية', 'form', {'view_exitsettlement', 'add_exitsettlement', 'change_exitsettlement'}, 'logout'),
    ('salary_certificate', 'Salary certificate', 'شهادة راتب', 'letter', {'view_letterrequest', 'change_letterrequest'}, 'workspace_premium'),
    ('experience_letter', 'Experience / employment letter', 'شهادة خبرة / عمل', 'letter', {'view_letterrequest', 'change_letterrequest'}, 'badge'),
    ('noc', 'No objection certificate (NOC)', 'شهادة عدم ممانعة', 'letter', {'view_letterrequest', 'change_letterrequest'}, 'verified'),
    ('salary_transfer', 'Salary transfer letter', 'خطاب تحويل الراتب', 'letter', {'view_letterrequest', 'change_letterrequest'}, 'account_balance'),
    ('offer_letter', 'Offer letter', 'خطاب عرض العمل', 'letter', {'view_offer', 'change_offer'}, 'handshake'),
    ('warning_letter', 'Warning letter', 'خطاب إنذار', 'letter', {'view_disciplinarycase', 'change_disciplinarycase'}, 'gavel'),
    ('loan_agreement', 'Loan agreement and schedule', 'اتفاقية القرض وجدول السداد', 'form', {'view_loanapplication', 'change_loanapplication'}, 'payments'),
    ('expense_claim', 'Expense claim', 'مطالبة المصروفات', 'form', {'view_expensereport', 'change_expensereport'}, 'request_quote'),
    ('leave_approval', 'Leave approval letter', 'خطاب الموافقة على الإجازة', 'letter', {'view_employee_leave_request', 'change_employee_leave_request'}, 'event_available'),
    ('asset_handover', 'Asset handover form', 'نموذج تسليم الأصول', 'form', {'view_assethandover', 'change_assethandover'}, 'devices'),
]
BY_KEY = {d[0]: d for d in DOC_TYPES}
# HR letter types of Self service → print document type
LETTER_DOC = {'salary_certificate': 'salary_certificate', 'salary_transfer': 'salary_transfer', 'bank_account': 'salary_transfer',
              'noc_travel': 'noc', 'noc_visa': 'noc', 'noc_other': 'noc', 'embassy': 'noc',
              'experience': 'experience_letter', 'employment': 'experience_letter'}


def T(lang, en, ar):
    return ar if lang == 'ar' else en


def money(v):
    try:
        return f'{Decimal(str(v or 0)):,.2f}'
    except Exception:   # noqa: BLE001
        return str(v or '')


def d(v, lang='en'):
    if not v:
        return ''
    if isinstance(v, str):
        try:
            v = datetime.fromisoformat(v.replace('Z', '+00:00'))
        except ValueError:
            return v[:10]
    if isinstance(v, datetime):
        v = timezone.localtime(v).date() if timezone.is_aware(v) else v.date()
    if lang == 'ar':
        return f'{v.day} {AR_MONTHS[v.month - 1]} {v.year}'
    return v.strftime('%d %b %Y')


def period(y, m, lang):
    return f'{AR_MONTHS[m - 1]} {y}' if lang == 'ar' else f'{EN_MONTHS[m - 1]} {y}'


def mask(s, keep=4):
    s = re.sub(r'\s+', '', str(s or ''))
    if not s:
        return ''
    if len(s) <= keep:
        return s
    head = s[:4] if s[:2].isalpha() else ''
    return (head + ' ' if head else '') + '•••• •••• ' + s[-keep:]


def words(v, lang):
    try:
        from PayrollPlus.services import amount_words
        return amount_words(v, lang)
    except Exception:   # noqa: BLE001
        return ''


def paragraphs(text):
    return [p.strip() for p in re.split(r'\n\s*\n|\r\n\s*\r\n|\n', text or '') if p.strip()]


def initials(name):
    stop = {'llc', 'l.l.c', 'fze', 'fzco', 'fzc', 'co', 'co.', 'company', 'and', '&', 'the', 'of', 'ltd', 'limited', 'est', 'group'}
    w = [x for x in re.split(r'[\s\-]+', name or '') if x and x.lower().strip('.') not in stop]
    return (''.join(x[0] for x in w[:2]) or 'Z').upper()


def _tr(obj, field, lang):
    v = getattr(obj, field, '') if obj is not None else ''
    if lang != 'ar' or obj is None:
        return v or ''
    try:
        from Translations.services import translated
        return translated(obj, field, 'ar') or v or ''
    except Exception:   # noqa: BLE001
        return v or ''


def _file_uri(f):
    try:
        if f and f.name:
            from .engine import data_uri
            return data_uri(f.path)
    except Exception:   # noqa: BLE001
        pass
    return ''


# ------------------------------------------------------------------ company, branding, employee
def branding():
    from .models import PrintBranding
    return PrintBranding.get()


def brand_ctx(b=None):
    b = b or branding()
    return {'primary': b.primary_color, 'accent': b.accent_color, 'text': b.text_color, 'font': b.font, 'font_arabic': b.font_arabic,
            'logo_position': b.logo_position, 'watermark': b.show_watermark, 'watermark_text': b.watermark_text,
            'show_stamp': b.show_stamp, 'show_signature': b.show_signature}


def company_ctx(branch=None, lang='en', b=None):
    b = b or branding()
    try:
        from SelfService.letters import company
        comp = company()
    except Exception:   # noqa: BLE001
        comp = None
    try:
        from OrgStructure.models import CompanyProfile
        prof = CompanyProfile.objects.order_by('id').first()
    except Exception:   # noqa: BLE001
        prof = None
    name = getattr(prof, 'legal_name', '') or getattr(comp, 'name', '') or ''
    name_ar = getattr(prof, 'name_local', '') or ''
    logo = ''
    if b.logo_source == 'own' or (b.logo and b.logo.name and b.logo_source != 'company'):
        logo = _file_uri(b.logo)
    if not logo and b.logo_source == 'auto' and branch is not None:
        logo = _file_uri(getattr(branch, 'branch_logo', None))
    if not logo:
        logo = _file_uri(getattr(comp, 'logo', None))
    addr = b.address or (getattr(branch, 'branch_address', '') or '').replace('\n', ', ') if branch is not None else b.address
    if not addr and comp is not None:
        addr = ', '.join(x for x in [getattr(comp, 'address_line1', ''), getattr(comp, 'address_line2', ''), getattr(comp, 'city', '')] if x)
    phone = getattr(prof, 'phone', '') or (getattr(branch, 'br_branch_nmbr_1', '') if branch is not None else '')
    email = getattr(prof, 'email', '') or (getattr(branch, 'br_branch_mail', '') if branch is not None else '')
    po = getattr(prof, 'po_box', '') or ''
    web = getattr(prof, 'website', '') or ''
    contact = ' · '.join(x for x in [f'P.O. Box {po}' if po else '', f'Tel {phone}' if phone else '', email or '', web] if x)
    return {
        'name': name, 'name_ar': name_ar, 'initials': initials(name), 'logo': logo, 'address': addr or '', 'address_ar': '', 'contact_line': contact,
        'po_box': getattr(prof, 'po_box', '') or '', 'phone': phone or '', 'email': email or '', 'website': getattr(prof, 'website', '') or '',
        'trn': b.trn or getattr(prof, 'tax_id', '') or '', 'licence_no': getattr(prof, 'licence_no', '') or '',
        'header_text': b.header_text or re.sub(r'<[^>]+>', ' ', getattr(prof, 'letterhead_header', '') or '').strip(),
        'footer_text': b.footer_text or re.sub(r'<[^>]+>', ' ', getattr(prof, 'letterhead_footer', '') or '').strip(),
        'signature': (_file_uri(b.signature) or _file_uri(getattr(prof, 'signature', None))) if b.show_signature else '',
        'stamp': (_file_uri(b.stamp) or _file_uri(getattr(prof, 'stamp', None))) if b.show_stamp else '',
        'signatory_name': getattr(prof, 'signatory_name', '') or '', 'signatory_title': getattr(prof, 'signatory_designation', '') or '',
    }


def employee_ctx(emp, lang='en'):
    if emp is None:
        return {}
    try:
        from SelfService import profile as P
        ident = P.employee_identity(emp)
    except Exception:   # noqa: BLE001
        ident = None
    name = ' '.join(x for x in [emp.emp_first_name, emp.emp_middle_name, emp.emp_last_name] if x)
    name_ar = getattr(ident, 'arabic_name', '') or ''
    g = (emp.emp_gender or '').lower()
    title = T(lang, 'Mr.' if g.startswith('m') else ('Ms.' if g.startswith('f') else ''), 'السيد' if g.startswith('m') else ('السيدة' if g.startswith('f') else ''))
    eid = getattr(ident, 'emirates_id', '') or ''
    return {
        'id': emp.pk, 'name': (name_ar or name) if lang == 'ar' else name, 'name_ar': name_ar, 'name_en': name, 'code': emp.emp_code, 'title': title,
        'designation': _tr(emp.emp_desgntn_id, 'desgntn_job_title', lang) if emp.emp_desgntn_id_id else '',
        'department': _tr(emp.emp_dept_id, 'dept_name', lang) if emp.emp_dept_id_id else '',
        'branch': _tr(emp.emp_branch_id, 'branch_name', lang) if emp.emp_branch_id_id else '',
        'nationality': _tr(emp.emp_nationality, 'N_name', lang) if emp.emp_nationality_id else '',
        'joined': d(emp.emp_joined_date, lang), 'email': emp.emp_company_email or emp.emp_personal_email or '',
        'passport_no': getattr(ident, 'passport_no', '') or '', 'emirates_id_masked': mask(eid, 4) if eid else '',
        'labour_card_no': getattr(ident, 'labour_card_no', '') or '',
    }


def doc_ctx(doc_type, lang, number='', when=None, status='', subtitle='', code=''):
    _, en, ar = BY_KEY[doc_type][:3]
    title, title_ar = (ar, '') if lang == 'ar' else ((en, ar) if lang == 'bi' else (en, ''))
    return {'title': title, 'title_ar': title_ar, 'subtitle': subtitle, 'number': number or '', 'date': d(when or date.today(), 'ar' if lang == 'ar' else 'en'),
            'status': status or '', 'verification_code': code or '', 'generated': timezone.localtime().strftime('%d %b %Y %H:%M')}


def base(doc_type, lang, emp=None, branch=None, b=None):
    b = b or branding()
    if branch is None and emp is not None and emp.emp_branch_id_id:
        branch = emp.emp_branch_id
    return {'lang': lang, 'dir': 'rtl' if lang == 'ar' else 'ltr', 'company': company_ctx(branch, lang, b), 'brand': brand_ctx(b),
            'employee': employee_ctx(emp, lang), 'doc': doc_ctx(doc_type, lang), 'signers': []}


def signers(c, lang, *items):
    """items: (English label, Arabic label, name, note, company signature?)"""
    out = []
    for en, ar, name, note, sig in items:
        out.append({'label': en if lang == 'en' else (ar if lang == 'ar' else f'{en} / {ar}'), 'name': name or '', 'note': note or '',
                    'image': c['company'].get('signature', '') if sig else '', 'stamp': c['company'].get('stamp', '') if sig else ''})
    return out


def bank_ctx(emp, lang):
    bk = M('EmpManagement', 'EmployeeBankDetail').objects.filter(employee=emp, is_active=True).order_by('-id').first() if emp else None
    return {'name': getattr(bk, 'bank_name', '') or '', 'iban': getattr(bk, 'iban_number', '') or '', 'iban_masked': mask(getattr(bk, 'iban_number', '')),
            'account': getattr(bk, 'account_number', '') or '', 'account_masked': mask(getattr(bk, 'account_number', '')),
            'mode': T(lang, 'WPS bank transfer', 'تحويل بنكي عبر نظام حماية الأجور') if bk else T(lang, 'Cash / cheque', 'نقدًا / شيك')}


# ------------------------------------------------------------------ payslip
def payslip_ctx(p, lang='en'):
    emp, r = p.employee, p.payroll_run
    c = base('payslip', lang, emp)
    cur = getattr(p.currency, 'currency_code', '') if p.currency_id else ''
    earn, ded = [], []
    for pc in p.components.select_related('component').order_by('id'):
        comp = pc.component
        if comp is None or not getattr(comp, 'show_in_payslip', True) or not pc.amount:
            continue
        row = {'name': _tr(comp, 'name', lang), 'amount': money(pc.amount), 'code': comp.code or ''}
        (earn if comp.component_type == 'addition' else ded).append(row)
    y, m = r.year, r.month
    ot = 0
    try:
        start = date(y, m, 1)
        end = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))
        ot = M('calendars', 'EmployeeOvertime').objects.filter(employee=emp, date__gte=start, date__lte=end, approved=True).aggregate(h=Sum('hours'))['h'] or 0
    except Exception:   # noqa: BLE001
        pass
    PS = M('PayrollManagement', 'Payslip')
    released = ('Approved', 'approved', 'paid', 'Paid', 'processed')
    yq = PS.objects.filter(employee=emp, payroll_run__year=y, payroll_run__month__lte=m).filter(Q(status__in=released) | Q(pk=p.pk))
    agg = yq.aggregate(g=Sum('gross_salary'), dd=Sum('total_deductions'), n=Sum('net_salary'))
    leave = []
    for lb in M('calendars', 'emp_leave_balance').objects.filter(employee=emp, balance__gt=0).select_related('leave_type').order_by('-balance')[:5]:
        leave.append({'type': _tr(lb.leave_type, 'name', lang) if lb.leave_type_id else '', 'balance': f'{float(lb.balance or 0):g}'})
    wd, dw = p.total_working_days or 0, p.days_worked or 0
    net = p.net_salary or 0
    c['payslip'] = {
        'period': period(y, m, lang), 'period_en': period(y, m, 'en'), 'month': m, 'year': y, 'run_name': r.name or '',
        'pay_date': d(r.payment_date, lang) if r.payment_date else '', 'currency': cur or 'AED',
        'working_days': f'{float(wd):g}', 'days_worked': f'{float(dw):g}', 'unpaid_days': f'{max(0.0, float(wd) - float(dw)):g}',
        'overtime_hours': f'{float(ot):g}', 'earnings': earn, 'deductions': ded,
        'total_earnings': money(sum(Decimal(e['amount'].replace(',', '')) for e in earn) if earn else p.gross_salary),
        'total_deductions': money(p.total_deductions), 'gross': money(p.gross_salary), 'net': money(net),
        'net_display': f'{cur or "AED"} {money(net)}', 'status': p.status or '',
        'net_words': words(net, 'ar' if lang == 'ar' else 'en'), 'net_words_ar': words(net, 'ar') if lang == 'bi' else '',
    }
    c['ytd'] = {'year': y, 'count': yq.count(), 'gross': money(agg['g']), 'deductions': money(agg['dd']), 'net': money(agg['n'])}
    c['leave'] = leave
    c['bank'] = bank_ctx(emp, lang)
    c['doc'].update(number=f'PS-{y}{m:02d}-{emp.emp_code}', subtitle=T(lang, 'For the month of ', 'عن شهر ') + period(y, m, lang),
                    date=d(r.payment_date or date.today(), 'ar' if lang == 'ar' else 'en'), status='')
    c['signers'] = signers(c, lang, ('Prepared by', 'أعدّه', '', T(lang, 'Payroll', 'الرواتب'), False),
                           ('Approved by', 'اعتمده', c['company']['signatory_name'], '', True),
                           ('Employee signature', 'توقيع الموظف', c['employee'].get('name'), '', False))
    return c


# ------------------------------------------------------------------ final settlement (the case of the Leavers workspace)
def settlement_ctx(case, lang='en'):
    st = case.get('settlement') or {}
    emp = M('EmpManagement', 'emp_master').objects.filter(emp_code=case.get('employee_code')).first()
    c = base('final_settlement', lang, emp)
    svc = case.get('service') or {}
    yy, mm_, dd_ = svc.get('years', 0), svc.get('months', 0), svc.get('days', 0)
    service = T(lang, f'{yy} years, {mm_} months, {dd_} days', f'{yy} سنة و{mm_} شهر و{dd_} يوم')

    def lines(kind):
        out = []
        for l in st.get('lines') or []:
            if l.get('kind') != kind:
                continue
            basis = l.get('basis') or ''
            if l.get('calc'):
                basis = (basis + ' · ' if basis else '') + ' · '.join(str(x) for x in l['calc'])
            out.append({'label': l.get('label', ''), 'basis': basis, 'amount': money(l.get('amount'))})
        return out
    net = st.get('net') or 0
    c['settlement'] = {
        'number': st.get('number') or '', 'status': case.get('stage_label') or '', 'exit_type': case.get('exit_type_label') or '',
        'joined': d(case.get('joined'), lang), 'last_working_date': d(case.get('last_working_date'), lang), 'service': service,
        'unpaid_days': f"{float(case.get('unpaid_days') or 0):g}", 'basic': 'AED ' + money(case.get('basic')), 'wage': 'AED ' + money(case.get('wage')),
        'due_date': d(case.get('due_date'), lang), 'earnings': lines('earning'), 'deductions': lines('deduction'),
        'notes': [(l.get('label', '') + (f" – {l['basis']}" if l.get('basis') else '')) for l in (st.get('lines') or []) if l.get('kind') in ('info', 'block')],
        'total_earnings': money(st.get('total_earnings')), 'total_deductions': money(st.get('total_deductions')), 'net': money(net),
        'net_display': 'AED ' + money(net), 'net_words': st.get('net_words') if lang == 'en' and st.get('net_words') else words(net, 'ar' if lang == 'ar' else 'en'),
        'net_words_ar': words(net, 'ar') if lang == 'bi' else '', 'paid_on': d(st.get('paid_on'), lang), 'payment_ref': st.get('payment_ref') or '',
        'currency': 'AED',
    }
    if not emp:
        c['employee'] = {'name': case.get('employee', ''), 'code': case.get('employee_code', ''), 'designation': case.get('designation', ''),
                         'department': case.get('department', ''), 'branch': case.get('branch', '')}
    c['doc'].update(number=st.get('number') or '', status=case.get('stage_label') or '',
                    subtitle=T(lang, 'End of service – ', 'نهاية الخدمة – ') + (case.get('exit_type_label') or ''))
    hist = {h.get('action'): h for h in (st.get('history') or [])}
    who = [hist.get('submit') or hist.get('calculated') or {}, hist.get('validate') or {}, hist.get('approve') or {}]
    c['signers'] = signers(c, lang, ('Prepared by HR', 'أعدّته الموارد البشرية', who[0].get('by', ''), d(who[0].get('at'), lang), False),
                           ('Checked by Finance', 'دققته المالية', who[1].get('by', ''), d(who[1].get('at'), lang), False),
                           ('Approved by', 'اعتمده', who[2].get('by', ''), d(who[2].get('at'), lang), True),
                           ('Employee acknowledgement', 'إقرار الموظف', c['employee'].get('name', ''),
                            T(lang, 'Received in full and final settlement.', 'استلمت المبلغ تسوية نهائية وكاملة.'), False))
    return c


def settlement_case(st, request):
    """The Leavers-workspace case of an ExitSettlement row (as the settlement PDF uses it)."""
    from PayrollPlus import leavers as L
    from AccessControl.access import ctx
    eos = M('EmpManagement', 'EndOfService').objects.select_related('resignation__employee').get(pk=st.eos_id)
    return L.case(eos.resignation, ctx(request), request.user)


# ------------------------------------------------------------------ HR letters (Self service letter requests)
def letter_ctx(lr, lang='en', tpl=None, vals=None, emp=None):
    from SelfService import letters as SL
    emp = emp or M('EmpManagement', 'emp_master').objects.select_related('emp_branch_id', 'emp_desgntn_id', 'emp_dept_id', 'emp_nationality').get(pk=lr.employee_id)
    doc_type = LETTER_DOC.get(lr.letter_type, 'experience_letter')
    c = base(doc_type, lang, emp)
    vals = vals or SL.values(emp, lr)
    tpl = tpl or SL.template_for(lr.letter_type, lr.language, lr.branch_id)
    body = SL.fill(tpl.body, vals) if tpl else (lr.body_snapshot or '')
    fig = {}
    try:
        from SelfService import profile as P
        fig = P.salary_figures(emp)
    except Exception:   # noqa: BLE001
        pass
    c['letter'] = {'title': SL.fill(tpl.title, vals) if tpl else SL.TYPE_LABEL.get(lr.letter_type, ''), 'addressee': vals.get('addressee', ''),
                   'paragraphs': paragraphs(body), 'purpose': vals.get('purpose', ''), 'type': lr.letter_type,
                   'signatory_name': (tpl.signatory_name if tpl else '') or c['company']['signatory_name'],
                   'signatory_title': (tpl.signatory_title if tpl else '') or c['company']['signatory_title'] or T(lang, 'Human Resources', 'الموارد البشرية'),
                   'footer': SL.fill(tpl.footer, vals) if tpl and tpl.footer else '', 'show_salary': bool(tpl and tpl.show_salary)}
    c['salary'] = {'basic': vals.get('basic_salary', money(fig.get('basic'))), 'housing': vals.get('housing_allowance', ''), 'transport': vals.get('transport_allowance', ''),
                   'other': vals.get('other_allowances', ''), 'gross': vals.get('gross_salary', ''), 'net': vals.get('net_salary', ''), 'currency': vals.get('currency', 'AED')}
    c['bank'] = dict(bank_ctx(emp, lang), name=vals.get('bank_name', ''), iban=vals.get('iban', ''), account=vals.get('account_number', ''))
    c['travel'] = {'destination': vals.get('destination', ''), 'start': vals.get('travel_from', ''), 'end': vals.get('travel_to', '')}
    if c['employee'].get('passport_no', '') == '' and vals.get('passport_no'):
        c['employee']['passport_no'] = vals['passport_no']
    c['doc'].update(number=lr.reference_no or lr.number or '')
    c['doc']['verification_code'] = lr.verification_code or ''
    if lang != 'bi':
        c['doc']['title'] = c['letter']['title'] or c['doc']['title']
    return c


# ------------------------------------------------------------------ offer letter
def offer_ctx(o, lang='en'):
    cand = o.application.candidate
    c = base('offer_letter', lang, None, o.branch if o.branch_id else None)
    total = (o.basic_salary or 0) + (o.housing_allowance or 0) + (o.transport_allowance or 0) + (o.other_allowance or 0)
    contract = o.get_contract_type_display() + (f' – {o.contract_years} year(s)' if o.contract_type == 'limited' and o.contract_years else '')
    pos = o.position_title or (str(o.designation) if o.designation_id else '')
    co = c['company']['name']
    c['offer'] = {'number': o.document_number or '', 'candidate': cand.full_name, 'position': pos, 'department': str(o.department) if o.department_id else '',
                  'branch': str(o.branch) if o.branch_id else '', 'contract': contract,
                  'probation': T(lang, f'{o.probation_months} months', f'{o.probation_months} أشهر') if o.probation_months else '',
                  'joining_date': d(o.joining_date, lang), 'valid_until': d(o.valid_until, lang),
                  'annual_leave': T(lang, f'{o.annual_leave_days} working days per year', f'{o.annual_leave_days} يوم عمل في السنة') if o.annual_leave_days else '',
                  'air_ticket': o.air_ticket or '', 'medical': o.medical_insurance or '', 'currency': 'AED',
                  'basic': money(o.basic_salary), 'housing': money(o.housing_allowance), 'transport': money(o.transport_allowance),
                  'other': money(o.other_allowance), 'total': money(total), 'terms': paragraphs(o.terms), 'status': o.get_status_display()}
    c['letter'] = {'title': T(lang, 'Offer of Employment', 'عرض عمل'), 'addressee': cand.full_name,
                   'paragraphs': offer_text(lang, cand.full_name, pos, co, d(o.joining_date, lang), d(o.valid_until, lang)),
                   'signatory_name': c['company']['signatory_name'], 'signatory_title': c['company']['signatory_title'] or T(lang, 'Human Resources', 'الموارد البشرية')}
    c['employee'] = {'name': cand.full_name}
    c['doc'].update(number=o.document_number or '', date=d(o.sent_on or o.created_at or date.today(), 'ar' if lang == 'ar' else 'en'))
    return c


def offer_text(lang, cand, pos, co, joining, valid):
    if lang == 'ar':
        return [f'يسرّ {co} أن تعرض عليكم وظيفة {pos} وفق الشروط والأحكام الواردة أدناه.',
                (f'نتوقع انضمامكم في {joining}. ' if joining else '') + (f'يسري هذا العرض حتى {valid}، و' if valid else 'و') + 'يخضع لاستكمال إجراءات التأشيرة وتصريح العمل والفحص الطبي وفق قوانين دولة الإمارات العربية المتحدة.']
    return [f'We are pleased to offer you the position of {pos} with {co} on the terms and conditions set out below.',
            (f'We look forward to you joining us on {joining}. ' if joining else '') + (f'This offer is valid until {valid} and is' if valid else 'This offer is')
            + ' subject to the visa, work permit and medical fitness formalities required under the laws of the United Arab Emirates.']


# ------------------------------------------------------------------ warning letter (disciplinary case)
def warning_ctx(o, lang='en'):
    emp = o.employee
    c = base('warning_letter', lang, emp, o.branch if o.branch_id else None)
    dec = o.get_decision_display() if o.decision else ''
    title = {'verbal': T(lang, 'Verbal warning – record', 'إنذار شفهي – محضر'), 'written': T(lang, 'Written warning', 'إنذار كتابي'),
             'final': T(lang, 'Final written warning', 'إنذار كتابي نهائي')}.get(o.decision) or (
        T(lang, 'Disciplinary decision', 'قرار تأديبي') if o.decision else T(lang, 'Show-cause notice', 'إشعار طلب إيضاح'))
    if o.decision:
        extra = f" – {o.fine_days:g} days' wage" if o.decision == 'fine' and o.fine_days else (f' – {o.suspension_days} days' if o.decision == 'suspension' else '')
        paras = [T(lang, f'This letter records the decision taken in disciplinary case {o.number} concerning "{o.title}" '
                         f'(incident of {d(o.incident_date, lang)}).', f'تسجل هذه الرسالة القرار المتخذ في القضية التأديبية رقم {o.number} بشأن "{o.title}".'),
                 T(lang, f'Decision: {dec}{extra}, effective {d(o.effective_date or o.decision_date, lang)}.', f'القرار: {dec}، اعتبارًا من {d(o.effective_date or o.decision_date, lang)}.')]
        paras += paragraphs(o.decision_notes)
        paras.append(T(lang, 'Any repetition may lead to further disciplinary action in line with the company policy and the UAE Labour Law. '
                             'You may appeal against this decision in writing to Human Resources within 7 days.',
                       'قد يؤدي تكرار المخالفة إلى اتخاذ إجراءات تأديبية إضافية وفقًا لسياسة الشركة وقانون العمل الإماراتي. يحق لك التظلم كتابيًا لدى الموارد البشرية خلال 7 أيام.'))
    else:
        paras = paragraphs(o.show_cause_text or o.description) or [o.title]
    c['case'] = {'number': o.number, 'subject': o.title, 'category': o.get_category_display(), 'incident_date': d(o.incident_date, lang),
                 'decision': dec, 'decision_date': d(o.decision_date, lang), 'effective_date': d(o.effective_date or o.decision_date, lang),
                 'status': o.get_status_display()}
    c['letter'] = {'title': title, 'addressee': c['employee'].get('name', ''), 'paragraphs': paras,
                   'signatory_name': c['company']['signatory_name'], 'signatory_title': c['company']['signatory_title'] or T(lang, 'Human Resources', 'الموارد البشرية')}
    c['doc'].update(number=o.number, date=d(o.decision_date or o.show_cause_sent or date.today(), 'ar' if lang == 'ar' else 'en'))
    return c


# ------------------------------------------------------------------ loan
def loan_ctx(o, lang='en'):
    emp = o.employee
    c = base('loan_agreement', lang, emp, o.branch if o.branch_id else None)
    amt = o.amount_requested or 0
    n = o.repayment_period or 0
    emi = o.emi_amount or (Decimal(amt) / n if n else 0)
    rep = list(M('PayrollManagement', 'LoanRepayment').objects.filter(loan=o).order_by('repayment_date', 'id'))
    sched, bal = [], Decimal(str(amt))
    start = o.disbursement_date or (o.approved_on.date() if isinstance(o.approved_on, datetime) else o.approved_on) or date.today()
    for i in range(int(n)):
        y, m = start.year + (start.month + i) // 12, (start.month + i) % 12 + 1
        paid = rep[i] if i < len(rep) else None
        amount = Decimal(str(paid.amount_paid)) if paid else min(Decimal(str(emi)), bal)
        bal = max(Decimal('0'), bal - amount)
        sched.append({'no': i + 1, 'date': period(y, m, lang), 'amount': money(amount), 'balance': money(bal),
                      'status': T(lang, 'Paid', 'مسدد') if paid else T(lang, 'Due', 'مستحق')})
    paid_total = sum((Decimal(str(r.amount_paid or 0)) for r in rep), Decimal('0'))
    c['loan'] = {'number': o.document_number or '', 'type': str(o.loan_type) if o.loan_type_id else '', 'amount': 'AED ' + money(amt), 'period': n,
                 'emi': 'AED ' + money(emi), 'disbursement_date': d(o.disbursement_date, lang), 'applied_on': d(o.applied_on, lang),
                 'approved_on': d(o.approved_on, lang), 'status': o.status, 'balance': 'AED ' + money(o.remaining_balance if o.remaining_balance is not None else amt - paid_total),
                 'paid': 'AED ' + money(paid_total), 'schedule': sched, 'terms': loan_terms(lang, money(emi))}
    c['doc'].update(number=o.document_number or '', status=o.status)
    c['signers'] = signers(c, lang, ('Employee (borrower)', 'الموظف (المقترض)', c['employee'].get('name'), '', False),
                           ('HR', 'الموارد البشرية', '', '', False), ('Approved by', 'اعتمده', c['company']['signatory_name'], '', True))
    return c


def loan_terms(lang, emi):
    if lang == 'ar':
        return [f'يُسدَّد القرض بأقساط شهرية قدرها {emi} درهم تُخصم من الراتب الشهري ابتداءً من الشهر المذكور في الجدول.',
                'في حال انتهاء الخدمة لأي سبب، يُخصم الرصيد المتبقي من مستحقات نهاية الخدمة.',
                'يوافق الموظف على الخصم من الراتب وفق هذا الجدول.']
    return [f'The loan is repaid in monthly instalments of AED {emi}, deducted from the salary starting with the first month of the schedule.',
            'If the employment ends for any reason, the outstanding balance is deducted from the final settlement.',
            'The employee authorises the company to make these deductions from the salary.']


# ------------------------------------------------------------------ expense claim
def expense_ctx(o, lang='en'):
    emp = M('EmpManagement', 'emp_master').objects.filter(pk=o.employee_id).first()
    c = base('expense_claim', lang, emp)
    lines = []
    for e in M('ExpenseManagement', 'Expense').objects.filter(report=o).select_related('category').order_by('date', 'id'):
        desc = ' – '.join(x for x in [e.merchant, e.description] if x)
        lines.append({'date': d(e.date, lang), 'category': _tr(e.category, 'name', lang) if e.category_id else '', 'merchant': e.merchant or '',
                      'description': desc, 'amount': money(e.amount), 'currency': e.currency or 'AED',
                      'amount_display': f'{e.currency or "AED"} {money(e.amount)}', 'amount_aed': money(e.amount_aed or e.amount)})
    c['claim'] = {'number': o.number or '', 'title': o.title or '', 'period': ' – '.join(x for x in [d(o.from_date, lang), d(o.to_date, lang)] if x),
                  'status': o.get_status_display() if hasattr(o, 'get_status_display') else o.status, 'submitted_at': d(o.submitted_at, lang),
                  'approved_at': d(o.approved_at, lang), 'total': money(o.total), 'advance': money(o.advance_applied),
                  'reimburse': money(o.amount_to_reimburse if o.amount_to_reimburse is not None else o.total),
                  'via': o.get_reimburse_via_display() if hasattr(o, 'get_reimburse_via_display') else (o.reimburse_via or ''),
                  'notes': o.notes or '', 'lines': lines}
    c['doc'].update(number=o.number or '', status=c['claim']['status'])
    c['signers'] = signers(c, lang, ('Claimed by', 'مقدم المطالبة', c['employee'].get('name'), '', False),
                           ('Approved by (manager)', 'اعتمده (المدير)', '', '', False), ('Finance', 'المالية', '', '', True))
    return c


# ------------------------------------------------------------------ leave approval letter
def leave_ctx(o, lang='en'):
    emp = o.employee
    c = base('leave_approval', lang, emp, o.branch if o.branch_id else None)
    lt = _tr(o.leave_type, 'name', lang) if o.leave_type_id else ''
    days = o.approved_days or o.number_of_days or o.applied_days or 0
    bal = M('calendars', 'emp_leave_balance').objects.filter(employee=emp, leave_type=o.leave_type).first() if o.leave_type_id else None
    st = (o.status or '').lower()
    c['leave'] = {'number': o.document_number or '', 'type': lt, 'start': d(o.start_date, lang), 'end': d(o.end_date, lang), 'days': f'{float(days):g}',
                  'status': T(lang, (o.status or '').title(), {'approved': 'معتمدة', 'pending': 'قيد الانتظار', 'rejected': 'مرفوضة'}.get(st, o.status or '')),
                  'reason': o.reason or '', 'applied_on': d(o.applied_on or o.created_at, lang), 'balance': f'{float(bal.balance):g}' if bal else ''}
    ret = d(o.end_date + timedelta(days=1), lang) if o.end_date else ''
    name = c['employee'].get('name', '')
    c['letter'] = {'title': T(lang, 'Leave approval', 'الموافقة على الإجازة'), 'addressee': name,
                   'paragraphs': [T(lang, f'Dear {name},', f'عزيزي/عزيزتي {name}،'),
                                  T(lang, f'This letter records your {lt} request from {c["leave"]["start"]} to {c["leave"]["end"]} ({c["leave"]["days"]} days). '
                                          f'Its status is: {c["leave"]["status"]}.',
                                    f'تسجل هذه الرسالة طلب {lt} من {c["leave"]["start"]} إلى {c["leave"]["end"]} ({c["leave"]["days"]} يوم). حالة الطلب: {c["leave"]["status"]}.')
                                  if st != 'approved' else
                                  T(lang, f'We are pleased to confirm that your {lt} from {c["leave"]["start"]} to {c["leave"]["end"]} '
                                          f'({c["leave"]["days"]} days) has been approved. You are expected to resume work on {ret}.',
                                    f'يسرنا إبلاغكم بالموافقة على {lt} من {c["leave"]["start"]} إلى {c["leave"]["end"]} ({c["leave"]["days"]} يوم). '
                                    f'يُتوقع عودتكم إلى العمل في {ret}.'),
                                  T(lang, 'Please make sure that your work is handed over before you leave. We wish you a pleasant leave.',
                                    'يرجى التأكد من تسليم مهامكم قبل بدء الإجازة. نتمنى لكم إجازة سعيدة.')],
                   'signatory_name': c['company']['signatory_name'], 'signatory_title': c['company']['signatory_title'] or T(lang, 'Human Resources', 'الموارد البشرية')}
    c['doc'].update(number=o.document_number or '', status=c['leave']['status'])
    return c


# ------------------------------------------------------------------ asset handover
def handover_ctx(o, lang='en'):
    emp = o.employee
    c = base('asset_handover', lang, emp, o.branch if o.branch_id else None)
    items = [{'asset': i.get('asset') or '', 'serial': i.get('serial') or '', 'condition': (i.get('condition') or '').title(),
              'accessories': i.get('accessories') or '', 'note': i.get('note') or ''} for i in (o.items or [])]
    sig = o.signature or ''
    if sig and not str(sig).startswith('data:image/'):
        sig = ''
    kind = T(lang, 'Handover' if o.kind == 'issue' else 'Return', 'تسليم' if o.kind == 'issue' else 'إرجاع')
    c['handover'] = {'number': o.number, 'kind': kind, 'date': d(o.date, lang), 'status': o.get_status_display(), 'terms': paragraphs(o.terms),
                     'damage_charge': ('AED ' + money(o.damage_charge)) if o.damage_charge else '', 'signed_by': o.signed_by_name or '',
                     'signed_at': d(o.signed_at, lang), 'signature': sig, 'items': items}
    c['doc'].update(number=o.number, status=o.get_status_display(),
                    title=T(lang, f'Asset {"handover" if o.kind == "issue" else "return"} form', f'نموذج {kind} الأصول') if lang != 'bi' else c['doc']['title'])
    c['signers'] = [{'label': T(lang, 'Employee', 'الموظف') if lang != 'bi' else 'Employee / الموظف', 'name': o.signed_by_name or c['employee'].get('name', ''),
                     'note': d(o.signed_at, lang), 'image': sig, 'stamp': ''}] + signers(
        c, lang, ('Issued by (IT / Admin)', 'سلّمه (تقنية المعلومات / الإدارة)', '', '', False), ('HR', 'الموارد البشرية', '', '', True))
    return c


# ------------------------------------------------------------------ loading records with the user's rights
SOURCES = {
    # doc type: (model label, employee field, employee field is an id, branch field)
    'payslip': ('PayrollManagement.Payslip', 'employee', False),
    'final_settlement': ('PayrollPlus.ExitSettlement', 'employee_id', True),
    'salary_certificate': ('SelfService.LetterRequest', 'employee_id', True),
    'experience_letter': ('SelfService.LetterRequest', 'employee_id', True),
    'noc': ('SelfService.LetterRequest', 'employee_id', True),
    'salary_transfer': ('SelfService.LetterRequest', 'employee_id', True),
    'offer_letter': ('RecruitmentManagement.Offer', 'employee', False),
    'warning_letter': ('ZeoHR.DisciplinaryCase', 'employee', False),
    'loan_agreement': ('PayrollManagement.LoanApplication', 'employee', False),
    'expense_claim': ('ExpenseManagement.ExpenseReport', 'employee_id', True),
    'leave_approval': ('calendars.employee_leave_request', 'employee', False),
    'asset_handover': ('ZeoHR.AssetHandover', 'employee', False),
}


def queryset(doc_type, c):
    """Records of the document type the user may print (c: AccessControl ctx). Employees: their own (released payslips only)."""
    label, ef, is_id = SOURCES[doc_type]
    try:
        qs = M(label).objects.all()
    except LookupError:
        return None
    if label == 'SelfService.LetterRequest':
        qs = qs.filter(letter_type__in=[k for k, v in LETTER_DOC.items() if v == doc_type])
    staff = c.admin or bool(BY_KEY[doc_type][4] & c.codes)
    if not staff:
        if c.emp is None or doc_type == 'offer_letter':
            return qs.none()
        qs = qs.filter(**{ef: c.emp.pk if is_id else c.emp})
        if doc_type == 'payslip':
            qs = qs.filter(status__in=('Approved', 'approved', 'paid', 'Paid'))
        if doc_type in ('salary_certificate', 'experience_letter', 'noc', 'salary_transfer'):
            qs = qs.filter(status='issued')
        return qs
    if c.branches is not None and not c.admin:
        if doc_type == 'offer_letter':
            qs = qs.filter(Q(branch_id__in=c.branches) | Q(branch__isnull=True))
        else:
            ids = list(M('EmpManagement', 'emp_master').objects.filter(emp_branch_id__in=c.branches).values_list('id', flat=True))
            qs = qs.filter(**{(ef if is_id else ef + '_id') + '__in': ids})
    return qs


def label_of(doc_type, o):
    try:
        if doc_type == 'payslip':
            return f'{o.employee.emp_code} {o.employee.emp_first_name} {o.employee.emp_last_name or ""} – {period(o.payroll_run.year, o.payroll_run.month, "en")}'
        if doc_type == 'offer_letter':
            return f'{o.document_number} – {o.application.candidate.full_name} ({o.position_title})'
        num = getattr(o, 'number', '') or getattr(o, 'document_number', '') or getattr(o, 'reference_no', '') or f'#{o.pk}'
        eid = getattr(o, 'employee_id', None)
        e = M('EmpManagement', 'emp_master').objects.filter(pk=eid).first() if eid else None
        who = f'{e.emp_code} {e.emp_first_name} {e.emp_last_name or ""}'.strip() if e else ''
        extra = getattr(o, 'letter_type', '') or getattr(o, 'title', '') or ''
        return ' – '.join(x for x in [num, who, str(extra).replace('_', ' ')] if x)
    except Exception:   # noqa: BLE001
        return f'#{o.pk}'


def records(doc_type, c, q='', limit=40):
    qs = queryset(doc_type, c)
    if qs is None:
        return []
    order = {'payslip': ('-payroll_run__year', '-payroll_run__month', 'employee__emp_code')}.get(doc_type, ('-id',))
    qs = qs.order_by(*order)
    if q:
        qs = _search(doc_type, qs, q)
    if doc_type == 'payslip':
        qs = qs.select_related('employee', 'payroll_run')
    return [{'id': o.pk, 'label': label_of(doc_type, o)} for o in qs[:limit]]


def _search(doc_type, qs, q):
    label, ef, is_id = SOURCES[doc_type]
    if doc_type == 'offer_letter':
        return qs.filter(Q(document_number__icontains=q) | Q(application__candidate__full_name__icontains=q))
    ids = list(M('EmpManagement', 'emp_master').objects.filter(Q(emp_code__icontains=q) | Q(emp_first_name__icontains=q) | Q(emp_last_name__icontains=q))
               .values_list('id', flat=True)[:500])
    return qs.filter(**{(ef if is_id else ef + '_id') + '__in': ids})


def build(doc_type, obj, lang, request=None):
    """Context of a record."""
    if doc_type == 'payslip':
        return payslip_ctx(obj, lang)
    if doc_type == 'final_settlement':
        return settlement_ctx(obj if isinstance(obj, dict) else settlement_case(obj, request), lang)
    if doc_type in ('salary_certificate', 'experience_letter', 'noc', 'salary_transfer'):
        return letter_ctx(obj, lang)
    return {'offer_letter': offer_ctx, 'warning_letter': warning_ctx, 'loan_agreement': loan_ctx, 'expense_claim': expense_ctx,
            'leave_approval': leave_ctx, 'asset_handover': handover_ctx}[doc_type](obj, lang)


def employee_of(doc_type, obj):
    if isinstance(obj, dict):
        return None
    label, ef, is_id = SOURCES[doc_type]
    return getattr(obj, ef, None) if is_id else getattr(obj, ef + '_id', None)


def branch_of(doc_type, obj):
    try:
        if isinstance(obj, dict):
            e = M('EmpManagement', 'emp_master').objects.filter(emp_code=obj.get('employee_code')).first()
            return e.emp_branch_id_id if e else None
        bid = getattr(obj, 'branch_id', None)
        if bid:
            return bid
        eid = employee_of(doc_type, obj)
        e = M('EmpManagement', 'emp_master').objects.filter(pk=eid).first() if eid else None
        return e.emp_branch_id_id if e else None
    except Exception:   # noqa: BLE001
        return None
