"""v1.24.0 – database settings for ZEO Cloud own databases. Imported by the settings files (no Django imports here).

    DATABASES = with_cloud_databases(DATABASES)

- 'default' uses ZeoCloud.dbbackend: when a company is chosen (set_tenant / set_schema / schema_context) the connection
  goes to the database the company lives in – the main database, or the own database of a Premium account.
- 'control' is the main database, always on the public schema. While 'default' is in an own database, the shared
  tables (users, companies, accounts …) are read and written there (ZeoCloud.dbrouting.ControlRouter).
"""


def with_cloud_databases(databases):
    d = dict(databases)
    main = dict(d['default'])
    main['ENGINE'] = 'ZeoCloud.dbbackend'
    d['default'] = main
    ctrl = dict(main, ENGINE='django_tenants.postgresql_backend')
    ctrl['TEST'] = {'MIRROR': 'default'}
    d['control'] = ctrl
    return d


ROUTERS = ('ZeoCloud.dbrouting.ControlRouter', 'django_tenants.routers.TenantSyncRouter')
