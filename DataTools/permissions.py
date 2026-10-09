"""
v1.12.0 – rights for the employee custom fields (form designer).

CustomFieldDesignPermission (field definitions: employee, family, qualification, job history, documents)
    read:   every logged-in user of the company (the employee screens, ESS profile and lists need the field list)
    change: company admin, or add_ / change_ / delete_<definition model> (as the action needs)

CustomFieldValuePermission (values of those fields)
    read:   every logged-in user; rows are limited by the central access filter (own rows for employees)
    change: company admin; add_ / change_ / delete_<value model>; change_<parent model> (e.g. change_emp_master);
            or an employee for their own record (self service) – fields marked "read-only for employees"
            are refused by the value check itself (DataTools.forms.check_row)
"""
from django.db import connection
from rest_framework import permissions

VERB = {'create': 'add', 'update': 'change', 'partial_update': 'change', 'destroy': 'delete'}
PARENT = {  # value model -> (parent field, parent model name)
    'emp_customfieldvalue': ('emp_master', 'emp_master'),
    'fam_customfieldvalue': ('emp_family', 'emp_family'),
    'jobhistory_customfieldvalue': ('emp_job_history', 'empjobhistory'),
    'qualification_customfieldvalue': ('emp_qualification', 'empqualification'),
    'doc_customfieldvalue': ('emp_documents', 'emp_documents'),
}


def _member(request):
    user = request.user
    if not user or not user.is_authenticated:
        return False
    schema = connection.schema_name
    return user.is_superuser or schema == 'public' or user.tenants.filter(schema_name=schema).exists()


def _model(view):
    qs = getattr(view, 'queryset', None)
    return qs.model if qs is not None else None


class CustomFieldDesignPermission(permissions.BasePermission):
    message = 'Only HR users with form designer rights can change custom fields.'

    def has_permission(self, request, view):
        if not _member(request):
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        from AccessControl.access import ctx
        c = ctx(request)
        if c.admin:
            return True
        name = _model(view)._meta.model_name
        verb = VERB.get(getattr(view, 'action', None)) or ('add' if request.method == 'POST' else 'delete' if request.method == 'DELETE' else 'change')
        return f'{verb}_{name}' in c.codes


def _own(c, parent_model, parent_id):
    """Does the parent record (employee / family / … row) belong to the user's own employee?"""
    if c.emp is None or parent_id in (None, ''):
        return False
    if parent_model._meta.model_name == 'emp_master':
        return str(parent_id) == str(c.emp.pk)
    return parent_model.objects.filter(pk=parent_id, emp_id=c.emp).exists()


class CustomFieldValuePermission(permissions.BasePermission):
    message = 'You can only change custom fields of your own record.'

    def _rights(self, request, view):
        from AccessControl.access import ctx
        c = ctx(request)
        model = _model(view)
        name = model._meta.model_name
        pfield, pname = PARENT.get(name, (None, None))
        verb = VERB.get(getattr(view, 'action', None), 'change')
        ok = c.admin or f'{verb}_{name}' in c.codes or (pname and {f'change_{pname}', f'add_{pname}'} & c.codes)
        return c, model, pfield, bool(ok)

    def has_permission(self, request, view):
        if not _member(request):
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        c, model, pfield, ok = self._rights(request, view)
        if ok:
            return True
        if request.method == 'POST' and pfield:
            parent_model = model._meta.get_field(pfield).related_model
            data = request.data if hasattr(request.data, 'get') else {}
            return _own(c, parent_model, data.get(pfield))
        return request.method in ('PUT', 'PATCH', 'DELETE')   # decided per row below

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        c, model, pfield, ok = self._rights(request, view)
        if ok:
            return True
        if not pfield:
            return False
        parent_model = model._meta.get_field(pfield).related_model
        if not _own(c, parent_model, getattr(obj, pfield + '_id', None)):
            return False
        data = request.data if hasattr(request.data, 'get') else {}
        if data.get(pfield) not in (None, '') and not _own(c, parent_model, data.get(pfield)):
            return False   # cannot move a value to someone else's record
        return True
