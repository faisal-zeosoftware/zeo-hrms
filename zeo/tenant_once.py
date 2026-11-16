"""v1.67.19 – data migrations of company apps that change the rights of the shared groups (auth_group is shared by every
company) must run once per database, not again for every company made later.

A company app's migrations run in every company schema. A migration that gives rights to "the groups that already have X"
ran again each time a company (or an own database) was made, and gave those rights again to the groups of every company –
for example the delete right on rosters to a group the administrator had made "change, no delete" (field check 1.67.19).

    if not tenant_once.first(schema_editor, 'ShiftPlanner', '0002_rights'):
        return
"""


def first(schema_editor, app, name):
    """True when no other schema has applied the migration yet (the first company that runs it) – in this database and in the
    main one (a company made in an own database writes the shared groups of the main database, ZeoCloud.dbrouting)."""
    conn = schema_editor.connection
    me = getattr(conn, 'schema_name', None) or 'public'
    if _seen(conn, me, app, name):
        return False
    try:
        from django.db import connections
        if 'control' in connections.settings:
            ctl = connections['control']
            if ctl.settings_dict.get('NAME') != conn.settings_dict.get('NAME') and _seen(ctl, None, app, name):
                return False
    except Exception:   # noqa: BLE001
        pass
    return True


def _seen(conn, me, app, name):
    with conn.cursor() as cur:
        cur.execute("SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg\\_%%' AND nspname <> 'information_schema'")
        schemas = [r[0] for r in cur.fetchall() if r[0] != me]
        for s in schemas:
            cur.execute('SELECT to_regclass(%s)', [f'"{s}".django_migrations'])
            if cur.fetchone()[0] is None:
                continue
            cur.execute(f'SELECT 1 FROM "{s}".django_migrations WHERE app = %s AND name = %s LIMIT 1', [app, name])
            if cur.fetchone():
                return True
    return False
