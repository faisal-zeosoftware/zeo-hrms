"""v1.15.0 – language packs: the ONE place server code gets translations from (builder langdata).

    from Translations.i18n import tr
    raise ValidationError(tr('Leave balance is {0} days.', bal))

* `tr(text, *args, lang=None, **kw)` – the English text is the key (`{0}`, `{1}` or `{name}` placeholders); the translated
  template comes from `LanguagePack.services.t` (Term table of the language packs) and is filled here, so the
  placeholders work the same in every language. Missing term / English / LanguagePack not installed → the English text.
* `t(text, lang=None, **params)` – same signature as `LanguagePack.services.t`.
* `current_lang(request=None)` – the request language: header `X-Language`, else Django's active language (set by the
  LanguagePack middleware from the user's preference / company default), else `en`.
* `msg(text, *args)` – a message that is translated LATER, in the recipient's language (notifications, e-mails):
  it is a normal English `str` and also carries the template and the values.
* `lang_for_user(user)` – the language a user reads (their preference, else the company default, else `en`).
"""
import logging

from django.utils import translation

log = logging.getLogger(__name__)
SOURCE_LANG = 'en'


def _core_t():
    try:
        from LanguagePack.services import t as core_t
        return core_t
    except Exception:  # ImportError until the LanguagePack app exists / apps not ready
        return None


def norm(code):
    code = (code or '').strip().lower().replace('_', '-')
    return code.split('-')[0] if code else ''


def _request():
    try:
        from Chatter.tracking import current_request
        return current_request()
    except Exception:
        return None


def current_lang(request=None):
    """Request language: what LanguageMiddleware resolved (header → user → company default), else the X-Language header,
    else the language of LanguagePack.services.override() / Django's active language, else en."""
    req = request if request is not None else _request()
    if req is not None:
        base = getattr(req, '_request', req)
        v = getattr(base, 'zeo_lang', None)
        if v:
            return norm(v) or SOURCE_LANG
        try:
            h = base.META.get('HTTP_X_LANGUAGE')
        except Exception:
            h = None
        if h:
            return norm(h) or SOURCE_LANG
    try:
        from LanguagePack.services import current_lang as core_current
        v = norm(core_current())
        if v:
            return v
    except Exception:
        pass
    return norm(translation.get_language()) or SOURCE_LANG


def _lookup(text, lang):
    """Translated template of `text` (no placeholders filled)."""
    if not text or not isinstance(text, str) or not lang or lang == SOURCE_LANG:
        return text
    f = _core_t()
    if f is None:
        return text
    try:
        out = f(text, lang)
    except TypeError:
        try:
            out = f(text, lang=lang)
        except Exception:
            out = None
    except Exception:
        log.debug('translation lookup failed', exc_info=True)
        out = None
    return out if isinstance(out, str) and out else text


def _fill(template, original, args, kw):
    if not (args or kw):
        return template
    try:
        return template.format(*args, **kw)
    except Exception:
        try:
            return original.format(*args, **kw)
        except Exception:
            return template


def tr(text, *args, lang=None, **kw):
    """Translate an English source text (the key) into the request language and fill `{0}` / `{name}` placeholders."""
    if lang is None:
        lang = current_lang()
    lang = norm(lang) or SOURCE_LANG
    if args and lang != SOURCE_LANG:   # v1.15.0: "day{1}" / "entr{1}" carry an English plural ending – dropped in other languages
        import re as _re
        slots = {int(n) for n in _re.findall(r'[A-Za-z]\{(\d+)\}', text or '')}
        if slots:
            args = tuple('' if i in slots and _re.fullmatch(r'[a-z]{0,3}', str(a)) else a for i, a in enumerate(args))
    return _fill(_lookup(text, lang), text, args, kw)


def t(text, lang=None, **params):
    """Same signature as LanguagePack.services.t (named placeholders only)."""
    return tr(text, lang=lang, **params)


class Msg(str):
    """English text that remembers its template + values, so it can be translated later for its reader."""
    template = ''
    args = ()
    kw = {}

    def __new__(cls, template, *args, **kw):
        obj = super().__new__(cls, _fill(template, template, args, kw))
        obj.template, obj.args, obj.kw = template, args, kw
        return obj


def msg(text, *args, **kw):
    return Msg(text, *args, **kw)


def for_lang(text, lang):
    """A message (plain str or Msg) in the reader's language."""
    if not text or not isinstance(text, str):
        return text
    lang = norm(lang) or SOURCE_LANG
    if lang == SOURCE_LANG:
        return str(text)
    if isinstance(text, Msg):
        return tr(text.template, *text.args, lang=lang, **text.kw)
    return tr(text, lang=lang)


def company_default_lang():
    try:
        from django.db import connection
        if getattr(connection, 'schema_name', 'public') == 'public':
            return SOURCE_LANG
        from LanguagePack.services import company_default
        return norm(company_default()) or SOURCE_LANG
    except Exception:
        return SOURCE_LANG


def lang_for_user(user):
    """The language `user` reads notifications / e-mails / PDFs in: own choice → company default → en."""
    if user is None or not getattr(user, 'pk', None):
        return company_default_lang()
    try:
        from LanguagePack.services import user_language
        return norm(user_language(user)) or SOURCE_LANG
    except Exception:
        log.debug('user language lookup failed', exc_info=True)
    return company_default_lang()


def active_languages():
    """[{code, name, english_name, dir}] of the languages the company translates into (English, the source, not included)."""
    out = []
    try:
        from LanguagePack import services as LS
        for code in LS.company_languages():
            if norm(code) == SOURCE_LANG:
                continue
            r = LS.language(code) or {}
            out.append({'code': code, 'name': r.get('name') or code, 'english_name': r.get('english_name') or code,
                        'dir': r.get('dir') or ('rtl' if is_rtl(code) else 'ltr')})
    except Exception:
        log.debug('language list failed', exc_info=True)
        out = []
    if not out:
        out = [{'code': 'ar', 'name': 'العربية', 'english_name': 'Arabic', 'dir': 'rtl'}]
    return out


def is_rtl(lang):
    return norm(lang) in ('ar', 'fa', 'ur', 'he')


def for_reader(text, user=None, employee=None):
    """A notification text (str or msg()) in the language of its reader (user, else the employee's login)."""
    if not text or not isinstance(text, str):
        return text
    try:
        if user is None and employee is not None:
            user = getattr(employee, 'users', None)
        return for_lang(text, lang_for_user(user))
    except Exception:
        return str(text)
