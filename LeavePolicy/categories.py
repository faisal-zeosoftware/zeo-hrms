"""v1.73.7 – leave categories: the system list (calendars.leave_type.CATEGORY_CHOICES) and the company's own."""
from django.apps import apps

SYSTEM = {'annual': ('Annual Leave', 10, 'الإجازة السنوية'), 'sick': ('Sick Leave', 20, 'الإجازة المرضية'), 'casual': ('Casual Leave', 30, 'الإجازة العارضة'),
          'maternity': ('Maternity Leave', 40, 'إجازة الأمومة'), 'paternity': ('Paternity Leave', 41, 'إجازة الأبوة'),
          'compensatory': ('Compensatory Leave', 50, 'الإجازة التعويضية'), 'bereavement': ('Bereavement Leave', 60, 'إجازة الحداد'),
          'unpaid': ('Unpaid Leave', 90, 'إجازة بدون راتب'),
          'on_duty': ('On Duty', 95, 'مهمة عمل')}   # v1.73.8 – out of office on work (client visit, training): paid, not an absence


def _model():
    try:
        return apps.get_model('LeavePolicy', 'LeaveCategory')
    except LookupError:
        return None


def ensure():
    Mo = _model()
    if Mo is None:
        return
    have = set(Mo.objects.filter(key__in=list(SYSTEM)).values_list('key', flat=True))
    for k, (name, order, ar) in SYSTEM.items():
        if k not in have:
            Mo.objects.create(key=k, name=name, name_ar=ar, order=order, system=True)


def rows():
    Mo = _model()
    try:
        ensure()
        return list(Mo.objects.all()) if Mo else []
    except Exception:   # noqa: BLE001
        return []


def all_keys():
    rs = rows()
    return {r.key for r in rs} if rs else set(SYSTEM)


def system_of(key):
    """the system category a key behaves like (a system key is itself)"""
    if key in SYSTEM:
        return key
    for r in rows():
        if r.key == key:
            return r.like or 'casual'
    return key


def label(key, lang='en', header=True):
    for r in rows():
        if r.key == key:
            if lang == 'ar':
                return (r.header_ar if header else '') or r.name_ar or r.header or r.name
            return (r.header if header else '') or r.name
    s = SYSTEM.get(key)
    return (s[2] if lang == 'ar' else s[0]) if s else key.replace('_', ' ').title()


def order_of(key):
    for r in rows():
        if r.key == key:
            return r.order
    return SYSTEM.get(key, ('', 100))[1]


def keys_like(system_key):
    """the system key and every company category that behaves like it (for queries: leave_category__in=keys_like('annual'))"""
    out = {system_key}
    for r in rows():
        if not r.system and (r.like or '') == system_key:
            out.add(r.key)
    return out
