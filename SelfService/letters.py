"""v1.13.0 – HR letters: templates with placeholders, request → HR approval → PDF with reference number and verification code."""
import io
import logging
import os
import re
import secrets
from datetime import date

from django.apps import apps
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import connection
from django.utils import timezone

from . import profile as P
from .arabic import has_arabic, shape, visual_line
from .models import LETTER_TYPES, LetterRequest, LetterTemplate

log = logging.getLogger(__name__)
M = apps.get_model
TYPE_LABEL = dict(LETTER_TYPES)

PLACEHOLDERS = [
    ('employee_name', 'Employee full name'), ('employee_code', 'Employee code'), ('title', 'Mr / Ms'),
    ('designation', 'Designation'), ('department', 'Department'), ('branch', 'Branch'), ('nationality', 'Nationality'),
    ('joining_date', 'Joining date'), ('passport_no', 'Passport number'), ('emirates_id', 'Emirates ID'),
    ('basic_salary', 'Basic salary'), ('housing_allowance', 'Housing allowance'), ('transport_allowance', 'Transport allowance'),
    ('other_allowances', 'Other allowances'), ('gross_salary', 'Gross monthly salary'), ('net_salary', 'Net monthly salary'),
    ('currency', 'Currency'), ('bank_name', 'Bank (addressee bank)'), ('iban', 'IBAN of the salary account'),
    ('account_number', 'Account number of the salary account'), ('addressee', 'Addressee'), ('purpose', 'Purpose'),
    ('destination', 'Travel destination'), ('travel_from', 'Travel from'), ('travel_to', 'Travel to'),
    ('company_name', 'Company name'), ('today', "Today's date"), ('reference_no', 'Reference number'),
    ('verification_code', 'Verification code'), ('last_working_date', 'Last working date (if left)'),
]

EN = {
    'salary_certificate': ('Salary certificate',
        'This is to certify that {title} {employee_name} (employee code {employee_code}), holder of passport number {passport_no}, '
        'is employed with {company_name} as {designation} since {joining_date}.\n\n'
        'The monthly salary is as follows:\nBasic salary: {currency} {basic_salary}\nHousing allowance: {currency} {housing_allowance}\n'
        'Transport allowance: {currency} {transport_allowance}\nOther allowances: {currency} {other_allowances}\n'
        'Total monthly salary: {currency} {gross_salary}\n\n'
        'This certificate is issued at the request of the employee for {purpose}, without any liability on the company.', True),
    'salary_transfer': ('Salary transfer letter',
        'We confirm that {title} {employee_name} (employee code {employee_code}), holder of passport number {passport_no}, '
        'is employed with us as {designation} since {joining_date} with a total monthly salary of {currency} {gross_salary}.\n\n'
        'We undertake to transfer the monthly salary and end-of-service benefits of the employee to the account held with {bank_name} '
        '(IBAN {iban}) and not to change this arrangement without a clearance letter from your bank.\n\n'
        'This letter is issued at the request of the employee for {purpose}.', True),
    'noc_travel': ('No objection certificate – travel',
        'This is to certify that {title} {employee_name} (employee code {employee_code}), holder of passport number {passport_no}, '
        'is employed with {company_name} as {designation} since {joining_date}.\n\n'
        'The company has no objection to the employee travelling to {destination} from {travel_from} to {travel_to}. '
        'The employee will resume duty after the approved leave.\n\nPurpose: {purpose}', False),
    'noc_visa': ('No objection certificate – visa',
        'This is to certify that {title} {employee_name} (employee code {employee_code}), holder of passport number {passport_no}, '
        'is employed with {company_name} as {designation} since {joining_date}.\n\n'
        'The company has no objection to the employee applying for a visa to {destination}. '
        'All expenses of the trip will be borne by the employee.\n\nPurpose: {purpose}', False),
    'noc_other': ('No objection certificate',
        'This is to certify that {title} {employee_name} (employee code {employee_code}) is employed with {company_name} '
        'as {designation} since {joining_date}.\n\nThe company has no objection to {purpose}.', False),
    'experience': ('Experience certificate',
        'This is to certify that {title} {employee_name} (employee code {employee_code}) worked with {company_name} '
        'as {designation} in the {department} department from {joining_date} to {last_working_date}.\n\n'
        'During this period we found the employee sincere and hard working. We wish them every success.', False),
    'employment': ('Employment certificate',
        'This is to certify that {title} {employee_name} (employee code {employee_code}), {nationality} national, holder of passport '
        'number {passport_no}, is employed with {company_name} as {designation} in the {department} department since {joining_date}.\n\n'
        'This certificate is issued at the request of the employee for {purpose}.', False),
    'embassy': ('Letter to the embassy',
        'This is to certify that {title} {employee_name}, {nationality} national, holder of passport number {passport_no}, '
        'is employed with {company_name} as {designation} since {joining_date} with a total monthly salary of {currency} {gross_salary}.\n\n'
        'The employee wishes to travel to {destination} from {travel_from} to {travel_to}. We kindly request you to grant the visa. '
        'The employee will return to resume duty after the trip.', True),
    'bank_account': ('Bank account opening letter',
        'This is to certify that {title} {employee_name} (employee code {employee_code}), holder of passport number {passport_no}, '
        'is employed with {company_name} as {designation} since {joining_date} with a total monthly salary of {currency} {gross_salary}.\n\n'
        'We have no objection to the employee opening an account with {bank_name}.', True),
}
AR_INTRO = 'نشهد بأن {employee_name}، الرقم الوظيفي {employee_code}، ويحمل جواز سفر رقم {passport_no}، يعمل لدى {company_name} بوظيفة {designation} منذ {joining_date}.'
AR = {
    'salary_certificate': ('شهادة راتب', AR_INTRO + '\n\nويتقاضى راتباً شهرياً إجمالياً قدره {gross_salary} {currency}، منها الراتب الأساسي {basic_salary} {currency}.\n\nأعطيت هذه الشهادة بناءً على طلبه لغرض {purpose} دون أدنى مسؤولية على الشركة.', True),
    'salary_transfer': ('رسالة تحويل راتب', AR_INTRO + '\n\nونتعهد بتحويل راتبه الشهري البالغ {gross_salary} {currency} ومستحقات نهاية الخدمة إلى حسابه لدى {bank_name} رقم الآيبان {iban}، وعدم تغيير ذلك إلا بموجب براءة ذمة من البنك.', True),
    'noc_travel': ('شهادة عدم ممانعة للسفر', AR_INTRO + '\n\nولا مانع لدى الشركة من سفره إلى {destination} خلال الفترة من {travel_from} إلى {travel_to}.', False),
    'noc_visa': ('شهادة عدم ممانعة للتأشيرة', AR_INTRO + '\n\nولا مانع لدى الشركة من تقدمه بطلب تأشيرة إلى {destination}.', False),
    'noc_other': ('شهادة عدم ممانعة', AR_INTRO + '\n\nولا مانع لدى الشركة من {purpose}.', False),
    'experience': ('شهادة خبرة', 'نشهد بأن {employee_name}، الرقم الوظيفي {employee_code}، عمل لدى {company_name} بوظيفة {designation} من {joining_date} إلى {last_working_date}.\n\nوقد كان مثالاً للجد والإخلاص، ونتمنى له التوفيق.', False),
    'employment': ('شهادة عمل', AR_INTRO + '\n\nأعطيت هذه الشهادة بناءً على طلبه لغرض {purpose}.', False),
    'embassy': ('رسالة إلى السفارة', AR_INTRO + '\n\nويتقاضى راتباً شهرياً قدره {gross_salary} {currency}، ويرغب في السفر إلى {destination} من {travel_from} إلى {travel_to}. نرجو التكرم بمنحه التأشيرة.', True),
    'bank_account': ('رسالة فتح حساب بنكي', AR_INTRO + '\n\nويتقاضى راتباً شهرياً قدره {gross_salary} {currency}، ولا مانع لدينا من فتح حساب له لدى {bank_name}.', True),
}
NEEDS = {
    'salary_transfer': ('bank_name',), 'bank_account': ('bank_name',), 'noc_travel': ('destination', 'travel_from', 'travel_to'),
    'noc_visa': ('destination',), 'embassy': ('destination', 'travel_from', 'travel_to'),
}


def ensure_templates():
    """Create the default templates once (HR edits them in Self-service settings → Letter templates)."""
    if LetterTemplate.objects.exists():
        return
    for t, (title, body, sal) in EN.items():
        LetterTemplate.objects.get_or_create(letter_type=t, language='en', branch_id=None, defaults={'title': title, 'body': body, 'show_salary': sal,
                                             'signatory_title': 'Human Resources'})
    for t, (title, body, sal) in AR.items():
        LetterTemplate.objects.get_or_create(letter_type=t, language='ar', branch_id=None, defaults={'title': title, 'body': body, 'show_salary': sal,
                                             'signatory_title': 'الموارد البشرية'})


def template_for(letter_type, language, branch_id):
    ensure_templates()
    qs = LetterTemplate.objects.filter(letter_type=letter_type, language=language, is_active=True)
    return qs.filter(branch_id=branch_id).first() or qs.filter(branch_id__isnull=True).first()


def _money(v):
    return f'{v:,.2f}'


def values(emp, lr):
    fig = P.salary_figures(emp)
    ident = P.employee_identity(emp)
    bank = M('EmpManagement', 'EmployeeBankDetail').objects.filter(employee=emp, is_active=True).order_by('-id').first()
    try:
        co = company()
        cur = (getattr(co.currency, 'currency_code', None) or 'AED') if getattr(co, 'currency_id', None) else 'AED'
    except Exception:
        cur = 'AED'
    passport = getattr(ident, 'passport_no', '') if ident is not None else ''
    if not passport:
        doc = M('EmpManagement', 'Emp_Documents').objects.filter(emp_id=emp, document_type__type_name__icontains='passport').order_by('-emp_doc_expiry_date').first()
        passport = doc.emp_doc_number if doc else ''
    g = (emp.emp_gender or '').lower()
    info = P.employment(emp)
    leave = info.get('date_of_leaving')
    v = {
        'employee_name': ' '.join(x for x in [emp.emp_first_name, emp.emp_middle_name, emp.emp_last_name] if x),
        'employee_code': emp.emp_code, 'title': 'Mr.' if g.startswith('m') else ('Ms.' if g.startswith('f') else ''),
        'designation': getattr(emp.emp_desgntn_id, 'desgntn_job_title', '') if emp.emp_desgntn_id_id else '',
        'department': getattr(emp.emp_dept_id, 'dept_name', '') if emp.emp_dept_id_id else '',
        'branch': getattr(emp.emp_branch_id, 'branch_name', '') if emp.emp_branch_id_id else '',
        'nationality': getattr(emp.emp_nationality, 'N_name', '') if emp.emp_nationality_id else '',
        'joining_date': emp.emp_joined_date.strftime('%d %B %Y') if emp.emp_joined_date else '',
        'passport_no': passport or '', 'emirates_id': getattr(ident, 'emirates_id', '') if ident is not None else '',
        'basic_salary': _money(fig['basic']), 'housing_allowance': _money(fig['housing']), 'transport_allowance': _money(fig['transport']),
        'other_allowances': _money(fig['other']), 'gross_salary': _money(fig['gross']), 'net_salary': _money(fig['net']), 'currency': cur,
        'bank_name': lr.bank_name or (bank.bank_name if bank else '') or '', 'iban': (bank.iban_number if bank else '') or '',
        'account_number': (bank.account_number if bank else '') or '', 'addressee': lr.addressee or 'To whom it may concern',
        'purpose': lr.purpose or 'personal use', 'destination': lr.destination or '',
        'travel_from': lr.travel_from.strftime('%d %B %Y') if lr.travel_from else '', 'travel_to': lr.travel_to.strftime('%d %B %Y') if lr.travel_to else '',
        'company_name': company_name(), 'today': date.today().strftime('%d %B %Y'),
        'reference_no': lr.reference_no or '', 'verification_code': lr.verification_code or '',
        'last_working_date': leave.strftime('%d %B %Y') if leave else 'date',
    }
    if lr.language == 'ar' and not lr.addressee:
        v['addressee'] = 'إلى من يهمه الأمر'
        v['purpose'] = lr.purpose or 'الاستخدام الشخصي'
    return v


def company():
    t = getattr(connection, 'tenant', None)
    if getattr(t, 'name', None) and getattr(t, 'schema_name', None) == connection.schema_name:
        return t
    try:
        from django_tenants.utils import get_tenant_model
        return get_tenant_model().objects.filter(schema_name=connection.schema_name).first()  # shared table, visible from the tenant
    except Exception:
        return None


def company_name():
    return getattr(company(), 'name', '') or ''


def fill(text, vals):
    return re.sub(r'\{(\w+)\}', lambda m: str(vals.get(m.group(1), m.group(0))), text or '')


# ------------------------------------------------------------------------------------------------ PDF
_FONT = None


def _font():
    global _FONT
    if _FONT is not None:
        return _FONT
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    for path, bold in (('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'),):
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont('EssSans', path))
                pdfmetrics.registerFont(TTFont('EssSans-Bold', bold if os.path.exists(bold) else path))
                _FONT = ('EssSans', 'EssSans-Bold')
                return _FONT
            except Exception:
                log.debug('font registration failed', exc_info=True)
    _FONT = ('Helvetica', 'Helvetica-Bold')
    return _FONT


def _wrap(text, font, size, width, rtl):
    from reportlab.pdfbase.pdfmetrics import stringWidth
    lines = []
    for para in text.split('\n'):
        if not para.strip():
            lines.append('')
            continue
        words, cur = para.split(' '), ''
        for w in words:
            t = (cur + ' ' + w).strip()
            meas = visual_line(t) if rtl else t
            if stringWidth(meas, font, size) <= width or not cur:
                cur = t
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
    return lines


def _logo_path(emp):
    for obj, attr in ((emp.emp_branch_id if emp.emp_branch_id_id else None, 'branch_logo'), (company(), 'logo')):
        f = getattr(obj, attr, None) if obj is not None else None
        try:
            if f and f.name and os.path.exists(f.path):
                return f.path
        except Exception:
            continue
    return None


def render_pdf(emp, lr, tpl, vals):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    font, bold = _font()
    rtl = lr.language == 'ar'
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    left, right = 20 * mm, W - 20 * mm
    c.setTitle(f'{TYPE_LABEL.get(lr.letter_type, "Letter")} {lr.reference_no}')

    def txt(x, y, s, f=font, size=10.5, align='left'):
        s = visual_line(s) if (rtl or has_arabic(s)) else s
        c.setFont(f, size)
        if align == 'right':
            c.drawRightString(x, y, s)
        elif align == 'center':
            c.drawCentredString(x, y, s)
        else:
            c.drawString(x, y, s)

    # letterhead
    y = H - 22 * mm
    logo = _logo_path(emp)
    if logo:
        try:
            c.drawImage(logo, left, y - 10 * mm, width=28 * mm, height=16 * mm, preserveAspectRatio=True, mask='auto')
        except Exception:
            logo = None
    br = emp.emp_branch_id if emp.emp_branch_id_id else None
    txt(right, y, vals['company_name'] or 'Company', bold, 15, 'right')
    lines = [x for x in [getattr(br, 'branch_name', ''), (getattr(br, 'branch_address', '') or '').replace('\n', ', '),
                         ' · '.join(x for x in [getattr(br, 'br_branch_nmbr_1', ''), getattr(br, 'br_branch_mail', '')] if x)] if x]
    yy = y - 5 * mm
    for ln in lines:
        txt(right, yy, ln[:110], font, 8.5, 'right')
        yy -= 4 * mm
    c.setStrokeColorRGB(0.12, 0.35, 0.6)
    c.setLineWidth(1.2)
    c.line(left, yy - 1 * mm, right, yy - 1 * mm)
    y = yy - 9 * mm
    # reference and date
    ref = f'Ref: {lr.reference_no}' if not rtl else f'المرجع: {lr.reference_no}'
    dt = f'Date: {date.today():%d %B %Y}' if not rtl else f'التاريخ: {date.today():%d/%m/%Y}'
    if rtl:
        txt(right, y, ref, font, 9.5, 'right')
        txt(left, y, dt, font, 9.5)
    else:
        txt(left, y, ref, font, 9.5)
        txt(right, y, dt, font, 9.5, 'right')
    y -= 12 * mm
    # addressee and title
    to = vals['addressee']
    if rtl:
        txt(right, y, f'إلى: {to}' if not to.startswith('إلى') else to, bold, 11, 'right')
    else:
        txt(left, y, f'To: {to}', bold, 11)
    y -= 12 * mm
    txt(W / 2, y, fill(tpl.title, vals), bold, 14, 'center')
    y -= 12 * mm
    body = fill(tpl.body, vals)
    for ln in _wrap(body, font, 10.5, right - left, rtl):
        if y < 45 * mm:
            c.showPage()
            y = H - 25 * mm
        if ln:
            txt(right if rtl else left, y, ln, font, 10.5, 'right' if rtl else 'left')
        y -= 6 * mm
    # signature
    y -= 10 * mm
    sx, al = (right, 'right') if rtl else (left, 'left')
    txt(sx, y, 'مع التحية،' if rtl else 'Yours faithfully,', font, 10.5, al)
    y -= 16 * mm
    if tpl.signatory_name:
        txt(sx, y, tpl.signatory_name, bold, 10.5, al)
        y -= 5 * mm
    txt(sx, y, tpl.signatory_title or ('الموارد البشرية' if rtl else 'Human Resources'), font, 10, al)
    txt(sx, y - 5 * mm, vals['company_name'], font, 10, al)
    # footer: verification
    c.setStrokeColorRGB(0.8, 0.8, 0.8)
    c.setLineWidth(0.6)
    c.line(left, 22 * mm, right, 22 * mm)
    foot = f'Verification code: {lr.verification_code}  ·  Reference: {lr.reference_no}  ·  Verify with HR or in ZEO HRMS → Self service → Verify a letter.'
    c.setFont(font, 7.5)
    c.drawString(left, 17 * mm, foot)
    if tpl.footer:
        txt(left, 13 * mm, fill(tpl.footer, vals)[:150], font, 7.5)
    c.showPage()
    c.save()
    return buf.getvalue()


def new_reference():
    year = timezone.now().year
    base = f'HRL-{year}-'
    last = LetterRequest.objects.filter(reference_no__startswith=base).order_by('-id').values_list('reference_no', flat=True).first()
    n = int(last.rsplit('-', 1)[1]) + 1 if last else 1
    return f'{base}{n:05d}'


def new_code():
    alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    return ''.join(secrets.choice(alphabet) for _ in range(10))


def validate_request(data):
    t = data.get('letter_type')
    if t not in TYPE_LABEL:
        raise ValidationError({'letter_type': 'Choose the type of letter.'})
    lang = data.get('language') or 'en'
    if lang not in ('en', 'ar'):
        raise ValidationError({'language': 'Choose English or Arabic.'})
    missing = [k for k in NEEDS.get(t, ()) if not data.get(k)]
    if missing:
        labels = {'bank_name': 'the bank', 'destination': 'the destination', 'travel_from': 'the travel start date', 'travel_to': 'the return date'}
        raise ValidationError({k: f'Enter {labels.get(k, k)} for this letter.' for k in missing})
    if data.get('travel_from') and data.get('travel_to') and str(data['travel_to']) < str(data['travel_from']):
        raise ValidationError({'travel_to': 'The return date cannot be before the travel date.'})
    return t, lang


def issue(lr, user, note=''):
    """Generate the PDF and mark the letter issued."""
    emp = M('EmpManagement', 'emp_master').objects.select_related('emp_branch_id', 'emp_desgntn_id', 'emp_dept_id', 'emp_nationality').get(pk=lr.employee_id)
    tpl = template_for(lr.letter_type, lr.language, lr.branch_id)
    if tpl is None:
        raise ValidationError({'template': 'There is no active template for this letter type and language – add one in Letter templates.'})
    if not lr.reference_no:
        lr.reference_no = new_reference()
    if not lr.verification_code:
        lr.verification_code = new_code()
    vals = values(emp, lr)
    pdf = render_pdf(emp, lr, tpl, vals)
    lr.body_snapshot = fill(tpl.body, vals)
    lr.pdf.save(f'{lr.reference_no}-{lr.verification_code}.pdf', ContentFile(pdf), save=False)
    lr.status, lr.decided_by_id, lr.decided_at, lr.decision_note = 'issued', getattr(user, 'pk', None), timezone.now(), (note or '')[:500]
    lr.save()
    return lr
