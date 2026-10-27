"""v1.62.0 – log and history cleaning (README-UPDATE 83.8; proposal "History cleaning and security", section 2).

Three kinds of data, three rules – there is no "delete history" button:
  A. system noise – read inbox items, read notifications, WhatsApp / SMS / push logs, webhook deliveries, automation run log,
     import history (Undo is then no longer possible), recently viewed (last 30 per user), staging and temporary files:
     cleaned after a number of days (the company can change them, or keep for ever);
  B. audit and change history – change history and audit log (RecordLog), approval / configuration audit (FlowAudit), the
     "view as" log: kept at least 1 year (default 5 years); the rows are written to a zip archive first (JSON lines, one file per
     category), then removed; the archive's name is in the audit line and on the page;
  C. business records – payroll, payslips, settlements, leave ledger, attendance, approved requests, documents, notes and
     files on records: never cleaned by date (employee anonymisation of 1.53.0 handles people who left).

Every cleaning starts with a preview (the rows and MB that would go); "Clean now" needs the company name typed; the nightly job
does the same at about 02:00 Gulf time when the company switched it on. Rows go in batches of 5,000 with a time limit, so
screens are never blocked – what is left goes the next night. Legal holds keep history out of every cleaning: an employee on
hold (and everything linked to them), a held case, records of an open disciplinary case, and – a hold on the whole company – all
of kind B. Each cleaning is written to the audit log for good ("Removed 12,340 read inbox items older than 180 days – nightly job").
"""
import io
import json
import logging
import os
import time
import zipfile
from datetime import timedelta

from django.apps import apps
from django.conf import settings
from django.db import connection, transaction
from django.db.models import Max, Min, Q
from django.utils import timezone

log = logging.getLogger(__name__)
BATCH = 5000
NIGHT_SECONDS = int(os.environ.get('ZEO_HISTORY_NIGHT_SECONDS', '1200'))
B_MIN_DAYS = 365

# key: (kind, label, default days (0 = for ever), fixed?, sources)
# source: (model label, date field, filter Q builder or None, has model/object_id for legal holds, public-schema model)
CATEGORIES = [
    ('inbox', 'A', 'Inbox items – read or done', 180, False,
     [('Chatter.InboxItem', 'created_at', lambda: Q(is_read=True) | Q(done=True), True, False)]),
    ('notifications', 'A', 'Bell notifications – read', 90, False, 'NOTIFICATIONS'),
    ('messages', 'A', 'WhatsApp, SMS and push log', 90, False,
     [('Chatter.MessageLog', 'at', None, False, False), ('ZeoMobile.PushLog', 'created_at', None, False, True)]),
    ('webhooks_ok', 'A', 'Webhook deliveries – successful', 30, False,
     [('ZeoAdmin.WebhookDelivery', 'at', lambda: Q(status='sent'), False, False)]),
    ('webhooks_failed', 'A', 'Webhook deliveries – failed', 90, False,
     [('ZeoAdmin.WebhookDelivery', 'at', lambda: ~Q(status='sent') & ~Q(status='queued'), False, False)]),
    ('automation', 'A', 'Automation execution log', 180, False,
     [('ZeoFlow.WorkflowRun', 'started_at', lambda: ~Q(status='running'), True, False)]),
    ('imports', 'A', 'Import history (Undo no longer possible)', 180, False,
     [('ZeoAdmin.ImportBatch', 'at', None, False, False)]),
    ('exports', 'A', 'Export log', 365, False, [('ZeoAdmin.ExportLog', 'at', None, False, False)]),
    ('recent', 'A', 'Recently viewed (last 30 per user)', 0, True, 'RECENT'),
    ('files', 'A', 'Staging and temporary files (older than 2 days)', 2, True, 'FILES'),
    ('record_log', 'B', 'Change history and audit log', 1825, False,
     [('Chatter.RecordLog', 'at', lambda: ~Q(action='login_as'), True, False)]),
    ('approval_audit', 'B', 'Approval and configuration audit', 1825, False, [('ZeoFlow.FlowAudit', 'at', None, False, False)]),
    ('view_as', 'B', '"View as" log', 1825, False, [('Chatter.RecordLog', 'at', lambda: Q(action='login_as'), False, False)]),
]
CAT = {c[0]: c for c in CATEGORIES}
NEVER = ['Payroll runs, payslips, WPS files, final settlements, gratuity and provisions',
         'Leave ledger, attendance results, approved requests and their approval trail',
         'Employee documents and identity details (use anonymisation for people who left)',
         'Notes and files on records (they go only with their record)',
         'Records under legal hold, and anything linked to an open disciplinary case']


class Refused(Exception):
    pass


def schema():
    return getattr(connection, 'schema_name', 'public')


def policy():
    from .models import HistoryPolicy
    p = HistoryPolicy.objects.first()
    return p or HistoryPolicy.objects.create()


def is_test_company():
    try:
        from ZeoApps.models import CompanyOption
        return CompanyOption.objects.filter(key__in=('test_company_of', 'neutralized')).exists()
    except Exception:
        return False


def days_for(key, p=None):
    c = CAT[key]
    if c[4]:   # fixed
        return c[3]
    p = p or policy()
    v = (p.days or {}).get(key)
    return c[3] if v is None else int(v)


def check_days(key, v, test=False):
    from Translations.i18n import tr
    c = CAT.get(key)
    if c is None:
        raise Refused(tr('Unknown category {0}.', key))
    if c[4]:
        raise Refused(tr('"{0}" has a fixed period.', tr(c[2])))
    v = int(v)
    if v < 0:
        raise Refused(tr('Days cannot be below 0.'))
    if v == 0:
        return 0   # keep for ever
    if c[1] == 'B' and v < B_MIN_DAYS and not test:
        raise Refused(tr('"{0}" is audit history – at least {1} days (1 year).', tr(c[2]), B_MIN_DAYS))
    if c[1] == 'A' and v < 7 and not test:
        raise Refused(tr('"{0}": at least 7 days.', tr(c[2])))
    return v


def _model(label):
    try:
        return apps.get_model(label)
    except LookupError:
        return None


def _sources(key):
    c = CAT[key]
    src = c[5]
    if src == 'NOTIFICATIONS':
        out = [('UserManagement.UserNotificationInbox', 'created_at', lambda: Q(is_read=True), False, True),
               ('ZeoFlow.FlowNotification', 'created_at', lambda: Q(is_read=True), True, False)]
        for m in apps.get_models():
            n = m.__name__
            if m._meta.app_label in ('EmpManagement', 'calendars', 'PayrollManagement', 'OrganisationManager') and n.endswith('Notification'):
                fields = {f.name for f in m._meta.get_fields()}
                if {'is_read', 'created_at'} <= fields:
                    out.append((m._meta.label, 'created_at', lambda: Q(is_read=True), False, False))
        return out
    return src if isinstance(src, list) else []


# ------------------------------------------------------------------ legal holds
def holds():
    """{'company': bool, 'employees': set(ids), 'cases': set((model key, id))}"""
    from .models import LegalHold
    out = {'company': False, 'employees': set(), 'cases': set()}
    for h in LegalHold.objects.filter(released_at__isnull=True):
        sc = getattr(h, 'scope', 'employee') or 'employee'
        if sc == 'company':
            out['company'] = True
        elif sc == 'case':
            lab = (h.case_model or '')
            if '.' in lab:
                a, m = lab.split('.', 1)
                out['cases'].add((f'{a}.{m.lower()}', str(h.case_id)))
            if h.employee_id:
                out['employees'].add(h.employee_id)
        elif h.employee_id:
            out['employees'].add(h.employee_id)
    try:   # an open disciplinary case keeps the employee's history
        DC = apps.get_model('ZeoHR', 'DisciplinaryCase')
        for pk, emp in DC.objects.exclude(status__in=('closed', 'withdrawn')).values_list('pk', 'employee_id'):
            out['cases'].add(('ZeoHR.disciplinarycase', str(pk)))
            if emp:
                out['employees'].add(emp)
    except Exception:
        pass
    return out


def _held_keys(qs, hold):
    """(model key, object id) pairs among the rows of qs that belong to held employees / cases."""
    if not hold['employees'] and not hold['cases']:
        return set()
    keys = set(hold['cases'])
    if not hold['employees']:
        return keys
    from AccessControl.access import emp_path
    emps = list(hold['employees'])
    for mkey in qs.values_list('model', flat=True).distinct()[:500]:
        if not mkey or '.' not in mkey:
            continue
        app, mname = mkey.split('.', 1)
        try:
            m = apps.get_model(app, mname)
        except LookupError:
            continue
        ep = emp_path(m)
        if ep is None:
            continue
        try:
            ids = m._default_manager.filter(**({'pk__in': emps} if ep == '' else {f'{ep}__in': emps})).values_list('pk', flat=True)[:100000]
            keys.update((mkey, str(i)) for i in ids)
        except Exception:
            log.info('held keys for %s', mkey, exc_info=True)
    return keys


def _exclude_held(qs, keys):
    if not keys:
        return qs
    by_model = {}
    for mk, oid in keys:
        by_model.setdefault(mk, []).append(oid)
    q = Q()
    for mk, ids in by_model.items():
        q |= Q(model=mk, object_id__in=ids)
    return qs.exclude(q)


# ------------------------------------------------------------------ the rows of a category
def _qs(src, cutoff, hold=None):
    label, dfield, flt, has_obj, public = src
    m = _model(label)
    if m is None:
        return None
    qs = m._default_manager.all()
    if public:
        fields = {f.name for f in m._meta.get_fields()}
        sch = schema()
        if 'schema_name' in fields:
            qs = qs.filter(schema_name=sch)
        elif 'schema' in fields:
            qs = qs.filter(schema=sch)
        else:
            return None
    if cutoff is not None:
        qs = qs.filter(**{f'{dfield}__lt': cutoff})
    if flt is not None:
        qs = qs.filter(flt())
    kept = 0
    if has_obj and hold is not None:
        keys = _held_keys(qs, hold)
        if keys:
            before = qs.count()
            qs = _exclude_held(qs, keys)
            kept = before - qs.count()
    return qs, kept


def _table_size(m):
    try:
        with transaction.atomic(), connection.cursor() as cur:   # the search path finds the company's table, else the shared one
            cur.execute('SELECT pg_total_relation_size(%s::regclass)', [f'"{m._meta.db_table}"'])
            return cur.fetchone()[0] or 0
    except Exception:
        return 0


def _est_bytes(m, rows):
    if not rows:
        return 0
    try:
        total = m._default_manager.count()
        return int(_table_size(m) * rows / total) if total else 0
    except Exception:
        return 0


def _recent_extra():
    """RecentRecord rows beyond the newest 30 of each user."""
    RR = _model('Chatter.RecentRecord')
    ids = []
    for uid in RR.objects.values_list('user_id', flat=True).distinct():
        keep = list(RR.objects.filter(user_id=uid).order_by('-at').values_list('pk', flat=True)[:30])
        ids += list(RR.objects.filter(user_id=uid).exclude(pk__in=keep).values_list('pk', flat=True))
    return RR.objects.filter(pk__in=ids)


def _file_dirs():
    base = os.path.join(str(settings.BASE_DIR))
    out = []
    st = os.environ.get('ZEO_STAGING_ROOT') or os.path.join(base, 'staging')
    out.append(st)
    out.append(os.path.join(str(settings.MEDIA_ROOT), 'tmp'))
    return [d for d in out if os.path.isdir(d)]


def _old_files(days):
    cut = time.time() - days * 86400
    found = []
    for d in _file_dirs():
        for root, _dirs, files in os.walk(d):
            for f in files:
                p = os.path.join(root, f)
                try:
                    stt = os.stat(p)
                except OSError:
                    continue
                if stt.st_mtime < cut:
                    found.append((p, stt.st_size))
    return found


def measure(key, p=None, hold=None, test=False):
    """{rows, bytes, oldest, kept_by_hold, total_rows, total_bytes} of what would be removed now."""
    c = CAT[key]
    days = days_for(key, p)
    out = {'key': key, 'kind': c[1], 'label': c[2], 'days': days, 'default_days': c[3], 'fixed': c[4], 'rows': 0, 'bytes': 0, 'oldest': None,
           'kept_by_hold': 0, 'total_rows': 0, 'total_bytes': 0}
    src = c[5]
    if src == 'FILES':   # server-wide: shown to every company, cleaned once a night by the server job
        out['server'] = True
        old = _old_files(days)
        out['rows'], out['bytes'] = len(old), sum(s for _, s in old)
        allf = _old_files(0)
        out['total_rows'], out['total_bytes'] = len(allf), sum(s for _, s in allf)
        return out
    if src == 'RECENT':
        RR = _model('Chatter.RecentRecord')
        if RR is None:
            return out
        extra = _recent_extra()
        out['rows'] = extra.count()
        out['bytes'] = _est_bytes(RR, out['rows'])
        out['total_rows'] = RR.objects.count()
        out['total_bytes'] = _table_size(RR)
        return out
    hold = hold if hold is not None else holds()
    if c[1] == 'B' and hold['company']:
        out['company_hold'] = True
    cutoff = timezone.now() - timedelta(days=days) if days else None
    for s in _sources(key):
        m = _model(s[0])
        if m is None:
            continue
        try:
            with transaction.atomic():
                base = _qs(s, None, None)
                if base is None:
                    continue
                allq, _ = base
                out['total_rows'] += allq.count()
                out['total_bytes'] += _est_bytes(m, allq.count()) if s[2] is not None or s[4] else _table_size(m)
                o = allq.aggregate(o=Min(s[1]))['o']
                if o and (out['oldest'] is None or o < out['oldest']):
                    out['oldest'] = o
                if not days or (c[1] == 'B' and hold['company']):
                    continue
                res = _qs(s, cutoff, hold)
                if res is None:
                    continue
                qs, kept = res
                n = qs.count()
                out['rows'] += n
                out['kept_by_hold'] += kept
                out['bytes'] += _est_bytes(m, n)
        except Exception:
            log.info('history measure %s %s', key, s[0], exc_info=True)
    return out


def overview():
    p = policy()
    hold = holds()
    rows = [measure(k, p, hold) for k, *_ in CATEGORIES]
    return {'categories': rows, 'nightly': p.nightly, 'holds': {'company': hold['company'], 'employees': len(hold['employees']),
                                                                 'cases': len(hold['cases'])},
            'test_company': is_test_company(), 'never': NEVER, 'b_min_days': B_MIN_DAYS}


# ------------------------------------------------------------------ cleaning
def _archive_dir():
    base = os.environ.get('ZEO_BACKUP_DIR') or getattr(settings, 'ZEO_BACKUP_DIR', '') or os.path.join(str(settings.BASE_DIR), 'backups')
    d = os.path.join(base, 'history-archive', schema())
    os.makedirs(d, exist_ok=True)
    return d


def _jsonable(v):
    if hasattr(v, 'isoformat'):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    return v if isinstance(v, (str, int, float, bool, type(None), list, dict)) else str(v)


def _delete_batches(qs, deadline, archive=None, arcname=''):
    """Delete qs in batches of BATCH (each batch archived first). (removed, finished)."""
    removed = 0
    m = qs.model
    while True:
        if time.time() > deadline:
            return removed, False
        ids = list(qs.order_by('pk').values_list('pk', flat=True)[:BATCH])
        if not ids:
            return removed, True
        with transaction.atomic():
            if archive is not None:
                buf = io.StringIO()
                for row in m._default_manager.filter(pk__in=ids).values():
                    buf.write(json.dumps({k: _jsonable(v) for k, v in row.items()}, ensure_ascii=False) + '\n')
                archive.writestr(f'{arcname}-{ids[0]}-{ids[-1]}.jsonl', buf.getvalue())
            n, _ = m._default_manager.filter(pk__in=ids).delete()
            removed += len(ids)


def clean(keys, how='manual', by=None, seconds=None, test=False):
    """Remove what is due in the chosen categories. Returns the HistoryRun."""
    from .models import HistoryRun
    t0 = time.time()
    deadline = t0 + (seconds or (NIGHT_SECONDS if how == 'nightly' else 600))
    p = policy()
    hold = holds()
    counts, finished = {}, True
    archive_path, arc_rows, zf = '', 0, None
    need_archive = any(CAT[k][1] == 'B' for k in keys if k in CAT)
    if need_archive and not hold['company']:
        archive_path = os.path.join(_archive_dir(), f'history-{timezone.localtime():%Y%m%d-%H%M%S}.zip')
        zf = zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_DEFLATED)
    try:
        for key in keys:
            if key not in CAT:
                continue
            c = CAT[key]
            days = days_for(key, p)
            res = {'rows': 0, 'bytes': 0, 'kept_by_hold': 0, 'days': days}
            if not days:
                counts[key] = res
                continue
            if c[5] == 'FILES':
                if how != 'server':   # the staging folder is shared by every company: only the server's nightly job empties it
                    counts[key] = dict(res, server=True)
                    continue
                for path, size in _old_files(days):
                    try:
                        os.remove(path)
                        res['rows'] += 1
                        res['bytes'] += size
                    except OSError:
                        pass
                counts[key] = res
                continue
            if c[5] == 'RECENT':
                qs = _recent_extra()
                RR = _model('Chatter.RecentRecord')
                n = qs.count()
                res['bytes'] = _est_bytes(RR, n)
                res['rows'], done = _delete_batches(qs, deadline)
                finished = finished and done
                counts[key] = res
                continue
            if c[1] == 'B' and hold['company']:
                res['company_hold'] = True
                counts[key] = res
                continue
            cutoff = timezone.now() - timedelta(days=days)
            for s in _sources(key):
                m = _model(s[0])
                if m is None:
                    continue
                try:
                    r = _qs(s, cutoff, hold)
                except Exception:
                    log.info('history source %s', s[0], exc_info=True)
                    continue
                if r is None:
                    continue
                qs, kept = r
                n = qs.count()
                res['kept_by_hold'] += kept
                res['bytes'] += _est_bytes(m, n)
                got, done = _delete_batches(qs, deadline, zf if c[1] == 'B' else None, f'{key}-{s[0]}')
                res['rows'] += got
                if c[1] == 'B':
                    arc_rows += got
                finished = finished and done
            counts[key] = res
    finally:
        if zf is not None:
            zf.writestr('README.txt', f'ZEO history archive – company {schema()} – {timezone.localtime():%d %b %Y %H:%M}\n'
                                      'One JSON object per line; file names: <category>-<table>-<first id>-<last id>.jsonl\n')
            zf.close()
            if not arc_rows:
                try:
                    os.remove(archive_path)
                except OSError:
                    pass
                archive_path = ''
    run = HistoryRun.objects.create(how=how, categories=list(keys), counts=counts, archive=archive_path, archive_rows=arc_rows, finished=finished,
                                    by=by, seconds=round(time.time() - t0, 2))
    _audit(run)
    total = sum(v.get('rows', 0) for v in counts.values())
    if total and how != 'user':
        try:
            from ZeoCloud.security import event
            event('history_cleaned', user=by, by=by, schema=schema(), detail=_sentence(run), notify=how == 'manual')
        except Exception:
            pass
    return run


def _sentence(run):
    parts = []
    for k, v in (run.counts or {}).items():
        if v.get('rows'):
            parts.append(f'{v["rows"]:,} {CAT[k][2].lower()} older than {v.get("days")} days')
    who = {'nightly': 'nightly job', 'manual': 'Clean now', 'user': 'my recently viewed', 'server': 'nightly job (server files)'}.get(run.how, run.how)
    txt = 'Removed ' + ('; '.join(parts) if parts else 'nothing') + f' – {who}'
    if run.archive:
        txt += f' (archive {os.path.basename(run.archive)}, {run.archive_rows:,} rows)'
    if not run.finished:
        txt += ' – stopped at the time limit, the rest goes next time'
    return txt


def _audit(run):
    """A permanent audit-log line (RecordLog, model ZeoAdmin.historyrun – never cleaned by itself: kind B keeps it 1 year+)."""
    try:
        from Chatter.models import RecordLog
        u = run.by
        RecordLog.objects.create(model='ZeoAdmin.historyrun', object_id=str(run.pk), record_label='Log and history cleaning'[:255], action='updated',
                                 changes={'cleaning': {'label': 'Cleaning', 'old': '', 'new': _sentence(run)[:2000]}},
                                 user=u, user_name=((u.get_full_name() or u.username) if u else 'ZEO')[:150])
    except Exception:
        log.exception('history audit line')


def run_json(r):
    return {'id': r.id, 'how': r.how, 'how_text': r.get_how_display(), 'categories': r.categories, 'counts': r.counts, 'archive': os.path.basename(r.archive or ''),
            'archive_rows': r.archive_rows, 'finished': r.finished, 'by': (r.by.get_full_name() or r.by.username) if r.by_id else 'ZEO', 'at': r.at,
            'seconds': r.seconds, 'text': _sentence(r)}


def nightly():
    """The job of one company (the Celery task runs it for every company that switched it on)."""
    p = policy()
    if not p.nightly:
        return 'off'
    keys = [k for k, *_ in CATEGORIES]
    run = clean(keys, 'nightly')
    return _sentence(run)


def clear_my_recent(user):
    RR = _model('Chatter.RecentRecord')
    n = RR.objects.filter(user=user).delete()[0] if RR else 0
    return n


def sizes_for_health():
    """Total rows / MB of each category (System health › storage)."""
    out = []
    for k, *_ in CATEGORIES:
        try:
            m = measure(k)
            out.append({'key': k, 'label': m['label'], 'kind': m['kind'], 'rows': m['total_rows'], 'bytes': m['total_bytes'],
                        'due_rows': m['rows'], 'due_bytes': m['bytes']})
        except Exception:
            log.info('history size %s', k, exc_info=True)
    return out


def clean_server_files(days=2):
    """The staging folder and media/tmp – shared by every company – emptied of files older than 2 days (server nightly job)."""
    n = size = 0
    for path, sz in _old_files(days):
        try:
            os.remove(path)
            n += 1
            size += sz
        except OSError:
            pass
    return {'files': n, 'bytes': size}
