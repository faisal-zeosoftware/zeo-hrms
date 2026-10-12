"""
v1.16.0 – fill the daily attendance results (AttendancePlus.AttendanceDay) for the history.

The daily results are only written for days that were recalculated (punch, nightly job, correction). Companies that
used the older attendance entries (calendars.Attendance: manual entry, imports, old punch screens) therefore had
attendance rows but no daily results, and the Attendance app showed empty history. This command computes the day of
every active employee from the punches – or, when there are none, from the attendance entry – with the shift, weekly
off, holidays, leave and the attendance rules, exactly like the nightly job.

    python manage.py rebuild_attendance_days                      dry run: what would be written (nothing is changed)
    python manage.py rebuild_attendance_days --apply              write the daily results
    options: --schema adepttest  --from 2026-01-01  --to 2026-09-30  --employee 12  --all (recompute days that already
             have a result)  --with-overtime (also write the overtime rows and the attendance calendar, like the nightly
             job – leave it off to rebuild history without creating overtime for months already paid)
Default period: from the first attendance entry of the company (at most 400 days back) to yesterday.
"""
from collections import Counter
from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_date
from django_tenants.utils import get_tenant_model, schema_context


class Command(BaseCommand):
    help = 'Compute the daily attendance results for past days from punches and attendance entries (dry run unless --apply).'

    def add_arguments(self, parser):
        parser.add_argument('--schema', help='Only this company (schema name).')
        parser.add_argument('--from', dest='date_from', help='First day (YYYY-MM-DD).')
        parser.add_argument('--to', dest='date_to', help='Last day (YYYY-MM-DD); default yesterday.')
        parser.add_argument('--employee', type=int, help='Only this employee id.')
        parser.add_argument('--all', action='store_true', help='Also recompute days that already have a daily result.')
        parser.add_argument('--apply', action='store_true', help='Write the results (default: dry run).')
        parser.add_argument('--with-overtime', action='store_true', help='Also write overtime rows and the attendance calendar.')

    def handle(self, *args, **o):
        qs = get_tenant_model().objects.exclude(schema_name='public')
        if o.get('schema'):
            qs = qs.filter(schema_name=o['schema'])
            if not qs.exists():
                raise CommandError(f"No company with schema {o['schema']}.")
        w = self.stdout.write
        w('APPLY' if o['apply'] else 'DRY RUN (nothing is changed – add --apply to write the daily results)')
        for t in qs.order_by('schema_name'):
            with schema_context(t.schema_name):
                res = rebuild(o)
            w(f"\n== {t.schema_name} ({t.name})")
            if res is None:
                w('   the Attendance app is not installed in this company')
                continue
            w(f"   period {res['from']} – {res['to']} · {res['employees']} employees · {res['days']} employee-days looked at")
            w(f"   {'written' if o['apply'] else 'would write'}: {res['write']} · kept (already computed): {res['kept']} · before joining: {res['skipped']}")
            w('   by status: ' + (', '.join(f'{k} {v}' for k, v in sorted(res['status'].items())) or '–'))
            w(f"   from attendance entries: {res['legacy']} · from punches: {res['punches']}")
            if res['errors']:
                w(f"   failed: {res['errors']} (see the log)")


def rebuild(o):
    import logging
    from django.apps import apps
    if not apps.is_installed('AttendancePlus'):
        return None
    from AttendancePlus import engine as E
    from AttendancePlus.models import AttendanceDay
    from calendars.models import Attendance
    from EmpManagement.models import emp_master
    log = logging.getLogger(__name__)
    today = E.today()
    d_to = parse_date(o['date_to']) if o.get('date_to') else today - timedelta(days=1)
    if o.get('date_from'):
        d_from = parse_date(o['date_from'])
    else:
        first = Attendance.objects.order_by('date').values_list('date', flat=True).first()
        d_from = max(first or (today - timedelta(days=90)), today - timedelta(days=400))
    if not d_from or not d_to or d_to < d_from:
        raise CommandError('Give a valid period: --from must be on or before --to.')
    d_to = min(d_to, today - timedelta(days=1))
    emps = emp_master.objects.filter(is_active=True).select_related('emp_branch_id', 'emp_dept_id')
    if o.get('employee'):
        emps = emps.filter(pk=o['employee'])
    have = set(AttendanceDay.objects.filter(date__gte=d_from, date__lte=d_to).exclude(status='not_marked').values_list('employee_id', 'date'))
    res = {'from': d_from, 'to': d_to, 'employees': 0, 'days': 0, 'write': 0, 'kept': 0, 'skipped': 0, 'status': Counter(), 'legacy': 0, 'punches': 0, 'errors': 0}
    for e in emps:
        res['employees'] += 1
        rule = E.rule_for(e)
        d = max(d_from, e.emp_joined_date) if e.emp_joined_date else d_from
        res['skipped'] += max(0, (d - d_from).days)
        while d <= d_to:
            res['days'] += 1
            if (e.id, d) in have and not o.get('all'):
                res['kept'] += 1
                d += timedelta(days=1)
                continue
            try:
                if o['apply'] and o.get('with_overtime'):
                    rec = E.recompute_day(e, d, rule, sync_calendar=True)
                    r = {'status': rec.status, 'flags': rec.flags, 'punch_count': rec.punch_count}
                else:
                    r = E.compute(e, d, rule)
                    if o['apply']:
                        fields = {k: v for k, v in r.items() if k not in ('employee_id', 'date')}
                        old = AttendanceDay.objects.filter(employee_id=e.id, date=d).first()
                        if old:   # keep what people decided on the day
                            fields['penalty_waived'] = old.penalty_waived
                            fields['notified_missing_at'] = old.notified_missing_at
                            fields['period_ot_minutes'] = old.period_ot_minutes
                        AttendanceDay.objects.update_or_create(employee_id=e.id, date=d, defaults=fields)
                res['write'] += 1
                res['status'][r['status']] += 1
                if r['flags'].get('from_attendance_row'):
                    res['legacy'] += 1
                elif r.get('punch_count'):
                    res['punches'] += 1
            except Exception:
                res['errors'] += 1
                log.exception('rebuild_attendance_days failed for employee %s on %s', e.pk, d)
            d += timedelta(days=1)
    return res
