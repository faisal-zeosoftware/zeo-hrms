# v1.67.8 (fixes)
"""v1.67.8 (fixes) – items 10 / 14: a sentence that was stored filled in, in English ("Employee Code E1 is twice in the file
(row 4)"), in the reader's language: the sentence is matched against its English templates ("Employee Code {0} is twice in
the file (row {1})"), the template is translated and filled with the same values – each value translated too when it is a
standard word ("Branch", "Date of Birth"). Unknown sentences stay as they are."""
import re

_CACHE = {}


def _compiled(templates):
    key = id(templates)
    got = _CACHE.get(key)
    if got is None:
        out = []
        for t in templates:
            rx = '^' + re.sub(r'\\\{(\d+)\\\}', r'(.+?)', re.escape(t)) + '$'
            out.append((re.compile(rx, re.S), t))
        out.sort(key=lambda x: -len(x[1]))
        _CACHE[key] = got = out
    return got


def translate_filled(text, templates, lang, depth=0):
    from Translations.i18n import tr
    if not text or not isinstance(text, str) or not lang or str(lang).startswith('en') or depth > 3:
        return text
    t = tr(text, lang=lang)
    if t != text:
        return t
    for rx, tpl in _compiled(templates):
        m = rx.match(text)
        if m:
            return tr(tpl, *[translate_filled(g, templates, lang, depth + 1) for g in m.groups()], lang=lang)
    return text
