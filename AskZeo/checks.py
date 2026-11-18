"""Ask ZEO 1.71.0 – checks that need no AI.

* setup_check (127)   before go-live: the setup guide's own readiness checks (ZeoAdmin/setup_guide.s_golive) plus leave types,
                       WPS employer ID / routing code, employees without a bank account, grades and approval rules.
* data_quality (137)  IBAN check digits (ISO 13616 mod 97), Emirates ID format and check digit, duplicates, documents that
                       expire in the next 90 days and missing documents.
* precheck (129)      the AI payroll auditor (ZeoAI/payroll_audit.py) of a run, through its own API.
Every finding has a link to the screen where it is fixed.
"""
from zeo.tz import today as _zt_today   # v1.73.16 the user's / company's day, not the server's UTC day
import re
from collections import defaultdict
from datetime import date, timedelta

from django.apps import apps
from django.db.models import Q

from Translations.i18n import tr

from . import scope as S

M = apps.get_model
MS = '/main-sidebar/'


# ------------------------------------------------------------------ IBAN / Emirates ID
IBAN_LEN = {'AE': 23, 'SA': 24, 'QA': 29, 'KW': 30, 'BH': 22, 'OM': 23, 'GB': 22, 'DE': 22, 'FR': 27, 'IN': 0, 'PK': 24, 'EG': 29, 'JO': 30, 'LB': 28}


def iban_problem(iban):
    """'' when the IBAN is right, else what is wrong (in English; translated by the caller)."""
    s = re.sub(r'\s+', '', iban or '').upper()
    if not s:
        return 'missing'
    if not re.fullmatch(r'[A-Z]{2}\d{2}[A-Z0-9]{10,30}', s):
        return 'not an IBAN (2 letters, 2 check digits, then the account)'
    want = IBAN_LEN.get(s[:2])
    if want and len(s) != want:
        return f'{s[:2]} IBANs have {want} characters, this one has {len(s)}'
    num = ''.join(str(int(ch, 36)) for ch in s[4:] + s[:4])
    if int(num) % 97 != 1:
        return 'the check digits are wrong (a typing mistake)'
    return ''


def luhn_ok(digits):
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def eid_problem(eid):
    s = re.sub(r'\D', '', eid or '')
    if not eid:
        return 'missing'
    if len(s) != 15:
        return f'an Emirates ID has 15 digits, this one has {len(s)}'
    if not s.startswith('784'):
        return 'an Emirates ID starts with 784'
    if not luhn_ok(s):
        return 'the last digit (check digit) does not match'
    return ''


def eid_formatted(eid):
    s = re.sub(r'\D', '', eid or '')
    return f'{s[:3]}-{s[3:7]}-{s[7:14]}-{s[14:]}' if len(s) == 15 else eid


# ------------------------------------------------------------------ data quality
def _is_national(e):
    n = getattr(e, 'emp_nationality', None)
    name = (str(n) if n else '').lower()
    return 'emirat' in name or name.strip() in ('uae', 'ae')


def data_quality(c, lang=None, days=90):
    """{'groups': [{key, title, items: [{employee, text, link}]}], 'counts': {...}, 'fixable_eid': [identity ids]}"""
    E = M('EmpManagement', 'emp_master')
    emps = list(S.employees(c).filter(Q(is_active=True) | Q(is_active__isnull=True)).select_related('emp_nationality'))
    ids = [e.pk for e in emps]
    by_id = {e.pk: e for e in emps}
    today = _zt_today()
    groups = defaultdict(list)
    fix_eid = []

    def add(key, e, text, link=None):
        groups[key].append({'employee': S.emp_label(e), 'employee_id': e.pk, 'text': text, 'link': link or S.emp_url(e)})

    # bank accounts / IBAN
    B = M('EmpManagement', 'EmployeeBankDetail')
    banks = defaultdict(list)
    for b in B.objects.filter(employee_id__in=ids, is_active=True):
        banks[b.employee_id].append(b)
    iban_seen = defaultdict(list)
    for e in emps:
        bl = banks.get(e.pk) or []
        if not bl:
            add('bank', e, tr('No bank account – the employee cannot be in the WPS file.', lang=lang))
            continue
        for b in bl:
            p = iban_problem(b.iban_number)
            if p == 'missing':
                add('iban', e, tr('Bank account {0} has no IBAN.', b.account_number, lang=lang))
            elif p:
                add('iban', e, tr('IBAN {0}: {1}.', b.iban_number, tr(p, lang=lang), lang=lang))
            if b.iban_number:
                iban_seen[re.sub(r'\s+', '', b.iban_number).upper()].append(e)

    # identity: Emirates ID, passport, visa, labour card – from the identity details (EmployeeProfile) and the uploaded documents
    # (Employees › documents, by document type name); the identity details win where both have a number
    KIND_RX = [('eid', r'emirates|\beid\b|هوية'), ('passport', r'passport|جواز'), ('visa', r'visa|residen|اقامة|إقامة'), ('labour', r'labou?r|work permit|mohre')]
    idn = defaultdict(dict)   # emp id → kind → {num, exp, model, id, field}
    D = M('EmpManagement', 'Emp_Documents')
    docs = list(D.objects.filter(emp_id_id__in=ids, is_active=True).select_related('document_type'))
    for d in docs:
        name = str(d.document_type or '').lower()
        kind = next((k for k, rx in KIND_RX if re.search(rx, name)), None)
        if kind:
            idn[d.emp_id_id].setdefault(kind, {'num': d.emp_doc_number, 'exp': d.emp_doc_expiry_date, 'model': 'EmpManagement.Emp_Documents', 'id': d.pk, 'field': 'emp_doc_number'})
    try:
        ID = M('EmployeeProfile', 'EmployeeIdentity')
        for x in ID.objects.filter(employee_id__in=ids):
            for kind, num, exp, fld in (('eid', x.emirates_id, x.emirates_id_expiry_date, 'emirates_id'), ('passport', x.passport_no, x.passport_expiry_date, 'passport_no'),
                                        ('visa', x.visa_number or x.visa_uid, x.visa_expiry_date, 'visa_number'), ('labour', x.labour_card_no, x.labour_card_expiry_date, 'labour_card_no')):
                if num or exp:
                    idn[x.employee_id][kind] = {'num': num, 'exp': exp, 'model': 'EmployeeProfile.EmployeeIdentity', 'id': x.pk, 'field': fld}
    except LookupError:
        pass
    eid_seen, pp_seen = defaultdict(list), defaultdict(list)
    for e in emps:
        k = idn.get(e.pk, {})
        national = _is_national(e)
        x = k.get('eid')
        p = eid_problem(x['num'] if x else '')
        if p == 'missing':
            add('missing_docs', e, tr('Emirates ID number missing.', lang=lang))
        elif p:
            add('eid', e, tr('Emirates ID {0}: {1}.', x['num'], tr(p, lang=lang), lang=lang))
        elif x['num'] != eid_formatted(x['num']):
            add('eid_format', e, tr('Emirates ID {0} is right but not written as 784-YYYY-NNNNNNN-N.', x['num'], lang=lang))
            fix_eid.append({'model': x['model'], 'id': x['id'], 'field': x['field'], 'value': eid_formatted(x['num']), 'employee': S.emp_label(e)})
        if x and x['num']:
            eid_seen[re.sub(r'\D', '', x['num'])].append(e)
        if not national:
            if not (k.get('passport') or {}).get('num'):
                add('missing_docs', e, tr('Passport number missing.', lang=lang))
            if not k.get('visa'):
                add('missing_docs', e, tr('Residence visa missing.', lang=lang))
            if not k.get('labour'):
                add('missing_docs', e, tr('Labour card (MOHRE work permit) missing.', lang=lang))
        if (k.get('passport') or {}).get('num'):
            pp_seen[k['passport']['num'].strip().upper()].append(e)
        for kind, label in (('passport', 'Passport'), ('visa', 'Residence visa'), ('eid', 'Emirates ID'), ('labour', 'Labour card')):
            d = (k.get(kind) or {}).get('exp')
            if d and d < today:
                add('expired', e, tr('{0} expired on {1}.', tr(label, lang=lang), d.strftime('%d/%m/%Y'), lang=lang), MS + 'sub-sidebar/document-expired')
            elif d and d <= today + timedelta(days=days):
                add('expiring', e, tr('{0} expires on {1} ({2} days).', tr(label, lang=lang), d.strftime('%d/%m/%Y'), (d - today).days, lang=lang), MS + 'sub-sidebar/document-expired')
    # other uploaded documents (not identity documents)
    for d in docs:
        name = str(d.document_type or tr('Document', lang=lang))
        if any(re.search(rx, name.lower()) for _, rx in KIND_RX):
            continue
        e = by_id.get(d.emp_id_id)
        if d.emp_doc_expiry_date and d.emp_doc_expiry_date < today:
            add('expired', e, tr('{0} {1} expired on {2}.', name, d.emp_doc_number, d.emp_doc_expiry_date.strftime('%d/%m/%Y'), lang=lang), MS + 'sub-sidebar/document-expired')
        elif d.emp_doc_expiry_date and d.emp_doc_expiry_date <= today + timedelta(days=days):
            add('expiring', e, tr('{0} {1} expires on {2} ({3} days).', name, d.emp_doc_number, d.emp_doc_expiry_date.strftime('%d/%m/%Y'),
                                  (d.emp_doc_expiry_date - today).days, lang=lang), MS + 'sub-sidebar/document-expired')

    # duplicates
    def dup(seen, label):
        for k, es in seen.items():
            if len({x.pk for x in es}) > 1:
                names = ', '.join(sorted({S.emp_label(x) for x in es}))
                for x in {x.pk: x for x in es}.values():
                    add('duplicates', x, tr('Same {0} as another employee: {1} ({2}).', tr(label, lang=lang), k, names, lang=lang))
    dup(iban_seen, 'IBAN')
    dup(eid_seen, 'Emirates ID')
    dup(pp_seen, 'passport number')
    names = defaultdict(list)
    for e in emps:
        key = (S.emp_name(e).lower(), e.emp_date_of_birth)
        if key[0] and key[1]:
            names[key].append(e)
    for (n, dob), es in names.items():
        if len(es) > 1:
            for x in es:
                add('duplicates', x, tr('Same name and date of birth as {0} – the same person entered twice?', ', '.join(S.emp_label(y) for y in es if y.pk != x.pk), lang=lang))
    for e in emps:
        if not e.person_id and not _is_national(e):
            add('missing_docs', e, tr('Person ID (MOHRE) missing – needed in the WPS file.', lang=lang))

    titles = [('iban', 'IBAN problems'), ('bank', 'No bank account'), ('eid', 'Emirates ID problems'), ('eid_format', 'Emirates ID not formatted'),
              ('duplicates', 'Possible duplicates'), ('expired', 'Expired documents'), ('expiring', 'Documents expiring in the next 90 days'),
              ('missing_docs', 'Missing documents and numbers')]
    out = [{'key': k, 'title': tr(t, lang=lang), 'count': len(groups[k]), 'items': groups[k][:50], 'more': max(0, len(groups[k]) - 50)} for k, t in titles if groups.get(k)]
    return {'employees': len(emps), 'groups': out, 'counts': {k: len(v) for k, v in groups.items()}, 'fixable_eid': fix_eid}


# ------------------------------------------------------------------ setup check before go-live
def setup_check(c, lang=None):
    from ZeoAdmin import setup_guide as G
    items = []
    try:
        st = G.s_golive()
        for x in st['blocking']:
            items.append({'level': 'block', 'text': x['text'], 'step': x.get('step_no'), 'link': x.get('link') or MS + 'settings/setup-guide'})
        for x in st['warnings']:
            items.append({'level': 'warn', 'text': x['text'], 'step': x.get('step_no'), 'link': x.get('link') or MS + 'settings/setup-guide'})
        for x in st.get('checks') or []:
            if not x['ok']:
                items.append({'level': 'warn', 'text': x['text'], 'step': None, 'link': x.get('link')})
        live = st.get('live_at')
    except Exception:
        live = None
        items.append({'level': 'warn', 'text': tr('The setup guide could not be read.', lang=lang), 'step': None, 'link': MS + 'settings/setup-guide'})
    known = {i['text'] for i in items}

    def extra(ok, text, link, level='warn'):
        t = tr(text, lang=lang)
        if not ok and t not in known:
            items.append({'level': level, 'text': t, 'step': None, 'link': link})
    try:
        from ZeoApps.services import is_active
        hr_on, pay_on = is_active('hrms'), is_active('payroll')
    except Exception:
        hr_on = pay_on = True
    if hr_on:
        try:
            LT = M('calendars', 'leave_type')
            extra(LT.objects.exists(), 'No leave types – load the country defaults (Leave › Leave types).', MS + 'leave-options/leave-master', 'block')
        except LookupError:
            pass
        try:
            G2 = M('OrgStructure', 'Grade')
            extra(G2.objects.exists(), 'No grades – optional, but needed for grade-based allowances and air ticket rules.', MS + 'org-structure')
        except LookupError:
            pass
    if pay_on:
        from django.db import connection
        from django_tenants.utils import get_tenant_model
        t = get_tenant_model().objects.filter(schema_name=connection.schema_name).first()
        extra(bool(t and getattr(t, 'employer_unique_id', '')), 'The company has no WPS employer ID (MOHRE establishment number).', MS + 'settings/location-master', 'block')
        extra(bool(t and getattr(t, 'bank_routing_code', '')), 'The company has no bank routing code for the WPS file.', MS + 'settings/location-master', 'block')
        emps = S.employees(c).filter(Q(is_active=True) | Q(is_active__isnull=True))
        nb = emps.exclude(bank_details__is_active=True).count()
        extra(nb == 0, tr('{0} employee(s) without a bank account.', nb, lang=lang) if nb else '', MS + 'report-options/r/wps')
        try:
            SS = M('PayrollManagement', 'EmployeeSalaryStructure')
            ns = emps.exclude(pk__in=SS.objects.values('employee_id')).count()
            extra(ns == 0, tr('{0} employee(s) without a salary.', ns, lang=lang) if ns else '', MS + 'salary-options/employee-salary', 'block')
        except LookupError:
            pass
    try:
        AF = M('ZeoFlow', 'ApprovalFlow')
        AL = M('calendars', 'LeaveApprovalLevels')
        extra(AF.objects.exists() or AL.objects.exists(), 'No approval rules – requests would be approved by the company administrator only.',
              MS + 'automation/approval-rules')
    except LookupError:
        pass
    items.sort(key=lambda x: 0 if x['level'] == 'block' else 1)
    return {'live_at': live, 'items': items, 'blocking': sum(1 for i in items if i['level'] == 'block'), 'warnings': sum(1 for i in items if i['level'] != 'block')}


# ------------------------------------------------------------------ payroll pre-check (the AI payroll auditor)
def pick_run(c, ym=None, statuses=None):
    PR = M('PayrollManagement', 'PayrollRun')
    from AccessControl.access import run_in_branches
    qs = PR.objects.all().order_by('-year', '-month', '-id')
    if ym:
        qs = qs.filter(year=ym[0], month=ym[1])
    if statuses:
        qs = qs.filter(status__in=statuses)
    for r in qs[:50]:
        if c.admin or run_in_branches(c.branches, r):
            return r
    return None


def precheck(request, c, run, lang=None):
    from . import apicall
    d = apicall.call(request, 'post', f'/zeo-ai/api/payroll/runs/{run.pk}/audit/')
    a = d.get('audit') or {}
    findings = a.get('findings') or []
    sev = defaultdict(int)
    for f in findings:
        sev[f.get('severity')] += 1
    top = [f for f in findings if f.get('severity') == 'critical'] + [f for f in findings if f.get('severity') == 'warning']
    return {'run': {'id': run.pk, 'name': run.name, 'month': run.month, 'year': run.year, 'status': run.status},
            'summary': a.get('summary') or '', 'counts': dict(sev), 'gate': d.get('gate'),
            'findings': [{'severity': f.get('severity'), 'title': f.get('title') or f.get('rule') or '', 'text': f.get('explanation') or '',
                          'employee': (f"{f.get('employee')} ({f.get('emp_code')})" if f.get('employee') else ''), 'status': f.get('status'),
                          'link': (f"{MS}salary-options/payroll-details/{f['payslip_id']}" if f.get('payslip_id') else '')} for f in top[:15]],
            'more': max(0, len(top) - 15), 'link': f'{MS}salary-options/payroll-run/{run.pk}'}
