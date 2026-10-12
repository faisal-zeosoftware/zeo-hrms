"""v1.15.0 – Language packs: translation helper, caches, language resolution and the pack loader.

Use in Python code (other apps import lazily, the app may be missing on old servers):

    from LanguagePack.services import t
    raise ValidationError(t('Choose a leave type.'))
    t('{0} of {1} days used', None, [used, total])          # positional {0} {1}
    t('Hello {name}', name=user.first_name)                  # named placeholders
    t('Payslip', 'ar')                                       # explicit language

`t()` returns the English text (with the placeholders filled) when the language is English, unknown, or the term has no
translation yet. The request language comes from LanguageMiddleware (X-Language header → user → company default → en);
outside a request it is English unless `with override('ar'):` is used (e-mails / PDFs for a given recipient).
"""
import contextlib
import contextvars
import json
import os
import re
import threading
import time

from django.conf import settings
from django.core.cache import cache
from django.db import connection

SOURCE_LANG = 'en'
WS = re.compile(r'\s+')
PH = re.compile(r'\{(\w+)\}')
CACHE_VER_KEY = 'langpack:ver'
LOCAL_CHECK_SECONDS = 2.0
PUBLIC_MODULE_PREFIXES = ('src/app/login/', 'src/app/company-selection/')
PUBLIC_TERMS = {'Language', 'English', 'Arabic'}       # the language picker on the login page

_current = contextvars.ContextVar('zeo_lang', default=None)
_lock = threading.Lock()
_local = {'ver': None, 'checked': 0.0, 'terms': {}, 'langs': None}


# ----------------------------------------------------------------------------------------------------------------- basics
def norm(text):
    """Key form of a text: trimmed, inner whitespace collapsed (like the extractor)."""
    return WS.sub(' ', str(text or '')).strip()


def base_lang(code):
    """'ar-AE' / 'ar_ae' / 'AR' → 'ar'; '' / None → ''."""
    c = str(code or '').strip().lower().replace('_', '-')
    return c.split('-')[0] if c else ''


def placeholders(text):
    return sorted(set(PH.findall(text or '')))


def fill(text, args=None, params=None):
    """Replace {0} / {name} placeholders; unknown ones stay as they are."""
    if not args and not params:
        return text
    args = list(args or [])
    params = params or {}

    def rep(m):
        k = m.group(1)
        if k.isdigit():
            i = int(k)
            return str(args[i]) if i < len(args) else m.group(0)
        return str(params[k]) if k in params else m.group(0)
    return PH.sub(rep, text)


def module_of(where):
    """Module of a term from its first reference: frontend folder under src/app (leave-policy …) or backend app (LeavePolicy)."""
    for w in where or []:
        f = str(w).rsplit(':', 1)[0]
        if f.startswith('src/app/'):
            parts = f.split('/')
            return parts[2] if len(parts) > 3 else 'app'
        return f.split('/')[0] if '/' in f else 'backend'
    return ''


def is_public(where, text=''):
    return text in PUBLIC_TERMS or any(str(w).startswith(PUBLIC_MODULE_PREFIXES) for w in where or [])


# ------------------------------------------------------------------------------------------------------------------ cache
def _version():
    v = cache.get(CACHE_VER_KEY)
    if v is None:
        v = int(time.time() * 1000)
        cache.set(CACHE_VER_KEY, v, None)
    return v


def _db_version():
    """Sum of the pack versions: every term change bumps its language, so other server processes notice it even when the
    cache is per process (ZEO_REDIS_CACHE_URL=locmem). One tiny query every 2 seconds at most."""
    try:
        from django.db.models import Count, Sum
        from .models import Language
        r = Language.objects.aggregate(s=Sum('version'), n=Count('id'))
        return (r['s'] or 0, r['n'] or 0)
    except Exception:
        return None


def clear_cache(lang=None):
    """Forget every cached pack (all server processes see it through the shared cache within 2 seconds)."""
    try:
        cache.set(CACHE_VER_KEY, int(time.time() * 1000) + 1, None)
    except Exception:
        pass
    with _lock:
        _local['ver'] = None
        _local['checked'] = 0.0
        _local['terms'] = {}
        _local['langs'] = None


def _fresh():
    now = time.monotonic()
    if now - _local['checked'] < LOCAL_CHECK_SECONDS and _local['ver'] is not None:
        return
    v = (_version(), _db_version())
    with _lock:
        _local['checked'] = now
        if v != _local['ver']:
            _local['ver'] = v
            _local['terms'] = {}
            _local['langs'] = None


def _languages_all():
    _fresh()
    if _local['langs'] is None:
        from .models import Language
        try:
            rows = list(Language.objects.all().values('code', 'name', 'english_name', 'dir', 'active', 'date_format',
                                                         'number', 'version', 'sequence'))
        except Exception:      # table missing (app not migrated yet)
            rows = []
        if not any(r['code'] == SOURCE_LANG for r in rows):
            rows.insert(0, {'code': 'en', 'name': 'English', 'english_name': 'English', 'dir': 'ltr', 'active': True,
                            'date_format': 'dd/MM/yyyy', 'number': {'decimal': '.', 'group': ','}, 'version': 1, 'sequence': 0})
        _local['langs'] = {r['code']: r for r in rows}
    return _local['langs']


def language(code):
    return _languages_all().get(base_lang(code))


def active_codes():
    return [c for c, r in _languages_all().items() if r['active']]


def terms(lang):
    """{english: translation} of a language (translated terms only), cached per process."""
    lang = base_lang(lang)
    if not lang:
        return {}
    _fresh()
    d = _local['terms'].get(lang)
    if d is None:
        from .models import Term
        try:
            d = dict(Term.objects.filter(lang=lang).exclude(value='').values_list('source', 'value'))
        except Exception:
            d = {}
        with _lock:
            _local['terms'][lang] = d
    return d


# -------------------------------------------------------------------------------------------------------------- translate
def current_lang():
    v = _current.get()
    if v:
        return v
    try:
        from django.utils import translation
        return base_lang(translation.get_language()) or SOURCE_LANG
    except Exception:
        return SOURCE_LANG


def t(text, lang=None, args=None, **params):
    """Translate an English source text into `lang` (default: the request language) and fill its placeholders."""
    if text is None:
        return ''
    s = str(text)
    code = base_lang(lang) if lang else current_lang()
    if code and code != SOURCE_LANG:
        d = terms(code)
        if d:
            v = d.get(s)
            if v is None:
                k = norm(s)
                v = d.get(k)
            if v:
                s = v
    return fill(s, args, params)


def t_lazy(text, **params):
    """Lazy version for module-level constants (translated when converted to str)."""
    from django.utils.functional import lazy
    return lazy(lambda: t(text, **params), str)()


@contextlib.contextmanager
def override(lang):
    """`with override('ar'):` – t() (and Django's own messages) in that language inside the block."""
    code = base_lang(lang) or SOURCE_LANG
    tok = _current.set(code)
    try:
        from django.utils import translation
        with translation.override(code):
            yield code
    finally:
        _current.reset(tok)


def set_current(code):
    return _current.set(base_lang(code) or SOURCE_LANG)


def reset_current(token):
    try:
        _current.reset(token)
    except Exception:
        _current.set(None)


# -------------------------------------------------------------------------------------------------- user / company language
def company_settings(schema=None):
    """{'default_lang', 'active_langs'} of a company (schema name; default: the active schema)."""
    schema = schema or getattr(connection, 'schema_name', 'public') or 'public'
    key = f'langpack:company:{schema}'
    v = cache.get(key)
    if v is None:
        from .models import CompanyLanguageSettings
        row = CompanyLanguageSettings.objects.filter(schema_name=schema).first() if schema != 'public' else None
        v = {'default_lang': (row.default_lang if row else SOURCE_LANG) or SOURCE_LANG,
             'active_langs': list(row.active_langs or []) if row else []}
        cache.set(key, v, 60)
    return v


def company_languages(schema=None):
    """Language codes a company offers: its own list (English always included) or every active language."""
    allc = active_codes()
    own = company_settings(schema)['active_langs']
    codes = [c for c in allc if c in own or c == SOURCE_LANG] if own else allc
    return codes or [SOURCE_LANG]


def company_default(schema=None):
    d = company_settings(schema)['default_lang']
    return d if d in company_languages(schema) else SOURCE_LANG


def user_pref(user_id):
    if not user_id:
        return None
    key = f'langpack:user:{user_id}'
    v = cache.get(key)
    if v is None:
        from .models import UserLanguage
        row = UserLanguage.objects.filter(user_id=user_id).first()
        v = row.lang if row else ''
        cache.set(key, v, 60)
    return v or None


def set_user_pref(user_id, lang):
    from .models import UserLanguage
    if lang:
        UserLanguage.objects.update_or_create(user_id=user_id, defaults={'lang': lang})
    else:
        UserLanguage.objects.filter(user_id=user_id).delete()
    cache.delete(f'langpack:user:{user_id}')


def forget_company(schema):
    cache.delete(f'langpack:company:{schema}')


def user_language(user, schema=None):
    """Language of a user (e.g. the recipient of an e-mail): own choice → company default → en."""
    uid = getattr(user, 'pk', user)
    allowed = company_languages(schema)
    p = user_pref(uid)
    if p and p in allowed:
        return p
    return company_default(schema)


def resolve(header_lang=None, user_id=None, schema=None):
    """(lang, source): X-Language header → user preference → company default → en."""
    allowed = company_languages(schema)
    h = base_lang(header_lang)
    if h and h in allowed:
        return h, 'header'
    p = user_pref(user_id) if user_id else None
    if p and p in allowed:
        return p, 'user'
    d = company_default(schema)
    if d and d != SOURCE_LANG:
        return d, 'company'
    return SOURCE_LANG, 'default'


# ------------------------------------------------------------------------------------------------------------------- packs
def repo_dir():
    return getattr(settings, 'LANGUAGE_PACK_DIR', None) or os.path.join(settings.BASE_DIR, 'i18n')


def pack_payload(code, public_only=False):
    """What GET pack/<lang>/ returns."""
    code = base_lang(code)
    L = language(code)
    if not L:
        return None
    d = terms(code)
    if public_only:
        from .models import Term
        keep = set(Term.objects.filter(lang=code, public=True).exclude(value='').values_list('source', flat=True))
        d = {k: v for k, v in d.items() if k in keep}
    return {'lang': code, 'name': L['name'], 'english_name': L['english_name'], 'dir': L['dir'],
            'date_format': L['date_format'], 'number': L['number'] or {'decimal': '.', 'group': ','},
            'version': L['version'], 'terms': d, 'partial': bool(public_only)}


def bump(lang):
    """A term of `lang` changed: new pack version (browser caches reload) and fresh server caches."""
    from django.db.models import F
    from .models import Language
    Language.objects.filter(code=base_lang(lang)).update(version=F('version') + 1)
    clear_cache(lang)


def read_source(path=None):
    path = path or os.path.join(repo_dir(), 'source.json')
    if not os.path.exists(path):
        return []
    with open(path, encoding='utf-8') as fh:
        return json.load(fh)


def load_packs(force=False, only=None, pack_dir=None, source_path=None, log=None):
    """Load i18n/packs/*.json (+ i18n/source.json) into Language / Term. Idempotent: a term edited on the Translations
    screen is kept unless force=True. Every source text gets a row per non-English language (empty value = missing)."""
    from django.db import transaction
    from .models import Language, Term, source_hash
    pack_dir = pack_dir or os.path.join(repo_dir(), 'packs')
    src = read_source(source_path)
    where_of = {norm(x['text']): x.get('where') or [] for x in src}
    stats = {}
    files = sorted(f for f in os.listdir(pack_dir) if f.endswith('.json')) if os.path.isdir(pack_dir) else []
    for f in files:
        with open(os.path.join(pack_dir, f), encoding='utf-8') as fh:
            p = json.load(fh)
        code = base_lang(p.get('lang') or f[:-5])
        if only and code not in only:
            continue
        st = {'added': 0, 'updated': 0, 'kept_edited': 0, 'unchanged': 0, 'missing': 0}
        with transaction.atomic():
            L, created = Language.objects.get_or_create(code=code, defaults={
                'name': p.get('name') or code, 'english_name': p.get('english_name') or code})
            L.name = p.get('name') or L.name
            L.english_name = p.get('english_name') or L.english_name
            L.dir = 'rtl' if p.get('dir') == 'rtl' else 'ltr'
            L.date_format = p.get('date_format') or L.date_format
            L.number = p.get('number') or L.number or {'decimal': '.', 'group': ','}
            if 'active' in p:
                L.active = bool(p['active'])
            L.sequence = 0 if code == SOURCE_LANG else (p.get('sequence') or L.sequence)
            L.save()
            if code == SOURCE_LANG and not p.get('terms'):
                stats[code] = st
                continue
            pack_terms = {norm(k): (v or '') for k, v in (p.get('terms') or {}).items() if norm(k)}
            existing = {r.source_hash: r for r in Term.objects.filter(lang=code)}
            wanted = dict.fromkeys(list(where_of) + [k for k in pack_terms if k not in where_of])
            new_rows, changed = [], []
            for text in wanted:
                h = source_hash(text)
                wh = where_of.get(text, [])
                val = pack_terms.get(text, '')
                mod = module_of(wh)
                pub = is_public(wh, text)
                row = existing.pop(h, None)
                if row is None:
                    new_rows.append(Term(lang=code, source=text, source_hash=h, value=val, where=wh, module=mod,
                                         in_source=text in where_of, public=pub))
                    st['added'] += 1
                    if not val:
                        st['missing'] += 1
                    continue
                dirty = False
                if row.where != wh or row.module != mod or row.in_source != (text in where_of) or row.public != pub:
                    row.where, row.module, row.in_source, row.public = wh, mod, text in where_of, pub
                    dirty = True
                if val and row.value != val:
                    if row.edited and not force:
                        st['kept_edited'] += 1
                    else:
                        row.value, row.edited = val, False
                        if force:
                            row.is_reviewed = False
                        dirty = True
                        st['updated'] += 1
                elif not dirty:
                    st['unchanged'] += 1
                if not row.value:
                    st['missing'] += 1
                if dirty:
                    changed.append(row)
            Term.objects.bulk_create(new_rows, batch_size=1000)
            if changed:
                Term.objects.bulk_update(changed, ['where', 'module', 'in_source', 'public', 'value', 'edited', 'is_reviewed'],
                                         batch_size=1000)
            # texts no longer in the code: keep the translation, mark them
            gone = [r.pk for r in existing.values() if r.in_source]
            if gone and src:
                Term.objects.filter(pk__in=gone).update(in_source=False)
            if new_rows or changed:
                L.version += 1
                L.save(update_fields=['version'])
        stats[code] = st
        if log:
            log(f'{code}: {st}')
    clear_cache()
    return stats
