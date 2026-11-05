"""v1.67.1 (companies) – API of Make inactive / Make active and Delete for companies and own databases.

    GET  /cloud/api/companies/<id>/life/?counts=1   owner / company administrator: state, history, what a deletion removes
    POST /cloud/api/companies/<id>/life/            {action: inactive | active}
    POST /cloud/api/companies/<id>/delete/          {confirm: <the exact company name>} → {download, file_name}
    GET  /cloud/api/deleted-backups/<token>/        the backup (.zip) – only for the person who deleted, for 24 hours
    POST /cloud/api/accounts/<id>/databases/<key>/  {action: inactive | active | delete (confirm: <database name>)} (views.py)
    GET  /cloud/api/console/deleted/                Adept: deleted companies / databases that can still be restored
    POST /cloud/api/console/deleted/                Adept: {company: <schema>} | {database: <key>} → restored
"""
import logging
import os
import tempfile

from django.http import FileResponse
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import lifecycle as L
from .services import tr

log = logging.getLogger(__name__)


def _company(request, pk):
    """The company if the user may manage it (owner / company administrator), else (None, Response)."""
    from UserManagement.models import company as Company
    comp = Company.objects.filter(pk=pk).exclude(schema_name__in=('public', 'zeo_template')).first()
    if comp is None or L.state_of(comp.schema_name) == 'deleted':
        return None, Response({'detail': tr('Not found.')}, status=404)
    if not L.can_manage(request.user, comp):
        visible = request.user.tenants.filter(pk=comp.pk).exists()
        return None, Response({'detail': tr('Only the owner or a company administrator can do this.') if visible else tr('Not found.')},
                              status=403 if visible else 404)
    return comp, None


def life_json(comp, with_counts=False):
    life = L.life_of(comp, create=False)
    st = L.state_of(comp.schema_name)
    out = {'id': comp.pk, 'name': comp.name, 'schema_name': comp.schema_name, 'state': st,
           'inactive_by_database': st == 'inactive' and not (life and life.state == 'inactive'),
           'history': [{k: h.get(k) for k in ('at', 'action', 'by', 'note')} for h in L.history_json(life)][:50],
           'keep_days': L.KEEP_DAYS}
    if with_counts:
        out['counts'] = L.counts(comp)
    return out


class CompanyLifeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        comp, err = _company(request, pk)
        if err:
            return err
        return Response(life_json(comp, with_counts=str(request.GET.get('counts') or '') in ('1', 'true')))

    def post(self, request, pk):
        comp, err = _company(request, pk)
        if err:
            return err
        a = str(request.data.get('action') or '')
        if a not in ('inactive', 'active'):
            return Response({'detail': tr('Unknown action.')}, status=400)
        try:
            L.set_active(comp, a == 'active', request.user)
        except L.LifeError as e:
            return Response({'detail': e.message}, status=e.status)
        return Response(life_json(comp))


class CompanyDeleteView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        comp, err = _company(request, pk)
        if err:
            return err
        try:
            r = L.delete_company(request.user, comp, request.data.get('confirm'))
        except L.LifeError as e:
            body = {'detail': e.message}
            if e.extra.get('field'):
                body[e.extra['field']] = [e.message]
            return Response(body, status=e.status)
        return Response({'deleted': True, 'name': comp.name, 'download': f'/cloud/api/deleted-backups/{r["token"]}/',
                         'file_name': r['file_name'],
                         'detail': tr('{0} was deleted. Its backup is downloading; the server keeps a copy too.', comp.name)})


class DeletedBackupView(APIView):
    """The backup made before a deletion, as one .zip – for the person who deleted, for 24 hours."""
    permission_classes = [IsAuthenticated]

    def get(self, request, token):
        d = L.read_token(token)
        if not d or d.get('u') != request.user.pk:
            return Response({'detail': tr('This download link has expired.')}, status=404)
        root = os.path.realpath(L.backup_root())
        folder = os.path.realpath(os.path.join(root, os.path.basename(str(d.get('f') or ''))))
        if not folder.startswith(root + os.sep) or not os.path.isfile(os.path.join(folder, 'manifest.json')):
            return Response({'detail': tr('The backup is no longer on the server.')}, status=404)
        fd, tmp = tempfile.mkstemp(suffix='.zip')
        os.close(fd)
        try:
            L.backup_zip(folder, tmp, d.get('n') or '')
            fh = open(tmp, 'rb')
        finally:
            try:
                os.remove(tmp)   # the open file stays readable until the response is sent
            except OSError:
                pass
        name = d.get('n') or 'zeo-backup.zip'
        return FileResponse(fh, as_attachment=True, filename=name, content_type='application/zip')


class ConsoleDeletedView(APIView):
    """Adept (server administrator): restore a deleted company or database within 30 days."""
    permission_classes = [IsAuthenticated]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        from .services import is_platform_admin
        if not is_platform_admin(request.user):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied(tr('The control console is for Adept staff only.'))

    def get(self, request):
        return Response(L.deleted_list())

    def post(self, request):
        try:
            if request.data.get('database'):
                r = L.restore_database(str(request.data['database']), by=request.user)
                msg = tr('Database restored: {0} ({1} companies).', L.db_title(r['server']), len(r['companies']))
            else:
                life = L.restore_company(str(request.data.get('company') or ''), by=request.user)
                msg = tr('Company restored: {0}.', life.company.name)
        except L.LifeError as e:
            return Response({'detail': e.message}, status=e.status)
        return Response(dict(L.deleted_list(), detail=msg))
