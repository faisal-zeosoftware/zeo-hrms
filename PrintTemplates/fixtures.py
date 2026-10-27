"""v1.62.0 print – sample data for the gallery thumbnails and for previews without a record (the company's own letterhead and
branding with a made-up employee)."""
from datetime import date

from .contexts import BY_KEY, T, base, d, money, period, signers, words, offer_text, loan_terms


def _emp(lang):
    return {'id': 0, 'name': 'أحمد خالد المنصوري' if lang == 'ar' else 'Ahmed Khalid Al Mansoori', 'name_ar': 'أحمد خالد المنصوري',
            'name_en': 'Ahmed Khalid Al Mansoori', 'code': 'EMP-1042', 'title': T(lang, 'Mr.', 'السيد'),
            'designation': T(lang, 'Senior Accountant', 'محاسب أول'), 'department': T(lang, 'Finance', 'المالية'),
            'branch': T(lang, 'Head office – Sharjah', 'المكتب الرئيسي – الشارقة'), 'nationality': T(lang, 'Jordanian', 'أردني'),
            'joined': d(date(2019, 3, 17), lang), 'email': 'ahmed.mansoori@example.ae', 'passport_no': 'N1234567',
            'emirates_id_masked': '784-•••• •••• 1234', 'labour_card_no': '98765432'}


def sample(doc_type, lang='en'):
    c = base(doc_type, lang)
    if not c['company'].get('name'):
        c['company'].update(name='Golden Dune Integrated Services LLC', initials='GD')
    c['employee'] = _emp(lang)
    name = c['employee']['name']
    today = date.today()
    sign = (c['company'].get('signatory_name') or 'Sara Al Hashimi')
    stitle = c['company'].get('signatory_title') or T(lang, 'HR Manager', 'مديرة الموارد البشرية')
    c['company']['signatory_name'], c['company']['signatory_title'] = sign, stitle
    if doc_type == 'payslip':
        y, m = 2026, 10
        earn = [('Basic salary', 'الراتب الأساسي', 9500), ('Housing allowance', 'بدل السكن', 3800), ('Transport allowance', 'بدل المواصلات', 950),
                ('Overtime', 'العمل الإضافي', 612.5)]
        ded = [('Loan instalment', 'قسط القرض', 750), ('Unpaid leave (1 day)', 'إجازة بدون أجر (يوم)', 316.67)]
        ge, gd = sum(x[2] for x in earn), sum(x[2] for x in ded)
        net = ge - gd
        c['payslip'] = {'period': period(y, m, lang), 'period_en': period(y, m, 'en'), 'month': m, 'year': y, 'run_name': 'October 2026',
                        'pay_date': d(date(2026, 10, 28), lang), 'currency': 'AED', 'working_days': '26', 'days_worked': '25', 'unpaid_days': '1',
                        'overtime_hours': '14', 'earnings': [{'name': T(lang, a, b), 'amount': money(v)} for a, b, v in earn],
                        'deductions': [{'name': T(lang, a, b), 'amount': money(v)} for a, b, v in ded], 'total_earnings': money(ge),
                        'total_deductions': money(gd), 'gross': money(ge), 'net': money(net), 'net_display': 'AED ' + money(net), 'status': 'Approved',
                        'net_words': words(net, 'ar' if lang == 'ar' else 'en'), 'net_words_ar': words(net, 'ar') if lang == 'bi' else ''}
        c['ytd'] = {'year': 2026, 'count': 10, 'gross': money(ge * 10 - 2100), 'deductions': money(gd * 10), 'net': money((ge - gd) * 10 - 2100)}
        c['leave'] = [{'type': T(lang, 'Annual leave', 'إجازة سنوية'), 'balance': '18.5'}, {'type': T(lang, 'Sick leave', 'إجازة مرضية'), 'balance': '12'}]
        c['bank'] = {'name': 'Emirates NBD', 'iban': 'AE070331234567890123456', 'iban_masked': 'AE07 •••• •••• 3456', 'account': '1234567890123',
                     'account_masked': '•••• •••• 0123', 'mode': T(lang, 'WPS bank transfer', 'تحويل بنكي عبر نظام حماية الأجور')}
        c['doc'].update(number='PS-202610-EMP-1042', subtitle=T(lang, 'For the month of ', 'عن شهر ') + period(y, m, lang), date=d(date(2026, 10, 28), 'ar' if lang == 'ar' else 'en'))
        c['signers'] = signers(c, lang, ('Prepared by', 'أعدّه', '', T(lang, 'Payroll', 'الرواتب'), False), ('Approved by', 'اعتمده', sign, '', True),
                               ('Employee signature', 'توقيع الموظف', name, '', False))
    elif doc_type == 'final_settlement':
        earn = [(T(lang, 'Salary 1 – 15 Oct 2026', 'راتب 1 – 15 أكتوبر 2026'), T(lang, 'Wage 14,250.00 ÷ 31 × 15 days', 'الأجر 14,250.00 ÷ 31 × 15 يومًا'), 6895.16),
                (T(lang, 'End-of-service gratuity', 'مكافأة نهاية الخدمة'), T(lang, '21 days × 5 years + 30 days × 2.6 years (basic)', '21 يومًا × 5 سنوات + 30 يومًا × 2.6 سنة (الأساسي)'), 51346.15),
                (T(lang, 'Leave salary – 18.5 days', 'بدل الإجازة – 18.5 يوم'), T(lang, 'Basic 9,500.00 ÷ 30 × 18.5', 'الأساسي 9,500.00 ÷ 30 × 18.5'), 5858.33),
                (T(lang, 'Air ticket entitlement', 'استحقاق تذكرة الطيران'), T(lang, 'Policy: 1 economy ticket per year', 'السياسة: تذكرة سياحية واحدة سنويًا'), 1850.00)]
        ded = [(T(lang, 'Loan balance', 'رصيد القرض'), 'LN-2025-0031', 3000.00),
               (T(lang, 'Laptop not returned', 'حاسوب محمول لم يُسلَّم'), T(lang, 'Asset AST-0451 (written down value)', 'الأصل AST-0451 (القيمة الدفترية)'), 1200.00)]
        ge, gd = sum(x[2] for x in earn), sum(x[2] for x in ded)
        c['settlement'] = {'number': 'FS-2026-0007', 'status': T(lang, 'Approved', 'معتمدة'), 'exit_type': T(lang, 'Resignation', 'استقالة'),
                           'joined': d(date(2019, 3, 17), lang), 'last_working_date': d(date(2026, 10, 15), lang),
                           'service': T(lang, '7 years, 6 months, 29 days', '7 سنة و6 شهر و29 يوم'), 'unpaid_days': '0', 'basic': 'AED 9,500.00',
                           'wage': 'AED 14,250.00', 'due_date': d(date(2026, 10, 29), lang),
                           'earnings': [{'label': a, 'basis': b, 'amount': money(v)} for a, b, v in earn],
                           'deductions': [{'label': a, 'basis': b, 'amount': money(v)} for a, b, v in ded],
                           'notes': [T(lang, 'Medical insurance card returned to HR.', 'تمت إعادة بطاقة التأمين الطبي.')],
                           'total_earnings': money(ge), 'total_deductions': money(gd), 'net': money(ge - gd), 'net_display': 'AED ' + money(ge - gd),
                           'net_words': words(ge - gd, 'ar' if lang == 'ar' else 'en'), 'net_words_ar': words(ge - gd, 'ar') if lang == 'bi' else '',
                           'paid_on': '', 'payment_ref': '', 'currency': 'AED'}
        c['doc'].update(number='FS-2026-0007', status=T(lang, 'Approved', 'معتمدة'), subtitle=T(lang, 'End of service – Resignation', 'نهاية الخدمة – استقالة'))
        c['signers'] = signers(c, lang, ('Prepared by HR', 'أعدّته الموارد البشرية', 'Mariam Yousef', d(date(2026, 10, 16), lang), False),
                               ('Checked by Finance', 'دققته المالية', 'Rahul Menon', d(date(2026, 10, 17), lang), False),
                               ('Approved by', 'اعتمده', sign, d(date(2026, 10, 18), lang), True),
                               ('Employee acknowledgement', 'إقرار الموظف', name, T(lang, 'Received in full and final settlement.', 'استلمت المبلغ تسوية نهائية وكاملة.'), False))
    elif doc_type in ('salary_certificate', 'experience_letter', 'noc', 'salary_transfer'):
        co = c['company']['name']
        txt = {
            'salary_certificate': (T(lang, 'Salary Certificate', 'شهادة راتب'), [
                T(lang, f'This is to certify that {name}, holder of passport no. N1234567, is employed with {co} as Senior Accountant since 17 March 2019.',
                  f'نشهد بأن {name}، حامل جواز السفر رقم N1234567، يعمل لدى {co} بوظيفة محاسب أول منذ 17 مارس 2019.'),
                T(lang, 'His monthly salary is AED 14,250.00 as detailed below. This certificate is issued at his request for bank purposes, without any liability on the company.',
                  'ويبلغ راتبه الشهري 14,250.00 درهم كما هو مفصل أدناه. وقد أُعطيت له هذه الشهادة بناءً على طلبه لتقديمها إلى البنك دون أدنى مسؤولية على الشركة.')]),
            'experience_letter': (T(lang, 'Experience Certificate', 'شهادة خبرة'), [
                T(lang, f'This is to certify that {name} has been working with {co} as Senior Accountant in the Finance department from 17 March 2019 to date.',
                  f'نشهد بأن {name} يعمل لدى {co} بوظيفة محاسب أول في قسم المالية منذ 17 مارس 2019 وحتى تاريخه.'),
                T(lang, 'During this period he has shown professionalism, integrity and dedication to his work. We wish him every success.',
                  'وقد أظهر خلال هذه الفترة احترافية ونزاهة وتفانيًا في عمله. ونتمنى له دوام التوفيق.')]),
            'noc': (T(lang, 'No Objection Certificate', 'شهادة عدم ممانعة'), [
                T(lang, f'This is to certify that {name}, Senior Accountant, is employed with {co} since 17 March 2019.',
                  f'نشهد بأن {name}، محاسب أول، يعمل لدى {co} منذ 17 مارس 2019.'),
                T(lang, 'The company has no objection to him travelling to the United Kingdom for tourism from 20 December 2026 to 3 January 2027. He will resume his duties on his return.',
                  'ولا تمانع الشركة من سفره إلى المملكة المتحدة لغرض السياحة من 20 ديسمبر 2026 إلى 3 يناير 2027، على أن يعود لمباشرة عمله بعد عودته.')]),
            'salary_transfer': (T(lang, 'Salary Transfer Letter', 'خطاب تحويل الراتب'), [
                T(lang, f'This is to certify that {name} is employed with {co} as Senior Accountant since 17 March 2019, drawing a monthly salary of AED 14,250.00.',
                  f'نشهد بأن {name} يعمل لدى {co} بوظيفة محاسب أول منذ 17 مارس 2019 براتب شهري قدره 14,250.00 درهم.'),
                T(lang, 'We undertake to transfer his monthly salary and end-of-service benefits to the account below with Emirates NBD until we receive a clearance letter from the bank.',
                  'ونتعهد بتحويل راتبه الشهري ومستحقات نهاية الخدمة إلى حسابه المذكور أدناه لدى بنك الإمارات دبي الوطني إلى حين استلام خطاب براءة ذمة من البنك.')]),
        }[doc_type]
        c['letter'] = {'title': txt[0], 'addressee': T(lang, 'To whom it may concern' if doc_type != 'salary_transfer' else 'The Manager, Emirates NBD, Dubai',
                                                         'إلى من يهمه الأمر' if doc_type != 'salary_transfer' else 'السيد المدير، بنك الإمارات دبي الوطني، دبي'),
                       'paragraphs': txt[1], 'purpose': '', 'type': doc_type, 'signatory_name': sign, 'signatory_title': stitle, 'footer': '', 'show_salary': True}
        c['salary'] = {'basic': '9,500.00', 'housing': '3,800.00', 'transport': '950.00', 'other': '0.00', 'gross': '14,250.00', 'net': '14,250.00', 'currency': 'AED'}
        c['bank'] = {'name': 'Emirates NBD', 'iban': 'AE07 0331 2345 6789 0123 456', 'account': '1234567890123', 'iban_masked': 'AE07 •••• •••• 3456', 'mode': ''}
        c['travel'] = {'destination': T(lang, 'United Kingdom', 'المملكة المتحدة'), 'start': d(date(2026, 12, 20), lang), 'end': d(date(2027, 1, 3), lang)}
        c['doc'].update(number='HRL-2026-00042', verification_code='K7QM2XPA9D')
        if lang != 'bi':
            c['doc']['title'] = txt[0]
    elif doc_type == 'offer_letter':
        c['offer'] = {'number': 'OFR-2026-0012', 'candidate': 'Priya Nair', 'position': 'HR Executive', 'department': 'Human Resources', 'branch': 'Dubai branch',
                      'contract': T(lang, 'Limited – 2 year(s)', 'محدد المدة – سنتان'), 'probation': T(lang, '6 months', '6 أشهر'),
                      'joining_date': d(date(2026, 12, 1), lang), 'valid_until': d(date(2026, 11, 15), lang),
                      'annual_leave': T(lang, '30 days per year', '30 يومًا في السنة'), 'air_ticket': 'Annual economy ticket to home country',
                      'medical': 'Comprehensive (Daman Enhanced)', 'currency': 'AED', 'basic': '7,000.00', 'housing': '2,800.00', 'transport': '700.00',
                      'other': '500.00', 'total': '11,000.00', 'terms': ['Working hours are 8 hours a day, 5 days a week, as per the company policy.'], 'status': 'Approved'}
        c['letter'] = {'title': T(lang, 'Offer of Employment', 'عرض عمل'), 'addressee': 'Priya Nair',
                       'paragraphs': offer_text(lang, 'Priya Nair', 'HR Executive', c['company']['name'], c['offer']['joining_date'], c['offer']['valid_until']),
                       'signatory_name': sign, 'signatory_title': stitle}
        c['employee'] = {'name': 'Priya Nair'}
        c['doc'].update(number='OFR-2026-0012')
    elif doc_type == 'warning_letter':
        c['case'] = {'number': 'DC-2026-0004', 'subject': T(lang, 'Repeated late arrival', 'التأخر المتكرر'), 'category': T(lang, 'Attendance', 'الحضور'),
                     'incident_date': d(date(2026, 10, 6), lang), 'decision': T(lang, 'Written warning', 'إنذار كتابي'), 'decision_date': d(date(2026, 10, 20), lang),
                     'effective_date': d(date(2026, 10, 20), lang), 'status': 'Decided'}
        c['letter'] = {'title': T(lang, 'Written warning', 'إنذار كتابي'), 'addressee': name, 'signatory_name': sign, 'signatory_title': stitle, 'paragraphs': [
            T(lang, 'This letter records the decision taken in disciplinary case DC-2026-0004 concerning repeated late arrival (six times in September and October 2026).',
              'تسجل هذه الرسالة القرار المتخذ في القضية التأديبية رقم DC-2026-0004 بشأن التأخر المتكرر (ست مرات خلال سبتمبر وأكتوبر 2026).'),
            T(lang, 'Decision: written warning, effective 20 Oct 2026. Any repetition may lead to further disciplinary action in line with the company policy and the UAE Labour Law.',
              'القرار: إنذار كتابي اعتبارًا من 20 أكتوبر 2026. وقد يؤدي تكرار المخالفة إلى إجراءات تأديبية إضافية وفقًا لسياسة الشركة وقانون العمل الإماراتي.'),
            T(lang, 'You may appeal against this decision in writing to Human Resources within 7 days.', 'يحق لك التظلم كتابيًا لدى الموارد البشرية خلال 7 أيام.')]}
        c['doc'].update(number='DC-2026-0004')
    elif doc_type == 'loan_agreement':
        sched = [{'no': i + 1, 'date': period(2026 + (9 + i) // 12, (9 + i) % 12 + 1, lang), 'amount': '1,000.00', 'balance': money(12000 - 1000 * (i + 1)),
                  'status': T(lang, 'Paid', 'مسدد') if i < 2 else T(lang, 'Due', 'مستحق')} for i in range(12)]
        c['loan'] = {'number': 'LN-2026-0031', 'type': T(lang, 'Personal loan', 'قرض شخصي'), 'amount': 'AED 12,000.00', 'period': 12, 'emi': 'AED 1,000.00',
                     'disbursement_date': d(date(2026, 9, 5), lang), 'applied_on': d(date(2026, 8, 28), lang), 'approved_on': d(date(2026, 9, 2), lang),
                     'status': 'Approved', 'balance': 'AED 10,000.00', 'paid': 'AED 2,000.00', 'schedule': sched, 'terms': loan_terms(lang, '1,000.00')}
        c['doc'].update(number='LN-2026-0031', status='Approved')
        c['signers'] = signers(c, lang, ('Employee (borrower)', 'الموظف (المقترض)', name, '', False), ('HR', 'الموارد البشرية', '', '', False),
                               ('Approved by', 'اعتمده', sign, '', True))
    elif doc_type == 'expense_claim':
        lines = [(date(2026, 10, 3), 'Travel', 'Emirates – DXB-RUH return', 1840.00), (date(2026, 10, 4), 'Hotel', 'Hilton Riyadh – 2 nights', 1620.00),
                 (date(2026, 10, 4), 'Meals', 'Client lunch', 310.50), (date(2026, 10, 6), 'Taxi', 'Careem rides', 145.00)]
        tot = sum(x[3] for x in lines)
        c['claim'] = {'number': 'EXP-2026-0118', 'title': T(lang, 'Client visit – Riyadh', 'زيارة عميل – الرياض'), 'period': f'{d(date(2026, 10, 3), lang)} – {d(date(2026, 10, 6), lang)}',
                      'status': T(lang, 'Approved', 'معتمدة'), 'submitted_at': d(date(2026, 10, 8), lang), 'approved_at': d(date(2026, 10, 10), lang),
                      'total': money(tot), 'advance': '1,000.00', 'reimburse': money(tot - 1000), 'via': T(lang, 'Payroll', 'الرواتب'), 'notes': '',
                      'lines': [{'date': d(a, lang), 'category': b, 'merchant': '', 'description': cc, 'amount': money(v), 'currency': 'AED',
                                 'amount_display': 'AED ' + money(v), 'amount_aed': money(v)} for a, b, cc, v in lines]}
        c['doc'].update(number='EXP-2026-0118', status=T(lang, 'Approved', 'معتمدة'))
        c['signers'] = signers(c, lang, ('Claimed by', 'مقدم المطالبة', name, '', False), ('Approved by (manager)', 'اعتمده (المدير)', 'Omar Haddad', '', False),
                               ('Finance', 'المالية', '', '', True))
    elif doc_type == 'leave_approval':
        c['leave'] = {'number': 'LV-2026-0215', 'type': T(lang, 'Annual leave', 'إجازة سنوية'), 'start': d(date(2026, 12, 20), lang), 'end': d(date(2027, 1, 3), lang),
                      'days': '10', 'status': T(lang, 'Approved', 'معتمدة'), 'reason': 'Family vacation', 'applied_on': d(date(2026, 10, 12), lang), 'balance': '8.5'}
        c['letter'] = {'title': T(lang, 'Leave approval', 'الموافقة على الإجازة'), 'addressee': name, 'signatory_name': sign, 'signatory_title': stitle, 'paragraphs': [
            T(lang, f'Dear {name},', f'عزيزي {name}،'),
            T(lang, f'We are pleased to confirm that your annual leave from {c["leave"]["start"]} to {c["leave"]["end"]} (10 working days) has been approved. You are expected to resume work on {d(date(2027, 1, 4), lang)}.',
              f'يسرنا إبلاغكم بالموافقة على إجازتكم السنوية من {c["leave"]["start"]} إلى {c["leave"]["end"]} (10 أيام عمل). يُتوقع عودتكم إلى العمل في {d(date(2027, 1, 4), lang)}.'),
            T(lang, 'Please make sure that your work is handed over before you leave. We wish you a pleasant leave.', 'يرجى التأكد من تسليم مهامكم قبل بدء الإجازة. نتمنى لكم إجازة سعيدة.')]}
        c['doc'].update(number='LV-2026-0215')
    elif doc_type == 'asset_handover':
        c['handover'] = {'number': 'AH-2026-0019', 'kind': T(lang, 'Handover', 'تسليم'), 'date': d(date(2026, 10, 1), lang), 'status': T(lang, 'Signed', 'موقّع'),
                         'terms': [T(lang, 'The employee is responsible for the assets above and will return them in good condition when asked or when the employment ends. '
                                           'Loss or damage through negligence may be charged to the employee.',
                                     'يتحمل الموظف مسؤولية الأصول المذكورة أعلاه ويلتزم بإعادتها بحالة جيدة عند الطلب أو عند انتهاء الخدمة. وقد يتحمل الموظف تكلفة الفقد أو التلف الناتج عن الإهمال.')],
                         'damage_charge': '', 'signed_by': name, 'signed_at': d(date(2026, 10, 1), lang), 'signature': '',
                         'items': [{'asset': 'Dell Latitude 7440 laptop', 'serial': 'AST-0451 / 7HX2QK3', 'condition': 'New', 'accessories': 'Charger, bag', 'note': ''},
                                   {'asset': 'iPhone 15 (company line)', 'serial': 'AST-0612', 'condition': 'Good', 'accessories': 'Cable', 'note': '+971 50 123 4567'},
                                   {'asset': 'Access card', 'serial': 'AC-1042', 'condition': 'New', 'accessories': '', 'note': 'Level 12'}]}
        c['doc'].update(number='AH-2026-0019', status=T(lang, 'Signed', 'موقّع'))
        c['signers'] = signers(c, lang, ('Employee', 'الموظف', name, d(date(2026, 10, 1), lang), False), ('Issued by (IT / Admin)', 'سلّمه (تقنية المعلومات / الإدارة)', '', '', False),
                               ('HR', 'الموارد البشرية', '', '', True))
    return c


def label(doc_type):
    return BY_KEY[doc_type][1]
