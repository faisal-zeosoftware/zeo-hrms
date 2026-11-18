"""Ask ZEO 1.72.0 – product, scope and implementation questions ("can ZEO …", "how do we set up … for a client", "what is needed for
WPS"): answered from ZEO's own guides (data/product_docs.json = the package's README-UPDATE, INSTALL, DEVELOPER-SETUP and the app
guides, cut into sections), the ready guides and the screens. Without an AI the best sections are shown; with the company's AI it
writes the answer from them (tool zeo_product_docs). Source badge: ZEO help.
"""
import json
import os
import re
from functools import lru_cache

from Translations.i18n import tr

from . import help as H
from .words import tokens

HERE = os.path.dirname(__file__)


@lru_cache(maxsize=1)
def sections():
    p = os.path.join(HERE, 'data', 'product_docs.json')
    if not os.path.exists(p):
        return [], []
    with open(p, encoding='utf-8') as f:
        secs = json.load(f)['sections']
    toks = [(tokens(s['section']) * 3 + tokens(s['text']), 1.0) for s in secs]
    return secs, toks


def search(question, n=4):
    q = tokens(question)
    secs, toks = sections()
    if not q or not secs:
        return []
    sc = H._bm25(q, toks)
    best = sorted(range(len(secs)), key=lambda i: -sc[i])[:n]
    return [dict(secs[i], score=round(sc[i], 2)) for i in best if sc[i] > 2.0]


DOC_NAMES = {'README-UPDATE': 'Update guide', 'INSTALL': 'Install guide', 'DEVELOPER-SETUP': 'Developer guide'}


def _where(h):
    """'Update guide › 81.4 Gratuity law changes' – the guide and the last two headings (not the guide's own title)."""
    doc = h['doc'].rsplit('/', 1)[-1].replace('.md', '')
    parts = [x.strip() for x in h['section'].split('›') if x.strip()]
    if len(parts) > 1:
        parts = parts[1:]
    return ' › '.join([DOC_NAMES.get(doc, doc)] + parts[-2:])


def _clean(text):
    lines = []
    for ln in text.splitlines():
        t = ln.strip()
        if t.startswith('|') or re.fullmatch(r'[-|: ]+', t or '-'):
            continue   # markdown tables do not read well as a snippet
        lines.append(re.sub(r'`([^`]*)`', r'\1', t))
    return ' '.join(x for x in lines if x)


def _snippet(text, question, size=600):
    text = _clean(text) or text
    words = [w for w in tokens(question) if len(w) > 3]
    low = text.lower()
    pos = min([low.find(w) for w in words if low.find(w) >= 0] or [0])
    start = max(0, pos - 120)
    s = re.sub(r'\s+', ' ', text[start:start + size]).strip()
    return ('…' if start else '') + s + ('…' if start + size < len(text) else '')


def answer_builtin(c, question, lang):
    hits = search(question, 3)
    screens = H.find_screens(c, question, 2)
    guides = H.find_guides(c, question, 1)
    if not hits and not screens and not guides:
        return None
    lines = [tr('From the ZEO guides:', lang=lang)] if hits else []
    for h in hits:
        lines.append(f"- **{_where(h)}** – {_snippet(h['text'], question)}")
    if screens:
        lines.append(tr('Screens: {0}', ' · '.join(s['where'] for s in screens), lang=lang))
    out = {'text': '\n'.join(lines), 'source': 'help', 'intent': 'product', 'links': [{'label': s['where'], 'url': s['link']} for s in screens]}
    if guides:
        from .engine import guide_answer
        out['cards'] = guide_answer(guides, lang)['cards']
    return out
