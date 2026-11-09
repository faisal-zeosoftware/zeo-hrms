"""v1.67.6 (setup) item 5 – a required setup for every app, like the HR setup guide (1.62.1).

HR has its 15-step setup guide with the "required setup" gate (ZeoAdmin.setup_gate). The other apps could be used the minute
they were activated, without numbering, defaults or masters. Now each of Projects, CRM, Property, Maintenance, Asset
Management, People and HSE has a short wizard (Settings › Apps › Setup), and its transactions (POST / PUT / PATCH on the app's
transaction screens) answer 409 `setup_required` until the wizard is complete – unless the administrator allows them early.
Payroll and HR keep the HR guide (Payroll needs HR).

Steps of every app (a step marked "auto" is ticked by itself when the data is there; the others are confirmed – or skipped
when they can be):
    industry   Industry and starting data   auto: the app's industry pack (pipelines, categories, permit types …) is installed
    numbering  Document numbering           auto: every series of the app exists (Settings › Document numbering, item 4)
    masters    The app's own masters        auto: e.g. Projects – SLA policy and issue categories; CRM – a pipeline;
                                            Property – a building; Maintenance – categories; Asset Management – categories and
                                            locations; HSE – a site; People – a benefit plan
    print      Printed documents            auto: every document of the app has a default design (item 6) – can be skipped
    accounts   Accounts                     confirm: the GL accounts of the app's postings (Property, Asset Management) – can be
                                            skipped (also for the apps without postings)
    people     Who uses the app             auto: a user has a role of the app (or confirm) – can be skipped

Companies already using an app count as complete: the gate is switched on only when an app is activated for the FIRST time
(never set up before) and has no records. State: ZeoApps.CompanyOption 'setup:<app>' {gate_on, allow, steps}.
"""
import json
import re

from django.apps import apps as djapps
from django.core.cache import cache
from django.db import connection
from django.http import HttpResponse
from django.utils import timezone

from .services import tr_ as tr

APPS_WITH_WIZARD = ('projects', 'crm', 'property', 'maintenance', 'assets', 'people', 'hse')
STEPS = [
    # key, title, can skip, auto check
    ('industry', 'Industry and starting data', False),
    ('numbering', 'Document numbering', False),
    ('masters', "The app's own masters", False),
    ('print', 'Printed documents', True),
    ('accounts', 'Accounts (if used)', True),
    ('people', 'Who uses the app', True),
]
MASTERS = {
    'projects': (['ZeoProjects.SLAPolicy', 'ZeoProjects.IssueCategory'], 'SLA policy and issue categories', '/main-sidebar/zeo-projects/settings'),
    'crm': (['ZeoCRM.Pipeline'], 'a sales pipeline', '/main-sidebar/zeo-crm/r/pipeline'),
    'property': (['ZeoProperty.Building'], 'a building or project with its units', '/main-sidebar/zeo-property/r/building'),
    'maintenance': (['ZeoMaintenance.Category'], 'work-order categories', '/main-sidebar/zeo-maintenance/r/category'),
    'assets': (['ZeoAssets.Category', 'ZeoAssets.Location'], 'asset categories and locations', '/main-sidebar/zeo-assets/r/category'),
    'people': (['ZeoPeople.BenefitPlan'], 'a benefit plan', '/main-sidebar/zeo-people/r/plan'),
    'hse': (['ZeoHSE.Site'], 'a site', '/main-sidebar/zeo-hse/setup'),
}
POSTS_TO_ACCOUNTS = ('property', 'assets')
ROLE_PREFIX = {'projects': ('zp_',), 'crm': ('crm_',), 'property': ('prop_',), 'maintenance': ('mnt_',), 'assets': ('am_', 'asset'),
               'people': ('ppl_',), 'hse': ('hse_',)}
SLUG = {'crm': 'zeo-crm', 'property': 'zeo-property', 'maintenance': 'zeo-maintenance', 'assets': 'zeo-assets', 'people': 'zeo-people'}
CACHE = 'zeo:appsetup:{}:{}'


def _tx_paths(app):
    """Regex of the app's transaction paths (writes there wait for the setup)."""
    if app == 'projects':
        return r'^/(zeo-projects/api/(tickets|amc|contracts|dev-requests|change-requests|worklogs|projects)|project/api/|project-control/api/(timesheets|entries))'
    if app == 'hse':
        return r'^/zeo-hse/api/(risks|permits|incidents|inspections|observations|toolbox|actions|audits|r/)'
    from .numbering import SERIES
    kinds = '|'.join(k for k, _l, _p in SERIES.get(app, []))
    return rf'^/{SLUG[app]}/api/(r/({kinds})/|quotation/|sales-order/)'


TX = {a: re.compile(_tx_paths(a)) for a in APPS_WITH_WIZARD}


def M(lab):
    return djapps.get_model(lab)


def _opt(app):
    CO = M('ZeoApps.CompanyOption')
    o = CO.objects.filter(key=f'setup:{app}').first()
    return o, (dict(o.value or {}) if o else {})


def _save(app, v):
    CO = M('ZeoApps.CompanyOption')
    CO.objects.update_or_create(key=f'setup:{app}', defaults={'value': v})
    forget(app)


def forget(app=None):
    for a in ([app] if app else APPS_WITH_WIZARD):
        cache.delete(CACHE.format(connection.schema_name, a))


# ------------------------------------------------------------------ the automatic checks
def _auto(app, key):
    from . import services as S
    try:
        if key == 'industry':
            if app == 'hse':
                return bool(M('ZeoHSE.HSESettings').get().packs_installed)
            if app == 'projects':
                return M('ZeoProjects.SLAPolicy').objects.exists()
            return bool(S.kit(app).settings().packs_installed)
        if key == 'numbering':
            from .numbering import missing
            return not missing([app])
        if key == 'masters':
            labels, _t, _l = MASTERS[app]
            return all(M(lab).objects.exists() for lab in labels)
        if key == 'print':
            from PrintTemplates.appdocs import APP_DOCS
            from PrintTemplates.models import PrintDefault
            docs = [d[0] for d in APP_DOCS if d[6] == app]
            return not docs or set(docs) <= set(PrintDefault.objects.filter(branch_id__isnull=True, language='').values_list('doc_type', flat=True))
        if key == 'accounts':   # v1.67.8 (fixes) item 8: the GL mappings are checked (None: nothing to map – skipped with a note)
            st, _note, _miss = accounts_check(app)
            return None if st == 'skip' else st == 'done'
        if key == 'people':
            from AccessControl.access import company_perms
            pre = ROLE_PREFIX.get(app, ())
            return company_perms().filter(is_superuser=False).filter(
                **{'groups__permissions__codename__startswith': pre[0]}).exists() if pre else False
    except Exception:   # noqa: BLE001
        return False
    return False


def has_accounting():
    """v1.67.8 (fixes) item 8: the company uses the accounting integration – an accounting app is connected, or accounts were
    mapped, or journals were made (an untouched default export set-up is not "accounting")."""
    try:
        AS, AM, JB = M('ZeoAccounting.AccountingSettings'), M('ZeoAccounting.AccountMap'), M('ZeoAccounting.JournalBatch')
    except LookupError:
        return False
    row = AS.objects.order_by('id').first()
    return bool((row is not None and row.connector != 'export') or AM.objects.exists() or JB.objects.exists())


def accounts_check(app):
    """v1.67.8 (fixes) item 8: ('done' | 'open' | 'skip', note, missing) of the Accounts step. Property: every Property account of
    Settings › Accounting integration › Account mapping has a mapped account (else the default code is used – listed as missing);
    Asset Management: every capitalised asset category has its GL accounts (asset, accumulated depreciation, depreciation
    expense, gain / loss on disposal)."""
    if app not in POSTS_TO_ACCOUNTS:
        return 'skip', tr('The app does not post to accounting – nothing to map.'), []
    if not has_accounting():
        return 'skip', tr('The company has no accounting integration yet (Settings › Accounting integration is not set up) – nothing to '
                          'map now. Once it is set up, this step lists the accounts that are missing.'), []
    missing = []
    if app == 'property':
        from ZeoAccounting.services import PROPERTY_ACCOUNTS
        rows = {r.key: r for r in M('ZeoAccounting.AccountMap').objects.filter(kind='property', department_id__isnull=True,
                                                                                 employee_id__isnull=True, branch_id__isnull=True)}
        for key, label, side, default, _group in PROPERTY_ACCOUNTS:
            r = rows.get(key)
            ok = r is not None and (side == 'credit' or (r.debit_account or '').strip()) and (side == 'debit' or (r.credit_account or '').strip())
            if not ok:
                missing.append(tr('{0} – not mapped (the default {1} would be used)', tr(label), default))
    elif app == 'assets':
        F = (('gl_asset', 'asset'), ('gl_accumulated', 'accumulated depreciation'), ('gl_expense', 'depreciation expense'),
             ('gl_disposal', 'gain / loss on disposal'))
        for c in M('ZeoAssets.Category').objects.filter(capitalise=True).order_by('code'):
            if hasattr(c, 'is_active') and not c.is_active:
                continue
            gone = [tr(lbl) for f, lbl in F if not (getattr(c, f, '') or '').strip()]
            if gone:
                missing.append(tr('{0}: {1}', c.name, ', '.join(gone)))
    return ('open' if missing else 'done'), '', missing


def steps(app):
    _o, v = _opt(app)
    marks = v.get('steps') or {}
    out = []
    labels, what, link = MASTERS.get(app, ([], '', ''))
    links = {'industry': f'/main-sidebar/settings/apps', 'numbering': '/main-sidebar/settings/document-numbering', 'masters': link,
             # v1.67.8 (fixes) item 8: was /main-sidebar/settings/accounting – an address without a page
             'print': '/main-sidebar/settings/print-templates',
             'accounts': {'property': '/main-sidebar/salary-options/accounting', 'assets': '/main-sidebar/zeo-assets/r/category'}.get(app, ''),
             'people': '/main-sidebar/settings/user-grouping-master'}
    hints = {'industry': 'The ready-made setup of the industry (categories, pipelines, permit types …) – from Settings › Apps.',
             'numbering': 'A numbering series for every document of the app.', 'masters': f'The app needs {what}.',
             'print': 'A default design for every printed document of the app.',
             'accounts': {'property': 'The GL accounts of the Property postings – checked in Settings › Accounting integration › Account mapping.',
                          'assets': 'The GL accounts of every capitalised asset category (asset, depreciation, disposal).'}.get(
                              app, 'The app does not post to accounting.'),
             'people': 'At least one user has a role of the app (Permission groups).'}
    for key, title, can_skip in STEPS:
        auto = _auto(app, key)
        mark = (marks.get(key) or {}).get('status', '')
        status = 'done' if auto else (mark if mark in ('done', 'skipped') else 'open')
        if mark == 'done' and auto is False and key in ('industry', 'numbering', 'masters', 'accounts'):   # v1.67.8 (fixes) + accounts
            status = 'open'   # the data is missing – confirming does not help
        row = {'key': key, 'title': title, 'can_skip': can_skip, 'auto': auto is not None, 'status': status, 'hint': hints[key],
               'link': links[key]}
        if key == 'accounts':   # v1.67.8 (fixes) item 8: what is missing, or why the step is skipped
            try:
                st, note, missing = accounts_check(app)
            except Exception:   # noqa: BLE001
                st, note, missing = 'open', '', []
            row.update(note=note, missing=missing, auto=True)
            if st == 'skip':
                row['status'] = 'skipped'
                row['link'] = '' if app not in POSTS_TO_ACCOUNTS else links[key]
        out.append(row)
    return out


def state(app, fresh=False):
    """{enforced, allow, blocked, open: [titles], steps}"""
    k = CACHE.format(connection.schema_name, app)
    st = None if fresh else cache.get(k)
    if st is None:
        _o, v = _opt(app)
        from .services import is_active
        on = bool(v.get('gate_on')) and is_active(app)
        sts = steps(app) if on or fresh else []
        open_ = [s['title'] for s in sts if not (s['status'] == 'done' or (s['can_skip'] and s['status'] == 'skipped'))]
        st = {'app': app, 'enforced': on, 'allow': bool(v.get('allow')), 'open': open_, 'blocked': on and bool(open_) and not v.get('allow')}
        cache.set(k, st, 20)
    return st


def full(app):
    st = dict(state(app, fresh=True))
    _o, v = _opt(app)
    st['steps'] = steps(app)
    st['started'] = v.get('started')
    return st


# ------------------------------------------------------------------ switching on, marking, allowing
def on_activate(app, first, user=None):
    """Activation: a first activation of an app without records needs its setup (the gate on); otherwise complete."""
    if app not in APPS_WITH_WIZARD:
        return
    o, v = _opt(app)
    if o is not None:
        forget(app)
        return
    from .industrylock import transactions
    gate = bool(first) and not transactions(app)
    _save(app, {'gate_on': gate, 'allow': False, 'steps': {}, 'started': timezone.now().isoformat(),
                'by': getattr(user, 'pk', None), 'complete_reason': '' if gate else 'in use before 1.67.6'})


def mark(app, key, status, user=None):
    if key not in {k for k, _t, _s in STEPS}:
        raise ValueError(tr('Unknown step.'))
    can_skip = next(s for k, _t, s in STEPS if k == key)
    if status == 'skipped' and not can_skip:
        raise ValueError(tr('This step cannot be skipped.'))
    if status not in ('done', 'skipped', 'open'):
        raise ValueError(tr('Unknown status.'))
    _o, v = _opt(app)
    s = dict(v.get('steps') or {})
    s[key] = {'status': status, 'by': getattr(user, 'pk', None), 'at': timezone.now().isoformat()}
    v['steps'] = s
    _save(app, v)
    return full(app)


def set_allow(app, on, user=None):
    _o, v = _opt(app)
    v.update(allow=bool(on), allow_by=getattr(user, 'pk', None), allow_at=timezone.now().isoformat())
    _save(app, v)
    return full(app)


def message(app, st):
    from .services import BY_KEY
    t = tr(BY_KEY[app][2])
    n = len(st['open'])
    names = ', '.join(tr(x) for x in st['open'])
    return tr('Finish the setup of {0} first – {1} step(s) still open ({2}). Settings › Apps › {0} › Setup.', t, n, names)


class AppSetupGateMiddleware:
    """409 setup_required on an app's transaction paths while its first-time setup is open (like the HR gate)."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method in ('POST', 'PUT', 'PATCH'):
            schema = getattr(connection, 'schema_name', 'public')
            if schema != 'public':
                for app, rx in TX.items():
                    if rx.match(request.path):
                        try:
                            st = state(app)
                        except Exception:   # noqa: BLE001 – never stop a company because the check failed
                            break
                        if st['blocked']:
                            from ZeoAdmin.setup_gate import _member
                            if _member(request, schema):
                                body = {'code': 'setup_required', 'app': app, 'detail': message(app, st), 'steps': st['open'],
                                        'link': f'/main-sidebar/settings/app-setup/{app}'}
                                return HttpResponse(json.dumps(body), status=409, content_type='application/json')
                        break
        return self.get_response(request)
