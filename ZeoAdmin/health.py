"""v1.52.0 – health checks for the IT administrator: one page with the state of what ZEO depends on.

database · cache (Redis) · Celery workers · scheduler (beat heartbeat) · e-mail settings (and an SMTP test on request) ·
storage (free disk, files of the company) · licence · last backup (ZEO_BACKUP_DIR / ZEO_BACKUP_STATUS_FILE) · version and
migrations not applied · WhatsApp / SMS and push · webhooks failing · scheduled reports failing.
Each check: {key, label, state: ok | warn | bad | off, text, detail}.
"""
import os
import shutil
import time
from datetime import timedelta

from django.conf import settings
from django.db import connection
from django.utils import timezone

HEARTBEAT_KEY = 'zeo:beat:heartbeat'


def _t(text, *a):
    from Translations.i18n import tr   # v1.53.1 the request language
    try:
        return tr(text, *a)
    except Exception:
        return text.format(*a) if a else text


def _c(key, label, state, text, detail=''):
    return {'key': key, 'label': _t(label), 'state': state, 'text': _t(text) if text else text, 'detail': _t(detail) if detail else detail}


def _age(ts):
    s = int(time.time() - ts)
    return _t('{0} s', s) if s < 120 else (_t('{0} min', s // 60) if s < 7200 else _t('{0} h', s // 3600))


def heartbeat():
    """Beat runs this every 5 minutes (task ZeoAdmin.tasks.heartbeat)."""
    from django.core.cache import cache
    cache.set(HEARTBEAT_KEY, time.time(), 24 * 3600)
    path = os.path.join(settings.BASE_DIR, '.zeo-beat-heartbeat')
    try:
        with open(path, 'w') as fh:
            fh.write(str(time.time()))
    except Exception:
        pass


def check_db():
    t = time.time()
    try:
        with connection.cursor() as cur:
            cur.execute('select 1')
            ms = int((time.time() - t) * 1000)
            cur.execute("select pg_size_pretty(sum(pg_total_relation_size(quote_ident(schemaname) || '.' || quote_ident(tablename)))::bigint) "
                        "from pg_tables where schemaname = current_schema()")
            size = cur.fetchone()[0] or '–'
        return _c('database', 'Database', 'ok' if ms < 300 else 'warn', _t('Answers in {0} ms', ms), _t('Company data {0}', size))
    except Exception as e:
        return _c('database', 'Database', 'bad', 'Not reachable', str(e)[:200])


def check_cache():
    try:
        from django.core.cache import cache
        cache.set('zeo:health', 'ok', 30)
        ok = cache.get('zeo:health') == 'ok'
        backend = settings.CACHES['default']['BACKEND'].rsplit('.', 1)[-1]
        return _c('cache', 'Cache', 'ok' if ok else 'bad', 'Working' if ok else 'Not working', backend)
    except Exception as e:
        return _c('cache', 'Cache', 'bad', 'Not working', str(e)[:200])


def check_celery():
    if getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False):
        return _c('celery', 'Background jobs (Celery)', 'off', 'Run inside the web server (test set-up)')
    try:
        from zeo.celery import app
        r = app.control.inspect(timeout=1.5).ping() or {}
        if r:
            return _c('celery', 'Background jobs (Celery)', 'ok', _t('{0} worker(s) answering', len(r)), ', '.join(sorted(r))[:200])
        return _c('celery', 'Background jobs (Celery)', 'bad', 'No worker answers', 'Start zeo-celery (systemctl start zeo-celery).')
    except Exception as e:
        return _c('celery', 'Background jobs (Celery)', 'bad', 'Cannot reach the broker', str(e)[:200])


def check_beat():
    ts = None
    try:
        from django.core.cache import cache
        ts = cache.get(HEARTBEAT_KEY)
    except Exception:
        pass
    if ts is None:
        try:
            with open(os.path.join(settings.BASE_DIR, '.zeo-beat-heartbeat')) as fh:
                ts = float(fh.read().strip())
        except Exception:
            ts = None
    if ts is None:
        return _c('beat', 'Scheduler (Celery beat)', 'warn', 'No heartbeat yet', 'The scheduler writes a heartbeat every 5 minutes; start zeo-celery-beat.')
    age = time.time() - ts
    return _c('beat', 'Scheduler (Celery beat)', 'ok' if age < 900 else 'bad', _t('Last heartbeat {0} ago', _age(ts)),
              '' if age < 900 else 'Daily jobs (accruals, reminders, scheduled reports) are not running.')


def check_mail(test=False):
    from django.apps import apps
    try:
        cfg = apps.get_model('EmpManagement', 'EmailConfiguration').objects.filter(is_active=True).first()
    except LookupError:
        cfg = None
    if cfg is None:
        return _c('mail', 'E-mail', 'warn', 'No e-mail settings for this company', 'Settings → E-mail configuration. Server defaults are used.')
    if not test:
        return _c('mail', 'E-mail', 'ok', _t('{0} as {1}', f'{cfg.email_host}:{cfg.email_port}', cfg.email_host_user), 'Press "Test e-mail" to connect.')
    try:
        from django.core.mail import get_connection
        conn = get_connection(host=cfg.email_host, port=cfg.email_port, username=cfg.email_host_user, password=cfg.email_host_password,
                              use_tls=cfg.email_use_tls, timeout=8)
        conn.open()
        conn.close()
        return _c('mail', 'E-mail', 'ok', _t('Connected to {0}', f'{cfg.email_host}:{cfg.email_port}'), 'Login accepted.')
    except Exception as e:
        return _c('mail', 'E-mail', 'bad', _t('Cannot connect to {0}', f'{cfg.email_host}:{cfg.email_port}'), str(e)[:200])


def _dir_size(path, limit=20000):
    total, n = 0, 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
            n += 1
            if n > limit:
                return total, True
    return total, False


def check_storage():
    root = str(settings.MEDIA_ROOT)
    try:
        du = shutil.disk_usage(root if os.path.exists(root) else settings.BASE_DIR)
        free_pct = du.free * 100 / du.total
        state = 'ok' if free_pct > 15 else ('warn' if free_pct > 5 else 'bad')
        return _c('storage', 'Storage (files)', state, _t('{0} GB free of {1} GB ({2} %)', f'{du.free / 1024 ** 3:,.1f}', f'{du.total / 1024 ** 3:,.0f}', f'{free_pct:.0f}'), root)
    except Exception as e:
        return _c('storage', 'Storage (files)', 'bad', 'Cannot read the disk', str(e)[:200])


def check_licence():
    try:
        from ZeoLicence import licence as L
        cur = L.current() or {}
        if not cur.get('edition'):
            return _c('licence', 'Licence', 'off', 'Cloud / standard edition – no licence file')
        st = cur.get('status') or 'missing'
        state = {'valid': 'ok', 'expiring': 'warn', 'grace': 'warn', 'evaluation': 'warn'}.get(st, 'bad')
        left = cur.get('days_left')
        return _c('licence', 'Licence', state, _t(str(st).capitalize()) + (_t(' – {0} days left', left) if left is not None else ''), cur.get('message') or cur.get('problem') or '')
    except Exception as e:
        return _c('licence', 'Licence', 'off', 'Not checked', str(e)[:120])


def check_backup():
    status_file = os.environ.get('ZEO_BACKUP_STATUS_FILE') or getattr(settings, 'ZEO_BACKUP_STATUS_FILE', '')
    folder = os.environ.get('ZEO_BACKUP_DIR') or getattr(settings, 'ZEO_BACKUP_DIR', '')
    ts, name = None, ''
    if status_file and os.path.exists(status_file):
        ts, name = os.path.getmtime(status_file), os.path.basename(status_file)
    elif folder and os.path.isdir(folder):
        files = [os.path.join(folder, f) for f in os.listdir(folder) if not f.startswith('.')]
        files = [f for f in files if os.path.isfile(f)]
        if files:
            newest = max(files, key=os.path.getmtime)
            ts, name = os.path.getmtime(newest), os.path.basename(newest)
    if ts is None:
        return _c('backup', 'Last backup', 'warn', 'Not known', 'Set ZEO_BACKUP_DIR (the folder the nightly backup writes to) or ZEO_BACKUP_STATUS_FILE in .env.')
    age_h = (time.time() - ts) / 3600
    detail = name
    state = 'ok' if age_h < 30 else ('warn' if age_h < 72 else 'bad')
    try:   # v1.62.0 encrypted? copied off the server?
        import json as _json
        st = _json.load(open(status_file)) if status_file and os.path.exists(status_file) else (
            _json.load(open(os.path.join(folder, 'last-backup.json'))) if folder and os.path.exists(os.path.join(folder, 'last-backup.json')) else {})
        bits = []
        if st:
            bits.append(_t('encrypted') if st.get('encrypted') else _t('NOT encrypted (set ZEO_BACKUP_KEY)'))
            cp = st.get('copy') or {}
            oks = [v.get('ok') for v in cp.values() if isinstance(v, dict)]
            bits.append(_t('copied off the server') if oks and all(oks) else (_t('the copy FAILED') if oks else _t('NOT copied off the server (ZEO_BACKUP_COPY_TO / ZEO_BACKUP_COPY_CMD)')))
            if oks and not all(oks) and state == 'ok':
                state = 'warn'
            detail = f'{name} – ' + ', '.join(bits)
    except Exception:
        pass
    return _c('backup', 'Last backup', state, _t('{0} ago', _age(ts)), detail)


def check_security():
    """v1.62.0 the server-side security settings (README-UPDATE 83.3)."""
    out = []
    https = bool(getattr(settings, 'ZEO_HTTPS', False))
    out.append(_c('https', 'HTTPS and security headers', 'ok' if https and not settings.DEBUG else 'warn',
                  _t('On') if https else _t('Off'),
                  _t('Secure cookies, HSTS and the https address are on.') if https else _t('Set ZEO_HTTPS=true in .env once nginx serves https (deploy/nginx-zeo.conf).')))
    mins = int(settings.SIMPLE_JWT['ACCESS_TOKEN_LIFETIME'].total_seconds() // 60)
    out.append(_c('tokens', 'Sign-in tokens', 'ok' if mins <= 60 else 'warn', _t('{0} minutes', mins),
                  _t('Access tokens last {0} minutes; refresh tokens are spent on use.', mins)))
    thr = bool(settings.REST_FRAMEWORK.get('DEFAULT_THROTTLE_CLASSES'))
    out.append(_c('throttle', 'Rate limits', 'ok' if thr else 'warn', _t('On') if thr else _t('Off'), '' if thr else _t('ZEO_THROTTLE=off in .env')))
    try:
        from zeo.uploadguard import _clamd_path
        cp = _clamd_path()
        out.append(_c('clamav', 'Virus scan of uploads', 'ok' if cp else 'off', _t('ClamAV') if cp else _t('Not installed'),
                      cp or _t('Install clamav-daemon (free) to scan uploads; the type and size checks run anyway.')))
    except Exception:
        pass
    try:
        from ZeoCloud.geo import status
        g = status()
        out.append(_c('geoip', 'Country database', 'ok' if g['ready'] else 'off', _t('{0} ranges', g['rows']) if g['ready'] else _t('Not set'),
                      g['file'] or _t('ZEO_GEOIP_DB – needed only for country rules and countries in sign-in alerts.')))
    except Exception:
        pass
    return out


def check_migrations():
    try:
        from django.db.migrations.executor import MigrationExecutor
        ex = MigrationExecutor(connection)
        plan = ex.migration_plan(ex.loader.graph.leaf_nodes())
        v = ''
        try:
            with open(os.path.join(settings.BASE_DIR, 'VERSION')) as fh:
                v = fh.read().strip()
        except Exception:
            pass
        if plan:
            return _c('version', 'Version and database updates', 'bad', _t('{0} database update(s) not applied', len(plan)),
                      _t('ZEO {0}: run python manage.py migrate_schemas. ', v) + ', '.join(f'{m.app_label}.{m.name}' for m, _ in plan[:5]))
        return _c('version', 'Version and database updates', 'ok', f'ZEO {v}', 'Every database update applied.')
    except Exception as e:
        return _c('version', 'Version and database updates', 'warn', 'Not checked', str(e)[:200])


def check_messaging():
    out = []
    try:
        from Chatter.messaging import settings as ms, problems
        s = ms()
        if not s.enabled:
            out.append(_c('messaging', 'WhatsApp / SMS', 'off', 'Off'))
        else:
            from Chatter.models import MessageLog
            since = timezone.now() - timedelta(days=1)
            failed = MessageLog.objects.filter(at__gte=since, status='failed').count()
            sent = MessageLog.objects.filter(at__gte=since, status='sent').count()
            p = problems(s)
            out.append(_c('messaging', 'WhatsApp / SMS', 'bad' if p else ('warn' if failed else 'ok'), p or _t('{0} sent, {1} failed in 24 h', sent, failed), s.get_provider_display()))
    except Exception as e:
        out.append(_c('messaging', 'WhatsApp / SMS', 'off', 'Not checked', str(e)[:120]))
    try:
        from ZeoMobile import push
        schema = getattr(connection, 'schema_name', '')
        out.append(_c('push', 'Mobile push', 'ok' if push.configured(schema) else 'off', 'Set up' if push.configured(schema) else 'Not set up'))
    except Exception:
        pass
    return out


def check_integrations():
    from .models import ReportSchedule, Webhook, WebhookDelivery
    out = []
    hooks = Webhook.objects.all()
    if hooks.exists():
        since = timezone.now() - timedelta(days=1)
        failed = WebhookDelivery.objects.filter(at__gte=since, status='failed').count()
        off = hooks.filter(active=False, failures__gte=20).count()
        out.append(_c('webhooks', 'Webhooks', 'bad' if off else ('warn' if failed else 'ok'),
                      _t('{0} active, {1} failed deliveries in 24 h', hooks.filter(active=True).count(), failed) + (_t(', {0} switched off after failures', off) if off else '')))
    sch = ReportSchedule.objects.filter(active=True)
    if sch.exists():
        bad = sch.filter(last_status__startswith='failed').count()
        out.append(_c('schedules', 'Scheduled reports', 'warn' if bad else 'ok', _t('{0} active', sch.count()) + (_t(', {0} failed last time', bad) if bad else '')))
    return out


def run(test_mail=False):
    checks = [check_db(), check_cache(), check_celery(), check_beat(), check_mail(test_mail), check_storage(), check_backup(), check_licence(),
              check_migrations()] + check_messaging() + check_integrations() + check_security()
    worst = 'bad' if any(c['state'] == 'bad' for c in checks) else ('warn' if any(c['state'] == 'warn' for c in checks) else 'ok')
    return {'state': worst, 'checked_at': timezone.now(), 'checks': checks}
