"""v1.67.6 (setup) item 4 – document numbering for every app with transactions.

Settings › Document numbering (OrganisationManager.DocumentNumbering, one row per branch and type) numbered only the HR and
Payroll documents. Projects, CRM, Property, Maintenance, Asset Management, People and HSE numbered their records with their own
counters (TKT-00001 …), so a company that activated Projects had no numbering to see or change ("after activating Projects
there is no document numbering").

Now every app declares its series (SERIES). Activating an app adds its missing series; the screen lists the series of the
active apps only. The apps take their numbers from the series (`take`); without a series row they keep their own counter, as
before.

* HR / Payroll types are per branch (as before: a row per branch, made with the branch).
* The other apps' records have no branch: their series is company-wide, kept on the company's first branch (the head office)
  and shown as "(company-wide)".
* Never renumbered: a series added for an existing company starts after the last number the app issued (its own counter)
  with the app's own prefix and length (TKT-00013 follows TKT-00012); existing rows are never changed.

`ensure(keys=None, apply=True)` adds what is missing (dry run: apply=False) – used on activation, at company creation, by
`manage.py add_numbering_series` and by the "Add the missing series" button.
"""
import logging

from django.apps import apps as djapps
from django.db import transaction

log = logging.getLogger(__name__)

# HR and Payroll – the types of DocumentNumbering since 1.10 / 1.22.1, per branch
BRANCH_TYPES = {
    'hrms': ['general_request', 'leave_request', 'asset_request', 'document_request', 'resignation_request', 'lateinearlyout_request',
             'expense_trip', 'expense_report', 'expense_advance', 'profile_change_request', 'letter_request', 'grievance',
             'asset_return', 'asset_transfer', 'asset_maintenance', 'asset_damage', 'asset_loss', 'asset_disposal',
             'manpower_requisition', 'job_offer', 'attendance_correction', 'employee_transfer', 'shift_request'],
    'payroll': ['payroll_run', 'advance_salary_request', 'air_ticket_request', 'loan_request', 'exit_settlement'],
}

# the other apps: (kind used by the app's counter, label, default prefix) – company-wide series
SERIES = {
    'projects': [('project', 'Project', 'PRJ'), ('ticket', 'Support ticket', 'TKT'), ('amc', 'AMC contract', 'AMC'),
                 ('dev', 'Development request', 'DR'), ('cr', 'Change request', 'CR')],
    'crm': [('lead', 'Lead', 'LD'), ('opportunity', 'Opportunity', 'OPP'), ('quotation', 'Quotation', 'QT'),
            ('sales_order', 'Sales order', 'SO'), ('activity', 'CRM activity', 'ACT'), ('viewing', 'Viewing', 'VW'), ('offer', 'Offer', 'OFR')],
    'property': [('lease', 'Lease', 'LSE'), ('receipt', 'Receipt', 'RCT'), ('cheque', 'Cheque', 'CHQ'), ('bill', 'Property bill', 'BIL'),
                 ('credit', 'Credit note', 'CN'), ('refund', 'Refund', 'RFD'), ('reservation', 'Unit reservation', 'RSV'),
                 ('sale', 'Sale (SPA)', 'SPA'), ('commission', 'Commission', 'COM'), ('moveout', 'Move-out', 'MO'),
                 ('inspection', 'Unit inspection', 'INS'), ('portal_request', 'Portal request', 'REQ'), ('agreement', 'Management agreement', 'MA'),
                 ('statement', 'Owner statement', 'OS'), ('slip', 'Bank deposit slip', 'DEP'), ('settlement', 'Settlement plan', 'STL'),
                 ('recurring', 'Recurring charge', 'RC'), ('purchase_order', 'Property purchase order', 'PO'), ('waiting', 'Waiting list', 'WL'),
                 ('budget', 'Service charge budget', 'SCB')],
    'maintenance': [('work_order', 'Work order', 'WO'), ('inspection', 'Maintenance inspection', 'INS'), ('quote', 'Maintenance quote', 'QT'),
                    ('plan', 'Preventive plan', 'PM')],
    'assets': [('work_order', 'Asset work order', 'AWO'), ('reservation', 'Asset reservation', 'RSV'), ('stock_move', 'Spare part movement', 'SM'),
               ('ticket', 'Asset help-desk ticket', 'AHD'), ('audit', 'Asset count', 'AUD'), ('depreciation', 'Depreciation run', 'DEP'),
               ('plan', 'Asset maintenance plan', 'APM'), ('part_request', 'Part request', 'PRQ'), ('stock_count', 'Stock count', 'SC')],
    'people': [('development_plan', 'Development plan', 'PDP'), ('sign_request', 'E-signature request', 'SGN'), ('survey', 'Survey', 'SRV'),
               ('enrolment', 'Benefit enrolment', 'BEN'), ('claim', 'Benefit claim', 'CLM'), ('form_submission', 'Form submission', 'FRM'),
               ('recognition', 'Recognition', 'REC'), ('reward', 'Reward', 'RWD')],
    'hse': [('incident', 'Incident', 'INC'), ('permit', 'Permit to work', 'PTW'), ('risk', 'Risk assessment', 'RA'),
            ('action', 'Corrective action (CAPA)', 'CAPA'), ('inspection', 'HSE inspection', 'INS'), ('observation', 'Observation', 'OBS'),
            ('toolbox', 'Toolbox talk', 'TBT'), ('health', 'Occupational health', 'OH'), ('moc', 'Management of change', 'MOC'),
            ('audit', 'HSE audit', 'AUD'), ('nc', 'Non-conformity', 'NC'), ('review', 'Management review', 'MR'),
            ('ergo', 'Ergonomics assessment', 'ERG'), ('waste', 'Waste record', 'WST'), ('chemical', 'Chemical record', 'CHM'),
            ('claim', 'Claim', 'CLM'), ('hygiene', 'Industrial hygiene', 'IH')],
}
KIT_LABEL = {'crm': 'ZeoCRM', 'property': 'ZeoProperty', 'maintenance': 'ZeoMaintenance', 'assets': 'ZeoAssets', 'people': 'ZeoPeople'}
LABEL_KEY = {v: k for k, v in KIT_LABEL.items()}
APP_PREFIX = {'projects': 'prj', 'crm': 'crm', 'property': 'prop', 'maintenance': 'mnt', 'assets': 'am', 'people': 'ppl', 'hse': 'hse'}


def code(app_key, kind):
    return f'{APP_PREFIX[app_key]}_{kind}'


TYPE_LABELS = [(code(a, k), lab) for a, rows in SERIES.items() for k, lab, _ in rows]
COMPANY_WIDE = {code(a, k): (a, k) for a, rows in SERIES.items() for k, _, _ in rows}
TYPE_APP = {**{t: a for a, ts in BRANCH_TYPES.items() for t in ts}, **{t: a for t, (a, _) in COMPANY_WIDE.items()}}


def app_of(doc_type):
    return TYPE_APP.get(doc_type, 'hrms')


def M(lab):
    return djapps.get_model(lab)


def main_branch():
    """The company's head office branch (the first one made) – where the company-wide series live."""
    B = M('OrganisationManager.brnch_mstr')
    return B.objects.order_by('id').first()


def active_types():
    from .services import is_active
    out = []
    for a, ts in BRANCH_TYPES.items():
        if is_active(a):
            out += ts
    for a, rows in SERIES.items():
        if is_active(a):
            out += [code(a, k) for k, _, _ in rows]
    return out


def labels():
    DN = M('OrganisationManager.DocumentNumbering')
    return dict(DN.DOCUMENT_TYPES)


# ------------------------------------------------------------------ the apps' own counters (start of a new series)
def app_state(app_key, kind, default_prefix):
    """(prefix, digits, last number issued) of the app's own counter."""
    try:
        if app_key == 'projects':
            from ZeoProjects.models import ProjectsSettings, Sequence
            s = ProjectsSettings.get()
            prefix = (getattr(s, f'{kind}_prefix', '') or default_prefix).strip()
            seq = Sequence.objects.filter(key=kind).first()
            return prefix, int(s.number_padding or 5), (seq.next_number - 1) if seq else 0
        if app_key == 'hse':
            from ZeoHSE.models import HSESettings, Sequence
            from ZeoHSE.services import PREFIX, DEFAULT_PREFIX
            s = HSESettings.get()
            prefix = getattr(s, PREFIX[kind]) if kind in PREFIX else DEFAULT_PREFIX.get(kind, default_prefix)
            seq = Sequence.objects.filter(kind=kind).first()
            return prefix or default_prefix, 5, seq.last if seq else 0
        from zeo.appkit.engine import REGISTRY
        app = REGISTRY.get(KIT_LABEL[app_key])
        seq = app.sequence_model.objects.filter(kind=kind).first() if app else None
        return (app.prefixes.get(kind, default_prefix) if app else default_prefix), 5, seq.last if seq else 0
    except Exception:
        log.exception('numbering: counter of %s %s', app_key, kind)
        return default_prefix, 5, 0


def _sync_counter(app_key, kind, last):
    """After a number came from the series, the app's own counter never falls behind (switching the series off later
    cannot give a number twice)."""
    try:
        if app_key == 'projects':
            from ZeoProjects.models import Sequence
            seq, _ = Sequence.objects.get_or_create(key=kind)
            if seq.next_number <= last:
                Sequence.objects.filter(pk=seq.pk).update(next_number=last + 1)
            return
        if app_key == 'hse':
            from ZeoHSE.models import Sequence
        else:
            from zeo.appkit.engine import REGISTRY
            Sequence = REGISTRY[KIT_LABEL[app_key]].sequence_model
        seq, _ = Sequence.objects.get_or_create(kind=kind)
        if seq.last < last:
            Sequence.objects.filter(pk=seq.pk).update(last=last)
    except Exception:
        log.exception('numbering: counter sync %s %s', app_key, kind)


def take(app_key, kind, taken=None):
    """The next number from the company-wide series of the app's `kind`, or None (no series row: the app's own counter)."""
    if app_key not in SERIES or kind not in {k for k, _, _ in SERIES[app_key]}:
        return None
    t = code(app_key, kind)
    try:
        DN = M('OrganisationManager.DocumentNumbering')
        br = main_branch()
        row = DN.objects.filter(branch_id=br, type=t).first() if br is not None else None
        if row is None or not row.automatic_numbering:
            return None
        num = DN.next_for(br, t, taken=taken)
        if num:
            last = DN.objects.filter(pk=row.pk).values_list('current_number', flat=True).first() or 0
            _sync_counter(app_key, kind, last)
        return num
    except Exception:
        log.exception('numbering: %s', t)
        return None


# ------------------------------------------------------------------ adding the missing series
def missing(keys=None):
    """[(branch, doc_type, prefix, total_length, current_number)] that `ensure` would add for the apps `keys` (default: active)."""
    from .services import is_active, KEYS
    keys = [k for k in (keys or KEYS) if is_active(k)]
    DN = M('OrganisationManager.DocumentNumbering')
    B = M('OrganisationManager.brnch_mstr')
    have = set(DN.objects.values_list('branch_id_id', 'type'))
    out = []
    branches = list(B.objects.order_by('id'))
    if not branches:
        return out
    try:
        from OrganisationManager.signals import branch_doc_prefix
    except Exception:
        branch_doc_prefix = None
    for a in keys:
        for t in BRANCH_TYPES.get(a, []):
            for b in branches:
                if (b.pk, t) not in have:
                    p = branch_doc_prefix(b, t) if branch_doc_prefix else t[:3].upper()
                    out.append((b, t, p, 12, 0))
        for k, _, dp in SERIES.get(a, []):
            t = code(a, k)
            hq = branches[0]
            if any(bid == hq.pk and tt == t for bid, tt in have) or DN.objects.filter(type=t).exists():
                continue
            prefix, digits, last = app_state(a, k, dp)
            out.append((hq, t, prefix, len(prefix) + 1 + max(digits, len(str(last + 1))), last))
    return out


def ensure(keys=None, apply=True, user=None):
    """Add the missing series of the apps `keys` (default: every active app). Existing rows are never changed. Returns the rows
    added (or that would be added) as dicts."""
    DN = M('OrganisationManager.DocumentNumbering')
    rows = missing(keys)
    out = [{'branch': b.branch_name, 'branch_id': b.pk, 'type': t, 'label': labels().get(t, t), 'app': app_of(t), 'prefix': p,
            'total_length': n, 'current_number': c, 'company_wide': t in COMPANY_WIDE} for b, t, p, n, c in rows]
    if apply and rows:
        with transaction.atomic():
            for b, t, p, n, c in rows:
                DN.objects.get_or_create(branch_id=b, type=t, defaults={'prefix': p, 'suffix': '', 'current_number': c, 'total_length': n,
                                                                         'automatic_numbering': True, 'created_by': user})
    return out


def types_json():
    """The types of the active apps for Settings › Document numbering."""
    from .services import BY_KEY
    lab = labels()
    out = []
    for t in active_types():
        a = app_of(t)
        from .services import tr_
        out.append({'value': t, 'label': tr_(lab.get(t, t)), 'app': a, 'app_title': tr_(BY_KEY[a][2]) if a in BY_KEY else a,
                    'company_wide': t in COMPANY_WIDE})
    return out
