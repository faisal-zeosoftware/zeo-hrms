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
    if not brs:
        return []
    added = []
    for label, wf, lv in SIMPLE:
        W, L = _m(wf), _m(lv)
        if W is None or L is None:
            continue
        covered = set(W.objects.values_list('branch', flat=True))
        need = [b for b in brs if b.pk not in covered]
        if not need:
            continue
        w = W.objects.filter(approval_type='reporting_manager').order_by('id').first()
        if w is None:
            w = W.objects.create(approval_type='reporting_manager')
            fk = next(f.name for f in L._meta.concrete_fields if f.is_relation and f.related_model is W)
            kw = {fk: w, 'level': 1}
            if any(f.name == 'role' for f in L._meta.concrete_fields):
                kw['role'] = ROLE
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
            w = LW.objects.filter(request_type=t, approval_type='reporting_manager').order_by('id').first() or \
                LW.objects.create(request_type=t, approval_type='reporting_manager', created_by=user)
            w.branch.add(*need)
            n += 1
        if n:
            added.append(f'Leave: {n} leave types')
    # loan types: one reporting-manager workflow each
    LoT, LoW, LoL = _m('PayrollManagement.LoanType'), _m('PayrollManagement.LoanApprovalWorkflow'), _m('PayrollManagement.LoanApprovalLevels')
    if LoT is not None and LoW is not None:
        n = 0
        for t in LoT.objects.all():
            if t.use_common_workflow if hasattr(t, 'use_common_workflow') else False:
                continue
            w = LoW.objects.filter(loan_type=t).first()
            if w is None:
                w = LoW.objects.create(loan_type=t, approval_type='reporting_manager')
                LoL.objects.create(workflow=w, level=1, role=ROLE)
                n += 1
            missing_b = [b for b in brs if not w.branch.filter(pk=b.pk).exists()]
            if missing_b:
                w.branch.add(*missing_b)
        if n:
            added.append(f'Loans: {n} loan types')
    return added
