"""v1.15.0 – loads the Arabic names of the standard UAE lists (standard_ar.py) into FieldTranslation.

Idempotent: a record that already has a translation keeps it (a user's own wording is never overwritten) unless force=True,
and then only translations that were loaded from the standard lists (is_standard) are replaced."""
from . import registry as R
from .models import FieldTranslation
from .standard_ar import LISTS

PACKS = {'ar': LISTS}


def load(lang='ar', force=False, only=None):
    """{model label: {'matched', 'created', 'updated', 'kept'}} for the current company schema."""
    out = {}
    for label, field, names, match in PACKS.get(lang, []):
        if only and label not in only:
            continue
        model = R.model_for(label)
        if model is None:
            continue
        lookup = {k.strip().lower(): v for k, v in names.items()}
        st = {'matched': 0, 'created': 0, 'updated': 0, 'kept': 0}
        existing = {(r.record_id, r.field): r for r in FieldTranslation.objects.filter(model=label, lang=lang, field=field)}
        for pk, name, key in model.objects.values_list('pk', field, match if match != 'name' else field):
            ar = lookup.get(str(key or '').strip().lower()) if match == 'name' else names.get(str(key or '').strip().upper())
            if not ar:
                continue
            st['matched'] += 1
            row = existing.get((pk, field))
            if row is None:
                FieldTranslation.objects.create(model=label, record_id=pk, field=field, lang=lang, value=ar, source=name or '', is_standard=True)
                st['created'] += 1
            elif force and row.is_standard and (row.value != ar or row.source != (name or '')):
                row.value, row.source = ar, name or ''
                row.save(update_fields=['value', 'source', 'updated_at'])
                st['updated'] += 1
            else:
                st['kept'] += 1
        out[label] = st
    return out
