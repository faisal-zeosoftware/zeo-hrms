# v1.67.8 (fixes)
"""v1.67.8 (fixes) – item 6: the automatic end date of the document numbering rows of a branch.

Until 1.67.7 the branch signal (`OrganisationManager.signals`, every new branch) and the repair command
`fix_document_prefixes` gave each numbering row of the branch start date = the day it was made and end date = that day + 365
days. After that year the branch could not number anything any more ("Document number cannot be generated outside the
valid date range"). New rows get no end date now; `auto_dated()` finds the rows whose end date was set that way and never
changed by a user, so it can be cleared:

  1. end date and start date are both filled;
  2. the start date is the day the row was made (created_at, company time) – the signal set both at the same moment;
  3. the end date is exactly the start date + 365 days;
  4. the row's change history (Chatter RecordLog, kept for every change made on the screens / API) has no change of the
     start or end date.
A row that fails any of these (a date typed by a user, a date range edited later, an end date of another length) is kept.
"""
from datetime import timedelta

from django.apps import apps
from django.utils import timezone

DN_LABEL = 'OrganisationManager.documentnumbering'


def _edited_ids():
    """ids of numbering rows whose start / end date was changed through the history-keeping save paths."""
    try:
        RL = apps.get_model('Chatter', 'RecordLog')
    except LookupError:
        return set()
    out = set()
    for oid, ch in RL.objects.filter(model__iexact=DN_LABEL, action='updated').values_list('object_id', 'changes'):
        if isinstance(ch, dict) and ('end_date' in ch or 'start_date' in ch):
            out.add(str(oid))
    return out


def auto_dated():
    DN = apps.get_model('OrganisationManager', 'DocumentNumbering')
    edited = _edited_ids()
    rows = []
    for r in DN.objects.filter(end_date__isnull=False, start_date__isnull=False).select_related('branch_id').order_by('branch_id_id', 'type'):
        made = timezone.localtime(r.created_at).date() if r.created_at else None
        if made is None or r.start_date != made or r.end_date != r.start_date + timedelta(days=365) or str(r.pk) in edited:
            continue
        rows.append(r)
    return rows


def clear(apply=False):
    """The auto-set rows (dicts for the report); with apply=True their end date is emptied (nothing else changes)."""
    DN = apps.get_model('OrganisationManager', 'DocumentNumbering')
    rows = auto_dated()
    out = [{'id': r.pk, 'branch': getattr(r.branch_id, 'branch_name', ''), 'type': r.type, 'start_date': r.start_date, 'end_date': r.end_date}
           for r in rows]
    if apply and rows:
        DN.objects.filter(pk__in=[r.pk for r in rows]).update(end_date=None)   # update(): no number, prefix or counter touched
    return out
