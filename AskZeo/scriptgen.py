"""Ask ZEO 1.73.4 (A15) – the AI writes a ZEO Developer script.

"Write a script that ...": when an SOP or a request needs logic the building blocks cannot express (camp bed allocation, a fuel
check, a client's own import layout), the company's AI writes a script in the ZEO script API (ZeoDeveloper/api.py, v1). ZEO checks
it (the sandbox's own check), runs it on sample data in a transaction that is rolled back (Developer › Test), and shows the code and
the test run. Nothing is saved before the administrator has read the code and confirms; Confirm saves the script (with its version
history) and switches it on, Undo switches it off and deletes it.

Rules kept: company administrators only, Developer mode on, the switch "Ask ZEO may change setup"; the script runs in the sandbox
with the script API only (no files, database or internet except sites the administrator allowed), in this company's schema, within the
time / step / record limits of Developer › Settings; ZEO's own code is never changed.
"""
import json
import re

from Translations.i18n import tr

from . import changes

MS = '/main-sidebar/'
TRIGGERS = ('manual', 'create', 'update', 'move', 'button', 'daily', 'monthly', 'app_create', 'app_update')


def _modules_text():
    from ZeoFlow.models import CustomModule
    out = []
    for m in CustomModule.objects.order_by('key')[:40]:
        fl = ', '.join(f"{f['key']} ({f['type']})" for f in (m.fields or [])[:30])
        mv = ', '.join(t['key'] for t in (m.blueprint or {}).get('transitions', [])[:12])
        out.append(f'- {m.key} "{m.name}": fields {fl}; moves {mv}')
    return '\n'.join(out) or '(no custom modules yet)'


def _system():
    from ZeoDeveloper import api as A
    return ('You write scripts for ZEO Developer. The scripts are restricted Python run in a sandbox: no import, no files, no database, no '
            'internet (except http.get / http.post to sites an administrator allowed), no dunder names, no classes. Only this API exists:\n'
            + (A.__doc__ or '') + '\nThe custom modules of this company:\n' + _modules_text() +
            '\nAnswer ONLY with JSON: {"name": short name, "key": small letters and _, "description": one sentence, "trigger": one of '
            + '|'.join(TRIGGERS) + ', "module_key": the custom module for a record trigger else "", "move_key": a move for trigger move else "", '
            '"button_label": for trigger button else "", "model_key": for app_create / app_update e.g. "ZeoCRM.lead" else "", "hour": 0-23, '
            '"day_of_month": 1-28, "code": the script, "test_note": what the test run shows}. Keep the code short, with comments in plain words. '
            'Never change payroll, rights, login or security; write into other apps only through actions.*.')


def draft(request, c, conv, question, uploads, lang):
    L = lang
    if not c.admin:
        return {'text': tr('Only a company administrator can make scripts (ZEO Developer).', lang=L), 'source': 'check', 'intent': 'write_script'}
    from ZeoDeveloper.models import DeveloperSettings, Script
    if not DeveloperSettings.get().developer_mode:
        return {'text': tr('Developer mode is off in this company (Settings › Developer mode). Switch it on, then ask again.', lang=L),
                'source': 'check', 'intent': 'write_script', 'links': [{'label': tr('Developer', lang=L), 'url': MS + 'developer'}]}
    from . import ai
    from .aiwork import _shield, _file_text
    sh = _shield(c)
    ask = question
    doc = next((u for u in uploads or [] if u.kind == 'document'), None)
    if doc is not None:
        ask += '\n\nThe document:\n' + _file_text(doc)[:12000]
    meter = ai.Meter('developer')
    try:
        r = ai.call(request, meter, _system(), [{'role': 'user', 'content': sh.text(ask)}], max_tokens=2500)
        p = ai.json_of(r)
    except ai.Unavailable as e:
        return {'text': tr('The script could not be written: {0}', str(e), lang=L), 'source': 'check', 'intent': 'write_script'}
    finally:
        meter.save(request.user)
    code = sh.restore(str(p.get('code') or '')).strip()
    name = sh.restore(str(p.get('name') or 'Script by Ask ZEO')).strip()[:120]
    key = re.sub(r'[^a-z0-9_]', '', str(p.get('key') or re.sub(r'[^a-z0-9]+', '_', name.lower())).lower()).strip('_')[:50] or 'ask_zeo_script'
    if not re.match(r'[a-z]', key):
        key = 's_' + key
    base, n = key, 2
    while Script.objects.filter(key=key).exists():
        key, n = f'{base}_{n}', n + 1
    trig = p.get('trigger') if p.get('trigger') in TRIGGERS else 'manual'
    data = {'name': name, 'key': key, 'description': (sh.restore(str(p.get('description') or '')) + ' – written by Ask ZEO')[:300], 'trigger': trig,
            'module_key': str(p.get('module_key') or '') if trig in ('create', 'update', 'move', 'button') else '',
            'move_key': str(p.get('move_key') or '') if trig == 'move' else '', 'button_label': str(p.get('button_label') or '')[:40] if trig == 'button' else '',
            'model_key': str(p.get('model_key') or '') if trig in ('app_create', 'app_update') else '',
            'hour': max(0, min(23, int(p.get('hour') or 2))), 'day_of_month': max(1, min(28, int(p.get('day_of_month') or 1))), 'code': code,
            'note': 'Written by Ask ZEO'}
    # 1) the sandbox's own check
    from ZeoDeveloper.sandbox import ScriptError, check
    try:
        check(code)
    except ScriptError as e:
        return {'text': tr('The AI wrote a script the sandbox does not accept: {0}. Ask again with more detail, or write it in Developer › Scripts.', str(e).rstrip('. '), lang=L),
                'source': 'check', 'intent': 'write_script', 'cards': [{'type': 'code', 'title': name, 'body': code}]}
    # 2) a test run on sample data (rolled back): the newest record of the module for a record script
    from ZeoDeveloper import services as DS
    record, ref = None, ''
    if data['module_key']:
        from ZeoFlow.models import CustomModule, CustomRecord
        m = CustomModule.objects.filter(key=data['module_key']).first()
        if m is None:
            return {'text': tr('The script belongs to the module "{0}", which does not exist.', data['module_key'], lang=L), 'source': 'check',
                    'intent': 'write_script', 'cards': [{'type': 'code', 'title': name, 'body': code}]}
        rec = CustomRecord.objects.filter(module=m).order_by('-pk').first()
        if rec is not None:
            record, ref = rec, f'{m.key} {rec.number}'
    try:
        run = DS.run_code(code, request.user, trigger='test', record=record, dry_run=True, trigger_info={'event': 'test'}, record_ref=ref)
        res = DS.run_json(run)
    except DS.DevError as e:
        res = {'status': 'error', 'error': str(e), 'output': '', 'changes': [], 'line': None}
    ok = res.get('status') == 'ok'
    test_rows = [[tr('Result', lang=L), tr('Passed', lang=L) if ok else tr('Error', lang=L) + (f" (line {res.get('line')})" if res.get('line') else '') + ': ' + str(res.get('error') or '')],
                 [tr('Sample record', lang=L), ref or tr('none – the script ran without a record', lang=L)],
                 [tr('Records it would change', lang=L), str(res.get('records_changed', 0))],
                 [tr('Output', lang=L), str(res.get('output') or '')[:600]]]
    trig_label = dict(__import__('ZeoDeveloper.models', fromlist=['TRIGGERS']).TRIGGERS).get(trig, trig)
    ch = changes.preview(request, conv, 'script', tr('New script: {0}', name, lang=L),
                         {'api': [{'method': 'post', 'path': '/developer/api/scripts/', 'data': data, 'label': tr('Save the script {0}', name, lang=L),
                                   'show': trig_label, 'undo': {'method': 'delete', 'path': '/developer/api/scripts/{id0}/'}},
                                  {'method': 'put', 'path': '/developer/api/scripts/{id0}/', 'data': {'active': True}, 'label': tr('Switch it on', lang=L),
                                   'show': '', 'undo': {'method': 'put', 'path': '/developer/api/scripts/{id0}/', 'data': {'active': False}}}]},
                         tr('Saved in Developer › Scripts with its version history and switched on. Undo switches it off and deletes it.', lang=L), group='setup')
    txt = tr('I wrote the script **{0}** ({1}). Read the code and the test run on sample data – nothing is saved before you confirm.', name, trig_label, lang=L)
    if not ok:
        txt += '\n\n' + tr('The test run failed – correct the request and ask again, or fix the code in Developer › Scripts after saving.', lang=L)
    return {'text': txt, 'source': 'check', 'intent': 'write_script', 'mode': 'ai', 'usage_logged': True,
            'cards': [{'type': 'code', 'title': name, 'body': code},
                      {'type': 'table', 'title': tr('Test on sample data (rolled back)', lang=L), 'columns': [tr('Check', lang=L), tr('Answer', lang=L)], 'rows': test_rows},
                      changes.card(ch, L)],
            'links': [{'label': tr('Developer › Scripts', lang=L), 'url': MS + 'developer'}]}
