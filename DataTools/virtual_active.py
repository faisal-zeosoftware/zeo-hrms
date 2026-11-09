# v1.67.9 (screens)
"""v1.67.9 (screens) – item 17: masters without an active column can be made inactive too.

The 1.67.8 holiday-calendar solution (calendars/holiday_active.py) made general: core tables cannot get a new column (no
core migration), so the inactive records of a company are kept in the non-core store `ZeoApps.CompanyOption`, one row per
model (key `inactive:<app label>.<model>`, value {"ids": [..]}); the row is removed when nothing is inactive.

The shared list reads / writes it through DataTools.usage (the field is shown as `is_active`, like a real column):
  * GET /tools/api/usage/   → the flag of each row (Active / Inactive / All filter, "Make inactive / Make active")
  * POST /tools/api/active/ → after the screen's own right check (change right, the record in the user's scope)
  * list reads for new choices (drop-downs) leave the inactive ones out (AccessControl.access.active_only); the master's
    own register screen still lists them (X-Include-Inactive), and a record that already uses one keeps showing it.

Only masters that new records pick are listed here (set-up data, not transactions).
"""
import logging

log = logging.getLogger(__name__)

# app label.model → the name of the record shown in messages (field), or '' for str(obj)
MASTERS = {
    'calendars.leave_type': 'name',
    'calendars.weekend_calendar': 'calendar_code',
    'calendars.Shift': 'name',
    'calendars.ShiftPattern': 'name',
    'calendars.LvRejectionReason': 'reason_text',
    'EmpManagement.RequestType': 'name',
    'PayrollManagement.LoanType': 'loan_type',
    'PayrollManagement.SalaryComponent': 'name',
    'OrganisationManager.AssetType': 'name',
}
_LOWER = {k.lower(): k for k in MASTERS}


def label_of(model):
    try:
        return _LOWER.get(model._meta.label_lower)
    except Exception:   # noqa: BLE001
        return None


def handles(model):
    return label_of(model) is not None


def _key(label):
    return ('inactive:' + label)[:60]


def _opt():
    from ZeoApps.models import CompanyOption
    return CompanyOption


def inactive_ids(model):
    lab = label_of(model)
    if not lab:
        return set()
    try:
        o = _opt().objects.filter(key=_key(lab)).first()
    except Exception:   # noqa: BLE001 – a schema without ZeoApps tables (public)
        return set()
    return {int(x) for x in ((o.value or {}).get('ids') or []) if str(x).isdigit()} if o else set()


def set_active(model, pk, active):
    lab = label_of(model)
    if not lab:
        return False
    ids = inactive_ids(model)
    if active:
        ids.discard(int(pk))
    else:
        ids.add(int(pk))
    if ids:
        _opt().objects.update_or_create(key=_key(lab), defaults={'value': {'ids': sorted(ids)}})
    else:   # nothing inactive: no row left behind
        _opt().objects.filter(key=_key(lab)).delete()
    return True


def forget(model, pk):
    """A deleted record leaves the store."""
    try:
        if int(pk) in inactive_ids(model):
            set_active(model, pk, True)
    except Exception:   # noqa: BLE001
        log.exception('virtual_active.forget')


def is_active(obj):
    return getattr(obj, 'pk', None) not in inactive_ids(type(obj))


class Adapter:
    """The module-like object DataTools.usage expects for a virtual flag (inactive_ids(), set_active())."""

    def __init__(self, model):
        self.model = model

    def inactive_ids(self):
        return inactive_ids(self.model)

    def set_active(self, pk, active):
        return set_active(self.model, pk, active)

    def forget(self, pk):
        forget(self.model, pk)


def name_of(obj):
    f = MASTERS.get(label_of(type(obj)) or '', '')
    v = getattr(obj, f, None) if f else None
    return str(v if v not in (None, '') else obj)[:80]
