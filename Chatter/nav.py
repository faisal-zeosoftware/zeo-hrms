# v1.67.9 (screens) item 19: dates shown dd/mm/yyyy (date-times dd/mm/yyyy HH:MM)
from zeo.tz import today as _zt_today   # v1.73.16 the user's / company's day, not the server's UTC day
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
    today = _zt_today()
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
    today = _zt_today()
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
            state, label = 'out', f"On leave{(' – ' + lv.leave_type.name) if lv.leave_type_id else ''} until {lv.end_date:%d/%m/%Y}"
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
    {'version': '1.73.18', 'date': '2026-11-17', 'title': 'Demo data, GCC rules and checked business apps', 'items': [   # v1.73.18 the team's demo data and 1.73.4 re-test, items 147, 155, 283, 289
        ('dataset', 'Do you want demo data?', 'Create company asks: no demo, Simple demo (1 month) or Heavy demo (6 months) – the time each takes is shown.'),
        ('public', 'All industries or one', '10 demo companies in the UAE, Saudi Arabia, Qatar, Oman, Bahrain, Kuwait and India – or one company of the industry you choose.'),
        ('autorenew', 'Always the latest months', 'The data ends today; Choose a company › Refresh demo data makes it again with new dates, Remove deletes the demo companies.'),
        ('upload_file', 'Large imports in parts', 'A large file is imported 200 rows at a time – the row numbers stay those of the file and Undo takes back the whole file.'),
        ('fact_check', 'Business apps checked field by field', 'CRM, Property, HSE, Maintenance and Assets: negative costs, prices and quantities and end dates before start dates are refused.'),
        ('account_balance', 'Kuwait PIFSS by tiers', 'Kuwaitis of a Kuwait branch: employee 10.5 % up to KWD 1,500 and 8 % above, employer 11.5 %, wage 230 – 2,750.'),
        ('savings', 'Oman Social Protection Fund', 'Omanis of an Oman branch: employee 8 %, employer 13.5 % – 14.5 % from 19/07/2026 with sick leave; ceiling OMR 3,000.'),
        ('dark_mode', 'Ramadan hours by country', 'A branch in Saudi Arabia, Qatar, Oman or Bahrain: at most 6 hours of work a day in Ramadan; Kuwait 36 hours a week.'),
        ('gavel', 'Checked in two sources', 'Kuwait resignation shares confirmed; India TDS named for the Income-tax Act 2025 (slabs unchanged).'),
    ]},
    {'version': '1.73.17', 'date': '2026-11-17', 'title': 'API for AI tools: proposals you approve', 'items': [   # v1.73.17 Ask ZEO A9
        ('smart_toy', 'AI tools propose', 'An AI tool with an API key (modules, reports, workflow or settings scope) proposes a change in plain words; ZEO keeps it as a draft.'),
        ('fact_check', 'You approve', 'Settings › Ask ZEO › Waiting for approval: each draft with its before and after – Approve or Reject. A key can never approve.'),
        ('history', 'Logged and undoable', 'Every proposal says which API key made it and who approved it; an applied change can be undone.'),
        ('hub', 'MCP connector', 'tools/mcp/zeo_mcp.py connects AI tools that speak MCP to ZEO (read with API v1, propose changes). Guide: tools/mcp/README.md.'),
    ]},
    {'version': '1.73.16', 'date': '2026-11-17', 'title': "Time zones: your day, your branch's day", 'items': [   # v1.73.16
        ('schedule', 'Your own time zone', 'Language menu › Time zone: automatic (your branch, else the company) or a zone you choose – "today" on the server is your day.'),
        ('public', "Each branch's day", "Attendance and leave of a Riyadh or Bangalore branch follow that branch's time zone (the branch › Registration and site details › Timezone)."),
        ('event_repeat', "Jobs on the company's day", "Daily jobs (reminders, accruals) use the company's day – not the server's UTC day, which was yesterday between 00:00 and 04:00."),
        ('lock_clock', 'Access checked safely', 'If the server is too slow to check your access, the page is not opened: "Couldn\'t check your access" with Try again.'),
    ]},
    {'version': '1.73.15', 'date': '2026-11-17', 'title': 'Setup by screens: imports and new companies', 'items': [   # v1.73.15 = the team's 1.73.4 (fresh-install walkthrough of 1.73.3)
        ('event', 'Financial year', 'Create company and Company save the financial year start you choose – 01/01 was saved as 31/12 of the year before.'),
        ('hourglass_top', 'New companies', 'A company still being created is listed with its progress and opens when it is ready.'),
        ('upload_file', 'More imports', 'Air ticket rules, assign weekends, shifts, expense category limits, increment bands, assets, asset allocations, late in / early out requests, timesheets and skill levels from a file; large imports no longer stop with "Request was throttled".'),
        ('link', 'Import links', 'A nomination or other record without a code that is not found is reported as not found, not linked to another record.'),
        ('work_history', 'Compensatory work requests', 'A screen to approve the compensatory work requests – each approved request credits the day.'),
    ]},
    {'version': '1.73.14', 'date': '2026-11-17', 'title': 'App homes, default dashboards and self-service', 'items': [   # v1.73.14 = the team's 1.73.3 -> 1.73.6
        ('dashboard', 'Default dashboards', 'Company, HR, Payroll, CRM, Property, Maintenance, Assets, HSE and Projects dashboards are made once for the apps you use.'),
        ('touch_app', 'Click to see the records', 'A click on a bar, slice or row opens the records behind it – search, export, open the full report; a date filter for the whole dashboard.'),
        ('palette', 'New app homes', 'CRM, Property, Maintenance and Assets homes with a themed banner, KPI tiles and View dashboard.'),
        ('person', 'Self-service hero', 'Home › My dashboard: your photo, role, service years, manager, quick actions and live tiles.'),
    ]},
    {'version': '1.73.13', 'date': '2026-11-17', 'title': '"+ Create new …" in master drop-downs', 'items': [   # v1.73.13 = the team's 1.73.5 create from drop-down
        ('add_circle', 'Create a missing master from the drop-down', 'A department, leave type, customer or project you need is missing? Choose "+ Create new …" at the end of the drop-down.'),
        ('verified_user', 'Same checks and rights', "The record is saved through the master's own screen rules – its checks, rights and numbering."),
        ('block', 'Not for people or branches', 'Employees, users, companies and branches are added on their own screens.'),
    ]},
    {'version': '1.73.12', 'date': '2026-11-17', 'title': 'Fixes from the full manual test (10 employees, 3 payrolls)', 'items': [   # v1.73.12 = the team's 1.73.2 fixes
        ('flight', 'Air ticket allocations', 'A pending allocation now has Approve / Reject, so the employee can request the ticket.'),
        ('lock_open', 'No more false "No access"', 'A slow rights check no longer locks approvers (e.g. Resignation approvals) out for the session.'),
        ('mail', 'Resignation approved e-mail', 'The employee now gets the "Resignation approved" e-mail.'),
        ('payments', 'Final settlement by finance', 'The Payroll Manager group can check and pay final settlements.'),
        ('badge', 'Smaller fixes', 'Salary assignment form starts empty; undoing an employee import frees the codes; no duplicate GPSSA component; clearer approval set-up message.'),
    ]},
    {'version': '1.73.11', 'date': '2026-11-17', 'title': 'Document Center: industry packs', 'items': [   # v1.73.11 DMS phase 6
        ('domain', 'Industry packs', 'Ready categories, checklists and letter templates for 23 kinds of business – construction, healthcare, schools, logistics, hotels and more.'),
        ('fact_check', 'Only adds what is missing', 'Installing never changes or removes what the company has; templates come as drafts to read and publish.'),
        ('badge', 'The UAE papers', 'Trade licence, establishment card, Ejari tenancy, VAT and corporate tax, passport, visa, Emirates ID, labour contract and work permit.'),
    ]},
    {'version': '1.73.10', 'date': '2026-11-17', 'title': 'Document Center: scans read (OCR) and search in words', 'items': [   # v1.73.10 DMS phase 5
        ('document_scanner', 'Scans are read', 'Photos and scanned PDFs are read on the server, in English and Arabic, so their words can be searched.'),
        ('manage_search', 'Search in words', 'Ask "Khalid\'s passport" or "expired trade licences": categories, employees, dates and the words in the files are understood.'),
        ('auto_awesome', 'Answers with your AI', 'When the company\'s AI is on for Documents, it answers from the documents found and names them.'),
    ]},
    {'version': '1.73.9', 'date': '2026-11-17', 'title': 'Document Center: checklists, upload links, templates and e-signature', 'items': [   # v1.73.9 DMS phase 4
        ('checklist', 'Document checklists', 'The documents each employee, project or partner must have – the folder shows what is missing, expired or to review.'),
        ('upload_file', 'Upload links', 'Ask a candidate, supplier or tenant for documents: they upload through a link into the right folder; you accept or reject.'),
        ('draw', 'Templates and e-signature', 'Controlled templates make PDFs with the record\'s details; send any document for signature inside or outside the company.'),
    ]},
    {'version': '1.73.8', 'date': '2026-11-17', 'title': 'Leave setup on one page, more leave rules, anniversary credit and leave grants', 'items': [   # v1.73.8
        ('tune', 'Leave setup on one page', 'Leave › Leave setup: one page per leave type – basics, days by policy, who & when, special rules – and Check my setup.'),
        ('rule', 'More leave rules', 'Windows to ask, days in a row, gap and how often, not next to another leave, marital status, designations and exceptions, a balance cap, restricted holidays, over the balance as unpaid.'),
        ('card_giftcard', 'Anniversary credit and grants', 'Extra days on the service anniversary (e.g. 10 days after 1 year), leave years from the joining date, leave grants with a reason, and four new leave reports.'),
    ]},
    {'version': '1.73.7', 'date': '2026-11-16', 'title': 'Payroll and leave categories, reports by category, no day paid twice', 'items': [   # v1.73.7
        ('label', 'Payroll and leave categories', 'Payroll › Payroll categories and Leave › Leave categories: the system ones plus your own; what each counts for (gratuity, pension, leave salary, WPS).'),
        ('view_column', 'Reports by category', 'Payroll and leave reports show one column per category – every Basic item in one Basic column – or one per item.'),
        ('health_and_safety', 'Safer payroll', 'Basic is every Basic item added up; no day is paid twice; a category change can switch the salary template; a health check before each run.'),
    ]},
    {'version': '1.73.6', 'date': '2026-11-16', 'title': 'Document Center: expiry and renewal, retention, legal hold and correspondence', 'items': [   # v1.73.6 DMS phase 3
        ('event_repeat', 'Expiry and renewal', 'Reminders 90, 60, 30, 15 and 7 days before a document expires go to its renewal owner and up to a named user when nothing is done; Renew files the new copy and replaces the old one.'),
        ('inventory_2', 'Retention and legal hold', 'Retention counts from the date the category says; at its end the document comes up for review. Legal hold stops disposal; every disposal is kept in a register with its certificate.'),
        ('mail', 'Correspondence register', 'Incoming and outgoing letters get IN / OUT numbers, are assigned with a reply-by date, approved before dispatch and linked to their replies and scanned documents.'),
    ]},
    {'version': '1.73.5', 'date': '2026-11-16', 'title': 'Ask ZEO: apps tried in the test company, print layouts and spoken SOPs', 'items': [   # v1.73.5
        ('science', 'Test company first', 'When the company has a test company, Ask ZEO offers to make the app there first; confirm the live one when it works.'),
        ('print', 'Print layout and settings', 'Apps made by Ask ZEO get an A4 print layout, and the preview shows their numbering and who may add and see records.'),
        ('record_voice_over', 'Spoken SOPs and code modules', 'The spoken words of an SOP video are read too; on own servers Ask ZEO drafts full code modules for a developer to review.'),
    ]},
    {'version': '1.73.4', 'date': '2026-11-16', 'title': 'Ask ZEO builds apps from SOPs and writes scripts', 'items': [   # v1.73.4 (A10, A11, A14, A15)
        ('description', 'App from an SOP', 'Attach an SOP (PDF, Word, text, photo or short video) and ask Ask ZEO to make the app: fields, approvals and reminders – preview, confirm, undo.'),
        ('functions', 'Calculated fields and tables', 'Apps built by Ask ZEO can have calculated fields, rate-table lookups, tables of rows, links to records and QR / barcodes.'),
        ('code', 'Scripts written by the AI', '"Write a script that …" – the AI writes a ZEO Developer script, tests it on sample data and saves it only after you read and confirm it.'),
    ]},
    {'version': '1.73.3', 'date': '2026-11-16', 'title': 'Ask ZEO and self-service fixes', 'items': [   # v1.73.3 (Ask ZEO test 1.73.2 + ESS test fixes merged)
        ('psychology', 'Ask ZEO', 'Better answers on CRM, assets, GCC and India questions; reports and dashboards built from a sentence (preview, confirm, undo); apps with any approver role and in Arabic.'),
        ('groups', 'CRM assignment', 'New leads and escalations go to the CRM roles, not to HR or administrators; amounts in the enquiry are read for assignment rules.'),
        ('person', 'Self-service', 'Withdraw a request nobody has acted on; tickets need the dates; limits on advances, expenses and short leave; requests without a workflow go to the reporting manager.'),
        ('lock', 'Rights', 'Employees no longer change attendance records, company objectives or HR-only pages.'),
    ]},
    {'version': '1.73.2', 'date': '2026-11-16', 'title': 'Same screen rules in HR, payroll and shifts', 'items': [   # v1.73.2 (1.73 screen rules – HR and payroll leftovers)
        ('filter_alt', 'One branch filter', 'Payroll runs, bulk final settlement and shift reports follow the branch chosen in the top bar.'),
        ('delete', 'Delete on pending records', 'Pending resignations and requests can be deleted from the list; decided ones are kept and the screen says why.'),
        ('edit_note', 'Row actions', 'Rosters and employee availability have Edit / Delete on each row and one "+ New"; overtime policy rows stay on one line.'),
        ('toggle_on', 'Active filter', 'Projects › Customers uses the same Active only / Inactive only filter as the other lists.'),
    ]},
    {'version': '1.73.1', 'date': '2026-11-16', 'title': 'CRM: industries, customer 360°, marketing and AI', 'items': [   # v1.73.1 (CRM 1.67.27 – 1.67.31 merged)
        ('category', 'Industry packs', '24 industry packs add the fields, pipelines and reports each industry needs; add the industries in CRM › Setup.'),
        ('groups', 'Customer 360° and onboarding', 'Enquiries, customer profiles, onboarding checklists, contracts, complaints, commissions and CRM documents.'),
        ('campaign', 'Marketing', 'Lead connectors, conversations, e-mail inboxes, templates, segments and campaign sends.'),
        ('auto_awesome', 'CRM AI', 'E-mail drafts, summaries, lead qualification, forecasts and deal analysis – reviewed by authorised users.'),
        ('build', 'Fixes', 'New companies keep every migration, and standard permission groups give their rights again.'),
    ]},
    {'version': '1.73.0', 'date': '2026-11-16', 'title': 'Same screen rules in Projects and HSE', 'items': [   # v1.73.0 (pending 158)
        ('edit', 'Edit and Delete on every row', 'Projects › Customers, AMC contracts, Renewals, Tickets and Projects, and HSE › Incidents, BBS observations and Corrective actions have Edit and Delete on each row, and Delete selected for ticked rows.'),
        ('block', 'Used records are kept', 'A customer already used is made inactive instead of deleted; contracts, tickets, projects and HSE records with work on them are not deleted – the message says what uses them.'),
        ('filter_alt', 'One branch filter', 'The dashboards of Projects, CRM, Property, Maintenance, Assets and HSE follow the branches ticked in the top bar – also for administrators – instead of a Branch drop-down of their own.'),
    ]},
    {'version': '1.70.9', 'date': '2026-11-16', 'title': 'HSE industry packs in Arabic', 'items': [   # v1.70.9 (open point of 1.70.2)
        ('translate', 'Checklists, permits and talks in Arabic', 'The content of the 15 HSE industry packs – checklist lines, permit checks, hazards and controls, toolbox talks – shows in Arabic, also in packs already installed.'),
    ]},
    {'version': '1.70.8', 'date': '2026-11-16', 'title': 'Files of the other apps in the Document Center', 'items': [   # v1.70.8 (pending: linked files)
        ('folder_shared', 'One place for a record\'s files', "An employee's folder shows the leave documents, letters, receipts, payslips, handovers and more; a project's folder its task and ticket files – by source, never copied, with each app's rights."),
        ('content_copy', 'Copy to Document Center', 'Make a managed document (versions, approval, distribution) from any of them; search finds them by name or source.'),
    ]},
    {'version': '1.70.7', 'date': '2026-11-16', 'title': 'Document Center fields', 'items': [   # v1.70.7 (pending: Document Center fields like the form designer)
        ('dynamic_form', 'The 20 field types', "A category's own fields get the form designer's types – amount, Yes / No, multi-select, employee, file, rating and more – with help text, default value, hidden / read-only and order."),
        ('note_add', 'Fields of every document and of folders', 'Document Center › Settings › Fields: fields on every document whatever its category, and folder fields in every folder\'s details. "Add field" and "Arrange fields" in the document window.'),
        ('manage_search', 'Search and export by field', 'Search by a field\'s value, and export the found documents to Excel with a column per field.'),
    ]},
    {'version': '1.70.6', 'date': '2026-11-16', 'title': "Today's date on the screens", 'items': [   # v1.70.6 (found in the 1.70.5 tests: the UTC day)
        ('today', 'Your own day, also after midnight', "The screens took today's date from the UTC clock, so between 00:00 and 04:00 in the UAE they showed yesterday. Punch map, the app forms, HSE due dates, Projects and Expenses now use your own calendar day."),
        ('event_available', 'Dates you pick stay the same', 'A date chosen in a calendar is sent as that day, never the day before.'),
    ]},
    {'version': '1.70.5', 'date': '2026-11-16', 'title': 'Document date and posting date', 'items': [   # v1.70.5 (pending: GL documents with both dates)
        ('event', 'Two dates on every journal', 'Accounting journals show the document date (payslip month, invoice, receipt …) and the posting date. The posting date starts as the document date and can be changed until the journal is posted.'),
        ('lock', 'Books closed up to', 'Settings of the accounting integration: no journal is posted on or before the date you close; a journal dated there is held until you change its posting date.'),
        ('rule', 'Right dates from the apps', 'Property bills use their invoice date (not the due date), vendor bills the supplier\'s invoice date, commissions the approval date; recoveries keep their own date; depreciation runs have a posting date.'),
        ('filter_alt', 'Filter by either date', 'Accounting journals: filter by document or posting date, and list the journals posted in a different period than their document.'),
    ]},
    {'version': '1.70.4', 'date': '2026-11-16', 'title': 'Ask ZEO', 'items': [   # v1.70.4 = Ask ZEO built as 1.71.0 – 1.74.0 on 1.67.26 (their four entries in one)
        ('construction', 'A custom module from a sentence', 'Ask "Create a vehicle request module with vehicle type (car, van or bus), from date, to date and purpose, approved by the line manager then HR": the fields and the Blueprint (states and moves) are shown first and made when you confirm.', ('hrms', 'settings')),
        ('add_box', 'Add fields to a module', 'Ask "Add a field driver needed (yes/no) to the vehicle request module" – existing records keep their data; Undo puts the old design back.', ('hrms', 'settings')),
        ('account_tree', 'Setup, data and development in one place', 'Setup checks and changes, client files loaded into the data workbook, and custom modules – all as a preview to confirm, recorded "via Ask ZEO" and undone in one click.', ('hrms', 'projects', 'settings')),
        ('search', 'Explain any record by its number', 'Ask "explain WO-00012" or "status of TKT-00045": the record of any app you may open, its main fields, status and last steps, with the link.', ('hrms', 'projects', 'settings')),
        ('fact_check', 'Is the app ready?', 'Ask "Is CRM ready?" or "Check the HSE setup": activation, the industry pack review, roles and the setup lists, each with the link that fixes it.', ('hrms', 'projects', 'settings')),
        ('inbox', 'What is waiting for me', 'Ask "What is waiting for me?": the work lists of Projects, CRM, Property, Maintenance, Assets and HSE in one table.', ('hrms', 'projects')),
        ('menu_book', 'Guides for every app', 'Rent billing and collection, permits to work, asset counts, preventive maintenance, the sales pipeline and project delivery – step by step with the screens.', ('hrms', 'projects')),
        ('smart_toy', 'Your company\'s own AI', 'Connect the company\'s own AI key in Settings › AI settings (the provider is chosen there); Ask ZEO then answers free questions, writes, translates and reads files. Figures still come from ZEO.', ('hrms', 'settings')),
        ('description', 'Answers from your policies', 'Add the handbook, policies and SOPs in Settings › Ask ZEO; answers name the document and the section, and each document says who may read it.', ('hrms', 'settings')),
        ('upload_file', 'Load setup and data files', 'Upload a client file: Ask ZEO maps its columns to the data workbook, shows the first rows and loads it after you confirm.', ('hrms',)),
        ('help', 'Product and scope questions', 'Ask how ZEO does something or how to implement a requirement; the answer comes from the ZEO guides, with the screens to use.', ('hrms', 'projects', 'settings')),
        ('mail', 'Monthly summary e-mail', 'On the 1st of the month: payroll changes and why, expiring documents, absence and overtime outliers.', ('hrms', 'settings')),
        ('forum', 'Ask ZEO', 'Ask in English or Arabic: your data with your own rights, where and how to do things in ZEO, explanations of payslips, leave, settlements and gratuity – AI assistants › Ask ZEO.', ('hrms', 'projects', 'settings')),
        ('fact_check', 'Checks before go-live and before approval', 'Ask "Am I ready to go live?", "Check data quality" (IBAN, Emirates ID, duplicates, documents) or "Check the payroll before approval".', ('hrms',)),
        ('playlist_play', 'Guided payroll', 'Say "Run payroll": inputs, preview, calculate, pre-check, approval, WPS file and payslips, one confirmed step at a time.', ('hrms',)),
        ('undo', 'Every change shown first, and undone in one click', 'Ask ZEO shows a preview; nothing changes before you confirm. Changes are recorded "via Ask ZEO" – Settings › Ask ZEO.', ('hrms', 'settings')),
    ]},
    {'version': '1.70.3', 'date': '2026-11-16', 'title': 'ZEO Developer', 'items': [   # v1.70.3 (ZEO Developer handoff, made as 1.74.0 on 1.67.26)
        ('code', 'Developer mode', 'Settings › Developer mode (company administrators): the Developer menu with rate tables, Python scripts with a code editor, test on sample data and versions, triggers by hand, on records of any app, daily or monthly.'),
        ('functions', 'Formulas, lookups and child tables in custom modules', 'Custom module fields: formula, lookup, child table, link to any record, QR code and barcode; show when / required when; LOOKUP() in rate tables.'),
        ('verified_user', 'Safe actions with approval', 'Scripts send e-mails and to-dos, make leave requests and expense claims at once; payroll inputs, deductions, journals and money records wait for another administrator.'),
        ('inventory_2', 'Reports, print layouts and packages', 'Reports and print layouts for custom modules, scheduled calculations, and packages to move modules between companies (imported scripts arrive switched off).'),
    ]},
    {'version': '1.70.2', 'date': '2026-11-15', 'title': 'HSE: PPE, emergency, equipment and contractors', 'items': [   # v1.70.2 (HSE scope patch 1.67.27)
        ('checkroom', 'PPE management', 'HSE › PPE, emergency & equipment: the PPE catalogue with stock and sizes, the PPE matrix (which PPE each job needs), issue to employees and contractor workers, "I received it", return, replace and replace-by dates.', ('hse',)),
        ('emergency', 'Emergency management', 'Emergency plans approved by the HSE manager, the emergency team (wardens, first aiders), assembly points, emergency equipment with inspections, and drills with evacuation times and actions.', ('hse',)),
        ('construction', 'Safety equipment and contractors', 'Lifting gear, scaffolds and harnesses with green / yellow / red tags and certificates; contractor workers cleared to work, and a contractor HSE score A–D per period.', ('hse',)),
        ('grid_on', 'Risk matrix size and 15 industry packs', 'Choose a 3×3, 4×4 or 5×5 risk matrix; HAZID and HAZOP assessments; site risk class and emergency contacts; packs for mining, logistics, hotels, facilities management, agriculture, food, ports, aviation, telecom, utilities, waste, cleaning, laboratories, shipyards and data centres.', ('hse',)),
    ]},
    {'version': '1.70.1', 'date': '2026-11-15', 'title': 'Document Center', 'items': [   # v1.70.1 (DMS SOP phases 1 and 2)
        ('folder_shared', 'One folder tree for the company', 'Documents › Document Center: a folder for every employee, project, business partner, building, asset, HSE site and CRM account (made and named from the record), General for company folders and My folders for each user. Users create their own folders where they have the right.'),
        ('admin_panel_settings', 'Folder rights', 'Give users, permission groups, branches, departments or everyone a preset (viewer, contributor, reviewer, approver, manager) or single rights; subfolders inherit them unless you switch it off. Employees always see their own folder.'),
        ('history', 'Versions, approval and distribution', 'Every new file is a version (1.0, 1.1, 2.0) with compare and restore. A category with a workflow sends documents through review and approval before they are published; published documents are locked. Send documents to people with a read acknowledgement, or share a link outside that expires.'),
        ('notifications_active', 'Document expiry alerts fixed', 'Alerts now use the current documents of active employees (also passport, visa, Emirates ID), follow their branches and departments, tell once when a document enters the window and once when it expires, and send each alert\'s own summary to its users.', ('hrms',)),
    ]},
    {'version': '1.70.0', 'date': '2026-11-15', 'title': 'GCC and India country rules', 'items': [   # v1.70.0 (pending items 142 – 151)
        ('public', 'End of service by the branch\'s country', 'Saudi Arabia and Kuwait on the full wage with their resignation shares, Bahrain with the SIO fund from 01/03/2024, Oman old / new law and the savings scheme from 19/07/2027, Qatar on the basic, India with the Labour Codes (fixed term after 1 year, the 50 % wages rule). Nationals covered by their pension get no gratuity.', ('hrms',)),
        ('account_balance', 'GOSI, SIO and Oman savings contributions', 'GOSI on basic + housing with the new-entrant rates from 03/07/2024; the Bahrain SIO end-of-service contribution (4.2 % / 8.4 %); the Oman savings scheme (9 %) from 19/07/2027.', ('hrms',)),
        ('event', 'Settlement due dates and deduction limits', 'Final settlement due in 7 / 14 days (Saudi Arabia) or 2 working days (India); deduction limit 50 % (Saudi Arabia) or 25 % with loans 10 % (Kuwait).', ('hrms',)),
    ]},
    {'version': '1.69.0', 'date': '2026-11-15', 'title': 'Printed reports, leave statement and punch map', 'items': [   # v1.69.0 (pending item 21)
        ('print', 'Print any report', 'Every report (also the ones made with the report builder) has a Print button: a PDF on the company letterhead with the period, branches, the table as you filtered it, totals and page numbers.', ('hrms',)),
        ('receipt_long', 'Employee leave statement', 'Reports › Leave & attendance › Employee leave statement and Self service › My leave statement: one year per leave type – brought forward, earned, taken, encashed, lapsed and the balance – with Print / PDF.', ('hrms',)),
        ('map', 'Punch map', 'Attendance › Punch map: the geofences and the punches of a day on a map – inside in green, outside in red with the metres outside.', ('hrms',)),
    ]},
    {'version': '1.68.0', 'date': '2026-11-15', 'title': 'UAE payroll extras', 'items': [   # v1.68.0 (pending items 29 – 40)
        ('percent', 'Commission plans', 'Payroll › Payroll extras › Commission: tiers on sales or on the % of the target; the commission goes to the payslip as a monthly input.', ('hrms',)),
        ('redeem', 'Retention bonus with clawback', 'Paid with a chosen payroll; repaid on the final settlement (whole or pro rata) when the employee leaves within the clawback months.', ('hrms',)),
        ('room_service', 'Service charge pool', 'The service charge of a month and branch shared by points, days worked or equally.', ('hrms',)),
        ('payments', 'Ramadan / Eid advance in one go', 'Approved advances for many employees at once (fixed or % of salary), recovered in instalments.', ('hrms',)),
        ('schedule', 'Hourly and daily pay, ILOE, Emiratisation', 'Hourly / daily rates for the formulas, ILOE subscriptions, Nafis and the Emiratisation quota, GCC nationals\' pension, cost to company.', ('hrms',)),
    ]},
    {'version': '1.67.28', 'date': '2026-11-15', 'title': 'Optional connections per branch', 'items': [   # v1.67.28 (optional by-branch connections)
        ('webhook', 'Webhooks for some branches only', 'Settings › API keys and webhooks and Accounting › API and webhooks: a webhook can be limited to the records or journals of chosen branches (none ticked: every branch, as before).', ('hrms',)),
        ('forum', 'A Teams / Slack channel per branch', 'Settings › Teams and Slack: a branch can have its own channel; its events go there instead of the company channel.', ('hrms',)),
        ('account_balance', 'Odoo / Zoho per branch', 'Accounting › Posting rules and connection › Branch connections: a branch can post its journals to its own Odoo database or Zoho Books organisation; journals are then made per branch.', ('hrms',)),
    ]},
    {'version': '1.67.27', 'date': '2026-11-15', 'title': 'Reports: payroll, attendance, helpdesk and talent', 'items': [   # v1.67.27 (pending item 1.62.13)
        ('shield', 'Social security and loss of pay', 'Social security summary (GPSSA, GOSI …), Loss of pay summary and Data for payroll – the figures to check before running the payroll.', ('hrms',)),
        ('account_balance', 'Payroll liability and journal', 'What the payroll owes and to whom, the accounting lines of the payroll journals, salary withhold, DIFC DEWS and ADPF.', ('hrms',)),
        ('timelapse', 'Daily leave, hours and remote work', 'Who is on leave today, expected against worked hours, office against remote days.', ('hrms',)),
        ('support_agent', 'HR helpdesk SLA and trends', 'Every question against its answer and solution times; month by month on time % and rating.', ('hrms',)),
        ('grid_view', 'Nine-box grid and salary bands', 'Performance against potential (Performance › Nine-box grid), salaries against the grade bands, learning progress and training ratings.', ('hrms',)),
    ]},
    {'version': '1.67.26', 'date': '2026-11-15', 'title': 'People: pre-onboarding, OKR, eNPS, knowledge base, integrations', 'items': [   # v1.67.26 (pending item 1.62.12)
        ('how_to_reg', 'Pre-onboarding', 'Send the new joiner a private link before the first day: they fill their details and upload their documents; HR accepts and the details go to the employee record.', ('hrms',)),
        ('flag', 'Objectives and key results', 'Company, department and personal objectives with key results and check-ins – People › OKR tree.', ('hrms',)),
        ('sentiment_satisfied', 'eNPS survey', 'A survey of the kind eNPS asks the 0–10 question anonymously and shows the score and its trend.', ('hrms',)),
        ('menu_book', 'HR knowledge base', 'HR writes the answers once; employees search them, and the helpdesk suggests them while typing the question.', ('hrms',)),
        ('payments', 'Compensation cycles and mail merge', 'Salary reviews against a budget, approved by another person, applied from one date, with increment letters for all in one ZIP.', ('hrms',)),
        ('event', 'Calendar sync', 'Your leave, your team\'s leave, holidays and interviews in Google Calendar, Outlook or your phone.', ('hrms',)),
        ('forum', 'Teams and Slack', 'Who is out, new joiners, announcements and approved payrolls posted to a channel.', ('hrms',)),
        ('widgets', 'Custom modules and Blueprint', 'Your own registers with their states and who may move a record – no programming.', ('hrms',)),
        ('language', 'Custom domain', 'Open ZEO at your own address, e.g. hr.yourcompany.com.', ('hrms',)),
        ('analytics', 'Bradford factor report', 'Short, frequent absences weigh more – Reports › Leave & attendance.', ('hrms',)),
    ]},
    {'version': '1.67.25', 'date': '2026-11-15', 'title': 'Demo workbook', 'items': [   # v1.67.25 (pending item 1.62.9)
        ('science', 'Try ZEO with a demo company', 'Settings › Data workbook › Download the demo workbook: a fictional trading company with 14 employees and three months of leave, payroll and WPS – load it into a new, empty company.', ('hrms',)),
    ]},
    {'version': '1.67.24', 'date': '2026-11-14', 'title': 'Fixes and decisions: payroll, leave, shifts, rights', 'items': [   # v1.67.24 (pending group 1 and 2)
        ('refresh', 'Recalculate keeps the payroll run', 'Recalculate makes the payslips again in the same run – same number, same page, and the history shows each recalculation.', ('hrms',)),
        ('picture_as_pdf', 'Payslip PDF with text', 'Download and Save PDF on the payslip page give the payslip made on the server – searchable text, in English, Arabic or both.', ('hrms',)),
        ('event_available', 'Approve fewer leave days', 'The approver can approve fewer days than asked; only those days come off the balance, the other days are leave without pay.', ('hrms',)),
        ('face', 'Blink to punch', 'Punching with the face can ask for one blink – a photo held to the camera is refused (setting – switch it on once the phones have the new app).', ('hrms',)),
        ('schedule', '24-hour duties and holidays on rotations', 'A shift from 07:00 to 07:00 is a 24-hour duty; a rotation pattern can keep its shifts on public holidays.', ('hrms',)),
        ('admin_panel_settings', 'Rights', 'The Payroll Manager group holds the whole payroll month; in Projects "change project" covers the projects you manage – "change every project" is its own right.', ('hrms', 'projects')),
    ]},
    {'version': '1.67.23', 'date': '2026-11-14', 'title': 'Projects: monthly AMC hours, checklists, GCC invoices', 'items': [   # v1.67.23 (pending 270, 271)
        ('schedule', 'AMC hours for each month', 'An AMC can give its included hours for each month; the hours over a month are billed after the month with "Bill extra hours".', ('projects',)),
        ('checklist', 'Service checklists you can edit', 'Change the service checklist of an equipment category on screen – the technician\'s visit uses it; the industry pack no longer overwrites it.', ('projects',)),
        ('receipt_long', 'Invoices for the GCC', 'Tax invoices follow the company\'s country: Saudi Arabia in Arabic with the ZATCA QR code, Bahrain and Oman with their VAT, Qatar and Kuwait without VAT.', ('projects',)),
    ]},
    {'version': '1.67.22', 'date': '2026-11-14', 'title': 'Payroll open points closed', 'items': [   # v1.67.22 (pending C3, C7, C11–C14)
        ('account_balance', 'Salary payments in accounting', 'Marking salaries paid posts a journal from the paying account (bank, cash or your own method); a split salary is shown on the payslip.', ('hrms',)),
        ('dark_mode', 'Ramadan in payroll', 'The planned hours of the month count the shorter Ramadan days, and a Ramadan break can be set.', ('hrms',)),
        ('flight', 'Air tickets bought by the company', 'Record a ticket bought by a travel agency, company card or expense claim – taken from the air ticket provision first; family relations can be set.', ('hrms',)),
        ('event_repeat', 'Instalments on weekly pay', 'Loans and advances can take a share of the instalment in every weekly period.', ('hrms',)),
        ('person_off', 'Out of office checks the right', 'A colleague who cannot approve is refused; HR and the mobile app can choose "Approvals go to" with a leave request.', ('hrms',)),
        ('calendar_month', 'Monthly inputs per month', 'Figures are kept for their own month – enter next month ahead without the current payroll taking them.', ('hrms',)),
    ]},
    {'version': '1.67.21', 'date': '2026-11-14', 'title': 'Approval rules and notifications per app', 'items': [   # v1.67.21 (item 1.62.8)
        ('approval', 'Approval rules in each app', 'HR, Payroll, Projects, CRM, Property, Maintenance, Asset Management and HSE each have their own Approval rules page: the approval levels and escalation of their requests and their approval flows.'),
        ('notifications', 'Notifications in each app', 'Each app shows its e-mail templates, alerts and the rules that send e-mails or notifications – only its own.'),
        ('fact_check', 'All apps for the administrator', 'Settings › Automation › Approval rules – all apps and Notifications – all apps: one row per app, open an app or show its rules below.'),
    ]},
    {'version': '1.67.20', 'date': '2026-11-13', 'title': 'Import from every screen', 'items': [   # v1.67.20 (1.67.17 import patch merged)
        ('upload_file', 'Import on more screens', 'Customers, contacts, sites, equipment, AMC contracts, projects, tickets, stores and vans, resignations, employees and their details, salaries, air tickets, loans and expenses have an Import button with an Excel template.'),
        ('fact_check', 'Check before import', 'Check now runs the screen\'s own save and undoes it, so the rows it says are ready really import.'),
        ('task_alt', 'Mark a setup step completed', 'Every setup guide step has "Mark as completed"; the checks still open stay as warnings.'),
        ('payments', 'Payroll and leave fixes', 'Gratuity is a provision and no longer paid on the payslip; National Service Leave is for nationals only; and more fixes from the payroll and leave tests.', ('hrms',)),
    ]},
    {'version': '1.67.19', 'date': '2026-11-12', 'title': 'Every field checked', 'items': [   # v1.67.19 (field-by-field check)
        ('tune', 'Your own fields on every form', 'Fields added with "Design this form" now show and save on the newer forms and the time / approval forms too.'),
        ('schedule', 'Dates and times stay as typed', 'A date and time you type (e.g. an announcement\'s "Show from") is kept exactly, no longer 19 minutes off.'),
        ('rule', 'Clear messages', 'Too long text, a wrong e-mail or a number below 0 now gets a message instead of a server error.'),
        ('lock', 'Rights checked on every save', 'A view-only right no longer lets a user add, change or delete attendance devices, punches or records of other employees.', ('hrms',)),
        ('apps', 'Only your apps', 'Settings, the calendar, rights groups, the audit log, API keys and notifications list only what belongs to the apps your company uses.'),
    ]},
    {'version': '1.67.18', 'date': '2026-11-11', 'title': 'One branch filter and dates', 'items': [   # v1.67.18 (checklist open points F15–F17)
        ('filter_alt', 'One branch filter', 'Employees, Leavers, Employee salary, Asset register, Org chart and Daily attendance follow the branches chosen in the top bar.'),
        ('event', 'Dates as dd/mm/yyyy', 'Rosters, schedules and the Audit log show dates as dd/mm/yyyy.'),
        ('translate', 'More Arabic', 'Texts built on the screen (weekdays, counts, report columns) now show in Arabic.'),
    ]},
    {'version': '1.67.17', 'date': '2026-11-11', 'title': 'Fixes from the release checklist', 'items': [   # v1.67.17 (checklist run of 1.67.16)
        ('lock', 'Report files only for those allowed', 'A saved report file opens only for an administrator or the user it was made for; report lists show only your own files.'),
        ('group', 'Users list', 'The list of users and administrators is shown only to those who manage users.'),
        ('event', 'Holiday calendar given twice', 'The same holiday calendar cannot be given twice to the same branch, department, category or employee.'),
        ('insights', 'Service KPIs for Management', 'The Projects Management role now opens Service KPIs and the service reports.'),
        ('badge', 'Give access by branch', 'HR users limited to some branches give access only to the employees of those branches.'),
    ]},
    {'version': '1.67.16', 'date': '2026-11-11', 'title': 'Bug fixes from the 1.67.5 test report', 'items': [   # v1.67.16 (bug list 11/11)
        ('face', 'Face registered in self service', 'Faces an employee registers in My profile now show in the Face and badge register.'),
        ('more_time', 'Late in / early out and overtime', 'Employees ask for late in / early out in Self service; HR adds overtime by hand.'),
        ('request_quote', 'My requests in one place', 'Every self-service request – general, letter, advance, loan, asset, air ticket, late in, resignation – starts from My requests.'),
        ('my_location', 'Recheck and punch photos', 'A recheck shows as waiting until the employee answers; punch photos show for every punch.'),
        ('credit_score', 'Loans ready for a new company', 'Personal and Emergency loan types with a reporting-manager approval are added; "No approval" approves at once.'),
    ]},
    {'version': '1.67.15', 'date': '2026-11-10', 'title': 'Payroll fixes', 'items': [   # v1.67.15 (fixes C10, C3, C14, C11)
        ('payments', 'Held salary at exit', 'A salary still held when an employee leaves is now paid in the final settlement.'),
        ('undo', 'Rejected payslips give everything back', 'Expense reports and carried-over deductions of a rejected or deleted payslip go to the next payslip.'),
        ('account_balance', 'Saudi wage file and salary split', 'The Mudad wage protection file pays only the bank part of a split salary.'),
        ('date_range', 'Monthly inputs preview', 'The preview counts the payroll period (attendance cut-off) and shows it.'),
        ('receipt', 'Recovery journals', 'A recovery settled early or written off now makes its accounting journal.'),
    ]},
    {'version': '1.67.14', 'date': '2026-11-09', 'title': 'Field service, all industry packs and accounting', 'items': [   # v1.67.14 (service releases 2 – 5, billing connectors)
        ('category', 'All 18 industry packs', 'Every service industry now has its own equipment, AMC types, checklists, CRM pipeline and maintenance trades.'),
        ('assignment_turned_in', 'Service report on the phone', 'Technicians start the job, tick the checklist, add parts, get the customer to sign and finish. The report is e-mailed to the customer.'),
        ('local_shipping', 'Van stock and parts', 'Parts used come out of the van. AMC parts limits are checked; the rest is chargeable and can be billed from the ticket.'),
        ('insights', 'Service KPIs', 'First-time fix, SLA, preventive visits, technician use and customer rating, with reports to download.'),
        ('sync_alt', 'Invoices to accounting', 'Tax invoices and payments go to ZEO Accounting, and on to Odoo or Zoho Books when connected.'),
    ]},
    {'version': '1.67.13', 'date': '2026-11-09', 'title': 'Service, billing and planners', 'items': [   # v1.67.13 (service, billing, planners)
        ('location_on', 'Sites and equipment', 'Customers have sites with address, GPS and access notes, and equipment per site with warranty and a QR label.'),
        ('verified', 'AMC types and coverage', 'AMC contracts have a type, visits and call-outs a year, and the sites and equipment they cover. Tickets show Warranty, Covered or Chargeable with the reason.'),
        ('category', 'Industry packs', "Pick one or more service industries. IT keeps today's setup; General has every feature with simple lists."),
        ('receipt_long', 'Proforma and tax invoices', 'Milestones and AMC lines go from PI to tax invoice with VAT, payments and follow-ups. Billing and collections shows what is due.'),
        ('event_available', 'Collection planner', 'Plan collections from opening balances, CRM, projects and AMC. Follow-ups go to the To-do list; the report adds them up.'),
        ('calendar_month', 'Support planner', 'Plan support jobs by day and person. The person gets the ticket and a notice, and can open the whole job.'),
    ]},
    {'version': '1.67.12', 'date': '2026-11-09', 'title': 'Effective dates and GPSSA first insured', 'items': [   # v1.67.12 (effective)
        ('event', 'Changes from a date', 'Leave policies, leave types, overtime, gratuity, air tickets, pay structures and attendance rules can change from a set date, with a history of changes.'),
        ('account_balance', 'GPSSA old or new law', 'Enter the date an Emirati was first insured. The payroll uses the old or new pension law from that date.'),
        ('beach_access', 'First-year leave and negative balance', 'First-year leave is worked out again from the joining date. Leave types can set how far below zero a balance may go.'),
        ('flight', 'Probation and air tickets', 'The confirmation date follows the probation. End of service shows only the unused part of the air ticket.'),
    ]},
    {'version': '1.67.11', 'date': '2026-11-09', 'title': 'Loans, advances and deduction limits', 'items': [   # v1.67.11 (loans)
        ('account_balance', 'Monthly deduction limit', 'For UAE companies, total deductions in a month stop at 50% of the wage. Anything above that moves to next month, with the reason on the payslip.'),
        ('rule', 'Loan type rules', 'Loan types set the highest amount and months, who may ask, and how many open loans an employee can have.'),
        ('payments', 'Pay out, prepay, skip and write off', 'Loans can be paid out, partly prepaid, moved by a month or written off with a second approval.'),
        ('savings', 'Advance limits and settlement recovery', 'Advances have a limit, one a month and instalments. Open balances are recovered from the final settlement in a set order.'),
    ]},
    {'version': '1.67.10', 'date': '2026-11-09', 'title': 'Regression tests and payroll fixes', 'items': [   # v1.67.10 (regression)
        ('calculate', 'Payroll runs can be recalculated', 'Recalculate and Delete on a calculated payroll run work again and give back loans, advances and inputs.'),
        ('payments', 'Payslip currency', "Payslips use the company's currency, not the employee's home country."),
        ('event', 'Expiry notices', 'Document expiry notices show dates as dd/mm/yyyy.'),
        ('translate', 'Leave balances in Arabic', 'My leave › How my balances work is fully in Arabic.'),
    ]},
    {'version': '1.67.9', 'date': '2026-11-09', 'title': 'Cleaner screens across every app', 'items': [   # v1.67.9 (screens)
        ('add_box', 'One New button and row actions', 'Every register has one New button at the top right, Edit and Delete on each row, and a selection bar for several rows.'),
        ('toggle_off', 'Inactive instead of delete', 'A record that is in use cannot be deleted; make it inactive instead. Lists filter Active, Inactive or All.'),
        ('view_sidebar', 'New forms', 'Branches, company, departments, designations, categories, users, leave and document types and e-mail templates open in a side panel.'),
        ('calendar_month', 'Dates and Arabic', 'Dates show as dd/mm/yyyy everywhere, list text no longer wraps, and many more screens are fully in Arabic.'),
    ]},
    {'version': '1.67.8', 'date': '2026-11-08', 'title': 'Fixes for calendars, settlements, apps without HR and Arabic screens', 'items': [   # v1.67.8 (fixes)
        ('event_busy', 'Holiday calendars', 'A calendar in use can be made inactive instead of deleted; unused calendars can be deleted.'),
        ('receipt_long', 'Final settlement', 'Your own settlement page sits below the top bar, and the Arabic print uses Arabic wording throughout.'),
        ('apps', 'Companies without HR', 'Accounting settings, partner roles and set-up wizards follow the apps that are active, without HR errors.'),
        ('translate', 'Arabic screens and e-mails', 'Security e-mails, app settings, imports and printed dates are shown in Arabic with dd/mm/yyyy dates.'),
        ('smartphone', 'Phones and dialogs', 'The phone top bar no longer overlaps, and long dialogs scroll properly.'),
    ]},
    {'version': '1.67.7', 'date': '2026-11-08', 'title': 'Administrators per company, Arabic e-mails and Print buttons', 'items': [   # v1.67.7 (admin)
        ('admin_panel_settings', 'Administrator per company', 'The owner is administrator in every company; other users can be made administrator of one company only, and the last administrator cannot be removed.'),
        ('translate', 'E-mails in your language', 'Sign-in, sign-in code, security and payslip e-mails are sent in each person\'s language, Arabic right to left.'),
        ('print', 'Print buttons', 'Tickets, AMC contracts, permits, incidents, quotations, leases, invoices, receipts and work orders can be printed from their lists and pages.'),
    ]},
    {'version': '1.67.6', 'date': '2026-11-08', 'title': 'Company set-up, app wizards and sign-in security', 'items': [   # v1.67.6 (setup)
        ('shield_lock', 'Sign-in security', 'Each company sees only its own sign-ins and users; sign-in history of 90 days and an e-mail when a sign-in comes from a new place, address or device.'),
        ('tag', 'Numbering for every app', 'Every app with documents gets its numbering series when it is activated; existing numbers are never changed.'),
        ('print', 'Printed documents for every app', 'Tickets, AMC contracts, quotations, leases, invoices, work orders, permits and more have a default printed design.'),
        ('checklist', 'Set-up wizard for every app', 'Projects, CRM, Property, Maintenance, Asset Management, People and HSE are set up step by step before the first transaction.'),
        ('domain', 'Company and branches', 'The first branch takes the company details, logo and financial year; administrators get every branch; one branch is chosen for you.'),
    ]},
    {'version': '1.67.5', 'date': '2026-11-06', 'title': 'Bug fixes and punch from My profile', 'items': [   # v1.67.5 (bugs)
        ('mail', 'Payslip e-mail on approval', 'An approved payslip is e-mailed to the employee with its PDF; the approver sees how many were sent, skipped or failed.'),
        ('event_available', 'Leave fixes', 'Each leave type shows once per employee, maternity leave only for women, Leave Type edit and scrolling fixed, the employee list stays open in the leave request.'),
        ('schedule', 'Shift fixes', 'Create Shift Pattern scrolls on its own, and the shift pattern and employee shift lists show every record correctly.'),
        ('upload_file', 'Employee import fields', 'Title, Arabic and preferred name, card / barcode, parents, visa and work location, city and profile picture in the import file.'),
        ('qr_code_scanner', 'Barcode and face punch', 'Employees can punch in from My profile with their card barcode, the company QR or their registered face.'),
    ]},
    {'version': '1.67.4', 'date': '2026-11-06', 'title': 'Companies, sign-up, apps on their own and shared masters', 'items': [   # v1.67.1–1.67.4
        ('domain_disabled', 'Delete, deactivate and restore', 'Make a company or database inactive, or delete it with a backup download; it is kept 30 days and only the server administrator can restore it.'),
        ('progress_activity', 'Honest sign-up progress', 'The trial sign-up bar follows the real copy of the company, with time left, Try again and contact person, number and country.'),
        ('apps', 'Apps work on their own', 'HR is now an app like the others: each app brings only what it needs, for example Payroll needs HR.'),
        ('badge', 'Employee directory', 'Without HR, employees are kept in Settings › Company › Employee directory and become HR employees when HR is switched on.'),
        ('inventory_2', 'Shared partners, items and locations', 'Projects, assets, expenses, HSE and maintenance use the shared partners, items and locations, ready for Sales, Purchase, Inventory and Accounting.'),
    ]},
    {'version': '1.67.0', 'date': '2026-11-03', 'title': 'Leave, air ticket and attendance rules', 'items': [   # v1.63.0–1.67.0
        ('calendar_view_month', 'Roster grid', 'Plan a whole month of shifts in one grid – drag, patterns, copy last month, conflicts and publish.'),
        ('nights_stay', 'Ramadan hours and night shifts', 'Ramadan hours per year, rest-day chips, 24-hour shifts and night-shift window settings.'),
        ('beach_access', 'Leave rules', 'Give back or extend an approved leave, leave salary before the vacation, sandwich rule, birth date for maternity and parental leave, nursing breaks.'),
        ('flight', 'Air ticket rules', 'Family tickets and fares, monthly accrual and lapse, provision in the accounts, request and policy checks.'),
        ('call_split', 'Salary split', 'Pay one salary through two bank accounts or part in cash; the WPS file carries the WPS part.'),
        ('replay', 'Recovery schedules', 'Recover accommodation, transport, uniform or damage over months with a monthly cap.'),
    ]},
    {'version': '1.62.11', 'date': '2026-11-03', 'title': 'Payroll essentials', 'items': [   # v1.62.11
        ('account_balance', 'Payments for every method', 'WPS files per bank and exchange house, bank transfer, cheque list, cash sheet and your own methods – each file layout can be designed by the company.'),
        ('lock', 'Hold salary and lock periods', 'Hold and release salary, lock a paid month, salary revision with approval, re-hire and pay-date shift.'),
        ('event_busy', 'Out of office', 'Set automatically when an approver\'s leave is approved, a colleague per approval and leave type, and approvals given back when he is back.'),
        ('grid_on', 'Monthly inputs', 'Type days or hours for every employee in one grid or import them from Excel; opening accrued values in bulk.'),
        ('logout', 'End of service settings', 'Gratuity and leave salary options, payroll cycle and month-by-month final salary, HR entries, reset, cancel and a richer print.'),
        ('groups', 'Bulk settlement and pension schemes', 'Final settlement for many leavers at once, password-protected payslips, ADPF and DIFC DEWS per branch.'),
    ]},
    {'version': '1.62.10', 'date': '2026-11-03', 'title': 'Old screens in the new design', 'items': [   # v1.62.10
        ('dashboard_customize', 'Requests and approvals', 'Loan, advance, air ticket and asset request forms in the new design, and one review window to approve, reject or delegate.'),
        ('event_note', 'Leave balances and documents', 'Adjust balances and compensatory leave through the leave ledger; document library and expiring documents in the new design.'),
        ('schedule', 'Attendance and calendars', 'Recheck, face register, shift patterns, employee shifts, weekend and holiday calendars in the new design.'),
        ('account_tree', 'Approval levels', 'One editor for the approval levels and escalation of every request type.'),
        ('tune', 'Setup screens', 'Policies, types, document numbering, e-mail configuration, permissions and the form designers in the new design.'),
        ('check_circle', 'One confirm window', 'Every "are you sure?" question opens the same window in the new design, in English and Arabic.'),
    ]},
    {'version': '1.62.7', 'date': '2026-11-03', 'title': 'Accrue now – pay later', 'items': [   # v1.62.7
        ('savings', 'Accrue now – pay later', 'A salary component (for example on-call or shift allowance) builds up each month outside net pay and is paid in a chosen month or at the final settlement.'),
        ('account_balance_wallet', 'Balances and opening balances', 'See each employee\'s balance and its movements; enter the go-live opening balance.'),
        ('receipt_long', 'Journals and report', 'Monthly accrual to the accrual account (224005), the payout from it, and the Accrued components report.'),
    ]},
    {'version': '1.62.6', 'date': '2026-11-03', 'title': 'Working days and night rules', 'items': [   # v1.62.6
        ('date_range', 'One weekend rule', 'Roster, shift override, shift pattern, then the weekend calendar – the most specific weekend wins everywhere: attendance, leave and payroll.'),
        ('lock', 'Approved leave keeps its days', 'An approved leave is counted again only when its dates or type change, or HR recalculates it.'),
        ('event_repeat', 'Weekend change from a date', 'See the leaves a weekend change affects and choose Recalculate or Keep as approved; paid periods never change.'),
        ('help', 'What decides the working days', 'The employee record and My schedule show which rule decides each day.'),
        ('bedtime', 'Night work', 'Night work without a shift is one day; the UAE night window 22:00–04:00 and the night overtime rate for workers not on shifts, as the law says.'),
    ]},
    {'version': '1.62.5', 'date': '2026-11-03', 'title': 'Leave on days already paid', 'items': [   # v1.62.5
        ('payments', 'Salary already paid', 'A leave on days whose salary is already paid says so (for example "November salary is already paid") instead of saving silently.'),
        ('add_card', 'Add to next month\'s salary', 'HR and payroll choose Cancel or add the pay difference to the next salary – a recovery at most 20 % of the wage a month.'),
        ('event', 'Cut-off dates', 'With a cut-off such as the 25th, leave after it goes to the next payroll without a message.'),
    ]},
    {'version': '1.62.4', 'date': '2026-11-03', 'title': 'Leave fixes', 'items': [   # v1.62.4
        ('event_available', 'First-year leave right', 'Joiners at the end of a month get their 12-day credit; part months and unpaid days count in months 7 to 12; re-hired employees accrue again.'),
        ('swap_horiz', 'Branch leave types', 'Leftover days on another branch\'s leave type move to the employee\'s own branch type, with ledger lines.'),
        ('calculate', 'Exact balances', 'A full year gives exactly the yearly days; "to earn by year end" and the leave liability of past months are right; every change is in the leave ledger.'),
        ('rule', 'Approve fewer days', 'When fewer days are approved, the other days stay unpaid; 0 approved days is refused.'),
        ('logout', 'Leave and the last working day', 'No leave after the last working day; end of service uses the balance on that day, also when it is negative, and the branch annual leave type.'),
        ('timer', 'Short leave in hours', 'Apply for a few hours on one working day; approved paid short leave covers late or early minutes.'),
    ]},
    {'version': '1.62.3', 'date': '2026-11-02', 'title': 'Recommended settings, air ticket and loan fixes', 'items': [   # v1.62.3
        ('account_balance', 'GPSSA by the law of each employee', 'Nationals who joined before 31/10/2023: 5 % employee, 12.5 % employer, salary 1,000–50,000; from that date 11 %, employer 12.5 % (15 % from AED 20,000), salary 3,000–70,000 – all in Payroll › Legal rates.'),
        ('undo', 'Leave pay corrected after a closed payslip', 'Salary paid for leave days that became half or no pay later is recovered with the next payrolls, at most 20 % of the wage a month.'),
        ('shield', 'Net pay kept at 0 or above', 'Recommended: switch on in Payroll controls with deductions at most 50 % of gross; the rest is carried to next month.'),
        ('flight', 'Air tickets paid once', 'No cash payout on a ticket already approved, on another employee\'s or a pending / expired allocation; "every 2 years" respected; one open ticket paid at exit.'),
        ('task_alt', 'Final settlement closes everything', 'Paying a final settlement closes the loans, advances and air tickets it settled and makes the employee inactive.'),
        ('edit', 'Loans changed after deductions', 'A new amount or number of months recovers the rest correctly; Disbursed and In progress loans are deducted.'),
    ]},
    {'version': '1.62.2', 'date': '2026-11-02', 'title': 'Payroll, leave and end of service fixes', 'items': [   # v1.62.2
        ('payments', 'Loans and advances', 'Two loans or two advances in one month are both deducted on one line; the last instalment never takes more than the balance and closes the loan.'),
        ('restore', 'Delete or recalculate a payroll run', 'Advances, air tickets, requests, monthly inputs and leave encashments used by a deleted run come back and are paid once by the new run.'),
        ('event', 'Salary from its date', 'A salary change with a valid-from date applies from that day (split by days inside the month); arrears use the salary valid in each month.'),
        ('account_balance', 'GPSSA deducted once', 'When the statutory GPSSA scheme applies, the older pension formula component is left off the payslip and noted.'),
        ('functions', 'Formulas and pay structures', 'A formula that uses another formula gets its pro-rated amount; each employee uses their own pay structure for the days of the month.'),
        ('trending_down', 'Negative net pay', 'Payroll controls › Net pay and deductions: optionally keep net pay at 0 or above and carry the rest to next month; otherwise a warning shows.'),
        ('sick', 'Sick and maternity pay steps', 'Half-pay and no-pay days are kept by the nightly job and worked out over the whole leave year, also when leaves are approved out of order or cancelled.'),
        ('work_history', 'End of service and provisions', 'Absent days counted once, overtime with the payroll formula, air ticket provision without tickets already taken, year-end lapse on the 31/12 balance.'),
        ('swap_horiz', 'Transfer with a salary change', 'Works also for employees revised before; the new salary applies from the transfer date.'),
    ]},
    {'version': '1.62.1', 'date': '2026-11-02', 'title': 'Screens, lists and forms', 'items': [   # v1.62.1 ui
        ('format_list_bulleted', 'One way on every list', '"+ New" at the top right, Edit and Delete on each row, and a selection bar to delete or export many rows at once.'),
        ('toggle_off', 'Make inactive instead of delete', 'A record already used in transactions cannot be deleted – make it inactive: it leaves the choices for new records and stays in history. Tick several rows to make them inactive (or active again) together.'),
        ('edit_note', 'New-design forms', 'Departments, designations, categories, branches, users, leave types, document types, e-mail templates and New employee open in the new form.'),
        ('domain', 'Company and branches', 'Find a company by name, see the recent ones, tick branches – lists and reports show only the ticked branches.'),
        ('calendar_month', 'Clear values', 'Dates as dd/mm/yyyy, amounts in AED with two decimals, names instead of sign-in names, Yes / No instead of true / false.'),
        ('mark_email_read', 'Send message reaches everyone', 'Messages are e-mailed with your company\'s mail server, also to employees without a login, and each name on To: shows whether the e-mail was sent.'),
        ('content_copy', 'Copy the setup to a new branch', 'A new branch takes the calendars, leave types, approvals and salary components of the branch you choose – or nothing; an existing branch can copy another branch\'s setup again.'),
        ('add_business', 'A new company from an existing one', 'When you create a company, copy the setup of a branch of another company you administer – rename the branch and its code; leave, payroll, calendars, approvals, numbering and settings come along, never people, bank details or transactions.'),
        ('bug_report', 'Tester fixes', 'App launcher tiles, newest employees first, company logos, permission groups in words, assign weekends and holidays, Recalculate, Recheck e-mail and more.'),
    ]},
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
