"""Ask ZEO 1.74.0 – development without programming: a custom module (ZeoFlow custom modules and Blueprint, 1.67.26) made or changed
from a description, as a preview to confirm, undone in one click.

    "Create a vehicle request module with vehicle type (car, van or bus), from date, to date, destination, purpose and the cost,
     approved by the line manager then HR"
    "Add a field driver needed (yes/no) to the vehicle request module"

Without the company's AI the description is read by rules (fields after "with" / "fields:", their type from the words – date, amount,
number, e-mail, phone, employee, yes/no, a list in brackets or "a, b or c" – and the approval chain after "approved by"). With the AI
the same design is asked for as JSON and checked the same way. Either way ZeoFlow's own checks (clean_fields, clean_blueprint) run on
the preview, the module is made by its own API (POST / PUT flow/api/custom-modules/) and Undo deletes it (or puts the old design back)
while it has no records. Company administrators only, and the switch "Ask ZEO may change setup".
"""
import copy
import json
import re

from Translations.i18n import tr

from . import changes

MS = '/main-sidebar/'
LINK = MS + 'settings/custom-modules'
WHO = {'line manager': 'manager', 'manager': 'manager', 'hr': 'hr', 'human resources': 'hr', 'admin': 'admin', 'administrator': 'admin',
       'finance': 'group:Finance', 'the requester': 'owner', 'requester': 'owner'}
TYPE_WORDS = [
    ('date', r'\b(date|day|from|to|until|deadline|due|start|end|joining|expiry)\b'),
    ('money', r'\b(amount|cost|price|fee|budget|salary|value|aed|total|allowance)\b'),
    ('number', r'\b(number of|qty|quantity|count|days|hours|nights|km|kilometres|people|persons|age)\b'),
    ('email', r'\b(e-?mail)\b'),
    ('phone', r'\b(phone|mobile|telephone)\b'),
    ('employee', r'\b(employee|requested for|staff member|driver name|approver name)\b'),
    ('yesno', r'\b(yes ?/ ?no|needed\?|is |has |required\?)'),
    ('textarea', r'\b(notes?|description|reason|purpose|comments?|details|remarks?|justification)\b'),
]


def approver(w):
    """v1.73.2: who approves a step – the four built-in roles, or a permission group of the company ("procurement manager",
    "finance manager", "general manager" were dropped before, so a three-step approval became Open → Closed)."""
    raw = re.sub(r'\s+', ' ', str(w or '').strip())
    w = raw.lower()
    if not w or len(w) > 60:
        return None
    if w in WHO:
        return WHO[w]
    if w in ('manager', 'line manager', 'reporting manager', 'direct manager', 'supervisor'):
        return 'manager'
    try:
        from django.contrib.auth.models import Group
        from ZeoApps import groupscope
        qs = Group.objects.filter(pk__in=groupscope.visible_ids()) if groupscope.schema() else Group.objects.all()
        names = list(qs.values_list('name', flat=True))
    except Exception:   # noqa: BLE001
        names = []
    for n in names:   # an existing group of that name first ("Finance" for "finance manager" only when nothing closer)
        if n.lower() == w:
            return 'group:' + n
    for n in names:
        if w in n.lower() and len(n) < 60:
            return 'group:' + n
    # v1.73.4: the words as written – "CFO", "HSE officer" (were "Cfo", "Hse officer")
    return 'group:' + ' '.join(r if r.isupper() and len(r) > 1 else r[:1].upper() + r[1:] for r in raw.split())


def group_missing(who):
    if not str(who).startswith('group:'):
        return False
    try:
        from django.contrib.auth.models import Group
        return not Group.objects.filter(name=who[6:]).exists()
    except Exception:   # noqa: BLE001
        return False


AR_TYPES = [('date', r'تاريخ|يوم|من تاريخ|الى تاريخ|بدايه|نهايه|انتهاء'), ('money', r'مبلغ|تكلفه|سعر|رسوم|قيمه|ميزانيه|راتب|بدل'),
            ('number', r'عدد|كميه|ساعات|ايام'), ('email', r'بريد|ايميل'), ('phone', r'هاتف|جوال|موبايل'), ('employee', r'الموظف|موظف'),
            ('yesno', r'هل |نعم ?/ ?لا'), ('textarea', r'سبب|ملاحظات|وصف|غرض|الغرض|تفاصيل')]
AR_WHO = {'المدير المباشر': 'manager', 'المدير': 'manager', 'مدير القسم': 'manager', 'الموارد البشريه': 'hr', 'شوون الموظفين': 'hr', 'المسوول': 'admin', 'الاداره': 'admin'}


def parse_ar(description):
    """v1.73.2: an Arabic description – "أنشئ وحدة طلب سيارة مع نوع السيارة (سيدان، فان) وتاريخ البداية وتاريخ النهاية والوجهة، يعتمدها المدير المباشر ثم الموارد البشرية"."""
    from .words import norm_ar
    d = ' '.join(str(description or '').split())
    n = norm_ar(d)
    m = re.search(r'(?:وحده|سجل|نموذج|تطبيق)\s+(?:باسم\s+|ل)?(.+?)\s+(?:مع|بحقول|فيها|يحتوي على|تحتوي على|ب)\s+(.+)$', n) or \
        re.search(r'(?:وحده|سجل|نموذج|تطبيق)\s+(?:باسم\s+|ل)?(.+?)\s*[:،,]\s*(.+)$', n)
    if not m:
        return None
    name, body = m.group(1).strip(' ،,.'), m.group(2)
    if len(n) == len(d):   # the name as written (ة, أ …), not the normalised letters
        name = d[m.start(1):m.end(1)].strip(' ،,.')
    appr = ''
    am = re.search(r'(?:يعتمدها|يعتمده|يوافق عليها|يوافق عليه|موافقه|باعتماد|اعتماد)\s+(.+)$', body)
    if am:
        appr, body = am.group(1), body[:am.start()]
    parts, depth, cur = [], 0, ''
    for ch in body:
        depth += ch == '('
        depth -= ch == ')' and depth > 0
        if ch in '،,' and depth == 0:
            parts.append(cur)
            cur = ''
        else:
            cur += ch
    parts.append(cur)
    out = []
    for p in parts:
        out += [x for x in re.split(r'\s+و(?=[؀-ۿ])(?![^()]*\))', ' ' + p) if x.strip()]
    fields, seen = [], set()
    for raw in out:
        raw = raw.strip(' ،,.')
        raw = re.sub(r'^و(?=[؀-ۿ]{3,})', '', raw)
        label = re.sub(r'\([^)]*\)', '', raw).strip()
        if len(label) < 2:
            continue
        t, opts = 'text', ''
        om = re.search(r'\(([^)]+)\)', raw)
        if om:
            o = [x.strip() for x in re.split(r'\s*(?:،|,|\bاو\b|/)\s*', om.group(1)) if x.strip()]
            if len(o) >= 2:
                t, opts = 'choice', '|'.join(x[:40] for x in o[:20])
        if t == 'text':
            for tt, rx in AR_TYPES:
                if re.search(rx, label):
                    t = tt
                    break
        if len(n) == len(d) and n.find(label) >= 0:   # show the label as written (ة, أ …)
            i = n.find(label)
            label = d[i:i + len(label)]
            if opts:
                opts = '|'.join(d[n.find(o):n.find(o) + len(o)] if n.find(o) >= 0 else o for o in opts.split('|'))
        k = 'f' + str(len(fields) + 1)
        if label in seen:
            continue
        seen.add(label)
        fields.append({'key': k, 'label': label[:80], 'type': t, 'options': opts, 'required': len(fields) == 0, 'in_list': len(fields) < 5})
    approvers = []
    for w in re.split(r'\s*(?:،|,|\bثم\b|\bو)\s*', appr):
        w = w.strip(' .')
        a = AR_WHO.get(w) or next((v for k2, v in AR_WHO.items() if k2 in w), None) or (approver(w) if w else None)
        if a and a not in approvers:
            approvers.append(a)
    return {'name': name[:80], 'plural': name[:80], 'prefix': 'REC', 'fields': fields, 'approvers': approvers, 'who_create': 'all', 'who_view': 'own'}


LINK_WORDS = {'asset': 'asset', 'project': 'project', 'employee': 'employee', 'staff member': 'employee', 'branch': 'branch', 'department': 'department',
              'designation': 'designation', 'job title': 'designation'}
CHILD_TYPE_WORDS = (('date', r'\b(date|day)\b'), ('money', r'\b(amount|cost|price|fee|value|aed|total)\b'),
                    ('number', r'\b(qty|quantity|number|count|hours|days|km|litres?|liters?)\b'), ('yesno', r'\b(yes ?/ ?no)\b'), ('employee', r'\bemployee\b'))


def _expr(text):
    """v1.73.4 (A11): "quantity × unit price" → "quantity * unit_price" (the keys of the fields; the formula engine checks the names)."""
    t = str(text or '').replace('×', '*').replace('÷', '/').strip(' .')
    t = re.sub(r'(?<=\w)\s+x\s+(?=\w)', ' * ', t)

    def name(m):
        w = m.group(0).strip()
        fn = m.string[m.end():].lstrip().startswith('(')   # a function only when called: "days x daily rate" is the field "days"
        return w.upper() if fn and re.fullmatch(r'(if|max|min|sum|round|abs|today|days|months|years|sumrows|countrows|lookup|adddays|addmonths)', w, re.I) else _key(w)
    return re.sub(r'\s+', ' ', re.sub(r'[A-Za-z_][A-Za-z0-9_ ]*[A-Za-z0-9_]|[A-Za-z_]', name, t)).strip()


def _child_columns(text):
    """"item, quantity, cost" → "item:Item:text, quantity:Quantity:number, cost:Cost:money" (the child table columns)."""
    cols = []
    for part in re.split(r'\s*(?:,|;|\band\b)\s*', text):
        lab = _clean_label(part)
        if not lab:
            continue
        t = next((t for t, rx in CHILD_TYPE_WORDS if re.search(rx, part, re.I)), 'text')
        cols.append(f'{_key(lab)}:{lab}:{t}')
    return ', '.join(cols[:12])


def _block_of(raw):
    """v1.73.4 (A11): the building blocks of ZEO Developer read from the words – (type, options, extra) or None.
    "total (= quantity × unit price)", "asset (linked to the asset)", "QR code", "items (table: item, quantity, cost)",
    "litres (lookup fuel_norm.litres_100km(vehicle_type))"."""
    low = raw.lower()
    m = re.search(r'(?:\(\s*=|=|\bcalculated as\b|\bcomputed as\b|\bworked out as\b)\s*([^)]+)\)?', raw, re.I)
    if m and re.search(r'[*/+×÷-]|\bx\b', m.group(1)):
        return 'formula', _expr(m.group(1)), {'result': 'money' if re.search(r'\b(amount|cost|price|total|value|fee)\b', low) else 'number'}
    m = re.search(r'\blookup\s+([a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\([a-z][a-z0-9_]*\))', low)
    if m:
        return 'lookup', m.group(1), {}
    m = re.search(r'\((?:table|rows|lines)\s*:\s*([^)]+)\)', raw, re.I)
    if m:
        cols = _child_columns(m.group(1))
        return ('table', cols, {}) if cols else None
    m = re.search(r'\b(?:linked|link)\s+to\s+(?:the\s+|an?\s+)?([a-z ]+?)(?:\)|$|\s+record)', low)
    if m and m.group(1).strip() in LINK_WORDS:
        return 'link', LINK_WORDS[m.group(1).strip()], {}
    if re.search(r'\bqr(?: code)?\b', low):
        return 'qr', '{number}', {}
    if re.search(r'\bbar ?code\b', low):
        return 'barcode', '{number}', {}
    return None


def _clean_block_label(raw):
    """The label without the block's own words ("= a × b", "linked to …", "table: …")."""
    t = re.sub(r'\s*(?:=|\bcalculated as\b|\bcomputed as\b|\bworked out as\b).*$', '', raw, flags=re.I)
    t = re.sub(r'\s*\b(?:linked|link)\s+to\b.*$', '', t, flags=re.I)
    t = re.sub(r'\s*\blookup\b.*$', '', t, flags=re.I)
    t = re.sub(r'\s*\([^)]*$', '', t)   # "asset (linked to the asset)" → "asset" (the bracket opened before the block's words)
    return _clean_label(t)


def _type_of(label):
    low = label.lower()
    m = re.search(r'\(([^)]+)\)', label)
    if m:
        inner = m.group(1).strip()
        tl = inner.lower()
        for t, words in (('date', 'date'), ('money', 'amount money'), ('number', 'number'), ('yesno', 'yes/no yes no'), ('email', 'email e-mail'),
                         ('phone', 'phone'), ('employee', 'employee'), ('textarea', 'long text notes')):
            if tl in words.split() or tl == words:
                return t, ''
        opts = [o.strip() for o in re.split(r'\s*(?:,|\||/|\bor\b)\s*', inner) if o.strip()]
        if len(opts) >= 2:
            return 'choice', '|'.join(o[:40] for o in opts[:20])
    for t, rx in TYPE_WORDS:
        if re.search(rx, low):
            return t, ''
    return 'text', ''


def _key(label):
    return re.sub(r'[^a-z0-9]+', '_', label.lower()).strip('_')[:40] or 'field'


def _clean_label(raw):
    lab = re.sub(r'\([^)]*\)', '', raw).strip(' .:;-')
    lab = re.sub(r'^(a|an|the|and)\s+', '', lab, flags=re.I).strip()
    lab = re.sub(r'^(a|an|the)?\s*(new\s+)?(field|column)s?\s+(called\s+|named\s+)?', '', lab, flags=re.I).strip()
    return lab[:1].upper() + lab[1:80] if lab else ''


def parse(description):
    """The design read by rules: {name, plural, prefix, fields, approvers, who_create, who_view} or None."""
    d = ' '.join(str(description or '').split())
    m = re.search(r'\b(?:module|register|form|app|list)\s+(?:called|named)\s+["“]?([^"”,.]+?)["”]?(?=\s+(?:with|for|that|where)\b|[,.]|$)', d, re.I) or \
        re.search(r'\b(?:module|register|form|app)\s+for\s+(?:the\s+)?([\w\s-]{3,40}?)(?=\s+(?:with|that|where)\b|[,.]|$)', d, re.I) or \
        re.search(r'\b(?:create|make|build|add|set up|design)\s+(?:a|an|the)?\s*(?:new\s+)?(?:custom\s+)?([\w\s-]{3,40}?)\s+(?:module|register|form|app)\b', d, re.I)
    if not m:
        return None
    name = m.group(1).strip()
    name = re.sub(r'^(a|an|the)\s+', '', name, flags=re.I).strip()
    if not name:
        return None
    name = name[:1].upper() + name[1:]
    body = d[m.end():]
    appr = ''
    am = re.search(r'\b(?:approved|approval|signed off)\s+by\s+(.+?)(?:\.|$)', body, re.I)
    if am:
        appr = am.group(1)
        body = body[:am.start()]
    fm = re.search(r'\b(?:with|fields?:?|columns?:?|it has|having)\s+(?:the\s+)?(?:fields?\s*:?\s*)?(.+)$', body, re.I) or \
        re.search(r'^\s*[:–-]\s*(.+)$', body)   # v1.73.2: "Build a training request module: course name, provider, …"
    raw = fm.group(1) if fm else ''
    parts = []
    depth, cur = 0, ''
    for ch in raw:   # split on commas and "and" outside brackets
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth = max(0, depth - 1)
        if ch == ',' and depth == 0:
            parts.append(cur)
            cur = ''
        else:
            cur += ch
    parts.append(cur)
    out = []
    for p in parts:
        for q in re.split(r'\s+and\s+(?![^()]*\))', p):
            q = q.strip()
            if q:
                out.append(q)
    fields, seen = [], set()
    for raw_f in out:
        blk = _block_of(raw_f)   # v1.73.4 (A11) formula, lookup, child table, link, QR / barcode
        label = _clean_block_label(raw_f) if blk else _clean_label(raw_f)
        if not label or len(label) < 2:
            continue
        t, opts = (blk[0], blk[1]) if blk else _type_of(raw_f)
        k = _key(label)
        if k in seen:
            continue
        seen.add(k)
        req = bool(re.search(r'\b(required|mandatory|must)\b', raw_f, re.I)) and t not in ('formula', 'lookup', 'qr', 'barcode')
        label = re.sub(r'\b(required|mandatory)\b', '', label, flags=re.I).strip()
        f = {'key': k, 'label': label, 'type': t, 'options': opts, 'required': req or (len(fields) == 0 and not blk), 'in_list': len(fields) < 5}
        f.update(blk[2] if blk else {})
        fields.append(f)
    approvers = []
    for w in re.split(r'\s*(?:,|\bthen\b|\band\b|→|->|\bfollowed by\b|\bafter that\b)\s*', appr):
        w = re.sub(r'^(the|a|an|by)\s+', '', w.strip().lower()).strip(' .')
        a = approver(w)
        if a and a not in approvers:
            approvers.append(a)
    prefix = ''.join(x[0] for x in re.findall(r'[A-Za-z]+', name)).upper()[:4] or 'REC'
    if len(prefix) < 2:
        prefix = re.sub(r'[^A-Z]', '', name.upper())[:3] or 'REC'
    who_create = 'hr' if re.search(r'\b(only hr|hr only|hr adds?)\b', d, re.I) else 'all'
    who_view = 'all' if re.search(r'\b(everyone|everybody|all employees) (can|may) see\b', d, re.I) else 'own'
    return {'name': name, 'plural': name + ('s' if not name.endswith('s') else ''), 'prefix': prefix, 'fields': fields, 'approvers': approvers,
            'who_create': who_create, 'who_view': who_view}


def blueprint(approvers):
    """Draft → Submitted → (one approval step per approver) → Approved, with Reject at each step and Back to draft."""
    if not approvers:
        return {'states': [{'key': 'open', 'label': 'Open'}, {'key': 'closed', 'label': 'Closed', 'final': True}], 'start': 'open',
                'transitions': [{'key': 'close', 'label': 'Close', 'from': ['open'], 'to': 'closed', 'who': 'hr'},
                                {'key': 'reopen', 'label': 'Reopen', 'from': ['closed'], 'to': 'open', 'who': 'hr'}]}
    names = {'manager': 'line manager', 'hr': 'HR', 'admin': 'administrator', 'owner': 'requester'}
    states = [{'key': 'draft', 'label': 'Draft'}, {'key': 'submitted', 'label': 'Submitted'}]
    trans = [{'key': 'submit', 'label': 'Submit', 'from': ['draft'], 'to': 'submitted', 'who': 'owner'}]
    prev = 'submitted'
    for i, w in enumerate(approvers):
        last = i == len(approvers) - 1
        nxt = 'approved' if last else f'approved_{i + 1}'
        if not last:
            states.append({'key': nxt, 'label': f"Approved by {names.get(w, w.split(':')[-1])}"})
        trans.append({'key': f'approve_{i + 1}', 'label': f"Approve ({names.get(w, w.split(':')[-1])})", 'from': [prev], 'to': nxt, 'who': w})
        trans.append({'key': f'reject_{i + 1}', 'label': f"Reject ({names.get(w, w.split(':')[-1])})", 'from': [prev], 'to': 'rejected', 'who': w, 'note': True})
        prev = nxt
    states += [{'key': 'approved', 'label': 'Approved', 'final': True}, {'key': 'rejected', 'label': 'Rejected', 'final': True}]
    trans.append({'key': 'back_to_draft', 'label': 'Back to draft', 'from': ['rejected'], 'to': 'draft', 'who': 'owner'})
    return {'states': states, 'start': 'draft', 'transitions': trans}


def _ai_design(request, description, lang):
    from . import ai
    from .aiwork import _shield
    from .scope import ctx
    meter, sh = ai.Meter('developer'), _shield(ctx(request))
    system = ('Design a custom register for an HR / business system from the request and answer ONLY with JSON: {"name": "one record, e.g. Vehicle '
              'request", "plural": text, "prefix": "2-4 capital letters", "fields": [{"label": text, "type": one of text|textarea|number|money|date|'
              'choice|yesno|employee|email|phone|formula|lookup|table|link|qr|barcode, "options": for a choice "A|B|C"; for a formula the expression with the '
              'keys of the other fields (small letters and _, e.g. quantity * unit_price; functions IF, MAX, MIN, ROUND, SUMROWS("table key", "column"), '
              'DAYS(from, to), TODAY()); for a table the columns "key:Label:type, …" (type text|number|money|date|choice|yesno|employee); for a link one '
              'of employee|branch|department|designation|asset|project; for a lookup "rate_table.column(key field)"; for qr / barcode "{number}"; '
              'else "", "key": small letters and _, "result": number|money|date|text for a formula, "show_if": a formula or "", '
              '"required": true|false}], "reminders": [{"field": the key of a date field, "days_before": number}] (only when asked for), '
              '"approvers": ordered list, each '
              'manager (the line manager) | hr | admin | owner | the name of a role as written in the request (e.g. "procurement manager"), '
              '"who_create": all|hr|admin, "who_view": own|all|hr}. At most 25 fields. Write the name and labels in the language of the request. '
              'No other text.')
    r = ai.call(request, meter, system, [{'role': 'user', 'content': sh.text(description)}], max_tokens=1500, small=True)
    p = ai.json_of(r)
    meter.save(request.user)
    fields = []
    for f in (p.get('fields') or [])[:25]:
        lab = str(f.get('label') or '').strip()[:80]
        if lab:
            k = _key(lab)
            if k == 'field' or k in {x['key'] for x in fields}:   # v1.73.2: Arabic labels gave every field the key "field"
                k = (k if k != 'field' else 'f') + str(len(fields) + 1)
            fk = str(f.get('key') or '').strip().lower()
            if re.fullmatch(r'[a-z][a-z0-9_]{0,39}', fk) and fk not in {x['key'] for x in fields}:   # v1.73.4: formulas refer to these keys
                k = fk
            row = {'key': k, 'label': lab, 'type': f.get('type') or 'text', 'options': str(f.get('options') or ''),
                   'required': bool(f.get('required')) and f.get('type') not in ('formula', 'lookup', 'qr', 'barcode'), 'in_list': len(fields) < 5}
            for x in ('result', 'show_if', 'required_if'):   # v1.73.4 (A11)
                if f.get(x):
                    row[x] = str(f[x])[:300]
            fields.append(row)
    return {'name': str(p.get('name') or '').strip()[:80], 'plural': str(p.get('plural') or '').strip()[:80],
            'prefix': re.sub(r'[^A-Z0-9]', '', str(p.get('prefix') or '').upper())[:6] or 'REC', 'fields': fields,
            'approvers': [x for x in (approver(a) for a in (p.get('approvers') or [])) if x][:6],   # v1.73.2: groups too
            'reminders': [r for r in (p.get('reminders') or []) if isinstance(r, dict)][:5],   # v1.73.4 (A10)
            'who_create': p.get('who_create') if p.get('who_create') in ('all', 'hr', 'admin') else 'all',
            'who_view': p.get('who_view') if p.get('who_view') in ('own', 'all', 'hr') else 'own'}


def _design_card(body, lang):
    L = lang
    types = {'text': 'Text', 'textarea': 'Long text', 'number': 'Number', 'money': 'Amount', 'date': 'Date', 'choice': 'Choice', 'yesno': 'Yes / no',
             'employee': 'Employee', 'email': 'E-mail', 'phone': 'Phone', 'formula': 'Calculated', 'lookup': 'From a rate table', 'table': 'Table of rows',
             'link': 'Link to a record', 'qr': 'QR code', 'barcode': 'Barcode'}   # v1.73.4 (A11)
    rows = [[f['label'], tr(types.get(f['type'], f['type']), lang=L) + (f" ({f['options'].replace('|', ', ')})" if f.get('options') and f['type'] not in ('qr', 'barcode') else ''),
             tr('Yes', lang=L) if f.get('required') else '', tr('Yes', lang=L) if f.get('in_list') else ''] for f in body['fields']]
    bp = body['blueprint']
    st = {s['key']: s['label'] for s in bp['states']}
    who = {'owner': tr('Requester', lang=L), 'manager': tr('Line manager', lang=L), 'hr': 'HR', 'admin': tr('Administrator', lang=L)}
    flow = ' → '.join(s['label'] for s in bp['states'] if s['key'] != 'rejected')
    return [{'type': 'table', 'title': tr('Fields of {0}', body['name'], lang=L), 'columns': [tr('Field', lang=L), tr('Type', lang=L), tr('Required', lang=L),
                                                                                               tr('In the list', lang=L)], 'rows': rows},
            {'type': 'table', 'title': tr('Blueprint (states and moves)', lang=L), 'columns': [tr('Move', lang=L), tr('From', lang=L), tr('To', lang=L), tr('Who', lang=L)],
             'rows': [[t['label'], ', '.join(st.get(x, x) for x in t['from']), st.get(t['to'], t['to']), who.get(t['who'], t['who'].split(':')[-1])] for t in bp['transitions']]},
            {'type': 'checks', 'title': tr('Flow', lang=L), 'items': [{'level': 'info', 'text': flow, 'link': ''}]}]


def _check(body):
    """ZeoFlow's own checks on the design (the same as the Custom modules screen) – raises CustomError."""
    from ZeoFlow import custom as CU
    body['fields'] = CU.clean_fields(body['fields'])
    body['blueprint'] = CU.clean_blueprint(body['blueprint'], {f['key'] for f in body['fields']})
    return body


def build(request, c, conv, question, lang):
    """A new custom module from a description (preview)."""
    L = lang
    if not c.admin:
        return {'text': tr('Only a company administrator can make custom modules.', lang=L), 'source': 'check', 'intent': 'build_module'}
    from ZeoFlow.custom import CustomError
    from ZeoFlow.models import CustomModule
    mode, d = 'builtin', None
    from . import ai
    if ai.ready(request):
        try:
            d = _ai_design(request, question, L)
            mode = 'ai'
        except ai.Unavailable:
            d = None
    if not d or not d.get('name') or not d.get('fields'):
        from .words import has_arabic
        d = (parse_ar(question) if has_arabic(question) else None) or parse(question)
        mode = 'builtin'
    if not d or not d.get('fields'):
        return {'text': tr('Say the module and its fields, for example: "Create a vehicle request module with vehicle type (car, van or bus), from date, '
                           'to date, destination and purpose, approved by the line manager then HR".', lang=L), 'source': 'help', 'intent': 'build_module',
                'links': [{'label': tr('Settings › Custom modules', lang=L), 'url': LINK}]}
    return _module_preview(request, conv, d, question, L, mode)


def _module_preview(request, conv, d, description, lang, mode, intent='build_module', extra_cards=None):
    """The preview of a new module from a design (sentence or SOP) – with its reminders as daily ZEO Developer scripts (v1.73.4)."""
    L = lang
    from ZeoFlow.custom import CustomError
    from ZeoFlow.models import CustomModule
    question = description
    key = _key(d['name'])
    if CustomModule.objects.filter(key=key).exists():
        return {'text': tr('A module "{0}" exists already – ask to add a field to it, or change it in Settings › Custom modules.', d['name'], lang=L),
                'source': 'check', 'intent': 'build_module', 'links': [{'label': tr('Settings › Custom modules', lang=L), 'url': LINK}]}
    body = {'key': key, 'name': d['name'], 'plural': d.get('plural') or d['name'] + 's', 'prefix': d.get('prefix') or 'REC', 'icon': 'widgets',
            'description': question[:300], 'fields': d['fields'], 'blueprint': blueprint(d.get('approvers') or []),
            'who_create': d.get('who_create') or 'all', 'who_view': d.get('who_view') or 'own'}
    try:
        body = _check(body)
    except CustomError as e:
        return {'text': tr('The design is not complete: {0}', str(e), lang=L), 'source': 'check', 'intent': 'build_module'}
    steps = [{'method': 'post', 'path': '/flow/api/custom-modules/', 'data': body, 'label': tr('Make the module {0}', body['name'], lang=L),
              'show': f"{len(body['fields'])} fields, {len(body['blueprint']['states'])} states",
              'undo': {'method': 'delete', 'path': f"/flow/api/custom-modules/{key}/"}}]
    rem_steps, rem_note = reminder_steps(body, d.get('reminders') or [], L, offset=len(steps))   # v1.73.4 (A10) reminders before a date
    steps += rem_steps
    from ZeoDeveloper.models import DeveloperSettings
    dev_on = DeveloperSettings.get().developer_mode
    html = print_layout_html(body)
    if dev_on:   # v1.73.5: the module's print layout (Developer › Print layouts) – made with it, removed by Undo
        ref = '{id%d}' % len(steps)
        steps.append({'method': 'post', 'path': '/developer/api/prints/', 'label': tr('Print layout {0}', body['name'], lang=L),
                      'data': {'module_key': body['key'], 'name': body['name'], 'html': html, 'paper': 'A4'}, 'show': 'A4',
                      'undo': {'method': 'delete', 'path': f'/developer/api/prints/{ref}/'}})
    ch = changes.preview(request, conv, 'module', tr('New module: {0}', body['name'], lang=L),
                         {'api': steps},
                         tr('Made as a custom module (Settings › Custom modules): records numbered {0}-0001 …; the Blueprint moves records from state to state. '
                            'Undo deletes the module while it has no records.', body['prefix'], lang=L), group='setup')
    missing = [w[6:] for w in (d.get('approvers') or []) if group_missing(w)]
    cards = (extra_cards or []) + _design_card(body, L) + [settings_card(body, dev_on, L)]
    test_ch = test_company_change(request, conv, body, steps, html, L)   # v1.73.5: try it in the test company first
    if rem_note:
        cards[-2]['items'].append({'level': 'warning' if not rem_steps else 'info', 'text': rem_note, 'link': MS + 'developer' if not rem_steps else ''})
    txt = tr('I designed **{0}**: {1} field(s) and the flow {2}. Check the preview – nothing is made before you confirm.', body['name'], len(body['fields']),
             ' → '.join(s['label'] for s in body['blueprint']['states'] if s['key'] != 'rejected'), lang=L)
    if missing:   # v1.73.2: say who cannot approve yet instead of dropping the step
        txt += '\n\n' + tr('No permission group yet for: {0}. Until it exists and has members, only an administrator can make that approval – '
                            'add it in Settings › Permission groups.', ', '.join(missing), lang=L)
        cards[-2]['items'].append({'level': 'warning', 'text': tr('Make the permission group(s) {0} and add the approvers.', ', '.join(missing), lang=L),
                                   'link': MS + 'settings/user-grouping-master'})
    return {'text': txt, 'source': 'check', 'intent': intent, 'mode': mode, 'usage_logged': mode == 'ai',
            'cards': cards + ([changes.card(test_ch, L)] if test_ch else []) + [changes.card(ch, L)],
            'links': [{'label': tr('Settings › Custom modules', lang=L), 'url': LINK}]}


def add_field(request, c, conv, question, lang):
    """"Add a field X (type) to the <module> module" – a preview of the changed design."""
    L = lang
    if not c.admin:
        return {'text': tr('Only a company administrator can make custom modules.', lang=L), 'source': 'check', 'intent': 'module_field'}
    from ZeoFlow.custom import CustomError, module_json
    from ZeoFlow.models import CustomModule
    m = re.search(r'\badd (?:a |an |the )?(?:new )?(?:field|column)s?\s+["“]?(.+?)["”]?\s+(?:to|in|on)\s+(?:the\s+)?(.+?)(?:\s+(?:module|register|form))?\s*[.?]?$', question, re.I)
    if not m:
        return None
    raw, mod_name = m.group(1), m.group(2).strip()
    mod = CustomModule.objects.filter(name__iexact=mod_name).first() or CustomModule.objects.filter(plural__iexact=mod_name).first() or \
        CustomModule.objects.filter(key=_key(mod_name)).first()
    if mod is None:
        return {'text': tr('No custom module "{0}".', mod_name, lang=L), 'source': 'check', 'intent': 'module_field',
                'links': [{'label': tr('Settings › Custom modules', lang=L), 'url': LINK}]}
    old = module_json(mod)
    new_fields = copy.deepcopy(mod.fields)
    for part in re.split(r'\s*,\s*|\s+and\s+(?![^()]*\))', raw):
        label = _clean_label(part)
        if not label:
            continue
        t, opts = _type_of(part)
        k = _key(label)
        if k == 'field' or k in {x['key'] for x in new_fields}:   # v1.73.2: Arabic labels
            k = 'f' + str(len(new_fields) + 1) + '_' + str(abs(hash(label)) % 1000)
        new_fields.append({'key': k, 'label': label, 'type': t, 'options': opts, 'required': bool(re.search(r'\b(required|mandatory)\b', part, re.I)),
                           'in_list': False})
    body = {'name': mod.name, 'plural': mod.plural, 'prefix': mod.prefix, 'icon': mod.icon, 'description': mod.description, 'fields': new_fields,
            'blueprint': mod.blueprint, 'who_create': mod.who_create, 'who_view': mod.who_view}
    try:
        body = _check(body)
    except CustomError as e:
        return {'text': tr('The design is not complete: {0}', str(e), lang=L), 'source': 'check', 'intent': 'module_field'}
    undo = {k: old[k] for k in ('name', 'plural', 'prefix', 'icon', 'description', 'fields', 'blueprint', 'who_create', 'who_view')}
    added = [f for f in body['fields'] if f['key'] not in {x['key'] for x in mod.fields}]
    ch = changes.preview(request, conv, 'module', tr('{0}: {1} new field(s)', mod.name, len(added), lang=L),
                         {'api': [{'method': 'put', 'path': f'/flow/api/custom-modules/{mod.key}/', 'data': body, 'label': tr('Change the module {0}', mod.name, lang=L),
                                   'show': ', '.join(f"{f['label']} ({f['type']})" for f in added),
                                   'undo': {'method': 'put', 'path': f'/flow/api/custom-modules/{mod.key}/', 'data': undo}}]},
                         tr('Existing records keep their data; the new fields are empty on them. Undo puts the old design back.', lang=L), group='setup')
    return {'text': tr('{0} new field(s) for **{1}**: {2}. Check the preview – nothing changes before you confirm.', len(added), mod.name,
                       ', '.join(f['label'] for f in added), lang=L),
            'source': 'check', 'intent': 'module_field', 'cards': [changes.card(ch, L)], 'links': [{'label': tr('Settings › Custom modules', lang=L), 'url': LINK}]}


# ------------------------------------------------------------------ v1.73.4 (A10) the app from an SOP; reminders
REMINDER_CODE = """# Made by Ask ZEO from the module design: reminder {days} day(s) before "{label}".
day = add_days(today(), {days})
for r in records.search("{module}", {{"{field}": day, "state__ne": "rejected"}}, limit=500):
    if r.owner_id:
        notify(r.owner_id, "{name} " + str(r.number), "{label}: " + str(r.get("{field}")) + " – in {days} day(s).")
"""


def reminder_steps(body, reminders, lang, offset=1):
    """Each reminder → a daily ZEO Developer script (made and switched on with the module; Undo switches it off and deletes it).
    Returns (api steps, note). Without Developer mode: no steps and a note."""
    dates = {f['key']: f['label'] for f in body['fields'] if f['type'] == 'date'}
    rem = [r for r in reminders if str(r.get('field') or '') in dates][:5]
    if not rem:
        return [], ''
    from ZeoDeveloper.models import DeveloperSettings
    if not DeveloperSettings.get().developer_mode:
        return [], tr('The reminders need Developer mode (Settings › Developer mode) – switch it on and ask again, or add them in Developer › Scripts.', lang=lang)
    steps = []
    for n, r in enumerate(rem):
        days = max(0, min(365, int(r.get('days_before') or 0)))
        code = REMINDER_CODE.format(days=days, label=dates[r['field']].replace('"', "'"), module=body['key'], field=r['field'],
                                    name=body['name'].replace('"', "'"))
        skey = f"{body['key']}_rem_{r['field']}"[:60]
        ref = '{id%d}' % (offset + len(steps))   # the id the POST answered with (changes._fill)
        steps.append({'method': 'post', 'path': '/developer/api/scripts/', 'label': tr('Reminder {0} day(s) before {1}', days, dates[r['field']], lang=lang),
                      'data': {'name': f"{body['name']} – reminder {dates[r['field']]}"[:120], 'key': skey, 'trigger': 'daily', 'hour': 7, 'code': code,
                               'description': 'Made by Ask ZEO with the module', 'module_key': body['key']},
                      'show': tr('Every day at 07:00', lang=lang),
                      'undo': {'method': 'delete', 'path': f'/developer/api/scripts/{ref}/'}})
        steps.append({'method': 'put', 'path': f'/developer/api/scripts/{ref}/', 'label': tr('Switch the reminder on', lang=lang), 'data': {'active': True},
                      'show': '', 'undo': {'method': 'put', 'path': f'/developer/api/scripts/{ref}/', 'data': {'active': False}}})
    return steps, tr('{0} reminder(s) as daily scripts in Developer › Scripts – sent to the person who made the record.', len(rem), lang=lang)


def _speech(path, request):
    """v1.73.5: the spoken words of a video – a speech engine on the server (faster-whisper, model ZEO_WHISPER_MODEL, e.g. "small")
    when installed, else the company's AI provider when it offers speech to text. ('', note) when neither can."""
    import os
    import shutil
    import subprocess
    import tempfile
    exe = shutil.which('ffmpeg')
    if not exe:
        return '', 'no ffmpeg'
    with tempfile.TemporaryDirectory() as d:
        mp3 = os.path.join(d, 'a.mp3')
        try:
            subprocess.run([exe, '-loglevel', 'error', '-i', path, '-vn', '-ac', '1', '-ar', '16000', '-b:a', '48k', '-t', '900', mp3],
                           timeout=300, check=False, capture_output=True)
        except Exception:
            return '', 'no audio'
        if not os.path.exists(mp3) or os.path.getsize(mp3) < 2000:
            return '', 'no audio'
        model = os.environ.get('ZEO_WHISPER_MODEL', '').strip()
        if model:
            try:
                from faster_whisper import WhisperModel
                segs, _info = WhisperModel(model, device='cpu', compute_type='int8').transcribe(mp3)
                return ' '.join(x.text.strip() for x in segs).strip(), 'server'
            except Exception as e:   # noqa: BLE001 – fall back to the provider
                import logging
                logging.getLogger(__name__).info('local speech engine not used: %s', e)
        if request is None:
            return '', 'no engine'
        from AISettings.service import ExternalUnavailable, transcribe
        try:
            with open(mp3, 'rb') as f:
                return transcribe(request, f.read(), 'sop.mp3'), 'ai'
        except ExternalUnavailable as e:
            return '', str(e)


def _video_text(u, request=None):
    """A filmed SOP: the spoken words (v1.73.5, see _speech) and the text shown on screen (one frame every 5 seconds, at most 60,
    read with the OCR)."""
    import os
    import shutil
    import subprocess
    import tempfile
    from AISettings.docread import _ocr_image
    exe = shutil.which('ffmpeg')
    if not exe:
        return ''
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, 'v' + os.path.splitext(u.name)[1].lower())
        with u.file.open('rb') as f, open(src, 'wb') as o:
            o.write(f.read())
        try:
            subprocess.run([exe, '-loglevel', 'error', '-i', src, '-vf', 'fps=1/5', '-frames:v', '60', os.path.join(d, 'f%03d.png')],
                           timeout=180, check=False, capture_output=True)
        except Exception:
            return ''
        spoken, how = _speech(src, request)
        u._speech = (bool(spoken), how)   # for the "Read from" card
        seen, out = set(), (['Spoken words:', spoken, '', 'Shown on screen:'] if spoken else [])
        for fn in sorted(x for x in os.listdir(d) if x.startswith('f') and x.endswith('.png')):
            with open(os.path.join(d, fn), 'rb') as fh:
                for line in _ocr_image(fh.read()).splitlines():
                    k = ' '.join(line.split()).lower()
                    if len(k) > 3 and k not in seen:
                        seen.add(k)
                        out.append(line.strip())
        return '\n'.join(out)


def sop_text(u, request=None):
    if u.kind == 'video':
        return _video_text(u, request)
    from .aiwork import _file_text
    return _file_text(u)


def from_sop(request, c, conv, question, uploads, lang):
    """v1.73.4 (A10): "make an app from this SOP" – the AI reads the SOP (PDF, Word, text, photo or the text shown in a video) and
    designs the custom module: fields (with the ZEO Developer building blocks), the approval Blueprint and reminders. The same
    preview, confirm and undo as a module from a sentence."""
    L = lang
    if not c.admin:
        return {'text': tr('Only a company administrator can make custom modules.', lang=L), 'source': 'check', 'intent': 'sop_app'}
    u = next((x for x in uploads if x.kind in ('document', 'image', 'video')), None)
    if u is None:
        return {'text': tr('Attach the SOP (PDF, Word, text, a photo or a short video) with 📎.', lang=L), 'source': 'check', 'intent': 'sop_app'}
    text = sop_text(u, request)
    if len(text.strip()) < 40:
        return {'text': tr('No text could be read from {0}.', u.name, lang=L)
                + (' ' + tr('Nothing was read from the video – neither spoken words nor text on screen. Attach the SOP document instead.', lang=L)
                   if u.kind == 'video' else ''), 'source': 'check', 'intent': 'sop_app'}
    from . import ai
    try:
        d = _ai_design(request, (question or '') + '\n\nThe SOP:\n' + text[:15000], L)
    except ai.Unavailable as e:
        return {'text': tr('The app could not be designed from the SOP: {0}', str(e), lang=L), 'source': 'check', 'intent': 'sop_app'}
    if not d.get('name') or not d.get('fields'):
        return {'text': tr('The AI did not find a register in {0} – describe the module in a sentence instead.', u.name, lang=L),
                'source': 'check', 'intent': 'sop_app'}
    note = [{'type': 'checks', 'title': tr('Read from {0}', u.name, lang=L),
             'items': [{'level': 'info', 'text': tr('{0} characters read ({1}).', len(text), tr({'video': 'text shown in the video', 'image': 'photo'}.get(u.kind, 'document'), lang=L), lang=L), 'link': ''}]}]
    sp = getattr(u, '_speech', None)
    if sp is not None:   # v1.73.5: say whether the spoken words were read
        note[0]['items'].append({'level': 'info' if sp[0] else 'warning', 'link': '',
                                 'text': tr('The spoken words were read ({0}).', tr({'server': 'speech engine on the server', 'ai': "the company's AI"}.get(sp[1], sp[1]), lang=L), lang=L)
                                 if sp[0] else tr('The spoken words were not read: {0}. Only the text shown on screen was used.', tr(sp[1], lang=L), lang=L)})
    return _module_preview(request, conv, d, f'From {u.name}: ' + (question or '')[:200], L, 'ai', intent='sop_app', extra_cards=note)


# ------------------------------------------------------------------ v1.73.5 print layout, settings, the test company first
def print_layout_html(body):
    """A clean A4 layout of a record of the module (ZEO Developer print layouts: {{ field }}, {% rows table %} … {% endrows %})."""
    import html as H
    rows, tables = [], []
    for f in body['fields']:
        if f['type'] == 'table':
            cols = [c.split(':') for c in str(f.get('options') or '').split(',') if c.strip()]
            cols = [(c[0].strip(), (c[1].strip() if len(c) > 1 else c[0].strip())) for c in cols]
            head = ''.join(f'<th>{H.escape(lab)}</th>' for _k, lab in cols)
            cells = ''.join('<td>{{ row.%s }}</td>' % k for k, _lab in cols)
            tables.append(f'<h3>{H.escape(f["label"])}</h3><table class="t"><tr>{head}</tr>{{% rows {f["key"]} %}}<tr>{cells}</tr>{{% endrows %}}</table>')
        elif f['type'] in ('qr', 'barcode'):
            rows.append(f'<tr><th>{H.escape(f["label"])}</th><td>{{{{ {f["key"]} }}}}</td></tr>')
        else:
            v = '{{ %s.label }}' % f['key'] if f['type'] == 'link' else '{{ %s }}' % f['key']
            rows.append(f'<tr><th>{H.escape(f["label"])}</th><td>{v}</td></tr>')
    return ('<style>body{font-family:Arial,sans-serif;font-size:12px}h1{font-size:18px;margin:0 0 4px}.m{color:#555;margin-bottom:12px}'
            'table{border-collapse:collapse;width:100%;margin-bottom:12px}th,td{border:1px solid #ccc;padding:5px 7px;text-align:left;vertical-align:top}'
            'th{background:#f3f4f6;width:32%}.t th{width:auto}.s{margin-top:40px;display:flex;justify-content:space-between}</style>'
            f'<h1>{H.escape(body["name"])} {{{{ number }}}}</h1><div class="m">{{{{ company }}}} · {{{{ state }}}} · {{{{ today }}}}</div>'
            f'<table>{"".join(rows)}</table>{"".join(tables)}<div class="s"><span>______________________</span><span>______________________</span></div>')


def settings_card(body, dev_on, lang):
    """The module's settings in the preview: numbering, who may add, who may see, the print layout."""
    L = lang
    who_c = {'all': tr('Everyone', lang=L), 'hr': 'HR', 'admin': tr('Administrator', lang=L)}
    who_v = {'own': tr('Own records (and their team)', lang=L), 'all': tr('Everyone', lang=L), 'hr': 'HR'}
    return {'type': 'table', 'title': tr('Settings of {0}', body['name'], lang=L), 'columns': [tr('Setting', lang=L), tr('Value', lang=L)],
            'rows': [[tr('Numbering', lang=L), f"{body['prefix']}-0001"], [tr('Who may add', lang=L), who_c.get(body.get('who_create'), body.get('who_create'))],
                     [tr('Who may see', lang=L), who_v.get(body.get('who_view'), body.get('who_view'))],
                     [tr('Print layout', lang=L), tr('A4, with every field and table', lang=L) if dev_on else
                      tr('Needs Developer mode (Settings › Developer mode)', lang=L)]]}


def _test_company():
    try:
        from ZeoAdmin.models import TestCompany
        return TestCompany.objects.filter(state='done').exclude(schema_name='').order_by('-at').first()
    except Exception:   # noqa: BLE001
        return None


def package_of(body, steps):
    """The design as a ZEO Developer package (packages.apply): the module, its reminder scripts and its print layout."""
    pkg = {'format': 'zeo-developer-package', 'format_version': 1, 'api_version': 'v1',
           'modules': [{k: body.get(k) for k in ('key', 'name', 'plural', 'icon', 'description', 'prefix', 'fields', 'who_create', 'who_view', 'blueprint')}],
           'rate_tables': [], 'scripts': [], 'reports': [], 'prints': [], 'scheduled': []}
    for st in steps:
        if st['path'] == '/developer/api/scripts/' and st['method'] == 'post':
            pkg['scripts'].append({k: st['data'].get(k) for k in ('key', 'name', 'description', 'module_key', 'trigger', 'hour', 'code')})
        if st['path'] == '/developer/api/prints/' and st['method'] == 'post':
            pkg['prints'].append({k: st['data'].get(k) for k in ('module_key', 'name', 'html', 'paper')})
    return pkg


def test_company_change(request, conv, body, steps, html, lang):
    """When the company has a test company (Settings › Test company): a second preview that makes the module there first. Its
    reminder scripts arrive switched off (the test company sends no e-mail anyway). Undo removes it from the test company."""
    t = _test_company()
    if t is None:
        return None
    pkg = package_of(body, steps)
    if not pkg['prints']:
        pkg['prints'] = [{'module_key': body['key'], 'name': body['name'], 'html': html, 'paper': 'A4'}]
    return changes.preview(request, conv, 'module', tr('In the test company {0}: {1}', t.name, body['name'], lang=lang),
                           {'api': [{'method': 'post', 'path': '/ask-zeo/api/test-company/', 'data': {'package': pkg},
                                     'label': tr('Make {0} in the test company {1}', body['name'], t.name, lang=lang),
                                     'show': tr('to try it before the live company', lang=lang),
                                     'undo': {'method': 'delete', 'path': f"/ask-zeo/api/test-company/{body['key']}/"}}]},
                           tr('Try it in the test company (switch the company in the top bar). When it works, confirm the other preview to make it '
                              'here – or export it from the test company in Developer › Packages.', lang=lang), group='setup')
