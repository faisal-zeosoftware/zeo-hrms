"""v1.62.0 print – the sample designs: blocks (template snippets) per document type, the four looks (CSS) and the composer that
turns block-editor settings into the HTML / CSS of a template.

Labels are written [[English|Arabic]] in the block sources; the composer keeps the language of the template (English, Arabic
or both, the Arabic under the English). The result is plain Django-template HTML – the "HTML / CSS" tab of the editor starts
from exactly this text.
"""
import re

# ------------------------------------------------------------------ helpers
LBL = re.compile(r'\[\[([^|\]]*)\|([^\]]*)\]\]')


def labels(src, lang):
    if lang == 'ar':
        return LBL.sub(lambda m: m.group(2), src)
    if lang == 'bi':
        return LBL.sub(lambda m: f'{m.group(1)}<span class="ar-sep"> / </span><span class="ar-l">{m.group(2)}</span>', src)
    return LBL.sub(lambda m: m.group(1), src)


def kv(rows, cols=2, cls='kv'):
    """rows: [('Label|تسمية', 'employee.name'), …] → a key / value table with `cols` pairs per line (empty values kept)."""
    out, line = [], []
    for lab, var in rows:
        en, ar = (lab.split('|') + [''])[:2]
        val = var if var.startswith('{') else '{{ ' + var + ' }}'
        line.append(f'<th>[[{en}|{ar}]]</th><td>{val}</td>')
        if len(line) == cols:
            out.append('<tr>' + ''.join(line) + '</tr>')
            line = []
    if line:
        line += ['<th></th><td></td>'] * (cols - len(line))
        out.append('<tr>' + ''.join(line) + '</tr>')
    return f'<table class="{cls} cols{cols}">' + '\n'.join(out) + '</table>'


def sec(title_en, title_ar, inner, cls=''):
    return f'<section class="blk {cls}"><h3 class="sec-title">[[{title_en}|{title_ar}]]</h3>{inner}</section>'


LOGO = ('{% if company.logo %}<img class="logo" src="{{ company.logo }}" alt="">{% else %}'
        '<div class="mono">{{ company.initials }}</div>{% endif %}')
CO_META = ('{% if company.address %}<div class="co-meta">{{ company.address }}</div>{% endif %}'
           '{% if company.contact_line %}<div class="co-meta">{{ company.contact_line }}</div>{% endif %}'
           '{% if company.trn %}<div class="co-meta">[[TRN|الرقم الضريبي]] {{ company.trn }}{% if company.licence_no %} · [[Licence|الرخصة]] {{ company.licence_no }}{% endif %}</div>{% endif %}'
           '{% if company.header_text %}<div class="co-meta">{{ company.header_text|linebreaksbr }}</div>{% endif %}')

HEADERS = {
    'letterhead': ('<header class="blk lh lh-{{ brand.logo_position }}"><div class="lh-logo">' + LOGO + '</div>'
                   '<div class="lh-co"><div class="co-name">{{ company.name }}</div>'
                   '{% if company.name_ar %}<div class="co-name-ar">{{ company.name_ar }}</div>{% endif %}' + CO_META + '</div></header>'),
    'band': ('<header class="blk band"><div class="band-logo">' + LOGO + '</div>'
             '<div class="band-co"><div class="co-name">{{ company.name }}</div>'
             '{% if company.name_ar %}<div class="co-name-ar">{{ company.name_ar }}</div>{% endif %}' + CO_META + '</div>'
             '<div class="band-doc">@@BANDDOC@@</div></header>'),
    'line': ('<header class="blk hline"><div class="hl-logo">' + LOGO + '</div>'
             '<div class="hl-co"><div class="co-name">{{ company.name }}</div><div class="co-meta">{{ company.address }}'
             '{% if company.trn %} · TRN {{ company.trn }}{% endif %}{% if company.phone %} · {{ company.phone }}{% endif %}</div></div>'
             '<div class="hl-doc">@@BANDDOC@@</div></header>'),
    'bilingual': ('{% if company.name_ar %}<header class="blk hbi"><div class="hbi-en"><div class="co-name">{{ company.name }}</div>' + CO_META + '</div>'
                  '<div class="hbi-logo">' + LOGO + '</div>'
                  '<div class="hbi-ar" dir="rtl"><div class="co-name">{{ company.name_ar|default:company.name }}</div>'
                  '{% if company.address_ar %}<div class="co-meta">{{ company.address_ar }}</div>{% endif %}'
                  '{% if company.trn %}<div class="co-meta">الرقم الضريبي {{ company.trn }}</div>{% endif %}'
                  '{% if company.phone %}<div class="co-meta">هاتف {{ company.phone }}</div>{% endif %}</div></header>'
                  '{% else %}@@LETTERHEAD@@{% endif %}'),
}
BANDDOC_FORM = ('<div class="bd-title">{{ doc.title }}</div>{% if doc.title_ar %}<div class="bd-title-ar">{{ doc.title_ar }}</div>{% endif %}'
                '<div class="bd-sub">{{ doc.subtitle }}</div>{% if doc.number %}<div class="bd-sub">[[No.|رقم]] {{ doc.number }}</div>{% endif %}')
BANDDOC_LETTER = ('{% if doc.number %}<div class="bd-sub">[[Ref.|المرجع]] {{ doc.number }}</div>{% endif %}'
                  '<div class="bd-sub">[[Date|التاريخ]] {{ doc.date }}</div>')

TITLE = ('<div class="blk doc-head"><h1 class="doc-title">{{ doc.title }}{% if doc.title_ar %}<span class="ar-l">{{ doc.title_ar }}</span>{% endif %}</h1>'
         '{% if doc.subtitle %}<div class="doc-sub">{{ doc.subtitle }}</div>{% endif %}'
         '<div class="doc-meta">{% if doc.number %}[[No.|رقم]] <b>{{ doc.number }}</b> · {% endif %}[[Date|التاريخ]] <b>{{ doc.date }}</b>'
         '{% if doc.status %} · [[Status|الحالة]] <b>{{ doc.status }}</b>{% endif %}</div></div>')
LETTER_TITLE = ('<div class="blk letter-title"><h1>{{ letter.title }}</h1></div>')
REFLINE = ('<div class="blk refline"><div>[[Ref.|المرجع]]: <b>{{ doc.number }}</b></div><div>[[Date|التاريخ]]: <b>{{ doc.date }}</b></div></div>')
ADDRESSEE = '<div class="blk addressee">{{ letter.addressee }}</div>'
BODY = ('<div class="blk body">{% for p in letter.paragraphs %}<p>{{ p }}</p>{% endfor %}</div>')
CLOSING = ('<div class="blk closing"><p>{{ letter.closing }}</p></div>')
SIGNATURES = ('<div class="blk sigs"><table class="sig-row"><tr>{% for s in signers %}<td class="sig">'
              '<div class="sig-space">{% if s.image %}<img class="sig-img" src="{{ s.image }}" alt="">{% endif %}'
              '{% if s.stamp %}<img class="stamp-img" src="{{ s.stamp }}" alt="">{% endif %}</div>'
              '<div class="sig-line"></div><div class="sig-label">{{ s.label }}</div>'
              '{% if s.name %}<div class="sig-name">{{ s.name }}</div>{% endif %}{% if s.note %}<div class="sig-note">{{ s.note }}</div>{% endif %}'
              '</td>{% endfor %}</tr></table></div>')
LETTER_SIGN = ('<div class="blk letter-sign"><div class="ls-closing">[[Yours faithfully,|وتفضلوا بقبول فائق الاحترام،]]</div>'
               '<div class="ls-space">{% if company.signature and brand.show_signature %}<img class="sig-img" src="{{ company.signature }}" alt="">{% endif %}'
               '{% if company.stamp and brand.show_stamp %}<img class="stamp-img" src="{{ company.stamp }}" alt="">{% endif %}</div>'
               '<div class="ls-name">{{ letter.signatory_name }}</div><div class="ls-title">{{ letter.signatory_title }}</div>'
               '<div class="ls-co">{{ company.name }}</div></div>')
VERIFY = ('{% if doc.verification_code %}<div class="blk verify">[[Verification code|رمز التحقق]] <b>{{ doc.verification_code }}</b> · '
          '[[This letter can be verified with Human Resources.|يمكن التحقق من هذه الرسالة لدى الموارد البشرية.]]</div>{% endif %}')
FOOTER = ('<div class="run-foot"><div class="rf-line">{% if company.footer_text %}{{ company.footer_text }}{% else %}{{ company.name }}'
          '{% if company.address %} · {{ company.address }}{% endif %}{% endif %}</div>'
          '<div class="rf-sub">[[This document was generated by ZEO HRMS on|أُنشئ هذا المستند بواسطة ZEO HRMS في]] {{ doc.generated }} · '
          '[[Page|صفحة]] <span class="pg"></span></div></div>')
WATERMARK = '{% if brand.watermark %}<div class="watermark">{{ brand.watermark_text }}</div>{% endif %}'

SALARY_TABLE = sec('Salary details', 'تفاصيل الراتب',
                   '<table class="lines"><thead><tr><th>[[Component|البند]]</th><th class="num">[[Monthly amount|المبلغ الشهري]] ({{ salary.currency }})</th></tr></thead><tbody>'
                   '<tr><td>[[Basic salary|الراتب الأساسي]]</td><td class="num">{{ salary.basic }}</td></tr>'
                   '<tr><td>[[Housing allowance|بدل السكن]]</td><td class="num">{{ salary.housing }}</td></tr>'
                   '<tr><td>[[Transport allowance|بدل المواصلات]]</td><td class="num">{{ salary.transport }}</td></tr>'
                   '<tr><td>[[Other allowances|بدلات أخرى]]</td><td class="num">{{ salary.other }}</td></tr></tbody>'
                   '<tfoot><tr><td>[[Total monthly salary|إجمالي الراتب الشهري]]</td><td class="num">{{ salary.gross }}</td></tr></tfoot></table>', 'salary')


def lines_table(var, cols, total=None, cls='lines'):
    """cols: [('Label|تسمية', 'field', 'num'?)]"""
    head = ''.join(f'<th class="{c[2] if len(c) > 2 else ""}">[[{c[0].split("|")[0]}|{c[0].split("|")[1]}]]</th>' for c in cols)
    body = ''.join(f'<td class="{c[2] if len(c) > 2 else ""}">{{{{ l.{c[1]} }}}}</td>' for c in cols)
    foot = ''
    if total:
        lab, tv = total
        foot = (f'<tfoot><tr><td colspan="{len(cols) - 1}">[[{lab.split("|")[0]}|{lab.split("|")[1]}]]</td>'
                f'<td class="num">{{{{ {tv} }}}}</td></tr></tfoot>')
    return (f'<table class="{cls}"><thead><tr>{head}</tr></thead><tbody>{{% for l in {var} %}}<tr>{body}</tr>'
            f'{{% empty %}}<tr><td colspan="{len(cols)}" class="muted">–</td></tr>{{% endfor %}}</tbody>{foot}</table>')


def tiles(items, cls='tiles'):
    """items: [('Label|تسمية', 'var', 'extra-class')]"""
    cells = ''.join(f'<td class="tile-c"><div class="tile {t[2] if len(t) > 2 else ""}"><div class="t-l">[[{t[0].split("|")[0]}|{t[0].split("|")[1]}]]</div>'
                    f'<div class="t-v">{{{{ {t[1]} }}}}</div></div></td>' for t in items)
    return f'<table class="{cls}"><tr>{cells}</tr></table>'


# ------------------------------------------------------------------ blocks per document type
EMP_ROWS = [('Employee|الموظف', 'employee.name'), ('Employee code|رقم الموظف', 'employee.code'),
            ('Designation|المسمى الوظيفي', 'employee.designation'), ('Department|القسم', 'employee.department')]

PAYSLIP = {
    'employee': ('Employee details', sec('Employee details', 'بيانات الموظف', kv(EMP_ROWS + [
        ('Branch|الفرع', 'employee.branch'), ('Date of joining|تاريخ الالتحاق', 'employee.joined'),
        ('Pay period|فترة الراتب', 'payslip.period'), ('Payment date|تاريخ الدفع', 'payslip.pay_date'),
        ('Labour card no.|رقم بطاقة العمل', 'employee.labour_card_no'), ('Emirates ID|الهوية الإماراتية', 'employee.emirates_id_masked')]))),
    'attendance': ('Attendance days', sec('Attendance', 'الحضور', tiles([
        ('Working days|أيام العمل', 'payslip.working_days'), ('Days worked|أيام الحضور', 'payslip.days_worked'),
        ('Unpaid days|أيام بدون أجر', 'payslip.unpaid_days'), ('Overtime hours|ساعات العمل الإضافي', 'payslip.overtime_hours')], 'tiles small'))),
    'earnings': ('Earnings and deductions', '<section class="blk"><table class="ed"><tr><td class="ed-col">'
                 + lines_table('payslip.earnings', [('Earnings|الاستحقاقات', 'name'), ('Amount|المبلغ', 'amount', 'num')],
                               ('Total earnings|إجمالي الاستحقاقات', 'payslip.total_earnings'), 'lines earn')
                 + '</td><td class="ed-gap"></td><td class="ed-col">'
                 + lines_table('payslip.deductions', [('Deductions|الاستقطاعات', 'name'), ('Amount|المبلغ', 'amount', 'num')],
                               ('Total deductions|إجمالي الاستقطاعات', 'payslip.total_deductions'), 'lines ded')
                 + '</td></tr></table><div class="cur-note">[[Amounts in|المبالغ بعملة]] {{ payslip.currency }}</div></section>'),
    'totals': ('Totals', '<section class="blk">' + tiles([('Gross pay|إجمالي الراتب', 'payslip.gross'),
                                                         ('Total deductions|إجمالي الاستقطاعات', 'payslip.total_deductions'),
                                                         ('Net pay|صافي الراتب', 'payslip.net_display', 'net')]) + '</section>'),
    'net_words': ('Net pay in words', '<div class="blk words"><b>[[Net pay in words|صافي الراتب كتابةً]]:</b> {{ payslip.net_words }}'
                  '{% if payslip.net_words_ar %}<div class="words-ar" dir="rtl">{{ payslip.net_words_ar }}</div>{% endif %}</div>'),
    'ytd': ('Year to date', sec('Year to date', 'منذ بداية السنة', '<table class="lines ytd"><thead><tr><th>[[Year|السنة]]</th>'
                                '<th class="num">[[Payslips|عدد القسائم]]</th><th class="num">[[Gross|الإجمالي]]</th>'
                                '<th class="num">[[Deductions|الاستقطاعات]]</th><th class="num">[[Net|الصافي]]</th></tr></thead>'
                                '<tbody><tr><td>{{ ytd.year }}</td><td class="num">{{ ytd.count }}</td><td class="num">{{ ytd.gross }}</td>'
                                '<td class="num">{{ ytd.deductions }}</td><td class="num">{{ ytd.net }}</td></tr></tbody></table>', 'half')),
    'leave': ('Leave balance', sec('Leave balance', 'رصيد الإجازات', '{% if leave %}<table class="lines leave"><thead><tr><th>[[Leave type|نوع الإجازة]]</th>'
                                   '<th class="num">[[Balance (days)|الرصيد (أيام)]]</th></tr></thead><tbody>{% for l in leave %}<tr><td>{{ l.type }}</td>'
                                   '<td class="num">{{ l.balance }}</td></tr>{% endfor %}</tbody></table>{% else %}<p class="muted">–</p>{% endif %}', 'half')),
    'bank': ('Bank / IBAN', '<div class="blk bank">[[Paid by|طريقة الدفع]] <b>{{ bank.mode }}</b>{% if bank.name %} · [[Bank|البنك]] <b>{{ bank.name }}</b>{% endif %}'
             '{% if bank.iban_masked %} · IBAN <b>{{ bank.iban_masked }}</b>{% endif %}</div>'),
}

SETTLEMENT = {
    'employee': ('Employee and exit', sec('Employee and exit', 'الموظف وإنهاء الخدمة', kv(EMP_ROWS + [
        ('Branch|الفرع', 'employee.branch'), ('Date of joining|تاريخ الالتحاق', 'settlement.joined'),
        ('Last working day|آخر يوم عمل', 'settlement.last_working_date'), ('Exit type|نوع إنهاء الخدمة', 'settlement.exit_type'),
        ('Total service|مدة الخدمة', 'settlement.service'), ('Unpaid days|أيام بدون أجر', 'settlement.unpaid_days'),
        ('Basic salary|الراتب الأساسي', 'settlement.basic'), ('Monthly wage|الأجر الشهري', 'settlement.wage')]))),
    'earnings': ('Earnings', sec('Earnings', 'المستحقات', lines_table('settlement.earnings', [
        ('Item|البند', 'label'), ('Basis|الأساس', 'basis', 'basis'), ('Amount|المبلغ', 'amount', 'num')],
        ('Total earnings|إجمالي المستحقات', 'settlement.total_earnings'), 'lines earn'))),
    'deductions': ('Deductions', sec('Deductions', 'الاستقطاعات', lines_table('settlement.deductions', [
        ('Item|البند', 'label'), ('Basis|الأساس', 'basis', 'basis'), ('Amount|المبلغ', 'amount', 'num')],
        ('Total deductions|إجمالي الاستقطاعات', 'settlement.total_deductions'), 'lines ded'))),
    'notes': ('Notes', '{% if settlement.notes %}<div class="blk notes"><b>[[Notes|ملاحظات]]</b><ul>{% for n in settlement.notes %}<li>{{ n }}</li>{% endfor %}</ul></div>{% endif %}'),
    'totals': ('Net payable', '<section class="blk">' + tiles([('Total earnings|إجمالي المستحقات', 'settlement.total_earnings'),
                                                              ('Total deductions|إجمالي الاستقطاعات', 'settlement.total_deductions'),
                                                              ('Net payable|صافي المستحق', 'settlement.net_display', 'net')]) + '</section>'),
    'net_words': ('Amount in words', '<div class="blk words"><b>[[Amount in words|المبلغ كتابةً]]:</b> {{ settlement.net_words }}'
                  '{% if settlement.net_words_ar %}<div class="words-ar" dir="rtl">{{ settlement.net_words_ar }}</div>{% endif %}</div>'),
    'payment': ('Payment', '<div class="blk payment">{% if settlement.paid_on %}[[Paid on|دُفع في]] <b>{{ settlement.paid_on }}</b> · [[Reference|المرجع]] '
                '<b>{{ settlement.payment_ref }}</b>{% else %}[[To be paid by|يُدفع في موعد أقصاه]] <b>{{ settlement.due_date }}</b> '
                '([[UAE Labour Law: within 14 days of the last working day|قانون العمل الإماراتي: خلال 14 يومًا من آخر يوم عمل]]).{% endif %}</div>'),
}

LETTER_COMMON = {
    'refline': ('Reference and date', REFLINE),
    'addressee': ('Addressee', ADDRESSEE),
    'title': ('Letter title', LETTER_TITLE),
    'body': ('Letter text', BODY),
    'sign': ('Signatory', LETTER_SIGN),
    'verify': ('Verification code', VERIFY),
}
NOC_DETAILS = ('{% if travel.destination %}<div class="blk">' + kv([('Destination|الوجهة', 'travel.destination'), ('From|من', 'travel.start'),
                                                                     ('To|إلى', 'travel.end'), ('Passport no.|رقم الجواز', 'employee.passport_no')]) + '</div>{% endif %}')
BANK_DETAILS = sec('Salary account', 'حساب الراتب', kv([('Bank|البنك', 'bank.name'), ('IBAN|رقم الآيبان', 'bank.iban'),
                                                         ('Account no.|رقم الحساب', 'bank.account'), ('Employee|الموظف', 'employee.name')]))
EMP_LETTER = ('<div class="blk">' + kv([('Employee|الموظف', 'employee.name'), ('Employee code|رقم الموظف', 'employee.code'),
                                         ('Designation|المسمى الوظيفي', 'employee.designation'), ('Nationality|الجنسية', 'employee.nationality'),
                                         ('Passport no.|رقم الجواز', 'employee.passport_no'), ('Date of joining|تاريخ الالتحاق', 'employee.joined')]) + '</div>')

OFFER = {
    'refline': ('Reference and date', REFLINE),
    'addressee': ('Candidate', '<div class="blk addressee">[[Dear|عزيزي/عزيزتي]] {{ offer.candidate }},</div>'),
    'title': ('Letter title', LETTER_TITLE),
    'body': ('Introduction', BODY),
    'position': ('Position', sec('Position', 'الوظيفة', kv([('Position|الوظيفة', 'offer.position'), ('Department|القسم', 'offer.department'),
                                                          ('Contract|العقد', 'offer.contract'), ('Probation|فترة التجربة', 'offer.probation'),
                                                          ('Joining date|تاريخ الالتحاق', 'offer.joining_date'), ('Offer valid until|العرض ساري حتى', 'offer.valid_until')]))),
    'salary': ('Salary', sec('Monthly salary', 'الراتب الشهري',
                             '<table class="lines"><thead><tr><th>[[Component|البند]]</th><th class="num">[[Amount|المبلغ]] ({{ offer.currency }})</th></tr></thead><tbody>'
                             '<tr><td>[[Basic salary|الراتب الأساسي]]</td><td class="num">{{ offer.basic }}</td></tr>'
                             '<tr><td>[[Housing allowance|بدل السكن]]</td><td class="num">{{ offer.housing }}</td></tr>'
                             '<tr><td>[[Transport allowance|بدل المواصلات]]</td><td class="num">{{ offer.transport }}</td></tr>'
                             '<tr><td>[[Other allowances|بدلات أخرى]]</td><td class="num">{{ offer.other }}</td></tr></tbody>'
                             '<tfoot><tr><td>[[Total monthly package|إجمالي الحزمة الشهرية]]</td><td class="num">{{ offer.total }}</td></tr></tfoot></table>')),
    'benefits': ('Benefits', sec('Benefits', 'المزايا', kv([('Annual leave|الإجازة السنوية', 'offer.annual_leave'), ('Air ticket|تذكرة الطيران', 'offer.air_ticket'),
                                                          ('Medical insurance|التأمين الطبي', 'offer.medical')], 2))),
    'terms': ('Other terms', '{% if offer.terms %}' + sec('Other terms', 'شروط أخرى', '<div class="body">{% for p in offer.terms %}<p>{{ p }}</p>{% endfor %}</div>') + '{% endif %}'),
    'sign': ('Signatory', LETTER_SIGN.replace('class="blk letter-sign"', 'class="blk letter-sign half"')),
    'accept': ('Acceptance', sec('Acceptance', 'القبول', '<p class="small">[[I accept this offer on the terms above.|أوافق على هذا العرض وفق الشروط المذكورة أعلاه.]]</p>'
                                 '<table class="sig-row"><tr><td class="sig"><div class="sig-space"></div><div class="sig-line"></div><div class="sig-label">[[Candidate signature|توقيع المرشح]]</div>'
                                 '<div class="sig-name">{{ offer.candidate }}</div></td><td class="sig"><div class="sig-space"></div><div class="sig-line"></div>'
                                 '<div class="sig-label">[[Date|التاريخ]]</div></td></tr></table>', 'half accept')),
}

WARNING = {
    'refline': ('Reference and date', REFLINE),
    'addressee': ('Employee', '<div class="blk addressee">{{ employee.title }} {{ employee.name }}<br><span class="muted">{{ employee.designation }} · {{ employee.department }} · {{ employee.code }}</span></div>'),
    'title': ('Letter title', LETTER_TITLE),
    'details': ('Case details', '<div class="blk">' + kv([('Case no.|رقم القضية', 'case.number'), ('Subject|الموضوع', 'case.subject'),
                                                          ('Category|الفئة', 'case.category'), ('Incident date|تاريخ الواقعة', 'case.incident_date'),
                                                          ('Decision|القرار', 'case.decision'), ('Effective from|يسري من', 'case.effective_date')]) + '</div>'),
    'body': ('Letter text', BODY),
    'sign': ('Signatory', LETTER_SIGN),
    'ack': ('Employee acknowledgement', sec('Employee acknowledgement', 'إقرار الموظف', '<p class="small">[[I received this letter. My signature does not mean that I agree with its content.|استلمت هذه الرسالة، ولا يعني توقيعي موافقتي على مضمونها.]]</p>'
                                         '<table class="sig-row"><tr><td class="sig"><div class="sig-space"></div><div class="sig-line"></div><div class="sig-label">[[Employee signature|توقيع الموظف]]</div></td>'
                                         '<td class="sig"><div class="sig-space"></div><div class="sig-line"></div><div class="sig-label">[[Date|التاريخ]]</div></td></tr></table>')),
}

LEAVE = {
    'refline': ('Reference and date', REFLINE),
    'addressee': ('Employee', '<div class="blk addressee">{{ employee.title }} {{ employee.name }}<br><span class="muted">{{ employee.designation }} · {{ employee.code }}</span></div>'),
    'title': ('Letter title', LETTER_TITLE),
    'body': ('Letter text', BODY),
    'details': ('Leave details', sec('Leave details', 'تفاصيل الإجازة', kv([('Leave type|نوع الإجازة', 'leave.type'), ('Request no.|رقم الطلب', 'leave.number'),
                                                                        ('From|من', 'leave.start'), ('To|إلى', 'leave.end'),
                                                                        ('Days|عدد الأيام', 'leave.days'), ('Status|الحالة', 'leave.status'),
                                                                        ('Applied on|تاريخ الطلب', 'leave.applied_on'), ('Balance after this leave|الرصيد بعد الإجازة', 'leave.balance')]))),
    'sign': ('Signatory', LETTER_SIGN),
}

LOAN = {
    'employee': ('Employee details', sec('Employee', 'الموظف', kv(EMP_ROWS + [('Branch|الفرع', 'employee.branch'), ('Date of joining|تاريخ الالتحاق', 'employee.joined')]))),
    'loan': ('Loan details', sec('Loan details', 'تفاصيل القرض', kv([('Loan no.|رقم القرض', 'loan.number'), ('Loan type|نوع القرض', 'loan.type'),
                                                                   ('Amount|المبلغ', 'loan.amount'), ('Instalments|عدد الأقساط', 'loan.period'),
                                                                   ('Monthly instalment|القسط الشهري', 'loan.emi'), ('Disbursed on|تاريخ الصرف', 'loan.disbursement_date'),
                                                                   ('Applied on|تاريخ الطلب', 'loan.applied_on'), ('Approved on|تاريخ الموافقة', 'loan.approved_on'),
                                                                   ('Repaid so far|المسدد حتى الآن', 'loan.paid'), ('Balance|الرصيد المتبقي', 'loan.balance')]))),
    'schedule': ('Repayment schedule', sec('Repayment schedule', 'جدول السداد', lines_table('loan.schedule', [
        ('No.|رقم', 'no'), ('Month|الشهر', 'date'), ('Instalment|القسط', 'amount', 'num'), ('Balance after|الرصيد بعد', 'balance', 'num'), ('Status|الحالة', 'status')]))),
    'terms': ('Terms', sec('Terms of the loan', 'شروط القرض', '<ol class="terms">{% for p in loan.terms %}<li>{{ p }}</li>{% endfor %}</ol>')),
}

EXPENSE = {
    'employee': ('Employee details', sec('Employee', 'الموظف', kv(EMP_ROWS + [('Branch|الفرع', 'employee.branch'), ('Claim no.|رقم المطالبة', 'claim.number')]))),
    'claim': ('Claim details', sec('Claim', 'المطالبة', kv([('Title|العنوان', 'claim.title'), ('Period|الفترة', 'claim.period'),
                                                         ('Submitted on|تاريخ التقديم', 'claim.submitted_at'), ('Approved on|تاريخ الموافقة', 'claim.approved_at'),
                                                         ('Status|الحالة', 'claim.status'), ('Reimbursed via|طريقة السداد', 'claim.via')]))),
    'lines': ('Expense lines', sec('Expenses', 'المصروفات', lines_table('claim.lines', [
        ('Date|التاريخ', 'date'), ('Category|الفئة', 'category'), ('Merchant / description|المورد / الوصف', 'description'),
        ('Amount|المبلغ', 'amount_display', 'num'), ('AED|درهم', 'amount_aed', 'num')], ('Total (AED)|الإجمالي (درهم)', 'claim.total')))),
    'totals': ('Totals', '<section class="blk">' + tiles([('Total claimed|إجمالي المطالبة', 'claim.total'), ('Advance applied|السلفة المخصومة', 'claim.advance'),
                                                         ('To reimburse|المبلغ المستحق', 'claim.reimburse', 'net')]) + '</section>'),
    'notes': ('Notes', '{% if claim.notes %}<div class="blk notes"><b>[[Notes|ملاحظات]]</b><p>{{ claim.notes|linebreaksbr }}</p></div>{% endif %}'),
}

ASSET = {
    'employee': ('Employee details', sec('Employee', 'الموظف', kv(EMP_ROWS + [('Handover no.|رقم التسليم', 'handover.number'), ('Date|التاريخ', 'handover.date')]))),
    'items': ('Assets', sec('Assets', 'الأصول', lines_table('handover.items', [
        ('Asset|الأصل', 'asset'), ('Serial / tag|الرقم التسلسلي', 'serial'), ('Condition|الحالة', 'condition'), ('Accessories|الملحقات', 'accessories'), ('Note|ملاحظة', 'note')]))),
    'charge': ('Damage charge', '{% if handover.damage_charge %}<div class="blk words"><b>[[Charge for damage / loss|رسوم التلف / الفقد]]:</b> {{ handover.damage_charge }}</div>{% endif %}'),
    'terms': ('Terms', sec('Terms', 'الشروط', '<div class="body">{% for p in handover.terms %}<p>{{ p }}</p>{% endfor %}</div>')),
}

# document family → (blocks, default order per look); header/title/signatures/footer are added to every document
FORM_BASE = ['header', 'title']
DOCS = {
    'payslip': (PAYSLIP, {
        'classic': ['header', 'title', 'employee', 'attendance', 'earnings', 'totals', 'net_words', 'ytd', 'leave', 'bank', 'signatures', 'footer'],
        'modern': ['header', 'employee', 'totals', 'earnings', 'net_words', 'attendance', 'ytd', 'leave', 'bank', 'footer'],
        'compact': ['header', 'employee', 'earnings', 'totals', 'net_words', 'bank', 'ytd', 'footer'],
        'bilingual': ['header', 'title', 'employee', 'attendance', 'earnings', 'totals', 'net_words', 'ytd', 'leave', 'bank', 'signatures', 'footer'],
    }, 'form'),
    'final_settlement': (SETTLEMENT, {
        'classic': ['header', 'title', 'employee', 'earnings', 'deductions', 'notes', 'totals', 'net_words', 'payment', 'signatures', 'footer'],
        'modern': ['header', 'employee', 'totals', 'earnings', 'deductions', 'notes', 'net_words', 'payment', 'signatures', 'footer'],
        'compact': ['header', 'employee', 'earnings', 'deductions', 'totals', 'net_words', 'payment', 'signatures', 'footer'],
        'bilingual': ['header', 'title', 'employee', 'earnings', 'deductions', 'totals', 'net_words', 'payment', 'signatures', 'footer'],
    }, 'form'),
    'loan_agreement': (LOAN, {
        'classic': ['header', 'title', 'employee', 'loan', 'schedule', 'terms', 'signatures', 'footer'],
        'modern': ['header', 'employee', 'loan', 'schedule', 'terms', 'signatures', 'footer'],
        'compact': ['header', 'employee', 'loan', 'schedule', 'signatures', 'footer'],
        'bilingual': ['header', 'title', 'employee', 'loan', 'schedule', 'terms', 'signatures', 'footer'],
    }, 'form'),
    'expense_claim': (EXPENSE, {
        'classic': ['header', 'title', 'employee', 'claim', 'lines', 'totals', 'notes', 'signatures', 'footer'],
        'modern': ['header', 'employee', 'totals', 'claim', 'lines', 'notes', 'signatures', 'footer'],
        'compact': ['header', 'employee', 'lines', 'totals', 'signatures', 'footer'],
        'bilingual': ['header', 'title', 'employee', 'claim', 'lines', 'totals', 'signatures', 'footer'],
    }, 'form'),
    'asset_handover': (ASSET, {
        'classic': ['header', 'title', 'employee', 'items', 'charge', 'terms', 'signatures', 'footer'],
        'modern': ['header', 'employee', 'items', 'charge', 'terms', 'signatures', 'footer'],
        'compact': ['header', 'employee', 'items', 'terms', 'signatures', 'footer'],
        'bilingual': ['header', 'title', 'employee', 'items', 'charge', 'terms', 'signatures', 'footer'],
    }, 'form'),
    'offer_letter': (OFFER, {
        'classic': ['header', 'refline', 'addressee', 'title', 'body', 'position', 'salary', 'benefits', 'terms', 'sign', 'accept', 'footer'],
        'modern': ['header', 'addressee', 'title', 'body', 'position', 'salary', 'benefits', 'terms', 'sign', 'accept', 'footer'],
        'compact': ['header', 'addressee', 'title', 'body', 'position', 'salary', 'benefits', 'sign', 'accept', 'footer'],
        'bilingual': ['header', 'refline', 'addressee', 'title', 'body', 'position', 'salary', 'benefits', 'terms', 'sign', 'accept', 'footer'],
    }, 'letter'),
    'warning_letter': (WARNING, {
        'classic': ['header', 'refline', 'addressee', 'title', 'details', 'body', 'sign', 'ack', 'footer'],
        'modern': ['header', 'addressee', 'title', 'details', 'body', 'sign', 'ack', 'footer'],
        'compact': ['header', 'addressee', 'title', 'details', 'body', 'sign', 'ack', 'footer'],
        'bilingual': ['header', 'refline', 'addressee', 'title', 'details', 'body', 'sign', 'ack', 'footer'],
    }, 'letter'),
    'leave_approval': (LEAVE, {
        'classic': ['header', 'refline', 'addressee', 'title', 'body', 'details', 'sign', 'footer'],
        'modern': ['header', 'addressee', 'title', 'details', 'body', 'sign', 'footer'],
        'compact': ['header', 'addressee', 'title', 'details', 'body', 'sign', 'footer'],
        'bilingual': ['header', 'refline', 'addressee', 'title', 'body', 'details', 'sign', 'footer'],
    }, 'letter'),
}
for _k, _extra in (('salary_certificate', {'employee': ('Employee details', EMP_LETTER), 'salary': ('Salary details', SALARY_TABLE)}),
                   ('experience_letter', {'employee': ('Employee details', EMP_LETTER)}),
                   ('noc', {'travel': ('Travel details', NOC_DETAILS)}),
                   ('salary_transfer', {'salary': ('Salary details', SALARY_TABLE), 'bank': ('Salary account', BANK_DETAILS)})):
    _blocks = dict(LETTER_COMMON, **_extra)
    _mid = {'salary_certificate': ['employee', 'salary'], 'experience_letter': [], 'noc': ['travel'], 'salary_transfer': ['salary', 'bank']}[_k]
    DOCS[_k] = (_blocks, {
        'classic': ['header', 'refline', 'addressee', 'title', 'body'] + _mid + ['sign', 'verify', 'footer'],
        'modern': ['header', 'addressee', 'title', 'body'] + _mid + ['sign', 'verify', 'footer'],
        'compact': ['header', 'addressee', 'title', 'body'] + _mid + ['sign', 'verify', 'footer'],
        'bilingual': ['header', 'refline', 'addressee', 'title', 'body'] + _mid + ['sign', 'verify', 'footer'],
    }, 'letter')

COMMON_BLOCKS = {'header': 'Letterhead / header', 'title': 'Document title', 'signatures': 'Signatures', 'footer': 'Footer (every page)'}

# ------------------------------------------------------------------ the looks
LOOKS = {
    'classic': {'name': 'Classic', 'header': 'letterhead', 'font': 'Lato', 'heading_font': 'Source Serif 4', 'font_size': 9,
                'description': 'Letterhead with the logo, boxed tables and a formal title – the traditional printed document.'},
    'modern': {'name': 'Modern', 'header': 'band', 'font': 'Inter', 'heading_font': 'Inter', 'font_size': 9,
               'description': 'Colour band header, clean tables and summary tiles.'},
    'compact': {'name': 'Compact', 'header': 'line', 'font': 'Inter', 'heading_font': 'Inter', 'font_size': 8,
                'description': 'Dense one-page layout with small type – for printing many at once.'},
    'bilingual': {'name': 'Bilingual EN / AR', 'header': 'bilingual', 'font': 'Cairo', 'heading_font': 'Cairo', 'font_size': 8.4,
                  'description': 'English and Arabic labels side by side, Arabic letterhead on the right.'},
    'arabic': {'name': 'Arabic', 'header': 'letterhead', 'font': 'Cairo', 'heading_font': 'Cairo', 'font_size': 8.8,
               'description': 'The whole document in Arabic, right to left.'},
}
ARABIC_DOCS = ('payslip', 'final_settlement', 'salary_certificate', 'experience_letter', 'noc', 'salary_transfer')


def tint(hex_color, f):
    """Mix a colour with white (f = 0 … 1, 1 = white)."""
    try:
        h = hex_color.lstrip('#')
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return '#%02x%02x%02x' % tuple(int(c + (255 - c) * f) for c in (r, g, b))
    except Exception:   # noqa: BLE001
        return '#f3f4f6'


def shade(hex_color, f):
    try:
        h = hex_color.lstrip('#')
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return '#%02x%02x%02x' % tuple(int(c * (1 - f)) for c in (r, g, b))
    except Exception:   # noqa: BLE001
        return '#111111'


BASE_CSS = """
* { box-sizing: border-box; }
html { color: var(--t); font-size: var(--fs); line-height: 1.42; }
body { margin: 0; }
h1, h2, h3 { font-family: var(--hf); margin: 0; }
p { margin: 0 0 2.4mm; }
img.logo { max-height: 18mm; max-width: 46mm; }
.mono { width: 14mm; height: 14mm; border-radius: 3mm; background: var(--p); color: #fff; font-family: var(--hf); font-weight: 700;
        font-size: 14pt; text-align: center; line-height: 14mm; letter-spacing: .5pt; }
.co-name { font-family: var(--hf); font-weight: 700; font-size: 1.45em; color: var(--p); line-height: 1.2; }
.co-name-ar { font-family: var(--fa); font-weight: 700; font-size: 1.15em; color: var(--p); }
.co-meta { font-size: .8em; color: var(--muted); }
.blk { margin-bottom: 3.2mm; }
.sec-title { font-size: .78em; text-transform: uppercase; letter-spacing: .9pt; color: var(--p); margin-bottom: 1.6mm; font-weight: 700; }
html[dir=rtl] .sec-title { letter-spacing: 0; text-transform: none; font-size: .9em; }
.ar-l { display: block; font-family: var(--fa); direction: rtl; font-size: .86em; font-weight: 600; color: var(--muted);
        text-transform: none; letter-spacing: 0; line-height: 1.25; }
th .ar-l, .t-l .ar-l, .sec-title .ar-l { font-weight: 500; }
.muted { color: var(--muted); }
.small { font-size: .85em; }
.num { text-align: right; white-space: nowrap; }
html[dir=rtl] .num { text-align: left; }
table { border-collapse: collapse; width: 100%; }
th { text-align: start; }
table.kv th { font-weight: 600; color: var(--muted); font-size: .84em; width: 17%; padding: 1mm 2mm; vertical-align: top; }
table.kv td { font-weight: 600; padding: 1mm 2mm; vertical-align: top; width: 33%; }
table.kv.cols1 th { width: 30%; } table.kv.cols1 td { width: 70%; }
table.kv.cols3 th { width: 11%; } table.kv.cols3 td { width: 22%; }
table.lines th { font-size: .82em; padding: 1.3mm 2mm; font-weight: 700; }
table.lines td { padding: 1.05mm 2mm; vertical-align: top; }
table.lines tfoot .ar-l { font-size: .8em; font-weight: 600; }
table.lines tfoot td { font-weight: 700; }
td.basis, th.basis { font-size: .82em; color: var(--muted); }
table.ed { table-layout: fixed; } td.ed-col { vertical-align: top; width: 48.5%; padding: 0; } td.ed-gap { width: 3%; }
.blk.half { display: inline-block; width: 48.6%; vertical-align: top; }
.blk.half + .blk.half { margin-inline-start: 2.2%; }
.accept { border: .6pt solid var(--line); padding: 2mm 3mm; border-radius: 1.5mm; }
.accept table.sig-row { margin-top: 1mm; }
.blk.half table.lines th, .blk.half table.lines td { padding-left: 1.4mm; padding-right: 1.4mm; }
.cur-note { font-size: .75em; color: var(--muted); margin-top: 1mm; }
table.tiles { table-layout: fixed; }
.tile-c { padding: 0 1.2mm; vertical-align: top; } .tile-c:first-child { padding-inline-start: 0; } .tile-c:last-child { padding-inline-end: 0; }
.tile { padding: 2.4mm 3mm; height: 100%; }
.t-l { font-size: .76em; color: var(--muted); font-weight: 600; }
.t-v { font-size: 1.35em; font-weight: 700; font-family: var(--hf); margin-top: .6mm; }
.tiles.small .t-v { font-size: 1.1em; }
.tiles.small .tile { padding: 1.5mm 2.6mm; }
.tile.net .t-l, .tile.net .t-l .ar-l { color: rgba(255,255,255,.85); }
.tile.net { background: var(--p); color: #fff; }
.words { font-size: .9em; padding: 2mm 3mm; background: var(--soft); border-inline-start: 1mm solid var(--a); }
.words-ar { font-family: var(--fa); margin-top: .8mm; }
.bank, .payment, .verify { font-size: .86em; color: var(--muted); }
.bank b, .payment b, .verify b { color: var(--t); }
.notes { font-size: .85em; } .notes ul { margin: 1mm 0 0; padding-inline-start: 5mm; }
table.sig-row { table-layout: fixed; margin-top: 3mm; page-break-inside: avoid; }
td.sig { vertical-align: top; padding: 0 3mm; text-align: center; }
.sig-space { height: 13mm; position: relative; }
.sig-img { max-height: 15mm; max-width: 40mm; }
.stamp-img { max-height: 22mm; max-width: 26mm; opacity: .85; position: absolute; top: -4mm; inset-inline-end: 0; }
.sig-line { border-top: .6pt solid #8b93a5; margin: 0 4mm 1.2mm; }
.sig-label { font-weight: 700; font-size: .85em; }
.sig-name { font-size: .85em; } .sig-note { font-size: .74em; color: var(--muted); }
.refline { display: flex; justify-content: space-between; font-size: .92em; }
.addressee { font-weight: 700; margin-top: 3mm; }
.letter-title h1 { text-align: center; font-size: 1.35em; text-decoration: underline; text-underline-offset: 2pt; margin: 3mm 0 2mm; color: var(--t); }
.body p { text-align: justify; line-height: 1.6; }
html[dir=rtl] .body p { line-height: 1.8; }
.letter-sign { margin-top: 4mm; page-break-inside: avoid; }
.ls-space { height: 16mm; position: relative; width: 70mm; }
.ls-space .stamp-img { inset-inline-end: auto; inset-inline-start: 36mm; }
.ls-name { font-weight: 700; } .ls-title, .ls-co { font-size: .9em; color: var(--muted); }
.verify { margin-top: 4mm; border-top: .5pt dashed var(--line); padding-top: 2mm; }
ol.terms { margin: 0; padding-inline-start: 5mm; font-size: .9em; } ol.terms li { margin-bottom: 1mm; }
.run-foot { position: running(foot); font-size: 7.2pt; color: var(--muted); text-align: center; border-top: .5pt solid var(--line); padding-top: 1.6mm; }
.rf-sub { font-size: 6.6pt; margin-top: .6mm; }
.pg::after { content: counter(page) " / " counter(pages); }
.watermark { position: fixed; top: 105mm; left: 0; right: 0; text-align: center; font-size: 62pt; font-weight: 700; color: rgba(31, 41, 55, .06);
             transform: rotate(-32deg); font-family: var(--hf); letter-spacing: 4pt; }
/* bilingual labels inside a line of text */
.ar-sep { display: none; }
.doc-meta .ar-l, .words .ar-l, .bank .ar-l, .run-foot .ar-l, .payment .ar-l, .verify .ar-l, .cur-note .ar-l, .refline .ar-l, .co-meta .ar-l,
.bd-sub .ar-l, .ls-closing .ar-l, .addressee .ar-l, p .ar-l { display: inline; font-size: 1em; color: inherit; font-weight: inherit; unicode-bidi: isolate; }
.doc-meta .ar-sep, .words .ar-sep, .bank .ar-sep, .run-foot .ar-sep, .payment .ar-sep, .verify .ar-sep, .cur-note .ar-sep, .refline .ar-sep,
.co-meta .ar-sep, .bd-sub .ar-sep, .ls-closing .ar-sep, .addressee .ar-sep, p .ar-sep { display: inline; }
.sec-title .ar-l, .t-l .ar-l { display: inline; float: right; font-size: 1.05em; }
/* letterhead */
.lh { display: flex; align-items: center; gap: 6mm; padding-bottom: 3.5mm; }
.lh-left .lh-co { flex: 1; text-align: end; }
.lh-right { flex-direction: row-reverse; } .lh-right .lh-co { flex: 1; text-align: start; }
.lh-center { flex-direction: column; text-align: center; gap: 2mm; } .lh-center .lh-co { text-align: center; }
/* colour band */
.band { display: flex; align-items: center; gap: 5mm; background: var(--p); color: #fff; padding: 5mm 6mm; border-radius: 2.5mm; }
.band .co-name, .band .co-name-ar { color: #fff; } .band .co-meta { color: rgba(255,255,255,.78); }
.band-logo { background: #fff; border-radius: 2mm; padding: 2mm; } .band-logo .mono { width: 12mm; height: 12mm; line-height: 12mm; font-size: 12pt; }
.band-logo img.logo { max-height: 13mm; max-width: 36mm; }
.band-co { flex: 1; }
.band-doc { text-align: end; }
.bd-title { font-family: var(--hf); font-size: 1.5em; font-weight: 700; letter-spacing: .3pt; }
.bd-title-ar { font-family: var(--fa); font-size: 1.1em; font-weight: 700; }
.bd-sub { font-size: .82em; color: rgba(255,255,255,.85); }
/* compact line */
.hline { display: flex; align-items: center; gap: 4mm; border-bottom: 1.2pt solid var(--p); padding-bottom: 2mm; }
.hline img.logo { max-height: 11mm; } .hline .mono { width: 10mm; height: 10mm; line-height: 10mm; font-size: 10pt; border-radius: 2mm; }
.hl-co { flex: 1; } .hline .co-name { font-size: 1.25em; }
.hl-doc { text-align: end; } .hl-doc .bd-title { font-size: 1.3em; color: var(--p); } .hl-doc .bd-sub { color: var(--muted); }
/* bilingual header */
.hbi { display: flex; align-items: center; gap: 4mm; border-bottom: 1.4pt solid var(--p); padding-bottom: 3mm; }
.hbi-en { flex: 1; text-align: left; } .hbi-ar { flex: 1; text-align: right; font-family: var(--fa); }
.hbi-ar .co-name { font-family: var(--fa); }
.hbi-logo { text-align: center; }
/* title bar */
.doc-head { text-align: center; }
.doc-title { font-size: 1.5em; color: var(--p); letter-spacing: .4pt; }
.doc-title .ar-l { color: var(--p); font-size: .75em; }
.doc-sub { font-size: .95em; color: var(--muted); margin-top: .8mm; }
.doc-meta { font-size: .82em; color: var(--muted); margin-top: 1mm; }
"""

LOOK_CSS = {
    'classic': """
.lh { border-bottom: 2.2pt double var(--p); }
.doc-head { margin: 0 0 4mm; padding: 1.8mm 0; border-top: .5pt solid var(--line); border-bottom: .5pt solid var(--line); }
.doc-title { text-transform: uppercase; letter-spacing: 2pt; font-size: 1.35em; }
html[dir=rtl] .doc-title { letter-spacing: 0; }
.sec-title { border-bottom: .8pt solid var(--a); padding-bottom: 1mm; }
table.kv { border: .6pt solid var(--line); }
table.kv th { background: var(--soft); border: .6pt solid var(--line); }
table.kv td { border: .6pt solid var(--line); }
table.lines { border: .6pt solid #b9c0cc; }
table.lines th { background: var(--p-soft); color: var(--p-dark); border: .6pt solid #b9c0cc; }
table.lines td { border-inline: .6pt solid #d4d8e0; border-bottom: .4pt solid #e3e6ec; }
table.lines tfoot td { background: var(--soft); border-top: 1pt solid var(--p); }
.tile { border: .7pt solid #b9c0cc; background: #fff; }
.tile.net { border-color: var(--p); }
.words { border: .6pt solid var(--line); border-inline-start: 1.2mm solid var(--a); }
""",
    'modern': """
.blk { margin-bottom: 4.6mm; }
.band { margin-bottom: 5.5mm; }
.doc-head { text-align: start; margin-bottom: 4mm; }
table.kv { background: var(--soft); border-radius: 2mm; }
table.kv th { padding: 1.5mm 3mm; } table.kv td { padding: 1.5mm 3mm; }
table.lines thead th { background: var(--p-soft); color: var(--p-dark); border: none; }
table.lines thead th:first-child { border-radius: 1.5mm 0 0 1.5mm; } table.lines thead th:last-child { border-radius: 0 1.5mm 1.5mm 0; }
table.lines td { border-bottom: .4pt solid #e6e8ee; }
table.lines tbody tr:nth-child(even) td { background: #fafbfd; }
table.lines tfoot td { border-top: 1.1pt solid var(--p); color: var(--p-dark); }
.tile { background: var(--soft); border-radius: 2.2mm; padding: 3mm 3.5mm; }
.tile.net { background: var(--p); }
.tile.net .t-v { font-size: 1.6em; }
.words { border-radius: 0 2mm 2mm 0; }
.sec-title { font-size: .74em; }
""",
    'compact': """
.blk { margin-bottom: 2.4mm; }
.sec-title { font-size: .72em; margin-bottom: .8mm; }
table.kv th, table.kv td { padding: .6mm 1.2mm; }
table.kv { border-top: .5pt solid var(--line); border-bottom: .5pt solid var(--line); }
table.lines th { padding: .9mm 1.2mm; border-bottom: .8pt solid var(--p); color: var(--p); }
table.lines td { padding: .7mm 1.2mm; border-bottom: .3pt solid #e5e7eb; }
table.lines tfoot td { border-top: .8pt solid var(--p); }
.tile { padding: 1.4mm 2mm; border: .5pt solid var(--line); }
.t-v { font-size: 1.15em; }
.words { padding: 1.2mm 2mm; }
.sig-space { height: 11mm; } table.sig-row { margin-top: 3mm; }
.letter-title h1 { font-size: 1.2em; }
.body p { line-height: 1.45; }
""",
    'bilingual': """
.hbi { margin-bottom: 4mm; }
.doc-head { margin-bottom: 4mm; background: var(--p-soft); padding: 2.4mm; border-radius: 1.5mm; }
.doc-title { font-size: 1.3em; }
table.kv { border: .6pt solid var(--line); }
table.kv th { background: var(--soft); border: .6pt solid var(--line); line-height: 1.2; }
table.kv td { border: .6pt solid var(--line); }
table.lines { border: .6pt solid var(--line); }
table.lines th { background: var(--p); color: #fff; line-height: 1.2; }
table.lines th .ar-l { color: rgba(255,255,255,.85); }
table.lines td { border-bottom: .4pt solid var(--line); }
table.lines tfoot td { background: var(--p-soft); line-height: 1.25; }
.tile { border: .6pt solid var(--line); background: var(--soft); }
""",
}
LOOK_CSS['arabic'] = LOOK_CSS['classic'] + """
html { line-height: 1.45; }
.sec-title { letter-spacing: 0; text-transform: none; }
"""


def defaults(doc_type, look):
    """Block-editor settings of a sample design."""
    blocks_def, orders, family = DOCS[doc_type]
    lk = LOOKS[look]
    order = orders.get('classic' if look == 'arabic' else look)
    allkeys = list(dict.fromkeys(order + ['header', 'title', 'signatures', 'footer'] + list(blocks_def)))
    if family == 'letter':
        allkeys = [k for k in allkeys if k not in ('signatures',)] if 'sign' in blocks_def else allkeys
    return {
        'look': look, 'header': lk['header'], 'font': lk['font'], 'heading_font': lk['heading_font'], 'font_size': lk['font_size'],
        'primary': '', 'accent': '', 'logo_position': '', 'paper': 'A4', 'orientation': 'portrait',
        'margins': {'top': 12, 'right': 14, 'bottom': 20, 'left': 14} if look != 'compact' else {'top': 9, 'right': 10, 'bottom': 16, 'left': 10},
        'blocks': [{'key': k, 'on': k in order} for k in allkeys],
        'watermark': None,
    }


def block_catalogue(doc_type):
    blocks_def, _, family = DOCS[doc_type]
    out = [{'key': 'header', 'label': COMMON_BLOCKS['header']}]
    if family == 'form':
        out += [{'key': 'title', 'label': COMMON_BLOCKS['title']}]
    out += [{'key': k, 'label': v[0]} for k, v in blocks_def.items()]
    if family == 'form':
        out += [{'key': 'signatures', 'label': COMMON_BLOCKS['signatures']}]
    out += [{'key': 'footer', 'label': COMMON_BLOCKS['footer']}]
    return out


def compose(doc_type, settings, lang):
    """Block-editor settings → (html, css) of the template (Django-template HTML with the labels of the language)."""
    blocks_def, orders, family = DOCS[doc_type]
    look = settings.get('look') or 'classic'
    hdr = settings.get('header') or LOOKS.get(look, LOOKS['classic'])['header']
    parts = [WATERMARK]
    for b in settings.get('blocks') or []:
        if not b.get('on'):
            continue
        k = b.get('key')
        if k == 'header':
            parts.append(HEADERS.get(hdr, HEADERS['letterhead']).replace('@@LETTERHEAD@@', HEADERS['letterhead'])
                         .replace('@@BANDDOC@@', BANDDOC_LETTER if family == 'letter' else BANDDOC_FORM))
        elif k == 'title' and 'title' not in blocks_def:
            parts.append(TITLE)
        elif k == 'signatures':
            parts.append(SIGNATURES)
        elif k == 'footer':
            parts.insert(1, FOOTER)
        elif k in blocks_def:
            parts.append(blocks_def[k][1])
    html = f'<div class="doc look-{look}">\n' + '\n'.join(parts) + '\n</div>'
    return labels(html, lang), LOOK_CSS.get(look, '')


def page_css(settings, brand, lang):
    """The part of the CSS that comes from the settings and the branding (page, fonts, colours) – used in both modes."""
    from .engine import font_faces, family_stack, FONT_KEYS
    primary = settings.get('primary') or brand.get('primary') or '#1f3a5f'
    accent = settings.get('accent') or brand.get('accent') or '#c8a24a'
    text = brand.get('text') or '#1f2430'
    font = settings.get('font') if settings.get('font') in FONT_KEYS else (brand.get('font') or 'Inter')
    hfont = settings.get('heading_font') if settings.get('heading_font') in FONT_KEYS else font
    fa = brand.get('font_arabic') if brand.get('font_arabic') in FONT_KEYS else 'Cairo'
    if lang == 'ar':
        font = hfont = font if font in ('Cairo', 'Tajawal', 'Amiri', 'Noto Sans Arabic', 'Noto Naskh Arabic') else fa
    m = settings.get('margins') or {}

    def mm(k, d):
        try:
            return max(0, min(60, float(m.get(k, d))))
        except (TypeError, ValueError):
            return d
    paper = 'letter' if str(settings.get('paper', 'A4')).lower() == 'letter' else 'A4'
    orient = 'landscape' if settings.get('orientation') == 'landscape' else 'portrait'
    try:
        fs = max(6.5, min(13, float(settings.get('font_size') or 9.5)))
    except (TypeError, ValueError):
        fs = 9.5
    faces = font_faces(sorted({font, hfont, fa, 'Noto Naskh Arabic'}))
    F, H, A = family_stack(font), family_stack(hfont), family_stack(fa)
    return f"""{faces}
@page {{ size: {paper} {orient}; margin: {mm('top', 12)}mm {mm('right', 14)}mm {mm('bottom', 20)}mm {mm('left', 14)}mm;
  @bottom-center {{ content: element(foot); vertical-align: top; }} }}
:root {{ --p: {primary}; --a: {accent}; --t: {text}; --muted: #5f6675; --line: #d9dde5; --soft: #f4f6f9;
  --p-soft: {tint(primary, .88)}; --p-dark: {shade(primary, .25)}; --fs: {fs}pt;
  --f: {F}; --hf: {H}, {A}, sans-serif; --fa: {A}, 'Noto Naskh Arabic', sans-serif; }}
html {{ font-family: {F}, {A}, 'Noto Naskh Arabic', sans-serif; }}
"""
