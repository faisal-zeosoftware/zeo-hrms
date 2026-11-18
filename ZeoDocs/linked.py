"""v1.70.8 – the files attached in the other apps, shown in the record's folder of the Document Center.

Never copied: each source reads its own table; who sees a file follows the source app's rights (its view permission, or the
employee's own record where that app lets the employee see it) on top of the record folder's rights. "Copy to Document Center"
makes a managed document (versions, approval, distribution) from it when one is needed.

An item's reference is "<source>:<pk>".
"""
import os
import re

from django.apps import apps
from django.db.models import Q

from Translations.i18n import tr


def _m(label):
    try:
        return apps.get_model(label)
    except (LookupError, ValueError):
        return None


def stored_name(path):
    """the name the file was uploaded with: ZEO's storage adds _<12 random letters> before the extension (zeo/storage.py)"""
    base = os.path.basename(str(path or ''))
    return re.sub(r'_[A-Za-z0-9]{12}(\.[A-Za-z0-9]{1,8})$', r'\1', base)


def _has_file(qs, attr):
    return qs.exclude(**{f'{attr}__isnull': True}).exclude(**{attr: ''})


class Source:
    """provider: the record folder kind; group: the sub-folder shown; base(M): every row of the source (with a file);
    of(M, record_id): the rows of one record; record(row): the record a row belongs to"""

    def __init__(self, key, provider, group, model, of, record, file='file', base=None, codes=None, own=False, label=None, date=None, name=None):
        self.key, self.provider, self.group, self.model_label = key, provider, group, model
        self._of, self._record, self._base = of, record, base
        self.file, self.own, self._label, self._date, self._name = file, own, label, date, name
        self.codes = codes   # None = the model's own view permission

    @property
    def model(self):
        return _m(self.model_label)

    def view_codes(self):
        if self.codes is not None:
            return self.codes
        M = self.model
        return (f'view_{M._meta.model_name}',) if M else ()

    def rows(self, record_id):
        M = self.model
        try:
            return _has_file(self._of(M, record_id), self.file)
        except Exception:   # noqa: BLE001  (an app that is not set up in this company)
            return M.objects.none() if M else []

    def all(self):
        M = self.model
        return _has_file(self._base(M) if self._base else M.objects.all(), self.file)

    def record(self, row):
        try:
            v = self._record(row)
            return int(v) if v not in (None, '') else None
        except Exception:   # noqa: BLE001
            return None

    def fileobj(self, row):
        f = getattr(row, self.file, None)
        return f if f else None

    def name(self, row):
        if self._name:
            return self._name(row) or ''
        f = self.fileobj(row)
        return getattr(row, 'name', '') or (stored_name(f.name) if f else '')

    def item(self, row):
        f = self.fileobj(row)
        try:
            size = f.size if f else 0
        except (OSError, ValueError):
            size = 0
        try:
            frm = self._label(row) if self._label else ''
        except Exception:   # noqa: BLE001
            frm = ''
        d = self._date(row) if self._date else (getattr(row, 'uploaded_at', None) or getattr(row, 'created_at', None))
        return {'ref': f'{self.key}:{row.pk}', 'source': self.key, 'group': tr(self.group), 'name': self.name(row), 'size': size,
                'date': d, 'from': ' '.join(str(frm or '').split())}


# ------------------------------------------------------------------ building blocks
def _by(field):
    return lambda M, rid: M.objects.filter(**{field: rid})


def _chatter(key):
    """files on the record bar of a record (Chatter attachments) – key = '<app>.<model name>'"""
    return dict(of=lambda M, rid: M.objects.filter(model=key, object_id=str(rid)), base=lambda M: M.objects.filter(model=key), record=lambda r: r.object_id)


def _kit(kind):
    """files of an add-on app's record (CRM, Property, HSE …): model = the register's kind"""
    return dict(of=lambda M, rid: M.objects.filter(model=kind, object_id=rid), base=lambda M: M.objects.filter(model=kind), record=lambda r: r.object_id)


def _ids(label, field, rid, out='pk'):
    X = _m(label)
    return list(X.objects.filter(**{field: rid}).values_list(out, flat=True)) if X else []


def _one(label, pk, attr):
    X = _m(label)
    o = X.objects.filter(pk=pk).first() if X else None
    return getattr(o, attr, None) if o else None


def _d(attr):
    return lambda r: getattr(r, attr, None)


def _s(attr):
    return lambda r: str(getattr(r, attr, '') or '')


def _run(r):
    pr = getattr(r, 'payroll_run', None)
    return f'{getattr(pr, "month", "")}-{getattr(pr, "year", "")}' if pr else ''


EMP = lambda r: r.employee_id  # noqa: E731

SOURCES = {s.key: s for s in [
    # ---------------------------------------------------------------- the employee's folder
    Source('emp_files', 'employee', 'Files on the record', 'Chatter.Attachment', codes=('view_emp_master',), label=lambda r: tr('Employee record'),
           **_chatter('EmpManagement.emp_master')),
    Source('leave', 'employee', 'Leave', 'calendars.employee_leave_request', _by('employee_id'), EMP, file='lv_document', own=True,
           label=lambda r: f'{r.document_number or ""} {r.start_date:%d/%m/%Y} – {r.end_date:%d/%m/%Y}', date=_d('applied_on')),
    Source('resignation', 'employee', 'Resignation', 'EmpManagement.EmployeeResignation', _by('employee_id'), EMP, file='attachment', own=True,
           label=_s('document_number'), date=_d('document_date')),
    Source('general_request', 'employee', 'Requests', 'EmpManagement.GeneralRequest', _by('employee_id'), EMP, file='request_document', own=True,
           label=_s('document_number'), date=_d('created_at_date')),
    Source('change_request', 'employee', 'Profile changes', 'SelfService.ChangeRequestAttachment',
           lambda M, rid: M.objects.filter(request_id__in=_ids('SelfService.ProfileChangeRequest', 'employee_id', rid)),
           lambda r: _one('SelfService.ProfileChangeRequest', r.request_id, 'employee_id'), codes=('view_profilechangerequest',), own=True,
           label=lambda r: tr('Change request {0}', r.request_id)),
    Source('letter', 'employee', 'Letters', 'SelfService.LetterRequest', _by('employee_id'), EMP, file='pdf', own=True,
           name=lambda r: f'{r.number or r.letter_type}.pdf', label=lambda r: f'{r.number or ""} {r.letter_type}', date=_d('created_at')),
    Source('qualification', 'employee', 'Qualifications', 'EmployeeProfile.QualificationExtra', _by('employee_id'), EMP, file='attachment',
           codes=('view_emp_qualification', 'view_emp_master'), own=True, label=lambda r: tr('Attested') if r.attested else tr('Certificate'), date=_d('updated_at')),
    Source('expense', 'employee', 'Expense receipts', 'ExpenseManagement.Expense', _by('employee_id'), EMP, file='receipt', own=True,
           label=lambda r: f'{r.date:%d/%m/%Y} {r.merchant or ""} {r.amount} {r.currency or ""}', date=_d('date')),
    Source('handover', 'employee', 'Asset handovers', 'ZeoHR.AssetHandover', _by('employee_id'), EMP, file='signed_document', own=True,
           label=_s('number'), date=_d('date')),
    Source('allocation', 'employee', 'Asset handovers', 'AssetPlus.AllocationExtra', _by('employee_id'), EMP, file='handover_file',
           codes=('view_assetallocation', 'view_asset'), own=True, label=lambda r: tr('Asset {0}', r.asset_id)),
    Source('payslip', 'employee', 'Payslips', 'PayrollManagement.Payslip', lambda M, rid: M.objects.filter(employee_id=rid).select_related('payroll_run'), EMP,
           file='payslip_pdf', codes=('view_payslip', 'view_payrollrun'), own=True, name=lambda r: f'Payslip {_run(r)}.pdf', label=_run, date=_d('created_at')),
    Source('grievance', 'employee', 'Grievances', 'SelfService.GrievanceMessage',
           lambda M, rid: M.objects.filter(grievance_id__in=_ids('SelfService.Grievance', 'employee_id', rid)),
           lambda r: _one('SelfService.Grievance', r.grievance_id, 'employee_id'), file='attachment', codes=('handle_grievance',),
           label=lambda r: tr('Grievance {0}', r.grievance_id)),
    # ---------------------------------------------------------------- the project's folder
    Source('project_doc', 'project', 'Project', 'ProjectManagement.Project', _by('pk'), lambda r: r.pk, file='document', codes=('view_project',),
           label=lambda r: r.title, date=_d('start_date')),
    Source('project_files', 'project', 'Files on the record', 'Chatter.Attachment', codes=('view_project',), label=lambda r: tr('Project record'),
           **_chatter('ProjectManagement.project')),
    Source('task_doc', 'project', 'Tasks', 'ProjectManagement.Task', _by('project_id'), lambda r: r.project_id, file='document', codes=('view_task', 'view_project'),
           label=lambda r: r.title, date=_d('start_date')),
    Source('task_file', 'project', 'Tasks', 'ZeoProjects.TaskFile', lambda M, rid: M.objects.filter(task_id__in=_ids('ProjectManagement.Task', 'project_id', rid)),
           lambda r: _one('ProjectManagement.Task', r.task_id, 'project_id'), codes=('view_task', 'view_project'),
           label=lambda r: _one('ProjectManagement.Task', r.task_id, 'title') or tr('Task {0}', r.task_id)),
    Source('ticket_file', 'project', 'Tickets', 'ZeoProjects.TicketFile', lambda M, rid: M.objects.filter(ticket__project_id=rid).select_related('ticket'),
           lambda r: r.ticket.project_id, codes=('view_ticket',), label=lambda r: getattr(r.ticket, 'number', '') or tr('Ticket {0}', r.ticket_id)),
    # ---------------------------------------------------------------- business partners and the add-on apps
    Source('partner_files', 'partner', 'Files on the record', 'Chatter.Attachment', codes=('view_partner',), label=lambda r: tr('Partner record'),
           **_chatter('ZeoMasters.partner')),
    Source('equipment_file', 'partner', 'Equipment', 'ZeoProjects.EquipmentFile',
           lambda M, rid: M.objects.filter(equipment__customer__partner_id=rid).select_related('equipment'), lambda r: r.equipment.customer.partner_id,
           codes=('view_customerequipment',), label=lambda r: str(getattr(r.equipment, 'serial_no', '') or r.equipment)),
    Source('building_file', 'building', 'Building', 'ZeoProperty.PropertyFile', codes=('view_building',), label=_s('note'), **_kit('building')),
    Source('asset_files', 'asset', 'Files on the record', 'Chatter.Attachment', codes=('view_assetcard',), label=lambda r: tr('Asset record'),
           **_chatter('ZeoAssets.assetcard')),
    Source('hse_site_file', 'hse_site', 'Site', 'ZeoHSE.HSEFile', codes=('view_site',), label=_s('note'), **_kit('site')),
    Source('crm_file', 'crm_account', 'Account', 'ZeoCRM.CRMFile', codes=('view_contact',), label=_s('note'), **_kit('contact')),
]}


# ------------------------------------------------------------------ rights
def may_see(src, who, record_id):
    """the source app's rights: its view permission (administrators: all), or the employee's own record where the app shows it to them"""
    if who.admin:
        return True
    if src.codes == ('handle_grievance',):   # complaints: only the people who handle them
        return 'handle_grievance' in who.codes
    if any(c in who.codes for c in src.view_codes()):
        return True
    return bool(src.own and src.provider == 'employee' and who.emp_id and record_id == who.emp_id)


def _folder_ok(folder, who, src, record_id):
    from . import services as S
    if folder is None:
        return who.dms_admin
    own_emp = src.provider == 'employee' and src.own and who.emp_id == record_id
    return own_emp or 'view' in S.folder_rights(folder, who)


def for_folder(folder, who):
    """the linked files of a record folder, grouped by where they come from (the folder's own rights are checked by the caller)"""
    if folder.kind != 'record' or not folder.provider or folder.record_id is None:
        return []
    groups = {}
    for src in SOURCES.values():
        if src.provider != folder.provider or src.model is None or not may_see(src, who, folder.record_id):
            continue
        for row in src.rows(folder.record_id)[:500]:
            it = src.item(row)
            groups.setdefault(it['group'], []).append(it)
    out = []
    for g, items in groups.items():
        items.sort(key=lambda x: str(x['date'] or ''), reverse=True)
        out.append({'group': g, 'items': items})
    out.sort(key=lambda g: g['group'])
    return out


def resolve(ref, who):
    """(source, row, record folder) for "<source>:<pk>" when the user may open it; raises LookupError / services.Refused"""
    from . import services as S
    from .models import DocFolder
    try:
        key, pk = str(ref).split(':', 1)
        src = SOURCES[key]
        row = src.all().filter(pk=int(pk)).first()
    except (ValueError, KeyError, AttributeError, TypeError):
        raise LookupError(ref)
    if row is None:
        raise LookupError(ref)
    rid = src.record(row)
    if rid is None:
        raise LookupError(ref)
    folder = DocFolder.objects.filter(provider=src.provider, record_id=rid, kind='record', deleted_at__isnull=True).first()
    if not _folder_ok(folder, who, src, rid) or not may_see(src, who, rid):
        raise S.Refused(tr('You do not have access to this file.'), 403)
    return src, row, folder


def search(who, q, limit=200):
    """linked files whose file name, number, note or source matches the words – in the record folders the user may see"""
    from . import services as S
    from .models import DocFolder
    q = ' '.join(str(q or '').split())
    if len(q) < 2:
        return []
    out, folders = [], {}
    for src in SOURCES.values():
        M = src.model
        if M is None:
            continue
        names = {f.name for f in M._meta.fields}
        cond = Q(**{f'{src.file}__icontains': q})
        for fld in ('name', 'number', 'document_number', 'title', 'note'):
            if fld in names:
                cond |= Q(**{f'{fld}__icontains': q})
        group_hit = q.lower() in tr(src.group).lower() or q.lower() in src.group.lower()
        try:
            rows = list((src.all() if group_hit else src.all().filter(cond))[:300])
        except Exception:   # noqa: BLE001
            continue
        for row in rows:
            rid = src.record(row)
            if rid is None or not may_see(src, who, rid):
                continue
            fk = (src.provider, rid)
            if fk not in folders:
                folders[fk] = DocFolder.objects.filter(provider=src.provider, record_id=rid, kind='record', deleted_at__isnull=True).first()
            f = folders[fk]
            if f is None or not _folder_ok(f, who, src, rid):
                continue
            it = src.item(row)
            it.update({'folder': f.pk, 'folder_path': S.path_of(f)})
            out.append(it)
            if len(out) >= limit:
                return out
    return out
