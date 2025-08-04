from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils.timezone import now
from .models import EmployeeResignation, EndOfService
from django.db import transaction


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
from .utils import calculate_settlement  # Ensure your gratuity calculation logic is here

@receiver(post_save, sender=EmployeeResignation)
def create_eos_on_approval(sender, instance, created, **kwargs):
    if instance.status == 'Approved':
        # Check if EOS already exists
        if not hasattr(instance, 'eos'):
            try:
                employee = instance.employee
                start_date = employee.emp_joined_date
                end_date = instance.last_working_date

                if not start_date or not end_date:
                    return

                total_days = (end_date - start_date).days
                years_of_service = total_days / 365.0

                with transaction.atomic():
                    eos = EndOfService.objects.create(
                        resignation=instance,
                        date_of_joining=start_date,
                        date_of_resignation_termination=instance.resigned_on,
                        last_working_date=end_date,
                        years_of_service=years_of_service,
                        total_service_days=total_days,
                    )
                    calculate_settlement(eos)

            except Exception as e:
                # Optional: log error
                print(f"Error creating EOS: {e}")