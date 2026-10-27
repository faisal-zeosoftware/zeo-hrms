"""v1.62.0 print – samples, copies, versions, defaults and rendering.

Everything here works in the schema of the current company: samples are seeded per company (read only; a product update may
refresh them with `seed_print_templates --refresh`), the company's own copies, defaults and branding are never touched by a seed.
"""
import base64
import hashlib
import logging
import os

from django.conf import settings as dj
from django.db import connection, transaction

from . import contexts as C
from . import designs as D
from .engine import PrintError, available, html_to_pdf, pdf_pages, pdf_to_png, render_html
from .models import PrintBranding, PrintDefault, PrintTemplate, PrintTemplateVersion

log = logging.getLogger(__name__)
SAMPLE_VERSION = 7   # raise when the sample designs of the code change (seed --refresh brings them to every company)
LANG_OF_LOOK = {'bilingual': 'bi', 'arabic': 'ar'}


# ------------------------------------------------------------------ samples
def sample_defs():
    for key, en, ar, family, perms, icon in C.DOC_TYPES:
        looks = ['classic', 'modern', 'compact', 'bilingual'] + (['arabic'] if key in D.ARABIC_DOCS else [])
        for look in looks:
            yield key, look, LANG_OF_LOOK.get(look, 'en')


def _sample_fields(doc_type, look, lang):
    lk = D.LOOKS[look]
    return {'name': lk['name'], 'description': lk['description'], 'language': lang, 'mode': 'blocks',
            'settings': dict(D.defaults(doc_type, look), sample_version=SAMPLE_VERSION), 'html': '', 'css': ''}


def seed(refresh=False):
    """Create the missing samples of this company (and bring old samples up to date with refresh=True). Company copies, defaults
    and branding are never changed. Returns (created, refreshed)."""
    made = upd = 0
    have = {(t.doc_type, t.sample_key): t for t in PrintTemplate.objects.filter(is_sample=True)}
    for doc_type, look, lang in sample_defs():
        f = _sample_fields(doc_type, look, lang)
        t = have.get((doc_type, look))
        if t is None:
            PrintTemplate.objects.create(doc_type=doc_type, is_sample=True, sample_key=look, **f)
            made += 1
        elif refresh or (t.settings or {}).get('sample_version', 0) < SAMPLE_VERSION:
            for k, v in f.items():
                setattr(t, k, v)
            t.version += 1
            t.save()
            upd += 1
    PrintBranding.get()
    return made, upd


_SEEDED = set()


def ensure_seeded():
    s = connection.schema_name
    if s in _SEEDED:
        return
    try:
        seed()
        _SEEDED.add(s)
    except Exception:   # noqa: BLE001 – the table may be missing in a company that was not migrated yet
        log.exception('print templates seed in %s', s)


def set_demo_default(doc_type='payslip', look='modern'):
    t = PrintTemplate.objects.filter(is_sample=True, doc_type=doc_type, sample_key=look).first()
    if t and not PrintDefault.objects.filter(doc_type=doc_type, branch_id__isnull=True, language='').exists():
        PrintDefault.objects.create(doc_type=doc_type, template=t)
        return True
    return False


# ------------------------------------------------------------------ copies, versions
def snapshot(t, user_id=None, note=''):
    return PrintTemplateVersion.objects.create(template=t, version=t.version, name=t.name, language=t.language, mode=t.mode, settings=t.settings,
                                               html=t.html, css=t.css, note=(note or '')[:200], saved_by_id=user_id)


@transaction.atomic
def copy(src, user_id=None, name=None):
    t = PrintTemplate.objects.create(doc_type=src.doc_type, name=(name or f'{src.name} (copy)')[:120], description=src.description,
                                     is_sample=False, sample_key=src.sample_key, language=src.language, mode=src.mode,
                                     settings={k: v for k, v in (src.settings or {}).items() if k != 'sample_version'},
                                     html=src.html, css=src.css, version=1, copied_from=src, created_by_id=user_id, updated_by_id=user_id)
    snapshot(t, user_id, f'Copied from {src.name}')
    return t


EDITABLE = ('name', 'description', 'language', 'mode', 'settings', 'html', 'css')


@transaction.atomic
def save(t, data, user_id=None):
    if t.is_sample:
        raise PrintError('The samples of ZEO cannot be changed – make a copy and edit the copy.')
    changed = False
    for k in EDITABLE:
        if k in data and data[k] is not None and getattr(t, k) != data[k]:
            v = data[k]
            if k == 'mode' and v not in ('blocks', 'html'):
                raise PrintError('Mode must be blocks or html.')
            if k == 'language' and v not in ('en', 'ar', 'bi'):
                raise PrintError('Language must be en, ar or bi.')
            if k == 'name':
                v = str(v).strip()[:120]
                if not v:
                    raise PrintError('Give the template a name.')
            if k == 'settings' and not isinstance(v, dict):
                raise PrintError('Settings must be an object.')
            setattr(t, k, v)
            changed = True
    if t.mode == 'html':
        from .engine import check_source
        check_source(t.html)
    if changed:
        t.version += 1
        t.updated_by_id = user_id
        t.save()
        snapshot(t, user_id, data.get('note') or '')
    return t


@transaction.atomic
def restore(t, version, user_id=None):
    if t.is_sample:
        raise PrintError('The samples of ZEO cannot be changed.')
    v = t.versions.filter(version=version).first()
    if v is None:
        raise PrintError('That version does not exist.')
    t.name, t.language, t.mode, t.settings, t.html, t.css = v.name, v.language, v.mode, v.settings, v.html, v.css
    t.version += 1
    t.updated_by_id = user_id
    t.save()
    snapshot(t, user_id, f'Restored version {version}')
    return t


def reset_sample(t):
    if not t.is_sample:
        raise PrintError('Only a sample can be reset.')
    f = dict(_sample_fields(t.doc_type, t.sample_key, LANG_OF_LOOK.get(t.sample_key, 'en')))
    for k, v in f.items():
        setattr(t, k, v)
    t.version += 1
    t.save()
    return t


# ------------------------------------------------------------------ defaults
COMPAT = {'en': ('en', 'bi'), 'ar': ('ar', 'bi'), 'bi': ('bi',)}


def default_for(doc_type, branch_id=None, lang=None):
    rows = list(PrintDefault.objects.filter(doc_type=doc_type).select_related('template'))
    ok = COMPAT.get(lang) if lang else None
    order = ([(branch_id, lang or ''), (branch_id, '')] if branch_id else []) + [(None, lang or ''), (None, '')]
    for b, l in order:
        for r in rows:
            if r.branch_id == b and r.language == l and (ok is None or r.template.language in ok):
                return r.template
    return None


def set_default(doc_type, t, branch_id=None, language='', user_id=None):
    if t.doc_type != doc_type:
        raise PrintError('The template is of another document type.')
    if language not in ('', 'en', 'ar'):
        raise PrintError('Language must be empty, en or ar.')
    row = PrintDefault.objects.filter(doc_type=doc_type, language=language, **({'branch_id': branch_id} if branch_id else {'branch_id__isnull': True})).first()
    if row is None:
        row = PrintDefault(doc_type=doc_type, branch_id=branch_id or None, language=language)
    row.template, row.updated_by_id = t, user_id
    row.save()
    return row


# ------------------------------------------------------------------ rendering
def sources(t_or_dict):
    """(html, css) of a template (or of unsaved editor data {doc_type, mode, settings, html, css, language})."""
    g = (lambda k, dflt=None: t_or_dict.get(k, dflt)) if isinstance(t_or_dict, dict) else (lambda k, dflt=None: getattr(t_or_dict, k, dflt))
    if g('mode') == 'html':
        return g('html') or '', g('css') or ''
    return D.compose(g('doc_type'), g('settings') or {}, g('language') or 'en')


def editable_css(t):
    """The CSS the HTML tab starts with (base + look)."""
    return D.BASE_CSS.strip() + '\n' + D.LOOK_CSS.get((t.settings or {}).get('look', 'classic'), '')


def document(t_or_dict, ctx):
    g = (lambda k, dflt=None: t_or_dict.get(k, dflt)) if isinstance(t_or_dict, dict) else (lambda k, dflt=None: getattr(t_or_dict, k, dflt))
    html_src, css = sources(t_or_dict)
    lang = g('language') or 'en'
    body = render_html(html_src, ctx)
    settings = g('settings') or {}
    pcss = D.page_css(settings, ctx.get('brand') or {}, lang)
    if g('mode') != 'html':
        css = D.BASE_CSS + css
    title = (ctx.get('doc') or {}).get('title', '')
    from django.utils.html import escape
    html = (f'<!doctype html><html lang="{"ar" if lang == "ar" else "en"}" dir="{"rtl" if lang == "ar" else "ltr"}"><head><meta charset="utf-8">'
            f'<title>{escape(title)}</title></head><body>{body}</body></html>')
    return html, pcss + '\n' + css


def pdf(t_or_dict, ctx):
    html, css = document(t_or_dict, ctx)
    return html_to_pdf(html, css)


def lang_of(t_or_dict):
    return (t_or_dict.get('language') if isinstance(t_or_dict, dict) else t_or_dict.language) or 'en'


def context_for(doc_type, t_or_dict, obj=None, request=None):
    lang = lang_of(t_or_dict)
    if obj is None:
        from .fixtures import sample
        return sample(doc_type, lang)
    return C.build(doc_type, obj, lang, request)


def render(doc_type, obj, request=None, lang=None, template=None, branch_id=None):
    """PDF bytes of a record with the template of its type (default of the branch / language), or None when the company has no
    default for it or the engine is not installed (the caller then makes its old PDF)."""
    if not available():
        return None
    ensure_seeded()
    if branch_id is None:
        branch_id = C.branch_of(doc_type, obj)
    t = template or default_for(doc_type, branch_id, lang)
    if t is None:
        return None
    return pdf(t, context_for(doc_type, t, obj, request))


# ------------------------------------------------------------------ thumbnails (made-up data, the company's branding)
def cache_dir():
    d = getattr(dj, 'ZEO_PRINT_CACHE', '') or os.path.join(dj.BASE_DIR, 'print_cache')
    d = os.path.join(d, connection.schema_name)
    os.makedirs(d, exist_ok=True)
    return d


def thumb(t, width=420):
    b = PrintBranding.get()
    key = hashlib.sha1(f'{t.pk}-{t.version}-{t.updated_at.timestamp()}-{b.updated_at.timestamp()}-{SAMPLE_VERSION}-{width}'.encode()).hexdigest()[:20]
    p = os.path.join(cache_dir(), f't{t.pk}-{key}.png')
    if os.path.exists(p):
        with open(p, 'rb') as fh:
            return fh.read()
    try:
        png = pdf_to_png(pdf(t, context_for(t.doc_type, t)), width, 1)[0]
    except PrintError as e:
        png = _error_png(e.message, width)
    for old in os.listdir(os.path.dirname(p)):
        if old.startswith(f't{t.pk}-') and old != os.path.basename(p):
            try:
                os.remove(os.path.join(os.path.dirname(p), old))
            except OSError:
                pass
    with open(p, 'wb') as fh:
        fh.write(png)
    return png


def _error_png(msg, width):
    try:
        from PIL import Image, ImageDraw
        h = int(width * 1.414)
        img = Image.new('RGB', (width, h), '#fff4f4')
        dr = ImageDraw.Draw(img)
        dr.multiline_text((16, 16), 'Template error:\n' + '\n'.join(msg[i:i + 40] for i in range(0, min(len(msg), 240), 40)), fill='#a11')
        import io
        b = io.BytesIO()
        img.save(b, 'PNG')
        return b.getvalue()
    except Exception:   # noqa: BLE001
        return b''


def preview(t_or_dict, obj=None, request=None, width=900, pages=6):
    """{ok, pages: [base64 PNG], page_count} or {ok: false, error, line} – never raises for a template mistake."""
    doc_type = t_or_dict['doc_type'] if isinstance(t_or_dict, dict) else t_or_dict.doc_type
    try:
        data = pdf(t_or_dict, context_for(doc_type, t_or_dict, obj, request))
        pngs = pdf_to_png(data, width, pages)
        return {'ok': True, 'pages': [base64.b64encode(x).decode() for x in pngs], 'page_count': pdf_pages(data)}
    except PrintError as e:
        return {'ok': False, 'error': e.message, 'line': e.line}
    except Exception as e:   # noqa: BLE001
        log.exception('print preview')
        return {'ok': False, 'error': f'The preview could not be made: {str(e)[:200]}', 'line': None}


def placeholders(doc_type, lang='en'):
    """Flat list of the context of a document type for the placeholder picker: [{path, example, loop?}]."""
    from .fixtures import sample
    ctx = sample(doc_type, lang)
    out = []

    def walk(prefix, v):
        if isinstance(v, dict):
            for k, x in v.items():
                walk(f'{prefix}.{k}' if prefix else k, x)
        elif isinstance(v, list):
            item = v[0] if v else None
            var = prefix.split('.')[-1][:1] or 'x'
            if isinstance(item, dict):
                out.append({'path': prefix, 'loop': True, 'fields': list(item.keys()), 'var': var,
                            'snippet': f'{{% for {var} in {prefix} %}}' + ' '.join(f'{{{{ {var}.{k} }}}}' for k in item.keys()) + '{% endfor %}'})
            else:
                out.append({'path': prefix, 'loop': True, 'fields': [], 'var': var, 'snippet': f'{{% for {var} in {prefix} %}}{{{{ {var} }}}}{{% endfor %}}'})
        else:
            s = '' if v is None else str(v)
            out.append({'path': prefix, 'example': ('(image)' if s.startswith('data:') else s[:60]), 'snippet': '{{ ' + prefix + ' }}'})
    for k in ('company', 'employee', 'doc', 'brand'):
        walk(k, ctx.get(k))
    for k, v in ctx.items():
        if k not in ('company', 'employee', 'doc', 'brand', 'lang', 'dir'):
            walk(k, v)
    return out
