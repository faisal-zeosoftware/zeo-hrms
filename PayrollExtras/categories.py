"""v1.73.7 – payroll categories: the system list with today's rules, the company's own categories, and the sets the
calculations read (categories that count for gratuity, pension, leave salary, encashment, WPS variable pay).

Every reader falls back to SYSTEM when the table is not there yet (a company being created) – the results are then exactly
the rules ZEO had in code before 1.73.7."""
from django.apps import apps

# key: (name, kind, order, gratuity, pension, leave_salary, encashment, wps_variable)
SYSTEM = {
    'basic': ('Basic Salary', 'addition', 10, True, True, True, True, False),
    'hra': ('HRA', 'addition', 20, False, True, False, False, False),
    'housing_allowance': ('Housing Allowance', 'addition', 21, False, True, False, False, False),
    'transport_allowance': ('Transport Allowance', 'addition', 22, False, True, False, False, False),
    'education_allowance': ('Education Allowance', 'addition', 30, False, False, False, False, False),
    'food_allowance': ('Food Allowance', 'addition', 31, False, False, False, False, False),
    'telephone_allowance': ('Telephone Allowance', 'addition', 32, False, False, False, False, False),
    'medical_allowance': ('Medical Allowance', 'addition', 33, False, False, False, False, False),
    'travel_allowance': ('Travel Allowance', 'addition', 34, False, False, False, False, False),
    'other_allowance': ('Other Allowance', 'addition', 39, False, False, False, False, False),
    'bonus': ('Bonus', 'addition', 50, False, False, False, False, True),
    'commission': ('Commission', 'addition', 51, False, False, False, False, True),
    'overtime': ('Overtime', 'addition', 52, False, False, False, False, True),
    'leave_encashment': ('Leave Encashment', 'addition', 53, False, False, False, False, True),
    'air_ticket': ('Air Ticket', 'addition', 54, False, False, False, False, True),
    'gratuity': ('Gratuity', 'addition', 55, False, False, False, False, True),
    'advance_salary': ('Advance Salary', 'addition', 56, False, False, False, False, True),
    'loan': ('Loan Deduction', 'deduction', 70, False, False, False, False, False),
    'pf': ('Provident Fund', 'deduction', 71, False, False, False, False, False),
    'esi': ('ESI', 'deduction', 72, False, False, False, False, False),
    'tax': ('Tax', 'deduction', 73, False, False, False, False, False),
}
FLAGS = ('gratuity', 'pension', 'leave_salary', 'encashment', 'wps_variable')
AR = {'basic': 'الراتب الأساسي', 'hra': 'بدل السكن (HRA)', 'housing_allowance': 'بدل السكن', 'transport_allowance': 'بدل المواصلات',
      'education_allowance': 'بدل التعليم', 'food_allowance': 'بدل الطعام', 'telephone_allowance': 'بدل الهاتف', 'medical_allowance': 'بدل العلاج',
      'travel_allowance': 'بدل السفر', 'other_allowance': 'بدلات أخرى', 'bonus': 'المكافأة', 'commission': 'العمولة', 'overtime': 'العمل الإضافي',
      'leave_encashment': 'صرف بدل الإجازة', 'air_ticket': 'تذكرة الطيران', 'gratuity': 'مكافأة نهاية الخدمة', 'advance_salary': 'سلفة الراتب',
      'loan': 'خصم القرض', 'pf': 'صندوق الادخار', 'esi': 'التأمين الاجتماعي (ESI)', 'tax': 'الضريبة'}


def _model():
    try:
        return apps.get_model('PayrollExtras', 'PayrollCategory')
    except LookupError:
        return None


def ensure():
    """the system categories exist (made once per company; names and ticks a company changed are kept)"""
    Mo = _model()
    if Mo is None:
        return
    have = set(Mo.objects.filter(key__in=list(SYSTEM)).values_list('key', flat=True))
    for k, (name, kind, order, *fl) in SYSTEM.items():
        if k not in have:
            Mo.objects.create(key=k, name=name, name_ar=AR.get(k, ''), kind=kind, order=order, system=True, active=True,
                              **dict(zip(FLAGS, fl)))


def rows():
    Mo = _model()
    try:
        ensure()
        return list(Mo.objects.all()) if Mo else []
    except Exception:   # noqa: BLE001 – the table is not there yet
        return []


def keys_with(flag):
    """the category keys that count for gratuity / pension / leave_salary / encashment / wps_variable"""
    rs = rows()
    if not rs:
        return {k for k, v in SYSTEM.items() if v[3 + FLAGS.index(flag)]}
    return {r.key for r in rs if r.active and getattr(r, flag)}


def all_keys():
    rs = rows()
    return {r.key for r in rs} if rs else set(SYSTEM)


def label(key, lang='en', header=False):
    for r in rows():
        if r.key == key:
            if header:
                return (r.header_ar if lang == 'ar' and r.header_ar else '') or (r.header if lang != 'ar' and r.header else '') or \
                    (r.name_ar if lang == 'ar' and r.name_ar else r.name)
            return r.name_ar if lang == 'ar' and r.name_ar else r.name
    s = SYSTEM.get(key)
    return (AR.get(key) if lang == 'ar' else None) or (s[0] if s else key.replace('_', ' ').title())


def order_of(key):
    for r in rows():
        if r.key == key:
            return r.order
    s = SYSTEM.get(key)
    return s[2] if s else 100


def kind_of(key):
    for r in rows():
        if r.key == key:
            return r.kind
    s = SYSTEM.get(key)
    return s[1] if s else 'addition'
