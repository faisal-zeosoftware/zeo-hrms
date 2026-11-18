"""Ask ZEO – calling ZEO's own existing APIs as the asking user (guided payroll, forms, letters, imports).

Nothing is done "around" the product: the same view, serializer, rights check, branch check and history as when the user
presses the button on the screen. The company is the request's company; the change history (Chatter.RecordLog) records
the user and the Ask ZEO address, so every change shows "via Ask ZEO" in the record's own history.
"""
import json
import logging

from django.urls import Resolver404, resolve

log = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(self, status, detail, data=None):
        super().__init__(detail)
        self.status, self.detail, self.data = status, detail, data


def _detail(data):
    if isinstance(data, dict):
        for k in ('detail', 'error', 'message', 'non_field_errors'):
            if data.get(k):
                v = data[k]
                return ' '.join(map(str, v)) if isinstance(v, list) else str(v)
        parts = []
        for k, v in data.items():
            v = ' '.join(map(str, v)) if isinstance(v, list) else (json.dumps(v) if isinstance(v, dict) else str(v))
            parts.append(f'{k}: {v}')
        return '; '.join(parts)[:500]
    if isinstance(data, list):
        return ' '.join(map(str, data))[:500]
    return str(data or '')[:500]


def call(request, method, path, data=None, query=None, files=None):
    """Runs the API at `path` (e.g. '/payroll/api/PayrollRun/') as request.user; returns the response data.
    Raises ApiError(status, detail) for a 4xx / 5xx answer."""
    from rest_framework.test import APIRequestFactory, force_authenticate
    path = '/' + path.lstrip('/')
    try:
        match = resolve(path)
    except Resolver404:
        raise ApiError(404, f'Unknown address {path}')
    f = APIRequestFactory()
    method = method.lower()
    extra = {}
    for h in ('HTTP_X_LANGUAGE', 'HTTP_ACCEPT_LANGUAGE', 'HTTP_X_BRANCH', 'HTTP_X_SCHEMA', 'REMOTE_ADDR', 'HTTP_USER_AGENT'):
        v = request.META.get(h) if hasattr(request, 'META') else None
        if v:
            extra[h] = v
    try:   # the same host as the user's request (Django refuses the factory's "testserver" when the view reads the host)
        extra['HTTP_HOST'] = request.get_host()
        extra['SERVER_NAME'] = request.get_host().split(':')[0]
        if request.is_secure():
            extra['wsgi.url_scheme'] = 'https'
    except Exception:
        pass
    if query:
        from urllib.parse import urlencode
        path = path + '?' + urlencode(query, doseq=True)
    if method == 'get':
        req = f.get(path, **extra)
    elif files:
        payload = {k: ('true' if v is True else 'false' if v is False else v) for k, v in (data or {}).items() if v is not None}
        payload.update(files)
        req = getattr(f, method)(path, payload, format='multipart', **extra)
    else:
        req = getattr(f, method)(path, data or {}, format='json', **extra)
    user = getattr(request, 'user', None)
    force_authenticate(req, user=user)
    for attr in ('tenant', 'urlconf'):
        if hasattr(request, attr):
            setattr(req, attr, getattr(request, attr))
    req.askzeo = True
    from django.db import transaction

    class _Undo(Exception):
        pass
    resp = None
    try:   # one savepoint: a call that fails (an error answer or an exception) leaves nothing half done
        with transaction.atomic():
            resp = match.func(req, *match.args, **match.kwargs)
            if hasattr(resp, 'render') and not getattr(resp, 'is_rendered', True):
                resp.render()
            if resp.status_code >= 400:
                raise _Undo()
    except _Undo:
        pass
    except Exception as exc:   # the view raised – show it as a refusal, never a crash of the chat
        log.exception('Ask ZEO call %s %s failed', method, path)
        raise ApiError(500, f'{exc.__class__.__name__}: {exc}')
    data_out = getattr(resp, 'data', None)
    if data_out is None:
        try:
            data_out = json.loads(resp.content or b'null')
        except (ValueError, AttributeError):
            data_out = {'content_type': resp.get('Content-Type', '') if hasattr(resp, 'get') else '', 'size': len(getattr(resp, 'content', b'') or b'')}
    if resp.status_code >= 400:
        raise ApiError(resp.status_code, _detail(data_out) or f'HTTP {resp.status_code}', data_out)
    return data_out
