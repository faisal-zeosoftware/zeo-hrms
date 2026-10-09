"""v1.13.0 – self-service business rules: rights, notifications, profile change requests, dashboard, payslips, announcements."""
import logging
from datetime import date

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from AccessControl.ess_guard import HIDDEN, HR, REQUEST, SELF, policy_map

from . import profile as P
from .models import ChangeRequestAttachment, EssFieldPolicy, ProfileChangeRequest

log = logging.getLogger(__name__)
M = apps.get_model

# rights
CR_HR = ('change_profilechangerequest', 'change_emp_master')          # approve profile changes
SETTINGS_HR = ('change_essfieldpolicy', 'change_emp_master')          # field policy screen
LETTER_HR = ('change_letterrequest', 'change_emp_master', 'change_documentrequest')
TEMPLATE_HR = ('change_lettertemplate', 'change_emp_master')
GRIEVANCE = ('handle_grievance',)
ANNOUNCE_HR = ('view_announcement', 'change_announcement', 'add_announcement', 'view_announcementview')
RELEASED = ('Approved', 'approved', 'paid', 'Paid')


# ------------------------------------------------------------------------------------------------ access
def ctx(request):
    from AccessControl.access import ctx as _c
    return _c(request)


def can(request, *codes):
    try:
        c = ctx(request)
        return c.admin or any(x in c.codes for x in codes)
    except Exception:
        return False


def branch_ok(request, branch_id):
    c = ctx(request)
    if c.admin or c.branches is None:
        return True
    return branch_id is not None and branch_id in c.branches


def branch_q(request, field='branch_id'):
    c = ctx(request)
    if c.admin or c.branches is None:
        return Q()
    return Q(**{f'{field}__in': c.branches})


def my_employee(request):
    try:
        return ctx(request).emp
    except Exception:
        return None


def employee(emp_id):
    return M('EmpManagement', 'emp_master').objects.filter(pk=emp_id).select_related('emp_branch_id', 'users').first()


def users_with(branch_id, codes, exclude=()):
    """Active users holding one of the codes (via their groups) whose branch access covers branch_id; plus company admins
    when nobody else is found."""
    from tenant_users.tenants.models import UserTenantPermissions
    from OrganisationManager.models import UserBranchAccess
    out, admins = [], []
    for utp in UserTenantPermissions.objects.select_related('profile').prefetch_related('groups__permissions'):
        u = utp.profile
        if not u or not u.is_active or u.pk in exclude:
            continue
        if utp.is_superuser:
            admins.append(u)
            continue
        cs = {p.codename for g in utp.groups.all() for p in g.permissions.all()}
        if not cs.intersection(codes):
            continue
        br = [b for b in UserBranchAccess.objects.filter(user=u).values_list('branch__id', flat=True) if b]
        if not br:
            e = M('EmpManagement', 'emp_master').objects.filter(users=u).only('emp_branch_id').first()
            br = [e.emp_branch_id_id] if e and e.emp_branch_id_id else []
        if branch_id is None or branch_id in br:
            out.append(u)
    return out or admins


def notify(user=None, employee=None, title='', message=''):
    try:
        from zeo.module_helpers import notify as _n
        _n(user=user, employee=employee, title=title[:100], message=message, notification_type='general')
    except Exception:
        log.debug('ess notice skipped', exc_info=True)


def next_number(Model, prefix):
    year = timezone.now().year
    base = f'{prefix}-{year}-'
    last = Model.objects.filter(number__startswith=base).order_by('-id').values_list('number', flat=True).first()
    n = 1
    if last:
        try:
            n = int(last.rsplit('-', 1)[1]) + 1
        except (ValueError, IndexError):
            n = Model.objects.filter(number__startswith=base).count() + 1
    return f'{base}{n:04d}'


def uname(user_id):
    if not user_id:
        return ''
    U = M('UserManagement', 'CustomUser')
    u = U.objects.filter(pk=user_id).first()
    if not u:
        return ''
    e = M('EmpManagement', 'emp_master').objects.filter(users=u).first()
    return P.person(e) if e else u.username


def chatter(record, text, user=None):
    """History note on the record (best effort)."""
    try:
        Msg = M('Chatter', 'Message')
        names = {f.name for f in Msg._meta.get_fields()}
        kw = {}
        if 'model' in names:
            kw['model'] = record._meta.label
        if 'object_id' in names:
            kw['object_id'] = record.pk
        if 'body' in names:
            kw['body'] = text
        elif 'text' in names:
            kw['text'] = text
        if 'author' in names and user is not None:
            kw['author'] = user
        Msg.objects.create(**kw)
    except Exception:
        log.debug('chatter note skipped', exc_info=True)


# ------------------------------------------------------------------------------------------------ field policy
def save_policies(rows, user):
    """rows: [{group, field, policy}] – only allowed values; locked fields can only be hr / hidden."""
    from AccessControl.ess_guard import DEFAULT_POLICY, LOCKED, POLICIES
    errors, saved = [], 0
    known = {(r['group'], r['field']) for r in P.policies()}
    for r in rows:
        g, f, p = r.get('group'), r.get('field') or '', r.get('policy')
        if (g, f) not in known:
            errors.append(f'{g}.{f}: unknown field.')
            continue
        if p not in POLICIES:
            errors.append(f'{g}.{f}: choose self, request, hr or hidden.')
            continue
        if ((g, f) in LOCKED or (P.GROUP.get(g) or {}).get('read_only')) and p in (SELF, REQUEST):
            errors.append(f'{g}.{f}: this field is kept by HR – it can only be "hr" or "hidden".')
            continue
        default = DEFAULT_POLICY.get((g, f), DEFAULT_POLICY.get((g, ''), HR) if f else HR)
        if p == default:
            EssFieldPolicy.objects.filter(group=g, field=f).delete()
        else:
            EssFieldPolicy.objects.update_or_create(group=g, field=f, defaults={'policy': p, 'updated_by_id': getattr(user, 'pk', None)})
        saved += 1
    return saved, errors


# ------------------------------------------------------------------------------------------------ profile changes
def _clean_data(group, data):
    keys = {f[0] for g in P.groups_definition() if g['key'] == group for f in g['fields']}
    out = {}
    for k, v in (data or {}).items():
        if k in keys or (group == 'skills' and k == 'kind'):
            out[k] = v
    return out


def submit_change(request, emp, group, action, record_id, data, reason='', files=()):
    """Employee proposes a change. Fields with policy self are applied at once; request fields create a change request;
    hr / hidden fields are refused. Returns {'applied': [...], 'request': ProfileChangeRequest|None}."""
    if group not in P.EDITABLE_GROUPS or not P.group_available(group):
        raise ValidationError({'group': 'This part of your profile cannot be changed from self service.'})
    if action not in ('create', 'update', 'delete'):
        raise ValidationError({'action': 'Choose create, update or delete.'})
    pm = policy_map()
    if group in ('personal', 'contact', 'address', 'identity'):
        if action != 'update':
            raise ValidationError({'action': 'These details can only be changed, not added or removed.'})
        record_id = None
    kind = (data or {}).get('kind') if group == 'skills' else None
    if action in ('update', 'delete') and group in P.RECORD_GROUPS:
        if not record_id or P.get_record(emp, group, record_id, kind) is None:
            raise ValidationError({'record_id': 'This record was not found in your profile.'})
    clean = _clean_data(group, data)
    if action == 'update' and group != 'skills':
        cur = P.old_values(emp, group, record_id, list(clean.keys()), kind)
        clean = {k: v for k, v in clean.items() if not _same(cur.get(k, None), v) or hasattr(v, 'read')}
    if action != 'delete' and not clean and not files:
        raise ValidationError({'data': 'Nothing was changed.'})

    direct, needs, refused = {}, {}, []
    if action in ('create', 'delete'):
        pol = P.effective(group, '', pm)
        if pol == SELF:
            direct = clean
        elif pol == REQUEST:
            needs = clean
        else:
            refused.append('(records)')
        for k in list(clean.keys()):
            if P.effective(group, k, pm) in (HR, HIDDEN) and k != 'kind':
                refused.append(k)
    else:
        for k, v in clean.items():
            pol = P.effective(group, k, pm)
            if k == 'kind':
                direct[k] = v
                needs[k] = v
                continue
            if pol == SELF:
                direct[k] = v
            elif pol == REQUEST:
                needs[k] = v
            else:
                refused.append(k)
    if refused:
        names = ', '.join(refused)
        raise ValidationError({'policy': f'These details are kept by HR and cannot be changed here: {names}. Ask HR if something is wrong.'})
    if group == 'skills' and action == 'update':
        if set(direct) == {'kind'}:
            direct = {}
        if set(needs) == {'kind'}:
            needs = {}
    if files and not needs and action != 'delete':
        needs, direct = {**direct, **needs}, {}  # a new file (e.g. passport copy) is always checked by HR
    if action == 'update' and group in ('personal', 'contact', 'address', 'identity') and not needs and not direct:
        raise ValidationError({'data': 'Nothing was changed.'})

    applied, req = [], None
    user = request.user
    if action == 'delete' and P.effective(group, '', pm) == SELF and not files:
        rid = P.apply_change(emp, group, record_id, {}, user, action)
        return {'applied': ['(record removed)'], 'record_id': record_id, 'request': None}
    if direct and not needs:
        P.validate_change(emp, group, record_id, direct, action)
        rid = P.apply_change(emp, group, record_id, direct, user, action)
        applied = [k for k in direct if k != 'kind'] or ['(record)']
        if action == 'create':
            applied = applied + ['(record added)']
        return {'applied': applied, 'record_id': rid, 'request': None}
    if direct and needs and action == 'update':
        P.validate_change(emp, group, record_id, {**direct}, action)
        P.apply_change(emp, group, record_id, direct, user, action)
        applied = [k for k in direct if k != 'kind']
    if needs or action == 'delete' or files:
        payload = {k: v for k, v in needs.items() if not hasattr(v, 'read')}
        try:
            P.validate_change(emp, group, record_id, payload, action)
        except ValidationError:
            raise
        if action == 'update' and record_id is None and group in P.RECORD_GROUPS:
            raise ValidationError({'record_id': 'Choose the record to change.'})
        dup = ProfileChangeRequest.objects.filter(employee_id=emp.pk, group=group, record_id=record_id, action=action, status='pending')
        if action != 'create' and dup.exists():
            raise ValidationError({'request': f'You already have a pending request ({dup.first().number}) for this – wait for HR or withdraw it first.'})
        with transaction.atomic():
            req = ProfileChangeRequest.objects.create(
                number=next_number(ProfileChangeRequest, 'PCR'), employee_id=emp.pk, branch_id=emp.emp_branch_id_id, group=group,
                record_id=record_id, action=action, old_values=P.old_values(emp, group, record_id, list(payload.keys()) or [f[0] for f in P.GROUP[group]['fields']][:8], kind) if action != 'create' else {},
                new_values=payload, reason=(reason or '')[:500], created_by_id=user.pk)
            for f in files or ():
                ChangeRequestAttachment.objects.create(request=req, file=f, name=getattr(f, 'name', '')[:200], uploaded_by_id=user.pk)
        chatter(req, f'Change request {req.number} created: {req.get_action_display().lower()} {P.GROUP[group]["label"].lower()}.', user)
        for u in users_with(emp.emp_branch_id_id, CR_HR, exclude=(user.pk,)):
            notify(user=u, title='Profile change to review',
                   message=f'{P.person(emp)} asked to {req.get_action_display().lower()} {P.GROUP[group]["label"].lower()} ({req.number}).')
    return {'applied': applied, 'record_id': record_id, 'request': req}


def _same(a, b):
    from AccessControl.ess_guard import _norm
    if isinstance(a, bool) or isinstance(b, bool):
        return _norm(a) == _norm(b)
    try:
        if a not in (None, '') and b not in (None, '') and float(a) == float(b):
            return True
    except (TypeError, ValueError):
        pass
    return _norm(a)[:10] == _norm(b)[:10] if (len(_norm(a)) == 10 and _norm(a)[4:5] == '-') else _norm(a) == _norm(b)


def decide_change(request, req, approve, note=''):
    if req.status != 'pending':
        raise ValidationError({'status': f'This request is already {req.get_status_display().lower()}.'})
    emp = employee(req.employee_id)
    if emp is None:
        raise ValidationError({'employee': 'The employee no longer exists.'})
    if emp.users_id == request.user.pk:
        raise ValidationError({'status': 'You cannot approve or reject your own change request.'})
    if not approve and not (note or '').strip():
        raise ValidationError({'decision_note': 'Give the reason for rejecting, so the employee knows what to fix.'})
    req.decided_by_id, req.decided_at, req.decision_note = request.user.pk, timezone.now(), (note or '')[:500]
    if not approve:
        req.status = 'rejected'
        req.save()
        chatter(req, f'Rejected by {uname(request.user.pk)}: {req.decision_note}', request.user)
        notify(employee=emp, title='Profile change rejected',
               message=f'HR rejected your change request {req.number} ({P.GROUP[req.group]["label"]}): {req.decision_note}')
        return req
    data = dict(req.new_values or {})
    att = req.attachments.first()
    key = {'documents': 'emp_doc_document', 'qualifications': 'attachment' if P.ep_installed() else None}.get(req.group)
    if att is not None and key and key not in data and req.action != 'delete':
        import os
        from django.core.files import File
        data[key] = File(att.file.open('rb'), name=os.path.basename(att.file.name))
    try:
        with transaction.atomic():
            rid = P.apply_change(emp, req.group, req.record_id, data, request.user, req.action)
            req.status, req.applied_at, req.applied_record_id, req.error = 'approved', timezone.now(), rid if isinstance(rid, int) else None, {}
            req.save()
    except ValidationError as exc:
        req.status = 'pending'
        req.decided_by_id = req.decided_at = None
        detail = exc.message_dict if hasattr(exc, 'message_dict') else {'__all__': exc.messages}
        raise ValidationError({'apply': 'The change could not be saved: ' + '; '.join(f'{k}: {" ".join(v) if isinstance(v, list) else v}' for k, v in detail.items())})
    chatter(req, f'Approved by {uname(request.user.pk)} and saved to the profile.' + (f' Note: {req.decision_note}' if req.decision_note else ''), request.user)
    notify(employee=emp, title='Profile change approved', message=f'HR approved your change request {req.number} ({P.GROUP[req.group]["label"]}).')
    return req


def change_json(req, hr=False):
    g = P.GROUP.get(req.group, {})
    labels = {k: l for k, l, _t in g.get('fields', [])}

    def show(vals):
        out = []
        for k, v in (vals or {}).items():
            if not hr and req.group == 'bank' and k in ('iban_number', 'account_number') and v:
                v = P.mask(v)
            out.append({'key': k, 'label': labels.get(k, k), 'value': v})
        return out
    emp = employee(req.employee_id) if hr else None
    return {'id': req.id, 'number': req.number, 'employee_id': req.employee_id, 'employee': P.person(emp) if emp else None,
            'group': req.group, 'group_label': g.get('label', req.group), 'record_id': req.record_id, 'action': req.action,
            'action_label': req.get_action_display(), 'old_values': show(req.old_values), 'new_values': show(req.new_values),
            'reason': req.reason, 'status': req.status, 'status_label': req.get_status_display(), 'decided_by': uname(req.decided_by_id),
            'decided_at': req.decided_at, 'decision_note': req.decision_note, 'applied_at': req.applied_at, 'created_at': req.created_at,
            'attachments': [{'id': a.id, 'name': a.name or a.file.name.rsplit('/', 1)[-1]} for a in req.attachments.all()]}


# ------------------------------------------------------------------------------------------------ payslips
def my_payslips(emp):
    PS = M('PayrollManagement', 'Payslip')
    return PS.objects.filter(employee=emp, status__in=RELEASED).select_related('payroll_run').order_by('-payroll_run__year', '-payroll_run__month', '-id')


def payslip_json(p, lines=False):
    r = p.payroll_run
    out = {'id': p.id, 'period': f'{r.get_month_display()} {r.year}' if r else '', 'year': r.year if r else None, 'month': r.month if r else None,
           'gross': float(p.gross_salary or 0), 'deductions': float(p.total_deductions or 0), 'net': float(p.net_salary or 0),
           'status': p.status, 'days_worked': p.days_worked, 'working_days': p.total_working_days}
    if lines:
        out['lines'] = [{'name': c.component.name, 'type': c.component.component_type, 'amount': float(c.amount or 0)}
                        for c in p.components.select_related('component') if c.component_id and getattr(c.component, 'show_in_payslip', True)]
    return out


def ytd(emp, year=None):
    year = year or date.today().year
    qs = my_payslips(emp).filter(payroll_run__year=year)
    agg = qs.aggregate(g=Sum('gross_salary'), d=Sum('total_deductions'), n=Sum('net_salary'))
    return {'year': year, 'payslips': qs.count(), 'gross': float(agg['g'] or 0), 'deductions': float(agg['d'] or 0), 'net': float(agg['n'] or 0)}


def next_payslip(emp):
    PS = M('PayrollManagement', 'Payslip')
    last = my_payslips(emp).first()
    if last and last.payroll_run_id:
        y, m = last.payroll_run.year, last.payroll_run.month + 1
        if m > 12:
            y, m = y + 1, 1
    else:
        t = date.today()
        y, m = t.year, t.month
    preparing = PS.objects.filter(employee=emp, payroll_run__year=y, payroll_run__month=m).exclude(status__in=RELEASED).exists()
    return {'year': y, 'month': m, 'period': date(y, m, 1).strftime('%B %Y'),
            'state': 'being prepared' if preparing else 'not started', 'expected': None}


# ------------------------------------------------------------------------------------------------ announcements
def my_announcements(emp, include_expired=False):
    from OrganisationManager.announcements import announcements_for
    AV = M('OrganisationManager', 'AnnouncementView')
    anns = list(announcements_for(emp, include_expired=include_expired))
    read = dict(AV.objects.filter(employee=emp, announcement__in=anns).values_list('announcement_id', 'viewed_at'))
    return [{'id': a.id, 'title': a.title, 'message': a.message, 'is_sticky': a.is_sticky, 'created_at': a.created_at,
             'schedule_at': a.schedule_at, 'expires_at': a.expires_at, 'has_attachment': bool(a.attachment),
             'read': a.id in read, 'read_at': read.get(a.id)} for a in anns]


def announcement_stats(a, request=None):
    from OrganisationManager.announcements import audience
    AV = M('OrganisationManager', 'AnnouncementView')
    aud = audience(a)
    if request is not None:
        aud = aud.filter(branch_q(request, 'emp_branch_id'))
    ids = set(aud.values_list('id', flat=True))
    reads = AV.objects.filter(announcement=a, employee_id__in=ids).select_related('employee').order_by('-viewed_at')
    return {'id': a.id, 'title': a.title, 'audience': len(ids), 'read': reads.count(),
            'readers': [{'employee': P.person(r.employee), 'read_at': r.viewed_at} for r in reads[:500]]}


# ------------------------------------------------------------------------------------------------ dashboard
def dashboard(request, emp):
    from .models import Grievance, LetterRequest
    out = {}
    out['change_requests_pending'] = ProfileChangeRequest.objects.filter(employee_id=emp.pk, status='pending').count()
    out['letters_ready'] = LetterRequest.objects.filter(employee_id=emp.pk, status='issued', decided_at__gte=timezone.now() - timezone.timedelta(days=30)).count()
    out['letters_pending'] = LetterRequest.objects.filter(employee_id=emp.pk, status='pending').count()
    out['complaints_open'] = Grievance.objects.filter(employee_id=emp.pk).exclude(status__in=('resolved', 'closed')).count()
    anns = my_announcements(emp)
    out['announcements_unread'] = sum(1 for a in anns if not a['read'])
    out['policies_unacknowledged'] = _unacked_policies(request, emp)
    out['next_payslip'] = next_payslip(emp)
    last = my_payslips(emp).first()
    out['last_payslip'] = payslip_json(last) if last else None
    docs = M('EmpManagement', 'Emp_Documents').objects.filter(emp_id=emp, is_active=True)   # v1.13.0: not the replaced copies
    soon = date.today() + timezone.timedelta(days=60)
    out['documents_expiring'] = docs.filter(emp_doc_expiry_date__lte=soon).count()
    if can(request, *CR_HR):
        out['hr_change_requests'] = ProfileChangeRequest.objects.filter(branch_q(request), status='pending').count()
    if can(request, *LETTER_HR):
        out['hr_letters'] = LetterRequest.objects.filter(branch_q(request), status='pending').count()
    return out


def _unacked_policies(request, emp):
    if not apps.is_installed('OrgStructure'):
        return 0
    try:
        from OrgStructure import services as OS
        from OrgStructure.models import PolicyAcknowledgement
        n = 0
        for p in M('OrganisationManager', 'CompanyPolicy').objects.all():
            if not OS.policy_applies(p, emp, user_id=request.user.pk):
                continue
            v = OS.policy_version(p)
            if v.requires_ack and not PolicyAcknowledgement.objects.filter(policy_id=p.pk, employee_id=emp.pk, version=v.version).exists():
                n += 1
        return n
    except Exception:
        log.debug('policy count skipped', exc_info=True)
        return 0
