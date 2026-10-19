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


PACKS = {
    'general': {'label': 'General', 'description': 'One sales pipeline (new → qualified → proposal → negotiation → won / lost), common lead sources and lost reasons.',
                'highlights': ['Sales pipeline', '7 lead sources', '5 lost reasons', '11 lead score rules'],
                'rows': rows([G_SALES], SOURCES_G, LOST_G)},
    'property_management': {
        'label': 'Property management', 'description': 'Leasing, property sales and lease renewal pipelines; property lead sources; viewings and offers. Works with ZEO Property.',
        'highlights': ['Leasing pipeline', 'Property sales pipeline', 'Renewal pipeline', 'Property lead sources', '15 property lead score rules'],
        'terms': {'opportunity': 'Enquiry', 'opportunities': 'Enquiries'},
        'rows': rows([P_LEASE, P_SALES, P_RENEW], SOURCES_G + SOURCES_P, LOST_G + LOST_P, SCORE_P)},
}
