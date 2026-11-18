from django.apps import AppConfig


class ZeoDeveloperConfig(AppConfig):
    """ZEO Developer 1.74.0 – formulas, safe Python scripts, safe actions and module packages (Settings › Developer)."""
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ZeoDeveloper'
    verbose_name = 'ZEO Developer'

    def ready(self):
        from django.db.models.signals import post_save
        from .appdata import on_save
        post_save.connect(on_save, dispatch_uid='zeodeveloper-app-triggers')   # scripts on records of every app (trigger app_create / app_update)
        from django.db.models.signals import post_delete
        from .appdata import on_script_change
        from .models import Script, DeveloperSettings
        for m in (Script, DeveloperSettings):   # v1.70.3: the trigger lists of all workers follow a change at once
            post_save.connect(on_script_change, sender=m, dispatch_uid=f'zeodeveloper-trig-{m.__name__}')
            post_delete.connect(on_script_change, sender=m, dispatch_uid=f'zeodeveloper-trigdel-{m.__name__}')
