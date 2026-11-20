"""v1.75.1 – progress of the payroll calculations (see run_progress.py / run_background.py).

GET  api/run-progress/?token=        one calculation
GET  api/run-progress/               the company's calculations running now (or ended in the last 10 minutes)
POST api/run-progress/continue/<run>/  continue a stopped calculation – only the employees without a payslip
"""
from django.apps import apps
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import run_background as BG
from . import run_progress as RP


def _can(user, *perms):
    return bool(user and (user.is_superuser or any(user.has_perm('PayrollManagement.' + p) for p in perms)))


class RunProgressView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _can(request.user, 'view_payrollrun', 'add_payrollrun', 'change_payrollrun'):
            return Response({'detail': 'You do not have the right to see the payroll runs.'}, status=403)
        BG.kick_waiting()
        tok = request.query_params.get('token')
        if tok:
            p = RP.get(tok)
            return Response(p or {'token': RP.clean_token(tok), 'status': 'unknown', 'total': 0, 'done': 0})
        return Response({'jobs': RP.active()})


class RunContinueView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        if not (_can(request.user, 'add_payrollrun') and _can(request.user, 'change_payrollrun')):
            return Response({'detail': 'You do not have the right to calculate this payroll.'}, status=403)
        run = apps.get_model('PayrollManagement', 'PayrollRun').objects.filter(pk=pk).first()
        if run is None:
            return Response({'detail': 'This payroll run does not exist.'}, status=404)
        try:
            from AccessControl.access import ctx, run_in_branches
            c = ctx(request)
            if not run_in_branches(c.branches, run):
                return Response({'detail': 'This payroll run does not exist.'}, status=404)
        except Exception:   # noqa: BLE001
            pass
        if not BG.enabled():
            return Response({'detail': 'Background calculation is switched off on this server (ZEO_PAYROLL_BACKGROUND=off).'}, status=409)
        try:
            token, left = BG.continue_run(run, request.user, request.headers.get('X-Zeo-Progress'))
        except BG.Busy as ex:
            return Response({'detail': str(ex)}, status=409)
        return Response({'background': True, 'progress_token': token, 'employees': left})
