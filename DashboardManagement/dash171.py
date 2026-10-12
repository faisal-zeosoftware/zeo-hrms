"""
v1.17.1 (dash171) – what the Home dashboards were missing. Read-only, no tables of its own: everything is read from the
existing models of the newer modules, with the same rights and scope as the module screens.

  * Approvals of the newer modules (expense reports / trips / advances, attendance corrections, shift requests and open-shift
    claims, roster approval, profile changes, letters, complaints, scheduled transfers whose date has come, asset returns,
    timesheet entries): who has to act is worked out exactly like the module does it (its own rights helpers), so a row is
    "waiting for you" only when the module would let you approve it.
        waiting_for(user, request)   rows for "Approvals waiting for you" / Team "Waiting for my approval"
        requests_of(emp_ids, open)   an employee's own requests (My requests) / open requests of a team
        queue_rows(run)              the approvals queue report (requests-pending) – every waiting item with level and approver
  * Company policies: what applies to me (acknowledged / to acknowledge / overdue), my entitlements (leave policy, expense
    policy limits, air ticket), the HR report "policy-acks", team members who have not acknowledged.
  * My shift (ShiftPlanner resolver), open shifts of the team, my expense reports by status, my certificates.
  * Report-centre lists behind the new HR operations figures: policy-acks, expense-reports, expense-advances, leaver-clearance.
"""
import logging
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.apps import apps
from django.db.models import Q
from django.utils import timezone

log = logging.getLogger(__name__)
M_ = '/main-sidebar/'


def _m(label):
    try:
        return apps.get_model(label)
    except (LookupError, ValueError):
        return None


def _on(app):
    return apps.is_installed(app)


def as_request(user, request=None):
    """The request when it is the user's own, else a stand-in that the AccessControl ctx accepts (it only reads .user)."""
    if request is not None and getattr(getattr(request, 'user', None), 'pk', None) == getattr(user, 'pk', None):
        return request
    return SimpleNamespace(user=user, GET={}, query_params={}, META={}, data={})


def _ctx(req):
    from AccessControl.access import ctx
    return ctx(req)


def _day(v):
    if isinstance(v, datetime):
        return timezone.localtime(v).date() if timezone.is_aware(v) else v.date()
    return v


def _name(e):
    if e is None:
        return ''
    return ' '.join(x for x in (e.emp_first_name, e.emp_last_name) if x) or e.emp_code


def _emps(ids):
    Emp = _m('EmpManagement.emp_master')
    ids = {int(i) for i in ids if i}
    if not ids:
        return {}
    return {e.id: e for e in Emp.objects.filter(id__in=ids).select_related('emp_branch_id', 'emp_dept_id', 'emp_desgntn_id', 'emp_ctgry_id')}


_UNAMES = {}


def uname(uid):
    """A user's display name (employee name when linked, else the user name)."""
    if not uid:
        return ''
    if uid in _UNAMES:
        return _UNAMES[uid]
    U = _m('UserManagement.CustomUser')
    u = U.objects.filter(pk=uid).first()
    if u is None:
        return ''
    e = _m('EmpManagement.emp_master').objects.filter(users=u).first()
    out = _name(e) if e else (' '.join(x for x in (getattr(u, 'first_name', ''), getattr(u, 'last_name', '')) if x) or u.username)
    _UNAMES[uid] = out
    return out


def money(v):
    return round(float(v or 0), 2)


PENDING = {'submitted', 'pending', 'manager', 'hr', 'peer', 'requested', 'acknowledged', 'investigating', 'waiting', 'scheduled'}
DONE = {'approved', 'reimbursed', 'issued', 'completed', 'resolved', 'closed', 'paid', 'settled', 'done', 'filled'}
REFUSED = {'rejected', 'declined', 'withdrawn', 'cancelled', 'sent_back', 'failed'}


def skey(code):
    c = (code or '').lower()
    return 'pending' if c in PENDING else 'approved' if c in DONE else 'rejected' if c in REFUSED else (c or 'pending')


def _row(key, label, obj, emp, summary, d, level='', approver='', doc=None, model=None, pk=None, due=None, branch_id=None):
    st = getattr(obj, 'status', None) or getattr(obj, 'approval_status', '')
    try:
        st_label = obj.get_status_display() if hasattr(obj, 'get_status_display') else (obj.get_approval_status_display())
    except Exception:
        st_label = str(st or '')
    return {'module': key, 'module_label': label, 'id': pk or obj.pk, 'request_id': pk or obj.pk, 'approval_id': pk or obj.pk,
            '_m': model or obj._meta.label, '_id': pk or obj.pk, 'document_number': doc, 'summary': summary, 'date': _day(d),
            'employee_id': emp.id if emp is not None else None, 'employee': _name(emp) if emp is not None else '',
            'employee_code': emp.emp_code if emp is not None else None, '_emp': emp, 'status': st_label, 'status_code': st,
            'status_key': skey(st), 'level': level, 'approver': approver, 'due': due,
            'branch_id': branch_id if branch_id is not None else (emp.emp_branch_id_id if emp is not None else None)}


# ====================================================================================================== approval modules
class Mod:
    """One request type of a newer module."""
    key = label = model = ''
    link = None            # the screen where the approver acts (frontend route)
    own_link = None        # the employee's own screen
    in_my_requests = True

    def ok(self):
        return _m(self.model) is not None

    def qs(self):
        return _m(self.model).objects.all()

    def open_q(self):
        return Q(status__in=('pending',))

    def mine_q(self):
        return Q()

    def emp_id(self, o):
        return o.employee_id

    def row(self, o, emp, cache):
        raise NotImplementedError

    def can_act(self, o, emp, user, req, cache):
        return False

    # generic
    def rows(self, objs, cache):
        objs = list(objs)
        emps = _emps(self.emp_id(o) for o in objs)
        out = []
        for o in objs:
            try:
                out.append(self.row(o, emps.get(self.emp_id(o)), cache))
            except Exception:
                log.exception('dash171 row failed: %s %s', self.key, getattr(o, 'pk', None))
        return out

    def requests_of(self, emp_ids, open_only=False, cache=None):
        cache = cache if cache is not None else {}
        qs = self.qs().filter(employee_id__in=list(emp_ids))
        qs = qs.filter(self.open_q()) if open_only else qs.filter(self.mine_q())
        return self.rows(qs, cache)

    def waiting(self, user, req, cache):
        objs = list(self.qs().filter(self.open_q()))
        emps = _emps(self.emp_id(o) for o in objs)
        out = []
        for o in objs:
            e = emps.get(self.emp_id(o))
            try:
                if self.can_act(o, e, user, req, cache):
                    out.append(self.row(o, e, cache))
            except Exception:
                log.exception('dash171 approver check failed: %s %s', self.key, o.pk)
        return out


class ExpenseReportMod(Mod):
    key, label, model = 'expense', 'Expense report', 'ExpenseManagement.ExpenseReport'
    link, own_link = M_ + 'expense-options/reports', M_ + 'expense-options/reports'

    def open_q(self):
        return Q(status='submitted')

    def mine_q(self):
        return ~Q(status='draft')      # drafts are "Your items needing input"

    def step(self, o):
        EA = _m('ExpenseManagement.ExpenseApproval')
        return EA.objects.filter(report=o, status='pending').order_by('-round', 'level').first()

    def row(self, o, emp, cache):
        st = self.step(o) if o.status == 'submitted' else None
        return _row(self.key, self.label, o, emp, f"{o.title} · AED {money(o.total):,.2f}", o.submitted_at or o.created_at,
                    level=st.level if st else '', approver=uname(st.approver_user_id) if st else '', doc=o.number)

    def waiting(self, user, req, cache):   # the pending step names the approver (ExpenseManagement.services._find_step)
        EA = _m('ExpenseManagement.ExpenseApproval')
        ids = EA.objects.filter(status='pending', approver_user_id=user.pk, report__status='submitted').values('report_id')
        return self.rows(self.qs().filter(pk__in=ids), cache)


class TripMod(ExpenseReportMod):
    key, label, model = 'trip', 'Business trip', 'ExpenseManagement.Trip'
    link = own_link = M_ + 'expense-options/trips'

    def step(self, o):
        EA = _m('ExpenseManagement.ExpenseApproval')
        return EA.objects.filter(trip=o, status='pending').order_by('-round', 'level').first()

    def row(self, o, emp, cache):
        st = self.step(o) if o.status == 'submitted' else None
        return _row(self.key, self.label, o, emp, f"{o.purpose} · {o.start_date:%d/%m/%Y} – {o.end_date:%d/%m/%Y}", o.created_at,
                    level=st.level if st else '', approver=uname(st.approver_user_id) if st else '', doc=o.number)

    def waiting(self, user, req, cache):
        EA = _m('ExpenseManagement.ExpenseApproval')
        ids = EA.objects.filter(status='pending', approver_user_id=user.pk, trip__status='submitted').values('trip_id')
        return self.rows(self.qs().filter(pk__in=ids), cache)


class AdvanceMod(Mod):
    key, label, model = 'expense_advance', 'Expense advance', 'ExpenseManagement.ExpenseAdvance'
    link = own_link = M_ + 'expense-options/advances'

    def open_q(self):
        return Q(status='requested')

    def _mgr(self, emp, cache):
        k = ('mgr', emp.pk if emp else None)
        if k not in cache:
            from LeavePolicy.approvers import holder
            cache[k] = holder('reporting_manager', emp) if emp is not None else None
        return cache[k]

    def row(self, o, emp, cache):
        m = self._mgr(emp, cache) if o.status == 'requested' else None
        return _row(self.key, self.label, o, emp, f"AED {money(o.amount):,.2f}" + (f" · {o.purpose}" if o.purpose else ''), o.created_at or o.date,
                    level=1 if o.status == 'requested' else '', approver=(_name_user(m) if m else 'Expense HR') if o.status == 'requested' else '', doc=o.number)

    def can_act(self, o, emp, user, req, cache):   # ExpenseManagement.views.AdvanceViewSet.approve
        if emp is None or emp.users_id == user.pk:
            return False
        m = self._mgr(emp, cache)
        if m is not None and m.pk == user.pk:
            return True
        c = _ctx(req)
        return (c.admin or 'change_expenseadvance' in c.codes) and (c.admin or c.branches is None or emp.emp_branch_id_id in (c.branches or []))


def _name_user(u):
    return uname(u.pk) if u is not None else ''


class CorrectionMod(Mod):
    key, label, model = 'correction', 'Attendance correction', 'AttendancePlus.CorrectionRequest'
    link = M_ + 'attendance-plus/corrections'
    own_link = M_ + 'attendance-plus/corrections'

    def open_q(self):
        return Q(status__in=('manager', 'hr'))

    def row(self, o, emp, cache):
        lvl = 1 if o.status == 'manager' else 2 if o.status == 'hr' else ''
        who = (uname(emp.emp_reporting_manager_id) if emp is not None else '') if o.status == 'manager' else ('HR' if o.status == 'hr' else '')
        return _row(self.key, self.label, o, emp, f"{o.get_kind_display()} · {o.date:%d/%m/%Y}", o.created_at, level=lvl, approver=who)

    def can_act(self, o, emp, user, req, cache):   # AttendancePlus.views.CorrectionViewSet._decide
        if emp is None or emp.users_id == user.pk:
            return False
        if o.status == 'manager':
            return emp.emp_reporting_manager_id == user.pk
        from AttendancePlus.access import hr_for_employee
        return hr_for_employee(req, emp.id)


def _sp_rights(req, cache):
    if 'sp' not in cache:
        from ShiftPlanner.services import Rights
        cache['sp'] = Rights(req)
    return cache['sp']


def _shift_names(cache):
    if 'shifts' not in cache:
        S = _m('calendars.Shift')
        cache['shifts'] = dict(S.objects.values_list('id', 'name')) if S else {}
    return cache['shifts']


class ShiftRequestMod(Mod):
    key, label, model = 'shift_request', 'Shift request', 'ShiftPlanner.ShiftRequest'
    link = own_link = M_ + 'shift-planner/requests'

    def open_q(self):
        return Q(status__in=('peer', 'pending'))

    def row(self, o, emp, cache):
        names = _shift_names(cache)
        txt = f"{o.get_kind_display()} · {o.date:%d/%m/%Y}"
        if o.kind == 'change' and o.to_shift_id:
            txt += f" → {names.get(o.to_shift_id, '')}"
        if o.kind == 'swap' and o.swap_employee_id:
            txt += f" ⇄ {_name(_emps([o.swap_employee_id]).get(o.swap_employee_id))}"
        lvl, who = '', ''
        if o.status == 'peer':
            lvl, who = 1, _name(_emps([o.swap_employee_id]).get(o.swap_employee_id)) if o.swap_employee_id else ''
        elif o.status == 'pending':
            lvl = 2 if o.kind == 'swap' else 1
            who = uname(emp.emp_reporting_manager_id) if emp is not None and emp.emp_reporting_manager_id else 'HR'
        return _row(self.key, self.label, o, emp, txt, o.created_at, level=lvl, approver=who)

    def can_act(self, o, emp, user, req, cache):   # ShiftPlanner: the colleague answers a swap, then manager / roster HR decide
        if o.status == 'peer':
            sw = _emps([o.swap_employee_id]).get(o.swap_employee_id) if o.swap_employee_id else None
            return sw is not None and sw.users_id == user.pk
        return _sp_rights(req, cache).can_decide_for(emp)


class OpenShiftClaimMod(Mod):
    key, label, model = 'open_shift', 'Open shift claim', 'ShiftPlanner.OpenShiftClaim'
    link = own_link = M_ + 'shift-planner/requests'

    def qs(self):
        return super().qs().select_related('open_shift')

    def row(self, o, emp, cache):
        s = o.open_shift
        who = (uname(emp.emp_reporting_manager_id) if emp is not None and emp.emp_reporting_manager_id else 'HR') if o.status == 'pending' else ''
        return _row(self.key, self.label, o, emp, f"{_shift_names(cache).get(s.shift_id, '')} · {s.date:%d/%m/%Y}", o.created_at,
                    level=1 if o.status == 'pending' else '', approver=who)

    def can_act(self, o, emp, user, req, cache):
        return _sp_rights(req, cache).can_decide_for(emp)


class RosterMod(Mod):
    """A submitted roster waits for its approver (no employee)."""
    key, label, model = 'roster', 'Roster to approve', 'ShiftPlanner.RosterPeriod'
    link = M_ + 'shift-planner/rosters'
    in_my_requests = False

    def open_q(self):
        return Q(status='submitted')

    def emp_id(self, o):
        return None

    def requests_of(self, emp_ids, open_only=False, cache=None):
        return []

    def row(self, o, emp, cache):
        r = _row(self.key, self.label, o, None, f"{o.name} · {o.date_from:%d/%m/%Y} – {o.date_to:%d/%m/%Y}", o.submitted_at or o.created_at,
                 level=1, approver=uname(o.approver_id) if o.approver_id else 'Roster approvers', branch_id=o.branch_id)
        r['employee'] = uname(o.submitted_by_id)   # who submitted it (a roster has no employee of its own)
        return r

    def can_act(self, o, emp, user, req, cache):
        return o.submitted_by_id != user.pk and _sp_rights(req, cache).can_approve_period(o)


class ProfileChangeMod(Mod):
    key, label, model = 'profile_change', 'Profile change', 'SelfService.ProfileChangeRequest'
    link, own_link = M_ + 'self-service/hr-requests', M_ + 'self-service/my-requests'
    codes = 'CR_HR'

    def row(self, o, emp, cache):
        return _row(self.key, self.label, o, emp, f"{str(o.group).replace('_', ' ').capitalize()} · {o.get_action_display()}", o.created_at,
                    level=1 if o.status == 'pending' else '', approver='HR' if o.status == 'pending' else '', doc=o.number or None)

    def can_act(self, o, emp, user, req, cache):   # SelfService.views._cr_for / letters: HR of the employee's branch, never their own
        from SelfService import services as SS
        if emp is not None and emp.users_id == user.pk:
            return False
        return SS.can(req, *getattr(SS, self.codes)) and SS.branch_ok(req, o.branch_id)


class LetterMod(ProfileChangeMod):
    key, label, model = 'letter', 'Letter request', 'SelfService.LetterRequest'
    link, own_link = M_ + 'self-service/hr-letters', M_ + 'self-service/letters'
    codes = 'LETTER_HR'

    def row(self, o, emp, cache):
        return _row(self.key, self.label, o, emp, o.get_letter_type_display() + (f" · {o.purpose}" if o.purpose else ''), o.created_at,
                    level=1 if o.status == 'pending' else '', approver='HR' if o.status == 'pending' else '', doc=o.number or None)


class GrievanceMod(Mod):
    """Complaints: open until resolved; handled by the assigned grievance officers / handle_grievance (never shown to others)."""
    key, label, model = 'grievance', 'Complaint', 'SelfService.Grievance'
    link, own_link = M_ + 'self-service/hr-complaints', M_ + 'self-service/complaints'

    def open_q(self):
        return Q(status__in=('submitted', 'acknowledged', 'investigating'))

    def row(self, o, emp, cache):
        who = ', '.join(x for x in (uname(u) for u in (o.assigned_user_ids or [])) if x) or 'Grievance officers'
        r = _row(self.key, self.label, o, emp if not o.anonymous else None, f"{o.get_category_display()} · {o.subject}"
                 + (f" · due {o.due_date:%d/%m/%Y}" if o.due_date and o.status in ('submitted', 'acknowledged', 'investigating') else ''),
                 o.created_at, level=1 if o.status in ('submitted', 'acknowledged', 'investigating') else '', approver=who,
                 doc=o.number or None, due=o.due_date, branch_id=o.branch_id)
        if o.anonymous:
            r['employee'] = 'Anonymous'
        return r

    def can_act(self, o, emp, user, req, cache):
        from SelfService.grievances import is_handler
        return is_handler(req, o)

    def waiting(self, user, req, cache):   # anonymous cases have no employee
        out = []
        for o in self.qs().filter(self.open_q()):
            if self.can_act(o, None, user, req, cache):
                out.append(self.row(o, _emps([o.employee_id]).get(o.employee_id), cache))
        return out


class TransferMod(Mod):
    """A scheduled transfer whose date has come and that the daily job could not carry out: HR has to do it."""
    key, label, model = 'transfer', 'Employee transfer', 'HRActions.EmployeeTransfer'
    link = M_ + 'sub-sidebar/employee-transfer'
    in_my_requests = False
    CODES = ('run_employee_transfer', 'add_employeetransfer', 'change_emp_master')

    def open_q(self):
        return Q(status='scheduled', effective_date__lte=timezone.localdate())

    def requests_of(self, emp_ids, open_only=False, cache=None):
        return [] if not open_only else super().requests_of(emp_ids, True, cache)

    def row(self, o, emp, cache):
        B = _m('OrganisationManager.brnch_mstr')
        bn = dict(B.objects.filter(id__in=[x for x in (o.from_branch_id, o.to_branch_id) if x]).values_list('id', 'branch_name')) if B else {}
        txt = f"{bn.get(o.from_branch_id, '–')} → {bn.get(o.to_branch_id, '–')} · {o.effective_date:%d/%m/%Y}"
        return _row(self.key, self.label, o, emp, txt, o.created_at, level=1, approver='HR')

    def can_act(self, o, emp, user, req, cache):   # HRActions.views.TransferActionView
        c = _ctx(req)
        if not (c.admin or any(x in c.codes for x in self.CODES)):
            return False
        return c.admin or c.branches is None or all(i in c.branches for i in (o.from_branch_id, o.to_branch_id) if i)


class AssetReturnMod(Mod):
    key, label, model = 'asset_return', 'Asset return', 'AssetPlus.AssetReturn'
    link, own_link = M_ + 'asset-plus/returns', M_ + 'asset-plus/my-assets'

    def open_q(self):
        return Q(status='requested')

    def row(self, o, emp, cache):
        A = _m('OrganisationManager.Asset')
        a = A.objects.filter(pk=o.asset_id).first() if A else None
        return _row(self.key, self.label, o, emp, (a.name if a else '') + (f" · {o.preferred_date:%d/%m/%Y}" if o.preferred_date else ''), o.created_at,
                    level=1 if o.status == 'requested' else '', approver='HR (assets)' if o.status == 'requested' else '', doc=o.number)

    def can_act(self, o, emp, user, req, cache):   # AssetPlus.views.ReturnViewSet.complete
        from AssetPlus import views as AV
        if not AV.can(req, 'alloc_change'):
            return False
        if 'assets' not in cache:
            cache['assets'] = AV.visible_asset_ids(req)
        vis = cache['assets']
        return vis is None or o.asset_id in vis


class TimesheetMod(Mod):
    key, label, model = 'timesheet', 'Timesheet entry', 'ProjectControl.TimesheetExtra'
    link, own_link = M_ + 'project-options/timesheet-approvals', M_ + 'project-options/my-timesheet'

    def ok(self):
        return _m(self.model) is not None and _m('ProjectManagement.TimeSheet') is not None

    def requests_of(self, emp_ids, open_only=False, cache=None):
        TS = _m('ProjectManagement.TimeSheet')
        ts = TS.objects.filter(employee_id__in=list(emp_ids)).values('id')
        want = ('submitted',) if open_only else ('submitted', 'rejected')
        return self.rows(self.qs().filter(timesheet_id__in=ts, approval_status__in=want), cache or {})

    def rows(self, objs, cache):
        TS = _m('ProjectManagement.TimeSheet')
        objs = list(objs)
        ts = {t.pk: t for t in TS.objects.filter(pk__in=[o.timesheet_id for o in objs]).select_related('project', 'task')}
        emps = _emps(t.employee_id for t in ts.values())
        out = []
        for o in objs:
            t = ts.get(o.timesheet_id)
            if t is None:
                continue
            e = emps.get(t.employee_id)
            r = _row(self.key, self.label, o, e, f"{t.project} · {t.task.title if t.task_id else ''} · {float(o.hours or 0):g} h".replace(' ·  ·', ' ·'),
                     t.date, level=1 if o.approval_status == 'submitted' else '',
                     approver=(uname(e.emp_reporting_manager_id) if e is not None and e.emp_reporting_manager_id else 'Project managers') if o.approval_status == 'submitted' else '',
                     model='ProjectManagement.TimeSheet', pk=t.pk)
            r['status_code'] = o.approval_status
            r['status_key'] = skey(o.approval_status)
            out.append(r)
        return out

    def waiting(self, user, req, cache):   # ProjectControl.services.approvable_timesheets + can_approve
        from ProjectControl.services import approvable_timesheets
        ids = approvable_timesheets(req).values('id')
        return self.rows(self.qs().filter(approval_status='submitted', timesheet_id__in=ids), cache)


MODS = [ExpenseReportMod(), TripMod(), AdvanceMod(), CorrectionMod(), ShiftRequestMod(), OpenShiftClaimMod(), RosterMod(), ProfileChangeMod(),
        LetterMod(), GrievanceMod(), TransferMod(), AssetReturnMod(), TimesheetMod()]
LABELS = {m.key: m.label for m in MODS}
LINKS = {m.key: {'admin': m.own_link or m.link, 'approval': m.link} for m in MODS}


def mods():
    return [m for m in MODS if m.ok()]


def _clean(rows):
    for r in rows:
        r.pop('_emp', None)
    return rows


def waiting_for(user, request=None):
    """Rows of the newer modules waiting for this user's decision (same shape as services.pending_approvals)."""
    req = as_request(user, request)
    cache, out = {}, []
    for m in mods():
        try:
            out += m.waiting(user, req, cache)
        except Exception:
            log.exception('dash171 waiting failed: %s', m.key)
    return _clean(out)


def requests_of(emp_ids, open_only=False):
    cache, out = {}, []
    for m in mods():
        if not m.in_my_requests and not open_only:
            continue
        try:
            out += m.requests_of(emp_ids, open_only, cache)
        except Exception:
            log.exception('dash171 requests failed: %s', m.key)
    return _clean(out)


def queue_rows(run):
    """requests-pending: every waiting item of the newer modules for the employees the user may see, with level and approver.
    Complaints only for their handlers (confidential)."""
    from .reports import emp_cols
    today = timezone.localdate()
    ids = set(run.all_employees.values_list('id', flat=True))
    cache, out = {}, []
    B = _m('OrganisationManager.brnch_mstr')
    bnames = dict(B.objects.values_list('id', 'branch_name')) if B else {}
    for m in mods():
        try:
            if m.key == 'grievance':
                rows = m.waiting(run.c.user, run.request, cache)
                rows = [r for r in rows if (r['employee_id'] in ids) or (r['employee_id'] is None and (run.branches is None or r['branch_id'] in run.branches))]
            elif m.key == 'roster':
                rows = [r for r in m.rows(m.qs().filter(m.open_q()), cache) if run.branches is None or r['branch_id'] in run.branches]
            else:
                rows = m.rows(m.qs().filter(m.open_q()), cache) if m.key != 'timesheet' else m.requests_of(ids, True, cache)
                rows = [r for r in rows if r['employee_id'] in ids]
        except Exception:
            log.exception('dash171 queue failed: %s', m.key)
            continue
        for r in rows:
            e = r.get('_emp')
            d = r['date']
            waiting = (today - d).days if d else ''
            base = emp_cols(e) if e is not None else {'_emp': None, 'employee': r['employee'], 'branch': bnames.get(r['branch_id'], ''),
                                                       'department': '', 'designation': '', 'category': ''}
            row = {**base, 'type': m.label, 'number': r['document_number'] or '', 'details': r['summary'], 'requested': d, 'waiting': waiting,
                   'over_7': 'Yes' if waiting != '' and waiting > 7 else 'No', 'step': r['level'], 'approver': r['approver'], 'status': 'Pending'}
            if m.key != 'grievance':     # a complaint is opened from the complaints screen only (confidential)
                row.update({'_m': r['_m'], '_id': r['_id']})
            out.append(row)
    return out


QUEUE_MODELS = {'ExpenseManagement.ExpenseReport', 'ExpenseManagement.Trip', 'ExpenseManagement.ExpenseAdvance', 'AttendancePlus.CorrectionRequest',
                'ShiftPlanner.ShiftRequest', 'ShiftPlanner.OpenShiftClaim', 'SelfService.ProfileChangeRequest', 'SelfService.LetterRequest',
                'HRActions.EmployeeTransfer', 'AssetPlus.AssetReturn', 'ProjectManagement.TimeSheet'}


# ====================================================================================================== company policies
def _policy_ctx():
    if not _on('OrgStructure'):
        return None
    CP = _m('OrganisationManager.CompanyPolicy')
    if CP is None:
        return None
    from OrgStructure import services as OS
    from OrgStructure.models import PolicyAcknowledgement
    pols = []
    for p in CP.objects.prefetch_related('branch', 'specific_users').order_by('title'):
        v = OS.policy_version(p)
        due = (v.version_date + timedelta(days=v.due_days)).date() if v.version_date else None   # = OrgStructure MyPoliciesView
        pols.append((p, v, due, [b.pk for b in p.branch.all()], [u.pk for u in p.specific_users.all()]))
    return OS, PolicyAcknowledgement, pols


def _state(acked, v, due, today):
    if acked:
        return 'acknowledged', 'Acknowledged'
    if not v.requires_ack:
        return 'not_required', 'Not required'
    if due and due < today:
        return 'overdue', 'Overdue'
    return 'to_acknowledge', 'To acknowledge'


def my_policies(emp, user_id=None, today=None):
    """Policies that apply to the employee with their state; [] when none / OrgStructure is not installed."""
    today = today or timezone.localdate()
    pc = _policy_ctx() if emp is not None else None
    if not pc:
        return []
    OS, ACK, pols = pc
    acks = {(a.policy_id, a.version): a for a in ACK.objects.filter(employee_id=emp.pk)}
    out = []
    for p, v, due, b, sp in pols:
        if not OS.policy_applies(p, emp, user_id=user_id or emp.users_id, branch_ids=b, specific=sp):
            continue
        a = acks.get((p.pk, v.version))
        key, label = _state(a, v, due, today)
        out.append({'id': p.pk, 'title': p.title, 'version': v.version, 'date': _day(v.version_date) if v.version_date else None, 'due': due,
                    'requires_ack': v.requires_ack, 'acknowledged': bool(a), 'acknowledged_at': _day(a.acknowledged_at) if a else None,
                    'state': key, 'state_label': label, 'has_file': bool(p.policy_file), 'module': 'policy'})
    return out


def policy_ack_rows(emps, today=None):
    """[(employee, policy row)] for every employee × policy that needs acknowledgement (HR report / team)."""
    today = today or timezone.localdate()
    pc = _policy_ctx()
    if not pc:
        return []
    OS, ACK, pols = pc
    emps = list(emps)
    ids = [e.pk for e in emps]
    acks = {(a.policy_id, a.employee_id, a.version): a for a in ACK.objects.filter(employee_id__in=ids)}
    out = []
    for p, v, due, b, sp in pols:
        if not v.requires_ack:
            continue
        for e in emps:
            if not OS.policy_applies(p, e, branch_ids=b, specific=sp):
                continue
            a = acks.get((p.pk, e.pk, v.version))
            key, label = _state(a, v, due, today)
            out.append((e, {'policy_id': p.pk, 'policy': p.title, 'version': v.version, 'version_date': _day(v.version_date) if v.version_date else None,
                            'due': due, 'state': key, 'status': 'Acknowledged' if a else ('Overdue' if key == 'overdue' else 'Pending'),
                            'acknowledged_at': _day(a.acknowledged_at) if a else None}))
    return out


def r_policy_acks(run):
    from .reports import EMP_COLS, emp_cols
    emps = run.all_employees.filter(Q(is_active=True) | Q(is_active__isnull=True))
    rows = []
    for e, x in policy_ack_rows(emps, run.today):
        rows.append({**emp_cols(e), 'policy': x['policy'], 'version': x['version'], 'version_date': x['version_date'], 'due': x['due'],
                     'status': x['status'], 'acknowledged_at': x['acknowledged_at'], '_m': 'EmpManagement.emp_master', '_id': e.pk})
    rows.sort(key=lambda r: (r['policy'], {'Overdue': 0, 'Pending': 1}.get(r['status'], 2), r['employee']))
    return [('policy', 'Policy', 'text')] + EMP_COLS + [('version', 'Version', 'number'), ('version_date', 'Version date', 'date'), ('due', 'Acknowledge by', 'date'),
                                                         ('status', 'Status', 'text'), ('acknowledged_at', 'Acknowledged on', 'date')], rows, \
        {'filters': ['policy', 'status', 'branch', 'department'], 'no_totals': ['version']}


def team_policy_rows(emps, today=None):
    """One row per team member with policies still to acknowledge."""
    per = defaultdict(list)
    by = {}
    for e, x in policy_ack_rows(emps, today):
        if x['status'] != 'Acknowledged':
            per[e.pk].append(x)
            by[e.pk] = e
    rows = []
    for eid, xs in per.items():
        e = by[eid]
        rows.append({'employee_id': eid, 'code': e.emp_code, 'name': _name(e), 'policies': ', '.join(x['policy'] for x in xs), 'count': len(xs),
                     'overdue': sum(1 for x in xs if x['status'] == 'Overdue'),
                     'state': 'Overdue' if any(x['status'] == 'Overdue' for x in xs) else 'Pending'})
    rows.sort(key=lambda r: (r['state'] != 'Overdue', r['name']))
    return rows


# ====================================================================================================== entitlements
def entitlements(emp, today=None):
    today = today or timezone.localdate()
    out = {}
    if emp is None:
        return out
    try:   # leave policy (LeavePolicy engine: own policy → category → default)
        if _on('LeavePolicy'):
            from LeavePolicy import engine
            p = engine.policy_for(emp)
            if p is not None:
                LT = _m('calendars.leave_type')
                names = dict(LT.objects.values_list('id', 'name')) if LT else {}
                lines = []
                for ln in p.lines.all().order_by('id'):
                    if ln.gender not in ('B', '', None) and ln.gender != (emp.emp_gender or ''):
                        continue
                    lines.append({'leave_type_id': ln.leave_type_id, 'name': names.get(ln.leave_type_id, ''), 'days': round(float(engine.days_a_year(ln, emp, today) or 0), 2),
                                  'accrual': ln.get_accrual_display(), 'carry': ln.carry_forward_max})
                out['leave'] = {'policy': p.name, 'lines': lines}
    except Exception:
        log.exception('entitlements: leave')
    try:   # expense policy (ExpenseManagement.services.policy_for) and its limits
        if _on('ExpenseManagement'):
            from ExpenseManagement.services import policy_for
            p = policy_for(emp)
            if p is not None:
                lim = [{'category': x.category.name, 'per_expense': money(x.per_expense_limit) if x.per_expense_limit is not None else None,
                        'per_day': money(x.per_day_limit) if x.per_day_limit is not None else None,
                        'per_report': money(x.per_report_limit) if x.per_report_limit is not None else None}
                       for x in p.limits.select_related('category').order_by('category__name')]
                out['expense'] = {'policy': p.name, 'currency': p.currency, 'mileage_rate': float(p.mileage_rate or 0), 'per_diem': money(p.per_diem_rate), 'limits': lim}
    except Exception:
        log.exception('entitlements: expense')
    try:   # air ticket: the active allocation (its policy)
        ATA = _m('PayrollManagement.AirTicketAllocation')
        al = ATA.objects.filter(employee=emp, is_active=True, status='APPROVED').select_related('policy').order_by('-id').first() if ATA else None
        if al is not None:
            p = al.policy
            out['air_ticket'] = {'policy': p.name, 'class': p.get_travel_class_display(), 'every_years': p.frequency_years, 'type': p.get_allowance_type_display(),
                                 'amount': money(al.amount), 'remaining': money(al.remaining_amount), 'expiry': al.expiry_date}
    except Exception:
        log.exception('entitlements: air ticket')
    return out


# ====================================================================================================== shifts
def my_shift(emp, today=None, days=7):
    """Today and the next `days` days from the shift planner resolver; None when no shift information exists."""
    if emp is None or not _on('ShiftPlanner'):
        return None
    today = today or timezone.localdate()
    try:
        from ShiftPlanner.resolver import Book
        book = Book(today, today + timedelta(days=days), [emp.pk])
        out, any_ = [], False
        for i in range(days + 1):
            d = today + timedelta(days=i)
            r = book.get(emp, d)
            if r is not None:
                any_ = True
            out.append({'date': d, 'off': bool(r and r['off']), 'known': r is not None, 'name': (r or {}).get('name') or '',
                        'start': r['start'].strftime('%H:%M') if r and r.get('start') else None, 'end': r['end'].strftime('%H:%M') if r and r.get('end') else None,
                        'hours': float(r['hours']) if r and r.get('hours') else 0, 'source': (r or {}).get('source') or ''})
        if not any_:
            return None
        return {'today': out[0], 'days': out[1:]}
    except Exception:
        log.exception('my shift failed')
        return None


def open_shifts(emps, today=None):
    """Open shifts (not filled / cancelled, from today) of the team's branches / departments that still have places."""
    OS_ = _m('ShiftPlanner.OpenShift')
    if OS_ is None:
        return []
    today = today or timezone.localdate()
    emps = list(emps)
    if not emps:
        return []
    br = {e.emp_branch_id_id for e in emps if e.emp_branch_id_id}
    dep = {e.emp_dept_id_id for e in emps if e.emp_dept_id_id}
    names = _shift_names({})
    B = _m('OrganisationManager.brnch_mstr')
    D = _m('OrganisationManager.dept_master')
    bn = dict(B.objects.values_list('id', 'branch_name')) if B else {}
    dn = dict(D.objects.values_list('id', 'dept_name')) if D else {}
    rows = []
    for o in OS_.objects.filter(status='open', date__gte=today, branch_id__in=br).filter(Q(department_id__isnull=True) | Q(department_id__in=dep)).prefetch_related('claims').order_by('date', 'id'):
        claims = list(o.claims.all())
        taken = sum(1 for c in claims if c.status == 'approved')
        left = max(o.slots - taken, 0)
        if left <= 0:
            continue
        rows.append({'_m': o._meta.label, '_id': o.pk, 'date': o.date, 'shift': names.get(o.shift_id, ''), 'branch': bn.get(o.branch_id, ''),
                     'department': dn.get(o.department_id, '') if o.department_id else '', 'slots': o.slots, 'left': left,
                     'claims': sum(1 for c in claims if c.status == 'pending'), 'note': o.note, 'module': 'open_shift'})
    return rows


# ====================================================================================================== expenses
EXP_STATES = [('draft', 'Draft'), ('waiting', 'Waiting for approval'), ('approved', 'Approved, not reimbursed'), ('reimbursed', 'Reimbursed'),
              ('returned', 'Sent back / rejected')]


def _exp_state(st):
    return {'draft': 'draft', 'submitted': 'waiting', 'approved': 'approved', 'reimbursed': 'reimbursed'}.get(st, 'returned')


def my_expenses(emp):
    ER = _m('ExpenseManagement.ExpenseReport')
    if ER is None or emp is None:
        return None
    qs = list(ER.objects.filter(employee_id=emp.pk))
    if not qs:
        return None
    c = Counter(_exp_state(r.status) for r in qs)
    amt = defaultdict(float)
    for r in qs:
        amt[_exp_state(r.status)] += float(r.amount_to_reimburse if r.status in ('approved', 'reimbursed') else r.total or 0)
    return {'states': [{'key': k, 'label': l, 'count': c.get(k, 0), 'amount': round(amt.get(k, 0), 2)} for k, l in EXP_STATES if c.get(k)],
            'total': len(qs)}


def my_expense_rows(emp, state=None):
    ER = _m('ExpenseManagement.ExpenseReport')
    if ER is None or emp is None:
        return []
    rows = []
    for r in ER.objects.filter(employee_id=emp.pk).order_by('-id'):
        k = _exp_state(r.status)
        if state and k != state:
            continue
        rows.append({'_m': r._meta.label, '_id': r.pk, 'number': r.number, 'title': r.title, 'total': money(r.total), 'reimburse': money(r.amount_to_reimburse),
                     'status': r.get_status_display(), 'date': _day(r.submitted_at or r.created_at), 'reimbursed_on': r.reimbursed_on})
    return rows


def _exp_date(r):
    if r.status in ('approved', 'reimbursed') and r.approved_at:
        return _day(r.approved_at)
    return _day(r.submitted_at or r.created_at)


def r_expense_reports(run):
    """Expense reports (not drafts) of the employees the user may see. Period = the approval date (approved / reimbursed
    reports) or the submission date (the others)."""
    from .reports import EMP_COLS, emp_cols
    ER = _m('ExpenseManagement.ExpenseReport')
    if ER is None:
        return [], [], {}
    emps = {e.pk: e for e in run.all_employees}
    EA = _m('ExpenseManagement.ExpenseApproval')
    steps = {}
    for a in EA.objects.filter(status='pending', report__isnull=False).order_by('-round', 'level'):
        steps.setdefault(a.report_id, a)
    rows = []
    for r in ER.objects.filter(employee_id__in=list(emps)).exclude(status='draft'):
        d = _exp_date(r)
        if d and not (run.date_from <= d <= run.date_to):
            continue
        st = steps.get(r.pk) if r.status == 'submitted' else None
        rows.append({**emp_cols(emps[r.employee_id]), 'number': r.number, 'title': r.title, 'date': d, 'total': money(r.total),
                     'advance': money(r.advance_applied), 'to_reimburse': money(r.amount_to_reimburse) if r.status == 'approved' else 0,
                     'reimbursed': money(r.amount_to_reimburse) if r.status == 'reimbursed' else 0,
                     'status': r.get_status_display(), 'waiting_for': uname(st.approver_user_id) if st else '', 'via': r.get_reimburse_via_display() if r.reimburse_via else '',
                     'reimbursed_on': r.reimbursed_on, '_m': r._meta.label, '_id': r.pk})
    rows.sort(key=lambda x: x['date'] or date.min, reverse=True)
    return [('number', 'Report', 'text')] + EMP_COLS + [('title', 'Title', 'text'), ('date', 'Date', 'date'), ('total', 'Total (AED)', 'number'),
                                                         ('advance', 'Advance used (AED)', 'number'), ('to_reimburse', 'To reimburse (AED)', 'number'),
                                                         ('reimbursed', 'Reimbursed (AED)', 'number'), ('status', 'Status', 'text'), ('waiting_for', 'Waiting for', 'text'),
                                                         ('via', 'Reimburse via', 'text'), ('reimbursed_on', 'Reimbursed on', 'date')], rows, \
        {'filters': ['status', 'branch', 'department']}


def r_expense_advances(run):
    """Expense advances with what is still open: outstanding = paid and not yet used against expense reports."""
    from .reports import EMP_COLS, emp_cols
    EAdv = _m('ExpenseManagement.ExpenseAdvance')
    if EAdv is None:
        return [], [], {}
    from ExpenseManagement.services import advance_balance
    emps = {e.pk: e for e in run.all_employees}
    rows = []
    for a in EAdv.objects.filter(employee_id__in=list(emps)).prefetch_related('applications'):
        bal = money(advance_balance(a)) if a.status == 'paid' else 0
        rows.append({**emp_cols(emps[a.employee_id]), 'number': a.number, 'date': a.date, 'purpose': a.purpose, 'amount': money(a.amount),
                     'balance': bal, 'status': a.get_status_display(), 'paid_on': a.paid_on, 'outstanding': 'Yes' if bal > 0 else 'No',
                     '_m': a._meta.label, '_id': a.pk})
    rows.sort(key=lambda x: x['date'] or date.min, reverse=True)
    return [('number', 'Advance', 'text')] + EMP_COLS + [('date', 'Date', 'date'), ('purpose', 'Purpose', 'text'), ('amount', 'Amount (AED)', 'number'),
                                                         ('balance', 'Not yet used (AED)', 'number'), ('status', 'Status', 'text'), ('paid_on', 'Paid on', 'date'),
                                                         ('outstanding', 'Outstanding', 'text')], rows, {'filters': ['status', 'outstanding', 'branch']}


# ====================================================================================================== leavers' clearance
OPEN_LOANS = ('Approved', 'Disbursed', 'In Progress', 'Paused')


def r_leaver_clearance(run):
    """Employees leaving (approved resignation, on notice, or a leaving date ahead / in the last 30 days) who still hold company
    assets, have an asset return open or a loan not repaid."""
    from .reports import EMP_COLS, emp_cols
    today = run.today
    since = today - timedelta(days=30)
    emps = {e.pk: e for e in run.all_employees}
    leaving = {}
    RES = _m('EmpManagement.EmployeeResignation')
    for r in RES.objects.filter(employee_id__in=list(emps), status__iexact='approved'):
        d = r.last_working_date
        if d is None or d >= since:
            leaving[r.employee_id] = (d, 'Resignation approved', r)
    EI = _m('EmployeeProfile.EmploymentInfo')
    if EI is not None:
        for i in EI.objects.filter(employee_id__in=list(emps)):
            if i.employee_id in leaving:
                if leaving[i.employee_id][0] is None and i.date_of_leaving:
                    leaving[i.employee_id] = (i.date_of_leaving,) + leaving[i.employee_id][1:]
                continue
            if i.status == 'on_notice':
                leaving[i.employee_id] = (i.date_of_leaving, 'On notice', None)
            elif i.date_of_leaving and i.date_of_leaving >= since and i.status in ('active', 'probation', 'on_notice', 'left', 'terminated', 'retired', 'absconded'):
                leaving[i.employee_id] = (i.date_of_leaving, 'Leaving date set', None)
    if not leaving:
        return _lc_cols(EMP_COLS), [], {'filters': ['assets', 'loans', 'branch']}
    AA = _m('OrganisationManager.AssetAllocation')
    held = defaultdict(list)
    for a in AA.objects.filter(employee_id__in=list(leaving), returned_date__isnull=True).select_related('asset'):
        held[a.employee_id].append(a.asset.name)
    ret = Counter()
    AR = _m('AssetPlus.AssetReturn')
    if AR is not None:
        ret = Counter(AR.objects.filter(employee_id__in=list(leaving), status='requested').values_list('employee_id', flat=True))
    LA = _m('PayrollManagement.LoanApplication')
    loans = defaultdict(float)
    nloans = Counter()
    for l in LA.objects.filter(employee_id__in=list(leaving), status__in=OPEN_LOANS):
        if float(l.remaining_balance or 0) > 0:
            loans[l.employee_id] += float(l.remaining_balance or 0)
            nloans[l.employee_id] += 1
    rows = []
    for eid, (d, why, res) in leaving.items():
        if not (held.get(eid) or ret.get(eid) or nloans.get(eid)):
            continue
        e = emps[eid]
        rows.append({**emp_cols(e), 'last_day': d, 'days_left': (d - today).days if d else '', 'reason': why, 'assets_held': len(held.get(eid, [])),
                     'asset_names': ', '.join(held.get(eid, [])), 'returns_open': ret.get(eid, 0), 'loans_open': nloans.get(eid, 0),
                     'loan_balance': round(loans.get(eid, 0), 2), 'assets': 'Yes' if (held.get(eid) or ret.get(eid)) else 'No',
                     'loans': 'Yes' if nloans.get(eid) else 'No', '_m': 'EmpManagement.emp_master', '_id': eid})
    rows.sort(key=lambda r: (r['last_day'] is None, r['last_day'] or date.max))
    return _lc_cols(EMP_COLS), rows, {'filters': ['assets', 'loans', 'reason', 'branch'], 'no_totals': ['days_left']}


def _lc_cols(EMP_COLS):
    return EMP_COLS + [('last_day', 'Last working day', 'date'), ('days_left', 'Days left', 'number'), ('reason', 'Why', 'text'),
                       ('assets_held', 'Assets held', 'number'), ('asset_names', 'Assets', 'text'), ('returns_open', 'Returns open', 'number'),
                       ('loans_open', 'Open loans', 'number'), ('loan_balance', 'Loan balance (AED)', 'number'), ('assets', 'Assets to return', 'text'),
                       ('loans', 'Loan to settle', 'text')]


# ====================================================================================================== report registration
REPORTS = {
    'policy-acks': ('Policy acknowledgements', 'People', 'Every employee and company policy that needs acknowledgement: acknowledged, pending or overdue.',
                    r_policy_acks, None, ['view_companypolicy', 'change_companypolicy', 'view_policyacknowledgement']),
    'expense-reports': ('Expense reports', 'Payroll', 'Expense reports with status, the approver they wait for, the amount to reimburse and what was reimbursed.',
                        r_expense_reports, None, ['view_expensereport', 'reimburse_expensereport', 'change_expensereport']),
    'expense-advances': ('Expense advances', 'Payroll', 'Expense advances with status and the part not yet used against expense reports.',
                         r_expense_advances, None, ['view_expenseadvance', 'change_expenseadvance']),
    'leaver-clearance': ('Leavers to clear', 'Requests & exits', 'Employees leaving who still hold company assets, have an asset return open or a loan to settle.',
                         r_leaver_clearance, None, ['view_employeeresignation', 'view_emp_master', 'view_assetallocation']),
}
REPORT_MODELS = {'policy-acks': {'EmpManagement.emp_master'}, 'expense-reports': {'ExpenseManagement.ExpenseReport'},
                 'expense-advances': {'ExpenseManagement.ExpenseAdvance'}, 'leaver-clearance': {'EmpManagement.emp_master'}}


def register(registry, models):
    for k, spec in REPORTS.items():
        registry[k] = spec
    models.update(REPORT_MODELS)
    if 'requests-pending' in models:   # the approvals queue lists the newer modules: their records open from it
        models['requests-pending'] = set(models['requests-pending']) | QUEUE_MODELS
    spec = registry.get('expense-reports')   # the Management expense tile opens it
    if spec and 'view_ceo_dashboard' not in spec[5]:
        registry['expense-reports'] = tuple(spec[:5]) + (list(spec[5]) + ['view_ceo_dashboard'],)
