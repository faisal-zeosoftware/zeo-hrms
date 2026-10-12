#!/usr/bin/env python3
"""v1.15.0 – language packs: collect the English source texts (the translation keys) of ZEO HRMS.

    python tools/i18n/extract.py [--frontend <hrms-project folder>] [--out i18n/source.json]

Plain Python (no Django, no Angular build). Scans
  * the frontend (src/app/**/*.html and inline `template:` strings of components; *.ts string literals that are captions
    – label / hint / title / placeholder / description … properties, alert / confirm / snackbar / toast messages,
    message assignments, t('…') calls and `'…' | t` pipes, the country-pack.service.ts captions),
  * the backend (texts returned to users: ValidationError / PermissionDenied … arguments, Response({'detail': …}) and other
    message dicts, t('…') calls, report titles / column labels / notes in DashboardManagement/reports.py, the captions of every
    CountryPack pack),
and writes i18n/source.json = sorted list of {"text": "...", "where": ["file:line", ...]}.

A text with Angular interpolations / f-string parts becomes a template with {0}, {1} … placeholders
("Showing {{ a }} of {{ n }}" → "Showing {0} of {1}"). The output is deterministic: run it again after every change and the new
texts show up as missing translations on Settings → Languages → Translations (after `manage.py load_language_packs`).
"""
import argparse
import ast
import html
import json
import os
import re
import sys
import warnings
from html.parser import HTMLParser

warnings.filterwarnings('ignore', category=SyntaxWarning)   # old regex strings in some apps
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FRONTEND_GUESSES = [
    os.environ.get('ZEO_FRONTEND_DIR', ''),
    os.path.join(REPO, '..', '..', 'zeofe', 'zeo-frontend', 'hrms-project'),
    os.path.join(REPO, '..', 'zeo-frontend', 'hrms-project'),
    os.path.join(REPO, '..', 'hrms-project'),
]
MAX_WHERE = 25          # file:line references kept per text (the rest is counted only)
MAX_LEN = 600

# ----------------------------------------------------------------------------------------------------------- text filter
WS = re.compile(r'\s+')
PLACEHOLDER = re.compile(r'\{\d+\}')
CAMEL = re.compile(r'[a-z][A-Z]')
CODE_HINTS = ('=>', '&&', '||', '===', '!==', 'function(', '();', '${', '{{', '}}', '</', '/>', 'px;', 'http:', 'https:',
              'mailto:', 'data:', 'rgba(', 'rgb(', '#{', 'var(--')
FILE_EXT = re.compile(r'\.(png|jpe?g|svg|gif|ico|css|scss|html|ts|js|json|pdf|xlsx|csv|py|txt|woff2?)$', re.I)


def norm(s):
    return WS.sub(' ', s or '').strip()


def is_text(s, level='medium'):
    """level: 'loose' (visible text node / caption property), 'medium' (bound display attribute, literal in a {{ }}),
    'strict' (generic literal: needs a space or a capitalised word)."""
    if not s or len(s) > MAX_LEN:
        return False
    bare = PLACEHOLDER.sub('', s)
    letters = re.findall(r'[A-Za-z]', bare)
    if len(letters) < 2 and bare.strip() not in ('A', 'I'):
        return False
    if any(h in s for h in CODE_HINTS):
        return False
    if s.startswith(('/', './', '../', '#', '@', '$', '<', '&', '?', '=', '[', '{', '%')) and not PLACEHOLDER.match(s):
        return False
    if s.startswith('.') and ' ' not in s:
        return False
    if FILE_EXT.search(s) and ' ' not in s:
        return False
    if ' ' not in bare.strip():
        w = bare.strip()
        if '_' in w or CAMEL.search(w) or re.search(r'[()=;\[\]<>|\\]', w):
            return False
        if re.match(r'^[a-z0-9]+(-[a-z0-9]+)+$', w):          # kebab-case: CSS classes, icon / route names
            return False
        if re.search(r'\w\.\w', w) and not re.match(r'^([A-Za-z]\.){2,}$', w):   # dotted paths ("a.b"); keep "e.g."
            return False
        if '/' in w and not re.match(r'^[A-Z][a-z]+(/[A-Z]?[a-z]+)+$', w):     # paths, dates formats; keep "Male/Female"
            return False
        if level == 'strict' and not re.match(r'^[A-Z][A-Za-z\'’.!?:-]*$', w):
            return False
        if level == 'medium' and not re.match(r'^[A-Z]', w) and w.lower() not in SMALL_WORDS:
            return False
        if re.match(r'^[A-Z0-9_]{2,}$', w) and level != 'loose' and len(w) > 5:   # CONSTANT_NAMES
            return False
    else:
        if level == 'strict' and re.match(r'^[a-z0-9_.-]+( [a-z0-9_.-]+)*$', bare.strip()) and len(bare.split()) <= 2 \
                and not re.search(r'[aeiou]{1}', bare):
            return False
        if re.match(r'^[\w-]+( [\w-]+)+$', bare.strip()) and all(re.match(r'^[a-z0-9]+(-[a-z0-9]+)+$|^[a-z]+-?$', x) for x in bare.split()) \
                and any('-' in x for x in bare.split()):
            return False                                      # "btn btn-primary" (CSS class lists)
    return True


SMALL_WORDS = {'or', 'and', 'of', 'to', 'by', 'in', 'on', 'at', 'from', 'for', 'with', 'yes', 'no', 'all', 'none', 'days',
               'day', 'hours', 'hour', 'months', 'month', 'years', 'year', 'minutes', 'min', 'items', 'records', 'rows',
               'selected', 'more', 'less', 'today', 'yesterday', 'tomorrow', 'week', 'weeks', 'employees', 'employee',
               'pending', 'approved', 'rejected', 'draft', 'open', 'closed', 'active', 'inactive', 'new', 'total'}


class Bag:
    def __init__(self):
        self.terms = {}

    def add(self, text, where, level='medium'):
        t = norm(text)
        if not is_text(t, level):
            return
        self.terms.setdefault(t, set()).add(where)

    def dump(self):
        out = []
        for t in sorted(self.terms, key=lambda x: (x.lower(), x)):
            ws = sorted(self.terms[t], key=_where_key)
            out.append({'text': t, 'where': ws[:MAX_WHERE]} if len(ws) <= MAX_WHERE else
                       {'text': t, 'where': ws[:MAX_WHERE], 'more': len(ws) - MAX_WHERE})
        return out


def _where_key(w):
    f, _, line = w.rpartition(':')
    return (f, int(line) if line.isdigit() else 0)


# ------------------------------------------------------------------------------------------------------------ literals
LIT = r"""'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*"|`(?:[^`\\]|\\.)*`"""
LIT_RE = re.compile(LIT)


def unquote(lit):
    """JS / TS string literal → (text with {n} placeholders for ${…} parts)."""
    q, body = lit[0], lit[1:-1]
    if q == '`':
        n = [0]

        def ph(_m):
            n[0] += 1
            return '{%d}' % (n[0] - 1)
        body = _replace_template_exprs(body, ph)
    body = re.sub(r'\\(u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|.)', _unescape, body)
    return body


def _replace_template_exprs(body, ph):
    out, i = [], 0
    while i < len(body):
        if body.startswith('${', i):
            depth, j = 1, i + 2
            while j < len(body) and depth:
                depth += {'{': 1, '}': -1}.get(body[j], 0)
                j += 1
            out.append(ph(None))
            i = j
        else:
            out.append(body[i])
            i += 1
    return ''.join(out)


def _unescape(m):
    c = m.group(1)
    if c[0] == 'u' and len(c) == 5:
        return chr(int(c[1:], 16))
    if c[0] == 'x' and len(c) == 3:
        return chr(int(c[1:], 16))
    return {'n': ' ', 't': ' ', 'r': ' '}.get(c, c)


def literals_in(expr):
    """String literals of an Angular / TS expression, skipping pipe arguments (| date:'…') and object keys."""
    res = []
    for m in LIT_RE.finditer(expr):
        before = expr[:m.start()].rstrip()
        after = expr[m.end():].lstrip()
        if before.endswith(':') and re.search(r'\|\s*\w+\s*:$', before):   # pipe argument
            continue
        if before.endswith(('[', '.')) or after.startswith((':',)) and not before.endswith('?'):
            continue                                                    # obj['key'] / { 'key': … }
        if re.search(r'(===?|!==?)\s*$', before) or re.match(r'^(===?|!==?)', after):
            continue                                                    # comparisons: x === 'approved'
        if re.search(r'\b(ngClass|class|style|routerLink|queryParams|navigate|getItem|setItem|get|post|put|patch|delete)\s*\(?\s*$', before):
            continue
        res.append(unquote(m.group(0)))
    return res


# ---------------------------------------------------------------------------------------------------------------- HTML
TEXT_ATTRS = {'placeholder', 'title', 'aria-label', 'mattooltip', 'alt', 'label', 'subtitle', 'hint', 'heading', 'emptytext',
              'data-tip', 'tooltip'}
SKIP_TAGS = {'script', 'style', 'mat-icon', 'code', 'pre', 'svg'}
SKIP_CLASS = re.compile(r'(^|\s)(material-icons[\w-]*|material-symbols[\w-]*)(\s|$)')
INTERP = re.compile(r'\{\{([\s\S]*?)\}\}')
PIPE_T = re.compile(r'\|\s*t\b(?!\w)')


class _Html(HTMLParser):
    def __init__(self, bag, path, line0):
        super().__init__(convert_charrefs=True)
        self.bag, self.path, self.line0 = bag, path, line0
        self.skip_tag, self.skip_depth = None, 0
        self.notrans = 0

    def where(self):
        return f'{self.path}:{self.getpos()[0] + self.line0}'

    def handle_starttag(self, tag, attrs):
        if self.skip_tag:
            if tag == self.skip_tag:
                self.skip_depth += 1
            return
        a = dict(attrs)
        if tag in SKIP_TAGS or SKIP_CLASS.search(a.get('class') or '') or 'data-no-translate' in a:
            if tag not in ('img', 'input', 'br', 'hr', 'meta', 'link'):
                self.skip_tag, self.skip_depth = tag, 1
            return
        w = self.where()
        for k, v in attrs:
            if v is None:
                continue
            kl = k.lower()
            if kl in TEXT_ATTRS:
                self._text(v, w, 'loose')
            elif kl.startswith('[') and kl.strip('[]').replace('attr.', '') in TEXT_ATTRS:
                lvl = 'loose' if PIPE_T.search(v) else 'medium'
                for s in literals_in(v):
                    self.bag.add(s, w, lvl)
            elif tag in ('button', 'input') and kl == 'value' and (a.get('type') in ('button', 'submit', 'reset') or tag == 'button'):
                self._text(v, w, 'loose')
        for k, v in attrs:   # (click)="… alert('…') / confirm('…')"
            if v and k.startswith('(') and re.search(r'\b(alert|confirm|t)\s*\(', v):
                for m in re.finditer(r'\b(?:alert|confirm|t)\s*\(\s*(' + LIT + ')', v):
                    self.bag.add(unquote(m.group(1)), w, 'medium')

    def handle_endtag(self, tag):
        if self.skip_tag and tag == self.skip_tag:
            self.skip_depth -= 1
            if self.skip_depth <= 0:
                self.skip_tag = None

    def handle_data(self, data):
        if self.skip_tag or not data.strip():
            return
        self._text(data, self.where(), 'loose')

    def _text(self, data, w, level):
        n = [0]
        lits = []

        def ph(m):
            lv = 'loose' if PIPE_T.search(m.group(1)) else 'medium'     # {{ 'text' | t }}: a caption by definition
            lits.extend((x, lv) for x in literals_in(m.group(1)))
            n[0] += 1
            return '{%d}' % (n[0] - 1)
        tmpl = INTERP.sub(ph, data)
        for s, lv in lits:
            self.bag.add(s, w, lv)
        if n[0] and not re.search(r'[A-Za-z]{2}', PLACEHOLDER.sub('', tmpl)):
            return
        self.bag.add(html.unescape(tmpl), w, level)


def scan_html(bag, text, path, line0=0):
    p = _Html(bag, path, line0)
    try:
        p.feed(text)
        p.close()
    except Exception as e:   # a broken template must not stop the extraction
        print(f'warning: {path}: {e}', file=sys.stderr)


# ------------------------------------------------------------------------------------------------------------------ TS
TS_KEYS = ('label|hint|title|placeholder|description|intro|help|emptyText|empty|emptyMessage|confirm|confirmText|itemName|'
           'subtitle|caption|tooltip|text|message|msg|note|header|heading|ph|l|suffix|prefix|okText|cancelText|okLabel|'
           'cancelLabel|question|detail|summary|group|section|tab|button|action|singular|plural|unit|legend|info|warning')
TS_KEY_RE = re.compile(r'(?<![\w.$])(?:' + TS_KEYS + r')\??\s*:\s*(' + LIT + r')(\s*\?\s*(' + LIT + r')\s*:\s*(' + LIT + r'))?')
TS_KEY_TERN_RE = re.compile(r'(?<![\w.$])(?:' + TS_KEYS + r')\s*:\s*[\w.!()\s=<>&|]{1,80}\?\s*(' + LIT + r')\s*:\s*(' + LIT + r')')
TS_CALL_RE = re.compile(r'(?<![\w$])(?:alert|confirm|prompt|open|success|error|warning|info|fire|notify|toast|showError|'
                        r'showSuccess|showMessage|showToast|flash|say|ok|fail|setMsg|setError|done|t|T|i18nT|translate)'
                        r'\s*\(\s*(' + LIT + r')')
TS_ASSIGN_RE = re.compile(r'(?<![\w$])(?:this\.)?(?:msg|message|errorMessage|errorMsg|successMessage|successMsg|err|error|'
                          r'notice|toastMsg|saveMsg|okMsg|info|hint|title|label|emptyText|banner|note|warn|warning|'
                          r'loadingText|statusMessage)\s*=\s*(' + LIT + r')')
TS_PIPE_RE = re.compile(r'(' + LIT + r')\s*\|\s*t\b')
TEMPLATE_RE = re.compile(r'(?<![\w$])template\s*:\s*`')


def _line_of(text, pos):
    return text.count('\n', 0, pos) + 1


def strip_comments(text):
    """TS without // and /* */ comments (replaced by spaces, newlines kept so line numbers stay right); strings untouched."""
    out, i, n = [], 0, len(text)
    q = None
    while i < n:
        ch = text[i]
        if q:
            out.append(ch)
            if ch == '\\' and i + 1 < n:
                out.append(text[i + 1]); i += 2; continue
            if ch == q:
                q = None
            i += 1
            continue
        if ch in '\'"`':
            q = ch; out.append(ch); i += 1; continue
        if ch == '/' and not text.startswith('//', i) and not text.startswith('/*', i):
            prev = ''.join(out).rstrip()[-1:]
            if prev == '' or prev in '(,=:[!&|?{};+-*%<>~^' or ''.join(out).rstrip().endswith(('return', 'typeof')):
                j, cls = i + 1, False           # regex literal: copy it as it is (it may contain quotes)
                while j < n and text[j] != '\n':
                    if text[j] == '\\':
                        j += 2; continue
                    if text[j] == '[':
                        cls = True
                    elif text[j] == ']':
                        cls = False
                    elif text[j] == '/' and not cls:
                        break
                    j += 1
                if j < n and text[j] == '/':
                    out.append(text[i:j + 1]); i = j + 1; continue
        if text.startswith('//', i):
            j = text.find('\n', i)
            j = n if j < 0 else j
            out.append(' ' * (j - i)); i = j; continue
        if text.startswith('/*', i):
            j = text.find('*/', i + 2)
            j = n if j < 0 else j + 2
            out.append(''.join(c if c == '\n' else ' ' for c in text[i:j])); i = j; continue
        out.append(ch)
        i += 1
    return ''.join(out)


def scan_ts(bag, text, path):
    text = strip_comments(text)
    # inline component templates (`template: \`…\``) are HTML
    for m in TEMPLATE_RE.finditer(text):
        start = m.end()
        j = start
        while j < len(text) and text[j] != '`':
            j += 2 if text[j] == '\\' else 1
        scan_html(bag, text[start:j].replace('\\`', '`'), path, _line_of(text, start) - 1)
    base = os.path.basename(path)
    for rx, level in ((TS_KEY_RE, 'medium'), (TS_CALL_RE, 'medium'), (TS_ASSIGN_RE, 'strict'), (TS_PIPE_RE, 'loose')):
        for m in rx.finditer(text):
            w = f'{path}:{_line_of(text, m.start())}'
            for g in m.groups()[:1] + (m.groups()[2:] if rx is TS_KEY_RE else ()):
                if g and g[0] in '\'"`':
                    bag.add(unquote(g), w, level)
    for m in TS_KEY_TERN_RE.finditer(text):
        w = f'{path}:{_line_of(text, m.start())}'
        for g in m.groups():
            bag.add(unquote(g), w, 'medium')
    # caption tables: const VIEW_LABEL: Record<…, string> = { list: 'List', … } / *_TITLES = { … }
    for m in re.finditer(r'\b[A-Z_]*(?:LABELS?|TITLES?|CAPTIONS?|NAMES?)\b[^=\n]{0,80}=\s*\{([^{}]*)\}', text):
        w = f'{path}:{_line_of(text, m.start())}'
        for lm in re.finditer(r'(?<![\w$])\w+\s*:\s*(' + LIT + ')', m.group(1)):
            bag.add(unquote(lm.group(1)), w, 'medium')
    if base == 'country-pack.service.ts':    # AE pack + neutral captions
        for m in re.finditer(r'(?<![\w$])\w+\s*:\s*(' + LIT + ')', text):
            bag.add(unquote(m.group(1)), f'{path}:{_line_of(text, m.start())}', 'medium')


def scan_frontend(bag, root):
    app = os.path.join(root, 'src', 'app')
    for d, dirs, files in os.walk(app):
        dirs.sort()
        for f in sorted(files):
            p = os.path.join(d, f)
            rel = os.path.relpath(p, root).replace(os.sep, '/')
            if f.endswith('.spec.ts'):
                continue
            if f.endswith('.html'):
                scan_html(bag, open(p, encoding='utf-8', errors='replace').read(), rel)
            elif f.endswith('.ts'):
                scan_ts(bag, open(p, encoding='utf-8', errors='replace').read(), rel)


# ------------------------------------------------------------------------------------------------------------- backend
EXC_NAMES = {'ValidationError', 'PermissionDenied', 'NotFound', 'ParseError', 'AuthenticationFailed', 'NotAuthenticated',
             'APIException', 'MethodNotAllowed', 'Throttled', 'NotAcceptable', 'UnsupportedMediaType', 'Http404',
             'DjangoValidationError', 'SerializerValidationError', 'BadRequest'}
TRANSLATE_CALLS = {'t', 'tr', '_tr', 'tmsg', 'msg', 'Msg', '_t', 'gettext_noop', '_', 'gettext', 'gettext_lazy', 'ugettext', 'ugettext_lazy', 'pgettext', 'ngettext'}
RESPONSE_CALLS = {'Response', 'JsonResponse', 'HttpResponseBadRequest'}
MSG_KEYS = {'detail', 'error', 'errors', 'message', 'msg', 'warning', 'info', 'note', 'notes', 'title', 'hint', 'help',
            'non_field_errors', 'reason', 'description', 'label', 'subtitle', 'success', 'status_text', 'summary'}
LABEL_CALLS = {'_f'}
ALL_TEXT_FILES = {'DashboardManagement/reports.py', 'CountryPack/packs.py',
                  # v1.15.0: screens built from server data (dashboards, profile field groups)
                  'DashboardManagement/overview.py', 'DashboardManagement/ceo.py', 'DashboardManagement/services.py',
                  'DashboardManagement/records.py', 'DashboardManagement/unified_calendar.py', 'SelfService/profile.py'}
SKIP_DIRS = {'migrations', 'tests', '__pycache__', 'management', 'zeo', 'tools', 'i18n', 'media', 'staticfiles', 'venv', 'node_modules'}


def _func_name(f):
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return ''


class _Py(ast.NodeVisitor):
    def __init__(self, bag, path, all_text):
        self.bag, self.path, self.all_text = bag, path, all_text
        self.dict_keys = set()
        self.docstrings = set()
        self.seen = set()

    def w(self, node):
        return f'{self.path}:{getattr(node, "lineno", 0)}'

    def strings(self, node, level='medium'):
        """Every text inside an expression (constants, f-strings, dict values, list items, both sides of x if c else y)."""
        if node is None:
            return
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            self._add(node.value, node, level)
        elif isinstance(node, ast.JoinedStr):
            self._add(self.fstring(node), node, level)
        elif isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                self.strings(v, level)
        elif isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            for e in node.elts:
                self.strings(e, level)
        elif isinstance(node, ast.IfExp):
            self.strings(node.body, level)
            self.strings(node.orelse, level)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
            if isinstance(node.op, ast.Add):
                self.strings(node.left, level)
            else:
                self.strings(node.left, level)
        elif isinstance(node, ast.Call):
            fn = _func_name(node.func)
            if fn == 'format' and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Constant) \
                    and isinstance(node.func.value.value, str):
                s = node.func.value.value
                n = [0]

                def ph(_m):
                    n[0] += 1
                    return '{%d}' % (n[0] - 1)
                self._add(re.sub(r'\{[^{}]*\}', ph, s), node, level)
            elif fn in TRANSLATE_CALLS | {'str', 'join'}:
                pass                                   # visited on their own
            elif fn in ('dict',):
                for kw in node.keywords:
                    self.strings(kw.value, level)

    def fstring(self, node):
        parts, n = [], 0
        for v in node.values:
            if isinstance(v, ast.Constant):
                parts.append(str(v.value).replace('{', '{{').replace('}', '}}') if False else str(v.value))
            else:
                parts.append('{%d}' % n)
                n += 1
        return ''.join(parts)

    def _add(self, s, node, level):
        if id(node) in self.docstrings:
            return
        self.bag.add(s, self.w(node), level)

    # --- visitors
    def visit_Module(self, node):
        self._mark_docstrings(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        self._mark_docstrings(node)
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self._mark_docstrings(node)
        self.generic_visit(node)

    def _mark_docstrings(self, node):
        body = getattr(node, 'body', [])
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            self.docstrings.add(id(body[0].value))

    def visit_Call(self, node):
        fn = _func_name(node.func)
        if fn in EXC_NAMES:
            for a in node.args:
                self.strings(a, 'medium')
            for kw in node.keywords:
                if kw.arg in ('detail', 'message', None):
                    self.strings(kw.value, 'medium')
        elif fn in TRANSLATE_CALLS and node.args:
            self.strings(node.args[0], 'loose')
        elif fn in RESPONSE_CALLS and node.args and isinstance(node.args[0], ast.Dict):
            self._msg_dict(node.args[0])
        elif fn in LABEL_CALLS and len(node.args) >= 2:   # v1.15.0 field definitions: _f('key', 'Label', ...)
            self.strings(node.args[1], 'loose')
        self.generic_visit(node)

    def visit_Dict(self, node):
        self._msg_dict(node)
        self.generic_visit(node)

    def _msg_dict(self, node):
        if id(node) in self.seen:
            return
        self.seen.add(id(node))
        for k, v in zip(node.keys, node.values):
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                self.dict_keys.add(id(k))
                if k.value in MSG_KEYS:
                    self.strings(v, 'strict' if k.value in ('error', 'errors', 'status_text') else 'medium')

    def visit_Constant(self, node):
        if self.all_text and isinstance(node.value, str) and id(node) not in self.docstrings and id(node) not in self.dict_keys:
            self.bag.add(node.value, self.w(node), 'strict')

    def visit_JoinedStr(self, node):
        if self.all_text:
            self.bag.add(self.fstring(node), self.w(node), 'strict')
        # do not descend: the parts are not texts on their own


def scan_backend(bag, root):
    for d, dirs, files in os.walk(root):
        rel_d = os.path.relpath(d, root).replace(os.sep, '/')
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and not x.startswith('.'))
        if rel_d == '.':
            dirs[:] = [x for x in dirs if os.path.exists(os.path.join(root, x, '__init__.py'))]
        for f in sorted(files):
            if not f.endswith('.py') or f.startswith('test') or f in ('settings.py',) or f.endswith('_test.py'):
                continue
            p = os.path.join(d, f)
            rel = os.path.relpath(p, root).replace(os.sep, '/')
            try:
                tree = ast.parse(open(p, encoding='utf-8', errors='replace').read(), filename=rel)
            except SyntaxError as e:
                print(f'warning: {rel}: {e}', file=sys.stderr)
                continue
            v = _Py(bag, rel, rel in ALL_TEXT_FILES)
            if f == 'models.py':   # v1.15.0 choice labels shown on screens: ('code', 'Label') pairs
                for n in ast.walk(tree):
                    if isinstance(n, ast.Tuple) and len(n.elts) == 2 and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in n.elts):
                        v.strings(n.elts[1], 'medium')
            # dict keys first, so the all-text files skip them
            for n in ast.walk(tree):
                if isinstance(n, ast.Dict):
                    for k in n.keys:
                        if isinstance(k, ast.Constant):
                            v.dict_keys.add(id(k))
                if v.all_text and isinstance(n, ast.Tuple) and len(n.elts) == 3 and all(isinstance(e, ast.Constant) for e in n.elts):
                    v.dict_keys.add(id(n.elts[0]))   # report columns: ('key', 'Label', 'type') – key and type are not texts
                    v.dict_keys.add(id(n.elts[2]))
                if v.all_text and isinstance(n, ast.Subscript):
                    s = n.slice
                    if isinstance(s, ast.Constant):
                        v.dict_keys.add(id(s))
                if v.all_text and isinstance(n, ast.Compare):
                    for c in [n.left] + list(n.comparators):
                        if isinstance(c, ast.Constant):
                            v.dict_keys.add(id(c))
                        if isinstance(c, (ast.Tuple, ast.List, ast.Set)):
                            for e in c.elts:
                                v.dict_keys.add(id(e))
                if v.all_text and isinstance(n, ast.Call):
                    fn = _func_name(n.func)
                    if fn in ('get', 'filter', 'exclude', 'values', 'values_list', 'order_by', 'getattr', 'setdefault', 'pop',
                              'select_related', 'prefetch_related', 'annotate', 'only', 'defer', 'getLogger', 'exception',
                              'info', 'debug', 'warning', 'error', 'startswith', 'endswith', 'split', 'strftime', 'replace',
                              'Q', 'F', 'join', 'strptime', 'reverse', 'hasattr', 'isinstance'):
                        for a in n.args:
                            if isinstance(a, ast.Constant):
                                v.dict_keys.add(id(a))
                    for kw in n.keywords:
                        if isinstance(kw.value, ast.Constant) and kw.arg not in ('title', 'label', 'note', 'description', 'detail'):
                            v.dict_keys.add(id(kw.value))
            v.visit(tree)


# ---------------------------------------------------------------------------------------------------------------- main
def find_frontend(arg):
    for c in ([arg] if arg else []) + FRONTEND_GUESSES:
        if c and os.path.isdir(os.path.join(c, 'src', 'app')):
            return os.path.abspath(c)
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--frontend', help='the Angular hrms-project folder (default: $ZEO_FRONTEND_DIR or a sibling checkout)')
    ap.add_argument('--backend', default=REPO, help='the Django repo (default: this repo)')
    ap.add_argument('--out', default=os.path.join(REPO, 'i18n', 'source.json'))
    ap.add_argument('--no-frontend', action='store_true', help='backend texts only')
    a = ap.parse_args(argv)
    bag = Bag()
    if not a.no_frontend:
        fe = find_frontend(a.frontend)
        if not fe:
            ap.error('frontend folder not found – pass --frontend <path to hrms-project> or set ZEO_FRONTEND_DIR')
        scan_frontend(bag, fe)
    scan_backend(bag, a.backend)
    data = bag.dump()
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    tmp = a.out + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
        fh.write('\n')
    os.replace(tmp, a.out)
    fe_n = sum(1 for x in data if any(w.startswith('src/') for w in x['where']))
    print(f'{len(data)} source texts written to {a.out} ({fe_n} from the frontend, {len(data) - fe_n} backend only)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
