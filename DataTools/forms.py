"""
Form designer support (no change to the existing tables):

* FormSetting keeps the designer's labels / mandatory / hidden / dropdown settings per company
  (they were only in each browser's local storage before).
* Custom fields (employee, family, qualification, job history, documents):
    - renaming a field moves the values already entered to the new name;
    - deleting a field deletes its values (they used to come back when a field with the same name was added again);
    - saving a value for a field that does not exist returns a clear 400 instead of a server error;
    - the "Mandatory field" tick is stored (as FormSetting 'custom:<form>') and returned as `mandatory`;
    - order, section, help text, placeholder (v1.7.0) and, from v1.12.0, the field rules (default, lowest / highest,
      length, pattern, show on, show only if, read-only for employees – see fieldrules.py) are stored as
      FormSetting 'layout:<form>' and returned with every field definition.
* v1.12.0 – one typed check for every field type, used by the API, the models (create and update), the import
  and the employee form:
    text / long text, whole number, decimal, amount (2 decimals), percentage (0-100), date (stored DD-MM-YYYY),
    date and time (YYYY-MM-DDTHH:MM), time (HH:MM), Yes / No (stored Yes / No), dropdown, radio, multi-select
    (stored "A, B" in the order of the options), e-mail, phone, web link, rating (1-5), colour (#RRGGBB).
* v1.12.0 – the employee form (EmpSerializer) can send its custom values with the employee itself:
  `custom_fields` = {"Field name": value, ...} (JSON text in a multipart form). They are checked before the
  employee is saved (mandatory, type, rules, defaults) and stored right after it. Built-in fields ticked
  "Mandatory" in the form designer are required on create, and cannot be emptied on edit.
"""
import json
import logging
from django.apps import apps
from django.db.models.signals import pre_save, post_delete
from rest_framework import serializers

from .fieldrules import RULE_KEYS, clean_rules, is_empty, show_if_ok, visible_on

logger = logging.getLogger(__name__)

# definition model, value model, form key, value -> parent record field
PAIRS = [
    ('EmpManagement.Emp_CustomField', 'EmpManagement.Emp_CustomFieldValue', 'employee', 'emp_master'),
    ('EmpManagement.EmpFamily_CustomField', 'EmpManagement.Fam_CustomFieldValue', 'family', 'emp_family'),
    ('EmpManagement.EmpJobHistory_CustomField', 'EmpManagement.JobHistory_CustomFieldValue', 'job_history', 'emp_job_history'),
    ('EmpManagement.EmpQualification_CustomField', 'EmpManagement.Qualification_CustomFieldValue', 'qualification', 'emp_qualification'),
    ('EmpManagement.EmpDocuments_CustomField', 'EmpManagement.Doc_CustomFieldValue', 'documents', 'emp_documents'),
]
OPTION_TYPES = ('dropdown', 'radio', 'multiselect')
_defs, _vals, _fk = {}, {}, {}

# employee form designer "Mandatory" ticks of the built-in fields -> employee column (create-employee screen)
BUILTIN_MANDATORY = {
    'isFirstNameMandatory': ('emp_first_name', 'firstNameFieldName', 'First name'),
    'isLastNameMandatory': ('emp_last_name', 'lastNameFieldName', 'Last name'),
    'isGenderMandatory': ('emp_gender', 'genderFieldName', 'Gender'),
    'isEmailMandatory': ('emp_personal_email', 'emailFieldName', 'E-mail'),
    'isCmpnoMandatory': ('emp_mobile_number_1', 'cmpnoFieldName', 'Company number'),
    'isPernoMandatory': ('emp_mobile_number_2', 'pernoFieldName', 'Personal number'),
    'isPeraddMandatory': ('emp_permenent_address', 'peraddressFieldName', 'Permanent address'),
    'isPresaddMandatory': ('emp_present_address', 'preaddressFieldName', 'Present address'),
    'isCityMandatory': ('emp_city', 'cityFieldName', 'City'),
    'isRelMandatory': ('emp_relegion', 'religionFieldName', 'Religion'),
    'isBloodMandatory': ('emp_blood_group', 'bloodFieldName', 'Blood group'),
    'isNatMandatory': ('emp_nationality', 'nationFieldName', 'Nationality'),
    'isMariMandatory': ('emp_marital_status', 'maritalFieldName', 'Marital status'),
    'isFatherMandatory': ('emp_father_name', 'fatherFieldName', 'Father name'),
    'isMotherMandatory': ('emp_mother_name', 'motherFieldName', 'Mother name'),
    'isLocationMandatory': ('emp_posting_location', 'locationFieldName', 'Location'),
    'isLCountryMandatory': ('emp_country_id', 'cntryFieldName', 'Country'),
    'isLBranchMandatory': ('emp_branch_id', 'brchFieldName', 'Branch'),
    'isLDepartmentMandatory': ('emp_dept_id', 'deptFieldName', 'Department'),
    'isLDesignationMandatory': ('emp_desgntn_id', 'desFieldName', 'Designation'),
    'isLCatogoryMandatory': ('emp_ctgry_id', 'catFieldName', 'Category'),
    'isHiringMandatory': ('emp_joined_date', 'hiredFieldName', 'Joining date'),
    'isreportingMandatory': ('emp_reporting_manager', 'repoFieldName', 'Reporting manager'),
}


def _load():
    if _defs:
        return
    for d, v, key, fk in PAIRS:
        try:
            D, V = apps.get_model(d), apps.get_model(v)
        except LookupError:
            continue
        _defs[D] = (V, key)
        _vals[V] = (D, key)
        _fk[V] = fk


def models_for(key):
    """(definition model, value model, parent field) of a form key ('employee', 'family' …)."""
    _load()
    for V, (D, k) in _vals.items():
        if k == key:
            return D, V, _fk[V]
    return None, None, None


def _mandatory_map(key):
    from .models import FormSetting
    try:
        row = FormSetting.objects.filter(form=f'custom:{key}').first()
        return dict(row.data) if row else {}
    except Exception:
        return {}


def set_mandatory(key, name, flag, user=None, old_name=None):
    from .models import FormSetting
    row, _ = FormSetting.objects.get_or_create(form=f'custom:{key}')
    data = dict(row.data or {})
    if old_name and old_name in data:
        data.pop(old_name)
    if flag is None:
        flag = data.get(name, False)
    data[name] = bool(flag)
    row.data = data
    if user is not None and getattr(user, 'is_authenticated', False):
        row.updated_by = user
    row.save()


LAYOUT_KEYS = ('order', 'section', 'help_text', 'placeholder')


def _layout_map(key):
    from .models import FormSetting
    try:
        row = FormSetting.objects.filter(form=f'layout:{key}').first()
        return dict(row.data) if row else {}
    except Exception:
        return {}


def rules_from(values, data_type='text', current=None):
    """Rules sent with a definition: {"rules": {...}} (replaces them) and / or top-level keys
    (min, max, regex, default …, change just those). Nothing sent: the current rules. Returns (rules, error)."""
    if not hasattr(values, 'get'):
        return dict(current or {}), None
    has_rules = isinstance(values.get('rules'), dict)
    top = {k: values.get(k) for k in RULE_KEYS if k in values}
    if has_rules:   # a whole field sent back (old designer: default '' next to rules) – empty extras change nothing
        top = {k: v for k, v in top.items() if v not in (None, '', [], {})}
    if not has_rules and not top:
        return dict(current or {}), None
    merged = dict(values.get('rules')) if has_rules else dict(current or {})
    merged.update(top)
    merged = {k: v for k, v in merged.items() if v not in (None, '', [], {})}
    return clean_rules(merged, data_type or 'text')


def set_layout(key, name, values, user=None, old_name=None, delete=False, data_type=None):
    """Order, section, help text, placeholder (v1.7.0) and rules (v1.12.0) of a custom field."""
    from .models import FormSetting
    row, _ = FormSetting.objects.get_or_create(form=f'layout:{key}')
    data = dict(row.data or {})
    if old_name and old_name in data:
        data[name] = data.pop(old_name)
    if delete:
        data.pop(name, None)
    elif values is not None:
        cur = dict(data.get(name) or {})
        for k in LAYOUT_KEYS:
            if k in values:
                v = values.get(k)
                cur[k] = int(v) if k == 'order' and str(v).lstrip('-').isdigit() else ('' if v is None else str(v)[:255])
        rules, err = rules_from(values, data_type, cur.get('rules'))
        if not err:
            if rules:
                cur['rules'] = rules
            else:
                cur.pop('rules', None)
        data[name] = cur
    row.data = data
    if user is not None and getattr(user, 'is_authenticated', False):
        row.updated_by = user
    row.save()


def field_meta(key):
    """{field name: {mandatory, order, section, help_text, placeholder, rules}} of a custom field form."""
    mand, lay = _mandatory_map(key), _layout_map(key)
    out = {}
    for name in set(mand) | set(lay):
        m = dict(lay.get(name) or {})
        m['mandatory'] = bool(mand.get(name, False))
        m['rules'] = m.get('rules') or {}
        out[name] = m
    return out


class _TypedField:
    """Adapter so employee custom fields use the same value checks as the designer fields of other screens."""
    def __init__(self, d, required=False, rules=None):
        self.label = d.emp_custom_field
        self.field_type = d.data_type or 'text'
        if self.field_type == 'multiselect':
            self.options = d.dropdown_values or d.checkbox_values or d.radio_values or []
        else:
            self.options = d.dropdown_values or d.radio_values or []
        self.required = required
        self.rules = rules or {}


def typed_value(definition, value, rules=None, required=False):
    """Normalised text to store, or None for an empty value; raises ValidationError with a clear message."""
    from Chatter.views import clean_value
    t = definition.data_type or 'text'
    f = _TypedField(definition, required, rules)
    if t in ('dropdown', 'radio') and not f.options:
        f.field_type = 'text'   # an old field without options: any text
    if t == 'multiselect' and not f.options:
        f.field_type = 'text'
    if t == 'checkbox' and definition.checkbox_values and not is_empty(value) and not isinstance(value, bool):
        # an old checkbox with its own option list: its first option means ticked
        opts = [str(o) for o in definition.checkbox_values]
        if str(value) in opts and str(value).lower() not in ('yes', 'no', 'true', 'false'):
            value = opts.index(str(value)) == 0
    val, err = clean_value(f, value)
    if err:
        raise serializers.ValidationError({'field_value': [err]})
    if val is None:
        return None
    if isinstance(val, list):
        return ', '.join(val)
    if isinstance(val, bool):
        return 'Yes' if val else 'No'
    if t == 'date':   # stored as DD-MM-YYYY like the values entered before
        y, m, d = str(val)[:10].split('-')
        return f'{d}-{m}-{y}'
    if t in ('integer', 'rating'):
        return str(int(val))
    if t == 'currency':
        return f'{float(val):.2f}'
    if t in ('decimal', 'percent'):
        return ('%f' % float(val)).rstrip('0').rstrip('.')
    return str(val)


def _request_is_ess(request):
    if request is None:
        return False
    try:
        from Chatter.views import is_ess
        return is_ess(request)
    except Exception:
        return False


def _definition_for(D, name):
    return D.objects.filter(emp_custom_field__iexact=str(name or '').strip()).first() if str(name or '').strip() else None


def check_row(V, name, value, parent=None, request=None, current=None, row_pk=None):
    """Check one custom value; returns (exact field name, normalised value) or raises ValidationError."""
    _load()
    D, key = _vals[V]
    d = _definition_for(D, name)
    if d is None:
        raise serializers.ValidationError({'emp_custom_field': [f'There is no custom field called “{name}”. Add it in the form designer first.']})
    meta = field_meta(key).get(d.emp_custom_field, {})
    rules = meta.get('rules') or {}
    others = dict(current or {})
    if parent is not None and current is None:
        fk = _fk[V]
        others = {n: v for n, v in V.objects.filter(**{fk: parent}).exclude(pk=row_pk).values_list('emp_custom_field', 'field_value')}
    others[d.emp_custom_field] = value
    shown = show_if_ok(rules, others)
    if _request_is_ess(request) and (rules.get('ess_read_only') or not visible_on(rules, 'ess')):   # read-only / hidden in self service
        old = None
        if parent is not None:
            old = V.objects.filter(**{_fk[V]: parent, 'emp_custom_field': d.emp_custom_field}).values_list('field_value', flat=True).first()
        new = typed_value(d, value, rules, False)
        if (new or None) != (old or None):
            raise serializers.ValidationError({'field_value': [f'{d.emp_custom_field} is kept up to date by HR; you cannot change it.']})
    return d.emp_custom_field, typed_value(d, value, rules, bool(meta.get('mandatory')) and shown)


# ---------------------------------------------------------------- signals
def _before_save(sender, instance, **kwargs):
    if not instance.pk:
        return
    old = sender.objects.filter(pk=instance.pk).values_list('emp_custom_field', flat=True).first()
    new = instance.emp_custom_field
    if old and new and old != new:
        V, key = _defs[sender]
        V.objects.filter(emp_custom_field=old).update(emp_custom_field=new)  # keep the entered values
        m = _mandatory_map(key)
        if old in m:
            set_mandatory(key, new, m[old], old_name=old)
        if old in _layout_map(key):
            set_layout(key, new, None, old_name=old)


def _after_delete(sender, instance, **kwargs):
    V, key = _defs[sender]
    if instance.emp_custom_field:
        V.objects.filter(emp_custom_field=instance.emp_custom_field).delete()
        m = _mandatory_map(key)
        if instance.emp_custom_field in m:
            from .models import FormSetting
            FormSetting.objects.filter(form=f'custom:{key}').update(data={k: v for k, v in m.items() if k != instance.emp_custom_field})
        if instance.emp_custom_field in _layout_map(key):
            set_layout(key, instance.emp_custom_field, None, delete=True)


# ---------------------------------------------------------------- model hooks (EmpManagement value models call these)
def clean_model_value(instance):
    """Value model clean(): the typed check (no request: no self-service rules)."""
    from django.core.exceptions import ValidationError as DjangoValidationError
    _load()
    V = type(instance)
    parent = getattr(instance, _fk[V] + '_id', None)
    try:
        name, val = check_row(V, instance.emp_custom_field, instance.field_value, parent=parent, row_pk=instance.pk)
    except serializers.ValidationError as e:
        det = e.detail if isinstance(e.detail, dict) else {'field_value': e.detail}
        raise DjangoValidationError({k: [str(x) for x in (v if isinstance(v, list) else [v])] for k, v in det.items()})
    instance.emp_custom_field = name
    instance.field_value = val


def save_model_value(instance, super_save, *args, **kwargs):
    """Value model save(): one row per record and field, checked on create AND update."""
    _load()
    V = type(instance)
    fk = _fk[V]
    if not instance.emp_custom_field:
        from django.core.exceptions import ValidationError as DjangoValidationError
        raise DjangoValidationError({'emp_custom_field': ['Choose the custom field.']})
    instance.full_clean()
    existing = V.objects.filter(emp_custom_field=instance.emp_custom_field, **{fk: getattr(instance, fk + '_id')}).exclude(pk=instance.pk).first()
    if existing is not None and not instance.pk:
        # same record and field again: update the row that is already there
        V.objects.filter(pk=existing.pk).update(field_value=instance.field_value)
        instance.pk = existing.pk
        instance.created_at = existing.created_at
        return
    super_save(*args, **kwargs)


# ---------------------------------------------------------------- serializer hooks
def _req(serializer):
    return serializer.context.get('request') if hasattr(serializer, 'context') else None


def _raw(serializer):
    raw = getattr(serializer, 'initial_data', None)
    return raw if hasattr(raw, 'get') else {}


def _check_definition(serializer, D, attrs):
    V, key = _defs[D]
    inst = getattr(serializer, 'instance', None)
    if 'emp_custom_field' in attrs:
        name = str(attrs.get('emp_custom_field') or '').strip()
        if not name:
            raise serializers.ValidationError({'emp_custom_field': ['Give the field a name.']})
        dup = D.objects.filter(emp_custom_field__iexact=name)
        if inst is not None:
            dup = dup.exclude(pk=inst.pk)
        if dup.exists():
            raise serializers.ValidationError({'emp_custom_field': [f'A field called “{name}” already exists on this form.']})
        attrs['emp_custom_field'] = name
    t = attrs.get('data_type', getattr(inst, 'data_type', None)) or 'text'
    if inst is None and not attrs.get('data_type'):
        attrs['data_type'] = t
    if t == 'checkbox' and not attrs.get('checkbox_values') and not getattr(inst, 'checkbox_values', None):
        attrs['checkbox_values'] = ['Yes', 'No']  # the designer has no option list for a Yes / No field
    if t in OPTION_TYPES:
        src = {'dropdown': 'dropdown_values', 'radio': 'radio_values', 'multiselect': 'dropdown_values'}[t]
        opts = attrs.get(src, getattr(inst, src, None) if inst is not None else None)
        if not opts and t == 'multiselect':   # older screens sent multi-select options as radio / checkbox values
            opts = attrs.get('radio_values') or attrs.get('checkbox_values')
        if isinstance(opts, str):
            opts = [o.strip() for o in opts.split(',')]
        opts = [str(o).strip() for o in (opts or []) if str(o).strip()]
        if not opts:
            raise serializers.ValidationError({src: ['Add at least one option.']})
        if len({o.lower() for o in opts}) != len(opts):
            raise serializers.ValidationError({src: ['Options must be different from each other.']})
        attrs[src] = opts
    raw = _raw(serializer)
    cur = {}
    if inst is not None:
        cur = (_layout_map(key).get(inst.emp_custom_field) or {}).get('rules') or {}
    rules, err = rules_from(raw, t, cur)
    if err:
        raise serializers.ValidationError({'rules': [err]})
    dv = rules.get('default')
    if dv not in (None, ''):
        try:
            typed_value(_Probe(attrs, inst, t), dv, rules, False)
        except serializers.ValidationError as e:
            raise serializers.ValidationError({'default': [f'Default value – {_msg(e)}']})
    # a new type / fewer options / new rules: the stored values that would no longer fit
    if inst is not None and str(raw.get('confirm')).lower() not in ('true', '1', 'yes'):
        probe = _Probe(attrs, inst, t)
        changed = (t != (inst.data_type or 'text') or (probe.dropdown_values or None) != (inst.dropdown_values or None)
                   or (probe.radio_values or None) != (inst.radio_values or None) or rules != cur)
        if changed:
            bad = []
            for pk, val in V.objects.filter(emp_custom_field=inst.emp_custom_field).exclude(field_value__isnull=True).exclude(field_value='').values_list('pk', 'field_value')[:5000]:
                try:
                    typed_value(probe, val, rules, False)
                except serializers.ValidationError as e:
                    bad.append({'value_id': pk, 'value': val, 'error': _msg(e)})
            if bad:
                raise serializers.ValidationError({
                    'confirm': [f'{len(bad)} record(s) have a value that does not fit the changed field. Send confirm=true to keep the change; '
                                f'those values stay as they are until the records are edited.'],
                    'affected': bad[:200]})
    serializer._zeo_rules = rules


class _Probe:
    """Definition as it will be after this save (for checking the default and the stored values)."""
    def __init__(self, attrs, inst, t):
        g = lambda k: attrs.get(k, getattr(inst, k, None) if inst is not None else None)
        self.emp_custom_field = g('emp_custom_field') or 'Field'
        self.data_type = t
        self.dropdown_values = g('dropdown_values')
        self.radio_values = g('radio_values')
        self.checkbox_values = g('checkbox_values')


def _msg(e):
    d = e.detail
    if isinstance(d, dict):
        d = [x for v in d.values() for x in (v if isinstance(v, list) else [v])]
    return ' '.join(str(x) for x in (d if isinstance(d, list) else [d]))


def _check_value_row(serializer, V, attrs):
    inst = getattr(serializer, 'instance', None)
    fk = _fk[V]
    name = attrs.get('emp_custom_field', getattr(inst, 'emp_custom_field', None))
    if not str(name or '').strip():
        raise serializers.ValidationError({'emp_custom_field': ['Choose the custom field.']})
    parent = attrs.get(fk, getattr(inst, fk, None) if inst is not None else None)
    value = attrs.get('field_value', getattr(inst, 'field_value', None) if inst is not None else None)
    exact, val = check_row(V, name, value, parent=parent, request=_req(serializer), row_pk=getattr(inst, 'pk', None))
    attrs['emp_custom_field'] = exact
    attrs['field_value'] = val


def _parse_custom(raw):
    cf = raw.get('custom_fields') if hasattr(raw, 'get') else None
    if cf in (None, ''):
        return None
    if isinstance(cf, str):
        try:
            cf = json.loads(cf)
        except ValueError:
            raise serializers.ValidationError({'custom_fields': ['Send the custom fields as {"Field name": value}.']})
    if isinstance(cf, list):
        cf = {str(x.get('emp_custom_field')): x.get('field_value') for x in cf if isinstance(x, dict) and x.get('emp_custom_field')}
    if not isinstance(cf, dict):
        raise serializers.ValidationError({'custom_fields': ['Send the custom fields as {"Field name": value}.']})
    return cf


def _check_employee(serializer, attrs):
    """Built-in fields ticked Mandatory, and the custom values sent with the employee."""
    from .models import FormSetting
    inst = getattr(serializer, 'instance', None)
    create = inst is None
    row = FormSetting.objects.filter(form='employee').first()
    data = (row.data if row else {}) or {}
    errors = {}
    for flag, (col, label_key, label) in BUILTIN_MANDATORY.items():
        if str(data.get(flag)).lower() != 'true' or str(data.get(flag.replace('Mandatory', 'FieldHidden'))).lower() == 'true':
            continue
        if create or col in attrs:
            v = attrs.get(col)
            if v is None or (isinstance(v, str) and not v.strip()):
                errors[col] = [f'{data.get(label_key) or label} is required.']
    cf = _parse_custom(_raw(serializer))
    if cf is not None:
        D, V, fk = models_for('employee')
        meta = field_meta('employee')
        defs = {d.emp_custom_field: d for d in D.objects.all()}
        lower = {k.lower(): k for k in defs}
        given = {}
        for k, v in cf.items():
            exact = lower.get(str(k).strip().lower())
            if exact is None:
                errors.setdefault('custom_fields', []).append(f'There is no custom field called “{k}”.')
            else:
                given[exact] = v
        if create:
            for n, d in defs.items():
                dv = (meta.get(n, {}).get('rules') or {}).get('default')
                if n not in given and dv not in (None, ''):
                    given[n] = dv
        current = {}
        if inst is not None:
            current = dict(V.objects.filter(emp_master=inst).values_list('emp_custom_field', 'field_value'))
        current.update(given)
        clean = {}
        req = _req(serializer)
        for n, d in defs.items():
            m = meta.get(n, {})
            rules = m.get('rules') or {}
            shown = show_if_ok(rules, current) and (visible_on(rules, 'create') if create else visible_on(rules, 'edit'))
            if n not in given:
                if create and m.get('mandatory') and shown:
                    errors.setdefault('custom_fields', []).append(f'{n} is required.' if d.data_type != 'checkbox' else f'{n} must be ticked.')
                continue
            try:
                _, clean[n] = check_row(V, n, given[n], parent=inst, request=req, current=current)
            except serializers.ValidationError as e:
                errors.setdefault('custom_fields', []).append(_msg(e))
        serializer._zeo_custom = clean
    if errors:
        raise serializers.ValidationError(errors)


def pre_data(serializer, data):
    """Custom field values sent as JSON true / false, a list or a number are taken as their text."""
    _load()
    model = getattr(getattr(serializer, 'Meta', None), 'model', None)
    if model not in _vals or not hasattr(data, 'get'):
        return data
    v = data.get('field_value')
    if isinstance(v, (bool, list, int, float)) and v is not None:
        data = data.copy() if hasattr(data, 'copy') else dict(data)
        data['field_value'] = ('Yes' if v else 'No') if isinstance(v, bool) else (', '.join(str(x) for x in v) if isinstance(v, list) else str(v))
    return data


def check_value(serializer, attrs):
    """Run from ModelSerializer.run_validation (DataTools.duplicates.install)."""
    _load()
    model = getattr(getattr(serializer, 'Meta', None), 'model', None)
    if not isinstance(attrs, dict) or model is None:
        return
    if model in _defs:
        _check_definition(serializer, model, attrs)
    elif model in _vals:
        if 'emp_custom_field' in attrs or 'field_value' in attrs or getattr(serializer, 'instance', None) is None:
            _check_value_row(serializer, model, attrs)
    elif model._meta.label == 'EmpManagement.emp_master' and type(serializer).__name__ == 'EmpSerializer':
        _check_employee(serializer, attrs)


def after_save(serializer, instance):
    _load()
    model = type(instance)
    req = _req(serializer)
    user = getattr(req, 'user', None)
    if model._meta.label == 'EmpManagement.emp_master' and getattr(serializer, '_zeo_custom', None) is not None:
        D, V, fk = models_for('employee')
        for n, v in serializer._zeo_custom.items():
            row = V.objects.filter(emp_master=instance, emp_custom_field=n).first()
            if row is None:
                if v is not None:
                    V.objects.create(emp_master=instance, emp_custom_field=n, field_value=v, created_by=user if getattr(user, 'is_authenticated', False) else None)
            elif row.field_value != v:
                V.objects.filter(pk=row.pk).update(field_value=v)
        return
    if model not in _defs:
        return
    raw = _raw(serializer)
    if not raw:
        return
    key = _defs[model][1]
    if any(k in raw for k in LAYOUT_KEYS + RULE_KEYS + ('rules',)):
        set_layout(key, instance.emp_custom_field, raw, user, data_type=instance.data_type)
    if 'mandatory' not in raw:
        return
    flag = str(raw.get('mandatory')).lower() in ('true', '1', 'yes')
    set_mandatory(key, instance.emp_custom_field, flag, user)


def add_mandatory(serializer, instance, rep):
    _load()
    model = type(instance)
    if not isinstance(rep, dict) or 'emp_custom_field' not in rep:
        return rep
    cache = serializer.__dict__.setdefault('_zeo_mand', {})
    if model in _defs:
        key = _defs[model][1]
        if key not in cache:
            cache[key] = _mandatory_map(key)
        rep['mandatory'] = bool(cache[key].get(instance.emp_custom_field, False))
        lk = 'layout:' + key
        if lk not in cache:
            cache[lk] = _layout_map(key)
        lay = cache[lk].get(instance.emp_custom_field) or {}
        rep['order'] = lay.get('order', instance.pk * 10)
        rep['section'] = lay.get('section', '')
        rep['help_text'] = lay.get('help_text', '')
        rep['placeholder'] = lay.get('placeholder', '')
        rules = lay.get('rules') or {}
        rep['rules'] = rules
        rep['default'] = rules.get('default', '')
        rep['options'] = (instance.dropdown_values or instance.radio_values or []) if (instance.data_type or 'text') in OPTION_TYPES else []
    elif model in _vals:
        # v1.12.0: values carry their field's type, so every panel shows them typed (not as plain text)
        D, key = _vals[model]
        dk = 'defs:' + key
        if dk not in cache:
            cache[dk] = {d.emp_custom_field: d.data_type or 'text' for d in D.objects.all()}
        rep['data_type'] = cache[dk].get(instance.emp_custom_field, 'text')
    return rep


_installed = False


def install():
    global _installed
    if _installed:
        return
    _load()
    for D in list(_defs):
        pre_save.connect(_before_save, sender=D, dispatch_uid=f'zeo_cf_rename_{D.__name__}')
        post_delete.connect(_after_delete, sender=D, dispatch_uid=f'zeo_cf_delete_{D.__name__}')
    orig_save = serializers.ModelSerializer.save
    orig_rep = serializers.ModelSerializer.to_representation

    def save(self, **kwargs):
        inst = orig_save(self, **kwargs)
        try:
            after_save(self, inst)
        except Exception:
            logger.exception('custom field settings / values not saved')
        return inst

    def to_representation(self, instance):
        rep = orig_rep(self, instance)
        try:
            return add_mandatory(self, instance, rep)
        except Exception:
            return rep

    serializers.ModelSerializer.save = save
    serializers.ModelSerializer.to_representation = to_representation
    _installed = True
