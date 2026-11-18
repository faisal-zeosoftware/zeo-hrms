"""v1.73.15 – demo data from the command line (the same job as Create company › "Do you want demo data?").

    python manage.py make_demo_data --level simple                    10 demo companies (7 countries, 10 industries), 1 month
    python manage.py make_demo_data --level heavy                     the same, 6 months
    python manage.py make_demo_data --level simple --industry HC --country QA     one industry in one country
    python manage.py make_demo_data --refresh [--level heavy]         the demo companies again with fresh dates
    python manage.py make_demo_data --remove                          delete every demo company (only companies marked as demo)
    python manage.py make_demo_data --list                            the demo companies on this server

Industries: CON FM PM OG RET HC MFG FIN EDU IT. Countries: AE SA QA OM BH KW IN.
"""
import argparse
import time

from django.core.management.base import BaseCommand, CommandError


def argparse_hidden():
    return argparse.SUPPRESS


class Command(BaseCommand):
    help = 'Make, refresh, list or remove the demo companies (7 countries, 10 industries).'
    requires_system_checks = []   # the job starts one process per company: no system checks each time

    def add_arguments(self, p):
        p.add_argument('--level', choices=['simple', 'heavy'], default=None)
        p.add_argument('--industry', default='')
        p.add_argument('--country', default='AE')
        p.add_argument('--user', default='', help='the administrator who makes them (default: the first system administrator)')
        p.add_argument('--refresh', action='store_true')
        p.add_argument('--remove', action='store_true')
        p.add_argument('--list', action='store_true')
        # used by the demo job itself: one company in its own process
        p.add_argument('--one-company', default='', help=argparse_hidden())
        p.add_argument('--job', default='', help=argparse_hidden())
        p.add_argument('--user-id', default='', help=argparse_hidden())

    def handle(self, *a, **o):
        from DataTools import demo_jobs as J
        from UserManagement.models import CustomUser
        if o['one_company']:   # a worker of the demo job (DataTools.demo_jobs._run)
            import json
            J.make_one_safe(o['job'], tuple(json.loads(o['one_company'])), o['level'] or 'simple', int(o['user_id']) if o['user_id'] else None)
            return
        if o['list']:
            for d in J.demo_companies():
                self.stdout.write(f"{d['name']:45} {d['schema']:40} {d.get('level', '')}: {d.get('from', '')} – {d.get('to', '')} "
                                  f"rows {d.get('rows', 0)}, skipped {d.get('failed', 0)}")
            return
        if o['remove']:
            gone = J.remove()
            self.stdout.write(self.style.SUCCESS(f'{len(gone)} demo companies removed.'))
            return
        user = CustomUser.objects.filter(username=o['user']).first() if o['user'] else \
            CustomUser.objects.filter(is_superuser=True, is_active=True).order_by('pk').first()
        if user is None:
            raise CommandError('No administrator found – use --user.')
        t0 = time.time()
        if o['refresh']:
            have = J.demo_companies()
            if not have:
                raise CommandError('There are no demo companies to refresh.')
            job = J.start(o['level'] or have[0].get('level') or 'simple', user, specs=[tuple(d['spec']) for d in have if d.get('spec')], run_now=True)
        else:
            if not o['level']:
                raise CommandError('Give --level simple or --level heavy.')
            scope = 'one' if o['industry'] else 'all'
            job = J.start(o['level'], user, scope=scope, industry=o['industry'].upper(), country=o['country'].upper(), run_now=True)
        st = J.status(job) or {}
        for c in st.get('companies', []):
            self.stdout.write(f"{c['state']:7} {c['name']:45} {c['country']} – {c['industry']}: {c.get('rows', 0)} rows, "
                              f"{c.get('failed', 0)} skipped, {c.get('seconds', 0)} s {c.get('step', '')}")
        self.stdout.write(self.style.SUCCESS(f"Demo data {st.get('state')} in {int(time.time() - t0)} s."))
