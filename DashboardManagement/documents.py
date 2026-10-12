"""
v1.17.0 (dash17, audit C1) – the CURRENT documents of employees, one row per employee + document type.

Rules (the same for the dashboards, the "Needs your action" strip, Team, My and the report centre):
  * employee documents (EmpManagement.Emp_Documents): only active documents (is_active) count; a renewed document is
    deactivated by the renewal, so the old copy never shows as expired again. Several active documents of one type:
    the one with the latest expiry date is the current one (the others are superseded).
  * identity file (EmployeeProfile.EmployeeIdentity: passport, visa, Emirates ID, labour card, work permit, driving licence,
    medical insurance) holds the same documents: per employee + type the source with the LATEST expiry wins, so a document
    is never counted twice (types matched by name, case-insensitive).
  * v1.17.1: the MOHRE labour contract of the identity file (contract number + end date; only a contract with an end date) is the
    document type "Labour contract"; an employee document named labour / MOHRE / employment contract is the same type (one row).
"""
from django.apps import apps

IDENTITY = [('Passport', 'passport_no', 'passport_expiry_date'), ('Visa', 'visa_number', 'visa_expiry_date'),
            ('Emirates ID', 'emirates_id', 'emirates_id_expiry_date'), ('Labour card', 'labour_card_no', 'labour_card_expiry_date'),
            ('Work permit', 'work_permit_no', 'work_permit_expiry_date'), ('Driving licence', 'driving_licence_no', 'driving_licence_expiry_date'),
            ('Medical insurance', 'insurance_card_no', 'insurance_expiry_date'),
            ('Labour contract', 'mohre_contract_no', 'mohre_contract_end')]   # v1.17.1
SAME = {'mohre contract': 'labour contract', 'mohre labour contract': 'labour contract', 'labor contract': 'labour contract',
        'employment contract': 'labour contract', 'mohre employment contract': 'labour contract'}   # v1.17.1: one type, never counted twice


def _m(label):
    try:
        return apps.get_model(label)
    except (LookupError, ValueError):
        return None


def _key(name):
    k = ' '.join(str(name or '').lower().replace('license', 'licence').split())
    return SAME.get(k, k)   # v1.17.1


def current_documents(emp_ids, today, with_identity=True):
    """[{employee_id, type, number, expiry, days, source, _m, _id, doc}] – one per employee + document type."""
    ids = list(emp_ids)
    best = {}

    def offer(row):
        k = (row['employee_id'], _key(row['type']))
        cur = best.get(k)
        if cur is None or (row['expiry'] or _MIN) > (cur['expiry'] or _MIN):
            best[k] = row

    DOC = _m('EmpManagement.Emp_Documents')
    if DOC is not None:
        for d in DOC.objects.filter(emp_id_id__in=ids, is_active=True).select_related('document_type'):
            offer({'employee_id': d.emp_id_id, 'type': str(d.document_type or '') or 'Document', 'number': d.emp_doc_number or '',
                   'expiry': d.emp_doc_expiry_date, 'source': 'Employee document', '_m': d._meta.label, '_id': d.id, 'doc': d})
    ID = _m('EmployeeProfile.EmployeeIdentity') if with_identity else None
    if ID is not None:
        for i in ID.objects.filter(employee_id__in=ids):
            for label, nf, ef in IDENTITY:
                num, exp = getattr(i, nf, None), getattr(i, ef, None)
                if not num and not exp:
                    continue
                if nf == 'mohre_contract_no' and not exp:   # v1.17.1: an unlimited contract has no end date – nothing to renew
                    continue
                offer({'employee_id': i.employee_id, 'type': label, 'number': num or '', 'expiry': exp, 'source': 'Identity',
                       '_m': 'EmpManagement.emp_master', '_id': i.employee_id, 'doc': None})
    out = list(best.values())
    for r in out:
        r['days'] = (r['expiry'] - today).days if r['expiry'] else None
    out.sort(key=lambda r: (r['expiry'] is None, r['expiry'] or _MAX))
    return out


def current_doc_ids(emp_ids):
    """ids of the Emp_Documents rows that are current (active, latest expiry of their employee + type)."""
    DOC = _m('EmpManagement.Emp_Documents')
    best = {}
    if DOC is None:
        return set()
    for d in DOC.objects.filter(emp_id_id__in=list(emp_ids), is_active=True).select_related('document_type'):
        k = (d.emp_id_id, _key(d.document_type))
        if k not in best or (d.emp_doc_expiry_date or _MIN) > (best[k][1] or _MIN):
            best[k] = (d.id, d.emp_doc_expiry_date)
    return {v[0] for v in best.values()}


from datetime import date as _date  # noqa: E402
_MIN, _MAX = _date(1, 1, 1), _date(9999, 12, 31)
