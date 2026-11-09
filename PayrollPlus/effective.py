# v1.67.12 (effective): new module
"""v1.67.12 (effective, item 64) – dated versions ("effective from") of the setup records that calculations read:

  kind               record                                  children kept with it
  leave_policy       LeavePolicy.LeavePolicy                 its lines (days a year, accrual, first-year rule, limits …)
  leave_type         calendars.leave_type                    its pay rules (LeavePayRule)
  overtime_policy    calendars.OvertimePolicy                its rules (OvertimeRule)
  gratuity           OrganisationManager.GratuityTable       the whole table is one record (object_id 0)
  air_ticket_policy  PayrollManagement.AirTicketPolicy       its rules (AirTicketRule)
  pay_structure      PayrollManagement.PayStructure          –
  attendance_rule    AttendancePlus.AttendanceRule           –

How a dated change is made: the edit is saved with `effective_from=yyyy-mm-dd` in the query string (the setup forms send it when
the "Effective from" date is filled in). The record keeps what was in force before as a version "before the first dated change"
(01/01/1900) and the new values as the version from that date. Without a date an edit is a correction of the rule in force today
(of every period when the record has no dated versions – as before 1.67.12).

How the versions are used: `as_of(kind, obj, on)` gives the record as it was on `on` (default: the rule day of CountryPack –
the 1st of the pay period in a payroll run, the last working day in an end of service, the month in a leave accrual, else today);
`gratuity_rows(on)` the gratuity table of that day; `children(obj, name)` the lines / rules of that version. A record without
versions is returned as it is (no query beyond one cached look-up).
"""
import copy
import datetime as _dt
import json
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from types import SimpleNamespace

from django.apps import apps
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection
from django.db.models.signals import post_delete, post_save, pre_save

log = logging.getLogger(__name__)
M = apps.get_model
BASE_DAY = _dt.date(1900, 1, 1)          # "before the first dated change"
_FORCED: ContextVar = ContextVar('v16712_effective_from', default=None)

# kind → (model label, [(child model label, fk attname to the record, name)], fields left out of the comparison)
KINDS = {
    'leave_policy': ('LeavePolicy.LeavePolicy', [('LeavePolicy.LeavePolicyLine', 'policy_id', 'lines')]),
    'leave_type': ('calendars.leave_type', [('calendars.LeavePayRule', 'leave_type_id', 'pay_rules')]),
    'overtime_policy': ('calendars.OvertimePolicy', [('calendars.OvertimeRule', 'policy_id', 'rules')]),
    'gratuity': ('OrganisationManager.GratuityTable', []),
    'air_ticket_policy': ('PayrollManagement.AirTicketPolicy', [('PayrollManagement.AirTicketRule', 'policy_id', 'rules')]),
    'pay_structure': ('PayrollManagement.PayStructure', []),
    'attendance_rule': ('AttendancePlus.AttendanceRule', []),
}
TABLE_KINDS = {'gratuity'}               # one record for the whole table
SKIP = {'id', 'created_at', 'updated_at', 'created_by', 'created_by_id'}


def _model(label):
    try:
        return M(label)
    except LookupError:
        return None


# ------------------------------------------------------------------ snapshots
def _fields(model):
    return [f for f in model._meta.concrete_fields]


def row_data(obj):
    """The concrete fields of a row as JSON values (dates as yyyy-mm-dd, decimals as text, durations ISO)."""
    out = {}
    for f in _fields(type(obj)):
        out[f.attname] = f.value_from_object(obj)
    return json.loads(json.dumps(out, cls=DjangoJSONEncoder))


def snapshot(kind, object_id):
    """The record (and its children) as stored now, or None when it does not exist."""
    label, kids = KINDS[kind]
    Model = _model(label)
    if Model is None:
        return None
    if kind in TABLE_KINDS:
        return {'rows': [row_data(r) for r in Model.objects.order_by('minimum_value', 'id')]}
    obj = Model.objects.filter(pk=object_id).first()
    if obj is None:
        return None
    data = {'record': row_data(obj)}
    for clabel, fk, name in kids:
        C = _model(clabel)
        if C is not None:
            data[name] = [row_data(c) for c in C.objects.filter(**{fk: object_id}).order_by('id')]
    return data


# ------------------------------------------------------------------ which record a save belongs to
_INDEX = None


def _index():
    """{model label (lower): (kind, None | fk attname)} – the records and their children."""
    global _INDEX
    if _INDEX is None:
        ix = {}
        for kind, (label, kids) in KINDS.items():
            ix[label.lower()] = (kind, None)
            for clabel, fk, _ in kids:
                ix[clabel.lower()] = (kind, fk)
        _INDEX = ix
    return _INDEX


def _owner(instance):
    m = type(instance)._meta
    hit = _index().get(f'{m.app_label}.{m.model_name}'.lower())
    if hit is None:
        return None, None
    kind, fk = hit
    if kind in TABLE_KINDS:
        return kind, 0
    oid = instance.pk if fk is None else getattr(instance, fk, None)
    return (kind, oid) if oid else (None, None)


# ------------------------------------------------------------------ the date of a save
def requested_date():
    """The "effective from" day of the save going on: a test / job sets it with `dated()`; a web request sends ?effective_from=."""
    d = _FORCED.get()
    if d is not None:
        return d
    try:
        from Chatter.tracking import current_request
        req = current_request()
    except Exception:
        req = None
    if req is None:
        return None
    raw = (req.GET.get('effective_from') or req.META.get('HTTP_X_ZEO_EFFECTIVE_FROM') or '').strip()
    if not raw:
        return None
    try:
        return _dt.date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _user_id():
    try:
        from Chatter.tracking import current_request
        req = current_request()
        u = getattr(req, 'user', None)
        return u.pk if getattr(u, 'is_authenticated', False) else None
    except Exception:
        return None


@contextmanager
def dated(d):
    """with dated(date(2027, 1, 1)): obj.save() – the save is a change from that day (jobs, tests)."""
    tok = _FORCED.set(d)
    try:
        yield d
    finally:
        _FORCED.reset(tok)


# ------------------------------------------------------------------ the versions (cached a few seconds per company)
_HAVE = {}   # (schema, kind) → (time, {object_id: [(effective_from, data)] newest first})
TTL = 5.0


def _forget(kind=None):
    s = getattr(connection, 'schema_name', 'public')
    for k in list(_HAVE):
        if k[0] == s and (kind is None or k[1] == kind):
            _HAVE.pop(k, None)


def _versions(kind):
    s = getattr(connection, 'schema_name', 'public')
    hit = _HAVE.get((s, kind))
    now = time.monotonic()
    if hit and now - hit[0] < TTL:
        return hit[1]
    out = {}
    try:
        RV = M('PayrollPlus', 'RuleVersion')
        for oid, d, data in RV.objects.filter(kind=kind).order_by('object_id', '-effective_from').values_list('object_id', 'effective_from', 'data'):
            out.setdefault(oid, []).append((d, data))
    except Exception:   # table not migrated yet
        out = {}
    _HAVE[(s, kind)] = (now, out)
    return out


def has_versions(kind, object_id):
    return bool(_versions(kind).get(object_id))


def version_data(kind, object_id, on=None):
    """(effective_from, data) of the version in force on `on`, or (None, None) when the record has no versions (or none so early)."""
    lst = _versions(kind).get(object_id)
    if not lst:
        return None, None
    on = _day(on)
    for d, data in lst:
        if d <= on:
            return d, data
    return None, None


def _day(on):
    if on is None:
        try:
            from CountryPack.services import rule_date
            on = rule_date()
        except Exception:
            on = _dt.date.today()
    if isinstance(on, _dt.datetime):
        on = on.date()
    return on


# ------------------------------------------------------------------ writing the versions (signals)
def _upsert(kind, object_id, day, data, note=''):
    RV = M('PayrollPlus', 'RuleVersion')
    RV.objects.update_or_create(kind=kind, object_id=object_id, effective_from=day,
                                defaults={'data': data, 'note': note[:255], 'created_by_id': _user_id()})
    _forget(kind)


def _ensure_base(kind, object_id):
    RV = M('PayrollPlus', 'RuleVersion')
    if RV.objects.filter(kind=kind, object_id=object_id).exists():
        return
    data = snapshot(kind, object_id)
    if data is not None:
        _upsert(kind, object_id, BASE_DAY, data, 'Before the first dated change')


def _is_record(instance, kind):
    m = type(instance)._meta
    return kind not in TABLE_KINDS and _index().get(f'{m.app_label}.{m.model_name}'.lower(), (None, None))[1] is None


def _before(sender, instance, raw=False, **kw):
    """A dated save of an existing record (or of one of its children / a gratuity band): keep the rule before it first."""
    if raw:
        return
    kind, oid = _owner(instance)
    if kind is None or requested_date() is None:
        return
    try:
        _ensure_base(kind, oid)
    except Exception:
        log.exception('v1.67.12 effective: base version not kept')


def _after(sender, instance, raw=False, **kw):
    if raw:
        return
    kind, oid = _owner(instance)
    if kind is None:
        return
    try:
        day = requested_date()
        RV = M('PayrollPlus', 'RuleVersion')
        if day is None:
            # a correction: the version in force today follows the record (only when the record has dated versions)
            if not RV.objects.filter(kind=kind, object_id=oid).exists():
                return
            v = RV.objects.filter(kind=kind, object_id=oid, effective_from__lte=_dt.date.today()).order_by('-effective_from').first()
            data = snapshot(kind, oid)
            if v is not None and data is not None:
                v.data = data
                v.save(update_fields=['data', 'updated_at'])
                _forget(kind)
            return
        data = snapshot(kind, oid)
        if data is not None:
            _upsert(kind, oid, day, data, 'Changed from this date')
    except Exception:
        log.exception('v1.67.12 effective: version not kept')


def _deleted(sender, instance, **kw):
    kind, oid = _owner(instance)
    if kind is None or _is_record(instance, kind):
        return   # the record itself is deleted: its versions stay with its id (history), nothing to update
    _after(sender, instance)


def _before_delete(sender, instance, **kw):
    kind, oid = _owner(instance)
    if kind is None or _is_record(instance, kind):
        return
    _before(sender, instance)


def connect():
    from django.db.models.signals import pre_delete
    labels = []
    for kind, (label, kids) in KINDS.items():
        labels += [label] + [c[0] for c in kids]
    for label in labels:   # the labels as written (app labels are case-sensitive)
        Model = _model(label)
        if Model is None:
            continue
        uid = f'v16712_eff_{label}'
        pre_save.connect(_before, sender=Model, dispatch_uid=uid + '_pre', weak=False)
        post_save.connect(_after, sender=Model, dispatch_uid=uid + '_post', weak=False)
        pre_delete.connect(_before_delete, sender=Model, dispatch_uid=uid + '_predel', weak=False)
        post_delete.connect(_deleted, sender=Model, dispatch_uid=uid + '_del', weak=False)


# ------------------------------------------------------------------ reading: the record as it was on a day
def _restore(Model, data, keep=None):
    """An unsaved Model row from a snapshot row (fields that no longer exist are skipped)."""
    obj = keep if keep is not None else Model()
    by = {f.attname: f for f in _fields(Model)}
    for k, v in (data or {}).items():
        f = by.get(k)
        if f is None or f.primary_key and keep is not None:
            continue
        try:
            setattr(obj, k, f.to_python(v) if v is not None else None)
        except Exception:
            setattr(obj, k, v)
    return obj


def as_of(kind, obj, on=None):
    """The record `obj` as in force on `on`: a copy with the fields of that version (never saved), or `obj` itself when the record
    has no dated versions. The copy carries `_v16712_from` (the version's day) and its children (see children())."""
    if obj is None or getattr(obj, 'pk', None) is None or kind not in KINDS:
        return obj
    d, data = version_data(kind, obj.pk, on)
    if data is None:
        return obj
    label, kids = KINDS[kind]
    c = copy.copy(obj)
    _restore(type(obj), data.get('record') or {}, keep=c)
    c.pk = obj.pk
    c._v16712_from = d
    c._v16712_kids = {}
    for clabel, fk, name in kids:
        C = _model(clabel)
        if C is None:
            continue
        rows = []
        for r in data.get(name) or []:
            x = _restore(C, r)
            if fk.endswith('_id'):
                try:
                    setattr(x, fk[:-3], c)    # the child points at the dated record (e.g. line.policy)
                except Exception:
                    pass
            rows.append(x)
        c._v16712_kids[name] = rows
    return c


def children(obj, name, live=None):
    """The children `name` (lines / rules / pay_rules) of `obj` – the dated version's when `obj` came from as_of(), else `live`
    (a callable or list) or getattr(obj, name).all()."""
    kids = getattr(obj, '_v16712_kids', None)
    if kids is not None and name in kids:
        return list(kids[name])
    if live is not None:
        return list(live() if callable(live) else live)
    return list(getattr(obj, name).all())


def gratuity_rows(on=None, active_only=True):
    """The gratuity table in force on `on` (rows with minimum_value, maximum_value, resignation_days, termination_days, is_active)."""
    from decimal import Decimal
    d, data = version_data('gratuity', 0, on)
    if data is None:
        qs = M('OrganisationManager', 'GratuityTable').objects.all()
        if active_only:
            qs = qs.filter(is_active=True)
        return list(qs.order_by('minimum_value'))
    out = []
    for r in data.get('rows') or []:
        if active_only and not r.get('is_active', True):
            continue
        D = lambda v: Decimal(str(v)) if v not in (None, '') else None
        out.append(SimpleNamespace(id=r.get('id'), pk=r.get('id'), minimum_value=D(r.get('minimum_value')), maximum_value=D(r.get('maximum_value')),
                                   resignation_days=int(r.get('resignation_days') or 0), termination_days=int(r.get('termination_days') or 0),
                                   is_active=bool(r.get('is_active', True)), _v16712_from=d))
    out.sort(key=lambda x: (x.minimum_value or 0))
    return out


# ------------------------------------------------------------------ history (the list per record)
FIELD_LABELS = {   # nicer field names for the history (Arabic terms in i18n/packs/ar.json)
    'allow_half_day': 'Half day allowed',
    'allowed_methods': 'Allowed punch methods',
    'auto_deduct_after_hours': 'Deduct the break after (hours)',
    'auto_deduct_break': 'Deduct the break automatically',
    'branch_id': 'Branch',
    'break_minutes': 'Break (minutes)',
    'break_paid': 'Paid break',
    'carry_forward_expiry_months': 'Carried days expire after (months)',
    'category_id': 'Category',
    'category_ids': 'Employee categories',
    'comp_expiry_days': 'Compensatory off expires after (days)',
    'comp_full_day_hours': 'Compensatory off: hours for a full day',
    'comp_half_day_hours': 'Compensatory off: hours for half a day',
    'correction_max_per_month': 'Corrections a month (at most)',
    'correction_needs_hr': 'Corrections approved by HR',
    'correction_window_days': 'Corrections within (days)',
    'count_days': 'Days counted',
    'cross_midnight': 'Shift across midnight',
    'day_change_hour': 'Day change hour',
    'days_per_year': 'Days a year',
    'default_end': 'Default end time',
    'default_start': 'Default start time',
    'department_id': 'Department',
    'early_beyond_action': 'Leaving early beyond the tolerance',
    'early_policy_id': 'Early exit policy',
    'early_tolerance_minutes': 'Early exit tolerance (minutes)',
    'encash_max_per_year': 'Encashment a year (at most)',
    'excess_action': 'Days above the carry-forward limit',
    'exempt_category_ids': 'Exempt categories',
    'exempt_employee_ids': 'Exempt employees',
    'exempt_manual_source': 'Manual attendance employees exempt',
    'first_year_uae': 'First-year rule (UAE)',
    'grace_in_minutes': 'Grace period in (minutes)',
    'grace_out_minutes': 'Grace period out (minutes)',
    'half_day_unpaid_fraction': 'Unpaid part of a half day',
    'include_dashboard': 'Shown on the dashboard',
    'include_holiday': 'Public holidays counted',
    'include_weekend': 'Weekends counted',
    'is_compensatory': 'Compensatory leave',
    'is_entitlement': 'Has entitlements',
    'late_beyond_action': 'Late beyond the tolerance',
    'late_from': 'Late counted from',
    'late_policy_id': 'Late coming policy',
    'late_tolerance_minutes': 'Late tolerance (minutes)',
    'leave_type_id': 'Leave type',
    'leave_year_start': 'Leave year starts (month)',
    'mark_absent_if_no_punch': 'Absent when there is no punch',
    'max_hours_action': 'Above the maximum hours',
    'max_per_request': 'Days a request (at most)',
    'max_per_year': 'Days a leave year (at most)',
    'max_times_in_service': 'Times in service (at most)',
    'min_hours_full_day': 'Hours for a full day',
    'min_hours_half_day': 'Hours for half a day',
    'min_minutes_between_punches': 'Minutes between punches (at least)',
    'min_service_months': 'Months of service needed',
    'missing_punch_after_hours': 'Missing punch after (hours)',
    'night_end': 'Night ends',
    'night_min_minutes': 'Night minutes (at least)',
    'night_start': 'Night starts',
    'notice_days': 'Notice (days)',
    'ot_after_minutes': 'Overtime starts after (minutes)',
    'ot_basis': 'Overtime counted',
    'ot_daily_threshold_hours': 'Overtime after (hours a day)',
    'ot_enabled': 'Overtime counted on',
    'ot_max_hours_per_day': 'Overtime a day (at most, hours)',
    'ot_min_minutes': 'Overtime minutes (at least)',
    'ot_monthly_threshold_hours': 'Overtime after (hours a month)',
    'ot_needs_approval': 'Overtime needs approval',
    'ot_rate_holiday': 'Overtime rate – public holiday',
    'ot_rate_night': 'Overtime rate – night',
    'ot_rate_normal': 'Overtime rate – normal',
    'ot_rate_weekend': 'Overtime rate – weekend',
    'ot_weekly_threshold_hours': 'Overtime after (hours a week)',
    'overtime_source': 'Overtime from',
    'pay_percentage': 'Pay %',
    'pay_slabs': 'Pay during leave (days @ pay %)',
    'prorate_fixed_days': 'Fixed days a month',
    'prorate_unpaid': 'Unpaid leave reduces what is earned',
    'require_geofence': 'Geofence required',
    'require_gps': 'GPS required',
    'require_ip': 'Office network required',
    'require_selfie': 'Selfie required',
    'requires_document': 'Supporting document',
    'rounding_minutes': 'Rounding (minutes)',
    'rounding_mode': 'Rounding',
    'service_steps': 'Days by length of service',
    'use_common_workflow': 'Common approval workflow',
    'worked_paid_leave': 'Paid leave counts as worked',
    'worked_source': 'Days worked from',
    'negative': 'Negative balance allowed',
    'allow_negative': 'Negative balance allowed',
    'rate_multiplier': 'Rate (× hourly pay)',
    'fixed_working_days': 'Fixed working days',
    'frequency_years': 'One ticket every (years)',
    'resignation_days': 'Days per year – resignation',
    'termination_days': 'Days per year – termination',
    'minimum_value': 'From (years)',
    'maximum_value': 'To (years)',
    'enable_leave_pay_rule': 'Pay rules by leave duration',
    'required_service_years': 'Years of service needed',
    'threshold_hours': 'Threshold (hours)',
}


def _tr(s, *a):
    try:
        from Translations.i18n import tr
        return tr(s, *a)
    except Exception:
        return s.format(*a) if a else s


def _label(Model, attname):
    if attname in FIELD_LABELS:
        return _tr(FIELD_LABELS[attname])
    for f in _fields(Model):
        if f.attname == attname:
            return _tr(str(f.verbose_name or attname).capitalize())
    return attname


def _show(v):
    if v is True:
        return _tr('Yes')
    if v is False:
        return _tr('No')
    if v in (None, ''):
        return '–'
    if isinstance(v, float) and v.is_integer():
        return str(int(v))   # 24.0 → 24
    if isinstance(v, str) and len(v) == 10 and v[4] == '-' and v[7] == '-':
        try:
            return _dt.date.fromisoformat(v).strftime('%d/%m/%Y')
        except ValueError:
            return v
    return str(v)


def _num(v):
    """'5.00' → '5', '2.50' → '2.5' (years of a gratuity band)."""
    from decimal import Decimal, InvalidOperation
    try:
        return format(Decimal(str(v)).normalize(), 'f') if v not in (None, '') else '–'
    except InvalidOperation:
        return str(v)


def _diff(kind, old, new):
    """[{field, before, after}] between two snapshots (children compared row by row by id)."""
    label, kids = KINDS[kind]
    Model = _model(label)
    out = []
    if kind in TABLE_KINDS:
        a = {r.get('id'): r for r in (old or {}).get('rows') or []}
        b = {r.get('id'): r for r in (new or {}).get('rows') or []}
        for i in sorted(set(a) | set(b), key=lambda x: (x is None, x)):
            ra, rb = a.get(i), b.get(i)
            name = _tr('Band {0}–{1} years', _num((rb or ra).get('minimum_value')), _num((rb or ra).get('maximum_value')))
            if ra is None:
                out.append({'field': name, 'before': '–', 'after': _tr('Added')})
                continue
            if rb is None:
                out.append({'field': name, 'before': _tr('Removed'), 'after': '–'})
                continue
            for k in rb:
                if k in SKIP or ra.get(k) == rb.get(k):
                    continue
                out.append({'field': f'{name}: {_label(Model, k)}', 'before': _show(ra.get(k)), 'after': _show(rb.get(k))})
        return out
    ra, rb = (old or {}).get('record') or {}, (new or {}).get('record') or {}
    for k in rb:
        if k in SKIP or ra.get(k) == rb.get(k):
            continue
        out.append({'field': _label(Model, k), 'before': _show(ra.get(k)), 'after': _show(rb.get(k))})
    for clabel, fk, name in kids:
        C = _model(clabel)
        a = {r.get('id'): r for r in (old or {}).get(name) or []}
        b = {r.get('id'): r for r in (new or {}).get(name) or []}
        for i in sorted(set(a) | set(b), key=lambda x: (x is None, x)):
            ca, cb = a.get(i), b.get(i)
            tag = _tr({'lines': 'Line {0}', 'rules': 'Rule {0}', 'pay_rules': 'Pay rule {0}'}.get(name, 'Line {0}'), i)
            if kind == 'leave_policy':
                lt = (cb or ca).get('leave_type_id')
                n = M('calendars', 'leave_type').objects.filter(pk=lt).values_list('name', flat=True).first()
                tag = n or tag
            if ca is None:
                out.append({'field': tag, 'before': '–', 'after': _tr('Added')})
                continue
            if cb is None:
                out.append({'field': tag, 'before': _tr('Removed'), 'after': '–'})
                continue
            for k in cb:
                if k in SKIP or k == fk or ca.get(k) == cb.get(k):
                    continue
                out.append({'field': f'{tag}: {_label(C, k)}', 'before': _show(ca.get(k)), 'after': _show(cb.get(k))})
    return out


def history(kind, object_id):
    """The versions of a record, oldest first: {id, effective_from, until, base, changes, created_at, created_by, in_force}."""
    RV = M('PayrollPlus', 'RuleVersion')
    rows = list(RV.objects.filter(kind=kind, object_id=object_id).order_by('effective_from'))
    today = _dt.date.today()
    users = {}
    try:
        U = M('UserManagement', 'CustomUser')
        ids = {r.created_by_id for r in rows if r.created_by_id}
        users = {u.pk: (u.get_full_name() or u.username) for u in U.objects.filter(pk__in=ids)} if ids else {}
    except Exception:
        pass
    in_force = None
    for r in rows:
        if r.effective_from <= today:
            in_force = r.pk
    out, prev = [], None
    for i, r in enumerate(rows):
        nxt = rows[i + 1].effective_from if i + 1 < len(rows) else None
        base = r.effective_from == BASE_DAY
        out.append({'id': r.pk, 'effective_from': None if base else r.effective_from.isoformat(), 'base': base,
                    'until': (nxt - _dt.timedelta(days=1)).isoformat() if nxt else None,
                    'changes': [] if prev is None else _diff(kind, prev.data, r.data), 'note': r.note,
                    'created_at': r.created_at.isoformat() if r.created_at else None, 'created_by': users.get(r.created_by_id, ''),
                    'in_force': r.pk == in_force, 'future': r.effective_from > today})
        prev = r
    return out
