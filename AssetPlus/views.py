"""
AssetPlus API (mounted at /asset-plus/), v1.12.0.

Rights (the central AccessControl layer is switched off for these views and the same rules are applied here,
because the side tables keep ids instead of foreign keys):
  * company admin: everything;
  * asset master: view_asset (read), add_asset / change_asset / delete_asset;
  * allocation, return: view_ / add_ / change_assetallocation;
  * transfer: request with add_assettransfer or add_assetallocation, approve with approve_assettransfer;
  * maintenance: view_ / add_ / change_maintenancerecord (or change_asset);
  * damage / loss: report with add_assetdamage / add_assetloss (or change_asset), approve with approve_assetdamage / approve_assetloss;
  * disposal: request with add_assetdisposal (or delete_asset), approve with approve_assetdisposal;
  * recovery instalments: view_recoveryinstalment, view_assetdamage, view_assetloss, view_payslip or view_payrollrun (payroll);
  * exit clearance: view_assetallocation or view_endofservice; waiver with waive_assetclearance;
  * branch users only see and act on assets of their branches (asset branch, else the branch of the holder);
  * employees (self-service) see only the assets with them, confirm receipt, report damage / loss, ask for a
    return or a transfer to a colleague, and see the history of their assets from the day they received them.
Nobody approves a request they raised themselves (except the company admin).
"""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.db import connection, transaction
from django.db.models import Count, Q, Sum
from django.utils.dateparse import parse_date
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services as S
from .models import (AllocationExtra, AssetDamage, AssetDisposal, AssetEvent, AssetFile, AssetLoss, AssetPlusSettings, AssetProfile,
                     AssetReturn, AssetTransfer, AssetTypeConfig, ClearanceWaiver, MaintenanceRecord, MaintenanceSchedule,
                     RecoveryInstalment, SharedAllocation)

R = {
    'master_view': ('view_asset', 'view_assetprofile'),
    'master_add': ('add_asset',),
    'master_change': ('change_asset', 'change_assetprofile'),
    'master_delete': ('delete_asset',),
    'alloc_view': ('view_assetallocation',),
    'alloc_add': ('add_assetallocation',),
    'alloc_change': ('change_assetallocation',),
    'transfer_view': ('view_assettransfer', 'view_assetallocation'),
    'transfer_add': ('add_assettransfer', 'add_assetallocation'),
    'transfer_approve': ('approve_assettransfer',),
    'maint_view': ('view_maintenancerecord', 'view_asset'),
    'maint_change': ('add_maintenancerecord', 'change_maintenancerecord', 'change_asset'),
    'damage_view': ('view_assetdamage', 'view_asset'),
    'damage_add': ('add_assetdamage', 'change_asset'),
    'damage_approve': ('approve_assetdamage',),
    'loss_view': ('view_assetloss', 'view_asset'),
    'loss_add': ('add_assetloss', 'change_asset'),
    'loss_approve': ('approve_assetloss',),
    'disposal_view': ('view_assetdisposal', 'view_asset'),
    'disposal_add': ('add_assetdisposal', 'delete_asset'),
    'disposal_approve': ('approve_assetdisposal',),
    'recovery_view': ('view_recoveryinstalment', 'view_assetdamage', 'view_assetloss', 'view_payslip', 'view_payrollrun'),
    'clearance_view': ('view_assetallocation', 'view_endofservice'),
    'clearance_change': ('change_assetallocation', 'change_endofservice'),
    'waive': ('waive_assetclearance',),
    'settings': ('change_assetplussettings', 'change_assettype'),
}


# ------------------------------------------------------------------ access helpers
def _ctx(request):
    from AccessControl.access import ctx
    return ctx(request)


def can(request, key):
    try:
        c = _ctx(request)
        return c.admin or any(x in c.codes for x in R[key])
    except Exception:
        return False


def is_admin(request):
    try:
        return _ctx(request).admin
    except Exception:
        return False


def my_emp(request):
    try:
        return _ctx(request).emp
    except Exception:
        return None


def user_branches(request):
    try:
        c = _ctx(request)
        return None if c.admin else (c.branches or [])
    except Exception:
        return []


def _schema(request):
    if connection.schema_name != 'public':
        return connection.schema_name
    return request.GET.get('schema')


class CompanyMember(BasePermission):
    message = 'You do not have access to this company.'

    def has_permission(self, request, view):
        u = request.user
        if not u or not u.is_authenticated:
            self.message = 'Please log in.'
            return False
        sch = _schema(request)
        if not sch or sch == 'public':
            self.message = 'Choose a company.'
            return False
        return u.is_superuser or u.tenants.filter(schema_name=sch).exists()


class Denied(Exception):
    def __init__(self, message='You do not have permission to do this.'):
        self.message = message


def need(request, key, message=None):
    if not can(request, key):
        raise Denied(message or 'You do not have the right to do this. Ask your administrator for the asset rights.')


def guard(fn):
    """Turn AssetError / Denied into a clean 400 / 403 / 404 response."""
    def wrapper(self, request, *a, **k):
        try:
            return fn(self, request, *a, **k)
        except S.AssetError as e:
            body = {'detail': e.message}
            if e.field:
                body[e.field] = [e.message]
            return Response(body, status=e.status)
        except Denied as e:
            return Response({'detail': e.message}, status=status.HTTP_403_FORBIDDEN)
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


class Base:
    zeo_access = False   # rights are checked here (see the module docstring)
    zeo_scope = False
    permission_classes = [CompanyMember]
    parser_classes = [JSONParser, MultiPartParser, FormParser]


# ------------------------------------------------------------------ input helpers
class In:
    def __init__(self, request):
        d = request.data
        try:
            self.d = d.dict() if hasattr(d, 'dict') else dict(d)
        except Exception:
            self.d = {}
        self.files = request.FILES

    def has(self, k):
        return k in self.d

    def s(self, k, default=''):
        v = self.d.get(k, default)
        return '' if v is None else str(v).strip()

    def i(self, k, required=False, label=None):
        v = self.d.get(k)
        if v in (None, '', 'null', 'None'):
            if required:
                raise S.AssetError(f'{label or k.replace("_", " ").capitalize()} is required.', k)
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            raise S.AssetError(f'{label or k.replace("_", " ").capitalize()} must be a number.', k)

    def n(self, k, default=None, label=None):
        v = self.d.get(k)
        if v in (None, '', 'null'):
            return default
        try:
            return Decimal(str(v))
        except (InvalidOperation, ValueError):
            raise S.AssetError(f'{label or k.replace("_", " ").capitalize()} must be a number.', k)

    def dt(self, k, required=False, label=None):
        v = self.d.get(k)
        if v in (None, '', 'null'):
            if required:
                raise S.AssetError(f'{label or k.replace("_", " ").capitalize()} is required.', k)
            return None
        try:
            r = parse_date(str(v)[:10])
        except ValueError:
            r = None
        if r is None:
            raise S.AssetError(f'{label or k.replace("_", " ").capitalize()} must be a date (YYYY-MM-DD).', k)
        return r

    def b(self, k, default=False):
        v = self.d.get(k)
        if v in (None, ''):
            return default
        return str(v).lower() in ('1', 'true', 'yes', 'on')

    def f(self, k):
        return self.files.get(k)


def url(request, f):
    try:
        return request.build_absolute_uri(f.url) if f else None
    except Exception:
        return None


def dstr(d):
    return d.isoformat() if d else None


def num(v):
    return str(S.money(v)) if v is not None else None


def r1(v):
    return f'{Decimal(v):.1f}' if v is not None else None


# ------------------------------------------------------------------ visibility
def holder_maps(asset_ids=None):
    A = S.Allocation()
    allocs = A.objects.filter(returned_date__isnull=True).select_related('employee')
    shared = SharedAllocation.objects.filter(returned_date__isnull=True)
    if asset_ids is not None:
        allocs = allocs.filter(asset_id__in=asset_ids)
        shared = shared.filter(asset_id__in=asset_ids)
    emp = {a.asset_id: a for a in allocs}
    sh = {s.asset_id: s for s in shared}
    return emp, sh


def visible_asset_ids(request):
    """None = every asset; else the asset ids this branch user may see."""
    br = user_branches(request)
    if br is None:
        return None
    br = set(br)
    profiles = dict(AssetProfile.objects.values_list('asset_id', 'branch_id'))
    emp, sh = holder_maps()
    out = set()
    for aid in S.Asset().objects.values_list('id', flat=True):
        b = profiles.get(aid)
        if not b:
            if aid in emp:
                b = emp[aid].employee.emp_branch_id_id
            elif aid in sh:
                b = sh[aid].branch_id
        if b is None or b in br:
            out.add(aid)
    return out


def ess_asset_ids(request, current_only=False):
    e = my_emp(request)
    if not e:
        return set()
    qs = S.Allocation().objects.filter(employee_id=e.pk)
    if current_only:
        qs = qs.filter(returned_date__isnull=True)
    ids = set(qs.values_list('asset_id', flat=True))
    sq = SharedAllocation.objects.filter(custodian_employee_id=e.pk)
    if current_only:
        sq = sq.filter(returned_date__isnull=True)
    return ids | set(sq.values_list('asset_id', flat=True))


def get_asset(request, pk, key='master_view', ess_ok=False):
    A = S.Asset()
    a = A.objects.select_related('asset_type').filter(pk=pk).first()
    if a is None:
        raise S.AssetError('Asset not found.', status=404)
    if can(request, key):
        vis = visible_asset_ids(request)
        if vis is not None and a.pk not in vis:
            raise S.AssetError('Asset not found.', status=404)
        return a
    if ess_ok and a.pk in ess_asset_ids(request):
        return a
    if ess_ok:
        raise S.AssetError('Asset not found.', status=404)
    raise Denied()


def check_branch(request, branch_id, field='branch_id'):
    if branch_id is None:
        return
    br = user_branches(request)
    if not S.M('OrganisationManager', 'brnch_mstr').objects.filter(pk=branch_id).exists():
        raise S.AssetError('This branch does not exist.', field)
    if br is not None and branch_id not in br:
        raise S.AssetError('You can only use your own branches.', field, status=403)


def check_employee(request, emp_id, field='employee'):
    e = S.emp(emp_id)
    if e is None:
        raise S.AssetError('Choose an existing employee.', field)
    br = user_branches(request)
    if br is not None and e.emp_branch_id_id not in br:
        raise S.AssetError('This employee is in a branch you do not manage.', field, status=403)
    return e


# ------------------------------------------------------------------ serialisation
def asset_row(request, a, p=None, emp_alloc=None, shared=None, open_maint=None, names=None):
    p = p or S.ensure_profile(a)
    names = names if names is not None else {}
    st = S.effective_status(a, p)
    hold = None
    if emp_alloc is not None or shared is not None:
        if emp_alloc is not None:
            hold = {'type': 'employee', 'employee_id': emp_alloc.employee_id, 'name': S.person(emp_alloc.employee), 'since': dstr(emp_alloc.assigned_date)}
        elif shared is not None:
            hold = {'type': shared.target_type, 'name': S.holder_label_shared(shared), 'since': dstr(shared.assigned_date),
                    'custodian_employee_id': shared.custodian_employee_id}
    branch_id = p.branch_id or (emp_alloc.employee.emp_branch_id_id if emp_alloc is not None else (shared.branch_id if shared is not None else None))
    wdays = (p.warranty_end - S.today()).days if p.warranty_end else None
    show_cost = can(request, 'master_view')
    bv = S.book_value(p, a) if show_cost else None
    if a.status == 'disposed':
        d = AssetDisposal.objects.filter(asset_id=a.pk, status='approved').order_by('-id').first()
        bv = S.money(0) if show_cost else None
        disposal = {'number': d.number, 'method': d.get_method_display(), 'date': dstr(d.disposal_date), 'value': num(d.value),
                    'book_value': num(d.book_value), 'gain_loss': num(d.gain_loss)} if d else None
    else:
        disposal = None
    row = {
        'id': a.pk, 'name': a.name, 'serial_number': a.serial_number, 'model': a.model, 'purchase_date': dstr(a.purchase_date),
        'asset_type': a.asset_type_id, 'asset_type_name': a.asset_type.name if a.asset_type_id else '',
        'status': st, 'status_label': S.STATUS_LABEL.get(st, st), 'base_status': a.status, 'condition': a.condition,
        'code': p.asset_code, 'barcode': p.barcode or p.asset_code, 'branch_id': branch_id, 'branch_name': names.get(('b', branch_id)) if names else S.branch_name(branch_id),
        'location': p.location, 'department_id': p.department_id, 'department_name': S.dept_name(p.department_id) if p.department_id else '',
        'vendor': p.vendor, 'invoice_no': p.invoice_no, 'invoice_file': url(request, p.invoice_file), 'purchase_order': p.purchase_order,
        'warranty_start': dstr(p.warranty_start), 'warranty_end': dstr(p.warranty_end), 'warranty_days_left': wdays,
        'warranty_reminder_days': p.warranty_reminder_days,
        'insurance_provider': p.insurance_provider, 'insurance_policy_no': p.insurance_policy_no, 'insurance_expiry': dstr(p.insurance_expiry),
        'depreciation_method': p.depreciation_method, 'depreciation_start': dstr(p.depreciation_start), 'useful_life_months': p.useful_life_months,
        'declining_rate': num(p.declining_rate) if p.declining_rate is not None else None,
        'meter_reading': r1(p.meter_reading), 'notes': p.notes,
        'holder': hold, 'in_maintenance': bool(open_maint) if open_maint is not None else (st == 'maintenance'),
        'disposal': disposal,
    }
    if show_cost:
        row.update({'purchase_cost': num(p.purchase_cost), 'currency': p.currency, 'salvage_value': num(p.salvage_value), 'book_value': num(bv)})
    return row


def custom_fields_of(asset):
    CF = S.M('OrganisationManager', 'AssetCustomField')
    CV = S.M('OrganisationManager', 'AssetCustomFieldValue')
    vals = {v.custom_field_id: v.field_value for v in CV.objects.filter(asset=asset)} if asset and asset.pk else {}
    out = []
    for f in CF.objects.filter(asset_type_id=asset.asset_type_id).order_by('id'):
        out.append({'id': f.id, 'name': f.custom_field, 'type': f.data_type or 'text',
                    'options': f.dropdown_values or f.radio_values or f.checkbox_values or [], 'value': vals.get(f.id)})
    return out


def validate_custom_value(field, value):
    """Check a value against the field type and options (the model's own clean() cannot be used – v1.12.0)."""
    import json
    if value in (None, ''):
        return None
    t = field.data_type or 'text'
    name = field.custom_field
    if t == 'date':
        if parse_date(str(value)[:10]) is None:
            raise S.AssetError(f'{name}: enter a date (YYYY-MM-DD).', 'custom_fields')
        return str(value)[:10]
    if t == 'dropdown' or t == 'radio':
        opts = [str(o) for o in ((field.dropdown_values if t == 'dropdown' else field.radio_values) or [])]
        if str(value) not in opts:
            raise S.AssetError(f'{name}: choose one of {", ".join(opts) or "(no options set)"}.', 'custom_fields')
        return str(value)
    if t == 'checkbox':
        opts = [str(o) for o in (field.checkbox_values or [])]
        vals = value
        if isinstance(value, str):
            try:
                vals = json.loads(value) if value.startswith('[') else [x.strip() for x in value.split(',') if x.strip()]
            except ValueError:
                vals = [x.strip() for x in value.split(',') if x.strip()]
        if not isinstance(vals, (list, tuple)):
            vals = [vals]
        bad = [str(v) for v in vals if str(v) not in opts]
        if bad:
            raise S.AssetError(f'{name}: {", ".join(bad)} is not an option ({", ".join(opts)}).', 'custom_fields')
        return ','.join(str(v) for v in vals)
    v = str(value)
    if len(v) > 2000:
        raise S.AssetError(f'{name}: keep the text under 2000 characters.', 'custom_fields')
    return v


def save_custom_values(asset, values):
    """values: {field id or name: value}. One value per field (old duplicates are removed)."""
    import json
    if values in (None, ''):
        return {}
    if isinstance(values, str):
        try:
            values = json.loads(values)
        except ValueError:
            raise S.AssetError('Custom fields must be sent as JSON.', 'custom_fields')
    if isinstance(values, list):
        values = {str(x.get('id') or x.get('name')): x.get('value') for x in values if isinstance(x, dict)}
    CF = S.M('OrganisationManager', 'AssetCustomField')
    CV = S.M('OrganisationManager', 'AssetCustomFieldValue')
    fields = list(CF.objects.filter(asset_type_id=asset.asset_type_id))
    by = {str(f.id): f for f in fields}
    by.update({f.custom_field: f for f in fields})
    changes = {}
    for k, v in values.items():
        f = by.get(str(k))
        if f is None:
            raise S.AssetError(f'"{k}" is not a field of the asset type {asset.asset_type.name}.', 'custom_fields')
        clean = validate_custom_value(f, v)
        rows = list(CV.objects.filter(asset=asset, custom_field=f).order_by('id'))
        old = rows[0].field_value if rows else None
        if rows:
            for extra in rows[1:]:
                extra.delete()
            if old != clean:
                rows[0].field_value = clean
                rows[0].save(update_fields=['field_value'])
        elif clean is not None:
            CV.objects.create(asset=asset, custom_field=f, field_value=clean)
        if (old or None) != (clean or None):
            changes[f.custom_field] = {'label': f.custom_field, 'old': old or '', 'new': clean or ''}
    return changes


PROFILE_TEXT = ('location', 'vendor', 'invoice_no', 'purchase_order', 'insurance_provider', 'insurance_policy_no', 'barcode', 'notes')
PROFILE_LABEL = {
    'asset_code': 'Asset code', 'branch_id': 'Branch', 'location': 'Location', 'department_id': 'Department', 'purchase_cost': 'Purchase cost',
    'currency': 'Currency', 'vendor': 'Vendor', 'invoice_no': 'Invoice no.', 'purchase_order': 'Purchase order', 'warranty_start': 'Warranty start',
    'warranty_end': 'Warranty end', 'warranty_reminder_days': 'Warranty reminder days', 'insurance_provider': 'Insurer',
    'insurance_policy_no': 'Insurance policy', 'insurance_expiry': 'Insurance expiry', 'depreciation_method': 'Depreciation method',
    'depreciation_start': 'Depreciation start', 'useful_life_months': 'Useful life (months)', 'salvage_value': 'Salvage value',
    'declining_rate': 'Declining rate', 'barcode': 'Barcode', 'meter_reading': 'Meter reading', 'notes': 'Notes', 'invoice_file': 'Invoice file',
}


def apply_profile(request, p, a, inp, creating=False):
    """Validate and copy the profile fields from the request into p (not saved). Returns the changes."""
    old = {k: getattr(p, k) for k in PROFILE_LABEL if k != 'invoice_file'}
    for k in PROFILE_TEXT:
        if inp.has(k):
            setattr(p, k, inp.s(k))
    if inp.has('asset_code') and inp.s('asset_code'):
        code = inp.s('asset_code').upper()
        if AssetProfile.objects.filter(asset_code=code).exclude(pk=p.pk).exists():
            raise S.AssetError(f'The asset code {code} is already used.', 'asset_code')
        p.asset_code = code
    if inp.has('branch_id'):
        b = inp.i('branch_id')
        check_branch(request, b)
        p.branch_id = b
    elif creating and user_branches(request) is not None and not p.branch_id:
        br = user_branches(request)
        if len(br) == 1:
            p.branch_id = br[0]
        else:
            raise S.AssetError('Choose the branch of the asset.', 'branch_id')
    if inp.has('department_id'):
        d = inp.i('department_id')
        if d and not S.M('OrganisationManager', 'dept_master').objects.filter(pk=d).exists():
            raise S.AssetError('This department does not exist.', 'department_id')
        p.department_id = d
    if inp.has('purchase_cost'):
        v = inp.n('purchase_cost', Decimal(0))
        if v < 0:
            raise S.AssetError('The purchase cost cannot be negative.', 'purchase_cost')
        p.purchase_cost = S.money(v)
    if inp.has('currency'):
        c = inp.s('currency').upper() or 'AED'
        if len(c) != 3 or not c.isalpha():
            raise S.AssetError('Use a 3-letter currency code such as AED.', 'currency')
        p.currency = c
    for k in ('warranty_start', 'warranty_end', 'insurance_expiry', 'depreciation_start'):
        if inp.has(k):
            setattr(p, k, inp.dt(k))
    if p.warranty_start and p.warranty_end and p.warranty_end < p.warranty_start:
        raise S.AssetError('The warranty ends before it starts.', 'warranty_end')
    if inp.has('warranty_reminder_days'):
        v = inp.i('warranty_reminder_days')
        if v is not None and not (0 <= v <= 365):
            raise S.AssetError('Remind between 0 and 365 days before the warranty ends.', 'warranty_reminder_days')
        p.warranty_reminder_days = v
    if inp.has('depreciation_method'):
        m = inp.s('depreciation_method') or 'straight_line'
        if m not in ('none', 'straight_line', 'declining'):
            raise S.AssetError('Choose no depreciation, straight line or declining balance.', 'depreciation_method')
        p.depreciation_method = m
    if inp.has('useful_life_months'):
        v = inp.i('useful_life_months')
        p.useful_life_months = v or 0
    if p.depreciation_method != 'none' and not (1 <= (p.useful_life_months or 0) <= 600):
        raise S.AssetError('Useful life must be between 1 and 600 months.', 'useful_life_months')
    if inp.has('salvage_value'):
        v = inp.n('salvage_value', Decimal(0))
        if v < 0:
            raise S.AssetError('The salvage value cannot be negative.', 'salvage_value')
        p.salvage_value = S.money(v)
    if S.money(p.salvage_value) > S.money(p.purchase_cost):
        raise S.AssetError('The salvage value cannot be more than the purchase cost.', 'salvage_value')
    if inp.has('declining_rate'):
        v = inp.n('declining_rate')
        if v is not None and not (0 < v <= 100):
            raise S.AssetError('The declining rate is a % per year between 0 and 100.', 'declining_rate')
        p.declining_rate = v
    if inp.has('meter_reading'):
        v = inp.n('meter_reading')
        if v is not None and v < 0:
            raise S.AssetError('The meter reading cannot be negative.', 'meter_reading')
        p.meter_reading = v
    changes = {}
    for k, o in old.items():
        n = getattr(p, k)
        if str(o if o is not None else '') != str(n if n is not None else ''):
            changes[k] = {'label': PROFILE_LABEL[k], 'old': str(o) if o not in (None, '') else '', 'new': str(n) if n not in (None, '') else ''}
    if inp.f('invoice_file'):
        p.invoice_file = inp.f('invoice_file')
        changes['invoice_file'] = {'label': 'Invoice file', 'old': '', 'new': inp.f('invoice_file').name}
    return changes


def validate_base(inp, a, creating):
    A = S.Asset()
    AT = S.M('OrganisationManager', 'AssetType')
    if creating or inp.has('asset_type'):
        t = inp.i('asset_type', required=True, label='Asset type')
        at = AT.objects.filter(pk=t).first()
        if not at:
            raise S.AssetError('Choose an existing asset type.', 'asset_type')
        if not creating and a.asset_type_id != at.pk and S.M('OrganisationManager', 'AssetCustomFieldValue').objects.filter(asset=a).exists():
            raise S.AssetError('This asset has custom field values of its type. Clear them before changing the type.', 'asset_type')
        a.asset_type = at
    if creating or inp.has('name'):
        n = inp.s('name')
        if not n:
            raise S.AssetError('Name is required.', 'name')
        if len(n) > 100:
            raise S.AssetError('Keep the name under 100 characters.', 'name')
        a.name = n
    if creating or inp.has('serial_number'):
        sn = inp.s('serial_number')
        if not sn:
            raise S.AssetError('Serial number is required.', 'serial_number')
        if len(sn) > 100:
            raise S.AssetError('Keep the serial number under 100 characters.', 'serial_number')
        if A.objects.filter(serial_number__iexact=sn).exclude(pk=a.pk).exists():
            raise S.AssetError(f'Another asset already has the serial number {sn}.', 'serial_number')
        a.serial_number = sn
    if inp.has('model'):
        m = inp.s('model')
        if len(m) > 100:
            raise S.AssetError('Keep the model under 100 characters.', 'model')
        a.model = m
    if creating or inp.has('purchase_date'):
        d = inp.dt('purchase_date', required=True, label='Purchase date')
        if d > S.today():
            raise S.AssetError('The purchase date cannot be in the future.', 'purchase_date')
        a.purchase_date = d
    if inp.has('condition'):
        c = inp.s('condition')
        if c not in ('healthy', 'minor_damage', 'major_damage'):
            raise S.AssetError('Choose the condition: healthy, minor damage or major damage.', 'condition')
        a.condition = c
    if inp.has('status') and inp.s('status') and inp.s('status') != (a.status if a.pk else 'available'):
        raise S.AssetError('The status changes through allocation, maintenance, loss and disposal – it cannot be edited here.', 'status')


def history_rows(qs):
    return [{'id': e.id, 'event': e.event, 'summary': e.summary, 'changes': e.changes, 'employee_id': e.employee_id,
             'ref_type': e.ref_type, 'ref_id': e.ref_id, 'user': e.user_name, 'at': e.at.isoformat()} for e in qs]


def file_row(request, f):
    return {'id': f.id, 'kind': f.kind, 'kind_label': f.get_kind_display(), 'name': f.name or f.file.name.rsplit('/', 1)[-1],
            'url': url(request, f.file), 'record_type': f.record_type, 'record_id': f.record_id,
            'uploaded_by': S.user_label(f.uploaded_by_id), 'uploaded_at': f.uploaded_at.isoformat()}


def save_files(request, asset_id, kind, record_type='', record_id=None, key='files'):
    out = []
    for f in request.FILES.getlist(key):
        if f.size > 15 * 1024 * 1024:
            raise S.AssetError(f'{f.name} is larger than 15 MB.', key)
        out.append(AssetFile.objects.create(asset_id=asset_id, kind=kind, record_type=record_type, record_id=record_id, file=f, name=f.name[:255],
                                            uploaded_by_id=request.user.pk))
    return out


# ------------------------------------------------------------------ asset master
class AssetViewSet(Base, viewsets.ViewSet):
    """Asset register: base asset + profile + custom fields in one record, and every life-cycle action."""

    def _rows(self, request, qs):
        assets = list(qs)
        S.ensure_profiles(assets)
        ids = [a.pk for a in assets]
        profiles = {p.asset_id: p for p in AssetProfile.objects.filter(asset_id__in=ids)}
        emp, sh = holder_maps(ids)
        maint = set(MaintenanceRecord.objects.filter(asset_id__in=ids, status='open').values_list('asset_id', flat=True))
        names = {('b', b.pk): b.branch_name for b in S.M('OrganisationManager', 'brnch_mstr').objects.all()}
        names[('b', None)] = ''
        return [asset_row(request, a, profiles.get(a.pk), emp.get(a.pk), sh.get(a.pk), a.pk in maint, names) for a in assets]

    @guard
    def list(self, request):
        A = S.Asset()
        qs = A.objects.select_related('asset_type').order_by('name', 'id')
        if not can(request, 'master_view'):
            qs = qs.filter(pk__in=ess_asset_ids(request, current_only=True))
        else:
            vis = visible_asset_ids(request)
            if vis is not None:
                qs = qs.filter(pk__in=vis)
        q = request.query_params
        if q.get('asset_type'):
            qs = qs.filter(asset_type_id=q.get('asset_type'))
        if q.get('condition'):
            qs = qs.filter(condition=q.get('condition'))
        rows = self._rows(request, qs)
        st = q.get('status')
        if st:
            rows = [r for r in rows if r['status'] == st]
        if q.get('branch'):
            rows = [r for r in rows if str(r['branch_id']) == str(q.get('branch'))]
        if q.get('employee'):
            rows = [r for r in rows if r['holder'] and str(r['holder'].get('employee_id')) == str(q.get('employee'))]
        if q.get('warranty_expiring'):
            days = int(q.get('warranty_expiring') or 30)
            rows = [r for r in rows if r['warranty_days_left'] is not None and 0 <= r['warranty_days_left'] <= days]
        if q.get('q'):
            t = q.get('q').lower()
            rows = [r for r in rows if t in ' '.join(str(r.get(k) or '') for k in ('name', 'serial_number', 'code', 'model', 'asset_type_name', 'location')).lower()
                    or (r['holder'] and t in r['holder']['name'].lower())]
        return Response(rows)

    @guard
    def retrieve(self, request, pk=None):
        a = get_asset(request, pk, ess_ok=True)
        rows = self._rows(request, S.Asset().objects.select_related('asset_type').filter(pk=a.pk))
        row = rows[0]
        p = AssetProfile.objects.get(asset_id=a.pk)
        row['custom_fields'] = custom_fields_of(a)
        row['holder_detail'] = S.holder(a.pk)
        if row['holder_detail'] and row['holder_detail'].get('since'):
            row['holder_detail']['since'] = dstr(row['holder_detail']['since'])
            row['holder_detail']['expected_return_date'] = dstr(row['holder_detail'].get('expected_return_date'))
        if can(request, 'master_view'):
            row['depreciation_schedule'] = [{**r, 'until': dstr(r['until']), 'opening': num(r['opening']), 'depreciation': num(r['depreciation']),
                                             'closing': num(r['closing'])} for r in S.depreciation_schedule(p, a)]
            row['counts'] = {
                'allocations': S.Allocation().objects.filter(asset=a).count() + SharedAllocation.objects.filter(asset_id=a.pk).count(),
                'maintenance': MaintenanceRecord.objects.filter(asset_id=a.pk).count(),
                'damages': AssetDamage.objects.filter(asset_id=a.pk).count(),
                'losses': AssetLoss.objects.filter(asset_id=a.pk).count(),
                'files': AssetFile.objects.filter(asset_id=a.pk).count(),
                'maintenance_cost': num(MaintenanceRecord.objects.filter(asset_id=a.pk).exclude(status='cancelled').aggregate(s=Sum('cost'))['s'] or 0),
            }
            row['pending'] = {
                'transfer': AssetTransfer.objects.filter(asset_id=a.pk, status='pending').values('id', 'number').first(),
                'disposal': AssetDisposal.objects.filter(asset_id=a.pk, status='pending').values('id', 'number').first(),
                'return': AssetReturn.objects.filter(asset_id=a.pk, status='requested').values('id', 'number').first(),
                'maintenance': MaintenanceRecord.objects.filter(asset_id=a.pk, status='open').values('id', 'number').first(),
                'loss': AssetLoss.objects.filter(asset_id=a.pk).exclude(status='rejected').filter(found_on__isnull=True).values('id', 'number', 'status').first(),
            }
        return Response(row)

    @guard
    @transaction.atomic
    def create(self, request):
        need(request, 'master_add')
        inp = In(request)
        A = S.Asset()
        a = A(status='available', condition='healthy')
        validate_base(inp, a, True)
        st = inp.s('status') or 'available'
        if st != 'available':
            raise S.AssetError('A new asset starts as available. Allocate it afterwards.', 'status')
        p = AssetProfile(asset_id=0, asset_code='', currency=AssetPlusSettings.get().currency)
        cfg = S.type_config(a.asset_type_id)
        p.depreciation_method, p.useful_life_months = cfg.depreciation_method, cfg.useful_life_months or 36
        apply_profile(request, p, a, inp, creating=True)
        if cfg.salvage_percent and not inp.has('salvage_value'):
            p.salvage_value = S.money(Decimal(p.purchase_cost) * cfg.salvage_percent / 100)
        code = p.asset_code
        with S.quiet():
            a.save()  # the signal creates a profile with the next code and the "created" event
        prof = AssetProfile.objects.get(asset_id=a.pk)
        for f in [x.name for x in AssetProfile._meta.concrete_fields if x.name not in ('id', 'asset_id', 'asset_code', 'created_at', 'updated_at', 'created_by_id',
                                                                                           'barcode', 'extra_status')]:
            setattr(prof, f, getattr(p, f))
        if code and code != prof.asset_code:
            AssetEvent.objects.filter(asset_id=a.pk, event='created').update(
                summary=f'Asset created – {a.name}, serial {a.serial_number}, code {code}')
            prof.asset_code = code
        prof.barcode = p.barcode or prof.asset_code
        prof.created_by_id = request.user.pk
        prof.save()
        save_custom_values(a, inp.d.get('custom_fields'))
        for f in request.FILES.getlist('photos'):
            AssetFile.objects.create(asset_id=a.pk, kind='photo', file=f, name=f.name[:255], uploaded_by_id=request.user.pk)
        return Response(self.retrieve(request, a.pk).data, status=status.HTTP_201_CREATED)

    @guard
    @transaction.atomic
    def partial_update(self, request, pk=None):
        need(request, 'master_change')
        a = get_asset(request, pk)
        if a.status == 'disposed':
            raise S.AssetError('A disposed asset cannot be changed.')
        inp = In(request)
        old_type = a.asset_type_id
        validate_base(inp, a, False)
        p = S.ensure_profile(a)
        changes = apply_profile(request, p, a, inp)
        a.save()  # base field changes are logged by the signal ("edited")
        p.save()
        cf = save_custom_values(a, inp.d.get('custom_fields')) if inp.has('custom_fields') else {}
        changes.update(cf)
        if changes:
            S.log_event(a.pk, 'edited', 'Changed ' + ', '.join(f"{v['label'].lower()} {v['old'] or '–'} → {v['new'] or '–'}" for v in changes.values()),
                        request.user, changes=changes)
        return self.retrieve(request, a.pk)

    update = partial_update

    @guard
    @transaction.atomic
    def destroy(self, request, pk=None):
        need(request, 'master_delete')
        a = get_asset(request, pk)
        used = (S.Allocation().objects.filter(asset=a).exists() or SharedAllocation.objects.filter(asset_id=a.pk).exists()
                or MaintenanceRecord.objects.filter(asset_id=a.pk).exists() or AssetDamage.objects.filter(asset_id=a.pk).exists()
                or AssetLoss.objects.filter(asset_id=a.pk).exists() or S.M('OrganisationManager', 'AssetRequest').objects.filter(requested_asset=a).exists())
        if used:
            raise S.AssetError('This asset has a history (allocations, requests, maintenance …). Dispose of it instead of deleting it.')
        aid = a.pk
        a.delete()
        for m in (AssetProfile, AssetFile, AssetEvent, MaintenanceSchedule, AssetDisposal, AssetTransfer):
            m.objects.filter(asset_id=aid).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    # ---------------- life cycle actions
    @action(detail=True, methods=['post'])
    @guard
    def allocate(self, request, pk=None):
        need(request, 'alloc_add')
        a = get_asset(request, pk)
        inp = In(request)
        target = inp.s('target_type') or 'employee'
        eid = inp.i('employee') or inp.i('employee_id')
        if target == 'employee':
            if not eid:
                raise S.AssetError('Choose the employee who receives the asset.', 'employee')
            check_employee(request, eid)
        branch_id = inp.i('branch_id')
        check_branch(request, branch_id)
        cust = inp.i('custodian_employee_id')
        if cust:
            check_employee(request, cust, 'custodian_employee_id')
        res = S.allocate(a, request.user, employee_id=eid, target_type=target, branch_id=branch_id, location=inp.s('location'),
                         department_id=inp.i('department_id'), custodian_employee_id=cust, assigned_date=inp.dt('assigned_date'),
                         expected_return_date=inp.dt('expected_return_date'), accessories=inp.s('accessories'),
                         handover_condition=inp.s('handover_condition'), handover_note=inp.s('handover_note'), handover_file=inp.f('handover_file'))
        return Response({'detail': 'Asset allocated.', 'allocation_id': res.pk if target == 'employee' else None,
                         'shared_id': res.pk if target != 'employee' else None, 'asset': self.retrieve(request, a.pk).data}, status=201)

    @action(detail=True, methods=['post'], url_path='return')
    @guard
    def return_asset(self, request, pk=None):
        need(request, 'alloc_change')
        a = get_asset(request, pk)
        inp = In(request)
        rr = None
        if inp.i('return_request'):
            rr = AssetReturn.objects.filter(pk=inp.i('return_request'), asset_id=a.pk, status='requested').first()
            if not rr:
                raise S.AssetError('The return request is not open.', 'return_request')
        r = S.complete_return(a, request.user, return_date=inp.dt('return_date'), condition=inp.s('condition') or 'healthy',
                              accessories_returned=inp.b('accessories_returned', True), missing_items=inp.s('missing_items'),
                              damage_found=inp.b('damage_found'), damage_description=inp.s('damage_description'),
                              damage_estimate=inp.n('damage_estimate', Decimal(0)), data_wiped=inp.b('data_wiped'), notes=inp.s('notes'), return_request=rr)
        save_files(request, a.pk, 'return', 'return', r.pk)
        return Response({'detail': 'Asset returned.' + (f' Damage {AssetDamage.objects.get(pk=r.damage_id).number} was recorded for approval.' if r.damage_id else ''),
                         'return': return_row(r), 'damage_id': r.damage_id})

    @action(detail=True, methods=['post'])
    @guard
    def transfer(self, request, pk=None):
        inp = In(request)
        ess = not can(request, 'transfer_add')
        a = get_asset(request, pk, key='transfer_add', ess_ok=True) if not ess else get_asset(request, pk, ess_ok=True)
        e = my_emp(request)
        if ess:
            h = S.holder(a.pk)
            if not e or not h or h.get('employee_id') != e.pk:
                raise Denied('You can only transfer an asset that is with you.')
            if (inp.s('to_type') or 'employee') != 'employee':
                raise Denied('In self-service you can only hand an asset to a colleague. Ask HR for other transfers.')
        to_type = inp.s('to_type') or 'employee'
        to_emp = inp.i('to_employee_id') or inp.i('to_employee')
        if to_type == 'employee' and to_emp and not ess:
            check_employee(request, to_emp, 'to_employee_id')
        to_branch = inp.i('to_branch_id')
        if not ess:
            check_branch(request, to_branch, 'to_branch_id')
        t = S.request_transfer(a, request.user, to_type=to_type, to_employee_id=to_emp, to_branch_id=to_branch, to_location=inp.s('to_location'),
                               to_department_id=inp.i('to_department_id'), transfer_date=inp.dt('transfer_date'), reason=inp.s('reason'),
                               requested_by_employee_id=e.pk if e else None)
        return Response(transfer_row(t), status=201)

    @action(detail=True, methods=['post'])
    @guard
    def maintenance(self, request, pk=None):
        need(request, 'maint_change')
        a = get_asset(request, pk)
        inp = In(request)
        r = S.start_maintenance(a, request.user, kind=inp.s('kind') or 'breakdown', description=inp.s('description'), vendor=inp.s('vendor'),
                                start_date=inp.dt('start_date'), schedule_id=inp.i('schedule'), damage_id=inp.i('damage'), cost=inp.n('cost', Decimal(0)),
                                reading=inp.n('reading'))
        save_files(request, a.pk, 'maintenance', 'maintenance', r.pk)
        return Response(maint_row(r), status=201)

    @action(detail=True, methods=['post'])
    @guard
    def damage(self, request, pk=None):
        hr = can(request, 'damage_add')
        a = get_asset(request, pk, key='damage_add', ess_ok=True)
        inp = In(request)
        e = my_emp(request)
        if not hr:
            h = S.holder(a.pk)
            if not e or not h or h.get('employee_id') != e.pk:
                raise Denied('You can only report damage on an asset that is with you.')
        eid = inp.i('employee') if hr else e.pk
        if hr and eid:
            check_employee(request, eid)
        d = S.report_damage(a, request.user, employee_id=eid, damage_date=inp.dt('damage_date'), description=inp.s('description'),
                            severity=inp.s('severity') or 'minor', repair_cost=inp.n('repair_cost', Decimal(0)), source='hr' if hr else 'ess')
        save_files(request, a.pk, 'damage', 'damage', d.pk)
        save_files(request, a.pk, 'damage', 'damage', d.pk, key='photos')
        return Response(incident_row(request, d, 'damage'), status=201)

    @action(detail=True, methods=['post'])
    @guard
    def loss(self, request, pk=None):
        hr = can(request, 'loss_add')
        a = get_asset(request, pk, key='loss_add', ess_ok=True)
        inp = In(request)
        e = my_emp(request)
        if not hr:
            h = S.holder(a.pk)
            if not e or not h or h.get('employee_id') != e.pk:
                raise Denied('You can only report an asset that is with you.')
        eid = inp.i('employee') if hr else e.pk
        if hr and eid:
            check_employee(request, eid)
        l = S.report_loss(a, request.user, employee_id=eid, loss_date=inp.dt('loss_date'), kind=inp.s('kind') or 'lost', description=inp.s('description'),
                          place=inp.s('place'), police_report_no=inp.s('police_report_no'), police_report_file=inp.f('police_report_file'),
                          source='hr' if hr else 'ess')
        return Response(incident_row(request, l, 'loss'), status=201)

    @action(detail=True, methods=['post'])
    @guard
    def dispose(self, request, pk=None):
        need(request, 'disposal_add')
        a = get_asset(request, pk)
        inp = In(request)
        d = S.request_disposal(a, request.user, method=inp.s('method'), disposal_date=inp.dt('disposal_date'), value=inp.n('value', Decimal(0)),
                               buyer=inp.s('buyer'), reason=inp.s('reason'))
        save_files(request, a.pk, 'disposal', 'disposal', d.pk)
        return Response(disposal_row(d), status=201)

    @action(detail=True, methods=['get'])
    @guard
    def history(self, request, pk=None):
        a = get_asset(request, pk, ess_ok=True)
        qs = AssetEvent.objects.filter(asset_id=a.pk)
        if not can(request, 'master_view'):
            e = my_emp(request)
            first = S.Allocation().objects.filter(asset=a, employee_id=e.pk).order_by('assigned_date').values_list('assigned_date', flat=True).first()
            first_ev = AssetEvent.objects.filter(asset_id=a.pk, employee_id=e.pk, event='allocated').order_by('at').first()
            qs = qs.filter(Q(employee_id=e.pk) | Q(employee_id__isnull=True))
            if first_ev:
                qs = qs.filter(at__gte=first_ev.at)
            elif first:
                qs = qs.filter(at__date__gte=first)
        return Response(history_rows(qs))

    @action(detail=True, methods=['get', 'post'])
    @guard
    def files(self, request, pk=None):
        a = get_asset(request, pk, ess_ok=True)
        if request.method == 'POST':
            need(request, 'master_change')
            kind = request.data.get('kind') or 'document'
            if kind not in dict(AssetFile.KINDS):
                raise S.AssetError('Choose the kind of file.', 'kind')
            if not request.FILES.getlist('files'):
                raise S.AssetError('Choose a file to upload.', 'files')
            rows = save_files(request, a.pk, kind)
            S.log_event(a.pk, 'file_added', f'{len(rows)} {dict(AssetFile.KINDS)[kind].lower()} file(s) added: ' + ', '.join(r.name for r in rows), request.user)
            return Response([file_row(request, f) for f in rows], status=201)
        qs = AssetFile.objects.filter(asset_id=a.pk)
        if not can(request, 'master_view'):
            qs = qs.filter(kind='photo')
        return Response([file_row(request, f) for f in qs])

    @action(detail=True, methods=['delete'], url_path=r'files/(?P<file_id>\d+)')
    @guard
    def delete_file(self, request, pk=None, file_id=None):
        need(request, 'master_change')
        a = get_asset(request, pk)
        f = AssetFile.objects.filter(pk=file_id, asset_id=a.pk).first()
        if not f:
            raise S.AssetError('File not found.', status=404)
        name = f.name
        f.file.delete(save=False)
        f.delete()
        S.log_event(a.pk, 'file_removed', f'File removed: {name}', request.user)
        return Response(status=204)

    @action(detail=True, methods=['get'])
    @guard
    def label(self, request, pk=None):
        """Data for the printed label (the screen draws the QR code / barcode from `payload`)."""
        a = get_asset(request, pk)
        p = S.ensure_profile(a)
        return Response({'code': p.asset_code, 'barcode': p.barcode or p.asset_code, 'name': a.name, 'serial_number': a.serial_number,
                         'type': a.asset_type.name, 'payload': f'ASSET:{p.asset_code}|SN:{a.serial_number}|ID:{a.pk}'})

    @action(detail=False, methods=['get'], url_path='by-code')
    @guard
    def by_code(self, request):
        code = (request.query_params.get('code') or '').strip()
        if code.startswith('ASSET:'):
            code = code.split('|')[0][6:]
        p = AssetProfile.objects.filter(Q(asset_code__iexact=code) | Q(barcode__iexact=code)).first()
        if not p:
            raise S.AssetError('No asset has this code.', status=404)
        return self.retrieve(request, p.asset_id)

    @action(detail=False, methods=['get'])
    @guard
    def summary(self, request):
        need(request, 'master_view')
        rows = self.list(request).data
        out = {'total': len(rows), 'by_status': {}, 'by_type': {}, 'book_value': '0.00', 'cost': '0.00', 'warranty_30': 0}
        bv = cost = Decimal(0)
        for r in rows:
            out['by_status'][r['status']] = out['by_status'].get(r['status'], 0) + 1
            out['by_type'][r['asset_type_name']] = out['by_type'].get(r['asset_type_name'], 0) + 1
            if r['status'] != 'disposed':
                bv += Decimal(r.get('book_value') or 0)
                cost += Decimal(r.get('purchase_cost') or 0)
            if r['warranty_days_left'] is not None and 0 <= r['warranty_days_left'] <= 30:
                out['warranty_30'] += 1
        out['book_value'], out['cost'] = num(bv), num(cost)
        vis = None if is_admin(request) else visible_asset_ids(request)
        f = (lambda qs: qs if vis is None else qs.filter(asset_id__in=vis))
        out['pending'] = {
            'transfers': f(AssetTransfer.objects.filter(status='pending')).count(),
            'returns': f(AssetReturn.objects.filter(status='requested')).count(),
            'damages': f(AssetDamage.objects.filter(status='reported')).count(),
            'losses': f(AssetLoss.objects.filter(status='reported')).count(),
            'disposals': f(AssetDisposal.objects.filter(status='pending')).count(),
            'maintenance_open': f(MaintenanceRecord.objects.filter(status='open')).count(),
            'acknowledgements': f(AllocationExtra.objects.filter(ack_status='pending', allocation_id__in=S.Allocation().objects.filter(
                returned_date__isnull=True).values('id'))).count(),
        }
        due = 0
        for s in f(MaintenanceSchedule.objects.filter(active=True)):
            if S.schedule_due(s, AssetProfile.objects.filter(asset_id=s.asset_id).first()):
                due += 1
        out['maintenance_due'] = due
        return Response(out)


# ------------------------------------------------------------------ allocations
def alloc_row(request, a, x=None):
    x = x or AllocationExtra.objects.filter(allocation_id=a.pk).first()
    p = AssetProfile.objects.filter(asset_id=a.asset_id).first()
    return {'id': a.pk, 'kind': 'employee', 'asset_id': a.asset_id, 'asset': a.asset.name, 'code': p.asset_code if p else '',
            'serial_number': a.asset.serial_number, 'employee_id': a.employee_id, 'employee': S.person(a.employee),
            'assigned_date': dstr(a.assigned_date), 'returned_date': dstr(a.returned_date), 'return_condition': a.return_condition,
            'expected_return_date': dstr(x.expected_return_date) if x else None,
            'overdue': bool(x and x.expected_return_date and not a.returned_date and x.expected_return_date < S.today()),
            'accessories': x.accessories if x else '', 'handover_note': x.handover_note if x else '',
            'handover_file': url(request, x.handover_file) if x else None,
            'ack_status': x.ack_status if x else 'not_required', 'ack_at': x.ack_at.isoformat() if x and x.ack_at else None,
            'ack_condition_notes': x.ack_condition_notes if x else '', 'issued_by': S.user_label(x.issued_by_id) if x else '',
            'transfer_id': x.transfer_id if x else None, 'open': a.returned_date is None}


def shared_row(s):
    a = S.Asset().objects.filter(pk=s.asset_id).first()
    p = AssetProfile.objects.filter(asset_id=s.asset_id).first()
    return {'id': s.pk, 'kind': s.target_type, 'asset_id': s.asset_id, 'asset': a.name if a else '', 'code': p.asset_code if p else '',
            'holder': S.holder_label_shared(s), 'branch_id': s.branch_id, 'location': s.location, 'department_id': s.department_id,
            'custodian_employee_id': s.custodian_employee_id, 'custodian': S.emp_name(s.custodian_employee_id),
            'assigned_date': dstr(s.assigned_date), 'expected_return_date': dstr(s.expected_return_date), 'returned_date': dstr(s.returned_date),
            'open': s.returned_date is None}


class AllocationViewSet(Base, viewsets.ViewSet):
    @guard
    def list(self, request):
        A = S.Allocation()
        qs = A.objects.select_related('asset', 'employee').order_by('-assigned_date', '-id')
        q = request.query_params
        hr = can(request, 'alloc_view')
        if not hr:
            e = my_emp(request)
            qs = qs.filter(employee_id=e.pk if e else -1)
        else:
            vis = visible_asset_ids(request)
            br = user_branches(request)
            if vis is not None:
                qs = qs.filter(Q(asset_id__in=vis) | Q(employee__emp_branch_id__in=br))
        if q.get('open') in ('1', 'true'):
            qs = qs.filter(returned_date__isnull=True)
        if q.get('employee'):
            qs = qs.filter(employee_id=q.get('employee'))
        if q.get('asset'):
            qs = qs.filter(asset_id=q.get('asset'))
        extras = {x.allocation_id: x for x in AllocationExtra.objects.filter(allocation_id__in=[a.pk for a in qs])}
        rows = [alloc_row(request, a, extras.get(a.pk)) for a in qs]
        if q.get('ack') :
            rows = [r for r in rows if r['ack_status'] == q.get('ack')]
        if q.get('overdue') in ('1', 'true'):
            rows = [r for r in rows if r['overdue']]
        if hr and q.get('shared') != '0':
            sq = SharedAllocation.objects.all().order_by('-assigned_date')
            vis = visible_asset_ids(request)
            if vis is not None:
                sq = sq.filter(asset_id__in=vis)
            if q.get('open') in ('1', 'true'):
                sq = sq.filter(returned_date__isnull=True)
            if q.get('asset'):
                sq = sq.filter(asset_id=q.get('asset'))
            if not q.get('employee'):
                rows += [shared_row(s) for s in sq]
        return Response(rows)

    @guard
    def partial_update(self, request, pk=None):
        """HR changes the expected return date, accessories or hand-over note of an open allocation."""
        need(request, 'alloc_change')
        a = S.Allocation().objects.select_related('asset', 'employee').filter(pk=pk).first()
        if not a:
            raise S.AssetError('Allocation not found.', status=404)
        get_asset(request, a.asset_id, 'alloc_view')
        x, _ = AllocationExtra.objects.get_or_create(allocation_id=a.pk, defaults={'asset_id': a.asset_id, 'employee_id': a.employee_id, 'ack_status': 'not_required'})
        inp = In(request)
        if inp.has('expected_return_date'):
            d = inp.dt('expected_return_date')
            if d and a.assigned_date and d < a.assigned_date:
                raise S.AssetError('The expected return date is before the allocation date.', 'expected_return_date')
            x.expected_return_date = d
            x.return_reminded_on = None
        for k in ('accessories', 'handover_note'):
            if inp.has(k):
                setattr(x, k, inp.s(k))
        if inp.f('handover_file'):
            x.handover_file = inp.f('handover_file')
        x.save()
        S.log_event(a.asset_id, 'allocation_updated', f'Allocation details updated (expected return {dstr(x.expected_return_date) or "–"})', request.user, a.employee_id,
                    ('allocation', a.pk))
        return Response(alloc_row(request, a, x))

    @action(detail=True, methods=['post'])
    @guard
    def acknowledge(self, request, pk=None):
        """The employee confirms receipt (with condition remarks)."""
        a = S.Allocation().objects.select_related('asset', 'employee').filter(pk=pk).first()
        e = my_emp(request)
        if not a or not e or a.employee_id != e.pk:
            raise S.AssetError('Allocation not found.', status=404)
        x, _ = AllocationExtra.objects.get_or_create(allocation_id=a.pk, defaults={'asset_id': a.asset_id, 'employee_id': a.employee_id})
        inp = In(request)
        S.acknowledge(x, request.user, accept=inp.b('accept', True), notes=inp.s('notes'))
        return Response(alloc_row(request, a, x))


# ------------------------------------------------------------------ transfers
def transfer_row(t):
    a = S.Asset().objects.filter(pk=t.asset_id).first()
    return {'id': t.id, 'number': t.number, 'asset_id': t.asset_id, 'asset': a.name if a else '', 'from_type': t.from_type, 'from': S.party_label(t, 'from'),
            'from_employee_id': t.from_employee_id, 'to_type': t.to_type, 'to': S.party_label(t, 'to'), 'to_employee_id': t.to_employee_id,
            'to_branch_id': t.to_branch_id, 'to_location': t.to_location, 'to_department_id': t.to_department_id,
            'transfer_date': dstr(t.transfer_date), 'reason': t.reason, 'status': t.status, 'status_label': t.get_status_display(),
            'requested_by': S.user_label(t.requested_by_id), 'requested_by_id': t.requested_by_id, 'decided_by': S.user_label(t.decided_by_id),
            'decided_at': t.decided_at.isoformat() if t.decided_at else None, 'decision_note': t.decision_note, 'created_at': t.created_at.isoformat()}


def scoped(request, qs, key, emp_field='employee_id'):
    """HR with the right: their branches; others: their own rows."""
    if can(request, key):
        vis = visible_asset_ids(request)
        return qs if vis is None else qs.filter(asset_id__in=vis)
    e = my_emp(request)
    if not e:
        return qs.none()
    if isinstance(emp_field, (list, tuple)):
        q = Q()
        for f in emp_field:
            q |= Q(**{f: e.pk})
        return qs.filter(q)
    return qs.filter(**{emp_field: e.pk})


def no_self_approval(request, requested_by_id):
    if requested_by_id and requested_by_id == request.user.pk and not is_admin(request):
        raise Denied('You cannot approve a request you raised yourself.')


class TransferViewSet(Base, viewsets.ViewSet):
    @guard
    def list(self, request):
        qs = scoped(request, AssetTransfer.objects.all(), 'transfer_view', ['from_employee_id', 'to_employee_id', 'requested_by_employee_id'])
        q = request.query_params
        if q.get('status'):
            qs = qs.filter(status=q.get('status'))
        if q.get('asset'):
            qs = qs.filter(asset_id=q.get('asset'))
        return Response([transfer_row(t) for t in qs])

    @guard
    def retrieve(self, request, pk=None):
        t = scoped(request, AssetTransfer.objects.all(), 'transfer_view', ['from_employee_id', 'to_employee_id', 'requested_by_employee_id']).filter(pk=pk).first()
        if not t:
            raise S.AssetError('Transfer not found.', status=404)
        return Response(transfer_row(t))

    def _get(self, request, pk, key):
        need(request, key)
        t = scoped(request, AssetTransfer.objects.all(), key).filter(pk=pk).first()
        if not t:
            raise S.AssetError('Transfer not found.', status=404)
        return t

    @action(detail=True, methods=['post'])
    @guard
    def approve(self, request, pk=None):
        t = self._get(request, pk, 'transfer_approve')
        no_self_approval(request, t.requested_by_id)
        if t.to_branch_id:
            check_branch(request, t.to_branch_id, 'to_branch_id') if t.to_type != 'employee' else None
        S.decide_transfer(t, request.user, True, In(request).s('note'))
        t.refresh_from_db()
        return Response(transfer_row(t))

    @action(detail=True, methods=['post'])
    @guard
    def reject(self, request, pk=None):
        t = self._get(request, pk, 'transfer_approve')
        inp = In(request)
        if not inp.s('note'):
            raise S.AssetError('Give the reason for rejecting.', 'note')
        S.decide_transfer(t, request.user, False, inp.s('note'))
        t.refresh_from_db()
        return Response(transfer_row(t))

    @action(detail=True, methods=['post'])
    @guard
    def cancel(self, request, pk=None):
        t = AssetTransfer.objects.filter(pk=pk).first()
        if not t or (t.requested_by_id != request.user.pk and not can(request, 'transfer_approve')):
            raise S.AssetError('Transfer not found.', status=404)
        if t.status != 'pending':
            raise S.AssetError('Only a transfer waiting for approval can be cancelled.')
        t.status = 'cancelled'
        t.save(update_fields=['status'])
        S.log_event(t.asset_id, 'transfer_cancelled', f'Transfer {t.number} cancelled', request.user, t.from_employee_id, ('transfer', t.pk))
        return Response(transfer_row(t))


# ------------------------------------------------------------------ maintenance
def maint_row(r):
    a = S.Asset().objects.filter(pk=r.asset_id).first()
    return {'id': r.id, 'number': r.number, 'asset_id': r.asset_id, 'asset': a.name if a else '', 'schedule': r.schedule_id, 'damage': r.damage_id,
            'kind': r.kind, 'kind_label': r.get_kind_display(), 'vendor': r.vendor, 'description': r.description, 'start_date': dstr(r.start_date),
            'end_date': dstr(r.end_date), 'cost': num(r.cost), 'downtime_hours': r1(r.downtime_hours),
            'reading': r1(r.reading), 'result': r.result, 'result_label': r.get_result_display() if r.result else '',
            'result_notes': r.result_notes, 'status': r.status, 'status_label': r.get_status_display(), 'created_by': S.user_label(r.created_by_id)}


def schedule_row(s):
    a = S.Asset().objects.filter(pk=s.asset_id).first()
    p = AssetProfile.objects.filter(asset_id=s.asset_id).first()
    return {'id': s.id, 'asset': s.asset_id, 'asset_name': a.name if a else '', 'code': p.asset_code if p else '', 'title': s.title,
            'interval_type': s.interval_type, 'interval_value': s.interval_value, 'last_done_date': dstr(s.last_done_date),
            'last_done_reading': r1(s.last_done_reading), 'next_due_date': dstr(s.next_due_date),
            'next_due_reading': r1(s.next_due_reading), 'remind_days_before': s.remind_days_before,
            'vendor': s.vendor, 'estimated_cost': num(s.estimated_cost), 'active': s.active, 'due': S.schedule_due(s, p),
            'meter_reading': r1(p.meter_reading) if p else None}


class MaintenanceViewSet(Base, viewsets.ViewSet):
    @guard
    def list(self, request):
        need(request, 'maint_view')
        qs = scoped(request, MaintenanceRecord.objects.all(), 'maint_view')
        q = request.query_params
        for k in ('status', 'kind'):
            if q.get(k):
                qs = qs.filter(**{k: q.get(k)})
        if q.get('asset'):
            qs = qs.filter(asset_id=q.get('asset'))
        return Response([maint_row(r) for r in qs])

    @guard
    def create(self, request):
        inp = In(request)
        a = inp.i('asset', required=True, label='Asset')
        return AssetViewSet().maintenance(request, pk=a)

    @guard
    def retrieve(self, request, pk=None):
        need(request, 'maint_view')
        r = scoped(request, MaintenanceRecord.objects.all(), 'maint_view').filter(pk=pk).first()
        if not r:
            raise S.AssetError('Maintenance record not found.', status=404)
        row = maint_row(r)
        row['files'] = [file_row(request, f) for f in AssetFile.objects.filter(record_type='maintenance', record_id=r.pk)]
        return Response(row)

    @guard
    def partial_update(self, request, pk=None):
        need(request, 'maint_change')
        r = scoped(request, MaintenanceRecord.objects.all(), 'maint_change').filter(pk=pk).first()
        if not r:
            raise S.AssetError('Maintenance record not found.', status=404)
        if r.status != 'open':
            raise S.AssetError('A completed maintenance record cannot be changed.')
        inp = In(request)
        for k in ('vendor', 'description'):
            if inp.has(k):
                setattr(r, k, inp.s(k))
        if inp.has('cost'):
            c = inp.n('cost', Decimal(0))
            if c < 0:
                raise S.AssetError('The cost cannot be negative.', 'cost')
            r.cost = S.money(c)
        r.save()
        return Response(maint_row(r))

    @action(detail=True, methods=['post'])
    @guard
    def close(self, request, pk=None):
        need(request, 'maint_change')
        r = scoped(request, MaintenanceRecord.objects.all(), 'maint_change').filter(pk=pk).first()
        if not r:
            raise S.AssetError('Maintenance record not found.', status=404)
        inp = In(request)
        r = S.close_maintenance(r, request.user, end_date=inp.dt('end_date'), cost=inp.n('cost'), downtime_hours=inp.n('downtime_hours'),
                                result=inp.s('result') or 'fixed', result_notes=inp.s('result_notes'), reading=inp.n('reading'), cancel=inp.b('cancel'))
        save_files(request, r.asset_id, 'maintenance', 'maintenance', r.pk)
        return Response(maint_row(r))


class ScheduleViewSet(Base, viewsets.ViewSet):
    @guard
    def list(self, request):
        need(request, 'maint_view')
        qs = scoped(request, MaintenanceSchedule.objects.all(), 'maint_view')
        if request.query_params.get('asset'):
            qs = qs.filter(asset_id=request.query_params.get('asset'))
        rows = [schedule_row(s) for s in qs.order_by('next_due_date', 'id')]
        if request.query_params.get('due') in ('1', 'true'):
            rows = [r for r in rows if r['due']]
        return Response(rows)

    def _apply(self, request, s, inp, creating):
        if creating or inp.has('title'):
            if not inp.s('title'):
                raise S.AssetError('Give the plan a title, e.g. "Oil change".', 'title')
            s.title = inp.s('title')[:150]
        if creating or inp.has('interval_type'):
            t = inp.s('interval_type') or 'days'
            if t not in ('days', 'km', 'hours'):
                raise S.AssetError('Repeat every N days, kilometres or running hours.', 'interval_type')
            s.interval_type = t
        if creating or inp.has('interval_value'):
            v = inp.i('interval_value', required=True, label='Interval')
            if v < 1:
                raise S.AssetError('The interval must be at least 1.', 'interval_value')
            s.interval_value = v
        for k in ('last_done_date', 'next_due_date'):
            if inp.has(k):
                setattr(s, k, inp.dt(k))
        if inp.has('last_done_reading'):
            s.last_done_reading = inp.n('last_done_reading')
        if inp.has('remind_days_before'):
            v = inp.i('remind_days_before')
            if v is not None and not (0 <= v <= 365):
                raise S.AssetError('Remind between 0 and 365 days before.', 'remind_days_before')
            s.remind_days_before = v or 0
        if inp.has('vendor'):
            s.vendor = inp.s('vendor')
        if inp.has('estimated_cost'):
            c = inp.n('estimated_cost', Decimal(0))
            if c < 0:
                raise S.AssetError('The cost cannot be negative.', 'estimated_cost')
            s.estimated_cost = S.money(c)
        if inp.has('active'):
            s.active = inp.b('active', True)
        if not inp.s('next_due_date') or s.interval_type != 'days':
            S.compute_next_due(s, AssetProfile.objects.filter(asset_id=s.asset_id).first())

    @guard
    def create(self, request):
        need(request, 'maint_change')
        inp = In(request)
        a = get_asset(request, inp.i('asset', required=True, label='Asset'))
        if a.status == 'disposed':
            raise S.AssetError('This asset has been disposed.', 'asset')
        s = MaintenanceSchedule(asset_id=a.pk)
        self._apply(request, s, inp, True)
        s.save()
        S.log_event(a.pk, 'schedule_added', f'Maintenance plan "{s.title}" every {s.interval_value} {s.get_interval_type_display().lower()}', request.user)
        return Response(schedule_row(s), status=201)

    @guard
    def partial_update(self, request, pk=None):
        need(request, 'maint_change')
        s = scoped(request, MaintenanceSchedule.objects.all(), 'maint_change').filter(pk=pk).first()
        if not s:
            raise S.AssetError('Maintenance plan not found.', status=404)
        self._apply(request, s, In(request), False)
        s.save()
        return Response(schedule_row(s))

    @guard
    def destroy(self, request, pk=None):
        need(request, 'maint_change')
        s = scoped(request, MaintenanceSchedule.objects.all(), 'maint_change').filter(pk=pk).first()
        if not s:
            raise S.AssetError('Maintenance plan not found.', status=404)
        S.log_event(s.asset_id, 'schedule_removed', f'Maintenance plan "{s.title}" removed', request.user)
        s.delete()
        return Response(status=204)


# ------------------------------------------------------------------ returns
def return_row(r):
    a = S.Asset().objects.filter(pk=r.asset_id).first()
    return {'id': r.id, 'number': r.number, 'asset_id': r.asset_id, 'asset': a.name if a else '', 'employee_id': r.employee_id, 'employee': S.emp_name(r.employee_id),
            'status': r.status, 'status_label': r.get_status_display(), 'request_note': r.request_note, 'preferred_date': dstr(r.preferred_date),
            'return_date': dstr(r.return_date), 'condition': r.condition, 'accessories_returned': r.accessories_returned, 'missing_items': r.missing_items,
            'damage_found': r.damage_found, 'damage_description': r.damage_description, 'damage_estimate': num(r.damage_estimate), 'damage_id': r.damage_id,
            'data_wiped': r.data_wiped, 'received_by': S.user_label(r.received_by_id), 'notes': r.notes, 'created_at': r.created_at.isoformat()}


class ReturnViewSet(Base, viewsets.ViewSet):
    @guard
    def list(self, request):
        qs = scoped(request, AssetReturn.objects.all(), 'alloc_view')
        if request.query_params.get('status'):
            qs = qs.filter(status=request.query_params.get('status'))
        return Response([return_row(r) for r in qs])

    @guard
    def create(self, request):
        """Self-service: ask to return an asset that is with me."""
        inp = In(request)
        e = my_emp(request)
        aid = inp.i('asset', required=True, label='Asset')
        if not e:
            raise Denied('Only employees can ask for a return. HR records returns from the asset page.')
        a = get_asset(request, aid, ess_ok=True)
        r = S.request_return(a, request.user, e.pk, inp.s('note'), inp.dt('preferred_date'))
        return Response(return_row(r), status=201)

    @action(detail=True, methods=['post'])
    @guard
    def complete(self, request, pk=None):
        need(request, 'alloc_change')
        r = scoped(request, AssetReturn.objects.all(), 'alloc_change').filter(pk=pk, status='requested').first()
        if not r:
            raise S.AssetError('Open return request not found.', status=404)
        a = get_asset(request, r.asset_id, 'alloc_change')
        inp = In(request)
        r = S.complete_return(a, request.user, return_date=inp.dt('return_date'), condition=inp.s('condition') or 'healthy',
                              accessories_returned=inp.b('accessories_returned', True), missing_items=inp.s('missing_items'),
                              damage_found=inp.b('damage_found'), damage_description=inp.s('damage_description'),
                              damage_estimate=inp.n('damage_estimate', Decimal(0)), data_wiped=inp.b('data_wiped'), notes=inp.s('notes'), return_request=r)
        return Response(return_row(r))

    @action(detail=True, methods=['post'])
    @guard
    def cancel(self, request, pk=None):
        r = AssetReturn.objects.filter(pk=pk, status='requested').first()
        e = my_emp(request)
        if not r or not ((e and r.employee_id == e.pk) or can(request, 'alloc_change')):
            raise S.AssetError('Open return request not found.', status=404)
        r.status = 'cancelled'
        r.save(update_fields=['status'])
        S.log_event(r.asset_id, 'return_cancelled', f'Return request {r.number} cancelled', request.user, r.employee_id, ('return', r.pk))
        return Response(return_row(r))


# ------------------------------------------------------------------ damage / loss
def instalments_of(kind, obj):
    return [{'id': i.id, 'month': dstr(i.month), 'amount': num(i.amount), 'status': i.status, 'status_label': i.get_status_display(),
             'payroll_run_id': i.payroll_run_id, 'payslip_id': i.payslip_id, 'done_on': dstr(i.done_on)}
            for i in RecoveryInstalment.objects.filter(source_type=kind, source_id=obj.pk)]


def incident_row(request, o, kind):
    a = S.Asset().objects.filter(pk=o.asset_id).first()
    row = {'id': o.id, 'number': o.number, 'kind_of_record': kind, 'asset_id': o.asset_id, 'asset': a.name if a else '', 'employee_id': o.employee_id,
           'employee': S.emp_name(o.employee_id), 'description': o.description, 'responsibility': o.responsibility,
           'responsibility_label': o.get_responsibility_display(), 'recovery_amount': num(o.recovery_amount), 'recovery_method': o.recovery_method,
           'recovery_method_label': o.get_recovery_method_display(), 'instalments': o.instalments, 'first_deduction_month': dstr(o.first_deduction_month),
           'recovered_amount': num(o.recovered_amount), 'outstanding': num(S.money(o.recovery_amount) - S.money(o.recovered_amount)) if o.recovery_method == 'payroll' else '0.00',
           'status': o.status, 'status_label': o.get_status_display(), 'source': o.source, 'reported_by': S.user_label(o.reported_by_id),
           'reported_by_id': o.reported_by_id, 'decided_by': S.user_label(o.decided_by_id), 'decided_at': o.decided_at.isoformat() if o.decided_at else None,
           'decision_note': o.decision_note, 'created_at': o.created_at.isoformat(), 'schedule': instalments_of(kind, o),
           'files': [file_row(request, f) for f in AssetFile.objects.filter(record_type=kind, record_id=o.pk)]}
    if kind == 'damage':
        row.update({'damage_date': dstr(o.damage_date), 'severity': o.severity, 'severity_label': o.get_severity_display(), 'repair_cost': num(o.repair_cost),
                    'return_id': o.return_id})
    else:
        row.update({'loss_date': dstr(o.loss_date), 'kind': o.kind, 'kind_label': o.get_kind_display(), 'place': o.place,
                    'police_report_no': o.police_report_no, 'police_report_file': url(request, o.police_report_file),
                    'investigation_status': o.investigation_status, 'investigation_label': o.get_investigation_status_display(),
                    'investigation_notes': o.investigation_notes, 'book_value': num(o.book_value), 'found_on': dstr(o.found_on)})
    return row


class _IncidentViewSet(Base, viewsets.ViewSet):
    model = None
    kind = ''
    view_key = add_key = approve_key = ''

    def _qs(self, request):
        return scoped(request, self.model.objects.all(), self.view_key)

    def _one(self, request, pk):
        o = self._qs(request).filter(pk=pk).first()
        if not o:
            raise S.AssetError('Record not found.', status=404)
        return o

    @guard
    def list(self, request):
        qs = self._qs(request)
        q = request.query_params
        if q.get('status'):
            qs = qs.filter(status=q.get('status'))
        if q.get('asset'):
            qs = qs.filter(asset_id=q.get('asset'))
        if q.get('employee'):
            qs = qs.filter(employee_id=q.get('employee'))
        return Response([incident_row(request, o, self.kind) for o in qs])

    @guard
    def retrieve(self, request, pk=None):
        return Response(incident_row(request, self._one(request, pk), self.kind))

    @guard
    def create(self, request):
        inp = In(request)
        aid = inp.i('asset', required=True, label='Asset')
        fn = AssetViewSet().damage if self.kind == 'damage' else AssetViewSet().loss
        return fn(request, pk=aid)

    def _decide(self, request, pk, approve):
        need(request, self.approve_key)
        o = self._one(request, pk)
        no_self_approval(request, o.reported_by_id)
        inp = In(request)
        data = {k: inp.d.get(k) for k in ('responsibility', 'recovery_method', 'instalments', 'note', 'investigation_status')}
        data['recovery_amount'] = inp.n('recovery_amount')
        data['repair_cost'] = inp.n('repair_cost')
        data['first_deduction_month'] = inp.dt('first_deduction_month')
        data['employee_id'] = inp.i('employee')
        if data['employee_id']:
            check_employee(request, data['employee_id'])
        if data.get('instalments') not in (None, ''):
            data['instalments'] = inp.i('instalments')
        if not approve and not inp.s('note'):
            raise S.AssetError('Give the reason for rejecting.', 'note')
        fn = S.decide_damage if self.kind == 'damage' else S.decide_loss
        o = fn(o, request.user, approve, data)
        return Response(incident_row(request, o, self.kind))

    @action(detail=True, methods=['post'])
    @guard
    def approve(self, request, pk=None):
        return self._decide(request, pk, True)

    @action(detail=True, methods=['post'])
    @guard
    def reject(self, request, pk=None):
        return self._decide(request, pk, False)

    @action(detail=True, methods=['post'], url_path='cash-received')
    @guard
    def cash_received(self, request, pk=None):
        """The employee paid the rest in cash: pending instalments are cancelled and the record closed."""
        need(request, self.approve_key)
        o = self._one(request, pk)
        if o.status != 'recovering':
            raise S.AssetError('Only a record being recovered can be settled in cash.')
        left = RecoveryInstalment.objects.filter(source_type=self.kind, source_id=o.pk, status='pending')
        amt = S.money(left.aggregate(s=Sum('amount'))['s'] or 0)
        left.update(status='cancelled', done_on=S.today())
        o.recovered_amount = S.money(o.recovered_amount) + amt
        o.status = 'closed'
        o.save(update_fields=['recovered_amount', 'status'])
        S.log_event(o.asset_id, 'recovered', f'{o.number}: {amt} received in cash – fully recovered', request.user, o.employee_id, (self.kind, o.pk))
        return Response(incident_row(request, o, self.kind))


class DamageViewSet(_IncidentViewSet):
    model, kind = AssetDamage, 'damage'
    view_key, add_key, approve_key = 'damage_view', 'damage_add', 'damage_approve'


class LossViewSet(_IncidentViewSet):
    model, kind = AssetLoss, 'loss'
    view_key, add_key, approve_key = 'loss_view', 'loss_add', 'loss_approve'

    @action(detail=True, methods=['post'])
    @guard
    def found(self, request, pk=None):
        need(request, 'loss_add')
        o = self._one(request, pk)
        inp = In(request)
        o = S.mark_found(o, request.user, inp.dt('found_on'), inp.s('note'))
        return Response(incident_row(request, o, 'loss'))

    @action(detail=True, methods=['post'])
    @guard
    def investigation(self, request, pk=None):
        need(request, 'loss_add')
        o = self._one(request, pk)
        inp = In(request)
        st = inp.s('investigation_status')
        if st not in dict(AssetLoss.INVESTIGATION) or st == 'closed_found':
            raise S.AssetError('Choose open, under investigation or closed – not found. Use "Mark as found" when the asset is back.', 'investigation_status')
        o.investigation_status = st
        if inp.s('notes'):
            o.investigation_notes = ((o.investigation_notes + '\n') if o.investigation_notes else '') + inp.s('notes')
        if inp.has('police_report_no'):
            o.police_report_no = inp.s('police_report_no')
        if inp.f('police_report_file'):
            o.police_report_file = inp.f('police_report_file')
        o.save()
        S.log_event(o.asset_id, 'investigation', f'{o.number}: investigation {o.get_investigation_status_display().lower()}' + (f' – {inp.s("notes")}' if inp.s('notes') else ''),
                    request.user, o.employee_id, ('loss', o.pk))
        return Response(incident_row(request, o, 'loss'))


# ------------------------------------------------------------------ disposals
def disposal_row(d):
    a = S.Asset().objects.filter(pk=d.asset_id).first()
    p = AssetProfile.objects.filter(asset_id=d.asset_id).first()
    return {'id': d.id, 'number': d.number, 'asset_id': d.asset_id, 'asset': a.name if a else '', 'code': p.asset_code if p else '', 'method': d.method,
            'method_label': d.get_method_display(), 'disposal_date': dstr(d.disposal_date), 'value': num(d.value), 'buyer': d.buyer, 'reason': d.reason,
            'purchase_cost': num(p.purchase_cost) if p else None, 'book_value': num(d.book_value), 'gain_loss': num(d.gain_loss),
            'result': 'gain' if d.gain_loss > 0 else ('loss' if d.gain_loss < 0 else 'none'), 'status': d.status, 'status_label': d.get_status_display(),
            'requested_by': S.user_label(d.requested_by_id), 'requested_by_id': d.requested_by_id, 'decided_by': S.user_label(d.decided_by_id),
            'decided_at': d.decided_at.isoformat() if d.decided_at else None, 'decision_note': d.decision_note, 'created_at': d.created_at.isoformat()}


class DisposalViewSet(Base, viewsets.ViewSet):
    def _qs(self, request):
        need(request, 'disposal_view')
        return scoped(request, AssetDisposal.objects.all(), 'disposal_view')

    @guard
    def list(self, request):
        qs = self._qs(request)
        if request.query_params.get('status'):
            qs = qs.filter(status=request.query_params.get('status'))
        return Response([disposal_row(d) for d in qs])

    @guard
    def create(self, request):
        aid = In(request).i('asset', required=True, label='Asset')
        return AssetViewSet().dispose(request, pk=aid)

    @guard
    def retrieve(self, request, pk=None):
        d = self._qs(request).filter(pk=pk).first()
        if not d:
            raise S.AssetError('Disposal not found.', status=404)
        return Response(disposal_row(d))

    def _decide(self, request, pk, approve):
        need(request, 'disposal_approve')
        d = self._qs(request).filter(pk=pk).first()
        if not d:
            raise S.AssetError('Disposal not found.', status=404)
        no_self_approval(request, d.requested_by_id)
        note = In(request).s('note')
        if not approve and not note:
            raise S.AssetError('Give the reason for rejecting.', 'note')
        d = S.decide_disposal(d, request.user, approve, note)
        return Response(disposal_row(d))

    @action(detail=True, methods=['post'])
    @guard
    def approve(self, request, pk=None):
        return self._decide(request, pk, True)

    @action(detail=True, methods=['post'])
    @guard
    def reject(self, request, pk=None):
        return self._decide(request, pk, False)

    @action(detail=True, methods=['post'])
    @guard
    def cancel(self, request, pk=None):
        d = self._qs(request).filter(pk=pk, status='pending').first()
        if not d or (d.requested_by_id != request.user.pk and not can(request, 'disposal_approve')):
            raise S.AssetError('Pending disposal not found.', status=404)
        d.status = 'cancelled'
        d.save(update_fields=['status'])
        S.log_event(d.asset_id, 'disposal_cancelled', f'Disposal {d.number} cancelled', request.user, None, ('disposal', d.pk))
        return Response(disposal_row(d))


# ------------------------------------------------------------------ recoveries, clearance, self-service, setup
class RecoveryView(Base, APIView):
    @guard
    def get(self, request):
        qs = RecoveryInstalment.objects.all()
        if not can(request, 'recovery_view'):
            e = my_emp(request)
            qs = qs.filter(employee_id=e.pk if e else -1)
        else:
            br = user_branches(request)
            if br is not None:
                qs = qs.filter(employee_id__in=S.Emp().objects.filter(emp_branch_id__in=br).values('id'))
        q = request.query_params
        if q.get('status'):
            qs = qs.filter(status=q.get('status'))
        if q.get('employee'):
            qs = qs.filter(employee_id=q.get('employee'))
        if q.get('month'):
            m = parse_date(q.get('month') + ('-01' if len(q.get('month')) == 7 else ''))
            if m:
                qs = qs.filter(month=S.first_of_month(m))
        rows = []
        for i in qs:
            src = (AssetDamage if i.source_type == 'damage' else AssetLoss).objects.filter(pk=i.source_id).first()
            rows.append({'id': i.id, 'employee_id': i.employee_id, 'employee': S.emp_name(i.employee_id), 'month': dstr(i.month), 'amount': num(i.amount),
                         'status': i.status, 'status_label': i.get_status_display(), 'source_type': i.source_type, 'source_id': i.source_id,
                         'number': src.number if src else '', 'asset_id': src.asset_id if src else None, 'payroll_run_id': i.payroll_run_id})
        return Response(rows)


def leaving_employees(request):
    """Employees with an approved resignation / end of service (not yet paid)."""
    R_ = S.M('EmpManagement', 'EmployeeResignation')
    ids = set(R_.objects.filter(status__iexact='approved').values_list('employee_id', flat=True))
    try:
        E = S.M('EmpManagement', 'EndOfService')
        ids |= set(E.objects.exclude(status='paid').values_list('resignation__employee_id', flat=True))
        paid = set(E.objects.filter(status='paid').values_list('resignation__employee_id', flat=True))
    except LookupError:
        paid = set()
    qs = S.Emp().objects.filter(pk__in=ids - paid)
    br = user_branches(request)
    if br is not None:
        qs = qs.filter(emp_branch_id__in=br)
    return qs


class ClearanceView(Base, APIView):
    @guard
    def get(self, request, emp_id=None):
        need(request, 'clearance_view')
        R_ = S.M('EmpManagement', 'EmployeeResignation')
        if emp_id:
            e = check_employee(request, int(emp_id))
            c = S.clearance(e.pk)
            return Response({**self._emp(e, R_), **c, 'items': [{**i, 'since': dstr(i.get('since')) if i.get('since') else None,
                                                                    'amount': num(i['amount']) if 'amount' in i else None} for i in c['items']],
                             'recovery_due': num(c['recovery_due']), 'waiver': {**c['waiver'], 'at': c['waiver']['at'].isoformat()} if c['waiver'] else None})
        rows = []
        for e in leaving_employees(request):
            c = S.clearance(e.pk)
            rows.append({**self._emp(e, R_), 'open_items': len(c['items']), 'assets': sum(1 for i in c['items'] if i['kind'] in ('asset', 'custody')),
                         'recovery_due': num(c['recovery_due']), 'blocked': c['blocked'], 'waived': c['waived']})
        return Response(rows)

    @staticmethod
    def _emp(e, R_):
        r = R_.objects.filter(employee=e).order_by('-id').first()
        eos = None
        try:
            eos = S.M('EmpManagement', 'EndOfService').objects.filter(resignation__employee=e).order_by('-id').first()
        except LookupError:
            pass
        return {'employee_id': e.pk, 'employee': S.person(e), 'branch': e.emp_branch_id.branch_name if e.emp_branch_id_id else '',
                'last_working_date': dstr(getattr(r, 'last_working_date', None)) if r else None,
                'resignation_status': r.status if r else None, 'eos_id': eos.pk if eos else None, 'eos_status': eos.status if eos else None}

    @guard
    def post(self, request, emp_id=None):
        """action=waive (with reason) | settle (move remaining deductions to the final settlement) | unwaive."""
        e = check_employee(request, int(emp_id))
        inp = In(request)
        act = inp.s('action')
        if act == 'waive':
            need(request, 'waive')
            if not inp.s('reason'):
                raise S.AssetError('Give the reason for releasing the clearance.', 'reason')
            ClearanceWaiver.objects.create(employee_id=e.pk, reason=inp.s('reason'), waived_by_id=request.user.pk)
            for i in S.clearance(e.pk)['items']:
                if i.get('asset_id'):
                    S.log_event(i['asset_id'], 'clearance_waived', f'Exit clearance of {S.person(e)} released: {inp.s("reason")}', request.user, e.pk)
        elif act == 'unwaive':
            need(request, 'waive')
            ClearanceWaiver.objects.filter(employee_id=e.pk, active=True).update(active=False)
        elif act == 'settle':
            need(request, 'clearance_change')
            amt = S.settle_in_final_settlement(e.pk, request.user)
            if not amt:
                raise S.AssetError('There is no recovery left to move to the final settlement.')
        else:
            raise S.AssetError('Unknown action. Use waive, unwaive or settle.', 'action')
        return self.get(request, emp_id)


class MyAssetsView(Base, APIView):
    """Self-service home: assets with me, receipts to confirm, my requests and my deductions."""

    @guard
    def get(self, request):
        e = my_emp(request)
        if not e:
            return Response({'employee': '', 'assets': [], 'pending_ack': 0, 'transfers': [], 'returns': [], 'damages': [], 'losses': [],
                             'recoveries': [], 'past': []})
        allocs = S.Allocation().objects.filter(employee_id=e.pk, returned_date__isnull=True).select_related('asset', 'asset__asset_type')
        extras = {x.allocation_id: x for x in AllocationExtra.objects.filter(allocation_id__in=[a.pk for a in allocs])}
        assets = []
        for a in allocs:
            p = S.ensure_profile(a.asset)
            x = extras.get(a.pk)
            assets.append({'allocation_id': a.pk, 'asset_id': a.asset_id, 'name': a.asset.name, 'type': a.asset.asset_type.name, 'code': p.asset_code,
                           'serial_number': a.asset.serial_number, 'model': a.asset.model, 'condition': a.asset.condition,
                           'status': S.effective_status(a.asset, p), 'assigned_date': dstr(a.assigned_date),
                           'expected_return_date': dstr(x.expected_return_date) if x else None, 'accessories': x.accessories if x else '',
                           'handover_note': x.handover_note if x else '', 'ack_status': x.ack_status if x else 'not_required',
                           'warranty_end': dstr(p.warranty_end), 'return_requested': AssetReturn.objects.filter(allocation_id=a.pk, status='requested').exists(),
                           'transfer_pending': AssetTransfer.objects.filter(asset_id=a.asset_id, status='pending').exists()})
        for s in SharedAllocation.objects.filter(custodian_employee_id=e.pk, returned_date__isnull=True):
            a = S.Asset().objects.select_related('asset_type').get(pk=s.asset_id)
            p = S.ensure_profile(a)
            assets.append({'allocation_id': None, 'shared_id': s.pk, 'asset_id': a.pk, 'name': a.name, 'type': a.asset_type.name, 'code': p.asset_code,
                           'serial_number': a.serial_number, 'model': a.model, 'condition': a.condition, 'status': S.effective_status(a, p),
                           'assigned_date': dstr(s.assigned_date), 'custodian_of': S.holder_label_shared(s), 'ack_status': 'not_required'})
        return Response({
            'employee': S.person(e), 'assets': assets, 'pending_ack': sum(1 for x in assets if x['ack_status'] == 'pending'),
            'transfers': [transfer_row(t) for t in AssetTransfer.objects.filter(Q(from_employee_id=e.pk) | Q(to_employee_id=e.pk))[:20]],
            'returns': [return_row(r) for r in AssetReturn.objects.filter(employee_id=e.pk)[:20]],
            'damages': [incident_row(request, d, 'damage') for d in AssetDamage.objects.filter(employee_id=e.pk)[:20]],
            'losses': [incident_row(request, d, 'loss') for d in AssetLoss.objects.filter(employee_id=e.pk)[:20]],
            'recoveries': [{'month': dstr(i.month), 'amount': num(i.amount), 'status': i.status, 'status_label': i.get_status_display()}
                           for i in RecoveryInstalment.objects.filter(employee_id=e.pk)],
            'past': [{'asset_id': a.asset_id, 'name': a.asset.name, 'assigned_date': dstr(a.assigned_date), 'returned_date': dstr(a.returned_date)}
                     for a in S.Allocation().objects.filter(employee_id=e.pk, returned_date__isnull=False).select_related('asset').order_by('-returned_date')[:20]],
        })


class TypeConfigView(Base, APIView):
    """Asset code prefix, next number and default depreciation per asset type."""

    @guard
    def get(self, request):
        AT = S.M('OrganisationManager', 'AssetType')
        rows = []
        for t in AT.objects.order_by('name'):
            c = S.type_config(t.pk)
            rows.append({'asset_type': t.pk, 'name': t.name, 'code_prefix': c.code_prefix, 'next_number': c.next_number,
                         'depreciation_method': c.depreciation_method, 'useful_life_months': c.useful_life_months, 'salvage_percent': str(c.salvage_percent),
                         'maintenance_interval_days': c.maintenance_interval_days, 'assets': t.assets.count(),
                         'custom_fields': [{'id': c.id, 'custom_field': c.custom_field, 'data_type': c.data_type or 'text',
                                            'options': c.dropdown_values or c.radio_values or c.checkbox_values or []}
                                           for c in t.custom_fields.order_by('id')]})
        return Response(rows)

    @guard
    def patch(self, request):
        need(request, 'settings')
        inp = In(request)
        c = S.type_config(inp.i('asset_type', required=True, label='Asset type'))
        if inp.has('code_prefix'):
            pre = inp.s('code_prefix').upper()
            if not pre or not pre.replace('-', '').isalnum() or len(pre) > 12:
                raise S.AssetError('Use 1–12 letters or digits for the prefix, e.g. LAP.', 'code_prefix')
            c.code_prefix = pre
        if inp.has('next_number'):
            n = inp.i('next_number')
            if not n or n < 1:
                raise S.AssetError('The next number must be 1 or more.', 'next_number')
            c.next_number = n
        if inp.has('depreciation_method'):
            m = inp.s('depreciation_method')
            if m not in ('none', 'straight_line', 'declining'):
                raise S.AssetError('Choose no depreciation, straight line or declining balance.', 'depreciation_method')
            c.depreciation_method = m
        if inp.has('useful_life_months'):
            n = inp.i('useful_life_months')
            if not n or not (1 <= n <= 600):
                raise S.AssetError('Useful life must be between 1 and 600 months.', 'useful_life_months')
            c.useful_life_months = n
        if inp.has('salvage_percent'):
            v = inp.n('salvage_percent', Decimal(0))
            if not (0 <= v <= 100):
                raise S.AssetError('Salvage is a % between 0 and 100.', 'salvage_percent')
            c.salvage_percent = v
        if inp.has('maintenance_interval_days'):
            c.maintenance_interval_days = inp.i('maintenance_interval_days')
        c.save()
        return self.get(request)


class SettingsView(Base, APIView):
    def _out(self, s):
        return {'currency': s.currency, 'code_padding': s.code_padding, 'warranty_reminder_days': s.warranty_reminder_days,
                'require_acknowledgement': s.require_acknowledgement, 'block_final_settlement': s.block_final_settlement,
                'max_instalments': s.max_instalments}

    @guard
    def get(self, request):
        return Response(self._out(AssetPlusSettings.get()))

    @guard
    def patch(self, request):
        need(request, 'settings')
        s = AssetPlusSettings.get()
        inp = In(request)
        if inp.has('currency'):
            c = inp.s('currency').upper()
            if len(c) != 3 or not c.isalpha():
                raise S.AssetError('Use a 3-letter currency code such as AED.', 'currency')
            s.currency = c
        for k, lo, hi in (('code_padding', 1, 8), ('warranty_reminder_days', 0, 365), ('max_instalments', 1, 60)):
            if inp.has(k):
                v = inp.i(k)
                if v is None or not (lo <= v <= hi):
                    raise S.AssetError(f'{k.replace("_", " ").capitalize()} must be between {lo} and {hi}.', k)
                setattr(s, k, v)
        for k in ('require_acknowledgement', 'block_final_settlement'):
            if inp.has(k):
                setattr(s, k, inp.b(k))
        s.save()
        return Response(self._out(s))

    put = patch


class EventsView(Base, APIView):
    """Company-wide activity feed (latest events) for the home page."""

    @guard
    def get(self, request):
        need(request, 'master_view')
        qs = AssetEvent.objects.all()
        vis = visible_asset_ids(request)
        if vis is not None:
            qs = qs.filter(asset_id__in=vis)
        names = dict(S.Asset().objects.values_list('id', 'name'))
        rows = history_rows(qs[:int(request.query_params.get('limit') or 30)])
        for r, e in zip(rows, qs[:len(rows)]):
            r['asset_id'], r['asset'] = e.asset_id, names.get(e.asset_id, '')
        return Response(rows)


class LookupsView(Base, APIView):
    """Pick lists for the asset screens. HR: employees / branches / departments they manage; employees: colleagues of their branch."""

    @guard
    def get(self, request):
        E = S.Emp()
        hr = can(request, 'master_view') or can(request, 'alloc_view')
        qs = E.objects.filter(is_active=True).order_by('emp_first_name', 'emp_last_name')
        br = user_branches(request)
        if hr:
            if br is not None:
                qs = qs.filter(emp_branch_id__in=br)
        else:
            e = my_emp(request)
            qs = qs.filter(emp_branch_id=e.emp_branch_id_id).exclude(pk=e.pk) if e else qs.none()
        out = {'employees': [{'id': e.pk, 'name': S.person(e), 'branch_id': e.emp_branch_id_id} for e in qs.only('id', 'emp_first_name', 'emp_last_name', 'emp_code', 'emp_branch_id')]}
        if hr:
            B_ = S.M('OrganisationManager', 'brnch_mstr').objects.order_by('branch_name')
            if br is not None:
                B_ = B_.filter(pk__in=br)
            out['branches'] = [{'id': b.pk, 'name': b.branch_name} for b in B_]
            out['departments'] = [{'id': d.pk, 'name': d.dept_name} for d in S.M('OrganisationManager', 'dept_master').objects.order_by('dept_name')]
            out['types'] = [{'id': t.pk, 'name': t.name} for t in S.M('OrganisationManager', 'AssetType').objects.order_by('name')]
        out['rights'] = {k: can(request, k) for k in R}
        out['employee_id'] = getattr(my_emp(request), 'pk', None)
        return Response(out)
