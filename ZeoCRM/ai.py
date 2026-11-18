"""v1.67.30 – CRM release 4: the 16 AI features of the CRM scope (part 5).

Two engines, chosen per company:
  * Built-in (always available, free, nothing leaves the server): models learnt from the company's own CRM history –
    naive-Bayes lead conversion and deal-win models, a churn model on the customer 360° figures, product co-occurrence for the
    quotation assistant and upselling, text rules for qualification (BANT), summaries, sentiment (English and Arabic word lists),
    voice notes, industry detection, and statistics for lost deals, campaigns and coaching.
  * External (Settings › AI settings › CRM assistant on, and the server allows external AI): e-mail drafts, summaries,
    qualification, sentiment and lost-deal write-ups are written by the company's AI provider; every call goes through
    AISettings.service.chat (server switch, company switch, monthly limit, call log). On any problem the built-in engine answers.

Every result is an **AI suggestion** (Sales › AI suggestions, and on the lead / opportunity / contact). Scores and probabilities are
shown on the records; anything that changes a record (status, fields, activities, quotation lines, new leads) happens only when an
authorised user presses *Accept* (scope: "AI recommendations should be reviewed by authorized employees").
"""
import logging
import math
import re
from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Avg, Count, Q, Sum

from zeo.appkit.engine import Act, AppError, Reg, d, emp_name, now, today, user_name

from .models import (AISuggestion, Activity, Campaign, CampaignSend, Complaint, Contact, Conversation, ConversationMessage, CustomerProfile, Lead, LostReason,
                     Opportunity, Quotation, QuotationLine, SalesOrderLine, Stage)

log = logging.getLogger(__name__)
CTX = {}
FREE_MAIL = {'gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com', 'live.com', 'icloud.com', 'aol.com', 'msn.com', 'proton.me', 'protonmail.com', 'gmx.com',
             'yandex.com', 'mail.com', 'zoho.com', 'yahoo.co.in', 'rediffmail.com', 'hotmail.co.uk'}
POS = {'thank', 'thanks', 'great', 'excellent', 'happy', 'good', 'perfect', 'appreciate', 'satisfied', 'love', 'pleased', 'quick', 'helpful', 'interested', 'agree',
       'approved', 'ok', 'okay', 'wonderful', 'شكرا', 'شكراً', 'ممتاز', 'رائع', 'جيد', 'سعيد', 'موافق', 'مهتم'}
NEG = {'bad', 'poor', 'angry', 'disappointed', 'complaint', 'delay', 'delayed', 'late', 'problem', 'issue', 'broken', 'worst', 'expensive', 'cancel', 'refund',
       'unhappy', 'slow', 'never', 'terrible', 'not', 'no', 'unacceptable', 'frustrated', 'سيء', 'مشكلة', 'تأخير', 'غالي', 'إلغاء', 'غاضب', 'شكوى', 'لا'}
ACTION_WORDS = ('will', 'need', 'needs', 'send', 'share', 'by ', 'deadline', 'follow', 'agree', 'agreed', 'next', 'call', 'schedule', 'prepare', 'confirm', 'quote',
                'visit', 'demo', 'sign', 'pay', 'deliver')


# ------------------------------------------------------------------ external engine
def llm(request, system, prompt, max_tokens=900):
    """Text from the company's external AI, or None (built-in mode, switched off, limit reached, provider error)."""
    try:
        from AISettings import service as S
        if S.feature_mode('crm')[0] != 'external':
            return None
        r = S.chat(request, 'crm', system, [{'role': 'user', 'content': prompt[:12000]}], max_tokens=max_tokens)
        parts = [c.get('text', '') for c in (r.get('content') or []) if isinstance(c, dict) and c.get('type') == 'text']
        out = '\n'.join(p for p in parts if p).strip()
        return out or None
    except Exception as e:   # noqa: BLE001 – the built-in engine answers instead
        log.info('CRM AI external not used: %s', e)
        return None


def engine_name():
    try:
        from AISettings import service as S
        if S.feature_mode('crm')[0] == 'external':
            row = S.settings_row()
            return f'external ({row.provider})'
    except Exception:   # noqa: BLE001
        pass
    return 'built-in'


def suggest(kind, title, text='', data=None, key='', confidence=None, engine='built-in', **links):
    """An open suggestion (the same key is not suggested twice while open)."""
    app = CTX['app']
    if key and AISuggestion.objects.filter(kind=kind, key=key, status='new').exists():
        return AISuggestion.objects.filter(kind=kind, key=key, status='new').first()
    branch = next((getattr(v, 'branch_id', None) for v in links.values() if v is not None and getattr(v, 'branch_id', None)), None)
    owner = links.pop('owner', None) or next((getattr(v, 'owner', None) for v in links.values() if v is not None and getattr(v, 'owner_id', None)), None)
    s = AISuggestion.objects.create(number=app.next_number('ai_suggestion', 'AI'), kind=kind, key=key[:120], title=title[:200], text=text, data=data or {},
                                    confidence=confidence, engine=engine, owner=owner, branch_id=branch, **{k: v for k, v in links.items() if v is not None})
    return s


# ------------------------------------------------------------------ naive Bayes on the company's history
class NB:
    """Two-class naive Bayes with Laplace smoothing over categorical features."""

    def __init__(self):
        self.n = Counter()
        self.f = defaultdict(Counter)
        self.vals = defaultdict(set)

    def fit(self, rows):
        for feats, y in rows:
            self.n[y] += 1
            for k, v in feats.items():
                self.f[(k, y)][v] += 1
                self.vals[k].add(v)
        return self

    @property
    def size(self):
        return sum(self.n.values())

    def predict(self, feats):
        """(probability of class True, [(feature, value, log-odds contribution)])."""
        n1, n0 = self.n[True], self.n[False]
        lo = math.log((n1 + 1) / (n0 + 1))
        parts = []
        for k, v in feats.items():
            V = max(2, len(self.vals[k]) + 1)
            p1 = (self.f[(k, True)][v] + 1) / (n1 + V)
            p0 = (self.f[(k, False)][v] + 1) / (n0 + V)
            c = math.log(p1 / p0)
            lo += c
            parts.append((k, v, c))
        lo = max(-30, min(30, lo))
        return 1 / (1 + math.exp(-lo)), sorted(parts, key=lambda x: -abs(x[2]))


def _bucket(v, edges):
    v = float(v or 0)
    for e in edges:
        if v < e:
            return f'<{e:g}'
    return f'≥{edges[-1]:g}'


def lead_features(l):
    jt = (l.job_title or '').lower()
    senior = 'senior' if re.search(r'\b(ceo|cto|cfo|coo|owner|founder|director|head|vp|president|partner|gm|general manager|manager|مدير)\b', jt) else 'other'
    return {'source': l.source.code if l.source_id else '-', 'channel': l.channel, 'industry': l.industry or '-', 'campaign': 'yes' if l.campaign_id else 'no',
            'email': 'yes' if l.email else 'no', 'phone': 'yes' if l.phone else 'no', 'company': 'yes' if l.company_name else 'no', 'title': senior,
            'value': _bucket(l.value, [1, 10000, 50000, 250000]), 'requirement': 'long' if len(l.requirements or '') > 60 else 'short' if l.requirements else 'none',
            'rule_score': _bucket(l.score, [20, 50, 80]), 'territory': 'yes' if l.territory_id else 'no'}


LABEL = {'source': 'source', 'channel': 'channel', 'industry': 'industry', 'campaign': 'from a campaign', 'email': 'e-mail given', 'phone': 'phone given',
         'company': 'company given', 'title': 'job title', 'value': 'estimated value', 'requirement': 'requirement', 'rule_score': 'rule score', 'territory': 'territory',
         'pipeline': 'pipeline', 'stage': 'stage reached', 'owner': 'agent', 'activities': 'activities', 'age': 'age', 'quote': 'quotation sent',
         'decision': 'decision maker known', 'discount': 'discount'}


def _why(parts, n=3):
    good = [f'{LABEL.get(k, k)}: {v}' for k, v, c in parts if c > 0.15][:n]
    bad = [f'{LABEL.get(k, k)}: {v}' for k, v, c in parts if c < -0.15][:n]
    return ('+ ' + ', '.join(good) if good else '') + (' · − ' + ', '.join(bad) if bad else '')


def lead_model():
    rows = [(lead_features(l), l.status == 'converted') for l in Lead.objects.filter(status__in=('converted', 'disqualified'), created_at__gte=now() - timedelta(days=730))
            .select_related('source')[:20000]]
    return NB().fit(rows)


def score_leads(model=None):
    model = model or lead_model()
    n = 0
    for l in Lead.objects.exclude(status__in=('converted', 'disqualified')).select_related('source'):
        if model.size >= 20:
            p, parts = model.predict(lead_features(l))
            l.ai_score, l.ai_reason = int(round(p * 100)), _why(parts)[:300]
        else:   # too little history: the rule score, said so
            l.ai_score, l.ai_reason = max(0, min(100, int(l.score or 0))), f'Learning – only {model.size} closed leads so far; the rule score is used.'
        l.save(update_fields=['ai_score', 'ai_reason'])
        n += 1
    return n


def opp_features(o):
    acts = o.activities.filter(status='done').count() if o.pk else 0
    age = (now() - o.created_at).days if o.created_at else 0
    return {'pipeline': o.pipeline.code if o.pipeline_id else '-', 'industry': o.industry or '-', 'source': o.source.code if o.source_id else '-',
            'owner': str(o.owner_id or '-'), 'value': _bucket(o.value, [10000, 50000, 250000, 1000000]), 'activities': _bucket(acts, [1, 3, 8]),
            'age': _bucket(age, [14, 45, 90, 180]), 'quote': 'yes' if o.pk and Quotation.objects.filter(opportunity=o, status__in=('sent', 'accepted')).exists() else 'no',
            'decision': 'yes' if o.pk and o.decision_makers.exists() else 'no'}


def opp_model():
    rows = [(opp_features(o), o.status == 'won') for o in Opportunity.objects.filter(status__in=('won', 'lost'), closed_at__gte=now() - timedelta(days=730))
            .select_related('pipeline', 'source')[:20000]]
    return NB().fit(rows)


def forecast_opps(model=None):
    model = model or opp_model()
    n = 0
    for o in Opportunity.objects.filter(status='open').select_related('pipeline', 'source', 'stage'):
        stage_p = (o.stage.probability if o.stage_id else 10) / 100
        if model.size >= 20:
            p, parts = model.predict(opp_features(o))
            prob = 0.5 * p + 0.5 * stage_p   # the history and the stage together
            why = _why(parts)
        else:
            prob, why = stage_p, f'Learning – only {model.size} closed deals so far; the stage probability is used.'
        o.ai_probability, o.ai_reason = int(round(prob * 100)), why[:300]
        Opportunity.objects.filter(pk=o.pk).update(ai_probability=o.ai_probability, ai_reason=o.ai_reason)
        n += 1
    return n


# ------------------------------------------------------------------ churn
def churn_scores():
    n = 0
    for p in CustomerProfile.objects.all():
        days = (today() - (p.last_won_at or p.last_activity_at)).days if (p.last_won_at or p.last_activity_at) else 400
        z = -2.2 + 0.006 * min(days, 730) - 0.025 * (p.relationship_score or 0) + 0.45 * (p.open_complaints or 0) - 0.5 * min(p.active_contracts or 0, 3)
        if p.satisfaction is not None:
            z += (3 - float(p.satisfaction)) * 0.6
        prob = int(round(100 / (1 + math.exp(-z))))
        p.churn_probability = prob
        p.save(update_fields=['churn_probability'])
        if prob >= 60 and p.won_count:
            suggest('churn', f'{p.account.name}: churn risk {prob} %', f'{p.churn_reason or "Little recent business"}. Plan a call or visit with the account manager, '
                    f'review open complaints and offer a renewal / loyalty proposal.', {'activity': {'kind': 'call', 'subject': f'Retention call – {p.account.name}'}},
                    key=f'churn:{p.account_id}', confidence=prob, account=p.account, owner=p.account_manager)
        n += 1
    return n


# ------------------------------------------------------------------ text: BANT, summary, sentiment, voice, industry
AMOUNT = re.compile(r'(?:aed|usd|sar|qar|\$|€|£|dhs?|inr)\s?\d[\d,]*(?:\.\d+)?\s?(?:k|m|million|thousand)?|\d[\d,]*(?:\.\d+)?\s?(?:k|m|million|thousand)?\s?(?:aed|usd|sar|dhs|dirhams?|dollars?)', re.I)
WHEN = re.compile(r'\b(today|tomorrow|this week|next week|this month|next month|this quarter|next quarter|q[1-4]|asap|urgent|immediately|'
                  r'january|february|march|april|may|june|july|august|september|october|november|december|\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|in \d+ (?:days|weeks|months))\b', re.I)


def bant(lead_or_opp):
    o = lead_or_opp
    text = ' '.join(str(x or '') for x in (getattr(o, 'requirements', ''), getattr(o, 'interest', ''), getattr(o, 'technical_requirements', '')))
    text += ' ' + ' '.join(a.outcome or '' for a in (o.activities.all()[:20] if o.pk else []))
    jt = (getattr(o, 'job_title', '') or (getattr(getattr(o, 'contact', None), 'job_title', '') or '')).lower()
    b = bool(AMOUNT.search(text) or re.search(r'\bbudget\b', text, re.I) or getattr(o, 'budget_max', None) or float(getattr(o, 'value', 0) or 0) > 0)
    a = bool(re.search(r'\b(ceo|cto|cfo|owner|founder|director|head|manager|partner|gm|decision)\b', jt + ' ' + text.lower()) or
             (isinstance(o, Opportunity) and o.pk and o.decision_makers.exists()))
    n = len(text.strip()) > 40 or bool(re.search(r'\b(need|require|looking for|want|interested)\b', text, re.I))
    t = bool(WHEN.search(text) or getattr(o, 'expected_close', None))
    neg = bool(re.search(r'\b(not interested|no budget|just looking|wrong number|spam|not now|next year)\b', text, re.I))
    return {'budget': b, 'authority': a, 'need': n, 'timeline': t, 'negative': neg}


def qualify(l, request=None):
    r = bant(l)
    got = sum(1 for k in ('budget', 'authority', 'need', 'timeline') if r[k])
    status = 'disqualified' if r['negative'] and got <= 1 else 'qualified' if got >= 3 else 'nurture' if got <= 1 else 'working'
    marks = ', '.join(f'{k.capitalize()} {"✓" if r[k] else "✗"}' for k in ('budget', 'authority', 'need', 'timeline'))
    text = f'BANT: {marks}.' + (' Negative signals in the notes.' if r['negative'] else '')
    eng = 'built-in'
    ext = llm(request, 'You qualify B2B sales leads with BANT. Answer in 3 short lines: verdict (qualified / working / nurture / disqualified), why, next question to ask.',
              f'Lead: {l.name}, {l.job_title}, {l.company_name}\nIndustry: {l.industry}\nRequirement: {l.requirements}\nInterest: {l.interest}\nValue: {l.value}\n'
              f'Notes: ' + ' | '.join(a.outcome or '' for a in l.activities.all()[:10]))
    if ext:
        text, eng = text + '\n' + ext, engine_name()
        m = re.search(r'\b(qualified|working|nurture|disqualified)\b', ext.lower())
        if m:
            status = m.group(1)
    missing = [k for k in ('budget', 'authority', 'need', 'timeline') if not r[k]]
    if missing:
        text += '\nAsk about: ' + ', '.join({'budget': 'the budget', 'authority': 'who decides', 'need': 'the exact requirement', 'timeline': 'when they need it'}[k]
                                             for k in missing) + '.'
    return suggest('qualify', f'{l.name}: suggested status "{status}"', text, {'lead_status': status}, key=f'qualify:{l.pk}:{status}',
                   confidence=25 * got, engine=eng, lead=l)


def sentences(text):
    return [s.strip() for s in re.split(r'(?<=[.!?؟])\s+|\n+', text or '') if len(s.strip()) > 3]


def summarise(text, request=None, what='meeting notes'):
    ext = llm(request, 'Summarise CRM notes for a salesperson. Give: Summary (2–3 sentences), Decisions, Action items with owner and date if known. Be brief.',
              f'{what}:\n{text}')
    if ext:
        return ext, engine_name()
    ss = sentences(text)
    acts = [s for s in ss if any(w in s.lower() for w in ACTION_WORDS)]
    dates = sorted(set(m.group(0) for m in WHEN.finditer(text)))
    out = 'Summary: ' + ' '.join(ss[:2])[:400]
    if acts:
        out += '\nAction items:\n' + '\n'.join(f'• {a[:200]}' for a in acts[:6])
    if dates:
        out += '\nDates mentioned: ' + ', '.join(dates[:6])
    return out, 'built-in'


def sentiment_of(texts):
    pos = neg = 0
    for t in texts:
        words = re.findall(r'[\w؀-ۿ]+', (t or '').lower())
        for i, w in enumerate(words):
            negated = i > 0 and words[i - 1] in ('not', 'no', 'never', 'لا', 'غير')
            if w in POS and w not in ('not', 'no'):
                neg += 1 if negated else 0
                pos += 0 if negated else 1
            elif w in NEG and w not in ('not', 'no', 'لا'):
                neg += 1
    total = pos + neg
    score = (pos - neg) / total if total else 0.0
    return ('positive' if score > 0.2 else 'negative' if score < -0.2 else 'neutral'), round(score, 2), pos, neg


EMAIL_RE = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')
PHONE_RE = re.compile(r'(?:\+|00)?\d[\d\s-]{7,15}\d')


def parse_voice(text, industry=''):
    """Structured data from a voice note / call transcript (industry fields of `industry` when given)."""
    from ZeoMasters.models import Product
    from .models import IndustryField
    t = text or ''
    out = {'email': (EMAIL_RE.search(t) or [None])[0] if EMAIL_RE.search(t) else '', 'phone': '', 'name': '', 'company': '', 'amount': '', 'when': '', 'products': [],
           'fields': {}, 'intent': 'note'}
    m = PHONE_RE.search(t)
    if m:
        out['phone'] = re.sub(r'[\s-]', '', m.group(0))
    m = re.search(r"\b(?:this is|i am|i'm|my name is|name is|spoke (?:to|with)|met|call(?:ed)? from)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)", t)
    if m:
        out['name'] = m.group(1)
    m = re.search(r'\b(?:from|at|of|company)\s+([A-Z][\w&.-]*(?:\s+[A-Z][\w&.-]*){0,3}(?:\s+(?:LLC|FZE|FZCO|FZ-LLC|Ltd|Limited|Inc|Group|Co\.?))?)', t)
    if m:
        out['company'] = m.group(1).strip()
    m = AMOUNT.search(t)
    if m:
        out['amount'] = m.group(0)
    m = WHEN.search(t)
    if m:
        out['when'] = m.group(0)
    low = t.lower()
    for p in Product.objects.filter(is_active=True).only('id', 'name')[:2000]:
        if len(p.name) >= 4 and p.name.lower() in low:
            out['products'].append({'id': p.pk, 'name': p.name})
    fq = IndustryField.objects.filter(is_active=True, field_type__in=('number', 'money'))
    if industry:
        fq = fq.filter(Q(industry=industry) | Q(industry=''))
    used_words = set()
    for f in fq.order_by('order', 'id')[:500]:
        lab = f.label.lower().split('(')[0].strip()
        pats = [rf'(\d[\d,.]*)\s*(?:{re.escape(lab)})|(?:{re.escape(lab)})\D{{0,12}}(\d[\d,.]*)']
        if f.unit and len(f.unit) <= 12 and not f.unit.lower().startswith(('month', 'day', 'week', 'hour', '%')):
            pats.append(rf'(\d[\d,.]*)\s*{re.escape(f.unit.lower())}\b()')   # "40 kW", "12 MT"
        w = (re.findall(r'[a-z]{4,}', lab) or [''])[0]
        if w and w not in used_words and industry:
            pats.append(rf'(\d[\d,.]*)\s*{re.escape(w)}s?\b()')              # "12 racks" for "Rack quantity"
        for pat in pats:
            mm = re.search(pat, low)
            if mm:
                v = (mm.group(1) or mm.group(2) or '').replace(',', '').rstrip('.')
                try:
                    out['fields'][f.code] = float(v) if '.' in v else int(v)
                    if w:
                        used_words.add(w)
                    break
                except ValueError:
                    pass
    out['intent'] = 'complaint' if re.search(r'\b(complain|complaint|not working|broken|problem|issue|angry|refund)\b', low) else \
        'enquiry' if re.search(r'\b(need|require|quote|quotation|price|interested|looking for|enquir)\b', low) else \
        'follow_up' if re.search(r'\b(follow up|call back|remind|next week|tomorrow)\b', low) else 'note'
    return out


# v1.73.2: trade words that name an industry better than the field labels do (each counts like a word of the industry's name)
TRADE_WORDS = {
    'hvac': 'hvac chiller chillers split ducted cooling airconditioning conditioner conditioners vrf ahu fcu refrigerant',
    'logistics': 'freight cargo shipment shipping warehouse warehousing courier delivery deliveries trucking customs clearance',
    'retail': 'shop shops store stores outlet outlets showroom pos supermarket ecommerce',
    'it': 'software network networking server servers laptops cloud cybersecurity erp website',
    'construction': 'villa villas building contractor contracting civil fitout',
    'fm': 'cleaning janitorial security guards facility facilities',
    'hospitality': 'hotel hotels resort resorts restaurant restaurants catering',
    'healthcare': 'clinic clinics hospital hospitals patients pharmacy',
    'fire': 'sprinkler sprinklers extinguisher extinguishers alarm', 'cctv': 'camera cameras surveillance access',
    'solar': 'solar panels photovoltaic inverter', 'lift': 'elevator elevators escalator escalators',
    'pest': 'termite termites rodents fumigation', 'pool': 'swimming pools', 'education': 'school schools students nursery training',
    'marine': 'vessel vessels boat boats yacht yachts', 'oil_gas': 'drilling pipeline refinery offshore',
}
VAGUE = {'budget', 'start', 'starting', 'year', 'years', 'month', 'months', 'week', 'weeks', 'price', 'prices', 'cost', 'costs', 'looking', 'want', 'like',
         'please', 'contact', 'call', 'quote', 'quotation', 'details', 'information', 'around', 'just', 'some', 'urgent', 'dubai', 'sharjah', 'abu', 'dhabi'}


def detect_industry(text):
    """[(pack code, label, score, matched words)] best first, from the pack labels, descriptions and field labels."""
    app = CTX['app']
    words = set(re.findall(r'[a-z]{4,}', (text or '').lower())) | set(re.findall(r'\b(?:it|pos|erp|vrf|ahu|fcu|ac)\b', (text or '').lower()))
    stop = {'with', 'from', 'that', 'this', 'have', 'need', 'their', 'your', 'will', 'more', 'than', 'they', 'into', 'about', 'other', 'industry', 'sales', 'customer',
            'requirement', 'service', 'services', 'management', 'project', 'projects', 'company'}
    stop |= VAGUE
    words -= stop
    t = (text or '').lower()
    words |= {'hvac'} if re.search(r'\b(a/?c|air[ -]?con(ditioning)?|hvac)\b', t) else set()
    words |= {'airconditioning'} if 'air conditioning' in t else set()
    out = []
    for code, p in app.packs.items():
        if code == 'general':
            continue
        vocab = ' '.join([p.get('label', ''), p.get('description', '')] + [f.get('label', '') for f in p.get('rows', {}).get('field', [])]).lower()
        vw = set(re.findall(r'[a-z]{4,}', vocab)) - stop
        hit = words & vw
        # v1.73.2: a word of the industry's own name weighs more ("HVAC maintenance for hotels" was Information technology on "maintenance, year")
        own = words & ((set(re.findall(r'[a-z]{4,}', (p.get('label', '') + ' ' + code).lower())) | set(TRADE_WORDS.get(code, '').split()) | ({code} if len(code) < 4 else set())) - stop)
        hit |= own
        if hit:
            out.append((code, p['label'], len(hit) + 3 * len(own), sorted(own) + sorted(hit - own)[:6 - len(own)]))
    out.sort(key=lambda x: -x[2])
    return out


# ------------------------------------------------------------------ products: quotation assistant, upselling
def _baskets():
    """{quotation / order id: set(product ids)} of accepted quotations and sales orders."""
    b = defaultdict(set)
    for qid, pid in QuotationLine.objects.filter(quotation__status='accepted', product__isnull=False).values_list('quotation_id', 'product_id')[:50000]:
        b[('q', qid)].add(pid)
    for sid, pid in SalesOrderLine.objects.filter(product__isnull=False).exclude(sales_order__status='cancelled').values_list('sales_order_id', 'product_id')[:50000]:
        b[('s', sid)].add(pid)
    return b


def recommend(have, industry='', account=None, n=5):
    """Products that go with `have` (co-occurrence), then the best sellers of the industry, without what the account already bought."""
    from ZeoMasters.models import Product
    baskets = _baskets()
    score = Counter()
    for items in baskets.values():
        if have & items:
            for p in items - have:
                score[p] += len(have & items)
    bought = set()
    if account is not None:
        bought = set(QuotationLine.objects.filter(quotation__account=account, quotation__status='accepted', product__isnull=False).values_list('product_id', flat=True)) | \
            set(SalesOrderLine.objects.filter(sales_order__account=account, product__isnull=False).values_list('product_id', flat=True))
    if industry:
        for pid, c in QuotationLine.objects.filter(quotation__industry=industry, quotation__status='accepted', product__isnull=False).values_list('product_id') \
                .annotate(c=Count('id')).values_list('product_id', 'c')[:200]:
            if pid not in have:
                score[pid] += 0.3 * c
    for p in list(score):
        if p in bought or p in have:
            del score[p]
    names = {p.pk: p for p in Product.objects.filter(pk__in=[p for p, _ in score.most_common(n)], is_active=True)}
    return [(names[p], round(s, 1)) for p, s in score.most_common(n) if p in names]


# ------------------------------------------------------------------ next actions, lost deals, campaigns, coaching
def next_actions():
    made = 0
    t = now()
    for o in Opportunity.objects.filter(status='open').select_related('stage', 'owner', 'contact'):
        last = o.activities.filter(status='done').order_by('-done_at').values_list('done_at', flat=True).first()
        open_acts = o.activities.filter(status='open').exists()
        tips = []
        if not open_acts and (last is None or (t - last).days >= 7):
            tips.append(('call', f'No contact for {(t - last).days if last else (t - o.created_at).days} days – call {o.contact.name}', 'call'))
        q = Quotation.objects.filter(opportunity=o, status='sent').order_by('-sent_at').first()
        if q and q.sent_at and (t - q.sent_at).days >= 5:
            tips.append(('quote', f'Quotation {q.number} sent {(t - q.sent_at).days} days ago – follow it up', 'call'))
        if o.expected_close and o.expected_close < today():
            tips.append(('close', f'Expected close {o.expected_close:%d/%m/%Y} has passed – update the date or the stage', 'task'))
        if not o.decision_makers.exists() and o.stage_id and o.stage.probability >= 40:
            tips.append(('dm', 'No decision maker recorded – find out who decides', 'task'))
        for code, text, kind in tips:
            suggest('next_action', f'{o.number}: {text}', text, {'activity': {'kind': kind, 'subject': text[:200], 'due_days': 1}}, key=f'next:{o.pk}:{code}',
                    opportunity=o, contact=o.contact, owner=o.owner)
            made += 1
    for l in Lead.objects.filter(status__in=('new', 'working'), first_contacted_at__isnull=True, created_at__lt=t - timedelta(hours=24)):
        suggest('next_action', f'{l.name}: not contacted yet', f'Created {(t - l.created_at).days} day(s) ago and nobody called. AI score {l.ai_score or "–"} %.',
                {'activity': {'kind': 'call', 'subject': f'First call – {l.name}', 'due_days': 0}}, key=f'next:lead:{l.pk}', lead=l, owner=l.owner)
        made += 1
    return made


def lost_analysis(days=180, request=None):
    qs = Opportunity.objects.filter(status='lost', closed_at__gte=now() - timedelta(days=days)).select_related('lost_reason')
    total = qs.count()
    if not total:
        return 'No lost opportunities in the period.', 'built-in', []
    by = Counter((o.lost_reason.name if o.lost_reason_id else 'No reason') for o in qs)
    value = defaultdict(float)
    for o in qs:
        value[o.lost_reason.name if o.lost_reason_id else 'No reason'] += float(o.value or 0)
    words = Counter(w for o in qs for w in re.findall(r'[a-z]{4,}', (o.lost_note or '').lower()) if w not in ('with', 'that', 'they', 'were', 'have', 'from', 'this', 'their'))
    comp = Counter(c.name for o in qs for c in o.competitors.all())
    lines = [f'{total} opportunities lost in {days} days.'] + [f'• {r}: {n} ({100 * n // total} %) – {value[r]:,.0f} value' for r, n in by.most_common(6)]
    if comp:
        lines.append('Lost to: ' + ', '.join(f'{c} ({n})' for c, n in comp.most_common(4)))
    if words:
        lines.append('Words in the notes: ' + ', '.join(w for w, _ in words.most_common(8)))
    top = by.most_common(1)[0][0]
    tip = {'Price': 'Review the price book and discount rules, show value / ROI earlier, offer payment terms.',
           'Chose a competitor': 'Record competitors on every deal, prepare battle cards, involve presales sooner.',
           'No response': 'Shorten follow-up gaps (AI next actions), use WhatsApp, qualify harder before quoting.',
           'Timing': 'Move these to nurture with a reminder date instead of losing them.'}.get(top, 'Look at the deals of the main reason together with the agents.')
    lines.append('Suggestion: ' + tip)
    text, eng = '\n'.join(lines), 'built-in'
    ext = llm(request, 'You are a sales analyst. From the lost-deal statistics write 4 short bullet points: main causes and what to change.', text)
    if ext:
        text, eng = text + '\n\nAI analysis:\n' + ext, engine_name()
    rows = [{'reason': r, 'lost': n, 'share': round(100 * n / total, 1), 'value': round(value[r], 2)} for r, n in by.most_common()]
    return text, eng, rows


def campaign_tips():
    made = 0
    for c in Campaign.objects.filter(is_active=True):
        leads = Lead.objects.filter(campaign=c)
        n = leads.count()
        won = float(Opportunity.objects.filter(campaign=c, status='won').aggregate(v=Sum('value'))['v'] or 0)
        cost = float(c.spent or c.budget or 0)
        conv = leads.filter(status='converted').count()
        tips = []
        if cost and n and cost / n > 0 and won < cost and n >= 10:
            tips.append(f'Spent {cost:,.0f} for {n} leads and {won:,.0f} won – return below cost. Narrow the audience or move budget to better campaigns.')
        if n >= 10 and conv / n < 0.05:
            tips.append(f'Only {conv} of {n} leads converted ({100 * conv / n:.0f} %). Check lead quality and the first-contact time.')
        sends = CampaignSend.objects.filter(campaign=c, status='sent', channel='email', sent__gt=0)
        for s in sends:
            if s.sent >= 20 and s.opened / s.sent < 0.15:
                tips.append(f'Send {s.number}: {100 * s.opened / s.sent:.0f} % opened – try another subject line or send time.')
        hours = Counter()
        for r in Lead.objects.filter(campaign=c).values_list('created_at', flat=True)[:2000]:
            hours[r.hour] += 1
        if sum(hours.values()) >= 20:
            h = hours.most_common(1)[0][0]
            tips.append(f'Most leads of this campaign come in around {h:02d}:00 – schedule sends and agent availability for that time.')
        if tips:
            suggest('campaign', f'Campaign {c.name}: {len(tips)} tip(s)', '\n'.join(f'• {t}' for t in tips), key=f'campaign:{c.pk}:{today():%Y-%m}', campaign=c, owner=c.owner)
            made += 1
    return made


def coaching(days=90):
    """Per agent against the team: win rate, deals, activities per deal, first-contact time, discount, cycle."""
    since = now() - timedelta(days=days)
    stats = {}
    for o in Opportunity.objects.filter(closed_at__gte=since, status__in=('won', 'lost')).select_related('owner'):
        if not o.owner_id:
            continue
        s = stats.setdefault(o.owner_id, {'emp': o.owner, 'won': 0, 'lost': 0, 'cycle': [], 'acts': 0, 'resp': [], 'disc': []})
        s['won' if o.status == 'won' else 'lost'] += 1
        s['cycle'].append((o.closed_at - o.created_at).days)
        s['acts'] += o.activities.filter(status='done').count()
        if o.first_contacted_at:
            s['resp'].append((o.first_contacted_at - o.created_at).total_seconds() / 3600)
    for q in Quotation.objects.filter(date__gte=since.date()).exclude(owner=None):
        if q.owner_id in stats and q.subtotal:
            stats[q.owner_id]['disc'].append(float(q.discount_total or 0) / float(q.subtotal) * 100)
    avg = lambda xs: sum(xs) / len(xs) if xs else None   # noqa: E731
    rows = []
    for k, s in stats.items():
        n = s['won'] + s['lost']
        rows.append({'emp': s['emp'], 'deals': n, 'win': 100 * s['won'] / n if n else 0, 'cycle': avg(s['cycle']), 'acts': s['acts'] / n if n else 0,
                     'resp': avg(s['resp']), 'disc': avg(s['disc'])})
    team = {k: avg([r[k] for r in rows if r[k] is not None]) for k in ('win', 'cycle', 'acts', 'resp', 'disc')}
    for r in rows:
        tips = []
        if team['win'] and r['win'] < team['win'] - 10:
            tips.append(f'win rate {r["win"]:.0f} % vs team {team["win"]:.0f} % – qualify harder before quoting')
        if team['acts'] and r['acts'] < team['acts'] * 0.6:
            tips.append(f'{r["acts"]:.1f} activities per deal vs {team["acts"]:.1f} – follow up more often')
        if r['resp'] is not None and team['resp'] and r['resp'] > team['resp'] * 1.5:
            tips.append(f'first contact after {r["resp"]:.0f} h vs {team["resp"]:.0f} h – call new enquiries sooner')
        if r['disc'] is not None and team['disc'] and r['disc'] > team['disc'] + 3:
            tips.append(f'average discount {r["disc"]:.1f} % vs {team["disc"]:.1f} % – sell value before price')
        if r['cycle'] and team['cycle'] and r['cycle'] > team['cycle'] * 1.4:
            tips.append(f'deals take {r["cycle"]:.0f} days vs {team["cycle"]:.0f} – agree next steps and dates with the customer')
        r['tips'] = tips
    return rows, team


# ------------------------------------------------------------------ e-mail drafts
PURPOSES = (('follow_up', 'Follow-up'), ('quotation', 'Send the quotation'), ('meeting', 'Ask for a meeting'), ('thank_you', 'Thank you'),
            ('renewal', 'Renewal'), ('apology', 'Apology (complaint)'), ('reply', 'Reply to the last message'))


def draft_email(o, purpose, request=None, extra=''):
    from .marketing import ctx_for
    c = ctx_for(o)
    last = ''
    conv = None
    contact = o if isinstance(o, Contact) else getattr(o, 'contact', None)
    if contact is not None:
        conv = Conversation.objects.filter(contact=contact).order_by('-last_message_at').first()
    if conv is not None:
        m = conv.messages.filter(direction='in').order_by('-at').first()
        last = m.text if m else ''
    facts = f'Customer: {c["name"]} ({c["company"]}). Subject: {c["title"]}. Value: {c["value"]}. Agent: {c["agent"]}. Last message from the customer: {last[:800]}. {extra}'
    ext = llm(request, 'Write a short, polite business e-mail for a salesperson in the UAE. Plain text. First line "Subject: …". No made-up facts.',
              f'Purpose: {dict(PURPOSES).get(purpose, purpose)}\n{facts}')
    if ext:
        m = re.match(r'\s*subject:\s*(.+)\n', ext, re.I)
        subject = m.group(1).strip() if m else f'{c["title"]}'
        body = ext[m.end():].strip() if m else ext
        return subject, body, engine_name()
    first = c['first_name'] or 'Sir / Madam'
    sign = f'\n\nKind regards,\n{c["agent"] or "Sales team"}'
    T = {'follow_up': (f'Following up – {c["title"]}', f'Dear {first},\n\nI hope you are well. I am following up on {c["title"] or "our discussion"}. '
                       f'Could you let me know if you have any questions, or if a short call this week would help to move forward?'),
         'quotation': (f'Quotation {c["number"]} – {c["company"] or c["name"]}', f'Dear {first},\n\nPlease find our quotation {c["number"]} for {c["title"]}'
                       + (f' ({c["value"]})' if c['value'] else '') + '. I would be glad to go through it with you and adjust it to your needs.'),
         'meeting': (f'Meeting – {c["title"]}', f'Dear {first},\n\nThank you for your interest. Would you have 30 minutes this week or next to discuss your requirements? '
                     f'Please suggest a time that suits you, or I can come to your office.'),
         'thank_you': (f'Thank you, {c["company"] or c["name"]}', f'Dear {first},\n\nThank you for choosing us. We look forward to working with you – '
                       f'I will stay your contact for anything you need.'),
         'renewal': (f'Renewal – {c["title"]}', f'Dear {first},\n\nYour contract / service {c["title"]} is coming up for renewal. I would like to share the renewal '
                     f'terms and any improvements we can offer. When would be a good time to talk?'),
         'apology': (f'We are sorry – {c["title"]}', f'Dear {first},\n\nI am sorry for the trouble with {c["title"]}. We are looking into it and will update you '
                     f'within the agreed time. Thank you for your patience.'),
         'reply': (f'Re: {c["title"]}', f'Dear {first},\n\nThank you for your message.' + (f' Regarding "{last[:120]}", ' if last else ' ') +
                   'I will come back to you shortly with the details.')}
    subject, body = T.get(purpose, T['follow_up'])
    return subject, body + sign, 'built-in'


# ------------------------------------------------------------------ accepting a suggestion
def accept(s, request):
    app = CTX['app']
    d_ = s.data or {}
    done = []
    target = s.opportunity or s.lead
    if d_.get('lead_status') and s.lead_id:
        l = s.lead
        st = d_['lead_status']
        if st == 'qualified':
            l.status = 'qualified'
        elif st == 'nurture':
            l.status = 'nurture'
        elif st == 'disqualified':
            l.status = 'disqualified'
            l.disqualify_reason = LostReason.objects.filter(code='NOTFIT').first() or LostReason.objects.first()
        elif st == 'working':
            l.status = 'working'
        l.save()
        app.event('lead', l.pk, f'Status → {l.get_status_display()} (AI suggestion {s.number} accepted by {user_name(request.user)})', request.user)
        done.append(f'lead {l.get_status_display().lower()}')
    if d_.get('activity'):
        a = d_['activity']
        act = Activity.objects.create(number=app.next_number('activity', 'ACT'), kind=a.get('kind', 'task'), subject=a.get('subject', s.title)[:200],
                                      contact=s.contact or getattr(target, 'contact', None), opportunity=s.opportunity, lead=s.lead, owner=s.owner,
                                      due_at=now() + timedelta(days=a.get('due_days', 1)), status='open', branch_id=s.branch_id)
        done.append(f'activity {act.number}')
    if d_.get('fields') and target is not None:
        vals = dict(target.extra or {})
        vals.update(d_['fields'])
        target.extra = vals
        target.save(update_fields=['extra'])
        done.append('industry fields filled')
    if d_.get('industry') and s.lead_id:
        Lead.objects.filter(pk=s.lead_id).update(industry=d_['industry'])
        done.append(f'industry {d_["industry"]}')
    if d_.get('lead_updates') and s.lead_id:
        Lead.objects.filter(pk=s.lead_id).update(**{k: v for k, v in d_['lead_updates'].items() if k in ('company_name', 'job_title', 'city', 'country')})
        if d_.get('account_id'):
            Lead.objects.filter(pk=s.lead_id).update(account_id=d_['account_id'])
        done.append('lead details filled')
    if d_.get('products') and s.opportunity_id:
        q = Quotation.objects.filter(opportunity=s.opportunity, status__in=('draft', 'approval')).order_by('-id').first()
        if q is not None:
            old = Quotation.objects.get(pk=q.pk)
            q.items = list(q.items or []) + [{'product': p, 'qty': 1, 'unit_price': None, 'discount_pct': 0} for p in d_['products']]
            app.regs['quotation'].saver(q, request, False, old)
            done.append(f'{len(d_["products"])} line(s) added to {q.number}')
    if d_.get('new_lead'):
        from .marketing import new_lead
        nl = d_['new_lead']
        l = new_lead(nl.get('name') or 'Voice note', phone=nl.get('phone', ''), email_=nl.get('email', ''), message=nl.get('message', ''), channel='phone',
                     company=nl.get('company', ''), extra_fields=nl.get('fields') or None)
        done.append(f'lead {l.number}')
        s.lead = l
    if d_.get('send_email') and s.contact_id and s.contact.email:
        from .marketing import send_email
        ok, res = send_email([s.contact.email], d_['send_email'].get('subject', ''), d_['send_email'].get('body', ''))
        done.append('e-mail sent' if ok else f'e-mail not sent ({res})')
    s.status, s.decided_by, s.decided_at = 'accepted', request.user, now()
    s.save()
    app.event('ai_suggestion', s.pk, 'Accepted' + (': ' + ', '.join(done) if done else ''), request.user)
    return {'message': 'Accepted' + (': ' + ', '.join(done) if done else '.')}


# ------------------------------------------------------------------ reports
def reports(a):
    from ZeoApps.reports import SLUG_PAGE
    codes = [a.code(r) for r, _ in a.roles]
    page = SLUG_PAGE['crm']

    def lost(run):
        days = max(1, (run.date_to - run.date_from).days) if run.date_from.year > 1900 else 365
        text, eng, rows = lost_analysis(days, run.request)
        return [('reason', 'Lost reason', 'text'), ('lost', 'Lost', 'number'), ('share', 'Share %', 'number'), ('value', 'Value lost', 'number')], rows, \
            {'notes': text.split('\n')[-1:] + [f'Engine: {eng}.'], 'no_totals': ['share']}

    def coach(run):
        rows, team = coaching()
        out = [{'agent': emp_name(r['emp']), 'deals': r['deals'], 'win': round(r['win'], 1), 'acts': round(r['acts'], 1),
                'resp': round(r['resp'], 1) if r['resp'] is not None else None, 'disc': round(r['disc'], 1) if r['disc'] is not None else None,
                'cycle': round(r['cycle']) if r['cycle'] is not None else None, 'tips': '; '.join(r['tips']) or 'On or above the team average'} for r in rows]
        return [('agent', 'Agent', 'text'), ('deals', 'Closed deals (90 days)', 'number'), ('win', 'Win rate %', 'number'), ('acts', 'Activities per deal', 'number'),
                ('resp', 'First contact (hours)', 'number'), ('disc', 'Average discount %', 'number'), ('cycle', 'Sales cycle (days)', 'number'), ('tips', 'Coaching', 'text')], out, \
            {'notes': [f'Team: win {team["win"] or 0:.0f} %, {team["acts"] or 0:.1f} activities per deal.'], 'no_totals': ['win', 'acts', 'resp', 'disc', 'cycle']}

    def forecast(run):
        qs = a.regs['opportunity'].scope(run.request).filter(status='open').select_related('stage', 'owner')
        data = defaultdict(lambda: {'open': 0, 'value': 0.0, 'stage_w': 0.0, 'ai_w': 0.0})
        for o in qs:
            k = o.expected_close.strftime('%Y-%m') if o.expected_close else 'No close date'
            v = float(o.value or 0)
            data[k]['open'] += 1
            data[k]['value'] += v
            data[k]['stage_w'] += v * (o.stage.probability if o.stage_id else 0) / 100
            data[k]['ai_w'] += v * (o.ai_probability if o.ai_probability is not None else (o.stage.probability if o.stage_id else 0)) / 100
        rows = [{'month': k, **{x: round(y, 2) for x, y in v.items()}} for k, v in sorted(data.items())]
        return [('month', 'Expected close', 'text'), ('open', 'Open', 'number'), ('value', 'Pipeline', 'number'), ('stage_w', 'Weighted by stage', 'number'),
                ('ai_w', 'AI forecast', 'number')], rows

    def scores(run):
        qs = a.regs['lead'].scope(run.request).filter(created_at__date__gte=run.date_from, created_at__date__lte=run.date_to, ai_score__isnull=False)
        data = defaultdict(lambda: {'leads': 0, 'converted': 0, 'disqualified': 0, 'open': 0})
        for l in qs:
            b = '80–100 %' if l.ai_score >= 80 else '60–79 %' if l.ai_score >= 60 else '40–59 %' if l.ai_score >= 40 else '20–39 %' if l.ai_score >= 20 else '0–19 %'
            data[b]['leads'] += 1
            data[b]['converted' if l.status == 'converted' else 'disqualified' if l.status == 'disqualified' else 'open'] += 1
        rows = [{'band': k, **v, 'rate': round(100 * v['converted'] / (v['converted'] + v['disqualified']), 1) if (v['converted'] + v['disqualified']) else None}
                for k, v in sorted(data.items(), reverse=True)]
        return [('band', 'AI score', 'text'), ('leads', 'Leads', 'number'), ('open', 'Open', 'number'), ('converted', 'Converted', 'number'),
                ('disqualified', 'Disqualified', 'number'), ('rate', 'Actual conversion %', 'number')], rows, {'no_totals': ['rate']}

    def sugg(run):
        qs = a.regs['ai_suggestion'].scope(run.request).filter(created_at__date__gte=run.date_from, created_at__date__lte=run.date_to)
        rows = []
        lab = dict(AISuggestion._meta.get_field('kind').choices)
        for x in qs.values('kind').annotate(n=Count('id'), acc=Count('id', filter=Q(status='accepted')), dis=Count('id', filter=Q(status='dismissed'))).order_by('-n'):
            rows.append({'kind': lab.get(x['kind'], x['kind']), 'made': x['n'], 'accepted': x['acc'], 'dismissed': x['dis'],
                         'rate': round(100 * x['acc'] / (x['acc'] + x['dis']), 1) if (x['acc'] + x['dis']) else None})
        return [('kind', 'Feature', 'text'), ('made', 'Suggestions', 'number'), ('accepted', 'Accepted', 'number'), ('dismissed', 'Dismissed', 'number'),
                ('rate', 'Acceptance %', 'number')], rows, {'no_totals': ['rate']}

    return {'crm-ai-lost': ('AI lost deal analysis', 'CRM', 'Lost opportunities by reason with share and value, the words of the notes, competitors and a suggestion.', lost, 'year', codes),
            'crm-ai-coaching': ('AI sales coaching', 'CRM', 'Every agent against the team (last 90 days): win rate, activities per deal, first-contact time, discount, cycle, with tips.', coach, None, codes),
            'crm-ai-forecast': ('AI sales forecast', 'CRM', 'Open pipeline by expected close month: weighted by stage and by the AI win chance learnt from your history.', forecast, None, codes),
            'crm-ai-lead-scores': ('AI lead scores', 'CRM', 'Leads in the period by AI score band with the actual conversion – how good the score is.', scores, 'year', codes),
            'crm-ai-suggestions': ('AI suggestions', 'CRM', 'AI suggestions made in the period by feature: accepted, dismissed, acceptance %.', sugg, 'year', codes)}


# ------------------------------------------------------------------ jobs
def daily_ai():
    out = {}
    if not CTX['app'].settings().ai_enabled:
        return {'ai': 'off'}
    for name, fn in (('ai_lead_scores', score_leads), ('ai_win_chances', forecast_opps), ('ai_churn', churn_scores), ('ai_next_actions', next_actions),
                     ('ai_campaign_tips', campaign_tips)):
        try:
            out[name] = fn()
        except Exception:   # noqa: BLE001
            log.exception('AI %s', name)
    try:
        for p in CustomerProfile.objects.all():
            texts = list(ConversationMessage.objects.filter(conversation__contact__account=p.account, direction='in').order_by('-at').values_list('text', flat=True)[:30])
            texts += list(Complaint.objects.filter(account=p.account).exclude(feedback='').values_list('feedback', flat=True)[:10])
            if texts:
                p.sentiment = sentiment_of(texts)[0]
                p.save(update_fields=['sentiment'])
    except Exception:   # noqa: BLE001
        log.exception('AI sentiment')
    try:
        for p in CustomerProfile.objects.filter(won_count__gt=0)[:500]:
            have = set(QuotationLine.objects.filter(quotation__account=p.account, quotation__status='accepted', product__isnull=False).values_list('product_id', flat=True))
            ind = Opportunity.objects.filter(account=p.account, status='won').values_list('industry', flat=True).first() or ''
            recs = recommend(have, ind, p.account, n=3)
            if recs and have:
                suggest('upsell', f'{p.account.name}: {len(recs)} product(s) to offer', 'Bought together by similar customers: ' + ', '.join(f'{x.name}' for x, _ in recs),
                        {'products': [x.pk for x, _ in recs]}, key=f'upsell:{p.account_id}:{"-".join(str(x.pk) for x, _ in recs)}', account=p.account, owner=p.account_manager)
    except Exception:   # noqa: BLE001
        log.exception('AI upsell')
    return out


# ------------------------------------------------------------------ registers and actions
def setup(app, MANAGE, VIEW, can_work, PAGE):
    CTX['app'] = app
    regs = app.regs
    ALLV = VIEW + ('crm_director',)

    def need_on():
        if not app.settings().ai_enabled:
            raise AppError('The CRM AI assistant is switched off (CRM setup).')

    def can_decide(s, r):
        return s.status == 'new' and (app.any(r, *MANAGE) or getattr(app.my_emp(r), 'pk', 0) == s.owner_id)

    def a_accept(o, request, f):
        return accept(o, request)

    def a_dismiss(o, request, f):
        o.status, o.decided_by, o.decided_at = 'dismissed', request.user, now()
        o.save()
        app.event('ai_suggestion', o.pk, 'Dismissed' + (f' – {f.s("note")}' if f.s('note') else ''), request.user)
        return o

    app.reg(Reg('ai_suggestion', AISuggestion, 'AI suggestion', 'AI suggestions', 'Sales',
                cols=('number', 'kind', 'title', 'confidence', 'owner', 'engine', 'created_at', 'status'),
                form=(), number=('ai_suggestion', 'AI'), manage=MANAGE, view=ALLV, create='none', own=(('owner', 'emp'),),
                search=('number', 'title', 'text'), filters=('status', 'kind', 'owner', 'engine'), title='title', subtitle='kind', files=False, edit_when=lambda o: False,
                info=('text', 'lead', 'opportunity', 'contact', 'account', 'conversation', 'campaign', 'decided_by', 'decided_at'),
                actions=[Act('accept', 'Accept', a_accept, can_decide, style='primary'),
                         Act('dismiss', 'Dismiss', a_dismiss, can_decide, fields=(('note', 'Why', 'text', False),))],
                help='What the CRM AI suggests – nothing changes until you accept it. Scores and forecasts are shown on leads, opportunities and customer profiles.'))
    for k, fk in (('lead', 'lead'), ('opportunity', 'opportunity'), ('contact', 'contact'), ('conversation', 'conversation'), ('campaign', 'campaign')):
        r_ = regs.get(k)
        if r_ is not None:
            r_.children = tuple(r_.children) + (('AI suggestions', 'ai_suggestion', fk),)

    # lead: score, enrich, qualify, industry, voice
    def a_ai_score(o, request, f):
        need_on()
        m = lead_model()
        if m.size >= 20:
            p, parts = m.predict(lead_features(o))
            o.ai_score, o.ai_reason = int(round(p * 100)), _why(parts)[:300]
        else:
            o.ai_score, o.ai_reason = max(0, min(100, int(o.score or 0))), f'Learning – only {m.size} closed leads so far; the rule score is used.'
        o.save(update_fields=['ai_score', 'ai_reason'])
        return {'message': f'AI score {o.ai_score} % – {o.ai_reason or "no strong signals"}'}

    def a_ai_enrich(o, request, f):
        need_on()
        from ZeoMasters.models import Account
        dom = (o.email.split('@')[-1].lower() if o.email and '@' in o.email else '')
        up = {}
        acc = None
        if dom and dom not in FREE_MAIL:
            acc = Account.objects.filter(Q(website__icontains=dom) | Q(email__iendswith='@' + dom)).first()
            if not o.company_name:
                up['company_name'] = acc.name if acc else dom.split('.')[0].replace('-', ' ').title()
        num = ''
        try:
            from .marketing import norm_phone
            num = norm_phone(o.phone)
        except Exception:   # noqa: BLE001
            pass
        cc = {'+971': 'United Arab Emirates', '+966': 'Saudi Arabia', '+974': 'Qatar', '+968': 'Oman', '+973': 'Bahrain', '+965': 'Kuwait', '+91': 'India',
              '+44': 'United Kingdom', '+1': 'United States', '+20': 'Egypt', '+92': 'Pakistan', '+63': 'Philippines'}
        if num and not o.country:
            for k, v in sorted(cc.items(), key=lambda x: -len(x[0])):
                if num.startswith(k):
                    up['country'] = v
                    break
        if not up and acc is None:
            return {'message': 'Nothing to add from the e-mail domain and the phone number (free e-mail address or details already there).'}
        text = '\n'.join(f'{k.replace("_", " ")}: {v}' for k, v in up.items()) + (f'\nExisting account: {acc.name} ({acc.number})' if acc else '')
        s = suggest('enrich', f'{o.name}: details found', text + '\nSource: the e-mail domain and the phone country code (no outside service).',
                    {'lead_updates': up, 'account_id': acc.pk if acc else None}, key=f'enrich:{o.pk}', confidence=60, lead=o)
        return {'message': f'Suggestion {s.number}: ' + text.replace('\n', '; ')}

    def a_ai_qualify(o, request, f):
        need_on()
        s = qualify(o, request)
        return {'message': f'{s.title} – {s.text.splitlines()[0]} (suggestion {s.number})'}

    def a_ai_industry(o, request, f):
        need_on()
        text = ' '.join(str(x or '') for x in (o.company_name, o.interest, o.requirements, o.job_title, f.s('text')))
        found = detect_industry(text)
        if not found:
            return {'message': 'No industry recognised in the requirement – describe the business or pick the industry.'}
        code, label, sc, hit = found[0]
        app_ = app
        qs = [x['label'] for x in app_.packs[code]['rows'].get('field', [])][:8]
        s = suggest('industry', f'{o.name}: industry "{label}"', f'Matched: {", ".join(hit)}.\nQuestions to ask: ' + '; '.join(qs) +
                    (f'\nOther possible industries: {", ".join(x[1] for x in found[1:3])}' if len(found) > 1 else ''),
                    {'industry': code}, key=f'industry:{o.pk}:{code}', confidence=min(95, 30 + 10 * sc), lead=o)
        return {'message': f'{label} ({", ".join(hit)}) – suggestion {s.number} with the questions to ask.'}

    def a_ai_voice(o, request, f):
        need_on()
        text = f.s('text')
        if len(text) < 10:
            raise AppError('Paste the voice note / call transcript.', 'text')
        p = parse_voice(text, getattr(o, 'industry', '') or '')
        summ, eng = summarise(text, request, 'call / voice note')
        app.event(regs_kind(o), o.pk, 'Voice note: ' + summ[:300], request.user)
        Activity.objects.create(number=app.next_number('activity', 'ACT'), kind='note', subject='Voice note', contact=getattr(o, 'contact', None) if not isinstance(o, Contact) else o,
                                lead=o if isinstance(o, Lead) else None, opportunity=o if isinstance(o, Opportunity) else None, owner=getattr(o, 'owner', None) or app.my_emp(request),
                                status='done', done_at=now(), outcome=f'{summ}\n\n---\n{text}'[:4000])
        data, made = {}, []
        if p['fields'] and isinstance(o, (Lead, Opportunity)):
            data['fields'] = p['fields']
            made.append('fields ' + ', '.join(f'{k}={v}' for k, v in p['fields'].items()))
        if p['intent'] in ('follow_up', 'enquiry') and p['when']:
            data['activity'] = {'kind': 'call', 'subject': f'Follow up ({p["when"]})', 'due_days': 1 if p['when'].lower() in ('tomorrow',) else 3}
            made.append(f'follow-up {p["when"]}')
        if p['products'] and isinstance(o, Opportunity):
            data['products'] = [x['id'] for x in p['products']]
            made.append('products ' + ', '.join(x['name'] for x in p['products']))
        s = suggest('voice', f'Voice note: {", ".join(made) or "summary"}', summ + '\n\nFound: ' + ', '.join(f'{k}: {v}' for k, v in p.items() if v and k not in ('fields', 'products')),
                    data, engine=eng, lead=o if isinstance(o, Lead) else None, opportunity=o if isinstance(o, Opportunity) else None,
                    contact=o if isinstance(o, Contact) else getattr(o, 'contact', None))
        return {'message': f'Saved as a note. Suggestion {s.number}: {", ".join(made) or "nothing to change"}.'}

    def regs_kind(o):
        return next((k for k, r in regs.items() if r.model is type(o)), '')

    # opportunity: win chance, next actions, quotation assistant, e-mail, summary
    def a_ai_win(o, request, f):
        need_on()
        m = opp_model()
        stage_p = (o.stage.probability if o.stage_id else 10) / 100
        if m.size >= 20:
            p, parts = m.predict(opp_features(o))
            o.ai_probability, o.ai_reason = int(round((0.5 * p + 0.5 * stage_p) * 100)), _why(parts)[:300]
        else:
            o.ai_probability, o.ai_reason = int(stage_p * 100), f'Learning – only {m.size} closed deals so far; the stage probability is used.'
        Opportunity.objects.filter(pk=o.pk).update(ai_probability=o.ai_probability, ai_reason=o.ai_reason)
        return {'message': f'AI win chance {o.ai_probability} % (stage {int(stage_p * 100)} %) – {o.ai_reason}'}

    def a_ai_quote(o, request, f):
        need_on()
        have = set(QuotationLine.objects.filter(quotation__opportunity=o, product__isnull=False).values_list('product_id', flat=True))
        recs = recommend(have, o.industry, o.account, n=5)
        intro = ''
        ext = llm(request, 'Write a 3-sentence introduction for a sales proposal. Plain text, no made-up facts.',
                  f'Customer: {o.contact.name} {getattr(o.account, "name", "")}\nRequirement: {o.requirements}\nTechnical: {o.technical_requirements}\nIndustry: {o.industry}')
        if ext:
            intro = '\nProposal introduction:\n' + ext
        if not recs and not intro:
            return {'message': 'No product suggestion yet – there are not enough accepted quotations to learn from.'}
        s = suggest('quote', f'{o.number}: {len(recs)} product(s) to add', ('Often sold together / to this industry: ' + ', '.join(f'{p.name} ({sc})' for p, sc in recs) if recs else '') + intro,
                    {'products': [p.pk for p, _ in recs]} if recs else {}, key=f'quote:{o.pk}:{"-".join(str(p.pk) for p, _ in recs)}', opportunity=o, engine=engine_name() if ext else 'built-in')
        return {'message': f'Suggestion {s.number}: ' + (', '.join(p.name for p, _ in recs) or 'proposal text') + ' – accept to add them to the draft quotation.'}

    PURP = 'choice:' + '|'.join(f'{k}={v}' for k, v in PURPOSES)

    def a_ai_email(o, request, f):
        need_on()
        subject, body, eng = draft_email(o, f.s('purpose') or 'follow_up', request, f.s('note'))
        contact = o if isinstance(o, Contact) else getattr(o, 'contact', None)
        s = suggest('email', f'E-mail draft: {subject}', f'Subject: {subject}\n\n{body}', {'send_email': {'subject': subject, 'body': body}} if contact and contact.email else {},
                    engine=eng, contact=contact, lead=o if isinstance(o, Lead) else None, opportunity=o if isinstance(o, Opportunity) else None)
        return {'message': f'Draft {s.number} ready ({eng}) – open it under AI suggestions; Accept sends it to {getattr(contact, "email", "") or "the contact"}.\n\nSubject: {subject}\n\n{body}'}

    def a_ai_summary(o, request, f):
        need_on()
        if isinstance(o, Conversation):
            text = '\n'.join(f'{"Customer" if m.direction == "in" else "Us"}: {m.text}' for m in o.messages.order_by('at')[:80])
        else:
            text = f.s('text') or '\n'.join(a.outcome or a.subject for a in o.activities.filter(status='done').order_by('-done_at')[:15])
        if not text.strip():
            raise AppError('Nothing to summarise – paste the notes.', 'text')
        summ, eng = summarise(text, request, 'conversation' if isinstance(o, Conversation) else 'meeting notes')
        s = suggest('summary', f'Summary – {getattr(o, "number", "")}', summ, {}, engine=eng, conversation=o if isinstance(o, Conversation) else None,
                    opportunity=o if isinstance(o, Opportunity) else None, contact=getattr(o, 'contact', None) if not isinstance(o, Contact) else o)
        return {'message': summ}

    def a_ai_sentiment(o, request, f):
        need_on()
        contact = o if isinstance(o, Contact) else getattr(o, 'contact', None)
        texts = list(ConversationMessage.objects.filter(conversation__contact=contact, direction='in').order_by('-at').values_list('text', flat=True)[:30]) if contact else []
        if isinstance(o, Conversation):
            texts = list(o.messages.filter(direction='in').values_list('text', flat=True)[:50])
        texts += [a.outcome for a in Activity.objects.filter(contact=contact).exclude(outcome='').order_by('-id')[:20]] if contact else []
        if not texts:
            return {'message': 'No messages from the customer yet.'}
        label, score, p, n = sentiment_of(texts)
        ext = llm(request, 'Rate the customer sentiment (positive / neutral / negative) of these messages and say why in one sentence.', '\n'.join(texts)[:6000])
        return {'message': f'Sentiment: {label} ({score:+.2f}; {p} positive and {n} negative signals in {len(texts)} messages).' + (f'\n{ext}' if ext else '')}

    for k in ('lead',):
        r_ = regs[k]
        r_.actions += [Act('ai_score', 'AI: score', a_ai_score, lambda o, r: True),
                       Act('ai_qualify', 'AI: qualify (BANT)', a_ai_qualify, lambda o, r: o.status not in ('converted', 'disqualified')),
                       Act('ai_enrich', 'AI: enrich details', a_ai_enrich, lambda o, r: o.status not in ('converted', 'disqualified')),
                       Act('ai_industry', 'AI: suggest industry and questions', a_ai_industry, lambda o, r: o.status not in ('converted', 'disqualified'),
                           fields=(('text', 'More about the business (optional)', 'textarea', False),))]
        r_.info = tuple(r_.info) + ('ai_score', 'ai_reason')
        r_.cols = tuple(r_.cols) + ('ai_score',) if 'ai_score' not in r_.cols else r_.cols
    for k in ('opportunity',):
        r_ = regs[k]
        r_.actions += [Act('ai_win', 'AI: win chance', a_ai_win, lambda o, r: o.status == 'open'),
                       Act('ai_quote', 'AI: quotation assistant', a_ai_quote, lambda o, r: o.status == 'open' and can_work(o, r))]
        r_.info = tuple(r_.info) + ('ai_probability', 'ai_reason')
        r_.cols = tuple(r_.cols) + ('ai_probability',) if 'ai_probability' not in r_.cols else r_.cols
    for k in ('lead', 'opportunity', 'contact', 'quotation', 'complaint'):
        r_ = regs.get(k)
        if r_ is not None:
            r_.actions.append(Act('ai_email', 'AI: write an e-mail', a_ai_email, lambda o, r: True,
                                  fields=(('purpose', 'Purpose', PURP, True), ('note', 'Points to include', 'textarea', False))))
    for k in ('lead', 'opportunity', 'contact'):
        regs[k].actions.append(Act('ai_voice', 'AI: voice note to CRM', a_ai_voice, lambda o, r: True, fields=(('text', 'Voice note / call transcript', 'textarea', True),)))
    for k in ('opportunity', 'conversation', 'contact'):
        regs[k].actions.append(Act('ai_summary', 'AI: summarise', a_ai_summary, lambda o, r: True,
                                   fields=(() if k == 'conversation' else (('text', 'Notes (empty = the last activities)', 'textarea', False),))))
    for k in ('contact', 'conversation'):
        regs[k].actions.append(Act('ai_sentiment', 'AI: customer sentiment', a_ai_sentiment, lambda o, r: True))
    preg = regs.get('customer_profile')
    if preg is not None:
        preg.info = tuple(preg.info) + ('churn_probability', 'sentiment')
        preg.cols = tuple(preg.cols) + ('churn_probability',)

    base_daily, base_dash = app.daily, app.dashboard

    def daily():
        out = base_daily() if base_daily else {}
        out.update(daily_ai())
        return out

    def dashboard(request):
        out = base_dash(request) if base_dash else {'cards': [], 'bars': [], 'lists': []}
        try:
            mine = regs['ai_suggestion'].scope(request).filter(status='new')
            out['cards'] = list(out.get('cards') or []) + [{'label': 'AI suggestions to review', 'value': mine.count(), 'link': 'r/ai_suggestion?status=new'}]
            out['lists'] = list(out.get('lists') or []) + [{'title': 'AI suggestions', 'kind': 'ai_suggestion',
                                                           'rows': [{'id': s.pk, 'title': s.title, 'sub': s.get_kind_display(), 'badge': f'{s.confidence} %' if s.confidence else ''}
                                                                    for s in mine[:8]]}]
        except Exception:   # noqa: BLE001
            log.exception('AI dashboard')
        return out
    app.daily, app.dashboard = daily, dashboard
    app.settings_fields = tuple(app.settings_fields) + ('ai_enabled',)
    return {'reports': reports, 'daily_ai': daily_ai, 'score_leads': score_leads, 'forecast_opps': forecast_opps, 'parse_voice': parse_voice,
            'detect_industry': detect_industry, 'lost_analysis': lost_analysis, 'coaching': coaching, 'recommend': recommend, 'draft_email': draft_email,
            'summarise': summarise, 'sentiment_of': sentiment_of, 'NB': NB}
