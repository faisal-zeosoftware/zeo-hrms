"""The workflow engine: triggers (signals, dates, schedules, manual), conditions, actions, runs and the scheduler."""
import datetime
import decimal
import logging
import re
import threading
import uuid

from django.db import connection, transaction
from django.db.models.signals import post_save, pre_save
from django.utils import timezone

from . import registry as R

log = logging.getLogger(__name__)
_local = threading.local()
MAX_DEPTH = 3            # a rule's action saving a record may start another rule, at most this deep
CACHE_SECONDS = 60
_rules_cache = {}        # schema -> (time, {model_key: [Workflow]})
SKIP_APPS = {'ZeoFlow', 'Chatter', 'migrations', 'contenttypes', 'sessions', 'admin', 'auth', 'token_blacklist',
             'django_celery_beat', 'django_celery_results', 'tenants', 'tenant_users', 'permissions', 'SavedViews', 'DataTools',
             'Translations', 'LanguagePack', 'ZeoCloud', 'ZeoLicence', 'ZeoGroup', 'AISettings', 'DashboardManagement', 'AccessControl'}


# ------------------------------------------------------------------------------------------------ helpers
def local_today():
    try:
        from ZeoProjects.services import local_today as lt
        return lt()
    except Exception:
        return timezone.localdate()


def plain(v):
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (datetime.date, datetime.datetime, datetime.time)):
        return v.isoformat()
    if hasattr(v, 'pk'):
        return v.pk
    return str(v)


def company():
    try:
        from UserManagement.models import company as C
        return C.objects.filter(schema_name=connection.schema_name).first()
    except Exception:
        return None


def rules_for(model_key, trigger=None):
    """Active rules of this company for a record type (cached a minute)."""
    from .models import Workflow
    sch = connection.schema_name
    now = timezone.now().timestamp()
    ts, by_model = _rules_cache.get(sch, (0, None))
    if by_model is None or now - ts > CACHE_SECONDS:
        by_model = {}
        try:
            for w in Workflow.objects.filter(state='active'):
                by_model.setdefault(w.model, []).append(w)
        except Exception:   # table not migrated yet
            by_model = {}
        _rules_cache[sch] = (now, by_model)
    rules = by_model.get(model_key, [])
    return [w for w in rules if trigger is None or w.trigger == trigger]


def invalidate():
    _rules_cache.pop(connection.schema_name, None)


def _depth():
    return getattr(_local, 'depth', 0)


def _chain():
    if not hasattr(_local, 'chain'):
        _local.chain = set()
    return _local.chain


# ------------------------------------------------------------------------------------------------ values
def get_value(obj, path):
    """record value by path: 'status', 'employee__emp_first_name', 'created_by__username'."""
    cur = obj
    for part in str(path or '').split('__'):
        if cur is None:
            return None
        if isinstance(cur, dict):
            cur = cur.get(part)
            continue
        try:
            cur = getattr(cur, part)
        except Exception:
            return None
        if callable(cur) and not hasattr(cur, 'pk'):
            try:
                cur = cur()
            except Exception:
                return None
    if hasattr(cur, 'all') and hasattr(cur, 'count'):   # m2m / reverse manager
        try:
            return list(cur.all())
        except Exception:
            return None
    return cur


def display(v):
    if v is None:
        return ''
    if isinstance(v, bool):
        return 'Yes' if v else 'No'
    if isinstance(v, datetime.datetime):
        return timezone.localtime(v).strftime('%d/%m/%Y %H:%M') if timezone.is_aware(v) else v.strftime('%d/%m/%Y %H:%M')
    if isinstance(v, datetime.date):
        return v.strftime('%d/%m/%Y')
    if isinstance(v, decimal.Decimal):
        return f'{v:,.2f}'
    if isinstance(v, (list, tuple)):
        return ', '.join(display(x) for x in v)
    return str(v)


VAR = re.compile(r'\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}')


def render(text, ctx):
    """'{{ record.emp_first_name }}' / '{{ record.employee.emp_code }}' / '{{ user.name }}' / '{{ link }}' / '{{ today }}'."""
    if not text:
        return ''

    def one(m):
        path = m.group(1)
        parts = path.split('.')
        head, rest = parts[0], parts[1:]
        if head == 'record':
            return display(get_value(ctx.get('record'), '__'.join(rest))) if rest else ctx.get('record_label', '')
        if head == 'old':
            old = ctx.get('old') or {}
            if not rest:
                return ''
            raw = old.get(rest[0])
            return display(_display_raw(ctx.get('record'), rest[0], raw) if ctx.get('record') is not None else raw)
        if head in ctx and not rest:
            return display(ctx[head])
        if head in ctx and rest:
            return display(get_value(ctx[head], '__'.join(rest)))
        return m.group(0)
    return VAR.sub(one, str(text))


def context_for(obj, old=None, user=None, run=None):
    key = R.model_key(type(obj)) if obj is not None else ''
    comp = company()
    ctx = {'record': obj, 'record_label': R.label_of(obj) if obj is not None else '', 'old': old or {}, 'user': user,
           'company': comp, 'today': local_today(), 'now': timezone.localtime(), 'link': '', 'model': key, 'run': run}
    if obj is not None and getattr(obj, 'pk', None):
        base = ''
        try:
            from django.conf import settings as S
            base = (getattr(S, 'ZEO_APP_URL', '') or '').rstrip('/')
        except Exception:
            pass
        ctx['link'] = base + R.record_link(key, obj.pk)
    if user is not None:
        ctx['user'] = _UserView(user)
    return ctx


class _UserView:
    def __init__(self, u):
        self.u = u
        self.name = (f"{getattr(u, 'first_name', '') or ''} {getattr(u, 'last_name', '') or ''}".strip()) or getattr(u, 'username', '')
        self.username = getattr(u, 'username', '')
        self.email = getattr(u, 'email', '') or ''

    def __getattr__(self, k):
        return getattr(self.u, k, None)

    def __str__(self):
        return self.name


# ------------------------------------------------------------------------------------------------ conditions
def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _date(v):
    if isinstance(v, datetime.datetime):
        return timezone.localtime(v).date() if timezone.is_aware(v) else v.date()
    if isinstance(v, datetime.date):
        return v
    s = str(v or '').strip().lower()
    if not s:
        return None
    if s == 'today':
        return local_today()
    m = re.match(r'^today\s*([+-])\s*(\d+)\s*d?$', s)
    if m:
        return local_today() + datetime.timedelta(days=int(m.group(2)) * (1 if m.group(1) == '+' else -1))
    try:
        return datetime.date.fromisoformat(s[:10])
    except ValueError:
        return None


def _as_list(v):
    if isinstance(v, (list, tuple)):
        return [str(x).strip().lower() for x in v]
    return [x.strip().lower() for x in str(v or '').split(',') if x.strip()]


def _cmp_value(v):
    if hasattr(v, 'pk'):
        return v.pk
    return v


def eval_condition(c, obj, old):
    """One condition {field, operator, value, value2} against the record (old = snapshot before the save)."""
    field, op = c.get('field', ''), c.get('operator', 'eq')
    if not field:
        return True   # an unfinished condition line is ignored
    want, want2 = c.get('value'), c.get('value2')
    cur = get_value(obj, field)
    base = field.split('__')[0]
    old_raw = (old or {}).get(base)
    if op in ('changed', 'changed_from', 'changed_to'):
        if old is None or base not in old:
            return op == 'changed' and False
        now_raw = getattr(obj, base + '_id', None) if hasattr(obj, base + '_id') else _cmp_value(cur)
        if plain(old_raw) == plain(now_raw):
            return False
        if op == 'changed':
            return True
        target = old_raw if op == 'changed_from' else now_raw
        return str(plain(target)).lower() == str(want).lower() or str(_display_raw(obj, base, target)).lower() == str(want).lower()
    v = _cmp_value(cur)
    if op == 'empty':
        return v in (None, '', [], {}) or (isinstance(cur, list) and not cur)
    if op == 'not_empty':
        return not (v in (None, '', [], {}) or (isinstance(cur, list) and not cur))
    if op == 'true':
        return bool(v) is True
    if op == 'false':
        return not bool(v)
    if op in ('related_exists', 'related_not_exists'):
        ok = bool(cur) if not isinstance(cur, list) else len(cur) > 0
        return ok if op == 'related_exists' else not ok
    if op in ('date_before', 'date_after', 'date_today', 'date_overdue', 'between', 'not_between') and (isinstance(cur, (datetime.date, datetime.datetime)) or op.startswith('date')):
        d = _date(cur)
        if d is None:
            return False
        if op == 'date_today':
            return d == local_today()
        if op == 'date_overdue':
            return d < local_today()
        w = _date(want)
        if op == 'date_before':
            return w is not None and d < w
        if op == 'date_after':
            return w is not None and d > w
        w2 = _date(want2)
        inside = w is not None and w2 is not None and w <= d <= w2
        return inside if op == 'between' else not inside
    nv, nw = _num(v), _num(want)
    if op in ('gt', 'lt', 'gte', 'lte', 'between', 'not_between'):
        if nv is None or nw is None:
            return False
        if op == 'gt':
            return nv > nw
        if op == 'lt':
            return nv < nw
        if op == 'gte':
            return nv >= nw
        if op == 'lte':
            return nv <= nw
        nw2 = _num(want2)
        inside = nw2 is not None and nw <= nv <= nw2
        return inside if op == 'between' else not inside
    sv = '' if v is None else str(v).strip().lower()
    sd = str(display(cur)).strip().lower()
    sw = '' if want is None else str(want).strip().lower()
    if op == 'eq':
        return sv == sw or sd == sw or (nv is not None and nw is not None and nv == nw)
    if op == 'ne':
        return not (sv == sw or sd == sw or (nv is not None and nw is not None and nv == nw))
    if op == 'contains':
        return sw in sv or sw in sd
    if op == 'not_contains':
        return sw not in sv and sw not in sd
    if op == 'starts':
        return sv.startswith(sw) or sd.startswith(sw)
    if op == 'ends':
        return sv.endswith(sw) or sd.endswith(sw)
    if op == 'in':
        lst = _as_list(want)
        return sv in lst or sd in lst
    if op == 'not_in':
        lst = _as_list(want)
        return sv not in lst and sd not in lst
    return False


def _display_raw(obj, field, raw):
    try:
        f = type(obj)._meta.get_field(field)
        if f.is_relation and raw is not None:
            o = f.related_model._default_manager.filter(pk=raw).first()
            return str(o) if o is not None else raw
        if f.choices:
            return dict(f.flatchoices).get(raw, raw)
    except Exception:
        pass
    return raw


def eval_group(g, obj, old):
    """{op: and|or, items: [condition | group]} – an empty group is true."""
    if not g:
        return True
    if 'items' not in g:
        return eval_condition(g, obj, old)
    items = g.get('items') or []
    if not items:
        return True
    results = (eval_group(i, obj, old) for i in items)
    return any(results) if str(g.get('op', 'and')).lower() == 'or' else all(results)


# ------------------------------------------------------------------------------------------------ recipients
def _group_users(group_id_or_name):
    from django.contrib.auth.models import Group
    from UserManagement.models import CustomUser
    g = Group.objects.filter(pk=group_id_or_name).first() if str(group_id_or_name).isdigit() else Group.objects.filter(name__iexact=str(group_id_or_name)).first()
    if g is None:
        return []
    from tenant_users.tenants.models import UserTenantPermissions
    ids = list(UserTenantPermissions.objects.filter(groups=g).values_list('profile_id', flat=True))
    return list(CustomUser.objects.filter(pk__in=ids, tenants__schema_name=connection.schema_name, is_active=True).distinct())


def _employee_of(obj):
    """The employee a record is about (the record itself, its employee field, or the user's employee)."""
    from EmpManagement.models import emp_master
    if isinstance(obj, emp_master):
        return obj
    for name in ('employee', 'emp_id', 'emp', 'employee_id', 'emp_master', 'requested_employee', 'created_for'):
        v = getattr(obj, name, None)
        if isinstance(v, emp_master):
            return v
    for f in type(obj)._meta.concrete_fields:
        if f.is_relation and f.related_model is emp_master:
            v = getattr(obj, f.name, None)
            if v is not None:
                return v
    return None


def _owner_user(obj):
    from UserManagement.models import CustomUser
    for name in ('owner', 'assigned_to', 'assignee', 'responsible', 'created_by', 'user', 'users', 'requested_by', 'submitted_by'):
        v = getattr(obj, name, None)
        if isinstance(v, CustomUser):
            return v
    emp = _employee_of(obj)
    return getattr(emp, 'users', None) if emp is not None else None


def _manager_user(obj):
    emp = _employee_of(obj)
    if emp is None:
        return None
    mgr = getattr(emp, 'emp_reporting_manager', None)
    if mgr is not None:
        return mgr
    rep = getattr(emp, 'emp_reports_to', None)
    return getattr(rep, 'users', None) if rep is not None else None


def resolve_recipients(spec, ctx):
    """spec: list of {type: user|role|owner|manager|employee|field|email|triggering_user, value}. Returns (users, emails)."""
    from UserManagement.models import CustomUser
    users, emails = [], []
    obj = ctx.get('record')
    for r in spec or []:
        if isinstance(r, str):
            r = {'type': 'email', 'value': r} if '@' in r else {'type': r}
        t, v = r.get('type'), r.get('value')
        try:
            if t == 'user' and v:
                u = CustomUser.objects.filter(pk=v).first()
                if u:
                    users.append(u)
            elif t == 'role' and v:
                users += _group_users(v)
            elif t == 'owner' and obj is not None:
                u = _owner_user(obj)
                if u:
                    users.append(u)
            elif t == 'manager' and obj is not None:
                u = _manager_user(obj)
                if u:
                    users.append(u)
            elif t == 'employee' and obj is not None:
                emp = _employee_of(obj)
                if emp is not None:
                    if getattr(emp, 'users', None):
                        users.append(emp.users)
                    for e in (emp.emp_company_email, emp.emp_personal_email):
                        if e:
                            emails.append(e)
                            break
            elif t == 'field' and v and obj is not None:
                val = get_value(obj, v)
                if isinstance(val, CustomUser):
                    users.append(val)
                elif val is not None and hasattr(val, 'users') and getattr(val, 'users', None):
                    users.append(val.users)
                elif isinstance(val, str) and '@' in val:
                    emails.append(val)
            elif t == 'triggering_user':
                u = ctx.get('user')
                if u is not None:
                    users.append(getattr(u, 'u', u))
            elif t == 'email' and v:
                emails += [x.strip() for x in str(render(v, ctx)).split(',') if '@' in x]
        except Exception:
            log.exception('recipient %s', r)
    seen, out = set(), []
    for u in users:
        if u and u.pk not in seen:
            seen.add(u.pk)
            out.append(u)
    for u in out:
        if u.email and u.email not in emails:
            emails.append(u.email)
    return out, list(dict.fromkeys(emails))


# ------------------------------------------------------------------------------------------------ actions
class Stop(Exception):
    pass


class Wait(Exception):
    def __init__(self, due_at, note=''):
        self.due_at, self.note = due_at, note


def _send_mail(subject, body, to):
    from django.core.mail import EmailMessage
    if not to:
        return 'no recipient'
    msg = EmailMessage(subject or '(no subject)', body or '', to=to)
    if '<' in (body or '') and '>' in (body or ''):
        msg.content_subtype = 'html'
    msg.send(fail_silently=False)
    return f'sent to {", ".join(to)}'


def _template(tid):
    from .models import FlowTemplate
    return FlowTemplate.objects.filter(pk=tid).first() if tid else None


def _coerce(model, name, value, ctx):
    """A value typed in the rule → the field's type (dates, numbers, booleans, links by id / code / name)."""
    try:
        f = model._meta.get_field(name)
    except Exception:
        return value
    if isinstance(value, str):
        value = render(value, ctx)
    if value in ('', None):
        return None
    if f.is_relation:
        if hasattr(value, 'pk'):
            return value
        rel = f.related_model
        s = str(value).strip()
        if s.isdigit():
            return rel._default_manager.filter(pk=int(s)).first()
        for k in ('code', 'name', 'username', 'emp_code', 'title', 'branch_name', 'dept_name'):
            if any(ff.name == k for ff in rel._meta.concrete_fields):
                o = rel._default_manager.filter(**{k + '__iexact': s}).first()
                if o:
                    return o
        return None
    from django.db import models as M
    if isinstance(f, M.BooleanField):
        return str(value).strip().lower() in ('1', 'true', 'yes', 'y', 'on')
    if isinstance(f, M.DateTimeField):
        d = _date(value)
        return timezone.make_aware(datetime.datetime.combine(d, datetime.time.min)) if d else None
    if isinstance(f, M.DateField):
        return _date(value)
    if isinstance(f, (M.IntegerField,)):
        return int(float(value))
    if isinstance(f, (M.DecimalField, M.FloatField)):
        return decimal.Decimal(str(value)) if isinstance(f, M.DecimalField) else float(value)
    if f.choices:
        s = str(value).strip()
        for k, v in f.flatchoices:
            if str(k).lower() == s.lower() or str(v).lower() == s.lower():
                return k
    return value


def _save_quiet(obj, fields=None):
    """Save inside a rule: one level deeper for the loop guard."""
    _local.depth = _depth() + 1
    try:
        obj.save(update_fields=fields) if fields else obj.save()
    finally:
        _local.depth = _depth() - 1


def do_update_fields(a, ctx, run):
    obj = ctx['record']
    if obj is None:
        return 'no record'
    changed = []
    for name, val in (a.get('fields') or {}).items():
        v = _coerce(type(obj), name, val, ctx)
        setattr(obj, name, v)
        changed.append(name)
    if changed:
        _save_quiet(obj)
    return 'set ' + ', '.join(changed)


def do_create_record(a, ctx, run):
    model = R.get_model(a.get('model', ''))
    if model is None:
        raise ValueError('Record type not found: %s' % a.get('model'))
    obj = model()
    src = ctx['record']
    for name, val in (a.get('fields') or {}).items():
        if isinstance(val, str) and val.strip() == '{{ record }}' and src is not None:
            setattr(obj, name, src)
            continue
        setattr(obj, name, _coerce(model, name, val, ctx))
    # link back to the source record when a field points at its type
    if src is not None and a.get('link_back', True):
        for f in model._meta.concrete_fields:
            if f.is_relation and f.related_model is type(src) and getattr(obj, f.attname, None) is None:
                setattr(obj, f.name, src)
                break
    u = ctx.get('user')
    for name in ('created_by',):
        if hasattr(obj, name + '_id') and getattr(obj, name + '_id', None) is None and u is not None:
            setattr(obj, name, getattr(u, 'u', u))
    _save_quiet(obj)
    return f'{R._nice(model._meta.verbose_name)} #{obj.pk} created'


def do_send_email(a, ctx, run):
    t = _template(a.get('template'))
    subject = render(a.get('subject') or (t.subject if t else ''), ctx)
    body = render(a.get('body') or (t.body if t else ''), ctx)
    users, emails = resolve_recipients(a.get('to'), ctx)
    emails = [e for e in emails if e]
    if not emails:
        return 'skipped – no e-mail address'
    return _send_mail(subject, body, emails[:50])


def do_notify(a, ctx, run):
    from .models import FlowNotification
    t = _template(a.get('template'))
    title = render(a.get('title') or (t.subject if t else ''), ctx)
    message = render(a.get('message') or (t.body if t else ''), ctx)
    users, _ = resolve_recipients(a.get('to'), ctx)
    obj = ctx['record']
    n = 0
    for u in users[:200]:
        FlowNotification.objects.create(recipient=u, title=title[:200], message=message[:2000] or title, link=R.record_link(ctx['model'], obj.pk) if obj is not None else '',
                                        model=ctx['model'], object_id=str(obj.pk) if obj is not None else '', run=run)
        n += 1
    return f'{n} notification(s)' if n else 'skipped – no user'


def do_create_task(a, ctx, run):
    from Chatter.models import Activity
    users, _ = resolve_recipients(a.get('assign') or [{'type': 'owner'}], ctx)
    if not users:
        u = ctx.get('user')
        users = [getattr(u, 'u', u)] if u is not None else []
    if not users:
        return 'skipped – nobody to assign to'
    obj = ctx['record']
    due = local_today() + datetime.timedelta(days=int(a.get('due_in_days') or 0))
    n = 0
    for u in users[:20]:
        Activity.objects.create(model=ctx['model'], object_id=str(obj.pk) if obj is not None else '', record_label=ctx.get('record_label', '')[:255],
                                page=R.record_link(ctx['model'], obj.pk) if obj is not None else '', activity_type=a.get('activity_type') or 'todo',
                                summary=render(a.get('summary') or 'Follow up', ctx)[:255], note=render(a.get('note') or '', ctx),
                                due_date=due, assigned_to=u, created_by=getattr(ctx.get('user'), 'u', None))
        n += 1
    return f'{n} task(s) due {due:%d/%m/%Y}'


def do_assign_owner(a, ctx, run):
    obj = ctx['record']
    if obj is None:
        return 'no record'
    field = a.get('field') or 'owner'
    how = a.get('how') or 'user'
    users = []
    if how == 'round_robin':
        users = _group_users(a.get('role')) if a.get('role') else []
        if users:
            from .models import WorkflowRun
            n = WorkflowRun.objects.filter(workflow_id=run.workflow_id, status='ok').count() if run else 0
            users = [users[n % len(users)]]
    else:
        users, _ = resolve_recipients([{'type': how, 'value': a.get('value') or a.get('user') or a.get('role')}], ctx)
    if not users:
        return 'skipped – no user found'
    setattr(obj, field, users[0])
    _save_quiet(obj, [field])
    return f'{field} = {users[0].username}'


def do_submit_approval(a, ctx, run):
    from .approvals import submit
    obj = ctx['record']
    if obj is None:
        return 'no record'
    req = submit(a.get('flow'), obj, user=getattr(ctx.get('user'), 'u', None), note=render(a.get('note') or '', ctx),
                 callbacks={'workflow': run.workflow_id if run else None, 'on_approved': a.get('on_approved') or [], 'on_rejected': a.get('on_rejected') or []},
                 run=run)
    return f'approval request #{req.pk} ({req.flow.name})'


def do_wait(a, ctx, run):
    until = None
    if a.get('until_field'):
        d = _date(get_value(ctx['record'], a['until_field']))
        if d:
            until = timezone.make_aware(datetime.datetime.combine(d, datetime.time(hour=8)))
            until += datetime.timedelta(days=int(a.get('offset_days') or 0))
    if until is None:
        until = timezone.now() + datetime.timedelta(days=int(a.get('days') or 0), hours=int(a.get('hours') or 0), minutes=int(a.get('minutes') or 0))
    raise Wait(until, a.get('note') or '')


def do_stop(a, ctx, run):
    raise Stop(render(a.get('note') or 'Stopped by the rule', ctx))


HANDLERS = {'update_fields': do_update_fields, 'create_record': do_create_record, 'send_email': do_send_email, 'notify': do_notify,
            'create_task': do_create_task, 'assign_owner': do_assign_owner, 'submit_approval': do_submit_approval, 'wait': do_wait, 'stop': do_stop}


def run_actions(actions, ctx, run, start=0, steps=None, path=''):
    """Runs actions[start:] in order. Returns (status, steps). A Wait schedules the rest and returns 'waiting'."""
    from .models import ScheduledAction
    steps = run.steps if steps is None else steps
    for i in range(start, len(actions or [])):
        a = actions[i] or {}
        t = a.get('type', '')
        idx = f'{path}{i + 1}'
        step = {'index': idx, 'type': t, 'label': a.get('label') or dict((k, v) for k, v, _ in R.ACTIONS).get(t, t), 'status': 'ok', 'detail': ''}
        try:
            if a.get('disabled'):
                step.update(status='skipped', detail='disabled')
            elif t == 'branch':
                ok = eval_group(a.get('conditions') or {}, ctx['record'], ctx.get('old'))
                step['detail'] = 'conditions true → then' if ok else 'conditions false → else'
                steps.append(step)
                sub = (a.get('then') if ok else a.get('else')) or []
                st = run_actions(sub, ctx, run, 0, steps, path=idx + ('a.' if ok else 'b.'))
                if st in ('waiting', 'stopped'):
                    if st == 'waiting':   # the rest of THIS level continues after the wait
                        pend = ScheduledAction.objects.filter(run=run, status='pending').order_by('-id').first()
                        if pend is not None:
                            pend.actions = list(pend.actions) + list(actions[i + 1:])
                            pend.save(update_fields=['actions'])
                    return st
                continue
            else:
                h = HANDLERS.get(t)
                if h is None:
                    step.update(status='skipped', detail=f'unknown action "{t}"')
                else:
                    step['detail'] = str(h(a, ctx, run) or '')[:500]
        except Wait as w:
            step.update(status='waiting', detail=f'until {timezone.localtime(w.due_at):%d/%m/%Y %H:%M} {w.note}'.strip())
            steps.append(step)
            obj = ctx['record']
            ScheduledAction.objects.create(workflow_id=run.workflow_id, run=run, kind='wait', model=ctx['model'], object_id=str(obj.pk) if obj is not None else '',
                                           record_label=ctx.get('record_label', '')[:255], due_at=w.due_at, resume_from=0,
                                           actions=list(actions[i + 1:]), note=w.note[:255])
            return 'waiting'
        except Stop as s:
            step.update(status='stopped', detail=str(s))
            steps.append(step)
            return 'stopped'
        except Exception as e:
            log.exception('action %s of run %s', t, run.pk)
            step.update(status='failed', detail=str(e)[:500])
            steps.append(step)
            if a.get('on_error') == 'continue':
                continue
            return 'failed'
        steps.append(step)
    return 'ok'


# ------------------------------------------------------------------------------------------------ runs
def _limits_ok(wf, obj):
    from .models import WorkflowRun
    key = str(getattr(obj, 'pk', '')) if obj is not None else ''
    if wf.once_per_record and key and WorkflowRun.objects.filter(workflow=wf, object_id=key).exclude(status__in=('skipped', 'cancelled')).exists():
        return 'already ran for this record'
    if wf.max_runs_per_day and WorkflowRun.objects.filter(workflow=wf, day=local_today()).exclude(status='skipped').count() >= wf.max_runs_per_day:
        return 'daily limit reached'
    return ''


def start_run(wf, obj, trigger, old=None, user=None, initiator='system', parent=None, correlation=None, check_conditions=True, force=False):
    """Evaluate a rule for a record and run its actions. Returns the WorkflowRun (or None when nothing was logged)."""
    from .models import Workflow, WorkflowRun, ScheduledAction
    key = R.model_key(type(obj)) if obj is not None else ''
    rk = (wf.pk, key, str(getattr(obj, 'pk', '')))
    chain = _chain()
    if rk in chain or _depth() > MAX_DEPTH:
        log.warning('workflow loop stopped: %s on %s', wf.name, rk)
        return None
    ctx = context_for(obj, old, user)
    run = WorkflowRun(workflow=wf, version=wf.version, model=key, object_id=str(getattr(obj, 'pk', '') or ''), record_label=ctx['record_label'][:255],
                      trigger=trigger, initiated_by=user, initiator=initiator, parent=parent, correlation_id=correlation or uuid.uuid4().hex[:20], day=local_today())
    if check_conditions and not force:
        why = _limits_ok(wf, obj)
        if why:
            run.status, run.error, run.finished_at = 'skipped', why, timezone.now()
            run.save()
            return run
        if not eval_group(wf.conditions or {}, obj, old):
            if trigger in ('date', 'schedule'):
                return None   # not logged: dates and schedules check many records every day
            run.status, run.error, run.finished_at = 'skipped', 'conditions not met', timezone.now()
            run.save()
            return run
    run.save()
    ctx['run'] = run
    if wf.delay_minutes and trigger not in ('resume',):
        due = timezone.now() + datetime.timedelta(minutes=wf.delay_minutes)
        ScheduledAction.objects.create(workflow=wf, run=run, kind='delay', model=key, object_id=run.object_id, record_label=run.record_label,
                                       due_at=due, resume_from=0, actions=list(wf.actions or []), note=f'delayed start ({wf.delay_minutes} min)')
        run.status = 'waiting'
        run.steps = [{'index': '0', 'type': 'delay', 'label': 'Delayed start', 'status': 'waiting', 'detail': f'runs at {timezone.localtime(due):%d/%m/%Y %H:%M}'}]
        run.save(update_fields=['status', 'steps'])
        return run
    chain.add(rk)
    _local.depth = _depth() + 1
    try:
        status = run_actions(list(wf.actions or []), ctx, run)
    finally:
        _local.depth = _depth() - 1
        chain.discard(rk)
    _finish(run, status)
    return run


def _finish(run, status):
    from .models import Workflow
    run.status = {'ok': 'ok', 'stopped': 'ok', 'failed': 'failed', 'waiting': 'waiting'}.get(status, status)
    if status != 'waiting':
        run.finished_at = timezone.now()
    if status == 'failed':
        run.error = next((s['detail'] for s in reversed(run.steps) if s.get('status') == 'failed'), 'failed')
    run.save()
    Workflow.objects.filter(pk=run.workflow_id).update(last_run_at=timezone.now(), runs_count=models_F('runs_count') + 1,
                                                       **({'errors_count': models_F('errors_count') + 1} if status == 'failed' else {}))


def models_F(name):
    from django.db.models import F
    return F(name)


def resume(sa):
    """Continue a waiting run (wait / delayed start) or run a date / schedule entry."""
    from .models import WorkflowRun
    run = sa.run
    wf = sa.workflow
    obj = None
    model = R.get_model(sa.model) if sa.model else None
    if model is not None and sa.object_id:
        obj = model._default_manager.filter(pk=sa.object_id).first()
        if obj is None:
            sa.status, sa.note, sa.done_at = 'cancelled', 'record no longer exists', timezone.now()
            sa.save()
            if run:
                run.status, run.error, run.finished_at = 'cancelled', 'record no longer exists', timezone.now()
                run.save()
            return
    if run is None or wf is None:
        sa.status, sa.done_at = 'cancelled', timezone.now()
        sa.save()
        return
    ctx = context_for(obj, None, run.initiated_by, run)
    run.status = 'running'
    run.save(update_fields=['status'])
    _local.depth = _depth() + 1
    try:
        status = run_actions(list(sa.actions or []), ctx, run, 0)
    finally:
        _local.depth = _depth() - 1
    sa.status, sa.done_at = 'done', timezone.now()
    sa.save()
    _finish(run, status)


def retry(run, user=None):
    """Run the failed step and the ones after it again."""
    model = R.get_model(run.model) if run.model else None
    obj = model._default_manager.filter(pk=run.object_id).first() if model and run.object_id else None
    wf = run.workflow
    failed_idx = next((i for i, s in enumerate(run.steps) if s.get('status') == 'failed'), None)
    actions = list(wf.actions or [])
    # top-level index of the failed step ("3" or "3a.2" → 3)
    start = 0
    if failed_idx is not None:
        try:
            start = int(str(run.steps[failed_idx].get('index', '1')).split('.')[0].rstrip('ab')) - 1
        except ValueError:
            start = 0
        run.steps = run.steps[:failed_idx]
    run.retry_count += 1
    run.status, run.error, run.initiator = 'running', '', 'retry'
    run.save()
    ctx = context_for(obj, None, user or run.initiated_by, run)
    _local.depth = _depth() + 1
    try:
        status = run_actions(actions, ctx, run, start)
    finally:
        _local.depth = _depth() - 1
    _finish(run, status)
    return run


# ------------------------------------------------------------------------------------------------ signals
def _tracked(sender):
    meta = sender._meta
    if meta.app_label in SKIP_APPS or meta.auto_created or connection.schema_name == 'public':
        return False
    return True


def _pre_save(sender, instance, raw=False, **kw):
    if raw or not _tracked(sender) or instance.pk is None or hasattr(instance, '_zeo_old'):
        return
    key = R.model_key(sender)
    if not rules_for(key):
        return
    try:
        old = sender._default_manager.filter(pk=instance.pk).first()
        instance._zeo_flow_old = {f.name: getattr(old, f.attname, None) for f in sender._meta.concrete_fields} if old is not None else None
    except Exception:
        instance._zeo_flow_old = None


def _post_save(sender, instance, created=False, raw=False, **kw):
    if raw or not _tracked(sender):
        return
    key = R.model_key(sender)
    rules = rules_for(key)
    if not rules:
        return
    old = getattr(instance, '_zeo_old', None) or getattr(instance, '_zeo_flow_old', None)
    user = None
    try:
        from Chatter.tracking import current_request
        req = current_request()
        user = getattr(req, 'user', None) if req is not None else None
        if user is not None and not getattr(user, 'is_authenticated', False):
            user = None
    except Exception:
        pass
    for wf in rules:
        try:
            if wf.trigger == 'created' and created:
                start_run(wf, instance, 'created', None, user, 'user' if user else 'system')
            elif wf.trigger == 'updated' and not created:
                start_run(wf, instance, 'updated', old, user, 'user' if user else 'system')
            elif wf.trigger == 'field_changed' and not created and old is not None:
                f = (wf.trigger_config or {}).get('field')
                if not f or f not in old:
                    continue
                now_raw = getattr(instance, f + '_id', None) if hasattr(instance, f + '_id') else getattr(instance, f, None)
                if plain(old.get(f)) == plain(_cmp_value(now_raw)):
                    continue
                cfg = wf.trigger_config or {}
                if cfg.get('to') not in (None, '') and str(cfg['to']).lower() not in (str(plain(_cmp_value(now_raw))).lower(), str(_display_raw(instance, f, now_raw)).lower()):
                    continue
                if cfg.get('from') not in (None, '') and str(cfg['from']).lower() not in (str(plain(old.get(f))).lower(), str(_display_raw(instance, f, old.get(f))).lower()):
                    continue
                start_run(wf, instance, 'field_changed', old, user, 'user' if user else 'system')
        except Exception:
            log.exception('workflow %s on %s', wf.name, key)


_installed = False


def install():
    global _installed
    if _installed:
        return
    pre_save.connect(_pre_save, dispatch_uid='zeo_flow_pre')
    post_save.connect(_post_save, dispatch_uid='zeo_flow_post')
    _installed = True


# ------------------------------------------------------------------------------------------------ scheduler
def _period_key(cfg, now):
    every = cfg.get('every') or 'daily'
    if every == 'weekly':
        return f'W{now.isocalendar()[0]}-{now.isocalendar()[1]}'
    if every == 'monthly':
        return f'M{now:%Y-%m}'
    if every == 'hourly':
        return f'H{now:%Y-%m-%d-%H}'
    return f'D{now:%Y-%m-%d}'


def _due_now(cfg, now, last_run_at):
    every = cfg.get('every') or 'daily'
    hh, mm = 8, 0
    try:
        hh, mm = [int(x) for x in str(cfg.get('time') or '08:00').split(':')[:2]]
    except ValueError:
        pass
    if every == 'hourly':
        due = now.replace(minute=mm % 60, second=0, microsecond=0)
    else:
        due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if every == 'weekly' and now.weekday() != int(cfg.get('weekday') or 0):
            return False
        if every == 'monthly' and now.day != int(cfg.get('day') or 1):
            return False
    if now < due:
        return False
    if last_run_at is not None and _period_key(cfg, timezone.localtime(last_run_at)) == _period_key(cfg, now):
        return False
    return True


def run_due(limit_records=500):
    """Every 15 minutes (Celery beat) or `manage.py run_workflows`: waits and delays that are due, date triggers of the
    day, scheduled rules, approval reminders / escalations. Returns counts."""
    from .models import ScheduledAction, Workflow, WorkflowRun
    from . import approvals
    out = {'resumed': 0, 'date_runs': 0, 'schedule_runs': 0, 'approval_reminders': 0}
    now = timezone.now()
    for sa in ScheduledAction.objects.filter(status='pending', due_at__lte=now, kind__in=('wait', 'delay')).select_related('run', 'workflow')[:200]:
        try:
            resume(sa)
            out['resumed'] += 1
        except Exception as e:
            log.exception('resume %s', sa.pk)
            sa.status, sa.note, sa.done_at = 'failed', str(e)[:255], now
            sa.save()
    today = local_today()
    for wf in Workflow.objects.filter(state='active', trigger='date'):
        cfg = wf.trigger_config or {}
        field, model = cfg.get('date_field'), R.get_model(wf.model)
        if not field or model is None:
            continue
        off = int(cfg.get('offset_days') or 0)
        when = cfg.get('when') or 'on'
        target = today + datetime.timedelta(days=off) if when == 'before' else (today - datetime.timedelta(days=off) if when == 'after' else today)
        try:
            qs = model._default_manager.filter(**{f'{field}__date': target}) if R.field_type(model._meta.get_field(field)) == 'datetime' else model._default_manager.filter(**{field: target})
        except Exception:
            log.exception('date trigger %s', wf.name)
            continue
        done = set(WorkflowRun.objects.filter(workflow=wf, day=today).values_list('object_id', flat=True))
        for obj in qs[:limit_records]:
            if str(obj.pk) in done:
                continue
            r = start_run(wf, obj, 'date', None, None, 'schedule')
            if r is not None and r.status != 'skipped':
                out['date_runs'] += 1
    lnow = timezone.localtime(now)
    for wf in Workflow.objects.filter(state='active', trigger='schedule'):
        cfg = wf.trigger_config or {}
        if not _due_now(cfg, lnow, wf.last_run_at):
            continue
        model = R.get_model(wf.model) if wf.model else None
        n = 0
        if model is None:
            r = start_run(wf, None, 'schedule', None, None, 'schedule', check_conditions=False)
            n += 1 if r else 0
        else:
            for obj in model._default_manager.all()[:limit_records]:
                r = start_run(wf, obj, 'schedule', None, None, 'schedule')
                if r is not None and r.status != 'skipped':
                    n += 1
        Workflow.objects.filter(pk=wf.pk).update(last_run_at=now)
        out['schedule_runs'] += n
    try:
        out['approval_reminders'] = approvals.sweep()
    except Exception:
        log.exception('approval sweep')
    return out
