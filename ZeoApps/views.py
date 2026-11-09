"""v1.21.0 – Settings → Apps: which apps the company uses, with the industry each one is set up for."""
from django.db import connection
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from zeo.appkit.engine import CompanyMember

from . import services as S


def _ctx(request):
    try:
        from AccessControl.access import ctx
        return ctx(request)
    except Exception:
        return None


def can_manage(request):
    c = _ctx(request)
    if c is not None and c.admin:
        return True
    u = request.user
    return bool(getattr(u, 'is_superuser', False) or (c is not None and 'manage_apps' in getattr(c, 'codes', set())))


class Base(APIView):
    zeo_access = False   # rights are checked here
    zeo_scope = False
    permission_classes = [CompanyMember]


class AppsView(Base):
    """GET – every app of ZEO: installed, active, industry (and the company industry, which is each app's default)."""

    def get(self, request):
        code, label = S.company_industry()
        lite = request.query_params.get('lite')
        if lite:
            out = {'active': S.active_keys(), 'can_manage': can_manage(request)}
            try:   # v1.55.0: pay frequencies switched on in Payroll controls (menu "Pay calendars" shows only then)
                from PayrollPlus.models import PayrollPlusSettings
                out['pay_cycles'] = PayrollPlusSettings.get().pay_frequencies()
            except Exception:
                out['pay_cycles'] = []
            try:   # v1.56.1: a login with no rights and no employee record sees only Home and its own profile in the menu
                from AccessControl.access import ctx
                c = ctx(request)
                out['bare_login'] = bool(c is not None and not c.admin and not c.codes and c.emp is None)
            except Exception:
                out['bare_login'] = False
            return Response(out)
        return Response({'apps': S.all_states(), 'company_industry': code, 'company_industry_label': label,
                         'industries': S.company_industries(), 'can_manage': can_manage(request), 'company': connection.schema_name,
                         'core': S.tr_(S.CORE_TEXT), 'depends': S.DEPENDS})   # v1.67.3 (apps)


class ActivateView(Base):
    """POST {industry} – activate the app (default industry: the company master's)."""

    def post(self, request, key):
        if not can_manage(request):
            return Response({'detail': 'Only a company administrator can activate apps.'}, status=status.HTTP_403_FORBIDDEN)
        try:
            added = S.activate(key, (request.data or {}).get('industry') or None, request.user)
        except S.AppsError as e:
            return Response({'detail': e.message}, status=e.status)
        out = S.state(key)
        out['added'] = added
        return Response(out)


class NumberingView(Base):
    """v1.67.6 (setup) item 4 – GET the document types of the active apps and the series still missing;
    POST adds the missing series (administrator or the add right of Document numbering). Existing series never change."""

    def _can(self, request, verb):
        c = _ctx(request)
        return bool(c is not None and (c.admin or f'{verb}_documentnumbering' in c.codes))

    def get(self, request):
        from . import numbering as N
        if not self._can(request, 'view'):
            return Response({'detail': S.tr_('You may not see the document numbering.')}, status=status.HTTP_403_FORBIDDEN)
        if request.query_params.get('list'):   # the choices of the form (the active apps only)
            return Response(N.types_json())
        return Response({'types': N.types_json(), 'missing': N.ensure(apply=False)})

    def post(self, request):
        from . import numbering as N
        if not self._can(request, 'add'):
            return Response({'detail': S.tr_('You may not add document numbering.')}, status=status.HTTP_403_FORBIDDEN)
        made = N.ensure(apply=True, user=request.user)
        return Response({'added': made, 'detail': S.tr_('{0} numbering series added.', len(made))})


class AppSetupView(Base):
    """v1.67.6 (setup) item 5 – GET the setup wizard of an app; POST {step, status: done|skipped|open} or {allow: true|false}."""

    def get(self, request, key):
        from . import appsetup as A
        if key not in A.APPS_WITH_WIZARD:
            return Response({'detail': S.tr_('This app has no setup wizard (HR and Payroll use the setup guide).')}, status=404)
        out = A.full(key)
        out['can_manage'] = can_manage(request)
        out['title'] = S.tr_(S.BY_KEY[key][2])
        return Response(out)

    def post(self, request, key):
        from . import appsetup as A
        if key not in A.APPS_WITH_WIZARD:
            return Response({'detail': S.tr_('This app has no setup wizard (HR and Payroll use the setup guide).')}, status=404)
        if not can_manage(request):
            return Response({'detail': S.tr_('Only a company administrator can change the setup of an app.')}, status=status.HTTP_403_FORBIDDEN)
        d = request.data or {}
        try:
            if 'allow' in d:
                out = A.set_allow(key, str(d.get('allow')).lower() in ('1', 'true'), request.user)
            else:
                out = A.mark(key, d.get('step'), d.get('status'), request.user)
        except ValueError as e:
            return Response({'detail': str(e)}, status=400)
        out['can_manage'] = True
        out['title'] = S.tr_(S.BY_KEY[key][2])
        return Response(out)


class DeactivateView(Base):
    def post(self, request, key):
        if not can_manage(request):
            return Response({'detail': 'Only a company administrator can deactivate apps.'}, status=status.HTTP_403_FORBIDDEN)
        try:
            S.deactivate(key, request.user)
        except S.AppsError as e:
            return Response({'detail': e.message}, status=e.status)
        return Response(S.state(key))
