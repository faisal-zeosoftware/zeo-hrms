"""v1.62.0 ui – user names for lists: the screens that still show a sign-in name (gdis_rajesh) show the person instead.
GET /tools/api/user-names/?schema=...  ->  [{"id": 710, "username": "gdis_rajesh", "name": "Rajesh Menon", "code": "GD-0003"}, ...]
Only names and employee codes (what every approval list already shows); no other employee data."""
from django.db import connection
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView


def _from_email(u):   # v1.62.0 ui: a user without an employee record (the company administrator)
    local = (u.email or '').split('@')[0] or u.username.split('_', 1)[-1]
    if local.lower() in ('admin', 'administrator', 'owner'):
        return 'Administrator'
    return ' '.join(w.capitalize() for w in local.replace('.', ' ').replace('_', ' ').split()) or u.username


class UserNamesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from EmpManagement.models import emp_master
        from django.contrib.auth import get_user_model
        out, seen = [], set()
        for e in emp_master.objects.filter(users__isnull=False).select_related('users').only(
                'emp_code', 'emp_first_name', 'emp_last_name', 'users__id', 'users__username'):
            u = e.users
            if u.id in seen:
                continue
            seen.add(u.id)
            name = ' '.join(x for x in (e.emp_first_name, e.emp_last_name) if x and str(x).strip()) or e.emp_code
            out.append({'id': u.id, 'username': u.username, 'name': name, 'code': e.emp_code or ''})
        prefix = (connection.schema_name or '') + '_'
        if prefix != '_' and prefix != 'public_':
            for u in get_user_model().objects.filter(username__startswith=prefix).exclude(id__in=seen).only('id', 'username', 'email'):
                out.append({'id': u.id, 'username': u.username, 'name': _from_email(u), 'code': ''})
        return Response(out)
