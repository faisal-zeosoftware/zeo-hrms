"""v1.67.28 – CRM release 2 (general CRM), end-to-end checks through the kit API.

    ZEO_TEST_SCHEMA=<company schema> python manage.py shell < tests/crm_general_test.py

Needs CRM activated (tests/crm_industry_test.py activates it and installs the data centre pack). Checks enquiries (evaluation,
clarification, approval, quotation with priced lines, industry fields), customer profiles (block / release, 360° figures, churn),
onboarding started by a won deal, contracts (approval, reminders, renewal opportunity, renewal when it is won), subscription
quotations → contracts, complaints (SLA, escalation, resolution, rating), commissions (calculation, approval, payment), documents
(approval, versions, expiry), quotation templates and comparison, follow-up escalation, the new roles, the new reports and the CRM home.
"""
import json
import os
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
    from ZeoCRM.app import app, GENERAL
    from ZeoCRM.models import (CRMSettings, Activity, Commission, CommissionPlan, Complaint, Contact, Contract, CRMDocument, CustomerProfile, Enquiry, Onboarding,
                               Opportunity, Pipeline, Quotation, QuotationTemplate, SalesOrder, Stage)
    from ZeoMasters.models import Account, Product, UOM

    U = get_user_model()
    user = U.objects.filter(is_superuser=True).order_by('id').first()
    rf = APIRequestFactory()

    def call(view, method='get', data=None, **kw):
        q = f'/?schema={SCHEMA}'
        if method == 'get':
            req = rf.get(q + ('&' + '&'.join(f'{k}={v}' for k, v in (data or {}).items()) if data else ''))
        else:
            req = getattr(rf, method)(q, data or {}, format='json')
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

    def post(kind, data):
        return call(V.RegisterListView, 'post', data, kind=kind)

    def act(kind, pk, action, data=None):
        return call(V.RegisterActionView, 'post', data or {}, kind=kind, pk=pk, action=action)

    def patch(kind, pk, data):
        return call(V.RegisterDetailView, 'patch', data, kind=kind, pk=pk)

    from EmpManagement.models import emp_master
    emp = emp_master.objects.first()
    if emp is None:
        from OrganisationManager.models import brnch_mstr
        emp = emp_master.objects.create(emp_code='T001', emp_first_name='Tara', emp_last_name='Tech', emp_branch_id=brnch_mstr.objects.first(), emp_joined_date=today())
    regs = {m['kind']: m for m in body(call(V.RegistersView))}
    for k in ('enquiry', 'onboarding', 'onboarding_template', 'contract', 'complaint', 'commission', 'commission_plan', 'document', 'customer_profile', 'customer_site',
              'quotation_template'):
        check(f'register {k}', k in regs)
    check('new roles exist', {'crm_director', 'crm_presales', 'crm_marketing', 'crm_account', 'crm_service', 'crm_leadgen'} <= {r for r, _ in app.roles})
    from django.contrib.auth.models import Permission
    check('role permissions in the database', Permission.objects.filter(codename='crm_director').exists())
    check('enquiry form has the industry and its fields', any(f['name'] == 'industry' for f in regs['enquiry']['form']) and any(f['name'] == 'x_dc_racks' for f in regs['enquiry']['form']))
    check('new activity kinds', any(c['value'] == 'site_visit' for f in regs['activity']['form'] if f['name'] == 'kind' for c in f['choices']))
    check('opportunity decision makers and strategy', {'decision_makers', 'strategy', 'technical_requirements'} <= {f['name'] for f in regs['opportunity']['form']})

    # ---------------------------------------------------------------- master data
    acc = Account.objects.create(number='ACC-T1', name='Falcon Hosting LLC', kind='prospect', email='it@falcon.test')
    c = Contact.objects.create(number='CON-T1', name='Omar Falcon', email='omar@falcon.test', account=acc, contact_type='prospect')
    uom = UOM.objects.first()
    p = Product.objects.create(code='RACK42', name='42U rack – colocation', sale_price=3500, uom=uom) if not Product.objects.filter(code='RACK42').exists() else Product.objects.get(code='RACK42')

    # ---------------------------------------------------------------- enquiry
    r = post('enquiry', {'title': 'Colocation 10 racks', 'account': acc.pk, 'contact': c.pk, 'classification': 'service', 'industry': 'data_center',
                         'items': [{'product': p.pk, 'description': '42U rack', 'qty': 10, 'uom': 'Rack'}], 'requirement': '10 racks, 2N power',
                         'estimated_value': 420000, 'technical_owner': emp.pk, 'site_visit': True, 'x_dc_racks': 10, 'x_dc_power_kw': 60})
    E = body(r)
    check('enquiry created with industry fields', r.status_code == 201 and E.get('x_dc_racks') == 10 and E.get('number', '').startswith('ENQ'), r.content[:300])
    eid = E.get('id')
    r = act('enquiry', eid, 'submit')
    check('submit → technical evaluation', Enquiry.objects.get(pk=eid).status == 'evaluation', r.content[:200])
    r = act('enquiry', eid, 'ask', {'question': 'Which redundancy – 2N or N+1?'})
    check('clarification asked', Enquiry.objects.get(pk=eid).status == 'clarification', r.content[:200])
    r = act('enquiry', eid, 'answer', {'answer': '2N'})
    check('clarified → back to evaluation', Enquiry.objects.get(pk=eid).status == 'evaluation', r.content[:200])
    r = act('enquiry', eid, 'evaluate', {'evaluation': 'feasible', 'notes': 'Capacity in DXB-1 hall B'})
    check('evaluated → waiting for approval', Enquiry.objects.get(pk=eid).status == 'approval', r.content[:200])
    r = act('enquiry', eid, 'quote')
    check('cannot quote before approval', r.status_code == 400, r.content[:200])
    r = act('enquiry', eid, 'approve', {'note': 'ok'})
    check('approved', Enquiry.objects.get(pk=eid).status == 'approved', r.content[:200])
    r = act('enquiry', eid, 'quote')
    Eo = Enquiry.objects.get(pk=eid)
    Q = Eo.quotation
    check('quotation made from the enquiry', r.status_code == 200 and Q is not None and Eo.status == 'quoted', r.content[:300])
    check('… with priced lines from the product', Q and Q.lines.count() == 1 and float(Q.subtotal) == 35000.0, Q and (Q.lines.count(), Q.subtotal))
    check('… and an opportunity in the data centre pipeline with the values', Eo.opportunity and Eo.opportunity.pipeline.code == 'DCS' and Eo.opportunity.extra.get('dc_racks') == 10,
          Eo.opportunity and (Eo.opportunity.pipeline.code, Eo.opportunity.extra))
    O = Eo.opportunity
    r = act('opportunity', O.pk, 'enquiry', {'title': 'Extra cross-connects'})
    check('New enquiry from an opportunity', r.status_code == 200 and Enquiry.objects.filter(opportunity=O).count() == 2, r.content[:200])

    # ---------------------------------------------------------------- customer profile: block / release
    prof = CustomerProfile.objects.create(account=acc)
    r = act('customer_profile', prof.pk, 'block', {'reason': 'Unpaid invoices'})
    check('customer blocked', CustomerProfile.objects.get(pk=prof.pk).status == 'blocked', r.content[:200])
    sales = Pipeline.objects.get(code='SALES')
    r = post('opportunity', {'title': 'Blocked deal', 'contact': c.pk, 'account': acc.pk, 'pipeline': sales.pk})
    check('no new opportunity for a blocked customer', r.status_code == 400 and 'blocked' in r.content.decode(), r.content[:200])
    r = act('customer_profile', prof.pk, 'release', {'note': 'paid'})
    check('released', CustomerProfile.objects.get(pk=prof.pk).status == 'active')

    # ---------------------------------------------------------------- won → onboarding
    sla = Stage.objects.get(code='DCS_QUOTE')
    patch('opportunity', O.pk, {'x_dc_sla': '99.99%', 'x_dc_redundancy': '2N'})
    r = act('opportunity', O.pk, 'won', {'note': 'PO 123'})
    check('opportunity won', Opportunity.objects.get(pk=O.pk).status == 'won', r.content[:300])
    onb = Onboarding.objects.filter(opportunity=O).first()
    check('onboarding started with a checklist', onb is not None and len(onb.checklist) >= 5, onb and onb.checklist)
    r = act('onboarding', onb.pk, 'complete')
    check('cannot complete with open steps', r.status_code == 400, r.content[:200])
    r = act('onboarding', onb.pk, 'welcome', {})
    check('welcome e-mail sent', Onboarding.objects.get(pk=onb.pk).welcome_sent, r.content[:200])
    r = act('onboarding', onb.pk, 'activate')
    check('customer account activated', Account.objects.get(pk=acc.pk).kind == 'customer' and Onboarding.objects.get(pk=onb.pk).account_activated, r.content[:200])
    ch = Onboarding.objects.get(pk=onb.pk).checklist
    for x in ch:
        x['done'] = True
    r = patch('onboarding', onb.pk, {'checklist': ch})
    check('checklist ticked → in progress', Onboarding.objects.get(pk=onb.pk).status == 'in_progress', r.content[:200])
    r = act('onboarding', onb.pk, 'complete')
    check('onboarding completed', Onboarding.objects.get(pk=onb.pk).status == 'completed', r.content[:200])

    # ---------------------------------------------------------------- contracts
    t = today()
    r = post('contract', {'title': 'Colocation DXB-1', 'kind': 'service', 'account': acc.pk, 'contact': c.pk, 'opportunity': O.pk, 'value': 420000,
                          'start': str(t - timedelta(days=345)), 'end': str(t + timedelta(days=20)), 'notice_days': 30, 'owner': emp.pk})
    C = body(r)
    check('contract created', r.status_code == 201, r.content[:300])
    act('contract', C['id'], 'submit')
    r = act('contract', C['id'], 'approve')
    check('approved → active', Contract.objects.get(pk=C['id']).status == 'active', r.content[:200])
    out = app.daily()
    Cx = Contract.objects.get(pk=C['id'])
    check('daily: reminder and renewal opportunity', Cx.renewal_opportunity_id is not None and Cx.reminded, (out, Cx.reminded))
    ro = Cx.renewal_opportunity
    check('renewal opportunity in the renewal pipeline', ro.pipeline.kind == 'renewal' and ro.account_id == acc.pk, ro.pipeline.code)
    r = act('opportunity', ro.pk, 'won', {'note': 'renewed'})
    Cx = Contract.objects.get(pk=C['id'])
    nxt = Contract.objects.filter(previous=Cx).first()
    check('won renewal renews the contract', Cx.status == 'renewed' and nxt is not None and nxt.status == 'active' and nxt.start == Cx.end + timedelta(days=1),
          (Cx.status, nxt and (nxt.status, nxt.start)))
    check('renewal does not start a new onboarding', not Onboarding.objects.filter(opportunity=ro).exists())
    r = act('contract', nxt.pk, 'revise', {'note': 'price change'})
    rev = Contract.objects.filter(previous=nxt, revision=2).first()
    check('contract revision', rev is not None and rev.status == 'draft', r.content[:200])
    r = act('contract', nxt.pk, 'terminate', {'reason': 'Customer moved'})
    check('termination (manager)', Contract.objects.get(pk=nxt.pk).status == 'terminated', r.content[:200])

    # subscription quotation → contract
    q = Quotation.objects.create(number='QT-SUB1', account=acc, contact=c, date=t, currency='AED', quote_kind='subscription', billing='monthly', status='sent', total=1000)
    q.status = 'accepted'
    q.save()
    check('accepted subscription quotation → subscription contract', Contract.objects.filter(quotation=q, kind='subscription', auto_renew=True).exists())

    # ---------------------------------------------------------------- complaints
    r = post('complaint', {'subject': 'Power outage in hall B', 'account': acc.pk, 'contact': c.pk, 'category': 'service', 'priority': 'urgent', 'owner': emp.pk})
    K = body(r)
    Kx = Complaint.objects.get(pk=K['id'])
    check('complaint with a 4-hour SLA', r.status_code == 201 and abs((Kx.due_at - Kx.created_at).total_seconds() - 4 * 3600) < 120 and Kx.status == 'assigned',
          (r.content[:200], Kx.due_at, Kx.created_at))
    Complaint.objects.filter(pk=Kx.pk).update(due_at=timezone.now() - timedelta(hours=1))
    app.daily()
    Kx.refresh_from_db()
    check('daily escalates a late complaint', Kx.sla_breached and Kx.escalated == 1, (Kx.sla_breached, Kx.escalated))
    act('complaint', Kx.pk, 'investigate', {'notes': 'UPS B tripped'})
    r = act('complaint', Kx.pk, 'resolve', {'resolution': 'UPS replaced'})
    check('resolved', Complaint.objects.get(pk=Kx.pk).status == 'resolved', r.content[:200])
    r = act('complaint', Kx.pk, 'close', {'rating': '4', 'feedback': 'Quick fix', 'confirmed': True})
    check('closed with rating', Complaint.objects.get(pk=Kx.pk).status == 'closed' and Complaint.objects.get(pk=Kx.pk).rating == 4, r.content[:200])
    r = act('complaint', Kx.pk, 'reopen', {'note': 'happened again'})
    check('reopen', Complaint.objects.get(pk=Kx.pk).status == 'investigating', r.content[:200])

    # ---------------------------------------------------------------- 360° figures
    prof.refresh_from_db()
    GENERAL['recalc_all']()
    prof.refresh_from_db()
    check('lifetime value and won deals', float(prof.lifetime_value) >= 35000 and prof.won_count >= 2, (prof.lifetime_value, prof.won_count))
    check('satisfaction from complaint ratings', prof.satisfaction is not None and float(prof.satisfaction) == 4.0, prof.satisfaction)
    check('relationship score worked out', prof.relationship_score > 0 and prof.segment in ('A', 'B', 'C'), (prof.relationship_score, prof.segment))

    # ---------------------------------------------------------------- commissions
    O2 = Opportunity.objects.get(pk=O.pk)
    O2.owner = emp
    O2.save(update_fields=['owner'])
    r = post('commission_plan', {'code': 'STD5', 'name': 'Standard 5 %', 'basis': 'won_value', 'rate': 5})
    plan = body(r)
    r = act('commission_plan', plan['id'], 'calculate', {'from': str(t - timedelta(days=5)), 'to': str(t)})
    cm = Commission.objects.filter(plan_id=plan['id'], source='opportunity', source_id=O.pk).first()
    check('commission calculated', cm is not None and float(cm.amount) == round(float(O2.value) * 0.05, 2), (r.content[:200], cm and cm.amount, O2.value))
    act('commission_plan', plan['id'], 'calculate', {'from': str(t - timedelta(days=5)), 'to': str(t)})
    check('calculating again does not double', Commission.objects.filter(plan_id=plan['id'], source='opportunity', source_id=O.pk).count() == 1)
    act('commission', cm.pk, 'approve')
    r = act('commission', cm.pk, 'pay', {'ref': 'PAYROLL-NOV'})
    check('commission approved and paid', Commission.objects.get(pk=cm.pk).status == 'paid', r.content[:200])

    # ---------------------------------------------------------------- documents
    r = post('document', {'title': 'Colocation SLA', 'kind': 'contract', 'account': acc.pk, 'expiry_date': str(t + timedelta(days=10))})
    D = body(r)
    act('document', D['id'], 'submit')
    act('document', D['id'], 'approve')
    r = act('document', D['id'], 'version', {'note': 'v2'})
    v2 = CRMDocument.objects.filter(previous_id=D['id']).first()
    act('document', v2.pk, 'approve')
    check('new version approved → old one superseded', CRMDocument.objects.get(pk=D['id']).status == 'superseded' and v2.version == 2)
    app.daily()
    check('expiry reminder', CRMDocument.objects.get(pk=v2.pk).reminded)

    # ---------------------------------------------------------------- quotation templates and comparison
    r = post('quotation_template', {'code': 'COLO', 'name': 'Colocation standard', 'quote_kind': 'service', 'delivery_terms': 'Racks ready in 10 working days',
                                    'scope_of_work': 'Rack, power, cooling, 24/7 access', 'terms': 'Monthly in advance', 'validity_days': 15,
                                    'items': [{'product': p.pk, 'description': 'Setup fee', 'qty': 1, 'unit_price': 5000}]})
    T = body(r)
    r = act('quotation', Q.pk, 'apply_template', {'template': T['id']})
    Qx = Quotation.objects.get(pk=Q.pk)
    check('template applied: lines, terms, validity', r.status_code == 200 and Qx.lines.count() == 2 and 'Racks ready' in Qx.delivery_terms and Qx.valid_until == Qx.date + timedelta(days=15),
          (r.content[:200], Qx.lines.count(), Qx.delivery_terms))
    from ZeoCRM.pdf import build
    check('PDF with scope of work and delivery terms', build(Qx, 'Quotation', app)[:4] == b'%PDF')
    r = act('quotation', Q.pk, 'revise', {'note': 'discount'})
    Q2 = Quotation.objects.filter(previous=Qx).first()
    items = Q2.items
    items[0]['qty'] = 12
    patch('quotation', Q2.pk, {'items': items, 'discount_pct': 5})
    r = act('quotation', Q2.pk, 'compare')
    msg = (body(r).get('result') or {}).get('message', '')
    check('version comparison lists the changes', 'qty 10.0 → 12' in msg and 'Overall discount' in msg, r.content[:600])

    # ---------------------------------------------------------------- follow-up escalation
    a = Activity.objects.create(number='ACT-T1', kind='call', subject='Call Omar', contact=c, owner=emp, due_at=timezone.now() - timedelta(hours=60), status='open')
    app.daily()
    a.refresh_from_db()
    check('missed follow-up escalated to the team lead (2)', a.escalated == 2, a.escalated)

    # ---------------------------------------------------------------- reports and home
    from DashboardManagement.reports import REPORTS, ReportView
    keys = ['crm-lead-ageing', 'crm-opportunity-ageing', 'crm-win-loss', 'crm-lead-conversion', 'crm-quotation-conversion', 'crm-customer-acquisition',
            'crm-customer-retention', 'crm-customer-churn', 'crm-customer-value', 'crm-followups', 'crm-forecast-quarter', 'crm-forecast-year', 'crm-forecast-territory',
            'crm-forecast-industry', 'crm-forecast-pipeline', 'crm-forecast-product', 'crm-complaints', 'crm-contracts-expiring', 'crm-commissions', 'crm-onboarding',
            'crm-enquiries', 'crm-campaign-roi']
    bad = []
    for k in keys:
        if REPORTS.get(k) is None:
            bad.append((k, 'missing'))
            continue
        req = rf.get(f'/?schema={SCHEMA}&period=year')
        force_authenticate(req, user=user)
        resp = ReportView.as_view()(req, key=k)
        resp.render()
        if resp.status_code != 200:
            bad.append((k, resp.status_code, resp.content[:200]))
    check(f'release 2 reports run ({len(keys) - len(bad)}/{len(keys)})', not bad, bad)
    req = rf.get(f'/?schema={SCHEMA}')
    force_authenticate(req, user=user)
    from rest_framework.views import APIView
    dreq = APIView().initialize_request(req)
    dreq.user = user
    dash = app.dashboard(dreq)
    labels = {c_['label'] for c_ in dash['cards']}
    check('CRM home cards for complaints, contracts, churn', {'Open complaints', 'Contracts ending in 60 days', 'Customers at churn risk'} <= labels, labels)
    s = CRMSettings.objects.first()
    check('settings fields', 'complaint_sla_urgent' in app.settings_fields and s.complaint_sla_urgent == 4)


with schema_context(SCHEMA):
    try:
        run()
    except Exception:
        traceback.print_exc()
        RESULTS.append(('crashed', False, ''))
fails = [r for r in RESULTS if not r[1]]
print(f'\n{len(RESULTS) - len(fails)} / {len(RESULTS)} checks passed')
