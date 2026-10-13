"""v1.24.0 – move companies between the shared cloud and an account's own database (the console does the same).

    python manage.py cloud_move --schemas a,b --to own            # to the own database of their account (created if needed)
    python manage.py cloud_move --schemas a --to shared           # back to the main database
    python manage.py cloud_move --back <job id>                   # roll back a finished move
    python manage.py cloud_move --list                            # moves and old copies
    python manage.py cloud_move --drop-old                        # remove old copies older than 7 days

Each company is closed (503) while it is copied, checked (tables, rows, numbering) and switched; the old copy is kept
7 days under the name zmv_<date>_<schema>.
"""
from django.core.management import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Move companies between the shared cloud and own databases'

    def add_arguments(self, p):
        p.add_argument('--schemas', default='')
        p.add_argument('--to', choices=['own', 'shared'])
        p.add_argument('--back', type=int)
        p.add_argument('--list', action='store_true')
        p.add_argument('--drop-old', action='store_true')
        p.add_argument('--settle', type=int, default=None, help='Seconds to wait so every server sees the change (default 12)')

    def handle(self, *a, **o):
        from ZeoCloud import owndb
        from ZeoCloud.models import AccountCompany, MoveJob
        from UserManagement.models import company
        if o['list']:
            for j in MoveJob.objects.all()[:50]:
                self.stdout.write(f'{j.id:5} {j.created_at:%Y-%m-%d %H:%M} {j.schema:30} {j.source or "main":8} → {j.target or "main":8} '
                                  f'{j.state:11} {j.tables} tables {j.rows} rows {j.old_copy} {j.error[:80]}')
            return
        if o['drop_old']:
            self.stdout.write(f'Removed: {owndb.drop_old_copies() or "nothing"}')
            return
        if o['back']:
            job = MoveJob.objects.filter(pk=o['back']).first()
            if not job:
                raise CommandError('No such move.')
            new = owndb.move_back(job, settle=o['settle'], run_now=True)
            self.stdout.write(f'{new.schema}: {new.state} {new.error}')
            return
        if not o['schemas'] or not o['to']:
            raise CommandError('Give --schemas and --to, or --back / --list / --drop-old.')
        for s in [x.strip() for x in o['schemas'].split(',') if x.strip()]:
            comp = company.objects.filter(schema_name=s).first()
            link = AccountCompany.objects.select_related('account').filter(company=comp).first() if comp else None
            if not link:
                self.stdout.write(self.style.ERROR(f'{s}: not a company of a cloud account'))
                continue
            if o['to'] == 'own':
                target = owndb.create_own_db(link.account).key
            else:
                target = ''
            try:
                job = owndb.start_move(comp, target, settle=o['settle'], run_now=True)
            except owndb.MoveError as e:
                self.stdout.write(self.style.ERROR(f'{s}: {e}'))
                continue
            style = self.style.SUCCESS if job.state == 'done' else self.style.ERROR
            self.stdout.write(style(f'{s}: {job.state} – {job.tables} tables, {job.rows} rows {job.error}'))
