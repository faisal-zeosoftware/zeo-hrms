#!/usr/bin/env python3
"""v1.16.0 – field help: list EVERY form field of ZEO HRMS (the inventory the help catalogue is built from).

    python tools/fieldhelp/extract_fields.py [--frontend <hrms-project folder>] [--out i18n/field_help_fields.json]

Plain Python (no Django, no Angular build). Collects
  * template fields (src/app/**/*.html and inline `template:` strings): <input>, <select>, <textarea>, <mat-select>,
    <mat-checkbox> with formControlName / name / [(ngModel)] target / id / type / placeholder and the field's label
    (<label for>, a wrapping <label>, the label just before the field, <mat-label>, .zf-label / .k captions, placeholder);
  * config-driven fields (crud-page / page configs: `fields: [{ key, label, type }]` in *.ts);
  * server-driven forms (EmployeeProfile.services.profile_groups `_f('key', 'Label', type)` and SelfService.profile.GROUPS),
  * form designer custom fields are not listed here: they carry their own help text / example from the designer.
Each field gets a help key: `label:<normalised English label>` (the runtime looks up name and label – see field-help.service.ts).
"""
import argparse
import json
import os
import re
import sys
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, 'tools', 'i18n'))
from extract import FRONTEND_GUESSES, TEMPLATE_RE, _line_of, strip_comments  # noqa: E402

WS = re.compile(r'\s+')
SKIP_TYPES = {'hidden', 'submit', 'button', 'reset', 'image'}
CONTROL_TAGS = {'input', 'select', 'textarea', 'mat-select', 'mat-checkbox'}
LABEL_CLASSES = {'zf-label', 'form-label', 'k', 'es-k', 'control-label', 'field-label', 'ex-label', 'lbl'}


def norm_label(s):
    s = WS.sub(' ', str(s or '')).strip()
    s = re.sub(r'\{\{.*?\}\}', '', s).strip()
    s = s.strip(' :*')
    return s


def help_key(label):
    """Normalised English label: lower case, letters / digits only, single spaces."""
    s = re.sub(r'[^a-z0-9]+', ' ', str(label or '').lower()).strip()
    return s


def name_key(name):
    s = str(name or '').strip()
    s = re.sub(r'^(f|form|model|formModel|vals|data|this|item|row|edit|draft|obj|record|newItem|filters?)\.', '', s)
    s = re.sub(r'\[.*?\]', '', s)
    parts = s.split('.')
    s = '_'.join(parts[-2:]) if len(parts) > 1 and parts[-1] in ('id', 'value', 'name', 'selected') else parts[-1]
    s = re.sub(r'([a-z])([A-Z])', r'\1_\2', s).lower()
    return re.sub(r'[^a-z0-9_]+', '', s)


class _Lab:
    def __init__(self, for_id, depth):
        self.for_id, self.depth, self.parts, self.done = for_id, depth, [], False

    @property
    def text(self):
        return norm_label(' '.join(self.parts))


class Parser(HTMLParser):
    def __init__(self, path, line0):
        super().__init__(convert_charrefs=True)
        self.path, self.line0 = path, line0
        self.stack = []          # open tags
        self.labels = []         # open label-like elements (wrapping)
        self.last_label = None   # last closed label-like element (preceding caption)
        self.by_for = {}
        self.fields = []
        self.open_controls = []  # mat-select / mat-checkbox / textarea / select collecting inner text
        self.icon = 0            # inside <mat-icon> (ligature names such as "badge" are not caption text)

    def _is_label(self, tag, attrs):
        if tag in ('label', 'mat-label'):
            return True
        cls = set((attrs.get('class') or '').split())
        return bool(cls & LABEL_CLASSES) and tag in ('div', 'span', 'p', 'small', 'b', 'strong', 'dt', 'th', 'legend')

    def handle_starttag(self, tag, attrs_l):
        attrs = {k.lower(): (v if v is not None else '') for k, v in attrs_l}
        line = self.line0 + self.getpos()[0]
        if tag == 'mat-icon' or (tag in ('i', 'span') and 'material-icons' in (attrs.get('class') or '')):
            self.icon += 1
        if self._is_label(tag, attrs):
            lab = _Lab(attrs.get('for') or attrs.get('[for]', '').strip('\'"'), len(self.stack))
            self.labels.append((tag, lab))
        if tag in CONTROL_TAGS:
            typ = attrs.get('type') or ('select' if tag in ('select', 'mat-select') else 'textarea' if tag == 'textarea' else 'checkbox' if tag == 'mat-checkbox' else 'text')
            if attrs.get('[type]'):
                typ = 'dynamic'
            if typ in SKIP_TYPES:
                pass
            else:
                name = (attrs.get('formcontrolname') or attrs.get('[formcontrolname]', '').strip('\'"') or attrs.get('name')
                        or attrs.get('[(ngmodel)]') or attrs.get('[ngmodel]') or attrs.get('[formcontrol]') or attrs.get('[value]') or attrs.get('id') or '')
                f = {'file': self.path, 'line': line, 'tag': tag, 'type': typ, 'name': name, 'id': attrs.get('id', ''),
                     'placeholder': norm_label(attrs.get('placeholder') or attrs.get('aria-label') or attrs.get('title') or ''),
                     'required': 'required' in attrs or '[required]' in attrs,
                     'maxlength': attrs.get('maxlength', ''), 'pattern': attrs.get('pattern', ''), 'label': '', '_wrap': None, '_inner': None}
                if self.labels:
                    f['_wrap'] = self.labels[-1][1]
                elif self.last_label is not None and not self.last_label.for_id:
                    f['label'] = self.last_label.text
                if tag in ('mat-checkbox',):
                    f['_inner'] = []
                    self.open_controls.append((tag, f))
                self.fields.append(f)
                self.last_label = None
        if tag not in ('input', 'br', 'img', 'hr', 'meta', 'link', 'col', 'source', 'wbr'):
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag == 'mat-icon' or (tag in ('i',) and self.icon):
            self.icon = max(0, self.icon - 1)
        if self.labels and self.labels[-1][0] == tag:
            _, lab = self.labels.pop()
            lab.done = True
            if lab.for_id:
                self.by_for[lab.for_id] = lab.text
            self.last_label = lab
        if self.open_controls and self.open_controls[-1][0] == tag:
            _, f = self.open_controls.pop()
            f['label'] = f['label'] or norm_label(' '.join(f['_inner']))
        while self.stack:
            t = self.stack.pop()
            if t == tag:
                break

    def handle_data(self, data):
        if self.icon:
            return
        if self.labels:
            for _, lab in self.labels:
                lab.parts.append(data)
        for _, f in self.open_controls:
            f['_inner'].append(data)

    def result(self):
        out = []
        for f in self.fields:
            lab = ''
            if f['id'] and f['id'] in self.by_for:
                lab = self.by_for[f['id']]
            if not lab and f['_wrap'] is not None:
                lab = f['_wrap'].text
            lab = lab or f['label'] or f['placeholder']
            f['label'] = norm_label(lab)
            f.pop('_wrap', None)
            f.pop('_inner', None)
            out.append(f)
        return out


def scan_html(text, path, line0=0):
    p = Parser(path, line0)
    try:
        p.feed(text)
        p.close()
    except Exception:
        pass
    return p.result()


FIELDS_RE = re.compile(r'\bfields\s*:\s*\[')
OBJ_RE = re.compile(r"\{\s*key\s*:\s*'([^']+)'\s*,\s*label\s*:\s*'((?:[^'\\]|\\.)*)'(?:\s*,\s*type\s*:\s*'([^']+)')?([^{}]*)\}")


def _balanced(text, start):
    depth, i = 0, start
    while i < len(text):
        c = text[i]
        if c in '[{(':
            depth += 1
        elif c in ']})':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
        i += 1
    return text[start:]


def scan_ts(text, path):
    text = strip_comments(text)
    out = []
    for m in TEMPLATE_RE.finditer(text):
        start = m.end()
        j = start
        while j < len(text) and text[j] != '`':
            j += 2 if text[j] == '\\' else 1
        out += scan_html(text[start:j].replace('\\`', '`'), path, _line_of(text, start) - 1)
    for m in FIELDS_RE.finditer(text):
        block = _balanced(text, m.end() - 1)
        for o in OBJ_RE.finditer(block):
            rest = o.group(4) or ''
            ph = re.search(r"placeholder\s*:\s*'((?:[^'\\]|\\.)*)'", rest)
            hp = re.search(r"help\s*:\s*'((?:[^'\\]|\\.)*)'", rest)
            out.append({'file': path, 'line': _line_of(text, m.start() + o.start()), 'tag': 'config', 'type': o.group(3) or 'text',
                        'name': o.group(1), 'id': '', 'placeholder': ph.group(1) if ph else '', 'required': 'required: true' in rest,
                        'maxlength': '', 'pattern': '', 'label': norm_label(o.group(2).replace("\\'", "'")), 'help': hp.group(1) if hp else ''})
    return out


def scan_server_forms(repo):
    out = []
    p = os.path.join(repo, 'EmployeeProfile', 'services.py')
    if os.path.exists(p):
        t = open(p, encoding='utf-8').read()
        for m in re.finditer(r"_f\('(\w+)',\s*'((?:[^'\\]|\\.)*)'(?:,\s*'(\w+)')?", t):
            out.append({'file': 'EmployeeProfile/services.py', 'line': _line_of(t, m.start()), 'tag': 'server', 'type': m.group(3) or 'text',
                        'name': m.group(1), 'id': '', 'placeholder': '', 'required': False, 'maxlength': '', 'pattern': '',
                        'label': norm_label(m.group(2).replace('’', "'"))})
    p = os.path.join(repo, 'SelfService', 'profile.py')
    if os.path.exists(p):
        t = open(p, encoding='utf-8').read()
        g = t[t.find('GROUPS = ['):t.find('GROUP = {')]
        for m in re.finditer(r"\('(\w+)',\s*(['\"])(.+?)\2,\s*'([\w:.]+)'\)", g):
            out.append({'file': 'SelfService/profile.py', 'line': _line_of(t, t.find('GROUPS = [') + m.start()), 'tag': 'server',
                        'type': m.group(4).split(':')[0], 'name': m.group(1), 'id': '', 'placeholder': '', 'required': False,
                        'maxlength': '', 'pattern': '', 'label': norm_label(m.group(3))})
    return out


def screen_of(path):
    parts = path.replace('\\', '/').split('/')
    if 'app' in parts:
        parts = parts[parts.index('app') + 1:]
    return parts[0] if len(parts) > 1 else os.path.splitext(parts[0])[0]


def collect(front, repo=REPO):
    app = os.path.join(front, 'src', 'app')
    fields = []
    for root, dirs, files in os.walk(app):
        dirs[:] = [d for d in dirs if d not in ('node_modules', 'dist', '.angular')]
        for fn in sorted(files):
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, front).replace('\\', '/')
            if fn.endswith('.spec.ts'):
                continue
            if fn.endswith('.html'):
                fields += scan_html(open(p, encoding='utf-8', errors='replace').read(), rel)
            elif fn.endswith('.ts'):
                fields += scan_ts(open(p, encoding='utf-8', errors='replace').read(), rel)
    fields += scan_server_forms(repo)
    out = []
    for f in fields:
        lab = f['label']
        if '{{' in (f.get('name') or '') or (not lab and not f.get('name')):
            f['dynamic'] = True
        if lab and len(lab) > 80:
            lab = lab[:80]
        f['label'] = lab
        f['screen'] = screen_of(f['file'])
        f['nkey'] = name_key(f.get('name'))
        f['key'] = 'label:' + help_key(lab) if help_key(lab) and not re.fullmatch(r'[\d ]*', help_key(lab)) else ('name:' + f['nkey'] if f['nkey'] else '')
        out.append(f)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--frontend', default='')
    ap.add_argument('--out', default=os.path.join(REPO, 'i18n', 'field_help_fields.json'))
    a = ap.parse_args()
    front = a.frontend or next((g for g in FRONTEND_GUESSES if g and os.path.isdir(os.path.join(g, 'src', 'app'))), '')
    if not front:
        sys.exit('Frontend folder not found: pass --frontend')
    fields = collect(os.path.abspath(front))
    json.dump(fields, open(a.out, 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
    print(f'{len(fields)} fields, {len({f["key"] for f in fields})} distinct help keys -> {a.out}')


if __name__ == '__main__':
    main()
