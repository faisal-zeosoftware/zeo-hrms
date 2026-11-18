"""Ask ZEO 1.71.0 – understanding a question without an AI: normalising, Arabic → English synonyms, intents, periods, months.

The question is lower-cased, Arabic letters are normalised (alef forms, ya, ta marbuta, diacritics) and every Arabic or
English synonym found is replaced by the English word the rules use. The rules (INTENTS) then pick what the user wants;
the report questions go on to the report centre's own words (DashboardManagement/assistant.pick_report).
"""
from zeo.tz import today as _zt_today   # v1.73.16 the user's / company's day, not the server's UTC day
import re
from datetime import date, timedelta

AR_DIACRITICS = re.compile('[ؐ-ًؚ-ٰٟۖ-ۭـ]')


def norm_ar(s):
    s = AR_DIACRITICS.sub('', s or '')
    for a, b in (('أ', 'ا'), ('إ', 'ا'), ('آ', 'ا'), ('ى', 'ي'), ('ة', 'ه'), ('ؤ', 'و'), ('ئ', 'ي'), ('؟', '?'), ('،', ','), ('٫', '.')):
        s = s.replace(a, b)
    return s.translate(str.maketrans('٠١٢٣٤٥٦٧٨٩', '0123456789'))


def has_arabic(s):
    return bool(re.search('[؀-ۿ]', s or ''))


# Arabic (normalised) → English words of the rules. Longest first when applied.
AR = {
    'رصيد الاجازات': 'leave balance', 'رصيد اجازتي': 'my leave balance', 'رصيدي': 'my leave balance', 'رصيد': 'balance',
    'الاجازه السنويه': 'annual leave', 'اجازه سنويه': 'annual leave', 'الاجازات': 'leave', 'اجازات': 'leave', 'الاجازه': 'leave', 'اجازه': 'leave',
    'اجازتي': 'my leave', 'طلبات الاجازه': 'leave requests', 'معلقه': 'pending', 'المعلقه': 'pending', 'قيد الانتظار': 'pending', 'بانتظار': 'pending',
    'قسيمه الراتب': 'payslip', 'قسيمه راتبي': 'my payslip', 'كشف الراتب': 'payslip', 'قسيمتي': 'my payslip', 'راتبي': 'my salary',
    'الرواتب': 'payroll', 'رواتب': 'payroll', 'مسير الرواتب': 'payroll run', 'دوره الرواتب': 'payroll run', 'الراتب': 'salary', 'راتب': 'salary',
    'صافي الراتب': 'net pay', 'الصافي': 'net pay', 'اجمالي': 'total', 'المجموع': 'total', 'مجموع': 'total',
    'نهايه الخدمه': 'end of service', 'مكافاه نهايه الخدمه': 'gratuity', 'المكافاه': 'gratuity', 'مكافاه': 'gratuity', 'التسويه النهاييه': 'final settlement',
    'تسويه': 'settlement', 'المستحقات': 'settlement',
    'الوثايق': 'documents', 'وثايق': 'documents', 'مستنداتي': 'my documents', 'المستندات': 'documents', 'مستندات': 'documents',
    'جواز السفر': 'passport', 'جواز': 'passport', 'الهويه الاماراتيه': 'emirates id', 'هويه': 'emirates id', 'الاقامه': 'visa', 'اقامه': 'visa', 'تاشيره': 'visa',
    'تنتهي': 'expire', 'تنتهي صلاحيتها': 'expire', 'منتهيه': 'expired', 'انتهاء': 'expiry', 'صلاحيه': 'expiry',
    'الموظفين': 'employees', 'موظفين': 'employees', 'الموظف': 'employee', 'موظف': 'employee', 'عدد': 'how many', 'كم': 'how many',
    'الحضور': 'attendance', 'حضور': 'attendance', 'الغياب': 'absent', 'غياب': 'absent', 'غايب': 'absent', 'التاخير': 'late', 'متاخر': 'late',
    'العمل الاضافي': 'overtime', 'ساعات اضافيه': 'overtime', 'اضافي': 'overtime',
    'القروض': 'loans', 'قرض': 'loan', 'السلف': 'advances', 'سلفه': 'advance', 'تذاكر الطيران': 'air tickets', 'تذكره': 'air ticket',
    'القسم': 'department', 'الاقسام': 'departments', 'قسم': 'department', 'الفرع': 'branch', 'فرع': 'branch', 'الفروع': 'branches',
    'حسب': 'by', 'لكل': 'per', 'هذا الشهر': 'this month', 'الشهر الماضي': 'last month', 'الشهر القادم': 'next month', 'هذا العام': 'this year',
    'هذه السنه': 'this year', 'العام الماضي': 'last year', 'اليوم': 'today', 'هذا الاسبوع': 'this week',
    'كيف': 'how do i', 'كيفيه': 'how to', 'اين': 'where is', 'وين': 'where is', 'شاشه': 'screen', 'قايمه': 'menu',
    'اشرح': 'explain', 'وضح': 'explain', 'لماذا': 'why', 'ليش': 'why', 'سبب': 'why', 'الفرق': 'difference', 'اختلاف': 'difference', 'تغير': 'changed',
    'فحص': 'check', 'تحقق': 'check', 'تدقيق': 'audit', 'قبل الاعتماد': 'before approval', 'اعتماد': 'approval', 'موافقه': 'approval',
    'الاعداد': 'setup', 'اعداد': 'setup', 'التشغيل الفعلي': 'go live', 'الاطلاق': 'go live', 'جاهزيه': 'readiness',
    'جوده البيانات': 'data quality', 'البيانات': 'data', 'بيانات': 'data', 'مكرر': 'duplicate', 'مكرره': 'duplicates', 'ناقصه': 'missing', 'مفقوده': 'missing',
    'الايبان': 'iban', 'ايبان': 'iban', 'الحساب البنكي': 'bank account', 'حماية الاجور': 'wps', 'حمايه الاجور': 'wps', 'ملف الرواتب': 'wps file',
    'شغل الرواتب': 'run payroll', 'تشغيل الرواتب': 'run payroll', 'احسب الرواتب': 'run payroll', 'اعداد الرواتب': 'run payroll',
    'شهاده راتب': 'salary certificate', 'شهاده الراتب': 'salary certificate', 'تحويل الراتب': 'salary transfer letter', 'شهاده خبره': 'experience certificate',
    'شهاده عمل': 'employment certificate', 'عدم ممانعه': 'noc', 'خطاب': 'letter', 'رساله': 'letter', 'شهاده': 'certificate',
    'نموذج': 'template', 'قالب': 'template', 'رفع': 'upload', 'استيراد': 'import', 'دليل': 'guide', 'خطوات': 'steps', 'طلب': 'request', 'اطلب': 'request',
    'تقرير': 'report', 'اعرض': 'show', 'اظهر': 'show', 'قايمه ب': 'list', 'من هم': 'who', 'من': 'who', 'التذاكر': 'tickets', 'تذاكر الدعم': 'support tickets',
    'العقود': 'contracts', 'عقود الصيانه': 'amc', 'المشاريع': 'projects', 'مشروع': 'project', 'المهام': 'tasks', 'العملاء': 'customers', 'عميل': 'customer',
    'المستاجرين': 'tenants', 'الايجار': 'rent', 'عقود الايجار': 'leases', 'الاشغال': 'occupancy', 'الوحدات': 'units', 'المباني': 'buildings',
    'اوامر العمل': 'work orders', 'امر عمل': 'work order', 'الصيانه الوقاييه': 'preventive plans', 'الاصول': 'assets', 'اصل': 'asset', 'الاهلاك': 'depreciation',
    'الحوادث': 'incidents', 'حادث': 'incident', 'تصاريح العمل': 'permits', 'السلامه': 'safety', 'المخاطر': 'risks',
    'الفرص': 'opportunities', 'الصفقات': 'deals', 'العملاء المحتملين': 'leads', 'خط المبيعات': 'pipeline',
    'ماذا تستطيع': 'what can you do', 'مساعده': 'help', 'مرحبا': 'hello', 'السلام عليكم': 'hello',
    'ترجم': 'translate', 'ترجمه': 'translate', 'اكتب': 'write', 'صياغه': 'write', 'قانون العمل': 'labour law', 'قانون': 'law', 'سياسه الشركه': 'company policy',
    'اختبار': 'quiz', 'زد': 'increase', 'زياده': 'increase', 'حمل': 'load', 'تحميل': 'load',
    'تراجع': 'undo', 'الغاء': 'undo', 'غير': 'change', 'عدل': 'change', 'حدث': 'update',
    # 1.73.0 every app
    'ماذا ينتظرني': 'what is waiting for me', 'ما الذي ينتظرني': 'what is waiting for me', 'مهامي': 'my tasks', 'جاهزه': 'ready', 'جاهز': 'ready',
    'اعداد': 'setup', 'افحص': 'check', 'فحص': 'check', 'اداره العملاء': 'crm', 'العقارات': 'property', 'الصيانه': 'maintenance',
    'اداره الاصول': 'asset management', 'الصحه والسلامه': 'hse', 'المشاريع': 'projects', 'التذكره': 'ticket', 'امر العمل': 'work order',
    # 1.74.0 development
    'انشي': 'create', 'انشئ': 'create', 'اصنع': 'make', 'وحده مخصصه': 'custom module', 'وحده': 'module', 'سجل مخصص': 'custom register', 'اضف حقل': 'add a field',
}
AR_KEYS = sorted(AR, key=len, reverse=True)

# English synonyms → the word the rules use
EN = {
    'pay slip': 'payslip', 'salary slip': 'payslip', 'pay stub': 'payslip', 'vacation': 'leave', 'holiday balance': 'leave balance',
    'time off': 'leave', 'annual leave balance': 'leave balance', 'eosb': 'gratuity', 'end-of-service': 'end of service',
    'final settlement': 'settlement', 'full and final': 'settlement', 'f&f': 'settlement', 'eid number': 'emirates id', 'emirates-id': 'emirates id',
    'labour card': 'labour card', 'labor card': 'labour card', 'go-live': 'go live', 'golive': 'go live', 'payroll run': 'payroll run',
    'process payroll': 'run payroll', 'process the payroll': 'run payroll', 'run the payroll': 'run payroll', 'do payroll': 'run payroll',
    'guided payroll': 'run payroll', 'payroll wizard': 'run payroll', 'pre-check': 'precheck', 'pre check': 'precheck', 'precheck': 'precheck',
    'no objection': 'noc', 'wage protection': 'wps', 'sif file': 'wps file', 'employee list': 'employees', 'staff list': 'employees',
}
EN_KEYS = sorted(EN, key=len, reverse=True)


def canon(question):
    """English text the rules understand (Arabic words replaced by their English meaning)."""
    q = ' ' + norm_ar(question).lower().strip() + ' '
    if has_arabic(q):
        # v1.73.2: the indefinite accusative ("اضيف موظفا جديدا") ends in an extra alef – the word list has the plain form
        q = re.sub(r'([؀-ۿ]{3,})ا(?![؀-ۿ])', lambda m: m.group(1) if m.group(1) in AR or m.group(1) + 'ه' in AR or len(m.group(1)) >= 4 and
                   any(k.endswith(m.group(1)) for k in AR) else m.group(0), q)
        for k in AR_KEYS:
            if k in q:
                q = re.sub(r'(?<![؀-ۿ])(?:ال|و|ب|ل)?' + re.escape(k) + r'(?![؀-ۿ])', ' ' + AR[k] + ' ', q)
    for k in EN_KEYS:
        q = re.sub(r'(?<![a-z])' + re.escape(k) + r'(?![a-z])', EN[k], q)
    return re.sub(r'\s+', ' ', q).strip()


# ------------------------------------------------------------------ intents (checked in this order)
I = re.compile
GCC_IN = (r'\b(saudi|ksa|kuwait|bahrain|oman|qatar|india|indian|gosi|sio|pasi|mudad|qiwa|gpssa abroad|epf|pf|esi|esic|labour codes?|'
          r'riyadh|jeddah|dammam|khobar|manama|muscat|salalah|doha|kuwait city|bengaluru|bangalore|mumbai|delhi|chennai|hyderabad|kochi|pune)\b')
INTENTS = [
    ('undo', I(r'^(undo|take back|reverse)\b')),
    # v1.73.2 – "can ZEO …" is a product question even when it names payroll ("can ZEO run payroll for …" is not the guided payroll)
    ('product', I(r'^\s*(can|does|is|will) zeo\b|^\s*is (it|there) (possible|a way)\b')),
    # v1.73.2 – reports and dashboards built from a sentence (report builder)
    ('build_report', I(r'\b(create|make|build|design|set up|add|prepare)\b.*\b(report|dashboard|chart|graph|kpi card)s?\b(?!.*\bmodule\b)')),
    # v1.73.2 – the rules of another GCC country or India: from the guides (country rules 1.70.0), not the UAE reports
    ('product', I(r'(?=.*' + GCC_IN + r')(?=.*\b(gratuity|end of service|eos|eosb|social (security|insurance)|insurance|pension|contribution|'
                  r'rates?|rules?|law|calculat\w*|support|file|provident|labour|deduction|limits?|settlement|leave)\b)')),
    ('my_work', I(r'\bmy (leads|opportunities|deals|quotations|follow-?ups|tickets|work orders|complaints|enquiries)\b')),
    ('greeting', I(r'^(hi|hello|hey|salam|good (morning|afternoon|evening))\b[\s!.?]*$|what can you do|^help\b|^help me$|what do you do')),
    # 1.74.0 – development without programming (ZeoFlow custom modules and Blueprint)
    # v1.73.5 – a full code module (Django + Angular, level 4) for an own server
    ('write_module', I(r'\b(write|create|draft|build|generate|make|develop)\b.*\b(full|code|python|django|angular|level 4)\b.*\b(module|app)\b')),
    # v1.73.4 (A15) – the AI writes a ZEO Developer script
    ('write_script', I(r'\b(write|create|make|draft|build|generate)\b.*\b(developer )?scripts?\b|\bscript (that|to|which)\b')),
    ('module_field', I(r'\badd (a |an |the )?(new )?(field|column)s?\b.*\b(to|in|on) (the )?.+\b(module|register|form|requests?)\b')),
    ('build_module', I(r'\b(create|make|build|design|set up|develop)\b.*\b(custom )?(module|register|custom form|app for|form (for|called|named))\b|'
                       r'\b(i|we) (need|want)\b.*\b(staff|employees|people|users|workers)\b.*\b(to )?(ask|request|apply|submit)\b.*\bfor\b.*\bapprov')),   # v1.73.2
    # v1.73.10 – find a file in the Document Center ("find Khalid's passport", "show expired trade licences", "where is the lease contract")
    ('find_file', I(r'\b(find|show|where is|where are|search for|search|open|get)\b.*\b(passports?|visas?|contracts?|licen[cs]es?|certificates?|'
                    r'emirates id|agreements?|scans?|files?|documents?|leases?)\b|\bابحث\b|\bاين\b.*(ملف|مستند|جواز)')),
    # v1.73.8 – the payroll health check ("which employees have no Basic?") and adding a payroll category
    ('payroll_health', I(r'\bpayroll health\b|\bsalary health\b|\b(which|who|list|show|any)\b.*\b(no|without|missing|zero)\b.*\b(basic|salary|salaries|payroll rule|pay rule)\b|'
                         r'\b(employees?|staff)\b.*\b(no|without|missing)\b.*\b(basic|salary|payroll rule)\b')),
    ('payroll_category', I(r'\b(add|create|new|make)\b.*\bpayroll categor(y|ies)\b|\bpayroll categor(y|ies)\b.*\b(add|create|new|list|show|which|what)\b|\b(which|what|list|show)\b.*\bpayroll categor(y|ies)\b')),
    ('guided_payroll', I(r'\brun payroll\b|\bpayroll (step|steps|guide)\b|\bstart (the )?payroll\b|\bnext payroll step\b')),
    ('precheck', I(r'\b(precheck|audit|check)\b.*\b(payroll|run|payslips?)\b|\b(payroll|run)\b.*\b(precheck|audit|check)\b|before approval')),
    # 1.73.0 – what waits for me in every app ("what is waiting for me?", "my to-do", "what do I need to do")
    ('my_work', I(r'\b(what|anything)\b.*\b(waiting|pending|open)\b.*\bfor me\b|\bmy (to-?dos?|tasks|work|pending items)\b|\bwhat do i (need|have) to do\b|'
                  r'\bwhat needs my (attention|action)\b')),
    # 1.73.0 – the setup check of one app ("is CRM ready?", "check the HSE setup")
    ('app_setup', I(r'(?=.*\b(check|ready|readiness|missing|complete|gaps?)\b)(?=.*\b(crm|property|maintenance|asset management|assets app|hse|health and safety|'
                    r'projects app|projects|people app|hr operations)\b)(?=.*\b(setup|set up|ready|readiness|configur\w*)\b)')),
    ('setup_check', I(r'\b(setup|set up|go live|readiness|ready to go live|configuration)\b.*\b(check|missing|ready|gaps?|complete)\b|\b(check|missing)\b.*\b(setup|go live)\b|\bam i ready\b|\breadiness\b')),
    ('data_quality', I(r'\bdata quality\b|\b(check|wrong|invalid|bad)\b.*\b(iban|emirates id|data)\b|\bduplicates?\b|\bmissing documents?\b|\b(iban|emirates id)s?\b.*\b(wrong|invalid|check)\b')),
    ('fix_eid', I(r'\b(fix|format|correct)\b.*\bemirates id')),
    ('explain_payslip', I(r'\b(explain|why|break ?down|difference|changed|what is)\b.*\b(payslip|net pay|salary|pay)\b|\b(payslip|salary|net pay)\b.*\b(explain|why|difference|changed|lower|higher|less|more)\b')),
    ('explain_leave', I(r'\b(explain|how|why)\b.*\bleave balance\b|\bleave balance\b.*\b(explain|how|why|work|calculated)\b|\bhow (is|was) my leave\b')),
    ('explain_settlement', I(r'\b(explain|why|how)\b.*\b(settlement|end of service)\b|\b(settlement|end of service)\b.*\b(explain|how|why|calculated)\b')),
    ('explain_gratuity', I(r'\b(explain|why|how)\b.*\bgratuity\b|\bgratuity\b.*\b(explain|how|why|calculated|for)\b')),
    # 1.72.0 – with the company's AI (each has a built-in answer or says that the AI is needed)
    ('translate', I(r'^\s*translate\b|\btranslate (this|the|it|to|into|in)\b|\bin arabic\b.*\btranslat|\btranslation\b')),
    ('quiz', I(r'\b(quiz|uat|test script|test cases?|acceptance test|exam questions?)\b')),
    ('law', I(r'\b(labou?r law|uae law|the law|legal|lawful|allowed by law|by law|decree|article \d+|mohre (rule|law)|entitled to|is it legal)\b')),
    ('write', I(r'\b(write|draft|compose|prepare)\b.*\b(letter|e-?mail|message|announcement|memo|notice|warning|offer|reply|post)\b')),
    ('bulk', I(r'\b(give|increase|raise|decrease|reduce|cut|add)\b.*(\d+(\.\d+)?\s*%|\baed\s*\d|\d+\s*aed\b)')),
    ('load_workbook', I(r'\b(load|import)\b.*\b(workbook|the data|my data|the file|uploaded)\b|\bstatus of (my|the) (workbook|import|upload|data)\b')),
    ('product', I(r'\b(can zeo|does zeo|is zeo|zeo (support|have|do)|is it possible|is there a way|scope|implement|implementation|requirements?|'
                  r'how should (we|i) (set up|configure|do)|best way to|which version|release notes?|not built|limitation|what is needed|what do (i|we) need|prerequisites?)\b')),
    ('document', I(r'\b(our|company|the company\'?s?|employee) (policy|policies|handbook|sop|rules?|procedure)\b|\bwhat does (our|the) .* say\b|'
                   r'\baccording to (our|the) (policy|handbook)\b')),
    ('letter', I(r'\b(salary certificate|salary transfer letter|experience certificate|employment certificate|noc|embassy letter|bank account opening letter|letter|certificate)\b')),
    ('template', I(r'\b(template|upload|import|data workbook|workbook)\b')),
    ('my_leave', I(r'\bmy (leave|leave balance|balance)\b|\bhow many (leave )?days (do i|have i)\b|\bmy leave balance\b')),
    ('my_payslip', I(r'\bmy (payslip|salary|net pay|last payslip)\b')),
    ('my_documents', I(r'\bmy (documents|passport|visa|emirates id)\b')),
    ('form', I(r'\b(apply for|request|raise|book|ask for)\b.*\b(leave|vacation|letter|document|loan|advance|air ticket)\b|\bi want (a |to take )?(leave|day off)\b')),
    ('guide', I(r'\b(guide|steps|step by step|checklist|walk ?through|procedure)\b')),
    ('howto', I(r'\b(how (do|can|to|should) (i|we|you)?|how to|where (is|are|do|can|to)|which (screen|menu|page)|where can i|can i|find the|open the)\b')),
]


def intent_of(cq):
    for name, rx in INTENTS:
        if rx.search(cq):
            return name
    return 'data'


# ------------------------------------------------------------------ months and periods
MONTHS = {m: i for i, m in enumerate(['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october',
                                       'november', 'december'], 1)}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})
MONTHS.update({'sept': 9})
AR_MONTHS = {'يناير': 1, 'فبراير': 2, 'مارس': 3, 'ابريل': 4, 'مايو': 5, 'يونيو': 6, 'يوليو': 7, 'اغسطس': 8, 'سبتمبر': 9, 'اكتوبر': 10,
             'نوفمبر': 11, 'ديسمبر': 12}


def month_in(question, today=None):
    """(year, month) named in the question, or None. 'last month' / 'this month' too."""
    today = today or _zt_today()
    q = norm_ar(question).lower()
    for k, v in AR_MONTHS.items():
        if k in q:
            q += f' {list(MONTHS)[v - 1]}'
    cq = canon(question)
    if 'last month' in cq:
        d = today.replace(day=1) - timedelta(days=1)
        return d.year, d.month
    if 'this month' in cq:
        return today.year, today.month
    m = re.search(r'\b(\d{4})-(\d{1,2})\b', q)
    if m and 1 <= int(m.group(2)) <= 12:
        return int(m.group(1)), int(m.group(2))
    for name, num in sorted(MONTHS.items(), key=lambda x: -len(x[0])):
        if re.search(r'(?<![a-z])' + name + r'(?![a-z])', q):
            y = re.search(r'\b(20\d\d)\b', q)
            year = int(y.group(1)) if y else (today.year if num <= today.month else today.year - 1)
            return year, num
    return None


STOP = set('a an the of in on for to and or is are was were be do does did i we you my our me it this that what which who how where when why '
           'can could should would will please show give tell list find open screen page menu zeo ask about with from by per all any '
           'there their them his her its as at into than then so if not no yes'.split())


def stem(w):
    """Plural → singular, enough for matching screen names (employees → employee, policies → policy, approvals → approval)."""
    if len(w) > 4 and w.endswith('ies'):
        return w[:-3] + 'y'
    if len(w) > 4 and w.endswith(('ches', 'shes', 'sses', 'xes')):
        return w[:-2]
    if len(w) > 3 and w.endswith('s') and not w.endswith(('ss', 'us', 'is')):
        return w[:-1]
    return w


def tokens(s):
    return [stem(w) for w in re.findall(r'[a-z0-9]+', canon(s)) if w not in STOP and len(w) > 1]
