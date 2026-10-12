"""v1.15.0 – applied once at start-up (TranslationsConfig.ready), like AccessControl / DataTools do:

* ModelSerializer.to_representation – GET responses of the translatable masters (registry.py) show the name in the request
  language (header X-Language / the user's language). Never on writes; `?raw=1` returns the originals.
* ModelSerializer.run_validation – a PUT / PATCH that sends back the translated name unchanged keeps the English original.
* APIView.finalize_response – messages (`detail`, `message`, `non_field_errors`, field errors) of every API response are
  translated through the language packs when the English text is a known term (the apps' own messages are also wrapped in
  `tr()` where they are built); lists of names (department "branch": [...]) are translated for display.
"""
import logging
import os

from rest_framework import serializers

log = logging.getLogger(__name__)
_installed = False
MESSAGE_KEYS = ('detail', 'message', 'error', 'errors', 'non_field_errors', 'warning', 'warnings')


def _tr_leaf(s, lang):
    from .i18n import tr
    if not isinstance(s, str) or not s.strip():
        return s
    out = tr(s, lang=lang)
    if out is s or out == s:
        return s
    if isinstance(s, serializers.ErrorDetail):
        return serializers.ErrorDetail(out, code=s.code)
    return out


def translate_messages(data, lang, error=False, depth=0):
    """Error bodies: every string leaf; other bodies: only the message keys."""
    if depth > 5:
        return data
    if isinstance(data, list):
        return [translate_messages(x, lang, error, depth + 1) if isinstance(x, (list, dict)) else (_tr_leaf(x, lang) if error else x) for x in data]
    if isinstance(data, dict):
        out = data if not isinstance(data, serializers.ReturnDict) else data
        for k, v in list(data.items()):
            if error or k in MESSAGE_KEYS:
                if isinstance(v, str):
                    out[k] = _tr_leaf(v, lang)
                elif isinstance(v, (list, dict)):
                    out[k] = translate_messages(v, lang, True, depth + 1)
        return out
    if isinstance(data, str) and error:
        return _tr_leaf(data, lang)
    return data


def install():
    global _installed
    if _installed or os.environ.get('ZEO_TRANSLATIONS_OFF'):
        return
    from rest_framework.views import APIView
    from . import services as S

    orig_rep = serializers.ModelSerializer.to_representation
    orig_val = serializers.ModelSerializer.run_validation
    orig_fin = APIView.finalize_response

    def to_representation(self, instance):
        rep = orig_rep(self, instance)
        try:
            return S.swap_representation(self, instance, rep)
        except Exception:
            log.debug('translation swap failed', exc_info=True)
            return rep

    def run_validation(self, data=serializers.empty):
        if data is not serializers.empty:
            try:
                data = S.guard_write(self, data)
            except Exception:
                log.debug('translation write guard failed', exc_info=True)
        return orig_val(self, data)

    def finalize_response(self, request, response, *args, **kwargs):
        response = orig_fin(self, request, response, *args, **kwargs)
        try:
            data = getattr(response, 'data', None)
            if data is None or getattr(response, '_is_rendered', False):
                return response
            from .i18n import current_lang, SOURCE_LANG
            lang = current_lang(getattr(request, '_request', request))
            if not lang or lang == SOURCE_LANG:
                return response
            if isinstance(data, (dict, list)):
                response.data = translate_messages(data, lang, error=response.status_code >= 400)
                rl = S.read_lang(request)
                if rl and response.status_code < 400:
                    S.swap_values(response.data, rl, request)
        except Exception:
            log.debug('translation of the response failed', exc_info=True)
        return response

    serializers.ModelSerializer.to_representation = to_representation
    serializers.ModelSerializer.run_validation = run_validation
    APIView.finalize_response = finalize_response
    _installed = True
