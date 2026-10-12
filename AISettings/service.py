"""v1.16.0 – AI settings: server switch, company settings, key encryption, the generic external client and the call log.

Who decides what
* Server administrator / SaaS operator (.env): ZEO_AI_EXTERNAL=true allows companies to connect an external AI.
  Default false: every company uses the built-in, free mode and the settings screen shows external AI as
  "switched off by the server administrator".
* Company administrator (Settings → AI settings): built-in (default) or external, provider, address, model, key,
  which features use it (Ask ZEO, Documents) and a monthly call limit.

Every external request goes through chat() below: it checks the server switch, the company choice, the feature switch
and the monthly limit, writes one AICallLog row (who, when, feature, sizes, result – never the content) and raises
ExternalUnavailable on any problem so the caller falls back to the built-in mode.

Server settings (environment / .env):
  ZEO_AI_EXTERNAL            true = companies may use an external AI (default false)
  ZEO_AI_ENCRYPTION_KEY      optional; secret used to encrypt the stored API keys (default: derived from ZEO_SECRET_KEY).
                             Changing it (or ZEO_SECRET_KEY without it) makes saved keys unreadable – admins re-enter them.
  ZEO_AI_ALLOW_PRIVATE_URL   true = the API address may be on this server / the local network (e.g. an on-premises
                             OpenAI-compatible server). Default false: only public https addresses.
"""
import base64
import ipaddress
import json
import logging
import os
import socket
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from django.utils import timezone

log = logging.getLogger(__name__)

DEFAULT_URLS = {
    'anthropic': 'https://api.anthropic.com/v1/messages',
    'openai': 'https://api.openai.com/v1/chat/completions',
    'azure': '',   # https://<resource>.openai.azure.com/openai/deployments/<deployment>/chat/completions?api-version=2024-10-21
}
DEFAULT_MODELS = {'anthropic': 'claude-sonnet-5-5', 'openai': 'gpt-4o-mini', 'azure': ''}
FEATURE_FIELDS = {'ask': 'use_ask', 'documents': 'use_documents'}
TIMEOUT = 90


class ExternalUnavailable(RuntimeError):
    """The external AI may not or could not be used – the caller answers in the built-in mode."""


def _truthy(v):
    return str(v or '').strip().lower() in ('1', 'true', 'yes', 'on')


def _env(name, default=''):
    from django.conf import settings
    return os.environ.get(name) or getattr(settings, name, None) or default


def server_allows_external():
    """The SaaS operator's switch (ZEO_AI_EXTERNAL in .env). Default: off."""
    return _truthy(_env('ZEO_AI_EXTERNAL', 'false'))


def allow_private_url():
    return _truthy(_env('ZEO_AI_ALLOW_PRIVATE_URL', 'false'))


# ------------------------------------------------------------------ key encryption
def _fernet():
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from django.conf import settings
    secret = _env('ZEO_AI_ENCRYPTION_KEY') or settings.SECRET_KEY
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=b'zeo-hrms-ai-settings', info=b'api-key-v1').derive(secret.encode())
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_key(plain):
    return _fernet().encrypt(plain.encode()).decode() if plain else ''


def decrypt_key(token):
    """The stored key, or None when it cannot be read (encryption secret changed)."""
    if not token:
        return ''
    from cryptography.fernet import InvalidToken
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return None


# ------------------------------------------------------------------ address check (no calls into the server's own network)
def check_url(url):
    """'' when the address may be used, else a message saying how to fix it."""
    from Translations.i18n import tr
    try:
        u = urlparse(url)
    except ValueError:
        return tr('The API address is not a valid web address.')
    if u.scheme not in ('https', 'http') or not u.hostname:
        return tr('The API address must start with https:// .')
    if u.username or u.password:
        return tr('Do not put a user name or password in the API address; use the API key field.')
    if allow_private_url():
        return ''
    if u.scheme != 'https':
        return tr('The API address must start with https:// .')
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or 443, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError):
        return tr('The API address {0} cannot be found. Check the spelling.', u.hostname)
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return tr('The API address points to this server or the local network, which the server administrator has not allowed (ZEO_AI_ALLOW_PRIVATE_URL).')
    return ''


# ------------------------------------------------------------------ settings → config
def settings_row():
    """The company's settings row. Raises ExternalUnavailable outside a company (public schema) or before migrate."""
    from django.db import DatabaseError, connection
    from .models import AISettings
    if getattr(connection, 'schema_name', 'public') == 'public':
        raise ExternalUnavailable('No company selected.')
    try:
        return AISettings.get()
    except DatabaseError:
        log.warning('AI settings table missing – run migrate_schemas', exc_info=True)
        raise ExternalUnavailable('The AI settings are not installed on this server.')


def month_usage():
    from .models import AICallLog
    start = timezone.localtime().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return AICallLog.objects.filter(at__gte=start, sent=True).exclude(feature='test').count()


def config_for(row, api_key=None):
    """The values a request needs (key decrypted). api_key given = use it instead of the stored one (test before save)."""
    key = api_key if api_key else decrypt_key(row.api_key_enc)
    return {'provider': row.provider, 'url': (row.api_url or DEFAULT_URLS.get(row.provider) or '').strip(),
            'model': (row.model or DEFAULT_MODELS.get(row.provider) or '').strip(), 'key': key}


def feature_mode(feature, row=None):
    """('external', '') when this feature should use the external AI now, else ('builtin', reason)."""
    if not server_allows_external():
        return 'builtin', 'server'
    if row is None:
        try:
            row = settings_row()
        except ExternalUnavailable:
            return 'builtin', 'company'
    if row.mode != 'external':
        return 'builtin', 'company'
    if not getattr(row, FEATURE_FIELDS.get(feature, ''), False):
        return 'builtin', 'feature'
    if not row.api_key_enc:
        return 'builtin', 'no key'
    if decrypt_key(row.api_key_enc) is None:
        return 'builtin', 'key unreadable'
    return 'external', ''


def _who(request):
    u = getattr(request, 'user', None) if request is not None else None
    return (getattr(u, 'pk', None), getattr(u, 'username', '') or '')


def _log(request, feature, cfg, sent, ok, result, req_b=0, resp_b=0, ms=0):
    from .models import AICallLog
    uid, uname = _who(request)
    try:
        AICallLog.objects.create(user_id=uid, username=uname[:150], feature=feature, provider=cfg.get('provider', '')[:12],
                                 model=(cfg.get('model') or '')[:120], sent=sent, ok=ok, result=result[:60],
                                 request_bytes=req_b, response_bytes=resp_b, duration_ms=ms)
    except Exception:   # the log must never break an answer
        log.exception('AI call log failed')


# ------------------------------------------------------------------ provider formats
def _openai_messages(system, messages):
    """Anthropic-style messages (content blocks, tool_use / tool_result) → OpenAI chat messages."""
    out = [{'role': 'system', 'content': system}] if system else []
    for m in messages:
        c = m['content']
        if isinstance(c, str):
            out.append({'role': m['role'], 'content': c})
            continue
        if m['role'] == 'assistant':
            text = '\n'.join(b.get('text', '') for b in c if b.get('type') == 'text')
            calls = [{'id': b['id'], 'type': 'function', 'function': {'name': b['name'], 'arguments': json.dumps(b.get('input') or {})}}
                     for b in c if b.get('type') == 'tool_use']
            msg = {'role': 'assistant', 'content': text or None}
            if calls:
                msg['tool_calls'] = calls
            out.append(msg)
        else:
            for b in c:
                if b.get('type') == 'tool_result':
                    out.append({'role': 'tool', 'tool_call_id': b['tool_use_id'], 'content': b.get('content') or ''})
                elif b.get('type') == 'text':
                    out.append({'role': 'user', 'content': b.get('text', '')})
    return out


def _openai_to_anthropic(r):
    ch = (r.get('choices') or [{}])[0]
    msg = ch.get('message') or {}
    blocks = []
    if msg.get('content'):
        blocks.append({'type': 'text', 'text': msg['content']})
    for tc in msg.get('tool_calls') or []:
        fn = tc.get('function') or {}
        try:
            args = json.loads(fn.get('arguments') or '{}')
        except ValueError:
            args = {}
        blocks.append({'type': 'tool_use', 'id': tc.get('id') or f"call_{len(blocks)}", 'name': fn.get('name'), 'input': args})
    stop = 'tool_use' if any(b['type'] == 'tool_use' for b in blocks) else 'end_turn'
    return {'content': blocks, 'stop_reason': stop}


def _build(cfg, system, messages, tools, max_tokens):
    p = cfg['provider']
    if p == 'anthropic':
        body = {'model': cfg['model'], 'max_tokens': max_tokens, 'system': system, 'messages': messages}
        if tools:
            body['tools'] = tools
        headers = {'content-type': 'application/json', 'x-api-key': cfg['key'], 'anthropic-version': '2023-06-01'}
    else:
        body = {'messages': _openai_messages(system, messages), 'max_tokens': max_tokens}
        if cfg['model']:
            body['model'] = cfg['model']
        if tools:
            body['tools'] = [{'type': 'function', 'function': {'name': t['name'], 'description': t.get('description', ''),
                                                               'parameters': t.get('input_schema') or {'type': 'object', 'properties': {}}}} for t in tools]
        headers = {'content-type': 'application/json'}
        if p == 'azure':
            headers['api-key'] = cfg['key']
        else:
            headers['authorization'] = 'Bearer ' + cfg['key']
    return json.dumps(body).encode(), headers


def _post(cfg, data, headers):
    req = urllib.request.Request(cfg['url'], data=data, method='POST', headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def _send(request, feature, cfg, system, messages, tools, max_tokens):
    """One request to the provider, logged. Returns an Anthropic-style response dict. Raises ExternalUnavailable."""
    from Translations.i18n import tr
    if not cfg.get('url'):
        _log(request, feature, cfg, False, False, 'no address')
        raise ExternalUnavailable(tr('The API address is missing. Enter it in Settings → AI settings.'))
    bad = check_url(cfg['url'])
    if bad:
        _log(request, feature, cfg, False, False, 'address refused')
        raise ExternalUnavailable(bad)
    if not cfg.get('key'):
        _log(request, feature, cfg, False, False, 'no key')
        raise ExternalUnavailable(tr('The API key is missing or cannot be read. Enter it again in Settings → AI settings.'))
    data, headers = _build(cfg, system, messages, tools, max_tokens)
    t0 = time.time()
    ms = lambda: int((time.time() - t0) * 1000)   # noqa: E731
    try:
        raw = _post(cfg, data, headers)
    except urllib.error.HTTPError as e:
        try:
            detail = e.read()[:300].decode('utf-8', 'replace')
        except Exception:
            detail = ''
        log.warning('external AI %s answered %s: %s', cfg['provider'], e.code, detail)
        _log(request, feature, cfg, True, False, f'http {e.code}', len(data), len(detail), ms())
        raise ExternalUnavailable({401: tr('The AI provider did not accept the API key (401). Check the key.'),
                                   403: tr('The AI provider refused the request (403). Check the key and its rights.'),
                                   404: tr('The AI provider does not know this address or model (404). Check the API address and model.'),
                                   429: tr('The AI provider is busy or your quota is used up (429). Try again later.')}.get(
            e.code, tr('The AI provider answered with error {0}.', e.code)))
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as e:
        log.warning('external AI %s unreachable: %s', cfg['provider'], e)
        _log(request, feature, cfg, True, False, 'unreachable', len(data), 0, ms())
        raise ExternalUnavailable(tr('The AI provider cannot be reached from the server. Check the API address and the server\'s internet access.'))
    try:
        r = json.loads(raw)
    except ValueError:
        _log(request, feature, cfg, True, False, 'bad answer', len(data), len(raw), ms())
        raise ExternalUnavailable(tr('The AI provider sent an answer ZEO cannot read. Check the provider type and API address.'))
    if cfg['provider'] != 'anthropic':
        if not isinstance(r, dict) or 'choices' not in r:
            _log(request, feature, cfg, True, False, 'bad answer', len(data), len(raw), ms())
            raise ExternalUnavailable(tr('The AI provider sent an answer ZEO cannot read. Check the provider type and API address.'))
        r = _openai_to_anthropic(r)
    elif not isinstance(r, dict) or 'content' not in r:
        _log(request, feature, cfg, True, False, 'bad answer', len(data), len(raw), ms())
        raise ExternalUnavailable(tr('The AI provider sent an answer ZEO cannot read. Check the provider type and API address.'))
    _log(request, feature, cfg, True, True, 'ok', len(data), len(raw), ms())
    return r


def chat(request, feature, system, messages, tools=None, max_tokens=2000):
    """External call for a feature (ask / documents) with every check. Raises ExternalUnavailable → use the built-in mode."""
    from Translations.i18n import tr
    if not server_allows_external():
        raise ExternalUnavailable(tr('External AI is switched off by the server administrator.'))
    row = settings_row()
    mode, why = feature_mode(feature, row)
    if mode != 'external':
        # never contact an outside service unless server AND company switched it on
        raise ExternalUnavailable({'server': tr('External AI is switched off by the server administrator.'),
                                   'company': tr('The company uses the built-in AI.'),
                                   'feature': tr('External AI is not switched on for this feature.'),
                                   'no key': tr('No API key is saved in Settings → AI settings.'),
                                   'key unreadable': tr('The saved API key cannot be read. Enter it again in Settings → AI settings.')}[why])
    cfg = config_for(row)
    if month_usage() >= row.monthly_limit:
        _log(request, feature, cfg, False, False, 'limit reached')
        raise ExternalUnavailable(tr('The monthly limit of {0} external AI calls is reached.', row.monthly_limit))
    return _send(request, feature, cfg, system, messages, tools, max_tokens)


def test_connection(request, row, override=None):
    """Settings screen: one tiny request with the saved values (or the ones being edited). Returns {ok, message, ms}."""
    from Translations.i18n import tr
    override = override or {}
    if not server_allows_external():
        return {'ok': False, 'message': tr('External AI is switched off by the server administrator.')}

    class _R:   # the values on the screen, falling back to the saved ones
        provider = override.get('provider') or row.provider
        api_url = override.get('api_url') if override.get('api_url') is not None else row.api_url
        model = override.get('model') if override.get('model') is not None else row.model
        api_key_enc = row.api_key_enc
    cfg = config_for(_R, api_key=(override.get('api_key') or '').strip() or None)
    if cfg['key'] is None:
        return {'ok': False, 'message': tr('The saved API key cannot be read. Enter it again.')}
    t0 = time.time()
    try:
        r = _send(request, 'test', cfg, 'You are a connection test.', [{'role': 'user', 'content': 'Reply with the word OK.'}], None, 16)
    except ExternalUnavailable as e:
        return {'ok': False, 'message': str(e)}
    text = ' '.join(b.get('text', '') for b in r.get('content') or [] if b.get('type') == 'text').strip()
    return {'ok': True, 'ms': int((time.time() - t0) * 1000),
            'message': tr('Connected. The AI answered in {0} ms.', int((time.time() - t0) * 1000)),
            'reply': text[:40]}
