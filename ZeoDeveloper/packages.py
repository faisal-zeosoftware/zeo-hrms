"""ZEO Developer – export / import of modules as a JSON package (D9) and the demo apps (D7).

A package moves a module from the test company to the live one: its fields, Blueprint, formulas, the rate tables they use, the
module's scripts (switched off after import – test, then switch on), reports, print layouts and scheduled calculations. Records are
never in a package. Import first shows what it would do (preview: new / changed / unchanged per item), then applies it in one
transaction.
"""
import json
import os

from django.db import transaction

from .models import RateTable, Script, ModuleReport, PrintLayout, ScheduledCalc, InstalledPackage

FORMAT = 'zeo-developer-package'
FORMAT_VERSION = 1
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), 'templates_apps')


class PackageError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


def export(module_keys):
    from ZeoFlow.models import CustomModule
    import re
    mods = list(CustomModule.objects.filter(key__in=module_keys))
    if not mods:
        raise PackageError('Choose at least one module to export.')
    keys = [m.key for m in mods]
    tables = set()
    for m in mods:
        for f in m.fields:
            for txt in (f.get('options') or '', f.get('show_if') or '', f.get('required_if') or ''):
                tables |= set(re.findall(r'LOOKUP\(\s*"([a-z][a-z0-9_]*)"', txt, re.I))
                if f['type'] == 'lookup':
                    tables.add(txt.split('.', 1)[0].strip())
    scripts = list(Script.objects.filter(module_key__in=keys))
    for s in scripts:
        tables |= set(re.findall(r'lookup\(\s*["\']([a-z][a-z0-9_]*)["\']', s.code or '', re.I))
    return {
        'format': FORMAT, 'format_version': FORMAT_VERSION, 'api_version': 'v1',
        'modules': [{'key': m.key, 'name': m.name, 'plural': m.plural, 'icon': m.icon, 'description': m.description, 'prefix': m.prefix,
                     'fields': m.fields, 'who_create': m.who_create, 'who_view': m.who_view, 'blueprint': m.blueprint} for m in mods],
        'rate_tables': [{'key': t.key, 'name': t.name, 'columns': t.columns, 'rows': t.rows} for t in RateTable.objects.filter(key__in=tables)],
        'scripts': [{'key': s.key, 'name': s.name, 'description': s.description, 'module_key': s.module_key, 'model_key': s.model_key, 'trigger': s.trigger,
                     'move_key': s.move_key, 'button_label': s.button_label, 'day_of_month': s.day_of_month, 'hour': s.hour, 'code': s.code} for s in scripts],
        'reports': [{'module_key': r.module_key, 'name': r.name, 'description': r.description, 'section': r.section, 'kind': r.kind, 'columns': r.columns,
                     'group_by': r.group_by, 'date_field': r.date_field, 'states': r.states, 'who': r.who} for r in ModuleReport.objects.filter(module_key__in=keys)],
        'prints': [{'module_key': p.module_key, 'name': p.name, 'html': p.html, 'paper': p.paper} for p in PrintLayout.objects.filter(module_key__in=keys)],
        'scheduled': [{'module_key': c.module_key, 'name': c.name, 'field_key': c.field_key, 'formula': c.formula, 'frequency': c.frequency, 'states': c.states}
                      for c in ScheduledCalc.objects.filter(module_key__in=keys)],
    }


def _load(pkg):
    if isinstance(pkg, (str, bytes)):
        try:
            pkg = json.loads(pkg)
        except ValueError:
            raise PackageError('The file is not a ZEO Developer package (not JSON).')
    if not isinstance(pkg, dict) or pkg.get('format') != FORMAT:
        raise PackageError('The file is not a ZEO Developer package.')
    if int(pkg.get('format_version') or 0) > FORMAT_VERSION:
        raise PackageError('The package was made by a newer ZEO – update this server first.')
    for k in ('modules', 'rate_tables', 'scripts', 'reports', 'prints', 'scheduled'):
        if not isinstance(pkg.get(k, []), list):
            raise PackageError(f'"{k}" is not a list.')
    if len(json.dumps(pkg)) > 5_000_000:
        raise PackageError('The package is larger than 5 MB.')
    return pkg


def _same(a, b, keys):
    return all(json.dumps(a.get(k), sort_keys=True, default=str) == json.dumps(b.get(k), sort_keys=True, default=str) for k in keys)


def preview(pkg):
    """[{'kind', 'key', 'name', 'action': new|change|same, 'note'}] – nothing is changed."""
    from ZeoFlow.models import CustomModule
    pkg = _load(pkg)
    out = []
    for m in pkg.get('modules', []):
        cur = CustomModule.objects.filter(key=m.get('key')).first()
        if cur is None:
            out.append({'kind': 'module', 'key': m.get('key'), 'name': m.get('plural') or m.get('name'), 'action': 'new'})
        else:
            curd = {'fields': cur.fields, 'blueprint': cur.blueprint, 'name': cur.name, 'plural': cur.plural, 'prefix': cur.prefix,
                    'who_create': cur.who_create, 'who_view': cur.who_view}
            same = _same(curd, m, ('fields', 'blueprint', 'name', 'plural', 'prefix', 'who_create', 'who_view'))
            note = f'{cur.records.count()} records kept' if not same else ''
            out.append({'kind': 'module', 'key': m.get('key'), 'name': m.get('plural') or m.get('name'), 'action': 'same' if same else 'change', 'note': note})
    for t in pkg.get('rate_tables', []):
        cur = RateTable.objects.filter(key=t.get('key')).first()
        act = 'new' if cur is None else ('same' if _same({'columns': cur.columns, 'rows': cur.rows}, t, ('columns', 'rows')) else 'change')
        out.append({'kind': 'rate table', 'key': t.get('key'), 'name': t.get('name'), 'action': act})
    for s in pkg.get('scripts', []):
        cur = Script.objects.filter(key=s.get('key')).first()
        act = 'new' if cur is None else ('same' if (cur.code == s.get('code') and cur.trigger == s.get('trigger')) else 'change')
        out.append({'kind': 'script', 'key': s.get('key'), 'name': s.get('name'), 'action': act, 'note': 'imported switched off' if act != 'same' else ''})
    for kind, M, lst in (('report', ModuleReport, 'reports'), ('print layout', PrintLayout, 'prints'), ('scheduled calculation', ScheduledCalc, 'scheduled')):
        for r in pkg.get(lst, []):
            cur = M.objects.filter(module_key=r.get('module_key'), name=r.get('name')).first()
            out.append({'kind': kind, 'key': r.get('module_key'), 'name': r.get('name'), 'action': 'new' if cur is None else 'change'})
    return out


@transaction.atomic
def apply(pkg, user, source='import', name=''):
    from ZeoFlow import custom as C
    from ZeoFlow.models import CustomModule
    from . import services as S
    from .reports import check_report, check_html
    pkg = _load(pkg)
    summary = {'modules': 0, 'rate_tables': 0, 'scripts': 0, 'reports': 0, 'prints': 0, 'scheduled': 0}
    for t in pkg.get('rate_tables', []):   # first: formulas of the modules look them up
        save_table(t, user)
        summary['rate_tables'] += 1
    for m in pkg.get('modules', []):
        cur = CustomModule.objects.filter(key=m.get('key')).first()
        try:
            C.save_module(dict(m), user, cur)
        except C.CustomError as e:
            raise PackageError(f'Module {m.get("key")}: {e}')
        summary['modules'] += 1
    for s in pkg.get('scripts', []):
        cur = Script.objects.filter(key=s.get('key')).first()
        data = {k: s.get(k) for k in ('key', 'name', 'description', 'module_key', 'model_key', 'trigger', 'move_key', 'button_label', 'day_of_month', 'hour', 'code')}
        data['note'] = f'Imported ({source})'
        try:
            obj = S.save_script(data, user, cur)
        except S.DevError as e:
            raise PackageError(f'Script {s.get("key")}: {e.message}')
        if cur is None or cur.code != obj.code:
            Script.objects.filter(pk=obj.pk).update(active=False)
        summary['scripts'] += 1
    mods = {m.key: m for m in CustomModule.objects.all()}
    for r in pkg.get('reports', []):
        m = mods.get(r.get('module_key'))
        if m is None:
            raise PackageError(f'Report {r.get("name")}: module {r.get("module_key")} is not in the company.')
        try:
            d = check_report(r, m)
        except ValueError as e:
            raise PackageError(f'Report {r.get("name")}: {e}')
        ModuleReport.objects.update_or_create(module_key=m.key, name=d['name'], defaults=d)
        summary['reports'] += 1
    for p in pkg.get('prints', []):
        try:
            check_html(p.get('html') or '')
        except ValueError as e:
            raise PackageError(f'Print layout {p.get("name")}: {e}')
        PrintLayout.objects.update_or_create(module_key=p.get('module_key'), name=str(p.get('name'))[:120],
                                             defaults={'html': p.get('html') or '', 'paper': str(p.get('paper') or 'A4')[:10]})
        summary['prints'] += 1
    for c in pkg.get('scheduled', []):
        from .schedule import check_calc
        m = mods.get(c.get('module_key'))
        if m is None:
            raise PackageError(f'Scheduled calculation {c.get("name")}: module {c.get("module_key")} is not in the company.')
        try:
            d = check_calc(c, m)
        except ValueError as e:
            raise PackageError(f'Scheduled calculation {c.get("name")}: {e}')
        ScheduledCalc.objects.update_or_create(module_key=m.key, name=d['name'], defaults=d)
        summary['scheduled'] += 1
    InstalledPackage.objects.create(name=(name or ', '.join(m.get('plural') or m.get('key') for m in pkg.get('modules', [])) or 'Package')[:120],
                                    source=source, summary=summary, installed_by_id=user.pk)
    return summary


def save_table(t, user, obj=None):
    import re
    key = str(t.get('key') or getattr(obj, 'key', '')).strip()
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,39}', key):
        raise PackageError('Rate table key: small letters, digits and _, starting with a letter.')
    cols = [str(c).strip() for c in (t.get('columns') or []) if str(c).strip()]
    if len(cols) < 2:
        raise PackageError(f'Rate table {key}: at least two columns (the key and a value).')
    if len(set(cols)) != len(cols):
        raise PackageError(f'Rate table {key}: a column name is used twice.')
    rows = []
    seen = set()
    for i, r in enumerate(t.get('rows') or [], 1):
        if not isinstance(r, dict):
            raise PackageError(f'Rate table {key}, row {i}: not valid.')
        row = {c: ('' if r.get(c) is None else str(r.get(c))[:200]) for c in cols}
        k0 = row[cols[0]].strip().lower()
        if not k0:
            continue
        if k0 in seen:
            raise PackageError(f'Rate table {key}: "{row[cols[0]]}" is in the first column twice.')
        seen.add(k0)
        rows.append(row)
    if len(rows) > 5000:
        raise PackageError(f'Rate table {key}: at most 5,000 rows.')
    other = RateTable.objects.filter(key=key).exclude(pk=getattr(obj, 'pk', None)).first()
    if obj is None and other is not None:
        obj = other
    elif other is not None:
        raise PackageError(f'Another rate table has the key "{key}".')
    obj = obj or RateTable(key=key)
    obj.key, obj.name, obj.columns, obj.rows, obj.updated_by_id = key, str(t.get('name') or key)[:80], cols, rows, user.pk
    obj.save()
    return obj


# ------------------------------------------------------------------ demo apps (D7)
def templates():
    out = []
    for fn in sorted(os.listdir(TEMPLATES_DIR)):
        if fn.endswith('.json'):
            with open(os.path.join(TEMPLATES_DIR, fn), encoding='utf-8') as f:
                d = json.load(f)
            out.append({'key': fn[:-5], 'name': d.get('title'), 'description': d.get('description'),
                        'modules': [m['plural'] for m in d.get('modules', [])], 'scripts': [s['name'] for s in d.get('scripts', [])],
                        'reports': [r['name'] for r in d.get('reports', [])]})
    return out


def template(key):
    import re
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,40}', str(key or '')):
        raise PackageError('Unknown demo app.', 404)
    p = os.path.join(TEMPLATES_DIR, f'{key}.json')
    if not os.path.exists(p):
        raise PackageError('Unknown demo app.', 404)
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def install_template(key, user):
    d = template(key)
    summary = apply(d, user, source='template', name=d.get('title') or key)
    from .models import Script
    # demo scripts are switched on at once (they were tested with the package); the admin can switch them off
    Script.objects.filter(key__in=[s['key'] for s in d.get('scripts', [])]).update(active=True, stopped=False, failures=0)
    return summary
