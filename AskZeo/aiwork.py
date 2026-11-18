"""Ask ZEO 1.72.0 – work done with the company's AI. Facts, amounts and every change stay on the server: the AI only reads
the words and returns a structured proposal; the server checks it, works out the figures and shows a preview to confirm.

* 99   messy client file (any layout) → mapped to a sheet of the ZEO data workbook → preview → the workbook's own upload + check
* 98   SOP / workflow document → steps on ZEO screens, the gaps, and a Word guide (with screenshots when the server can take them)
* 126  the company's policy document → proposed leave policy lines and allowances → preview → confirm (undo)
* 131  bulk update from a sentence ("give everyone in Sales a 5% housing increase from January") → preview table → confirm (undo)
* 133 / 134  letters and messages;  136 quizzes and UAT scripts;  138 translation
"""
from zeo.tz import today as _zt_today   # v1.73.16 the user's / company's day, not the server's UTC day
import io
import json
import logging
import os
import re
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.apps import apps
from django.core.files.base import ContentFile

from Translations.i18n import tr

from . import ai, changes
from . import help as H
from . import scope as S
from .privacy import Shield

log = logging.getLogger(__name__)
M = apps.get_model
MS = '/main-sidebar/'


def _shield(c):
    from .models import AskSettings
    st = AskSettings.get()
    return Shield(st.hide_salary, st.mask_names, ai._employee_names(c) if st.mask_names else None)


def _done(request, meter, text, intent, source='general', extra=None, shield=None, lang='en'):
    if shield is not None:
        text = shield.restore(text)
    meter.save(request.user)
    out = {'text': text, 'source': source, 'intent': intent, 'mode': 'ai', 'usage_logged': True,
           'badges': [tr('General knowledge', lang=lang)] if source == 'general' else []}
    if extra:
        out.update(extra)
    return out


def _file_text(u):
    from .docs import extract
    with u.file.open('rb') as f:
        return extract(u.name, f.read())


# ------------------------------------------------------------------ 133 / 134 letters and messages
def write(request, c, conv, question, uploads, lang):
    meter, sh = ai.Meter('write'), _shield(c)
    facts = ''
    emp = S.find_employee(c, question) if S.is_hr(c) else c.emp
    if emp is not None:
        e = S.employees(c).filter(pk=emp.pk).select_related('emp_desgntn_id', 'emp_dept_id', 'emp_branch_id').first()
        if e is not None:
            facts = (f"\nFacts about the employee (use only these): name {S.emp_name(e)}, code {e.emp_code}, designation {e.emp_desgntn_id or ''}, "
                     f"department {e.emp_dept_id or ''}, branch {e.emp_branch_id or ''}, joined {e.emp_joined_date or ''}.")
    from django.db import connection
    system = ('You are "Ask ZEO" in ZEO HRMS. Write the letter, e-mail, announcement or message the user asks for, ready to send, in '
              f"{'Arabic' if lang == 'ar' else 'English'} unless the user asks for another language. Company: {getattr(getattr(connection, 'tenant', None), 'name', '') or ''}. "
              'Use placeholders in [square brackets] for anything you do not know. No legal promises. Today is ' + _zt_today().strftime('%d %B %Y') + '.' + sh.text(facts))
    r = ai.call(request, meter, system, [{'role': 'user', 'content': sh.text(question)}], max_tokens=1200)
    t = ai.text_of(r)
    return _done(request, meter, t, 'write', 'general', {'cards': [{'type': 'letter', 'title': tr('Draft', lang=lang), 'body': sh.restore(t), 'lang': lang}]}, sh, lang)


# ------------------------------------------------------------------ 138 translation
def translate(request, c, conv, question, uploads, lang):
    meter, sh = ai.Meter('translate'), _shield(c)
    text = question
    m = re.search(r'(?:translate|ترجم)\w*\s*(?:this|the following|هذا|التالي)?\s*(?:into|to|الى|إلى)?\s*(arabic|english|العربيه|العربية|الانجليزيه|الإنجليزية)?\s*[:：]?\s*(.+)$', question, re.I | re.S)
    if m and m.group(2).strip():
        text = m.group(2).strip()
    if uploads:
        text = _file_text(uploads[0])[:12000] or text
    to = 'English' if re.search(r'[؀-ۿ]', text) and not re.search(r'arabic|العربي', question, re.I) else 'Arabic'
    if re.search(r'\benglish\b|الانجليزي|الإنجليزي', question, re.I):
        to = 'English'
    system = (f'Translate the text into {to}. Keep names, numbers, codes and placeholders like ⟦N1⟧ exactly. Use the terms of UAE HR and payroll '
              '(e.g. gratuity = مكافأة نهاية الخدمة, payslip = قسيمة الراتب, leave = إجازة). Answer with the translation only.')
    r = ai.call(request, meter, system, [{'role': 'user', 'content': sh.text(text)}], max_tokens=3000, feature='documents' if uploads else 'ask', small=True)   # v1.73.2: typed text is Ask ZEO, a file is Documents
    t = ai.text_of(r)
    return _done(request, meter, t, 'translate', 'general',
                 {'cards': [{'type': 'letter', 'title': tr('Translation into {0}', tr(to, lang=lang), lang=lang), 'body': sh.restore(t), 'lang': 'ar' if to == 'Arabic' else 'en'}]}, sh, lang)


# ------------------------------------------------------------------ 136 quizzes and UAT scripts
def quiz(request, c, conv, question, uploads, lang):
    from .docs import search
    meter, sh = ai.Meter('quiz'), _shield(c)
    material = []
    for g in H.find_guides(c, question, 3):
        material.append(f"ZEO guide – {g['title']['en']}: " + ' / '.join(s['en'] for s in g['steps']))
    for s in H.find_screens(c, question, 6):
        material.append(f"ZEO screen – {s['where']}: {s['hint']}")
    for h in search(c, question, 4):
        material.append(f"Company document {h['document']} › {h['section']}: {h['text'][:1200]}")
    uat = bool(re.search(r'\buat\b|test script|test cases?|acceptance', question, re.I))
    system = ('You are "Ask ZEO". ' + ('Write a user acceptance test (UAT) script as a markdown table: No., Screen, Steps, Expected result, Pass/Fail. '
                                        'Use the ZEO screens in the material.' if uat else
                                        'Write a short quiz (5 to 10 multiple-choice questions with the right answer marked and a one-line reason) on the topic.')
              + ' Base it on the material below; when it is not enough, say what is missing. Answer in ' + ('Arabic.' if lang == 'ar' else 'English.')
              + '\n\nMaterial:\n' + sh.text('\n'.join(material) or '(none found)'))
    r = ai.call(request, meter, system, [{'role': 'user', 'content': sh.text(question)}], max_tokens=2500)
    return _done(request, meter, ai.text_of(r), 'quiz', 'help' if material else 'general', None, sh, lang)


# ------------------------------------------------------------------ 131 bulk update from a sentence
def _lookup(model, field, name):
    if not name:
        return None
    Mdl = M(*model.split('.'))
    return Mdl.objects.filter(**{f'{field}__iexact': str(name).strip()}).first() or Mdl.objects.filter(**{f'{field}__icontains': str(name).strip()}).first()


def bulk(request, c, conv, question, uploads, lang):
    if not S.can(c, {'change_employeesalarystructure', 'add_employeesalarystructure'}):
        return {'text': tr('Bulk salary changes need the right to change employee salaries.', lang=lang), 'source': 'check', 'intent': 'bulk'}
    meter = ai.Meter('bulk')
    D = M('OrganisationManager', 'dept_master')
    B = M('OrganisationManager', 'brnch_mstr')
    SC = M('PayrollManagement', 'SalaryComponent')
    comps = sorted(set(SC.objects.values_list('name', flat=True)))
    lists = {'departments': list(D.objects.values_list('dept_name', flat=True)[:200]), 'branches': list(B.objects.values_list('branch_name', flat=True)[:200]),
             'components': comps[:200]}
    system = ('Read the request for a salary change and answer ONLY with JSON: {"component": one of components, "kind": "percent"|"amount"|"set", '
              '"value": number, "department": one of departments or null, "branch": one of branches or null, "designation": text or null, '
              '"employees": [employee codes] or [], "effective_from": "YYYY-MM-DD" or null, "understood": "the change in one sentence"}. '
              f'Today is {_zt_today().isoformat()}; "from January" means the next 1 January. Lists: ' + json.dumps(lists, ensure_ascii=False))
    r = ai.call(request, meter, system, [{'role': 'user', 'content': question}], max_tokens=500, small=True)
    p = ai.json_of(r)
    comp_name = p.get('component')
    if comp_name not in comps:
        meter.save(request.user)
        return {'text': tr('I could not tell which salary component to change ({0}). Name it as on Salary components.', comp_name or '–', lang=lang), 'source': 'check', 'intent': 'bulk'}
    try:
        value = Decimal(str(p.get('value')))
    except Exception:
        meter.save(request.user)
        return {'text': tr('I could not read the amount or the percentage.', lang=lang), 'source': 'check', 'intent': 'bulk'}
    kind = p.get('kind') if p.get('kind') in ('percent', 'amount', 'set') else 'percent'
    if kind == 'percent' and not (Decimal('-50') <= value <= Decimal('100')):
        meter.save(request.user)
        return {'text': tr('A change of {0}% is outside −50 % to +100 % – change it on Employee salary instead.', value, lang=lang), 'source': 'check', 'intent': 'bulk'}
    emps = S.employees(c).filter(is_active=True)
    where = []
    if p.get('department'):
        d = _lookup('OrganisationManager.dept_master', 'dept_name', p['department'])
        if d is None:
            meter.save(request.user)
            return {'text': tr('No department "{0}".', p['department'], lang=lang), 'source': 'check', 'intent': 'bulk'}
        emps = emps.filter(emp_dept_id=d)
        where.append(tr('department {0}', d.dept_name, lang=lang))
    if p.get('branch'):
        b = _lookup('OrganisationManager.brnch_mstr', 'branch_name', p['branch'])
        if b is None:
            meter.save(request.user)
            return {'text': tr('No branch "{0}".', p['branch'], lang=lang), 'source': 'check', 'intent': 'bulk'}
        emps = emps.filter(emp_branch_id=b)
        where.append(tr('branch {0}', b.branch_name, lang=lang))
    if p.get('designation'):
        emps = emps.filter(emp_desgntn_id__desgntn_job_title__icontains=p['designation'])
        where.append(tr('designation {0}', p['designation'], lang=lang))
    if p.get('employees'):
        emps = emps.filter(emp_code__in=[str(x) for x in p['employees']][:500])
    eff = None
    if p.get('effective_from'):
        try:
            eff = date.fromisoformat(str(p['effective_from']))
        except ValueError:
            eff = None
    ESS = M('PayrollManagement', 'EmployeeSalaryStructure')
    rows = ESS.objects.filter(employee__in=emps, component__name=comp_name, is_active=True).select_related('employee', 'component')
    ops, steps = [], []
    for r0 in rows[:1000]:
        if r0.valid_until and r0.valid_until < _zt_today():
            continue
        old = Decimal(str(r0.amount or 0))
        new = (old * (1 + value / 100)) if kind == 'percent' else (old + value if kind == 'amount' else value)
        new = new.quantize(Decimal('0.01'), ROUND_HALF_UP)
        if new == old:
            continue
        lab = f'{S.emp_label(r0.employee)} – {comp_name}'
        if eff:
            # 1.72.0 fix: a change from a date goes through Payroll › Revise salary (the product's own dated change with its approval
            # workflow, arrears for past dates); an employee has one salary row per component, so a second dated row cannot be made.
            k = len(steps)
            steps.append({'method': 'post', 'path': '/payroll-plus/api/payctl/revisions/', 'label': lab, 'show': f'{old:,.2f} → {new:,.2f} from {eff:%d/%m/%Y}',
                          'data': {'employee': r0.employee_id, 'effective_from': eff.isoformat(), 'reason': (p.get('understood') or 'Ask ZEO')[:255],
                                   'lines': [{'component_id': r0.component_id, 'new': str(new)}]},
                          'undo': {'by_status': {   # waiting → cancel it; applied at once (no approval levels) → a revision back to the old amount
                              'pending': {'method': 'post', 'path': '/payroll-plus/api/payctl/revisions/{id%d}/cancel/' % k},
                              'applied': {'method': 'post', 'path': '/payroll-plus/api/payctl/revisions/',
                                          'data': {'employee': r0.employee_id, 'effective_from': eff.isoformat(), 'reason': 'Undo of an Ask ZEO change',
                                                   'lines': [{'component_id': r0.component_id, 'new': str(old)}]}}}}})
        else:
            ops.append({'op': 'update', 'model': 'PayrollManagement.EmployeeSalaryStructure', 'id': r0.pk, 'values': {'amount': new}, 'label': lab})
    meter.save(request.user)
    if not ops and not steps:
        return {'text': tr('No employee of your branches has {0} matching this request ({1}).', comp_name, ', '.join(where) or tr('everyone', lang=lang), lang=lang),
                'source': 'check', 'intent': 'bulk'}
    n = len(ops) + len(steps)
    desc = {'percent': f'{value:+}%', 'amount': f'{value:+,.2f}', 'set': f'= {value:,.2f}'}[kind]
    summary = ((p.get('understood') or '') + (' ' + tr('From {0}.', eff.strftime('%d/%m/%Y'), lang=lang) if eff else '') +
               ' ' + tr('The amounts are worked out by ZEO; the AI only read the sentence.', lang=lang))
    if steps:
        summary += ' ' + tr('Each change from a date is a salary revision that goes through its approval (Payroll › Revise salary); Undo cancels it while it waits.', lang=lang)
    ch = changes.preview(request, conv, 'bulk', tr('{0} {1} for {2} employee(s)', comp_name, desc, n, lang=lang),
                         {'records': ops} if ops else {'api': steps, 'atomic': True}, summary, group='setup')
    return {'text': tr('I understood: {0}. Check the preview – nothing changes before you confirm.', p.get('understood') or desc, lang=lang),
            'source': 'data', 'intent': 'bulk', 'mode': 'ai', 'usage_logged': True, 'cards': [changes.card(ch, lang)]}


# ------------------------------------------------------------------ 99 messy file → data workbook
def _sheet_columns():
    import openpyxl
    from DataTools.workbook_jobs import TEMPLATE_PATH
    wb = openpyxl.load_workbook(TEMPLATE_PATH, read_only=True)
    out = {}
    for n in wb.sheetnames:
        if n in ('Read me', 'Index', 'Columns', 'Columns help', 'Lists'):
            continue
        hdr = next(wb[n].iter_rows(values_only=True, max_row=1), ())
        out[n] = [h for h in hdr if h and not str(h).startswith('@')]
    wb.close()
    return out


def _read_table(u):
    import csv
    with u.file.open('rb') as f:
        data = f.read()
    low = u.name.lower()
    rows = []
    if low.endswith('.csv'):
        txt = data.decode('utf-8-sig', 'replace')
        rows = [r for r in csv.reader(io.StringIO(txt))]
    else:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb[wb.sheetnames[0]]
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        wb.close()
    rows = [r for r in rows if any(v not in (None, '') for v in r)]
    # the header row: the first row with at least half of its cells filled with text
    hi = 0
    for i, r in enumerate(rows[:15]):
        txt = [v for v in r if isinstance(v, str) and v.strip()]
        if len(txt) >= max(2, len([v for v in r if v not in (None, '')]) // 2):
            hi = i
            break
    hdr = [str(v).strip() if v not in (None, '') else f'Column {j + 1}' for j, v in enumerate(rows[hi])] if rows else []
    return hdr, rows[hi + 1:]


def map_file(request, c, conv, question, uploads, lang):
    if not c.admin:
        return {'text': tr('Only a company administrator can load data workbooks.', lang=lang), 'source': 'check', 'intent': 'map'}
    u = next((x for x in uploads if x.kind == 'spreadsheet'), None)
    if u is None:
        return {'text': tr('Attach the Excel or CSV file to map (📎).', lang=lang), 'source': 'check', 'intent': 'map'}
    hdr, data = _read_table(u)
    if not hdr or not data:
        return {'text': tr('{0} has no rows to map.', u.name, lang=lang), 'source': 'check', 'intent': 'map'}
    sheets = _sheet_columns()
    sh = _shield(c)
    sample = [{h: sh.protect(v, h) for h, v in zip(hdr, r)} for r in data[:3]]
    meter = ai.Meter('map')
    system = ('Map the columns of a client file to ONE sheet of the ZEO data workbook. Answer ONLY with JSON: {"sheet": sheet name, '
              '"map": {"file column": "workbook column" or null}, "notes": [short notes on columns that need attention]}. '
              'Use only these sheets and columns: ' + json.dumps(sheets, ensure_ascii=False))
    r = ai.call(request, meter, system, [{'role': 'user', 'content': json.dumps({'file': u.name, 'columns': hdr, 'sample_rows': sample, 'request': sh.text(question)},
                                                                               default=str, ensure_ascii=False)}], max_tokens=1500, feature='documents', small=True)
    p = ai.json_of(r)
    sheet = p.get('sheet')
    if sheet not in sheets:
        meter.save(request.user)
        return {'text': tr('The AI could not tell which sheet of the workbook this file belongs to.', lang=lang), 'source': 'check', 'intent': 'map'}
    cols = sheets[sheet]
    mp = {k: v for k, v in (p.get('map') or {}).items() if k in hdr and v in cols}
    if not mp:
        meter.save(request.user)
        return {'text': tr('No column of the file matched a column of the sheet {0}.', sheet, lang=lang), 'source': 'check', 'intent': 'map'}
    idx = {h: i for i, h in enumerate(hdr)}
    out_rows = []
    for r in data:
        row = {}
        for src, dst in mp.items():
            v = r[idx[src]] if idx[src] < len(r) else None
            if v not in (None, ''):
                row[dst] = v
        if row:
            out_rows.append(row)
    meter.save(request.user)
    preview_cols = [cname for cname in cols if cname in mp.values()][:8]
    ch = changes.preview(request, conv, 'map', tr('Put {0} row(s) of {1} into the workbook sheet "{2}" and check them', len(out_rows), u.name, sheet, lang=lang),
                         {'custom': 'map_upload', 'upload': u.pk, 'sheet': sheet, 'map': mp, 'api': [{'method': 'post', 'path': '/tools/api/workbooks/', 'label': tr('Data workbook', lang=lang),
                                                                                                         'show': tr('upload and check – nothing is loaded yet', lang=lang)}]},
                         tr('The file becomes a ZEO data workbook; the workbook\'s own check then runs. You load it in Settings › Data workbook.', lang=lang),
                         group='work', undoable=False)
    return {'text': tr('The columns of **{0}** fit the sheet **{1}**: {2} column(s) mapped, {3} not used.', u.name, sheet, len(mp), len(hdr) - len(mp), lang=lang)
            + ('\n\n' + '\n'.join('- ' + str(n) for n in (p.get('notes') or [])[:8]) if p.get('notes') else ''),
            'source': 'data', 'intent': 'map', 'mode': 'ai', 'usage_logged': True,
            'cards': [{'type': 'table', 'title': tr('Column mapping', lang=lang), 'columns': [tr('Column in the file', lang=lang), tr('Column of the workbook', lang=lang)],
                       'rows': [[k, v] for k, v in mp.items()]},
                      {'type': 'table', 'title': tr('First rows after mapping', lang=lang), 'columns': preview_cols,
                       'rows': [[str(r.get(cn, '') if r.get(cn) is not None else '') for cn in preview_cols] for r in out_rows[:5]]},
                      changes.card(ch, lang)]}


def run_map_upload(request, ch):
    """changes.confirm: write the workbook (the ZEO template with the mapped rows) and upload + check it with the workbook API."""
    import openpyxl
    from django.core.files.uploadedfile import SimpleUploadedFile
    from DataTools.workbook_jobs import TEMPLATE_PATH
    from . import apicall
    from .models import Upload
    u = Upload.objects.get(pk=ch.plan['upload'])
    hdr, data = _read_table(u)
    idx = {h: i for i, h in enumerate(hdr)}
    wb = openpyxl.load_workbook(TEMPLATE_PATH)
    ws = wb[ch.plan['sheet']]
    head = [cell.value for cell in ws[1]]
    for row in list(ws.iter_rows(min_row=2)):   # the template's example rows go
        for cell in row:
            cell.value = None
    n = 0
    for r in data:
        vals = {dst: (r[idx[src]] if idx[src] < len(r) else None) for src, dst in ch.plan['map'].items()}
        if not any(v not in (None, '') for v in vals.values()):
            continue
        n += 1
        for j, h in enumerate(head):
            if h in vals:
                ws.cell(row=n + 1, column=j + 1, value=vals[h])
    bio = io.BytesIO()
    wb.save(bio)
    name = re.sub(r'\.\w+$', '', u.name) + ' – ZEO workbook.xlsx'
    up = apicall.call(request, 'post', '/tools/api/workbooks/', files={'file': SimpleUploadedFile(name, bio.getvalue())})
    run = apicall.call(request, 'post', f"/tools/api/workbooks/{up['id']}/check/", {'only': ch.plan['sheet']})
    return {'done': [{'label': name, 'answer': {'rows': n, 'workbook': up.get('id'), 'run': run.get('id') if isinstance(run, dict) else None}}],
            'download': MS + 'settings/data-workbook'}


# ------------------------------------------------------------------ 98 SOP → steps, gaps, Word guide
def sop(request, c, conv, question, uploads, lang):
    from .models import Upload
    u = next((x for x in uploads if x.kind in ('document', 'spreadsheet')), None)
    if u is None:
        return {'text': tr('Attach the SOP or workflow document (PDF, Word or text) with 📎.', lang=lang), 'source': 'check', 'intent': 'sop'}
    text = _file_text(u)
    if len(text.strip()) < 40:
        return {'text': tr('No text could be read from {0}.', u.name, lang=lang), 'source': 'check', 'intent': 'sop'}
    sh = _shield(c)
    meter = ai.Meter('sop')
    system = ('Read the SOP / workflow document. Answer ONLY with JSON: {"title": text, "steps": [{"step": what is done, "who": role, '
              '"search": 2-5 words to find the ZEO screen}], "gaps": [steps or rules the document leaves unclear]}. At most 25 steps.')
    r = ai.call(request, meter, system, [{'role': 'user', 'content': sh.text(text[:20000])}], max_tokens=2500, feature='documents', small=True)
    p = ai.json_of(r)
    steps = []
    for s in (p.get('steps') or [])[:25]:
        hit = H.find_screens(c, str(s.get('search') or s.get('step') or ''), 1)
        steps.append({'step': sh.restore(str(s.get('step') or '')), 'who': sh.restore(str(s.get('who') or '')),
                      'screen': hit[0]['where'] if hit else '', 'link': hit[0]['link'] if hit else ''})
    gaps = [sh.restore(str(g)) for g in (p.get('gaps') or [])][:20]
    no_screen = [s['step'] for s in steps if not s['screen']]
    title = sh.restore(str(p.get('title') or u.name))
    data = word_guide(title, steps, gaps + [tr('No ZEO screen found for: {0}', x, lang=lang) for x in no_screen], lang)
    gu = Upload(user=request.user, conversation=conv, name=re.sub(r'[^\w\- ]', '', title)[:80] + '.docx', size=len(data), kind='document')
    gu.file.save(gu.name, ContentFile(data), save=True)
    meter.save(request.user)
    return {'text': tr('**{0}** – {1} step(s), {2} on a ZEO screen; {3} gap(s) to clarify. The Word guide is ready.', title, len(steps), len(steps) - len(no_screen),
                       len(gaps) + len(no_screen), lang=lang),
            'source': 'document', 'intent': 'sop', 'mode': 'ai', 'usage_logged': True,
            'cards': [{'type': 'table', 'title': tr('Steps', lang=lang), 'columns': [tr('Step', lang=lang), tr('Who', lang=lang), tr('ZEO screen', lang=lang)],
                       'rows': [[s['step'], s['who'], s['screen'] or tr('– gap –', lang=lang)] for s in steps]},
                      {'type': 'checks', 'title': tr('Gaps to clarify', lang=lang), 'items': [{'level': 'warn', 'text': g, 'link': ''} for g in gaps]},
                      {'type': 'file', 'title': gu.name, 'upload': gu.pk}]}


def _screenshot(link):
    """A PNG of a ZEO screen, when the server can take one (ZEO_ASKZEO_SCREENSHOTS=<frontend URL> and Playwright installed)."""
    base = os.environ.get('ZEO_ASKZEO_SCREENSHOTS', '').rstrip('/')
    if not base or not link:
        return None
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={'width': 1280, 'height': 760})
            st = os.environ.get('ZEO_ASKZEO_SCREENSHOT_STATE', '')
            if st and os.path.exists(st):
                ctx = b.new_context(storage_state=st, viewport={'width': 1280, 'height': 760})
                pg = ctx.new_page()
            pg.goto(base + link, wait_until='networkidle', timeout=30000)
            pg.wait_for_timeout(1500)
            png = pg.screenshot()
            b.close()
            return png
    except Exception:
        log.info('Ask ZEO: no screenshot of %s', link, exc_info=True)
        return None


def word_guide(title, steps, gaps, lang):
    import docx
    from docx.shared import Inches
    d = docx.Document()
    d.add_heading(title, 0)
    d.add_paragraph(tr('Made by Ask ZEO from the SOP – each step with its ZEO screen. Check it before you share it.', lang=lang))
    shots = {}
    for i, s in enumerate(steps, 1):
        d.add_heading(f'{i}. {s["step"]}', level=2)
        if s['who']:
            d.add_paragraph(tr('Who: {0}', s['who'], lang=lang))
        d.add_paragraph(tr('ZEO screen: {0}', s['screen'], lang=lang) if s['screen'] else tr('No ZEO screen – see the gaps.', lang=lang))
        if s['link']:
            if s['link'] not in shots:
                shots[s['link']] = _screenshot(s['link'])
            if shots[s['link']]:
                d.add_picture(io.BytesIO(shots[s['link']]), width=Inches(6))
    if gaps:
        d.add_heading(tr('Gaps to clarify', lang=lang), level=1)
        for g in gaps:
            d.add_paragraph(g, style='List Bullet')
    bio = io.BytesIO()
    d.save(bio)
    return bio.getvalue()


# ------------------------------------------------------------------ 126 setup from the company's policy document
def policy_setup(request, c, conv, question, uploads, lang):
    if not c.admin:
        return {'text': tr('Only a company administrator can change the setup from a policy document.', lang=lang), 'source': 'check', 'intent': 'policy_setup'}
    u = next((x for x in uploads if x.kind == 'document'), None)
    if u is None:
        return {'text': tr('Attach the policy document (PDF, Word or text) with 📎.', lang=lang), 'source': 'check', 'intent': 'policy_setup'}
    text = _file_text(u)
    LT = M('calendars', 'leave_type')
    LP = M('LeavePolicy', 'LeavePolicy')
    LPL = M('LeavePolicy', 'LeavePolicyLine')
    types = {t.pk: t.name for t in LT.objects.all()}
    meter = ai.Meter('policy_setup')
    sh = _shield(c)
    system = ('Read the company policy. Answer ONLY with JSON: {"leave": [{"leave_type": one of the leave types, "days_per_year": number or null, '
              '"carry_forward_max": number or null, "encashable": true/false/null, "max_per_request": number or null, "quote": the sentence it comes from}], '
              '"allowances": [{"name": text, "percent_of_basic": number or null, "quote": text}], "overtime": {"normal_percent": number or null, '
              '"rest_day_percent": number or null, "quote": text} }. Leave types: ' + json.dumps(sorted(set(types.values())), ensure_ascii=False))
    r = ai.call(request, meter, system, [{'role': 'user', 'content': sh.text(text[:20000])}], max_tokens=2500, feature='documents', small=True)
    p = ai.json_of(r)
    meter.save(request.user)
    pol = LP.objects.order_by('pk').first()
    ops, info = [], []
    by_name = {v.lower(): k for k, v in types.items()}
    for ln in (p.get('leave') or [])[:30]:
        tid = by_name.get(str(ln.get('leave_type') or '').lower())
        if tid is None or pol is None:
            continue
        line = LPL.objects.filter(policy=pol, leave_type_id=tid).first()
        if line is None:
            info.append(tr('{0}: not in the policy {1} – add it on Leave policies.', types[tid], pol.name if hasattr(pol, 'name') else pol.pk, lang=lang))
            continue
        vals = {}
        for f in ('days_per_year', 'carry_forward_max', 'max_per_request'):
            v = ln.get(f)
            if isinstance(v, (int, float)) and 0 <= v <= 366 and float(getattr(line, f) or 0) != float(v):
                vals[f] = float(v)
        if isinstance(ln.get('encashable'), bool) and line.encashable != ln['encashable']:
            vals['encashable'] = ln['encashable']
        if vals:
            ops.append({'op': 'update', 'model': 'LeavePolicy.LeavePolicyLine', 'id': line.pk, 'values': vals,
                        'label': f"{getattr(pol, 'name', pol.pk)} – {types[tid]}"})
    for a in (p.get('allowances') or [])[:10]:
        info.append(tr('Allowance in the policy: {0}{1} – set it on Salary components / Salary structures.', a.get('name'),
                       f" ({a['percent_of_basic']}% of basic)" if a.get('percent_of_basic') else '', lang=lang))
    ot = p.get('overtime') or {}
    if ot.get('normal_percent') or ot.get('rest_day_percent'):
        info.append(tr('Overtime in the policy: normal {0}%, rest day {1}% – the UAE law minimum is 125% / 150% (Decree-Law 33/2021 Art. 19). Set it on the overtime policy.',
                       ot.get('normal_percent') or '–', ot.get('rest_day_percent') or '–', lang=lang))
    cards = [{'type': 'checks', 'title': tr('Also in the policy', lang=lang), 'items': [{'level': 'warn', 'text': x, 'link': ''} for x in info]}] if info else []
    if ops:
        ch = changes.preview(request, conv, 'setup', tr('Leave policy from {0}: {1} line(s)', u.name, len(ops), lang=lang), {'records': ops},
                             tr('Proposed from the document; each value is checked against the leave policy now. Undo puts the old values back.', lang=lang), group='setup')
        cards.insert(0, changes.card(ch, lang))
    return {'text': tr('I read **{0}**: {1} change(s) to the leave policy proposed, {2} more point(s) to set on their screens.', u.name, len(ops), len(info), lang=lang),
            'source': 'document', 'intent': 'policy_setup', 'mode': 'ai', 'usage_logged': True, 'cards': cards}


def read_doc(request, c, conv, question, uploads, lang):
    """A question about an attached document (contract, letter, policy …): answered from its text only, naming the part used."""
    u = next((x for x in uploads if x.kind in ('document', 'image')), None)
    if u is None:
        return None
    text = _file_text(u)
    if len(text.strip()) < 20:
        return {'text': tr('No text could be read from {0}.', u.name, lang=lang), 'source': 'check', 'intent': 'read_doc'}
    meter, sh = ai.Meter('read_doc'), _shield(c)
    system = ('Answer the question from the attached document only. Quote the part you used (a short quote) and say when the document does not '
              'answer it. Answer in ' + ('Arabic.' if lang == 'ar' else 'English.'))
    r = ai.call(request, meter, system, [{'role': 'user', 'content': sh.text(f'Document {u.name}:\n' + text[:30000] + '\n\nQuestion: ' + question)}],
                max_tokens=1500, feature='documents', small=True)
    return _done(request, meter, ai.text_of(r), 'read_doc', 'document', None, sh, lang)


HANDLERS = {'read_doc': read_doc, 'write': write, 'translate': translate, 'quiz': quiz, 'bulk': bulk, 'map': map_file, 'sop': sop, 'policy_setup': policy_setup}
