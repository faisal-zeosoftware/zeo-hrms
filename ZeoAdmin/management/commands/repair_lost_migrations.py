"""v1.67.31 – python manage.py repair_lost_migrations [--schema X] [--check]

Repair for companies created before 1.67.31. While a new company schema was being made, the webhook handler (ZeoAdmin) also fired
on the row that records a migration as applied. The webhook table did not exist yet, the failed query left the migration's
transaction aborted, and Postgres rolled the whole migration back at COMMIT without an error. Which migrations were lost depended
on timing (the handler looks up the webhooks once a minute): ZeoMasters 0007_accounting_ready (partner credit / balance columns,
"column ZeoMasters_partner.credit_hold does not exist"), RecruitmentManagement 0003_talent_pool_rights and others.

`migrate_schemas` cannot bring them back: the later migrations of the app are recorded, so Django sees nothing to do.

This command finds, in every company schema, the migrations that are not recorded although a migration after them is, and applies
them in dependency order (they were rolled back completely, so applying them again is safe). Companies without such a gap are not
touched. --check only lists them. Safe to run again."""
import importlib

from django.core.management.base import BaseCommand
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django_tenants.utils import get_tenant_model, schema_context

# data steps that ran while a lost migration was missing and must run once more after it is applied
FOLLOW_UP = {
    ('ZeoMasters', '0007_accounting_ready'): ('ZeoMasters.migrations.0008_fill_accounting_ready', 'fill'),
}


def gaps(executor):
    """Unapplied migrations that have an applied migration after them, in the order they must run."""
    loader = executor.loader
    graph, applied = loader.graph, loader.applied_migrations
    missing = {n for n in graph.nodes if n not in applied}
    def later(n):   # every migration that depends on n, directly or through others
        seen, todo = set(), [graph.node_map[n]]
        while todo:
            for c in todo.pop().children:
                if c.key not in seen:
                    seen.add(c.key)
                    todo.append(c)
        return seen
    lost = {n for n in missing if any(d in applied for d in later(n))}
    order = []
    for leaf in graph.leaf_nodes():
        for n in graph.forwards_plan(leaf):
            if n in lost and n not in order:
                order.append(n)
    return order


class Command(BaseCommand):
    help = 'Apply migrations that were rolled back unnoticed while a company was created (fixed in 1.67.31).'

    def add_arguments(self, p):
        p.add_argument('--schema', help='Only this company schema')
        p.add_argument('--check', action='store_true', help='Only list the companies and migrations')

    def handle(self, *a, **o):
        ts = get_tenant_model().objects.exclude(schema_name='public')
        if o.get('schema'):
            ts = ts.filter(schema_name=o['schema'])
        total = 0
        for t in ts.order_by('schema_name'):
            with schema_context(t.schema_name):
                ex = MigrationExecutor(connection)
                todo = gaps(ex)
                if not todo:
                    continue
                total += len(todo)
                names = ', '.join(f'{app}.{name}' for app, name in todo)
                if o.get('check'):
                    self.stdout.write(f'{t.schema_name}: {len(todo)} to repair – {names}')
                    continue
                for node in todo:
                    m = ex.loader.get_migration(*node)
                    state = ex.loader.project_state(node, at_end=False)
                    ex.apply_migration(state, m)
                    self.stdout.write(f'{t.schema_name}: applied {node[0]}.{node[1]}')
                    fu = FOLLOW_UP.get(node)
                    if fu:
                        try:
                            from django.apps import apps as _apps
                            getattr(importlib.import_module(fu[0]), fu[1])(_apps, None)
                        except Exception as e:   # noqa: BLE001
                            self.stderr.write(f'{t.schema_name}: follow-up of {node[1]} skipped ({e})')
                    ex = MigrationExecutor(connection)   # reload: the next migration sees this one as applied
                self.stdout.write(self.style.SUCCESS(f'{t.schema_name}: repaired ({len(todo)})'))
        if not total:
            self.stdout.write('Nothing to repair.')
