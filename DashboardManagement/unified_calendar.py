"""
Unified calendar (v1.12).

GET /dashboard/api/calendar/?from=YYYY-MM-DD&to=YYYY-MM-DD&layers=leave,holiday,...&scope=me|team|company&branch=1,2
    → {from, to, scope, scopes, layers: [{key, label, color, count}], events: [{id, layer, title, start, end, allDay, color,
       employee: {id, name} | null, link, status}]}

Layers: leave, holiday, attendance, shift, training, interview, meeting, events (anniversaries, probation end, document expiry),
birthday (day and month only), company (announcements).

Rights
* me      – everybody: own leave / attendance / shifts / trainings / interviews on the panel / to-dos, holidays, company news,
            birthdays of the colleagues in the own branch (names only, no record link).
* team    – reporting managers: their direct and indirect reports (attendance and shifts summarised per day).
* company – company admins and users with the employee or leave view right: the employees of their branches
            (attendance and shifts summarised per day).
A scope the user may not use falls back to the widest one they have.
"""
import re
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta

from django.apps import apps
from django.db.models import Q
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services as S
from .overview import rec, rpt

M_ = '/main-sidebar/'
MAX_DAYS = 100
LAYERS = [
    ('leave', 'Leave', '#7C6FF0'), ('holiday', 'Public holidays', '#E5484D'), ('attendance', 'Attendance', '#0CA30C'),
    ('shift', 'Shifts', '#0E8AC7'), ('training', 'Training', '#D97706'), ('interview', 'Interviews', '#C026D3'),
    ('meeting', 'Meetings & to-dos', '#0F9F8F'), ('events', 'Employee events', '#64748B'), ('birthday', 'Birthdays', '#DB2777'),
    ('company', 'Company events', '#5B4FE0'),
]
COLOR = {k: c for k, _, c in LAYERS}
LABEL = {k: l for k, l, _ in LAYERS}
HR_CODES = ('view_emp_master', 'view_employee_leave_request', 'view_attendance')


def _m(label):
    try:
        return apps.get_model(label)
    except (LookupError, ValueError):
        return None


def _date(s):
    try:
        return date.fromisoformat(str(s)) if s else None
    except ValueError:
        return None


def allowed_scopes(c, user):
    out = ['me']
    if S.team_ids(user, depth=1):
        out.append('team')
    if c.admin or any(x in c.codes for x in HR_CODES):
        out.append('company')
    return out


def _who(e, link=True):
    if e is None:
        return None
    return {'id': e.id if link else None, 'name': S.emp_name(e)}


class Cal:
    def __init__(self, request):
        from AccessControl.access import ctx
        from .reports import Run
        self.request, self.user = request, request.user
        self.c = ctx(request)
        q = request.query_params
        self.today = timezone.localdate()
        self.d_from = _date(q.get('from')) or self.today.replace(day=1)
        self.d_to = _date(q.get('to')) or (self.d_from.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        if self.d_to < self.d_from:
            self.d_from, self.d_to = self.d_to, self.d_from
        if (self.d_to - self.d_from).days > MAX_DAYS:
            self.d_to = self.d_from + timedelta(days=MAX_DAYS)
        self.scopes = allowed_scopes(self.c, self.user)
        want = q.get('scope') or ('team' if 'team' in self.scopes else 'company' if 'company' in self.scopes else 'me')
        self.scope = want if want in self.scopes else self.scopes[-1] if want != 'me' else 'me'
        raw = q.get('layers')
        self.layers = [x for x in re.split(r'[,\s]+', raw) if x in COLOR] if raw else list(COLOR)
        self.me = self.c.emp
        if self.scope == 'company':
            self.emps = list(Run(request, {'period': None}).employees)
        elif self.scope == 'team':
            self.emps = list(S.scope_employees(self.user, 'team').select_related('emp_branch_id'))
        else:
            self.emps = [self.me] if self.me else []
        self.ids = [e.id for e in self.emps]
        self.by_id = {e.id: e for e in self.emps}
        self.events = []

    # ------------------------------------------------------------------ helpers
    def add(self, layer, key, title, start, end=None, all_day=True, employee=None, link=None, status=None, extra=None):
        ev = {'id': f'{layer}-{key}', 'layer': layer, 'title': title, 'start': start, 'end': end or start, 'allDay': all_day,
              'color': COLOR[layer], 'employee': employee, 'link': link, 'status': status}
        if extra:
            ev.update(extra)
        self.events.append(ev)

    def days(self):
        d = self.d_from
        while d <= self.d_to:
            yield d
            d += timedelta(days=1)

    def overlap(self, start_f, end_f):
        return Q(**{f'{start_f}__lte': self.d_to, f'{end_f}__gte': self.d_from})

    def in_range(self, d):
        return d is not None and self.d_from <= d <= self.d_to

    # ------------------------------------------------------------------ layers
    def leave(self):
        LR = _m('calendars.employee_leave_request')
        for l in LR.objects.filter(employee_id__in=self.ids, status__in=('approved', 'pending')).filter(self.overlap('start_date', 'end_date')).select_related('employee', 'leave_type'):
            typ = l.leave_type.name if l.leave_type_id else 'Leave'
            mine = self.me is not None and l.employee_id == self.me.id
            title = (typ if mine else f'{S.emp_name(l.employee)} · {typ}') + (' (pending)' if l.status == 'pending' else '')
            self.add('leave', l.id, title, l.start_date, l.end_date, employee=_who(l.employee), link=rec(l), status=l.status,
                     extra={'days': l.number_of_days, 'tentative': l.status == 'pending'})

    def holiday(self):
        H, AH = _m('calendars.holiday'), _m('calendars.assign_holiday')
        cal_ids = set()
        if self.scope == 'me' and self.me:
            try:
                from calendars.utils import get_employee_holiday_calendar
                hc = get_employee_holiday_calendar(self.me)
                if hc:
                    cal_ids.add(hc.id)
            except Exception:
                pass
        elif AH is not None:
            branches = {e.emp_branch_id_id for e in self.emps if e.emp_branch_id_id}
            cal_ids |= set(AH.objects.filter(Q(branch__in=branches) | Q(employee__in=self.ids)).values_list('holiday_model_id', flat=True))
        qs = H.objects.filter(self.overlap('start_date', 'end_date'))
        if cal_ids:
            qs = qs.filter(calendar_id__in=cal_ids)
        for h in qs.order_by('start_date'):
            self.add('holiday', h.id, h.description, h.start_date, h.end_date, link=M_ + 'settings/holiday-calendar', extra={'restricted': h.restricted})

    def attendance(self):
        from .reports import day_statuses
        Att = _m('calendars.Attendance')
        att = {(a.employee_id, a.date): a for a in Att.objects.filter(employee_id__in=self.ids, date__gte=self.d_from, date__lte=self.d_to).select_related('shift')}
        res = S.ShiftResolver(self.ids, self.d_from, self.d_to) if att else None
        statuses = day_statuses(self.emps, self.d_from, self.d_to, self.today)
        if self.scope == 'me':
            for e in self.emps:
                for d in statuses.get(e.id, []):
                    a = att.get((e.id, d['date']))
                    if d['status'] == 'Present' and a:
                        self._att_event(e, a, res)
                    elif d['status'] == 'Absent':
                        hr = self.c.admin or any(x in self.c.codes for x in ('view_attendancereport', 'view_attendance'))
                        self.add('attendance', f"{e.id}-{d['date']}", 'Absent' + (f" · leave pending ({d['pending'].leave_type})" if d['pending'] else ''),
                                 d['date'], employee=_who(e), status='absent',
                                 link=rpt('attendance', {'employee': self._full(e)}, **{'from': d['date'], 'to': d['date']}) if hr else None)
                a = att.get((e.id, self.today))
                if a and self.in_range(self.today):
                    self._att_event(e, a, res)
            return
        per = defaultdict(Counter)
        for (eid, d), a in att.items():
            per[d]['present'] += 1
            if S.minutes_late(a, res.shift(eid, d, a) if res else None):
                per[d]['late'] += 1
            if a.check_in_time and not a.check_out_time and d < self.today:
                per[d]['missing'] += 1
        for lst in statuses.values():
            for d in lst:
                if d['status'] == 'Absent':
                    per[d['date']]['absent'] += 1
                elif d['status'] == 'On Leave':
                    per[d['date']]['leave'] += 1
        for d in sorted(per):
            c = per[d]
            parts = [f"{c['present']} present"] + [f"{c[k]} {k}" for k in ('late', 'absent', 'leave') if c[k]] + ([f"{c['missing']} missing punch"] if c['missing'] else [])
            self.add('attendance', d.isoformat(), ' · '.join(parts), d, status='bad' if c['absent'] or c['missing'] else 'good',
                     link=rpt('attendance', **{'from': d, 'to': d}), extra={'counts': dict(c)})

    def _full(self, e):
        from .reports import full_name
        return full_name(e)

    def _att_event(self, e, a, res):
        sh = res.shift(e.id, a.date, a) if res else None
        late = S.minutes_late(a, sh)
        early = S.minutes_early(a, sh)
        t = f"In {a.check_in_time:%H:%M}" if a.check_in_time else 'No check-in'
        t += f" – out {a.check_out_time:%H:%M}" if a.check_out_time else (' (still in)' if a.date >= self.today else ' – no check-out')
        flags = (['late %d min' % late] if late else []) + (['left %d min early' % early] if early else [])
        st = 'bad' if (not a.check_out_time and a.date < self.today) else 'warn' if flags else 'good'
        self.add('attendance', f'{e.id}-{a.date}', t + (' · ' + ', '.join(flags) if flags else ''), a.date, employee=_who(e), link=rec(a), status=st)

    def shift(self):
        if not self.ids:
            return
        res = S.ShiftResolver(self.ids, self.d_from, self.d_to)
        if not res.sched and not res.over:
            return
        if self.scope == 'me':
            for e in self.emps:
                for d in self.days():
                    sh = res.shift(e.id, d)
                    if sh is None:
                        continue
                    st, en = sh.start_time, sh.end_time
                    end_day = d + timedelta(days=1) if en and st and en <= st else d
                    self.add('shift', f'{e.id}-{d}', sh.name, datetime.combine(d, st) if st else d, datetime.combine(end_day, en) if en else d,
                             all_day=not st, employee=_who(e), link=M_ + 'shift-options/shift-override' if (e.id, d) in res.over else None,
                             extra={'override': (e.id, d) in res.over})
            return
        if (self.d_to - self.d_from).days > 45:
            return   # per-day team summaries only for up to ~6 weeks
        for d in self.days():
            c = Counter()
            for i in self.ids:
                sh = res.shift(i, d)
                if sh is not None:
                    c[sh.name] += 1
            for name, n in c.items():
                self.add('shift', f'{d}-{name}', f'{name}: {n}', d, extra={'count': n})

    def training(self):
        TS, NOM = _m('LearningManagement.TrainingSession'), _m('LearningManagement.Nomination')
        if TS is None:
            return
        qs = TS.objects.filter(self.overlap('start_date', 'end_date')).exclude(status__in=('cancelled', 'draft')).select_related('course')
        who = defaultdict(list)
        if self.scope != 'company':
            noms = NOM.objects.filter(employee_id__in=self.ids, session__in=qs).exclude(seat_status='cancelled').select_related('employee')
            for n in noms:
                who[n.session_id].append(n)
            trainer = set(qs.filter(trainer_employee_id__in=self.ids).values_list('id', flat=True))
            qs = qs.filter(Q(id__in=list(who)) | Q(id__in=trainer))
        for s in qs:
            names = ', '.join(S.emp_name(n.employee) for n in who.get(s.id, [])[:3]) if self.scope == 'team' else ''
            title = (s.course.title if s.course_id else s.code) + (f' · {names}' if names else '')
            self.add('training', s.id, title, datetime.combine(s.start_date, s.start_time) if s.start_time else s.start_date,
                     datetime.combine(s.end_date or s.start_date, s.end_time) if s.end_time else (s.end_date or s.start_date), all_day=not s.start_time,
                     link=rec(s), status=s.status, extra={'venue': s.venue or s.online_link or ''})

    def interview(self):
        IV = _m('RecruitmentManagement.Interview')
        if IV is None:
            return
        qs = IV.objects.filter(scheduled_at__date__gte=self.d_from, scheduled_at__date__lte=self.d_to).exclude(status='cancelled').select_related(
            'application__candidate', 'application__job').prefetch_related('panel')
        if self.scope == 'company' and (self.c.admin or 'view_interview' in self.c.codes):
            pass
        else:
            qs = qs.filter(Q(panel__in=self.ids) | Q(application__job__hiring_manager=self.user)).distinct()
        for iv in qs:
            st = timezone.localtime(iv.scheduled_at) if timezone.is_aware(iv.scheduled_at) else iv.scheduled_at
            cand = iv.application.candidate
            title = f"Interview · {cand.first_name} {cand.last_name or ''}".strip() + f" ({iv.application.job.title}) · {iv.round_name or ''}".rstrip(' ·')
            self.add('interview', iv.id, title, st.replace(tzinfo=None), (st + timedelta(minutes=iv.duration_minutes or 60)).replace(tzinfo=None), all_day=False,
                     link=rec(iv), status=iv.status, extra={'mode': iv.mode, 'where': iv.location_or_link or ''})

    def meeting(self):
        ACT = _m('Chatter.Activity')
        if ACT is None:
            return
        users = {self.user.id}
        if self.scope == 'team':
            users |= {e.users_id for e in self.emps if e.users_id}
        for a in ACT.objects.filter(assigned_to_id__in=users, due_date__gte=self.d_from, due_date__lte=self.d_to).exclude(state='cancelled').select_related('assigned_to'):
            mine = a.assigned_to_id == self.user.id
            title = (a.get_activity_type_display() + ': ' if a.activity_type != 'todo' else '') + (a.summary or a.record_label or 'Activity')
            if not mine:
                title += f' · {a.assigned_to.username}'
            self.add('meeting', a.id, title, a.due_date, link=a.page or M_ + 'todo', status=a.state,
                     extra={'overdue': a.state == 'open' and a.due_date < self.today, 'record': a.record_label})

    def events_(self):
        for e in self.emps:
            if e.emp_joined_date:
                for y in range(self.d_from.year, self.d_to.year + 1):
                    d = _anniv(e.emp_joined_date, y)
                    if self.in_range(d) and y > e.emp_joined_date.year:
                        n = y - e.emp_joined_date.year
                        self.add('events', f'anniv-{e.id}-{y}', f"{S.emp_name(e)} · {n} year{'s' if n > 1 else ''} with us", d, employee=_who(e), link=rec(e), status='anniversary')
            if self.in_range(e.emp_date_of_confirmation):
                self.add('events', f'prob-{e.id}', f'{S.emp_name(e)} · probation ends', e.emp_date_of_confirmation, employee=_who(e), link=rec(e), status='probation')
            if self.in_range(e.emp_joined_date):
                self.add('events', f'join-{e.id}', f'{S.emp_name(e)} · joins', e.emp_joined_date, employee=_who(e), link=rec(e), status='joining')
        DOC = _m('EmpManagement.Emp_Documents')
        for d in DOC.objects.filter(emp_id_id__in=self.ids, emp_doc_expiry_date__gte=self.d_from, emp_doc_expiry_date__lte=self.d_to).select_related('emp_id', 'document_type'):
            typ = d.document_type.type_name if d.document_type_id else 'Document'
            self.add('events', f'doc-{d.id}', f'{typ} expires · {S.emp_name(d.emp_id)}', d.emp_doc_expiry_date, employee=_who(d.emp_id), link=rec(d),
                     status='expired' if d.emp_doc_expiry_date < self.today else 'expiry')

    def birthday(self):
        if self.scope == 'me':
            Emp = _m('EmpManagement.emp_master')
            q = Emp.objects.filter(is_active=True, emp_date_of_birth__isnull=False)
            if self.me and self.me.emp_branch_id_id:
                q = q.filter(emp_branch_id=self.me.emp_branch_id_id)
            elif self.c.branches is not None:
                q = q.filter(emp_branch_id__in=self.c.branches)
            people, links = list(q.only('id', 'emp_first_name', 'emp_last_name', 'emp_code', 'emp_date_of_birth')), False
        else:
            people, links = [e for e in self.emps if e.emp_date_of_birth], True
        for e in people:
            for y in range(self.d_from.year, self.d_to.year + 1):
                d = _anniv(e.emp_date_of_birth, y)
                if self.in_range(d):
                    # day and month only – never the year of birth or the age
                    self.add('birthday', f'{e.id}-{y}', f'Birthday · {S.emp_name(e)}', d, employee={'id': e.id if links else None, 'name': S.emp_name(e)},
                             link=None, extra={'day': d.strftime('%d %b')})

    def company(self):
        A = _m('OrganisationManager.Announcement')
        if A is None:
            return
        qs = A.objects.all().prefetch_related('specific_employees', 'branches', 'department', 'designation', 'category')
        hr = self.c.admin or 'view_announcement' in self.c.codes
        for a in qs:
            when = a.schedule_at or a.created_at
            if when is None:
                continue
            d = timezone.localtime(when).date() if timezone.is_aware(when) else when.date()
            if not self.in_range(d):
                continue
            if not hr and not _for_me(a, self.me, self.c.branches):
                continue
            self.add('company', a.id, a.title, d, link=M_ + 'general-sidebar/announcement-master' if hr else None, status='sticky' if a.is_sticky else None,
                     extra={'message': (a.message or '')[:200]})

    def build(self):
        fn = {'leave': self.leave, 'holiday': self.holiday, 'attendance': self.attendance, 'shift': self.shift, 'training': self.training,
              'interview': self.interview, 'meeting': self.meeting, 'events': self.events_, 'birthday': self.birthday, 'company': self.company}
        errors = []
        for k in self.layers:
            try:
                fn[k]()
            except Exception as exc:   # one broken source never hides the others
                import logging
                logging.getLogger(__name__).exception('calendar layer %s failed', k)
                errors.append(k)
        cnt = Counter(e['layer'] for e in self.events)
        self.events.sort(key=lambda e: (str(e['start'])[:10], 0 if e['allDay'] else 1, str(e['start']), e['layer']))
        return {'from': self.d_from, 'to': self.d_to, 'today': self.today, 'scope': self.scope, 'scopes': self.scopes,
                'layers': [{'key': k, 'label': l, 'color': c, 'count': cnt.get(k, 0), 'on': k in self.layers} for k, l, c in LAYERS],
                'events': self.events, 'errors': errors, 'employees': len(self.ids)}


def _anniv(d0, y):
    try:
        return d0.replace(year=y)
    except ValueError:
        return d0.replace(year=y, day=28)


def _for_me(a, me, branches=None):
    """Is an announcement addressed to this employee (no target = everybody)? Users without an employee record: by their branches."""
    if me is None:
        if a.specific_employees.exists() or a.department.exists() or a.designation.exists() or a.category.exists():
            return False
        ids = [x.id for x in a.branches.all()]
        return not ids or branches is None or bool(set(ids) & set(branches))
    checks = [(a.specific_employees.all(), me.id), (a.branches.all(), me.emp_branch_id_id), (a.department.all(), me.emp_dept_id_id),
              (a.designation.all(), me.emp_desgntn_id_id), (a.category.all(), me.emp_ctgry_id_id)]
    for qs, val in checks:
        ids = [x.id for x in qs]
        if ids and val not in ids:
            return False
    return True


class CalendarView(APIView):
    """GET dashboard/api/calendar/?from&to&layers&scope&branch – one calendar over every module."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(Cal(request).build())
