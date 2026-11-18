"""ZEO Developer – the safe action library (D2). The ONLY way a module or a script writes into ZEO's core apps.

Every action is kept in SafeAction (history: who, what, when, result). The actions that touch money wait for an administrator
(Developer › Approvals) and run only after the approval, with the approver's rights:

  action              money   how it runs
  send_email          –       e-mail (the company's mail server)
  notify              –       ZEO notification (+ e-mail as the person's notification settings say)
  create_task         –       a to-do (Chatter activity) for a user
  leave_request       –       POST calendars/api/emp-leave-request/ as the user – the leave approval levels follow
  expense_request     –       POST expense/api/expenses/ as the user (an unreported claim the employee adds to a report)
  document_expiry     –       the expiry date of an employee document (Emp_Documents) – the expiry notices follow
  payroll_input       yes     Payroll › Monthly inputs (PayrollPlus.monthly_inputs.save_grid – the same checks as the grid)
  settlement_deduction yes    a deduction line on the employee's open final settlement (draft / submitted)
  post_journal        yes     a balanced journal to ZeoAccounting (source "developer", posted like every other journal)

ZEO's own calculations (payroll engine, gratuity, WPS, leave salary, rights, login, security) are never changed by a script –
they only receive these inputs.
"""
from zeo.tz import today as _zt_today   # v1.73.16 the user's / company's day, not the server's UTC day
import datetime
import re
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from .models import SafeAction

MONEY_ACTIONS = ('payroll_input', 'settlement_deduction', 'post_journal')   # + app_write on a record type that holds money
LABELS = {
    'send_email': 'Send an e-mail', 'notify': 'Send a notification', 'create_task': 'Create a to-do', 'leave_request': 'Create a leave request',
    'expense_request': 'Create an expense claim', 'document_expiry': 'Set a document expiry', 'payroll_input': 'Add a payroll monthly input',
    'settlement_deduction': 'Add a final settlement deduction', 'post_journal': 'Post a journal',
    'app_write': 'Create / change a record of an app',
}


def needs_approval(action, p):
    if action in MONEY_ACTIONS:
        return True
    if action == 'app_write':
        from .appdata import known, is_money
        m = known(p.get('key'))
        return bool(m is not None and is_money(m))
    return False


class ActionRefused(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


def _tr(s, *a):
    try:
        from Translations.i18n import tr
        return tr(s, *a)
    except Exception:   # noqa: BLE001
        return s.format(*a)


def _uname(u):
    return ((u.get_full_name() or u.username) if u else '')[:120]


def _emp(v):
    """An employee from an EmployeeProxy, an id or an employee code."""
    from EmpManagement.models import emp_master
    pk = getattr(v, 'id', None) if not isinstance(v, (int, str)) else v
    e = None
    if isinstance(pk, int) or (isinstance(pk, str) and pk.isdigit()):
        e = emp_master.objects.filter(pk=int(pk)).first()
    if e is None and isinstance(pk, str):
        e = emp_master.objects.filter(emp_code=pk).first()
    if e is None:
        raise ActionRefused(_tr('Employee not found: {0}.', v if isinstance(v, (int, str)) else '?'))
    return e


def _date(v, what):
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    try:
        return datetime.date.fromisoformat(str(v)[:10])
    except ValueError:
        raise ActionRefused(_tr('{0}: "{1}" is not a date (yyyy-mm-dd).', what, v))


def _money(v, what):
    try:
        d = Decimal(str(v).replace(',', ''))
    except (InvalidOperation, ValueError):
        raise ActionRefused(_tr('{0}: "{1}" is not a number.', what, v))
    return d.quantize(Decimal('0.01'))


def _may(c, *codes):
    return bool(c.admin or set(codes) & set(c.codes))


# ------------------------------------------------------------------ the checks before an action is accepted (same for dry runs)
def check(action, params, ctx):
    """Normalises the parameters; raises ActionRefused. ctx = the run context (user, access ctx)."""
    c = ctx.access
    p = dict(params or {})
    if action == 'send_email':
        to = p.get('to')
        to = [to] if isinstance(to, str) else list(to or [])
        out = []
        for x in to[:50]:
            if hasattr(x, 'email'):
                x = x.email
            x = str(x or '').strip()
            if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', x):
                raise ActionRefused(_tr('send_email: "{0}" is not an e-mail address.', x))
            out.append(x)
        if not out:
            raise ActionRefused(_tr('send_email: give at least one address.'))
        return {'to': out, 'subject': str(p.get('subject') or '')[:200] or '(no subject)', 'body': str(p.get('body') or '')[:20000]}
    if action == 'notify':
        who = p.get('who')
        user = _user_of(who)
        if user is None:
            raise ActionRefused(_tr('notify: the person has no ZEO login.'))
        return {'user_id': user.pk, 'title': str(p.get('title') or '')[:120] or 'ZEO', 'message': str(p.get('message') or '')[:255]}
    if action == 'create_task':
        user = _user_of(p.get('assign_to'))
        if user is None:
            raise ActionRefused(_tr('create_task: assign_to must be an employee with a login or a user.'))
        days = int(p.get('due_in_days') or 0)
        if not -365 <= days <= 3650:
            raise ActionRefused(_tr('create_task: due_in_days from -365 to 3650.'))
        if not str(p.get('summary') or '').strip():
            raise ActionRefused(_tr('create_task: write the summary.'))
        return {'user_id': user.pk, 'summary': str(p['summary'])[:255], 'note': str(p.get('note') or '')[:2000], 'due_in_days': days,
                'record': p.get('record') or ''}
    if action == 'leave_request':
        e = _emp(p.get('employee'))
        from calendars.models import leave_type
        lt_v = str(p.get('leave_type') or '').strip()
        lt = leave_type.objects.filter(code__iexact=lt_v).first() or leave_type.objects.filter(name__iexact=lt_v).first() if lt_v else None
        if lt is None and lt_v.isdigit():
            lt = leave_type.objects.filter(pk=int(lt_v)).first()
        if lt is None:
            raise ActionRefused(_tr('leave_request: leave type "{0}" not found.', lt_v))
        a, b = _date(p.get('start'), 'leave_request start'), _date(p.get('end') or p.get('start'), 'leave_request end')
        if b < a:
            raise ActionRefused(_tr('leave_request: the end is before the start.'))
        if not str(p.get('reason') or '').strip():
            raise ActionRefused(_tr('leave_request: write the reason.'))
        return {'employee': e.pk, 'leave_type': lt.pk, 'start_date': a.isoformat(), 'end_date': b.isoformat(), 'reason': str(p['reason'])[:500],
                'employee_label': f'{e.emp_code}', 'leave_type_label': lt.name}
    if action == 'expense_request':
        e = _emp(p.get('employee'))
        from ExpenseManagement.models import ExpenseCategory
        cv = str(p.get('category') or '').strip()
        cat = ExpenseCategory.objects.filter(name__iexact=cv).first() or (ExpenseCategory.objects.filter(pk=int(cv)).first() if cv.isdigit() else None)
        if cat is None:
            raise ActionRefused(_tr('expense_request: expense category "{0}" not found.', cv))
        amt = _money(p.get('amount'), 'expense_request amount')
        if amt <= 0:
            raise ActionRefused(_tr('expense_request: the amount must be more than 0.'))
        return {'employee_id': e.pk, 'category': cat.pk, 'amount': str(amt), 'date': _date(p.get('date') or _zt_today(), 'expense_request date').isoformat(),
                'description': str(p.get('description') or '')[:500], 'merchant': str(p.get('merchant') or '')[:150]}
    if action == 'document_expiry':
        e = _emp(p.get('employee'))
        from EmpManagement.models import Emp_Documents
        dt = str(p.get('document_type') or '').strip()
        qs = Emp_Documents.objects.filter(emp_id=e, is_active=True)
        doc = qs.filter(document_type__type_name__iexact=dt).order_by('-emp_doc_expiry_date').first() if dt else None
        if doc is None and dt:
            doc = qs.filter(emp_doc_number=dt).first()
        if doc is None:
            raise ActionRefused(_tr('document_expiry: {0} has no active document "{1}".', e.emp_code, dt))
        d = _date(p.get('expiry'), 'document_expiry expiry')
        if d < doc.emp_doc_issued_date:
            raise ActionRefused(_tr('document_expiry: the expiry is before the issue date.'))
        return {'document_id': doc.pk, 'expiry': d.isoformat(), 'employee_label': e.emp_code, 'document_label': f'{doc.document_type} {doc.emp_doc_number}'}
    if action == 'payroll_input':
        e = _emp(p.get('employee'))
        from PayrollPlus import monthly_inputs as MI
        m = MI.parse_month(p.get('month') or _zt_today().strftime('%Y-%m'))
        if m is None:
            raise ActionRefused(_tr('payroll_input: month as yyyy-mm.'))
        code = str(p.get('code') or '').strip().upper()
        val = _money(p.get('value'), 'payroll_input value')
        if code not in {c['code'].upper() for c in MI.input_components(e.emp_branch_id_id)}:
            raise ActionRefused(_tr('payroll_input: {0} is not a monthly input of the employee\'s branch (Payroll › Monthly inputs).', code))
        return {'employee': e.pk, 'branch_id': e.emp_branch_id_id, 'month': m.strftime('%Y-%m'), 'code': code, 'value': str(val), 'employee_label': e.emp_code}
    if action == 'settlement_deduction':
        e = _emp(p.get('employee'))
        from PayrollPlus.models import ExitSettlement
        s = ExitSettlement.objects.filter(employee_id=e.pk, status__in=('draft', 'submitted')).order_by('-id').first()
        if s is None:
            raise ActionRefused(_tr('settlement_deduction: {0} has no open final settlement (draft or waiting for Finance).', e.emp_code))
        amt = _money(p.get('amount'), 'settlement_deduction amount')
        if amt <= 0:
            raise ActionRefused(_tr('settlement_deduction: the amount must be more than 0.'))
        if not str(p.get('reason') or '').strip():
            raise ActionRefused(_tr('settlement_deduction: write the reason.'))
        return {'settlement_id': s.pk, 'employee': e.pk, 'amount': str(amt), 'reason': str(p['reason'])[:200], 'employee_label': e.emp_code,
                'settlement_label': s.number or f'#{s.pk}'}
    if action == 'post_journal':
        lines = list(p.get('lines') or [])
        if not 2 <= len(lines) <= 200:
            raise ActionRefused(_tr('post_journal: give 2 to 200 lines.'))
        out, dr, cr = [], Decimal('0'), Decimal('0')
        for i, ln in enumerate(lines, 1):
            ln = dict(ln or {})
            acc = str(ln.get('account') or '').strip()
            if not acc:
                raise ActionRefused(_tr('post_journal line {0}: the account is missing.', i))
            d, k = _money(ln.get('debit') or 0, f'line {i} debit'), _money(ln.get('credit') or 0, f'line {i} credit')
            if d < 0 or k < 0 or (d and k) or not (d or k):
                raise ActionRefused(_tr('post_journal line {0}: a debit or a credit, more than 0.', i))
            dr, cr = dr + d, cr + k
            out.append({'account': acc[:60], 'label': str(ln.get('label') or p.get('memo') or '')[:200], 'debit': str(d), 'credit': str(k)})
        if dr != cr:
            raise ActionRefused(_tr('post_journal: debits {0} and credits {1} must be equal.', dr, cr))
        return {'lines': out, 'date': _date(p.get('date') or _zt_today(), 'post_journal date').isoformat(), 'memo': str(p.get('memo') or 'ZEO Developer')[:120],
                'total': str(dr), 'reference': str(p.get('reference') or '')[:60]}
    if action == 'app_write':
        from .appdata import known, is_protected, routes, registry
        m = known(p.get('key'))
        if m is None:
            raise ActionRefused(_tr('Unknown record type "{0}".', p.get('key')))
        if is_protected(m):
            raise ActionRefused(_tr('{0} belongs to ZEO\'s own calculations or security – it is never changed by a script.', p.get('key')), 403)
        k = registry().model_key(m)
        if not routes().get(k, {}).get('detail' if p.get('pk') else 'list'):
            raise ActionRefused(_tr('{0} cannot be changed by scripts.', k))
        return {'key': k, 'pk': p.get('pk'), 'values': p.get('values') or {}}
    raise ActionRefused(_tr('Unknown action "{0}".', action))


def _user_of(v):
    from UserManagement.models import CustomUser
    if v is None:
        return None
    if isinstance(v, int) or (isinstance(v, str) and v.isdigit()):
        u = CustomUser.objects.filter(pk=int(v)).first()
        if u is not None:
            return u
    uid = getattr(v, 'user_id', None)
    if uid:
        return CustomUser.objects.filter(pk=uid).first()
    try:
        e = _emp(v)
        return e.users
    except ActionRefused:
        return None


def label_of(action, p):
    if action == 'payroll_input':
        return f'{LABELS[action]}: {p["employee_label"]} {p["code"]} = {p["value"]} ({p["month"]})'
    if action == 'settlement_deduction':
        return f'{LABELS[action]}: {p["employee_label"]} {p["amount"]} – {p["reason"]}'
    if action == 'post_journal':
        return f'{LABELS[action]}: {p["memo"]} {p["total"]} ({p["date"]})'
    if action == 'app_write':
        return f'{"Change" if p.get("pk") else "Create"} {p["key"]}{" #" + str(p["pk"]) if p.get("pk") else ""}: ' + ', '.join(f'{k}={v}' for k, v in list(p['values'].items())[:4])[:180]
    if action == 'leave_request':
        return f'{LABELS[action]}: {p["employee_label"]} {p["leave_type_label"]} {p["start_date"]} – {p["end_date"]}'
    if action == 'send_email':
        return f'{LABELS[action]}: {", ".join(p["to"])[:120]} – {p["subject"]}'
    return LABELS.get(action, action)


# ------------------------------------------------------------------ requesting / running
def request(action, params, ctx):
    """From a script (ctx = RunContext). Money actions → waiting for approval; others run now. In a dry run nothing runs."""
    p = check(action, params, ctx)
    money = needs_approval(action, p)
    lbl = label_of(action, p)
    if ctx.dry_run:
        ctx.changes.append({'action': action, 'label': lbl, 'approval': money, 'params': _plain(p)})
        return {'status': 'test', 'label': lbl, 'needs_approval': money}
    sa = SafeAction.objects.create(action=action, label=lbl[:255], params=_plain(p), needs_approval=money, status='pending' if money else 'done',
                                   script=ctx.script, run=ctx.run, requested_by_id=getattr(ctx.user, 'pk', None), requested_by_name=_uname(ctx.user))
    ctx.changes.append({'action': action, 'label': lbl, 'approval': money, 'id': sa.pk})
    if money:
        _tell_admins(sa)
        return {'status': 'pending', 'id': sa.pk, 'label': lbl, 'needs_approval': True}
    try:
        with transaction.atomic():
            res = execute(sa, ctx.user)
        sa.result, sa.status = _plain(res), 'done'
    except (ActionRefused, Exception) as e:   # noqa: BLE001
        sa.status, sa.error = 'failed', getattr(e, 'message', None) or str(e)
        sa.save(update_fields=['status', 'error', 'result'])
        raise ActionRefused(f'{LABELS.get(action, action)}: {sa.error}')
    sa.save(update_fields=['status', 'error', 'result'])
    return {'status': 'done', 'id': sa.pk, 'label': lbl, **(res if isinstance(res, dict) else {})}


def approve(sa, user, note=''):
    """Developer › Approvals – a company administrator approves; the action runs now with the approver's rights."""
    from .services import access_of
    if not access_of(user).admin:
        raise ActionRefused(_tr('Only a company administrator may approve.'), 403)
    if sa.status != 'pending':
        raise ActionRefused(_tr('This action is not waiting for approval.'), 409)
    if sa.requested_by_id == user.pk and not _single_admin():
        raise ActionRefused(_tr('Another administrator approves an action you started.'), 403)
    sa.decided_by_id, sa.decided_by_name, sa.decided_at, sa.note = user.pk, _uname(user), timezone.now(), (note or '')[:300]
    try:
        with transaction.atomic():
            res = execute(sa, user, approved=True)
        sa.status, sa.result, sa.error = 'done', _plain(res), ''
    except (ActionRefused, Exception) as e:   # noqa: BLE001
        sa.status, sa.error = 'failed', getattr(e, 'message', None) or str(e)
    sa.save()
    return sa


def reject(sa, user, note=''):
    from .services import access_of
    if not access_of(user).admin:
        raise ActionRefused(_tr('Only a company administrator may reject.'), 403)
    if sa.status != 'pending':
        raise ActionRefused(_tr('This action is not waiting for approval.'), 409)
    if not (note or '').strip():
        raise ActionRefused(_tr('Write why it is rejected.'))
    sa.status, sa.decided_by_id, sa.decided_by_name, sa.decided_at, sa.note = 'rejected', user.pk, _uname(user), timezone.now(), note[:300]
    sa.save()
    return sa


def _single_admin():
    """A company with one administrator may approve its own script's actions (there is nobody else). Administrators counted the
    same way as everywhere (AccessControl: company superuser, the "Company administrators" group, the ZEO Cloud company rights)."""
    try:
        from django.db import connection
        from UserManagement.models import CustomUser
        from .services import access_of
        n = 0
        for u in CustomUser.objects.filter(is_active=True, tenants__schema_name=connection.schema_name).distinct()[:200]:
            if access_of(u).admin:
                n += 1
                if n > 1:
                    return False
        return True
    except Exception:   # noqa: BLE001
        return True


def execute(sa, user, approved=False):
    """Runs a recorded action. Money actions only after approval – refused otherwise (also when called directly)."""
    if (sa.needs_approval or sa.action in MONEY_ACTIONS) and not (approved and sa.decided_by_id):
        raise ActionRefused(_tr('This action touches money and needs an administrator\'s approval first.'), 403)
    p = sa.params
    a = sa.action
    if a == 'send_email':
        from django.core.mail import EmailMessage
        msg = EmailMessage(p['subject'], p['body'], to=p['to'])
        if '<' in p['body'] and '>' in p['body']:
            msg.content_subtype = 'html'
        msg.send(fail_silently=False)
        return {'sent_to': p['to']}
    if a == 'notify':
        from UserManagement.models import CustomUser
        from zeo.module_helpers import notify
        notify(user=CustomUser.objects.filter(pk=p['user_id']).first(), title=p['title'], message=p['message'], notification_type='general')
        return {'user_id': p['user_id']}
    if a == 'create_task':
        from Chatter.models import Activity
        from UserManagement.models import CustomUser
        due = _zt_today() + datetime.timedelta(days=p['due_in_days'])
        act = Activity.objects.create(model='zeoflow.customrecord', object_id=str(p.get('record') or ''), record_label=str(p.get('record') or '')[:255],
                                      page='', activity_type='todo', summary=p['summary'], note=p['note'], due_date=due,
                                      assigned_to=CustomUser.objects.get(pk=p['user_id']), created_by=user if getattr(user, 'pk', None) else None)
        return {'task_id': act.pk, 'due': due.isoformat()}
    if a == 'leave_request':
        from .internal import call, ApiRefused
        try:
            _st, data = call(user, 'post', '/calendars/api/emp-leave-request/', {k: p[k] for k in ('employee', 'leave_type', 'start_date', 'end_date', 'reason')})
        except ApiRefused as e:
            raise ActionRefused(e.message, e.status)
        return {'leave_request_id': (data or {}).get('id'), 'number': (data or {}).get('document_number')}
    if a == 'expense_request':
        from .internal import call, ApiRefused
        try:
            _st, data = call(user, 'post', '/expense/api/expenses/', {k: p[k] for k in ('employee_id', 'category', 'amount', 'date', 'description', 'merchant')})
        except ApiRefused as e:
            raise ActionRefused(e.message, e.status)
        return {'expense_id': (data or {}).get('id')}
    if a == 'document_expiry':
        from EmpManagement.models import Emp_Documents
        from .services import access_of
        doc = Emp_Documents.objects.select_related('emp_id').get(pk=p['document_id'])
        c = access_of(user)
        if not _may(c, 'change_emp_documents'):
            raise ActionRefused(_tr('You may not change employee documents.'), 403)
        if c.branches is not None and doc.emp_id.emp_branch_id_id not in c.branches:
            raise ActionRefused(_tr('This employee is not in your branches.'), 403)
        old = doc.emp_doc_expiry_date
        doc.emp_doc_expiry_date = datetime.date.fromisoformat(p['expiry'])
        doc.updated_by = user
        doc.save()
        return {'document_id': doc.pk, 'old_expiry': old.isoformat() if old else None, 'expiry': p['expiry']}
    if a == 'payroll_input':
        from PayrollPlus import monthly_inputs as MI
        from .services import access_of
        c = access_of(user)
        if not _may(c, 'change_employeesalarystructure', 'add_payrollrun', 'change_payrollrun'):
            raise ActionRefused(_tr('You may not enter payroll inputs.'), 403)
        try:
            res = MI.save_grid(MI.parse_month(p['month']), p['branch_id'], [{'employee': p['employee'], 'values': {p['code']: p['value']}}], user=user)
        except MI.Refused as e:
            detail = ' '.join(' '.join(v.values()) for v in (e.problems or {}).values())
            raise ActionRefused(f'{e.message} {detail}'.strip(), e.status)
        return {k: (v.isoformat() if hasattr(v, 'isoformat') else v) for k, v in res.items()}
    if a == 'settlement_deduction':
        from PayrollPlus.models import ExitSettlement
        s = ExitSettlement.objects.select_for_update().get(pk=p['settlement_id'])
        if s.status not in ('draft', 'submitted'):
            raise ActionRefused(_tr('The final settlement is no longer open ({0}).', s.get_status_display()), 409)
        line = {'id': f'dev{sa.pk}', 'kind': 'deduction', 'label': p['reason'], 'amount': p['amount'], 'note': f'ZEO Developer action #{sa.pk}',
                'by_id': user.pk, 'by': _uname(user), 'at': timezone.now().isoformat()}
        s.other_lines = (s.other_lines or []) + [line]
        s.history = (s.history or []) + [{'action': 'deduction added (ZEO Developer)', 'by': _uname(user), 'at': timezone.now().isoformat(), 'note': p['reason']}]
        s.save(update_fields=['other_lines', 'history', 'updated_at'])
        try:   # the totals as the Leavers workspace makes them
            from PayrollPlus import eos as EOS
            if hasattr(EOS, 'recalc_totals'):
                EOS.recalc_totals(s)
        except Exception:   # noqa: BLE001
            pass
        return {'settlement_id': s.pk, 'line': line['id']}
    if a == 'post_journal':
        from ZeoAccounting import services as ACC
        b = ACC.Builder('developer', f'SA{sa.pk}', p['memo'], datetime.date.fromisoformat(p['date']), p.get('reference') or p['memo'])
        for ln in p['lines']:
            d, k = Decimal(ln['debit']), Decimal(ln['credit'])
            b.add(ln['account'], ln['label'] or p['memo'], d if d else k, 'debit' if d else 'credit')
        try:
            batch = b.save(user)
        except ACC.Refused as e:
            raise ActionRefused(e.message, e.status)
        return {'journal_id': batch.pk, 'number': batch.number, 'status': batch.status}
    if a == 'app_write':
        from .appdata import write
        from .internal import ApiRefused
        try:
            return write(user, p['key'], p.get('pk'), p['values'])
        except ApiRefused as e:
            raise ActionRefused(e.message, e.status)
    raise ActionRefused(_tr('Unknown action "{0}".', a))


def _tell_admins(sa):
    try:
        from tenant_users.tenants.models import UserTenantPermissions
        from zeo.module_helpers import notify
        for utp in UserTenantPermissions.objects.filter(is_superuser=True).select_related('profile')[:10]:
            notify(user=utp.profile, title='ZEO Developer – approval needed', message=sa.label[:200], notification_type='general')
    except Exception:   # noqa: BLE001
        pass


def _plain(v):
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    return v
