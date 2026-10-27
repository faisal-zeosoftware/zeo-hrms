"""v1.62.0 workbook – Settings › Data workbook API (company administrators; a token or an API key of one works the same):

GET    /tools/api/workbooks/                       history: uploads with their last check / load
POST   /tools/api/workbooks/                       multipart "file" (.xlsx) → {id, file_name, sheets, rows}
GET    /tools/api/workbooks/<id>/                  the upload, its last run (state, progress, totals, per sheet, errors) and all runs
POST   /tools/api/workbooks/<id>/check/            {"quick": false, "only": ["Employees"]} → the run (a trial that is rolled back)
POST   /tools/api/workbooks/<id>/load/             {"from_month": "2026-08", "only": [...]} → the run (background)
POST   /tools/api/workbooks/<id>/stop/             stop the running check / load after the current row
GET    /tools/api/workbooks/<id>/errors/[?run=N]   the error report (.xlsx) of the last (or that) run
GET    /tools/api/workbooks/<id>/runs/<run>/       one run (?all=1 – every error, not only the first 300)
DELETE /tools/api/workbooks/<id>/                  remove an upload (its history stays in the runs of other uploads)
GET    /tools/api/workbooks/template/              the blank client template ZEO_Client_Data_Template_1.62.0.xlsx
"""
import os

from django.db import connection
from django.http import HttpResponse
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from Translations.i18n import tr

from . import workbook_jobs as J

XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def _admin(request):
    try:
        from AccessControl.access import ctx
        return bool(ctx(request).admin)
    except Exception:   # noqa: BLE001
        return bool(getattr(request.user, 'is_superuser', False))


class _Base(APIView):
    permission_classes = [IsAuthenticated]

    def initial(self, request, *a, **kw):
        super().initial(request, *a, **kw)
        from rest_framework.exceptions import PermissionDenied, ValidationError
        if connection.schema_name in ('', 'public'):
            raise ValidationError({'detail': tr('Open a company first (?schema=<company>).')})
        if not _admin(request):
            raise PermissionDenied(tr('Only a company administrator can load data workbooks.'))

    @staticmethod
    def upload(pk):
        return J.M('WorkbookUpload').objects.select_related('uploaded_by').filter(pk=pk).first()


def _file(data, name):
    r = HttpResponse(data, content_type=XLSX)
    r['Content-Disposition'] = f'attachment; filename="{name}"'
    r['Access-Control-Expose-Headers'] = 'Content-Disposition'
    return r


class WorkbookListView(_Base):
    def get(self, request):
        J.refresh_states()
        ups = J.M('WorkbookUpload').objects.select_related('uploaded_by').all()[:100]
        runs = J.M('WorkbookRun').objects.select_related('upload', 'created_by').all()[:200]
        hist = [{k: v for k, v in J.run_json(r).items() if k not in ('errors', 'sheets')} | {'file_name': r.upload.file_name} for r in runs]
        return Response({'results': [J.upload_json(u, runs=False) for u in ups], 'history': hist, 'template': J.TEMPLATE_NAME,
                         'runner': 'celery' if J.celery_ready() else 'process'})

    def post(self, request):
        f = request.FILES.get('file')
        if f is None:
            return Response({'detail': tr('Choose the workbook (.xlsx) to upload – form field "file".')}, status=400)
        try:
            up = J.save_upload(f, request.user)
        except ValueError as ex:
            return Response({'detail': tr(str(ex))}, status=400)
        return Response(J.upload_json(up), status=201)


class WorkbookDetailView(_Base):
    def get(self, request, pk):
        J.refresh_states()
        up = self.upload(pk)
        if up is None:
            return Response({'detail': tr('Not found.')}, status=404)
        return Response(J.upload_json(up, full=request.query_params.get('all') in ('1', 'true')))

    def delete(self, request, pk):
        up = self.upload(pk)
        if up is None:
            return Response({'detail': tr('Not found.')}, status=404)
        if up.runs.filter(state__in=('queued', 'running')).exists():
            return Response({'detail': tr('It is running – stop it first.')}, status=400)
        try:
            up.file.delete(save=False)
        except Exception:   # noqa: BLE001
            pass
        up.delete()
        return Response(status=204)


class WorkbookActionView(_Base):
    def post(self, request, pk, action):
        if action not in ('check', 'load', 'stop'):
            return Response({'detail': tr('Not found.')}, status=404)
        up = self.upload(pk)
        if up is None:
            return Response({'detail': tr('Not found.')}, status=404)
        d = request.data if hasattr(request.data, 'get') else {}
        only = d.get('only') or []
        if isinstance(only, str):
            only = [x.strip() for x in only.split(',') if x.strip()]
        names = {s['sheet'] for s in up.sheets or []}
        bad = [x for x in only if x not in names]
        if bad:
            return Response({'detail': tr('Not sheets of the Index: {0}', ', '.join(bad))}, status=400)
        if action == 'stop':
            run = up.runs.filter(state__in=('queued', 'running')).first()
            if run is None:
                return Response({'detail': tr('Nothing is running.')}, status=400)
            J.request_stop(run)
            return Response(J.run_json(J.M('WorkbookRun').objects.get(pk=run.pk)))
        opts = {'only': only} if only else {}
        if action == 'load':
            fm = str(d.get('from_month') or '').strip()[:7]
            if fm:
                import re
                if not re.fullmatch(r'\d{4}-\d{2}', fm):
                    return Response({'detail': tr('From month: write it as YYYY-MM (e.g. 2026-08).')}, status=400)
                opts['from_month'] = fm
            kind = 'load'
        else:
            kind = 'quick' if str(d.get('quick', '')).lower() in ('1', 'true') or d.get('quick') is True else 'check'
        try:
            run = J.start(up, kind, request.user, opts)
        except ValueError as ex:
            return Response({'detail': tr(str(ex))}, status=409)
        return Response(J.run_json(J.M('WorkbookRun').objects.select_related('created_by').get(pk=run.pk)), status=202)


class WorkbookRunView(_Base):
    def get(self, request, pk, run):
        J.refresh_states()
        r = J.M('WorkbookRun').objects.select_related('created_by').filter(pk=run, upload_id=pk).first()
        if r is None:
            return Response({'detail': tr('Not found.')}, status=404)
        return Response(J.run_json(r, full=request.query_params.get('all') in ('1', 'true')))


class WorkbookErrorsView(_Base):
    def get(self, request, pk):
        up = self.upload(pk)
        if up is None:
            return Response({'detail': tr('Not found.')}, status=404)
        qs = up.runs.select_related('upload', 'created_by')
        r = qs.filter(pk=request.query_params.get('run')).first() if request.query_params.get('run') else qs.exclude(state__in=('queued', 'running')).first()
        if r is None:
            return Response({'detail': tr('Check or load it first – the report is made by a finished run.')}, status=404)
        return _file(J.error_report(r), J.report_name(r))


class WorkbookTemplateView(_Base):
    def get(self, request):
        if not os.path.exists(J.TEMPLATE_PATH):
            return Response({'detail': tr('The template is not installed on this server.')}, status=404)
        with open(J.TEMPLATE_PATH, 'rb') as fh:
            return _file(fh.read(), J.TEMPLATE_NAME)
