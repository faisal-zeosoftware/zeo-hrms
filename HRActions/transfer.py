"""
Employee transfer (v1.11.0): move an employee to another branch / department / designation / category / manager on a date,
and carry what belongs to them. preview() lists every change without saving; apply() does it in one transaction.

What moves                                   How
-------------------------------------------  ------------------------------------------------------------------------------
Employee record                              branch, department, designation, category, reporting manager
Leave balances of branch-only leave types    balance moved to the same leave type (code / name) of the new branch, with ledger lines
Salary components of the old branch          switched to the component with the same code in the new branch (copied when missing)
Salary structures                            membership moved to the structure with the same name in the new branch
Optional new salary amounts                  applied with a salary revision line
Open requests (leave, loan, advance, …)      branch changed; pending leave approval steps re-sent to the new branch's approvers
User branch access                           old branch replaced by the new one (when the user had the old one)
Attendance geo-fences                        removed from fences of the old branch only
Documents, attendance, payslips, history     stay with the employee (they are linked to the employee, not the branch)
Weekend / holiday calendar                   follows the new branch automatically (unless set for the employee personally)
Leave policy                                 follows the new category (unless the employee has a policy of their own)
"""
import logging
from datetime import date
from decimal import Decimal

from django.apps import apps
from django.db import transaction
from django.utils import timezone

log = logging.getLogger(__name__)
M = apps.get_model

OPEN_REQUESTS = [('EmpManagement', 'GeneralRequest'), ('EmpManagement', 'DocumentRequest'), ('OrganisationManager', 'AssetRequest'),
                 ('calendars', 'employee_leave_request'), ('calendars', 'LateinEarlyoutRequest'), ('PayrollManagement', 'LoanApplication'),
                 ('PayrollManagement', 'AdvanceSalaryRequest'), ('PayrollManagement', 'AirTicketRequest')]


def _name(obj, *attrs):
    for a in attrs:
        v = getattr(obj, a, None)
        if v:
            return str(v)
    return str(obj) if obj is not None else '–'


def _obj(model, pk):
    return M(*model.split('.')).objects.filter(pk=pk).first() if pk else None


def _pending(qs):
    f = qs.model._meta.get_field('status') if any(x.name == 'status' for x in qs.model._meta.fields) else None
    if f is None:
        return qs.none()
    return qs.filter(status__iregex=r'^(pending|draft|submitted|in_progress|in progress)$')


REQ_NAMES = {'GeneralRequest': 'General requests', 'DocumentRequest': 'Document requests', 'AssetRequest': 'Asset requests',
             'employee_leave_request': 'Leave requests', 'LateinEarlyoutRequest': 'Late-in / early-out requests', 'LoanApplication': 'Loan applications',
             'AdvanceSalaryRequest': 'Salary advance requests', 'AirTicketRequest': 'Air ticket requests'}


def _shared_type(lt, branch_id):
    """The old branch's leave type is already used by employees of the new branch (the company shares it)."""
    return M('calendars', 'emp_leave_balance').objects.filter(leave_type=lt, employee__emp_branch_id=branch_id).exists()


def _shared_components(branch_id):
    """The new branch has no salary components of its own – the company shares one set."""
    return not M('PayrollManagement', 'SalaryComponent').objects.filter(branch_id=branch_id).exists()


def _match_type(lt, branch_id):
    LT = M('calendars', 'leave_type')
    qs = LT.objects.filter(branch_id=branch_id)
    return (qs.filter(code=lt.code).first() if lt.code else None) or qs.filter(name__iexact=lt.name).first()


def _match_component(c, branch_id):
    SC = M('PayrollManagement', 'SalaryComponent')
    qs = SC.objects.filter(branch_id=branch_id)
    return (qs.filter(code=c.code).first() if c.code else None) or qs.filter(name__iexact=c.name).first()


def plan(t):
    """What the transfer will do: {'changes': [...], 'warnings': [...], 'blockers': [...]} – nothing is saved."""
    E = M('EmpManagement', 'emp_master')
    emp = E.objects.filter(pk=t.employee_id).first()
    out = {'changes': [], 'warnings': [], 'blockers': [], 'stays': []}
    if emp is None:
        out['blockers'].append('Employee not found.')
        return out
    nb = t.to_branch_id and t.to_branch_id != emp.emp_branch_id_id
    fields = [('Branch', 'OrganisationManager.brnch_mstr', emp.emp_branch_id_id, t.to_branch_id, ('branch_name',)),
              ('Department', 'OrganisationManager.dept_master', emp.emp_dept_id_id, t.to_department_id, ('dept_name',)),
              ('Designation', 'OrganisationManager.desgntn_master', emp.emp_desgntn_id_id, t.to_designation_id, ('desgntn_job_title',)),
              ('Category', 'OrganisationManager.ctgry_master', emp.emp_ctgry_id_id, t.to_category_id, ('ctgry_title',)),
              ('Reporting manager', 'UserManagement.CustomUser', emp.emp_reporting_manager_id, t.to_manager_id, ('username',))]
    moved = False
    for label, model, old, new, attrs in fields:
        if new and new != old:
            if model == 'UserManagement.CustomUser':   # v1.62.0 people: the manager by name, not the login name
                from .names import user_name
                out['changes'].append({'area': 'Employee', 'what': label, 'from': user_name(old), 'to': user_name(new)})
            else:
                out['changes'].append({'area': 'Employee', 'what': label, 'from': _name(_obj(model, old), *attrs), 'to': _name(_obj(model, new), *attrs)})
            moved = True
    try:  # v1.12.0: location / division / section / cost centre / grade / position / employment type (OrgStructure)
        if apps.is_installed('OrgStructure') and (t.options or {}).get('org'):
            from OrgStructure.hooks import transfer_plan
            moved = transfer_plan(t, emp, out) or moved
    except Exception:
        log.exception('org transfer plan failed')
    if not moved and not t.salary_changes:
        out['blockers'].append('Nothing changes – choose a new branch, department, designation, category, manager, organisation unit or salary.')
    if not emp.is_active:
        out['blockers'].append('The employee is not active.')
    if t.to_department_id:
        D = M('OrganisationManager', 'dept_master').objects.filter(pk=t.to_department_id).first()
        if D is not None and D.branch.exists() and not D.branch.filter(pk=t.to_branch_id or emp.emp_branch_id_id).exists():
            out['warnings'].append(f'Department {D.dept_name} is not set up for the new branch (Departments → branches).')
    # payroll already processed for the month of the effective date
    PR = M('PayrollManagement', 'PayrollRun')
    eff = t.effective_date
    done = PR.objects.filter(month=eff.month, year=eff.year, status__in=['processed', 'approved', 'paid']).filter(employees=emp).exists() or \
        PR.objects.filter(month=eff.month, year=eff.year, status__in=['processed', 'approved', 'paid'], branch=emp.emp_branch_id).exists()
    if done and eff.day != 1:
        out['blockers'].append(f'Payroll for {eff:%m/%Y} is already processed for the old branch – use the 1st of next month as the effective date.')
    elif not done:
        out['warnings'].append(f'Payroll for {eff:%m/%Y} is not processed yet: the whole month is paid by the new branch.' if nb else '')
    out['warnings'] = [w for w in out['warnings'] if w]
    if nb:
        # leave balances of leave types that belong to the old branch
        B = M('calendars', 'emp_leave_balance')
        for b in B.objects.filter(employee=emp, leave_type__branch_id=emp.emp_branch_id_id).select_related('leave_type'):
            if _shared_type(b.leave_type, t.to_branch_id):
                continue
            tgt = _match_type(b.leave_type, t.to_branch_id)
            if tgt is None and not float(b.balance or 0):
                continue
            if tgt is None:
                out['warnings'].append(f'Leave type {b.leave_type.name} has no match in the new branch – balance {b.balance or 0:g} stays on the old type.')
            elif float(b.balance or 0):
                out['changes'].append({'area': 'Leave', 'what': f'{b.leave_type.name} balance', 'from': f'{b.balance:g} days (old branch type)', 'to': f'{tgt.name} of the new branch'})
        # salary components
        shared = _shared_components(t.to_branch_id)
        if shared:
            out['stays'].append('salary components (the company uses one set for all branches)')
        for s in ([] if shared else M('PayrollManagement', 'EmployeeSalaryStructure').objects.filter(employee=emp, component__branch_id=emp.emp_branch_id_id).select_related('component')):
            tgt = _match_component(s.component, t.to_branch_id)
            out['changes'].append({'area': 'Salary', 'what': f'{s.component.name} ({s.component.code})', 'from': f'{s.amount} – old branch component',
                                   'to': f'{tgt.name} ({tgt.code}) of the new branch' if tgt else 'copied to the new branch (no component with this code there)'})
        for st in M('PayrollManagement', 'SalaryStructure').objects.filter(employees=emp):
            if st.branch.filter(pk=t.to_branch_id).exists() or not st.branch.exists():
                continue
            tgt = M('PayrollManagement', 'SalaryStructure').objects.filter(name__iexact=st.name, branch=t.to_branch_id).first()
            out['changes'].append({'area': 'Salary', 'what': f'Salary structure {st.name}', 'from': 'old branch', 'to': tgt.name + ' (new branch)' if tgt else 'removed – no structure with this name in the new branch'})
        for app, model in OPEN_REQUESTS:
            Mo = M(app, model)
            n = _pending(Mo.objects.filter(employee=emp)).count()
            if n:
                out['changes'].append({'area': 'Requests', 'what': REQ_NAMES.get(model, Mo._meta.verbose_name.title()), 'from': f'{n} open', 'to': 'moved to the new branch'
                                       + (' – approvals re-sent to the new approvers' if model == 'employee_leave_request' else '')})
        if emp.users_id:
            UBA = M('OrganisationManager', 'UserBranchAccess')
            if UBA.objects.filter(user_id=emp.users_id, branch=emp.emp_branch_id).exists():
                out['changes'].append({'area': 'Access', 'what': 'User branch access', 'from': _name(emp.emp_branch_id, 'branch_name'), 'to': _name(_obj('OrganisationManager.brnch_mstr', t.to_branch_id), 'branch_name')})
        GF = M('OrganisationManager', 'BranchGeoFence')
        fences = [g for g in GF.objects.filter(employee=emp) if g.branch.filter(pk=emp.emp_branch_id_id).exists() and not g.branch.filter(pk=t.to_branch_id).exists()]
        if fences:
            out['changes'].append({'area': 'Attendance', 'what': 'Punch locations (geo-fence)', 'from': ', '.join(g.location_name for g in fences), 'to': 'removed – add the new site in Geo-fences'})
    # leave policy
    try:
        from LeavePolicy.engine import policy_for
        old_p = policy_for(emp)
        if t.to_category_id and t.to_category_id != emp.emp_ctgry_id_id:
            emp2 = E.objects.get(pk=emp.pk)
            emp2.emp_ctgry_id_id = t.to_category_id
            new_p = policy_for(emp2)
            if getattr(old_p, 'pk', None) != getattr(new_p, 'pk', None):
                out['changes'].append({'area': 'Leave', 'what': 'Leave policy', 'from': getattr(old_p, 'name', '–'), 'to': getattr(new_p, 'name', '–')})
    except Exception:
        pass
    SCodes = {c.code: c for c in M('PayrollManagement', 'SalaryComponent').objects.filter(code__in=list((t.salary_changes or {}).keys()))}
    for code, amt in (t.salary_changes or {}).items():
        s = M('PayrollManagement', 'EmployeeSalaryStructure').objects.filter(employee=emp, component__code=code).first()
        if code not in SCodes:
            out['blockers'].append(f'Salary component {code} does not exist.')
        else:
            out['changes'].append({'area': 'Salary', 'what': f'{SCodes[code].name} amount', 'from': f'{s.amount}' if s else 'not in the salary', 'to': f'{Decimal(str(amt)):.2f}'})
    docs = M('EmpManagement', 'Emp_Documents').objects.filter(emp_id=emp).count()
    out['stays'] = out['stays'] + [f'{docs} document(s)', 'attendance, payslips, leave history, loans, assets, goals and training records',
                    'weekend / holiday calendar: follows the new branch unless set for the employee personally']
    return out


@transaction.atomic
def apply(t, user=None):
    """Carry out a transfer (status scheduled → done). Raises ValueError with the blockers."""
    p = plan(t)
    if p['blockers']:
        raise ValueError(' '.join(p['blockers']))
    E = M('EmpManagement', 'emp_master')
    emp = E.objects.select_for_update().get(pk=t.employee_id)
    old_branch = emp.emp_branch_id_id
    nb = t.to_branch_id and t.to_branch_id != old_branch
    t.from_branch_id, t.from_department_id, t.from_designation_id = old_branch, emp.emp_dept_id_id, emp.emp_desgntn_id_id
    t.from_category_id, t.from_manager_id = emp.emp_ctgry_id_id, emp.emp_reporting_manager_id
    done = []
    upd = []
    for f, v in (('emp_branch_id_id', t.to_branch_id), ('emp_dept_id_id', t.to_department_id), ('emp_desgntn_id_id', t.to_designation_id),
                 ('emp_ctgry_id_id', t.to_category_id), ('emp_reporting_manager_id', t.to_manager_id)):
        if v and getattr(emp, f) != v:
            setattr(emp, f, v)
            upd.append(f[:-3])
    if upd:
        emp.save(update_fields=upd)           # through save: change history is recorded
        done.append(f'Employee: {", ".join(upd)}')
    if (t.options or {}).get('org') and apps.is_installed('OrgStructure'):
        from OrgStructure.hooks import transfer_apply   # v1.12.0 – validated in plan(); errors roll the transfer back
        done += transfer_apply(t, emp, user)
    if nb:
        from LeavePolicy import engine
        B = M('calendars', 'emp_leave_balance')
        for b in B.objects.filter(employee=emp, leave_type__branch_id=old_branch).select_related('leave_type'):
            if _shared_type(b.leave_type, t.to_branch_id):
                continue
            tgt = _match_type(b.leave_type, t.to_branch_id)
            bal = float(b.balance or 0)
            if tgt is None or not bal:
                continue
            engine.post(emp.pk, b.leave_type_id, t.effective_date, 'adjustment', -bal, note=f'Transfer: moved to {tgt.name} of the new branch', user_id=getattr(user, 'pk', None), move_balance=True)
            engine.post(emp.pk, tgt.pk, t.effective_date, 'adjustment', bal, note=f'Transfer: from {b.leave_type.name} of the old branch', user_id=getattr(user, 'pk', None), move_balance=True)
            done.append(f'Leave: {bal:g} days {b.leave_type.name} → {tgt.name}')
        ESS = M('PayrollManagement', 'EmployeeSalaryStructure')
        SC = M('PayrollManagement', 'SalaryComponent')
        for s in ([] if _shared_components(t.to_branch_id) else ESS.objects.filter(employee=emp, component__branch_id=old_branch).select_related('component')):
            c = s.component
            tgt = _match_component(c, t.to_branch_id)
            if tgt is None:
                tgt = SC.objects.create(name=c.name, code=c.code, component_type=c.component_type, payroll_category=c.payroll_category, branch_id=t.to_branch_id,
                                        deduct_leave=c.deduct_leave, component_value_type=c.component_value_type, formula=c.formula, description=c.description,
                                        show_in_payslip=c.show_in_payslip)
                done.append(f'Salary: component {c.code} copied to the new branch')
            if ESS.objects.filter(employee=emp, component=tgt).exclude(pk=s.pk).exists():
                ESS.objects.filter(employee=emp, component=tgt).exclude(pk=s.pk).update(amount=s.amount, is_active=s.is_active)
                s.delete()
            else:
                ESS.objects.filter(pk=s.pk).update(component=tgt)
            done.append(f'Salary: {c.code} {s.amount} → new branch component')
        SS = M('PayrollManagement', 'SalaryStructure')
        for st in SS.objects.filter(employees=emp):
            if st.branch.filter(pk=t.to_branch_id).exists() or not st.branch.exists():
                continue
            st.employees.remove(emp)
            tgt = SS.objects.filter(name__iexact=st.name, branch=t.to_branch_id).first()
            if tgt:
                tgt.employees.add(emp)
            done.append(f'Salary structure {st.name}: ' + ('moved' if tgt else 'removed'))
        for app, model in OPEN_REQUESTS:
            Mo = M(app, model)
            ids = list(_pending(Mo.objects.filter(employee=emp)).values_list('pk', flat=True))
            if not ids:
                continue
            Mo.objects.filter(pk__in=ids).update(branch_id=t.to_branch_id)
            done.append(f'{REQ_NAMES.get(model, Mo._meta.verbose_name.title())}: {len(ids)} open moved')
            if model == 'employee_leave_request':
                LA = M('calendars', 'LeaveApproval')
                from calendars.models import create_initial_leave_approval
                for r in Mo.objects.filter(pk__in=ids):
                    LA.objects.filter(leave_request=r, status='Pending').delete()
                    if not LA.objects.filter(leave_request=r).exists():
                        create_initial_leave_approval(Mo, r, created=True)
        if emp.users_id:
            UBA = M('OrganisationManager', 'UserBranchAccess')
            for a in UBA.objects.filter(user_id=emp.users_id, branch=old_branch):
                a.branch.remove(old_branch)
                a.branch.add(t.to_branch_id)
                done.append('User branch access moved')
        GF = M('OrganisationManager', 'BranchGeoFence')
        for g in GF.objects.filter(employee=emp):
            if g.branch.filter(pk=old_branch).exists() and not g.branch.filter(pk=t.to_branch_id).exists():
                g.employee.remove(emp)
                done.append(f'Removed from punch location {g.location_name}')
    ESS = M('PayrollManagement', 'EmployeeSalaryStructure')
    SRH = M('PayrollManagement', 'SalaryRevisionHistory')
    changed_from = {}   # v1.62.2 (item 55): component id → amount before the transfer
    for code, amt in (t.salary_changes or {}).items():
        amt = Decimal(str(amt)).quantize(Decimal('0.01'))
        comp = M('PayrollManagement', 'SalaryComponent').objects.filter(code=code, branch_id=emp.emp_branch_id_id).first() \
            or M('PayrollManagement', 'SalaryComponent').objects.filter(code=code).first()
        s = ESS.objects.filter(employee=emp, component__code=code).first()
        old = s.amount if s else None
        if s:
            ESS.objects.filter(pk=s.pk).update(amount=amt)
            comp = s.component   # v1.62.2 (item 55): the revision is of the component the employee has
        else:
            ESS.objects.create(employee=emp, component=comp, amount=amt, is_active=True, valid_from=t.effective_date)
        # v1.62.2 (item 55): one revision row per employee + component (unique) – SRH.objects.create failed with an IntegrityError
        # for anyone whose salary was revised before; inside the atomic transfer that broke the transaction and the transfer
        # was not saved ("current transaction is aborted"). The change is now added to the row's revision list, in a savepoint.
        try:
            with transaction.atomic():
                h, _ = SRH.objects.get_or_create(employee=emp, component=comp, defaults={'created_by': user if getattr(user, 'pk', None) else None})
                SRH.objects.filter(pk=h.pk).update(old_amount=old, new_amount=amt, effective_period=f'{t.effective_date:%B%Y}',
                                                   remarks=f'Transfer {t.number or t.pk} from {t.effective_date:%d/%m/%Y}'[:255])
                h.refresh_from_db()
                h.add_revision(old, amt, revised_by=user if getattr(user, 'pk', None) else None, effective_period=f'{t.effective_date:%B%Y}',
                               remarks=f'Transfer {t.number or t.pk} from {t.effective_date:%d/%m/%Y}')
        except Exception:
            log.exception('salary revision line failed')
        done.append(f'Salary: {code} {old} → {amt}')
        if old is not None and Decimal(str(old)) != amt:
            changed_from[comp.pk] = Decimal(str(old))
    if changed_from:   # v1.62.2 (item 55): the new salary from the transfer date
        done += _salary_from_date(emp, t, changed_from, user)
    try:
        Msg = M('Chatter', 'Message')
        Msg.objects.create(model='EmpManagement.emp_master', object_id=str(emp.pk), author=user,
                           body=f'Transferred on {t.effective_date:%d/%m/%Y}' + (f' – {t.reason}' if t.reason else '') + ':\n' + '\n'.join(done))
    except Exception:
        log.exception('transfer note failed')
    t.status, t.done_at, t.result = 'done', timezone.now(), {'done': done, 'plan': p}
    t.save()
    return done


def _salary_from_date(emp, t, old_amounts, user=None):
    """v1.62.2 (item 55): the salary amounts of a transfer apply from its effective date. Payroll pays the amounts of the salary
    structure for the whole month, so
    * payslips already made for days from the effective date (a transfer dated back) get arrear lines for those days
      (PayrollPlus arrears, paid with the next payroll – the same as a salary revision with an earlier effective date);
    * the month of the effective date when no payslip has it yet: the days before the date are at the old amount – a
      correction line (old − new) × days before ÷ days of the month waits for that month's payroll."""
    out = []
    try:
        from PayrollPlus import cycles as C
        from PayrollPlus.models import ArrearLine
    except ImportError:
        return out
    eff = t.effective_date
    try:
        r = C.calculate([emp.pk], eff, component_ids=list(old_amounts), user=user if getattr(user, 'pk', None) else None, save=True)
        n = sum(len(x.get('lines') or []) for x in r.get('rows') or [])
        if n:
            out.append(f'Salary: {n} arrear line(s) for payslips from {eff:%d/%m/%Y}')
    except Exception:
        log.exception('transfer arrears failed')
    if eff.day == 1:
        return out
    import calendar as _cal
    P = M('PayrollManagement', 'Payslip')
    if P.objects.filter(employee=emp, payroll_run__year=eff.year, payroll_run__month=eff.month).exists():
        return out   # the month is paid – the arrear lines above correct it
    ESS = M('PayrollManagement', 'EmployeeSalaryStructure')
    SC = M('PayrollManagement', 'SalaryComponent')
    dim = _cal.monthrange(eff.year, eff.month)[1]
    before = Decimal(eff.day - 1)
    m0 = eff.replace(day=1)
    for cid, old in old_amounts.items():
        c = SC.objects.filter(pk=cid).first()
        new = Decimal(str(ESS.objects.filter(employee=emp, component_id=cid).values_list('amount', flat=True).first() or 0))
        if c is None or not (c.component_value_type == 'fixed' or C.scalable(c)):
            continue
        diff = ((old - new) * before / Decimal(dim)).quantize(Decimal('0.01'))
        if not diff:
            continue
        ArrearLine.objects.filter(employee_id=emp.pk, component_id=cid, month=m0, source_payslip_id__isnull=True, effective_from=eff,
                                  status='pending').delete()
        ArrearLine.objects.create(employee_id=emp.pk, component_id=cid, month=m0, old_amount=old, new_amount=new, amount=-diff if c.component_type == 'deduction' else diff,
                                  effective_from=eff, note=f'Transfer {t.number or t.pk}: {int(before)} day(s) before {eff:%d/%m/%Y} at the old amount'[:200],
                                  created_by_id=getattr(user, 'pk', None))
        out.append(f'Salary: {c.code} {diff:+.2f} for {int(before)} day(s) before {eff:%d/%m/%Y} at the old amount (with the payroll of {eff:%B %Y})')
    return out


def due(today=None):
    """Scheduled transfers whose date has come – carried out by the daily job."""
    from .models import EmployeeTransfer
    out = []
    for t in EmployeeTransfer.objects.filter(status='scheduled', effective_date__lte=today or date.today()):
        try:
            apply(t)
            out.append((t.pk, 'done'))
        except Exception as e:
            out.append((t.pk, str(e)))
    return out
