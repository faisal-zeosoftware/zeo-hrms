"""v1.67.26 (pending item 1.62.12, item 236) – calendar sync with Google Calendar, Outlook and phones (iCalendar subscription).

Self service › My settings › Calendar sync gives the user a private link:
    <server>/self-service/api/calendar/<token>.ics?schema=<company>
Google Calendar (Other calendars › From URL), Outlook (Add calendar › Subscribe from web) or the phone subscribe to it and refresh
it themselves (every few hours). It holds, from 3 months back to 12 months ahead:
  * the user's own leave (approved, and waiting for approval marked "(waiting)"),
  * the leave of the people who report to the user (managers – can be switched off),
  * the public holidays of the user's holiday calendar,
  * the interviews the user sits on the panel of.
Read-only: nothing comes back from the calendar. The link is personal – "New link" stops the old one at once. No paid service and
no account with Google or Microsoft is needed.
"""
from zeo.tz import today as _zt_today   # v1.73.16 the user's / company's day, not the server's UTC day
import secrets
from datetime import date, datetime, timedelta

from django.apps import apps
from django.utils import timezone

M = apps.get_model


def feed_for(user, make=True):
    F = M('SelfService', 'CalendarFeed')
    f = F.objects.filter(user_id=user.pk).first()
    if f is None and make:
        f = F.objects.create(user_id=user.pk, token=secrets.token_urlsafe(32))
    return f


def renew(user):
    f = feed_for(user)
    f.token = secrets.token_urlsafe(32)
    f.save(update_fields=['token'])
    return f


def url(request, f):
    from django.conf import settings
    from django.db import connection
    base = (getattr(settings, 'ZEO_API_URL', '') or '').rstrip('/')
    if not base:   # v1.67.26: the API is served at the app's address (nginx) – keep its scheme and port (Host has no port behind nginx)
        from urllib.parse import urlparse
        app = (getattr(settings, 'ZEO_APP_URL', '') or '').rstrip('/')
        if app and urlparse(app).hostname == request.get_host().split(':')[0]:
            base = app
    base = base or request.build_absolute_uri('/').rstrip('/')
    return f'{base}/self-service/api/calendar/{f.token}.ics?schema={connection.schema_name}'


def _esc(s):
    return str(s or '').replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,').replace('\n', '\\n')


def _fold(line):
    """RFC 5545: lines longer than 75 octets are folded."""
    b = line.encode('utf-8')
    if len(b) <= 75:
        return line
    out, cur = [], b''
    for ch in line:
        e = ch.encode('utf-8')
        if len(cur) + len(e) > (75 if not out else 74):
            out.append(cur.decode('utf-8'))
            cur = b''
        cur += e
    out.append(cur.decode('utf-8'))
    return '\r\n '.join(out)


def _all_day(uid, start, end, summary, desc='', cat=''):
    return ['BEGIN:VEVENT', f'UID:{uid}', f'DTSTAMP:{timezone.now().strftime("%Y%m%dT%H%M%SZ")}', f'DTSTART;VALUE=DATE:{start:%Y%m%d}',
            f'DTEND;VALUE=DATE:{(end + timedelta(days=1)):%Y%m%d}', f'SUMMARY:{_esc(summary)}'] + \
           ([f'DESCRIPTION:{_esc(desc)}'] if desc else []) + ([f'CATEGORIES:{_esc(cat)}'] if cat else []) + ['TRANSP:TRANSPARENT', 'END:VEVENT']


def build(f):
    """The iCalendar text of the feed."""
    from django.db import connection
    from UserManagement.models import CustomUser
    user = CustomUser.objects.filter(pk=f.user_id).first()
    E = M('EmpManagement', 'emp_master')
    emp = E.objects.filter(users_id=f.user_id).first()
    a, b = _zt_today() - timedelta(days=92), _zt_today() + timedelta(days=366)
    sch = connection.schema_name
    co = ''
    try:
        from SelfService.letters import company_name
        co = company_name()
    except Exception:   # noqa: BLE001
        pass
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//ZEO HRMS//Calendar sync//EN', 'CALSCALE:GREGORIAN', 'METHOD:PUBLISH',
             f'X-WR-CALNAME:{_esc("ZEO – " + (co or "HR"))}', 'X-PUBLISHED-TTL:PT4H', 'REFRESH-INTERVAL;VALUE=DURATION:PT4H']
    LR = M('calendars', 'employee_leave_request')
    if emp is not None:
        for r in LR.objects.filter(employee=emp, status__in=('approved', 'pending'), start_date__lte=b, end_date__gte=a).select_related('leave_type'):
            w = '' if r.status == 'approved' else ' (waiting)'
            lines += _all_day(f'zeo-{sch}-leave-{r.pk}', r.start_date, r.end_date, f'{r.leave_type.name if r.leave_type_id else "Leave"}{w}',
                              f'{r.document_number or ""} – {r.reason or ""}'.strip(' –'), 'Leave')
    if user is not None and f.team:
        team = E.objects.filter(emp_reporting_manager=user, is_active=True)
        for r in LR.objects.filter(employee__in=team, status='approved', start_date__lte=b, end_date__gte=a).select_related('leave_type', 'employee'):
            nm = ' '.join(x for x in (r.employee.emp_first_name, r.employee.emp_last_name) if x)
            lines += _all_day(f'zeo-{sch}-team-{r.pk}', r.start_date, r.end_date, f'{nm} – {r.leave_type.name if r.leave_type_id else "leave"}', '', 'Team leave')
    if emp is not None and f.holidays:
        try:
            from calendars.utils import get_employee_holiday_calendar
            cal = get_employee_holiday_calendar(emp)
            if cal is not None:
                for h in M('calendars', 'holiday').objects.filter(calendar=cal, start_date__lte=b, end_date__gte=a):
                    lines += _all_day(f'zeo-{sch}-hol-{h.pk}', h.start_date, h.end_date, h.description, h.note or '', 'Public holiday')
        except Exception:   # noqa: BLE001
            import logging
            logging.getLogger(__name__).exception('holidays in the calendar feed')
    if emp is not None and f.interviews and apps.is_installed('RecruitmentManagement'):
        IV = M('RecruitmentManagement', 'Interview')
        for iv in IV.objects.filter(panel=emp, scheduled_at__date__gte=a, scheduled_at__date__lte=b).exclude(status='cancelled').select_related('application__candidate', 'application__job'):
            st = iv.scheduled_at.astimezone(timezone.utc)
            en = st + timedelta(minutes=iv.duration_minutes or 60)
            cand = iv.application.candidate
            lines += ['BEGIN:VEVENT', f'UID:zeo-{sch}-iv-{iv.pk}', f'DTSTAMP:{timezone.now().strftime("%Y%m%dT%H%M%SZ")}',
                      f'DTSTART:{st:%Y%m%dT%H%M%SZ}', f'DTEND:{en:%Y%m%dT%H%M%SZ}',
                      f'SUMMARY:{_esc("Interview: " + (cand.full_name if cand else "") + " – " + (iv.round_name or ""))}',
                      f'LOCATION:{_esc(iv.location_or_link or "")}', 'CATEGORIES:Interview', 'END:VEVENT']
    lines.append('END:VCALENDAR')
    return '\r\n'.join(_fold(x) for x in lines) + '\r\n'
