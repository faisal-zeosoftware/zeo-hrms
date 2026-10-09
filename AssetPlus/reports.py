"""
Asset reports for the report centre (DashboardManagement/reports.py conventions, v1.12.0):
`def r_x(run): return columns, rows`; rows are limited to the branches the user may see (asset branch, else
the holder's branch) and carry src(asset) for the drill-down.
"""
from decimal import Decimal

from . import services as S
from .models import AllocationExtra, AssetDamage, AssetDisposal, AssetLoss, AssetProfile, MaintenanceRecord, MaintenanceSchedule, RecoveryInstalment


def _h():
    from DashboardManagement import reports as R
    return R


def _assets(run):
    """[(asset, profile, holder allocation, shared)] the user may see."""
    A = S.Asset()
    assets = list(A.objects.select_related('asset_type').order_by('name', 'id'))
    S.ensure_profiles(assets)
    prof = {p.asset_id: p for p in AssetProfile.objects.all()}
    emp = {a.asset_id: a for a in S.Allocation().objects.filter(returned_date__isnull=True).select_related(
        'employee__emp_branch_id', 'employee__emp_dept_id', 'employee__emp_desgntn_id', 'employee__emp_ctgry_id')}
    from .models import SharedAllocation
    sh = {s.asset_id: s for s in SharedAllocation.objects.filter(returned_date__isnull=True)}
    out = []
    for a in assets:
        p, h, s = prof.get(a.pk), emp.get(a.pk), sh.get(a.pk)
        b = (p.branch_id if p else None) or (h.employee.emp_branch_id_id if h else (s.branch_id if s else None))
        if run.branches is not None and b is not None and b not in run.branches:
            continue
        out.append((a, p, h, s, b))
    return out


def _branch_names():
    from django.apps import apps
    return dict(apps.get_model('OrganisationManager', 'brnch_mstr').objects.values_list('id', 'branch_name'))


def r_asset_register(run):
    R = _h()
    names = _branch_names()
    # v1.12.0: the report centre fills an open end date with 31-12-2999 for reports without a period – book value is as of today then
    on = run.date_to if run.date_to and run.date_to.year < 2999 else run.today
    rows = []
    for a, p, h, s, b in _assets(run):
        st = S.effective_status(a, p)
        cost = S.money(p.purchase_cost)
        bv = Decimal(0) if a.status == 'disposed' else S.book_value(p, a, on)
        rows.append({'code': p.asset_code, 'name': a.name, 'type': a.asset_type.name, 'serial': a.serial_number, 'branch': names.get(b, ''),
                     'location': p.location, 'status': S.STATUS_LABEL.get(st, st), 'condition': R.nice(a.condition),
                     'held_by': R.full_name(h.employee) if h else (S.holder_label_shared(s) if s else ''),
                     'purchased': a.purchase_date, 'vendor': p.vendor, 'cost': R.money(cost), 'method': p.get_depreciation_method_display(),
                     'life': str(p.useful_life_months) if p.depreciation_method != 'none' else '', 'accumulated': R.money(cost - bv if a.status != 'disposed' else cost),
                     'book_value': R.money(bv), 'warranty_end': p.warranty_end or '', **R.src(a), '_emp': h.employee_id if h else None})
    return [('code', 'Asset code', 'text'), ('name', 'Asset', 'text'), ('type', 'Type', 'text'), ('serial', 'Serial number', 'text'),
            ('branch', 'Branch', 'text'), ('location', 'Location', 'text'), ('status', 'Status', 'text'), ('condition', 'Condition', 'text'),
            ('held_by', 'Held by', 'text'), ('purchased', 'Purchase date', 'date'), ('vendor', 'Vendor', 'text'), ('cost', 'Cost', 'number'),
            ('method', 'Depreciation', 'text'), ('life', 'Life (months)', 'text'),   # v1.12.0: text so the report does not total the lives
            ('accumulated', 'Accumulated depreciation', 'number'),
            ('book_value', 'Book value', 'number'), ('warranty_end', 'Warranty ends', 'date')], rows


def r_maintenance_due(run):
    R = _h()
    vis = {a.pk: (a, p) for a, p, h, s, b in _assets(run)}
    rows = []
    for sc in MaintenanceSchedule.objects.filter(active=True, asset_id__in=vis.keys()):
        a, p = vis[sc.asset_id]
        due = S.schedule_due(sc, p, run.today)
        if run.date_to and sc.next_due_date and sc.next_due_date > run.date_to and not due:
            continue
        rows.append({'code': p.asset_code, 'asset': a.name, 'plan': sc.title, 'every': f'{sc.interval_value} {sc.get_interval_type_display().lower()}',
                     'last_done': sc.last_done_date or '', 'next_due': sc.next_due_date or '',
                     'next_reading': float(sc.next_due_reading) if sc.next_due_reading is not None else '',
                     'reading_now': float(p.meter_reading) if p.meter_reading is not None else '',
                     'state': {'overdue': 'Overdue', 'due': 'Due soon'}.get(due, 'Planned'), 'vendor': sc.vendor,
                     'estimate': R.money(sc.estimated_cost), **R.src(a)})
    for m in MaintenanceRecord.objects.filter(status='open', asset_id__in=vis.keys()):
        a, p = vis[m.asset_id]
        rows.append({'code': p.asset_code, 'asset': a.name, 'plan': f'{m.number} – {m.description[:60]}', 'every': '', 'last_done': '',
                     'next_due': m.start_date, 'next_reading': '', 'reading_now': '', 'state': 'In maintenance', 'vendor': m.vendor,
                     'estimate': R.money(m.cost), **R.src(a)})
    order = {'Overdue': 0, 'In maintenance': 1, 'Due soon': 2, 'Planned': 3}
    rows.sort(key=lambda r: (order.get(r['state'], 9), str(r['next_due'])))
    return [('code', 'Asset code', 'text'), ('asset', 'Asset', 'text'), ('plan', 'Plan / work', 'text'), ('every', 'Every', 'text'),
            ('last_done', 'Last done', 'date'), ('next_due', 'Next due', 'date'), ('next_reading', 'Due at reading', 'number'),
            ('reading_now', 'Reading now', 'number'), ('state', 'State', 'text'), ('vendor', 'Vendor', 'text'), ('estimate', 'Estimated cost', 'number')], rows


def r_assets_by_employee(run):
    R = _h()
    rows = []
    qs = S.Allocation().objects.filter(run.emp_q(), returned_date__isnull=True).select_related(
        'asset__asset_type', 'employee__emp_branch_id', 'employee__emp_dept_id', 'employee__emp_desgntn_id', 'employee__emp_ctgry_id')
    extras = {x.allocation_id: x for x in AllocationExtra.objects.filter(allocation_id__in=qs.values('id'))}
    prof = {p.asset_id: p for p in AssetProfile.objects.filter(asset_id__in=qs.values('asset_id'))}
    for a in qs:
        x, p = extras.get(a.pk), prof.get(a.asset_id)
        overdue = bool(x and x.expected_return_date and x.expected_return_date < run.today)
        rows.append({**R.emp_cols(a.employee), 'code': p.asset_code if p else '', 'asset': a.asset.name, 'type': a.asset.asset_type.name,
                     'serial': a.asset.serial_number, 'since': a.assigned_date, 'expected': x.expected_return_date if x and x.expected_return_date else '',
                     'overdue': 'Yes' if overdue else 'No', 'ack': x.get_ack_status_display() if x else 'Not required',
                     'book_value': R.money(S.book_value(p, a.asset)) if p else 0, **R.src(a), '_cell': {'asset': R.src(a.asset)}})
    rows.sort(key=lambda r: (r['employee'], r['asset']))
    return R.EMP_COLS + [('code', 'Asset code', 'text'), ('asset', 'Asset', 'text'), ('type', 'Type', 'text'), ('serial', 'Serial number', 'text'),
                         ('since', 'Since', 'date'), ('expected', 'Expected back', 'date'), ('overdue', 'Overdue', 'text'),
                         ('ack', 'Receipt confirmed', 'text'), ('book_value', 'Book value', 'number')], rows


def r_asset_recovery(run):
    R = _h()
    E = S.Emp()
    vis = set(run.all_employees.values_list('id', flat=True))
    names = dict(S.Asset().objects.values_list('id', 'name'))
    rows = []
    for model, kind in ((AssetDamage, 'Damage'), (AssetLoss, 'Loss')):
        date_f = 'damage_date' if model is AssetDamage else 'loss_date'
        for o in model.objects.exclude(status='rejected').filter(run.in_period(date_f)):
            if o.employee_id and o.employee_id not in vis:
                continue
            if not o.employee_id and run.branches is not None:
                continue
            e = E.objects.filter(pk=o.employee_id).select_related('emp_branch_id', 'emp_dept_id', 'emp_desgntn_id', 'emp_ctgry_id').first()
            inst = RecoveryInstalment.objects.filter(source_type=kind.lower(), source_id=o.pk)
            pending = sum((i.amount for i in inst if i.status == 'pending'), Decimal(0))
            rows.append({**R.emp_cols(e), 'number': o.number, 'kind': kind, 'asset': names.get(o.asset_id, ''), 'date': getattr(o, date_f),
                         'cost': R.money(o.repair_cost if model is AssetDamage else o.book_value), 'responsibility': o.get_responsibility_display(),
                         'method': o.get_recovery_method_display(), 'to_recover': R.money(o.recovery_amount), 'recovered': R.money(o.recovered_amount),
                         'outstanding': R.money(pending), 'instalments_left': sum(1 for i in inst if i.status == 'pending'),
                         'status': o.get_status_display(), '_cell': {'asset': {'_m': 'OrganisationManager.Asset', '_id': o.asset_id}}})
    rows.sort(key=lambda r: str(r['date']))
    return R.EMP_COLS + [('number', 'Record', 'text'), ('kind', 'Type', 'text'), ('asset', 'Asset', 'text'), ('date', 'Date', 'date'),
                         ('cost', 'Repair cost / book value', 'number'), ('responsibility', 'Responsibility', 'text'), ('method', 'Recovery', 'text'),
                         ('to_recover', 'To recover', 'number'), ('recovered', 'Recovered', 'number'), ('outstanding', 'Outstanding', 'number'),
                         ('instalments_left', 'Instalments left', 'number'), ('status', 'Status', 'text')], rows


def r_asset_disposals(run):
    R = _h()
    vis = {a.pk: (a, p) for a, p, h, s, b in _assets(run)}
    rows = []
    for d in AssetDisposal.objects.filter(asset_id__in=vis.keys()).exclude(status='cancelled').filter(run.in_period('disposal_date')):
        a, p = vis[d.asset_id]
        rows.append({'number': d.number, 'code': p.asset_code, 'asset': a.name, 'type': a.asset_type.name, 'method': d.get_method_display(),
                     'date': d.disposal_date, 'buyer': d.buyer, 'cost': R.money(p.purchase_cost), 'book_value': R.money(d.book_value),
                     'value': R.money(d.value), 'gain_loss': R.money(d.gain_loss),
                     'result': 'Gain' if d.gain_loss > 0 else ('Loss' if d.gain_loss < 0 else '–'), 'status': d.get_status_display(), **R.src(a)})
    return [('number', 'Disposal', 'text'), ('code', 'Asset code', 'text'), ('asset', 'Asset', 'text'), ('type', 'Type', 'text'),
            ('method', 'Method', 'text'), ('date', 'Date', 'date'), ('buyer', 'Buyer', 'text'), ('cost', 'Cost', 'number'),
            ('book_value', 'Book value', 'number'), ('value', 'Sale / scrap value', 'number'), ('gain_loss', 'Gain / loss', 'number'),
            ('result', 'Result', 'text'), ('status', 'Status', 'text')], rows


# key: (title, section, description, function, default period, rights) – same shape as DashboardManagement.reports.REPORTS
REPORTS = {
    'asset-register': ('Asset register (book value)', 'Requests & exits', 'Every asset with code, branch, status, holder, cost, accumulated depreciation and book value on the period end date.',
                       r_asset_register, None, ['view_asset', 'view_assetreport']),
    'asset-maintenance-due': ('Maintenance due', 'Requests & exits', 'Maintenance plans overdue, due soon or planned in the period, and assets now in maintenance.',
                              r_maintenance_due, 'next90', ['view_asset', 'view_maintenancerecord']),
    'assets-by-employee': ('Assets by employee', 'Requests & exits', 'Assets each employee holds now, with expected return date, overdue flag and receipt confirmation.',
                           r_assets_by_employee, None, ['view_assetallocation']),
    'asset-recovery': ('Damage and loss recovery', 'Requests & exits', 'Damage and loss records with responsibility, amount to recover, recovered by payroll and outstanding.',
                       r_asset_recovery, 'year', ['view_assetdamage', 'view_assetloss', 'view_asset']),
    'asset-disposals': ('Disposals – gain / loss', 'Requests & exits', 'Assets sold, scrapped, donated or written off with book value, value received and gain or loss.',
                        r_asset_disposals, 'year', ['view_asset', 'view_assetdisposal']),
}
REPORT_MODELS = {
    'asset-register': {'OrganisationManager.Asset'}, 'asset-maintenance-due': {'OrganisationManager.Asset'},
    'assets-by-employee': {'OrganisationManager.AssetAllocation', 'OrganisationManager.Asset', 'EmpManagement.emp_master'},
    'asset-recovery': {'OrganisationManager.Asset', 'EmpManagement.emp_master'}, 'asset-disposals': {'OrganisationManager.Asset'},
}
