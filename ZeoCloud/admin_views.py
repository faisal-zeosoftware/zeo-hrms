"""v1.67.7 (admin) – Settings › Users: "Administrator in this company" (on / off) and the owner badge.

GET  /users/company-admins/?schema=X   {owner_id, can_change, rows: [{id, username, email, admin, owner, invited, system}]}
POST /users/company-admins/?schema=X   {user, admin: true|false} → the row; 403 / 404 / 409 with the reason when refused
(not the owner or an administrator of the company, the owner's rights by someone else, the last administrator).
"""
from django.db import connection
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from Translations.i18n import tr

from . import companyadmin as CA


def _schema(request):
    s = getattr(connection, 'schema_name', '') or ''
    if s in ('', 'public'):
        s = request.query_params.get('schema') or request.META.get('HTTP_X_SCHEMA') or ''
    if not s or s == 'public':
        return ''
    u = request.user
    if not (u.is_superuser or u.tenants.filter(schema_name=s).exists()):
        return ''
    return s


def _row(u, schema, owner, invited):
    return {'id': u.pk, 'username': u.username, 'email': u.email, 'name': u.get_full_name() or u.username,
            'admin': CA.is_admin(u.pk, schema), 'owner': u.pk == owner, 'invited': u.pk in invited,
            'system': bool(u.pk in CA._load()['sys'])}


def _invited(schema):
    from .models import CompanyAccess
    return set(CompanyAccess.objects.filter(company__schema_name=schema).values_list('user_id', flat=True))


class CompanyAdminsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        schema = _schema(request)
        if not schema:
            return Response({'detail': tr('You do not have access to this company.')}, status=403)
        owner = CA.owner_id(schema)
        inv = _invited(schema)
        rows = [_row(u, schema, owner, inv) for u in CA.members(schema)]
        return Response({'owner_id': owner, 'can_change': CA.can_manage(request.user, schema), 'rows': rows,
                         'admins': sum(1 for r in rows if r['admin'])})

    def post(self, request):
        schema = _schema(request)
        if not schema:
            return Response({'detail': tr('You do not have access to this company.')}, status=403)
        uid = request.data.get('user')
        if not str(uid or '').isdigit():
            return Response({'detail': tr('Choose a user.')}, status=400)
        on = str(request.data.get('admin')).lower() in ('1', 'true', 'yes', 'on')
        try:
            res = CA.set_admin(int(uid), schema, on, by=request.user, request=request)
        except CA.AdminError as e:
            return Response({'detail': e.msg, 'code': e.code}, status=e.status)
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.get(pk=int(uid))
        out = _row(u, schema, CA.owner_id(schema), _invited(schema))
        out['changed'] = res['changed']
        out['detail'] = (tr('{0} is now an administrator of this company.', out['name']) if on
                         else tr('{0} is no longer an administrator of this company.', out['name']))
        return Response(out)
