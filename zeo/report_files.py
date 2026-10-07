"""
Where saved / standard report files go (v1.7.0).

The report screens used to write every file straight into MEDIA_ROOT by its name ("std_report.json"), so
  * the employee report and the asset report overwrote each other's standard report (same name);
  * every company wrote to the same files, so one company's report could be shown to another company;
  * a report name containing "../" could write outside the media folder.
Files now go to MEDIA_ROOT/reports/<company>/<report kind>/<name>.json.
"""
import os
import re

from django.conf import settings
from django.db import connection


def _safe(s):
    return re.sub(r'[^A-Za-z0-9_.-]+', '_', str(s or '')).strip('._')[:120] or 'report'


def report_rel(file_name, kind):
    """Path stored in the report's FileField (relative to MEDIA_ROOT)."""
    return f"reports/{_safe(connection.schema_name)}/{_safe(kind)}/{_safe(file_name)}.json"


def report_file(file_name, kind):
    """Absolute path to write the report to (folders are created)."""
    path = os.path.join(settings.MEDIA_ROOT, report_rel(file_name, kind))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path
