"""v1.12.0 – shift planner permissions; groups that already plan shifts (change_employeeshiftschedule) get the
roster rights (view / add / change / delete roster, open shifts, requests, availability and publish_rosterperiod).
approve_rosterperiod is NOT given to anybody – the company admin assigns it (or names an approver on each roster)."""
from django.db import migrations

MODELS = ['shiftrule', 'rosterperiod', 'rosterentry', 'availability', 'openshift', 'openshiftclaim', 'shiftrequest', 'shiftnotice']
EXTRA = [('rosterperiod', 'approve_rosterperiod', 'Can approve shift rosters'), ('rosterperiod', 'publish_rosterperiod', 'Can publish shift rosters')]


def add(apps, schema_editor):
    CT = apps.get_model('contenttypes', 'ContentType')
    P = apps.get_model('auth', 'Permission')
    G = apps.get_model('auth', 'Group')
    perms = {}
    for m in MODELS:
        ct, _ = CT.objects.get_or_create(app_label='ShiftPlanner', model=m)
        for verb in ('view', 'add', 'change', 'delete'):
            p, _ = P.objects.get_or_create(codename=f'{verb}_{m}', content_type=ct, defaults={'name': f'Can {verb} {m}'})
            perms[p.codename] = p
    for m, code, name in EXTRA:
        ct, _ = CT.objects.get_or_create(app_label='ShiftPlanner', model=m)
        p, _ = P.objects.get_or_create(codename=code, content_type=ct, defaults={'name': name})
        perms[code] = p
    give = [p for c, p in perms.items() if c != 'approve_rosterperiod']
    for g in G.objects.filter(permissions__codename='change_employeeshiftschedule').distinct():
        g.permissions.add(*give)


class Migration(migrations.Migration):
    dependencies = [('ShiftPlanner', '0001_initial'), ('contenttypes', '0002_remove_content_type_name'), ('auth', '0012_alter_user_first_name_max_length')]
    operations = [migrations.RunPython(add, migrations.RunPython.noop)]
