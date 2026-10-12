"""ESS / manager dashboard API (read-only). All endpoints take ?schema=<company> like the rest of ZEO."""
from django.apps import apps
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services


class EssDashboardView(APIView):
    """GET dashboard/api/ess/ – the logged-in employee's own dashboard (all modules)."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from django.utils import timezone
        from .period import resolve
        a, b, preset = resolve(request.query_params, timezone.localdate())   # v1.16.0 dashboard period
        data = services.ess_summary(request.user, a, b)
        data['period'] = {'from': a, 'to': b, 'preset': preset}
        return Response(data)


class TeamDashboardView(APIView):
    """GET dashboard/api/team/?scope=team|company&department=<id> – manager / HR dashboard."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        scope = request.query_params.get('scope') or ('company' if services.is_admin(request.user) else 'team')
        from django.utils import timezone
        from .period import resolve
        a, b, preset = resolve(request.query_params, timezone.localdate())   # v1.16.0 dashboard period
        data = services.team_summary(request.user, scope, request.query_params.get('department') or None, a, b)
        data['period']['preset'] = preset
        return Response(data)


class DrillView(APIView):
    """GET dashboard/api/drill/?metric=...&scope=...&module=...  – rows behind any tile or chart bar."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        metric = request.query_params.get('metric', '')
        return Response(services.drill(request.user, metric, request.query_params, request))


class Employee360View(APIView):
    """GET dashboard/api/employee/<id>/ – one employee across all modules (self, own team or HR admin)."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        Emp = apps.get_model('EmpManagement', 'emp_master')
        emp = Emp.objects.filter(pk=pk).select_related('emp_dept_id', 'emp_desgntn_id', 'emp_reporting_manager').first()
        if not emp:
            return Response({'detail': 'Employee not found.'}, status=404)
        if not services.can_view_employee(request.user, emp):
            return Response({'detail': 'You can only open your own team.'}, status=403)
        return Response(services.employee_360(request.user, emp))


class OverviewView(APIView):
    """GET dashboard/api/overview/ – main dashboard (v1.7.0): one card per module the user may see, plus 'My space'."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from .overview import my_space, overview
        data = overview(request)
        _localize_links(data, request)
        if not request.query_params.get('card') and request.query_params.get('me') != '0':   # v1.16.0: ?card=<key>&from&to reloads one card
            data['me'] = my_space(request)   # v1.17.0: ?me=0 – the HR operations view (the user's own items are on My dashboard)
        return Response(data)


def _localize_links(data, request):
    """v1.16.0: department / branch … names in the drill-down filters of the dashboard links in the request language,
    the way the report rows are translated (Translations.reports), so a click still finds its rows in Arabic."""
    try:
        from urllib.parse import parse_qsl, urlencode
        from Translations.reports import _lang, _name
    except ImportError:
        return
    lang = _lang(request)
    if not lang:
        return

    def fix(link):
        if not isinstance(link, str) or '/report-options/r/' not in link or '?' not in link:
            return link
        path, q = link.split('?', 1)
        pairs = [(k, '|'.join(_name(k[2:], x, lang, request) for x in v.split('|')) if k.startswith('f_') else v) for k, v in parse_qsl(q)]
        return path + '?' + urlencode(pairs)
    for c in data.get('cards') or []:
        c['route'] = fix(c.get('route'))
        for k in (c.get('kpis') or []) + (c.get('items') or []):
            k['link'] = fix(k.get('link'))
