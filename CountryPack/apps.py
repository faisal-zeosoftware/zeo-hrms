from django.apps import AppConfig


class CountryPackConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'CountryPack'
    verbose_name = 'Country packs'

    def ready(self):
        # v1.14.0: a company's country change (company master) clears its resolved pack
        from django.db.models.signals import post_save
        from UserManagement.models import company
        from .services import on_company_saved
        post_save.connect(on_company_saved, sender=company, dispatch_uid='countrypack_company_saved')
