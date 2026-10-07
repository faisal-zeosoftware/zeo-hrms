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
        return Response(services.ess_summary(request.user))


class TeamDashboardView(APIView):
    """GET dashboard/api/team/?scope=team|company&department=<id> – manager / HR dashboard."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        scope = request.query_params.get('scope') or ('company' if services.is_admin(request.user) else 'team')
        return Response(services.team_summary(request.user, scope, request.query_params.get('department') or None))


class DrillView(APIView):
    """GET dashboard/api/drill/?metric=...&scope=...&module=...  – rows behind any tile or chart bar."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        metric = request.query_params.get('metric', '')
        return Response(services.drill(request.user, metric, request.query_params))


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
        data['me'] = my_space(request)
        return Response(data)
