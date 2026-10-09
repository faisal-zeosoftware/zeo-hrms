"""
Employee profile API (v1.13.0), mounted at /employee-profile/api/.

Rights (the central AccessControl layer is switched off for these views because the tables keep integer ids; the
same rules are applied here):
  * HR – company admin or a user with view_emp_master / change_emp_master – read the employees of their branches;
    changing needs change_emp_master (or add_emp_master) and the employee in one of their branches;
  * an employee reads only their own details (my-profile, or their own id); changes by employees go through the
    self-service app (SelfService → services.apply_change after HR approval);
  * employee code numbering: HR with change_emp_master for their branches; the company default needs an
    unrestricted user (company admin / all branches).
"""
import logging
from datetime import date

from django.apps import apps
from django.db import transaction
from django.db.models import Q
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services as S
from . import validators as V
from .models import BankExtra, DependentExtra, EmergencyContact, EmployeeCodeSetting, EmployeeIdentity, EmploymentInfo, QualificationExtra
from .serializers import (BankExtraSerializer, CodeSettingSerializer, DependentExtraSerializer, EmergencySerializer, EmploymentSerializer,
                          IdentitySerializer, QualificationExtraSerializer)

log = logging.getLogger(__name__)
M = apps.get_model


def _schema(request):
    from django.db import connection
    if connection.schema_name != 'public':
        return connection.schema_name
    return request.GET.get('schema')


class Member(BasePermission):
    message = 'You do not have access to this company.'

    def has_permission(self, request, view):
        u = request.user
        if not u or not u.is_authenticated:
            self.message = 'Please log in.'
            return False
        sch = _schema(request)
        if not sch or sch == 'public':
            self.message = 'Choose a company.'
            return False
        return u.is_superuser or u.tenants.filter(schema_name=sch).exists()


class Base(APIView):
    zeo_access = False   # rights are checked here (module docstring)
    zeo_scope = False
    permission_classes = [Member]

    def handle_exception(self, exc):
        if isinstance(exc, ValidationError):
            return Response(exc.detail, status=400)
        return super().handle_exception(exc)


def deny(msg, code=403):
    return Response({'detail': msg}, status=code)


def _emp(request, emp_id, write=False):
    emp, err = S.visible_emp(request, emp_id, write=write)
    return emp, (deny(*err) if err else None)


def _date(v, field):
    if v in (None, ''):
        return None
    try:
        return serializers.DateField().to_internal_value(v)
    except serializers.ValidationError:
        raise ValidationError({field: 'Enter a valid date (YYYY-MM-DD).'})


def _ids(v):
    return [int(x) for x in str(v or '').replace(';', ',').split(',') if x.strip().isdigit()]


# ------------------------------------------------------------------ whole profile
def _rows_with_extra(qs, extra_model, key, ser, fields, request=None):
    extras = {getattr(x, key): x for x in extra_model.objects.filter(**{key + '__in': [r.pk for r in qs]})}
    out = []
    for r in qs:
        d = {'id': r.pk}
        for f in fields:
            v = getattr(r, f)
            d[f] = v
        x = extras.get(r.pk)
        d['extra'] = ser(x, context={'request': request}).data if x else None
        out.append(d)
    return out


def profile_json(request, emp, can_edit):
    ident = S.identity_for(emp.pk)
    fam = M('EmpManagement', 'emp_family').objects.filter(emp_id=emp).order_by('id')
    bank = M('EmpManagement', 'EmployeeBankDetail').objects.filter(employee=emp).order_by('id')
    qual = M('EmpManagement', 'EmpQualification').objects.filter(emp_id=emp).order_by('id')
    contacts = EmergencyContact.objects.filter(employee_id=emp.pk)
    info = S.ensure_info(emp)
    return {
        'employee': {'id': emp.pk, 'code': emp.emp_code, 'name': S.full_name(emp), 'branch_id': emp.emp_branch_id_id,
                     'joined_date': emp.emp_joined_date, 'nationality': str(emp.emp_nationality or '')},
        'can_edit': can_edit,
        'identity': IdentitySerializer(ident).data if ident else None,
        'emergency_contacts': EmergencySerializer(contacts, many=True).data,
        'dependents': _rows_with_extra(fam, DependentExtra, 'family_id', DependentExtraSerializer, ['ef_member_name', 'emp_relation', 'ef_date_of_birth', 'ef_company_expence'], request),
        'bank': _rows_with_extra(bank, BankExtra, 'bank_detail_id', BankExtraSerializer, ['bank_name', 'branch_name', 'account_number', 'iban_number', 'route_code', 'is_active'], request),
        'qualifications': _rows_with_extra(qual, QualificationExtra, 'qualification_id', QualificationExtraSerializer, ['emp_qualification', 'emp_qf_instituition', 'emp_qf_year', 'emp_qf_subject'], request),
        'employment': S.employment_json(emp, info),
        'skills': S.skills_json(emp),
        'completeness': S.completeness(emp, ident, contacts.exists()),
    }


class ProfileView(Base):
    def get(self, request, emp_id):
        emp, err = _emp(request, emp_id)
        if err:
            return err
        return Response(profile_json(request, emp, S.is_hr(request, write=True) and not S.visible_emp(request, emp.pk, write=True)[1]))


class MyProfileView(Base):
    """ESS: the user's own employee profile, read only."""

    def get(self, request):
        emp = S.my_employee(request)
        if emp is None:
            return Response({'detail': 'Your login is not linked to an employee.'}, status=404)
        return Response(profile_json(request, emp, False))


class GroupsView(Base):
    def get(self, request):
        return Response(S.profile_groups())


# ------------------------------------------------------------------ identity
class IdentityView(Base):
    def get(self, request, emp_id):
        emp, err = _emp(request, emp_id)
        if err:
            return err
        o = S.identity_for(emp.pk)
        d = IdentitySerializer(o).data if o else {'employee_id': emp.pk}
        return Response(d)

    def put(self, request, emp_id):
        emp, err = _emp(request, emp_id, write=True)
        if err:
            return err
        obj, sync = S.save_identity(emp, request.data, request.user)
        d = IdentitySerializer(obj).data
        d['document_sync'] = sync
        return Response(d)

    patch = put
    post = put


class DocSyncView(Base):
    def post(self, request, emp_id):
        emp, err = _emp(request, emp_id, write=True)
        if err:
            return err
        o = S.identity_for(emp.pk)
        if o is None:
            return Response({'detail': 'This employee has no identity details yet.'}, status=400)
        return Response({'document_sync': S.sync_documents(o, emp, request.user)})


class EmployeeColumnsView(Base):
    """GET ?ids=1,2 → passport / visa / EID expiry, status, probation end and manager per employee (list columns)."""

    def get(self, request):
        if not S.is_hr(request):
            me = S.my_employee(request)
            if me is None:
                return Response([])
            return Response(S.employee_columns([me]))
        qs = S.emp_qs(request)
        ids = _ids(request.query_params.get('ids'))
        if ids:
            qs = qs.filter(pk__in=ids)
        return Response(S.employee_columns(list(qs.order_by('emp_code'))))


# ------------------------------------------------------------------ emergency contacts
class EmergencyListView(Base):
    def get(self, request):
        emp, err = _emp(request, request.query_params.get('employee'))
        if err:
            return err
        return Response(EmergencySerializer(EmergencyContact.objects.filter(employee_id=emp.pk), many=True).data)

    def post(self, request):
        emp, err = _emp(request, (request.data or {}).get('employee') or request.query_params.get('employee'), write=True)
        if err:
            return err
        data = {k: v for k, v in S._plain(request.data).items() if k not in ('employee', 'employee_id')}
        return Response(S.apply_change(emp, 'emergency', None, data, request.user), status=201)


class EmergencyDetailView(Base):
    def _get(self, request, pk, write):
        c = EmergencyContact.objects.filter(pk=pk).first()
        if c is None:
            return None, None, deny('Emergency contact not found.', 404)
        emp, err = _emp(request, c.employee_id, write=write)
        return c, emp, err

    def get(self, request, pk):
        c, emp, err = self._get(request, pk, False)
        return err or Response(EmergencySerializer(c).data)

    def put(self, request, pk):
        c, emp, err = self._get(request, pk, True)
        if err:
            return err
        data = {k: v for k, v in S._plain(request.data).items() if k not in ('employee', 'employee_id', 'id', 'created_at', 'updated_at')}
        return Response(S.apply_change(emp, 'emergency', c.pk, data, request.user))

    patch = put

    def delete(self, request, pk):
        c, emp, err = self._get(request, pk, True)
        if err:
            return err
        S.apply_change(emp, 'emergency', c.pk, {}, request.user, action='delete')
        return Response(status=204)


# ------------------------------------------------------------------ extras of existing rows
class _ExtraView(Base):
    parent = ''          # app.model
    parent_fk = ''       # field to emp_master
    model = None
    key = ''
    ser = None

    def _parent(self, request, pk, write):
        P = M(*self.parent.split('.'))
        row = P.objects.filter(pk=pk).first() if str(pk).isdigit() else None
        if row is None:
            return None, None, deny('Record not found.', 404)
        emp, err = _emp(request, getattr(row, self.parent_fk + '_id'), write=write)
        return row, emp, err

    def get(self, request, pk):
        row, emp, err = self._parent(request, pk, False)
        if err:
            return err
        x = self.model.objects.filter(**{self.key: row.pk}).first()
        return Response(self.ser(x, context={'request': request}).data if x else {self.key: row.pk, 'employee_id': emp.pk})

    def put(self, request, pk):
        row, emp, err = self._parent(request, pk, True)
        if err:
            return err
        x = self.model.objects.filter(**{self.key: row.pk}).first()
        data = {k: v for k, v in S._plain(request.data).items() if k not in ('id', self.key, 'employee_id', 'updated_at')}
        if 'attachment' in request.FILES:
            data['attachment'] = request.FILES['attachment']
        s = self.ser(x, data=data, partial=True)
        s.is_valid(raise_exception=True)
        with transaction.atomic():
            obj = s.save(**{self.key: row.pk, 'employee_id': emp.pk})
            self.after(obj)
        return Response(self.ser(obj, context={'request': request}).data)

    patch = put
    post = put

    def after(self, obj):
        pass


class DependentView(_ExtraView):
    parent, parent_fk, model, key, ser = 'EmpManagement.emp_family', 'emp_id', DependentExtra, 'family_id', DependentExtraSerializer


class BankExtraView(_ExtraView):
    parent, parent_fk, model, key, ser = 'EmpManagement.EmployeeBankDetail', 'employee', BankExtra, 'bank_detail_id', BankExtraSerializer

    def after(self, obj):
        if obj.is_primary:
            BankExtra.objects.filter(employee_id=obj.employee_id, is_primary=True).exclude(pk=obj.pk).update(is_primary=False)


class QualificationExtraView(_ExtraView):
    parent, parent_fk, model, key, ser = 'EmpManagement.EmpQualification', 'emp_id', QualificationExtra, 'qualification_id', QualificationExtraSerializer


class IbanCheckView(Base):
    def post(self, request):
        try:
            v = V.clean_iban((request.data or {}).get('iban'))
        except ValueError as e:
            return Response({'ok': False, 'message': str(e)})
        if not v:
            return Response({'ok': False, 'message': 'Enter the IBAN.'})
        return Response({'ok': True, 'iban': v, 'bank_code': v[4:7], 'message': 'The IBAN is valid.'})


class EidCheckView(Base):
    def post(self, request):
        try:
            v = V.clean_emirates_id((request.data or {}).get('emirates_id'))
        except ValueError as e:
            return Response({'ok': False, 'message': str(e)})
        return Response({'ok': bool(v), 'emirates_id': v, 'message': 'The Emirates ID is valid.' if v else 'Enter the Emirates ID.'})


# ------------------------------------------------------------------ generic change (HR) – same code path as self-service
class ChangeView(Base):
    """POST {group, record_id, data, action ('delete'), dry_run} → saved record (or {valid: true} for a dry run)."""

    def post(self, request, emp_id):
        emp, err = _emp(request, emp_id, write=True)
        if err:
            return err
        body = request.data
        group = body.get('group')
        rid = body.get('record_id') or None
        data = body.get('data')
        if data is None:
            data = {k: v for k, v in S._plain(body).items() if k not in ('group', 'record_id', 'action', 'dry_run')}
            for k, f in request.FILES.items():
                data[k] = f
        action = body.get('action') or None
        if str(body.get('dry_run', '')).lower() in ('1', 'true', 'yes'):
            S.validate_change(emp, group, rid, data, action=action)
            return Response({'valid': True})
        return Response(S.apply_change(emp, group, rid, data, request.user, action=action))


# ------------------------------------------------------------------ employment / probation
class EmploymentView(Base):
    def get(self, request, emp_id):
        emp, err = _emp(request, emp_id)
        if err:
            return err
        return Response(S.employment_json(emp))

    def put(self, request, emp_id):
        emp, err = _emp(request, emp_id, write=True)
        if err:
            return err
        info = S.ensure_info(emp)
        s = EmploymentSerializer(info, data=request.data, partial=True, context={'joined': emp.emp_joined_date})
        s.is_valid(raise_exception=True)
        old = info.status
        info = s.save(updated_by_id=request.user.pk)
        if 'status' in s.validated_data and info.status != old:
            if info.status in S.LEAVING_STATUSES and not info.leaving_source:
                info.leaving_source = 'manual'
                info.save(update_fields=['leaving_source'])
            S.note(emp, f'Employment status changed from {dict(EmploymentInfo.STATUS).get(old)} to {info.get_status_display()}'
                   + (f' (last working day {info.date_of_leaving:%d/%m/%Y})' if info.date_of_leaving else '') + '.', request.user)
        return Response(S.employment_json(emp, info))

    patch = put


class ProbationActionView(Base):
    def post(self, request, emp_id, act):
        emp, err = _emp(request, emp_id, write=True)
        if err:
            return err
        d = request.data or {}
        if act == 'confirm':
            S.confirm_probation(emp, request.user, _date(d.get('date'), 'date'), d.get('note') or '')
            return Response(S.employment_json(emp))
        if act == 'extend':
            S.extend_probation(emp, request.user, _date(d.get('to_date'), 'to_date'), d.get('reason') or '')
            return Response(S.employment_json(emp))
        if act == 'fail':
            info, short = S.fail_probation(emp, request.user, _date(d.get('last_day'), 'last_day'), d.get('reason') or '')
            out = S.employment_json(emp, info)
            if short:
                out['warning'] = f'The last working day is less than {S.PROBATION_NOTICE_DAYS} days away – UAE law asks for {S.PROBATION_NOTICE_DAYS} days’ written notice.'
            return Response(out)
        return deny('Unknown action.', 404)


class ProbationDueView(Base):
    """Employees whose probation ends within ?days= (default 30), overdue first."""

    def get(self, request):
        if not S.is_hr(request):
            return deny('This list is for HR users.')
        try:
            days = max(0, min(366, int(request.query_params.get('days') or 30)))
        except ValueError:
            days = 30
        qs = S.emp_qs(request).filter(Q(is_active=True) | Q(is_active__isnull=True))
        emps = {e.pk: e for e in qs}
        today = date.today()
        out = []
        for i in S.probation_due(qs, days).order_by('probation_end_date'):
            e = emps.get(i.employee_id)
            if e is None:
                continue
            out.append({'employee_id': e.pk, 'employee_code': e.emp_code, 'employee': S.full_name(e),
                        'branch': e.emp_branch_id.branch_name if e.emp_branch_id_id else '', 'department': e.emp_dept_id.dept_name if e.emp_dept_id_id else '',
                        'joined_date': e.emp_joined_date, 'probation_end_date': i.probation_end_date, 'days_left': (i.probation_end_date - today).days,
                        'probation_status': i.get_probation_status_display(), 'overdue': i.probation_end_date < today,
                        'manager': e.emp_reporting_manager.username if e.emp_reporting_manager_id else ''})
        return Response(out)


# ------------------------------------------------------------------ employee code numbering
def _can_code(request, branch_id):
    if not S.is_hr(request, write=True):
        return 'You may not change employee code numbering.'
    allowed = S.user_branches(request)
    if allowed is None:
        return None
    if branch_id is None:
        return 'Only a company administrator can change the company default.'
    return None if int(branch_id) in allowed else 'You can only set numbering for your own branches.'


class CodeSettingsView(Base):
    def get(self, request):
        if not S.is_hr(request):
            return deny('This page is for HR users.')
        qs = EmployeeCodeSetting.objects.all()
        allowed = S.user_branches(request)
        if allowed is not None:
            qs = qs.filter(Q(branch_id__in=allowed) | Q(branch_id__isnull=True))
        return Response(CodeSettingSerializer(qs, many=True).data)

    def post(self, request):
        b = (request.data or {}).get('branch_id')
        msg = _can_code(request, b if b not in ('', None) else None)
        if msg:
            return deny(msg)
        s = CodeSettingSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        return Response(CodeSettingSerializer(s.save(updated_by_id=request.user.pk)).data, status=201)


class CodeSettingDetailView(Base):
    def put(self, request, pk):
        o = EmployeeCodeSetting.objects.filter(pk=pk).first()
        if o is None:
            return deny('Numbering rule not found.', 404)
        msg = _can_code(request, o.branch_id)
        if msg:
            return deny(msg)
        if 'branch_id' in (request.data or {}) and (request.data.get('branch_id') or None) != o.branch_id:
            msg = _can_code(request, request.data.get('branch_id') or None)
            if msg:
                return deny(msg)
        s = CodeSettingSerializer(o, data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        return Response(CodeSettingSerializer(s.save(updated_by_id=request.user.pk)).data)

    patch = put

    def delete(self, request, pk):
        o = EmployeeCodeSetting.objects.filter(pk=pk).first()
        if o is None:
            return deny('Numbering rule not found.', 404)
        msg = _can_code(request, o.branch_id)
        if msg:
            return deny(msg)
        o.delete()
        return Response(status=204)


class NextCodeView(Base):
    """GET ?branch=<id> → {enabled, next_code} for the employee form ("Generated on save")."""

    def get(self, request):
        b = request.query_params.get('branch')
        b = int(b) if str(b or '').isdigit() else None
        s = S.code_setting_for(b)
        on = bool(s and s.enabled)
        return Response({'enabled': on, 'next_code': S.preview_code(b) if on else None,
                         'rule': CodeSettingSerializer(s).data if s else None})


# ------------------------------------------------------------------ skills / history / completeness
class SkillsView(Base):
    def get(self, request, emp_id):
        emp, err = _emp(request, emp_id)
        if err:
            return err
        return Response(S.skills_json(emp))


class HistoryView(Base):
    def get(self, request, emp_id):
        emp, err = _emp(request, emp_id)
        if err:
            return err
        q = request.query_params
        types = [t for t in (q.get('types') or '').split(',') if t.strip()] or None
        ev = S.timeline(emp, types, _date(q.get('from'), 'from'), _date(q.get('to'), 'to'))
        return Response({'types': [{'key': k, 'label': l} for k, l in S.HISTORY_TYPES], 'events': ev})


class CompletenessView(Base):
    def get(self, request, emp_id=None):
        if emp_id is not None:
            emp, err = _emp(request, emp_id)
            if err:
                return err
            return Response(S.completeness(emp))
        if not S.is_hr(request):
            return deny('This list is for HR users.')
        out = []
        for e in S.emp_qs(request).filter(Q(is_active=True) | Q(is_active__isnull=True)).select_related('emp_nationality').order_by('emp_code'):
            c = S.completeness(e)
            out.append({'employee_id': e.pk, 'employee_code': e.emp_code, 'employee': S.full_name(e), **c})
        return Response(out)


# ------------------------------------------------------------------ identity import (dry run first)
IMPORT_COLUMNS = ['employee_code', 'arabic_name', 'title', 'preferred_name', 'place_of_birth', 'passport_no', 'passport_issue_date', 'passport_expiry_date',
                  'passport_place_of_issue', 'passport_country', 'visa_type', 'visa_number', 'visa_uid', 'visa_file_no', 'visa_issue_date',
                  'visa_expiry_date', 'visa_sponsor', 'emirates_id', 'emirates_id_issue_date', 'emirates_id_expiry_date', 'labour_card_no',
                  'labour_card_issue_date', 'labour_card_expiry_date', 'work_permit_no', 'work_permit_expiry_date', 'mohre_contract_type',
                  'mohre_contract_no', 'mohre_contract_start', 'mohre_contract_end', 'driving_licence_no', 'driving_licence_issue_date',
                  'driving_licence_expiry_date', 'driving_licence_emirate', 'driving_licence_categories',
                  'insurance_provider', 'insurance_card_no', 'insurance_expiry_date']


def _import_date(v):
    from datetime import datetime
    if v in (None, ''):
        return None
    if hasattr(v, 'year'):
        return (v.date() if hasattr(v, 'date') and callable(v.date) else v).isoformat()
    s = str(v).strip().split(' ')[0]
    try:   # Excel day number (cells read without date formatting)
        n = float(s)
        if 20000 <= n <= 80000:
            from datetime import timedelta
            return (date(1899, 12, 30) + timedelta(days=int(n))).isoformat()
    except ValueError:
        pass
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%d.%m.%Y'):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return s   # the serializer explains the format


class IdentityImportView(Base):
    def get(self, request):
        return Response({'columns': IMPORT_COLUMNS, 'example': {
            'employee_code': 'EMP1001', 'passport_no': 'N1234567', 'passport_issue_date': '2023-01-15', 'passport_expiry_date': '2033-01-14',
            'emirates_id': '784-1990-1234567-1', 'emirates_id_issue_date': '2024-02-01', 'emirates_id_expiry_date': '2026-02-01'},
            'notes': 'Dates as YYYY-MM-DD or DD/MM/YYYY. passport_country is the country name. Empty cells are left unchanged. '
                     'Send dry_run=true first: nothing is saved and every row is checked.'})

    def post(self, request):
        if not S.is_hr(request, write=True):
            return deny('You may not change employee identity details.')
        body = request.data if isinstance(request.data, dict) else {'rows': request.data}
        rows = body.get('rows') or []
        dry = str(body.get('dry_run', request.query_params.get('dry_run', ''))).lower() in ('1', 'true', 'yes')
        Emp = M('EmpManagement', 'emp_master')
        C = M('Core', 'cntry_mstr')
        out, ok = [], 0
        seen = {}
        for i, r in enumerate(rows, 1):
            r = {str(k).strip().lower().replace(' ', '_'): v for k, v in (r or {}).items()}
            code = str(r.get('employee_code') or '').strip()
            emp = Emp.objects.filter(emp_code__iexact=code).first() if code else None
            if emp is None:
                out.append({'row': i, 'ok': False, 'errors': {'employee_code': f'No employee with code "{code}".' if code else 'Enter the employee code.'}})
                continue
            emp, err = S.visible_emp(request, emp.pk, write=True)
            if err:
                out.append({'row': i, 'ok': False, 'errors': {'employee_code': err[0]}})
                continue
            data = {}
            errors = {}
            for k in IMPORT_COLUMNS[1:]:
                v = r.get(k)
                if v in (None, ''):
                    continue
                if k == 'passport_country':
                    c = C.objects.filter(Q(country_name__iexact=str(v).strip()) | Q(country_code__iexact=str(v).strip())).first()
                    if c is None:
                        errors['passport_country'] = f'No country "{v}".'
                    else:
                        data['passport_country_id'] = c.pk
                    continue
                data[k] = _import_date(v) if k.endswith('_date') or k in ('mohre_contract_start', 'mohre_contract_end') else str(v).strip()
            # the same number twice in the file
            if data.get('driving_licence_emirate'):   # name or code, e.g. "Abu Dhabi" or "abu_dhabi"
                data['driving_licence_emirate'] = data['driving_licence_emirate'].strip().lower().replace(' ', '_')
            for k in ('passport_no', 'emirates_id', 'labour_card_no', 'driving_licence_no'):
                v = str(data.get(k) or '').replace('-', '').upper()
                if v:
                    if (k, v) in seen and seen[(k, v)] != emp.pk:
                        errors[k] = f'The same number is on row {seen[(k, v, "row")]} for another employee.'
                    seen[(k, v)] = emp.pk
                    seen[(k, v, 'row')] = i
            if not errors:
                try:
                    inst = S.identity_for(emp.pk)
                    ser = IdentitySerializer(inst, data=data, partial=True, context={'employee_id': emp.pk})
                    if not ser.is_valid():
                        errors.update({k: (v[0] if isinstance(v, list) else v) for k, v in ser.errors.items()})
                    elif not dry:
                        obj = ser.save(employee_id=emp.pk, updated_by_id=request.user.pk)
                        sync = S.sync_documents(obj, emp, request.user)
                        probs = {k: v['message'] for k, v in sync.items() if v['status'] == 'problem'}
                        if probs:
                            out.append({'row': i, 'ok': True, 'employee_id': emp.pk, 'employee': S.full_name(emp), 'warnings': probs})
                            ok += 1
                            continue
                except Exception as e:
                    log.exception('identity import row')
                    errors['detail'] = str(e)
            if errors:
                out.append({'row': i, 'ok': False, 'employee_id': emp.pk, 'errors': {k: str(v) for k, v in errors.items()}})
            else:
                out.append({'row': i, 'ok': True, 'employee_id': emp.pk, 'employee': S.full_name(emp)})
                ok += 1
        return Response({'dry_run': dry, 'total': len(rows), 'ok': ok, 'failed': len(rows) - ok, 'results': out})
