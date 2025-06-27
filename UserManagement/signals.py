
from .models import company

from django.db.models.signals import post_save
from .models import CustomUser






# Signal handler function to update superuser tenants
def add_company_to_superusers(sender, instance, created, **kwargs):
    if created:  # Only process newly created companies
        superusers = CustomUser.objects.filter(is_superuser=True)
        for user in superusers:
            user.tenants.add(instance)
            user.save()

post_save.connect(add_company_to_superusers, sender=company)
