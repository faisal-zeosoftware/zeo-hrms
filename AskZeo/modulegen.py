"""Ask ZEO 1.73.5 – the AI drafts a full code module (level 4 of ZEO Developer: a Django app and an Angular page) for a company on
its own server / Enterprise.

"Write a full module for …" – only where the server allows it (ZEO_DEV_FULL_MODULES=true in .env; never on ZEO Cloud, where
companies get sandboxed scripts only, like Odoo Online). The AI writes the module after the skeleton of DEVELOPER-MODULES.md
(ZeoAcme). ZEO checks it: file names inside the module only, Python that parses, no change to ZEO's own apps, and lists what a
reviewer must look at (raw SQL, files, programs, network, eval …). The result is a zip to download – it is NEVER installed by
ZEO: a developer reviews, tests and installs it (DEVELOPER-MODULES.md, level 4) and runs makemigrations for that app only.
"""
import ast
import io
import json
import os
import re
import zipfile

from django.apps import apps as django_apps
from django.core.files.base import ContentFile

from Translations.i18n import tr

MS = '/main-sidebar/'
CORE = ('Core', 'EmpManagement', 'OrganisationManager', 'PayrollManagement', 'ProjectManagement', 'UserManagement', 'calendars')
RISKY = [(r'\.raw\(|cursor\(|connection\.cursor', 'raw SQL'), (r'\bsubprocess\b|os\.system|os\.popen', 'runs programs'),
         (r'\bopen\(|\bos\.remove|shutil\.', 'reads or writes files'), (r'\brequests\.|urllib|http\.client|socket\.', 'network calls'),
         (r'\beval\(|\bexec\(|__import__|pickle\.loads', 'runs code from data'), (r'csrf_exempt|AllowAny|permission_classes\s*=\s*\[\s*\]', 'opens access'),
         (r'\bsettings\.SECRET_KEY|\.env\b', 'reads secrets')]


def allowed():
    return os.environ.get('ZEO_DEV_FULL_MODULES', '').strip().lower() in ('1', 'true', 'yes', 'on')


def _skeleton():
    p = os.path.join(os.path.dirname(__file__), 'data', 'module_skeleton.json')
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:   # noqa: BLE001
        return {}


def _system():
    sk = _skeleton()
    files = '\n\n'.join(f'--- {k}\n{v}' for k, v in sk.items())
    return ('You write a ZEO Books level 4 code module: a Django app (REST API with Django REST framework) and an Angular standalone page, '
            'exactly in the style of this skeleton (rename ZeoAcme / acme):\n' + files +
            '\nRules: the app is new (name ZeoSomething); link to ZEO tables only with ForeignKey from your own tables '
            "('EmpManagement.emp_master', 'OrganisationManager.brnch_mstr', 'UserManagement.CustomUser'); never change ZEO's apps; no raw "
            'SQL, no files, no network, no eval; rights come from AccessControl (add_/change_/delete_/view_<model>); English texts in plain '
            'whole phrases and their Arabic in ar_terms_<app>.json. Answer ONLY with JSON: {"app": "ZeoSomething", "slug": "something", '
            '"title": text, "summary": two sentences, "files": [{"path": "backend/ZeoSomething/models.py" | … | "frontend/zeo-something/x.component.ts" '
            '| "backend/ar_terms_something.json", "content": text}]}. Files: apps.py, __init__.py, models.py, serializers.py, views.py, urls.py, '
            'migrations/__init__.py, tests/test_api.py and the Angular component. No migrations (the developer makes them).')


def check(p):
    """(app, slug, files {path: content}, problems [refusing], notes [for the reviewer])."""
    app = str(p.get('app') or '')
    slug = str(p.get('slug') or '').lower()
    problems, notes, files = [], [], {}
    if not re.fullmatch(r'Zeo[A-Z][A-Za-z0-9]{2,30}', app):
        problems.append(f'The app name "{app}" must look like ZeoSomething.')
    elif django_apps.is_installed(app) or app in CORE:
        problems.append(f'An app named {app} exists already in ZEO.')
    if not re.fullmatch(r'[a-z][a-z0-9-]{1,30}', slug):
        problems.append(f'The short name "{slug}" must be small letters.')
    ok_path = re.compile(rf'^(backend/{re.escape(app)}/(__init__|apps|models|serializers|views|urls|admin|services)\.py|'
                         rf'backend/{re.escape(app)}/migrations/__init__\.py|backend/{re.escape(app)}/tests/(__init__|test_\w+)\.py|'
                         rf'backend/ar_terms_{re.escape(slug.replace("-", "_"))}\.json|frontend/zeo-{re.escape(slug)}/[\w.-]+\.(ts|html|css)|README\.md)$')
    for f in (p.get('files') or [])[:40]:
        path, content = str(f.get('path') or '').strip(), str(f.get('content') or '')
        if not ok_path.match(path) or '..' in path:
            problems.append(f'File outside the module refused: {path}')
            continue
        if len(content) > 60000:
            problems.append(f'{path} is too long.')
            continue
        if path.endswith('.py'):
            try:
                ast.parse(content)
            except SyntaxError as e:
                problems.append(f'{path}: Python error on line {e.lineno}.')
            for c in CORE:
                if re.search(rf'from\s+{c}\.(models|views|serializers)\s+import\s+\w+.*\n.*\.(save|delete|update)\(', content):
                    notes.append(f'{path}: writes into a ZEO app ({c}) – check it only reads.')
        if path.endswith('.json'):
            try:
                json.loads(content)
            except ValueError:
                problems.append(f'{path} is not valid JSON.')
        for rx, what in RISKY:
            if re.search(rx, content):
                notes.append(f'{path}: {what} – review before installing.')
        files[path] = content
    must = [f'backend/{app}/{x}' for x in ('apps.py', 'models.py', 'views.py', 'urls.py')]
    for m in must:
        if m not in files:
            problems.append(f'{m} is missing.')
    return app, slug, files, problems, notes


def readme(app, slug, title, summary):
    return f"""# {title} – code module drafted by Ask ZEO (ZEO Books level 4)

{summary}

**Not installed.** Ask ZEO drafted this module with the company's AI. A developer must read every file, test it and install it on the
company's own server – ZEO never installs it. Code modules are not sandboxed: they run with full server rights.

Install (DEVELOPER-MODULES.md, level 4):
1. Copy `backend/{app}` next to `manage.py`; add `'{app}'` to `TENANT_APPS` in `zeo/settings.py`.
2. `zeo/urls.py`: `path('{slug}/', include('{app}.urls'))`.
3. `python manage.py makemigrations {app}` – **only this app** – then `python manage.py migrate_schemas`.
4. Frontend: copy `frontend/zeo-{slug}/` to `src/app/`, add a route with `loadComponent` and a menu item; build.
5. Arabic: merge `backend/ar_terms_{slug.replace('-', '_')}.json` into `i18n/packs/ar.json`, run `python manage.py load_language_packs`.
6. Run `backend/{app}/tests/` and test it in the test company first.
"""


def draft(request, c, conv, question, uploads, lang):
    L = lang
    if not c.admin:
        return {'text': tr('Only a company administrator can have code modules drafted.', lang=L), 'source': 'check', 'intent': 'write_module'}
    if not allowed():
        return {'text': tr('Full code modules are for companies on their own server (Enterprise) – this server does not allow them. Use a custom module, '
                           'calculated fields or a script instead (Ask ZEO can make those).', lang=L), 'source': 'check', 'intent': 'write_module'}
    from . import ai
    from .aiwork import _shield, _file_text
    from .models import Upload
    sh = _shield(c)
    ask = question
    doc = next((u for u in uploads or [] if u.kind == 'document'), None)
    if doc is not None:
        ask += '\n\nThe document:\n' + _file_text(doc)[:12000]
    meter = ai.Meter('developer')
    try:
        r = ai.call(request, meter, _system(), [{'role': 'user', 'content': sh.text(ask)}], max_tokens=8000)
        p = ai.json_of(r)
    except ai.Unavailable as e:
        return {'text': tr('The module could not be drafted: {0}', str(e), lang=L), 'source': 'check', 'intent': 'write_module'}
    finally:
        meter.save(request.user)
    app, slug, files, problems, notes = check(p)
    if problems:
        return {'text': tr('The AI drafted a module ZEO does not accept: {0}. Ask again with more detail.', '; '.join(problems[:5]), lang=L),
                'source': 'check', 'intent': 'write_module', 'mode': 'ai', 'usage_logged': True}
    title = sh.restore(str(p.get('title') or app))[:120]
    summary = sh.restore(str(p.get('summary') or ''))[:600]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for path, content in files.items():
            z.writestr(f'{app}-module/{path}', sh.restore(content))
        z.writestr(f'{app}-module/README.md', readme(app, slug, title, summary) +
                   ('\n## For the reviewer\n' + '\n'.join(f'* {n}' for n in notes) + '\n' if notes else ''))
    data = buf.getvalue()
    gu = Upload(user=request.user, conversation=conv, name=f'{app}-module.zip', size=len(data), kind='other')
    gu.file.save(gu.name, ContentFile(data), save=True)
    txt = tr('I drafted the code module **{0}** ({1} files). It is NOT installed: a developer reviews, tests and installs it on your server '
             '(README in the zip).', title, len(files), lang=L)
    return {'text': txt, 'source': 'document', 'intent': 'write_module', 'mode': 'ai', 'usage_logged': True,
            'cards': [{'type': 'table', 'title': tr('Files', lang=L), 'columns': [tr('File', lang=L), tr('Lines', lang=L)],
                       'rows': [[k, str(v.count('\n') + 1)] for k, v in files.items()]},
                      {'type': 'checks', 'title': tr('For the reviewer', lang=L),
                       'items': [{'level': 'warning', 'text': n, 'link': ''} for n in notes] or
                                [{'level': 'info', 'text': tr('No raw SQL, files, programs, network or eval found – still read every file.', lang=L), 'link': ''}]},
                      {'type': 'file', 'title': gu.name, 'upload': gu.pk}]}
