"""Ask ZEO 1.73.0 – every app, not only HRMS: Projects, CRM, Property, Maintenance, Asset Management, HSE (and the other kit apps).

* explain any record by its number or name ("explain WO-00012", "status of lease LS-0003", "tell me about ticket TK-000045"):
  found with the global search of the user (Chatter.nav – only records the user may open) or, for Projects and HSE, by the number
  field of their records; the record is read with the app's own API AS THE USER (rights, branches) and shown as a card – the main
  fields, the status, the last steps of its history and the link to open it.
* the setup check of an app ("is CRM ready?", "check the HSE setup"): activated, industry pack reviewed, roles given to users, the
  setup lists filled (pipelines, unit types, categories and SLA, teams, sites, permit types …), each with the link that fixes it.
* the guides of every app (data/guides.json) and the data questions of every app (report centre) were already in 1.71.0.
Nothing here changes data.
"""
import importlib
import logging
import re

from django.apps import apps as django_apps

from Translations.i18n import tr

from . import apicall

log = logging.getLogger(__name__)
MS = '/main-sidebar/'

# app key (ZeoApps) → (kit label or None, title, words that name it)
APPS = {
    'crm': ('ZeoCRM', 'CRM', r'crm|sales pipeline|leads?'),
    'property': ('ZeoProperty', 'Property', r'property|properties|leasing|real estate'),
    'maintenance': ('ZeoMaintenance', 'Maintenance', r'maintenance|work orders?'),
    'assets': ('ZeoAssets', 'Asset Management', r'asset management|assets? app|fixed assets?'),
    'people': ('ZeoPeople', 'People', r'people app|benefits|e-?signature|surveys?'),
    'hr': ('ZeoHR', 'HR operations', r'hr operations|helpdesk|pro services'),
    'projects': (None, 'Projects', r'projects?|support|tickets?|amc'),
    'hse': (None, 'HSE', r'hse|health and safety|safety|permits? to work|incidents?'),
}
KIT = [v[0] for v in APPS.values() if v[0]]
# Projects / HSE records with a number the user may type
NUMBERED = [('ZeoProjects', ('number', 'code')), ('ZeoHSE', ('number', 'code', 'ref'))]
NUM_RX = re.compile(r'\b([A-Za-z]{1,8}[-/]?\d{2,}[A-Za-z0-9/-]*)\b')
SKIP_FIELDS = re.compile(r'(^_|_label$|^id$|^kind$|password|secret|token|iban|passport|emirates|^files?$|^history$|^children$|^actions$|^info$|^tables$|'
                         r'^can_edit$|^pdf$|^print_doc$|restricted_hidden|review_status|^pack$)', re.I)


def kit_apps():
    from zeo.appkit.engine import REGISTRY
    for label in KIT:
        try:
            importlib.import_module(f'{label}.app')
        except Exception:
            continue
    return {k: v for k, v in REGISTRY.items() if k in KIT}


def _active(key):
    try:
        from ZeoApps.services import is_active
        return bool(is_active(key))
    except Exception:
        return True


def app_named(cq):
    """The app key named in the question, or None."""
    for key, (_, title, words) in APPS.items():
        if re.search(r'\b(' + words + r')\b', cq):
            return key
    return None


# ------------------------------------------------------------------ explain a record
def number_in(question):
    """A record number typed in the question ('WO-00012', 'LS0003', 'TK-2026/0045'), or None. Plain years or amounts are not numbers."""
    for m in NUM_RX.finditer(question or ''):
        t = m.group(1)
        if re.fullmatch(r'(19|20)\d\d', t) or re.fullmatch(r'\d+', t):
            continue
        if re.fullmatch(r'(aed|usd|eur|sar|qar|omr|bhd|kwd)\d+', t, re.I):
            continue
        return t
    return None


def _find_numbered(request, token):
    """[(app label, model label, pk)] of Projects / HSE records whose number / code is the token."""
    out = []
    for app_label, fields in NUMBERED:
        try:
            cfg = django_apps.get_app_config(app_label)
        except LookupError:
            continue
        for M in cfg.get_models():
            names = {f.name for f in M._meta.concrete_fields}
            for fn in fields:
                if fn in names:
                    try:
                        for pk in M.objects.filter(**{f'{fn}__iexact': token}).values_list('pk', flat=True)[:3]:
                            out.append((app_label, M._meta.label, pk))
                    except Exception:
                        pass
                    break
    return out


def find_record(request, c, question):
    """{'kind': 'kit'|'record', ...} of the record the question names, or None. Only records the user may open."""
    token = number_in(question)
    terms = [token] if token else []
    m = re.search(r'(?:explain|status of|tell me about|details of|show me|open|what is|what\'s|where is)\s+(?:the\s+)?(?:\w+\s+)?["“]?([^"”?]{3,60})["”]?\??$', question.strip(), re.I)
    if m and not token:
        terms.append(m.group(1).strip())
    from Chatter.nav import search_records
    for t in terms:
        try:
            res = search_records(request, t, per_reg=3, limit=20)
        except Exception:
            log.info('Ask ZEO record search', exc_info=True)
            res = []
        tl = t.lower()
        exact = [x for x in res if (x.get('number') or '').lower() == tl or x['title'].lower() == tl]
        pick = exact[0] if exact else (res[0] if res and len(res) == 1 else None)
        if pick:
            return {'kind': 'kit', 'endpoint': pick['endpoint'], 'id': pick['id'], 'page': pick['page'], 'app': pick.get('app') or '', 'label': pick.get('label') or ''}
        if len(res) > 1 and not exact:
            return {'kind': 'many', 'results': res[:8]}
    if token:
        for app_label, label, pk in _find_numbered(request, token):
            return {'kind': 'record', 'label': label, 'id': pk, 'app': 'Projects' if app_label == 'ZeoProjects' else 'HSE',
                    'page': f'{MS}report-options/rec/{label}/{pk}'}
    return None


def _plain(v):
    if isinstance(v, bool):
        return tr('Yes') if v else tr('No')
    if isinstance(v, (list, dict)):
        return ''
    return str(v) if v not in (None, '') else ''


def explain_record(request, c, question, lang):
    found = find_record(request, c, question)
    if found is None:
        return None
    L = lang
    if found['kind'] == 'many':
        lines = [tr('More than one record matches – which one?', lang=L)]
        lines += [f"- **{x['title']}**" + (f" ({x['number']})" if x.get('number') else '') + f" – {x.get('subtitle') or x.get('app')}" for x in found['results']]
        return {'text': '\n'.join(lines), 'source': 'data', 'intent': 'record',
                'links': [{'label': x['title'], 'url': x['page']} for x in found['results'][:6]]}
    try:
        if found['kind'] == 'kit':
            d = apicall.call(request, 'get', f"{found['endpoint']}{found['id']}/")
            fields = [(k, d.get(k + '_label') if d.get(k + '_label') not in (None, '') else d.get(k)) for k in d if not SKIP_FIELDS.search(k)]
            title = d.get('_title') or str(found['id'])
            status = d.get('status_label') or d.get('status') or ''
            hist = [h for h in (d.get('history') or []) if isinstance(h, dict)][:5]
            hist_lines = [f"{str(h.get('at') or h.get('created_at') or '')[:16].replace('T', ' ')} – {h.get('text') or h.get('summary') or ''}"
                          + (f" ({h.get('user')})" if h.get('user') else '') for h in hist]
            kind_label = found.get('label') or ''
        else:
            d = apicall.call(request, 'get', f"/dashboard/api/record/{found['label']}/{found['id']}/")
            fields = [(f.get('label'), f.get('value')) for f in (d.get('fields') or [])]
            title = d.get('title') or d.get('name') or next((v for k, v in fields if str(k).lower() in ('number', 'code', 'subject', 'name', 'title') and v), str(found['id']))
            status = next((v for k, v in fields if str(k).lower() in ('status', 'stage')), '')
            hist_lines = []
            kind_label = d.get('label') or d.get('verbose_name') or found['label'].split('.')[-1]
    except apicall.ApiError as e:
        if e.status in (403, 404):
            return {'text': tr('You may not open this record.', lang=L), 'source': 'check', 'intent': 'record'}
        raise
    rows = []
    for k, v in fields:
        pv = _plain(v)
        if not pv or len(pv) > 300:
            continue
        lab = tr(str(k).replace('_', ' ').capitalize(), lang=L) if '_' in str(k) or str(k).islower() else str(k)
        rows.append([re.sub(r'\b(Sla|Amc|Vat|Ppm|Hse|Crm|Id)\b', lambda x: x.group(1).upper(), lab), pv])
        if len(rows) >= 16:
            break
    text = tr('**{0}** – {1} ({2}).', title, kind_label, found.get('app') or '', lang=L)
    if status:
        text += ' ' + tr('Status: **{0}**.', status, lang=L)
    if hist_lines:
        text += '\n\n' + tr('Last steps:', lang=L) + '\n' + '\n'.join('- ' + x for x in hist_lines)
    return {'text': text, 'source': 'data', 'intent': 'record',
            'cards': [{'type': 'table', 'title': f'{kind_label} {title}', 'columns': [tr('Field', lang=L), tr('Value', lang=L)], 'rows': rows}],
            'links': [{'label': tr('Open {0}', title, lang=L), 'url': found['page']}]}


# ------------------------------------------------------------------ setup check of an app
def _item(level, text, link=''):
    return {'level': level, 'text': text, 'link': link}


def _kit_check(request, c, key, lang):
    L = lang
    label, title, _ = APPS[key]
    a = kit_apps().get(label)
    if a is None:
        return None
    items = []
    try:
        info = apicall.call(request, 'get', f'/{a.slug}/api/app/')
    except apicall.ApiError as e:
        return {'text': tr('You may not see the setup of {0}.', title, lang=L), 'source': 'check', 'intent': 'app_setup'} if e.status in (401, 403) else None
    if not info.get('can_setup'):   # the setup of an app is for its manager and the administrator
        return {'text': tr('You may not see the setup of {0}.', title, lang=L), 'source': 'check', 'intent': 'app_setup'}
    setup = f'{a.page}setup'
    if not info.get('activated'):
        items.append(_item('block', tr('{0} is not activated yet – choose the industry and activate it (the ready lists are added).', title, lang=L), setup))
        return items
    if info.get('to_review'):
        items.append(_item('warn', tr('{0} rows of the industry pack are waiting for review.', info['to_review'], lang=L), setup))
    for code, rlabel in a.roles:
        try:
            n = len(list(a.users_with_role(code)))
        except Exception:
            n = 0
        if n == 0 and code.endswith(('_manager',)):
            items.append(_item('block', tr('Nobody has the role "{0}".', rlabel, lang=L), MS + 'settings/user-rights'))
        elif n == 0 and not code.endswith(('_viewer', '_auditor')):
            items.append(_item('info', tr('Nobody has the role "{0}".', rlabel, lang=L), MS + 'settings/user-rights'))
    for kind, r in a.regs.items():
        if r.group != 'Setup' or r.child_of:
            continue
        try:
            n = r.model.objects.count()
        except Exception:
            continue
        if n == 0:
            items.append(_item('warn', tr('{0}: none yet.', r.plural if isinstance(r.plural, str) else r.label, lang=L), f'{a.page}r/{kind}'))
    return items


def _projects_check(request, c, lang):
    L = lang
    items = []
    base = MS + 'zeo-projects/'
    try:
        lk = apicall.call(request, 'get', '/zeo-projects/api/lookups/')
    except apicall.ApiError as e:
        return {'text': tr('You may not see the setup of {0}.', 'Projects', lang=L), 'source': 'check', 'intent': 'app_setup'} if e.status in (401, 403) else None
    for key, text, link in (('customers', 'No customers yet.', base + 'customers'), ('teams', 'No support teams yet.', base + 'setup'),
                            ('products', 'No service products yet.', base + 'setup')):
        if not lk.get(key):
            items.append(_item('warn', tr(text, lang=L), link))
    try:
        st = apicall.call(request, 'get', '/zeo-projects/api/settings/')
        if not st.get('default_team'):
            items.append(_item('warn', tr('No default support team – new tickets are not assigned.', lang=L), base + 'setup'))
        if not st.get('default_sla'):
            items.append(_item('warn', tr('No default SLA – new tickets have no response and resolution times.', lang=L), base + 'setup'))
    except apicall.ApiError:
        pass
    for kind, text in (('categories', 'No issue categories yet.'), ('sla-policies', 'No SLA policies yet.')):
        try:
            if not apicall.call(request, 'get', f'/zeo-projects/api/masters/{kind}/'):
                items.append(_item('warn', tr(text, lang=L), base + 'setup'))
        except apicall.ApiError:
            pass
    return items


def _hse_check(request, c, lang):
    L = lang
    items = []
    base = MS + 'zeo-hse/setup/'
    try:
        a = apicall.call(request, 'get', '/zeo-hse/api/activate/')
    except apicall.ApiError as e:
        return {'text': tr('You may not see the setup of {0}.', 'HSE', lang=L), 'source': 'check', 'intent': 'app_setup'} if e.status in (401, 403) else None
    if not a.get('can_activate'):
        return {'text': tr('You may not see the setup of {0}.', 'HSE', lang=L), 'source': 'check', 'intent': 'app_setup'}
    if not a.get('activated'):
        items.append(_item('block', tr('{0} is not activated yet – choose the industry and activate it (the ready lists are added).', 'HSE', lang=L), base + 'pack'))
        return items
    for kind, text, link in (('sites', 'No HSE sites yet.', base + 'sites'), ('permit-types', 'No permit types yet.', base + 'sites'),
                             ('categories', 'No incident categories yet.', base + 'settings'), ('templates', 'No inspection forms yet.', base + 'sites')):
        try:
            rows = apicall.call(request, 'get', f'/zeo-hse/api/masters/{kind}/')
            if not (rows.get('rows') if isinstance(rows, dict) else rows):
                items.append(_item('warn', tr(text, lang=L), link))
        except apicall.ApiError:
            pass
    return items


def setup_check(request, c, key, lang):
    L = lang
    title = APPS[key][1]
    if not _active(key):
        return {'text': tr('{0} is not switched on for this company (Settings › Apps).', title, lang=L), 'source': 'check', 'intent': 'app_setup',
                'links': [{'label': tr('Settings › Apps', lang=L), 'url': MS + 'settings/apps'}]}
    if key == 'projects':
        items = _projects_check(request, c, L)
    elif key == 'hse':
        items = _hse_check(request, c, L)
    else:
        items = _kit_check(request, c, key, L)
    if isinstance(items, dict) or items is None:
        return items
    blocks = sum(1 for x in items if x['level'] == 'block')
    warns = sum(1 for x in items if x['level'] == 'warn')
    if not items:
        text = tr('**{0}** is set up: nothing missing found.', title, lang=L)
    else:
        text = tr('**{0} setup:** {1} item(s) to do before use, {2} to look at.', title, blocks, warns, lang=L)
    return {'text': text, 'source': 'check', 'intent': 'app_setup',
            'cards': [{'type': 'checks', 'title': tr('{0} setup', title, lang=L), 'items': items, 'block_label': tr('To do first', lang=L)}] if items else []}


# ------------------------------------------------------------------ my work in every app
PJ_LISTS = (('tickets', 'My tickets', 'zeo-projects/tickets'), ('unassigned', 'Tickets to assign', 'zeo-projects/tickets'),
            ('tasks', 'My project tasks', 'project-options/project-tasks'), ('dev_requests', 'Development requests', 'zeo-projects/dev-requests'))
HSE_LISTS = (('approvals', 'HSE approvals', 'zeo-hse/my'), ('to_issue', 'Permits to issue', 'zeo-hse/permits'), ('actions', 'Corrective actions', 'zeo-hse/my'),
             ('verify', 'Actions to verify', 'zeo-hse/my'), ('permits', 'My permits', 'zeo-hse/permits'),
             ('investigations', 'Investigations', 'zeo-hse/my'), ('other', 'Other HSE items', 'zeo-hse/my'))


def my_work(request, c, lang):
    """What waits for the user in every active app (the apps' own "My work" lists, with the user's rights)."""
    L = lang
    rows, links = [], []
    for key, (label, title, _) in APPS.items():
        if not _active(key):
            continue
        try:
            if label:
                a = kit_apps().get(label)
                if a is None or not a.activated():
                    continue
                for grp in apicall.call(request, 'get', f'/{a.slug}/api/my/') or []:
                    for r in (grp.get('rows') or [])[:10]:
                        rows.append([tr(title, lang=L), tr(grp.get('title') or '', lang=L), r.get('title') or '', r.get('badge') or r.get('sub') or ''])
                    if grp.get('rows'):
                        links.append({'label': f"{tr(title, lang=L)} › {tr(grp.get('title') or '', lang=L)}", 'url': f"{a.page}r/{grp.get('kind')}"})
            elif key == 'projects':
                d = apicall.call(request, 'get', '/zeo-projects/api/my/')
                for k, gt, page in PJ_LISTS:
                    for r in (d.get(k) or [])[:10]:
                        rows.append([tr(title, lang=L), tr(gt, lang=L), f"{r.get('number') or ''} {r.get('subject') or r.get('title') or r.get('name') or ''}".strip(),
                                     r.get('priority_label') or r.get('stage_label') or r.get('status_label') or ''])
                    if d.get(k):
                        links.append({'label': f"{tr(title, lang=L)} › {tr(gt, lang=L)}", 'url': MS + page})
            elif key == 'hse':
                d = apicall.call(request, 'get', '/zeo-hse/api/my/')
                for k, gt, page in HSE_LISTS:
                    for r in (d.get(k) or [])[:10]:
                        rows.append([tr(title, lang=L), tr(gt, lang=L), f"{r.get('number') or ''} {r.get('title') or ''}".strip(), r.get('tag') or r.get('status_label') or ''])
                    if d.get(k):
                        links.append({'label': f"{tr(title, lang=L)} › {tr(gt, lang=L)}", 'url': MS + page})
        except apicall.ApiError:
            continue
        except Exception:
            log.info('Ask ZEO my work %s', key, exc_info=True)
            continue
    if not rows:
        return {'text': tr('Nothing is waiting for you in the other apps.', lang=L), 'source': 'data', 'intent': 'my_work'}
    return {'text': tr('**{0}** item(s) wait for you in the apps:', len(rows), lang=L), 'source': 'data', 'intent': 'my_work',
            'cards': [{'type': 'table', 'title': tr('Waiting for you', lang=L), 'columns': [tr('App', lang=L), tr('List', lang=L), tr('Record', lang=L), tr('Note', lang=L)],
                       'rows': rows[:60]}],
            'links': links[:8]}


def route(request, c, conv, question, cq, intent, suite, lang):
    """1.73.0: the answers of this module, or None (the other answers then run)."""
    if intent == 'my_work':
        return my_work(request, c, lang)
    if intent == 'app_setup':
        key = app_named(cq) or (suite if suite in APPS else None)
        if key:
            return setup_check(request, c, key, lang)
        return None
    if intent == 'record' or re.search(r'\b(explain|status of|tell me about|details of)\b', cq):
        try:
            return explain_record(request, c, question, lang)
        except apicall.ApiError as e:
            return {'text': e.detail, 'source': 'check', 'intent': 'record'}
    return None
