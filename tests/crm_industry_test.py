"""v1.67.27 – CRM industry release 1, end-to-end checks in one company (through the kit API, as a user works).

    ZEO_TEST_SCHEMA=<company schema> python manage.py shell < tests/crm_industry_test.py

Activates CRM (if needed), installs the data centre, IT and logistics packs, then checks: industry choices, the fields on the
form metadata with their "when", list columns and filters, create / change / history / required fields, stage rules on Move and
Won, lead → opportunity → quotation → sales order carry-over, revision, Excel export, filter by a field, duplicate, mass change,
lead scoring on fields, web capture with fields, the quotation PDF, every industry report of the installed packs (report centre
spec and listing), the CRM home bars, and that a company with CRM alone (General) still works. Prints PASS / FAIL per check.
"""
import json
import os
import traceback
from io import BytesIO

from django.db import connection
from django_tenants.utils import schema_context
from rest_framework.test import APIRequestFactory, force_authenticate

SCHEMA = os.environ.get('ZEO_TEST_SCHEMA') or 'zttest'
RESULTS = []


def check(name, cond, detail=''):
    RESULTS.append((name, bool(cond), detail))
    print(('PASS ' if cond else 'FAIL ') + name + ('' if cond else f'  – {detail}'))


def run():
    from django.contrib.auth import get_user_model
    from zeo.appkit import views as V
    from ZeoCRM.app import app, INDUSTRY
    from ZeoCRM.models import (IndustryField, IndustryReport, Lead, LeadScoreRule, Opportunity, Pipeline, Quotation, SalesOrder, Stage)
    from ZeoMasters.models import Account

    U = get_user_model()
    user = U.objects.filter(is_superuser=True).order_by('id').first()
    rf = APIRequestFactory()

    def call(view, method='get', path='/', data=None, **kw):
        q = f'?schema={SCHEMA}'
        if method == 'get':
            req = rf.get(path + q + ('&' + '&'.join(f'{k}={v}' for k, v in (data or {}).items()) if data else ''))
        else:
            req = getattr(rf, method)(path + q, data or {}, format='json')
        force_authenticate(req, user=user)
        resp = view.as_view(app=app)(req, **kw)
        if hasattr(resp, 'render'):
            resp.render()
        return resp

    def body(resp):
        try:
            return json.loads(resp.content or b'{}')
        except Exception:
            return {}

    # ---------------------------------------------------------------- activation and packs
    s = app.settings()
    if not s.activated_at:
        r = call(V.ActivateView, 'post', data={'industry': 'general'})
        check('activate CRM (general)', r.status_code == 200, r.content[:300])
    for code in ('data_center', 'it', 'logistics'):
        r = call(V.PackView, 'post', data={'code': code})
        check(f'install pack {code}', r.status_code == 200, r.content[:300])
    check('fields installed', IndustryField.objects.filter(industry='data_center').count() >= 20, IndustryField.objects.filter(industry='data_center').count())
    check('reports installed', IndustryReport.objects.filter(industry='data_center').count() >= 8)
    dcs = Pipeline.objects.filter(code='DCS').first()
    check('data centre pipeline with industry', dcs is not None and dcs.industry == 'data_center', getattr(dcs, 'industry', None))
    check('general Sales pipeline has no industry', Pipeline.objects.filter(code='SALES', industry='').exists())
    again = call(V.PackView, 'post', data={'code': 'data_center'})
    check('installing again adds nothing', body(again).get('added') in ({}, None), body(again).get('added'))

    # ---------------------------------------------------------------- metadata
    regs = {m['kind']: m for m in body(call(V.RegistersView))}
    lm, om, qm = regs['lead'], regs['opportunity'], regs['quotation']
    ind = next((f for f in lm['form'] if f['name'] == 'industry'), None)
    check('industry is a pick list of the installed industries', ind and ind['type'] == 'choice' and {'data_center', 'it', 'logistics'} <= {c['value'] for c in ind['choices']},
          ind)
    xr = next((f for f in om['form'] if f['name'] == 'x_dc_racks'), None)
    check('rack quantity on the opportunity form', xr is not None and xr['dyn'] and xr['type'] == 'decimal', xr)
    check('… with "when" on industry and pipeline', xr and {c['field'] for c in xr['when']} == {'industry', 'pipeline'} and dcs.pk in xr['when'][1]['in'], xr and xr['when'])
    check('rack quantity is a list column', any(c['name'] == 'x_dc_racks' for c in om['cols']))
    check('redundancy is a list filter', any(c['name'] == 'x_dc_redundancy' for c in om['filters']))
    yn = next((f for f in om['form'] if f['name'] == 'x_dc_ab_feed'), None)
    check('yes / no field is three-state', yn and yn['type'] == 'bool3', yn)
    check('industry fields not on quotation unless marked', not any(f['name'] == 'x_dc_business' for f in qm['form']) and any(f['name'] == 'x_dc_racks' for f in qm['form']))
    check('industry field register in Setup', 'industry_field' in regs and 'industry_report' in regs)

    # ---------------------------------------------------------------- lead with industry fields
    r = call(V.RegisterListView, 'post', '/', {'name': 'Rania Test', 'company_name': 'Cloudy Hosting LLC', 'email': 'rania@cloudy.test', 'phone': '0501234567',
                                                'industry': 'data_center', 'x_dc_racks': '12', 'x_dc_power_kw': '48.5', 'x_dc_redundancy': '2N',
                                                'x_dc_ab_feed': True, 'x_dc_facility': 'DXB-1', 'x_dc_duration': '36', 'x_it_users': '99'}, kind='lead')
    lead = body(r)
    check('lead created with fields', r.status_code == 201 and lead.get('x_dc_racks') == 12 and lead.get('x_dc_power_kw') == 48.5, r.content[:400])
    L = Lead.objects.get(pk=lead['id'])
    check('values in extra', L.extra.get('dc_redundancy') == '2N' and L.extra.get('dc_ab_feed') is True, L.extra)
    check('a field of another industry is not kept', 'it_users' not in L.extra, L.extra)
    r = call(V.RegisterListView, 'post', '/', {'name': 'Bad', 'industry': 'data_center', 'x_dc_redundancy': '3N'}, kind='lead')
    check('pick-list value checked', r.status_code == 400 and 'valid' in r.content.decode(), r.content[:200])
    r = call(V.RegisterListView, 'post', '/', {'name': 'Bad', 'industry': 'data_center', 'x_dc_racks': 'many'}, kind='lead')
    check('number checked', r.status_code == 400, r.content[:200])
    r = call(V.RegisterListView, 'post', '/', {'name': 'Ship it', 'industry': 'logistics'}, kind='lead')
    check('always-required field of the industry (origin)', r.status_code == 400 and 'Origin' in r.content.decode(), r.content[:200])
    r = call(V.RegisterListView, 'post', '/', {'name': 'Ship it', 'industry': 'logistics', 'x_lg_origin': 'Jebel Ali', 'x_lg_destination': 'Riyadh',
                                               'x_lg_mode': 'Road'}, kind='lead')
    check('… saved when given', r.status_code == 201, r.content[:200])
    r = call(V.RegisterListView, 'post', '/', {'name': 'Other industry', 'industry': 'it'}, kind='lead')
    check('logistics required fields do not apply to IT', r.status_code == 201, r.content[:200])
    r = call(V.RegisterListView, 'post', '/', {'name': 'Unknown', 'industry': 'space_mining'}, kind='lead')
    check('unknown industry refused', r.status_code == 400, r.content[:200])

    r = call(V.RegisterDetailView, 'patch', '/', {'x_dc_racks': '14'}, kind='lead', pk=L.pk)
    d = body(r)
    check('change one field keeps the others', r.status_code == 200 and d.get('x_dc_racks') == 14 and d.get('x_dc_redundancy') == '2N', r.content[:300])
    check('change in the history with old → new', any('Rack quantity (12 → 14)' in (h.get('text') or '') for h in d.get('history', [])), d.get('history', [])[:3])
    r = call(V.RegisterDetailView, 'patch', '/', {'x_dc_facility': ''}, kind='lead', pk=L.pk)
    check('emptying a field removes it', 'dc_facility' not in Lead.objects.get(pk=L.pk).extra)
    r = call(V.RegisterListView, 'get', '/', {'x_dc_redundancy': '2N'}, kind='lead')
    check('list filter by an industry field', body(r).get('count') == 1, body(r).get('count'))
    r = call(V.RegisterListView, 'get', '/', {}, kind='lead')
    row = next((x for x in body(r).get('results', []) if x['id'] == L.pk), {})
    check('list shows the list columns', 'x_dc_facility' in row or 'x_dc_racks' in row, list(row)[:30])
    check('list shows the industry label', row.get('industry_label') == 'Data centres and critical infrastructure', row.get('industry_label'))

    # scoring on fields
    sc = Lead.objects.get(pk=L.pk).score
    LeadScoreRule.objects.create(name='Big rack test', when='field', value='dc_racks>=10', points=33)
    LeadScoreRule.objects.create(name='DC test', when='industry', value='data_center', points=7)
    from ZeoCRM.app import SALES
    check('score rules on industry fields', SALES['score_of'](Lead.objects.get(pk=L.pk)) >= sc + 40, (sc, SALES['score_of'](Lead.objects.get(pk=L.pk))))

    # ---------------------------------------------------------------- convert → opportunity in the data centre pipeline
    r = call(V.RegisterActionView, 'post', '/', {}, kind='lead', pk=L.pk, action='convert')
    check('convert', r.status_code == 200, r.content[:300])
    O = Opportunity.objects.filter(lead_id=L.pk).first()
    check('opportunity in the industry pipeline', O and O.pipeline.code == 'DCS' and O.industry == 'data_center', O and (O.pipeline.code, O.industry))
    check('values carried to the opportunity', O and O.extra.get('dc_racks') == 14 and O.extra.get('dc_redundancy') == '2N', O and O.extra)
    check('values the opportunity does not have are not carried', O and 'it_users' not in O.extra, O and O.extra)

    # stage rules: SLA requirement needed from "SLA and commercial quotation"
    q_stage = Stage.objects.get(code='DCS_QUOTE')
    r = call(V.RegisterActionView, 'post', '/', {'stage': q_stage.pk}, kind='opportunity', pk=O.pk, action='move')
    check('Move stage refused while a stage field is empty', r.status_code == 400 and 'SLA requirement' in r.content.decode(), r.content[:300])
    r = call(V.RegisterDetailView, 'patch', '/', {'x_dc_sla': '99.99% uptime, 4h hardware replacement'}, kind='opportunity', pk=O.pk)
    check('fill it', r.status_code == 200, r.content[:300])
    r = call(V.RegisterActionView, 'post', '/', {'stage': q_stage.pk}, kind='opportunity', pk=O.pk, action='move')
    check('… then Move stage works', r.status_code == 200 and Opportunity.objects.get(pk=O.pk).stage_id == q_stage.pk, r.content[:300])
    r = call(V.RegisterDetailView, 'patch', '/', {'x_dc_sla': ''}, kind='opportunity', pk=O.pk)
    check('cannot empty a field the stage needs', r.status_code == 400, r.content[:300])

    # an opportunity made by hand in the general pipeline gets the fields from the industry chosen
    sales = Pipeline.objects.get(code='SALES')
    from ZeoCRM.models import Contact
    c = Contact.objects.first()
    r = call(V.RegisterListView, 'post', '/', {'title': 'Hand made', 'contact': c.pk, 'pipeline': sales.pk, 'industry': 'it', 'x_it_users': '25'}, kind='opportunity')
    check('opportunity in the general pipeline keeps the chosen industry', r.status_code == 201 and body(r).get('industry') == 'it' and body(r).get('x_it_users') == 25,
          r.content[:300])
    r = call(V.RegisterListView, 'post', '/', {'title': 'Pipeline decides', 'contact': c.pk, 'pipeline': dcs.pk, 'industry': 'it'}, kind='opportunity')
    check('an industry pipeline decides the industry', r.status_code == 201 and body(r).get('industry') == 'data_center', r.content[:300])

    # ---------------------------------------------------------------- quotation, PDF, revision, order
    r = call(V.RegisterActionView, 'post', '/', {}, kind='opportunity', pk=O.pk, action='quote')
    check('new quotation from the opportunity', r.status_code == 200, r.content[:300])
    Q = Quotation.objects.filter(opportunity=O).order_by('-id').first()
    check('quotation has the industry and the quotation fields', Q and Q.industry == 'data_center' and Q.extra.get('dc_racks') == 14 and 'dc_business' not in Q.extra,
          Q and Q.extra)
    from ZeoCRM.pdf import build
    pdf = build(Q, 'Quotation', app)
    check('quotation PDF builds', pdf[:4] == b'%PDF' and len(pdf) > 1500)
    rows = INDUSTRY['pdf_rows'](Q)
    check('PDF prints the marked fields', any(k.startswith('Rack quantity') and v == '14' for k, v in rows), rows)
    from ZeoMasters.models import Product
    p = Product.objects.filter(is_active=True).first()
    if p is not None:
        call(V.RegisterDetailView, 'patch', '/', {'items': [{'product': p.pk, 'description': 'Rack', 'qty': 14, 'unit_price': 3500}]}, kind='quotation', pk=Q.pk)
    r = call(V.RegisterActionView, 'post', '/', {}, kind='quotation', pk=Q.pk, action='revise')
    Q2 = Quotation.objects.filter(previous=Q).first()
    check('revision keeps industry and values', Q2 and Q2.industry == 'data_center' and Q2.extra == Q.extra, Q2 and Q2.extra)
    r = call(V.RegisterActionView, 'post', '/', {}, kind='quotation', pk=Q2.pk, action='accept')
    so = SalesOrder.objects.filter(quotation=Q2).first()
    check('sales order gets industry and its fields', so and so.industry == 'data_center' and so.extra.get('dc_duration') == 36, so and so.extra)

    # won needs every stage field
    r = call(V.RegisterDetailView, 'patch', '/', {'x_dc_power_kw': ''}, kind='opportunity', pk=O.pk)
    check('power needed (from Power and cooling stage) cannot be emptied', r.status_code == 400, r.content[:200])

    # ---------------------------------------------------------------- export, duplicate, mass change
    r = call(V.RegisterExportView, 'get', '/', {}, kind='opportunity')
    from openpyxl import load_workbook
    wb = load_workbook(BytesIO(r.content))
    head = [c.value for c in wb.active[1]]
    check('Excel export has the industry fields', 'Rack quantity' in head and 'Power requirement (kW)' in head, head[-10:])
    r = call(V.RegisterDuplicateView, 'post', '/', {}, kind='lead', pk=L.pk)
    check('duplicate copies the industry fields', r.status_code == 201 and body(r).get('x_dc_racks') == 14, r.content[:300])
    r = call(V.RegisterBulkView, 'post', '/', {'ids': [L.pk, body(r).get('id')], 'action': 'set', 'field': 'x_dc_cooling', 'value': 'In-row'}, kind='lead')
    check('mass change of an industry field', len(body(r).get('done', [])) >= 1, r.content[:300])

    # ---------------------------------------------------------------- web capture with industry fields
    s = app.settings()
    out = SALES['lead_capture'](name='Web Visitor', email='web@visitor.test', industry='data_center', fields={'dc_racks': '4', 'dc_redundancy': 'N+1', 'dc_bad': 'x'},
                                x_dc_facility='AUH-2')
    W = Lead.objects.get(pk=out['id'])
    check('web capture with industry and fields', W.industry == 'data_center' and W.extra.get('dc_racks') == 4 and W.extra.get('dc_facility') == 'AUH-2'
          and 'dc_bad' not in W.extra, W.extra)

    # ---------------------------------------------------------------- reports
    class _Req:
        pass

    from ZeoCRM.industry import report_rows
    req = rf.get('/')
    force_authenticate(req, user=user)
    from rest_framework.request import Request
    from rest_framework.views import APIView
    dreq = APIView().initialize_request(req)
    dreq._force_auth_user = user
    dreq.user = user
    n_ok = n = 0
    bad = []
    for rep in IndustryReport.objects.filter(industry__in=('data_center', 'it', 'logistics')):
        n += 1
        try:
            cols, rows_ = report_rows(rep, dreq)
            assert cols and isinstance(rows_, list)
            n_ok += 1
        except Exception as e:   # noqa: BLE001
            bad.append((rep.code, repr(e)))
    check(f'every industry report runs ({n_ok}/{n})', n_ok == n, bad[:3])
    rep = IndustryReport.objects.get(code='dc_rack_demand')
    cols, rows_ = report_rows(rep, dreq)
    check('rack demand per facility adds up racks', any(r_['group'] in ('DXB-1', '(not given)') for r_ in rows_) and any(c_[0] == 'measure' for c_ in cols), rows_)
    spec = INDUSTRY['report_spec']('crmx-dc_rack_demand')
    check('report centre spec', spec is not None and spec[0] == 'Rack capacity demand' and spec[1].startswith('CRM – '), spec and spec[:2])
    from AccessControl.access import ctx
    lst = INDUSTRY['report_listing'](dreq, ctx(dreq))
    check('report centre listing', any(x['key'] == 'crmx-dc_colo_pipeline' for x in lst), len(lst))
    from DashboardManagement.reports import REPORTS
    check('REPORTS resolves crmx- keys', REPORTS.get('crmx-dc_colo_pipeline') is not None)
    dash = app.dashboard(dreq)
    check('CRM home shows the marked industry reports', any(b['title'].startswith('Colocation sales pipeline') or b['title'].startswith('Rack capacity demand')
                                                            for b in dash.get('bars', [])), [b['title'] for b in dash.get('bars', [])])

    # setup checks
    r = call(V.RegisterListView, 'post', '/', {'label': 'Bad code', 'code': 'Bad Code', 'field_type': 'text', 'on_lead': True}, kind='industry_field')
    check('field code checked', r.status_code == 400, r.content[:200])
    r = call(V.RegisterListView, 'post', '/', {'label': 'Fuel type', 'code': 'zz_fuel', 'field_type': 'choice', 'choices': 'Diesel\nGas', 'industry': 'data_center',
                                               'on_lead': True, 'on_opportunity': True, 'in_list': True}, kind='industry_field')
    check('company adds its own field', r.status_code == 201, r.content[:300])
    regs2 = {m['kind']: m for m in body(call(V.RegistersView))}
    check('… and it is on the lead form at once', any(f['name'] == 'x_zz_fuel' for f in regs2['lead']['form']))
    r = call(V.RegisterListView, 'post', '/', {'name': 'Bad group', 'code': 'zz_bad', 'source': 'lead', 'group_by': 'stage'}, kind='industry_report')
    check('report group checked', r.status_code == 400, r.content[:200])
    r = call(V.RegisterListView, 'post', '/', {'name': 'Fuel mix', 'code': 'zz_fuel_mix', 'source': 'opportunity', 'group_by': 'field:zz_fuel', 'industry': 'data_center'},
             kind='industry_report')
    check('company adds its own report', r.status_code == 201, r.content[:300])
    r = call(V.RegisterDetailView, 'patch', '/', {'code': 'dc_racks_new'}, kind='industry_field', pk=IndustryField.objects.get(code='dc_racks').pk)
    check('a used field code cannot be renamed', r.status_code == 400, r.content[:200])


with schema_context(SCHEMA):
    try:
        run()
    except Exception:
        traceback.print_exc()
        RESULTS.append(('crashed', False, ''))
fails = [r for r in RESULTS if not r[1]]
print(f'\n{len(RESULTS) - len(fails)} / {len(RESULTS)} checks passed')
