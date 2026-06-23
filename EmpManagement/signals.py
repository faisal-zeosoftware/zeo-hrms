from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils.timezone import now
from .models import EmployeeResignation, EndOfService
from django.db import transaction
from .models import RequestType, ApprovalWorkflow, ApprovalLevel
from django.utils import timezone
from .utils import send_notification_email
from django.core.mail import send_mail
from .models import ApprovalDeligation

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

@receiver(post_save, sender=RequestType)
def create_workflow_and_default_level(sender, instance, created, **kwargs):
    if not created:
        return

    workflow = ApprovalWorkflow.objects.create(
        request_type=instance,
        approval_type='no_approval'
    )

    ApprovalLevel.objects.create(
        workflow=workflow,
        level=1,
        role="Auto Level",
        approver=None
    )




@receiver(post_save, sender=ApprovalDeligation)
def delegation_notification(sender, instance, created, **kwargs):
    if not created:
        return
    delegate_user = instance.deligate_to

    if delegate_user and delegate_user.email:

        subject = "Delegation Assigned"

        message = f"""
    Delegation Assigned
    Hello {delegate_user.get_username() or delegate_user.username},
        You have been assigned a new delegation request.

    DELEGATION DETAILS
    ___________________
    Delegator   : {instance.deligator}
    Delegate To  : {delegate_user}
    Reason       : {instance.reason}
    Start Date   : {instance.start_date}
    End Date     : {instance.end_date}
    Created At   : {instance.created_at}
    
    REQUEST DETAILS
    _________________
    Document Number : {instance.request.document_number}
    Employee        : {instance.request.employee}
    Request Type    : {instance.request.request_type}
    Status          : {instance.request.status}
    
    Please take necessary action.

    """

        send_mail(
                subject,
                message,
                None,  # DEFAULT_FROM_EMAIL
                [delegate_user.email],
                fail_silently=False,
            )

        # IN-APP NOTIFICATION
        send_notification_email(
            user=delegate_user,
            employee=None,
            branch=None,
            title="Delegation Assigned",
            notification_type="delegation",
            message=f"{instance.deligator} has delegated responsibilities to you.",
            template_type="request_created",
            delegate_user=instance.deligator,
        )