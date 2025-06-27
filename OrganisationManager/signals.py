from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import cmpny_mastr,brnch_mstr

@receiver(post_save, sender=cmpny_mastr)
def create_branch(sender, instance, created, **kwargs):
    if created:
        brnch_mstr.objects.create(
            branch_name=f"{instance.cmpny_name} Branch",
            br_state_id=instance.cmpny_state_id,
            br_country=instance.cmpny_country,
            br_city=instance.cmpny_city,
            br_company_id=instance,
            br_created_by=instance.cmpny_created_by,
            br_updated_by=instance.cmpny_updated_by,
            br_branch_mail=instance.cmpny_mail,
            br_branch_nmbr_1=instance.cmpny_nmbr_1,
            # Add other fields as needed
        )