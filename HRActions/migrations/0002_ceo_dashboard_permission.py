"""v1.11.0 – permission 'view_ceo_dashboard' (CEO dashboard for non-admin users)."""
from django.db import migrations


def add(apps, schema_editor):
    CT = apps.get_model('contenttypes', 'ContentType')
    P = apps.get_model('auth', 'Permission')
    ct, _ = CT.objects.get_or_create(app_label='DashboardManagement', model='ceodashboard')
    P.objects.get_or_create(codename='view_ceo_dashboard', content_type=ct, defaults={'name': 'Can view CEO dashboard'})


class Migration(migrations.Migration):
    dependencies = [('HRActions', '0001_initial'), ('contenttypes', '0002_remove_content_type_name'), ('auth', '0012_alter_user_first_name_max_length')]
    operations = [migrations.RunPython(add, migrations.RunPython.noop)]
