"""Shared helper for the approval-workflow serializers.

Creating a request type / asset type / loan type auto-creates a default "no approval" workflow, and the
Approval Level screen then POSTs a *new* workflow. The request signals pick `.first()`, i.e. the old
default, so the configured approval levels were silently ignored. Creating a workflow now replaces the
existing workflow(s) for the same request type and branch(es), so the latest configuration wins.
"""


def replace_existing_workflows(model, branches=None, **key):
    qs = model.objects.filter(**{k: v for k, v in key.items() if v is not None}) if any(v is not None for v in key.values()) else model.objects.all()
    if key and all(v is None for v in key.values()):
        qs = qs.filter(**{f'{k}__isnull': True for k in key})
    branch_ids = {getattr(b, 'pk', b) for b in (branches or [])}
    for wf in qs:
        try:
            wf_branches = set(wf.branch.values_list('pk', flat=True))
        except Exception:
            wf_branches = set()
        if not branch_ids or not wf_branches or wf_branches & branch_ids:
            wf.delete()
