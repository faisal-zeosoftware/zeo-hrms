"""v1.21.0 – the apps of a company: what is installed, what is active, with which industry, and activation.

Every app works on its own. When two apps are active in the same company their links switch on by themselves
(HSE and the kit apps check that the other app is active); deactivating an app hides it and switches its links off,
its data is kept.

    hrms        HRMS (people, time, pay, assets, talent)              state: ZeoApps.AppState
    projects    Projects (support tickets, AMC, delivery)             state: ZeoApps.AppState
    crm         CRM (contacts, pipelines, opportunities)              state: ZeoCRM.CRMSettings (app kit)
    property    Property (buildings, units, leases, sales, portals)   state: ZeoProperty.PropertySettings (app kit)
    maintenance Maintenance (work orders, preventive plans)           state: ZeoMaintenance.MaintSettings (app kit)
    hse         HSE (risk, permits, incidents, CAPA …)                state: ZeoHSE.HSESettings
"""
import importlib
import logging

from django.apps import apps as djapps
from django.contrib.auth.models import Group, Permission
from django.db import connection, transaction
from django.utils import timezone

log = logging.getLogger(__name__)

# key, Django app label, title, icon, what it is for, kind, launcher landing page
APPS = [
    ('hrms', 'EmpManagement', 'HRMS', 'groups', 'People, time and attendance, leave, payroll, assets and talent', 'core', '/main-sidebar/home'),
    ('projects', 'ZeoProjects', 'Projects', 'account_tree', 'Support tickets with SLA, customers and AMC contracts, project delivery', 'core', '/main-sidebar/zeo-projects/dashboard'),
    ('crm', 'ZeoCRM', 'CRM', 'handshake', 'Contacts, pipelines, opportunities, viewings, offers and listings', 'kit', '/main-sidebar/zeo-crm/home'),
    ('property', 'ZeoProperty', 'Property', 'apartment', 'Buildings, units, owners, leases, sales, billing, cheques and portals', 'kit', '/main-sidebar/zeo-property/home'),
    ('maintenance', 'ZeoMaintenance', 'Maintenance', 'build', 'Work orders, quotes, preventive plans, assets and inspections', 'kit', '/main-sidebar/zeo-maintenance/home'),
    ('hse', 'ZeoHSE', 'HSE', 'health_and_safety', 'Risk, permits to work, incidents, inspections, audits and corrective actions', 'hse', '/main-sidebar/zeo-hse/home'),
]
KEYS = [a[0] for a in APPS]
BY_KEY = {a[0]: a for a in APPS}
BY_LABEL = {a[1]: a[0] for a in APPS}


def company():
    try:
        from UserManagement.models import company as C
        return C.objects.filter(schema_name=connection.schema_name).first()
    except Exception:
        return None


def company_industry():
    t = company()
    code = (getattr(t, 'industry_type', None) or '') if t else ''
    return code, (t.get_industry_type_display() if t and code else '')


def company_industries():
    from UserManagement.models import company as C
    return [{'code': k, 'label': str(v)} for k, v in C.INDUSTRY_CHOICES]


def installed(key):
    label = BY_KEY[key][1]
    if not djapps.is_installed(label) and label not in {c.label for c in djapps.get_app_configs()}:
        return False
    if key in ('hrms', 'projects'):
        return True
    try:
        return model_of(key)._meta.db_table.lower() in {t.lower() for t in connection.introspection.table_names()}
    except Exception:
        return False


def kit(key):
    label = BY_KEY[key][1]
    importlib.import_module(f'{label}.app')
    from zeo.appkit.engine import REGISTRY
    return REGISTRY[label]


def model_of(key):
    if key == 'hse':
        from ZeoHSE.models import HSESettings
        return HSESettings
    if key in ('crm', 'property', 'maintenance'):
        return kit(key).settings_model
    from .models import AppState
    return AppState


def core_state(key):
    from .models import AppState
    return AppState.objects.filter(app=key).first()


# ------------------------------------------------------------------ state
def is_active(key):
    """Is the app active in the current company? (HRMS / Projects: yes unless switched off.)"""
    try:
        if key in ('hrms', 'projects'):
            s = core_state(key)
            return True if s is None else s.active
        if not installed(key):
            return False
        if key == 'hse':
            from ZeoHSE.models import HSESettings
            return HSESettings.activated()
        return kit(key).activated()
    except Exception:
        log.exception('app state %s', key)
        return False


def industries_of(key):
    """[(code, label)] the app can be set up for, and the one suggested by the company master."""
    code, _ = company_industry()
    if key == 'hse':
        from ZeoHSE.models import INDUSTRIES, INDUSTRY_FROM_COMPANY
        return list(INDUSTRIES), INDUSTRY_FROM_COMPANY.get(code, 'general')
    if key in ('crm', 'property', 'maintenance'):
        a = kit(key)
        return [(c, a.packs[c]['label']) for c in a.packs], a.industry_from_company.get(code, 'general')
    from UserManagement.models import company as C
    return [(k, str(v)) for k, v in C.INDUSTRY_CHOICES], code or 'OTH'


def state(key):
    t = BY_KEY[key]
    out = {'key': key, 'label': t[1], 'title': t[2], 'icon': t[3], 'description': t[4], 'kind': t[5], 'page': t[6],
           'installed': installed(key), 'active': False, 'industry': '', 'industry_label': '', 'industry_source': '',
           'activated_at': None, 'activated_by': ''}
    if not out['installed']:
        return out
    inds, sug = industries_of(key)
    labels = dict(inds)
    out.update(industries=[{'code': c, 'label': l} for c, l in inds], suggested=sug, suggested_label=labels.get(sug, sug))
    out['active'] = is_active(key)
    s = None
    if key in ('hrms', 'projects'):
        s = core_state(key)
    elif key == 'hse':
        from ZeoHSE.models import HSESettings
        s = HSESettings.get()
    else:
        s = kit(key).settings()
    if s is not None:
        ind = getattr(s, 'industry', '') or ''
        out.update(industry=ind, industry_label=labels.get(ind, ind), industry_source=getattr(s, 'industry_source', '') or '',
                   activated_at=s.activated_at.isoformat() if getattr(s, 'activated_at', None) else None,
                   activated_by=_uname(getattr(s, 'activated_by', None)))
    if key in ('hrms', 'projects') and s is None:
        code, _ = company_industry()
        out.update(industry=code, industry_label=labels.get(code, ''), industry_source='company')
    # switched off before: activating again keeps the industry it had (unless another one is chosen)
    out['previous'] = previous_industry(key) if not out['active'] else ''
    out['roles'] = role_groups(key)
    out['links'] = links_of(key)
    return out


def _uname(u):
    if u is None:
        return ''
    return (u.get_full_name() if hasattr(u, 'get_full_name') else '') or u.username


def all_states():
    return [state(k) for k in KEYS]


def active_keys():
    return [k for k in KEYS if is_active(k)]


# ------------------------------------------------------------------ links between apps (on when both apps are active)
LINKS_WITH = {
    'hrms': [('projects', 'Employees are project team members; timesheets, project cost and profitability come from salaries.'),
             ('hse', 'Training certificates, attendance hours, sick leave, assets, shift roster, muster list and HSE induction for new joiners.')],
    'projects': [('hrms', 'Team members, timesheets and employee cost come from HRMS.'),
                 ('hse', 'A site belongs to a project; each project shows an HSE tab; serious incidents add a project issue; settled claims add a project cost.')],
    'hse': [('hrms', 'Training, attendance hours (TRIR / LTIFR), lost days from sick leave, equipment from assets, permit crew roster, muster list.'),
            ('projects', 'Sites by project, HSE tab on projects, project issues and costs.')],
}


def links_of(key):
    out = []
    if key in ('crm', 'property', 'maintenance'):
        try:
            pairs = [(BY_LABEL.get(lbl), what) for lbl, _t, what in kit(key).links]
        except Exception:
            pairs = []
    else:
        pairs = LINKS_WITH.get(key, [])
    me = is_active(key)
    for other, what in pairs:
        if other not in BY_KEY:
            continue
        on = installed(other) and is_active(other)
        out.append({'with': other, 'title': BY_KEY[other][2], 'what': what, 'other_active': on, 'on': bool(me and on)})
    return out


# ------------------------------------------------------------------ role groups (Settings → Permission groups)
ROLE_PREFIX = {'projects': 'Projects – ', 'crm': 'CRM – ', 'property': 'Property – ', 'maintenance': 'Maintenance – ', 'hse': 'HSE – '}


def role_groups(key):
    p = ROLE_PREFIX.get(key)
    if not p:
        return []
    return list(Group.objects.filter(name__startswith=p).order_by('name').values_list('name', flat=True))


def make_roles(key):
    """Create the app's role groups "<App> – <role>" (safe to run again)."""
    if key in ('crm', 'property', 'maintenance'):
        from zeo.appkit.command import make_roles as mk
        return mk(kit(key))
    if key == 'hse':
        from ZeoHSE.management.commands.zeohse_setup import Command
        return Command().roles()
    if key == 'projects':
        from ZeoProjects.management.commands.zeoprojects_setup import Command
        return Command().roles()
    return []


# ------------------------------------------------------------------ activation
class AppsError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


def previous_industry(key):
    """The industry an app had when it was switched off ('' if it was never activated)."""
    try:
        if key in ('hrms', 'projects'):
            r = core_state(key)
            return (r.industry or '') if r is not None and r.activated_at else ''
        if key == 'hse':
            from ZeoHSE.models import HSESettings
            r = HSESettings.get()
        else:
            r = kit(key).settings()
        return (r.industry or '') if r.packs_installed else ''
    except Exception:
        return ''


def activate(key, industry=None, user=None):
    """Activate the app with an industry (default: the one suggested by the company master). Returns what was added."""
    if key not in BY_KEY:
        raise AppsError('Unknown app.')
    if not installed(key):
        raise AppsError(f'{BY_KEY[key][2]} is not installed on this server.')
    inds, sug = industries_of(key)
    ind = industry or previous_industry(key) or sug
    if ind not in dict(inds):
        raise AppsError(f'Choose the industry for {BY_KEY[key][2]}.')
    source = 'company' if ind == sug else 'chosen'
    added = {}
    with transaction.atomic():
        if key in ('hrms', 'projects'):
            from .models import AppState
            s = AppState.objects.filter(app=key).first() or AppState(app=key)
            s.active, s.industry, s.industry_source = True, ind, source
            s.activated_at, s.activated_by = timezone.now(), user
            s.save()
            if key == 'projects':   # v1.22.0: SLA policy, teams, categories … when Projects was not chosen at company set-up
                try:
                    from ZeoProjects.models import SLAPolicy
                    if not SLAPolicy.objects.exists():
                        from ZeoProjects.management.commands.zeoprojects_setup import Command as _PC
                        added = {'defaults': _PC().defaults()}
                except Exception:
                    log.exception('Projects defaults')
        elif key == 'hse':
            from ZeoHSE.models import HSESettings
            from ZeoHSE.packs import install
            s = HSESettings.get()
            if s.activated_at is None:
                again = bool(s.packs_installed)
                if not again or ind != s.industry:
                    added = install(ind, user, primary=True)
                s = HSESettings.get()
                if not again or ind != s.industry:
                    s.industry, s.industry_source = ind, source
                s.activated_at, s.activated_by = timezone.now(), user
                s.save()
        else:
            from zeo.appkit import packs as P
            a = kit(key)
            s = a.settings()
            if s.activated_at is None:
                if s.packs_installed:
                    # activated before and switched off: on again; a different industry only adds its pack
                    if ind != s.industry:
                        added = P.install(a, ind, user, primary=True)
                        s = a.settings()
                        s.industry_source = source
                    s.activated_at, s.activated_by = timezone.now(), user
                    s.save()
                else:
                    added = P.activate(a, ind, user)
    try:
        make_roles(key)
    except Exception:
        log.exception('role groups for %s', key)
    _forget()
    return added


def deactivate(key, user=None):
    """Hide the app and switch its links off; its data is kept and comes back when it is activated again."""
    if key not in BY_KEY:
        raise AppsError('Unknown app.')
    others = [k for k in KEYS if k != key and is_active(k)]
    if not others:
        raise AppsError('At least one app must stay active.')
    with transaction.atomic():
        if key in ('hrms', 'projects'):
            from .models import AppState
            s = AppState.objects.filter(app=key).first() or AppState(app=key)
            if s.pk is None:
                code, _ = company_industry()
                s.industry, s.industry_source = code, 'company'
            s.active = False
            s.save()
        elif key == 'hse':
            from ZeoHSE.models import HSESettings
            s = HSESettings.get()
            s.activated_at = None
            s.save(update_fields=['activated_at'])
        else:
            s = kit(key).settings()
            s.activated_at = None
            s.save(update_fields=['activated_at'])
    _forget()


def set_core_default(keys, industry=''):
    """New company: HRMS / Projects are active only when chosen (rows written so the choice is explicit)."""
    from .models import AppState
    for k in ('hrms', 'projects'):
        s = AppState.objects.filter(app=k).first() or AppState(app=k)
        s.active = k in keys
        s.industry, s.industry_source = industry or '', 'company'
        if s.active and not s.activated_at:
            s.activated_at = timezone.now()
        s.save()


def setup_new_company(tenant, keys, user=None):
    """Company creation: the apps ticked on the form (at least one). HRMS / Projects on or off; CRM, Property, Maintenance and
    HSE activated with the industry that matches the company industry type (it can be changed later in Settings → Apps)."""
    from django_tenants.utils import schema_context
    keys = [k for k in (keys or []) if k in BY_KEY] or ['hrms']
    done, failed = [], {}
    with schema_context(tenant.schema_name):
        set_core_default(keys, getattr(tenant, 'industry_type', '') or '')
        for k in keys:
            if k in ('hrms', 'projects'):
                try:
                    make_roles(k)
                except Exception:
                    log.exception('role groups for %s', k)
                done.append(k)
                continue
            try:
                activate(k, None, user)
                done.append(k)
            except Exception as e:   # the company is made anyway; the app can be activated in Settings → Apps
                log.exception('activate %s for %s', k, tenant.schema_name)
                failed[k] = str(getattr(e, 'message', e))[:200]
    return done, failed


def _forget():
    try:
        from zeo.appkit.engine import forget_tables
        forget_tables()
    except Exception:
        pass
