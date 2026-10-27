"""v1.47.0 – navigation and the social side of the product:

* search()        global search (Ctrl+K): employees and the records of every active app, with the user's rights
* quick_creates() what the user may create from the '+' button (records of the active apps)
* wall()          birthdays, work anniversaries, new joiners and announcements with reactions and comments
* team_hub()      the manager's "My team" page: who is in / out today, approvals, documents, goals, celebrations, 1:1s
* RELEASES        the release notes of the "What's new" panel
"""
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from .models import OneOnOne, UserFlag, WallComment, WallReaction

WALL_EMOJIS = ['👍', '❤️', '🎉', '👏', '🎂', '🙏']


# ------------------------------------------------------------------ helpers
def my_emp(user):
    from EmpManagement.models import emp_master
    if user is None or not getattr(user, 'pk', None):
        return None
    return emp_master.objects.filter(users=user).select_related('emp_branch_id', 'emp_dept_id', 'emp_desgntn_id').first()


def emp_name(e):
    if not e:
        return ''
    return ' '.join(x for x in (e.emp_first_name, e.emp_last_name) if x) or e.emp_code


def photo(e):
    try:
        return e.emp_profile_pic.url if e.emp_profile_pic else None
    except Exception:
        return None


def emp_page(eid):
    return f'/main-sidebar/sub-sidebar/employee-details/{eid}/details'


def active_emps():
    from EmpManagement.models import emp_master
    qs = emp_master.objects.all()
    if any(f.name == 'is_active' for f in emp_master._meta.fields):
        qs = qs.filter(Q(is_active=True) | Q(is_active__isnull=True))
    return qs


def branch_emps(request):
    """Active employees of the branches the user works in (everyone for a company admin)."""
    from AccessControl.access import allowed_branches
    qs = active_emps()
    try:
        b = allowed_branches(request)
    except Exception:
        b = []
    if b is not None:
        me = my_emp(request.user)
        qs = qs.filter(Q(emp_branch_id__in=b) | Q(pk=me.pk if me else 0)) if b else qs.filter(pk=me.pk if me else 0)
    return qs


def kit_apps():
    """The kit apps (CRM, Property, Maintenance, Assets, People, Masters …) active in the company."""
    try:
        from zeo.appkit.engine import REGISTRY
    except Exception:
        return []
    out = []
    for a in REGISTRY.values():
        try:
            if a.activated():
                out.append(a)
        except Exception:
            continue
    return out


# ------------------------------------------------------------------ global search
def _emp_scope(request, qs):
    from AccessControl.access import scope_queryset

    class _V:
        zeo_scope = True
    return scope_queryset(request, _V(), qs)


def search_employees(request, q, limit=8):
    qs = active_emps()
    cond = Q()
    for part in q.split():
        cond &= (Q(emp_code__icontains=part) | Q(emp_first_name__icontains=part) | Q(emp_last_name__icontains=part)
                 | Q(emp_personal_email__icontains=part) | Q(emp_company_email__icontains=part) | Q(emp_mobile_number_1__icontains=part))
    try:
        qs = _emp_scope(request, qs.filter(cond))
    except Exception:
        return []
    qs = qs.select_related('emp_dept_id', 'emp_desgntn_id')
    rows = list(qs.order_by('emp_first_name', 'emp_code')[:limit])
    exact = [e for e in rows if (e.emp_code or '').lower() == q.lower()]
    rows = exact + [e for e in rows if e not in exact]
    return [{'type': 'employee', 'group': 'Employees', 'title': emp_name(e), 'number': e.emp_code,
             'subtitle': ' · '.join(x for x in (e.emp_code, str(e.emp_desgntn_id) if e.emp_desgntn_id_id else '',
                                               str(e.emp_dept_id) if e.emp_dept_id_id else '') if x),
             'page': emp_page(e.id), 'photo': photo(e), 'icon': 'person'} for e in rows]


def search_records(request, q, per_reg=4, limit=30):
    from zeo.appkit.engine import field_of, value
    out = []
    for a in kit_apps():
        for r in a.regs.values():
            if len(out) >= limit:
                return out
            try:
                if r.child_of or not r.can_open(request):
                    continue
                fields = list(dict.fromkeys(list(r.search or ()) + ([r.title] if isinstance(r.title, str) else [])))
                cond = Q()
                for s_ in fields:
                    base = s_.split('__')[0]
                    f = field_of(r.model, base)
                    if f is None:
                        continue
                    if getattr(f, 'is_relation', False) and '__' not in s_:
                        continue
                    if f.get_internal_type() not in ('CharField', 'TextField', 'EmailField', 'SlugField') and '__' not in s_:
                        continue
                    cond |= Q(**{s_ + '__icontains': q})
                if not cond:
                    continue
                rows = list(r.scope(request).filter(cond)[:per_reg])
            except Exception:
                continue
            for obj in rows:
                try:
                    t, tl = value(r, obj, r.title)
                    title = str(tl if tl else (t if t is not None else obj.pk))
                    sub = ''
                    if r.subtitle:
                        sv, sl = value(r, obj, r.subtitle, not r.show_restricted(request, obj))
                        sub = str(sl if sl is not None else (sv if sv is not None else ''))
                    num = getattr(obj, 'number', '') or ''
                except Exception:
                    continue
                st = obj.get_status_display() if hasattr(obj, 'get_status_display') else ''
                out.append({'type': 'record', 'group': a.title, 'app': a.title, 'kind': r.kind, 'label': r.label, 'id': obj.pk,
                            'title': title, 'number': num if num != title else '',
                            'subtitle': ' · '.join(x for x in (r.label, sub, st) if x),
                            'page': f'{a.page}r/{r.kind}/{obj.pk}', 'endpoint': f'/{a.slug}/api/r/{r.kind}/', 'icon': 'description'})
    return out


def search(request, q):
    q = ' '.join(str(q or '').split())[:80]
    if len(q) < 2:
        return {'q': q, 'results': []}
    res = search_employees(request, q) + search_records(request, q)
    ql = q.lower()
    res.sort(key=lambda x: (0 if (x.get('number') or '').lower() == ql or x['title'].lower() == ql else
                            1 if x['title'].lower().startswith(ql) else 2))
    return {'q': q, 'results': res}


# ------------------------------------------------------------------ quick create
def quick_creates(request):
    out = []
    for a in kit_apps():
        for r in a.regs.values():
            try:
                if r.child_of or r.create == 'none' or not r.can_create(request):
                    continue
            except Exception:
                continue
            out.append({'app': a.title, 'kind': r.kind, 'label': r.label, 'group': r.group, 'page': f'{a.page}r/{r.kind}', 'new': True})
    me = my_emp(request.user)
    return {'employee_id': me.id if me else None, 'records': out}


# ------------------------------------------------------------------ company wall
def _celebrations(emps, today, back=3, ahead=7):
    d_from, d_to = today - timedelta(days=back), today + timedelta(days=ahead)
    out = []
    for e in emps:
        for kind, d0 in (('bday', e.emp_date_of_birth), ('anniv', e.emp_joined_date)):
            if not d0:
                continue
            for y in range(d_from.year, d_to.year + 1):
                try:
                    d = d0.replace(year=y)
                except ValueError:
                    d = d0.replace(year=y, day=28)
                if d_from <= d <= d_to and (kind == 'bday' or y > d0.year):
                    years = y - d0.year
                    out.append({'item': f'{kind}:{e.id}:{y}', 'kind': kind, 'date': d, 'employee_id': e.id, 'employee': emp_name(e),
                                'photo': photo(e), 'designation': str(e.emp_desgntn_id) if e.emp_desgntn_id_id else '',
                                'title': (f'Happy birthday, {emp_name(e)}!' if kind == 'bday' else
                                          f'{emp_name(e)} – {years} year{"s" if years != 1 else ""} with us'),
                                'years': years if kind == 'anniv' else None, 'today': d == today})
    return out


def _joiners(emps, today, days=30):
    out = []
    for e in emps.filter(emp_joined_date__gte=today - timedelta(days=days), emp_joined_date__lte=today):
        out.append({'item': f'join:{e.id}', 'kind': 'join', 'date': e.emp_joined_date, 'employee_id': e.id, 'employee': emp_name(e),
                    'photo': photo(e), 'designation': str(e.emp_desgntn_id) if e.emp_desgntn_id_id else '',
                    'department': str(e.emp_dept_id) if e.emp_dept_id_id else '',
                    'title': f'Welcome {emp_name(e)}', 'today': e.emp_joined_date == today})
    return out


def my_announcements(request, n=20):
    from OrganisationManager.models import Announcement
    now = timezone.now()
    me = my_emp(request.user)
    if me is not None:
        from OrganisationManager.announcements import announcements_for
        qs = announcements_for(me)
    else:
        qs = (Announcement.objects.filter(Q(expires_at__isnull=True) | Q(expires_at__gte=now))
              .filter(Q(schedule_at__isnull=True) | Q(schedule_at__lte=now)).order_by('-is_sticky', '-created_at'))
    return list(qs.select_related('created_by')[:n])


def item_ok(request, item):
    """Is this a wall item the user can see (and react to)?"""
    kind, _, rest = str(item or '').partition(':')
    if kind == 'ann':
        return rest.isdigit() and any(a.id == int(rest) for a in my_announcements(request, 200))
    if kind in ('bday', 'anniv', 'join'):
        eid = rest.split(':')[0]
        return eid.isdigit() and branch_emps(request).filter(pk=int(eid)).exists()
    return False


def comments_allowed(item):
    if not item.startswith('ann:'):
        return True
    from OrganisationManager.models import Announcement
    a = Announcement.objects.filter(pk=item.split(':')[1]).first()
    return bool(a and a.allow_comments)


def social(items, user):
    """Reactions {emoji: {n, mine}} and comments of the wall items."""
    keys = [i['item'] for i in items]
    reacts, comments = {}, {}
    for r in WallReaction.objects.filter(item__in=keys).select_related('user'):
        d = reacts.setdefault(r.item, {}).setdefault(r.emoji, {'n': 0, 'mine': False, 'users': []})
        d['n'] += 1
        d['mine'] = d['mine'] or r.user_id == user.id
        d['users'].append(uname(r.user))
    for c in WallComment.objects.filter(item__in=keys).select_related('user'):
        comments.setdefault(c.item, []).append(comment_json(c, user))
    for i in items:
        i['reactions'] = reacts.get(i['item'], {})
        i['comments'] = comments.get(i['item'], [])
    return items


def uname(u):
    from .views import uname as _u
    return _u(u)


def comment_json(c, user=None):
    return {'id': c.id, 'item': c.item, 'body': c.body, 'author': uname(c.user), 'author_id': c.user_id, 'at': c.at,
            'mine': bool(user and c.user_id == user.id)}


def wall(request):
    today = timezone.localdate()
    emps = branch_emps(request).select_related('emp_desgntn_id', 'emp_dept_id')
    items = _celebrations(list(emps), today) + _joiners(emps, today)
    for a in my_announcements(request):
        items.append({'item': f'ann:{a.id}', 'kind': 'ann', 'date': timezone.localtime(a.created_at).date() if a.created_at else today,
                      'at': a.created_at, 'title': a.title, 'message': a.message, 'sticky': a.is_sticky, 'author': uname(a.created_by),
                      'comments_allowed': a.allow_comments, 'has_attachment': bool(a.attachment), 'id': a.id, 'today': False})
    order = {'ann': 2, 'bday': 0, 'anniv': 0, 'join': 1}

    def key(i):   # today's celebrations first, sticky announcements next, then the newest first
        return (0 if i.get('today') else 1, 0 if i.get('sticky') else 1, -i['date'].toordinal(), order[i['kind']])
    items.sort(key=key)
    return {'today': today, 'emojis': WALL_EMOJIS, 'items': social(items, request.user)}


def notify_celebrant(item, user, text, emoji=False):
    """A wish on a birthday / anniversary / welcome goes to the bell of that employee."""
    kind, _, rest = item.partition(':')
    if kind not in ('bday', 'anniv', 'join'):
        return
    from EmpManagement.models import emp_master
    e = emp_master.objects.filter(pk=rest.split(':')[0]).select_related('users').first()
    if e is None or not e.users_id or e.users_id == user.id:
        return
    try:
        from . import collab as C
        what = {'bday': 'your birthday', 'anniv': 'your work anniversary', 'join': 'your first days'}[kind]
        C._bell(e.users, f'{uname(user)} {"reacted" if emoji else "wrote"} on {what}', text[:200], '/main-sidebar/wall', 'Chatter.wall', item)
    except Exception:
        pass


# ------------------------------------------------------------------ my team
def team_members(user, include_indirect=False):
    from EmpManagement.models import emp_master
    me = my_emp(user)
    q = Q(emp_reporting_manager=user)
    if me is not None:
        q |= Q(emp_reports_to=me)
    ids = set(active_emps().filter(q).exclude(pk=me.pk if me else 0).values_list('id', flat=True))
    if include_indirect:
        from DashboardManagement.services import team_ids
        ids |= set(team_ids(user))
        frontier = list(ids)
        for _ in range(6):   # Reports to (employee) chains too
            new = set(active_emps().filter(emp_reports_to_id__in=frontier).values_list('id', flat=True)) - ids
            if not new:
                break
            ids |= new
            frontier = list(new)
    if me is not None:
        ids.discard(me.pk)
    return emp_master.objects.filter(pk__in=ids).select_related('emp_dept_id', 'emp_desgntn_id', 'emp_branch_id')


def is_manager_of(request, emp):
    from .views import is_admin
    if is_admin(request):
        return True
    return team_members(request.user, include_indirect=True).filter(pk=emp.pk).exists()


def oo_json(o, viewer_is_manager=True):
    d = {'id': o.id, 'employee_id': o.employee_id, 'employee': emp_name(o.employee), 'manager': uname(o.manager), 'manager_id': o.manager_id,
         'date': o.date, 'agenda': o.agenda, 'notes': o.notes, 'actions': o.actions or [], 'shared': o.shared,
         'open_actions': sum(1 for a in (o.actions or []) if not a.get('done')), 'updated_at': o.updated_at}
    if viewer_is_manager:
        d['private_note'] = o.private_note
    return d


def team_hub(request, include_indirect=False):
    from DashboardManagement import services as S
    user = request.user
    today = timezone.localdate()
    emps = list(team_members(user, include_indirect))
    ids = [e.id for e in emps]
    out = {'today': today, 'headcount': len(ids), 'working_day': S.is_working_day(today), 'indirect': include_indirect}
    st = S.today_status(team_members(user, include_indirect), today) if ids else {'att': {}, 'present': [], 'late': [], 'on_leave': [],
                                                                                   'leave_rows': {}, 'not_punched': []}
    upcoming = {}
    for l in S.leave_on(ids, today + timedelta(days=1), today + timedelta(days=14), statuses=('approved', 'pending')).order_by('start_date'):
        upcoming.setdefault(l.employee_id, l)
    docs = {}
    try:
        from DashboardManagement.documents import current_documents
        for d in current_documents(ids, today):
            if d['days'] is not None and d['days'] <= 60:
                docs.setdefault(d['employee_id'], []).append({'type': d['type'], 'number': d['number'], 'expiry': d['expiry'], 'days': d['days']})
    except Exception:
        pass
    goals = {}
    try:
        from django.apps import apps as A
        GS = A.get_model('PerformanceManagement', 'GoalSheet')
        Cy = A.get_model('PerformanceManagement', 'AppraisalCycle')
        cyc = Cy.objects.filter(status__in=('active', 'calibration')).order_by('-period_from').first()
        if cyc and ids:
            for r in S.team_goals(ids, cyc)['rows']:
                goals[r['employee_id']] = r
            out['goal_cycle'] = cyc.name
    except Exception:
        pass
    last_oo, next_oo = {}, {}
    for o in OneOnOne.objects.filter(employee_id__in=ids).order_by('date'):
        if o.date <= today:
            last_oo[o.employee_id] = o.date
        elif o.employee_id not in next_oo:
            next_oo[o.employee_id] = o.date
    cel = {}
    for c in _celebrations(emps, today, back=0, ahead=30):
        cel.setdefault(c['employee_id'], c)
    members = []
    for e in emps:
        a = st['att'].get(e.id)
        lv = st['leave_rows'].get(e.id)
        if lv is not None:
            state, label = 'out', f"On leave{(' – ' + lv.leave_type.name) if lv.leave_type_id else ''} until {lv.end_date:%d %b}"
        elif a is not None:
            late = e.id in st['late']
            t = getattr(a, 'check_in_time', None) or getattr(a, 'check_in', None)
            state, label = ('late' if late else 'in'), (('Late – in at ' if late else 'In since ') + (t.strftime('%H:%M') if hasattr(t, 'strftime') else str(t or '')[:5])).strip()
        elif e.id in st['not_punched']:
            state, label = 'missing', 'Not in yet'
        else:
            state, label = 'off', 'Day off' if not out['working_day'] else 'Not in yet'
        g = goals.get(e.id)
        up = upcoming.get(e.id)
        c = cel.get(e.id)
        members.append({'id': e.id, 'code': e.emp_code, 'name': emp_name(e), 'photo': photo(e), 'page': emp_page(e.id),
                        'designation': str(e.emp_desgntn_id) if e.emp_desgntn_id_id else '', 'department': str(e.emp_dept_id) if e.emp_dept_id_id else '',
                        'state': state, 'state_label': label,
                        'next_leave': {'from': up.start_date, 'to': up.end_date, 'status': up.status,
                                       'type': up.leave_type.name if up.leave_type_id else 'Leave'} if up else None,
                        'documents': docs.get(e.id, []),
                        'goals': {'n': g['goals'], 'progress': g['progress'], 'at_risk': g['at_risk'], 'done': g['done']} if g else None,
                        'last_1on1': last_oo.get(e.id), 'next_1on1': next_oo.get(e.id),
                        'celebration': {'kind': c['kind'], 'date': c['date'], 'years': c.get('years')} if c else None})
    rank = {'out': 2, 'missing': 0, 'late': 1, 'in': 3, 'off': 4}
    members.sort(key=lambda m: (rank[m['state']], m['name'].lower()))
    out['members'] = members
    out['counts'] = {k: sum(1 for m in members if m['state'] == k) for k in ('in', 'late', 'out', 'missing', 'off')}
    try:   # v1.58.0 matrix reporting: people for whom this user is the functional manager or the manager of an assignment
        from ZeoHR.matrix import dotted_reports
        direct = {m['id'] for m in members}
        out['dotted'] = [{'id': e.id, 'code': e.emp_code, 'name': emp_name(e), 'photo': photo(e), 'page': emp_page(e.id), 'relation': rel,
                          'designation': str(e.emp_desgntn_id) if e.emp_desgntn_id_id else '', 'department': str(e.emp_dept_id) if e.emp_dept_id_id else ''}
                         for e, rel in dotted_reports(user) if e.id not in direct]
    except Exception:
        out['dotted'] = []
    try:
        out['approvals'] = S.summarize_approvals(S.pending_approvals(user))
    except Exception:
        out['approvals'] = {'total': 0, 'by_module': [], 'items': []}
    out['documents'] = sorted([dict(d, employee=m['name'], employee_id=m['id']) for m in members for d in m['documents']], key=lambda d: d['days'])
    out['celebrations'] = sorted([dict(m['celebration'], employee=m['name'], employee_id=m['id']) for m in members if m['celebration']],
                                 key=lambda c: c['date'])
    out['upcoming_leave'] = sorted([dict(m['next_leave'], employee=m['name'], employee_id=m['id']) for m in members if m['next_leave']],
                                   key=lambda l: l['from'])
    out['one_on_ones'] = [oo_json(o) for o in OneOnOne.objects.filter(employee_id__in=ids).select_related('employee', 'manager')[:10]]
    return out


# ------------------------------------------------------------------ flags and release notes
def flags(user):
    return {f.key: f.value for f in UserFlag.objects.filter(user=user)}


def set_flag(user, key, value):
    key = str(key or '').strip()[:80]
    if not key:
        return None
    f, _ = UserFlag.objects.update_or_create(user=user, key=key, defaults={'value': value if value is not None else {}})
    return f


RELEASES = [
    {'version': '1.62.0', 'date': '2026-10-26', 'title': 'Security hardening and history cleaning', 'items': [
        ('cleaning_services', 'Log and history retention', 'How long inbox items, notifications, logs and the audit history are kept – preview, Clean now, a nightly job and a zip archive of the audit history removed.'),
        ('how_to_reg', 'Give access', 'Logins for employees without e-mail: a printed slip with a code, a WhatsApp / SMS code, a generated password or the kiosk – never one shared password.'),
        ('verified_user', 'Stronger sign-in', 'Short sign-in tokens, sign-out after idle time, sign-ins bound to the device, country rules and the password asked again before payroll approval, bank changes and exports.'),
        ('account_balance', 'Bank changes need a second person', 'An IBAN changed by HR waits for a second person, and the employee is told about every change.'),
        ('shield', 'Data protection', 'Masked IBAN, ID and salary numbers, export rights and row limits, watermarked PDFs, safe uploads with a virus scan, files only for users of their record.'),
        ('security', 'Security dashboard', 'Users without the authenticator, wrong passwords, sign-ins, API keys, exports and e-mails to administrators about security events.'),
        ('receipt_long', 'Invoices instead of journal vouchers', 'Property invoices, receipts and vendor bills can go to Odoo or Zoho Books as invoices, payments matched to them and bills (Accounting › Settings).'),
        ('table_view', 'Demo and test data workbook', 'Load a whole company from one Excel workbook – six months of requests, approvals, attendance, payroll and billing replayed month by month (load_workbook).'),
    ]},
    {'version': '1.61.0', 'date': '2026-10-26', 'title': 'Security, integration and scheduling', 'items': [
        ('lan', 'Networks allowed and sign-in alerts', 'Limit a company to its own networks; users get an e-mail after a sign-in from a new network and device, and answer "this was me" or "this was not me".'),
        ('api', 'API v1', 'A stable API for integrations with API keys that carry scopes – employees, organisation, leave, attendance (also punches from time clocks) and payslip totals.'),
        ('call_split', 'Split shifts and skills cover', 'Shifts in two or more parts with unpaid time between them; shifts that need skills, with the gaps and who can cover them on the roster.'),
        ('gavel', 'Legal hold, letter versions, insurer file', 'Hold an employee\'s records against deletion; every wording of a letter template is kept; the member file of a medical plan for the insurer.'),
        ('smart_toy', 'HR tickets sorted', 'New HR tickets get a suggested category and priority (built-in rules, or your external AI when switched on).'),
        ('backup', 'Backup command', 'python manage.py zeo_backup makes and checks a backup of the database and the files.'),
    ]},
    {'version': '1.59.0', 'date': '2026-10-25', 'title': 'Legal rules by date', 'items': [
        ('event', 'Statutory rates by date', 'When a contribution or tax rate changes, add the new rates from the day they start – each payroll month uses the rates of its 1st day.'),
        ('gavel', 'Legal rates', 'The GPSSA rates and salary limits ZEO uses, each with a value from a date; without one the built-in figure applies.'),
        ('history_edu', 'Gratuity law changes', 'New gratuity rules from a date in Country policies: the end of service uses the rules of the last working day.'),
        ('replay', 'Re-runs keep their month', 'A re-run of an earlier month, the provisions and the gratuity report use the rules in force on their own date.'),
    ]},
    {'version': '1.58.0', 'date': '2026-10-25', 'title': 'Assignments and matrix reporting', 'items': [
        ('swap_horiz', 'Assignments and secondments', 'An employee can work in a second branch, department, project or acting role at the same time, with a share of the time and a manager.'),
        ('call_split', 'Functional managers', 'A dotted-line manager besides the reporting manager: an approval level of its own, a Dotted line group in My team, the org chart.'),
        ('rate_review', 'Functional input in appraisals', 'The functional manager rates the employee; the reporting manager sees the input and a blended score.'),
        ('pie_chart', 'Cost split and FTE', 'Payroll cost follows the share of each assignment; a report shows who works where and the full-time equivalents by department.'),
    ]},
    {'version': '1.57.0', 'date': '2026-10-25', 'title': 'Careers page and talent pools', 'items': [
        ('public', 'Careers page', 'A public page with your open jobs and an apply form, in English and Arabic – applications arrive in the candidate pipeline.'),
        ('quiz', 'Questions on the apply form', 'Ask yes / no, number or choice questions; applications that miss a needed answer are flagged.'),
        ('rss_feed', 'Job-board feed', 'An XML and JSON feed of the open jobs for LinkedIn, Indeed, Bayt and other job sites.'),
        ('groups', 'Talent pools', 'Keep strong candidates with tags and write to them about new jobs – only those who agreed that you keep their CV.'),
    ]},
    {'version': '1.56.1', 'date': '2026-10-25', 'title': 'Release checklist fixes', 'items': [
        ('lock', 'Files need sign-in', 'Uploaded files open only for signed-in users; company logos stay public.'),
        ('admin_panel_settings', 'Tighter rights', 'A login without a right can no longer create set-up records, export other employees or list every user.'),
        ('account_tree', 'Standard approval chains', 'A new company starts with the reporting manager approving every request type.'),
        ('speed', 'Faster big lists', 'The employee list of a company with 1,280 employees opens in under a second.'),
        ('keyboard', 'Escape closes pop-ups', 'Escape closes the open pop-up on every screen.'),
    ]},
    {'version': '1.56.0', 'date': '2026-10-25', 'title': 'Setup guide', 'items': [
        ('checklist', 'Setup guide', 'A new company step by step to the first payroll: 14 steps and one optional, defaults of the country, checks before each Next, readiness check.'),
        ('tag', 'One numbering pattern', 'One pattern for every document type – type, branch, year and counter – that restarts every year or month.'),
        ('event_available', 'Mid-year go-live', 'Salary paid up to, unpaid days and gratuity start before ZEO – no second salary for a month already paid.'),
        ('mark_email_read', 'Sign-in links', 'Employees choose their own password from an e-mail link – no password in the e-mail.'),
    ]},
    {'version': '1.55.0', 'date': '2026-10-25', 'title': 'Pay calendars, off-cycle runs and arrears', 'items': [
        ('date_range', 'Pay calendars', 'Pay some employees weekly, bi-weekly or semi-monthly once the frequency is switched on in Payroll controls – each period is a normal payroll run.'),
        ('bolt', 'Off-cycle runs', 'Pay a bonus, commission, correction or final settlement outside the monthly payroll.'),
        ('history', 'Arrears', 'A salary change with an earlier effective date: the difference of every payslip since then is paid with the next payslip.'),
    ]},
    {'version': '1.54.0', 'date': '2026-10-25', 'title': 'Single sign-on and Active Directory', 'items': [
        ('key', 'Sign in with Microsoft or Google', 'Your users sign in with the account of your company – Microsoft (Office 365), Google Workspace, another OpenID Connect provider or SAML (ADFS, Okta …).'),
        ('lock', 'Password off', 'Optionally switch off the password for the users of your domain; new employees can get their self-service login at the first sign-in.'),
        ('dns', 'Active Directory', 'On-premises Active Directory: the Windows password is checked by your domain controller, directory groups give ZEO permission groups, and a nightly sync switches off accounts disabled in AD.'),
        ('sync', 'Provisioning (SCIM)', 'Microsoft Entra ID and Okta create, change and switch off ZEO logins themselves.'),
        ('smartphone', 'Phone app', 'The phone app signs in through the phone\'s browser and continues by itself.'),
    ]},
    {'version': '1.53.2', 'date': '2026-10-24', 'title': 'Documents: update the record, upload many files', 'items': [
        ('fact_check', 'Update the employee record', 'Fill from document now compares the passport, visa or Emirates ID with the employee record and saves the values you tick – the change history says which document they came from.'),
        ('drive_folder_upload', 'Upload document files', 'One zip with the scans of many employees: matched by folder and file name, read, checked by you, then imported – and undone in Import history if needed.'),
    ]},
    {'version': '1.53.1', 'date': '2026-10-24', 'title': 'Arabic for the new screens', 'items': [
        ('translate', 'Arabic', 'The screens of 1.49 to 1.53 – sign-in security, out of office, report builder, dashboards, data retention, test company, WhatsApp / SMS, helpdesk and more – and their messages in Arabic.'),
        ('link', 'CRM and Property', 'An opportunity shows the reservation made for it in Property.'),
    ]},
    {'version': '1.53.0', 'date': '2026-10-24', 'title': 'Your own reports and dashboards', 'items': [
        ('table_view', 'Report builder', 'Pick a record type, columns, filters, grouping and totals – the report opens in the report centre and can be scheduled.'),
        ('space_dashboard', 'Dashboards', 'Make dashboards from any report: numbers, bars, a line by month or a short table, private or shared.'),
        ('currency_exchange', 'Payroll in one currency', 'The group sees the payroll of every company in one currency with the exchange rates of the month.'),
        ('science', 'Test company', 'A company with your setup and none of your data, to try changes and train people – e-mails off.'),
        ('privacy_tip', 'Data retention and privacy', 'Anonymise employees who left years ago and export all the data about a person on request.'),
    ]},
    {'version': '1.52.0', 'date': '2026-10-24', 'title': 'Administration and sign-in security', 'items': [
        ('phonelink_lock', 'Authenticator app', 'Sign in with a 6-digit code from Google or Microsoft Authenticator, recovery codes, and the company can require it.'),
        ('password', 'Password rules and lock', 'Length, letters, digits, symbols, expiry and history; the sign-in locks after wrong passwords and HR unlocks it.'),
        ('devices', 'Where you are signed in', 'Every browser and phone you use, and sign out of the others at once.'),
        ('beach_access', 'Out of office', 'While you are away your approvals go to a colleague – also the ones already waiting.'),
        ('schedule_send', 'Scheduled reports', 'Any report e-mailed as Excel or CSV every day, week or month.'),
        ('webhook', 'API keys and webhooks', 'Other systems read ZEO with a key and hear about new and changed records; automations can call a webhook.'),
        ('undo', 'Undo an import', 'Imports remember how the columns of your file match, and an import can be undone.'),
        ('visibility', 'View as a user', 'Administrators see ZEO as a user to help them – read only by default and written to the audit log.'),
        ('monitor_heart', 'System health', 'Database, background jobs, scheduler, e-mail, storage, backups and licence on one page.'),
    ]},
    {'version': '1.51.0', 'date': '2026-10-24', 'title': 'Employee help and messages', 'items': [
        ('contact_support', 'Ask HR', 'Questions to HR by category with answer and solution times; replies, rating and reopening in Self service.'),
        ('event_busy', 'Leave conflicts', 'Colleagues of the same role away, department staffing and blackout periods shown before a leave is sent or approved.'),
        ('chat', 'WhatsApp and SMS', 'Approvals, decisions, payslips and HR answers through your own WhatsApp / SMS provider – optional.'),
        ('tablet', 'Self-service on the kiosk', 'Staff without a phone see payslips and leave balance, ask for leave and ask HR with their ID and PIN.'),
        ('notifications_active', 'Push with your own app', 'Use your Firebase project for push notifications and choose which ones are sent.'),
    ]},
    {'version': '1.50.0', 'date': '2026-10-24', 'title': 'Payroll planning and travel', 'items': [
        ('science', 'Payroll what-if', 'Try an increment or a new allowance on the real payroll before you apply it: the difference per employee, department and component.'),
        ('call_split', 'Cost allocation', 'Split payroll cost over projects and cost centres by percentages or timesheet hours; CSV and posting to accounting.'),
        ('document_scanner', 'Read the receipt', 'Amount, date, VAT and TRN read from the receipt photo; monthly limits, duplicates and claims that differ from the receipt are flagged.'),
        ('flight_takeoff', 'Travel requests', 'Ticket, hotel, per diem and advance in one request; the travel desk books it and the trip shows every step to the settlement.'),
        ('translate', 'English + Arabic payslip', 'Each label in both languages on the same payslip – for all payslips, or downloaded from My payslips.'),
    ]},
    {'version': '1.49.0', 'date': '2026-10-24', 'title': 'Payroll and time', 'items': [
        ('savings', 'Monthly provisions', 'Gratuity, leave salary and air-ticket liability of every employee at each month end, the change on the month before, CSV and posting.'),
        ('credit_score', 'Loans', 'Schedule of paid and planned months, top-up, new instalment, pause until a date and early settlement; My loans for employees.'),
        ('gavel', 'Overtime law of your country', 'UAE, KSA, Oman, Qatar, Kuwait and India rates on the attendance rules; employees see how their overtime was paid.'),
        ('beach_access', 'Leave balances explained', 'How each balance is earned, carried and encashed, what is still to come by the year end and carried days that expire.'),
        ('photo_camera', 'Regularisation with a proof photo', 'A reason list with photo / document and monthly limits; kiosk photos with every punch and a review page for HR.'),
        ('swap_horiz', 'Offer a shift', 'Give your shift to a colleague of the branch who takes it; the manager approves.'),
    ]},
    {'version': '1.48.0', 'date': '2026-10-24', 'title': 'HR operations', 'items': [
        ('gavel', 'Disciplinary and grievance cases', 'Investigation, show-cause notice and the written reply, hearing, decision, appeal – the warning goes to the employee timeline.'),
        ('logout', 'Exit interviews', 'Leavers answer in Self service; reasons for leaving, rehire and recommend show on the HR operations home.'),
        ('event_busy', 'Document expiry hub', 'Every expired and expiring passport, visa, ID, labour card and insurance in one page – start renewals in one click.'),
        ('account_balance', 'PRO services', 'Visas, IDs, labour cards, medicals and company licences with the usual steps of your country, fees and results.'),
        ('assignment_turned_in', 'Asset handover with e-signature', 'Issue and return of laptops, phones and tools, signed by the employee; a signed return closes the allocation.'),
    ]},
    {'version': '1.47.0', 'date': '2026-10-24', 'title': 'Find and move faster', 'items': [
        ('search', 'Search everything', 'Press Ctrl+K (or click the magnifier) to find employees, records of every app, pages and reports.'),
        ('add_circle', 'Create from anywhere', 'The + button creates a to-do, a leave request or a record of any app without hunting for the menu.'),
        ('keyboard', 'Keyboard shortcuts', 'Press ? to see them: / to search, N to create, G then H for home, G then I for the inbox.'),
        ('groups', 'My team', 'Managers get one page: who is in or out today, approvals, expiring documents, goals, birthdays and 1:1 notes.'),
        ('celebration', 'Company wall', 'Birthdays, work anniversaries, new joiners and announcements – with reactions and wishes.'),
        ('tour', 'Guided tours', 'A short walkthrough the first time you open a main page. Start it again from the help button.'),
    ]},
    {'version': '1.46.0', 'date': '2026-10-23', 'title': 'Records and collaboration', 'items': [
        ('forum', 'Threads on notes', 'Reply under a note, react with an emoji and mark the thread resolved.'),
        ('alarm', 'Remind me', 'A reminder on any record arrives in your inbox on the day.'),
        ('mic', 'Voice and photo notes', 'Record a short voice note or take a photo from the note box.'),
        ('functions', 'List totals and colours', 'Totals under the list, colour rules and editing cells in the list.'),
        ('policy', 'Audit log', 'Every change in the company in one page (Settings).'),
        ('print', 'Print any record', 'A PDF of the record with its notes and history.'),
    ]},
    {'version': '1.45.0', 'date': '2026-10-22', 'title': 'Followers, inbox and tags', 'items': [
        ('notifications', 'Follow records', 'Followers get the messages and changes of a record.'),
        ('inbox', 'Inbox', 'Messages, mentions, e-mail replies and approvals in one place.'),
        ('star', 'Starred and recent', 'Star a record to find it again from the launcher.'),
        ('sell', 'Tags', 'Colour tags on any record, filter lists by them.'),
    ]},
]
