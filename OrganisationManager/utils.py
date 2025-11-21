from django_tenants.utils import connection
from celery import shared_task

@shared_task
def asset_schedule_escalation(approval, level_rule):
    from .tasks import asset_escalate_approval_task
    """
    Schedule a Celery countdown task for automatic escalation.
    """
    total_seconds = (
        (level_rule.escalate_after_days or 0) * 86400 +
        (level_rule.escalate_after_hours or 0) * 3600 +
        (level_rule.escalate_after_minutes or 0) * 60
    )

    if total_seconds > 0 and level_rule.escalate_to:
        schema_name = connection.schema_name
        asset_escalate_approval_task.apply_async((approval.id, schema_name), countdown=total_seconds)
        print(f"🕒 Escalation task scheduled for approval {approval.id} after {total_seconds} seconds.")

