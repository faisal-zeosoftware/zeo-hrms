"""v1.22.0 – a branch added after the company was set up gets the set-up of the first branch.

Before, only departments, designations, categories and document numbering reached a new branch, so it had no weekend or
holiday calendar, no leave types, no salary components and no leave approval workflow – leave requests of its employees
failed with "Approval workflow not configured for this branch." until someone set it all up by hand.

copy_setup(new_branch) copies from the oldest other branch (or a given one):
- calendars assigned to that branch (weekend, holiday) – the new branch is added to the same assignment;
- company-wide leave types (no branch) – the new branch joins their approval workflows and entitlements;
- leave types of that branch – copied with their entitlements, reset policies, pay rules and approval workflows;
- salary components of that branch – copied (same codes and formulas); salary / pay structures and document types – joined.
Nothing that already exists for the new branch is changed. Safe to run again.
"""
import logging

from django.apps import apps
from django.db import transaction

log = logging.getLogger(__name__)


def M(app, model):
    try:
        return apps.get_model(app, model)
    except LookupError:
        return None


def _clone(obj, **changes):
    """A copy of obj (concrete fields), with changes; many-to-many fields are copied as they are."""
    m2m = {f.name: list(getattr(obj, f.name).all()) for f in obj._meta.many_to_many}
    obj = type(obj).objects.get(pk=obj.pk)
    obj.pk = None
    obj.id = None
    for k, v in changes.items():
        setattr(obj, k, v)
    obj._state.adding = True
    obj.save()
    for k, vals in m2m.items():
        if k == 'branch' or k == 'branches':
            continue
        getattr(obj, k).set(vals)
    return obj


def _join(model, field, source, new, **flt):
    """Add the new branch wherever the source branch is in a many-to-many branch field."""
    if model is None:
        return 0
    n = 0
    for o in model.objects.filter(**{field: source}, **flt).distinct():
        rel = getattr(o, field)
        if not rel.filter(pk=new.pk).exists():
            rel.add(new)
            n += 1
    return n


def copy_setup(new, source=None, user=None):
    Branch = M('OrganisationManager', 'brnch_mstr')
    if source is None:
        source = Branch.objects.exclude(pk=new.pk).order_by('pk').first()
    if source is None:
        return {}
    done = {}
    with transaction.atomic():
        # calendars
        done['weekend'] = _join(M('calendars', 'assign_weekend'), 'branch', source, new, related_to='branch')
        done['holiday'] = _join(M('calendars', 'assign_holiday'), 'branch', source, new, related_to='branch')
        # company-wide leave types: workflows and entitlements that cover the source branch cover the new one too
        WF, Ent, LT = M('calendars', 'LVApprovalWorkflow'), M('calendars', 'leave_entitlement'), M('calendars', 'leave_type')
        done['workflows'] = _join(WF, 'branch', source, new, request_type__branch__isnull=True)
        done['entitlements'] = _join(Ent, 'branches', source, new, leave_type__branch__isnull=True)
        # leave types of the source branch
        Reset, Pay = M('calendars', 'LeaveResetPolicy'), M('calendars', 'LeavePayRule')
        made = 0
        for t in LT.objects.filter(branch=source):
            if LT.objects.filter(branch=new).filter(code=t.code).exists() or LT.objects.filter(branch=new, name=t.name).exists():
                continue
            nt = _clone(t, branch=new)
            made += 1
            for e in Ent.objects.filter(leave_type=t):
                ne = _clone(e, leave_type=nt)
                ne.branches.set([new])
                if Reset is not None:
                    for r in Reset.objects.filter(leave_entitlement=e):
                        _clone(r, leave_type=nt, leave_entitlement=ne)
            if Reset is not None:
                for r in Reset.objects.filter(leave_type=t, leave_entitlement__isnull=True):
                    _clone(r, leave_type=nt)
            if Pay is not None:
                for p in Pay.objects.filter(leave_type=t):
                    _clone(p, leave_type=nt)
            for w in WF.objects.filter(request_type=t):
                nw = _clone(w, request_type=nt)
                nw.branch.set([new])
            if not WF.objects.filter(request_type=nt).exists():   # every leave type needs a workflow
                nw = WF.objects.create(request_type=nt, approval_type='reporting_manager', created_by=user)
                nw.branch.set([new])
        done['leave_types'] = made
        # payroll
        SC = M('PayrollManagement', 'SalaryComponent')
        made = 0
        for c in SC.objects.filter(branch=source):
            if SC.objects.filter(branch=new, code=c.code).exists() or SC.objects.filter(branch=new, name=c.name).exists():
                continue
            _clone(c, branch=new)
            made += 1
        done['salary_components'] = made
        done['salary_structures'] = _join(M('PayrollManagement', 'SalaryStructure'), 'branch', source, new)
        done['pay_structures'] = _join(M('PayrollManagement', 'PayStructure'), 'branch', source, new)
        done['document_types'] = _join(M('EmpManagement', 'document_type'), 'branch', source, new)
    return done
