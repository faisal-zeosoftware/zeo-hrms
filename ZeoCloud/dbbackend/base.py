"""v1.24.0 – django_tenants backend that follows the company to its database.

set_tenant / set_schema only record which database is wanted; the switch happens when the next cursor is opened
(close, change NAME / HOST …, connect). With ZEO_CLOUD off, or while no company lives in an own database, it behaves
exactly like django_tenants.postgresql_backend.
"""
from django.conf import settings
from django.db.utils import ProgrammingError

from django_tenants.postgresql_backend.base import DatabaseWrapper as TenantWrapper

CONTROL = 'control'


class DatabaseWrapper(TenantWrapper):

    def __init__(self, settings_dict, *args, **kwargs):
        # own copy: Django gives every thread's connection the same dict, and the switch changes NAME / HOST …
        settings_dict = dict(settings_dict)
        self._zeo_db = CONTROL      # database the open connection points to
        self._zeo_want = CONTROL    # database the chosen company lives in
        self._zeo_pin = None        # dbrouting.pin(): this database for the public schema too (migrations, copies)
        super().__init__(settings_dict, *args, **kwargs)
        self._zeo_main = {k: self.settings_dict.get(k) for k in ('NAME', 'HOST', 'PORT', 'USER', 'PASSWORD')}

    # ------------------------------------------------------------ choosing
    def _zeo_target(self, schema_name):
        if self._zeo_pin:
            return self._zeo_pin
        if not getattr(settings, 'ZEO_CLOUD', False) or self.alias != 'default':
            return CONTROL
        if not schema_name or schema_name == 'public':
            return CONTROL
        from ZeoCloud import dbrouting
        return dbrouting.db_of_schema(schema_name) or CONTROL

    def set_tenant(self, tenant, include_public=True):
        self._zeo_want = self._zeo_target(getattr(tenant, 'schema_name', None))
        return super().set_tenant(tenant, include_public)

    # set_schema and set_schema_to_public go through set_tenant (FakeTenant)

    # ------------------------------------------------------------ switching
    def _zeo_switch(self):
        want = self._zeo_want
        if want == self._zeo_db:
            return
        if self.connection is not None:
            if self.in_atomic_block:
                raise ProgrammingError(f'Company data is in database "{want}" but a transaction is open on "{self._zeo_db}" '
                                       '– open the company outside the transaction.')
            self.close()
        if want == CONTROL:
            params = self._zeo_main
        else:
            from ZeoCloud import dbrouting
            params = dbrouting.params_of(want, self._zeo_main)
            if params is None:
                raise ProgrammingError(f'Database "{want}" is not registered.')
        self.settings_dict.update(params)
        self._zeo_db = want

    def ensure_connection(self):
        self._zeo_switch()
        return super().ensure_connection()

    def _cursor(self, name=None):
        self._zeo_switch()
        return super()._cursor(name)

    @property
    def zeo_database(self):
        """Where the next query goes: 'control' or an own database key."""
        return self._zeo_want
