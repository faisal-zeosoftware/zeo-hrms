from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils.timezone import now
from .models import EmployeeResignation, EndOfService


@receiver(post_save, sender=EmployeeResignation)
def deactivate_employee_on_approval(sender, instance, created, **kwargs):
    if created:
        return  # Only act on updates, not creation

    if instance.status == 'Approved':
        try:
            eos = instance.eos  # Related EndOfService (via OneToOneField or reverse relation)
            if eos.last_working_date <= now().date():
                employee = instance.employee
                employee.is_active = False
                employee.emp_status = False  # Optional
                employee.save()
        except EndOfService.DoesNotExist:
            # EOS hasn't been created yet
            pass
