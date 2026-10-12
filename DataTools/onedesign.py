"""
v1.16.0 – ONE form designer per form.

Before v1.16.0 a form could get custom fields from several designers:
  * the employee form designer (Settings → Form designer, "AddMoreField", record panel "Design employee fields")
    → EmpManagement.Emp_CustomField / EmpFamily_ / EmpQualification_ / EmpJobHistory_ / EmpDocuments_CustomField
      + values in the matching *_CustomFieldValue tables, layout / rules / mandatory in DataTools.FormSetting;
  * the asset UDF screen (Assets → Custom fields) → OrganisationManager.AssetCustomField / AssetCustomFieldValue
    (used by AssetPlus asset create / edit / detail);
  * the generic "Design this form" of the record panel (v1.7.0) → Chatter.FieldDef / FieldValue – also offered on
    the employee and asset screens, so the same field could be defined (and filled in) twice;
  * the built-in fields added in v1.13.0 (EmployeeProfile: UAE identity, employment status …; AssetPlus: asset
    profile) – customers had added custom fields such as "Visa Type" or "Labour Card Expiry" before those existed.

Decision (see REPORT.txt): the store that already holds the customer's data owns the form –
  employee forms → the employee custom fields (EmpManagement), assets → AssetCustomField.
  Chatter.FieldDef stays the designer of every OTHER screen.
This module:
  * OWNERS            – Chatter model key → owning designer (the record panel / Chatter API redirect to it);
  * builtin_fields()  – the built-in fields of each form, so a custom field cannot duplicate one;
  * duplicate checks  – used by every designer API (employee, asset, Chatter);
  * asset field layout (mandatory, section, order, help text, example, rules) in FormSetting 'layout:asset';
  * unify()           – the data migration (management command `unify_form_designers`, dry run by default).
"""
import datetime
import json
import re

from django.apps import apps
from django.db import transaction

# Chatter model key -> owning designer. kind = frontend designer kind, form = DataTools form key
OWNERS = {
    'EmpManagement.emp_master': {'store': 'employee', 'kind': 'employee', 'form': 'employee', 'title': 'Employee'},
    'EmpManagement.emp_family': {'store': 'employee', 'kind': 'family', 'form': 'family', 'title': 'Family'},
    'EmpManagement.empqualification': {'store': 'employee', 'kind': 'qualification', 'form': 'qualification', 'title': 'Qualification'},
    'EmpManagement.empjobhistory': {'store': 'employee', 'kind': 'job', 'form': 'job_history', 'title': 'Job history'},
    'EmpManagement.emp_documents': {'store': 'employee', 'kind': 'documents', 'form': 'documents', 'title': 'Documents'},
    'OrganisationManager.asset': {'store': 'asset', 'kind': 'asset', 'form': 'asset', 'title': 'Asset'},
}
# where the owning designer lives in the app (shown in messages and used by the frontend redirect)
DESIGNER_PLACE = {
    'employee': 'Settings → Form designer (employee fields)',
    'asset': 'Assets → Custom fields',
}
HIDDEN = ['none']   # rules.visible_on of a merged (alias) field: shown nowhere, values kept
ASSET_TYPES = ('text', 'textarea', 'integer', 'decimal', 'currency', 'percent', 'date', 'datetime', 'time', 'checkbox',
               'dropdown', 'multiselect', 'radio', 'email', 'phone', 'url', 'rating', 'color')


def owner_of(model_key):
    o = OWNERS.get(model_key)
    if not o:
        return None
    return dict(o, place=DESIGNER_PLACE[o['store']], model=model_key)


def owner_message(model_key):
    o = owner_of(model_key)
    from Translations.i18n import tr
    return tr('{0} fields are designed in {1}, so each field is defined and saved only once. Open that designer to add or change fields.',
              o['title'], tr(o['place']))


# ---------------------------------------------------------------- label matching
_SYN = {'number': 'no', 'num': 'no', 'nbr': 'no', 'nos': 'no', 'dob': 'birth', 'e': '', 'mail': 'email', 'mobile': 'phone',
        'telephone': 'phone', 'tel': 'phone', 'expiry': 'expiry', 'expires': 'expiry', 'expiration': 'expiry', 'exp': 'expiry',
        'eid': 'emiratesid', 'licence': 'license', 'organisation': 'organization', 'colour': 'color'}
_STOP = {'the', 'of', 'a', 'an', 'emp', 'date', 'dt', 'field', 'details', 'detail', 'in', 'for'}


def norm(label):
    """Words of a label that decide whether two fields are the same ("Labour Card Expiry" = "Labour card expiry date")."""
    s = str(label or '').lower().replace('’', '').replace("'", '').replace('e-mail', 'email').replace('emirates id', 'emiratesid')
    words = [_SYN.get(w, w) for w in re.findall(r'[a-z0-9]+|[؀-ۿ]+', s)]
    return ' '.join(w for w in words if w and w not in _STOP)


# ---------------------------------------------------------------- built-in fields of each form
def _profile_groups():
    try:
        from EmployeeProfile.services import profile_groups
        return profile_groups()
    except Exception:
        return []


def builtin_fields(form):
    """[{label, model, field, key, group}] – model + field = where the built-in value lives;
    key = how the row is found from the form's record id ('pk' or the side table's id column)."""
    out = []

    def add(label, model, field, key='pk', group=''):
        if label and field:
            out.append({'label': str(label), 'model': model, 'field': field, 'key': key, 'group': group})

    groups = {g['key']: g for g in _profile_groups()}
    if form == 'employee':
        for gk in ('personal', 'contact', 'address'):
            for f in groups.get(gk, {}).get('fields', []):
                add(f['label'], 'EmpManagement.emp_master', f['key'], 'pk', groups[gk]['label'])
        for f in groups.get('identity', {}).get('fields', []):
            add(f['label'], 'EmployeeProfile.EmployeeIdentity', f['key'], 'employee_id', groups['identity']['label'])
        from .forms import BUILTIN_MANDATORY
        from .models import FormSetting
        row = FormSetting.objects.filter(form='employee').first()
        names = (row.data if row else {}) or {}
        for flag, (col, label_key, label) in BUILTIN_MANDATORY.items():
            add(label, 'EmpManagement.emp_master', col, 'pk', 'Employee')
            if names.get(label_key) and str(names[label_key]).strip() and norm(names[label_key]) != norm(label):
                add(names[label_key], 'EmpManagement.emp_master', col, 'pk', 'Employee')   # the company's own caption
        for label, col in (('Employee code', 'emp_code'), ('Employee ID', 'emp_code'), ('Date of joining', 'emp_joined_date'),
                           ('Joining date', 'emp_joined_date'), ('Date of birth', 'emp_date_of_birth'), ('Gender', 'emp_gender')):
            add(label, 'EmpManagement.emp_master', col, 'pk', 'Employee')
        for label, col in (('Employment status', 'status'), ('Probation end date', 'probation_end_date'),
                           ('Probation status', 'probation_status'), ('Date of leaving', 'date_of_leaving'),
                           ('Leaving reason', 'leaving_reason'), ('Confirmation date', 'confirmed_on')):
            add(label, 'EmployeeProfile.EmploymentInfo', col, 'employee_id', 'Employment')
    elif form in ('family', 'qualification', 'job_history', 'documents'):
        gk = {'family': 'family', 'qualification': 'qualifications', 'job_history': 'experience', 'documents': 'documents'}[form]
        g = groups.get(gk) or {}
        core = g.get('model', '')
        extra = g.get('extra_model', '')
        core_names = set()
        try:
            core_names = {f.name for f in apps.get_model(core)._meta.concrete_fields}
        except Exception:
            pass
        ekey = {'family': 'family_id', 'qualification': 'qualification_id'}.get(form, '')
        for f in g.get('fields', []):
            if f['key'] in core_names:
                add(f['label'], core, f['key'], 'pk', g.get('label', ''))
            elif extra and ekey:
                add(f['label'], extra, f['key'], ekey, g.get('label', ''))
    elif form == 'asset':   # (asset fields below)
        for label, col in (('Asset name', 'name'), ('Name', 'name'), ('Serial number', 'serial_number'), ('Model', 'model'),
                           ('Purchase date', 'purchase_date'), ('Status', 'status'), ('Condition', 'condition'), ('Asset type', 'asset_type')):
            add(label, 'OrganisationManager.Asset', col, 'pk', 'Asset')
        try:
            from AssetPlus.views import PROFILE_LABEL
            for col, label in PROFILE_LABEL.items():
                add(label, 'AssetPlus.AssetProfile', col, 'asset_id', 'Asset profile')
            for label, col in (('Warranty expiry', 'warranty_end'), ('Warranty end date', 'warranty_end'), ('Insurance policy no', 'insurance_policy_no'),
                               ('Supplier', 'vendor'), ('Invoice number', 'invoice_no'), ('PO number', 'purchase_order')):
                add(label, 'AssetPlus.AssetProfile', col, 'asset_id', 'Asset profile')
        except Exception:
            pass
    main = {}
    for b in out:   # the first caption of a built-in field is its name in messages and reports
        b['main'] = main.setdefault((b['model'], b['field']), b['label'])
    return out


def builtin_match(form, label):
    """The built-in field a custom field name duplicates, or None."""
    n = norm(label)
    if not n:
        return None
    for b in builtin_fields(form):
        if norm(b['label']) == n:
            return b
    return None


def builtin_message(form, label, b):
    from Translations.i18n import tr
    where = f"{b['group']} → {b.get('main') or b['label']}" if b.get('group') else (b.get('main') or b['label'])
    return tr('“{0}” is already a field of this form ({1}). Use that field instead of adding a custom one.', label, where)


def chatter_duplicate(model_key, label):
    """An active generic extra field (record panel designer) with this name on the model (before the migration)."""
    try:
        from Chatter.models import FieldDef
        n = norm(label)
        for d in FieldDef.objects.filter(model=model_key, active=True):
            if norm(d.label) == n:
                return d
    except Exception:
        pass
    return None


def check_new_custom_field(form, label, exclude_name=None):
    """Error message when a custom field name would define a field twice on this form, else None."""
    if exclude_name is not None and norm(exclude_name) == norm(label):
        return None   # unchanged name (old fields that pre-date the check keep working)
    b = builtin_match(form, label)
    if b:
        return builtin_message(form, label, b)
    mk = next((k for k, o in OWNERS.items() if o['form'] == form), None)
    if mk and chatter_duplicate(mk, label):
        from Translations.i18n import tr
        return tr('“{0}” already exists as an extra field of this form. Run the form designer migration (unify_form_designers) to move it here.', label)
    return None


def model_field_duplicate(model, label):
    """Generic screens (Chatter designer): a built-in column of the screen's model with the same caption."""
    n = norm(label)
    if not n or model is None:
        return None
    for f in model._meta.concrete_fields:
        if f.primary_key or f.name in ('created_at', 'updated_at', 'created_by', 'updated_by'):
            continue
        cap = str(getattr(f, 'verbose_name', '') or f.name)
        if norm(cap) == n or norm(f.name.replace('_', ' ')) == n:
            return cap
    return None


# ---------------------------------------------------------------- asset field layout (FormSetting, like the employee forms)
def asset_meta():
    from .forms import field_meta
    return field_meta('asset')


def asset_field_json(f, meta=None):
    meta = asset_meta() if meta is None else meta
    m = meta.get(f.custom_field, {})
    t = f.data_type or 'text'
    opts = (f.dropdown_values or f.radio_values or []) if t in ('dropdown', 'radio', 'multiselect') else []
    rules = m.get('rules') or {}
    return {'id': f.id, 'name': f.custom_field, 'type': t, 'options': opts, 'asset_type': f.asset_type_id,
            'required': bool(m.get('mandatory')), 'section': m.get('section', ''), 'order': m.get('order', f.id * 10),
            'help_text': m.get('help_text', ''), 'placeholder': m.get('placeholder', ''), 'example': rules.get('example', ''),
            'rules': rules, 'default': rules.get('default', ''), 'alias_of': m.get('alias_of', '')}


class _AssetTyped:
    """An asset custom field as Chatter.clean_value needs it."""
    def __init__(self, f, meta):
        self.label = f.custom_field
        t = f.data_type or 'text'
        if t == 'checkbox' and f.checkbox_values and [str(x).lower() for x in f.checkbox_values] not in (['yes', 'no'], ['no', 'yes']):
            t = 'multiselect'   # an old asset "checkbox" with its own options = tick several
        self.field_type = t
        self.options = [str(o) for o in (f.dropdown_values or f.radio_values or (f.checkbox_values if t == 'multiselect' else None) or [])]
        self.required = bool(meta.get('mandatory'))
        self.rules = meta.get('rules') or {}


def asset_typed(f, value, meta=None, required=None):
    """Normalised text to store for an asset custom field, or raises ValueError(message)."""
    from Chatter.views import clean_value
    m = (asset_meta() if meta is None else meta).get(f.custom_field, {})
    p = _AssetTyped(f, m)
    if required is not None:
        p.required = required
    if p.field_type in ('dropdown', 'radio', 'multiselect') and not p.options:
        p.field_type = 'text'
    val, err = clean_value(p, value)
    if err:
        raise ValueError(err)
    if val is None:
        return None
    if isinstance(val, list):
        return ','.join(val) if (f.data_type or '') == 'checkbox' else ', '.join(val)
    if isinstance(val, bool):
        return 'Yes' if val else 'No'
    if p.field_type == 'currency':
        return f'{float(val):.2f}'
    if p.field_type in ('decimal', 'percent'):
        return ('%f' % float(val)).rstrip('0').rstrip('.')
    return str(val)


# ---------------------------------------------------------------- the migration
def _str_value(v):
    if isinstance(v, dict):
        return str(v.get('name') or v.get('code') or v.get('id') or '')
    if isinstance(v, list):
        return ', '.join(str(x) for x in v)
    if isinstance(v, bool):
        return 'Yes' if v else 'No'
    return '' if v is None else str(v)


def _emp_type(t):
    return {'file': 'text', 'employee': 'text'}.get(t, t)


def _parse_any_date(v):
    s = str(v or '').strip()[:10]
    for fmt in ('%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y', '%Y/%m/%d'):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _to_builtin(field, value):
    """Custom value -> value for the built-in model field, or raises ValueError(reason)."""
    from django.db import models as dm
    s = _str_value(value).strip()
    if not s:
        return None
    if field.choices:
        for k, lab in field.choices:
            if s.lower() in (str(k).lower(), str(lab).lower()):
                return k
        near = [k for k, lab in field.choices if re.search(r'\b' + re.escape(s.lower()) + r'\b', str(lab).lower())]
        if len(near) == 1:   # "Employment" -> "Employment visa", "Family" -> "Residence (family sponsored)"
            return near[0]
        raise ValueError(f'“{s}” is not one of the choices ({", ".join(str(l) for _, l in field.choices)})')
    if isinstance(field, dm.DateField):
        d = _parse_any_date(s)
        if d is None:
            raise ValueError(f'“{s}” is not a date')
        return d
    if isinstance(field, dm.BooleanField):
        if s.lower() in ('yes', 'true', '1', 'y'):
            return True
        if s.lower() in ('no', 'false', '0', 'n'):
            return False
        raise ValueError(f'“{s}” is not Yes / No')
    if isinstance(field, (dm.IntegerField,)):
        return int(float(s.replace(',', '')))
    if isinstance(field, dm.DecimalField):
        from decimal import Decimal
        return Decimal(s.replace(',', ''))
    if isinstance(field, dm.FloatField):
        return float(s.replace(',', ''))
    if isinstance(field, dm.ForeignKey):
        raise ValueError('a link to another record cannot be copied automatically')
    if isinstance(field, (dm.CharField, dm.TextField)):
        ml = getattr(field, 'max_length', None)
        if ml and len(s) > ml:
            raise ValueError(f'longer than {ml} characters')
        return s
    raise ValueError('this kind of field cannot be copied automatically')


def _same(a, b):
    if a in (None, '') and b in (None, ''):
        return True
    return str(a).strip().lower() == str(b).strip().lower()


def _copy_to_builtin(b, parent_id, value, apply, report, src):
    """Copy one value into the built-in field when that is empty (or equal). Returns 'copied' | 'equal' | 'conflict'."""
    M = apps.get_model(b['model'])
    field = M._meta.get_field(b['field'])
    try:
        new = _to_builtin(field, value)
    except (ValueError, ArithmeticError) as e:
        report['conflicts'].append({**src, 'record': parent_id, 'value': _str_value(value), 'reason': str(e)})
        return 'conflict'
    if new is None:
        return 'equal'
    if b['key'] == 'pk':
        row = M._default_manager.filter(pk=parent_id).first()
        if row is None:
            return 'equal'
    else:
        row = M._default_manager.filter(**{b['key']: parent_id}).first()
    cur = getattr(row, field.attname, None) if row is not None else None
    if cur not in (None, '') and not _same(cur, new):
        report['conflicts'].append({**src, 'record': parent_id, 'value': _str_value(value), 'builtin_value': str(cur),
                                    'reason': 'the built-in field already has a different value (kept)'})
        return 'conflict'
    if cur not in (None, '') and _same(cur, new):
        return 'equal'
    if apply:
        if row is None and b['model'] == 'AssetPlus.AssetProfile':
            # an asset without its AssetPlus profile yet: create it the normal way (asset code, branch …)
            from AssetPlus.services import ensure_profile
            a = apps.get_model('OrganisationManager.Asset')._default_manager.filter(pk=parent_id).first()
            if a is None:
                return 'equal'
            row = ensure_profile(a)
        if row is not None:
            M._default_manager.filter(pk=row.pk).update(**{field.attname: new})
        else:
            extra = {}
            names = {f.name for f in M._meta.concrete_fields}
            if 'employee_id' in names and b['key'] != 'employee_id':
                core = OWNER_PARENT.get(b['model'])
                if core:
                    cm, attr = core
                    p = apps.get_model(cm)._default_manager.filter(pk=parent_id).values_list(attr, flat=True).first()
                    extra['employee_id'] = p
            if 'employee_id' in names and extra.get('employee_id') is None and b['key'] != 'employee_id':
                report['conflicts'].append({**src, 'record': parent_id, 'value': _str_value(value), 'reason': 'record not found'})
                return 'conflict'
            M._default_manager.create(**{b['key']: parent_id, field.attname: new, **extra})
    return 'copied'


# side table -> (core model, attribute holding the employee id) – to create a missing side row
OWNER_PARENT = {
    'EmployeeProfile.DependentExtra': ('EmpManagement.emp_family', 'emp_id_id'),
    'EmployeeProfile.QualificationExtra': ('EmpManagement.EmpQualification', 'emp_id_id'),
}


def _emp_pairs():
    from .forms import PAIRS
    out = {}
    for d, v, key, fk in PAIRS:
        out[key] = (apps.get_model(d), apps.get_model(v), fk)
    return out


def _backup(apply, data):
    """Everything the migration changes, kept in FormSetting 'backup:unify:<timestamp>' (to undo by hand)."""
    if not apply:
        return ''
    from .models import FormSetting
    key = 'backup:unify:' + datetime.datetime.now().strftime('%Y%m%d%H%M%S%f')
    FormSetting.objects.create(form=key[:60], data=json.loads(json.dumps(data, default=str)))
    return key


def unify(apply=False, purge_merged=False, log=None):
    """Move every duplicate field definition + its values into the one designer of each form (current schema).
    Dry run unless apply. Returns a report dict."""
    from Chatter.models import FieldDef, FieldValue
    from .forms import set_layout, set_mandatory, field_meta, typed_value, _layout_map
    from .models import FormSetting
    report = {'moved': [], 'merged': [], 'aliased': [], 'conflicts': [], 'values_moved': 0, 'values_copied_to_builtin': 0,
              'apply': bool(apply), 'backup': '', 'purged': []}
    backup = {'fielddefs': [], 'layouts': {}}
    pairs = _emp_pairs()
    with transaction.atomic():
        _unify(report, backup, pairs, apply, purge_merged)
    return report


def _unify(report, backup, pairs, apply, purge_merged):
    from Chatter.models import FieldDef, FieldValue
    from .forms import set_layout, set_mandatory, typed_value, _layout_map
    if True:
        # ---- 1. generic extra fields (Chatter) on forms owned by another designer -> that designer
        for d in FieldDef.objects.filter(model__in=list(OWNERS), active=True).order_by('id'):
            o = OWNERS[d.model]
            values = list(FieldValue.objects.filter(field=d))
            backup['fielddefs'].append({'id': d.id, 'model': d.model, 'label': d.label, 'type': d.field_type, 'rules': d.rules,
                                        'values': {v.object_id: v.value for v in values}})
            entry = {'from': f'Chatter extra field #{d.id} “{d.label}” ({d.model})', 'label': d.label, 'values': len(values)}
            if o['store'] == 'employee':
                D, V, fk = pairs[o['form']]
                target = D.objects.filter(emp_custom_field__iexact=d.label.strip()).first()
                t = _emp_type(d.field_type)
                if target is None:
                    entry['to'] = f'{o["title"]} custom field “{d.label}” (new, {t})'
                    report['moved'].append(entry)
                    if apply:
                        kw = {'emp_custom_field': d.label.strip()[:100], 'data_type': t}
                        if t in ('dropdown', 'multiselect'):
                            kw['dropdown_values'] = list(d.options or [])
                        elif t == 'radio':
                            kw['radio_values'] = list(d.options or [])
                        elif t == 'checkbox':
                            kw['checkbox_values'] = ['Yes', 'No']
                        target = D.objects.create(**kw)
                        rules = dict(d.rules or {})
                        rules.pop('moved_to', None)
                        if d.default not in (None, ''):
                            rules['default'] = d.default
                        set_layout(o['form'], target.emp_custom_field, {'order': d.order, 'section': d.section, 'help_text': d.help_text, 'rules': rules}, data_type=t)
                        set_mandatory(o['form'], target.emp_custom_field, d.required)
                else:
                    entry['to'] = f'{o["title"]} custom field “{target.emp_custom_field}” (already there – merged)'
                    report['merged'].append(entry)
                name = target.emp_custom_field if target is not None else d.label.strip()
                existing = {}
                if target is not None:
                    existing = {str(pid): val for pid, val in V.objects.filter(emp_custom_field=name).values_list(fk + '_id', 'field_value')}
                new_rows = []
                for v in values:
                    raw = v.value
                    try:
                        sval = typed_value(target, raw if not isinstance(raw, dict) else _str_value(raw)) if target is not None else _str_value(raw)
                    except Exception:
                        sval = _str_value(raw)
                    if sval in (None, ''):
                        continue
                    cur = existing.get(str(v.object_id))
                    if cur not in (None, ''):
                        if not _same(cur, sval):
                            report['conflicts'].append({'field': name, 'form': o['form'], 'record': v.object_id, 'value': sval, 'kept': cur,
                                                        'reason': 'both designers had a value; the employee field value is kept'})
                        continue
                    report['values_moved'] += 1
                    if apply:
                        new_rows.append(V(**{fk + '_id': int(v.object_id), 'emp_custom_field': name, 'field_value': sval}))
                if apply and new_rows:
                    V.objects.bulk_create(new_rows)
            else:   # asset
                from OrganisationManager.models import AssetCustomField, AssetCustomFieldValue
                target = AssetCustomField.objects.filter(custom_field__iexact=d.label.strip()).first()
                t = _emp_type(d.field_type)
                if target is None:
                    entry['to'] = f'Asset custom field “{d.label}” (new, all asset types, {t})'
                    report['moved'].append(entry)
                    if apply:
                        kw = {'custom_field': d.label.strip()[:100], 'data_type': t, 'asset_type': None}
                        if t in ('dropdown', 'multiselect'):
                            kw['dropdown_values'] = list(d.options or [])
                        elif t == 'radio':
                            kw['radio_values'] = list(d.options or [])
                        elif t == 'checkbox':
                            kw['checkbox_values'] = ['Yes', 'No']
                        target = AssetCustomField.objects.create(**kw)
                        rules = dict(d.rules or {})
                        rules.pop('moved_to', None)
                        if d.default not in (None, ''):
                            rules['default'] = d.default
                        set_layout('asset', target.custom_field, {'order': d.order, 'section': d.section, 'help_text': d.help_text, 'rules': rules}, data_type=t)
                        set_mandatory('asset', target.custom_field, d.required)
                else:
                    entry['to'] = f'Asset custom field “{target.custom_field}” (already there – merged)'
                    report['merged'].append(entry)
                existing = {}
                if target is not None:
                    existing = {str(a): val for a, val in AssetCustomFieldValue.objects.filter(custom_field=target).values_list('asset_id', 'field_value')}
                new_rows = []
                for v in values:
                    sval = _str_value(v.value)
                    if target is not None and sval:
                        try:
                            sval = asset_typed(target, v.value if not isinstance(v.value, dict) else sval, required=False) or ''
                        except ValueError:
                            pass
                    if not sval:
                        continue
                    cur = existing.get(str(v.object_id))
                    if cur not in (None, ''):
                        if not _same(cur, sval):
                            report['conflicts'].append({'field': d.label, 'form': 'asset', 'record': v.object_id, 'value': sval, 'kept': cur,
                                                        'reason': 'both designers had a value; the asset field value is kept'})
                        continue
                    report['values_moved'] += 1
                    if apply:
                        new_rows.append(AssetCustomFieldValue(asset_id=int(v.object_id), custom_field=target, field_value=sval))
                if apply and new_rows:
                    AssetCustomFieldValue.objects.bulk_create(new_rows)
            if apply:   # the generic field is switched off (its values stay in the table, read-only, for the backup)
                d.active = False
                d.rules = {**(d.rules or {}), 'moved_to': f"{o['store']}:{o['form']}:{entry['label']}"}
                d.save(update_fields=['active', 'rules'])

        # ---- 2. custom fields that duplicate a built-in field -> merged into the built-in field (alias, hidden)
        forms = [(k, D, V, fk) for k, (D, V, fk) in pairs.items()]
        for key, D, V, fk in forms:
            lay = _layout_map(key)
            backup['layouts'][key] = lay
            for c in D.objects.all().order_by('id'):
                b = builtin_match(key, c.emp_custom_field)
                if not b:
                    continue
                if (lay.get(c.emp_custom_field) or {}).get('alias_of'):
                    continue   # done before
                src = {'field': c.emp_custom_field, 'form': key, 'builtin': f"{b['model']}.{b['field']}"}
                n_copied = n_conf = 0
                for pid, val in V.objects.filter(emp_custom_field=c.emp_custom_field).values_list(fk + '_id', 'field_value'):
                    r = _copy_to_builtin(b, pid, val, apply, report, src)
                    n_copied += r == 'copied'
                    n_conf += r == 'conflict'
                report['values_copied_to_builtin'] += n_copied
                report['aliased'].append({**src, 'builtin_label': b.get('main') or b['label'], 'group': b['group'], 'copied': n_copied, 'conflicts': n_conf})
                if apply:
                    _mark_alias(key, c.emp_custom_field, b)
        from OrganisationManager.models import AssetCustomField, AssetCustomFieldValue
        lay = _layout_map('asset')
        backup['layouts']['asset'] = lay
        for c in AssetCustomField.objects.all().order_by('id'):
            b = builtin_match('asset', c.custom_field)
            if not b or (lay.get(c.custom_field) or {}).get('alias_of'):
                continue
            src = {'field': c.custom_field, 'form': 'asset', 'builtin': f"{b['model']}.{b['field']}"}
            n_copied = n_conf = 0
            for aid, val in AssetCustomFieldValue.objects.filter(custom_field=c).values_list('asset_id', 'field_value'):
                if aid is None:
                    continue
                r = _copy_to_builtin(b, aid, val, apply, report, src)
                n_copied += r == 'copied'
                n_conf += r == 'conflict'
            report['values_copied_to_builtin'] += n_copied
            report['aliased'].append({**src, 'builtin_label': b.get('main') or b['label'], 'group': b['group'], 'copied': n_copied, 'conflicts': n_conf})
            if apply:
                _mark_alias('asset', c.custom_field, b)

        # ---- 3. optional: remove merged fields whose every value is now in the built-in field
        if purge_merged:
            for a in report['aliased']:
                if a['conflicts']:
                    continue
                report['purged'].append(a['field'])
                if apply:
                    if a['form'] == 'asset':
                        AssetCustomField.objects.filter(custom_field=a['field']).delete()
                    else:
                        pairs[a['form']][0].objects.filter(emp_custom_field=a['field']).first().delete()
        report['backup'] = _backup(apply, backup)


def _mark_alias(form, name, b):
    """A custom field merged into a built-in field: hidden everywhere (values kept), writes go to the built-in field."""
    from .models import FormSetting
    row, _ = FormSetting.objects.get_or_create(form=f'layout:{form}')
    data = dict(row.data or {})
    cur = dict(data.get(name) or {})
    rules = dict(cur.get('rules') or {})
    rules['visible_on'] = list(HIDDEN)
    cur['rules'] = rules
    cur['alias_of'] = f"{b['model']}.{b['field']}"
    cur['alias_label'] = b.get('main') or b['label']
    cur['help_text'] = f"Merged into the built-in field “{cur['alias_label']}” ({b['group']}) in v1.16.0 – fill that field instead."
    data[name] = cur
    row.data = data
    row.save()


def alias_of(form, name):
    """(built-in model.field, label) when the custom field was merged into a built-in field, else None."""
    from .forms import _layout_map
    m = _layout_map(form).get(name) or {}
    return (m['alias_of'], m.get('alias_label', '')) if m.get('alias_of') else None


def format_report(r, schema=''):
    lines = [f'== {schema or "company"}: {"APPLIED" if r["apply"] else "DRY RUN (nothing changed – add --apply)"}']
    lines.append(f'Extra fields moved to the owning designer: {len(r["moved"])}; merged into an existing field: {len(r["merged"])}; values moved: {r["values_moved"]}')
    for e in r['moved'] + r['merged']:
        lines.append(f'  - {e["from"]} -> {e["to"]} ({e["values"]} value(s))')
    lines.append(f'Custom fields that duplicate a built-in field (merged, hidden, values copied where the built-in field was empty): {len(r["aliased"])}; values copied: {r["values_copied_to_builtin"]}')
    for a in r['aliased']:
        lines.append(f'  - {a["form"]}: “{a["field"]}” = built-in “{a["builtin_label"]}” ({a["group"]}): {a["copied"]} copied, {a["conflicts"]} conflict(s)')
    lines.append(f'Conflicts (nothing overwritten – please check): {len(r["conflicts"])}')
    for c in r['conflicts'][:200]:
        lines.append('  - ' + json.dumps(c, ensure_ascii=False, default=str))
    if r['purged']:
        lines.append('Merged fields removed (--purge-merged): ' + ', '.join(r['purged']))
    if r['backup']:
        lines.append(f'Backup of the old definitions and values: DataTools FormSetting “{r["backup"]}”')
    return '\n'.join(lines)
