
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

from django_tenants.signals import post_schema_sync
from django_tenants.utils import schema_context

def create_default_email_templates(sender, tenant, **kwargs):
    from EmpManagement.models import EmailTemplate, DocExpEmailTemplate, DocRequestEmailTemplate, ResignationEmailTemplate
    from calendars.models import LvEmailTemplate,LatinEarlyoutEmailTemplate
    from OrganisationManager.models import AssetEmailTemplate
    from PayrollManagement.models import LoanEmailTemplate, AdvanceSalaryEmailTemplate, AirticketEmailTemplate

    with schema_context(tenant.schema_name):
            # GeneralRequest EmailTemplate
            for t_type in ['request_created', 'request_approved', 'request_rejected']:
                EmailTemplate.objects.get_or_create(
                    template_type=t_type,
                    defaults={
                        'subject': f'General Request - {t_type.replace("_", " ").title()}', 
                        'body': f'Dear {{{{ recipient_name }}}},\n\nYour general request has been {"created" if "created" in t_type else "approved" if "approved" in t_type else "rejected"}.\n\nRegards,\nManagement'
                    }
                )
            
            # DocExpEmailTemplate
            for t_type in ['Employee Notification', 'User Notification']:
                DocExpEmailTemplate.objects.get_or_create(
                    template_name=t_type,
                    defaults={
                        'subject': f'Document Expiry - {t_type}', 
                        'body': f'Dear {{{{ recipient_name }}}},\n\nThis is a notification regarding document expiry.\n\nRegards,\nManagement'
                    }
                )

            # DocRequestEmailTemplate
            for t_type in ['request_created', 'request_approved', 'request_rejected']:
                DocRequestEmailTemplate.objects.get_or_create(
                    template_type=t_type,
                    defaults={
                        'subject': f'Document Request - {t_type.replace("_", " ").title()}', 
                        'body': f'Dear {{{{ recipient_name }}}},\n\nYour document request has been {"created" if "created" in t_type else "approved" if "approved" in t_type else "rejected"}.\n\nRegards,\nManagement'
                    }
                )

            # ResignationEmailTemplate
            for t_type in ['resignation_created', 'resignation_approved', 'resignation_rejected']:
                ResignationEmailTemplate.objects.get_or_create(
                    template_type=t_type,
                    defaults={
                        'subject': f'Resignation Request - {t_type.replace("_", " ").title()}', 
                        'body': f'Dear {{{{ recipient_name }}}},\n\nYour resignation request has been {"created" if "created" in t_type else "approved" if "approved" in t_type else "rejected"}.\n\nRegards,\nManagement'
                    }
                )

            # LvEmailTemplate
            for t_type in ['request_created', 'request_approved', 'request_rejected']:
                LvEmailTemplate.objects.get_or_create(
                    template_type=t_type,
                    defaults={
                        'subject': f'Leave Request - {t_type.replace("_", " ").title()}', 
                        'body': f'Dear {{{{ recipient_name }}}},\n\nYour leave request has been {"created" if "created" in t_type else "approved" if "approved" in t_type else "rejected"}.\n\nRegards,\nManagement'
                    }
                )

            # AssetEmailTemplate
            for t_type in ['asset_created', 'asset_approved', 'asset_rejected']:
                AssetEmailTemplate.objects.get_or_create(
                    template_type=t_type,
                    defaults={
                        'subject': f'Asset Request - {t_type.replace("_", " ").title()}', 
                        'body': f'Dear {{{{ recipient_name }}}},\n\nYour asset request has been {"created" if "created" in t_type else "approved" if "approved" in t_type else "rejected"}.\n\nRegards,\nManagement'
                    }
                )

            # LoanEmailTemplate
            for t_type in ['request_created', 'request_approved', 'request_rejected']:
                LoanEmailTemplate.objects.get_or_create(
                    template_type=t_type,
                    defaults={
                        'subject': f'Loan Request - {t_type.replace("_", " ").title()}', 
                        'body': f'Dear {{{{ recipient_name }}}},\n\nYour loan request has been {"created" if "created" in t_type else "approved" if "approved" in t_type else "rejected"}.\n\nRegards,\nManagement'
                    }
                )

            # AdvanceSalaryEmailTemplate
            for t_type in ['request_created', 'request_approved', 'request_rejected']:
                AdvanceSalaryEmailTemplate.objects.get_or_create(
                    template_type=t_type,
                    defaults={
                        'subject': f'Advance Salary Request - {t_type.replace("_", " ").title()}', 
                        'body': f'Dear {{{{ recipient_name }}}},\n\nYour advance salary request has been {"created" if "created" in t_type else "approved" if "approved" in t_type else "rejected"}.\n\nRegards,\nManagement'
                    }
                )
            #AirticketEmailTemplate
            for t_type in ['request_created', 'request_approved', 'request_rejected']:
                AirticketEmailTemplate.objects.get_or_create(
                    template_type=t_type,
                    defaults={
                        'subject': f'Airticket Request - {t_type.replace("_", " ").title()}', 
                        'body': f'Dear {{{{ recipient_name }}}},\n\nYour airticket request has been {"created" if "created" in t_type else "approved" if "approved" in t_type else "rejected"}.\n\nRegards,\nManagement'
                    }
                )
            #LateinearlyoutEmailTemplate
            for t_type in ['request_created', 'request_approved', 'request_rejected']:
                LatinEarlyoutEmailTemplate.objects.get_or_create(
                    template_type=t_type,
                    defaults={
                        'subject': f'Lateinearlyout Request - {t_type.replace("_", " ").title()}', 
                        'body': f'Dear {{{{ recipient_name }}}},\n\nYour lateinearlyout request has been {"created" if "created" in t_type else "approved" if "approved" in t_type else "rejected"}.\n\nRegards,\nManagement'
                    }
                )
post_save.connect(add_company_to_superusers, sender=company)
post_schema_sync.connect(create_default_email_templates)
