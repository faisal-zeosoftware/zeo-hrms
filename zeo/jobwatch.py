"""v1.73.19 (item 10) – did every scheduled job run? Celery beat sends the jobs of settings.CELERY_BEAT_SCHEDULE to the workers; until
now only the beat itself left a heartbeat, so a job that failed every night (or a task name that no longer exists) went unnoticed.
Every run of a scheduled job is recorded here (start, end, seconds, ok / the error) in the cache, and Settings › System health ›
"Scheduled jobs" lists the jobs that failed, never ran or are late for their own schedule.
"""
import logging
import time

from django.conf import settings

log = logging.getLogger(__name__)
KEY = 'zeo:jobs:runs:' + __import__('hashlib').md5(str(getattr(settings, 'BASE_DIR', '')).encode()).hexdigest()[:8]   # one server's jobs (a shared Redis)
KEEP = 40 * 24 * 3600


def scheduled():
    """{task name: (entry name, schedule)} of the beat schedule"""
    out = {}
    for name, e in (getattr(settings, 'CELERY_BEAT_SCHEDULE', {}) or {}).items():
        if e.get('task'):
            out[e['task']] = (name, e.get('schedule'))
    return out


def runs():
    try:
        from django.core.cache import cache
        return dict(cache.get(KEY) or {})
    except Exception:   # noqa: BLE001
        return {}


def _save(task, **kw):
    try:
        from django.core.cache import cache
        d = dict(cache.get(KEY) or {})
        row = dict(d.get(task) or {})
        row.update(kw)
        d[task] = row
        cache.set(KEY, d, KEEP)
    except Exception:   # noqa: BLE001
        log.debug('job watch not saved', exc_info=True)


_started = {}


def on_prerun(sender=None, task_id=None, task=None, **kw):
    name = getattr(task, 'name', None) or getattr(sender, 'name', None)
    if name in scheduled():
        _started[task_id] = time.time()
        _save(name, started=time.time(), running=True)


def on_postrun(sender=None, task_id=None, task=None, state=None, retval=None, **kw):
    name = getattr(task, 'name', None) or getattr(sender, 'name', None)
    if name not in scheduled():
        return
    t0 = _started.pop(task_id, None)
    ok = state in (None, 'SUCCESS')
    row = {'ended': time.time(), 'running': False, 'secs': round(time.time() - t0, 1) if t0 else None, 'ok': ok}
    if ok:
        row.update(last_ok=time.time(), error='')
    _save(name, **row)


def on_failure(sender=None, task_id=None, exception=None, **kw):
    name = getattr(sender, 'name', None)
    if name in scheduled():
        _save(name, ok=False, failed=time.time(), error=f'{type(exception).__name__}: {exception}'[:300], running=False)


def install():
    from celery.signals import task_failure, task_postrun, task_prerun
    task_prerun.connect(on_prerun, weak=False, dispatch_uid='zeo_jobwatch_pre')
    task_postrun.connect(on_postrun, weak=False, dispatch_uid='zeo_jobwatch_post')
    task_failure.connect(on_failure, weak=False, dispatch_uid='zeo_jobwatch_fail')


def every_seconds(schedule):
    """How often a crontab / timedelta schedule runs (the longest gap), in seconds."""
    from datetime import timedelta
    if isinstance(schedule, timedelta):
        return schedule.total_seconds()
    if isinstance(schedule, (int, float)):
        return float(schedule)
    try:
        mins, hours = schedule.minute, schedule.hour
        dom, dow, moy = schedule.day_of_month, schedule.day_of_week, schedule.month_of_year
        if len(moy) < 12:
            return 366 * 86400
        if len(dom) < 31:
            return 31 * 86400
        if len(dow) < 7:
            return 7 * 86400
        if len(hours) < 24:
            hs = sorted(hours)
            gaps = [b - a for a, b in zip(hs, hs[1:])] + [24 - hs[-1] + hs[0]]
            return max(gaps) * 3600
        ms = sorted(mins)
        gaps = [b - a for a, b in zip(ms, ms[1:])] + [60 - ms[-1] + ms[0]]
        return max(gaps) * 60
    except Exception:   # noqa: BLE001
        return 86400


def status(now=None):
    """[{job, task, every, last_run, last_ok, secs, state: ok | failed | late | never | missing, error}] – missing = the task is not
    registered (a typo or a removed app)."""
    now = now or time.time()
    try:
        from zeo.celery import app
        app.loader.import_default_modules()   # the web server has not imported the apps' tasks modules
        registered = set(app.tasks.keys())
    except Exception:   # noqa: BLE001
        registered = None
    d = runs()
    out = []
    for task, (name, sch) in sorted(scheduled().items(), key=lambda x: x[1][0]):
        every = every_seconds(sch)
        r = d.get(task) or {}
        last = r.get('ended') or r.get('failed') or r.get('started')
        if registered is not None and task not in registered:
            st = 'missing'
        elif r.get('ok') is False:
            st = 'failed'
        elif not last:
            st = 'never'
        elif now - last > every * 2 + 900:
            st = 'late'
        else:
            st = 'ok'
        out.append({'job': name, 'task': task, 'every': every, 'last_run': last, 'last_ok': r.get('last_ok'), 'secs': r.get('secs'),
                    'running': bool(r.get('running')), 'state': st, 'error': r.get('error') or ''})
    return out
