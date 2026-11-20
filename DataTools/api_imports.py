"""v1.67.17: Excel template + import for screens whose list is not a DRF ViewSet (Projects app: customers, contacts, sites,
equipment, AMC contracts, projects, tickets; Asset Management stores and vans).

Before, the Import menu of these lists said "This list cannot be imported". Each row is sent to the screen's own create
(POST of the list endpoint – same rights, rules and numbering as the New form); readable values (customer name, employee code,
category code …) are turned into ids first. Check = the same save in a transaction that is always rolled back.
"""
import re
from datetime import date, datetime

from django.apps import apps
from django.db import transaction
from django.db.models import Q


def _tr(s, *a):   # v1.67.24: import row messages in the user's language
    try:
        from Translations.i18n import tr
        return tr(s, *a)
    except Exception:   # noqa: BLE001
        return s.format(*a) if a else s

EMP = ('EmpManagement', 'emp_master')


def F(key, label, type_='text', required=False, choices=None, link=None, many=False, scope=None, match=None, help_=''):
    d = {'key': key, 'label': label, 'type': type_, 'required': required}
    if choices:
        d['type'] = 'choice'
        d['choices'] = [{'value': v, 'label': lb} for v, lb in choices]
    if link:
        d['type'] = 'link'
        d['link'] = label
        d['_link'] = link
        d['_scope'] = scope
        d['_match'] = match
        d['many'] = many
        d['match_by'] = list(match or ('code', 'name'))
    if help_:
        d['help'] = help_
    return d


def _choices(name):
    try:
        from ZeoProjects import models as PM
        return getattr(PM, name)
    except Exception:   # noqa: BLE001
        return ()


def _asset_choices(name):
    try:
        from ZeoAssets import models as AM
        return getattr(AM, name)
    except Exception:   # noqa: BLE001
        return ()


def _resignation_types():
    try:
        return tuple(apps.get_model('EmpManagement', 'EmployeeResignation').TERMINATION_TYPE_CHOICES)
    except Exception:   # noqa: BLE001
        return (('resignation', 'Resignation'), ('termination', 'Termination'))


def _specs():
    from ZeoProjects.models import Contact
    cust = ('ZeoProjects', 'Customer')
    return {
        '/zeo-projects/api/customers/': [
            F('code', 'Code'), F('name', 'Name', required=True), F('trn', 'TRN'), F('email', 'E-mail'), F('phone', 'Phone'),
            F('address', 'Address'), F('city', 'City'), F('country', 'Country'), F('payment_days', 'Payment days', 'number'),
            F('account_manager', 'Account manager', link=EMP, match=('emp_code',)), F('notes', 'Notes')],
        '/zeo-projects/api/contacts/': [
            F('customer', 'Customer', required=True, link=cust), F('name', 'Name', required=True), F('email', 'E-mail'),
            F('phone', 'Phone'), F('designation', 'Designation'), F('is_primary', 'Primary contact', 'yes/no'),
            F('portal_role', 'Portal role', choices=Contact.PORTAL_ROLES)],
        '/zeo-projects/api/equipment-categories/': [
            F('code', 'Code', required=True), F('name', 'Name', required=True),
            F('pm_months', 'Preventive maintenance every (months)', 'number'),
            F('checklist', 'Service checklist', help_='One item per line (Alt+Enter in Excel); empty = keep the checklist as it is.')],   # v1.67.23 (271)
        '/zeo-projects/api/sites/': [
            F('customer', 'Customer', required=True, link=cust), F('code', 'Code'), F('name', 'Name', required=True),
            F('site_type', 'Site type', choices=_choices('SITE_TYPES')), F('address', 'Address'), F('area', 'Area'),
            F('city', 'City'), F('emirate', 'Emirate'), F('contact_name', 'Contact name'), F('contact_phone', 'Contact phone'),
            F('working_hours', 'Working hours')],
        '/zeo-projects/api/equipment/': [
            F('customer', 'Customer', required=True, link=cust, help_='Only to find the site of the customer.'),
            F('site', 'Site', required=True, link=('ZeoProjects', 'CustomerSite'), scope='customer'),
            F('category', 'Category', required=True, link=('ZeoProjects', 'EquipmentCategory')),
            F('name', 'Name', required=True), F('make', 'Make'), F('model_no', 'Model'), F('serial_no', 'Serial no.'),
            F('location_in_site', 'Location in the site'), F('install_date', 'Installed on', 'date'),
            F('warranty_end', 'Warranty until', 'date'), F('status', 'Status', choices=_choices('EQUIPMENT_STATUS'))],
        '/zeo-projects/api/contracts/': [
            F('customer', 'Customer', required=True, link=cust), F('title', 'Title', required=True),
            F('amc_type', 'AMC type', link=('ZeoProjects', 'AMCType')),
            F('contract_type', 'Contract type', choices=_choices('CONTRACT_TYPES')),
            F('start_date', 'Start date', 'date', required=True), F('end_date', 'End date', 'date', required=True),
            F('value', 'Contract value', 'number'), F('billing_frequency', 'Billing', choices=_choices('BILLING_FREQ')),
            F('included_hours', 'Included hours', 'number'), F('hourly_rate_outside', 'Hourly rate outside the AMC', 'number'),
            F('hours_period', 'Included hours are for', choices=[('term', 'Whole contract term'), ('month', 'Each month')],
              help_='Each month = a monthly allowance; the hours over it are billed per month at the hourly rate.'),   # v1.67.23 (270)
            F('parts_limit', 'Parts limit (value)', 'number'),
            F('account_manager', 'Account manager', link=EMP, match=('emp_code',)), F('notes', 'Notes'),
            F('activate', 'Activate now', 'yes/no', help_='Yes = active with its billing lines (as "Activate" on the contract).')],
        '/zeo-projects/api/projects/': [
            F('title', 'Title', required=True), F('customer', 'Customer', link=cust),
            F('project_type', 'Project type', choices=_choices('PROJECT_TYPES')),
            F('billing_method', 'Billing method', choices=_choices('BILLING_METHODS')),
            F('start_date', 'Start date', 'date', required=True), F('end_date', 'End date', 'date'),
            F('managers', 'Managers', link=EMP, many=True, match=('emp_code',), help_='Employee codes, separated by commas.'),
            F('members', 'Members', link=EMP, many=True, match=('emp_code',), help_='Employee codes, separated by commas.'),
            F('description', 'Description')],
        '/zeo-projects/api/tickets/': [
            F('subject', 'Subject', required=True), F('description', 'Description'), F('customer', 'Customer', link=cust),
            F('contact', 'Contact', link=('ZeoProjects', 'Contact'), scope='customer', match=('name', 'email')),
            F('contract', 'AMC contract', link=('ZeoProjects', 'AMCContract'), scope='customer', match=('number', 'title')),
            F('site', 'Site', link=('ZeoProjects', 'CustomerSite'), scope='customer'),
            F('equipment', 'Equipment', link=('ZeoProjects', 'CustomerEquipment'), scope='customer', match=('number', 'serial_no', 'name')),
            F('priority', 'Priority', choices=_choices('PRIORITIES')), F('channel', 'Channel', choices=_choices('CHANNELS')),
            F('ticket_kind', 'Kind', choices=(('request', 'Request'), ('incident', 'Incident'), ('visit', 'Visit'))),
            F('assigned_to', 'Assigned to', link=EMP, match=('emp_code',))],
        # HR › Employees › Exit › Resignations (its own view: numbering, approval chain, e-mails)
        '/hr-actions/api/resignations/': [
            F('employee', 'Employee', required=True, link=EMP, match=('emp_code',)),
            F('document_date', 'Document date', 'date'), F('resigned_on', 'Resigned on', 'date', required=True),
            F('notice_period', 'Notice period (days)', 'number'), F('last_working_date', 'Last working day', 'date'),
            F('termination_type', 'Type', choices=_resignation_types()),
            F('reason_for_leaving', 'Reason for leaving')],
        # Asset Management (app kit registers): stores and vans; stock receipts / transfers
        '/zeo-assets/api/r/location/': [
            F('code', 'Code'), F('name', 'Name', required=True), F('kind', 'Kind', choices=_asset_choices('LOCATION_KINDS')),
            F('custodian', 'Person in charge', link=EMP, match=('emp_code',)), F('address', 'Address')],
        '/zeo-assets/api/r/stock_move/': [
            F('date', 'Date', 'date', required=True), F('kind', 'Kind', required=True, choices=_asset_choices('MOVE')),
            F('part', 'Part', required=True, link=('ZeoAssets', 'Part')), F('qty', 'Quantity', 'number', required=True),
            F('from_location', 'From store', link=('ZeoAssets', 'Location')), F('to_location', 'To store', link=('ZeoAssets', 'Location')),
            F('unit_cost', 'Unit cost', 'number'), F('reference', 'Supplier invoice / reference'), F('note', 'Note')],
    }


def spec(path):
    try:
        out = _specs().get(path)
    except Exception:   # noqa: BLE001 – Projects app not installed
        out = None
    if out is None:
        out = kit_spec(path)
    return out


_KIT = re.compile(r'^/([a-z0-9-]+)/api/r/([a-z0-9_]+)/$')
# how a record of another list is found from the readable value of the file (first match wins; the id works too)
_MATCH = ('code', 'number', 'sku', 'barcode', 'emp_code', 'name', 'title', 'email')


def kit_spec(path):
    """v1.79.1 every list of the app-kit apps (Inventory, Accounting, Sales, Purchase, Manufacturing, CRM, Property, Maintenance,
    Assets, HSE, People, HR …) can be imported: the columns are the fields of the list's New form – the same rules, numbering
    and rights as the form, each row through the list's own create. A link to another record (item, customer, warehouse,
    account …) is given by its code, number or name; choice fields by their value or label; tables (lines) are not in the file."""
    m = _KIT.match(path or '')
    if not m:
        return None
    try:
        from zeo.appkit.engine import REGISTRY, fmeta
    except Exception:   # noqa: BLE001
        return None
    app = next((a for a in REGISTRY.values() if a.slug == m.group(1)), None)
    r = app.regs.get(m.group(2)) if app else None
    if r is None or r.create == 'none':
        return None
    out = []
    for name in r.form:
        try:
            if r.link_off(name) or name in (r.readonly or ()) or name in (r.links or {}):
                continue
            meta = fmeta(r, name)
        except Exception:   # noqa: BLE001 – a form entry that is not a field (section title)
            continue
        t, label, req = meta.get('type'), meta.get('label') or name, bool(meta.get('required'))
        if t in ('json', 'file', 'image'):
            continue
        if t in ('fk', 'm2m'):
            try:
                from zeo.appkit.engine import field_of
                rm = field_of(r.model, name).related_model
            except Exception:   # noqa: BLE001
                continue
            if rm is None:
                continue
            names = {f.name for f in rm._meta.get_fields()}
            match = tuple(k for k in _MATCH if k in names) or ('name',)
            out.append(F(name, label, required=req, link=(rm._meta.app_label, rm.__name__), many=t == 'm2m', match=match,
                         help_=meta.get('help') or ''))
        elif t == 'choice':
            out.append(F(name, label, required=req, choices=[(c['value'], c['label']) for c in meta.get('choices') or []], help_=meta.get('help') or ''))
        elif t in ('bool', 'bool3'):
            out.append(F(name, label, 'yes/no', help_=meta.get('help') or ''))
        elif t in ('int', 'decimal'):
            out.append(F(name, label, 'number', required=req, help_=meta.get('help') or ''))
        elif t in ('date', 'datetime'):
            out.append(F(name, label, 'date', required=req, help_=meta.get('help') or ''))
        else:
            out.append(F(name, label, required=req, help_=meta.get('help') or ''))
    return out or None


def public_fields(path):
    return [{k: v for k, v in f.items() if not k.startswith('_')} for f in spec(path) or []]


def _date(v):
    if isinstance(v, (datetime, date)):
        return v.strftime('%Y-%m-%d')
    s = str(v).strip()
    m = re.fullmatch(r'(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})', s)
    if m:
        return f'{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}'
    return s[:10]


def _find(model, raw, match, scope_q):
    s = str(raw).strip()
    q = Q(pk=int(s)) if s.isdigit() else Q()
    names = {f.name for f in model._meta.get_fields()}
    for k in (match or ('code', 'name')):
        if k in names:
            q |= Q(**{f'{k}__iexact': s})
            if k == 'title':   # "AMC-2601" finds "AMC-2601 ERP AMC (Odoo) …"
                q |= Q(title__istartswith=s + ' ')
    if model._meta.model_name == 'emp_master':
        p = s.split()
        if len(p) >= 2:
            q |= Q(emp_first_name__iexact=p[0], emp_last_name__iexact=' '.join(p[1:]))
    if not q:
        return []
    qs = model._default_manager.filter(q)
    if scope_q:
        qs = qs.filter(**scope_q)
    return list(qs.values_list('pk', flat=True)[:3])


def convert(path, row):
    """row {key: readable value} -> (data for the screen's create, problems)"""
    data, problems = {}, []
    fields = spec(path)
    for f in fields:
        v = row.get(f['key'])
        if v in (None, ''):
            if f['required']:
                problems.append(_tr('{0}: this field is required', _tr(f['label'])))
            continue
        t = f['type']
        if t == 'yes/no':
            data[f['key']] = str(v).strip().lower() in ('yes', 'y', 'true', '1')
        elif t == 'number':
            try:
                data[f['key']] = float(str(v).replace(',', ''))
            except ValueError:
                problems.append(_tr('{0}: “{1}” is not a number', _tr(f['label']), v))
        elif t == 'date':
            data[f['key']] = _date(v)
        elif t == 'choice':
            s = str(v).strip().lower()
            hit = next((c['value'] for c in f['choices'] if s in (str(c['value']).lower(), str(c['label']).lower())), None)
            if hit is None:
                problems.append(_tr('{0}: “{1}” – use one of {2}', _tr(f['label']), v, ', '.join(_tr(c['label']) for c in f['choices'])))
            else:
                data[f['key']] = hit
        elif t == 'link':
            model = apps.get_model(*f['_link'])
            scope_q = None
            if f.get('_scope'):
                if data.get(f['_scope']) is None:
                    problems.append(_tr('{0}: give the {1} too', _tr(f['label']), _tr(f['_scope'])))
                    continue
                scope_q = {f"{f['_scope']}_id": data[f['_scope']]}
            vals = [x.strip() for x in str(v).split(',')] if f.get('many') else [v]
            ids = []
            for x in vals:
                if not str(x).strip():
                    continue
                hits = _find(model, x, f.get('_match'), scope_q)
                if len(hits) == 1:
                    ids.append(hits[0])
                elif hits:
                    problems.append(_tr('{0}: “{1}” matches several records – use the code', _tr(f['label']), x))
                else:
                    problems.append(_tr('{0}: “{1}” not found', _tr(f['label']), x))
            if ids:
                data[f['key']] = ids if f.get('many') else ids[0]
        else:
            data[f['key']] = str(v).strip()
    return data, problems


def run(request, path, rows, dry, schema, factory):
    """Each row through the screen's own create (POST of the list endpoint)."""
    from django.urls import resolve
    from rest_framework.test import force_authenticate
    from .views import _sub_request, _flatten, _Rollback
    match = resolve(path)
    func = match.func
    results, ok = [], 0
    try:
        from zeo import mailguard
        guard = mailguard.suppressed
    except Exception:   # noqa: BLE001
        import contextlib
        guard = contextlib.nullcontext
    try:   # v1.73.4 a large file comes in parts: the rows keep their numbers in the file
        start = 1 + max(0, int((request.data or {}).get('row_offset') or 0))
    except Exception:   # noqa: BLE001
        start = 1
    from .import_jobs import row_done   # v1.79.1 the count of a background import
    for i, row in enumerate(rows, start=start):
        data, problems = convert(path, row if isinstance(row, dict) else {})
        if problems:
            results.append({'row': i, 'ok': False, 'errors': problems})
            row_done(False)
            continue
        sub = _sub_request(factory, path, schema, data, request)
        force_authenticate(sub, user=request.user)
        sub.tenant = getattr(request, 'tenant', None)
        try:
            with (guard() if dry else _null()), transaction.atomic():
                resp = func(sub, *match.args, **match.kwargs)   # app-kit registers take the register "kind"
                if resp.status_code >= 400:
                    raise _Rollback(resp)
                if dry:
                    transaction.set_rollback(True)
            res = {'row': i, 'ok': True}
            if not dry and isinstance(getattr(resp, 'data', None), dict):
                res['id'] = resp.data.get('id')
            results.append(res)
            ok += 1
            row_done(True)
        except _Rollback as rb:
            results.append({'row': i, 'ok': False, 'errors': _flatten(getattr(rb.resp, 'data', None) or 'Rejected')})
            row_done(False)
        except Exception as exc:   # noqa: BLE001
            results.append({'row': i, 'ok': False, 'errors': [str(exc)[:200]]})
            row_done(False)
    return {'dry_run': dry, 'total': len(rows), 'ok': ok, 'failed': len(rows) - ok, 'results': results}


class _null:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False
