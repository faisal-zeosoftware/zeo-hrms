# -*- coding: utf-8 -*-
"""v1.16.0 – field help knowledge base: what a field means, its format, an example, its rules and UAE notes, in English and
Arabic. Rules are matched (first match wins) against the field's normalised English label, then its control name
(formControlName / name, `emp_` prefix removed). Country variants: `cc` = {pack: {...}} (SA, QA, OM, BH, KW, GEN = any other
country) replace the English / Arabic parts for that country pack; `ae` = the field is UAE-specific (other countries get the
`cc['GEN']` text or the UAE note is left out).

Each text part:  w = what the field means · f = format · x = example · r = rules · u = UAE note.
Keep sentences short, plain and in sentence case (they are shown next to the field).
"""
import re

KB = []


def R(rid, pattern, typ, en, ar, cc=None, ae=False, names=None, only=None):
    KB.append({'id': rid, 're': re.compile(r'^(?:' + pattern + r')$'), 'names': re.compile(r'^(?:' + names + r')$') if names else None,
               't': typ, 'en': dict(zip('wfxru', en)), 'ar': dict(zip('wfxru', ar)), 'cc': cc or {}, 'ae': ae, 'only': only})


E = ''   # empty part

# ------------------------------------------------------------------------------------------------ UAE identity
R('emirates_id', r'(employee )?(emirates id|eid|national id|resident id|emirates id number|emirates id no|national id no|iqama( number)?|qid|civil id( number)?|cpr( number)?)( number| no)?',
  'text',
  ('The Emirates ID number printed on the front of the employee’s Emirates ID card.', '784-YYYY-NNNNNNN-N: 15 digits, starting with 784, then the year of birth.',
   '784-1990-1234567-1', 'Exactly 15 digits (dashes optional). The same number cannot belong to two employees.',
   'Issued by ICP (Federal Authority for Identity, Citizenship, Customs and Port Security). Needed for WPS, MOHRE and medical insurance.'),
  ('رقم الهوية الإماراتية المطبوع على وجه بطاقة الهوية.', '784-YYYY-NNNNNNN-N: ‏15 رقمًا تبدأ بـ 784 ثم سنة الميلاد.',
   '784-1990-1234567-1', '15 رقمًا بالضبط (الشرطات اختيارية). لا يجوز تكرار الرقم لموظفين اثنين.',
   'تصدرها الهيئة الاتحادية للهوية والجنسية والجمارك وأمن المنافذ. مطلوبة لنظام حماية الأجور ووزارة الموارد البشرية والتأمين الصحي.'),
  cc={'SA': {'en': {'w': 'The national ID (citizens) or Iqama number (residents).', 'f': '10 digits; starts with 1 for citizens, 2 for residents.', 'x': '2123456789', 'r': 'Exactly 10 digits.', 'u': ''},
             'ar': {'w': 'رقم الهوية الوطنية (للمواطنين) أو رقم الإقامة (للمقيمين).', 'f': '10 أرقام؛ تبدأ بـ 1 للمواطن و2 للمقيم.', 'x': '2123456789', 'r': '10 أرقام بالضبط.', 'u': ''}},
      'QA': {'en': {'w': 'The Qatar ID (QID) number.', 'f': '11 digits.', 'x': '28435612345', 'r': 'Exactly 11 digits.', 'u': ''},
             'ar': {'w': 'رقم البطاقة الشخصية القطرية.', 'f': '11 رقمًا.', 'x': '28435612345', 'r': '11 رقمًا بالضبط.', 'u': ''}},
      'GEN': {'en': {'w': 'The national or resident ID number as printed on the card.', 'f': 'As printed on the ID card.', 'x': '', 'r': 'The same number cannot belong to two employees.', 'u': ''},
              'ar': {'w': 'رقم الهوية الوطنية أو هوية الإقامة كما هو مطبوع على البطاقة.', 'f': 'كما هو مطبوع على البطاقة.', 'x': '', 'r': 'لا يجوز تكرار الرقم لموظفين اثنين.', 'u': ''}}},
  ae=True, names=r'emirates_id|eid|national_id')
R('emirates_id_dates', r'(emirates id|national id|eid|iqama|qid|civil id)( card)? (issue|expiry|expiration|expiry date|issue date|expires on)( date)?|(emirates id|national id) expiry',
  'date',
  ('The issue or expiry date printed on the Emirates ID card.', 'A date (day / month / year).', '14/03/2027',
   'The expiry date must be after the issue date. HR is reminded before it expires.', 'Renew the Emirates ID together with the residence visa.'),
  ('تاريخ الإصدار أو الانتهاء المطبوع على بطاقة الهوية.', 'تاريخ (يوم / شهر / سنة).', '14/03/2027',
   'يجب أن يكون تاريخ الانتهاء بعد تاريخ الإصدار. تصل للموارد البشرية تذكرة قبل الانتهاء.', 'تُجدَّد الهوية الإماراتية مع تأشيرة الإقامة.'),
  cc={'GEN': {'en': {'u': ''}, 'ar': {'u': ''}}}, ae=True)
R('passport_no', r'passport( number| no)?', 'text',
  ('The passport number as printed on the passport’s photo page.', 'Letters and digits, no spaces.', 'N1234567',
   'Up to 12 characters. The same passport cannot belong to two employees.', 'The passport must be valid for at least 6 months for a UAE visa.'),
  ('رقم جواز السفر كما هو مطبوع في صفحة الصورة.', 'حروف وأرقام بدون مسافات.', 'N1234567',
   'حتى 12 حرفًا. لا يجوز تكرار الجواز لموظفين اثنين.', 'يجب أن يكون الجواز صالحًا 6 أشهر على الأقل لتأشيرة الإمارات.'),
  cc={'GEN': {'en': {'u': ''}, 'ar': {'u': ''}}}, names=r'passport_no|passport_number')
R('passport_dates', r'passport (issue|expiry|expiration)( date)?|passport expiry|passport valid until', 'date',
  ('The issue or expiry date printed on the passport.', 'A date (day / month / year).', '20/08/2030', 'The expiry date must be after the issue date.', E),
  ('تاريخ إصدار أو انتهاء جواز السفر.', 'تاريخ (يوم / شهر / سنة).', '20/08/2030', 'يجب أن يكون تاريخ الانتهاء بعد تاريخ الإصدار.', E))
R('passport_place', r'passport (place of issue|issue place|issued at)|place of issue', 'text',
  ('The city or country where the passport was issued.', 'Text.', 'New Delhi', E, E), ('المدينة أو الدولة التي صدر فيها الجواز.', 'نص.', 'نيودلهي', E, E))
R('passport_country', r'passport country|issuing country', 'select',
  ('The country that issued the passport.', 'Choose from the list.', 'India', E, E), ('الدولة التي أصدرت الجواز.', 'اختر من القائمة.', 'الهند', E, E))
R('visa_uid', r'uid( number| no)?|unified (id|number)|unified number', 'text',
  ('The Unified ID (UID) number shown on the residence visa and entry permit.', 'Digits only.', '123456789', 'Digits only, up to 15.',
   'The UID stays the same for the person across visas.'),
  ('الرقم الموحد (UID) الظاهر على تأشيرة الإقامة وتصريح الدخول.', 'أرقام فقط.', '123456789', 'أرقام فقط، حتى 15 رقمًا.', 'يبقى الرقم الموحد ثابتًا للشخص في كل التأشيرات.'),
  cc={'GEN': {'en': {'w': 'The resident’s unique number on the residence permit.', 'u': ''}, 'ar': {'w': 'الرقم الموحد للمقيم على تصريح الإقامة.', 'u': ''}}}, ae=True, names=r'visa_uid|uid')
R('visa_file', r'visa file( number| no)?|file number|file no', 'text',
  ('The visa file number printed on the residence visa.', 'Emirate code / year / number.', '201/2024/1234567', 'Up to 30 characters.', 'The first part is the issuing emirate (101 Abu Dhabi, 201 Dubai, 301 Sharjah …).'),
  ('رقم ملف التأشيرة المطبوع على تأشيرة الإقامة.', 'رمز الإمارة / السنة / الرقم.', '201/2024/1234567', 'حتى 30 حرفًا.', 'الجزء الأول هو الإمارة المُصدِرة (101 أبوظبي، 201 دبي، 301 الشارقة …).'),
  cc={'GEN': {'en': {'f': 'As printed on the residence permit.', 'x': '', 'u': ''}, 'ar': {'f': 'كما هو مطبوع على تصريح الإقامة.', 'x': '', 'u': ''}}}, ae=True)
R('visa_no', r'visa( residence)?( number| no)|residence( visa)?( number| no)|visa residence number|entry permit( number| no)?', 'text',
  ('The residence visa or entry permit number.', 'Letters, digits and slashes as printed.', '201/2024/7654321', 'Up to 30 characters.', E),
  ('رقم تأشيرة الإقامة أو تصريح الدخول.', 'حروف وأرقام وشرطات مائلة كما هي مطبوعة.', '201/2024/7654321', 'حتى 30 حرفًا.', E))
R('visa_type', r'visa type|visa status|type of visa', 'select',
  ('The kind of visa the employee holds.', 'Choose from the list.', 'Employment visa', E,
   'UAE / GCC nationals need no visa: choose “UAE / GCC national (no visa)”. Golden and green visas are self-sponsored.'),
  ('نوع التأشيرة التي يحملها الموظف.', 'اختر من القائمة.', 'تأشيرة عمل', E, 'مواطنو الإمارات ودول الخليج لا يحتاجون تأشيرة. التأشيرة الذهبية والخضراء كفالة ذاتية.'),
  cc={'GEN': {'en': {'u': ''}, 'ar': {'u': ''}}})
R('visa_dates', r'visa (issue|expiry|expiration)( date)?|visa expiry|visa valid until|residence expiry', 'date',
  ('The issue or expiry date of the residence visa.', 'A date (day / month / year).', '31/12/2026', 'The expiry date must be after the issue date. HR is reminded before it expires.',
   'Overstaying after the expiry leads to daily fines – renew in time.'),
  ('تاريخ إصدار أو انتهاء تأشيرة الإقامة.', 'تاريخ (يوم / شهر / سنة).', '31/12/2026', 'يجب أن يكون تاريخ الانتهاء بعد الإصدار. تصل للموارد البشرية تذكرة قبل الانتهاء.', 'التأخر بعد انتهاء التأشيرة يترتب عليه غرامات يومية – جدّدها في الوقت المناسب.'),
  cc={'GEN': {'en': {'u': ''}, 'ar': {'u': ''}}})
R('visa_sponsor', r'visa sponsor|sponsor( name)?', 'text',
  ('Who sponsors the residence visa (the company, a family member or the employee).', 'Text.', 'Adept Business Solutions LLC', E, E),
  ('الجهة الكافلة لتأشيرة الإقامة (الشركة أو أحد أفراد الأسرة أو الموظف).', 'نص.', 'شركة أديبت لحلول الأعمال ذ.م.م', E, E))
R('visa_location', r'visa location|select visa location|visa emirate', 'select',
  ('The emirate (or place) that issued the visa.', 'Choose from the list.', 'Sharjah', E, E), ('الإمارة (أو الجهة) التي أصدرت التأشيرة.', 'اختر من القائمة.', 'الشارقة', E, E))
R('labour_card', r'(labour|labor) card( number| no)?|work permit( number| no)?|labour card no', 'text',
  ('The MOHRE labour card (work permit) number.', 'Digits as printed on the labour card.', '12345678', 'Up to 20 characters.',
   'Issued by the Ministry of Human Resources and Emiratisation (MOHRE); needed for the WPS salary file.'),
  ('رقم بطاقة العمل (تصريح العمل) من وزارة الموارد البشرية والتوطين.', 'أرقام كما هي مطبوعة على بطاقة العمل.', '12345678', 'حتى 20 حرفًا.',
   'تصدرها وزارة الموارد البشرية والتوطين وهي مطلوبة لملف حماية الأجور.'),
  cc={'GEN': {'en': {'w': 'The work permit number issued by the labour authority.', 'f': 'As printed on the permit.', 'u': ''}, 'ar': {'w': 'رقم تصريح العمل الصادر عن جهة العمل المختصة.', 'f': 'كما هو مطبوع على التصريح.', 'u': ''}}},
  ae=True, names=r'labour_card_no|work_permit_no')
R('labour_card_dates', r'(labour|labor) card (issue|expiry|expiration)( date)?|work permit (issue|expiry)( date)?', 'date',
  ('The issue or expiry date of the labour card / work permit.', 'A date (day / month / year).', '15/06/2027', 'The expiry date must be after the issue date.', 'Renew the labour card before it expires to avoid MOHRE fines.'),
  ('تاريخ إصدار أو انتهاء بطاقة العمل / تصريح العمل.', 'تاريخ (يوم / شهر / سنة).', '15/06/2027', 'يجب أن يكون تاريخ الانتهاء بعد الإصدار.', 'جدّد بطاقة العمل قبل انتهائها لتجنب غرامات الوزارة.'),
  cc={'GEN': {'en': {'u': ''}, 'ar': {'u': ''}}}, ae=True)
R('person_id', r'(mohre )?person(al)? id|mohre person id|person code|personal id', 'text',
  ('The 14-digit MOHRE person ID (person code) of the employee.', '14 digits.', '10012345678901', 'Exactly 14 digits.',
   'Printed on the labour card and the MOHRE contract; the WPS SIF file uses it to identify the employee.'),
  ('الرقم الشخصي للموظف لدى وزارة الموارد البشرية والتوطين (14 رقمًا).', '14 رقمًا.', '10012345678901', '14 رقمًا بالضبط.', 'مطبوع على بطاقة العمل وعقد الوزارة، ويستخدمه ملف حماية الأجور لتعريف الموظف.'),
  cc={'GEN': {'en': {'w': 'The person number used by the labour authority.', 'f': 'As issued.', 'x': '', 'r': E, 'u': ''}, 'ar': {'w': 'الرقم الشخصي لدى جهة العمل المختصة.', 'f': 'كما صدر.', 'x': '', 'r': '', 'u': ''}}},
  ae=True, names=r'person_id|mohre_person_id')
R('mohre_contract', r'mohre contract( number| no)?|labour contract( number| no)?|contract number', 'text',
  ('The MOHRE employment contract number.', 'As printed on the contract.', 'MB123456789AE', 'Up to 30 characters.', 'Contracts are fixed-term (Federal Decree-Law 33/2021).'),
  ('رقم عقد العمل لدى وزارة الموارد البشرية والتوطين.', 'كما هو مطبوع على العقد.', 'MB123456789AE', 'حتى 30 حرفًا.', 'عقود العمل محددة المدة (المرسوم بقانون اتحادي 33 لسنة 2021).'),
  cc={'GEN': {'en': {'w': 'The employment contract number.', 'u': ''}, 'ar': {'w': 'رقم عقد العمل.', 'u': ''}}}, ae=True)
R('mohre_contract_type', r'mohre contract type|contract type', 'select',
  ('Limited (fixed term) or unlimited contract.', 'Choose from the list.', 'Limited (fixed term)', E, 'New UAE contracts are limited (fixed term).'),
  ('عقد محدد المدة أو غير محدد المدة.', 'اختر من القائمة.', 'محدد المدة', E, 'العقود الجديدة في الإمارات محددة المدة.'), cc={'GEN': {'en': {'u': ''}, 'ar': {'u': ''}}})
R('contract_dates', r'contract (start|end)( date)?|contract end date', 'date',
  ('The first or last day of the employment contract.', 'A date (day / month / year).', '01/01/2026', 'The end date must be after the start date.', E),
  ('اليوم الأول أو الأخير من عقد العمل.', 'تاريخ (يوم / شهر / سنة).', '01/01/2026', 'يجب أن يكون تاريخ النهاية بعد البداية.', E))
R('driving_licence', r'driving licen[cs]e( number| no)?|licen[cs]e( number| no)', 'text',
  ('The driving licence number.', 'As printed on the licence.', '1234567', 'Up to 20 characters.', 'A UAE licence is issued per emirate (choose the emirate below).'),
  ('رقم رخصة القيادة.', 'كما هو مطبوع على الرخصة.', '1234567', 'حتى 20 حرفًا.', 'تصدر رخصة القيادة في الإمارات من كل إمارة (اختر الإمارة).'), cc={'GEN': {'en': {'u': ''}, 'ar': {'u': ''}}})
R('driving_licence_dates', r'driving licen[cs]e (issue|expiry)( date)?', 'date',
  ('The issue or expiry date of the driving licence.', 'A date (day / month / year).', '10/10/2028', 'The expiry date must be after the issue date.', E),
  ('تاريخ إصدار أو انتهاء رخصة القيادة.', 'تاريخ (يوم / شهر / سنة).', '10/10/2028', 'يجب أن يكون تاريخ الانتهاء بعد الإصدار.', E))
R('driving_licence_cat', r'driving licen[cs]e (categories|category|emirate)', 'text',
  ('The licence categories, or the emirate that issued the licence.', 'Text or a choice from the list.', 'Light vehicle, Motorcycle', E, E),
  ('فئات الرخصة أو الإمارة التي أصدرتها.', 'نص أو اختيار من القائمة.', 'مركبة خفيفة، دراجة نارية', E, E))
R('insurance_card', r'(medical )?insurance card( number| no)?|insurance (policy )?(number|no)|insurance policy', 'text',
  ('The medical (or asset) insurance card / policy number.', 'As printed on the card or policy.', 'DHA-123456789', 'Up to 40 characters.', 'Medical insurance is mandatory for employees in Dubai and Abu Dhabi.'),
  ('رقم بطاقة أو وثيقة التأمين (الطبي أو تأمين الأصل).', 'كما هو مطبوع على البطاقة أو الوثيقة.', 'DHA-123456789', 'حتى 40 حرفًا.', 'التأمين الصحي إلزامي للموظفين في دبي وأبوظبي.'), cc={'GEN': {'en': {'u': ''}, 'ar': {'u': ''}}})
R('insurance_provider', r'insurance provider|insurer|provider', 'text',
  ('The insurance company.', 'Text.', 'Daman', E, E), ('شركة التأمين.', 'نص.', 'ضمان', E, E))
R('insurance_expiry', r'insurance expiry( date)?|insurance expires', 'date',
  ('The date the insurance cover ends.', 'A date (day / month / year).', '31/12/2026', 'HR is reminded before it expires.', E),
  ('تاريخ انتهاء التغطية التأمينية.', 'تاريخ (يوم / شهر / سنة).', '31/12/2026', 'تصل للموارد البشرية تذكرة قبل الانتهاء.', E))
R('arabic_name', r'name in arabic|arabic name|full name arabic', 'text',
  ('The employee’s full name in Arabic, as on the Emirates ID or visa.', 'Arabic letters.', 'أحمد محمد المنصوري', 'Up to 150 characters.', 'Used on Arabic letters, contracts and MOHRE documents.'),
  ('الاسم الكامل للموظف بالعربية كما في الهوية أو التأشيرة.', 'حروف عربية.', 'أحمد محمد المنصوري', 'حتى 150 حرفًا.', 'يُستخدم في الخطابات والعقود ومستندات الوزارة العربية.'), cc={'GEN': {'en': {'u': ''}, 'ar': {'u': ''}}})

# ------------------------------------------------------------------------------------------------ person
R('first_name', r'(enter )?first name|given name|fname', 'text',
  ('The employee’s first (given) name as in the passport.', 'Letters, spaces, apostrophes and hyphens.', 'Ahmed', 'Required. Up to 50 characters.', 'Use the spelling of the passport / Emirates ID – WPS and visa papers must match.'),
  ('الاسم الأول للموظف كما في جواز السفر.', 'حروف ومسافات وفواصل علوية وشرطات.', 'أحمد', 'إلزامي. حتى 50 حرفًا.', 'استخدم تهجئة جواز السفر / الهوية – يجب أن تتطابق مستندات الأجور والتأشيرة.'), cc={'GEN': {'en': {'u': ''}, 'ar': {'u': ''}}},
  names=r'first_name|firstname')
R('middle_name', r'middle name', 'text', ('The employee’s middle name, if any.', 'Letters.', 'Mohammed', 'Optional. Up to 50 characters.', E),
  ('الاسم الأوسط للموظف إن وجد.', 'حروف.', 'محمد', 'اختياري. حتى 50 حرفًا.', E))
R('last_name', r'(enter )?last name|surname|family name', 'text', ('The employee’s last name (family name) as in the passport.', 'Letters, spaces and hyphens.', 'Al Mansoori', 'Up to 50 characters.', E),
  ('اسم العائلة للموظف كما في جواز السفر.', 'حروف ومسافات وشرطات.', 'المنصوري', 'حتى 50 حرفًا.', E), names=r'last_name|lastname')
R('preferred_name', r'preferred name|known as|nick ?name', 'text', ('The name the employee likes to be called (shown in the app).', 'Text.', 'Sam', E, E),
  ('الاسم الذي يفضّل الموظف أن يُنادى به (يظهر في التطبيق).', 'نص.', 'سام', E, E))
R('father_mother', r'father s name|father name|mother s name|mother name', 'text', ('The parent’s full name, as on official papers.', 'Letters.', 'Mohammed Al Mansoori', E, E),
  ('الاسم الكامل للوالد / الوالدة كما في المستندات الرسمية.', 'حروف.', 'محمد المنصوري', E, E))
R('dob', r'date of birth|birth date|dob|employee date of birth', 'date',
  ('The employee’s date of birth as in the passport.', 'A date (day / month / year).', '15/04/1990', 'Must be in the past; employees must be at least 15 years old (18 for most jobs).',
   'UAE law: no employment under 15; workers under 18 need a special permit.'),
  ('تاريخ ميلاد الموظف كما في جواز السفر.', 'تاريخ (يوم / شهر / سنة).', '15/04/1990', 'يجب أن يكون في الماضي؛ لا يقل عمر الموظف عن 15 سنة (18 لمعظم الوظائف).', 'قانون الإمارات: لا تشغيل لمن هم دون 15 سنة، ومن هم دون 18 يحتاجون تصريحًا خاصًا.'),
  cc={'GEN': {'en': {'u': ''}, 'ar': {'u': ''}}}, names=r'date_of_birth|dob|ef_date_of_birth')
R('place_of_birth', r'place of birth|birth place', 'text', ('The city and country where the employee was born.', 'Text.', 'Kerala, India', E, E), ('مدينة ودولة ميلاد الموظف.', 'نص.', 'كيرالا، الهند', E, E))
R('gender', r'(select )?gender|sex', 'select', ('The employee’s gender as in the passport.', 'Choose from the list.', 'Male', E, 'Used for gender-based leave (maternity / paternity).'),
  ('جنس الموظف كما في جواز السفر.', 'اختر من القائمة.', 'ذكر', E, 'يُستخدم لإجازات الأمومة والأبوة.'), cc={'GEN': {'en': {'u': ''}, 'ar': {'u': ''}}}, names=r'gender')
R('marital', r'marital status|marital', 'select', ('Single, married, divorced or widowed.', 'Choose from the list.', 'Married', E, E), ('أعزب أو متزوج أو مطلق أو أرمل.', 'اختر من القائمة.', 'متزوج', E, E), names=r'marital_status|marital')
R('nationality', r'nationality|citizenship', 'select', ('The employee’s nationality (passport country).', 'Choose from the list.', 'Indian', E, 'UAE nationals count towards the Emiratisation target.'),
  ('جنسية الموظف (دولة جواز السفر).', 'اختر من القائمة.', 'هندي', E, 'المواطنون الإماراتيون يُحتسبون ضمن نسبة التوطين.'), cc={'GEN': {'en': {'u': ''}, 'ar': {'u': ''}}}, names=r'nationality')
R('religion', r'religion', 'select', ('The employee’s religion (used for religious holidays, if set up).', 'Choose from the list.', 'Islam', 'Optional.', E),
  ('ديانة الموظف (تُستخدم للعطل الدينية إن وُجدت).', 'اختر من القائمة.', 'الإسلام', 'اختياري.', E), names=r'relegion|religion')
R('blood', r'blood group|blood type', 'select', ('The employee’s blood group (for emergencies).', 'Choose from the list.', 'O+', 'Optional.', E),
  ('فصيلة دم الموظف (للحالات الطارئة).', 'اختر من القائمة.', 'O+', 'اختياري.', E), names=r'blood_group|blood')
R('title', r'title|salutation', 'select', ('How the person is addressed in letters.', 'Choose from the list.', 'Mr', E, E), ('طريقة مخاطبة الشخص في الخطابات.', 'اختر من القائمة.', 'السيد', E, E))
R('photo', r'photo(s)?|profile (picture|pic|photo)|picture|image', 'file', ('A clear photo (head and shoulders).', 'JPG or PNG image.', 'photo.jpg', 'Up to 5 MB.', E),
  ('صورة واضحة (الرأس والكتفين).', 'صورة JPG أو PNG.', 'photo.jpg', 'حتى 5 ميغابايت.', E), names=r'profile_pic|photo|profileupload')

# ------------------------------------------------------------------------------------------------ contact
R('email', r'(personal |company |work |official )?(e mail|email)( id| address)?|personal e mail|company e mail|email id|e mail', 'email',
  ('An e-mail address used for notifications, payslips and password resets.', 'name@domain', 'ahmed.mansoori@company.ae', 'Must be a valid e-mail address. A login e-mail cannot be used twice.', E),
  ('بريد إلكتروني للإشعارات وقسائم الرواتب واستعادة كلمة المرور.', 'name@domain', 'ahmed.mansoori@company.ae', 'يجب أن يكون عنوانًا صحيحًا. لا يتكرر بريد الدخول.', E),
  names=r'.*email.*')
R('mobile', r'(personal |company )?(mobile|phone|contact|telephone|tel|other phone|alt phone)( number| no)?( 1| 2)?( optional)?|company number( 1| 2)?( optional)?|contact( 2)?|mobile number', 'tel',
  ('A phone number the person can be reached on.', 'International format: +971 5X XXX XXXX.', '+971 50 123 4567', 'Digits, spaces, + and dashes; 7 to 15 digits.',
   'UAE mobile numbers start with 05 (050, 052, 054, 055, 056, 058) – +971 5X with the country code.'),
  ('رقم هاتف يمكن التواصل عبره.', 'الصيغة الدولية: ‎+971 5X XXX XXXX', '+971 50 123 4567', 'أرقام ومسافات و+ وشرطات؛ من 7 إلى 15 رقمًا.', 'أرقام الجوال في الإمارات تبدأ بـ 05 (050، 052، 054، 055، 056، 058) – أي ‎+971 5X مع رمز الدولة.'),
  cc={'SA': {'en': {'f': '+966 5X XXX XXXX', 'x': '+966 55 123 4567', 'u': 'Saudi mobile numbers start with 05.'}, 'ar': {'f': '‎+966 5X XXX XXXX', 'x': '+966 55 123 4567', 'u': 'أرقام الجوال السعودية تبدأ بـ 05.'}},
      'QA': {'en': {'f': '+974 XXXX XXXX (8 digits)', 'x': '+974 5512 3456', 'u': ''}, 'ar': {'f': '‎+974 XXXX XXXX (8 أرقام)', 'x': '+974 5512 3456', 'u': ''}},
      'OM': {'en': {'f': '+968 9XXX XXXX (8 digits)', 'x': '+968 9123 4567', 'u': ''}, 'ar': {'f': '‎+968 9XXX XXXX (8 أرقام)', 'x': '+968 9123 4567', 'u': ''}},
      'BH': {'en': {'f': '+973 3XXX XXXX (8 digits)', 'x': '+973 3612 3456', 'u': ''}, 'ar': {'f': '‎+973 3XXX XXXX (8 أرقام)', 'x': '+973 3612 3456', 'u': ''}},
      'KW': {'en': {'f': '+965 XXXX XXXX (8 digits)', 'x': '+965 5012 3456', 'u': ''}, 'ar': {'f': '‎+965 XXXX XXXX (8 أرقام)', 'x': '+965 5012 3456', 'u': ''}},
      'GEN': {'en': {'f': 'International format: +<country code> <number>.', 'x': '+44 20 7946 0958', 'u': ''}, 'ar': {'f': 'الصيغة الدولية: ‎+رمز الدولة ثم الرقم.', 'x': '+44 20 7946 0958', 'u': ''}}},
  names=r'.*(mobile|phone).*')
R('address', r'(present |permanent |home |current |bank |company )?address( line [12])?|street', 'textarea',
  ('The full postal address.', 'Building / flat, street, area, city.', 'Flat 1204, Al Taawun Tower, Al Taawun, Sharjah', 'Up to 255 characters.', 'UAE addresses usually have no postcode – give the area and a P.O. Box if there is one.'),
  ('العنوان البريدي الكامل.', 'المبنى / الشقة، الشارع، المنطقة، المدينة.', 'شقة 1204، برج التعاون، التعاون، الشارقة', 'حتى 255 حرفًا.', 'العناوين في الإمارات غالبًا بلا رمز بريدي – اذكر المنطقة وصندوق البريد إن وجد.'),
  cc={'GEN': {'en': {'u': ''}, 'ar': {'u': ''}}}, names=r'.*address.*')
R('city', r'city|town', 'text', ('The city.', 'Text.', 'Sharjah', E, E), ('المدينة.', 'نص.', 'الشارقة', E, E), names=r'city')
R('state', r'state|state emirate|emirate|province|provinces?|region', 'select', ('The emirate, state or region.', 'Choose from the list.', 'Sharjah', E, 'The seven emirates: Abu Dhabi, Dubai, Sharjah, Ajman, Umm Al Quwain, Ras Al Khaimah, Fujairah.'),
  ('الإمارة أو الولاية أو المنطقة.', 'اختر من القائمة.', 'الشارقة', E, 'الإمارات السبع: أبوظبي، دبي، الشارقة، عجمان، أم القيوين، رأس الخيمة، الفجيرة.'), cc={'GEN': {'en': {'x': '', 'u': ''}, 'ar': {'x': '', 'u': ''}}}, names=r'state_id|state')
R('country', r'(select )?country|country name', 'select', ('The country.', 'Choose from the list.', 'United Arab Emirates', E, E), ('الدولة.', 'اختر من القائمة.', 'الإمارات العربية المتحدة', E, E), names=r'country_id|country|cntry')
R('pincode', r'pin ?code|postcode|post code|zip( code)?|po box|p o box', 'text', ('The postcode or P.O. Box.', 'Digits.', '12345', E, 'The UAE has no postcodes – enter the P.O. Box.'),
  ('الرمز البريدي أو صندوق البريد.', 'أرقام.', '12345', E, 'لا توجد رموز بريدية في الإمارات – أدخل صندوق البريد.'), cc={'GEN': {'en': {'u': ''}, 'ar': {'u': ''}}})
R('emergency_name', r'family member name|member name|contact name|emergency contact', 'text', ('The person’s full name.', 'Text.', 'Fatima Al Mansoori', E, E), ('الاسم الكامل للشخص.', 'نص.', 'فاطمة المنصوري', E, E))
R('relation', r'relation(ship)?|emp relation', 'text', ('How this person is related to the employee.', 'Text or a choice.', 'Spouse', E, E), ('صلة القرابة بالموظف.', 'نص أو اختيار.', 'زوجة', E, E))

# ------------------------------------------------------------------------------------------------ employment
R('emp_code', r'employee (code|id|no|number)|emp(loyee)? code|staff (id|no)|select employee id', 'text',
  ('The employee’s unique code in the company.', 'Prefix + number (automatic numbering may fill it in).', 'EMP-0042', 'Unique in the company; up to 50 characters.', E),
  ('رقم الموظف الفريد في الشركة.', 'بادئة + رقم (قد يملؤه الترقيم التلقائي).', 'EMP-0042', 'فريد في الشركة؛ حتى 50 حرفًا.', E), names=r'emp_code|employee_code|code')
R('join_date', r'(date of )?joining( date)?|joined( date)?|hired( date| on)?|date of joining|hire date|start your first payroll from', 'date',
  ('The first working day of the employee in the company.', 'A date (day / month / year).', '01/02/2024', 'Required. Leave, gratuity and probation are counted from this date.',
   'End-of-service gratuity starts after one year of continuous service.'),
  ('أول يوم عمل للموظف في الشركة.', 'تاريخ (يوم / شهر / سنة).', '01/02/2024', 'إلزامي. تُحسب الإجازات ومكافأة نهاية الخدمة والتجربة من هذا التاريخ.', 'تستحق مكافأة نهاية الخدمة بعد سنة من الخدمة المتصلة.'),
  cc={'GEN': {'en': {'u': ''}, 'ar': {'u': ''}}}, names=r'joined_date|joining_date|emp_joined_date')
R('branch', r'(select )?branch(es)?( name)?|branch choose|branches empty all|select branches|work branch', 'select',
  ('The company branch (office / site) the record belongs to.', 'Choose from the list.', 'Sharjah', 'You only see the branches you have access to.', E),
  ('فرع الشركة (المكتب / الموقع) الذي يتبعه السجل.', 'اختر من القائمة.', 'الشارقة', 'تظهر لك الفروع المسموح لك بها فقط.', E), names=r'branch(_id)?|brch|brnch|branch_ids')
R('department', r'(select )?department( name)?|department choose|department all departments|dept', 'select',
  ('The department.', 'Choose from the list.', 'Finance', E, E), ('القسم.', 'اختر من القائمة.', 'المالية', E, E), names=r'dept(_id)?|department(_id)?')
R('designation', r'(select )?designation( name)?|job title|position title|position|employee designation', 'select',
  ('The employee’s job title.', 'Choose from the list (or text).', 'Accountant', E, 'The MOHRE job title on the labour card may differ – keep both consistent.'),
  ('المسمى الوظيفي للموظف.', 'اختر من القائمة (أو نص).', 'محاسب', E, 'قد يختلف المسمى الوظيفي في بطاقة العمل – حافظ على تطابقهما.'), cc={'GEN': {'en': {'u': ''}, 'ar': {'u': ''}}}, names=r'desgntn(_id)?|designation(_id)?')
R('category', r'(select )?category( name)?|employee category|grade|grade result', 'select',
  ('The employee category / grade (used by leave, overtime and salary rules).', 'Choose from the list.', 'Staff', E, E),
  ('فئة / درجة الموظف (تستخدمها قواعد الإجازات والعمل الإضافي والرواتب).', 'اختر من القائمة.', 'موظفون', E, E), names=r'ctgry(_id)?|category(_id)?')
R('manager', r'(reporting )?manager|reports to|line manager|task manager|select task manager|search manager', 'select',
  ('The employee who approves this person’s requests first.', 'Choose an employee.', 'Khalid Al Mazrouei', 'Cannot be the employee themself.', E),
  ('الموظف الذي يعتمد طلبات هذا الشخص أولًا.', 'اختر موظفًا.', 'خالد المزروعي', 'لا يمكن أن يكون الموظف نفسه.', E), names=r'reporting_manager|manager')
R('location', r'(work |posting )?location( name)?|select work location|current location|venue|where', 'text',
  ('The place where the work (or event) happens.', 'Text or a choice.', 'Head office – Sharjah', E, E), ('مكان العمل (أو الحدث).', 'نص أو اختيار.', 'المكتب الرئيسي – الشارقة', E, E), names=r'posting_location|location')
R('employment_type', r'employment type|employee type|work type', 'select',
  ('Full time, part time, temporary …', 'Choose from the list.', 'Full time', E, 'UAE law allows full-time, part-time, temporary and flexible work.'),
  ('دوام كامل أو جزئي أو مؤقت …', 'اختر من القائمة.', 'دوام كامل', E, 'يسمح قانون الإمارات بالدوام الكامل والجزئي والمؤقت والمرن.'), cc={'GEN': {'en': {'u': ''}, 'ar': {'u': ''}}})
R('probation', r'probation( end)?( date)?|probation (period|days|status)|allowed in probation', 'date',
  ('The end of the probation period.', 'A date (day / month / year).', '01/08/2024', 'At most 6 months after joining.', 'UAE probation is at most 6 months; notice during probation is 14 days (employer).'),
  ('نهاية فترة التجربة.', 'تاريخ (يوم / شهر / سنة).', '01/08/2024', 'بحد أقصى 6 أشهر بعد التعيين.', 'فترة التجربة في الإمارات 6 أشهر كحد أقصى؛ والإشعار خلالها 14 يومًا من صاحب العمل.'), cc={'GEN': {'en': {'u': ''}, 'ar': {'u': ''}}})
R('notice', r'notice (period|days)|resignation days|termination days', 'number',
  ('The notice period in days.', 'Whole number of days.', '30', 'Between 30 and 90 days.', 'UAE law: notice is 30 to 90 days as agreed in the contract.'),
  ('فترة الإشعار بالأيام.', 'عدد صحيح من الأيام.', '30', 'بين 30 و90 يومًا.', 'قانون الإمارات: الإشعار من 30 إلى 90 يومًا حسب العقد.'), cc={'GEN': {'en': {'r': '', 'u': ''}, 'ar': {'r': '', 'u': ''}}})
R('lwd', r'last working (date|day)|resigned on|date of leaving|leaving date|relieving date', 'date',
  ('The employee’s last day of work.', 'A date (day / month / year).', '30/09/2026', 'Not before the joining date.', 'Final settlement and gratuity are paid within 14 days after the last day.'),
  ('آخر يوم عمل للموظف.', 'تاريخ (يوم / شهر / سنة).', '30/09/2026', 'ليس قبل تاريخ التعيين.', 'تُدفع التسوية النهائية والمكافأة خلال 14 يومًا من آخر يوم.'), cc={'GEN': {'en': {'u': ''}, 'ar': {'u': ''}}})
R('confirmation', r'confirmation date|confirmed on', 'date', ('The date the employee passed probation.', 'A date (day / month / year).', '01/08/2024', E, E), ('تاريخ اجتياز الموظف فترة التجربة.', 'تاريخ (يوم / شهر / سنة).', '01/08/2024', E, E))
R('leaving_reason', r'leaving reason|reason for leaving', 'textarea', ('Why the employee left (or left the previous job).', 'Text.', 'Better opportunity', E, E), ('سبب ترك الموظف للعمل (أو العمل السابق).', 'نص.', 'فرصة أفضل', E, E))
R('is_ess', r'is ess|self service( access)?|ess access', 'checkbox', ('Tick to let the employee use self service (leave, payslips, profile).', 'Tick box.', 'Ticked', 'The employee needs a login e-mail.', E),
  ('اختر للسماح للموظف باستخدام الخدمة الذاتية (الإجازات وقسائم الراتب والملف).', 'مربع اختيار.', 'محدد', 'يحتاج الموظف بريدًا للدخول.', E), names=r'is_ess')
R('active', r'(is |employee is )?active|enabled|status active', 'checkbox', ('Untick to stop using this record without deleting it.', 'Tick box.', 'Ticked', 'Inactive records are hidden from new transactions but kept in history.', E),
  ('ألغِ التحديد لإيقاف استخدام السجل دون حذفه.', 'مربع اختيار.', 'محدد', 'السجلات غير النشطة لا تظهر في المعاملات الجديدة لكنها تبقى في السجل.', E), names=r'is_active|active|enabled')
R('username', r'user ?name|login( name)?', 'text', ('The name used to log in.', 'Letters, digits, dots, underscores.', 'ahmed.mansoori', 'Unique; no spaces.', E),
  ('الاسم المستخدم لتسجيل الدخول.', 'حروف وأرقام ونقاط وشرطات سفلية.', 'ahmed.mansoori', 'فريد؛ بلا مسافات.', E), names=r'username|user_name')
R('password', r'(new |current |confirm |email host )?password', 'password', ('A secret password.', 'At least 8 characters.', '********', 'Use letters, digits and a symbol; never share it.', E),
  ('كلمة مرور سرية.', '8 أحرف على الأقل.', '********', 'استخدم حروفًا وأرقامًا ورمزًا؛ لا تشاركها.', E), names=r'.*password.*')

# ------------------------------------------------------------------------------------------------ bank / payroll
R('iban', r'iban( number| no)?', 'text',
  ('The employee’s bank account IBAN for salary transfer.', 'AE + 21 digits (23 characters), no spaces.', 'AE070331234567890123456', 'Exactly 23 characters starting with AE; the check digits are verified.',
   'WPS salary files need the IBAN of a UAE bank account in the employee’s name.'),
  ('رقم الآيبان لحساب الموظف المصرفي لتحويل الراتب.', 'AE + ‏21 رقمًا (23 خانة) بلا مسافات.', 'AE070331234567890123456', '23 خانة بالضبط تبدأ بـ AE؛ يتم التحقق من خانات التدقيق.', 'ملف حماية الأجور يحتاج آيبان حساب في بنك إماراتي باسم الموظف.'),
  cc={'SA': {'en': {'f': 'SA + 22 digits (24 characters).', 'x': 'SA0380000000608010167519', 'r': 'Exactly 24 characters starting with SA.', 'u': 'Mudad / WPS needs a Saudi IBAN.'}, 'ar': {'f': 'SA + ‏22 رقمًا (24 خانة).', 'x': 'SA0380000000608010167519', 'r': '24 خانة بالضبط تبدأ بـ SA.', 'u': 'منصة مدد تحتاج آيبان سعودي.'}},
      'QA': {'en': {'f': 'QA + 27 characters (29 in all).', 'x': 'QA58DOHB00001234567890ABCDEFG', 'r': 'Exactly 29 characters starting with QA.', 'u': ''}, 'ar': {'f': 'QA + ‏27 خانة (29 خانة).', 'x': 'QA58DOHB00001234567890ABCDEFG', 'r': '29 خانة بالضبط تبدأ بـ QA.', 'u': ''}},
      'OM': {'en': {'f': 'OM + 21 characters (23 in all).', 'x': 'OM810180000001299123456', 'r': 'Exactly 23 characters starting with OM.', 'u': ''}, 'ar': {'f': 'OM + ‏21 خانة (23 خانة).', 'x': 'OM810180000001299123456', 'r': '23 خانة بالضبط تبدأ بـ OM.', 'u': ''}},
      'BH': {'en': {'f': 'BH + 20 characters (22 in all).', 'x': 'BH67BMAG00001299123456', 'r': 'Exactly 22 characters starting with BH.', 'u': ''}, 'ar': {'f': 'BH + ‏20 خانة (22 خانة).', 'x': 'BH67BMAG00001299123456', 'r': '22 خانة بالضبط تبدأ بـ BH.', 'u': ''}},
      'KW': {'en': {'f': 'KW + 28 characters (30 in all).', 'x': 'KW81CBKU0000000000001234560101', 'r': 'Exactly 30 characters starting with KW.', 'u': ''}, 'ar': {'f': 'KW + ‏28 خانة (30 خانة).', 'x': 'KW81CBKU0000000000001234560101', 'r': '30 خانة بالضبط تبدأ بـ KW.', 'u': ''}},
      'GEN': {'en': {'f': 'Country code + 2 check digits + account (up to 34 characters).', 'x': 'GB29NWBK60161331926819', 'r': 'The check digits are verified.', 'u': ''}, 'ar': {'f': 'رمز الدولة + خانتا تدقيق + الحساب (حتى 34 خانة).', 'x': 'GB29NWBK60161331926819', 'r': 'يتم التحقق من خانات التدقيق.', 'u': ''}}},
  names=r'iban(_number)?')
R('routing', r'(bank )?routing code( 9 digits)?|route code|routing( number)?|swift|bic|agent id|wps agent( id)?', 'text',
  ('The bank (or exchange house) routing code used by the WPS salary file.', '9 digits.', '803320101', 'Exactly 9 digits (UAE Central Bank routing code).',
   'Find it on the bank’s website or ask the bank; exchange houses have their own WPS agent ID.'),
  ('رمز توجيه البنك (أو شركة الصرافة) المستخدم في ملف حماية الأجور.', '9 أرقام.', '803320101', '9 أرقام بالضبط (رمز التوجيه لدى المصرف المركزي).', 'تجده في موقع البنك أو اطلبه من البنك؛ لشركات الصرافة رقم وكيل خاص بنظام حماية الأجور.'),
  cc={'GEN': {'en': {'w': 'The bank routing / sort code given by the bank.', 'f': 'As given by the bank.', 'x': '', 'r': E, 'u': ''}, 'ar': {'w': 'رمز التوجيه / الفرز الذي يحدده البنك.', 'f': 'كما يحدده البنك.', 'x': '', 'r': '', 'u': ''}}},
  ae=True, names=r'route_code|routing_code|wps_agent')
R('bank_name', r'bank( name)?|bank branch|branch name', 'text', ('The bank (and its branch) that holds the account.', 'Text.', 'Emirates NBD – Al Qasba', E, E),
  ('البنك (وفرعه) الذي يوجد فيه الحساب.', 'نص.', 'بنك الإمارات دبي الوطني – القصباء', E, E), names=r'bank_name|bank')
R('account_no', r'(bank )?account (number|no)|account', 'text', ('The bank account number.', 'Digits as given by the bank.', '1012345678901', 'Digits only.', 'For WPS, the IBAN is what counts.'),
  ('رقم الحساب المصرفي.', 'أرقام كما يحددها البنك.', '1012345678901', 'أرقام فقط.', 'في نظام حماية الأجور يُعتمد الآيبان.'), cc={'GEN': {'en': {'u': ''}, 'ar': {'u': ''}}}, names=r'account_number|account_no')
R('payment_mode', r'payment mode|pay mode|salary (account|mode)', 'select',
  ('How the salary is paid.', 'Choose from the list.', 'WPS (salary transfer)', E, 'Companies registered with MOHRE must pay through WPS.'),
  ('طريقة دفع الراتب.', 'اختر من القائمة.', 'نظام حماية الأجور (تحويل)', E, 'الشركات المسجلة لدى الوزارة ملزمة بالدفع عبر نظام حماية الأجور.'), cc={'GEN': {'en': {'u': ''}, 'ar': {'u': ''}}})
R('wps', r'wps|wps (file|sif)|employer (unique )?id|establishment id|mol (id|establishment)', 'text',
  ('The WPS (Wage Protection System) setting or file.', 'As registered with MOHRE / the bank.', '0000123456789', E, 'The SIF file is uploaded to the bank / exchange house each month.'),
  ('إعداد أو ملف نظام حماية الأجور.', 'كما هو مسجل لدى الوزارة / البنك.', '0000123456789', E, 'يُرفع ملف SIF للبنك / شركة الصرافة كل شهر.'),
  cc={'GEN': {'en': {'w': 'The salary protection setting or file.', 'u': ''}, 'ar': {'w': 'إعداد أو ملف حماية الأجور.', 'u': ''}}}, ae=True)
R('basic', r'basic( salary| pay)?|gross( salary)?|net( salary)?|salary( per month)?|monthly salary|last salary per month|employee salary per month', 'number',
  ('The salary amount per month.', 'Amount with up to 2 decimals.', '8500.00', 'Not negative.', 'Gratuity is worked out on the basic salary (21 days per year for the first 5 years, 30 days after).'),
  ('مبلغ الراتب الشهري.', 'مبلغ حتى منزلتين عشريتين.', '8500.00', 'لا يكون سالبًا.', 'تُحسب مكافأة نهاية الخدمة على الراتب الأساسي (21 يومًا عن كل سنة في أول 5 سنوات و30 يومًا بعدها).'),
  cc={'GEN': {'en': {'u': ''}, 'ar': {'u': ''}}})
R('salary_component', r'(salary )?component( name)?|component type|component value type|payroll category|pay', 'select',
  ('A salary component (allowance or deduction) of the pay structure.', 'Choose from the list / text.', 'Housing allowance', E, E),
  ('بند من بنود الراتب (بدل أو استقطاع) في هيكل الراتب.', 'اختر من القائمة / نص.', 'بدل سكن', E, E))
R('formula', r'formula|calculation|salary calculation type', 'text', ('How the amount is worked out.', 'An expression with component codes.', 'BASIC * 0.25', 'Use the codes of other components; no circular references.', E),
  ('طريقة احتساب المبلغ.', 'معادلة برموز البنود.', 'BASIC * 0.25', 'استخدم رموز البنود الأخرى؛ دون إحالات دائرية.', E), names=r'formula')
R('amount', r'(total |requested |maximum |remaining |estimated |opening )?amount( requested| paid| aed)?|amount aed|requested amount|maximum amount|remaining amount|estimated cost|cost|purchase cost|price|value', 'number',
  ('An amount of money.', 'Number with up to 2 decimals, in the company currency (AED).', '1250.50', 'Not negative.', E),
  ('مبلغ مالي.', 'رقم حتى منزلتين عشريتين بعملة الشركة (درهم).', '1250.50', 'لا يكون سالبًا.', E),
  cc={'GEN': {'en': {'f': 'Number with up to 2 decimals, in the company currency.'}, 'ar': {'f': 'رقم حتى منزلتين عشريتين بعملة الشركة.'}}})
R('emi', r'emi( amount)?|instalment( amount)?|installment( amount)?|monthly deduction', 'number',
  ('The amount taken from the salary each month.', 'Amount with up to 2 decimals.', '500.00', 'Not more than the loan amount.', 'Total deductions should not exceed 50% of the wage.'),
  ('المبلغ المستقطع من الراتب كل شهر.', 'مبلغ حتى منزلتين عشريتين.', '500.00', 'لا يزيد عن مبلغ القرض.', 'يجب ألا تتجاوز مجموع الاستقطاعات 50% من الأجر.'), cc={'GEN': {'en': {'u': ''}, 'ar': {'u': ''}}})
R('repayment', r'repayment (period|months|date)|no of instalments|repayment', 'number',
  ('How (and over how many months) the amount is paid back.', 'Whole number of months, or a date.', '12', 'At least 1.', E), ('طريقة السداد (وعدد الأشهر).', 'عدد صحيح من الأشهر أو تاريخ.', '12', 'على الأقل 1.', E))
R('currency', r'currency', 'select', ('The currency of the amount.', 'Three-letter code.', 'AED', E, E), ('عملة المبلغ.', 'رمز من 3 حروف.', 'AED', E, E),
  cc={'SA': {'en': {'x': 'SAR'}, 'ar': {'x': 'SAR'}}, 'GEN': {'en': {'x': 'USD'}, 'ar': {'x': 'USD'}}}, names=r'currency')
R('percentage', r'(.* )?percent(age)?|.* %|rate|accrual rate|increment|weight', 'number',
  ('A percentage.', 'Number from 0 to 100.', '25', 'Between 0 and 100.', E), ('نسبة مئوية.', 'رقم من 0 إلى 100.', '25', 'بين 0 و100.', E))
R('gratuity', r'gratuity|end of service|eos', 'number',
  ('End-of-service gratuity settings or amount.', 'Days of basic salary per year of service.', '21', E,
   'UAE: 21 days’ basic salary per year for the first 5 years, 30 days per year after; total capped at 2 years’ wage.'),
  ('إعدادات أو مبلغ مكافأة نهاية الخدمة.', 'أيام من الراتب الأساسي لكل سنة خدمة.', '21', E, 'الإمارات: 21 يومًا من الأساسي عن كل سنة في أول 5 سنوات و30 يومًا بعدها، بحد أقصى أجر سنتين.'),
  cc={'GEN': {'en': {'u': ''}, 'ar': {'u': ''}}})
R('payroll_period', r'(payroll |pay )?(month|period)|search month|payroll month|accrual month', 'select',
  ('The month (pay period).', 'Choose the month.', 'September', E, E), ('الشهر (فترة الراتب).', 'اختر الشهر.', 'سبتمبر', E, E), names=r'month')
R('year', r'(search )?year|financial year|fiscal year|frequency years', 'number', ('The year.', 'Four digits.', '2026', E, E), ('السنة.', 'أربعة أرقام.', '2026', E, E), names=r'year')
R('fy_start', r'financial year start( date)?|fiscal year start', 'date', ('The first day of the company’s financial year.', 'A date.', '01/01/2026', E, E), ('أول يوم في السنة المالية للشركة.', 'تاريخ.', '01/01/2026', E, E))
R('show_payslip', r'show in payslip|deduct leave|apply in next payroll|billable( to the client)?', 'checkbox', ('Tick to switch this on.', 'Tick box.', 'Ticked', E, E), ('اختر لتفعيل هذا الخيار.', 'مربع اختيار.', 'محدد', E, E))
R('trn', r'trn|tax (registration )?(number|no)|vat (number|no)|gst|vat|tax id', 'text',
  ('The company’s tax registration number (TRN).', '15 digits.', '100123456700003', 'Exactly 15 digits.', 'Issued by the Federal Tax Authority (FTA); printed on tax invoices.'),
  ('رقم التسجيل الضريبي للشركة.', '15 رقمًا.', '100123456700003', '15 رقمًا بالضبط.', 'تصدره الهيئة الاتحادية للضرائب ويُطبع على الفواتير الضريبية.'),
  cc={'SA': {'en': {'w': 'The VAT registration number.', 'f': '15 digits, starts and ends with 3.', 'x': '300123456700003', 'u': 'Issued by ZATCA.'}, 'ar': {'w': 'رقم التسجيل في ضريبة القيمة المضافة.', 'f': '15 رقمًا تبدأ وتنتهي بـ 3.', 'x': '300123456700003', 'u': 'تصدره هيئة الزكاة والضريبة والجمارك.'}},
      'GEN': {'en': {'f': 'As issued by the tax authority.', 'x': '', 'r': E, 'u': ''}, 'ar': {'f': 'كما أصدرته الجهة الضريبية.', 'x': '', 'r': '', 'u': ''}}}, names=r'trn|gst|vat|tax_id')

# ------------------------------------------------------------------------------------------------ leave / attendance
R('leave_type', r'(select )?leave type|leave category|type of leave', 'select',
  ('The kind of leave (annual, sick, maternity …).', 'Choose from the list.', 'Annual leave', 'Only leave types your policy allows are listed.',
   'UAE: 30 days’ annual leave after 1 year (2 days per month after 6 months); 90 days’ sick leave per year (15 full, 30 half, 45 unpaid).'),
  ('نوع الإجازة (سنوية، مرضية، أمومة …).', 'اختر من القائمة.', 'إجازة سنوية', 'تظهر أنواع الإجازة المسموح بها في سياستك فقط.', 'الإمارات: 30 يومًا إجازة سنوية بعد سنة (يومان شهريًا بعد 6 أشهر)؛ 90 يومًا إجازة مرضية سنويًا (15 بأجر كامل و30 بنصف أجر و45 بدون أجر).'),
  cc={'GEN': {'en': {'u': ''}, 'ar': {'u': ''}}}, names=r'leave_type')
R('date_from', r'(start|from|begin|valid from|effective from|effective)( date)?|(departure|assigned|request|issue|issued|document|pause start|resume|return|due|purchase|allocation|repayment|travel) date|date from|employee job history from date', 'date',
  ('The date this starts (or happens).', 'A date (day / month / year).', '01/10/2026', 'Must be a valid date; the end date cannot be before it.', E),
  ('تاريخ البداية (أو الحدوث).', 'تاريخ (يوم / شهر / سنة).', '01/10/2026', 'يجب أن يكون تاريخًا صحيحًا؛ ولا يكون تاريخ النهاية قبله.', E), names=r'(start|from)_date|date_from|start|from')
R('date_to', r'(end|to|valid to|valid until|until)( date)?|(expiry|expired|expiration|last) date|expiry|date to|employee job history to date', 'date',
  ('The date this ends (or expires).', 'A date (day / month / year).', '05/10/2026', 'Must not be before the start date.', E),
  ('تاريخ النهاية (أو الانتهاء).', 'تاريخ (يوم / شهر / سنة).', '05/10/2026', 'يجب ألا يكون قبل تاريخ البداية.', E), names=r'(end|to)_date|date_to|end|to|expiry_date')
R('half_day', r'half day( period)?|dis half day|allow half day|session', 'select',
  ('Whether only half a day is taken, and which half.', 'Choose first half / second half.', 'First half', 'Counts as 0.5 day.', E),
  ('هل الإجازة نصف يوم، وأي نصف.', 'اختر النصف الأول / الثاني.', 'النصف الأول', 'تُحتسب نصف يوم.', E))
R('reason', r'(enter )?reason|purpose|why|what happened|remarks?|notes?|comment(s)?|add a comment here|description|message|details|internal note.*|feedback( optional)?|enter your response|response', 'textarea',
  ('A short explanation for the approver and the record.', 'Free text.', 'Family visit to Kerala', 'Keep it short and clear; up to 500 characters is usually enough.', E),
  ('شرح مختصر للمعتمِد وللسجل.', 'نص حر.', 'زيارة عائلية إلى كيرالا', 'اجعله قصيرًا وواضحًا؛ 500 حرف تكفي عادةً.', E), names=r'reason|remarks?|notes?|comment|description|message|purpose')
R('rejection', r'(add a )?rejection reason|reject(ion)? reason|decline reason', 'textarea',
  ('Why the request is rejected – the employee sees this.', 'Free text.', 'Overlaps with the year-end closing', 'Required when rejecting.', E),
  ('سبب رفض الطلب – يراه الموظف.', 'نص حر.', 'يتعارض مع إقفال نهاية السنة', 'إلزامي عند الرفض.', E))
R('days', r'(total |annual leave |encashment |paternity |leave )?days( to deduct)?|number of days|no of days|total days|days before expiry|days after expiry|escalate after days|notice days|leave days to deduct|cf max limit|encashment max limit', 'number',
  ('A number of days.', 'Whole number (or .5 for half days where allowed).', '5', 'Not negative.', E),
  ('عدد من الأيام.', 'عدد صحيح (أو 0.5 لنصف يوم حيث يُسمح).', '5', 'لا يكون سالبًا.', E))
R('hours', r'(threshold |escalate after |time spend )?hours|break duration|escalate after minutes|minutes|time spent|time spend|experience( yrs)?|required service years|capped at years of pay', 'number',
  ('A length of time (hours, minutes or years as the label says).', 'Number.', '8', 'Not negative.', E), ('مدة زمنية (ساعات أو دقائق أو سنوات حسب التسمية).', 'رقم.', '8', 'لا تكون سالبة.', E))
R('time', r'(start|end|from|to|check in|check out|clock in|clock out|correct clock in|correct clock out|schedule at|early check in|late check in|early check out|late check out)( time)?|time|start time|end time', 'time',
  ('A time of day.', 'HH:MM (24-hour clock).', '08:30', 'The end time must be after the start time (unless the shift crosses midnight).', 'UAE: at most 8 hours a day / 48 a week; 2 hours less during Ramadan.'),
  ('وقت من اليوم.', 'HH:MM (نظام 24 ساعة).', '08:30', 'يجب أن يكون وقت النهاية بعد البداية (إلا إذا امتدت الوردية بعد منتصف الليل).', 'الإمارات: 8 ساعات يوميًا / 48 أسبوعيًا كحد أقصى؛ وتقل ساعتين في رمضان.'),
  cc={'GEN': {'en': {'u': ''}, 'ar': {'u': ''}}}, only=('time', 'text', 'select', 'datetime'))
R('shift', r'shift( name| pattern| override| optional any shift)?|select shift|shift employee', 'select',
  ('The work shift.', 'Choose from the list.', 'General shift 08:00–17:00', E, E), ('وردية العمل.', 'اختر من القائمة.', 'الوردية العامة 08:00–17:00', E, E))
R('overtime', r'ot (type|applicable to)|overtime( type| rule| policy)?|rate multiplier|ot rate', 'number',
  ('Overtime setting (type or rate).', 'Number or choice.', '1.25', E, 'UAE: overtime is paid at least 125% (150% between 10 pm and 4 am or on rest days).'),
  ('إعداد العمل الإضافي (النوع أو المعدل).', 'رقم أو اختيار.', '1.25', E, 'الإمارات: يُدفع العمل الإضافي بنسبة 125% على الأقل (150% بين 10 مساءً و4 صباحًا أو أيام الراحة).'),
  cc={'GEN': {'en': {'u': ''}, 'ar': {'u': ''}}})
R('geo', r'latitude|longitude|radius|geo ?fence|enable geo fence', 'number',
  ('The map position (or radius) used to check where people punch in.', 'Decimal degrees (latitude / longitude) or metres (radius).', '25.3463', 'Latitude −90 to 90, longitude −180 to 180.', E),
  ('الموقع على الخريطة (أو نصف القطر) للتحقق من مكان تسجيل الحضور.', 'درجات عشرية (خط العرض / الطول) أو أمتار (نصف القطر).', '25.3463', 'خط العرض بين −90 و90 وخط الطول بين −180 و180.', E))
R('calendar', r'(holiday|weekend|week) calendar|calendar (code|title)|holiday calendar|weekend calendar|general week off|alternative week off', 'select',
  ('The holiday or weekend calendar that applies.', 'Choose from the list.', 'UAE public holidays 2026', E, 'UAE weekend: Saturday and Sunday for most private companies.'),
  ('تقويم العطل أو عطلة نهاية الأسبوع المطبق.', 'اختر من القائمة.', 'العطل الرسمية في الإمارات 2026', E, 'عطلة نهاية الأسبوع في الإمارات: السبت والأحد لمعظم الشركات الخاصة.'),
  cc={'GEN': {'en': {'u': ''}, 'ar': {'u': ''}}})
R('weekday', r'monday|tuesday|wednesday|thursday|friday|saturday|sunday', 'checkbox', ('Tick the days this applies to.', 'Tick boxes.', 'Saturday, Sunday', E, E), ('حدد الأيام التي ينطبق عليها ذلك.', 'مربعات اختيار.', 'السبت، الأحد', E, E), only=('checkbox', 'select'))
R('accrual', r'accrual( frequency| day| month)?|prorate accrual|carry forward( choice)?|allow cf|cf( value| unit or percentage| expires in value| time choice)?|allow encashment|encashment( value)?|unit or percentage|negative|include (holiday|weekend|dashboard)|reset( monthly)?|leave unit|paid leave counts', 'select',
  ('A leave policy setting: how leave is earned, carried forward or paid out.', 'Number, choice or tick box.', '2.5 days per month', E, 'UAE: unused annual leave may be carried forward or paid out as agreed; leave pay uses the basic salary.'),
  ('إعداد في سياسة الإجازات: طريقة الاستحقاق أو الترحيل أو الصرف.', 'رقم أو اختيار أو مربع اختيار.', '2.5 يوم شهريًا', E, 'الإمارات: يجوز ترحيل الإجازة السنوية غير المستخدمة أو صرفها حسب الاتفاق؛ ويُحسب أجر الإجازة على الراتب الأساسي.'),
  cc={'GEN': {'en': {'u': ''}, 'ar': {'u': ''}}})
R('balance', r'(leave |remaining |opening )?balance|remaining balance|opening balance', 'number', ('The number of days (or amount) still available.', 'Number.', '12.5', E, E),
  ('عدد الأيام (أو المبلغ) المتبقي.', 'رقم.', '12.5', E, E))
R('attendance_source', r'attendance (source|card barcode|policy|cycle type)|cycle (start|end) day|attendance', 'select',
  ('How attendance is recorded or counted.', 'Choose from the list.', 'Biometric device', E, E), ('طريقة تسجيل أو احتساب الحضور.', 'اختر من القائمة.', 'جهاز البصمة', E, E))
R('late_policy', r'late (occurrence limit|coming policy)|early exit policy|penalty type|grace( minutes| period)?', 'number',
  ('A late-coming / early-going rule.', 'Number or choice.', '3', E, E), ('قاعدة التأخر / الخروج المبكر.', 'رقم أو اختيار.', '3', E, E))

# ------------------------------------------------------------------------------------------------ assets
R('asset_name', r'asset( name)?|select (requested )?asset|asset type( name)?|select asset type|type name', 'select',
  ('The asset (or asset type).', 'Choose from the list or type the name.', 'Dell Latitude 5440 laptop', E, E), ('الأصل (أو نوع الأصل).', 'اختر من القائمة أو اكتب الاسم.', 'حاسوب محمول ديل لاتيتيود 5440', E, E))
R('serial', r'serial( number| no)?|imei|barcode|asset code|asset tag|police report no|reference( no)?', 'text',
  ('The unique number printed on the item (or document).', 'Letters and digits.', 'SN-5CG1234XYZ', 'Unique – the same number cannot be used twice.', E),
  ('الرقم الفريد المطبوع على الأصل (أو المستند).', 'حروف وأرقام.', 'SN-5CG1234XYZ', 'فريد – لا يتكرر الرقم.', E), names=r'serial_number|serial_no|barcode|asset_code')
R('model', r'model|make|brand', 'text', ('The make / model of the item.', 'Text.', 'Latitude 5440', E, E), ('الطراز / الموديل.', 'نص.', 'Latitude 5440', E, E), names=r'model')
R('vendor', r'vendor|supplier', 'text', ('Who the item was bought from.', 'Text.', 'Jumbo Electronics LLC', E, E), ('الجهة التي اشتُري منها الأصل.', 'نص.', 'جمبو للإلكترونيات ذ.م.م', E, E))
R('invoice', r'invoice (no|number)|purchase order|po (no|number)|invoice', 'text', ('The supplier invoice or purchase order number.', 'As printed on the invoice.', 'INV-2026-0042', E, 'A UAE tax invoice shows the supplier’s TRN.'),
  ('رقم فاتورة المورد أو أمر الشراء.', 'كما هو مطبوع على الفاتورة.', 'INV-2026-0042', E, 'الفاتورة الضريبية في الإمارات تظهر الرقم الضريبي للمورد.'), cc={'GEN': {'en': {'u': ''}, 'ar': {'u': ''}}})
R('warranty', r'warranty( start| end| until| expiry| reminder days)?', 'date', ('The warranty period of the item.', 'A date (day / month / year).', '31/12/2027', 'The end must be after the start.', E),
  ('فترة ضمان الأصل.', 'تاريخ (يوم / شهر / سنة).', '31/12/2027', 'يجب أن تكون النهاية بعد البداية.', E))
R('condition', r'(returned )?condition|lost or stolen.*', 'select', ('The condition of the item.', 'Choose from the list.', 'Healthy', E, E), ('حالة الأصل.', 'اختر من القائمة.', 'سليم', E, E))
R('depreciation', r'depreciation( method| start| from)?|useful life( months)?|salvage( value)?|declining rate|meter reading.*', 'number',
  ('How the item’s value goes down over time.', 'Choice, months or amount.', '36', E, E), ('طريقة انخفاض قيمة الأصل مع الزمن.', 'اختيار أو أشهر أو مبلغ.', '36', E, E))

# ------------------------------------------------------------------------------------------------ workflow / requests
R('approver', r'approver|approved by|delegat(e|or)( to)?|select user|notify users|specific users|users?|assigned to|select task members|search member', 'select',
  ('The person who approves or receives this.', 'Choose a user.', 'hr.sharjah', E, E), ('الشخص الذي يعتمد أو يستلم ذلك.', 'اختر مستخدمًا.', 'hr.sharjah', E, E))
R('level', r'(select )?level|sequence|order|priority|stage|select stages|is end stage', 'number',
  ('The step (order) in the approval chain.', 'Whole number: 1 is first.', '1', 'Each level number is used once.', E), ('الخطوة (الترتيب) في سلسلة الاعتماد.', 'عدد صحيح: 1 هو الأول.', '1', 'يُستخدم كل رقم مستوى مرة واحدة.', E), names=r'level|sequence|order')
R('role', r'role|approval type|template type|request type|select request type|type|kind|mode|source|frequency|transaction type|base type|pattern type|rule type|policy( name)?|select policy|default policy.*', 'select',
  ('The kind of record or rule.', 'Choose from the list.', 'Leave request', E, E), ('نوع السجل أو القاعدة.', 'اختر من القائمة.', 'طلب إجازة', E, E))
R('escalation', r'escalate after( days| hours| minutes)?|escalation', 'number',
  ('How long a request may wait before it moves to the next approver.', 'Whole number.', '2', 'At least 1.', E), ('المدة التي ينتظرها الطلب قبل تحويله للمعتمِد التالي.', 'عدد صحيح.', '2', 'على الأقل 1.', E))
R('subject', r'subject|title|calendar title|email subject', 'text', ('A short title.', 'Text.', 'Leave approved', 'Up to 200 characters.', E), ('عنوان مختصر.', 'نص.', 'تمت الموافقة على الإجازة', 'حتى 200 حرف.', E), names=r'subject|title')
R('status', r'status|search status|employment status|visa status|search by .*status.*', 'select', ('The state of the record.', 'Choose from the list.', 'Approved', E, E), ('حالة السجل.', 'اختر من القائمة.', 'معتمد', E, E), names=r'status')

# ------------------------------------------------------------------------------------------------ documents
R('doc_type', r'(select )?document type|document request type|request document|employee document type', 'select',
  ('The kind of document.', 'Choose from the list.', 'Passport', E, E), ('نوع المستند.', 'اختر من القائمة.', 'جواز السفر', E, E), names=r'document_type')
R('doc_number', r'document (number|no)|doc (number|no)', 'text', ('The number printed on the document.', 'Letters and digits.', 'N1234567', 'Up to 50 characters.', E),
  ('الرقم المطبوع على المستند.', 'حروف وأرقام.', 'N1234567', 'حتى 50 حرفًا.', E), names=r'doc_number|document_number')
R('file', r'(upload |supporting |select your |attested |policy |employee |request )?(document|file|attachment|copy|cv|resume)( optional)?|filename|police report|photos?|attachment optional|upload document|request attachment', 'file',
  ('A file to attach.', 'PDF, JPG, PNG, Word or Excel.', 'passport.pdf', 'Up to 25 MB per file.', E), ('ملف للإرفاق.', 'PDF أو JPG أو PNG أو Word أو Excel.', 'passport.pdf', 'حتى 25 ميغابايت للملف.', E),
  names=r'.*(file|document|attachment|cv).*')

# ------------------------------------------------------------------------------------------------ company / settings
R('company_name', r'company( name)?|enter company|select company|search company|industry type', 'text', ('The company’s legal name (as on the trade licence).', 'Text.', 'Adept Business Solutions LLC', E, 'Use the name on the trade licence – it appears on letters and the WPS file.'),
  ('الاسم القانوني للشركة (كما في الرخصة التجارية).', 'نص.', 'شركة أديبت لحلول الأعمال ذ.م.م', E, 'استخدم الاسم الوارد في الرخصة التجارية – يظهر في الخطابات وملف حماية الأجور.'), cc={'GEN': {'en': {'u': ''}, 'ar': {'u': ''}}})
R('code', r'(branch |department |designation |category |document |calendar |component |course )?code|preffix|prefix|suffix|current number|next number|total length|digits|automatic numbering', 'text',
  ('A short code (or numbering part) used to identify records.', 'Letters and digits, no spaces.', 'SHJ', 'Unique; keep it short.', E), ('رمز قصير (أو جزء من الترقيم) لتعريف السجلات.', 'حروف وأرقام بلا مسافات.', 'SHJ', 'فريد؛ اجعله قصيرًا.', E),
  names=r'.*_code|code|prefix|suffix')
R('email_server', r'email (host|port|host user|use tls)|smtp.*|email host|email port', 'text', ('The outgoing mail (SMTP) server setting.', 'Host name, port number or tick box.', 'smtp.office365.com / 587', 'Port 587 with TLS is the usual choice.', E),
  ('إعداد خادم البريد الصادر (SMTP).', 'اسم الخادم أو رقم المنفذ أو مربع اختيار.', 'smtp.office365.com / 587', 'المنفذ 587 مع TLS هو الخيار المعتاد.', E))
R('fax', r'fax', 'tel', ('The fax number (if the company still has one).', '+971 6 XXX XXXX', '+971 6 555 1234', 'Optional.', E), ('رقم الفاكس (إن وجد).', '‎+971 6 XXX XXXX', '+971 6 555 1234', 'اختياري.', E), cc={'GEN': {'en': {'f': '+<country code> <number>', 'x': ''}, 'ar': {'f': '‎+رمز الدولة ثم الرقم', 'x': ''}}})
R('timezone', r'time ?zone', 'select', ('The time zone used for punches and dates.', 'Choose from the list.', 'Asia/Dubai (UTC+4)', E, E), ('المنطقة الزمنية لتسجيل الحضور والتواريخ.', 'اختر من القائمة.', 'آسيا/دبي (UTC+4)', E, E))
R('language', r'language( used on every screen| of the screens)?|select employee langauge|lang|default language', 'select', ('The language of the screens, messages and documents.', 'Choose from the list.', 'العربية', E, E), ('لغة الشاشات والرسائل والمستندات.', 'اختر من القائمة.', 'العربية', E, E))
R('emiratisation', r'emiratisation|emiratization|nationals', 'checkbox', ('Counts the employee towards the Emiratisation target.', 'Tick box.', 'Ticked', E, 'Companies with 50+ employees must raise Emirati staff by 2% a year.'),
  ('يحتسب الموظف ضمن نسبة التوطين.', 'مربع اختيار.', 'محدد', E, 'الشركات التي يعمل بها 50 موظفًا أو أكثر ملزمة بزيادة نسبة المواطنين 2% سنويًا.'), cc={'GEN': {'en': {'w': 'Counts the employee as a national.', 'u': ''}, 'ar': {'w': 'يحتسب الموظف ضمن المواطنين.', 'u': ''}}}, ae=True)

# ------------------------------------------------------------------------------------------------ form designer
R('designer_field_name', r'field name|field label|label|select field names', 'text', ('The caption of the custom field as users will see it.', 'Text.', 'Cost centre', 'Unique on the form; must not repeat a built-in field such as “Visa type”.', E),
  ('اسم الحقل المخصص كما سيظهر للمستخدمين.', 'نص.', 'مركز التكلفة', 'فريد في النموذج؛ ولا يكرر حقلًا أساسيًا مثل «نوع التأشيرة».', E))
R('designer_type', r'data type|field type', 'select', ('What kind of value the field holds.', 'Choose a type.', 'Dropdown', E, E), ('نوع القيمة التي يحفظها الحقل.', 'اختر نوعًا.', 'قائمة منسدلة', E, E))
R('designer_options', r'(dropdown|radio|checkbox) values( comma separated)?|options( comma separated)?|enter', 'text', ('The choices users can pick from.', 'Comma separated (or one per line).', 'CC-100, CC-200, CC-300', 'At least one option; no duplicates.', E),
  ('الخيارات المتاحة للمستخدمين.', 'مفصولة بفواصل (أو خيار في كل سطر).', 'CC-100, CC-200, CC-300', 'خيار واحد على الأقل دون تكرار.', E))
R('designer_mandatory', r'mandatory( field)?|required|is required', 'checkbox', ('Tick to make the field required on the form.', 'Tick box.', 'Ticked', 'A required field must be filled in before saving.', E),
  ('اختر لجعل الحقل إلزاميًا في النموذج.', 'مربع اختيار.', 'محدد', 'لا يمكن الحفظ قبل تعبئة الحقل الإلزامي.', E))
R('designer_default', r'default value|default', 'text', ('The value filled in on new records.', 'A value of the field’s type.', 'CC-100', E, E), ('القيمة التي تُملأ تلقائيًا في السجلات الجديدة.', 'قيمة من نوع الحقل.', 'CC-100', E, E))
R('min_max', r'minimum value|maximum value|lowest value|highest value|minimum|maximum', 'number', ('The lowest / highest value allowed.', 'Number.', '0', 'The lowest must not be above the highest.', E),
  ('أدنى / أعلى قيمة مسموحة.', 'رقم.', '0', 'يجب ألا تزيد القيمة الدنيا عن العليا.', E))

# ------------------------------------------------------------------------------------------------ patterns (v1.16.0 wider coverage)
R('money_aed', r'.*\baed\b.*|.* cost( .*)?|.*(salary|budget|wage|allowance|bonus|housing|fee|fees|price|cost)( from| to| aed)?|contract value|.* amount', 'number',
  ('An amount of money in the company currency.', 'Number with up to 2 decimals.', '1500.00', 'Not negative.', E),
  ('مبلغ مالي بعملة الشركة.', 'رقم حتى منزلتين عشريتين.', '1500.00', 'لا يكون سالبًا.', E), only=('number', 'text'))
R('duration', r'.*\b(minutes|min|hours|hrs|h|days|day|months|years|yrs|weeks)\b.*', 'number',
  ('A length of time, in the unit the label shows.', 'Whole number (decimals where it makes sense).', '15', 'Not negative; 0 usually means “no limit” or “none”.', E),
  ('مدة زمنية بالوحدة الظاهرة في التسمية.', 'عدد صحيح (أو عشري حيث يناسب).', '15', 'لا تكون سالبة؛ 0 يعني غالبًا «بلا حد» أو «لا شيء».', E), only=('number', 'text', 'select'))
R('rate_multiplier', r'rate( .*)?|.* rate( .*)?|multiplier', 'number',
  ('A rate (multiplier or amount per unit, as the label says).', 'Decimal number.', '1.25', 'Not negative.', 'UAE overtime: at least 1.25 (1.5 at night and on rest days).'),
  ('معدل (مُضاعِف أو مبلغ لكل وحدة حسب التسمية).', 'رقم عشري.', '1.25', 'لا يكون سالبًا.', 'العمل الإضافي في الإمارات: 1.25 على الأقل (1.5 ليلًا وفي أيام الراحة).'),
  cc={'GEN': {'en': {'u': ''}, 'ar': {'u': ''}}}, only=('number', 'text'))
R('due_date', r'.* (due|on|before|deadline|starts|expiry|return|done|from|to)', 'date',
  ('The date this happens (or is due).', 'A date (day / month / year).', '15/10/2026', E, E), ('تاريخ الحدوث (أو الاستحقاق).', 'تاريخ (يوم / شهر / سنة).', '15/10/2026', E, E), only=('date', 'datetime', 'month'))
R('long_text', r'.*(comments?|notes?|objectives|requirements|justification|description|feedback|benefits|terms|outline|achievements|needs|evidence|explanation|summary|scope|instructions?)( .*)?', 'textarea',
  ('Free text for the record – write what the reader needs to know.', 'Text, several lines allowed.', 'Agreed with the line manager on 10 Oct.', 'Keep it short and clear.', E),
  ('نص حر للسجل – اكتب ما يحتاج القارئ معرفته.', 'نص، يمكن كتابة عدة أسطر.', 'تم الاتفاق مع المدير المباشر في 10 أكتوبر.', 'اجعله قصيرًا وواضحًا.', E), only=('textarea', 'text'))
R('change_to', r'new (.* )?(no change)|new .*|.* no change', 'select',
  ('The new value after this action; leave it on “no change” to keep the current one.', 'Choose from the list.', 'Dubai', E, E),
  ('القيمة الجديدة بعد هذا الإجراء؛ اتركها على «بدون تغيير» للإبقاء على الحالية.', 'اختر من القائمة.', 'دبي', E, E), only=('select',))
R('empty_all', r'.*(empty all|empty any|empty course cost|empty none|optional none|leave empty.*)', 'multiselect',
  ('Limit this to the items you pick; leave it empty to apply to all.', 'Pick one or more – or none.', E, 'Empty = all.', E),
  ('حدد العناصر التي ينطبق عليها ذلك؛ اتركه فارغًا ليشمل الكل.', 'اختر واحدًا أو أكثر – أو لا شيء.', E, 'فارغ = الكل.', E), only=('multiselect', 'select'))
R('switch', r'(enable|allow|accept|must|mark|deduct|block|include|require|requires|required|send|use|show|count|apply|auto|is) .*|.* (required|applicable|allowed|counted|enabled|returned|attended|done)', 'checkbox',
  ('Tick to switch this rule on; untick to switch it off.', 'Tick box.', 'Ticked', 'The change applies to new records and calculations from now on.', E),
  ('اختر لتفعيل هذه القاعدة؛ ألغِ التحديد لإيقافها.', 'مربع اختيار.', 'محدد', 'يسري التغيير على السجلات والحسابات الجديدة من الآن.', E), only=('checkbox',))
R('person_pick', r'.*\b(by|owner|trainer|colleague|custodian|head|panel|nominated|referred|replacing|responsible|participants?|members?)\b.*', 'select',
  ('The person (employee or user) for this role.', 'Choose from the list (type to search).', 'Khalid Al Mazrouei', E, E),
  ('الشخص (موظف أو مستخدم) لهذا الدور.', 'اختر من القائمة (اكتب للبحث).', 'خالد المزروعي', E, E), only=('select', 'multiselect'))

# ------------------------------------------------------------------------------------------------ lists / search / selection
R('search', r'search( .*)?|serach|find employee|find .*|search by .*', 'search', ('Type part of a name, code or number to filter the list.', 'Any text; not case sensitive.', 'ahmed', 'The list updates as you type.', E),
  ('اكتب جزءًا من الاسم أو الرمز أو الرقم لتصفية القائمة.', 'أي نص؛ لا يفرّق بين الحروف الكبيرة والصغيرة.', 'أحمد', 'تتحدث القائمة أثناء الكتابة.', E), names=r'search.*|q|query')
R('select_all', r'select all|all employees|all|yes', 'checkbox', ('Tick to select every row shown (untick to clear).', 'Tick box.', 'Ticked', 'Only the rows that match the current filter are selected.', E),
  ('اختر لتحديد كل الصفوف الظاهرة (ألغِ التحديد للمسح).', 'مربع اختيار.', 'محدد', 'تُحدد الصفوف المطابقة للتصفية الحالية فقط.', E), names=r'.*selected|select_all.*|all_.*selected', only=('checkbox',))
R('select_employee', r'(select |choose |pick )?(an )?employees?( choose| for the sample| pick an employee| hr empty me| id| name)?|employee pick an employee|only people with leave', 'select',
  ('The employee this record is for.', 'Choose an employee (type to search).', 'Ahmed Al Mansoori (EMP-0042)', 'You only see employees you have access to.', E),
  ('الموظف الذي يخصه السجل.', 'اختر موظفًا (اكتب للبحث).', 'أحمد المنصوري (EMP-0042)', 'يظهر لك الموظفون المسموح لك بهم فقط.', E), names=r'emp(loyee)?(_id)?|employee')
R('access_right', r'.*( modules| master| template| report| request| approvals?| approval level| escalation| list| policy| calender| calendar| type| allocation| rule| repayment| marking| register| fence| entry| details| numbering| settings| configuration| permission for user| grouping| form)', 'checkbox',
  ('Tick to give this group access to the screen or function named here.', 'Tick box.', 'Ticked', 'Rights take effect at the users’ next login or page refresh.', E),
  ('اختر لمنح هذه المجموعة صلاحية الوصول إلى الشاشة أو الوظيفة المذكورة.', 'مربع اختيار.', 'محدد', 'تسري الصلاحيات عند الدخول التالي للمستخدمين أو تحديث الصفحة.', E), only=('checkbox',))
R('name', r'(enter )?name|full name|(group|location|policy|department|component|asset type|company|calendar|course|skill|project|task) name|skills?|course|group|project( pick a project)?|task( no task)?', 'text',
  ('The name of the record as users will see it in lists.', 'Text.', 'Sharjah head office', 'Required. Unique where the screen says so.', E),
  ('اسم السجل كما سيظهر في القوائم.', 'نص.', 'المكتب الرئيسي بالشارقة', 'إلزامي. فريد حيث تشير الشاشة لذلك.', E), names=r'name|.*_name')
R('date_generic', r'(.* )?date|day|when|due date|document date|schedule at|expires at', 'date', ('A date.', 'A date (day / month / year).', '10/10/2026', E, E), ('تاريخ.', 'تاريخ (يوم / شهر / سنة).', '10/10/2026', E, E))


def _ok(r, ftype):
    return not r['only'] or not ftype or ftype in r['only']


def match(label_key, name_key='', ftype=''):
    """The knowledge base rule for a field (normalised label, normalised control name, field type) or None."""
    lk = (label_key or '').strip()
    if lk:
        for r in KB:
            if r['re'].match(lk) and _ok(r, ftype):
                return r
    nk = re.sub(r'^(emp_|temp_emp_|temp_|ef_|emp)', '', (name_key or '').strip())
    if nk:
        for r in KB:
            if r['names'] is not None and (r['names'].match(nk) or r['names'].match(name_key)) and _ok(r, ftype):
                return r
        words = nk.replace('_', ' ')
        for r in KB:
            if r['re'].match(words) and _ok(r, ftype):
                return r
    return None
