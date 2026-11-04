"""v1.52.0 – out of office: between two dates the approvals of a user go to a colleague.

* the approvals already waiting are handed over when the period starts (when include_pending), the new ones as they are
  made – on every approval type of HRMS (leave, late in / early out, general and document requests, assets, loans,
  advance salary, air tickets, resignations) and the Automation approval steps;
* the approver stays the approver: the colleague acts for them (the existing delegation of each approval).

v1.62.11 (item 95):
* automatic out of office – when the company switched it on (Settings › Out of office rules, CompanyOption 'ooo_auto'), the
  final approval of the leave of a user who approves anything makes an out-of-office row for the leave dates (source
  'leave', linked to the leave request); a later cancellation / rejection cancels it, a shorter or longer leave moves its end;
* give back – when the period ends (the day after the last day, by the daily job; at once when the approver ends it early or
  the leave is cancelled / shortened), every approval this row handed over that is still waiting goes back to the approver.
  Only the rows listed in `handed` ({kind: {approval id: colleague id}}) come back, and only while they still wait for that
  same colleague – a delegation made by hand, or a decision of the colleague, stays;
* a different colleague per approval type (and per leave type for leave approvals). Who gets one approval, the first that
  can take it (not the approver, not away himself on that day):
    1. the approver's colleague for that leave type          (row rules / "My stand-ins")
    2. the approver's colleague for that approval type       (row rules / "My stand-ins")
    3. the stand-in HR set on that level of the workflow      (StandIn scope 'level')
    4. the default colleague of the row (the leave request's "Approvals go to", for an automatic row)
    5. the approver's own manager
    6. the approver of the next level of the same workflow, then HR (branch HR, company HR, a company administrator)
  A colleague without the right to approve that type is only warned about when the row is saved (the existing delegate
  actions of every approval accept the delegate without that right).
* payslip approvals have no delegation fields (core model, no core migration): the approver of a waiting payslip step is
  changed to the colleague and changed back when the approver returns.
"""
import logging
from datetime import date, timedelta

from django.apps import apps
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

log = logging.getLogger(__name__)

OPT_KEY = 'ooo_auto'   # v1.62.11 (item 95)
DEFAULTS = {'on': False, 'to': 'named', 'types': 'full_days', 'type_ids': [], 'give_back': True}
# approval kind → level model of its workflow (the workflow model is the level's `workflow`) and the request's type field
LEVELS = {'leave': ('calendars.LeaveApprovalLevels', 'leave_type_id'), 'latein': ('calendars.LateinEarlyoutApprovalLevel', None),
          'general': ('EmpManagement.ApprovalLevel', 'request_type_id'), 'document': ('EmpManagement.DocumentApprovalLevel', 'request_type_id'),
          'asset': ('OrganisationManager.AssetApprovalLevel', 'asset_type_id'), 'loan': ('PayrollManagement.LoanApprovalLevels', 'loan_type_id'),
          'advance': ('PayrollManagement.AdvanceCommonWorkflow', None), 'airticket': ('PayrollManagement.AirticketWorkflow', None),
          'resignation': ('EmpManagement.ResignationApprovalLevel', None), 'payslip': ('PayrollManagement.PayslipCommonWorkflow', None)}
# the rights that let a user approve the kind (only for a warning)
RIGHTS = {'leave': ('approver_leaveapproval', 'view_leaveapproval', 'role_line_manager'), 'latein': ('approver_lateinearlyoutapproval', 'view_lateinearlyoutapproval'),
          'general': ('approver_approval', 'view_approval'), 'document': ('approver_documentapproval', 'view_documentapproval'),
          'asset': ('approver_assetapproval', 'view_assetapproval'), 'loan': ('approver_loanapproval', 'view_loanapproval'),
          'advance': ('approver_advancesalaryapproval', 'view_advancesalaryapproval'), 'airticket': ('approver_airticketapproval', 'view_airticketapproval'),
          'resignation': ('approver_resignationapproval', 'view_resignationapproval'), 'payslip': ('approver_payslipapproval', 'view_payslipapproval')}
FLOW, PAYSLIP = 'flow', 'payslip'


# ------------------------------------------------------------------ registry
def approval_models():
    """[(key, label, approval model)] of the HRMS approvals with the delegation fields (REQUESTS)."""
    out = []
    try:
        from DashboardManagement.services import REQUESTS
        for key, label, rm, am, fk, dfield, summ in REQUESTS:
            try:
                out.append((key, label, apps.get_model(*am.split('.'))))
            except LookupError:
                continue
    except Exception:
        log.exception('approval registry')
    return out


def kinds():
    """v1.62.11 (item 95): every approval kind out of office handles: [(key, label, model, request fk, request model label)]."""
    out = []
    try:
        from DashboardManagement.services import REQUESTS
        for key, label, rm, am, fk, dfield, summ in REQUESTS:
            try:
                out.append((key, label, apps.get_model(*am.split('.')), fk, rm))
            except LookupError:
                continue
    except Exception:
        log.exception('approval registry')
    for key, label, am, fk, rm in ((PAYSLIP, 'Payslip approval', 'PayrollManagement.PayslipApproval', 'request', 'PayrollManagement.Payslip'),
                                   (FLOW, 'Automation approvals', 'ZeoFlow.ApprovalStep', 'request', 'ZeoFlow.ApprovalRequest')):
        try:
            out.append((key, label, apps.get_model(*am.split('.')), fk, rm))
        except LookupError:
            pass
    return out


def kind_of_model(model):
    for k, label, M, fk, rm in kinds():
        if M is model:
            return k
    return None


def _waiting(kind, M, user_id):
    """The approvals of user_id waiting and not delegated yet."""
    if kind == FLOW:
        return M.objects.filter(approver_id=user_id, status__in=('waiting', 'pending'), delegate__isnull=True)
    if kind == PAYSLIP:
        return M.objects.filter(approver_id=user_id, status='Pending')
    return M.objects.filter(approver_id=user_id, status__iexact='pending', is_deligate=False)


# ------------------------------------------------------------------ company setting
def settings_get():
    try:
        CO = apps.get_model('ZeoApps', 'CompanyOption')
        o = CO.objects.filter(key=OPT_KEY).first()
        v = dict(DEFAULTS, **((o.value or {}) if o else {}))
    except Exception:
        v = dict(DEFAULTS)
    v['type_ids'] = [int(x) for x in (v.get('type_ids') or []) if str(x).isdigit()]
    return v


def settings_set(data):
    CO = apps.get_model('ZeoApps', 'CompanyOption')
    cur = settings_get()
    for k in DEFAULTS:
        if k in data:
            cur[k] = data[k]
    cur['on'], cur['give_back'] = bool(cur['on']), bool(cur['give_back'])
    if cur['to'] not in ('named', 'manager', 'hr'):
        cur['to'] = 'named'
    if cur['types'] not in ('full_days', 'all', 'some'):
        cur['types'] = 'full_days'
    cur['type_ids'] = [int(x) for x in (cur.get('type_ids') or []) if str(x).isdigit()]
    CO.objects.update_or_create(key=OPT_KEY, defaults={'value': cur})
    return cur


def leave_counts(req, s=None):
    """The leave type / length the company's setting makes an out of office for."""
    s = s or settings_get()
    if s['types'] == 'all':
        return True
    if s['types'] == 'some':
        return req.leave_type_id in s['type_ids']
    lt = getattr(req, 'leave_type', None)
    return not req.dis_half_day and getattr(lt, 'unit', 'days') != 'hours'


# ------------------------------------------------------------------ people
def _U():
    return apps.get_model('UserManagement', 'CustomUser')


def emp_of(user_id):
    E = apps.get_model('EmpManagement', 'emp_master')
    return E.objects.filter(users_id=user_id).select_related('emp_branch_id').first() if user_id else None


def is_approver(user):
    """Approves anything: named on a workflow level, has approval rows, a stand-in of nobody counts not, a reporting manager."""
    uid = getattr(user, 'pk', user)
    if not uid:
        return False
    for k, label, M, fk, rm in kinds():
        try:
            if M.objects.filter(approver_id=uid).exists():
                return True
        except Exception:
            pass
    for k, (lm, tf) in LEVELS.items():
        try:
            if apps.get_model(*lm.split('.')).objects.filter(approver_id=uid).exists():
                return True
        except Exception:
            pass
    try:
        if apps.get_model('EmpManagement', 'emp_master').objects.filter(emp_reporting_manager_id=uid, is_active=True).exists():
            return True
    except Exception:
        pass
    try:
        if apps.get_model('LeavePolicy', 'OrgRole').objects.filter(user_id=uid).exists():
            return True
    except Exception:
        pass
    return False


def user_codes(user_id):
    """(is company admin, permission codes) of a user in this company."""
    try:
        from tenant_users.tenants.models import UserTenantPermissions
        u = _U().objects.filter(pk=user_id).first()
        if u is None:
            return False, set()
        utp = UserTenantPermissions.objects.filter(profile=u).prefetch_related('groups__permissions').first()
        codes = set()
        if utp:
            for g in utp.groups.all():
                codes.update(p.codename for p in g.permissions.all())
        return bool(u.is_superuser or (utp and utp.is_superuser)), codes
    except Exception:
        return False, set()


def can_approve(user_id, kind):
    if kind == FLOW:
        return True
    admin, codes = user_codes(user_id)
    if admin or codes & set(RIGHTS.get(kind, ())):
        return True
    lm = LEVELS.get(kind)
    try:
        if lm and apps.get_model(*lm[0].split('.')).objects.filter(approver_id=user_id).exists():
            return True
    except Exception:
        pass
    return False


def current(user_id, day=None):
    from .models import OutOfOffice
    day = day or date.today()
    return OutOfOffice.objects.filter(user_id=user_id, active=True, waiting_leave=False, start_date__lte=day, end_date__gte=day) \
        .select_related('delegate', 'user').first()


def away(user_id, day=None):
    return current(user_id, day) is not None


def manager_of(user_id):
    try:
        from LeavePolicy.approvers import holder
        u = holder('reporting_manager', emp_of(user_id))
        return u.pk if u is not None else None
    except Exception:
        return None


def hr_chain(user_id):
    out = []
    try:
        from LeavePolicy.approvers import fallback_chain
        for how, u in fallback_chain(emp_of(user_id)):
            if u is not None:
                out.append(u.pk)
    except Exception:
        pass
    return out


# ------------------------------------------------------------------ workflow levels
def _request(kind, a):
    fk = {k: f for k, l, M, f, rm in kinds()}.get(kind)
    return getattr(a, fk, None) if fk else None


def workflow_of(kind, a):
    """The workflow (of the level models) the approval's request follows – type + the employee's branch, else the type's first."""
    lm = LEVELS.get(kind)
    if not lm:
        return None
    try:
        LM = apps.get_model(*lm[0].split('.'))
        WF = LM._meta.get_field('workflow').related_model
        r = _request(kind, a)
        if r is None:
            return None
        qs = WF.objects.all()
        if lm[1]:
            tf = next((f for f in ('request_type', 'loan_type', 'asset_type') if any(x.name == f for x in WF._meta.get_fields())), None)
            if tf:
                qs = qs.filter(**{f'{tf}_id': getattr(r, lm[1], None)})
        emp = getattr(r, 'employee', None)
        br = getattr(emp, 'emp_branch_id_id', None) if emp is not None else None
        return (qs.filter(branch=br).first() if br else None) or qs.first()
    except Exception:
        log.debug('workflow of %s', kind, exc_info=True)
        return None


def _level_model(kind):
    lm = LEVELS.get(kind)
    return apps.get_model(*lm[0].split('.')) if lm else None


def level_standin(kind, a):
    from .models import StandIn
    wf = workflow_of(kind, a)
    if wf is None:
        return None
    s = StandIn.objects.filter(scope='level', kind=kind, workflow_id=wf.pk, level_no=getattr(a, 'level', None)).first()
    return s.delegate_id if s else None


def next_level_approver(kind, a):
    wf = workflow_of(kind, a)
    if wf is None:
        return None
    try:
        n = _level_model(kind).objects.filter(workflow=wf, level__gt=getattr(a, 'level', 0) or 0).order_by('level').first()
        return n.approver_id if n else None
    except Exception:
        return None


# ------------------------------------------------------------------ who gets one approval
def leave_type_of(kind, a):
    if kind != 'leave':
        return None
    r = getattr(a, 'leave_request', None)
    return r.leave_type_id if r is not None else None


def candidates(o, kind, a=None, lt=None):
    """[(colleague id, why)] in the order of the module docstring."""
    rules = o.rules or []
    out = []
    if kind == 'leave' and lt:
        out += [(r.get('delegate_id'), 'leave type') for r in rules if r.get('kind') == 'leave' and r.get('leave_type_id') == lt]
    out += [(r.get('delegate_id'), 'approval type') for r in rules if r.get('kind') == kind and not r.get('leave_type_id')]
    if a is not None:
        out.append((level_standin(kind, a), 'workflow level'))
    out.append((o.delegate_id, 'default'))
    out.append((manager_of(o.user_id), 'manager'))
    if a is not None:
        out.append((next_level_approver(kind, a), 'next level'))
    out += [(u, 'hr') for u in hr_chain(o.user_id)]
    return out


def pick(o, kind, a=None, day=None):
    """The colleague who gets this approval (None: nobody can take it – it stays with the approver)."""
    day = day or date.today()
    lt = leave_type_of(kind, a) if a is not None else None
    for uid, why in candidates(o, kind, a, lt):
        if not uid or uid == o.user_id:
            continue
        if a is not None and _employee_user(kind, a) == uid:   # never the employee who asked
            continue
        if away(uid, day):
            continue
        return uid
    return None


def _employee_user(kind, a):
    r = _request(kind, a)
    e = getattr(r, 'employee', None) if r is not None else None
    return getattr(e, 'users_id', None) if e is not None else None


# ------------------------------------------------------------------ hand over / give back
def _note(o):
    return f'Out of office {o.start_date:%d/%m} – {o.end_date:%d/%m}' + (f': {o.note}' if o.note else '')


def _move(kind, M, a, uid, o):
    if kind == FLOW:
        return M.objects.filter(pk=a.pk, delegate__isnull=True).update(delegate_id=uid)
    if kind == PAYSLIP:
        return M.objects.filter(pk=a.pk, approver_id=o.user_id, status='Pending').update(approver_id=uid)
    return M.objects.filter(pk=a.pk, is_deligate=False).update(deligate_to_id=uid, is_deligate=True, deligate_response=_note(o)[:500])


def _record(o, kind, pairs):
    """Add {approval id: colleague id} to the row's list (the row re-read under a lock)."""
    if not pairs:
        return
    from .models import OutOfOffice
    with transaction.atomic():
        x = OutOfOffice.objects.select_for_update().get(pk=o.pk)
        h = dict(x.handed or {})
        m = dict(h.get(kind) or {})
        if isinstance(h.get(kind), list):   # (never stored so, kept safe)
            m = {str(i): x.delegate_id for i in h[kind]}
        for aid, uid in pairs:
            m[str(aid)] = uid
        h[kind] = m
        x.handed = h
        x.moved = (x.moved or 0) + len(pairs)
        x.save(update_fields=['handed', 'moved'])
        o.handed, o.moved = x.handed, x.moved


def hand_over(o, only_ids=None):
    """Hand the waiting approvals of o.user to the colleagues of the row. Returns the number handed over."""
    n = 0
    for kind, label, M, fk, rm in kinds():
        pairs = []
        try:
            qs = _waiting(kind, M, o.user_id)
            if only_ids is not None:
                qs = qs.filter(pk__in=only_ids)
            for a in qs.select_related(fk) if kind not in (FLOW,) else qs:
                uid = pick(o, kind, a)
                if uid and _move(kind, M, a, uid, o):
                    pairs.append((a.pk, uid))
        except Exception:
            log.exception('out of office %s', kind)
        _record(o, kind, pairs)
        n += len(pairs)
    return n


def _names(ids):
    return {u.pk: (u.get_full_name() or u.username) for u in _U().objects.filter(pk__in=[i for i in ids if i])}


def _history(rm, req_id, text):
    try:
        from Chatter.models import Message
        Message.objects.create(model=rm, object_id=str(req_id), body=text, kind='note')
    except Exception:
        log.debug('history note', exc_info=True)


def give_back(o, why='back'):
    """The approvals this row handed over that still wait for the same colleague go back to the approver. Returns the number."""
    from .models import OutOfOffice
    from Translations.i18n import company_default_lang, for_lang, msg
    who = o.user.get_full_name() or o.user.username
    note = for_lang(msg('Back from out of office – returned to {0}', who), company_default_lang())
    n = 0
    for kind, label, M, fk, rm in kinds():
        m = (o.handed or {}).get(kind) or {}
        if not m:
            continue
        try:
            for a in M.objects.filter(pk__in=[int(i) for i in m.keys()]):
                to = m.get(str(a.pk))
                if kind == FLOW:
                    ok = a.status in ('waiting', 'pending') and a.approver_id == o.user_id and a.delegate_id == to and \
                        M.objects.filter(pk=a.pk, delegate_id=to).update(delegate_id=None)
                elif kind == PAYSLIP:
                    ok = a.status == 'Pending' and a.approver_id == to and M.objects.filter(pk=a.pk, approver_id=to).update(approver_id=o.user_id)
                else:
                    ok = str(a.status).lower() == 'pending' and a.approver_id == o.user_id and a.is_deligate and a.deligate_to_id == to and \
                        M.objects.filter(pk=a.pk, deligate_to_id=to, is_deligate=True).update(deligate_to_id=None, is_deligate=False, deligate_response=note[:500])
                if ok:
                    n += 1
                    rid = getattr(a, f'{fk}_id', None)
                    if rid:
                        _history(rm, rid, note)
        except Exception:
            log.exception('give back %s', kind)
    OutOfOffice.objects.filter(pk=o.pk).update(returned=(o.returned or 0) + n, returned_at=timezone.now())
    o.returned, o.returned_at = (o.returned or 0) + n, timezone.now()
    if n:
        try:
            from zeo.module_helpers import notify
            notify(user=o.user, title=msg('Back from out of office'),
                   message=msg('Welcome back. {0} approval(s) that were still waiting came back to you.', n))
        except Exception:
            pass
    return n


def start(o, user=None):
    """The period starts (today within it): hand over what is waiting and tell the colleague."""
    if not o.active or o.waiting_leave or not (o.start_date <= date.today() <= o.end_date):
        return 0
    n = hand_over(o) if o.include_pending else 0
    try:
        from zeo.module_helpers import notify
        from Translations.i18n import msg
        who = o.user.get_full_name() or o.user.username
        to = {o.delegate_id} | {r.get('delegate_id') for r in (o.rules or []) if r.get('delegate_id')}
        for kind_map in (o.handed or {}).values():
            to |= set((kind_map or {}).values()) if isinstance(kind_map, dict) else set()
        for u in _U().objects.filter(pk__in=[x for x in to if x]):
            notify(user=u, title=msg('You approve for {0}', who),
                   message=msg('{0} is out of office from {1} to {2}; their approvals come to you.', who, f'{o.start_date:%d/%m/%Y}', f'{o.end_date:%d/%m/%Y}')
                   + (f' ({n})' if n else ''))
    except Exception:
        pass
    return n


def on_new_approval(instance):
    """post_save of a new approval: its approver is out of office today → the colleague gets it."""
    kind = kind_of_model(type(instance))
    uid = getattr(instance, 'approver_id', None)
    if not uid or not kind:
        return
    if kind == PAYSLIP:
        if instance.status != 'Pending':
            return
    elif getattr(instance, 'is_deligate', False) or str(getattr(instance, 'status', '')).lower() != 'pending':
        return
    o = current(uid)
    if o is None:
        return
    to = pick(o, kind, instance)
    if to and _move(kind, type(instance), instance, to, o):
        _record(o, kind, [(instance.pk, to)])


def on_new_step(instance):
    uid = getattr(instance, 'approver_id', None)
    if not uid or instance.delegate_id or instance.status not in ('waiting', 'pending'):
        return
    o = current(uid)
    if o is None:
        return
    to = pick(o, FLOW, instance)
    if to and type(instance).objects.filter(pk=instance.pk, delegate__isnull=True).update(delegate_id=to):
        _record(o, FLOW, [(instance.pk, to)])


def end(o, day=None):
    """The approver is back (or the period was cut): end the row now and give back."""
    day = day or date.today()
    from .models import OutOfOffice
    o.active, o.end_date = False, max(o.start_date, min(o.end_date, day - timedelta(days=1)))
    OutOfOffice.objects.filter(pk=o.pk).update(active=False, end_date=o.end_date)
    return give_back(o) if o.give_back and not o.returned_at else 0


def daily():
    """Periods starting today hand over what is waiting; periods that ended yesterday give back (v1.62.11 item 95)."""
    from .models import OutOfOffice
    t = date.today()
    n = 0
    for o in OutOfOffice.objects.filter(active=True, waiting_leave=False, start_date=t).select_related('user', 'delegate'):
        n += start(o)
    back = 0
    for o in OutOfOffice.objects.filter(active=True, waiting_leave=False, end_date__lt=t, returned_at__isnull=True).select_related('user', 'delegate'):
        if o.give_back:
            back += give_back(o)
        else:
            OutOfOffice.objects.filter(pk=o.pk).update(returned_at=timezone.now())
    return {'started': n, 'given_back': back}


# ------------------------------------------------------------------ checks
def check(user, delegate, a, b, exclude_id=None, rules=None):
    """'' when the row can be saved, else the problem (rules: the colleagues per approval type)."""
    from .models import OutOfOffice
    if delegate is None:
        return 'Choose the colleague who approves for you.'
    if delegate.pk == user.pk:
        return 'Choose someone else.'
    if a is None or b is None or b < a:
        return 'Give the first and the last day (the last on or after the first).'
    clash = OutOfOffice.objects.filter(user=user, active=True, waiting_leave=False, start_date__lte=b, end_date__gte=a).exclude(pk=exclude_id or 0)
    if clash.exists():
        return 'You already have an out-of-office period on these days.'
    for uid in [delegate.pk] + [r.get('delegate_id') for r in (rules or [])]:
        if not uid:
            return 'Choose the colleague for each approval type you added.'
        if uid == user.pk:
            return 'Choose someone else.'
        if OutOfOffice.objects.filter(user_id=uid, active=True, waiting_leave=False, start_date__lte=b, end_date__gte=a).exclude(pk=exclude_id or 0).exists():
            from Translations.i18n import msg
            return msg('{0} is out of office on these days too. Choose another colleague.', _names([uid]).get(uid, ''))
    return ''


def warnings(rules, delegate_id):
    """The colleagues who have no right to approve the type they get (saved anyway)."""
    out = []
    names = _names([delegate_id] + [r.get('delegate_id') for r in rules or []])
    labels = {k: l for k, l, M, fk, rm in kinds()}
    for r in rules or []:
        if r.get('delegate_id') and not can_approve(r['delegate_id'], r.get('kind')):
            out.append({'kind': r.get('kind'), 'delegate_id': r['delegate_id'], 'name': names.get(r['delegate_id'], ''), 'label': labels.get(r.get('kind'), r.get('kind'))})
    return out


def clean_rules(raw):
    keys = {k for k, l, M, fk, rm in kinds()}
    out, seen = [], set()
    for r in raw or []:
        try:
            k = str(r.get('kind') or '')
            lt = int(r['leave_type_id']) if r.get('leave_type_id') not in (None, '', 0, '0') else None
            d = int(r['delegate_id']) if r.get('delegate_id') not in (None, '') else None
        except (TypeError, ValueError, AttributeError):
            continue
        if k not in keys or (lt and k != 'leave') or (k, lt) in seen:
            continue
        seen.add((k, lt))
        out.append({'kind': k, 'leave_type_id': lt, 'delegate_id': d})
    return out


def my_standins(user_id):
    """("My stand-ins") the default colleague id and the rules saved for next time."""
    from .models import StandIn
    d, rules = None, []
    for s in StandIn.objects.filter(scope='mine', user_id=user_id):
        if not s.kind:
            d = s.delegate_id
        else:
            rules.append({'kind': s.kind, 'leave_type_id': s.leave_type_id, 'delegate_id': s.delegate_id})
    return d, rules


def save_my_standins(user, delegate_id, rules, by=None):
    from .models import StandIn
    StandIn.objects.filter(scope='mine', user=user).delete()
    if delegate_id:
        StandIn.objects.create(scope='mine', user=user, kind='', delegate_id=delegate_id, updated_by=by)
    for r in rules:
        if r.get('delegate_id'):
            StandIn.objects.create(scope='mine', user=user, kind=r['kind'], leave_type_id=r.get('leave_type_id'), delegate_id=r['delegate_id'], updated_by=by)


# ------------------------------------------------------------------ automatic out of office from a leave (v1.62.11 item 95)
def _leave_user(req):
    e = req.employee
    return getattr(e, 'users', None) if e is not None else None


def default_for_leave(user_id, named_id=None, s=None):
    """The default colleague of an automatic row: the setting's order (named → manager → HR), never someone away."""
    s = s or settings_get()
    mine, _ = my_standins(user_id)
    order = []
    if s['to'] == 'named':
        order += [named_id, mine]
    if s['to'] in ('named', 'manager'):
        order.append(manager_of(user_id))
    order += hr_chain(user_id)
    return next((u for u in order if u and u != user_id), None)


def leave_approved(req):
    """The leave was approved (the last level): make the out-of-office row of the leave days."""
    from .models import OutOfOffice
    from Translations.i18n import msg
    s = settings_get()
    user = _leave_user(req)
    waiting = OutOfOffice.objects.filter(leave_request_id=req.pk, waiting_leave=True).first()
    if not s['on'] or user is None or not leave_counts(req, s) or not is_approver(user):
        if waiting:
            waiting.delete()
        return None
    if OutOfOffice.objects.filter(leave_request_id=req.pk, source='leave', waiting_leave=False, cancelled=False).exists():
        return None   # already made (the request is saved twice inside one approval)
    a, b = req.start_date, req.end_date
    if OutOfOffice.objects.filter(user=user, active=True, waiting_leave=False, start_date__lte=b, end_date__gte=a).exists():
        if waiting:
            waiting.delete()
        return None   # the approver set it already for these days
    named = waiting.delegate_id if waiting else None
    d = default_for_leave(user.pk, named, s)
    if d is None:
        if waiting:
            waiting.delete()
        return None
    _, mine_rules = my_standins(user.pk)
    rules = (waiting.rules if waiting and waiting.rules else None) or mine_rules
    o = waiting or OutOfOffice(user=user, source='leave', leave_request_id=req.pk)
    o.delegate_id, o.start_date, o.end_date, o.rules = d, a, b, rules
    o.source, o.leave_request_id, o.leave_number, o.waiting_leave = 'leave', req.pk, req.document_number or '', False
    o.active, o.include_pending, o.give_back, o.cancelled = True, True, bool(s['give_back']), False
    o.note = (o.note or f'{req.leave_type.name if req.leave_type_id else "Leave"} {req.document_number or ""}'.strip())[:255]
    o.save()
    n = start(o) if a <= date.today() <= b else 0
    try:
        from zeo.module_helpers import notify
        dn = _names([d]).get(d, '')
        notify(user=user, title=msg('Out of office set for your leave'),
               message=msg('Your leave {0} is approved. From {1} to {2} your approvals go to {3}.', req.document_number or '', f'{a:%d/%m/%Y}', f'{b:%d/%m/%Y}', dn))
        if n == 0 and a > date.today():   # the colleague hears it now too (start() tells them when it starts today)
            who = user.get_full_name() or user.username
            for u in _U().objects.filter(pk__in={d} | {r.get('delegate_id') for r in rules if r.get('delegate_id')}):
                notify(user=u, title=msg('You approve for {0}', who),
                       message=msg('{0} is out of office from {1} to {2}; their approvals come to you.', who, f'{a:%d/%m/%Y}', f'{b:%d/%m/%Y}'))
    except Exception:
        pass
    return o


def leave_changed(req, was, old):
    """post_save of a leave request: approved → row; cancelled / rejected → row cancelled and approvals back; dates moved."""
    from .models import OutOfOffice
    now = req.status
    if now == 'approved' and was != 'approved':
        return leave_approved(req)
    rows = list(OutOfOffice.objects.filter(leave_request_id=req.pk, cancelled=False).select_related('user', 'delegate'))
    if not rows:
        return None
    for o in rows:
        if o.waiting_leave:
            if now == 'rejected':
                o.delete()
            continue
        if now != 'approved':   # cancelled / rejected after approval
            cancel(o)
        elif old and (old.get('start_date'), old.get('end_date')) != (req.start_date, req.end_date):
            moved(o, req.start_date, req.end_date)
    return None


def cancel(o):
    from .models import OutOfOffice
    from Translations.i18n import msg
    OutOfOffice.objects.filter(pk=o.pk).update(active=False, cancelled=True)
    o.active, o.cancelled = False, True
    n = give_back(o) if not o.returned_at else 0
    try:
        from zeo.module_helpers import notify
        notify(user=o.user, title=msg('Out of office cancelled'), message=msg('Your leave {0} was cancelled; your approvals are yours again.', o.leave_number))
        who = o.user.get_full_name() or o.user.username
        to = {o.delegate_id} | {r.get('delegate_id') for r in (o.rules or []) if r.get('delegate_id')}
        for u in _U().objects.filter(pk__in=[x for x in to if x]):
            notify(user=u, title=msg('Out of office cancelled'), message=msg('{0} is not away after all; their approvals go back to them.', who))
    except Exception:
        pass
    return n


def moved(o, a, b):
    """The leave's dates changed: the row follows; a leave that now ended gives back at once."""
    from .models import OutOfOffice
    t = date.today()
    OutOfOffice.objects.filter(pk=o.pk).update(start_date=a, end_date=b)
    o.start_date, o.end_date = a, b
    if b < t:
        OutOfOffice.objects.filter(pk=o.pk).update(active=False)
        return give_back(o) if o.give_back and not o.returned_at else 0
    if a <= t:
        return start(o) if not o.handed else 0
    return 0


def leave_cut(req_id, new_end):
    """HR Actions › Return from leave shortened the leave (an update without a save signal)."""
    from .models import OutOfOffice
    for o in OutOfOffice.objects.filter(leave_request_id=req_id, cancelled=False, waiting_leave=False).select_related('user', 'delegate'):
        if new_end < o.end_date:
            moved(o, o.start_date, new_end)


def leave_deleted(req_id):
    from .models import OutOfOffice
    for o in OutOfOffice.objects.filter(leave_request_id=req_id, cancelled=False).select_related('user', 'delegate'):
        if o.waiting_leave:
            o.delete()
        else:
            cancel(o)


def choose_for_leave(req, user, delegate_id, rules=None):
    """The applicant's "Approvals go to" on a leave request: kept on a waiting row until the leave is approved; on a leave
    already approved (no workflow) the automatic row takes the colleague (what it handed over moves to them)."""
    from .models import OutOfOffice
    o = OutOfOffice.objects.filter(leave_request_id=req.pk, cancelled=False).first()
    if o is None:
        if req.status == 'approved':
            return None
        o = OutOfOffice(user=user, source='leave', leave_request_id=req.pk, leave_number=req.document_number or '', waiting_leave=True,
                        active=False, start_date=req.start_date, end_date=req.end_date)
    o.delegate_id = delegate_id
    if rules is not None:
        o.rules = rules
    if o.pk and not o.waiting_leave and o.active:
        o.save(update_fields=['delegate', 'rules'])
        give_back_quiet(o)
        if o.start_date <= date.today() <= o.end_date:
            hand_over(o)
        return o
    o.save()
    return o


def give_back_quiet(o):
    """Take back what the row handed over (to give it again to other colleagues) – no note, no notice."""
    from .models import OutOfOffice
    for kind, label, M, fk, rm in kinds():
        m = (o.handed or {}).get(kind) or {}
        for aid, to in list(m.items()):
            if kind == FLOW:
                M.objects.filter(pk=aid, delegate_id=to, status__in=('waiting', 'pending')).update(delegate_id=None)
            elif kind == PAYSLIP:
                M.objects.filter(pk=aid, approver_id=to, status='Pending').update(approver_id=o.user_id)
            else:
                M.objects.filter(pk=aid, deligate_to_id=to, is_deligate=True, status__iexact='pending').update(deligate_to_id=None, is_deligate=False)
    OutOfOffice.objects.filter(pk=o.pk).update(handed={}, moved=0)
    o.handed, o.moved = {}, 0


def handed_info(user, kind_key=None):
    """For the inbox / review window: {kind: {approval id: {from, to, back_on, leave}}} of the rows handed over to or by user."""
    from .models import OutOfOffice
    out = {}
    qs = OutOfOffice.objects.filter(waiting_leave=False, returned_at__isnull=True, cancelled=False).select_related('user')
    for o in qs:
        for kind, m in (o.handed or {}).items():
            if kind_key and kind != kind_key or not isinstance(m, dict):
                continue
            for aid, to in m.items():
                if to != user.pk and o.user_id != user.pk:
                    continue
                out.setdefault(kind, {})[str(aid)] = {'from': o.user.get_full_name() or o.user.username, 'from_id': o.user_id, 'to_id': to,
                                                      'back_on': (o.end_date + timedelta(days=1)) if o.give_back else None,
                                                      'until': o.end_date, 'leave': o.leave_number, 'ooo_id': o.pk}
    return out
