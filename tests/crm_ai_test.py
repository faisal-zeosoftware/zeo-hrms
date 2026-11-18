"""v1.67.30 – CRM release 4 (AI), end-to-end checks.

    ZEO_TEST_SCHEMA=<company schema> python manage.py shell < tests/crm_ai_test.py

Builds a small history (closed leads and deals, accepted quotations, lost reasons, a campaign), then checks the 16 AI features
with the built-in engine, the review step (Accept / Dismiss), the daily AI job, the 5 AI reports, the CRM home, the external
engine through AISettings (with a stand-in provider) and the CRM setup switch.
"""
import json
import os
import random
import traceback
from datetime import timedelta

from django_tenants.utils import schema_context
from rest_framework.test import APIRequestFactory, force_authenticate

SCHEMA = os.environ.get('ZEO_TEST_SCHEMA') or 'zttest'
RESULTS = []


def check(name, cond, detail=''):
    RESULTS.append((name, bool(cond), detail))
    print(('PASS ' if cond else 'FAIL ') + name + ('' if cond else f'  – {str(detail)[:400]}'))


def run():
    from django.contrib.auth import get_user_model
    from django.utils import timezone
    from zeo.appkit import views as V
    from zeo.appkit.engine import today
    from ZeoCRM.app import app, AI
    from ZeoCRM import ai as A
    from ZeoCRM.models import (AISuggestion, Activity, Campaign, Contact, Conversation, ConversationMessage, CustomerProfile, Lead, LostReason, Opportunity, Pipeline,
                               Quotation, Source, Stage)
    from ZeoMasters.models import Account, Product
    from EmpManagement.models import emp_master

    random.seed(7)
    U = get_user_model()
    user = U.objects.filter(is_superuser=True).order_by('id').first()
    rf = APIRequestFactory()
    emp = emp_master.objects.first()

    def call(view, method='get', data=None, **kw):
        q = f'/?schema={SCHEMA}'
        req = rf.get(q + ('&' + '&'.join(f'{k}={v}' for k, v in (data or {}).items()) if data else '')) if method == 'get' else getattr(rf, method)(q, data or {}, format='json')
        force_authenticate(req, user=user)
        resp = view.as_view(app=app)(req, **kw)
        if hasattr(resp, 'render'):
            resp.render()
        return resp

    def body(r):
        try:
            return json.loads(r.content or b'{}')
        except Exception:
            return {}

    def act(kind, pk, action, data=None):
        r = call(V.RegisterActionView, 'post', data or {}, kind=kind, pk=pk, action=action)
        return r, (body(r).get('result') or {}).get('message', '')

    regs = {m['kind']: m for m in body(call(V.RegistersView))}
    check('register ai_suggestion', 'ai_suggestion' in regs)
    check('AI score on the lead list', any(c['name'] == 'ai_score' for c in regs['lead']['cols']))
    s = app.settings()
    s.ai_enabled = True
    s.save()
    from AISettings import service as S
    check('built-in engine by default (nothing leaves the server)', S.feature_mode('crm')[0] == 'builtin' and A.llm(None, 'x', 'y') is None)

    # ---------------------------------------------------------------- history
    ref, _ = Source.objects.get_or_create(code='REFERRAL', defaults={'name': 'Referral'})
    soc, _ = Source.objects.get_or_create(code='SOCIAL', defaults={'name': 'Social media'})
    notfit = LostReason.objects.filter(code='NOTFIT').first() or LostReason.objects.first()
    price = LostReason.objects.filter(code='PRICE').first() or LostReason.objects.first()
    for i in range(40):
        good = i % 2 == 0
        Lead.objects.create(number=f'LDH{i}', name=f'Hist {i}', source=ref if good else soc, job_title='Director' if good else '', company_name='Co' if good else '',
                            email=f'h{i}@x.test', status='converted' if (good and i % 10 != 0) or (not good and i % 9 == 1) else 'disqualified', channel='manual')
    sales = Pipeline.objects.get(code='SALES')
    won_st = sales.stages.get(is_won=True)
    lost_st = sales.stages.get(is_lost=True)
    acc = Account.objects.create(number='ACC-A1', name='Gulf Ships LLC', kind='customer')
    c = Contact.objects.create(number='CON-A1', name='Ali Gulf', email='ali@gulfships.test', account=acc, job_title='Director', consent=True)
    p1 = Product.objects.create(number='PAI1', code='AIP1', name='Rack hosting', sale_price=1000)
    p2 = Product.objects.create(number='PAI2', code='AIP2', name='Remote hands', sale_price=200)
    p3 = Product.objects.create(number='PAI3', code='AIP3', name='Cross connect', sale_price=300)
    for i in range(30):
        won = i % 3 != 0
        o = Opportunity.objects.create(number=f'OPH{i}', title=f'Deal {i}', contact=c, account=acc if i < 3 else None, pipeline=sales, stage=won_st if won else lost_st,
                                       status='won' if won else 'lost', owner=emp, value=10000 + 1000 * i, source=ref if won else soc,
                                       closed_at=timezone.now() - timedelta(days=i), lost_reason=None if won else (price if i % 2 else notfit),
                                       lost_note='' if won else 'Too expensive compared to competitor')
        Opportunity.objects.filter(pk=o.pk).update(created_at=timezone.now() - timedelta(days=30 + i))
        if won:
            q = Quotation.objects.create(number=f'QTH{i}', opportunity=o, account=acc, contact=c, date=today(), status='accepted', currency='AED',
                                         items=[{'product': p1.pk, 'qty': 1, 'unit_price': 1000}, {'product': p2.pk, 'qty': 1, 'unit_price': 200}])
            app.regs['quotation'].saver(q, None, True, None) if False else None
            from ZeoCRM.models import QuotationLine
            QuotationLine.objects.create(quotation=q, order=1, product=p1, qty=1, unit_price=1000, net=1000, total=1000)
            QuotationLine.objects.create(quotation=q, order=2, product=p2, qty=1, unit_price=200, net=200, total=200)
            if i % 4 == 0:
                QuotationLine.objects.create(quotation=q, order=3, product=p3, qty=1, unit_price=300, net=300, total=300)

    # ---------------------------------------------------------------- 1 lead scoring
    good = Lead.objects.create(number='LD-AI1', name='Good Lead', source=ref, job_title='Director', company_name='Big Co', email='good@big.test', status='new', channel='manual')
    bad = Lead.objects.create(number='LD-AI2', name='Weak Lead', source=soc, email='weak@x.test', status='new', channel='manual')
    n = A.score_leads()
    good.refresh_from_db()
    bad.refresh_from_db()
    check('AI lead scores from the history', n >= 2 and good.ai_score is not None and good.ai_score > bad.ai_score + 20, (good.ai_score, bad.ai_score))
    check('… with the reasons', 'source' in good.ai_reason, good.ai_reason)
    r, msg = act('lead', good.pk, 'ai_score')
    check('AI: score action', r.status_code == 200 and 'AI score' in msg, msg)

    # ---------------------------------------------------------------- 2 enrichment
    en = Lead.objects.create(number='LD-AI3', name='Enrich Me', email='sam@falcon-hosting.ae', phone='+971501230000', status='new', channel='manual')
    r, msg = act('lead', en.pk, 'ai_enrich')
    sg = AISuggestion.objects.filter(kind='enrich', lead=en).first()
    check('AI enrichment from the e-mail domain and phone', sg and sg.data['lead_updates'].get('company_name') == 'Falcon Hosting' and sg.data['lead_updates'].get('country') == 'United Arab Emirates',
          (msg, sg and sg.data))
    r, msg = act('ai_suggestion', sg.pk, 'accept')
    en.refresh_from_db()
    check('accepting fills the lead', en.company_name == 'Falcon Hosting' and en.country == 'United Arab Emirates', (en.company_name, en.country))
    gm = Lead.objects.create(number='LD-AI4', name='Gmail', email='x@gmail.com', status='new', channel='manual', country='India')
    r, msg = act('lead', gm.pk, 'ai_enrich')
    check('free e-mail domains are not guessed', 'Nothing to add' in msg, msg)

    # ---------------------------------------------------------------- 3 qualification
    q_l = Lead.objects.create(number='LD-AI5', name='Qualify Me', job_title='Managing Director', requirements='We need 10 racks in Dubai, budget AED 400,000, start next month.',
                              status='new', channel='manual', value=400000)
    r, msg = act('lead', q_l.pk, 'ai_qualify')
    sg = AISuggestion.objects.filter(kind='qualify', lead=q_l).first()
    check('BANT qualification → qualified', sg and sg.data.get('lead_status') == 'qualified' and 'Budget ✓' in sg.text, (msg, sg and sg.text))
    act('ai_suggestion', sg.pk, 'accept')
    check('accepted: lead qualified', Lead.objects.get(pk=q_l.pk).status == 'qualified')
    nq = Lead.objects.create(number='LD-AI6', name='Not Now', requirements='just looking, not interested now', status='new', channel='manual')
    act('lead', nq.pk, 'ai_qualify')
    sg = AISuggestion.objects.filter(kind='qualify', lead=nq).first()
    check('negative signals → disqualify / nurture suggested', sg and sg.data.get('lead_status') in ('disqualified', 'nurture'), sg and sg.data)
    r, _ = act('ai_suggestion', sg.pk, 'dismiss', {'note': 'keep'})
    check('dismiss keeps the lead', Lead.objects.get(pk=nq.pk).status == 'new' and AISuggestion.objects.get(pk=sg.pk).status == 'dismissed')

    # ---------------------------------------------------------------- 16 industry assistant
    il = Lead.objects.create(number='LD-AI7', name='DC Lead', requirements='Colocation of 10 racks with 2N power, cooling and cross-connects in our data centre', status='new', channel='manual')
    r, msg = act('lead', il.pk, 'ai_industry')
    sg = AISuggestion.objects.filter(kind='industry', lead=il).first()
    check('industry assistant finds data centres and the questions', sg and sg.data.get('industry') == 'data_center' and 'Rack quantity' in sg.text, (msg, sg and sg.text[:200]))
    act('ai_suggestion', sg.pk, 'accept')
    check('accepted: industry set', Lead.objects.get(pk=il.pk).industry == 'data_center')

    # ---------------------------------------------------------------- 15 voice to CRM
    dcs = Pipeline.objects.get(code='DCS')
    vo = Opportunity.objects.create(number='OP-AI1', title='Voice deal', contact=c, account=acc, pipeline=dcs, stage=dcs.stages.order_by('order').first(), owner=emp,
                                    industry='data_center', status='open')
    r, msg = act('opportunity', vo.pk, 'ai_voice', {'text': 'Spoke with Ali from Gulf Ships LLC. They need 12 racks and power of 40 kW. Please call back next week with the price.'})
    sg = AISuggestion.objects.filter(kind='voice', opportunity=vo).first()
    check('voice note → fields and follow-up', sg and sg.data.get('fields', {}).get('dc_racks') == 12 and sg.data['fields'].get('dc_power_kw') == 40 and 'activity' in sg.data,
          (msg, sg and sg.data))
    check('… saved as a note activity', Activity.objects.filter(opportunity=vo, kind='note', subject='Voice note').exists())
    act('ai_suggestion', sg.pk, 'accept')
    vo.refresh_from_db()
    check('accepted: fields filled, follow-up made', vo.extra.get('dc_racks') == 12 and Activity.objects.filter(opportunity=vo, status='open', kind='call').exists(), vo.extra)
    p = A.parse_voice('Call from Nadia, nadia@new.test, 050 123 4567, wants a quotation for Rack hosting')
    check('voice parser: e-mail, phone, product, intent', p['email'] == 'nadia@new.test' and p['phone'].startswith('050') and p['products'] and p['intent'] == 'enquiry', p)

    # ---------------------------------------------------------------- 7 forecasting
    o_new = Opportunity.objects.create(number='OP-AI2', title='Forecast me', contact=c, pipeline=sales, stage=sales.stages.order_by('order').first(), owner=emp, source=ref,
                                       value=15000, status='open')
    r, msg = act('opportunity', o_new.pk, 'ai_win')
    o_new.refresh_from_db()
    check('AI win chance from the history', o_new.ai_probability is not None and 'AI win chance' in msg, msg)
    check('forecast job', A.forecast_opps() >= 2)

    # ---------------------------------------------------------------- 4 next actions
    Opportunity.objects.filter(pk=o_new.pk).update(created_at=timezone.now() - timedelta(days=12), expected_close=today() - timedelta(days=2))
    n = A.next_actions()
    sgs = AISuggestion.objects.filter(kind='next_action', opportunity=o_new)
    check('next actions: no contact, close date passed', sgs.count() >= 2, [x.title for x in sgs])
    A.next_actions()
    check('… not suggested twice', AISuggestion.objects.filter(kind='next_action', opportunity=o_new).count() == sgs.count())
    act('ai_suggestion', sgs.first().pk, 'accept')
    check('accepted: activity planned', Activity.objects.filter(opportunity=o_new, status='open').exists())

    # ---------------------------------------------------------------- 10 quotation assistant, 11 upselling
    dq = Quotation.objects.create(number='QT-AI1', opportunity=o_new, account=acc, contact=c, date=today(), status='draft', currency='AED',
                                  items=[{'product': p1.pk, 'qty': 1, 'unit_price': 1000}])
    from ZeoCRM.models import QuotationLine
    QuotationLine.objects.create(quotation=dq, order=1, product=p1, qty=1, unit_price=1000, net=1000, total=1000)
    r, msg = act('opportunity', o_new.pk, 'ai_quote')
    sg = AISuggestion.objects.filter(kind='quote', opportunity=o_new).first()
    check('quotation assistant suggests what is sold together', sg and p2.pk in sg.data.get('products', []), (msg, sg and sg.data))
    act('ai_suggestion', sg.pk, 'accept')
    check('accepted: line added to the draft quotation', Quotation.objects.get(pk=dq.pk).lines.filter(product=p2).exists())
    recs = A.recommend({p1.pk}, '', None)
    check('co-occurrence ranking', recs and recs[0][0].pk == p2.pk, recs)

    # ---------------------------------------------------------------- 12 churn, 11 upsell (daily), 8 sentiment
    # v1.67.31: a won deal makes the profile at once – update it (it was created here)
    prof, _ = CustomerProfile.objects.get_or_create(account=acc)
    CustomerProfile.objects.filter(pk=prof.pk).update(won_count=5, last_won_at=today() - timedelta(days=400), relationship_score=10, open_complaints=2)
    prof.refresh_from_db()
    A.churn_scores()
    prof.refresh_from_db()
    check('churn probability', prof.churn_probability is not None and prof.churn_probability >= 60, prof.churn_probability)
    check('churn suggestion for the account manager', AISuggestion.objects.filter(kind='churn', account=acc).exists())
    conv = Conversation.objects.create(number='CNV-AI1', channel='whatsapp', address='+971500000001', contact=c, name='Ali')
    for t in ('The delivery is late again, very disappointed', 'This is a problem, we may cancel', 'Bad service this month'):
        ConversationMessage.objects.create(conversation=conv, direction='in', text=t)
    r, msg = act('conversation', conv.pk, 'ai_sentiment')
    check('sentiment: negative', 'Sentiment: negative' in msg, msg)
    check('Arabic words count', A.sentiment_of(['شكرا ممتاز'])[0] == 'positive')
    r, msg = act('conversation', conv.pk, 'ai_summary')
    check('conversation summary (built-in)', r.status_code == 200 and msg.startswith('Summary:'), msg)
    out = AI['daily_ai']()
    check('daily AI job', 'ai_lead_scores' in out and 'ai_churn' in out, out)
    prof.refresh_from_db()
    check('… sentiment on the customer profile', prof.sentiment == 'negative', prof.sentiment)

    # ---------------------------------------------------------------- 6 summariser
    txt, eng = A.summarise('Met Ali today. They agreed to the 2N design. We will send the revised quotation by Thursday. Ali needs approval from the CFO next week.')
    check('meeting summary: action items and dates', 'Action items' in txt and 'next week' in txt, txt)

    # ---------------------------------------------------------------- 5 e-mail generator
    r, msg = act('opportunity', o_new.pk, 'ai_email', {'purpose': 'quotation'})
    sg = AISuggestion.objects.filter(kind='email', opportunity=o_new).first()
    check('e-mail draft (built-in)', sg and 'Quotation' in sg.title and 'Dear Ali' in sg.text, (msg[:200], sg and sg.text[:200]))
    r, msg = act('ai_suggestion', sg.pk, 'accept')
    check('accepted: e-mail sent to the contact', 'e-mail sent' in msg and AISuggestion.objects.get(pk=sg.pk).status == 'accepted', msg)

    # ---------------------------------------------------------------- 9 lost deals, 13 campaigns, 14 coaching
    text, eng, rows = A.lost_analysis(365)
    check('lost deal analysis', rows and 'Suggestion:' in text and 'competitor' in text.lower(), text)
    camp = Campaign.objects.create(code='AICAMP', name='Expensive ads', kind='ads', budget=50000, spent=50000, is_active=True, owner=emp)
    for i in range(12):
        Lead.objects.create(number=f'LDC{i}', name=f'Camp {i}', campaign=camp, status='new', channel='facebook')
    A.campaign_tips()
    check('campaign optimisation tip', AISuggestion.objects.filter(kind='campaign', campaign=camp).exists())
    rows, team = A.coaching()
    check('sales coaching per agent', rows and 'tips' in rows[0], rows[:1])

    # ---------------------------------------------------------------- reports and home
    from DashboardManagement.reports import ReportView
    bad_ = []
    for k in ('crm-ai-lost', 'crm-ai-coaching', 'crm-ai-forecast', 'crm-ai-lead-scores', 'crm-ai-suggestions'):
        req = rf.get(f'/?schema={SCHEMA}&period=year')
        force_authenticate(req, user=user)
        resp = ReportView.as_view()(req, key=k)
        resp.render()
        if resp.status_code != 200:
            bad_.append((k, resp.status_code, resp.content[:200]))
    check('AI reports run', not bad_, bad_)
    req = rf.get(f'/?schema={SCHEMA}')
    force_authenticate(req, user=user)
    from rest_framework.views import APIView
    dreq = APIView().initialize_request(req)
    dreq.user = user
    check('CRM home: AI suggestions to review', any(x['label'] == 'AI suggestions to review' for x in app.dashboard(dreq)['cards']))

    # ---------------------------------------------------------------- external engine (stand-in provider)
    S.feature_mode = lambda feature, row=None: ('external', '')
    S.chat = lambda request, feature, system, messages, tools=None, max_tokens=2000: {'content': [{'type': 'text', 'text': 'Subject: Your colocation proposal\n\nDear Ali,\nplease find …'}]}
    S.settings_row = lambda: type('R', (), {'provider': 'anthropic'})()
    subj, bodyt, eng = A.draft_email(o_new, 'quotation')
    check('external engine writes the e-mail when allowed', subj == 'Your colocation proposal' and eng.startswith('external'), (subj, eng))

    # ---------------------------------------------------------------- switch
    s.ai_enabled = False
    s.save()
    r, msg = act('lead', good.pk, 'ai_score')
    check('CRM setup switch off → AI actions refused', r.status_code == 400 and 'switched off' in r.content.decode(), r.content[:200])
    s.ai_enabled = True
    s.save()


with schema_context(SCHEMA):
    try:
        run()
    except Exception:
        traceback.print_exc()
        RESULTS.append(('crashed', False, ''))
fails = [r for r in RESULTS if not r[1]]
print(f'\n{len(RESULTS) - len(fails)} / {len(RESULTS)} checks passed')
