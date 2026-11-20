# your_project/celery.py
from __future__ import absolute_import, unicode_literals
import os
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'zeo.settings')

app = Celery('zeo')

# Load task modules from all registered Django app configs.
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

try:   # v1.73.19 (item 10): every run of a scheduled job is recorded (Settings › System health › Scheduled jobs)
    from zeo import jobwatch as _jobwatch
    _jobwatch.install()
except Exception:   # noqa: BLE001
    pass

@app.task(bind=True)
def debug_task(self):
    print(f'Request: {self.request!r}')

