"""v1.62.0 workbook – the data workbook check / load runs in a Celery worker when one answers (DataTools/workbook_jobs.py)."""
from celery import shared_task


@shared_task(ignore_result=True, acks_late=False, time_limit=6 * 3600, soft_time_limit=None)
def run_workbook(schema, run_id):
    from .workbook_jobs import execute
    execute(schema, run_id)
