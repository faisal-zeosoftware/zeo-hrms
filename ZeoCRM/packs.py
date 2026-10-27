"""CRM industry packs. General = no industry; the property management pack adds leasing, sales and renewal pipelines."""


def pipe(code, name, kind, order, stages):
    """stages: [(code, name, probability, won, lost)]"""
    return ({'code': code, 'name': name, 'kind': kind, 'order': order},
            [{'pipeline': f'@pipeline:{code}', 'code': f'{code}_{c}', 'name': n, 'order': i + 1, 'probability': p, 'is_won': w, 'is_lost': l}
             for i, (c, n, p, w, l) in enumerate(stages)])


G_SALES = pipe('SALES', 'Sales', 'sales', 1, [('NEW', 'New', 10, False, False), ('QUALIFIED', 'Qualified', 25, False, False),
                                               ('PROPOSAL', 'Proposal / offer', 50, False, False), ('NEGOTIATION', 'Negotiation', 75, False, False),
                                               ('WON', 'Won', 100, True, False), ('LOST', 'Lost', 0, False, True)])
P_LEASE = pipe('LEASING', 'Leasing', 'leasing', 1, [('NEW', 'New enquiry', 10, False, False), ('CONTACTED', 'Contacted', 20, False, False),
                                                     ('VIEWING', 'Viewing', 40, False, False), ('OFFER', 'Offer made', 60, False, False),
                                                     ('RESERVED', 'Reserved', 85, False, False), ('WON', 'Lease signed', 100, True, False),
                                                     ('LOST', 'Lost', 0, False, True)])
P_SALES = pipe('PSALES', 'Property sales', 'sales', 2, [('NEW', 'New enquiry', 10, False, False), ('CONTACTED', 'Contacted', 20, False, False),
                                                        ('VIEWING', 'Viewing', 35, False, False), ('OFFER', 'Offer made', 55, False, False),
                                                        ('RESERVED', 'Reserved', 80, False, False), ('WON', 'Sale contract signed', 100, True, False),
                                                        ('LOST', 'Lost', 0, False, True)])
P_RENEW = pipe('RENEWAL', 'Lease renewals', 'renewal', 3, [('DUE', 'Renewal due', 50, False, False), ('OFFER', 'Renewal offered', 60, False, False),
                                                           ('NEGOTIATION', 'Negotiating', 75, False, False), ('WON', 'Renewed', 100, True, False),
                                                           ('LOST', 'Not renewing', 0, False, True)])

SOURCES_G = [('WEB', 'Website'), ('REFERRAL', 'Referral'), ('WALKIN', 'Walk-in'), ('PHONE', 'Phone call'), ('SOCIAL', 'Social media'),
             ('EMAIL', 'E-mail campaign'), ('PARTNER', 'Partner')]
SOURCES_P = [('PORTAL', 'Property portal listing'), ('SIGNBOARD', 'Signboard'), ('BROKER', 'Broker / agent'), ('EXISTING', 'Existing tenant')]
LOST_G = [('PRICE', 'Price'), ('COMPETITOR', 'Chose a competitor'), ('NORESP', 'No response'), ('NOTFIT', 'Not a fit'), ('TIMING', 'Timing')]
LOST_P = [('LOCATION', 'Location'), ('SIZE', 'Size / layout'), ('NOTAVAIL', 'Unit not available'), ('FINANCE', 'Financing / budget'),
          ('OTHERPROP', 'Took another property')]


# v1.33.0 lead score rules (name, when, value, points) – the qualify score is 50 in CRM setup
SCORE = [('Has an e-mail', 'has_email', '', 10), ('Has a phone number', 'has_phone', '', 15), ('Company given', 'has_company', '', 15),
         ('Referral', 'source', 'REFERRAL', 20), ('Walk-in', 'source', 'WALKIN', 10), ('Value 10,000 or more', 'value_min', '10000', 20),
         ('Value 50,000 or more', 'value_min', '50000', 20), ('Spoke to the lead', 'activity', '', 25), ('Decision maker', 'job_title', 'manager', 10),
         ('Owner / director', 'job_title', 'director', 15), ('Urgent requirement', 'keyword', 'urgent', 15)]


# v1.33.1 property enquiries: budget, bedrooms, community, portal / broker / existing tenant, viewing done
SCORE_P = [('Has a phone number', 'has_phone', '', 15), ('Has an e-mail', 'has_email', '', 10), ('Budget given', 'has_budget', '', 15),
           ('Bedrooms given', 'has_bedrooms', '', 10), ('Community / building given', 'has_area', '', 10), ('Looking to buy', 'interest', 'buy', 10),
           ('Existing tenant', 'source', 'EXISTING', 20), ('Broker / agent', 'source', 'BROKER', 10), ('Property portal', 'source', 'PORTAL', 10),
           ('Referral', 'source', 'REFERRAL', 20), ('Budget 100,000 or more', 'value_min', '100000', 15), ('Ready to move', 'keyword', 'ready', 10),
           ('Urgent requirement', 'keyword', 'urgent', 15), ('Spoke to the lead', 'activity', '', 20), ('Viewing / site visit done', 'meeting', '', 25)]


def rows(pipes, sources, lost, score=None):
    return {'pipeline': [p[0] for p in pipes], 'stage': [s for p in pipes for s in p[1]],
            'source': [{'code': c, 'name': n} for c, n in sources], 'lost_reason': [{'code': c, 'name': n} for c, n in lost],
            'score_rule': [{'name': n, 'when': w, 'value': v, 'points': p, 'order': i + 1} for i, (n, w, v, p) in enumerate(score or SCORE)]}


# v1.38.0 industry pipelines – one per business type of the company master (Settings → Company → Industry)
I_CONSTRUCTION = pipe('TENDER', 'Tenders and contracts', 'sales', 1, [('ENQUIRY', 'Enquiry / RFQ', 10, False, False), ('SITEVISIT', 'Site visit', 25, False, False),
                                                                       ('ESTIMATE', 'Estimation / BOQ', 40, False, False), ('TENDER', 'Tender submitted', 55, False, False),
                                                                       ('NEGOTIATION', 'Negotiation / LOI', 75, False, False), ('WON', 'Contract awarded', 100, True, False),
                                                                       ('LOST', 'Lost', 0, False, True)])
I_OILGAS = pipe('OGBID', 'Bids and service contracts', 'sales', 1, [('PREQUAL', 'Pre-qualification', 10, False, False), ('RFQ', 'RFQ / ITT received', 25, False, False),
                                                                     ('TECH', 'Technical bid', 40, False, False), ('COMM', 'Commercial bid', 60, False, False),
                                                                     ('CLARIF', 'Clarifications / negotiation', 75, False, False), ('WON', 'PO / contract received', 100, True, False),
                                                                     ('LOST', 'Not awarded', 0, False, True)])
I_HEALTH = pipe('CORP', 'Corporate and referral clients', 'sales', 1, [('ENQUIRY', 'Enquiry', 10, False, False), ('CONSULT', 'Consultation / visit', 30, False, False),
                                                                        ('PROPOSAL', 'Package proposed', 50, False, False), ('APPROVAL', 'Insurance / approval', 70, False, False),
                                                                        ('WON', 'Contract / treatment started', 100, True, False), ('LOST', 'Lost', 0, False, True)])
I_EDU = pipe('ADMISSION', 'Admissions', 'sales', 1, [('ENQUIRY', 'Enquiry', 10, False, False), ('TOUR', 'Campus tour / open day', 30, False, False),
                                                      ('ASSESS', 'Assessment / interview', 50, False, False), ('OFFER', 'Offer letter', 70, False, False),
                                                      ('WON', 'Enrolled', 100, True, False), ('LOST', 'Withdrawn', 0, False, True)])
I_MFG = pipe('DEALER', 'Dealers and projects', 'sales', 1, [('ENQUIRY', 'Enquiry', 10, False, False), ('SAMPLE', 'Sample / demo', 30, False, False),
                                                             ('QUOTE', 'Quotation', 50, False, False), ('TRIAL', 'Trial order', 70, False, False),
                                                             ('WON', 'Supply agreement', 100, True, False), ('LOST', 'Lost', 0, False, True)])
I_RETAIL = pipe('B2B', 'Wholesale and corporate', 'sales', 1, [('ENQUIRY', 'Enquiry', 10, False, False), ('CATALOGUE', 'Catalogue / price list sent', 30, False, False),
                                                                ('QUOTE', 'Quotation', 50, False, False), ('NEGOTIATION', 'Negotiation', 75, False, False),
                                                                ('WON', 'Order placed', 100, True, False), ('LOST', 'Lost', 0, False, True)])
I_IT = pipe('SOLUTION', 'Solutions and subscriptions', 'sales', 1, [('MQL', 'Marketing qualified', 10, False, False), ('DISCOVERY', 'Discovery call', 25, False, False),
                                                                     ('DEMO', 'Demo / POC', 45, False, False), ('PROPOSAL', 'Proposal', 60, False, False),
                                                                     ('NEGOTIATION', 'Negotiation / legal', 80, False, False), ('WON', 'Closed won', 100, True, False),
                                                                     ('LOST', 'Closed lost', 0, False, True)])
I_FIN = pipe('ADVISORY', 'Advisory and financing', 'sales', 1, [('ENQUIRY', 'Enquiry', 10, False, False), ('KYC', 'KYC / documents', 30, False, False),
                                                                 ('PROPOSAL', 'Proposal / term sheet', 55, False, False), ('APPROVAL', 'Credit / compliance approval', 75, False, False),
                                                                 ('WON', 'Agreement signed', 100, True, False), ('LOST', 'Declined', 0, False, True)])
SOURCES_B2B = [('TENDER', 'Tender portal'), ('EXHIBITION', 'Exhibition / event'), ('CONSULTANT', 'Consultant'), ('EXISTING', 'Existing client')]
SOURCES_HC = [('REFERRAL_DR', 'Doctor referral'), ('INSURANCE', 'Insurance network'), ('CORPORATE', 'Corporate tie-up')]
SOURCES_EDU = [('SCHOOLFAIR', 'School fair'), ('SIBLING', 'Sibling / parent referral'), ('AGENT', 'Education agent')]
SOURCES_IT = [('WEBINAR', 'Webinar'), ('MARKETPLACE', 'Marketplace / app store'), ('INBOUND', 'Inbound (content)')]
LOST_B2B = [('BUDGET', 'Budget not approved'), ('SPEC', 'Specification not met'), ('DELIVERY', 'Delivery time')]
SCORE_B2B = SCORE + [('Tender / RFQ in hand', 'keyword', 'tender', 15), ('Consultant involved', 'source', 'CONSULTANT', 10)]


def _ind(label, description, highlights, pipes, sources_extra, lost_extra, score=None, terms=None):
    d = {'label': label, 'description': description, 'highlights': highlights, 'rows': rows(pipes, SOURCES_G + sources_extra, LOST_G + lost_extra, score)}
    if terms:
        d['terms'] = terms
    return d


PACKS = {
    'general': {'label': 'General', 'description': 'One sales pipeline (new → qualified → proposal → negotiation → won / lost), common lead sources and lost reasons.',
                'highlights': ['Sales pipeline', '7 lead sources', '5 lost reasons', '11 lead score rules'],
                'rows': rows([G_SALES], SOURCES_G, LOST_G)},
    'property_management': {
        'label': 'Property management', 'description': 'Leasing, property sales and lease renewal pipelines; property lead sources; viewings and offers. Works with ZEO Property.',
        'highlights': ['Leasing pipeline', 'Property sales pipeline', 'Renewal pipeline', 'Property lead sources', '15 property lead score rules'],
        'terms': {'opportunity': 'Enquiry', 'opportunities': 'Enquiries'},
        'rows': rows([P_LEASE, P_SALES, P_RENEW], SOURCES_G + SOURCES_P, LOST_G + LOST_P, SCORE_P)},
    # v1.38.0: one pack per business type of the company master
    'construction': _ind('Construction and engineering', 'Tenders and contracts pipeline (enquiry → site visit → estimation / BOQ → tender → LOI → award); tender portal, exhibition and consultant sources.',
                         ['Tenders pipeline', 'Tender / consultant sources', 'Budget / specification lost reasons'], [I_CONSTRUCTION, G_SALES], SOURCES_B2B, LOST_B2B, SCORE_B2B,
                         {'opportunity': 'Tender', 'opportunities': 'Tenders'}),
    'oil_gas': _ind('Oil and gas', 'Bids and service contracts pipeline (pre-qualification → RFQ / ITT → technical and commercial bids → clarifications → PO).',
                    ['Bids pipeline', 'Pre-qualification stage', 'Tender sources'], [I_OILGAS, G_SALES], SOURCES_B2B, LOST_B2B, SCORE_B2B, {'opportunity': 'Bid', 'opportunities': 'Bids'}),
    'healthcare': _ind('Healthcare', 'Corporate and referral clients pipeline (enquiry → consultation → package → insurance approval → treatment); doctor referral, insurance and corporate sources.',
                       ['Corporate / referral pipeline', 'Doctor referral and insurance sources'], [I_HEALTH, G_SALES], SOURCES_HC, [('COVER', 'Not covered by insurance')]),
    'education': _ind('Education', 'Admissions pipeline (enquiry → campus tour → assessment → offer → enrolled); school fair, sibling referral and agent sources.',
                      ['Admissions pipeline', 'Education sources'], [I_EDU, G_SALES], SOURCES_EDU, [('FEES', 'Fees'), ('SEATS', 'No seats in the grade')], None,
                      {'opportunity': 'Admission', 'opportunities': 'Admissions'}),
    'manufacturing': _ind('Manufacturing', 'Dealers and projects pipeline (enquiry → sample / demo → quotation → trial order → supply agreement); exhibition and consultant sources.',
                          ['Dealer pipeline', 'Sample / trial stages'], [I_MFG, G_SALES], SOURCES_B2B, LOST_B2B, SCORE_B2B),
    'retail': _ind('Retail and trading', 'Wholesale and corporate pipeline (enquiry → catalogue → quotation → negotiation → order) next to the walk-in sales pipeline.',
                   ['Wholesale pipeline', 'Catalogue stage'], [G_SALES, I_RETAIL], [('MARKETPLACE', 'Marketplace'), ('EXISTING', 'Existing customer')], [('DELIVERY', 'Delivery time')]),
    'it': _ind('Information technology', 'Solutions and subscriptions pipeline (MQL → discovery → demo / POC → proposal → legal → closed won); webinar, marketplace and inbound sources.',
               ['Solutions pipeline', 'Demo / POC stage', 'Inbound sources'], [I_IT, G_SALES], SOURCES_IT, [('CHURN', 'Chose to stay / churned')],
               SCORE + [('Demo done', 'meeting', '', 25), ('Decision maker', 'job_title', 'cto', 15)]),
    'finance': _ind('Finance', 'Advisory and financing pipeline (enquiry → KYC → proposal / term sheet → credit and compliance approval → agreement).',
                    ['Advisory pipeline', 'KYC and compliance stages'], [I_FIN, G_SALES], [('EXISTING', 'Existing client'), ('BANK', 'Bank / partner referral')], [('COMPLIANCE', 'Compliance / KYC')]),
}
