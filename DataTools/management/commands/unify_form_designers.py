"""v1.16.0 – one form designer per form (DataTools.onedesign).

    python manage.py unify_form_designers                 # dry run: lists what would move, nothing changes
    python manage.py unify_form_designers --apply         # moves definitions + values, keeps a backup
    python manage.py unify_form_designers --schema adepttest --apply
    python manage.py unify_form_designers --apply --purge-merged   # also removes merged fields with no conflicts

1. Extra fields made with the record panel's "Design this form" on an employee form (employee, family, qualification,
   job history, documents) or on assets move into that form's own designer (employee custom fields / asset custom
   fields) with their type, options, mandatory flag, section, order, help text, default and rules, and their values.
   A field already defined in the owning designer under the same name is merged (its values win; differences are listed).
   The old extra field is switched off (kept in the database for the backup).
2. Custom fields that duplicate a built-in field (e.g. "Visa Type" = UAE identity → Visa type) are merged into the
   built-in field: values are copied where the built-in field is empty, differences are listed and never overwritten,
   and the custom field is hidden on every form (its values stay).
Idempotent: running it again changes nothing.
"""
from django.core.management.base import BaseCommand
from django_tenants.utils import get_tenant_model, schema_context


class Command(BaseCommand):
    help = 'Move duplicate form designer fields and values into the one designer of each form (dry run unless --apply).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Change the data (default: dry run).')
        parser.add_argument('--schema', default='', help='Only this company schema.')
        parser.add_argument('--purge-merged', action='store_true', help='Also delete merged custom fields that had no conflicts.')

    def handle(self, *args, **opts):
        from DataTools.onedesign import unify, format_report
        qs = get_tenant_model().objects.exclude(schema_name='public')
        if opts['schema']:
            qs = qs.filter(schema_name=opts['schema'])
        for t in qs.order_by('schema_name'):
            with schema_context(t.schema_name):
                r = unify(apply=opts['apply'], purge_merged=opts['purge_merged'])
                self.stdout.write(format_report(r, t.schema_name))
        if not opts['apply']:
            self.stdout.write('Dry run only. Run again with --apply to make these changes.')
