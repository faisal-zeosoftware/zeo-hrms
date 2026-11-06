"""v1.67.3 (apps) – the employee directory: part of the company core, there with or without the HR app.

Name, code, branch, department, designation, contact (company e-mail, mobile) and status of every employee – the same
`EmpManagement.emp_master` records the HR app works on, so when HR is activated later the employees are already HR employees
(nothing is entered again). Projects team members and timesheets, the Maintenance technicians, the HSE crew … pick from it.

    GET   /zeo-apps/api/directory/?search=&branch=&status=active|inactive|all&page=&page_size=
    POST  /zeo-apps/api/directory/            {emp_code, emp_first_name, emp_last_name, emp_branch_id, emp_dept_id, emp_desgntn_id,
                                               emp_company_email, emp_mobile_number_1, emp_joined_date}
    GET   /zeo-apps/api/directory/<id>/
    PATCH /zeo-apps/api/directory/<id>/       the same fields, and status (active / inactive)

Writes go through the HR employee serializer's own rules (code numbering per branch, unique code, phone, name, branch), so a
directory employee is a valid HR employee. Rights: company administrator, or view / add / change employee (view_emp_master …);
users limited to some branches see and write those branches only.
"""
import datetime

from django.db.models import F, Q, Value as V
from django.db.models.functions import Coalesce, Concat
from rest_framework.response import Response

from Translations.i18n import tr

from .views import Base, _ctx

FIELDS = ('emp_code', 'emp_first_name', 'emp_last_name', 'emp_branch_id', 'emp_dept_id', 'emp_desgntn_id', 'emp_company_email',
          'emp_mobile_number_1', 'emp_joined_date')


def _emp():
    from EmpManagement.models import emp_master
    return emp_master


def _can(request, verb):
    c = _ctx(request)
    if c is None:
        return False, None
    if c.admin:
        return True, c
    codes = getattr(c, 'codes', set())
    return (f'{verb}_emp_master' in codes or (verb == 'view' and bool({'add_emp_master', 'change_emp_master'} & codes))), c


def row(e):
    name = ' '.join(x for x in (e.emp_first_name, e.emp_last_name) if x)
    return {'id': e.pk, 'emp_code': e.emp_code, 'name': name, 'emp_first_name': e.emp_first_name or '', 'emp_last_name': e.emp_last_name or '',
            'emp_branch_id': e.emp_branch_id_id, 'branch': getattr(e.emp_branch_id, 'branch_name', '') or '',
            'emp_dept_id': e.emp_dept_id_id, 'department': getattr(e.emp_dept_id, 'dept_name', '') or '',
            'emp_desgntn_id': e.emp_desgntn_id_id, 'designation': getattr(e.emp_desgntn_id, 'desgntn_job_title', '') or '',
            'emp_company_email': e.emp_company_email or '', 'emp_mobile_number_1': e.emp_mobile_number_1 or '',
            'emp_joined_date': e.emp_joined_date.isoformat() if e.emp_joined_date else None,
            'status': 'active' if e.is_active is not False else 'inactive', 'has_login': bool(e.users_id)}


def _qs(c):
    qs = _emp().objects.select_related('emp_branch_id', 'emp_dept_id', 'emp_desgntn_id').order_by('emp_code')
    if c is not None and not c.admin and c.branches is not None:
        qs = qs.filter(emp_branch_id__in=list(c.branches))
    return qs


def _branch_ok(c, b):
    return c is None or c.admin or c.branches is None or (b is not None and int(b) in {int(x) for x in c.branches})


def _write(request, inst, c):
    from EmpManagement.serializer import EmpSerializer
    d = request.data or {}
    data = {k: d.get(k) for k in FIELDS if k in d}
    for k in ('emp_branch_id', 'emp_dept_id', 'emp_desgntn_id'):
        if data.get(k) in ('', 0, '0'):
            data[k] = None
    if inst is None and not data.get('emp_joined_date'):
        data['emp_joined_date'] = datetime.date.today().isoformat()   # the model needs it; HR can correct it later
    if 'emp_code' in data and data['emp_code'] is None:
        data['emp_code'] = ''
    b = data.get('emp_branch_id', getattr(inst, 'emp_branch_id_id', None))
    if b not in (None, '') and not _branch_ok(c, b):
        return Response({'emp_branch_id': [tr('You can only work with records of your own branches.')]}, status=400)
    s = EmpSerializer(inst, data=data, partial=inst is not None, context={'request': request})
    if not s.is_valid():
        return Response(s.errors, status=400)
    e = s.save()
    st = str(d.get('status') or '').lower()
    if inst is not None and st in ('active', 'inactive'):
        e.is_active = st == 'active'
        e.save(update_fields=['is_active'])
    e = _qs(None).get(pk=e.pk)
    return Response(row(e), status=201 if inst is None else 200)


class DirectoryView(Base):
    def get(self, request):
        ok, c = _can(request, 'view')
        if not ok:
            return Response({'detail': tr('You do not have permission to view employees.')}, status=403)
        qs = _qs(c)
        p = request.query_params
        st = (p.get('status') or 'active').lower()
        if st == 'active':
            qs = qs.exclude(is_active=False)
        elif st == 'inactive':
            qs = qs.filter(is_active=False)
        if p.get('branch'):
            qs = qs.filter(emp_branch_id=p.get('branch'))
        q = (p.get('search') or p.get('q') or '').strip()
        if q:
            qs = qs.annotate(_full=Concat(Coalesce(F('emp_first_name'), V('')), V(' '), Coalesce(F('emp_last_name'), V(''))))
            for w in q.split()[:5]:
                qs = qs.filter(Q(emp_code__icontains=w) | Q(_full__icontains=w) | Q(emp_company_email__icontains=w)
                               | Q(emp_mobile_number_1__icontains=w))
        try:
            size = max(1, min(int(p.get('page_size') or 200), 1000))
            page = max(1, int(p.get('page') or 1))
        except (TypeError, ValueError):
            size, page = 200, 1
        total = qs.count()
        try:
            from .services import is_active
            hr = bool(is_active('hrms'))
        except Exception:
            hr = True
        return Response({'count': total, 'page': page, 'page_size': size, 'results': [row(e) for e in qs[(page - 1) * size: page * size]],
                         'hr_active': hr, 'can_add': _can(request, 'add')[0], 'can_change': _can(request, 'change')[0]})

    def post(self, request):
        ok, c = _can(request, 'add')
        if not ok:
            return Response({'detail': tr('You do not have permission to add employees.')}, status=403)
        return _write(request, None, c)

    def options(self, request, *args, **kwargs):   # the shared form reads which fields are required
        req = {'emp_first_name', 'emp_branch_id'}
        return Response({'actions': {k: {f: {'required': f in req} for f in FIELDS} for k in ('POST', 'PUT')}})


class DirectoryOneView(Base):
    def _get(self, request, pk):
        ok, c = _can(request, 'view')
        if not ok:
            return None, c
        return _qs(c).filter(pk=pk).first(), c

    def get(self, request, pk):
        e, _c = self._get(request, pk)
        if e is None:
            return Response({'detail': tr('Not found.')}, status=404)
        return Response(row(e))

    def patch(self, request, pk):
        ok, c = _can(request, 'change')
        if not ok:
            return Response({'detail': tr('You do not have permission to change employees.')}, status=403)
        e = _qs(c).filter(pk=pk).first()
        if e is None:
            return Response({'detail': tr('Not found.')}, status=404)
        return _write(request, e, c)

    put = patch

    def options(self, request, *args, **kwargs):
        return DirectoryView().options(request)
