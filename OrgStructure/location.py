"""
v1.30.0 – Location level: a location is a place inside a branch (site, shop, project office, warehouse) and, when
Settings › Organisation › *Location-level control* is on, a level the company works at:

  * every transaction record is tagged with a location (RecordLocation side table – the transaction apps have no
    committed migrations, so no column is added to them);
  * the header switcher narrows every list to the selected locations (X-Zeo-Locations) and names the working
    location new records are made in (X-Zeo-Location);
  * users with *User location access* (or an own location) see and change only the records of those locations
    (location_restrict);
  * document numbers come from the location's series when one exists (location_numbering);
  * a new transaction must have a location (location_required).

Off (the default) nothing changes: locations stay a plain master of HR › Organisation.
"""
import logging
import threading
import time

from django.apps import apps
from django.db import connection
from django.db.models import Q
from django.db.models.signals import post_save

logger = logging.getLogger(__name__)
_local = threading.local()
_cache = {}            # schema → (expires, settings row)
_models_cache = {}     # schema → (expires, set of model keys)

HEADER_SELECTED = 'HTTP_X_ZEO_LOCATIONS'   # comma separated ids the user ticked in the switcher (filter)
HEADER_CURRENT = 'HTTP_X_ZEO_LOCATION'     # the working location (default for new records / numbering)
BODY_KEY = 'zeo_location'                  # explicit location in a POST / PUT body (wins over the header)

MASTER_WORDS = ('master', 'mstr', 'type', 'category', 'ctgry', 'policy', 'setting', 'template', 'config', 'grade', 'designation',
                'desgntn', 'department', 'dept', 'branch', 'brnch', 'holiday', 'calendar', 'component', 'rule', 'structure',
                'formula', 'slab', 'bank', 'currency', 'country', 'cntry', 'state', 'document_type', 'doctype', 'skill',
                'language', 'folder', 'numbering', 'shift', 'roster', 'kpi', 'competenc', 'course', 'plan', 'stage',
                'pipeline', 'source', 'tag', 'checklist', 'question', 'survey', 'useraccess', 'access', 'geofence', 'option',
                'period', 'fiscal', 'wps', 'cycle', 'version', 'report', 'view', 'dashboard', 'widget', 'reason', 'status')
# in the candidate list but off unless ticked: sub-tables, approvals, balances, technical rows
OFF_WORDS = ('approval', 'workflow', 'balance', 'transaction', 'notify', 'mapping', 'summary', 'entitlement', 'certificate', 'result',
             'outcome', 'sheet', 'task', 'family', 'qualification', 'documents', 'candidatedocument', 'accrual', 'payslip', 'goal',
             'checkin', 'weekend', 'rejoining', 'recheck', 'participant', 'nomination', 'application', 'candidate', 'line', 'item',
             'detail', 'member', 'assignment', 'enrol', 'score', 'rating', 'answer', 'attempt', 'milestone', 'timesheet',
             'vendor', 'customer', 'contact', 'product', 'owner', 'party', 'building', 'unit', 'site', 'area', 'team', 'event', 'file',
             'message', 'parameter', 'chemical', 'obligation', 'location', 'profile', 'announcement')
ALWAYS = ('EmpManagement.emp_master',)   # employees are scoped by their own location, never tagged


def current_request():
    try:
        from Chatter.tracking import current_request as cr
        return cr()
    except Exception:
        return None


# ------------------------------------------------------------------ settings
def settings_row():
    """OrgSettings of the current company, cached 30 s per schema."""
    sch = connection.schema_name
    if sch == 'public':
        return None
    hit = _cache.get(sch)
    if hit and hit[0] > time.time():
        return hit[1]
    from .models import OrgSettings
    try:
        # v1.33.0: savepoint – while a new company's tables are still being created (migrations) the table may not exist
        # yet; a failed read must not break the migration's transaction.
        from django.db import transaction
        with transaction.atomic():
            row = OrgSettings.get()
    except Exception:
        return None
    _cache[sch] = (time.time() + 30, row)
    return row


def invalidate():
    _cache.pop(connection.schema_name, None)
    _models_cache.pop(connection.schema_name, None)


def enabled():
    s = settings_row()
    return bool(s and s.location_control and s.use_locations)


def flag(name):
    s = settings_row()
    return bool(s and s.location_control and s.use_locations and getattr(s, 'location_' + name, False))


def model_key(model):
    return f'{model._meta.app_label}.{model._meta.model_name}'


def _is_transaction(model):
    """A record type that belongs to a location: linked to an employee or a branch, and not a master."""
    from AccessControl.access import emp_path, branch_path
    name = model._meta.model_name
    label = model._meta.app_label
    if label in ('OrgStructure', 'AccessControl', 'Chatter', 'ZeoFlow', 'ZeoApps', 'ZeoCloud', 'ZeoLicence', 'ZeoGroup', 'UserManagement',
                 'Core', 'Translations', 'LanguagePack', 'CountryPack', 'DataTools', 'SavedViews', 'DashboardManagement', 'AISettings',
                 'NotificationManagement', 'ZeoMobile'):
        return False
    if model._meta.auto_created or model._meta.proxy:
        return False
    if any(w in name for w in MASTER_WORDS):
        return False
    try:
        from ZeoFlow.registry import SKIP_WORDS
        if any(w in name for w in SKIP_WORDS):
            return False
    except Exception:
        pass
    ep = emp_path(model)
    if ep:
        return True
    bp, _ = branch_path(model)
    return bool(bp and bp != 'id')


def candidate_models():
    """Every record type that could carry a location: [{key, label, app, default}] (for the settings page)."""
    out = []
    try:
        from ZeoFlow.registry import MODULES, _nice
    except Exception:
        MODULES, _nice = {}, (lambda s: s)
    for module, labels in MODULES.items():
        for label in labels:
            if not apps.is_installed(label):
                continue
            for m in apps.get_app_config(label).get_models():
                if _is_transaction(m):
                    name = m._meta.model_name
                    out.append({'key': model_key(m), 'label': _nice(m._meta.verbose_name), 'app': label, 'module': module,
                                'default': not any(w in name for w in OFF_WORDS)})
    out.sort(key=lambda d: (d['module'], d['label']))
    return out


def location_models():
    """Set of model keys that carry a location (settings list, or every candidate)."""
    sch = connection.schema_name
    hit = _models_cache.get(sch)
    if hit and hit[0] > time.time():
        return hit[1]
    s = settings_row()
    keys = set()
    if s and s.location_control and s.use_locations:
        chosen = [k for k in (s.location_models or []) if isinstance(k, str)]
        keys = set(chosen) if chosen else {d['key'] for d in candidate_models() if d['default']}
        keys.update(ALWAYS)
    _models_cache[sch] = (time.time() + 30, keys)
    return keys


def has_location(model):
    return model_key(model) in location_models()


# ------------------------------------------------------------------ the user's locations
def _ints(v):
    out = []
    for x in (v or '').split(','):
        x = x.strip()
        if x.isdigit():
            out.append(int(x))
    return out


def branch_locations(branches):
    """Ids of the active locations of the given branches (None = every branch)."""
    from .models import Location
    qs = Location.objects.filter(active=True)
    if branches is not None:
        qs = qs.filter(branch_id__in=list(branches))
    return list(qs.values_list('id', flat=True))


def own_location(request):
    """The requesting user's own location (EmployeeOrg of their employee record), or None."""
    try:
        from AccessControl.access import ctx
        from .models import EmployeeOrg
        c = ctx(request)
        if c.emp is None:
            return None
        return EmployeeOrg.objects.filter(employee_id=c.emp.id).values_list('location_id', flat=True).first()
    except Exception:
        return None


def allowed_locations(request):
    """None = every location (admins, restriction off, or a user without a location), else the user's location ids."""
    if request is None or not flag('restrict'):
        return None
    cached = getattr(request, '_zeo_locs', 'x')
    if cached != 'x':
        return cached
    res = None
    try:
        from AccessControl.access import ctx
        from .models import UserLocationAccess
        c = ctx(request)
        if not c.admin and c.tenant_schema != 'public':
            ids = UserLocationAccess.objects.filter(user_id=c.user.pk).values_list('location_ids', flat=True).first() or []
            ids = [int(i) for i in ids if str(i).isdigit()]
            if c.branches is not None and ids:   # never outside the user's branches
                inb = set(branch_locations(c.branches))
                ids = [i for i in ids if i in inb]
            if not ids:
                mine = own_location(request)
                ids = [mine] if mine else []
            res = ids or None
    except Exception:
        logger.exception('allowed locations')
        res = None
    request._zeo_locs = res
    return res


def selected_locations(request):
    """The switcher's ticked locations (within the allowed ones). None = no filter."""
    if request is None or not enabled():
        return None
    ids = _ints(request.META.get(HEADER_SELECTED, ''))
    allowed = allowed_locations(request)
    if allowed is not None:
        ids = [i for i in ids if i in allowed] if ids else list(allowed)
    return ids or None


def effective_locations(request):
    """What the user may see right now: selection ∩ allowed; None = everything."""
    sel = selected_locations(request)
    if sel is not None:
        return sel
    return allowed_locations(request)


def current_location(request, data=None):
    """The location a new record is made in: body zeo_location → X-Zeo-Location → the only allowed / selected → own."""
    if request is None or not enabled():
        return None
    allowed = allowed_locations(request)
    cand = None
    if isinstance(data, dict) and str(data.get(BODY_KEY) or '').isdigit():
        cand = int(data[BODY_KEY])
    if cand is None:
        v = str(request.META.get(HEADER_CURRENT, '') or '').strip()
        cand = int(v) if v.isdigit() else None
    if cand is None:
        sel = selected_locations(request)
        if sel and len(sel) == 1:
            cand = sel[0]
    if cand is None and allowed and len(allowed) == 1:
        cand = allowed[0]
    if cand is None:
        cand = own_location(request)
    if cand is not None and allowed is not None and cand not in allowed:
        return None
    return cand


def loc_emp_ids(locs):
    """Employee ids visible for the given locations: employees in them plus employees without a location."""
    from .models import EmployeeOrg
    Emp = apps.get_model('EmpManagement', 'emp_master')
    placed = EmployeeOrg.objects.filter(location_id__isnull=False)
    return Emp.objects.filter(Q(id__in=EmployeeOrg.objects.filter(location_id__in=list(locs)).values('employee_id'))
                              | ~Q(id__in=placed.values('employee_id'))).values('id')


# ------------------------------------------------------------------ row filter and save check
def filter_qs(request, qs, model):
    """Narrow a queryset to the user's effective locations (no-op when control is off / nothing selected)."""
    try:
        if not enabled():
            return qs
        from .models import Location, RecordLocation
        locs = effective_locations(request)
        if locs is None:
            return qs
        if model is Location:
            return qs.filter(id__in=locs)
        key = model_key(model)
        if key not in location_models():
            return qs
        from AccessControl.access import emp_path
        emps = loc_emp_ids(locs)
        if key == 'EmpManagement.emp_master':
            return qs.filter(id__in=emps)
        ep = emp_path(model)
        emp_cond = Q()
        if ep:
            emp_cond = Q(**{f'{ep}__in': emps}) | Q(**{f'{ep}__isnull': True})
        tagged_in = RecordLocation.objects.filter(model=key, location_id__in=locs).values('record_id')
        tagged_out = RecordLocation.objects.filter(model=key).exclude(location_id__in=locs).values('record_id')
        cond = Q(id__in=tagged_in) | (~Q(id__in=tagged_out) & emp_cond)
        # rows addressed to the user (approver, delegate, assignee) stay visible whatever their location (as 1.28.1 for branches)
        try:
            from AccessControl.access import user_fields
            u = getattr(request, 'user', None)
            if u is not None and u.is_authenticated:
                for f in user_fields(model):
                    cond |= Q(**{f: u})
        except Exception:
            pass
        return qs.filter(cond)
    except Exception:
        logger.exception('location filter %s', getattr(model, '__name__', model))
        return qs


def save_ok(request, model, data, verb):
    """(ok, message) for an add / change by a location-restricted user, or when a location is required."""
    try:
        if not enabled():
            return True, ''
        key = model_key(model)
        if key not in location_models() or key in ALWAYS:
            return True, ''
        allowed = allowed_locations(request)
        loc = current_location(request, data)
        if verb == 'add' and flag('required') and not loc and not getattr(request, '_zeo_loc_detail', False):
            return False, 'Choose the location of this record (location-level control is on).'
        if allowed is None:
            return True, ''
        explicit = None
        if isinstance(data, dict) and str(data.get(BODY_KEY) or '').isdigit():
            explicit = int(data[BODY_KEY])
        else:
            v = str(request.META.get(HEADER_CURRENT, '') or '').strip()
            explicit = int(v) if v.isdigit() else None
        if explicit is not None and explicit not in allowed:
            return False, 'You can only work with records of your own locations.'
        from AccessControl.access import emp_path, _values
        ep = emp_path(model)
        if ep and isinstance(data, dict):
            vals = [v for v in _values(data.get(ep.split('__')[0])) if str(v).isdigit()]
            if vals:
                Emp = apps.get_model('EmpManagement', 'emp_master')
                ids = {int(v) for v in vals}
                ok = set(Emp.objects.filter(id__in=ids).filter(id__in=loc_emp_ids(allowed)).values_list('id', flat=True))
                if ids - ok:
                    return False, 'You can only work with employees of your own locations.'
        return True, ''
    except Exception:
        logger.exception('location save check')
        return True, ''


# ------------------------------------------------------------------ tagging new records
def employee_location(model, obj):
    from AccessControl.access import emp_path
    from .models import EmployeeOrg
    ep = emp_path(model)
    if not ep:
        return None
    try:
        cur = obj
        for part in ep.split('__'):
            cur = getattr(cur, part, None)
            if cur is None:
                return None
        return EmployeeOrg.objects.filter(employee_id=cur.pk).values_list('location_id', flat=True).first()
    except Exception:
        return None


def tag(model, obj, location_id, user_id=None):
    from .models import RecordLocation
    if not location_id:
        return
    RecordLocation.objects.update_or_create(model=model_key(model), record_id=obj.pk,
                                            defaults={'location_id': location_id, 'set_by_id': user_id})


def get_tag(model, pk):
    from .models import RecordLocation
    return RecordLocation.objects.filter(model=model_key(model), record_id=pk).select_related('location').first()


def _on_save(sender, instance, created, **kwargs):
    if not created or kwargs.get('raw') or connection.schema_name == 'public':
        return
    try:
        if not enabled() or model_key(sender) not in location_models() or model_key(sender) in ALWAYS:
            return
        req = current_request()
        data = None
        if req is not None:
            try:
                data = req.data if isinstance(getattr(req, 'data', None), dict) else None
            except Exception:
                data = None
        loc = current_location(req, data) if req is not None else None
        if not loc:
            loc = employee_location(sender, instance)
        if loc:
            tag(sender, instance, loc, getattr(getattr(req, 'user', None), 'pk', None))
    except Exception:
        logger.exception('location tag %s', sender.__name__)


def install():
    post_save.connect(_on_save, dispatch_uid='zeo-location-tag', weak=False)


# ------------------------------------------------------------------ numbering
def override_number(doc_type, branch_id=None):
    """The next number from the working location's series for doc_type, or None (then the branch series is used)."""
    try:
        if not flag('numbering'):
            return None
        req = current_request()
        data = None
        if req is not None:
            try:
                data = req.data if isinstance(getattr(req, 'data', None), dict) else None
            except Exception:
                data = None
        loc = current_location(req, data)
        if not loc:
            return None
        from .models import LocationNumbering
        row = LocationNumbering.objects.filter(location_id=loc, type=doc_type, automatic_numbering=True).select_related('location').first()
        if row is None:
            return None
        if branch_id and row.location.branch_id and int(row.location.branch_id) != int(branch_id):
            return None
        return row.get_next_number()
    except Exception:
        logger.exception('location numbering %s', doc_type)
        return None


def payload(request):
    """For the header switcher: control flags, the user's locations grouped by branch, the current one."""
    from .models import Location
    s = settings_row()
    on = enabled()
    out = {'enabled': on, 'required': flag('required'), 'numbering': flag('numbering'), 'restrict': flag('restrict'),
           'label': s.label('locations') if s else 'Location', 'locations': [], 'allowed': None, 'current': None}
    if not on:
        return out
    from AccessControl.access import ctx
    c = ctx(request)
    allowed = allowed_locations(request)
    qs = Location.objects.filter(active=True)
    if c.branches is not None:
        qs = qs.filter(branch_id__in=c.branches)
    if allowed is not None:
        qs = qs.filter(id__in=allowed)
    B = apps.get_model('OrganisationManager', 'brnch_mstr')
    names = dict(B.objects.values_list('id', 'branch_name'))
    out['locations'] = [{'id': l.id, 'code': l.code, 'name': l.name, 'branch_id': l.branch_id, 'branch_name': names.get(l.branch_id, ''),
                         'is_default': l.is_default} for l in qs.order_by('branch_id', 'code')]
    out['allowed'] = allowed
    out['current'] = current_location(request)
    out['own'] = own_location(request)
    return out
