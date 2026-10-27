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
                         'industries': S.company_industries(), 'can_manage': can_manage(request), 'company': connection.schema_name})


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


class DeactivateView(Base):
    def post(self, request, key):
        if not can_manage(request):
            return Response({'detail': 'Only a company administrator can deactivate apps.'}, status=status.HTTP_403_FORBIDDEN)
        try:
            S.deactivate(key, request.user)
        except S.AppsError as e:
            return Response({'detail': e.message}, status=e.status)
        return Response(S.state(key))
