"""v1.21.0 – the apps of a company: what is installed, what is active, with which industry, and activation.

Every app works on its own. When two apps are active in the same company their links switch on by themselves
(HSE and the kit apps check that the other app is active); deactivating an app hides it and switches its links off,
its data is kept.

    hrms        HR (people, time, leave, documents, self service)    state: ZeoApps.AppState  – v1.67.3: an app of its own
    payroll     Payroll (salaries, payslips, WPS, gratuity)          state: ZeoApps.AppState  – v1.23.0; no row = as HR; needs HR
    projects    Projects (support tickets, AMC, delivery)             state: ZeoApps.AppState
    crm         CRM (contacts, pipelines, opportunities)              state: ZeoCRM.CRMSettings (app kit)
    property    Property (buildings, units, leases, sales, portals)   state: ZeoProperty.PropertySettings (app kit)
    maintenance Maintenance (work orders, preventive plans)           state: ZeoMaintenance.MaintSettings (app kit)
    assets      Asset Management (register, labels, counts, depreciation) state: ZeoAssets.AssetSettings (app kit) – v1.33.0
    hse         HSE (risk, permits, incidents, CAPA …)                state: ZeoHSE.HSESettings

v1.67.3 (apps) – user decision of 5 Nov 2026: the apps work on their own, like Odoo and Zoho. The company core is always on and
is not an app: company, branches, departments, designations, users and rights, the shared masters (partners, items, locations)
and a basic employee directory (name, code, branch, department, designation, contact, status – ZeoApps.directory). Every app
declares what it needs (DEPENDS); activating an app activates what it needs, deactivating is refused while an active app needs
it ("Payroll needs HR"). HR no longer comes with every app; companies that have HR keep it.
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
    ('hrms', 'EmpManagement', 'HR', 'groups', 'People, leave, attendance, documents, letters, self service, expenses, assets and talent', 'core', '/main-sidebar/home'),
    # v1.23.0: Payroll is its own app; with HR it is the same menu as ZEO HRMS before
    ('payroll', 'PayrollManagement', 'Payroll', 'payments', 'Salaries, payslips, WPS, gratuity, loans and final settlement', 'core', '/main-sidebar/salary-options/pay-roll'),
    ('projects', 'ZeoProjects', 'Projects', 'account_tree', 'Support tickets with SLA, customers and AMC contracts, project delivery', 'core', '/main-sidebar/zeo-projects/dashboard'),
    ('crm', 'ZeoCRM', 'CRM', 'handshake', 'Leads, accounts, contacts, pipelines, opportunities, quotations, sales orders, targets and forecast', 'kit', '/main-sidebar/zeo-crm/home'),
    ('property', 'ZeoProperty', 'Property', 'apartment', 'Buildings, units, owners, leases, sales, billing, cheques and portals', 'kit', '/main-sidebar/zeo-property/home'),
    ('maintenance', 'ZeoMaintenance', 'Maintenance', 'build', 'Work orders, quotes, preventive plans, assets and inspections', 'kit', '/main-sidebar/zeo-maintenance/home'),
    ('assets', 'ZeoAssets', 'Asset Management', 'inventory_2', 'Asset register shared with HR, QR labels and scanning, check-out, reservations, physical counts and depreciation', 'kit', '/main-sidebar/zeo-assets/home'),
    ('people', 'ZeoPeople', 'People', 'diversity_3', 'Part of HRMS (v1.38.1): benefits, e-signature, forms, surveys, recognition and rewards, chat, 360° reviews and development plans – switch off to hide these HR pages', 'kit', '/main-sidebar/zeo-people/home'),
    ('hse', 'ZeoHSE', 'HSE', 'health_and_safety', 'Risk, permits to work, incidents, inspections, audits and corrective actions', 'hse', '/main-sidebar/zeo-hse/home'),
]
KEYS = [a[0] for a in APPS]
KIT_KEYS = ('crm', 'property', 'maintenance', 'assets', 'people')   # apps on the app kit (zeo.appkit)
CORE = ('hrms', 'projects', 'payroll')   # state in ZeoApps.AppState
BY_KEY = {a[0]: a for a in APPS}
BY_LABEL = {a[1]: a[0] for a in APPS}

# v1.67.3 (apps) what every app needs besides the company core (Odoo-style "depends"). Asset Management works on the shared asset
# register (OrganisationManager.Asset + AssetPlus profile): those tables are always there and Asset Management reaches them through
# its own screens and services, so it needs only the core; the HR pages around the register (custody, asset requests, recoveries,
# exit clearance) stay in HR and link up when HR is active. People is "part of HRMS" (benefits through payroll, HR letters to sign,
# 360° reviews next to Performance, HR forms) – it needs HR.
DEPENDS = {
    'hrms': [],
    'payroll': ['hrms'],
    'projects': [],
    'crm': [],
    'property': [],
    'maintenance': [],
    'assets': [],
    'people': ['hrms'],
    'hse': [],
}
CORE_TEXT = ('Always on, not an app: company, branches, departments, designations, users and rights, business partners, items, '
             'locations and the employee directory.')


def requires(key):
    """Every app `key` needs (what it needs needs too), the deepest first."""
    out = []

    def walk(k, seen):
        for d in DEPENDS.get(k, []):
            if d in seen:
                continue
            walk(d, seen | {d})
            if d not in out:
                out.append(d)
    walk(key, {key})
    return out


def with_deps(keys):
    """The apps chosen plus what they need, each needed app before the app that needs it (order of KEYS otherwise)."""
    out = []
    for k in [k for k in KEYS if k in (keys or [])]:
        for d in requires(k) + [k]:
            if d not in out:
                out.append(d)
    return out


def dependents(key):
    """The apps that need `key` (directly or through another app)."""
    return [k for k in KEYS if k != key and key in requires(k)]


def needed_by_active(key):
    """The active apps that need `key` – it cannot be switched off while there is one."""
    return [k for k in dependents(key) if installed(k) and is_active(k)]


def brings(key):
    """What activating `key` switches on with it: the apps it needs that are not active yet."""
    return [d for d in requires(key) if not is_active(d)]


def needs_message(key, users):
    """'Payroll needs HR.' / 'Payroll and People need HR.'"""
    t = [tr_(BY_KEY[k][2]) for k in users]   # the app names in the user's language too
    if len(t) == 1:
        return tr_('{0} needs {1}.', t[0], tr_(BY_KEY[key][2]))
    return tr_('{0} and {1} need {2}.', ', '.join(t[:-1]), t[-1], tr_(BY_KEY[key][2]))


def tr_(text, *args):
    try:
        from Translations.i18n import tr
        return tr(text, *args)
    except Exception:
        return text.format(*args) if args else text


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
    if key in CORE:
        return True
    try:
        return _has_table(model_of(key)._meta.db_table)
    except Exception:
        return False


_TABLES = set()   # (schema, table) seen to exist – tables are never dropped while the process runs


def _has_table(table):
    """v1.23.0: one cheap lookup instead of listing every table of the schema (was 36 ms per call)."""
    k = (getattr(connection, 'schema_name', 'public'), table.lower())
    if k in _TABLES:
        return True
    with connection.cursor() as c:
        c.execute('SELECT 1 FROM pg_tables WHERE schemaname = %s AND lower(tablename) = %s', [k[0], k[1]])
        ok = c.fetchone() is not None
    if ok:
        _TABLES.add(k)
    return ok


def kit(key):
    label = BY_KEY[key][1]
    importlib.import_module(f'{label}.app')
    from zeo.appkit.engine import REGISTRY
    return REGISTRY[label]


def model_of(key):
    if key == 'hse':
        from ZeoHSE.models import HSESettings
        return HSESettings
    if key in KIT_KEYS:
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
        if key in CORE:
            s = core_state(key)
            if s is None and key == 'payroll':   # v1.23.0: companies from before the split – Payroll as HR
                return is_active('hrms')
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
    if key in KIT_KEYS:
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
    if key in CORE:
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
    # v1.67.3 (apps): what the app needs, what activating it brings with it now and the active apps that need it
    out['depends'] = [{'key': d, 'title': BY_KEY[d][2]} for d in requires(key)]
    out['brings'] = [{'key': d, 'title': BY_KEY[d][2]} for d in brings(key)] if not out['active'] else []
    nb = needed_by_active(key) if out['active'] else []
    out['needed_by'] = [{'key': d, 'title': BY_KEY[d][2]} for d in nb]
    out['needed_message'] = needs_message(key, nb) if nb else ''
    if out['active']:   # v1.67.6 (setup) item 5: the app's setup wizard (HR and Payroll: the setup guide)
        try:
            from .appsetup import APPS_WITH_WIZARD, state as _setup
            if key in APPS_WITH_WIZARD:
                st = _setup(key, fresh=True)
                out['setup'] = {'enforced': st['enforced'], 'open': len(st['open']), 'blocked': st['blocked'], 'allow': st['allow'],
                                'link': f'/main-sidebar/settings/app-setup/{key}'}
        except Exception:
            pass
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
    'hrms': [('payroll', 'Unpaid leave becomes a deduction, attendance gives overtime and absence, leave encashment and final settlement go to payroll, expenses are paid in payroll, payslips in self service.'),
             ('projects', 'Employees are project team members; timesheets, project cost and profitability come from salaries.'),
             ('hse', 'Training certificates, attendance hours, sick leave, assets, shift roster, muster list and HSE induction for new joiners.'),
             ('assets', 'One asset register: HR → Assets and Asset Management show the same assets; custody, requests, recoveries and exit clearance stay in HR.'),
             ('people', 'Benefit deductions and paid claims go to payroll; HR letters, offers and policies can be e-signed; 360° reviews sit next to Performance; forms, surveys, recognition and chat for every employee.')],
    'payroll': [('hrms', 'Leave, attendance, encashment, exits and expenses from HR; payslips and salary certificate in self service.')],
    'projects': [('hrms', 'Team members, timesheets and employee cost come from HRMS.'),
                 ('hse', 'A site belongs to a project; each project shows an HSE tab; serious incidents add a project issue; settled claims add a project cost.')],
    'hse': [('hrms', 'Training, attendance hours (TRIR / LTIFR), lost days from sick leave, equipment from assets, permit crew roster, muster list.'),
            ('projects', 'Sites by project, HSE tab on projects, project issues and costs.')],
}


def links_of(key):
    out = []
    if key in KIT_KEYS:
        try:
            pairs = [(BY_LABEL.get(lbl), what) for lbl, _t, what in kit(key).links]
            if key == 'assets':   # v1.33.0: Asset Management always works on the HR asset register
                pairs = [('hrms', 'The same assets as HR → Assets: check-out, transfers, disposal and counts go through HR, so payroll '
                                  'recovery, exit clearance and HSE see them.')] + pairs
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
ROLE_PREFIX = {'projects': 'Projects – ', 'crm': 'CRM – ', 'property': 'Property – ', 'maintenance': 'Maintenance – ', 'assets': 'Asset Management – ', 'people': 'People – ', 'hse': 'HSE – '}


def role_groups(key):
    p = ROLE_PREFIX.get(key)
    if not p:
        return []
    # v1.62.0 isolation: the role groups of this company (its own copies shown with the standard name; not other companies' copies)
    from . import groupscope
    gs = Group.objects.filter(name__startswith=p)
    if groupscope.schema():
        gs = gs.filter(pk__in=groupscope.visible_ids() | set(groupscope.rows()))
    return sorted({groupscope.label(g) for g in gs if groupscope.label(g).startswith(p)})


def make_roles(key):
    """Create the app's role groups "<App> – <role>" (safe to run again)."""
    if key in KIT_KEYS:
        from zeo.appkit.command import make_roles as mk
        return mk(kit(key))
    if key == 'hse':
        from ZeoHSE.management.commands.zeohse_setup import Command
        return Command().roles()
    if key == 'projects':
        from ZeoProjects.management.commands.zeoprojects_setup import Command
        return Command().roles()
    if key == 'hrms':
        # v1.62.0: HR operations (part of HR) and Business partners (shared by every app) are on the app kit too, but they are
        # not apps of their own here – their role groups were never made, so the checklist roles could not be given to users
        from zeo.appkit.command import make_roles as mk
        from zeo.appkit.engine import REGISTRY
        out = []
        import types
        for label, title in (('ZeoHR', None), ('ZeoMasters', 'Business partners')):
            try:
                importlib.import_module(f'{label}.app')
                a = REGISTRY[label]
                if title:   # the Masters app's roles are shown as "Business partners – …" (as in Settings › Apps and the checklist)
                    a = types.SimpleNamespace(title=title, roles=a.roles, label=a.label, code=a.code)
                out += mk(a)
            except Exception:
                log.exception('role groups of %s', label)
        return out
    return []


def make_core_roles():
    """v1.67.3 (apps) the role groups of the company core (Business partners – …), there with or without HR."""
    from zeo.appkit.command import make_roles as mk
    from zeo.appkit.engine import REGISTRY
    import types
    importlib.import_module('ZeoMasters.app')
    a = REGISTRY['ZeoMasters']
    return mk(types.SimpleNamespace(title='Business partners', roles=a.roles, label=a.label, code=a.code))


# ------------------------------------------------------------------ activation
class AppsError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


def previous_industry(key):
    """The industry an app had when it was switched off ('' if it was never activated)."""
    try:
        if key in CORE:
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
    if industry:   # v1.67.6 (setup) item 8: an app with records keeps its industry
        from .industrylock import refusal
        msg = refusal(key, industry)
        if msg:
            raise AppsError(msg, 409)
    # v1.67.3 (apps): what the app needs is switched on first (each with its own industry default and licence check)
    need = [d for d in requires(key) if not is_active(d)]
    for d in need:
        if not installed(d):
            raise AppsError(tr_('{0} needs {1}, which is not installed on this server.', tr_(BY_KEY[key][2]), tr_(BY_KEY[d][2])))
        _cloud_check(d)
    _cloud_check(key)
    for d in need:
        activate(d, None, user)
    first_time = _never_set_up(key)   # v1.67.6 (setup) item 5
    inds, sug = industries_of(key)
    ind = industry or previous_industry(key) or sug
    if ind not in dict(inds):
        raise AppsError(f'Choose the industry for {BY_KEY[key][2]}.')
    source = 'company' if ind == sug else 'chosen'
    added = {}
    with transaction.atomic():
        if key in CORE:
            from .models import AppState
            s = AppState.objects.filter(app=key).first() or AppState(app=key)
            first_hr = key == 'hrms' and s.pk is not None and s.activated_at is None and not s.active   # v1.67.3 (apps) HR added later
            s.active, s.industry, s.industry_source = True, ind, source
            s.activated_at, s.activated_by = timezone.now(), user
            s.save()
            if key == 'hrms':
                _hr_on(first_hr)
                try:   # v1.67.8 (fixes) item 5: the HR defaults of a company made without HR
                    from .hrdefaults import ensure as _hr_defaults
                    r = _hr_defaults(user)
                    if r:
                        added = {'hr_defaults': r}
                except Exception:
                    log.exception('HR defaults when HR is activated')
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
    # v1.67.3 (apps): HR no longer comes with every app (v1.23.0 switched it on here) – only what the app needs (above)
    _forget()
    try:   # v1.67.6 (setup) item 4: the app's document numbering series (and those of what came with it) – never renumbered
        from . import numbering as _N
        made = _N.ensure([key] + need, apply=True, user=user if getattr(user, 'pk', None) else None)
        if made:
            added = dict(added or {}, numbering=len(made))
    except Exception:
        log.exception('numbering series for %s', key)
    try:   # v1.67.6 (setup) item 5: a first activation waits for the app's setup wizard (like HR); apps in use count as complete
        from .appsetup import on_activate
        on_activate(key, first_time, user)
        for d in need:
            on_activate(d, True, user)
    except Exception:
        log.exception('setup wizard for %s', key)
    try:   # v1.67.6 (setup) item 6: the app's print designs and their defaults (samples are made for every document once)
        from PrintTemplates.services import seed as _print_seed
        _print_seed()
    except Exception:
        log.exception('print templates for %s', key)
    if need:
        added = dict(added or {}, **{'with': [BY_KEY[d][2] for d in need]})
    return added


def _never_set_up(key):
    """v1.67.6 (setup) item 5: the app was never activated in this company (no industry pack, no activation date)."""
    try:
        if key in CORE:
            r = core_state(key)
            return r is None or r.activated_at is None
        if key == 'hse':
            from ZeoHSE.models import HSESettings
            return not HSESettings.get().packs_installed
        return not kit(key).settings().packs_installed
    except Exception:
        return False


def _hr_on(first):
    """v1.67.3 (apps) HR switched on. The employees of the directory are the HR employees already (the same records). A company
    that never had HR (made with other apps only) finishes the required setup steps before HR transactions, as a new company
    does (v1.62.1); the administrator can allow them earlier in the setup guide. The sample print templates are added."""
    try:
        from ZeoAdmin import setup_gate
        if first:
            setup_gate.enforce_new()
        setup_gate.forget()
    except Exception:
        log.exception('setup gate when HR is activated')
    if first:
        try:
            from PrintTemplates.services import seed as _print_seed
            _print_seed()
        except Exception:
            log.exception('print templates when HR is activated')


def deactivate(key, user=None):
    """Hide the app and switch its links off; its data is kept and comes back when it is activated again."""
    if key not in BY_KEY:
        raise AppsError('Unknown app.')
    others = [k for k in KEYS if k != key and is_active(k)]
    if not others:
        raise AppsError('At least one app must stay active.')
    # v1.67.3 (apps): refused while an active app needs it ("Payroll needs HR") – HR is no longer needed by every app
    nb = needed_by_active(key)
    if nb:
        raise AppsError(tr_('{0} cannot be switched off: {1} Switch off {2} first.', tr_(BY_KEY[key][2]), needs_message(key, nb),
                            ', '.join(tr_(BY_KEY[k][2]) for k in nb)), 409)
    with transaction.atomic():
        if key in CORE:
            from .models import AppState
            s = AppState.objects.filter(app=key).first() or AppState(app=key)
            if s.pk is None:
                code, _ = company_industry()
                s.industry, s.industry_source = code, 'company'
            s.active = False
            s.save()
            if key == 'hrms':   # v1.67.3 (apps) no HR setup gate for a company without HR
                try:
                    from ZeoAdmin import setup_gate
                    setup_gate.forget()
                except Exception:
                    pass
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
    for k in CORE:
        s = AppState.objects.filter(app=k).first() or AppState(app=k)
        s.active = k in keys
        s.industry, s.industry_source = industry or '', 'company'
        if s.active and not s.activated_at:
            s.activated_at = timezone.now()
        s.save()


def setup_new_company(tenant, keys, user=None, progress=None):
    """Company creation: the apps ticked on the form (at least one). HRMS / Projects on or off; CRM, Property, Maintenance and
    HSE activated with the industry that matches the company industry type (it can be changed later in Settings → Apps)."""
    from django_tenants.utils import schema_context
    # v1.67.3 (apps): the apps chosen and what they need – HR only when chosen or needed (Payroll, People); none chosen = HR
    keys = with_deps([k for k in (keys or []) if k in BY_KEY]) or ['hrms']
    done, failed = [], {}
    with schema_context(tenant.schema_name):
        set_core_default(keys, getattr(tenant, 'industry_type', '') or '')
        try:   # v1.67.8 (fixes) item 6: a copy of the template carries its automatic one-year end dates – a new company has none
            from .enddates import clear as _clear_end_dates
            _clear_end_dates(apply=True)
        except Exception:
            log.exception('numbering end dates of %s', tenant.schema_name)
        if 'hrms' not in keys:   # v1.67.3 (apps) the Business partners role groups belong to the core (made with HR before)
            try:
                make_core_roles()
            except Exception:
                log.exception('core role groups of %s', tenant.schema_name)
            try:   # v1.67.8 (fixes) item 5: no HR defaults without HR (leave types, holidays, weekend …) – loaded when HR is activated
                from .hrdefaults import strip
                strip(user)
            except Exception:
                log.exception('HR defaults of %s', tenant.schema_name)
        if 'hrms' in keys:
            try:   # v1.62.1 (user request 2 Nov): a new company finishes the required setup steps before HR transactions
                from ZeoAdmin.setup_gate import enforce_new
                enforce_new()
            except Exception:
                log.exception('setup gate of %s', tenant.schema_name)
            try:   # v1.62.0 print – the sample print templates of the new company (payslip, letters, settlement …)
                from PrintTemplates.services import seed as _print_seed
                _print_seed()
            except Exception:
                log.exception('print templates of %s', tenant.schema_name)
        for i, k in enumerate(keys):
            if progress is not None:   # v1.62.1: progress of the company job – (done so far, total, app name)
                try:
                    progress(i, len(keys), str(BY_KEY[k][2]) if k in BY_KEY else k)
                except Exception:
                    pass
            if k in ('hrms', 'projects'):
                try:
                    make_roles(k)
                except Exception:
                    log.exception('role groups for %s', k)
                if k == 'projects':   # v1.67.6 (setup) item 5: the SLA policy, teams and categories, as when activated later
                    try:
                        from ZeoProjects.models import SLAPolicy
                        if not SLAPolicy.objects.exists():
                            from ZeoProjects.management.commands.zeoprojects_setup import Command as _PC
                            _PC().defaults()
                    except Exception:
                        log.exception('Projects defaults of %s', tenant.schema_name)
                try:   # v1.67.6 (setup) item 5: Projects of a new company waits for its setup wizard
                    from .appsetup import on_activate
                    on_activate(k, True, user)
                except Exception:
                    log.exception('setup wizard for %s', k)
                done.append(k)
                continue
            try:
                activate(k, None, user)
                done.append(k)
            except Exception as e:   # the company is made anyway; the app can be activated in Settings → Apps
                log.exception('activate %s for %s', k, tenant.schema_name)
                failed[k] = str(getattr(e, 'message', e))[:200]
        try:   # v1.67.6 (setup) item 4: every chosen app has its numbering series from the first day
            from . import numbering as _N
            _N.ensure(keys, apply=True)
        except Exception:
            log.exception('numbering series of %s', tenant.schema_name)
        try:   # v1.67.6 (setup) item 2: the default branch takes the company master's logo, address, contact, TRN / licence, year
            from .branchinherit import inherit
            inherit(user)
        except Exception:
            log.exception('default branch of %s', tenant.schema_name)
        try:   # v1.67.6 (setup) item 12: the "Company administrators" group (every branch), linked to the administrators
            from .adminbranches import ensure_group
            ensure_group()
        except Exception:
            log.exception('administrators group of %s', tenant.schema_name)
        try:   # v1.67.6 (setup) item 6: the print designs of every chosen app, a default for each document
            from PrintTemplates.services import seed as _print_seed
            _print_seed()
        except Exception:
            log.exception('print templates of %s', tenant.schema_name)
    return done, failed


def _forget():
    try:
        from zeo.appkit.engine import forget_tables
        forget_tables()
    except Exception:
        pass


def _cloud_check(key):
    """v1.23.0 ZEO Cloud: an app is switched on only if the company's account has a free licence place for it."""
    try:
        from django.conf import settings
        if not getattr(settings, 'ZEO_CLOUD', False):
            return
        from ZeoCloud.services import account_of_schema, can_activate
        acc = account_of_schema(connection.schema_name)
        if acc is None:
            return
        ok, msg = can_activate(acc, key, connection.schema_name)
    except Exception:
        log.exception('cloud licence check')
        return
    if not ok:
        raise AppsError(msg, 409)
