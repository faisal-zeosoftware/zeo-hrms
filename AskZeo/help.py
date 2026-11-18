"""Ask ZEO (A4, 98a) – "where / how do I … in ZEO": the menus of every app (data/menu.json, exported from the frontend's
shared-ui/menu-config.ts by tools/export_menu.js), the ready guides (data/guides.json) and the "What's new" list
(Chatter/nav.py RELEASES). Only screens the user may open (rights, active apps) are offered, each with its link.
"""
import json
import re
import math
import os
from collections import Counter
from functools import lru_cache

from .words import tokens

HERE = os.path.dirname(__file__)
SUITE_APP = {'': 'hrms', 'hrms': 'hrms', 'projects': 'projects', 'crm': 'crm', 'property': 'property', 'maintenance': 'maintenance',
             'assets': 'assets', 'hse': 'hse'}   # settings / common: always there
SUITE_LABEL = {'': 'HRMS', 'hrms': 'HRMS', 'projects': 'Projects', 'crm': 'CRM', 'property': 'Property', 'maintenance': 'Maintenance',
               'assets': 'Asset Management', 'hse': 'HSE', 'settings': 'Settings', 'common': ''}


@lru_cache(maxsize=1)
def menu():
    with open(os.path.join(HERE, 'data', 'menu.json'), encoding='utf-8') as f:
        return json.load(f)


@lru_cache(maxsize=1)
def guides():
    with open(os.path.join(HERE, 'data', 'guides.json'), encoding='utf-8') as f:
        return json.load(f)['guides']


def active_apps():
    try:
        from ZeoApps.services import is_active
    except Exception:
        return lambda k: True
    cache = {}

    def f(key):
        if key not in cache:
            try:
                cache[key] = bool(is_active(key))
            except Exception:
                cache[key] = True
        return cache[key]
    return f


def _app_cond_ok(expr, active):
    """The app: parts of a menu condition ('app:payroll|property', '!app:hrms'); country parts are left to the country pack."""
    for part in [p.strip() for p in (expr or '').split(',') if p.strip()]:
        neg = part.startswith('!')
        k = part[1:].strip() if neg else part
        if k.startswith('app:'):
            ok = any(active(x) for x in k[4:].split('|'))
            if ok == neg:
                return False
    return True


def screens(c):
    """Every screen the user may open: [{label, link, hint, app, section, suite}]."""
    active = active_apps()
    out = []
    for g in menu()['groups']:
        suite = g.get('suite') or ''
        app_key = SUITE_APP.get(suite)
        if app_key and not active(app_key):
            continue
        for a in g['apps']:
            if a.get('perms') and not (c.admin or set(a['perms']) & c.codes):
                continue
            if not _app_cond_ok(a.get('country'), active):
                continue
            if a.get('link') and not a.get('sections'):
                out.append({'label': a['label'], 'link': a['link'], 'hint': '', 'app': a['label'], 'section': '', 'suite': suite})
            for s in a.get('sections') or []:
                for i in s['items']:
                    if i.get('perms') and not (c.admin or set(i['perms']) & c.codes):
                        continue
                    if not _app_cond_ok(i.get('country'), active):
                        continue
                    out.append({'label': i['label'], 'link': i['link'], 'hint': i.get('hint', ''), 'app': a['label'], 'section': s['title'], 'suite': suite})
    return out


def where(item):
    path = [SUITE_LABEL.get(item['suite'], ''), item['app']]
    if item['section'] and item['section'] != item['app']:
        path.append(item['section'])
    out = []
    for p in path:
        if p and p not in out:
            out.append(p)
    if not out or out[-1] != item['label']:
        out.append(item['label'])
    return ' › '.join(out)


def _bm25(query, docs, k1=1.4, b=0.75):
    """docs: [(tokens, weight_boost)] → scores. A small BM25 so no paid search service is needed."""
    n = len(docs) or 1
    avg = sum(len(d) for d, _ in docs) / n or 1
    df = Counter()
    for d, _ in docs:
        for t in set(d):
            df[t] += 1
    scores = []
    for d, boost in docs:
        tf = Counter(d)
        s = 0.0
        for t in set(query):
            if t not in tf:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            s += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(d) / avg))
        scores.append(s * boost)
    return scores


VERBS = {'add', 'new', 'create', 'make', 'enter', 'set', 'get', 'see', 'view', 'change', 'edit', 'update', 'delete', 'remove', 'want', 'need', 'use',
         'go', 'put', 'give', 'run', 'check', 'start', 'look', 'up'}


def find_screens(c, question, n=3):
    q = [t for t in tokens(question) if t not in VERBS] or tokens(question)
    if not q:
        return []
    items = screens(c)
    docs = []
    for i in items:
        label = tokens(i['label'])
        # a screen whose whole name is asked for ("employees") comes first
        boost = 1.6 if label and set(label) <= set(q) else 1.0
        docs.append((label * 3 + tokens(i['hint']) + tokens(i['app']) + tokens(i['section']), boost))
    sc = _bm25(q, docs)
    ranked = sorted(((s, i) for s, i in zip(sc, items) if s > 0), key=lambda x: -x[0])
    out, seen = [], set()
    for s, i in ranked:
        if i['link'] in seen:
            continue
        seen.add(i['link'])
        out.append(dict(i, score=round(s, 2), where=where(i)))
        if len(out) >= n:
            break
    return out


def guide_ok(c, g, active):
    if g.get('admin') and not c.admin:
        return False
    if g.get('app') and not active(g['app'] if g['app'] != 'payroll' else 'payroll'):
        return False
    if g.get('suite') and SUITE_APP.get(g['suite']) and not active(SUITE_APP[g['suite']]):
        return False
    if g.get('perms') and not (c.admin or set(g['perms']) & c.codes):
        return False
    return True


def find_guides(c, question, n=2):
    q = tokens(question)
    active = active_apps()
    gs = [g for g in guides() if guide_ok(c, g, active)]
    if not q or not gs:
        return []
    sc = _bm25(q, [(tokens(g['title']['en']) * 3 + tokens(g['words']), 1.0) for g in gs])
    return [g for s, g in sorted(zip(sc, gs), key=lambda x: -x[0]) if s > 0.5][:n]


def find_news(question, n=2):
    if not re.search(r"what'?s new|\bnew in\b|\blatest (version|release|update)|\brelease notes?\b|\bnew features?\b", question.lower()):
        return []
    try:
        from Chatter.nav import RELEASES
    except Exception:
        return []
    q = tokens(question)
    items = [(r['version'], it) for r in RELEASES[:30] for it in r['items']]
    if not q or not items:
        return []
    sc = _bm25(q, [(tokens(it[1]) * 2 + tokens(it[2]), 1.0) for _, it in items])
    return [{'version': v, 'title': it[1], 'text': it[2]} for s, (v, it) in sorted(zip(sc, items), key=lambda x: -x[0]) if s > 1.5][:n]
