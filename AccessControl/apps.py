from django.apps import AppConfig


class AccesscontrolConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'AccessControl'
    verbose_name = 'Company, branch and role access'

    def ready(self):
        from .access import install
        install()
        from .approvals import install_steps   # v1.73.19 (item 5)
        install_steps()
        install_record_names()   # v1.73.21


def _numbered_str(self):
    """v1.73.21 a document without its own name shows its number (and title): "PO-00014 – Paint for unit 204", not
    "PurchaseOrder object (14)" in Home, the Inbox, approvals and record history."""
    num = getattr(self, 'number', '') or ''
    for f in ('title', 'subject', 'name', 'description'):
        v = getattr(self, f, None)
        if isinstance(v, str) and v.strip():
            return (f'{num} – {v.strip()}' if num else v.strip())[:120]
    return num or f'{self._meta.verbose_name} {self.pk}'


def install_record_names():
    from django.apps import apps
    from django.db import models
    for m in apps.get_models():
        if m.__str__ is models.Model.__str__ and not m._meta.proxy and any(f.name == 'number' for f in m._meta.fields):
            m.__str__ = _numbered_str
