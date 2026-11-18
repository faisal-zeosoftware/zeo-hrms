"""Ask ZEO 1.71.0 – ready items that need no AI: letters (133a), template upload (99a), guided forms (130a).

Letters use SelfService (HR letters): an employee's request is a Change that calls "My letters"; HR gets the filled
preview of the template for the employee named. Template upload: the Data workbook (Settings › Data workbook) – a file
with the workbook's Index sheet is uploaded and checked with the workbook's own API. Guided forms: a leave request filled
in the chat and sent with the same API as Self service › My leave.
"""
import re
from datetime import date

from Translations.i18n import tr

from . import apicall, changes
from . import scope as S

MS = '/main-sidebar/'
LETTERS = [('salary_transfer', r'salary transfer|transfer letter|bank letter'), ('salary_certificate', r'salary certificate'),
           ('experience', r'experience'), ('employment', r'employment certificate|to whom'), ('noc_travel', r'noc.*travel|travel.*noc'),
           ('noc_visa', r'noc.*visa|visa.*noc'), ('noc_other', r'\bnoc\b'), ('embassy', r'embassy'), ('bank_account', r'bank account opening|open.*account')]


def letter_type(cq):
    for k, rx in LETTERS:
        if re.search(rx, cq):
            return k
    return 'salary_certificate' if 'salary' in cq else None


def letter(request, c, conv, cq, question, lang):
    from SelfService.models import LETTER_TYPES
    t = letter_type(cq)
    names = dict(LETTER_TYPES)
    if t is None:
        return {'text': tr('Which letter do you need? {0}', ', '.join(tr(v, lang=lang) for k, v in LETTER_TYPES if k not in ('increment', 'general')), lang=lang),
                'links': [{'label': tr('My letters', lang=lang), 'url': MS + 'self-service/letters'}], 'intent': 'letter'}
    emp = S.find_employee(c, question) if S.is_hr(c) else None
    llang = 'ar' if lang == 'ar' or re.search(r'\barabic\b', cq) else 'en'
    if emp is not None and (c.emp is None or emp.pk != c.emp.pk):
        try:
            d = apicall.call(request, 'post', '/self-service/api/letters/preview/', {'employee_id': emp.pk, 'letter_type': t, 'language': llang})
        except apicall.ApiError as e:
            return {'text': e.detail, 'intent': 'letter'}
        return {'text': tr('Here is the {0} for {1}, filled from the employee record. Issue it from HR letters (it gets a reference number and a check code).',
                           tr(names[t], lang=lang).lower(), S.emp_label(emp), lang=lang),
                'cards': [{'type': 'letter', 'title': d.get('title'), 'body': d.get('body'), 'lang': llang}],
                'links': [{'label': tr('HR letters', lang=lang), 'url': MS + 'self-service/hr-letters'}], 'intent': 'letter'}
    if c.emp is None:
        return {'text': tr('Name the employee (name or code) for the letter, e.g. "Salary certificate for E0002".', lang=lang), 'intent': 'letter'}
    needs = {'salary_transfer': 'bank_name', 'bank_account': 'bank_name', 'noc_travel': 'destination'}
    body = {'letter_type': t, 'language': llang, 'purpose': '', 'addressee': ''}
    m = re.search(r'\b(?:to|for|bank)\s+([A-Z][\w&\- ]{2,40}?)(?:\s+bank)?\s*(?:$|[.,?])', question)
    if needs.get(t) == 'bank_name':
        if not m:
            return {'text': tr('Which bank is the {0} for? For example: "{1} to Emirates NBD".', tr(names[t], lang=lang).lower(), tr(names[t], lang=lang), lang=lang), 'intent': 'letter'}
        body['bank_name'] = m.group(1).strip()
    if needs.get(t) == 'destination':
        if not m:
            return {'text': tr('Where do you travel to, and when? Use My letters for the travel dates.', lang=lang),
                    'links': [{'label': tr('My letters', lang=lang), 'url': MS + 'self-service/letters'}], 'intent': 'letter'}
        body['destination'] = m.group(1).strip()
    ch = changes.preview(request, conv, 'request', tr('Request a {0}', tr(names[t], lang=lang).lower(), lang=lang),
                         {'api': [{'method': 'post', 'path': '/self-service/api/letters/mine/', 'data': body, 'label': tr(names[t], lang=lang),
                                   'show': ('العربية' if llang == 'ar' else 'English') + (f" – {body.get('bank_name') or body.get('destination')}" if body.get('bank_name') or body.get('destination') else ''),
                                   'undo': {'method': 'post', 'path': '/self-service/api/letters/{id}/withdraw/'}}]},
                         tr('HR approves the request; the letter then appears in My letters.', lang=lang), group='work')
    return {'text': tr('I can send this request to HR for you. Check it and confirm.', lang=lang), 'cards': [changes.card(ch, lang)], 'intent': 'letter'}


def template(request, c, conv, cq, uploads, lang):
    """Templates: the data workbook template and the demo; an uploaded workbook is checked with the workbook's own API."""
    links = [{'label': tr('Settings › Data workbook', lang=lang), 'url': MS + 'settings/data-workbook'},
             {'label': tr('Monthly inputs (Excel template)', lang=lang), 'url': MS + 'salary-options/monthly-inputs'}]
    wb = [u for u in uploads if u.kind == 'workbook']
    if wb:
        if not c.admin:
            return {'text': tr('Only a company administrator can load data workbooks.', lang=lang), 'intent': 'template'}
        u = wb[0]
        try:
            with u.file.open('rb') as f:
                from django.core.files.uploadedfile import SimpleUploadedFile
                up = apicall.call(request, 'post', '/tools/api/workbooks/', files={'file': SimpleUploadedFile(u.name, f.read())})
            run = apicall.call(request, 'post', f"/tools/api/workbooks/{up['id']}/check/", {'quick': False})
        except apicall.ApiError as e:
            return {'text': tr('The workbook could not be checked: {0}', e.detail, lang=lang), 'links': links, 'intent': 'template'}
        sheets = up.get('sheets') or []
        rows = [[s.get('sheet'), s.get('rows') if s.get('rows') is not None else s.get('count', ''), s.get('status') or ''] for s in sheets]
        load = _load_change(request, conv, up, lang)
        return {'text': tr('**{0}** is uploaded to the Data workbook and the check has started ({1} sheet(s)). Ask "status of my workbook" for the result; Load below loads it with the workbook\'s own loader – nothing is loaded before you confirm.',
                           u.name, len(sheets), lang=lang),
                'cards': ([{'type': 'table', 'title': tr('Sheets in the workbook', lang=lang), 'columns': [tr('Sheet', lang=lang), tr('Rows', lang=lang), tr('Status', lang=lang)], 'rows': rows}] if rows else []) + [changes.card(load, lang)],
                'links': links, 'intent': 'template', 'data': {'workbook': up.get('id'), 'run': run.get('id') if isinstance(run, dict) else None}}
    others = [u for u in uploads if u.kind == 'spreadsheet']
    if others:
        return {'text': tr('{0} is not a ZEO data workbook (it has no Index sheet). Download the template, copy your data into its sheets and upload it again. '
                           'With the company\'s AI switched on, Ask ZEO can map a file in any layout to the template.', others[0].name, lang=lang),
                'links': [{'label': tr('Download the workbook template', lang=lang), 'url': MS + 'settings/data-workbook'}] + links, 'intent': 'template'}
    return {'text': tr('Templates you can download and upload again:', lang=lang) + '\n' +
            '\n'.join(['- ' + tr('**Data workbook** – employees, salaries, opening balances, leave, loans … in one Excel file (Settings › Data workbook › Download the template).', lang=lang),
                       '- ' + tr('**Demo workbook** – a fictional company with three months of data, to try ZEO.', lang=lang),
                       '- ' + tr('**Import ▾** on the list screens – the Excel / CSV template of that screen.', lang=lang),
                       '- ' + tr('**Monthly inputs** – the month\'s additions and deductions as an Excel sheet.', lang=lang),
                       tr('Attach a filled workbook here (📎) and ask "check this workbook".', lang=lang)]),
            'links': links, 'intent': 'template'}


def _load_change(request, conv, up, lang):
    return changes.preview(request, conv, 'data_load', tr('Load the workbook {0} into this company', up.get('file_name') or up.get('name') or up.get('id'), lang=lang),
                           {'api': [{'method': 'post', 'path': f"/tools/api/workbooks/{up['id']}/load/", 'data': {}, 'label': tr('Data workbook › Load', lang=lang),
                                     'show': tr('{0} sheet(s)', len(up.get('sheets') or []), lang=lang), 'undo': None}]},
                           tr('The rows are loaded by the Data workbook (rows already there are skipped); it runs in the background – follow it in Settings › '
                              'Data workbook. A load is not undone from Ask ZEO.', lang=lang), group='setup', undoable=False)


def load_workbook(request, c, conv, lang):
    """"Load my workbook" / "status of my workbook": the newest workbook of this company, its last check or load, and Load as a preview."""
    if not c.admin:
        return {'text': tr('Only a company administrator can load data workbooks.', lang=lang), 'intent': 'load_workbook'}
    try:
        d = apicall.call(request, 'get', '/tools/api/workbooks/')
    except apicall.ApiError as e:
        return {'text': e.detail, 'intent': 'load_workbook'}
    ups = d.get('results') or []
    if not ups:
        return {'text': tr('No workbook is uploaded yet. Attach it here (📎) or use Settings › Data workbook.', lang=lang), 'intent': 'load_workbook',
                'links': [{'label': tr('Settings › Data workbook', lang=lang), 'url': MS + 'settings/data-workbook'}]}
    up = ups[0]
    hist = [h for h in d.get('history') or [] if h.get('upload') == up.get('id')]
    last = hist[0] if hist else None
    state = (last or {}).get('message') or (last or {}).get('state_text') or tr('not checked yet', lang=lang)
    cards = []
    if not (last and last.get('kind') == 'load' and last.get('state') in ('queued', 'running', 'done')):
        cards.append(changes.card(_load_change(request, conv, up, lang), lang))
    return {'text': tr('Workbook **{0}** – last step: {1}.', up.get('file_name') or up.get('id'), str(state).rstrip('. '), lang=lang), 'intent': 'load_workbook', 'cards': cards,
            'links': [{'label': tr('Settings › Data workbook', lang=lang), 'url': MS + 'settings/data-workbook'}]}


# ------------------------------------------------------------------ guided forms
def leave_form(request, c, lang):
    if c.emp is None:
        return {'text': tr('Your login is not linked to an employee, so you cannot ask for leave.', lang=lang), 'intent': 'form'}
    try:
        bal = apicall.call(request, 'get', f'/employee/api/Employee/{c.emp.pk}/leave_balance/')
    except apicall.ApiError as e:
        return {'text': e.detail, 'intent': 'form'}
    bal = bal or {}
    by_name = {b.get('leave_type_name') or b.get('leave_type'): b.get('balance') for b in bal.get('leave_balance') or []}
    ok_ids = set(bal.get('applicable_type_ids') or [])
    needs_doc = _doc_types(c.emp)
    types = []
    for t in bal.get('available_leave_types') or []:
        if ok_ids and t.get('id') not in ok_ids:
            continue
        b = by_name.get(t.get('name'))
        label = f"{t.get('name')} ({tr('balance', lang=lang)} {b:g})" if isinstance(b, (int, float)) else t.get('name')
        if t.get('id') in needs_doc:
            label += ' – ' + tr('needs a document', lang=lang)
        types.append({'value': t.get('id'), 'label': label, 'document': t.get('id') in needs_doc})
    return {'text': tr('Fill in your leave request. Ask ZEO shows it to you once more before it is sent for approval.', lang=lang),
            'cards': [{'type': 'form', 'form': 'leave', 'title': tr('Leave request', lang=lang), 'fields': [
                {'key': 'leave_type', 'label': tr('Leave type', lang=lang), 'type': 'select', 'options': types, 'required': True},
                {'key': 'start_date', 'label': tr('First day', lang=lang), 'type': 'date', 'required': True},
                {'key': 'end_date', 'label': tr('Last day', lang=lang), 'type': 'date', 'required': True},
                {'key': 'reason', 'label': tr('Reason', lang=lang), 'type': 'text', 'required': False, 'max': 500},
                {'key': 'document', 'label': tr('Supporting document', lang=lang), 'type': 'file', 'required': False}]}],
            'links': [{'label': tr('My leave', lang=lang), 'url': MS + 'self-service/leave'}], 'intent': 'form'}


def _doc_types(emp):
    """Leave types of the employee's leave policy that need a supporting document."""
    try:
        from LeavePolicy.engine import policy_for
        pol = policy_for(emp)
        return {ln.leave_type_id for ln in pol.lines.all() if ln.requires_document} if pol else set()
    except Exception:
        return set()


def submit_form(request, c, conv, form, values, lang):
    """Validates the form and returns the Change to confirm. Raises changes.Refused with the field errors."""
    if form != 'leave':
        raise changes.Refused(tr('Unknown form.', lang=lang), 404)
    if c.emp is None:
        raise changes.Refused(tr('Your login is not linked to an employee, so you cannot ask for leave.', lang=lang), 403)
    err = {}
    lt = values.get('leave_type')
    if not str(lt or '').isdigit():
        err['leave_type'] = tr('Choose the leave type.', lang=lang)
    ds = {}
    for k in ('start_date', 'end_date'):
        try:
            ds[k] = date.fromisoformat(str(values.get(k) or ''))
        except ValueError:
            err[k] = tr('Give the date as dd/mm/yyyy.', lang=lang)
    if not err and ds['end_date'] < ds['start_date']:
        err['end_date'] = tr('The last day cannot be before the first day.', lang=lang)
    reason = str(values.get('reason') or '')
    if len(reason) > 500:
        err['reason'] = tr('At most 500 characters.', lang=lang)
    doc = values.get('document')
    from .models import Upload
    up = None
    if doc not in (None, ''):
        up = Upload.objects.filter(pk=doc, user=request.user).first() if str(doc).isdigit() else None
        if up is None:
            err['document'] = tr('Attach the document again.', lang=lang)
    if 'leave_type' not in err and int(lt) in _doc_types(c.emp) and up is None:
        err['document'] = tr('This leave type needs a supporting document – attach it.', lang=lang)
    if err:
        e = changes.Refused('; '.join(err.values()))
        e.fields = err
        raise e
    body = {'employee': c.emp.pk, 'leave_type': int(lt), 'start_date': ds['start_date'].isoformat(), 'end_date': ds['end_date'].isoformat(),
            'reason': reason, 'dis_half_day': False}
    return changes.preview(request, conv, 'request', tr('Leave request {0} – {1}', ds['start_date'].strftime('%d/%m/%Y'), ds['end_date'].strftime('%d/%m/%Y'), lang=lang),
                           {'api': [{'method': 'post', 'path': '/calendars/api/emp-leave-request/', 'data': body, 'label': tr('Leave request', lang=lang),
                                     'files': {'lv_document': up.pk} if up else None,
                                     'show': f"{ds['start_date']:%d/%m/%Y} – {ds['end_date']:%d/%m/%Y}" + (f' + {up.name}' if up else ''),
                                     'undo': {'method': 'delete', 'path': '/calendars/api/emp-leave-request/{id}/'}}]},
                           tr('It goes to your approvers like a request from My leave. Undo withdraws it while it is waiting.', lang=lang), group='work')
