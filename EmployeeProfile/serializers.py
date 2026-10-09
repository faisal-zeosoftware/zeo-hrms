"""v1.13.0 – serializers with the server-side checks (format, check digits, date ranges, duplicates)."""
import os
from datetime import date

from django.apps import apps
from rest_framework import serializers

from . import validators as V
from .models import (BankExtra, DependentExtra, EmergencyContact, EmployeeCodeSetting, EmployeeIdentity, EmploymentInfo,
                     QualificationExtra)

M = apps.get_model


def _run(fn, value, *a):
    try:
        return fn(value, *a)
    except ValueError as e:
        raise serializers.ValidationError(str(e))


def emp_label(emp):
    if emp is None:
        return ''
    name = ' '.join(x for x in [emp.emp_first_name, emp.emp_last_name] if x)
    return f'{emp.emp_code} ({name})' if name else emp.emp_code


def _country_ok(v, label='country'):
    if v in (None, ''):
        return None
    if not M('Core', 'cntry_mstr').objects.filter(pk=v).exists():
        raise serializers.ValidationError(f'Choose a {label} from the list.')
    return v


def _nationality_ok(v):
    if v in (None, ''):
        return None
    if not M('Core', 'Nationality').objects.filter(pk=v).exists():
        raise serializers.ValidationError('Choose a nationality from the list.')
    return v


class IdentitySerializer(serializers.ModelSerializer):
    # wider than the columns so the format checks below give the message (values are cleaned to the column size)
    passport_no = serializers.CharField(required=False, allow_blank=True, max_length=40)
    emirates_id = serializers.CharField(required=False, allow_blank=True, max_length=40)
    passport_country_name = serializers.SerializerMethodField()
    documents = serializers.SerializerMethodField()

    class Meta:
        model = EmployeeIdentity
        exclude = ['doc_links', 'doc_problems']
        read_only_fields = ['employee_id', 'updated_at', 'updated_by_id']

    def get_passport_country_name(self, o):
        if not o.passport_country_id:
            return ''
        c = M('Core', 'cntry_mstr').objects.filter(pk=o.passport_country_id).first()
        return c.country_name if c else ''

    def get_documents(self, o):
        return {'links': o.doc_links or {}, 'problems': o.doc_problems or {}}

    # ---- single fields
    def validate_arabic_name(self, v):
        v = (v or '').strip()
        if v and not any('؀' <= ch <= 'ۿ' for ch in v):
            raise serializers.ValidationError('Write the Arabic name in Arabic letters.')
        return v

    def validate_passport_no(self, v):
        return _run(V.clean_passport, v)

    def validate_passport_country_id(self, v):
        return _country_ok(v, 'passport country')

    def validate_emirates_id(self, v):
        return _run(V.clean_emirates_id, v)

    def validate_visa_uid(self, v):
        return _run(V.clean_digits, v, 'The UID number', 5, 15)

    def validate_visa_number(self, v):
        return _run(V.clean_code, v, 'Visa / residence number', 4, 30, '/-')

    def validate_visa_file_no(self, v):
        return _run(V.clean_code, v, 'Visa file number', 4, 30, '/-')

    def validate_labour_card_no(self, v):
        return _run(V.clean_code, v, 'Labour card number', 4, 20, '-')

    def validate_work_permit_no(self, v):
        return _run(V.clean_code, v, 'Work permit number', 4, 20, '-')

    def validate_driving_licence_no(self, v):
        return _run(V.clean_code, v, 'Driving licence number', 4, 20, '-/')

    def validate_place_of_birth(self, v):
        return (v or '').strip()

    def validate_driving_licence_categories(self, v):
        return ', '.join(x.strip() for x in (v or '').split(',') if x.strip())

    def validate_mohre_contract_no(self, v):
        return _run(V.clean_code, v, 'MOHRE contract number', 3, 30, '/-')

    # ---- combined
    def validate(self, attrs):
        inst = self.instance
        errors = {}
        today = date.today()

        def now(k):
            return attrs[k] if k in attrs else (getattr(inst, k, None) if inst is not None else None)

        for label, i, e in (('passport', 'passport_issue_date', 'passport_expiry_date'), ('visa', 'visa_issue_date', 'visa_expiry_date'),
                            ('Emirates ID', 'emirates_id_issue_date', 'emirates_id_expiry_date'),
                            ('labour card', 'labour_card_issue_date', 'labour_card_expiry_date'),
                            ('driving licence', 'driving_licence_issue_date', 'driving_licence_expiry_date')):
            if i in attrs or e in attrs:
                V.check_dates(now(i), now(e), label, errors, i, e, today)
        for k, label in (('work_permit_expiry_date', 'work permit'), ('insurance_expiry_date', 'insurance card')):
            d = attrs.get(k)
            if d and d.year < 1950:
                errors[k] = f'Check the {label} expiry date.'
        # numbers need their dates (the expiry alerts work on them)
        for num, i, e, label in (('passport_no', 'passport_issue_date', 'passport_expiry_date', 'passport'),
                                 ('emirates_id', 'emirates_id_issue_date', 'emirates_id_expiry_date', 'Emirates ID')):
            if any(k in attrs for k in (num, i, e)) and now(num) and not now(e):
                errors.setdefault(e, f'Enter the {label} expiry date.')
        if any(k in attrs for k in ('mohre_contract_type', 'mohre_contract_start', 'mohre_contract_end')):
            t, s, en = now('mohre_contract_type'), now('mohre_contract_start'), now('mohre_contract_end')
            if s and en and en <= s:
                errors['mohre_contract_end'] = 'The contract end date must be after the start date.'
            if t == 'limited' and s and not en:
                errors['mohre_contract_end'] = 'A limited (fixed-term) contract needs an end date.'
            if t == 'unlimited' and en:
                errors['mohre_contract_end'] = 'An unlimited contract has no end date – clear it or choose Limited.'
            if s and en and (en - s).days > 3 * 366 + 1 and t == 'limited':
                errors['mohre_contract_end'] = 'A limited contract runs for at most 3 years – check the end date (renew it afterwards).'
        if 'visa_type' in attrs and attrs['visa_type'] == 'national':
            for k in ('visa_number', 'visa_uid', 'visa_file_no'):
                if now(k):
                    errors.setdefault('visa_type', 'UAE / GCC nationals need no visa – clear the visa numbers or choose another visa type.')
        # same number on another employee
        emp_id = self.context.get('employee_id') or (inst.employee_id if inst else None)
        Emp = M('EmpManagement', 'emp_master')
        for k, label in (('passport_no', 'Passport number'), ('emirates_id', 'Emirates ID'), ('labour_card_no', 'Labour card number'),
                         ('visa_uid', 'UID number'), ('work_permit_no', 'Work permit number'),
                         ('driving_licence_no', 'Driving licence number')):
            v = attrs.get(k)
            if not v or k in errors:
                continue
            other = EmployeeIdentity.objects.filter(**{k + '__iexact': v}).exclude(employee_id=emp_id).first()
            if other:
                errors[k] = f'{label} {v} is already used by employee {emp_label(Emp.objects.filter(pk=other.employee_id).first()) or other.employee_id}.'
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class EmergencySerializer(serializers.ModelSerializer):
    class Meta:
        model = EmergencyContact
        fields = '__all__'
        read_only_fields = ['employee_id', 'created_at', 'updated_at']

    def validate_name(self, v):
        v = (v or '').strip()
        if not v:
            raise serializers.ValidationError('Enter the contact’s name.')
        return v

    def validate_relation(self, v):
        v = (v or '').strip()
        if not v:
            raise serializers.ValidationError('Enter the relation (e.g. Spouse, Father, Friend).')
        return v

    def validate_mobile(self, v):
        v = _run(V.clean_phone, v, 'Mobile number')
        if not v:
            raise serializers.ValidationError('Enter the mobile number.')
        return v

    def validate_alt_phone(self, v):
        return _run(V.clean_phone, v, 'Other phone')


class DependentExtraSerializer(serializers.ModelSerializer):
    passport_no = serializers.CharField(required=False, allow_blank=True, max_length=40)
    emirates_id = serializers.CharField(required=False, allow_blank=True, max_length=40)

    class Meta:
        model = DependentExtra
        fields = '__all__'
        read_only_fields = ['family_id', 'employee_id', 'updated_at']

    def validate_passport_no(self, v):
        return _run(V.clean_passport, v)

    def validate_emirates_id(self, v):
        return _run(V.clean_emirates_id, v)

    def validate_visa_number(self, v):
        return _run(V.clean_code, v, 'Visa number', 4, 30, '/-')

    def validate_nationality_id(self, v):
        return _nationality_ok(v)

    def validate(self, attrs):
        errors = {}
        for k in ('passport_expiry_date', 'emirates_id_expiry_date', 'visa_expiry_date'):
            d = attrs.get(k)
            if d and d.year < 1950:
                errors[k] = 'Check this expiry date.'
        inst = self.instance

        def now(k):
            return attrs[k] if k in attrs else (getattr(inst, k, None) if inst is not None else None)

        for num, exp, label in (('passport_no', 'passport_expiry_date', 'passport'), ('emirates_id', 'emirates_id_expiry_date', 'Emirates ID'),
                                ('visa_number', 'visa_expiry_date', 'visa')):
            if (num in attrs or exp in attrs) and now(exp) and not now(num):
                errors.setdefault(num, f'Enter the {label} number for this expiry date.')
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class BankExtraSerializer(serializers.ModelSerializer):
    class Meta:
        model = BankExtra
        fields = '__all__'
        read_only_fields = ['bank_detail_id', 'employee_id', 'updated_at']

    def validate_wps_agent(self, v):
        v = (v or '').strip()
        if v and len(v) > 60:
            raise serializers.ValidationError('Use at most 60 characters.')
        return v


class QualificationExtraSerializer(serializers.ModelSerializer):
    country_name = serializers.SerializerMethodField()

    class Meta:
        model = QualificationExtra
        fields = '__all__'
        read_only_fields = ['qualification_id', 'employee_id', 'updated_at']

    def get_country_name(self, o):
        if not o.country_id:
            return ''
        c = M('Core', 'cntry_mstr').objects.filter(pk=o.country_id).first()
        return c.country_name if c else ''

    def validate_country_id(self, v):
        return _country_ok(v)

    def validate_attestation_date(self, v):
        if v and v > date.today():
            raise serializers.ValidationError('The attestation date cannot be in the future.')
        return v

    def validate_attachment(self, f):
        if not f:
            return f
        ext = os.path.splitext(getattr(f, 'name', '') or '')[1].lower()
        if ext not in ('.pdf', '.jpg', '.jpeg', '.png'):
            raise serializers.ValidationError('Attach a PDF, JPG or PNG file.')
        if getattr(f, 'size', 0) > 5 * 1024 * 1024:
            raise serializers.ValidationError('The file is larger than 5 MB.')
        return f

    def validate(self, attrs):
        inst = self.instance
        att = attrs['attested'] if 'attested' in attrs else (inst.attested if inst else False)
        if attrs.get('attestation_date') and not att:
            attrs['attested'] = True
        return attrs


class EmploymentSerializer(serializers.ModelSerializer):
    LEAVING = ('on_notice', 'left', 'terminated', 'absconded', 'retired')

    class Meta:
        model = EmploymentInfo
        fields = ['status', 'date_of_leaving', 'leaving_reason']

    def validate(self, attrs):
        inst = self.instance
        errors = {}

        def now(k):
            return attrs[k] if k in attrs else getattr(inst, k, None)

        st, dol = now('status'), now('date_of_leaving')
        if st in self.LEAVING and not dol:
            errors['date_of_leaving'] = 'Enter the date of leaving (last working day) for this status.'
        if st in ('active', 'probation'):
            if 'date_of_leaving' in attrs and attrs['date_of_leaving']:
                errors['date_of_leaving'] = 'An active employee has no date of leaving – change the status first.'
            attrs['date_of_leaving'] = None
            if 'leaving_reason' not in attrs:
                attrs['leaving_reason'] = ''
        if st == 'probation' and inst is not None and inst.probation_status not in ('on_probation', 'extended'):
            errors['status'] = 'The probation is already decided – use Active, or record a decision on the Employment tab.'
        joined = self.context.get('joined')
        if dol and joined and dol < joined:
            errors['date_of_leaving'] = 'The date of leaving cannot be before the joining date.'
        if st == 'on_notice' and dol and dol < date.today():
            errors['status'] = 'The last working day has passed – choose Left, Terminated, Retired or Absconded.'
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class CodeSettingSerializer(serializers.ModelSerializer):
    branch_name = serializers.SerializerMethodField()
    preview = serializers.SerializerMethodField()

    class Meta:
        model = EmployeeCodeSetting
        fields = ['id', 'branch_id', 'branch_name', 'prefix', 'next_number', 'padding', 'enabled', 'preview', 'updated_at']
        read_only_fields = ['updated_at']
        extra_kwargs = {'branch_id': {'validators': []}}

    def get_branch_name(self, o):
        if not o.branch_id:
            return 'Company default'
        b = M('OrganisationManager', 'brnch_mstr').objects.filter(pk=o.branch_id).first()
        return b.branch_name if b else f'Branch {o.branch_id}'

    def get_preview(self, o):
        return o.format()

    def validate_branch_id(self, v):
        if v in (None, ''):
            return None
        if not M('OrganisationManager', 'brnch_mstr').objects.filter(pk=v).exists():
            raise serializers.ValidationError('Choose a branch from the list.')
        qs = EmployeeCodeSetting.objects.filter(branch_id=v)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError('This branch already has a numbering rule – edit that one.')
        return v

    def validate_prefix(self, v):
        import re
        v = (v or '').strip()
        if v and not re.fullmatch(r'[A-Za-z0-9\-_/]{1,20}', v):
            raise serializers.ValidationError('Use up to 20 letters, digits, - _ or /.')
        return v

    def validate_padding(self, v):
        if v is None or not 1 <= int(v) <= 10:
            raise serializers.ValidationError('Use 1 to 10 digits.')
        return v

    def validate_next_number(self, v):
        if v is None or int(v) < 1:
            raise serializers.ValidationError('The next number must be 1 or more.')
        return v

    def validate(self, attrs):
        if 'branch_id' not in attrs and self.instance is None:
            if EmployeeCodeSetting.objects.filter(branch_id__isnull=True).exists():
                raise serializers.ValidationError({'branch_id': 'The company default already exists – edit it, or choose a branch.'})
        return attrs
