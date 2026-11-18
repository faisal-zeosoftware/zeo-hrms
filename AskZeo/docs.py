"""Ask ZEO 1.72.0 (A3) – company documents: policy, handbook, SOPs. Uploaded by HR, cut into sections (headings or ~1,200
characters), searched locally with BM25 (no paid embeddings, nothing sent anywhere to index). Each document says who may read
it: everybody, HR / managers (back office) or administrators only, optionally only some branches. Answers name the document
and the section. Works with and without the company's AI (without it, the best sections are shown as they are).
"""
import io
import re
from functools import lru_cache

from django.db.models import Q

from .help import _bm25
from .words import tokens

ACCESS = (('all', 'Everybody in the company'), ('backoffice', 'HR and managers'), ('admin', 'Administrators only'))
HEAD_RX = re.compile(r'^\s*((\d+(\.\d+)*[.)]?\s+[A-Z؀-ۿ].{2,80})|([A-Z][A-Z \-&/]{3,60})|(#+\s.+)|((section|article|chapter|part|clause|المادة|البند|الفصل)\s+\S+.*))\s*$', re.I)


def extract(name, data):
    low = (name or '').lower()
    if low.endswith('.docx'):
        import docx
        d = docx.Document(io.BytesIO(data))
        lines = []
        for p in d.paragraphs:
            t = p.text.strip()
            if not t:
                continue
            if (p.style.name or '').lower().startswith('heading'):
                t = '## ' + t
            lines.append(t)
        for tb in d.tables:
            for row in tb.rows:
                lines.append(' | '.join(c.text.strip() for c in row.cells))
        return '\n'.join(lines)
    if low.endswith(('.txt', '.md')):
        return data[:2_000_000].decode('utf-8', 'replace')
    from AISettings.docread import extract_text
    t, _ = extract_text(name, data)
    return t


def split(text, size=1200):
    """[(section title, text)]"""
    parts, title, buf = [], '', []

    def flush():
        body = '\n'.join(buf).strip()
        if body:
            for i in range(0, len(body), size):
                parts.append((title or '', body[i:i + size]))
    for line in (text or '').splitlines():
        if HEAD_RX.match(line) and len(line.strip()) < 90:
            flush()
            buf = []
            title = line.strip().lstrip('#').strip()
            continue
        buf.append(line)
    flush()
    return parts


def visible(c):
    from .models import CompanyDoc
    qs = CompanyDoc.objects.filter(active=True)
    if c.admin:
        return qs
    qs = qs.exclude(access='admin')
    if not c.backoffice:
        qs = qs.filter(access='all')
    if c.branches is not None:
        mine = list(c.branches or [])
        if c.emp is not None and c.emp.emp_branch_id_id:
            mine.append(c.emp.emp_branch_id_id)
        qs = qs.filter(Q(branches__isnull=True) | Q(branches__in=mine)).distinct()
    elif c.emp is not None and not c.backoffice:
        qs = qs.filter(Q(branches__isnull=True) | Q(branches=c.emp.emp_branch_id_id)).distinct()
    return qs


def search(c, question, n=4):
    from .models import DocPart
    q = tokens(question)
    if not q:
        return []
    ids = list(visible(c).values_list('pk', flat=True))
    if not ids:
        return []
    parts = list(DocPart.objects.filter(doc_id__in=ids).select_related('doc').only('id', 'title', 'text', 'doc__title', 'doc__id', 'order'))
    if not parts:
        return []
    toks = [tokens(p.title) * 2 + tokens(p.text) for p in parts]
    sc = _bm25(q, [(t, 1.0) for t in toks])
    # 1.72.0 fix: BM25 scores are small when the company has only one or two documents (every word is in "all" of them), so a fixed
    # score limit found nothing. A section counts when it shares words with the question and scores near the best one.
    from .help import VERBS
    words = {w for w in q if w not in VERBS and len(w) > 2 and not w.isdigit()}
    best = sorted(zip(sc, parts, toks), key=lambda x: -x[0])
    top = best[0][0] if best else 0
    out = []
    for s, p, t in best[:n]:
        shared = words & set(t)
        if s > 0 and s >= top * 0.35 and (len(shared) >= 2 or (len(shared) == 1 and len(words) <= 2)):
            out.append({'doc_id': p.doc.id, 'document': p.doc.title, 'section': p.title or f'part {p.order + 1}', 'text': p.text, 'score': round(s, 2)})
    return out


def strong(c, question):
    """A company-document section that clearly answers the question (at least two of its words, the best score clearly ahead) –
    used for questions not phrased as "what does our policy say" ("when is the board bonus paid")."""
    from .help import VERBS
    hits = search(c, question, 2)
    if not hits:
        return False
    q = {w for w in tokens(question) if w not in VERBS and len(w) > 2}
    words = set(tokens(hits[0]['section']) + tokens(hits[0]['text']))
    return len(q & words) >= max(2, round(len(q) * 0.6))


def answer_builtin(c, question, lang):
    from Translations.i18n import tr
    hits = search(c, question, 3)
    if not hits:
        return None
    lines = [tr('From your company documents:', lang=lang)]
    for h in hits:
        snippet = re.sub(r'\s+', ' ', h['text']).strip()
        lines.append(f"- **{h['document']} › {h['section']}** – {snippet[:500]}{'…' if len(snippet) > 500 else ''}")
    return {'text': '\n'.join(lines), 'source': 'document', 'intent': 'document',
            'links': [{'label': tr('Company documents', lang=lang), 'url': '/main-sidebar/settings/ask-zeo'}]}
