"""
v1.62.0 talent-ops – the lists the Learning tools open on (instead of a bare "pick a session / pick an employee" select):

GET  api/attendance/sessions/      sessions that take attendance: dates, course, confirmed participants, days marked, state
GET  api/history/people/           employees with their training count, passed, hours, valid certificates and last training

Read only; the attendance sheet and the training history keep their own endpoints.
"""
from collections import defaultdict
from datetime import timedelta

from django.db.models import Count
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from Translations.i18n import tr

from . import models as M
from .access import employees_qs, is_hr
from .views import LPView, _lm


def _days(s):
    n = (s.end_date - s.start_date).days + 1 if s.start_date and s.end_date else 1
    return max(1, min(n, 60))


class AttendanceSessionsView(LPView):
    def get(self, request):
        if not is_hr(request, 'participantresult', 'change'):
            raise PermissionDenied(tr('The attendance sheet is for L&D / HR.'))
        m = _lm()
        today = timezone.localdate()
        qs = (m.TrainingSession.objects.filter(status__in=('published', 'in_progress', 'completed'))
              .select_related('course', 'trainer_employee').order_by('-start_date'))
        sessions = list(qs[:200])
        ids = [s.id for s in sessions]
        confirmed = dict(m.Nomination.objects.filter(session_id__in=ids, seat_status='confirmed').values('session_id')
                         .annotate(n=Count('id')).values_list('session_id', 'n'))
        marked = defaultdict(set)
        for a in M.SessionAttendance.objects.filter(session_id__in=ids).values('session_id', 'date'):
            marked[a['session_id']].add(a['date'])
        results = dict(m.ParticipantResult.objects.filter(nomination__session_id__in=ids).values('nomination__session_id')
                       .annotate(n=Count('id')).values_list('nomination__session_id', 'n'))
        rows = []
        for s in sessions:
            days = _days(s)
            done_days = len(marked.get(s.id, ()))
            if s.start_date and s.start_date > today:
                state = 'upcoming'
            elif done_days >= days or (results.get(s.id) and s.status == 'completed'):
                state = 'marked'
            elif done_days:
                state = 'partly'
            else:
                state = 'to_mark'
            rows.append({'id': s.id, 'code': s.code, 'course': s.course.title if s.course_id else '', 'start': s.start_date, 'end': s.end_date,
                         'days': days, 'days_marked': done_days, 'confirmed': confirmed.get(s.id, 0), 'results': results.get(s.id, 0),
                         'status': s.status, 'status_label': s.get_status_display(), 'state': state,
                         'venue': s.venue or '', 'trainer': s.trainer_external or (' '.join(x for x in (s.trainer_employee.emp_first_name, s.trainer_employee.emp_last_name) if x)
                                                               if s.trainer_employee_id else '')})
        order = {'to_mark': 0, 'partly': 1, 'upcoming': 2, 'marked': 3}
        rows.sort(key=lambda r: (order.get(r['state'], 9), -(r['start'].toordinal() if r['start'] else 0)))
        summary = {k: len([r for r in rows if r['state'] == k]) for k in order}
        return Response({'rows': rows, 'summary': summary})


class HistoryPeopleView(LPView):
    def get(self, request):
        if not is_hr(request):
            raise PermissionDenied(tr('Training history of other employees is for L&D / HR.'))
        m = _lm()
        today = timezone.localdate()
        emps = list(employees_qs(request).exclude(is_active=False).select_related('emp_dept_id', 'emp_desgntn_id').order_by('emp_code'))
        ids = [e.id for e in emps]
        noms = (m.Nomination.objects.filter(employee_id__in=ids, seat_status='confirmed').select_related('session', 'session__course'))
        res = {r.nomination_id: r for r in m.ParticipantResult.objects.filter(nomination__employee_id__in=ids)}
        stats = defaultdict(lambda: {'sessions': 0, 'done': 0, 'passed': 0, 'hours': 0.0, 'last': None, 'last_course': '', 'upcoming': 0})
        for n in noms:
            s = n.session
            st = stats[n.employee_id]
            if s.start_date and s.start_date > today:
                st['upcoming'] += 1
                continue
            st['sessions'] += 1
            r = res.get(n.id)
            if r is not None:
                st['done'] += 1
                if r.passed:
                    st['passed'] += 1
            dur = getattr(s.course, 'duration_hours', None) if s.course_id else None
            st['hours'] += float(dur or 0)
            if s.start_date and (st['last'] is None or s.start_date > st['last']):
                st['last'], st['last_course'] = s.start_date, s.course.title if s.course_id else ''
        certs = defaultdict(lambda: {'valid': 0, 'expiring': 0, 'expired': 0})
        soon = today + timedelta(days=60)
        for c_ in m.Certificate.objects.filter(employee_id__in=ids).values('employee_id', 'expiry_date'):
            x = certs[c_['employee_id']]
            exp = c_['expiry_date']
            if exp and exp < today:
                x['expired'] += 1
            elif exp and exp <= soon:
                x['expiring'] += 1
            else:
                x['valid'] += 1
        rows = []
        for e in emps:
            st = stats[e.id]
            rows.append({'id': e.id, 'code': e.emp_code, 'name': ' '.join(x for x in (e.emp_first_name, e.emp_last_name) if x),
                         'department': e.emp_dept_id.dept_name if e.emp_dept_id_id else '',
                         'designation': e.emp_desgntn_id.desgntn_job_title if e.emp_desgntn_id_id else '',
                         'sessions': st['sessions'], 'passed': st['passed'], 'done': st['done'], 'hours': round(st['hours'], 1),
                         'upcoming': st['upcoming'], 'last': st['last'], 'last_course': st['last_course'], 'certificates': certs[e.id]})
        trained = [r for r in rows if r['sessions']]
        summary = {'employees': len(rows), 'trained': len(trained), 'never': len(rows) - len(trained),
                   'hours': round(sum(r['hours'] for r in rows), 1),
                   'expiring': sum(r['certificates']['expiring'] for r in rows), 'expired': sum(r['certificates']['expired'] for r in rows)}
        return Response({'rows': rows, 'summary': summary})
