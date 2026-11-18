"""Ask ZEO 1.72.0 – the company's own AI key (Settings › AI settings: every provider of AISettings).

When the server allows an external AI (ZEO_AI_EXTERNAL=true) and the company switched it on for Ask ZEO, free questions,
writing, translating, quizzes, law questions, document reading and mapping go to the company's AI. The AI never sees the
database: it calls ZEO's tools, which run AS THE USER (rights, branches, company); every tool result goes through the privacy
shield (privacy.py) before it is sent; the answer is put back together on the server (amounts and names restored).

Cost controls (A5): an answer is kept 30 minutes (same user, same question → no new call); the system prompt is cached by the
provider where it can (prompt caching); a model per feature (a smaller one for documents, mapping and translation); a monthly
budget in USD; tokens and estimated cost are kept per answer (AskUsage) and shown on Settings › Ask ZEO; every step of an answer
is one call of the monthly call limit of AI settings. When anything fails, the built-in answer is given instead.
"""
from zeo.tz import today as _zt_today   # v1.73.16 the user's / company's day, not the server's UTC day
import hashlib
import json
import logging
import re
from datetime import date
from decimal import Decimal

from django.core.cache import cache
from django.db import connection
from django.utils import timezone

from Translations.i18n import tr

from . import scope as S
from .privacy import Shield, scan

log = logging.getLogger(__name__)
MAX_ROUNDS = 8
CACHE_SECONDS = 1800
AI_INTENTS = {'data', 'howto', 'write', 'translate', 'quiz', 'law', 'general', 'document', 'bulk', 'map', 'sop', 'policy_setup', 'product', 'guide', 'read_doc', 'sop_app', 'write_script', 'write_module'}   # v1.73.4 (A10, A15), 1.73.5
LEGAL_NOTE = ('This is general information about UAE labour law, not legal advice. Check with your legal adviser before you act on it.')
LEGAL_NOTE_AR = 'هذه معلومات عامة عن قانون العمل الإماراتي وليست استشارة قانونية. راجع مستشارك القانوني قبل أن تتصرف بناءً عليها.'


class Unavailable(Exception):
    pass


def _row():
    from AISettings.service import ExternalUnavailable, settings_row
    try:
        return settings_row()
    except ExternalUnavailable as e:
        raise Unavailable(str(e))


def ready(request, feature='ask'):
    """True when this company's AI may be used now for Ask ZEO."""
    try:
        from AISettings.service import feature_mode
        if feature_mode(feature)[0] != 'external':
            return False
    except Exception:
        return False
    return not budget_used()


def month_cost():
    from django.db.models import Sum
    from .models import AskUsage
    start = timezone.localtime().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return AskUsage.objects.filter(at__gte=start).aggregate(s=Sum('cost_usd'))['s'] or Decimal('0')


def budget_used():
    from .models import AskSettings
    s = AskSettings.get()
    return bool(s.monthly_budget_usd and month_cost() >= s.monthly_budget_usd)


class Meter:
    """Tokens, calls and cost of one answer."""

    def __init__(self, feature):
        from .models import AskSettings
        self.s = AskSettings.get()
        self.feature, self.calls, self.tin, self.tout = feature, 0, 0, 0

    def add(self, r):
        u = (r or {}).get('usage') or {}
        self.calls += 1
        self.tin += int(u.get('input_tokens') or 0) + int(u.get('cache_read_input_tokens') or 0) + int(u.get('cache_creation_input_tokens') or 0)
        self.tout += int(u.get('output_tokens') or 0)

    @property
    def cost(self):
        return (Decimal(self.tin) * self.s.price_in_per_mtok + Decimal(self.tout) * self.s.price_out_per_mtok) / Decimal(1_000_000)

    def save(self, user, cached=False):
        from .models import AskUsage
        AskUsage.objects.create(user=user, feature=self.feature, mode='ai', calls=self.calls, tokens_in=self.tin, tokens_out=self.tout,
                                cached=cached, cost_usd=self.cost.quantize(Decimal('0.00001')))


def call(request, meter, system, messages, tools=None, max_tokens=1500, feature='ask', small=False):
    """One call to the company's AI (logged in AISettings' call log, counted against the call limit and the budget)."""
    from AISettings.service import ExternalUnavailable, chat
    if budget_used():
        raise Unavailable(tr('The monthly AI budget of this company is used up.'))
    model = (meter.s.model_small if small else meter.s.model_ask) or None
    sysmsg = system
    try:
        if _row().provider == 'anthropic':   # prompt caching: the long, unchanging system text is cached by the provider
            sysmsg = [{'type': 'text', 'text': system, 'cache_control': {'type': 'ephemeral'}}]
    except Unavailable:
        pass
    out_text = json.dumps(messages, default=str)
    if scan(out_text):   # last check – the shield must have caught these already
        raise Unavailable('A private number was about to be sent to the AI – the answer is given without the AI.')
    try:
        r = chat(request, 'documents' if feature == 'documents' else 'ask', sysmsg, messages, tools, max_tokens, model=model)
    except ExternalUnavailable as e:
        raise Unavailable(str(e))
    meter.add(r)
    return r


def text_of(r):
    return '\n'.join(b.get('text', '') for b in (r or {}).get('content') or [] if b.get('type') == 'text').strip()


def json_of(r):
    t = text_of(r)
    m = re.search(r'```(?:json)?\s*(\{.*\}|\[.*\])\s*```', t, re.S) or re.search(r'(\{.*\}|\[.*\])', t, re.S)
    if not m:
        raise Unavailable('The AI did not answer in the expected form.')
    try:
        return json.loads(m.group(1))
    except ValueError:
        raise Unavailable('The AI did not answer in the expected form.')


# ------------------------------------------------------------------ tools the AI may call (all as the user)
TOOLS_EXTRA = [
    {'name': 'search_records', 'description': 'Search every record the user may open in every active app (employees, customers, tickets, leases, work orders, assets, '
                                              'incidents …) by a name, number or word. Returns titles, numbers and links.',
     'input_schema': {'type': 'object', 'properties': {'q': {'type': 'string'}}, 'required': ['q']}},
    {'name': 'search_documents', 'description': 'Search the company\'s own documents (policies, handbook, SOPs) the user may read. Returns document, section and text. '
                                                'Name the document and section in the answer.',
     'input_schema': {'type': 'object', 'properties': {'q': {'type': 'string'}}, 'required': ['q']}},
    {'name': 'find_files', 'description': 'v1.73.10 Find files in the Document Center the user may open (passports, contracts, licences, letters, scans – '
                                          'the text of scans is read too) from words like "Khalid passport" or "expired trade licences". Returns number, title, '
                                          'category, folder, expiry and the sentence found.',
     'input_schema': {'type': 'object', 'properties': {'q': {'type': 'string'}}, 'required': ['q']}},
    {'name': 'zeo_help', 'description': 'Where and how to do something in ZEO: the screens (menu path and link) and ready guides.',
     'input_schema': {'type': 'object', 'properties': {'q': {'type': 'string'}}, 'required': ['q']}},
    {'name': 'zeo_product_docs', 'description': 'ZEO\'s own guides (release notes of every version, installation, developer setup, the app guides): what ZEO can '
                                                'do, how a feature works, how to set it up or implement it for a company, what is not built. Use it for product, scope '
                                                'and implementation questions and say when ZEO does not do something.',
     'input_schema': {'type': 'object', 'properties': {'q': {'type': 'string'}}, 'required': ['q']}},
]


class AITools:
    def __init__(self, request, c, branch, shield):
        from DashboardManagement.assistant import Tools
        self.base = Tools(request, branch)
        self.request, self.c, self.shield = request, c, shield
        self.used = set()

    @property
    def links(self):
        return self.base.links

    def schemas(self):
        from DashboardManagement.assistant import all_tools
        return all_tools() + TOOLS_EXTRA

    def run(self, name, args):
        self.used.add(name)
        if name == 'search_records':
            from Chatter.nav import search
            d = search(self.request, (args or {}).get('q', ''))
            out = [{'title': x.get('title'), 'number': x.get('number'), 'kind': x.get('kind') or x.get('register') or x.get('app'), 'open': x.get('url') or x.get('link')}
                   for x in d.get('results', [])[:20]]
            return {'results': out}
        if name == 'search_documents':
            from .docs import search
            return {'results': [{'document': h['document'], 'section': h['section'], 'text': h['text'][:1500]} for h in search(self.c, (args or {}).get('q', ''), 5)]}
        if name == 'find_files':   # v1.73.10 the Document Center, with the user's own rights
            from ZeoDocs.phase5 import smart_search
            from ZeoDocs.services import Who
            r = smart_search(Who(self.request), (args or {}).get('q', ''), 10)
            return {'results': [{'number': x['number'], 'title': x['title'], 'category': x['category'], 'folder': x['folder_path'], 'expiry': str(x['expiry_date'] or ''),
                                 'found': x['snippet'][:300], 'open': f"/main-sidebar/documents?doc={x['id']}"} for x in r['results']]}
        if name == 'zeo_product_docs':
            from .product import search
            return {'sections': [{'guide': h['doc'], 'section': h['section'], 'text': h['text'][:1800]} for h in search((args or {}).get('q', ''), 5)]}
        if name == 'zeo_help':
            from .help import find_guides, find_screens
            q = (args or {}).get('q', '')
            return {'screens': [{'where': s['where'], 'open': s['link'], 'hint': s['hint']} for s in find_screens(self.c, q, 4)],
                    'guides': [{'title': g['title']['en'], 'steps': [s['en'] for s in g['steps']]} for g in find_guides(self.c, q, 2)]}
        return self.base.run(name, args)


def _employee_names(c):
    try:
        return [S.emp_name(e) for e in S.employees(c).only('emp_first_name', 'emp_last_name', 'emp_code')[:5000]]
    except Exception:
        return []


def system_prompt(request, c, lang, extra=''):
    from DashboardManagement.assistant import system_prompt as base
    rules = base(request, '')
    rules = rules.replace('"Ask ZEO AI"', '"Ask ZEO"')
    return rules + f"""

Ask ZEO rules (always)
- Never name an AI model or company; you are "Ask ZEO".
- Amounts may arrive as tokens like ⟦V3⟧ and names as ⟦N2⟧: write them exactly as they are; ZEO puts the real values back. Never guess them.
- Some values are "[hidden]" on purpose (IBAN, passport, Emirates ID …): never ask for them.
- Start the answer with one of these tags on its own line, for the source of the answer: [data] (from ZEO's tools), [document] (from the
  company's documents – name the document and section), [help] (how to use ZEO), [general] (your own general knowledge).
- Answer in the language of the user's last question – Arabic, English, Hindi, Urdu, Malayalam, Tagalog, French … (Urdu is not Arabic);
  when it is not clear, in {'Arabic' if lang == 'ar' else 'English'}. Keep ZEO menu names, codes and numbers as they are.
- You cannot change anything; to change data the user confirms a preview in Ask ZEO.{extra}"""


TAG_RX = re.compile(r'^\s*\[(data|document|help|general)\]\s*', re.I)


def _finish(request, c, shield, meter, text, links, source, intent, lang, legal=False, extra=None):
    m = TAG_RX.match(text or '')
    if m:
        source = source or m.group(1).lower()
        text = text[m.end():]
    text = shield.restore(text)
    if legal:
        text = text.rstrip() + '\n\n_' + (LEGAL_NOTE_AR if lang == 'ar' else tr(LEGAL_NOTE, lang=lang)) + '_'
    badges = []
    if source == 'general':
        badges.append(tr('General knowledge', lang=lang))
    meter.save(request.user)
    out = {'text': text, 'source': source or 'data', 'intent': intent, 'mode': 'ai', 'links': links or [], 'usage_logged': True,
           'badges': badges, 'ai_usage': {'calls': meter.calls, 'tokens_in': meter.tin, 'tokens_out': meter.tout, 'cost_usd': str(meter.cost.quantize(Decimal('0.00001')))}}
    if extra:
        out.update(extra)
    return out


def _cache_key(c, question, lang, intent):
    h = hashlib.sha256(f'{question.strip().lower()}|{lang}|{intent}'.encode()).hexdigest()[:24]
    return f'askzeo:ai:{connection.schema_name}:{c.user.pk}:{_zt_today().isoformat()}:{h}'


def answer(request, c, conv, question, uploads, suite, branch, lang, intent):
    """The AI answer, or None (the rules answer). Raises Unavailable when the AI cannot be used."""
    from . import aiwork
    if not ready(request):
        return None
    if intent == 'sop_app':   # v1.73.4 (A10)
        from . import developer
        return developer.from_sop(request, c, conv, question, uploads, lang)
    if intent == 'write_module':   # v1.73.5 a full code module for an own server
        from . import modulegen
        return modulegen.draft(request, c, conv, question, uploads, lang)
    if intent == 'write_script':   # v1.73.4 (A15)
        from . import scriptgen
        return scriptgen.draft(request, c, conv, question, uploads, lang)
    if intent in aiwork.HANDLERS:
        return aiwork.HANDLERS[intent](request, c, conv, question, uploads, lang)
    if intent not in ('data', 'howto', 'general', 'document', 'law', 'product', 'guide'):
        return None
    key = _cache_key(c, question, lang, intent)
    hit = cache.get(key)
    if hit:
        from .models import AskUsage
        AskUsage.objects.create(user=request.user, feature=intent, mode='ai', cached=True)
        return dict(hit, cached=True, usage_logged=True)
    from .models import AskSettings
    st = AskSettings.get()
    shield = Shield(st.hide_salary, st.mask_names, _employee_names(c) if st.mask_names else None)
    tools = AITools(request, c, branch, shield)
    meter = Meter(intent)
    legal = intent == 'law'
    extra = ''
    if legal:
        extra = ('\n- This is a labour-law question: cite at least two sources by name (for example Federal Decree-Law No. 33 of 2021 and its article, '
                 'Cabinet Resolution No. 1 of 2022, the MOHRE website). If you are not sure, say so. Tag the answer [general] unless a tool gave the facts.')
    system = system_prompt(request, c, lang, extra)
    msgs = [{'role': m.role, 'content': shield.text(m.text)} for m in (conv.messages.order_by('-created_at')[1:9] if conv else [])][::-1]
    while msgs and msgs[0]['role'] != 'user':
        msgs.pop(0)
    msgs.append({'role': 'user', 'content': shield.text(question)})
    used_tool = False
    for _ in range(MAX_ROUNDS):
        r = call(request, meter, system, msgs, tools.schemas())
        blocks = r.get('content') or []
        uses = [b for b in blocks if b.get('type') == 'tool_use']
        if r.get('stop_reason') != 'tool_use' or not uses:
            src = '' if used_tool else ''
            out = _finish(request, c, shield, meter, text_of(r) or tr('I could not find an answer.', lang=lang), tools.links, src, intent, lang, legal)
            if not used_tool and out['source'] == 'data':
                out['source'] = 'general'
                out['badges'] = [tr('General knowledge', lang=lang)]
            if 'search_documents' in tools.used and out['source'] in ('data', 'general'):
                out['source'], out['badges'] = 'document', []
            if tools.used and tools.used <= {'zeo_help', 'zeo_product_docs'} and out['source'] in ('data', 'general'):
                out['source'], out['badges'] = 'help', []
            cache.set(key, {k: v for k, v in out.items() if k != 'ai_usage'}, CACHE_SECONDS)
            return out
        used_tool = True
        msgs.append({'role': 'assistant', 'content': blocks})
        results = []
        for u in uses:
            res = tools.run(u.get('name'), u.get('input') or {})
            res = shield.protect(res)
            results.append({'type': 'tool_result', 'tool_use_id': u['id'], 'content': json.dumps(res, default=str, ensure_ascii=False)[:60000],
                            **({'is_error': True} if isinstance(res, dict) and res.get('error') else {})})
        msgs.append({'role': 'user', 'content': results})
    meter.save(request.user)
    return {'text': tr('This question needed too many steps. Try asking it in smaller parts.', lang=lang), 'source': 'data', 'intent': intent, 'mode': 'ai', 'usage_logged': True}
