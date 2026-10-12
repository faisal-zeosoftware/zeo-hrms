"""
v1.19.0 – the employee page and the employee list in the new design ("Employee Master design recommendations").

GET  api/record/<emp_id>/                   everything the employee page shows, in one call: header (chips, key facts, next expiry,
                                            completeness, previous / next), the six groups' cards and tables, UAE document cards with
                                            expiry badges, WPS readiness, pay / time / asset summaries, next steps, rights
GET  api/record/<emp_id>/attendance/?month=YYYY-MM   one month of daily attendance (present / absent / leave / holiday …)
PUT  api/record/<emp_id>/job/               job card: branch, department, designation, category, reporting manager, joining and
                                            confirmation date, work / visa location, attendance source, overtime, ESS, card number
GET  api/directory/?q&branch&department&designation&category&status&nationality&joined_from&joined_to&expiring&incomplete
                                            the employee list: one row per employee with status, worst document expiry and completeness,
                                            plus the four counters (active, on probation, documents expiring in 30 days, files incomplete)
GET  api/duplicates/?emirates_id&passport_no&company_email&personal_email&mobile&person_id&exclude=<emp_id>
                                            other employees that already use one of these values (duplicate check while typing)
Writes of the cards and rows use the existing api/change/<emp_id>/ (one group at a time) and api/identity/<emp_id>/.
"""
import logging
import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.apps import apps
from django.db.models import Q, Sum
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from . import services as S
from .models import BankExtra, DependentExtra, EmergencyContact, EmployeeIdentity, EmploymentInfo, QualificationExtra
from .views import Base, _emp, deny
from Translations.i18n import tr

log = logging.getLogger(__name__)
M = apps.get_model

# the identity documents shown as cards (label, number field(s), issue field, expiry field, extra fields)
DOCS = [
    ('passport', 'Passport', ['passport_no'], 'passport_issue_date', 'passport_expiry_date', ['passport_place_of_issue', 'passport_country_id']),
    ('visa', 'Residence visa', ['visa_number', 'visa_uid'], 'visa_issue_date', 'visa_expiry_date', ['visa_type', 'visa_file_no', 'visa_sponsor']),
    ('emirates_id', 'Emirates ID', ['emirates_id'], 'emirates_id_issue_date', 'emirates_id_expiry_date', []),
    ('labour_card', 'Labour card / work permit', ['labour_card_no', 'work_permit_no'], 'labour_card_issue_date', 'labour_card_expiry_date', ['work_permit_expiry_date']),
    ('mohre_contract', 'MOHRE contract', ['mohre_contract_no'], 'mohre_contract_start', 'mohre_contract_end', ['mohre_contract_type']),
    ('insurance', 'Medical insurance', ['insurance_card_no'], None, 'insurance_expiry_date', ['insurance_provider']),
    ('driving_licence', 'Driving licence', ['driving_licence_no'], 'driving_licence_issue_date', 'driving_licence_expiry_date', ['driving_licence_emirate', 'driving_licence_categories']),
]


def expiry(d, today=None):
    """Badge of a document by days to expiry: >60 valid (green), 31–60 amber, 1–30 red outline, expired solid red."""
    if not d:
        return None
    today = today or date.today()
    days = (d - today).days
    if days < 0:
        return {'date': d, 'days': days, 'level': 'expired', 'label': tr('Expired {0} days ago', -days) if days < -1 else tr('Expired yesterday')}
    if days == 0:
        return {'date': d, 'days': 0, 'level': 'red', 'label': tr('Expires today')}
    if days <= 30:
        return {'date': d, 'days': days, 'level': 'red', 'label': tr('Expires in {0} days', days)}
    if days <= 60:
        return {'date': d, 'days': days, 'level': 'amber', 'label': tr('Expires in {0} days', days)}
    return {'date': d, 'days': days, 'level': 'valid', 'label': tr('Valid')}


LEVEL_ORDER = {'expired': 0, 'red': 1, 'amber': 2, 'valid': 3}


def doc_cards(ident, emp):
    out = []
    for key, label, nums, issue, exp, extra in DOCS:
        g = (lambda f: getattr(ident, f, None) if ident is not None else None)
        number = ' / '.join(str(g(n)) for n in nums if g(n))
        card = {'key': key, 'label': label, 'number': number, 'numbers': {n: g(n) or '' for n in nums},
                'issue_date': g(issue) if issue else None, 'expiry_date': g(exp), 'badge': expiry(g(exp)),
                'extra': {f: (str(g(f)) if g(f) is not None else '') for f in extra}, 'fields': nums + ([issue] if issue else []) + [exp] + extra}
        out.append(card)
    if emp.person_id:
        out.append({'key': 'person_id', 'label': 'MOHRE person code', 'number': emp.person_id, 'numbers': {'person_id': emp.person_id},
                    'issue_date': None, 'expiry_date': None, 'badge': None, 'extra': {}, 'fields': []})
    return out


def worst(badges):
    b = [x for x in badges if x]
    if not b:
        return None
    return sorted(b, key=lambda x: (LEVEL_ORDER[x['level']], x['days']))[0]


def name_of(obj, *attrs):
    if obj is None:
        return ''
    for a in attrs:
        v = getattr(obj, a, None)
        if v:
            return str(v)
    return str(obj)


def user_label(u):
    if u is None:
        return ''
    E = M('EmpManagement', 'emp_master')
    e = E.objects.filter(users=u).first()
    return S.full_name(e) if e else (u.get_full_name() or u.username)


def initials(e):
    a = (e.emp_first_name or '').strip()[:1]
    b = (e.emp_last_name or '').strip()[:1] or (e.emp_middle_name or '').strip()[:1]
    return (a + b).upper() or (e.emp_code or '?')[:2].upper()


def media(request, f):
    try:
        return request.build_absolute_uri(f.url) if f else None
    except Exception:
        return None


def money(v):
    return float(Decimal(str(v or 0)).quantize(Decimal('0.01')))


def years_months(start, end):
    if not start or not end or end < start:
        return 0, 0
    m = (end.year - start.year) * 12 + (end.month - start.month) - (1 if end.day < start.day else 0)
    return m // 12, m % 12


def ym_text(y, m):
    parts = []
    if y:
        parts.append(f'{y} yr')
    if m or not y:
        parts.append(f'{m} mo')
    return ' '.join(parts)


# ------------------------------------------------------------------ header + groups
def header(request, emp, ident, info, comp, docs):
    today = date.today()
    st = info.status if info else 'active'
    chips = [{'level': {'active': 'ok', 'probation': 'info', 'on_notice': 'warn'}.get(st, 'bad'),
              'label': dict(EmploymentInfo.STATUS).get(st, st)}]
    if info and info.probation_status in ('on_probation', 'extended') and info.probation_end_date:
        left = (info.probation_end_date - today).days
        pchip = {'level': 'info' if left >= 0 else 'warn', 'label': tr('On probation ({0} days left)', left) if left >= 0 else tr('Probation ended {0} days ago', -left)}
        if st == 'probation':
            chips[0] = pchip          # one chip, not "On probation" twice
        else:
            chips.append(pchip)
    if emp.is_ess:
        chips.append({'level': 'info', 'label': tr('ESS user')})
    if not emp.is_active:
        chips.append({'level': 'bad', 'label': tr('Inactive')})
    nxt = worst([d['badge'] for d in docs if d['badge'] and d['badge']['days'] >= -3650])
    nxt_doc = None
    if nxt:
        nxt_doc = next(d['label'] for d in docs if d['badge'] is nxt)
    nat = name_of(emp.emp_nationality, 'N_name')
    return {
        'id': emp.pk, 'code': emp.emp_code, 'name': S.full_name(emp), 'arabic_name': getattr(ident, 'arabic_name', '') or '',
        'first_name': emp.emp_first_name or '', 'initials': initials(emp), 'photo': media(request, emp.emp_profile_pic),
        'designation': name_of(emp.emp_desgntn_id, 'desgntn_job_title'), 'department': name_of(emp.emp_dept_id, 'dept_name'),
        'branch': name_of(emp.emp_branch_id, 'branch_name'), 'category': name_of(emp.emp_ctgry_id, 'ctgry_title'),
        'chips': chips, 'status': st,
        'facts': {'joined': emp.emp_joined_date, 'manager': user_label(emp.emp_reporting_manager), 'nationality': nat if emp.emp_nationality else '',
                  'next_expiry': ({'document': nxt_doc, **nxt} if nxt else None)},
        'completeness': comp, 'is_active': bool(emp.is_active), 'is_ess': bool(emp.is_ess), 'user_id': emp.users_id,
    }


def single(emp, ident, keys):
    out = {}
    for k in keys:
        if hasattr(emp, k) or k in ('emp_nationality', 'emp_relegion', 'emp_country_id', 'emp_state_id'):
            if k in ('emp_nationality', 'emp_relegion', 'emp_country_id', 'emp_state_id'):
                out[k] = getattr(emp, k + '_id', None)
                out[k + '__label'] = str(getattr(emp, k) or '')
            else:
                out[k] = getattr(emp, k)
        elif ident is not None and hasattr(ident, k):
            out[k] = getattr(ident, k)
    return out


def group_defs():
    return {g['key']: g for g in S.profile_groups()}


def record(request, emp, can_edit):
    today = date.today()
    ident = S.identity_for(emp.pk)
    info = S.ensure_info(emp)
    contacts = list(EmergencyContact.objects.filter(employee_id=emp.pk).order_by('-is_primary', 'id'))
    comp = S.completeness(emp, ident, bool(contacts))
    docs = doc_cards(ident, emp)
    G = group_defs()
    fk = lambda g: [f['key'] for f in G.get(g, {}).get('fields', [])]

    # many-row groups
    fam_qs = list(M('EmpManagement', 'emp_family').objects.filter(emp_id=emp).order_by('id'))
    fx = {x.family_id: x for x in DependentExtra.objects.filter(family_id__in=[f.pk for f in fam_qs])}
    family = []
    for f in fam_qs:
        x = fx.get(f.pk)
        age = None
        if f.ef_date_of_birth:
            age = today.year - f.ef_date_of_birth.year - ((today.month, today.day) < (f.ef_date_of_birth.month, f.ef_date_of_birth.day))
        row = {'id': f.pk, 'ef_member_name': f.ef_member_name, 'emp_relation': f.emp_relation, 'ef_date_of_birth': f.ef_date_of_birth, 'age': age,
               'ef_company_expence': money(f.ef_company_expence) if f.ef_company_expence is not None else None}
        for k in ('gender', 'nationality_id', 'passport_no', 'passport_expiry_date', 'emirates_id', 'emirates_id_expiry_date', 'visa_number',
                  'visa_expiry_date', 'insured', 'visa_sponsored'):
            row[k] = getattr(x, k, None) if x else None
        row['visa_badge'] = expiry(row['visa_expiry_date'])
        row['eid_badge'] = expiry(row['emirates_id_expiry_date'])
        family.append(row)

    banks = list(M('EmpManagement', 'EmployeeBankDetail').objects.filter(employee=emp).order_by('id'))
    bx = {x.bank_detail_id: x for x in BankExtra.objects.filter(bank_detail_id__in=[b.pk for b in banks])}
    bank = []
    for b in banks:
        x = bx.get(b.pk)
        iban = (b.iban_number or '').replace(' ', '')
        bank.append({'id': b.pk, 'bank_name': b.bank_name, 'branch_name': b.branch_name, 'account_number': b.account_number, 'iban_number': iban,
                     'iban_masked': (' '.join(iban[i:i + 4] for i in range(0, len(iban), 4))) if iban else '',
                     'iban_last4': iban[-4:] if iban else '', 'route_code': b.route_code, 'bank_address': getattr(b, 'bank_address', ''), 'is_active': b.is_active,
                     'payment_mode': getattr(x, 'payment_mode', 'wps') if x else 'wps', 'wps_agent': getattr(x, 'wps_agent', '') if x else '',
                     'is_primary': bool(getattr(x, 'is_primary', False)) if x else False, 'effective_from': getattr(x, 'effective_from', None) if x else None,
                     'iban_ok': _iban_ok(iban)})
    if bank and not any(r['is_primary'] for r in bank):
        act = [r for r in bank if r['is_active']] or bank
        act[0]['is_primary_auto'] = True

    quals = list(M('EmpManagement', 'EmpQualification').objects.filter(emp_id=emp).order_by('-emp_qf_year', 'id'))
    qx = {x.qualification_id: x for x in QualificationExtra.objects.filter(qualification_id__in=[q.pk for q in quals])}
    qualifications = []
    for q in quals:
        x = qx.get(q.pk)
        qualifications.append({'id': q.pk, 'emp_qualification': q.emp_qualification, 'emp_qf_subject': q.emp_qf_subject, 'emp_qf_instituition': q.emp_qf_instituition,
                               'emp_qf_year': q.emp_qf_year, 'grade': getattr(x, 'grade', '') if x else '', 'country_id': getattr(x, 'country_id', None) if x else None,
                               'attested': bool(getattr(x, 'attested', False)) if x else False, 'attestation_date': getattr(x, 'attestation_date', None) if x else None,
                               'equivalency': bool(getattr(x, 'equivalency', False)) if x else False,
                               'attachment': media(request, getattr(x, 'attachment', None)) if x else None,
                               'attestation': 'Attested' if x and x.attested else 'Pending'})

    jobs = list(M('EmpManagement', 'EmpJobHistory').objects.filter(emp_id=emp).order_by('-emp_jh_from_date', '-id'))
    experience, total_m = [], 0
    for j in jobs:
        y, mo = years_months(j.emp_jh_from_date, j.emp_jh_end_date)
        total_m += y * 12 + mo
        experience.append({'id': j.pk, 'emp_jh_company_name': j.emp_jh_company_name, 'emp_jh_designation': j.emp_jh_designation,
                           'emp_jh_from_date': j.emp_jh_from_date, 'emp_jh_end_date': j.emp_jh_end_date, 'duration': ym_text(y, mo),
                           'emp_jh_leaving_salary_permonth': money(j.emp_jh_leaving_salary_permonth) if j.emp_jh_leaving_salary_permonth is not None else None,
                           'emp_jh_reason': j.emp_jh_reason, 'emp_jh_years_experiance': getattr(j, 'emp_jh_years_experiance', None)})
    employers = len({(j.emp_jh_company_name or '').strip().lower() for j in jobs if j.emp_jh_company_name})
    exp_total = {'text': ym_text(total_m // 12, total_m % 12), 'employers': employers} if jobs else None

    docs_qs = M('EmpManagement', 'Emp_Documents').objects.filter(emp_id=emp).select_related('document_type').order_by('emp_doc_expiry_date', 'id')
    files = []
    for d in docs_qs:
        files.append({'id': d.pk, 'document_type': d.document_type_id, 'type': name_of(d.document_type, 'type_name', 'name'),
                      'emp_doc_number': d.emp_doc_number, 'emp_doc_issued_date': d.emp_doc_issued_date, 'emp_doc_expiry_date': d.emp_doc_expiry_date,
                      'badge': expiry(d.emp_doc_expiry_date), 'file': media(request, d.emp_doc_document),
                      'file_name': (d.emp_doc_document.name.rsplit('/', 1)[-1] if d.emp_doc_document else '')})

    emergency = [{'id': c.pk, 'name': c.name, 'relation': c.relation, 'mobile': c.mobile, 'alt_phone': c.alt_phone, 'email': c.email,
                  'address': c.address, 'is_primary': c.is_primary} for c in contacts]

    sk = S.skills_json(emp)
    skills = ([{'id': s['id'], 'kind': 'language', 'type': tr('Language'), 'skill': s['skill'], 'level': s['percentage']} for s in sk['language']] +
              [{'id': s['id'], 'kind': 'programming', 'type': tr('Technical'), 'skill': s['skill'], 'level': s['percentage']} for s in sk['programming']] +
              [{'id': s['id'], 'kind': 'marketing', 'type': tr('Marketing'), 'skill': s['skill'], 'level': s['percentage']} for s in sk['marketing']] +
              [{'id': s['id'], 'kind': 'learning', 'type': s['category'] or tr('Skill'), 'skill': s['skill'], 'level': s['level'], 'source': s['source']} for s in sk['learning']])

    hdr = header(request, emp, ident, info, comp, docs)
    job = {
        'emp_branch_id': emp.emp_branch_id_id, 'branch': hdr['branch'], 'emp_dept_id': emp.emp_dept_id_id, 'department': hdr['department'],
        'emp_desgntn_id': emp.emp_desgntn_id_id, 'designation': hdr['designation'], 'emp_ctgry_id': emp.emp_ctgry_id_id, 'category': hdr['category'],
        'emp_reporting_manager': emp.emp_reporting_manager_id, 'manager': hdr['facts']['manager'], 'emp_joined_date': emp.emp_joined_date,
        'emp_date_of_confirmation': emp.emp_date_of_confirmation, 'work_location': emp.work_location_id, 'work_location__label': name_of(emp.work_location, 'branch_name') if emp.work_location_id else '',
        'visa_location': emp.visa_location_id, 'visa_location__label': name_of(emp.visa_location, 'branch_name') if emp.visa_location_id else '',
        'attendance_source': emp.attendance_source, 'emp_ot_applicable': bool(emp.emp_ot_applicable), 'is_ess': bool(emp.is_ess),
        'barcode_number': emp.barcode_number or '', 'person_id': emp.person_id or '',
    }
    org = None
    try:
        from OrgStructure import services as OS
        fields = OS.active_emp_fields()
        if fields:
            o = OS.org_rows([emp.pk]).get(emp.pk)
            org = {'fields': fields, **OS.org_values(o, fields)}
    except Exception:
        org = None

    pay = pay_summary(request, emp, bank)
    time_assets = time_summary(emp)
    nxt_steps = [
        {'key': 'identity', 'label': tr('Add passport and visa'), 'done': bool(ident and ident.passport_no and (ident.visa_number or S._is_national(emp, ident))), 'group': 'documents'},
        {'key': 'salary', 'label': tr('Add salary'), 'done': bool(pay['components']), 'group': 'pay'},
        {'key': 'bank', 'label': tr('Add bank (WPS)'), 'done': any(b['iban_number'] for b in bank), 'group': 'pay'},
        {'key': 'family', 'label': tr('Add family'), 'done': bool(family), 'group': 'personal'},
        {'key': 'emergency', 'label': tr('Add emergency contact'), 'done': bool(emergency), 'group': 'personal'},
    ]
    return {
        'header': hdr, 'can_edit': can_edit, 'next_steps': nxt_steps,
        'personal': single(emp, ident, fk('personal')), 'contact': single(emp, ident, fk('contact')), 'address': single(emp, ident, fk('address')),
        'identity': single(emp, ident, fk('identity')), 'identity_id': getattr(ident, 'pk', None),
        'employment': S.employment_json(emp, info), 'job': job, 'org': org,
        'family': family, 'bank': bank, 'qualifications': qualifications, 'experience': experience, 'experience_total': exp_total,
        'documents': docs, 'files': files, 'emergency': emergency, 'skills': skills,
        'pay': pay, 'time': time_assets,
        'history': S.timeline(emp)[:8] if hasattr(S, 'timeline') else [],
    }


def _iban_ok(iban):
    if not iban:
        return None
    try:
        from .validators import iban_mod97_ok
        return bool(re.fullmatch(r'AE\d{21}', iban)) and iban_mod97_ok(iban)
    except Exception:
        return None


def pay_summary(request, emp, bank):
    ESS = M('PayrollManagement', 'EmployeeSalaryStructure')
    comps = []
    gross, ded = Decimal('0'), Decimal('0')
    for r in ESS.objects.filter(employee=emp, is_active=True).select_related('component').order_by('component__component_type', 'id'):
        c = r.component
        fixed = c.component_value_type == 'fixed'
        amt = Decimal(str(r.amount or 0))
        if fixed and c.component_type == 'addition':
            gross += amt
        elif fixed and c.component_type == 'deduction':
            ded += amt
        comps.append({'id': r.pk, 'component': c.name, 'code': c.code, 'type': c.component_type, 'category': c.get_payroll_category_display() if hasattr(c, 'get_payroll_category_display') else c.payroll_category,
                      'fixed': fixed, 'amount': money(amt) if fixed else None, 'formula': '' if fixed else (c.formula or '')})
    rev = []
    try:
        for h in M('PayrollManagement', 'SalaryRevisionHistory').objects.filter(employee=emp).select_related('component').order_by('-revised_on')[:12]:
            rev.append({'date': h.revised_on, 'component': name_of(h.component, 'name'), 'old': money(h.old_amount), 'new': money(h.new_amount),
                        'reason': h.remarks or '', 'period': h.effective_period or ''})
    except Exception:
        log.exception('salary revisions')
    slips = []
    for p in M('PayrollManagement', 'Payslip').objects.filter(employee=emp).select_related('payroll_run').order_by('-payroll_run__year', '-payroll_run__month', '-id')[:24]:
        r = p.payroll_run
        slips.append({'id': p.pk, 'month': date(r.year, r.month, 1) if r else None, 'run': r.name if r else '',
                      'period_from': getattr(r, 'attendance_start_date', None), 'period_to': getattr(r, 'attendance_end_date', None),
                      'gross': money(p.gross_salary), 'net': money(p.net_salary), 'status': p.status or '', 'run_status': getattr(r, 'status', ''),
                      'pdf': f'/payroll/api/payslip/employee/{emp.emp_code}/download/{r.year}/{r.month}/' if r else None})
    loans = []
    for l in M('PayrollManagement', 'LoanApplication').objects.filter(employee=emp).select_related('loan_type').order_by('-applied_on'):
        amt = Decimal(str(l.amount_requested or 0))
        bal = Decimal(str(l.remaining_balance if l.remaining_balance is not None else amt))
        loans.append({'id': l.pk, 'type': name_of(l.loan_type, 'loan_type', 'name'), 'amount': money(amt), 'emi': money(l.emi_amount),
                      'paid': money(amt - bal), 'balance': money(bal), 'status': l.status, 'progress': round(float((amt - bal) / amt * 100)) if amt else 0})
    primary = next((b for b in bank if b.get('is_primary')), None) or next((b for b in bank if b.get('is_primary_auto')), None)
    wps = [{'label': tr('MOHRE person code'), 'ok': bool(emp.person_id and len(emp.person_id) == 14)},
           {'label': tr('Salary IBAN'), 'ok': bool(primary and primary['iban_number'] and primary.get('iban_ok') is not False)},
           {'label': tr('Routing code'), 'ok': bool(primary and primary.get('route_code') and len(str(primary['route_code'])) == 9)}]
    return {'gross': money(gross), 'deductions': money(ded), 'net': money(gross - ded), 'components': comps, 'revisions': rev,
            'payslips': slips, 'loans': loans, 'wps': wps, 'wps_ready': all(x['ok'] for x in wps)}


def time_summary(emp):
    leaves = []
    try:
        from LeavePolicy import engine
        B = M('calendars', 'emp_leave_balance')
        LR = M('calendars', 'employee_leave_request')
        for b in B.objects.filter(employee=emp).select_related('leave_type').order_by('leave_type__name'):
            lt = b.leave_type
            line = engine.line_for(emp, lt.pk)
            if line is None and not b.balance:
                continue
            y0, y1 = engine.leave_year(line.policy, date.today()) if line else (date(date.today().year, 1, 1), date(date.today().year, 12, 31))
            taken = engine.used_in(emp.pk, lt.pk, y0, y1, statuses=('approved',)) if line else 0
            pending = engine.used_in(emp.pk, lt.pk, y0, y1, statuses=('pending',)) if line else 0
            ent = float(line.days_per_year or 0) if line else None
            leaves.append({'leave_type_id': lt.pk, 'type': lt.name, 'entitlement': ent, 'taken': float(taken or 0), 'pending': float(pending or 0),
                           'balance': float(b.balance or 0)})
    except Exception:
        log.exception('leave balances')
    assets = []
    try:
        for a in M('OrganisationManager', 'AssetAllocation').objects.filter(employee=emp).select_related('asset').order_by('-assigned_date'):
            assets.append({'id': a.pk, 'asset_id': a.asset_id, 'asset': a.asset.name if a.asset_id else '', 'serial': getattr(a.asset, 'serial_number', '') if a.asset_id else '',
                           'issued': a.assigned_date, 'returned': a.returned_date, 'condition': getattr(a, 'return_condition', '') or getattr(a.asset, 'condition', ''),
                           'status': tr('Returned') if a.returned_date else tr('With employee')})
    except Exception:
        log.exception('assets')
    return {'leaves': leaves, 'assets': assets}


def attendance_month(emp, y, m):
    import calendar
    first = date(y, m, 1)
    last = date(y, m, calendar.monthrange(y, m)[1])
    days = {}
    try:
        for d in M('AttendancePlus', 'AttendanceDay').objects.filter(employee_id=emp.pk, date__range=(first, last)):
            days[d.date] = {'status': d.status, 'late': bool(getattr(d, 'is_late', False)), 'worked': round((getattr(d, 'worked_minutes', 0) or 0) / 60, 2), 'shift': getattr(d, 'shift_name', '') or ''}
    except LookupError:
        pass
    if not days:
        for a in M('calendars', 'Attendance').objects.filter(employee=emp, date__range=(first, last)):
            days[a.date] = {'status': 'present' if a.check_in_time else 'absent', 'late': False, 'worked': 0}
    out, totals = [], defaultdict(int)
    d = first
    while d <= last:
        x = days.get(d, {'status': 'future' if d > date.today() else 'not_marked', 'late': False, 'worked': 0})
        totals[x['status']] += 1
        out.append({'date': d, **x})
        d += timedelta(days=1)
    return {'month': first, 'days': out, 'totals': dict(totals)}


# ------------------------------------------------------------------ views
class RecordView(Base):
    def get(self, request, emp_id):
        emp, err = _emp(request, emp_id)
        if err:
            return err
        can_edit = S.is_hr(request, write=True) and not S.visible_emp(request, emp.pk, write=True)[1]
        data = record(request, emp, can_edit)
        # previous / next in the list the user came from (ids passed by the list) or by code
        order = [int(x) for x in str(request.GET.get('order') or '').split(',') if x.strip().isdigit()]
        if emp.pk in order:
            i = order.index(emp.pk)
            data['prev_id'] = order[i - 1] if i > 0 else None
            data['next_id'] = order[i + 1] if i + 1 < len(order) else None
        else:
            qs = S.emp_qs(request).filter(is_active=True).order_by('emp_code')
            data['prev_id'] = qs.filter(emp_code__lt=emp.emp_code).order_by('-emp_code').values_list('id', flat=True).first()
            data['next_id'] = qs.filter(emp_code__gt=emp.emp_code).values_list('id', flat=True).first()
        return Response(data)


class RecordAttendanceView(Base):
    def get(self, request, emp_id):
        emp, err = _emp(request, emp_id)
        if err:
            return err
        m = request.GET.get('month') or date.today().strftime('%Y-%m')
        try:
            y, mo = [int(x) for x in m.split('-')[:2]]
            date(y, mo, 1)
        except Exception:
            raise ValidationError({'month': tr('Use YYYY-MM.')})
        return Response(attendance_month(emp, y, mo))


JOB_FIELDS = ('emp_branch_id', 'emp_dept_id', 'emp_desgntn_id', 'emp_ctgry_id', 'emp_reporting_manager', 'emp_joined_date', 'emp_date_of_confirmation',
              'work_location', 'visa_location', 'attendance_source', 'emp_ot_applicable', 'is_ess', 'barcode_number', 'person_id')


class RecordJobView(Base):
    def put(self, request, emp_id):
        emp, err = _emp(request, emp_id, write=True)
        if err:
            return err
        if not S.is_hr(request, write=True):
            return deny(tr('You may not change job details.'))
        data = {k: v for k, v in request.data.items() if k in JOB_FIELDS}
        unknown = [k for k in request.data if k not in JOB_FIELDS]
        if unknown:
            raise ValidationError({k: tr('This field cannot be changed here.') for k in unknown})
        for k in ('person_id', 'barcode_number'):
            if k in data and data[k] in ('',):
                data[k] = None
        from EmpManagement.serializer import EmpSerializer
        ser = EmpSerializer(emp, data=data, partial=True, context={'request': request})
        ser.is_valid(raise_exception=True)
        ser.save()
        emp.refresh_from_db()
        return Response(record(request, emp, True)['job'])

    patch = put


def directory_rows(request, emps, today=None):
    today = today or date.today()
    ids = [e.pk for e in emps]
    idn = {i.employee_id: i for i in EmployeeIdentity.objects.filter(employee_id__in=ids)}
    have = set(EmploymentInfo.objects.filter(employee_id__in=ids).values_list('employee_id', flat=True))
    for e in emps:
        if e.pk not in have:
            S.ensure_info(e)
    infos = {i.employee_id: i for i in EmploymentInfo.objects.filter(employee_id__in=ids)}
    contacts = set(EmergencyContact.objects.filter(employee_id__in=ids).values_list('employee_id', flat=True))
    banks = set(M('EmpManagement', 'EmployeeBankDetail').objects.filter(employee_id__in=ids, is_active=True).exclude(iban_number__isnull=True)
                .exclude(iban_number='').values_list('employee_id', flat=True))
    rows = []
    for e in emps:
        i, f = idn.get(e.pk), infos.get(e.pk)
        badges = []
        for key, label, nums, issue, exp, extra in DOCS:
            if key in ('driving_licence',):
                continue
            b = expiry(getattr(i, exp, None) if i else None, today)
            if b:
                badges.append((label, b))
        w = sorted(badges, key=lambda x: (LEVEL_ORDER[x[1]['level']], x[1]['days']))[0] if badges else None
        comp = S.completeness(e, i, e.pk in contacts, e.pk in banks)
        st = f.status if f else 'active'
        prob_left = (f.probation_end_date - today).days if f and f.probation_status in ('on_probation', 'extended') and f.probation_end_date else None
        rows.append({
            'id': e.pk, 'code': e.emp_code, 'name': S.full_name(e), 'initials': initials(e), 'photo': media(request, e.emp_profile_pic),
            'designation': name_of(e.emp_desgntn_id, 'desgntn_job_title') if e.emp_desgntn_id_id else '', 'designation_id': e.emp_desgntn_id_id,
            'department': name_of(e.emp_dept_id, 'dept_name') if e.emp_dept_id_id else '', 'department_id': e.emp_dept_id_id,
            'branch': name_of(e.emp_branch_id, 'branch_name') if e.emp_branch_id_id else '', 'branch_id': e.emp_branch_id_id,
            'category': name_of(e.emp_ctgry_id, 'ctgry_title') if e.emp_ctgry_id_id else '', 'category_id': e.emp_ctgry_id_id,
            'nationality': str(e.emp_nationality or '') if e.emp_nationality_id else '', 'nationality_id': e.emp_nationality_id,
            'joined': e.emp_joined_date, 'status': st, 'status_label': dict(EmploymentInfo.STATUS).get(st, st), 'probation_days_left': prob_left,
            'is_active': bool(e.is_active), 'is_ess': bool(e.is_ess), 'mobile': e.emp_mobile_number_1 or '', 'email': e.emp_company_email or e.emp_personal_email or '',
            'emirates_id': getattr(i, 'emirates_id', '') if i else '', 'manager_id': e.emp_reporting_manager_id,
            'doc': ({'document': w[0], **w[1]} if w else None), 'completeness': comp['score'], 'missing': comp['missing'][:6],
        })
    return rows


class DirectoryView(Base):
    def get(self, request):
        if not (S.is_hr(request) or S.has(request, 'view_emp_master')):
            return deny(tr('You may not see the employee list.'))
        q = request.GET
        qs = S.emp_qs(request).select_related('emp_desgntn_id', 'emp_dept_id', 'emp_branch_id', 'emp_ctgry_id', 'emp_nationality')
        if q.get('active', '1') in ('1', 'true'):
            qs = qs.filter(is_active=True)
        for key, field in (('branch', 'emp_branch_id_id'), ('department', 'emp_dept_id_id'), ('designation', 'emp_desgntn_id_id'),
                           ('category', 'emp_ctgry_id_id'), ('nationality', 'emp_nationality_id')):
            ids = [int(x) for x in str(q.get(key, '')).split(',') if x.strip().isdigit()]
            if ids:
                qs = qs.filter(**{field + '__in': ids})
        if q.get('joined_from'):
            qs = qs.filter(emp_joined_date__gte=q['joined_from'])
        if q.get('joined_to'):
            qs = qs.filter(emp_joined_date__lte=q['joined_to'])
        t = (q.get('q') or '').strip()
        if t:
            ids_eid = list(EmployeeIdentity.objects.filter(Q(emirates_id__icontains=t) | Q(passport_no__icontains=t)).values_list('employee_id', flat=True))
            qs = qs.filter(Q(emp_first_name__icontains=t) | Q(emp_last_name__icontains=t) | Q(emp_middle_name__icontains=t) | Q(emp_code__icontains=t) |
                           Q(emp_mobile_number_1__icontains=t) | Q(emp_company_email__icontains=t) | Q(emp_personal_email__icontains=t) | Q(person_id__icontains=t) |
                           Q(pk__in=ids_eid))
        emps = list(qs.order_by('emp_code')[:3000])
        rows = directory_rows(request, emps)
        counters = {'active': sum(1 for r in rows if r['status'] in ('active', 'probation', 'on_notice')),
                    'probation': sum(1 for r in rows if r['probation_days_left'] is not None),
                    'expiring': sum(1 for r in rows if r['doc'] and r['doc']['days'] <= 30),
                    'incomplete': sum(1 for r in rows if r['completeness'] < 100)}
        st = q.get('status')
        if st:
            want = set(st.split(','))
            rows = [r for r in rows if r['status'] in want or ('probation' in want and r['probation_days_left'] is not None)]
        if q.get('expiring'):
            days = int(q.get('expiring') or 30)
            rows = [r for r in rows if r['doc'] and r['doc']['days'] <= days]
        if q.get('incomplete') in ('1', 'true'):
            rows = [r for r in rows if r['completeness'] < 100]
        return Response({'rows': rows, 'count': len(rows), 'counters': counters,
                         'can_add': S.has(request, 'add_emp_master') or S.ctx(request).admin, 'can_change': S.is_hr(request, write=True),
                         'can_delete': S.has(request, 'delete_emp_master') or S.ctx(request).admin})


class DuplicatesView(Base):
    """Other employees using the same Emirates ID, passport, e-mail, mobile or MOHRE person code."""

    def get(self, request):
        q = request.GET
        exclude = int(q['exclude']) if str(q.get('exclude', '')).isdigit() else None
        E = M('EmpManagement', 'emp_master')
        out = []

        def add(field, label, emps):
            for e in emps:
                if exclude and e.pk == exclude:
                    continue
                out.append({'field': field, 'label': label, 'employee_id': e.pk, 'employee': S.full_name(e), 'code': e.emp_code})

        eid = re.sub(r'\D', '', q.get('emirates_id') or '')
        if len(eid) >= 15:
            ids = [i.employee_id for i in EmployeeIdentity.objects.all().only('employee_id', 'emirates_id') if re.sub(r'\D', '', i.emirates_id or '') == eid]
            add('emirates_id', tr('Emirates ID'), E.objects.filter(pk__in=ids))
        pp = (q.get('passport_no') or '').strip().upper()
        if len(pp) >= 5:
            ids = EmployeeIdentity.objects.filter(passport_no__iexact=pp).values_list('employee_id', flat=True)
            add('passport_no', tr('Passport number'), E.objects.filter(pk__in=list(ids)))
        for key, label in (('company_email', tr('Company e-mail')), ('personal_email', tr('Personal e-mail'))):
            v = (q.get(key) or '').strip()
            if '@' in v:
                add(key, label, E.objects.filter(Q(emp_company_email__iexact=v) | Q(emp_personal_email__iexact=v)))
        mob = re.sub(r'\D', '', q.get('mobile') or '')
        if len(mob) >= 7:
            tail = mob[-9:]
            # numbers are stored with or without spaces / +971, so compare the last 9 digits
            add('mobile', tr('Mobile'), [e for e in E.objects.exclude(emp_mobile_number_1__isnull=True).exclude(emp_mobile_number_1='')
                                         .filter(emp_mobile_number_1__contains=tail[-2:]) if re.sub(r'\D', '', e.emp_mobile_number_1 or '').endswith(tail)])
        pid = (q.get('person_id') or '').strip()
        if len(pid) >= 10:
            add('person_id', tr('MOHRE person code'), E.objects.filter(person_id__iexact=pid))
        return Response({'matches': out})


class LookupsView(Base):
    """Pick lists for the employee page and list: branches (the user's), departments, designations, categories, managers,
    nationalities, religions, countries, states, document types."""

    def get(self, request):
        def rows(model, attr, qs=None):
            Mo = M(*model.split('.'))
            qs = qs if qs is not None else Mo.objects.all()
            return [{'value': o.pk, 'label': getattr(o, attr, None) or str(o)} for o in qs.order_by(attr)[:2000]]
        br = S.user_branches(request)
        B = M('OrganisationManager', 'brnch_mstr')
        E = M('EmpManagement', 'emp_master')
        users = {}
        for e in E.objects.filter(is_active=True, users__isnull=False).select_related('users').order_by('emp_first_name'):
            users[e.users_id] = f'{S.full_name(e)} ({e.emp_code})'
        return Response({
            'branches': rows('OrganisationManager.brnch_mstr', 'branch_name', B.objects.filter(pk__in=br) if br is not None else None),
            'departments': rows('OrganisationManager.dept_master', 'dept_name'),
            'designations': rows('OrganisationManager.desgntn_master', 'desgntn_job_title'),
            'categories': rows('OrganisationManager.ctgry_master', 'ctgry_title'),
            'managers': [{'value': k, 'label': v} for k, v in users.items()],
            'nationalities': rows('Core.Nationality', 'N_name'), 'religions': rows('Core.ReligionMaster', 'religion'),
            'countries': rows('Core.cntry_mstr', 'country_name'), 'states': rows('Core.state_mstr', 'state_name'),
            'document_types': rows('EmpManagement.document_type', 'type_name'),
            'groups': S.profile_groups(),
        })
