"""v1.56.1 – the standard approval chains: the reporting manager approves.

A request type without an approval workflow for the employee's branch was refused ("Approval workflow is not configured for
this branch") – in a new company every late-in / early-out, advance salary, resignation and air-ticket request failed until
someone set the chains up by hand. ensure() adds, for every branch that has none, a workflow "Reporting manager" with one
level (the employee's reporting manager approves), and the same for every leave type and loan type. Nothing that exists is
changed. Run by setup_zeo and the company creation, for a new branch, by the setup guide (step 10) and by
`python manage.py ensure_approval_chains`.
"""
import logging

from django.apps import apps
from django.db import transaction

log = logging.getLogger(__name__)
ROLE = 'Reporting Manager'
# (label, workflow model, level model, related name of the levels, level fields)
SIMPLE = [
    ('Late in / early out', 'calendars.LatinEarlyApprovalWorkflow', 'calendars.LateinEarlyoutApprovalLevel'),
    ('Advance salary', 'PayrollManagement.AdvanceApprovalWorkflow', 'PayrollManagement.AdvanceCommonWorkflow'),
    ('Resignation', 'EmpManagement.ResignationApprovalWorkflow', 'EmpManagement.ResignationApprovalLevel'),
    ('Air ticket', 'PayrollManagement.AirticketApprovalWorkflow', 'PayrollManagement.AirticketWorkflow'),
]
PAYROLL = ('Advance salary', 'Air ticket')   # v1.67.19: made only while Payroll is active


def _on(key):
    """v1.67.19: the chains belong to HR (advance salary and air ticket to Payroll) – none while the app is off."""
    try:
        from ZeoApps.services import is_active
        return is_active(key)
    except Exception:   # noqa: BLE001
        return True


def _m(label):
    try:
        return apps.get_model(*label.split('.'))
    except LookupError:
        return None


def missing(branches=None):
    """[(label, branch names)] of the request types some branch has no workflow for."""
    B = apps.get_model('OrganisationManager', 'brnch_mstr')
    brs = list(branches) if branches is not None else list(B.objects.all())
    out = []
    for label, wf, lv in SIMPLE:
        W = _m(wf)
        if W is None:
            continue
        covered = set(W.objects.values_list('branch', flat=True))
        miss = [b.branch_name for b in brs if b.pk not in covered]
        if miss:
            out.append((label, miss))
    LT, LW = _m('calendars.leave_type'), _m('calendars.LVApprovalWorkflow')
    if LT is not None:
        have = set(LW.objects.values_list('request_type_id', 'branch'))
        miss = set()
        for t in LT.objects.all():
            for b in brs:
                if (t.branch_id in (None, b.pk)) and (t.pk, b.pk) not in have:
                    miss.add(b.branch_name)
        if miss:
            out.append(('Leave', sorted(miss)))
    return out


@transaction.atomic
def ensure(branches=None, user=None):
    """Adds the reporting-manager chains that are missing. Returns a list of what was added (labels)."""
    B = apps.get_model('OrganisationManager', 'brnch_mstr')
    brs = list(branches) if branches is not None else list(B.objects.all())
    if not brs or not _on('hrms'):
        return []
    pay = _on('payroll')
    added = []
    try:   # v1.75.1 a company with the approval hierarchy off (every new company): the chains are "No approval"
        from ZeoApps.hrfeatures import on as _feat
        kind = 'reporting_manager' if _feat('approvals') else 'no_approval'
    except Exception:   # noqa: BLE001
        kind = 'reporting_manager'
    role = ROLE if kind == 'reporting_manager' else 'No approval'
    for label, wf, lv in SIMPLE:
        if label in PAYROLL and not pay:
            continue
        W, L = _m(wf), _m(lv)
        if W is None or L is None:
            continue
        covered = set(W.objects.values_list('branch', flat=True))
        need = [b for b in brs if b.pk not in covered]
        if not need:
            continue
        w = W.objects.filter(approval_type=kind).order_by('id').first()
        if w is None:
            w = W.objects.create(approval_type=kind)
            fk = next(f.name for f in L._meta.concrete_fields if f.is_relation and f.related_model is W)
            kw = {fk: w, 'level': 1}
            if any(f.name == 'role' for f in L._meta.concrete_fields):
                kw['role'] = role
            L.objects.create(**kw)
        w.branch.add(*need)
        added.append(f'{label}: {", ".join(b.branch_name for b in need)}')
    # leave types: one reporting-manager workflow per type and branch that has none
    LT, LW = _m('calendars.leave_type'), _m('calendars.LVApprovalWorkflow')
    if LT is not None and LW is not None:
        have = set(LW.objects.values_list('request_type_id', 'branch'))
        n = 0
        for t in LT.objects.all():
            need = [b for b in brs if t.branch_id in (None, b.pk) and (t.pk, b.pk) not in have]
            if not need:
                continue
            w = LW.objects.filter(request_type=t, approval_type=kind).order_by('id').first() or \
                LW.objects.create(request_type=t, approval_type=kind, created_by=user)
            w.branch.add(*need)
            n += 1
        if n:
            added.append(f'Leave: {n} leave types')
    # loan types: one reporting-manager workflow each
    LoT, LoW, LoL = _m('PayrollManagement.LoanType'), _m('PayrollManagement.LoanApprovalWorkflow'), _m('PayrollManagement.LoanApprovalLevels')
    if LoT is not None and LoW is not None and pay:
        n = 0
        for t in LoT.objects.all():
            if t.use_common_workflow if hasattr(t, 'use_common_workflow') else False:
                continue
            w = LoW.objects.filter(loan_type=t).first()
            if w is None:
                w = LoW.objects.create(loan_type=t, approval_type=kind)
                LoL.objects.create(workflow=w, level=1, role=role)
                n += 1
            missing_b = [b for b in brs if not w.branch.filter(pk=b.pk).exists()]
            if missing_b:
                w.branch.add(*missing_b)
        if n:
            added.append(f'Loans: {n} loan types')
    # v1.67.19 (walkthrough): general requests, document requests and asset requests – one reporting-manager workflow per type
    # and branch, like leave (the button said "every request type that has no chain yet" but left these out)
    for label, tmodel, wmodel, fk in TYPED:
        T, Wf = _m(tmodel), _m(wmodel)
        if T is None or Wf is None:
            continue
        have = set(Wf.objects.values_list(f'{fk}_id', 'branch'))
        # a workflow of the type without branches is used for every branch (the request falls back to it) – kept as it is
        every = {t for t, b in have if b is None}
        n = 0
        for t in T.objects.all():
            if getattr(t, 'use_common_workflow', False) or t.pk in every:
                continue
            bf = next((f for f in T._meta.get_fields() if f.name == 'branch'), None)
            if bf is not None and bf.many_to_many:
                only = set(t.branch.values_list('pk', flat=True))   # none ticked = every branch
            else:
                only = {t.branch_id} if getattr(t, 'branch_id', None) else set()
            need = [b for b in brs if (not only or b.pk in only) and (t.pk, b.pk) not in have]
            if not need:
                continue
            w = Wf.objects.filter(**{fk: t, 'approval_type': kind}).order_by('id').first()
            if w is None:
                kw = {fk: t, 'approval_type': kind}
                if any(f.name == 'created_by' for f in Wf._meta.concrete_fields) and user is not None and getattr(user, 'pk', None):
                    kw['created_by'] = user
                w = Wf.objects.create(**kw)
            w.branch.add(*need)
            n += 1
        if n:
            added.append(f'{label}: {n} types')
    return added


# (label, type model, workflow model, foreign key of the workflow to the type)
TYPED = [
    ('General requests', 'EmpManagement.RequestType', 'EmpManagement.ApprovalWorkflow', 'request_type'),
    ('Document requests', 'EmpManagement.DocRequestType', 'EmpManagement.DocumentApprovalWorkflow', 'request_type'),
    ('Asset requests', 'OrganisationManager.AssetType', 'OrganisationManager.AssetApprovalWorkflow', 'asset_type'),
]
