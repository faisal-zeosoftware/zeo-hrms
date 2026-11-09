"""v1.67.6 (setup) item 9 – users and employees, from both sides.

    GET  /zeo-apps/api/user-employee/?user=<id>        the employee of a login (+ the employees without a login, to link)
    GET  /zeo-apps/api/user-employee/?employee=<id>    the login of an employee (+ the logins of the company without an employee)
    POST {action: link, user_id, employee_id}          link them (one to one: refused when either is linked already)
    POST {action: unlink, employee_id}                 the employee keeps its record, the login keeps signing in
    POST {action: create_employee, user_id, emp_first_name, emp_last_name, emp_branch_id, emp_code?}
                                                       a directory employee made from a login and linked to it
    POST {action: create_user, employee_id, method?}   a login for an employee (Give access: e-mail link, slip, code …)

Rights: linking and unlinking need the change right on employees and on users (or a company administrator); making an
employee needs the add right on employees, making a login the add right on users. Branch-limited users only reach the
employees of their branches. Works with or without HR (the directory employees are the same records).
"""
from django.db import connection
from rest_framework.response import Response
from rest_framework.views import APIView

from zeo.appkit.engine import CompanyMember

from .services import tr_ as tr


def _ctx(request):
    from AccessControl.access import ctx
    return ctx(request)


def _can(c, *codes):
    return bool(c.admin or all(x in c.codes for x in codes))


def _emp_qs(c):
    from EmpManagement.models import emp_master
    qs = emp_master.objects.all()
    if c.branches is not None and not c.admin:
        qs = qs.filter(emp_branch_id__in=list(c.branches))
    return qs


def _users():
    from UserManagement.models import CustomUser
    return CustomUser.objects.filter(tenants__schema_name=connection.schema_name).distinct()


def _ename(e):
    return ' '.join(x for x in [e.emp_first_name, e.emp_last_name] if x) or e.emp_code


def ejson(e):
    return {'id': e.pk, 'code': e.emp_code, 'name': _ename(e), 'branch': getattr(e.emp_branch_id, 'branch_name', '') if e.emp_branch_id_id else ''}


def ujson(u):
    full = u.get_full_name() if hasattr(u, 'get_full_name') else ''
    return {'id': u.pk, 'username': u.username, 'email': u.email or '', 'name': full or u.username, 'active': u.is_active}


class UserEmployeeView(APIView):
    zeo_access = False   # rights are checked here
    zeo_scope = False
    permission_classes = [CompanyMember]

    def get(self, request):
        c = _ctx(request)
        if not (_can(c, 'view_emp_master') or _can(c, 'view_customuser')):
            return Response({'detail': tr('You may not see users or employees.')}, status=403)
        uid, eid = request.query_params.get('user'), request.query_params.get('employee')
        if uid:
            u = _users().filter(pk=uid).first()
            if u is None:
                return Response({'detail': tr('User not found in this company.')}, status=404)
            e = _emp_qs(c).filter(users=u).select_related('emp_branch_id').first()
            free = [ejson(x) for x in _emp_qs(c).filter(users__isnull=True).select_related('emp_branch_id').order_by('emp_first_name')[:500]]
            return Response({'user': ujson(u), 'employee': ejson(e) if e else None, 'free_employees': free,
                             'can_link': _can(c, 'change_emp_master', 'change_customuser'), 'can_create': _can(c, 'add_emp_master')})
        if eid:
            e = _emp_qs(c).filter(pk=eid).select_related('emp_branch_id').first()
            if e is None:
                return Response({'detail': tr('Employee not found.')}, status=404)
            from EmpManagement.models import emp_master
            taken = set(emp_master.objects.filter(users__isnull=False).values_list('users_id', flat=True))
            u = _users().filter(pk=e.users_id).first() if e.users_id else None
            free = [ujson(x) for x in _users().filter(is_active=True).exclude(pk__in=taken).order_by('username')[:500]]
            return Response({'employee': ejson(e), 'user': ujson(u) if u else None, 'free_users': free,
                             'can_link': _can(c, 'change_emp_master', 'change_customuser'), 'can_create': _can(c, 'add_customuser')})
        return Response({'detail': tr('Give a user or an employee.')}, status=400)

    def post(self, request):
        from EmpManagement.models import emp_master
        c = _ctx(request)
        d = request.data or {}
        act = d.get('action')
        if act in ('link', 'unlink') and not _can(c, 'change_emp_master', 'change_customuser'):
            return Response({'detail': tr('You may not link users and employees.')}, status=403)
        if act == 'link':
            u = _users().filter(pk=d.get('user_id')).first()
            e = _emp_qs(c).filter(pk=d.get('employee_id')).first()
            if u is None or e is None:
                return Response({'detail': tr('User or employee not found in this company.')}, status=404)
            if e.users_id and e.users_id != u.pk:
                o = _users().filter(pk=e.users_id).first()
                return Response({'detail': tr('{0} already has the login {1}. One employee has one login – unlink it first.', _ename(e), getattr(o, 'username', '?'))}, status=409)
            other = emp_master.objects.filter(users=u).exclude(pk=e.pk).first()
            if other is not None:
                return Response({'detail': tr('The login {0} already belongs to {1} ({2}). One login has one employee – unlink it first.', u.username, _ename(other), other.emp_code)}, status=409)
            emp_master.objects.filter(pk=e.pk).update(users=u)
            return Response({'detail': tr('{0} is linked to the login {1}.', _ename(e), u.username), 'employee': ejson(e), 'user': ujson(u)})
        if act == 'unlink':
            e = _emp_qs(c).filter(pk=d.get('employee_id')).first()
            if e is None:
                return Response({'detail': tr('Employee not found.')}, status=404)
            emp_master.objects.filter(pk=e.pk).update(users=None)
            return Response({'detail': tr('{0} is no longer linked to a login.', _ename(e))})
        if act == 'create_employee':
            if not _can(c, 'add_emp_master'):
                return Response({'detail': tr('You may not add employees.')}, status=403)
            u = _users().filter(pk=d.get('user_id')).first()
            if u is None:
                return Response({'detail': tr('User not found in this company.')}, status=404)
            other = emp_master.objects.filter(users=u).first()
            if other is not None:
                return Response({'detail': tr('The login {0} already belongs to {1} ({2}). One login has one employee.', u.username, _ename(other), other.emp_code)}, status=409)
            from .directory import _write

            class R:   # the directory's own write path (numbering, unique code, branch rights)
                pass
            req = R()
            req.data = {'emp_first_name': d.get('emp_first_name') or getattr(u, 'first_name', '') or u.username,
                        'emp_last_name': d.get('emp_last_name') or getattr(u, 'last_name', '') or '',
                        'emp_branch_id': d.get('emp_branch_id'), 'emp_code': d.get('emp_code') or '', 'emp_company_email': u.email or '',
                        'emp_dept_id': d.get('emp_dept_id'), 'emp_desgntn_id': d.get('emp_desgntn_id')}
            req.user, req.query_params, req.method = request.user, request.query_params, 'POST'
            r = _write(req, None, c)
            if r.status_code != 201:
                return r
            emp_master.objects.filter(pk=r.data['id']).update(users=u)
            e = emp_master.objects.select_related('emp_branch_id').get(pk=r.data['id'])
            return Response({'detail': tr('Employee {0} made and linked to the login {1}.', e.emp_code, u.username), 'employee': ejson(e), 'user': ujson(u)}, status=201)
        if act == 'create_user':
            if not _can(c, 'add_customuser'):
                return Response({'detail': tr('You may not add users.')}, status=403)
            e = _emp_qs(c).filter(pk=d.get('employee_id')).first()
            if e is None:
                return Response({'detail': tr('Employee not found.')}, status=404)
            if e.users_id:
                o = _users().filter(pk=e.users_id).first()
                return Response({'detail': tr('{0} already has the login {1}.', _ename(e), getattr(o, 'username', '?'))}, status=409)
            from ZeoAdmin import giveaccess as G
            try:
                res = G.give(e, d.get('method') or 'auto', request.user)
            except G.Refused as x:
                return Response({'detail': str(getattr(x, 'message', x))}, status=400)
            e.refresh_from_db()
            u = _users().filter(pk=e.users_id).first() if e.users_id else None
            return Response({'detail': res.get('detail') or tr('A login was made for {0}.', _ename(e)), 'method': res.get('method'),
                             'user': ujson(u) if u else None, 'employee': ejson(e), **{k: v for k, v in res.items() if k in ('password', 'slip', 'code')}},
                            status=201)
        return Response({'detail': tr('Unknown action.')}, status=400)
