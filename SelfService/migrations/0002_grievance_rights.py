"""v1.13.0 – permission handle_grievance (+ view_grievance for the menu); given to the groups that already manage employees (change_emp_master)."""
from django.db import migrations


def add(apps, schema_editor):
    CT = apps.get_model('contenttypes', 'ContentType')
    P = apps.get_model('auth', 'Permission')
    G = apps.get_model('auth', 'Group')
    ct, _ = CT.objects.get_or_create(app_label='SelfService', model='grievance')
    perm, _ = P.objects.get_or_create(codename='handle_grievance', content_type=ct, defaults={'name': 'Can handle employee complaints / grievances'})
    view, _ = P.objects.get_or_create(codename='view_grievance', content_type=ct, defaults={'name': 'Can view grievance'})
    for g in G.objects.filter(permissions__codename='change_emp_master').distinct():
        g.permissions.add(perm, view)


class Migration(migrations.Migration):
    dependencies = [('SelfService', '0001_initial'), ('contenttypes', '0002_remove_content_type_name'), ('auth', '0012_alter_user_first_name_max_length')]
    operations = [migrations.RunPython(add, migrations.RunPython.noop)]
