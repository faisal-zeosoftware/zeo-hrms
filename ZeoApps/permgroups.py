"""v1.67.6 (setup) item 10 – Settings › Permission groups in the new design.

The old screen (a pop-up of 2 800 lines with hand-picked sections, every app shown) is replaced by the shared list + form +
confirm. Writes still go through `/organisation/api/Group/` (its company isolation of shared groups is kept); this module gives
the screen what it needs:

    GET /zeo-apps/api/permission-groups/          the company's groups: name, kind (standard / own copy / own), users, rights
    GET /zeo-apps/api/permission-catalogue/       every right of the ACTIVE apps (item 7), per app and record type:
                                                  view / add / change / delete + the special rights of the record type
"""
from django.apps import apps as djapps
from django.contrib.auth.models import Permission
from django.db import connection
from rest_framework.response import Response
from rest_framework.views import APIView

from zeo.appkit.engine import CompanyMember

from . import services as S

# Django app label → ZEO app (unknown labels belong to the company core "Company & users")
APP_OF_LABEL = {}
try:
    from ZeoFlow.registry import MODULES as _MOD
    for _k, _labels in _MOD.items():
        for _l in _labels:
            APP_OF_LABEL[_l] = _k
except Exception:   # noqa: BLE001
    pass
APP_OF_LABEL.update({'ZeoHR': 'hrms', 'ZeoPeople': 'people', 'ZeoMasters': 'settings', 'OrganisationManager': 'settings'})
CORE_TITLE = 'Company & users'
SKIP_LABELS = {'admin', 'auth', 'contenttypes', 'sessions', 'django_tenants', 'tenants', 'permissions', 'oauth2_provider', 'token_blacklist',
               'django_celery_beat', 'django_celery_results', 'corsheaders', 'rest_framework', 'authtoken', 'Translations', 'LanguagePack',
               'ZeoCloud', 'ZeoLicence', 'ZeoGroup', 'SavedViews', 'Core'}
VERBS = ('view', 'add', 'change', 'delete')


def _can(request, verb='view'):
    try:
        from AccessControl.access import ctx
        c = ctx(request)
        return bool(c.admin or f'{verb}_group' in c.codes)
    except Exception:   # noqa: BLE001
        return False


def _active_apps():
    out = {'settings'}
    for k in S.KEYS:
        try:
            if S.is_active(k):
                out.add(k)
        except Exception:   # noqa: BLE001
            pass
    return out


def catalogue():
    act = _active_apps()
    titles = {a[0]: a[2] for a in S.APPS}
    titles['settings'] = CORE_TITLE
    groups = {}
    perms = Permission.objects.select_related('content_type').order_by('content_type__app_label', 'content_type__model', 'codename')
    for p in perms:
        label, model = p.content_type.app_label, p.content_type.model
        if label in SKIP_LABELS:
            continue
        app = APP_OF_LABEL.get(label, 'settings')
        if app not in act:
            continue
        a = groups.setdefault(app, {})
        m = a.get((label, model))
        if m is None:
            try:
                mc = djapps.get_model(label, model)
                nice = str(mc._meta.verbose_name)
            except LookupError:
                nice = model
            m = a[(label, model)] = {'key': f'{label}.{model}', 'label': nice[:1].upper() + nice[1:], 'rights': {}, 'other': []}
        verb = p.codename.split('_', 1)[0]
        if verb in VERBS and p.codename == f'{verb}_{model}':
            m['rights'][verb] = p.pk
        else:
            m['other'].append({'id': p.pk, 'label': p.name})
    out = []
    order = [k for k in [a[0] for a in S.APPS] + ['settings'] if k in groups]
    for k in order:
        models = sorted(groups[k].values(), key=lambda x: x['label'].lower())
        out.append({'key': k, 'title': titles.get(k, k), 'models': models,
                    'count': sum(len(m['rights']) + len(m['other']) for m in models)})
    return out


class Base(APIView):
    zeo_access = False   # rights are checked here
    zeo_scope = False
    permission_classes = [CompanyMember]


class CatalogueView(Base):
    def get(self, request):
        if not _can(request):
            return Response({'detail': S.tr_('You may not see the permission groups.')}, status=403)
        return Response({'apps': catalogue()})


class GroupsView(Base):
    def get(self, request):
        if not _can(request):
            return Response({'detail': S.tr_('You may not see the permission groups.')}, status=403)
        from django.contrib.auth.models import Group
        from tenant_users.permissions.models import UserTenantPermissions as UTP
        from . import groupscope as GS
        try:   # v1.67.6 (setup) item 12: the "Company administrators" group is there and linked (made once, reused after)
            from .adminbranches import ensure_group, _option_ids
            if not _option_ids():
                ensure_group()
        except Exception:   # noqa: BLE001
            pass
        ids = GS.visible_ids()
        rows = GS.rows()
        users = {}
        through = UTP.groups.through
        mine = UTP.objects.filter(profile__tenants__schema_name=connection.schema_name).values_list('id', flat=True)
        for gid in through.objects.filter(group_id__in=ids, usertenantpermissions_id__in=mine).values_list('group_id', flat=True):
            users[gid] = users.get(gid, 0) + 1
        act_perm = {p for a in catalogue() for m in a['models'] for p in list(m['rights'].values()) + [o['id'] for o in m['other']]}
        out = []
        for g in Group.objects.filter(pk__in=ids).prefetch_related('permissions'):
            pids = [p.pk for p in g.permissions.all()]
            own_copy = bool((rows.get(g.pk) or (None, ''))[0])
            std = GS.is_standard_name(g.name)
            out.append({'id': g.pk, 'name': GS.label(g), 'kind': 'copy' if own_copy else ('standard' if std else 'own'),
                        'users': users.get(g.pk, 0), 'rights': len([p for p in pids if p in act_perm]), 'rights_all': len(pids),
                        'admin_all_branches': g.pk in _all_ids()})
        out.sort(key=lambda r: r['name'].lower())
        return Response({'rows': out, 'can_add': _can(request, 'add'), 'can_change': _can(request, 'change'), 'can_delete': _can(request, 'delete')})


def _all_ids():
    from .adminbranches import _option_ids
    return set(_option_ids())
