"""v1.56.0 – one numbering pattern for every document type (setup guide, step 4).

Pattern tokens: {TYPE} the type's code · {BRANCH} branch code · {LOC} location code · {YYYY} / {YY} year · {MM} month ·
{SEQ:n} the counter with n digits. The counter is kept per company, branch or location and restarts every year, every
month or never. While the pattern is on, it is used instead of the prefix / length rows of Settings › Document numbering
(those rows are kept and work again when the pattern is switched off). Numbers already issued are never changed.
"""
import logging
import re

from django.apps import apps
from django.db import transaction
from django.utils import timezone

log = logging.getLogger(__name__)

# the 28 document types and their suggested codes (deck slide 25)
SUGGESTED = {
    'leave_request': 'LV', 'lateinearlyout_request': 'LE', 'attendance_correction': 'AC', 'shift_request': 'SR',
    'payroll_run': 'PR', 'exit_settlement': 'FS', 'resignation_request': 'RS', 'advance_salary_request': 'AS',
    'loan_request': 'LN', 'air_ticket_request': 'AT',
    'general_request': 'GR', 'document_request': 'DR', 'letter_request': 'HL', 'profile_change_request': 'PC', 'grievance': 'GV',
    'expense_trip': 'TR', 'expense_report': 'ER', 'expense_advance': 'EA',
    'asset_request': 'AR', 'asset_return': 'AN', 'asset_transfer': 'AF', 'asset_maintenance': 'AM', 'asset_damage': 'AD',
    'asset_loss': 'AL', 'asset_disposal': 'AX',
    'manpower_requisition': 'MR', 'job_offer': 'JO', 'employee_transfer': 'ET',
}
AREAS = [
    ('Leave and time', ['leave_request', 'lateinearlyout_request', 'attendance_correction', 'shift_request']),
    ('Payroll and exit', ['payroll_run', 'exit_settlement', 'resignation_request', 'advance_salary_request', 'loan_request', 'air_ticket_request']),
    ('Self-service', ['general_request', 'document_request', 'letter_request', 'profile_change_request', 'grievance']),
    ('Expenses', ['expense_trip', 'expense_report', 'expense_advance']),
    ('Assets', ['asset_request', 'asset_return', 'asset_transfer', 'asset_maintenance', 'asset_damage', 'asset_loss', 'asset_disposal']),
    ('Recruitment and HR actions', ['manpower_requisition', 'job_offer', 'employee_transfer']),
]
TOKEN = re.compile(r'\{(TYPE|BRANCH|LOC|YYYY|YY|MM|SEQ(?::(\d{1,2}))?)\}')


class PatternError(ValueError):
    pass


LABELS = {
    'leave_request': 'Leave request', 'lateinearlyout_request': 'Late-in / early-out', 'attendance_correction': 'Attendance correction',
    'shift_request': 'Shift request', 'payroll_run': 'Payroll run', 'exit_settlement': 'Final settlement', 'resignation_request': 'Resignation',
    'advance_salary_request': 'Advance salary', 'loan_request': 'Loan', 'air_ticket_request': 'Air ticket', 'general_request': 'General request',
    'document_request': 'Document request', 'letter_request': 'HR letter', 'profile_change_request': 'Profile change', 'grievance': 'Grievance',
    'expense_trip': 'Business trip', 'expense_report': 'Expense report', 'expense_advance': 'Expense advance', 'asset_request': 'Asset request',
    'asset_return': 'Asset return', 'asset_transfer': 'Asset transfer', 'asset_maintenance': 'Asset maintenance', 'asset_damage': 'Asset damage',
    'asset_loss': 'Asset loss', 'asset_disposal': 'Asset disposal', 'manpower_requisition': 'Manpower requisition', 'job_offer': 'Job offer',
    'employee_transfer': 'Employee transfer',
}


def type_labels():
    DN = apps.get_model('OrganisationManager', 'DocumentNumbering')
    return {**dict(DN.DOCUMENT_TYPES), **LABELS}


def validate(pattern, codes):
    """Problems of a pattern and its codes (empty list = fine)."""
    out = []
    p = (pattern or '').strip()
    if not p:
        return ['Enter a pattern.']
    if len(p) > 80:
        out.append('The pattern is longer than 80 characters.')
    seq = re.findall(r'\{SEQ(?::(\d{1,2}))?\}', p)
    if len(seq) != 1:
        out.append('The pattern needs exactly one counter: {SEQ:5} (5 digits).')
    elif seq[0] and not 1 <= int(seq[0]) <= 9:
        out.append('The counter has 1 to 9 digits.')
    rest = TOKEN.sub('', p)
    if '{' in rest or '}' in rest:
        out.append('Unknown token – use {TYPE} {BRANCH} {LOC} {YYYY} {YY} {MM} {SEQ:n}.')
    if re.search(r'[^A-Za-z0-9\-_/.]', rest):
        out.append('Between the tokens use letters, digits and - _ / . only.')
    if '{TYPE}' in p:
        seen = {}
        for t, c in (codes or {}).items():
            c = (c or '').strip().upper()
            if not re.fullmatch(r'[A-Z0-9]{1,6}', c):
                out.append(f'The code of {t} must be 1 to 6 letters or digits.')
            elif c in seen:
                out.append(f'{t} and {seen[c]} have the same code {c} – no two types may share a code.')
            seen.setdefault(c, t)
    return out


def _branch_code(branch):
    if branch is None:
        return 'HO'
    code = (getattr(branch, 'branch_code', '') or '').strip().upper()
    if not code:
        code = re.sub(r'[^A-Z]', '', (branch.branch_name or '').upper())[:3] or f'B{branch.pk}'
    return re.sub(r'[^A-Z0-9]', '', code)[:8] or f'B{branch.pk}'


def _location():
    try:
        from OrgStructure.location import current_location, current_request
        loc = current_location(current_request(), None)
        if loc:
            L = apps.get_model('OrgStructure', 'Location')
            return L.objects.filter(pk=loc).first()
    except Exception:
        return None
    return None


def render(pattern, code, branch, loc, when, seq):
    def sub(m):
        k = m.group(1)
        if k == 'TYPE':
            return code
        if k == 'BRANCH':
            return _branch_code(branch)
        if k == 'LOC':
            return re.sub(r'[^A-Z0-9]', '', ((loc.code if loc else '') or '').upper())[:8] or _branch_code(branch)
        if k == 'YYYY':
            return f'{when.year:04d}'
        if k == 'YY':
            return f'{when.year % 100:02d}'
        if k == 'MM':
            return f'{when.month:02d}'
        return str(seq).zfill(int(m.group(2) or 4))
    return TOKEN.sub(sub, pattern)


def preview(pattern, codes, branch=None, types=('leave_request', 'payroll_run')):
    when = timezone.localdate()
    B = apps.get_model('OrganisationManager', 'brnch_mstr')
    branches = [branch] if branch is not None else list(B.objects.order_by('id')[:2]) or [None]
    out = []
    for i, t in enumerate(types):
        b = branches[i % len(branches)]
        out.append(render(pattern, (codes or {}).get(t) or SUGGESTED.get(t, 'DOC'), b, None, when, 1))
    return out


def active():
    try:
        P = apps.get_model('ZeoAdmin', 'NumberingPattern')
        row = P.objects.order_by('id').first()
        return row if row is not None and row.active else None
    except Exception:
        return None


def next_number(doc_type, branch, taken=None):
    """The next number of doc_type from the pattern, or None when the pattern is off."""
    row = active()
    if row is None:
        return None
    try:
        if branch is not None and isinstance(branch, int):
            branch = apps.get_model('OrganisationManager', 'brnch_mstr').objects.filter(pk=branch).first()
        when = timezone.localdate()
        loc = _location() if (row.scope == 'location' or '{LOC}' in row.pattern) else None
        if row.scope == 'company':
            key = ''
        elif row.scope == 'location' and loc is not None:
            key = f'l{loc.pk}'
        else:
            key = f'b{branch.pk}' if branch is not None else ''
        period = f'{when.year:04d}' if row.restart == 'year' else f'{when.year:04d}{when.month:02d}' if row.restart == 'month' else ''
        code = (row.codes or {}).get(doc_type) or SUGGESTED.get(doc_type) or doc_type[:3].upper()
        C = apps.get_model('ZeoAdmin', 'NumberingCounter')
        with transaction.atomic():
            c, _ = C.objects.select_for_update().get_or_create(type=doc_type, scope_key=key, period=period)
            for _ in range(10000):
                c.last += 1
                num = render(row.pattern, code, branch, loc, when, c.last)
                if not (taken and taken(num)):
                    break
            c.save(update_fields=['last'])
        return num
    except Exception:
        log.exception('numbering pattern %s', doc_type)
        return None
