"""v1.23.0 – give or take Adept staff access to the control console: cloud_admin --add a@adept.ae / --remove a@adept.ae / --list"""
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q


class Command(BaseCommand):
    help = 'Adept staff who may open the ZEO Cloud control console'

    def add_arguments(self, p):
        p.add_argument('--add')
        p.add_argument('--remove')
        p.add_argument('--list', action='store_true')

    def handle(self, *args, **o):
        from UserManagement.models import CustomUser
        from ZeoCloud.models import PlatformAdmin
        for key in ('add', 'remove'):
            v = o.get(key)
            if not v:
                continue
            u = CustomUser.objects.filter(Q(email__iexact=v) | Q(username__iexact=v)).first()
            if u is None:
                raise CommandError(f'No user {v}')
            if key == 'add':
                PlatformAdmin.objects.get_or_create(user=u)
                self.stdout.write(self.style.SUCCESS(f'{u.username} can open the console.'))
            else:
                PlatformAdmin.objects.filter(user=u).delete()
                self.stdout.write(f'{u.username} removed from the console.')
        if o.get('list') or not (o.get('add') or o.get('remove')):
            for a in PlatformAdmin.objects.select_related('user'):
                self.stdout.write(f'{a.user.username} <{a.user.email}>')
