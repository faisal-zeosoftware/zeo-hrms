"""v1.61.0 – suggest the category and priority of a new HR ticket.

Built-in (always available, nothing leaves the server): each category scores the words of the subject and question against its
keywords (Helpdesk categories › Keywords, any language), its name and its description; urgency words set the priority.
External AI (only when the server and the company switched it on and "HR tickets" is on in AI settings): the subject, the
question and the category list are sent; the answer is checked against the real categories, and any failure falls back to
the built-in rules.

HR operations settings › Ticket category and priority: off / suggest only (default) / suggest and apply when sure –
"apply" moves a ticket from a general category (e.g. Other) to the suggested one and raises (never lowers) the priority,
when the suggestion is at least the set % sure.
"""
import json
import logging
import re

log = logging.getLogger(__name__)
RANK = {'low': 0, 'normal': 1, 'high': 2, 'urgent': 3}
STOP = set('the a an and or of to for in on at is are was be my me i we our your it this that with from by as not no do does did can '
           'please hi hello dear thanks thank regards sir madam kindly need want would like about have has had will get'.split())
URGENT = ('urgent', 'emergency', 'immediately', 'asap', 'right now', 'today itself', 'cannot work', 'stuck at the airport', 'police',
          'عاجل', 'طارئ', 'فورا', 'فوراً', 'حالا')
HIGH = ('not paid', 'not received my salary', 'salary not received', 'no salary', 'unpaid', 'wrong salary', 'visa expired', 'visa expires',
        'expired', 'fine', 'penalty', 'blocked', 'locked out', 'cannot log in', "can't log in", 'deadline', 'tomorrow', 'travel',
        'لم أستلم', 'الراتب لم', 'منتهية', 'غرامة', 'مخالفة')
LOW = ('no rush', 'when convenient', 'whenever', 'just a question', 'for information', 'just wondering', 'not urgent', 'ليس عاجلا')
DEFAULT_KEYWORDS = {
    'Payslip or salary question': 'payslip, salary, pay slip, deduction, deducted, allowance, overtime, bonus, net pay, gross, wps, bank transfer, increment, '
                                  'راتب, الراتب, قسيمة, خصم, بدل, عمل إضافي',
    'Leave and attendance': 'leave, annual leave, sick leave, vacation, holiday, balance, absent, attendance, punch, late, check in, check out, '
                            'إجازة, اجازة, رصيد, حضور, غياب, بصمة',
    'Letter or certificate': 'letter, certificate, salary certificate, salary transfer letter, noc, no objection, experience letter, employment letter, '
                             'to whom it may concern, شهادة, شهادة راتب, شهادة خبرة, خطاب, رسالة, عدم ممانعة, لمن يهمه الأمر',
    'Documents, visa and ID': 'visa, passport, emirates id, eid, iqama, labour card, work permit, residence, medical test, renewal, '
                              'تأشيرة, جواز, هوية, إقامة, اقامة, بطاقة عمل',
    'Benefits and insurance': 'insurance, medical, health card, clinic, hospital, air ticket, ticket allowance, dependant, dependent, family, benefit, '
                              'تأمين, تامين, طبي, تذكرة, بدل سكن',
    'Policy question': 'policy, rule, allowed, dress code, working hours, handbook, procedure, سياسة, لائحة, قواعد',
    'Access to ZEO': 'login, log in, password, reset password, otp, app, mobile app, zeo, account locked, cannot sign in, sign in, '
                     'كلمة المرور, تسجيل الدخول, التطبيق',
}


def _norm(t):
    return re.sub(r'\s+', ' ', str(t or '').lower()).strip()


def _words(t):
    return [w for w in re.findall(r'[\w؀-ۿ]+', _norm(t)) if len(w) > 2 and w not in STOP]


def _terms(cat):
    kw = [k.strip().lower() for k in re.split(r'[,\n;]+', cat.keywords or '') if k.strip()]
    if not kw and cat.name in DEFAULT_KEYWORDS:
        kw = [k.strip().lower() for k in DEFAULT_KEYWORDS[cat.name].split(',') if k.strip()]
    return kw, set(_words(cat.name)) | set(_words(cat.description))


def priority_of(text):
    t = _norm(text)
    if any(w in t for w in URGENT):
        return 'urgent', 'urgent words'
    if any(w in t for w in HIGH):
        return 'high', 'words about pay, an expiry or a block'
    if any(w in t for w in LOW):
        return 'low', 'the employee says it is not urgent'
    return 'normal', ''


def builtin(subject, description, cats):
    text = _norm(f'{subject} {subject} {description}')   # the subject counts twice
    words = set(_words(text))
    scored = []
    for c in cats:
        if c.redirect or c.catch_all:
            continue
        kw, loose = _terms(c)
        hits = [k for k in kw if (k in text if ' ' in k or not k.isascii() else re.search(r'\b' + re.escape(k) + r's?\b', text))]
        score = 3 * len(hits) + len(words & loose)
        if score:
            scored.append((score, c, hits))
    pr, why_p = priority_of(f'{subject} {description}')
    if not scored:
        return {'category': None, 'priority': pr, 'confidence': 0, 'source': 'builtin', 'reason': 'no category words found' + (f'; {why_p}' if why_p else '')}
    scored.sort(key=lambda x: -x[0])
    top = scored[0]
    second = scored[1][0] if len(scored) > 1 else 0
    conf = int(round(100 * top[0] / (top[0] + second + 2)))   # one strong match ≈ 60 %, several ≈ 75–90 %
    reason = ('matched: ' + ', '.join(top[2][:4])) if top[2] else 'words of the category name'
    return {'category': top[1], 'priority': pr, 'confidence': max(1, min(conf, 95)), 'source': 'builtin', 'reason': reason + (f'; {why_p}' if why_p else '')}


def external(request, subject, description, cats):
    from AISettings import service as AI
    usable = [c for c in cats if not c.redirect]
    listing = '\n'.join(f'{c.pk}: {c.name}' + (f' – {c.description}' if c.description else '') for c in usable)
    system = ('You sort questions employees send to HR. Answer with JSON only: {"category_id": <id from the list>, "priority": "low|normal|high|urgent", '
              '"confidence": <0-100>, "reason": "<at most 15 words>"}. Urgent = the employee cannot work, travel or get paid today; '
              'high = pay, an expiry or a deadline within days; low = the employee says it is not urgent.')
    msg = f'Categories:\n{listing}\n\nSubject: {subject[:200]}\nQuestion: {description[:2000]}'
    r = AI.chat(request, 'tickets', system, [{'role': 'user', 'content': msg}], None, 200)
    text = ' '.join(b.get('text', '') for b in r.get('content') or [] if b.get('type') == 'text')
    m = re.search(r'\{.*\}', text, re.S)
    if not m:
        raise AI.ExternalUnavailable('no JSON in the answer')
    d = json.loads(m.group(0))
    cat = next((c for c in usable if str(c.pk) == str(d.get('category_id'))), None)
    pr = d.get('priority') if d.get('priority') in RANK else 'normal'
    try:
        conf = max(0, min(int(d.get('confidence') or 0), 100))
    except (TypeError, ValueError):
        conf = 0
    if cat is None:
        conf = 0
    return {'category': cat, 'priority': pr, 'confidence': conf, 'source': 'external', 'reason': str(d.get('reason') or '')[:300]}


def classify(subject, description, request=None, cats=None):
    from .helpdesk import categories
    cats = list(cats if cats is not None else categories())
    try:
        from AISettings import service as AI
        if AI.feature_mode('tickets')[0] == 'external':
            try:
                return external(request, subject, description, cats)
            except Exception as e:   # any failure → the built-in rules
                log.info('ticket AI fell back to the built-in rules: %s', e)
    except Exception:
        pass
    return builtin(subject, description, cats)


def suggest(o, request=None, settings=None, apply=None):
    """Store the suggestion on the ticket (not saved); apply it when the setting says so. Returns the suggestion."""
    from .models import HRSettings
    s = settings or HRSettings.get()
    if s.ticket_ai == 'off':
        return None
    r = classify(o.subject, o.description, request)
    o.ai_category, o.ai_priority, o.ai_confidence, o.ai_source, o.ai_reason = r['category'], r['priority'], r['confidence'], r['source'], r['reason'][:300]
    o.ai_applied = False
    do_apply = (s.ticket_ai == 'apply') if apply is None else apply
    if do_apply and r['confidence'] >= (s.ticket_ai_min or 70):
        if r['category'] is not None and o.category_id and o.category.catch_all and r['category'].pk != o.category_id:
            o.category = r['category']
            o.ai_applied = True
        if RANK.get(r['priority'], 1) > RANK.get(o.priority, 1):
            o.priority = r['priority']
            o.ai_applied = True
    return r
