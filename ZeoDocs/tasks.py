"""v1.70.1 – Document Center daily job: expired documents, approval reminders, the trash emptied after 30 days."""
from zeo.tz import today as _zt_today   # v1.73.16 the user's / company's day, not the server's UTC day
import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone
from django_tenants.utils import get_tenant_model, schema_context

log = logging.getLogger(__name__)
TRASH_DAYS = 30


def daily_company(today=None):
    from . import models as Mo
    from . import services as S
    today = today or _zt_today()
    out = {'expired': 0, 'reminded': 0, 'purged': 0}
    # documents past their expiry date (published or approved ones) become Expired
    out['expired'] = Mo.Document.objects.filter(deleted_at__isnull=True, expiry_date__lt=today, status__in=('published', 'approved')).update(status='expired')
    # overdue approval steps: the approver gets a to-do again (once a day)
    for a in Mo.Approval.objects.filter(decision='pending', due_date__lt=today, version__document__deleted_at__isnull=True).select_related('version__document'):
        d = a.version.document
        S._todo(a.approver_id, f'{d.number} {d.title} ({a.version.label}) – overdue since {a.due_date:%d %b %Y}', d)
        out['reminded'] += 1
    # the trash is emptied after 30 days (not documents under legal hold)
    cut = timezone.now() - timedelta(days=TRASH_DAYS)
    held = set()
    try:
        from ZeoAdmin.legalhold import held_ids
        held = set(held_ids())
    except Exception:   # noqa: BLE001
        pass
    for d in Mo.Document.objects.filter(deleted_at__lt=cut):
        if d.legal_hold or (d.employee_id and d.employee_id in held):   # v1.73.6 the document's own legal hold too
            continue
        for v in d.versions.all():
            try:
                v.file.delete(save=False)
            except Exception:   # noqa: BLE001
                pass
        Mo.Document.objects.filter(pk=d.pk).update(current=None, published=None)
        d.delete()
        out['purged'] += 1
    for f in Mo.DocFolder.objects.filter(deleted_at__lt=cut, kind='user'):
        if not Mo.Document.objects.filter(folder__in=[x.pk for x in S.descendants(f)]).exists():
            f.delete()
    from .lifecycle import daily   # v1.73.6 expiry alerts, escalation, retention review, correspondence deadlines
    out.update(daily(today))
    try:   # v1.73.10 scans not read yet (big files, documents of before 1.73.10): up to 200 a day
        from .phase5 import run_pending
        out['ocr'] = run_pending(200)
    except Exception:   # noqa: BLE001
        log.exception('dms ocr')
    return out


@shared_task
def dms_daily():
    try:
        from ZeoCloud.lifecycle import off_schemas
        off = list(off_schemas())
    except Exception:   # noqa: BLE001
        off = []
    for t in get_tenant_model().objects.exclude(schema_name__in=list(off) + ['public']):
        try:
            with schema_context(t.schema_name):
                daily_company()
        except Exception:   # noqa: BLE001
            log.exception('dms daily %s', t.schema_name)
