"""ZEO Developer – daily / monthly scripts (D5) and scheduled calculations (D1), run by Celery beat every hour
(ZeoDeveloper.tasks.run_due) or `python manage.py run_developer_schedules` from cron. Each job runs once per day / month and
company, at or after its hour (company time)."""
import logging

from django.db import transaction
from django.utils import timezone

from .models import DeveloperSettings, Script, ScheduledCalc

log = logging.getLogger(__name__)


def check_calc(data, m):
    from . import formulas as F
    fields = {f['key']: f for f in m.fields}
    fk = str(data.get('field_key') or '')
    f = fields.get(fk)
    if f is None or f['type'] not in ('number', 'money', 'date', 'text', 'yesno'):
        raise ValueError('Choose a number, amount, date, text or yes / no field of the module to set.')
    formula = str(data.get('formula') or '').strip()
    probs = F.problems(formula, set(fields) | {'number', 'state'})
    if probs:
        raise ValueError(probs[0])
    freq = data.get('frequency') or 'monthly'
    if freq not in ('daily', 'monthly'):
        raise ValueError('Every day or every month.')
    states = [s for s in (data.get('states') or []) if s]
    if any(s not in {x['key'] for x in m.blueprint.get('states', [])} for s in states):
        raise ValueError('A state is not a state of the module.')
    name = str(data.get('name') or '').strip()
    if not name:
        raise ValueError('Give the calculation a name.')
    return {'name': name[:120], 'field_key': fk, 'formula': formula, 'frequency': freq, 'states': states}


def run_calc(c, user, dry_run=False):
    """Sets the field on every record of the module (in the states chosen) – one transaction; the history of each record says so."""
    from ZeoFlow.models import CustomModule, CustomRecord
    from . import formulas as F
    from .fields import variables_of, lookup_value, compute
    m = CustomModule.objects.get(key=c.module_key)
    f = next(x for x in m.fields if x['key'] == c.field_key)
    qs = CustomRecord.objects.filter(module=m)
    if c.states:
        qs = qs.filter(state__in=c.states)
    changed, errors, sample = 0, [], []
    with transaction.atomic():
        for r in qs.select_for_update():
            vars_ = variables_of(m, r.data, r.number, r.state)
            children = {x['key']: r.data.get(x['key']) or [] for x in m.fields if x['type'] == 'table'}
            try:
                v = F.evaluate(c.formula, vars_, children, lookup_value)
                if f['type'] == 'yesno':
                    new = bool(v)
                else:
                    new = F.as_result(v, {'number': 'number', 'money': 'money', 'date': 'date', 'text': 'text'}[f['type']])
            except (F.FormulaError, ValueError) as e:
                errors.append(f'{r.number}: {e}')
                continue
            if r.data.get(f['key']) != new:
                old = r.data.get(f['key'])
                r.data[f['key']] = new
                try:
                    compute(m, r.data, r.number, r.state)   # fields calculated from it follow
                except Exception as e:   # noqa: BLE001
                    errors.append(f'{r.number}: {e}')
                    continue
                r.history = (r.history or []) + [{'at': timezone.now().isoformat(), 'by': user.pk if user else None, 'by_name': 'ZEO Developer',
                                                   'action': c.name, 'note': f'{f["label"]}: {old} → {new}'}]
                r.save(update_fields=['data', 'history', 'updated_at'])
                changed += 1
                if len(sample) < 5:
                    sample.append({'record': r.number, 'old': old, 'new': new})
        if dry_run:
            transaction.set_rollback(True)
    return {'changed': changed, 'errors': errors[:20], 'sample': sample}


def _period(freq, now):
    return now.strftime('%Y-%m-%d') if freq == 'daily' else now.strftime('%Y-%m')


def run_due(force=False):
    """One company (the open schema). Returns what ran."""
    from . import services as S
    st = DeveloperSettings.get()
    if not st.developer_mode:
        return {'skipped': 'developer mode is off'}
    now = timezone.localtime()
    user = S.run_as_user()
    if user is None:
        return {'skipped': 'no company administrator to run as'}
    out = {'scripts': [], 'calculations': []}
    for s in Script.objects.filter(trigger__in=('daily', 'monthly'), active=True, stopped=False):
        per = _period(s.trigger, now)
        due = force or (s.last_period != per and now.hour >= s.hour and (s.trigger == 'daily' or now.day >= s.day_of_month))
        if not due:
            continue
        Script.objects.filter(pk=s.pk).update(last_period=per)
        try:
            run = S.run_code(s.code, user, script=s, trigger=s.trigger, trigger_info={'event': s.trigger, 'period': per})
            out['scripts'].append({'script': s.key, 'status': run.status})
        except S.DevError as e:
            out['scripts'].append({'script': s.key, 'status': 'not run', 'error': e.message})
    for c in ScheduledCalc.objects.filter(active=True):
        per = _period(c.frequency, now)
        if not force and c.last_period == per:
            continue
        try:
            res = run_calc(c, user)
            c.last_result = (f'{res["changed"]} records changed' + (f', {len(res["errors"])} errors: {res["errors"][0]}' if res['errors'] else ''))[:300]
        except Exception as e:   # noqa: BLE001
            log.exception('scheduled calculation %s', c.pk)
            c.last_result = f'failed: {e}'[:300]
        c.last_period = per
        c.save(update_fields=['last_period', 'last_result', 'updated_at'])
        out['calculations'].append({'name': c.name, 'result': c.last_result})
    return out
