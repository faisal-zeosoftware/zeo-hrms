"""ZEO Developer – calling ZEO's own API inside the server, as a user.

The safe actions (D2) create leave requests, expense claims … through the same API the screens use, with the running user's
login: the same checks, rights, numbering, approvals, e-mails and history as when the user does it on screen – a script can
never do more than its user could do by hand."""
from django.db import connection
from django.urls import resolve
from rest_framework.test import APIRequestFactory, force_authenticate


class ApiRefused(Exception):
    def __init__(self, message, status=400, data=None):
        super().__init__(message)
        self.message, self.status, self.data = message, status, data


def _message(data):
    if isinstance(data, dict):
        if data.get('detail'):
            return str(data['detail'])
        if data.get('error'):
            return str(data['error'])
        parts = []
        for k, v in data.items():
            if isinstance(v, (list, tuple)):
                v = ' '.join(str(x) for x in v)
            parts.append(f'{k}: {v}' if k not in ('non_field_errors', '__all__') else str(v))
        return '; '.join(parts)[:500] or 'refused'
    if isinstance(data, list):
        return ' '.join(str(x) for x in data)[:500]
    return str(data)[:500]


# The guards of settings.MIDDLEWARE that refuse writes (company read-only or suspended, licence ended, HRMS switched off, the setup
# guide not finished, an app's setup wizard open). An internal call goes through them like a request from the screen.
GUARDS = ('ZeoCloud.middleware.CloudMiddleware', 'ZeoLicence.middleware.LicenceMiddleware', 'ZeoApps.middleware.HRMSActiveMiddleware',
          'ZeoAdmin.setup_gate.SetupGateMiddleware', 'ZeoApps.appsetup.AppSetupGateMiddleware')


def _guarded(view):
    from django.conf import settings
    from django.utils.module_loading import import_string
    h = view
    for p in reversed(GUARDS):
        if p in settings.MIDDLEWARE:
            h = import_string(p)(h)
    return h


def call(user, method, path, data=None, params=None):
    """(status, data). path like '/calendars/api/emp-leave-request/'. Raises ApiRefused for 4xx / 5xx."""
    f = APIRequestFactory()
    q = {'schema': connection.schema_name}
    q.update(params or {})
    from urllib.parse import urlencode
    url = f'{path}?{urlencode(q)}'
    m = method.lower()
    if m == 'get':
        req = f.get(url)
    else:
        req = getattr(f, m)(url, data or {}, format='json')
    force_authenticate(req, user=user)
    try:
        req.tenant = connection.tenant
    except Exception:   # noqa: BLE001
        pass
    req.user = user
    match = resolve(path)

    def view(r):
        return match.func(r, *match.args, **match.kwargs)
    resp = _guarded(view)(req)
    if hasattr(resp, 'render') and not getattr(resp, 'is_rendered', True):
        resp.render()
    out = getattr(resp, 'data', None)
    if out is None:   # a guard's own answer (409 setup_required, read-only company …)
        try:
            import json
            out = json.loads(resp.content.decode('utf-8') or 'null')
        except (ValueError, AttributeError):
            out = {'detail': (getattr(resp, 'content', b'') or b'').decode('utf-8', errors='replace')[:300]}
    if resp.status_code >= 400:
        raise ApiRefused(_message(out), resp.status_code, out)
    return resp.status_code, out
