from django.apps import AppConfig


class ZeoMastersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ZeoMasters'
    verbose_name = 'ZEO shared masters'

    def ready(self):
        # v1.41.0: business partners – app records ↔ partner sync (signals); the forms are attached by each app module
        from django.db.models.signals import post_save
        from . import extensions  # noqa: F401
        from .models import Partner
        from .partners import partner_saved
        post_save.connect(partner_saved, sender=Partner, dispatch_uid='mst_partner_saved')
        from django.apps import apps as _apps
        from .partners import employee_saved   # v1.42.0
        post_save.connect(employee_saved, sender=_apps.get_model('EmpManagement', 'emp_master'), dispatch_uid='mst_employee_saved')
        from . import item_exts  # noqa: F401  v1.43.0 item master
        from .items import item_saved
        post_save.connect(item_saved, sender=_apps.get_model('ZeoMasters', 'Product'), dispatch_uid='mst_item_saved')
