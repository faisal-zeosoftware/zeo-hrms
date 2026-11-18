"""ZEO Developer – the triggers (D5) called by ZeoFlow.custom: on create / on update / on a state move of a custom module record;
and what a record page shows extra (script buttons, print layouts)."""
import logging

from django.db import connection

log = logging.getLogger(__name__)


def _enabled():
    from .models import DeveloperSettings
    try:
        return DeveloperSettings.get().developer_mode
    except Exception:   # noqa: BLE001 – table not there yet (before migrate)
        return False


def record_event(event, r, request, **kw):
    """Runs the active scripts of the module for this event, as the user who saved the record. A failing script never undoes the
    user's save – its run is logged with the error (Developer › Run log) and after repeated failures it is switched off."""
    from .models import Script
    from . import services as S
    if not _enabled():
        return []
    qs = Script.objects.filter(module_key=r.module.key, trigger=event, active=True, stopped=False)
    if event == 'move':
        scripts = [s for s in qs if not s.move_key or s.move_key == kw.get('transition')]
    else:
        scripts = list(qs)
    if not scripts:
        return []
    if S.in_dry_run():   # a test run never starts the module's other scripts
        return []
    info = {'event': event, 'transition': kw.get('transition') or '', 'old_state': kw.get('old_state') or '', 'changed': kw.get('changed') or [],
            'old': kw.get('old') or {}}
    out = []
    for s in scripts:
        try:
            run = S.run_code(s.code, request.user, script=s, trigger=event, record=r, trigger_info=info)
            out.append(run)
            r.refresh_from_db()
        except S.DevError as e:
            log.info('script %s not run: %s', s.key, e.message)
    return out


def record_extras(r, request):
    """Buttons of the module's button scripts and its print layouts (for the record page)."""
    from .models import Script, PrintLayout
    if not _enabled():
        return {}
    btns = [{'script': s.pk, 'label': s.button_label or s.name} for s in Script.objects.filter(module_key=r.module.key, trigger='button', active=True, stopped=False)]
    prints = [{'id': p.pk, 'name': p.name} for p in PrintLayout.objects.filter(module_key=r.module.key, active=True)]
    return {'buttons': btns, 'prints': prints}
