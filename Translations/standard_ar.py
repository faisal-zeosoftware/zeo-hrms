"""v1.15.0 – Arabic names of the standard UAE lists (Modern Standard Arabic, UAE HR usage). Loaded by
`python manage.py load_standard_translations` into FieldTranslation for the records whose English name matches
(case-insensitive). The customer reviews / changes them with the Translate button on each record."""

DEPARTMENTS = {
    'Human Resources': 'الموارد البشرية', 'HR': 'الموارد البشرية', 'Human Resource': 'الموارد البشرية',
    'Finance': 'المالية', 'Accounts': 'الحسابات', 'Accounting': 'المحاسبة', 'Finance and Accounts': 'المالية والحسابات',
    'Administration': 'الإدارة', 'Admin': 'الإدارة', 'Management': 'الإدارة العليا', 'General Management': 'الإدارة العامة',
    'Sales': 'المبيعات', 'Marketing': 'التسويق', 'Sales and Marketing': 'المبيعات والتسويق', 'Business Development': 'تطوير الأعمال',
    'Operations': 'العمليات', 'Information Technology': 'تقنية المعلومات', 'IT': 'تقنية المعلومات',
    'Procurement': 'المشتريات', 'Purchase': 'المشتريات', 'Purchasing': 'المشتريات', 'Logistics': 'الخدمات اللوجستية',
    'Supply Chain': 'سلسلة التوريد', 'Warehouse': 'المستودع', 'Stores': 'المخازن', 'Store': 'المخزن',
    'Legal': 'الشؤون القانونية', 'Compliance': 'الامتثال', 'Audit': 'التدقيق', 'Internal Audit': 'التدقيق الداخلي',
    'Customer Service': 'خدمة العملاء', 'Customer Support': 'دعم العملاء', 'Production': 'الإنتاج', 'Manufacturing': 'التصنيع',
    'Quality': 'الجودة', 'Quality Control': 'مراقبة الجودة', 'Quality Assurance': 'ضمان الجودة', 'Engineering': 'الهندسة',
    'Maintenance': 'الصيانة', 'Projects': 'المشاريع', 'Project Management': 'إدارة المشاريع', 'Research and Development': 'البحث والتطوير',
    'R&D': 'البحث والتطوير', 'Health and Safety': 'الصحة والسلامة', 'HSE': 'الصحة والسلامة والبيئة', 'Security': 'الأمن',
    'Government Relations': 'العلاقات الحكومية', 'PRO': 'العلاقات الحكومية', 'Public Relations': 'العلاقات العامة',
    'Transport': 'النقل', 'Transportation': 'النقل', 'Facilities': 'المرافق', 'Facility Management': 'إدارة المرافق',
    'Housekeeping': 'التدبير المنزلي', 'Training': 'التدريب', 'Learning and Development': 'التعلم والتطوير', 'Payroll': 'الرواتب',
    'Treasury': 'الخزينة', 'Credit Control': 'مراقبة الائتمان', 'Estimation': 'التقديرات', 'Design': 'التصميم', 'Planning': 'التخطيط',
    'Contracts': 'العقود', 'Recruitment': 'التوظيف', 'Executive Office': 'المكتب التنفيذي', 'Retail': 'التجزئة', 'Kitchen': 'المطبخ',
    'Food and Beverage': 'الأغذية والمشروبات', 'Front Office': 'المكتب الأمامي', 'Medical': 'الطبي', 'Nursing': 'التمريض',
}

DESIGNATIONS = {
    'Manager': 'مدير', 'General Manager': 'مدير عام', 'Assistant Manager': 'مساعد مدير', 'Senior Manager': 'مدير أول',
    'Chief Executive Officer': 'الرئيس التنفيذي', 'CEO': 'الرئيس التنفيذي', 'Managing Director': 'العضو المنتدب', 'Director': 'مدير إدارة',
    'Chief Financial Officer': 'المدير المالي', 'CFO': 'المدير المالي', 'Operations Manager': 'مدير العمليات', 'HR Manager': 'مدير الموارد البشرية',
    'HR Executive': 'أخصائي موارد بشرية', 'HR Officer': 'موظف موارد بشرية', 'HR Assistant': 'مساعد موارد بشرية', 'Recruiter': 'أخصائي توظيف',
    'Finance Manager': 'مدير مالي', 'Accountant': 'محاسب', 'Senior Accountant': 'محاسب أول', 'Chief Accountant': 'رئيس الحسابات',
    'Accounts Assistant': 'مساعد حسابات', 'Cashier': 'أمين صندوق', 'Auditor': 'مدقق حسابات', 'Payroll Officer': 'موظف رواتب',
    'Sales Manager': 'مدير المبيعات', 'Sales Executive': 'مندوب مبيعات', 'Salesman': 'بائع', 'Sales Representative': 'مندوب مبيعات',
    'Marketing Manager': 'مدير التسويق', 'Marketing Executive': 'أخصائي تسويق', 'Business Development Manager': 'مدير تطوير الأعمال',
    'Team Lead': 'قائد فريق', 'Team Leader': 'قائد فريق', 'Supervisor': 'مشرف', 'Executive': 'تنفيذي', 'Senior Executive': 'تنفيذي أول',
    'Assistant': 'مساعد', 'Administrative Assistant': 'مساعد إداري', 'Admin Assistant': 'مساعد إداري', 'Secretary': 'سكرتير',
    'Receptionist': 'موظف استقبال', 'Office Boy': 'مراسل', 'Clerk': 'كاتب', 'Data Entry Operator': 'مدخل بيانات', 'Coordinator': 'منسق',
    'Engineer': 'مهندس', 'Senior Engineer': 'مهندس أول', 'Site Engineer': 'مهندس موقع', 'Civil Engineer': 'مهندس مدني',
    'Mechanical Engineer': 'مهندس ميكانيكي', 'Electrical Engineer': 'مهندس كهربائي', 'Software Engineer': 'مهندس برمجيات',
    'Software Developer': 'مطور برمجيات', 'Network Engineer': 'مهندس شبكات', 'IT Support': 'دعم تقني', 'IT Manager': 'مدير تقنية المعلومات',
    'System Administrator': 'مسؤول أنظمة', 'Project Manager': 'مدير مشروع', 'Project Engineer': 'مهندس مشروع', 'Quantity Surveyor': 'مساح كميات',
    'Draftsman': 'رسام هندسي', 'Foreman': 'رئيس عمال', 'Technician': 'فني', 'Electrician': 'كهربائي', 'Plumber': 'سباك', 'Mechanic': 'ميكانيكي',
    'Carpenter': 'نجار', 'Mason': 'بناء', 'Painter': 'دهان', 'Welder': 'لحام', 'Labourer': 'عامل', 'Labour': 'عامل', 'Helper': 'مساعد عامل',
    'Driver': 'سائق', 'Heavy Driver': 'سائق معدات ثقيلة', 'Storekeeper': 'أمين مخزن', 'Store Keeper': 'أمين مخزن', 'Warehouse Supervisor': 'مشرف مستودع',
    'Purchase Officer': 'موظف مشتريات', 'Procurement Officer': 'موظف مشتريات', 'Logistics Coordinator': 'منسق لوجستي',
    'Security Guard': 'حارس أمن', 'Cleaner': 'عامل نظافة', 'Cook': 'طباخ', 'Chef': 'رئيس طهاة', 'Waiter': 'نادل', 'Nurse': 'ممرض',
    'Doctor': 'طبيب', 'Pharmacist': 'صيدلي', 'Teacher': 'معلم', 'PRO': 'مندوب علاقات حكومية', 'Public Relations Officer': 'مسؤول علاقات حكومية',
    'Graphic Designer': 'مصمم جرافيك', 'Merchandiser': 'منسق بضائع', 'Customer Service Executive': 'موظف خدمة عملاء', 'Legal Advisor': 'مستشار قانوني',
    'Consultant': 'مستشار', 'Analyst': 'محلل', 'Business Analyst': 'محلل أعمال', 'Intern': 'متدرب', 'Trainee': 'متدرب',
}

CATEGORIES = {
    'Technical': 'فني', 'Non-Technical': 'غير فني', 'Management': 'الإدارة', 'Office Staff': 'موظفو المكتب', 'Field / Site Staff': 'موظفو الموقع',
    'Site Staff': 'موظفو الموقع', 'Field Staff': 'موظفو الميدان', 'Skilled Worker': 'عامل ماهر', 'Unskilled Worker': 'عامل غير ماهر',
    'Semi-skilled Worker': 'عامل شبه ماهر', 'Staff': 'موظفون', 'Labour': 'عمال', 'Workers': 'عمال', 'Executive': 'تنفيذي', 'Senior': 'أول',
    'Junior': 'مبتدئ', 'Permanent': 'دائم', 'Contract': 'عقد', 'Temporary': 'مؤقت', 'Part time': 'دوام جزئي', 'Full time': 'دوام كامل',
    'Intern': 'متدرب', 'Trainee': 'متدرب', 'Consultant': 'مستشار', 'Probation': 'تحت الاختبار',
}

LEAVE_TYPES = {
    'Annual Leave': 'إجازة سنوية', 'Sick Leave': 'إجازة مرضية', 'Maternity Leave': 'إجازة أمومة', 'Maternity Extension (unpaid)': 'تمديد إجازة الأمومة (بدون أجر)',
    'Paternity Leave': 'إجازة أبوة', 'Parental Leave': 'إجازة والدية', 'Bereavement – Spouse': 'إجازة حداد – الزوج/الزوجة',
    'Bereavement – Family': 'إجازة حداد – الأقارب', 'Bereavement Leave': 'إجازة حداد', 'Compassionate Leave': 'إجازة عائلية طارئة',
    'Study Leave': 'إجازة دراسية', 'Hajj Leave': 'إجازة الحج', 'Umrah Leave': 'إجازة العمرة', 'Unpaid Leave': 'إجازة بدون أجر',
    'Casual Leave': 'إجازة عارضة', 'Compensatory Leave': 'إجازة تعويضية', 'Comp Off': 'إجازة تعويضية', 'Emergency Leave': 'إجازة طارئة',
    'Marriage Leave': 'إجازة زواج', 'Iddah Leave': 'إجازة العدة', 'Business Trip': 'رحلة عمل', 'Half Day Leave': 'إجازة نصف يوم',
    'Public Holiday': 'عطلة رسمية', 'Leave Without Pay': 'إجازة بدون أجر', 'Exam Leave': 'إجازة امتحانات',
}

DOCUMENT_TYPES = {
    'Passport': 'جواز السفر', 'Emirates ID': 'الهوية الإماراتية', 'Visa': 'التأشيرة', 'Residence Visa': 'تأشيرة الإقامة', 'Residence Permit': 'تصريح الإقامة',
    'Labour Card': 'بطاقة العمل', 'Work Permit': 'تصريح العمل', 'Labour Contract': 'عقد العمل', 'Employment Contract': 'عقد العمل',
    'MOHRE Contract': 'عقد وزارة الموارد البشرية والتوطين', 'Offer Letter': 'خطاب العرض', 'Medical Insurance Card': 'بطاقة التأمين الصحي',
    'Health Insurance': 'التأمين الصحي', 'Driving Licence': 'رخصة القيادة', 'Driving License': 'رخصة القيادة', 'Educational Certificate': 'الشهادة الدراسية',
    'Degree Certificate': 'شهادة الدرجة العلمية', 'Experience Certificate': 'شهادة الخبرة', 'Photo': 'صورة شخصية', 'Photograph': 'صورة شخصية',
    'Bank Letter': 'خطاب البنك', 'Police Clearance': 'شهادة حسن السيرة والسلوك', 'Good Conduct Certificate': 'شهادة حسن السيرة والسلوك',
    'Medical Fitness Certificate': 'شهادة اللياقة الطبية', 'Medical Certificate': 'شهادة طبية', 'Health Card': 'البطاقة الصحية',
    'Occupational Health Card': 'بطاقة الصحة المهنية', 'Trade Licence': 'الرخصة التجارية', 'Vehicle Registration': 'ملكية المركبة',
    'Tenancy Contract': 'عقد الإيجار', 'Ejari': 'إيجاري', 'Resume': 'السيرة الذاتية', 'CV': 'السيرة الذاتية', 'Birth Certificate': 'شهادة الميلاد',
    'Marriage Certificate': 'عقد الزواج', 'NOC': 'شهادة عدم ممانعة', 'Salary Certificate': 'شهادة راتب', 'Passport Release': 'تسليم جواز السفر',
    'Emirates ID Copy': 'نسخة الهوية الإماراتية', 'Visa Copy': 'نسخة التأشيرة', 'Experience Letter': 'خطاب خبرة',
}

SALARY_COMPONENTS = {
    'Basic': 'الراتب الأساسي', 'Basic Salary': 'الراتب الأساسي', 'HRA': 'بدل السكن', 'Housing Allowance': 'بدل السكن', 'House Rent Allowance': 'بدل السكن',
    'Transport Allowance': 'بدل المواصلات', 'Transportation Allowance': 'بدل المواصلات', 'Food Allowance': 'بدل الطعام', 'Phone Allowance': 'بدل الهاتف',
    'Mobile Allowance': 'بدل الهاتف المتحرك', 'Other Allowance': 'بدلات أخرى', 'Other Allowances': 'بدلات أخرى', 'Overtime': 'العمل الإضافي',
    'OT': 'العمل الإضافي', 'Bonus': 'مكافأة', 'Commission': 'عمولة', 'Incentive': 'حافز', 'Air Ticket': 'تذكرة السفر', 'Petty Cash': 'العهدة النقدية',
    'Gratuity': 'مكافأة نهاية الخدمة', 'End of Service Benefit': 'مكافأة نهاية الخدمة', 'Leave Salary': 'راتب الإجازة', 'Leave Encashment': 'بدل الإجازة النقدي',
    'Loan Deduction': 'خصم القرض', 'Advance Salary Deduction': 'خصم السلفة', 'Salary Advance': 'سلفة الراتب', 'Absence Deduction': 'خصم الغياب',
    'Late Deduction': 'خصم التأخير', 'Fine': 'غرامة', 'Pension': 'المعاش', 'GPSSA Contribution': 'اشتراك الهيئة العامة للمعاشات',
    'Fuel Allowance': 'بدل الوقود', 'Education Allowance': 'بدل التعليم', 'Utility Allowance': 'بدل المرافق',
}

RELIGIONS = {
    'Islam': 'الإسلام', 'Muslim': 'مسلم', 'Christianity': 'المسيحية', 'Christian': 'مسيحي', 'Hinduism': 'الهندوسية', 'Hindu': 'هندوسي',
    'Buddhism': 'البوذية', 'Buddhist': 'بوذي', 'Sikhism': 'السيخية', 'Sikh': 'سيخي', 'Judaism': 'اليهودية', 'Jainism': 'الجاينية',
    'Zoroastrianism': 'الزرادشتية', 'Atheist': 'ملحد', 'Agnostic': 'لاأدري', 'Other': 'أخرى', 'Prefer not to say': 'أفضل عدم الإفصاح', 'None': 'لا يوجد',
}

NATIONALITIES = {
    'AFGHAN': 'أفغاني', 'ALBANIAN': 'ألباني', 'ALGERIAN': 'جزائري', 'AMERICAN': 'أمريكي', 'AMERICAN SAMOAN': 'من ساموا الأمريكية', 'ANDORRAN': 'أندوري',
    'ANGOLAN': 'أنغولي', 'ANGUILLAN': 'من أنغيلا', 'ANTARCTIC': 'من القارة القطبية الجنوبية', 'ANTIGUAN OR BARBUDAN': 'من أنتيغوا وباربودا',
    'ARGENTINE': 'أرجنتيني', 'ARMENIAN': 'أرميني', 'ARUBAN': 'من أروبا', 'AUSTRALIAN': 'أسترالي', 'AUSTRIAN': 'نمساوي', 'AZERBAIJANI': 'أذربيجاني',
    'AZERI': 'أذري', 'BAHAMIAN': 'باهامي', 'BAHRAINI': 'بحريني', 'BAHRAINIAN': 'بحريني', 'BANGLADESHI': 'بنغلاديشي', 'BARBADIAN': 'بربادوسي',
    'BASOTHO': 'ليسوتي', 'BELARUSIAN': 'بيلاروسي', 'BELGIAN': 'بلجيكي', 'BELIZEAN': 'بليزي', 'BENINESE': 'بنيني', 'BENINOIS': 'بنيني',
    'BERMUDAN': 'من برمودا', 'BERMUDIAN': 'من برمودا', 'BHUTANESE': 'بوتاني', 'BIOT': 'من إقليم المحيط الهندي البريطاني', 'BISSAU-GUINEAN': 'من غينيا بيساو',
    'BOLIVIAN': 'بوليفي', 'BONAIRE': 'من بونير', 'BOSNIAN': 'بوسني', 'BOTSWANAN': 'بوتسواني', 'BOUVET ISLAND': 'من جزيرة بوفيه', 'BRAZILIAN': 'برازيلي',
    'BRITISH': 'بريطاني', 'BRUNEIAN': 'بروناي', 'BULGARIAN': 'بلغاري', 'BURMESE': 'ميانماري', 'BURUNDIAN': 'بوروندي', 'CABO VERDEAN': 'من الرأس الأخضر',
    'CAMBODIAN': 'كمبودي', 'CAMEROONIAN': 'كاميروني', 'CANADIAN': 'كندي', 'CAYMANIAN': 'من جزر كايمان', 'CENTRAL AFRICAN': 'من أفريقيا الوسطى',
    'CHADIAN': 'تشادي', 'CHANNEL ISLAND': 'من جزر القنال', 'CHILEAN': 'تشيلي', 'CHINESE': 'صيني', 'CHRISTMAS ISLAND': 'من جزيرة كريسماس',
    'COCOS ISLAND': 'من جزر كوكوس', 'COLOMBIAN': 'كولومبي', 'COMORAN': 'قمري', 'COMORIAN': 'قمري', 'CONGOLESE': 'كونغولي', 'COOK ISLAND': 'من جزر كوك',
    'COSTA RICAN': 'كوستاريكي', 'CROATIAN': 'كرواتي', 'CUBAN': 'كوبي', 'CURAÇAOAN': 'من كوراساو', 'CYPRIOT': 'قبرصي', 'CZECH': 'تشيكي', 'DANISH': 'دنماركي',
    'DJIBOUTIAN': 'جيبوتي', 'DOMINICAN': 'دومينيكاني', 'DUTCH': 'هولندي', 'ECUADORIAN': 'إكوادوري', 'EGYPTIAN': 'مصري', 'EMIRATI': 'إماراتي',
    'EQUATOGUINEAN': 'من غينيا الاستوائية', 'EQUATORIAL GUINEAN': 'من غينيا الاستوائية', 'ERITREAN': 'إريتري', 'ESTONIAN': 'إستوني', 'ETHIOPIAN': 'إثيوبي',
    'FALKLAND ISLAND': 'من جزر فوكلاند', 'FAROESE': 'من جزر فارو', 'FIJIAN': 'فيجي', 'FILIPINO': 'فلبيني', 'FINNISH': 'فنلندي', 'FRENCH': 'فرنسي',
    'FRENCH GUIANESE': 'من غويانا الفرنسية', 'FRENCH POLYNESIAN': 'من بولينيزيا الفرنسية', 'FRENCH SOUTHERN TERRITORIES': 'من الأقاليم الجنوبية الفرنسية',
    'GABONESE': 'غابوني', 'GAMBIAN': 'غامبي', 'GEORGIAN': 'جورجي', 'GERMAN': 'ألماني', 'GHANAIAN': 'غاني', 'GIBRALTAR': 'من جبل طارق',
    'GREEK, HELLENIC': 'يوناني', 'GREENLANDIC': 'من غرينلاند', 'GRENADIAN': 'غرينادي', 'GUADELOUPE': 'من غوادلوب', 'GUAMANIAN': 'من غوام',
    'GUAMBAT': 'من غوام', 'GUATEMALAN': 'غواتيمالي', 'GUINEAN': 'غيني', 'GUYANESE': 'غوياني', 'HAITIAN': 'هايتي', 'HEARD ISLAND': 'من جزيرة هيرد',
    'HERZEGOVINIAN': 'هرسكي', 'HONDURAN': 'هندوراسي', 'HONG KONG': 'من هونغ كونغ', 'HONG KONGESE': 'من هونغ كونغ', 'HUNGARIAN': 'هنغاري',
    'I-KIRIBATI': 'كيريباتي', 'ICELANDIC': 'آيسلندي', 'INDIAN': 'هندي', 'INDONESIAN': 'إندونيسي', 'IRANIAN': 'إيراني', 'IRAQI': 'عراقي',
    'IRISH': 'إيرلندي', 'ISRAELI': 'إسرائيلي', 'ITALIAN': 'إيطالي', 'IVORIAN': 'إيفواري', 'JAMAICAN': 'جامايكي', 'JAPANESE': 'ياباني',
    'JORDANIAN': 'أردني', 'KAZAKH': 'كازاخي', 'KAZAKHSTANI': 'كازاخستاني', 'KENYAN': 'كيني', 'KIRGHIZ': 'قيرغيزي', 'KIRGIZ': 'قيرغيزي',
    'KUWAITI': 'كويتي', 'KYRGYZ': 'قيرغيزي', 'KYRGYZSTANI': 'قيرغيزستاني', 'LAO': 'لاوسي', 'LAOTIAN': 'لاوسي', 'LATVIAN': 'لاتفي',
    'LEBANESE': 'لبناني', 'LIBERIAN': 'ليبيري', 'LIBYAN': 'ليبي', 'LIECHTENSTEIN': 'من ليختنشتاين', 'LITHUANIAN': 'ليتواني',
    'LUXEMBOURG': 'لوكسمبورغي', 'LUXEMBOURGISH': 'لوكسمبورغي', 'MACANESE': 'من ماكاو', 'MACEDONIAN': 'مقدوني', 'MAGYAR': 'مجري',
    'MAHORAN': 'من مايوت', 'MALAGASY': 'مدغشقري', 'MALAWIAN': 'مالاوي', 'MALAYSIAN': 'ماليزي', 'MALDIVIAN': 'مالديفي', 'MALIAN': 'مالي',
    'MALINESE': 'مالي', 'MALTESE': 'مالطي', 'MANX': 'من جزيرة مان', 'MARSHALLESE': 'من جزر مارشال', 'MARTINICAN': 'من مارتينيك',
    'MARTINIQUAIS': 'من مارتينيك', 'MAURITANIAN': 'موريتاني', 'MAURITIAN': 'موريشيوسي', 'MCDONALD ISLANDS': 'من جزر ماكدونالد',
    'MEXICAN': 'مكسيكي', 'MICRONESIAN': 'ميكرونيزي', 'MOLDOVAN': 'مولدوفي', 'MONGOLIAN': 'منغولي', 'MONTENEGRIN': 'من الجبل الأسود',
    'MONTSERRATIAN': 'من مونتسرات', 'MOROCCAN': 'مغربي', 'MOTSWANA': 'بوتسواني', 'MOZAMBICAN': 'موزمبيقي', 'NAMIBIAN': 'ناميبي',
    'NAURUAN': 'ناوروي', 'NEPALESE': 'نيبالي', 'NEPALI': 'نيبالي', 'NETHERLANDIC': 'هولندي', 'NEW CALEDONIAN': 'من كاليدونيا الجديدة',
    'NEW ZEALAND': 'نيوزيلندي', 'NEW ZEALANDER': 'نيوزيلندي', 'NICARAGUAN': 'نيكاراغوي', 'NIGERIAN': 'نيجيري', 'NIGERIEN': 'من النيجر',
    'NIUEAN': 'من نييوي', 'NORTH KOREAN': 'كوري شمالي', 'OMANI': 'عماني', 'PAKISTANI': 'باكستاني', 'PERSIAN': 'فارسي', 'QATARI': 'قطري',
    'RUSSIAN': 'روسي', 'SALVADORAN': 'سلفادوري', 'SAUDI': 'سعودي', 'SINGAPOREAN': 'سنغافوري', 'SOUTH AFRICAN': 'جنوب أفريقي',
    'SOUTH KOREAN': 'كوري جنوبي', 'SPANISH': 'إسباني', 'SRI LANKAN': 'سريلانكي', 'SUDANESE': 'سوداني', 'SWISS': 'سويسري', 'SYRIAN': 'سوري',
    'THAI': 'تايلندي', 'TUNISIAN': 'تونسي', 'TURKISH': 'تركي', 'UGANDAN': 'أوغندي', 'UKRAINIAN': 'أوكراني', 'VATICAN': 'من الفاتيكان',
    'VIETNAMESE': 'فيتنامي', 'YEMENI': 'يمني', 'PALESTINIAN': 'فلسطيني', 'SOMALI': 'صومالي', 'MALAYALI': 'هندي',
}

# by ISO code (Core.cntry_mstr.country_code)
COUNTRIES = {
    'AE': 'الإمارات العربية المتحدة', 'SA': 'المملكة العربية السعودية', 'QA': 'قطر', 'OM': 'عُمان', 'BH': 'البحرين', 'KW': 'الكويت',
    'AF': 'أفغانستان', 'AL': 'ألبانيا', 'DZ': 'الجزائر', 'AS': 'ساموا الأمريكية', 'AD': 'أندورا', 'AO': 'أنغولا', 'AI': 'أنغيلا',
    'AQ': 'القارة القطبية الجنوبية', 'AG': 'أنتيغوا وباربودا', 'AR': 'الأرجنتين', 'AM': 'أرمينيا', 'AW': 'أروبا', 'AU': 'أستراليا',
    'AT': 'النمسا', 'AZ': 'أذربيجان', 'BS': 'جزر البهاما', 'BD': 'بنغلاديش', 'BB': 'باربادوس', 'BY': 'بيلاروس', 'BE': 'بلجيكا',
    'BZ': 'بليز', 'BJ': 'بنين', 'BM': 'برمودا', 'BT': 'بوتان', 'BO': 'بوليفيا', 'BQ': 'بونير وسينت أوستاتيوس وسابا', 'BA': 'البوسنة والهرسك',
    'BW': 'بوتسوانا', 'BV': 'جزيرة بوفيه', 'BR': 'البرازيل', 'IO': 'إقليم المحيط الهندي البريطاني', 'BN': 'بروناي', 'BG': 'بلغاريا',
    'BI': 'بوروندي', 'CV': 'الرأس الأخضر', 'KH': 'كمبوديا', 'CM': 'الكاميرون', 'CA': 'كندا', 'KY': 'جزر كايمان', 'CF': 'جمهورية أفريقيا الوسطى',
    'TD': 'تشاد', 'CL': 'تشيلي', 'CN': 'الصين', 'CX': 'جزيرة كريسماس', 'CC': 'جزر كوكوس', 'CO': 'كولومبيا', 'KM': 'جزر القمر',
    'CD': 'جمهورية الكونغو الديمقراطية', 'CG': 'جمهورية الكونغو', 'CK': 'جزر كوك', 'CR': 'كوستاريكا', 'HR': 'كرواتيا', 'CU': 'كوبا',
    'CW': 'كوراساو', 'CY': 'قبرص', 'CZ': 'التشيك', 'CI': 'ساحل العاج', 'DK': 'الدنمارك', 'DJ': 'جيبوتي', 'DM': 'دومينيكا',
    'DO': 'جمهورية الدومينيكان', 'EC': 'الإكوادور', 'EG': 'مصر', 'SV': 'السلفادور', 'GQ': 'غينيا الاستوائية', 'ER': 'إريتريا',
    'EE': 'إستونيا', 'ET': 'إثيوبيا', 'FK': 'جزر فوكلاند', 'FO': 'جزر فارو', 'FJ': 'فيجي', 'FI': 'فنلندا', 'FR': 'فرنسا',
    'GF': 'غويانا الفرنسية', 'PF': 'بولينيزيا الفرنسية', 'TF': 'الأقاليم الجنوبية الفرنسية', 'GA': 'الغابون', 'GM': 'غامبيا', 'GE': 'جورجيا',
    'DE': 'ألمانيا', 'GH': 'غانا', 'GI': 'جبل طارق', 'GR': 'اليونان', 'GL': 'غرينلاند', 'GD': 'غرينادا', 'GP': 'غوادلوب', 'GU': 'غوام',
    'GT': 'غواتيمالا', 'GG': 'غيرنزي', 'GN': 'غينيا', 'GW': 'غينيا بيساو', 'GY': 'غيانا', 'HT': 'هايتي', 'HM': 'جزيرة هيرد وجزر ماكدونالد',
    'HN': 'هندوراس', 'HK': 'هونغ كونغ', 'HU': 'المجر', 'IS': 'آيسلندا', 'IN': 'الهند', 'ID': 'إندونيسيا', 'IR': 'إيران', 'IQ': 'العراق',
    'IE': 'أيرلندا', 'IM': 'جزيرة مان', 'IL': 'إسرائيل', 'IT': 'إيطاليا', 'JM': 'جامايكا', 'JP': 'اليابان', 'JE': 'جيرسي', 'JO': 'الأردن',
    'KZ': 'كازاخستان', 'KE': 'كينيا', 'KI': 'كيريباتي', 'KP': 'كوريا الشمالية', 'KR': 'كوريا الجنوبية', 'KG': 'قيرغيزستان', 'LA': 'لاوس',
    'LV': 'لاتفيا', 'LB': 'لبنان', 'LS': 'ليسوتو', 'LR': 'ليبيريا', 'LY': 'ليبيا', 'LI': 'ليختنشتاين', 'LT': 'ليتوانيا', 'LU': 'لوكسمبورغ',
    'MO': 'ماكاو', 'MK': 'مقدونيا الشمالية', 'MG': 'مدغشقر', 'MW': 'مالاوي', 'MY': 'ماليزيا', 'MV': 'جزر المالديف', 'ML': 'مالي', 'MT': 'مالطا',
    'MH': 'جزر مارشال', 'MQ': 'مارتينيك', 'MR': 'موريتانيا', 'MU': 'موريشيوس', 'YT': 'مايوت', 'MX': 'المكسيك', 'FM': 'ميكرونيزيا',
    'MD': 'مولدوفا', 'MN': 'منغوليا', 'ME': 'الجبل الأسود', 'MS': 'مونتسرات', 'MA': 'المغرب', 'MZ': 'موزمبيق', 'MM': 'ميانمار', 'NA': 'ناميبيا',
    'NR': 'ناورو', 'NP': 'نيبال', 'NL': 'هولندا', 'NC': 'كاليدونيا الجديدة', 'NZ': 'نيوزيلندا', 'NI': 'نيكاراغوا', 'NE': 'النيجر', 'NG': 'نيجيريا',
    'NU': 'نييوي', 'NF': 'جزيرة نورفولك', 'MP': 'جزر ماريانا الشمالية', 'NO': 'النرويج', 'PK': 'باكستان', 'PW': 'بالاو', 'PS': 'فلسطين',
    'PA': 'بنما', 'PG': 'بابوا غينيا الجديدة', 'PY': 'باراغواي', 'PE': 'بيرو', 'PH': 'الفلبين', 'PN': 'جزر بيتكيرن', 'PL': 'بولندا', 'PT': 'البرتغال',
    'PR': 'بورتوريكو', 'RO': 'رومانيا', 'RU': 'روسيا', 'RW': 'رواندا', 'SH': 'سانت هيلينا وأسينشين وتريستان دا كونا', 'KN': 'سانت كيتس ونيفيس',
    'LC': 'سانت لوسيا', 'MF': 'سانت مارتن (الجزء الفرنسي)', 'PM': 'سان بيير وميكلون', 'VC': 'سانت فينسنت والغرينادين', 'WS': 'ساموا',
    'SM': 'سان مارينو', 'SN': 'السنغال', 'RS': 'صربيا', 'SC': 'سيشل', 'SL': 'سيراليون', 'SG': 'سنغافورة', 'SX': 'سينت مارتن (الجزء الهولندي)',
    'SK': 'سلوفاكيا', 'SI': 'سلوفينيا', 'SB': 'جزر سليمان', 'SO': 'الصومال', 'ZA': 'جنوب أفريقيا', 'GS': 'جورجيا الجنوبية وجزر ساندويتش الجنوبية',
    'SS': 'جنوب السودان', 'ES': 'إسبانيا', 'LK': 'سريلانكا', 'SD': 'السودان', 'SR': 'سورينام', 'SJ': 'سفالبارد ويان ماين', 'SZ': 'إسواتيني',
    'SE': 'السويد', 'CH': 'سويسرا', 'SY': 'سوريا', 'TW': 'تايوان', 'TJ': 'طاجيكستان', 'TZ': 'تنزانيا', 'TH': 'تايلاند', 'TL': 'تيمور الشرقية',
    'TG': 'توغو', 'TK': 'توكيلاو', 'TO': 'تونغا', 'TT': 'ترينيداد وتوباغو', 'TN': 'تونس', 'TR': 'تركيا', 'TM': 'تركمانستان',
    'TC': 'جزر توركس وكايكوس', 'TV': 'توفالو', 'UG': 'أوغندا', 'UA': 'أوكرانيا', 'GB': 'المملكة المتحدة', 'UM': 'جزر الولايات المتحدة الصغيرة النائية',
    'US': 'الولايات المتحدة الأمريكية', 'UY': 'أوروغواي', 'UZ': 'أوزبكستان', 'VU': 'فانواتو', 'VA': 'الفاتيكان', 'VE': 'فنزويلا', 'VN': 'فيتنام',
    'VG': 'جزر العذراء البريطانية', 'VI': 'جزر العذراء الأمريكية', 'WF': 'واليس وفوتونا', 'EH': 'الصحراء الغربية', 'YE': 'اليمن', 'ZM': 'زامبيا',
    'ZW': 'زيمبابوي',
}

EMPLOYMENT_TYPES = {'Full time': 'دوام كامل', 'Part time': 'دوام جزئي', 'Contract (fixed term)': 'عقد محدد المدة', 'Temporary': 'مؤقت', 'Intern': 'متدرب'}

EXPENSE_CATEGORIES = {
    'Mileage': 'بدل المسافة', 'Travel - Air': 'سفر - طيران', 'Hotel': 'فندق', 'Meals': 'وجبات', 'Per diem': 'بدل يومي', 'Taxi / Transport': 'تاكسي / مواصلات',
    'Fuel': 'وقود', 'Parking / Salik': 'مواقف / سالك', 'Telephone': 'هاتف', 'Client entertainment': 'ضيافة العملاء', 'Visa & government fees': 'تأشيرات ورسوم حكومية',
    'Office supplies': 'لوازم مكتبية', 'Training': 'تدريب', 'Other': 'أخرى',
}

ASSET_TYPES = {
    'Laptop': 'حاسوب محمول', 'Desktop': 'حاسوب مكتبي', 'Mobile Phone': 'هاتف متحرك', 'Vehicle': 'مركبة', 'Access Card': 'بطاقة دخول',
    'SIM Card': 'شريحة اتصال', 'Tablet': 'جهاز لوحي', 'Monitor': 'شاشة', 'Printer': 'طابعة', 'Furniture': 'أثاث', 'Tools': 'أدوات', 'Uniform': 'زي موحد',
}

DOC_REQUEST_TYPES = {
    'Passport Release': 'تسليم جواز السفر', 'Emirates ID Copy': 'نسخة الهوية الإماراتية', 'Salary Certificate': 'شهادة راتب',
    'Experience Certificate': 'شهادة خبرة', 'NOC': 'شهادة عدم ممانعة', 'Passport Copy': 'نسخة جواز السفر', 'Visa Copy': 'نسخة التأشيرة',
}

# (model label, field, {english: arabic}, match on: 'name' | 'code')
LISTS = [
    ('OrganisationManager.dept_master', 'dept_name', DEPARTMENTS, 'name'),
    ('OrganisationManager.desgntn_master', 'desgntn_job_title', DESIGNATIONS, 'name'),
    ('OrganisationManager.ctgry_master', 'ctgry_title', CATEGORIES, 'name'),
    ('calendars.leave_type', 'name', LEAVE_TYPES, 'name'),
    ('EmpManagement.document_type', 'type_name', DOCUMENT_TYPES, 'name'),
    ('EmpManagement.DocRequestType', 'type_name', DOC_REQUEST_TYPES, 'name'),
    ('PayrollManagement.SalaryComponent', 'name', SALARY_COMPONENTS, 'name'),
    ('Core.ReligionMaster', 'religion', RELIGIONS, 'name'),
    ('Core.Nationality', 'N_name', NATIONALITIES, 'name'),
    ('Core.cntry_mstr', 'country_name', COUNTRIES, 'country_code'),
    ('OrgStructure.EmploymentType', 'name', EMPLOYMENT_TYPES, 'name'),
    ('ExpenseManagement.ExpenseCategory', 'name', EXPENSE_CATEGORIES, 'name'),
    ('OrganisationManager.AssetType', 'name', ASSET_TYPES, 'name'),
]
