"""ZEO Developer – running scripts (D3, D5, D6, D8), versions, settings."""
import difflib
import logging
import time

from django.db import connection, transaction
from django.utils import timezone

from .models import DeveloperSettings, Script, ScriptRun, ScriptVersion, VERSION
from .sandbox import Interpreter, ScriptError, LimitError, UserRaised, check

log = logging.getLogger(__name__)
_depth = __import__('threading').local()
MAX_TRIGGER_DEPTH = 3


class _DryRun(Exception):
    pass


class DevError(Exception):
    def __init__(self, message, status=400, field=None):
        super().__init__(message)
        self.message, self.status, self.field = message, status, field


def _tr(s, *a):
    try:
        from Translations.i18n import tr
        return tr(s, *a)
    except Exception:   # noqa: BLE001
        return s.format(*a)


def access_of(user):
    """The access facts (admin, rights, branches) of a user in the open company – the same as for the user's own requests."""
    from AccessControl.access import Ctx
    from .api import _Req
    return Ctx(_Req(user), connection.schema_name)


def uname(u):
    return ((u.get_full_name() or u.username) if u else '')[:120]


def draft_script(spec):
    """A15 hook (AI-written scripts) – comes after "Ask ZEO" is merged. Returns None until then."""
    return None


# ------------------------------------------------------------------ scripts
def save_script(data, user, s=None):
    import re
    name = str(data.get('name') or (s.name if s else '')).strip()
    if not name:
        raise DevError(_tr('Give the script a name.'), field='name')
    key = str(data.get('key') or (s.key if s else '') or re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_'))[:60]
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,59}', key):
        raise DevError(_tr('Key: small letters, digits and _, starting with a letter.'), field='key')
    if Script.objects.filter(key=key).exclude(pk=getattr(s, 'pk', None)).exists():
        raise DevError(_tr('Another script has the key "{0}".', key), field='key')
    trigger = data.get('trigger', getattr(s, 'trigger', 'manual'))
    if trigger not in dict(Script._meta.get_field('trigger').choices):
        raise DevError(_tr('Choose when the script runs.'), field='trigger')
    module_key = str(data.get('module_key', getattr(s, 'module_key', '')) or '')
    if module_key:
        from ZeoFlow.models import CustomModule
        m = CustomModule.objects.filter(key=module_key).first()
        if m is None:
            raise DevError(_tr('There is no custom module "{0}".', module_key), field='module_key')
    if trigger in ('create', 'update', 'move', 'button') and not module_key:
        raise DevError(_tr('A script that runs on a record belongs to a custom module – choose the module.'), field='module_key')
    model_key = str(data.get('model_key', getattr(s, 'model_key', '')) or '')
    if trigger in ('app_create', 'app_update'):
        from .appdata import known, registry
        mm = known(model_key)
        if mm is None:
            raise DevError(_tr('Choose the record type of an active app (e.g. ZeoCRM.lead).'), field='model_key')
        model_key = registry().model_key(mm)
    else:
        model_key = ''
    move_key = str(data.get('move_key', getattr(s, 'move_key', '')) or '')
    if trigger == 'move' and move_key:
        if move_key not in {t['key'] for t in m.blueprint.get('transitions', [])}:
            raise DevError(_tr('The module has no move "{0}".', move_key), field='move_key')
    button = str(data.get('button_label', getattr(s, 'button_label', '')) or '').strip()
    if trigger == 'button' and not button:
        raise DevError(_tr('Write the text of the button.'), field='button_label')
    dom = int(data.get('day_of_month', getattr(s, 'day_of_month', 1)) or 1)
    hour = int(data.get('hour', getattr(s, 'hour', 2)) or 0)
    if not 1 <= dom <= 28 or not 0 <= hour <= 23:
        raise DevError(_tr('Day of the month 1–28 and hour 0–23.'), field='day_of_month')
    code = data.get('code') if 'code' in data else (s.code if s else '')
    code = str(code or '')
    try:
        check(code) if code.strip() else None
    except ScriptError as e:
        raise DevError(str(e), field='code')
    new = s is None
    s = s or Script(created_by_id=user.pk, key=key)
    changed_code = new or code != s.code
    s.key, s.name, s.description, s.module_key, s.trigger = key, name[:120], str(data.get('description', s.description or '') or '')[:300], module_key, trigger
    s.move_key, s.button_label, s.day_of_month, s.hour, s.model_key = move_key, button[:40], dom, hour, model_key
    if 'active' in data:
        want = str(data['active']).lower() in ('1', 'true', 'yes')
        if want and not s.code.strip() and not code.strip():
            raise DevError(_tr('Write the script before switching it on.'), field='active')
        if want and s.stopped:
            s.stopped, s.failures = False, 0
        s.active = want
    s.updated_by_id = user.pk
    if changed_code and not new:
        s.version += 1
    s.code = code
    from .appdata import invalidate
    invalidate()
    with transaction.atomic():
        s.save()
        if changed_code:
            ScriptVersion.objects.create(script=s, version=s.version, code=code, note=str(data.get('note') or ('Created' if new else ''))[:200],
                                         saved_by_id=user.pk, saved_by_name=uname(user))
    return s


def restore(s, version, user):
    v = ScriptVersion.objects.filter(script=s, version=int(version)).first()
    if v is None:
        raise DevError(_tr('Version {0} not found.', version), 404)
    return save_script({'code': v.code, 'note': f'Restored from version {v.version}'}, user, s)


def compare(s, a, b):
    va = ScriptVersion.objects.filter(script=s, version=int(a)).first()
    vb = ScriptVersion.objects.filter(script=s, version=int(b)).first() if str(b) != 'current' else None
    if va is None or (vb is None and str(b) != 'current'):
        raise DevError(_tr('Version not found.'), 404)
    left = va.code.splitlines()
    right = (vb.code if vb else s.code).splitlines()
    lines = []
    for ln in difflib.unified_diff(left, right, f'version {va.version}', f'version {vb.version}' if vb else 'current', lineterm='', n=3):
        kind = 'meta' if ln.startswith(('---', '+++', '@@')) else 'add' if ln.startswith('+') else 'del' if ln.startswith('-') else 'same'
        lines.append({'kind': kind, 'text': ln})
    return {'from': va.version, 'to': vb.version if vb else 'current', 'lines': lines, 'same': not lines}


# ------------------------------------------------------------------ running
def runs_this_month():
    now = timezone.now()
    return ScriptRun.objects.filter(started_at__year=now.year, started_at__month=now.month, dry_run=False).count()


def limits_of(st):
    return {'steps': st.max_steps, 'seconds': float(min(st.time_limit_seconds or 5, 30)), 'items': st.max_items}


def run_code(code, user, *, script=None, trigger='manual', record=None, trigger_info=None, dry_run=False, record_ref='', app_record=None):
    """Runs a script (or a text being tested) and logs the run. Returns the ScriptRun.

    dry_run: everything happens inside a transaction that is rolled back at the end – the run shows its output and the changes it
    would make; e-mails, notifications and safe actions are only listed."""
    from .api import RunContext, globals_for
    st = DeveloperSettings.get()
    if not st.developer_mode:
        raise DevError(_tr('Developer mode is off in this company (Settings › Developer mode).'), 409)
    if not dry_run and runs_this_month() >= st.monthly_run_limit:
        raise DevError(_tr('The monthly limit of {0} script runs is reached (Developer › Settings).', st.monthly_run_limit), 429)
    if script is not None and not dry_run and (script.stopped or (not script.active and trigger != 'manual')):
        raise DevError(_tr('The script is switched off.'), 409)
    if getattr(_depth, 'n', 0) >= MAX_TRIGGER_DEPTH:
        raise DevError(_tr('Scripts started each other more than {0} deep – stopped.', MAX_TRIGGER_DEPTH), 409)
    run = ScriptRun(script=script, script_name=(script.name if script else 'Test')[:120], trigger=trigger[:12], user_id=user.pk, user_name=uname(user),
                    dry_run=dry_run, record_ref=(record_ref or (f'{record.module.key} {record.number}' if record is not None else ''))[:60],
                    version=script.version if script else 0)
    if not dry_run:
        run.save()
    ctx = RunContext(user, access_of(user), st, dry_run=dry_run, script=script, run=run if not dry_run else None)
    interp = Interpreter(limits=limits_of(st), log=ctx.log)
    ctx.interp = interp
    t0 = time.monotonic()
    status, error, line = 'ok', '', None
    _depth.n = getattr(_depth, 'n', 0) + 1
    was_dry = getattr(_depth, 'dry', False)
    _depth.dry = was_dry or dry_run
    try:
        try:   # one transaction: a failed run keeps nothing; a test run (dry run) is always rolled back
            with transaction.atomic():
                env = {}
                interp.globals = globals_for(ctx, record, trigger_info, app_record)
                interp.run_text(code, env)
                if dry_run:
                    raise _DryRun()
        except _DryRun:
            pass
    except LimitError as e:
        status, error, line = 'limit', e.message, e.line
    except ScriptError as e:
        status, error, line = ('refused' if e.kind == 'refused' else 'error'), e.message, e.line
    except UserRaised as e:
        status, error, line = 'error', f'stopped by the script: {e}', interp.line
    except RecursionError:
        status, error, line = 'limit', 'the script is nested too deeply (brackets or calls).', interp.line
    except Exception as e:   # noqa: BLE001 – a fault in ZEO, not in the script: logged in full on the server
        log.exception('script run failed')
        status, error, line = 'error', f'ZEO could not finish the run ({type(e).__name__}). The server log has the details.', interp.line
    finally:
        _depth.n -= 1
        _depth.dry = was_dry
    run.status, run.error, run.error_line = status, (error or '')[:2000], line
    run.duration_ms = int((time.monotonic() - t0) * 1000)
    run.steps, run.records_read, run.records_changed = interp.steps, ctx.read, ctx.changed
    run.output = '\n'.join(ctx.log)[:20000]
    run.changes = ctx.changes[:500]
    if not dry_run:
        run.save()
        if script is not None:
            _after(script, run, st)
    return run


def _after(script, run, st):
    script.last_run_at = timezone.now()
    if run.status == 'ok':
        script.failures = 0
    else:
        script.failures += 1
        if script.failures >= st.max_failures and not script.stopped:
            script.stopped = True
            script.active = False
            _tell_admins(script, run)
    Script.objects.filter(pk=script.pk).update(last_run_at=script.last_run_at, failures=script.failures, stopped=script.stopped, active=script.active)


def _tell_admins(script, run):
    try:
        from tenant_users.tenants.models import UserTenantPermissions
        from zeo.module_helpers import notify
        for utp in UserTenantPermissions.objects.filter(is_superuser=True).select_related('profile')[:10]:
            notify(user=utp.profile, title='ZEO Developer – script switched off',
                   message=f'{script.name}: {script.failures} failures in a row. Last: {run.error[:150]}', notification_type='general')
    except Exception:   # noqa: BLE001
        pass


def in_dry_run():
    return bool(getattr(_depth, 'dry', False))


def run_json(r, full=True):
    out = {'id': r.pk, 'script': r.script_id, 'script_name': r.script_name, 'trigger': r.trigger, 'record': r.record_ref, 'user': r.user_name,
           'dry_run': r.dry_run, 'status': r.status, 'started_at': r.started_at, 'duration_ms': r.duration_ms, 'steps': r.steps,
           'records_read': r.records_read, 'records_changed': r.records_changed, 'error': r.error, 'line': r.error_line, 'version': r.version}
    if full:
        out.update({'output': r.output, 'changes': r.changes})
    return out


def settings_json(st):
    return {'developer_mode': st.developer_mode, 'monthly_run_limit': st.monthly_run_limit, 'max_failures': st.max_failures,
            'time_limit_seconds': float(st.time_limit_seconds), 'max_steps': st.max_steps, 'max_items': st.max_items,
            'max_records_read': st.max_records_read, 'max_records_changed': st.max_records_changed, 'allowed_sites': st.allowed_sites or [],
            'run_as_user_id': st.run_as_user_id, 'runs_this_month': runs_this_month(), 'version': VERSION, 'api_version': 'v1'}


def save_settings(data, user):
    import re
    st = DeveloperSettings.get()
    ints = {'monthly_run_limit': (1, 1_000_000), 'max_failures': (1, 100), 'max_steps': (10_000, 50_000_000), 'max_items': (1_000, 2_000_000),
            'max_records_read': (10, 100_000), 'max_records_changed': (1, 20_000)}
    for k, (lo, hi) in ints.items():
        if k in data:
            try:
                v = int(data[k])
            except (TypeError, ValueError):
                raise DevError(_tr('{0}: a whole number.', k), field=k)
            if not lo <= v <= hi:
                raise DevError(_tr('{0}: from {1} to {2}.', k, lo, hi), field=k)
            setattr(st, k, v)
    if 'time_limit_seconds' in data:
        try:
            v = float(data['time_limit_seconds'])
        except (TypeError, ValueError):
            raise DevError(_tr('Time limit: a number of seconds.'), field='time_limit_seconds')
        if not 0.5 <= v <= 30:
            raise DevError(_tr('Time limit: from 0.5 to 30 seconds.'), field='time_limit_seconds')
        st.time_limit_seconds = v
    if 'allowed_sites' in data:
        sites = data['allowed_sites']
        sites = [x.strip().lower() for x in (sites.split(',') if isinstance(sites, str) else sites or []) if str(x).strip()]
        for h in sites:
            if not re.fullmatch(r'[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+', h) or re.fullmatch(r'[0-9.]+', h):
                raise DevError(_tr('Allowed sites: "{0}" is not a host name (e.g. api.example.com – no https://, no IP address).', h), field='allowed_sites')
        st.allowed_sites = sorted(set(sites))[:50]
    if 'developer_mode' in data:
        st.developer_mode = str(data['developer_mode']).lower() in ('1', 'true', 'yes')
        if st.developer_mode and not st.run_as_user_id:
            st.run_as_user_id = user.pk
    if 'run_as_user_id' in data and data['run_as_user_id']:
        from UserManagement.models import CustomUser
        u = CustomUser.objects.filter(pk=int(data['run_as_user_id'])).first()
        if u is None or not access_of(u).admin:
            raise DevError(_tr('Scheduled scripts run as a company administrator.'), field='run_as_user_id')
        st.run_as_user_id = u.pk
    st.updated_by_id = user.pk
    st.save()
    return st


def run_as_user():
    from UserManagement.models import CustomUser
    st = DeveloperSettings.get()
    u = CustomUser.objects.filter(pk=st.run_as_user_id, is_active=True).first() if st.run_as_user_id else None
    if u is not None and access_of(u).admin:
        return u
    try:
        from tenant_users.tenants.models import UserTenantPermissions
        utp = UserTenantPermissions.objects.filter(is_superuser=True, profile__is_active=True).select_related('profile').order_by('pk').first()
        return utp.profile if utp else None
    except Exception:   # noqa: BLE001
        return None
