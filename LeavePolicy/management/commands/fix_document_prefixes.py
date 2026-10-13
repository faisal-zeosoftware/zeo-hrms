"""v1.10.0 – one-off: give every branch its own document prefix per document type.

Older versions created every branch with the same prefixes (BR-LEA, BR-LOA …), so two branches produced the
same document numbers and saving failed. The first branch keeps its prefix; the others get one from their
branch code (e.g. BR-DXB → DXB-LEA). Run once per server:  python manage.py fix_document_prefixes [--dry-run]
"""
from django.core.management.base import BaseCommand
from django_tenants.utils import get_tenant_model, schema_context


class Command(BaseCommand):
    help = 'Give every branch its own document number prefix for each document type (all companies).'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Only show what would change')
        parser.add_argument('--schema', help='Only this company schema')
        parser.add_argument('--add-missing', action='store_true',
                            help='v1.22.1: also give every branch a numbering row for each document type it does not have yet')

    def handle(self, *args, **o):
        from OrganisationManager.models import DocumentNumbering
        from OrganisationManager.signals import branch_doc_prefix
        tenants = get_tenant_model().objects.exclude(schema_name='public')
        if o.get('schema'):
            tenants = tenants.filter(schema_name=o['schema'])
        total = 0
        for t in tenants:
            with schema_context(t.schema_name):
                seen = {}
                for d in DocumentNumbering.objects.select_related('branch_id').order_by('type', 'id'):
                    key = (d.type, (d.prefix or '').upper(), (d.suffix or '').upper())
                    owner = seen.setdefault(key, d.branch_id_id)
                    if owner == d.branch_id_id:
                        continue
                    new = branch_doc_prefix(d.branch_id, d.type)
                    total += 1
                    self.stdout.write(f'{t.schema_name}: {d.branch_id} {d.type}: {d.prefix} -> {new}')
                    if not o['dry_run']:
                        DocumentNumbering.objects.filter(pk=d.pk).update(prefix=new)
                        seen[(d.type, new.upper(), (d.suffix or '').upper())] = d.branch_id_id
        self.stdout.write(self.style.SUCCESS(f'{total} prefix(es) {"would change" if o["dry_run"] else "changed"}.'))
        if o.get('add_missing'):
            self.add_missing(tenants, o['dry_run'])

    def add_missing(self, tenants, dry):
        """v1.22.1: rows for the document types a branch has no numbering for (e.g. the types added in 1.22.1)."""
        from datetime import timedelta
        from django.utils import timezone
        from OrganisationManager.models import DocumentNumbering, brnch_mstr
        from OrganisationManager.signals import branch_doc_prefix
        made = 0
        for t in tenants:
            with schema_context(t.schema_name):
                for b in brnch_mstr.objects.all():
                    have = set(DocumentNumbering.objects.filter(branch_id=b).values_list('type', flat=True))
                    for doc_type, label in DocumentNumbering.DOCUMENT_TYPES:
                        if doc_type in have:
                            continue
                        prefix = branch_doc_prefix(b, doc_type)
                        made += 1
                        self.stdout.write(f'{t.schema_name}: {b} {label}: {prefix}')
                        if not dry:
                            DocumentNumbering.objects.create(branch_id=b, type=doc_type, prefix=prefix, suffix='', current_number=0,
                                                             total_length=12, start_date=timezone.now().date(),
                                                             end_date=timezone.now().date() + timedelta(days=365))
        self.stdout.write(self.style.SUCCESS(f'{made} numbering row(s) {"would be added" if dry else "added"}.'))
