"""
Form designer support (no change to the existing tables):

* FormSetting keeps the designer's labels / mandatory / hidden / dropdown settings per company
  (they were only in each browser's local storage before).
* Custom fields (employee, family, qualification, job history, documents):
    - renaming a field moves the values already entered to the new name;
    - deleting a field deletes its values (they used to come back when a field with the same name was added again);
    - saving a value for a field that does not exist returns a clear 400 instead of a server error;
    - the "Mandatory field" tick is stored (as FormSetting 'custom:<form>') and returned as `mandatory`.
"""
import logging
from django.apps import apps
from django.db.models.signals import pre_save, post_delete
from rest_framework import serializers

logger = logging.getLogger(__name__)

# definition model, value model, form key
PAIRS = [
    ('EmpManagement.Emp_CustomField', 'EmpManagement.Emp_CustomFieldValue', 'employee'),
    ('EmpManagement.EmpFamily_CustomField', 'EmpManagement.Fam_CustomFieldValue', 'family'),
    ('EmpManagement.EmpJobHistory_CustomField', 'EmpManagement.JobHistory_CustomFieldValue', 'job_history'),
    ('EmpManagement.EmpQualification_CustomField', 'EmpManagement.Qualification_CustomFieldValue', 'qualification'),
    ('EmpManagement.EmpDocuments_CustomField', 'EmpManagement.Doc_CustomFieldValue', 'documents'),
]
_defs, _vals = {}, {}


def _load():
    if _defs:
        return
    for d, v, key in PAIRS:
        try:
            D, V = apps.get_model(d), apps.get_model(v)
        except LookupError:
            continue
        _defs[D] = (V, key)
        _vals[V] = (D, key)


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


def set_layout(key, name, values, user=None, old_name=None, delete=False):
    """Order, section, help text and placeholder of a custom field (v1.7.0)."""
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
        data[name] = cur
    row.data = data
    if user is not None and getattr(user, 'is_authenticated', False):
        row.updated_by = user
    row.save()


class _TypedField:
    """Adapter so employee custom fields use the same value checks as the designer fields of other screens."""
    def __init__(self, d, required=False):
        self.label = d.emp_custom_field
        self.field_type = d.data_type or 'text'
        self.options = d.dropdown_values or d.radio_values or []
        self.required = required


def typed_value(definition, value):
    """Normalised text value or raise ValidationError (number, e-mail, phone, URL, multi-select …)."""
    if (definition.data_type or 'text') in ('text', 'dropdown', 'radio', 'checkbox', 'date'):
        return value   # the model's own checks apply
    from Chatter.views import clean_value
    val, err = clean_value(_TypedField(definition), value)
    if err:
        raise serializers.ValidationError({'field_value': [err]})
    if val is None:
        return value
    if isinstance(val, list):
        return ', '.join(val)
    if isinstance(val, bool):
        return 'Yes' if val else 'No'
    return str(val)


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


# ---------------------------------------------------------------- serializer hooks
def check_value(serializer, attrs):
    """Value rows must point to an existing custom field (case-insensitive, stored with its exact name)."""
    _load()
    model = getattr(getattr(serializer, 'Meta', None), 'model', None)
    if model in _defs and isinstance(attrs, dict) and attrs.get('data_type') == 'checkbox' and not attrs.get('checkbox_values'):
        attrs['checkbox_values'] = ['Yes', 'No']  # the designer has no option list for a checkbox
    if model not in _vals or not isinstance(attrs, dict) or 'emp_custom_field' not in attrs:
        return
    D, key = _vals[model]
    name = str(attrs.get('emp_custom_field') or '').strip()
    hit = D.objects.filter(emp_custom_field__iexact=name).values_list('emp_custom_field', flat=True).first() if name else None
    if not hit:
        raise serializers.ValidationError({'emp_custom_field': [f'There is no custom field called “{name}”. Add it in the form designer first.']})
    attrs['emp_custom_field'] = hit
    if 'field_value' in attrs and attrs.get('field_value') not in (None, ''):
        definition = D.objects.filter(emp_custom_field=hit).first()
        if definition is not None:
            attrs['field_value'] = typed_value(definition, attrs['field_value'])


def after_save(serializer, instance):
    _load()
    model = type(instance)
    if model not in _defs:
        return
    raw = getattr(serializer, 'initial_data', None)
    if not isinstance(raw, dict):
        return
    req = serializer.context.get('request') if hasattr(serializer, 'context') else None
    if any(k in raw for k in LAYOUT_KEYS):
        set_layout(_defs[model][1], instance.emp_custom_field, raw, getattr(req, 'user', None))
    if 'mandatory' not in raw:
        return
    flag = str(raw.get('mandatory')).lower() in ('true', '1', 'yes')
    set_mandatory(_defs[model][1], instance.emp_custom_field, flag, getattr(req, 'user', None))


def add_mandatory(serializer, instance, rep):
    _load()
    model = type(instance)
    if model in _defs and isinstance(rep, dict) and 'emp_custom_field' in rep:
        cache = serializer.__dict__.setdefault('_zeo_mand', {})
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
            logger.exception('custom field mandatory flag not saved')
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
