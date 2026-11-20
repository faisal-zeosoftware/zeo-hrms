"""v1.75.0 Settings › Features (design rule #155): a new company sees the standard features only; every advanced feature is off until an
administrator switches it on (per app). A feature names the registers (screens), form fields and set-up fields it shows; switching it off hides
them again – the data is kept, and a feature in use cannot be switched off. Stored in ZeoApps.CompanyOption ("features:<app>") – no table of its own.

An app declares:  app.features = [Feature('lots', 'Lots and serial numbers', 'Tracking', 'Batches with expiry …', regs=('lot',), fields={'line': ('lot', …)},
                                          needs=('…',), in_use=lambda: Model.objects.exists()), …]
                  app.presets = {'food': ('Food trading', ('lots', 'expiry', …)), …}"""
import threading

_TL = threading.local()


class Feature:
    def __init__(self, key, label, group, help='', standard=False, regs=(), fields=None, settings=(), needs=(), in_use=None, who=''):
        self.key, self.label, self.group, self.help, self.standard = key, label, group, help, standard
        self.regs, self.fields, self.settings, self.needs, self.in_use, self.who = tuple(regs), dict(fields or {}), tuple(settings), tuple(needs), in_use, who


def _opt():
    from django.apps import apps
    return apps.get_model('ZeoApps', 'CompanyOption')


def _key(app):
    return f'features:{app.slug}'


def _cache():
    """per thread, per company, for 2 seconds (one request) – a change in another worker shows on the next request"""
    import time
    from django.db import connection
    c = getattr(_TL, 'c', None)
    now = time.monotonic()
    if c is None or c[0] != connection.schema_name or now - c[2] > 2:
        c = (connection.schema_name, {}, now)
        _TL.c = c
    return c[1]


def forget():
    _TL.c = None


def used(f):
    try:
        return int(f.in_use()) if f.in_use else 0
    except Exception:   # noqa: BLE001
        return 0


def state(app):
    """{feature key: on}. A company without a saved choice gets the standard features plus every feature it already uses
    (existing companies keep what they use)."""
    feats = getattr(app, 'features', None) or []
    if not feats:
        return {}
    c = _cache()
    if app.slug in c:
        return c[app.slug]
    st = None
    try:
        row = _opt().objects.filter(key=_key(app)).first()
        st = dict(row.value or {}) if row is not None else None
    except Exception:   # noqa: BLE001 – no table yet
        st = None
    out = {}
    for f in feats:
        if st is not None and f.key in st:
            out[f.key] = bool(st[f.key])
        else:
            out[f.key] = f.standard or bool(used(f))
    c[app.slug] = out
    return out


def on(app, key):
    s = state(app)
    return s.get(key, True)


def save(app, values, user=None):
    feats = {f.key: f for f in getattr(app, 'features', [])}
    cur = dict(state(app))
    changed = []
    for k, v in values.items():
        if k not in feats:
            continue
        v = bool(v)
        if cur.get(k) == v:
            continue
        if not v and used(feats[k]):
            from .engine import AppError
            raise AppError(f'"{feats[k].label}" is in use ({used(feats[k])} records) – it stays on.', k)
        cur[k] = v
        changed.append((k, v))
    # dependencies: switching on brings what it needs; switching off takes what needs it
    for _ in range(5):
        for k, f in feats.items():
            if cur.get(k):
                for n in f.needs:
                    if n in feats and not cur.get(n):
                        cur[n] = True
                        changed.append((n, True))
        for k, f in feats.items():
            if not cur.get(k):
                for k2, f2 in feats.items():
                    if cur.get(k2) and k in f2.needs and not used(f2):
                        cur[k2] = False
                        changed.append((k2, False))
    O = _opt()
    row, _ = O.objects.get_or_create(key=_key(app), defaults={'value': {}})
    row.value = cur
    row.save()
    forget()
    if changed:
        try:
            app.event('settings', 0, 'Features: ' + ', '.join(f'{feats[k].label} {"on" if v else "off"}' for k, v in changed), user)
        except Exception:   # noqa: BLE001
            pass
    return cur


def hidden_regs(app):
    s = state(app)
    out = set()
    for f in getattr(app, 'features', []):
        if not s.get(f.key, True):
            out |= set(f.regs)
    # a register shown by another feature that is on stays shown
    for f in getattr(app, 'features', []):
        if s.get(f.key, True):
            out -= set(f.regs)
    return out


def hidden_fields(app, kind):
    s = state(app)
    out = set()
    for f in getattr(app, 'features', []):
        if not s.get(f.key, True):
            out |= set(f.fields.get(kind, ()))
    for f in getattr(app, 'features', []):
        if s.get(f.key, True):
            out -= set(f.fields.get(kind, ()))
    return out


def hidden_settings(app):
    s = state(app)
    out = set()
    for f in getattr(app, 'features', []):
        if not s.get(f.key, True):
            out |= set(f.settings)
    return out


def listing(app):
    s = state(app)
    feats = getattr(app, 'features', [])
    labels = {f.key: f.label for f in feats}
    return {'features': [{'key': f.key, 'label': f.label, 'group': f.group, 'help': f.help, 'who': f.who, 'standard': f.standard, 'on': s.get(f.key, True),
                          'needs': [labels.get(n, n) for n in f.needs], 'used': used(f), 'screens': list(f.regs)} for f in feats],
            'presets': [{'key': k, 'label': v[0], 'features': [labels.get(x, x) for x in v[1]]} for k, v in (getattr(app, 'presets', None) or {}).items()]}
