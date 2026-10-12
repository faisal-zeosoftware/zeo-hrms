#!/usr/bin/env python3
"""v1.16.0 – build the field help catalogue i18n/field_help.json (English + Arabic) for EVERY form field of ZEO HRMS.

    python tools/fieldhelp/build_catalogue.py [--frontend <hrms-project folder>] [--out i18n/field_help.json]

Plain Python (no Django). Steps:
 1. extract_fields.collect(): every template / config / server-form field with its label, control name, type, screen;
 2. kb.match(): the knowledge-base entry (meaning, format, example, rules, UAE note; English + Arabic; country variants);
 3. model rules from the backend models (ast): max length, required, choices – by API field name;
 4. a field without a knowledge-base entry gets the generic help of its type, worded with its own label
    ("Enter the Visa sponsor." / "أدخل كفيل التأشيرة.") – so no field is left without help;
 5. Arabic labels come from i18n/packs/ar.json.
Run it again after adding screens; admins' edits (Settings → Field help) are stored separately and are never overwritten.
"""
import argparse
import ast
import datetime
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import kb  # noqa: E402
from extract_fields import FRONTEND_GUESSES, collect, help_key, name_key  # noqa: E402

# generic help by field type ({0} = the field's own label)
GENERIC = {
    'text': ({'w': 'Enter the {0}.', 'f': 'Text.', 'x': '', 'r': '', 'u': ''}, {'w': 'أدخل {0}.', 'f': 'نص.', 'x': '', 'r': '', 'u': ''}),
    'textarea': ({'w': 'Describe the {0} in a few words or sentences.', 'f': 'Free text, several lines allowed.', 'x': '', 'r': '', 'u': ''},
                 {'w': 'اكتب {0} في بضع كلمات أو جمل.', 'f': 'نص حر، يمكن كتابة عدة أسطر.', 'x': '', 'r': '', 'u': ''}),
    'number': ({'w': 'Enter the {0} as a number.', 'f': 'Digits; use a dot for decimals.', 'x': '12.5', 'r': '', 'u': ''},
               {'w': 'أدخل {0} كرقم.', 'f': 'أرقام؛ استخدم النقطة للكسور العشرية.', 'x': '12.5', 'r': '', 'u': ''}),
    'date': ({'w': 'Pick the {0}.', 'f': 'A date (day / month / year) – type it or use the calendar.', 'x': '10/10/2026', 'r': '', 'u': ''},
             {'w': 'اختر {0}.', 'f': 'تاريخ (يوم / شهر / سنة) – اكتبه أو استخدم التقويم.', 'x': '10/10/2026', 'r': '', 'u': ''}),
    'datetime': ({'w': 'Pick the {0}.', 'f': 'Date and time.', 'x': '10/10/2026 09:30', 'r': '', 'u': ''}, {'w': 'اختر {0}.', 'f': 'التاريخ والوقت.', 'x': '10/10/2026 09:30', 'r': '', 'u': ''}),
    'time': ({'w': 'Enter the {0}.', 'f': 'HH:MM (24-hour clock).', 'x': '08:30', 'r': '', 'u': ''}, {'w': 'أدخل {0}.', 'f': 'HH:MM (نظام 24 ساعة).', 'x': '08:30', 'r': '', 'u': ''}),
    'month': ({'w': 'Pick the {0}.', 'f': 'Month and year.', 'x': '10/2026', 'r': '', 'u': ''}, {'w': 'اختر {0}.', 'f': 'الشهر والسنة.', 'x': '10/2026', 'r': '', 'u': ''}),
    'email': ({'w': 'Enter the {0}.', 'f': 'name@domain', 'x': 'name@company.ae', 'r': 'Must be a valid e-mail address.', 'u': ''},
              {'w': 'أدخل {0}.', 'f': 'name@domain', 'x': 'name@company.ae', 'r': 'يجب أن يكون بريدًا إلكترونيًا صحيحًا.', 'u': ''}),
    'tel': ({'w': 'Enter the {0}.', 'f': 'International format: +971 5X XXX XXXX.', 'x': '+971 50 123 4567', 'r': '7 to 15 digits.', 'u': ''},
            {'w': 'أدخل {0}.', 'f': 'الصيغة الدولية: ‎+971 5X XXX XXXX', 'x': '+971 50 123 4567', 'r': 'من 7 إلى 15 رقمًا.', 'u': ''}),
    'url': ({'w': 'Enter the {0}.', 'f': 'A web address.', 'x': 'https://www.company.ae', 'r': '', 'u': ''}, {'w': 'أدخل {0}.', 'f': 'عنوان موقع إلكتروني.', 'x': 'https://www.company.ae', 'r': '', 'u': ''}),
    'password': ({'w': 'Enter the {0}.', 'f': 'At least 8 characters.', 'x': '', 'r': 'Never share passwords.', 'u': ''}, {'w': 'أدخل {0}.', 'f': '8 أحرف على الأقل.', 'x': '', 'r': 'لا تشارك كلمات المرور.', 'u': ''}),
    'select': ({'w': 'Choose the {0} from the list.', 'f': 'Pick one option.', 'x': '', 'r': '', 'u': ''}, {'w': 'اختر {0} من القائمة.', 'f': 'اختر خيارًا واحدًا.', 'x': '', 'r': '', 'u': ''}),
    'multiselect': ({'w': 'Choose one or more {0}.', 'f': 'Pick several options.', 'x': '', 'r': '', 'u': ''}, {'w': 'اختر واحدًا أو أكثر من {0}.', 'f': 'اختر عدة خيارات.', 'x': '', 'r': '', 'u': ''}),
    'checkbox': ({'w': 'Tick to switch on “{0}”.', 'f': 'Tick box (ticked = yes).', 'x': '', 'r': '', 'u': ''}, {'w': 'اختر لتفعيل «{0}».', 'f': 'مربع اختيار (محدد = نعم).', 'x': '', 'r': '', 'u': ''}),
    'radio': ({'w': 'Choose one {0}.', 'f': 'Pick one of the options shown.', 'x': '', 'r': '', 'u': ''}, {'w': 'اختر {0}.', 'f': 'اختر أحد الخيارات الظاهرة.', 'x': '', 'r': '', 'u': ''}),
    'file': ({'w': 'Attach the {0}.', 'f': 'PDF, image, Word or Excel file.', 'x': 'document.pdf', 'r': 'Up to 25 MB per file.', 'u': ''},
             {'w': 'أرفق {0}.', 'f': 'ملف PDF أو صورة أو Word أو Excel.', 'x': 'document.pdf', 'r': 'حتى 25 ميغابايت للملف.', 'u': ''}),
    'search': ({'w': 'Type to filter the list by {0}.', 'f': 'Any text.', 'x': '', 'r': '', 'u': ''}, {'w': 'اكتب لتصفية القائمة حسب {0}.', 'f': 'أي نص.', 'x': '', 'r': '', 'u': ''}),
    'color': ({'w': 'Pick the {0}.', 'f': 'A colour.', 'x': '#0F766E', 'r': '', 'u': ''}, {'w': 'اختر {0}.', 'f': 'لون.', 'x': '#0F766E', 'r': '', 'u': ''}),
    'range': ({'w': 'Set the {0}.', 'f': 'Drag the slider.', 'x': '', 'r': '', 'u': ''}, {'w': 'اضبط {0}.', 'f': 'اسحب المؤشر.', 'x': '', 'r': '', 'u': ''}),
}
RIGHTS_SCREENS = ('user-role-grouping-create', 'user-grouping-edit', 'permission-assigned', 'branch-permissions', 'user-details', 'user-edit')
KB_BY_ID = {r['id']: r for r in kb.KB}
TYPE_ALIASES = {'datetime-local': 'datetime', 'integer': 'number', 'decimal': 'number', 'currency': 'number', 'percent': 'number', 'money': 'number',
                'rating': 'number', 'phone': 'tel', 'bool': 'checkbox', 'json': 'textarea', 'chips': 'multiselect', 'fk': 'select', 'choice': 'select',
                'dropdown': 'select', 'mat-select': 'select', 'dynamic': 'text', 'mat-checkbox': 'checkbox', '': 'text', 'week': 'date'}
# texts of the help panel itself (English → Arabic) – also listed in NEW_TERMS.txt
UI = {
    'Field help': 'مساعدة الحقل', 'What it is': 'ما هو', 'Format': 'الصيغة', 'Example': 'مثال', 'Rules': 'القواعد', 'UAE note': 'ملاحظة للإمارات',
    'Country note': 'ملاحظة الدولة', 'Required': 'إلزامي', 'Optional': 'اختياري', 'Up to {0} characters': 'حتى {0} حرفًا', 'At least {0} characters': '{0} أحرف على الأقل',
    'Lowest: {0}': 'الحد الأدنى: {0}', 'Highest: {0}': 'الحد الأعلى: {0}', 'Allowed values: {0}': 'القيم المسموحة: {0}', 'Pattern: {0}': 'النمط: {0}',
    'Turn off field help': 'إيقاف مساعدة الحقول', 'Close help': 'إغلاق المساعدة', 'Show help for this field': 'عرض المساعدة لهذا الحقل',
    'Field help is off. Turn it on again in Settings → Field help.': 'مساعدة الحقول متوقفة. أعد تشغيلها من الإعدادات ← مساعدة الحقول.',
    'and {0} more': 'و{0} أخرى',
}


def model_rules(repo):
    """{field name: {'max': n, 'req': bool, 'choices': [labels]}} from every models.py (ast; no Django)."""
    out = {}
    for app in sorted(os.listdir(repo)):
        p = os.path.join(repo, app, 'models.py')
        if not os.path.isfile(p):
            continue
        try:
            tree = ast.parse(open(p, encoding='utf-8').read())
        except SyntaxError:
            continue
        consts = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                try:
                    consts[node.targets[0].id] = ast.literal_eval(node.value)
                except Exception:
                    pass
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            for node in cls.body:
                if not (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Call)):
                    continue
                fn = node.value.func
                kind = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, 'id', '')
                if not kind.endswith('Field') or kind in ('ManyToManyField',):
                    continue
                name = node.targets[0].id
                kw = {k.arg: k.value for k in node.value.keywords if k.arg}
                info = {}
                if 'max_length' in kw:
                    try:
                        info['max'] = int(ast.literal_eval(kw['max_length']))
                    except Exception:
                        pass
                blank = null = False
                try:
                    blank = bool(ast.literal_eval(kw['blank'])) if 'blank' in kw else False
                    null = bool(ast.literal_eval(kw['null'])) if 'null' in kw else False
                except Exception:
                    pass
                has_default = 'default' in kw
                if kind not in ('BooleanField', 'AutoField', 'BigAutoField') and not blank and not null and not has_default and 'auto_now' not in kw and 'auto_now_add' not in kw:
                    info['req'] = True
                if 'choices' in kw:
                    ch = None
                    c = kw['choices']
                    try:
                        ch = ast.literal_eval(c)
                    except Exception:
                        if isinstance(c, ast.Name):
                            ch = consts.get(c.id)
                        elif isinstance(c, ast.Attribute):
                            ch = None
                    if isinstance(ch, (list, tuple)) and ch and all(isinstance(x, (list, tuple)) and len(x) == 2 for x in ch):
                        info['choices'] = [str(lab) for _, lab in ch][:20]
                if info:
                    cur = out.setdefault(name, {})
                    for k, v in info.items():
                        cur.setdefault(k, v)
    return out


def ar_label(terms, label):
    if not label:
        return ''
    for cand in (label, label[:1].upper() + label[1:], label.capitalize(), label.title(), label.rstrip('.:*').strip()):
        if cand in terms and terms[cand]:
            return terms[cand]
    return ''


def ftype(f):
    t = (f.get('type') or 'text').lower()
    if f.get('tag') in ('select', 'mat-select'):
        t = 'select'
    if f.get('tag') == 'textarea':
        t = 'textarea'
    if f.get('tag') == 'mat-checkbox':
        t = 'checkbox'
    t = TYPE_ALIASES.get(t, t)
    return t if t in GENERIC else 'text'


def build(front, repo=REPO):
    fields = collect(front, repo)
    terms = {}
    try:
        terms = json.load(open(os.path.join(repo, 'i18n', 'packs', 'ar.json'), encoding='utf-8')).get('terms', {})
    except Exception:
        pass
    mrules = model_rules(repo)
    used_kb = set()
    entries, names = {}, {}
    total = specific = 0
    for f in fields:
        if f.get('dynamic') or not f.get('key'):
            continue
        t = ftype(f)
        lk = f['key'][6:] if f['key'].startswith('label:') else ''
        raw_name = f.get('name') or ''
        nk = f.get('nkey') or name_key(raw_name)
        r = kb.match(lk, nk, t)
        if r is None and t == 'checkbox' and f['screen'] in RIGHTS_SCREENS:
            r = KB_BY_ID['access_right']   # the permission matrices: one tick per screen / function
        total += 1
        specific += 1 if r or f.get('help') else 0
        e = entries.get(f['key'])
        if e is None:
            e = entries[f['key']] = {'l': f['label'] or nk.replace('_', ' '), 't': t, 'kb': r['id'] if r else '', 's': []}
            la = ar_label(terms, f['label'])
            if la:
                e['la'] = la
            if f.get('help'):
                e['h'] = f['help']
        elif not e['kb'] and r:
            e['kb'] = r['id']
        if r:
            used_kb.add(r['id'])
        if f['screen'] not in e['s'] and len(e['s']) < 6:
            e['s'].append(f['screen'])
        m = mrules.get(re.sub(r'[^a-z0-9_]', '', raw_name.split('.')[-1].lower())) or mrules.get(nk)
        if m and 'm' not in e:
            e['m'] = m
        if f.get('maxlength') and str(f['maxlength']).isdigit():
            e.setdefault('m', {})['max'] = int(f['maxlength'])
        if f.get('required'):
            e.setdefault('m', {})['req'] = True
        # control names the runtime can see in the DOM (formControlName / name attributes) point to the entry
        if nk and not raw_name.startswith(('[', '(')) and '.' not in raw_name:
            names.setdefault(nk, f['key'])
    kbo = {}
    for r in kb.KB:
        if r['id'] in used_kb or True:   # every entry (custom fields and server-driven forms use them by label too)
            kbo[r['id']] = {'t': r['t'], 'ae': r['ae'], 'en': r['en'], 'ar': r['ar'], 'cc': r['cc'],
                            're': r['re'].pattern[4:-2], 'nm': r['names'].pattern[4:-2] if r['names'] else '', 'only': list(r['only'] or [])}
    generic = {k: {'en': v[0], 'ar': v[1]} for k, v in GENERIC.items()}
    # other countries: no UAE phone format in the generic phone help
    generic['tel']['cc'] = {'GEN': {'en': {'f': 'International format: +<country code> <number>.', 'x': '+44 20 7946 0958'},
                                    'ar': {'f': 'الصيغة الدولية: ‎+رمز الدولة ثم الرقم.', 'x': '+44 20 7946 0958'}}}
    body = {'kb': kbo, 'generic': generic, 'fields': entries, 'names': names, 'ui': UI}
    version = hashlib.sha1(json.dumps(body, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()[:12]
    stats = {'fields': total, 'help_keys': len(entries), 'specific': specific, 'specific_pct': round(100.0 * specific / max(total, 1), 1),
             'generic_only': total - specific, 'kb_entries': len(kb.KB), 'kb_used': len(used_kb)}
    return dict(version=version, generated=datetime.date.today().isoformat(), stats=stats, **body)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--frontend', default='')
    ap.add_argument('--out', default=os.path.join(REPO, 'i18n', 'field_help.json'))
    a = ap.parse_args()
    front = a.frontend or next((g for g in FRONTEND_GUESSES if g and os.path.isdir(os.path.join(g, 'src', 'app'))), '')
    if not front:
        sys.exit('Frontend folder not found: pass --frontend')
    cat = build(os.path.abspath(front))
    json.dump(cat, open(a.out, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'), sort_keys=True)
    print(json.dumps(cat['stats']), '->', a.out, f'{os.path.getsize(a.out) // 1024} KB')


if __name__ == '__main__':
    main()
