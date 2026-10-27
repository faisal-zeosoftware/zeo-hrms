"""v1.62.0 print – the template engine: a restricted Django template engine (HTML) + WeasyPrint (PDF) + pypdfium2 (PNG previews).

Sandbox
* only these tags: if, for, with, cycle, firstof, now, spaceless, comment, verbatim, widthratio, ifchanged, regroup, filter,
  templatetag, resetcycle – no {% load %}, {% include %}, {% extends %}, {% url %}, {% debug %}, {% csrf_token %}, {% autoescape %};
* the default filters without safe / safeseq / pprint / ljust / rjust / center / force_escape-off tricks; autoescape is always on;
* the context holds plain data only (dicts, lists, text, numbers, dates) – never a model, a request or the settings;
* time limits for the HTML (5 s) and the PDF (30 s); the HTML may not be larger than 2 MB;
* WeasyPrint may read only the bundled fonts and data: URIs (images are embedded by the context builder) – no file:// or http(s)://
  address of a template is fetched, so a template cannot attach or show a file of the server.
"""
import base64
import glob
import io
import logging
import os
import re
import threading

log = logging.getLogger(__name__)
logging.getLogger('weasyprint').setLevel(logging.ERROR)   # fontTools subsetting notes
logging.getLogger('fontTools').setLevel(logging.ERROR)
HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(HERE, 'fonts')
NASKH_DIR = os.path.join(os.path.dirname(HERE), 'Translations', 'fonts')
MAX_HTML = 2 * 1024 * 1024
HTML_SECONDS = 5
PDF_SECONDS = 30

_RANGES_UNUSED = {
    'arabic': 'U+0600-06FF, U+0750-077F, U+0870-088E, U+0890-0891, U+0898-08E1, U+08E3-08FF, U+200C-200E, U+2010-2011, U+204F, U+2E41, U+FB50-FDFF, U+FE70-FE74, U+FE76-FEFC',
    'latin': 'U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD',
    'latin-ext': 'U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7, U+02DD-02FF, U+0304, U+0308, U+0329, U+1D00-1DBF, U+1E00-1E9F, U+1EF2-1EFF, U+2020, U+20A0-20AB, U+20AD-20C0, U+2113, U+2C60-2C7F, U+A720-A7FF',
}
# family → file prefix in fonts/ (fontsource subsets, SIL Open Font License – see fonts/LICENSE-*.txt)
FONTS = {
    'Inter': 'inter', 'Lato': 'lato', 'Source Serif 4': 'source-serif-4',
    'Cairo': 'cairo', 'Tajawal': 'tajawal', 'Amiri': 'amiri', 'Noto Sans Arabic': 'noto-sans-arabic',
}
FONT_CHOICES = [
    {'key': 'Inter', 'label': 'Inter (modern sans)', 'arabic': False},
    {'key': 'Lato', 'label': 'Lato (friendly sans)', 'arabic': False},
    {'key': 'Source Serif 4', 'label': 'Source Serif (classic serif)', 'arabic': False},
    {'key': 'Cairo', 'label': 'Cairo (Arabic + Latin)', 'arabic': True},
    {'key': 'Tajawal', 'label': 'Tajawal (Arabic + Latin)', 'arabic': True},
    {'key': 'Amiri', 'label': 'Amiri (Arabic + Latin, serif)', 'arabic': True},
    {'key': 'Noto Sans Arabic', 'label': 'Noto Sans Arabic', 'arabic': True},
    {'key': 'Noto Naskh Arabic', 'label': 'Noto Naskh Arabic', 'arabic': True},
]
FONT_KEYS = {f['key'] for f in FONT_CHOICES}


class PrintError(Exception):
    """A template problem the user can fix (shown as the message)."""

    def __init__(self, message, line=None):
        super().__init__(message)
        self.message = message
        self.line = line


# ------------------------------------------------------------------ fonts
SUB_SUFFIX = {'latin': '', 'latin-ext': ' Ext', 'arabic': ' Arabic'}


def _subsets(fam):
    pre = FONTS.get(fam)
    if not pre:
        return []
    out = {}
    for path in sorted(glob.glob(os.path.join(FONT_DIR, f'{pre}-*-normal.woff'))):
        m = re.match(rf'{re.escape(pre)}-(arabic|latin-ext|latin)-(\d+)-normal\.woff$', os.path.basename(path))
        if m:
            out.setdefault(m.group(1), []).append((m.group(2), path))
    return out


def family_stack(fam):
    """CSS font-family list of a bundled font: one family per subset ('Cairo', 'Cairo Arabic', 'Cairo Ext') – WeasyPrint picks the
    one that has the character (several @font-face rules of one family with unicode-range mixed up the glyphs)."""
    if fam == 'Noto Naskh Arabic' or fam not in FONTS:
        return f"'{fam}'"
    subs = _subsets(fam)
    order = ['latin', 'arabic', 'latin-ext']
    return ', '.join(f"'{fam}{SUB_SUFFIX[x]}'" for x in order if x in subs) or f"'{fam}'"


def font_faces(families):
    out = []
    for fam in families:
        if fam == 'Noto Naskh Arabic':
            for w, f in ((400, 'NotoNaskhArabic-Regular.ttf'), (700, 'NotoNaskhArabic-Bold.ttf')):
                p = os.path.join(NASKH_DIR, f)
                if os.path.exists(p):
                    out.append(f"@font-face{{font-family:'Noto Naskh Arabic';font-weight:{w};src:url('file://{p}')}}")
            continue
        for sub, files in _subsets(fam).items():
            for w, path in files:
                out.append(f"@font-face{{font-family:'{fam}{SUB_SUFFIX[sub]}';font-weight:{w};src:url('file://{path}')}}")
    return '\n'.join(out)


def _allowed(url):
    if url.startswith('data:'):
        return url
    if url.startswith('file://'):
        p = os.path.realpath(url[7:].split('?')[0])
        if (p.startswith(FONT_DIR + os.sep) or p.startswith(NASKH_DIR + os.sep)) and os.path.exists(p):
            return 'file://' + p
    raise ValueError('Only embedded images and the fonts of ZEO can be used in a print template.')


def fetcher():
    """A WeasyPrint URL fetcher that reads only the bundled fonts and data: URIs."""
    try:
        from weasyprint.urls import URLFetcher

        class _Fetcher(URLFetcher):
            def fetch(self, url, headers=None):
                return super().fetch(_allowed(url), headers)
        return _Fetcher(allowed_protocols=('file', 'data'), timeout=5)
    except ImportError:   # older WeasyPrint: a function
        from weasyprint import default_url_fetcher

        def f(url, *a, **k):
            return default_url_fetcher(_allowed(url), *a, **k)
        return f


# ------------------------------------------------------------------ sandboxed Django templates
SAFE_TAGS = ('if', 'for', 'with', 'cycle', 'firstof', 'now', 'spaceless', 'comment', 'verbatim', 'widthratio', 'ifchanged',
             'regroup', 'filter', 'templatetag', 'resetcycle')
BLOCKED_FILTERS = {'safe', 'safeseq', 'pprint', 'ljust', 'rjust', 'center', 'unordered_list'}
FORBIDDEN = re.compile(r'{%-?\s*(load|include|extends|block|url|debug|ssi|csrf_token|autoescape|static|get_static_prefix|get_media_prefix|lorem)\b')
_ENGINE = None


def engine():
    global _ENGINE
    if _ENGINE is None:
        from django.template import Engine
        from django.template.library import Library
        from django.template import defaulttags, defaultfilters
        lib = Library()
        for t in SAFE_TAGS:
            if t in defaulttags.register.tags:
                lib.tags[t] = defaulttags.register.tags[t]
        for n, f in defaultfilters.register.filters.items():
            if n not in BLOCKED_FILTERS:
                lib.filters[n] = f
        e = Engine(dirs=[], app_dirs=False, debug=True, libraries={}, builtins=[], autoescape=True, string_if_invalid='')
        e.template_builtins = [lib]
        e.template_libraries = {}
        _ENGINE = e
    return _ENGINE


def _run(fn, seconds, what):
    box = {}

    def go():
        try:
            box['v'] = fn()
        except BaseException as e:   # noqa: BLE001
            box['e'] = e
    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        raise PrintError(f'The {what} took longer than {seconds} seconds – simplify the template (fewer loops or images).')
    if 'e' in box:
        raise box['e']
    return box.get('v')


def check_source(source):
    m = FORBIDDEN.search(source or '')
    if m:
        raise PrintError(f'{{% {m.group(1)} %}} is not allowed in a print template – use the placeholders of the list.',
                         (source[:m.start()].count('\n') + 1))
    if len(source or '') > MAX_HTML:
        raise PrintError('The template is too large (2 MB at most).')


def render_html(source, context):
    """Template source + context (plain data) → HTML text. PrintError with the line for a template mistake."""
    from django.template import Context, TemplateSyntaxError
    check_source(source)
    try:
        tpl = engine().from_string(source)
    except TemplateSyntaxError as e:
        dbg = getattr(e, 'template_debug', None) or {}
        msg = str(e)
        if 'Invalid block tag' in msg or 'is not a registered tag library' in msg:
            msg = msg.split(". Did you forget")[0] + ' – only the tags of the placeholder list can be used.'
        raise PrintError(f'Template error: {msg}', dbg.get('line'))

    def go():
        return tpl.render(Context(context, autoescape=True))
    try:
        html = _run(go, HTML_SECONDS, 'template')
    except PrintError:
        raise
    except Exception as e:   # noqa: BLE001 – a filter used on a wrong value …
        dbg = getattr(e, 'template_debug', None) or {}
        raise PrintError(f'Template error: {e.__class__.__name__}: {str(e)[:200]}', dbg.get('line'))
    if len(html) > MAX_HTML:
        raise PrintError('The document is too large (2 MB of HTML at most) – check the loops of the template.')
    return html


def html_to_pdf(html, css=''):
    from weasyprint import CSS, HTML
    from weasyprint.text.fonts import FontConfiguration

    def go():
        fc = FontConfiguration()
        uf = fetcher()
        doc = HTML(string=html, url_fetcher=uf, base_url=None)
        return doc.write_pdf(stylesheets=[CSS(string=css, url_fetcher=uf, font_config=fc)] if css else None, font_config=fc)
    try:
        return _run(go, PDF_SECONDS, 'PDF')
    except PrintError:
        raise
    except Exception as e:   # noqa: BLE001
        log.exception('print template PDF')
        raise PrintError(f'The PDF could not be made: {str(e)[:200]}')


def available():
    """True when WeasyPrint (and its system libraries pango / harfbuzz) can be used."""
    try:
        import weasyprint  # noqa: F401
        return True
    except Exception:   # noqa: BLE001 – OSError when pango is missing
        log.warning('WeasyPrint is not available – the old PDFs are used', exc_info=True)
        return False


def pdf_pages(pdf):
    try:
        import pypdfium2 as pdfium
        return len(pdfium.PdfDocument(pdf))
    except Exception:   # noqa: BLE001
        return 0


def pdf_to_png(pdf, width=900, pages=4):
    """PNG images (bytes) of the first pages."""
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(pdf)
    out = []
    for i in range(min(len(doc), pages)):
        page = doc[i]
        scale = width / page.get_width()
        img = page.render(scale=scale).to_pil()
        b = io.BytesIO()
        img.save(b, 'PNG', optimize=True)
        out.append(b.getvalue())
    return out


def data_uri(path_or_file, mime=None):
    """An image as a data: URI (so the PDF never reads a file by its address)."""
    try:
        if hasattr(path_or_file, 'read'):
            data = path_or_file.read()
            name = getattr(path_or_file, 'name', '') or ''
        else:
            if not path_or_file or not os.path.exists(path_or_file):
                return ''
            with open(path_or_file, 'rb') as fh:
                data = fh.read()
            name = path_or_file
        if len(data) > 3 * 1024 * 1024:
            return ''
        ext = os.path.splitext(name)[1].lower().lstrip('.')
        mime = mime or {'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'gif': 'image/gif', 'svg': 'image/svg+xml',
                        'webp': 'image/webp'}.get(ext, 'image/png')
        return f'data:{mime};base64,' + base64.b64encode(data).decode()
    except Exception:   # noqa: BLE001
        return ''
