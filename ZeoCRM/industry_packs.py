"""v1.67.27 – CRM industry packs, release 1: the industry fields, workflows (pipelines), lead sources, lost reasons, score rules and
reports of the 24 industries of the CRM functional scope ("CRM Software – Complete General & Industry-Wise Functional Scope", part 2).

Content only. packs.py merges this into PACKS:
  * an industry that already had a pack (IT, construction, manufacturing, oil and gas, healthcare, education, retail, property, and the
    service packs FM, telecom, cleaning, pest control) keeps its pipeline and gets the fields, reports and extra sources / lost reasons;
  * a new industry gets its own pack with the workflow of the scope as its pipeline (next to the general sales pipeline).
Packs only ADD: a company that already installed a pack installs it again (Setup › Industry pack) to receive the new rows.

Field codes are unique across every pack (prefix per industry). Stage codes are <pipeline code>_<stage>.
"""

# ------------------------------------------------------------------ the little language of this file
# f(code, label, type, ...)   type: text | textarea | number | money | date | choice | yesno
#   on:  letters – l lead, o opportunity, q quotation, s sales order, p print on the quotation (implies q)
#   lst: list column · flt: list filter (pick list / yes-no) · req: always required · rf: required from this stage code on (and to win)
# rp(code, name, group, ...)  group: stage | pipeline | agent | source | status | territory | campaign | account | month | lost_reason
#                                    | field:<code>   (a date field groups by month)
#   src: lead | opportunity | quotation · measure: field:<code> (number / amount total) · status: all | open | won | lost
#   date: created | expected_close | closed | none · dash: show on the CRM home


def f(code, label, typ='text', ch=None, *, unit='', on='lo', req=False, rf='', lst=False, flt=False, help='', sec=''):
    return {'code': code, 'label': label, 'field_type': typ, 'choices': '\n'.join(ch or []), 'unit': unit, 'section': sec, 'help': help,
            'on_lead': 'l' in on, 'on_opportunity': 'o' in on, 'on_enquiry': 'l' in on or 'o' in on, 'on_quotation': 'q' in on or 'p' in on, 'on_order': 's' in on,
            'on_pdf': 'p' in on, 'required': req, 'required_from': rf, 'in_list': lst, 'in_filter': flt}


def rp(code, name, group, *, src='opportunity', measure='', status='all', date='created', dash=False, desc=''):
    return {'code': code, 'name': name, 'group_by': group, 'source': src, 'measure': measure, 'status': status, 'date_field': date,
            'on_dashboard': dash, 'description': desc}


def wf(code, name, stages, kind='sales'):
    """A pipeline from the scope's workflow: [(stage, name, probability)], then Won and Lost."""
    return {'code': code, 'name': name, 'kind': kind, 'stages': stages}


YN = None
MONTHS = 'months'


# ================================================================== 01 ERP, IT software and technology
IT = {
    'label': 'ERP, IT software and technology',
    'description': 'Software and ERP sales: requirement collection, discovery, demo, gap analysis, estimation, licence / subscription quotation, '
                   'implementation handover, AMC and renewal.',
    'pipeline': wf('ITIMPL', 'Software implementation', [('REQ', 'Requirement collection', 10), ('DISCOVERY', 'Discovery meeting', 20), ('DEMO', 'Product demo', 30),
                                                         ('GAP', 'Gap analysis', 40), ('SOLUTION', 'Solution proposal', 50), ('ESTIMATE', 'Development estimation', 60),
                                                         ('QUOTE', 'Commercial quotation', 70), ('NEGOTIATION', 'Negotiation', 80),
                                                         ('WON', 'Sales order / implementation handover', 100), ('LOST', 'Lost', 0)]),
    'sources': [('DEMO_REQ', 'Demo request'), ('PARTNER_REF', 'Software partner referral')],
    'lost': [('GAP', 'Too many gaps / customisation'), ('INCUMBENT_SW', 'Stayed on current software')],
    'fields': [
        f('it_product', 'Software product', 'choice', ['ERP', 'CRM', 'HRMS / payroll', 'Accounting', 'POS', 'Inventory / WMS', 'Industry solution', 'Infrastructure / cloud',
                                                     'Cybersecurity', 'Other'], on='lop', lst=True, flt=True, sec='Software requirement'),
        f('it_version', 'Software version / edition', on='lop', sec='Software requirement'),
        f('it_modules', 'Modules required', 'textarea', on='lop', sec='Software requirement'),
        f('it_users', 'Number of users', 'number', on='lop', lst=True, sec='Software requirement'),
        f('it_companies', 'Number of companies', 'number', on='lop', sec='Software requirement'),
        f('it_branches', 'Branches and locations', 'number', on='lop', sec='Software requirement'),
        f('it_deploy', 'Cloud / on-premise', 'choice', ['Cloud', 'On-premise', 'Hybrid'], on='lop', flt=True, sec='Software requirement'),
        f('it_migration', 'Data migration needed', 'yesno', on='lop', sec='Scope'),
        f('it_custom', 'Customisation needed', 'yesno', on='lo', flt=True, sec='Scope'),
        f('it_customization', 'Customisation requirement', 'textarea', on='lop', sec='Scope'),
        f('it_integration', 'Integration requirement', 'textarea', on='lop', sec='Scope'),
        f('it_third_party', 'Third-party applications', on='lo', sec='Scope'),
        f('it_timeline', 'Implementation timeline', 'number', unit='weeks', on='lop', sec='Scope'),
        f('it_dev_hours', 'Estimated development hours', 'number', unit='hours', on='oq', rf='ITIMPL_QUOTE', sec='Scope',
          help='Needed from the Commercial quotation stage of the implementation pipeline'),
        f('it_training', 'Training needed', 'yesno', on='lop', sec='Scope'),
        f('it_amc', 'Annual maintenance (AMC) needed', 'yesno', on='lop', flt=True, sec='Commercial'),
        f('it_subscription', 'Subscription value (per year)', 'money', on='lop', sec='Commercial'),
        f('it_licence', 'Licence model', 'choice', ['Perpetual', 'Subscription (SaaS)', 'Per user per month', 'Open source + services'], on='lop', sec='Commercial'),
    ],
    'reports': [
        rp('it_product_enquiries', 'Product-wise enquiries', 'field:it_product', src='lead', dash=True),
        rp('it_erp_opps', 'ERP implementation opportunities', 'stage', status='open', measure='field:it_users'),
        rp('it_demo_conversion', 'Demo-to-sale conversion by product', 'field:it_product', date='closed'),
        rp('it_dev_hours', 'Development hours pipeline', 'stage', status='open', measure='field:it_dev_hours', date='none'),
        rp('it_custom_opps', 'Customisation opportunities', 'field:it_custom', status='open', date='none'),
        rp('it_saas_forecast', 'SaaS subscription forecast', 'month', status='open', measure='field:it_subscription', date='expected_close'),
        rp('it_amc', 'AMC and support opportunities', 'field:it_amc', measure='field:it_subscription'),
        rp('it_upsell', 'Customer upselling opportunities', 'account', status='won', date='closed'),
    ],
    'score': [('Users given', 'field', 'it_users>=10', 10), ('Demo requested', 'source', 'DEMO_REQ', 15)],
}

# ================================================================== 02 Construction and civil engineering
CONSTRUCTION = {
    'label': 'Construction and civil engineering',
    'description': 'Project enquiries and tenders: tender register, BOQ, drawings, site visit, estimation, bid submission, variations, contract award.',
    'sources': [('CONSULTANT_REF', 'Consultant nomination'), ('MAIN_CONTR', 'Main contractor enquiry')],
    'lost': [('BOND', 'Bond / guarantee not possible'), ('PRICE_L1', 'Not the lowest bidder')],
    'fields': [
        f('cn_project', 'Project name', on='lop', lst=True, req=False, sec='Project'),
        f('cn_location', 'Project location', on='lop', sec='Project'),
        f('cn_type', 'Project type', 'choice', ['Residential', 'Commercial', 'Industrial', 'Infrastructure', 'Hospitality', 'Healthcare', 'Education', 'Fit-out', 'MEP',
                                               'Other'], on='lop', flt=True, sec='Project'),
        f('cn_client', 'Client', on='lop', sec='Project parties'),
        f('cn_consultant', 'Consultant', on='lop', sec='Project parties'),
        f('cn_main_contractor', 'Main contractor', on='lop', sec='Project parties'),
        f('cn_owner', 'Project owner / developer', on='lop', sec='Project parties'),
        f('cn_tender_ref', 'Tender reference', on='op', lst=True, sec='Tender'),
        f('cn_tender_close', 'Tender closing date', 'date', on='lo', lst=True, rf='TENDER_TENDER', sec='Tender'),
        f('cn_boq_ref', 'BOQ reference', on='op', rf='TENDER_ESTIMATE', sec='Tender'),
        f('cn_boq_received', 'BOQ received', 'yesno', on='lo', flt=True, sec='Tender'),
        f('cn_drawing_rev', 'Drawing revision', on='op', sec='Tender'),
        f('cn_area', 'Built-up area', 'number', unit='sq ft', on='lop', sec='Project'),
        f('cn_value', 'Project value', 'money', on='lo', sec='Project'),
        f('cn_duration', 'Construction duration', 'number', unit=MONTHS, on='lop', sec='Project'),
        f('cn_scope', 'Scope of work', 'textarea', on='lop', sec='Scope'),
        f('cn_materials', 'Material requirements', 'textarea', on='lop', sec='Scope'),
        f('cn_bond', 'Performance bond required', 'yesno', on='lop', sec='Commercial'),
        f('cn_bond_pct', 'Performance bond', 'number', unit='%', on='lop', sec='Commercial'),
        f('cn_variation', 'Variation / change order', 'yesno', on='lo', flt=True, sec='Commercial', help='The opportunity is a variation on an awarded project'),
    ],
    'reports': [
        rp('cn_tender_pipeline', 'Tender pipeline', 'stage', status='open', measure='field:cn_value', date='none', dash=True),
        rp('cn_win_loss', 'Tender win / loss', 'status', date='closed'),
        rp('cn_quote_status', 'Project quotation status', 'status', src='quotation'),
        rp('cn_boq_opps', 'BOQ-based opportunities', 'field:cn_boq_received', status='open', date='none'),
        rp('cn_by_client', 'Client-wise project opportunities', 'field:cn_client', measure='field:cn_value'),
        rp('cn_bid_calendar', 'Bid submission calendar', 'field:cn_tender_close', status='open', date='none'),
        rp('cn_by_type', 'Opportunities by project type', 'field:cn_type', measure='field:cn_value'),
        rp('cn_forecast', 'Project sales forecast', 'month', status='open', date='expected_close'),
    ],
    'score': [('BOQ received', 'field', 'cn_boq_received=yes', 15), ('Tender closing date given', 'field', 'cn_tender_close', 10)],
}

# ================================================================== 03 Manufacturing and industrial products
MANUFACTURING = {
    'label': 'Manufacturing and industrial products',
    'description': 'Product enquiries: specifications, drawings, feasibility, samples and approval, MOQ, customer pricing, repeat orders, dealers.',
    'sources': [('DEALER', 'Dealer / distributor')],
    'lost': [('MOQ', 'Below MOQ'), ('FEASIBILITY', 'Not feasible to produce'), ('SAMPLE_REJ', 'Sample rejected')],
    'fields': [
        f('mf_category', 'Product category', on='lop', lst=True, sec='Product'),
        f('mf_spec', 'Product specification', 'textarea', on='lop', sec='Product'),
        f('mf_dimensions', 'Dimensions', on='lop', sec='Product'),
        f('mf_material', 'Material type', on='lop', sec='Product'),
        f('mf_finish', 'Colour and finish', on='lop', sec='Product'),
        f('mf_qty', 'Required quantity', 'number', on='lops', lst=True, sec='Quantity and delivery'),
        f('mf_uom', 'Unit of measure', 'choice', ['Pcs', 'Kg', 'MT', 'm', 'm²', 'm³', 'Set', 'Box', 'Roll', 'Litre'], on='lop', sec='Quantity and delivery'),
        f('mf_moq', 'MOQ', 'number', on='lop', sec='Quantity and delivery'),
        f('mf_customized', 'Customised product', 'yesno', on='lo', flt=True, sec='Product'),
        f('mf_custom_req', 'Customisation requirement', 'textarea', on='lop', sec='Product'),
        f('mf_drawing', 'Drawing reference', on='lop', sec='Product'),
        f('mf_sample', 'Sample needed', 'yesno', on='lo', sec='Sample'),
        f('mf_sample_status', 'Sample status', 'choice', ['Not needed', 'Requested', 'In preparation', 'Sent', 'Approved', 'Rejected'], on='lo', lst=True, flt=True,
          sec='Sample'),
        f('mf_feasibility', 'Technical feasibility', 'choice', ['Pending', 'Feasible', 'Feasible with changes', 'Not feasible'], on='o', flt=True, rf='DEALER_QUOTE',
          sec='Sample'),
        f('mf_production_date', 'Expected production date', 'date', on='ops', sec='Quantity and delivery'),
        f('mf_delivery', 'Delivery schedule', on='lops', sec='Quantity and delivery'),
        f('mf_packaging', 'Packaging requirement', on='lops', sec='Quantity and delivery'),
        f('mf_certification', 'Certification requirements', on='lop', sec='Product'),
    ],
    'reports': [
        rp('mf_enquiry_pipeline', 'Product enquiry pipeline', 'field:mf_category', status='open', measure='field:mf_qty', date='none', dash=True),
        rp('mf_customer_demand', 'Customer-wise demand', 'account', measure='field:mf_qty'),
        rp('mf_sample_status', 'Sample approval status', 'field:mf_sample_status', status='open', date='none'),
        rp('mf_customized', 'Customised product enquiries', 'field:mf_customized', src='lead'),
        rp('mf_quote_conversion', 'Quotation conversion', 'status', src='quotation'),
        rp('mf_dealer', 'Dealer performance', 'account', status='won', date='closed'),
        rp('mf_repeat', 'Repeat order analysis', 'account', status='won', date='closed', measure='field:mf_qty'),
    ],
    'score': [('Quantity at or above MOQ', 'field', 'mf_qty>=1', 5), ('Drawing given', 'field', 'mf_drawing', 10)],
}

# ================================================================== 04 Oil, gas and petrochemical
OIL_GAS = {
    'label': 'Oil, gas and petrochemical',
    'description': 'EPC tenders, RFQs, equipment supply and maintenance contracts: vendor registration, pre-qualification, technical and commercial bids.',
    'sources': [('VENDOR_PORTAL', 'Vendor portal (ADNOC / Aramco …)')],
    'lost': [('PREQUAL_FAIL', 'Not pre-qualified'), ('TECH_REJ', 'Technically rejected')],
    'fields': [
        f('og_facility', 'Facility and plant', on='lop', lst=True, sec='Facility'),
        f('og_project_ref', 'Project reference', on='lop', sec='Facility'),
        f('og_equipment', 'Equipment category', 'choice', ['Rotating', 'Static', 'Electrical', 'Instrumentation', 'Piping', 'Valves', 'Safety', 'Chemicals',
                                                           'Manpower / services', 'Other'], on='lop', flt=True, sec='Requirement'),
        f('og_spec', 'Technical specifications', 'textarea', on='lop', sec='Requirement'),
        f('og_standards', 'API / industry standards', on='lop', sec='Requirement', help='e.g. API 610, ASME B31.3, NACE'),
        f('og_vendor_status', 'Vendor registration status', 'choice', ['Not registered', 'Applied', 'Registered', 'Expired'], on='lo', flt=True, sec='Qualification'),
        f('og_prequal', 'Pre-qualification status', 'choice', ['Not started', 'Submitted', 'Approved', 'Rejected'], on='lo', lst=True, flt=True, rf='OGBID_RFQ',
          sec='Qualification'),
        f('og_tender_no', 'Tender number', on='op', lst=True, sec='Bid'),
        f('og_tech_deadline', 'Technical bid deadline', 'date', on='o', sec='Bid'),
        f('og_comm_deadline', 'Commercial bid deadline', 'date', on='o', sec='Bid'),
        f('og_tech_status', 'Technical bid status', 'choice', ['Preparing', 'Submitted', 'Clarifications', 'Technically accepted', 'Technically rejected'], on='o',
          flt=True, sec='Bid'),
        f('og_duration', 'Contract duration', 'number', unit=MONTHS, on='lop', sec='Contract'),
        f('og_mobilization', 'Mobilisation requirement', 'textarea', on='op', sec='Contract'),
        f('og_site_access', 'Site access requirements', 'textarea', on='op', sec='Contract'),
    ],
    'reports': [
        rp('og_rfq_pipeline', 'RFQ pipeline', 'stage', status='open', date='none', dash=True),
        rp('og_participation', 'Tender participation', 'status'),
        rp('og_prequal', 'Pre-qualification status', 'field:og_prequal', date='none'),
        rp('og_tech_bid', 'Technical bid status', 'field:og_tech_status', status='open', date='none'),
        rp('og_contract_opps', 'Contract opportunities by equipment', 'field:og_equipment'),
        rp('og_facility_forecast', 'Customer and facility-wise sales forecast', 'field:og_facility', status='open', date='expected_close'),
    ],
}

# ================================================================== 05 Data centres and critical infrastructure (new)
DATA_CENTER = {
    'label': 'Data centres and critical infrastructure',
    'description': 'Colocation, cloud and managed services: rack, power, cooling and connectivity needs, capacity check, SLA proposal, onboarding and renewal.',
    'pipeline': wf('DCS', 'Data centre services', [('ENQUIRY', 'Enquiry', 10), ('DISCOVERY', 'Technical discovery', 20), ('CAPACITY', 'Capacity availability check', 30),
                                                    ('POWER', 'Power and cooling assessment', 40), ('NETWORK', 'Network requirement assessment', 50),
                                                    ('PROPOSAL', 'Service proposal', 60), ('QUOTE', 'SLA and commercial quotation', 70), ('APPROVAL', 'Contract approval', 85),
                                                    ('WON', 'Contract signed – onboarding', 100), ('LOST', 'Lost', 0)]),
    'sources': [('CARRIER', 'Carrier / cloud partner'), ('BROKER_DC', 'Data centre broker'), ('EXISTING', 'Existing customer')],
    'lost': [('NO_CAPACITY', 'No rack / power capacity'), ('LOCATION_DC', 'Facility location'), ('SLA', 'SLA not acceptable')],
    'fields': [
        f('dc_facility', 'Data centre facility', on='lop', lst=True, flt=False, sec='Facility'),
        f('dc_business', 'Customer business type', 'choice', ['Enterprise', 'Cloud / hosting provider', 'Telecom carrier', 'Financial services', 'Government',
                                                              'Content / media', 'Other'], on='lo', flt=True, sec='Facility'),
        f('dc_service', 'Service', 'choice', ['Colocation', 'Cross-connect', 'Connectivity', 'Cloud / hosting', 'Managed services', 'Migration', 'Disaster recovery'],
          on='lop', lst=True, flt=True, sec='Facility'),
        f('dc_racks', 'Rack quantity', 'number', on='lop', lst=True, rf='DCS_PROPOSAL', sec='Space and power'),
        f('dc_rack_units', 'Rack unit requirement', 'number', unit='U', on='lop', sec='Space and power'),
        f('dc_power_kw', 'Power requirement', 'number', unit='kW', on='lop', lst=True, rf='DCS_POWER', sec='Space and power'),
        f('dc_redundancy', 'Redundancy', 'choice', ['N', 'N+1', '2N', '2N+1'], on='lop', flt=True, sec='Space and power'),
        f('dc_ab_feed', 'A / B power feed', 'yesno', on='lop', sec='Space and power'),
        f('dc_cooling', 'Cooling requirement', 'choice', ['Standard air', 'Hot / cold aisle containment', 'In-row', 'Rear-door heat exchanger', 'Liquid / direct-to-chip'],
          on='lop', sec='Space and power'),
        f('dc_carrier', 'Connectivity provider', on='lop', sec='Network'),
        f('dc_bandwidth', 'Bandwidth', 'number', unit='Mbps', on='lop', sec='Network'),
        f('dc_cross_connects', 'Cross-connects', 'number', on='lop', sec='Network'),
        f('dc_ip', 'IP address requirement', on='lop', sec='Network', help='e.g. /29 IPv4, IPv6 block'),
        f('dc_migration', 'Migration timeline', on='lop', sec='Service level'),
        f('dc_availability', 'Availability requirement', 'choice', ['99.9%', '99.95%', '99.99%', '99.999%'], on='lop', flt=True, sec='Service level'),
        f('dc_sla', 'SLA requirement', 'textarea', on='op', rf='DCS_QUOTE', sec='Service level'),
        f('dc_duration', 'Contract duration', 'number', unit=MONTHS, on='lops', sec='Service level'),
        f('dc_compliance', 'Security and compliance requirements', on='lop', sec='Service level', help='e.g. ISO 27001, PCI DSS, Uptime Tier III, NESA'),
        f('dc_mrc', 'Monthly recurring charge', 'money', on='lop', sec='Commercial'),
        f('dc_nrc', 'One-time (installation) charge', 'money', on='lop', sec='Commercial'),
    ],
    'reports': [
        rp('dc_colo_pipeline', 'Colocation sales pipeline', 'stage', status='open', date='none', dash=True),
        rp('dc_rack_demand', 'Rack capacity demand', 'field:dc_facility', status='open', measure='field:dc_racks', date='none', dash=True),
        rp('dc_power_demand', 'Power capacity demand', 'field:dc_facility', status='open', measure='field:dc_power_kw', date='none'),
        rp('dc_revenue_forecast', 'Customer-wise revenue forecast', 'account', status='open', measure='field:dc_mrc', date='expected_close'),
        rp('dc_cross_connect', 'Cross-connect opportunities', 'field:dc_facility', measure='field:dc_cross_connects'),
        rp('dc_renewals', 'Contracts won by month', 'month', status='won', date='closed', measure='field:dc_mrc'),
        rp('dc_sla_pipeline', 'SLA opportunity pipeline', 'field:dc_availability', status='open', date='none'),
        rp('dc_expansion', 'Customer service expansion', 'account', status='won', date='closed', measure='field:dc_racks'),
    ],
    'score': [('Rack quantity given', 'field', 'dc_racks>=1', 15), ('Power 10 kW or more', 'field', 'dc_power_kw>=10', 15),
              ('Contract 36 months or more', 'field', 'dc_duration>=36', 10)],
}

# ================================================================== 06 Warehousing, logistics and transportation (new)
LOGISTICS = {
    'label': 'Warehousing, logistics and transportation',
    'description': 'Freight, transport, warehousing and contract logistics: route and capacity check, freight costing, rate agreements, repeat shipments.',
    'pipeline': wf('LOGS', 'Logistics services', [('ENQUIRY', 'Enquiry', 10), ('ROUTE', 'Route and service assessment', 25), ('CAPACITY', 'Capacity verification', 35),
                                                  ('COSTING', 'Freight costing', 50), ('QUOTE', 'Quotation', 65), ('NEGOTIATION', 'Negotiation', 80),
                                                  ('WON', 'Booking confirmed – operations handover', 100), ('LOST', 'Lost', 0)]),
    'sources': [('FREIGHT_PORTAL', 'Freight marketplace'), ('AGENT_NET', 'Agent network'), ('EXISTING', 'Existing customer')],
    'lost': [('RATE', 'Freight rate'), ('TRANSIT', 'Transit time'), ('NO_SPACE', 'No space / equipment')],
    'fields': [
        f('lg_origin', 'Origin', on='lops', lst=True, req=True, sec='Route'),
        f('lg_destination', 'Destination', on='lops', lst=True, req=True, sec='Route'),
        f('lg_mode', 'Transport mode', 'choice', ['Sea', 'Air', 'Road', 'Rail', 'Multimodal', 'Warehousing only'], on='lops', flt=True, sec='Route'),
        f('lg_shipment', 'Shipment type', 'choice', ['FCL', 'LCL', 'FTL', 'LTL', 'Air freight', 'Courier', 'Project cargo', 'Warehousing', 'Distribution'], on='lops', flt=True,
          sec='Cargo'),
        f('lg_cargo', 'Cargo category', 'choice', ['General', 'Dangerous goods', 'Perishable', 'Reefer', 'Oversized / heavy lift', 'High value', 'Vehicles'], on='lops',
          sec='Cargo'),
        f('lg_weight', 'Weight', 'number', unit='kg', on='lops', sec='Cargo'),
        f('lg_volume', 'Volume', 'number', unit='CBM', on='lops', sec='Cargo'),
        f('lg_container', 'Container type', 'choice', ["20' GP", "40' GP", "40' HC", 'Reefer', 'Open top', 'Flat rack', 'Not applicable'], on='lops', sec='Cargo'),
        f('lg_delivery_date', 'Required delivery date', 'date', on='lops', sec='Route'),
        f('lg_customs', 'Customs clearance needed', 'yesno', on='lops', sec='Services'),
        f('lg_wh_space', 'Warehousing capacity', 'number', unit='pallets', on='lops', sec='Services'),
        f('lg_storage_days', 'Storage duration', 'number', unit='days', on='lops', sec='Services'),
        f('lg_handling', 'Handling requirements', 'textarea', on='lop', sec='Services'),
        f('lg_temperature', 'Temperature requirement', on='lops', sec='Services', help='e.g. 2–8 °C, -18 °C'),
        f('lg_insurance', 'Insurance needed', 'yesno', on='lops', sec='Services'),
        f('lg_frequency', 'Shipment frequency', 'choice', ['One-off', 'Weekly', 'Monthly', 'Contract / regular'], on='lo', flt=True, sec='Services'),
    ],
    'reports': [
        rp('lg_pipeline', 'Shipment enquiry pipeline', 'stage', status='open', date='none', dash=True),
        rp('lg_routes', 'Route-wise opportunities (by destination)', 'field:lg_destination'),
        rp('lg_by_mode', 'Opportunities by transport mode', 'field:lg_mode'),
        rp('lg_customer_revenue', 'Customer freight revenue', 'account', status='won', date='closed'),
        rp('lg_warehousing', 'Warehousing enquiries', 'field:lg_shipment', src='lead'),
        rp('lg_quote_conversion', 'Quotation conversion', 'status', src='quotation'),
        rp('lg_repeat', 'Repeat customer analysis', 'field:lg_frequency', status='won', date='closed'),
    ],
    'score': [('Regular shipments', 'field', 'lg_frequency=Contract / regular', 20), ('Weight given', 'field', 'lg_weight', 5)],
}

# ================================================================== 07 Healthcare, hospitals and clinics
HEALTHCARE = {
    'label': 'Healthcare, hospitals and clinics',
    'description': 'Patient, corporate and insurance enquiries, packages, doctor referrals. Commercial details only – clinical records stay in the clinical system.',
    'sources': [('APPT_WEB', 'Online appointment request')],
    'lost': [('DOCTOR_AVAIL', 'Doctor not available')],
    'fields': [
        f('hc_class', 'Patient / customer classification', 'choice', ['Individual patient', 'Corporate', 'Insurance company', 'Referral partner', 'Medical tourism'],
          on='lop', lst=True, flt=True, sec='Enquiry'),
        f('hc_service', 'Healthcare service', on='lop', sec='Enquiry'),
        f('hc_department', 'Department', 'choice', ['General medicine', 'Paediatrics', 'Obstetrics & gynaecology', 'Orthopaedics', 'Cardiology', 'Dental', 'Dermatology',
                                                   'Ophthalmology', 'ENT', 'Radiology', 'Laboratory', 'Physiotherapy', 'Other'], on='lop', flt=True, sec='Enquiry'),
        f('hc_package', 'Medical package', on='lop', sec='Enquiry'),
        f('hc_corporate', 'Corporate account', on='lop', sec='Corporate and insurance'),
        f('hc_insurance', 'Insurance provider', on='lop', lst=True, sec='Corporate and insurance'),
        f('hc_referral', 'Referral source (doctor / partner)', on='lo', sec='Enquiry'),
        f('hc_appt_date', 'Appointment preference', 'date', on='lo', sec='Enquiry'),
        f('hc_doctor', 'Preferred doctor', on='lo', sec='Enquiry'),
        f('hc_channel', 'Enquiry channel', 'choice', ['Phone', 'Walk-in', 'Website', 'WhatsApp', 'Referral', 'Corporate'], on='lo', flt=True, sec='Enquiry'),
        f('hc_contract_req', 'Corporate contract requirement', 'textarea', on='op', sec='Corporate and insurance', help='Covered employees, services, rates'),
        f('hc_covered', 'Covered lives / employees', 'number', on='lop', sec='Corporate and insurance'),
    ],
    'reports': [
        rp('hc_sources', 'Patient enquiry sources', 'source', src='lead', dash=True),
        rp('hc_appt_conversion', 'Appointment conversion by classification', 'field:hc_class', src='lead'),
        rp('hc_corporate_pipeline', 'Corporate healthcare pipeline', 'stage', status='open', measure='field:hc_covered', date='none'),
        rp('hc_insurance', 'Insurance partner performance', 'field:hc_insurance'),
        rp('hc_packages', 'Medical package sales', 'field:hc_package', status='won', date='closed'),
        rp('hc_departments', 'Enquiries by department', 'field:hc_department', src='lead'),
        rp('hc_lost', 'Why enquiries are lost', 'lost_reason', status='lost', date='closed'),
    ],
}

# ================================================================== 08 Hospitality, hotels and restaurants (new)
HOSPITALITY = {
    'label': 'Hospitality, hotels and restaurants',
    'description': 'Rooms, corporate accommodation, events, banquets, weddings, catering and group bookings; travel agents, corporate rates, loyalty.',
    'pipeline': wf('HOSP', 'Bookings and events', [('ENQUIRY', 'Enquiry', 10), ('REQUIREMENT', 'Guest / event requirement', 20), ('AVAILABILITY', 'Availability check', 35),
                                                   ('PACKAGE', 'Package proposal', 50), ('QUOTE', 'Quotation', 65), ('WON', 'Confirmed – booking handover', 100),
                                                   ('LOST', 'Lost', 0)]),
    'sources': [('OTA', 'Online travel agency'), ('TRAVEL_AGENT', 'Travel agent'), ('CORP_DESK', 'Corporate travel desk'), ('EXISTING', 'Returning guest')],
    'lost': [('NO_AVAIL', 'No availability on the dates'), ('RATE_H', 'Rate')],
    'fields': [
        f('hs_property', 'Property / outlet', on='lops', lst=True, sec='Booking'),
        f('hs_type', 'Booking type', 'choice', ['Rooms', 'Corporate accommodation', 'Event / banquet', 'Wedding', 'Restaurant group', 'Catering'], on='lops', lst=True, flt=True,
          sec='Booking'),
        f('hs_room', 'Room category', 'choice', ['Standard', 'Deluxe', 'Club', 'Suite', 'Apartment', 'Villa'], on='lop', sec='Rooms'),
        f('hs_checkin', 'Check-in', 'date', on='lops', lst=True, sec='Rooms'),
        f('hs_checkout', 'Check-out', 'date', on='lops', sec='Rooms'),
        f('hs_rooms', 'Rooms', 'number', on='lops', sec='Rooms'),
        f('hs_guests', 'Guest count', 'number', on='lops', sec='Rooms'),
        f('hs_event_type', 'Event type', 'choice', ['Wedding', 'Conference', 'Meeting', 'Birthday', 'Corporate dinner', 'Gala', 'Exhibition', 'Other'], on='lops', flt=True,
          sec='Events'),
        f('hs_event_date', 'Event date', 'date', on='lops', sec='Events'),
        f('hs_capacity', 'Banquet capacity needed', 'number', unit='pax', on='lops', sec='Events'),
        f('hs_catering', 'Catering requirements', 'textarea', on='lop', sec='Events'),
        f('hs_meal', 'Meal preference', 'choice', ['Buffet', 'Set menu', 'Canapés', 'BBQ', 'Live stations', 'Vegetarian'], on='lop', sec='Events'),
        f('hs_corp_rate', 'Corporate rate (per night)', 'money', on='lop', sec='Commercial'),
        f('hs_agent', 'Travel agent', on='lo', sec='Commercial'),
        f('hs_special', 'Special requirements', 'textarea', on='lop', sec='Commercial'),
        f('hs_loyalty', 'Loyalty member', 'yesno', on='lo', sec='Commercial'),
    ],
    'reports': [
        rp('hs_room_conversion', 'Room enquiry conversion', 'field:hs_room', src='lead'),
        rp('hs_corporate_pipeline', 'Corporate booking pipeline', 'stage', status='open', date='none', dash=True),
        rp('hs_event_pipeline', 'Event and banquet pipeline', 'field:hs_event_type', status='open', measure='field:hs_capacity', date='none'),
        rp('hs_agents', 'Travel agent performance', 'field:hs_agent'),
        rp('hs_retention', 'Guest retention (repeat bookings)', 'account', status='won', date='closed'),
        rp('hs_source_revenue', 'Revenue by booking source', 'source', status='won', date='closed'),
        rp('hs_arrivals', 'Arrivals by month', 'field:hs_checkin', status='open', measure='field:hs_rooms', date='none'),
    ],
}

# ================================================================== 09 Facilities management and maintenance (service pack "fm")
FM = {
    'label': 'Facilities management and maintenance',
    'description': 'Building maintenance, AMC, HVAC / electrical contracts, cleaning, security, pest control and manpower supply; site surveys and SLA proposals.',
    'fields': [
        f('fm_building', 'Facility / building', on='lops', lst=True, sec='Site'),
        f('fm_buildings', 'Number of buildings', 'number', on='lop', sec='Site'),
        f('fm_area', 'Floor area', 'number', unit='sq ft', on='lop', sec='Site'),
        f('fm_assets', 'Asset count', 'number', on='lop', sec='Site'),
        f('fm_hvac_qty', 'HVAC equipment quantity', 'number', on='lop', sec='Site'),
        f('fm_category', 'Service category', 'choice', ['Integrated FM', 'Hard FM', 'Soft FM', 'HVAC', 'Electrical', 'Plumbing', 'Cleaning', 'Security', 'Pest control',
                                                       'Manpower supply', 'Landscaping'], on='lops', lst=True, flt=True, sec='Service'),
        f('fm_frequency', 'Maintenance frequency', 'choice', ['Daily', 'Weekly', 'Monthly', 'Quarterly', 'Half-yearly', 'Yearly', 'On call'], on='lop', sec='Service'),
        f('fm_manpower', 'Manpower requirement', 'number', unit='staff', on='lop', sec='Service'),
        f('fm_duration', 'Contract duration', 'number', unit=MONTHS, on='lops', sec='Contract'),
        f('fm_sla_hours', 'SLA response time', 'number', unit='hours', on='lop', sec='Contract'),
        f('fm_survey', 'Site visit needed', 'yesno', on='lo', flt=True, sec='Site'),
        f('fm_survey_done', 'Site survey done', 'yesno', on='o', flt=True, rf='FM_QUOTE', sec='Site'),
        f('fm_scope', 'Scope of work', 'textarea', on='lop', sec='Service'),
        f('fm_materials', 'Material requirement', 'textarea', on='lop', sec='Service'),
    ],
    'reports': [
        rp('fm_amc_pipeline', 'AMC opportunity pipeline', 'stage', status='open', date='none', dash=True),
        rp('fm_facility', 'Facility-wise enquiries', 'field:fm_building', src='lead'),
        rp('fm_category', 'Opportunities by service category', 'field:fm_category', measure='field:fm_manpower'),
        rp('fm_forecast', 'Service contract forecast', 'month', status='open', date='expected_close'),
        rp('fm_survey_pending', 'Site survey pending', 'field:fm_survey_done', status='open', date='none'),
        rp('fm_quote_conversion', 'Quotation conversion', 'status', src='quotation'),
        rp('fm_renewals', 'Contracts won by month', 'month', status='won', date='closed'),
    ],
}

# ================================================================== 10 Retail, malls and supermarkets
RETAIL = {
    'label': 'Retail, shopping malls and supermarkets',
    'description': 'Walk-in, online, corporate and bulk enquiries; store and branch, brands, loyalty, campaigns and repeat purchases.',
    'sources': [('INSTORE', 'In store'), ('ECOM', 'Online store'), ('LOYALTY_APP', 'Loyalty app')],
    'lost': [('STOCK', 'Out of stock')],
    'fields': [
        f('rt_customer_type', 'Customer type', 'choice', ['Walk-in', 'Online', 'Corporate', 'Wholesale', 'Reseller'], on='lop', lst=True, flt=True, sec='Customer'),
        f('rt_store', 'Store and branch', on='lops', lst=True, sec='Customer'),
        f('rt_category', 'Product category', on='lop', sec='Products'),
        f('rt_brand', 'Brand', on='lop', sec='Products'),
        f('rt_qty', 'Quantity', 'number', on='lops', sec='Products'),
        f('rt_preference', 'Purchase preference', 'choice', ['In store', 'Home delivery', 'Click and collect'], on='lops', sec='Products'),
        f('rt_budget', 'Budget', 'money', on='lo', sec='Products'),
        f('rt_location', 'Preferred location', on='lo', sec='Customer'),
        f('rt_loyalty', 'Loyalty member', 'yesno', on='lo', flt=True, sec='Loyalty'),
        f('rt_loyalty_no', 'Loyalty number', on='lo', sec='Loyalty'),
        f('rt_history', 'Purchase history notes', 'textarea', on='lo', sec='Loyalty'),
    ],
    'reports': [
        rp('rt_acquisition', 'Customer acquisition by source', 'source', src='lead', dash=True),
        rp('rt_branch_leads', 'Branch-wise leads', 'field:rt_store', src='lead'),
        rp('rt_product', 'Product enquiries', 'field:rt_category', src='lead'),
        rp('rt_retention', 'Customer retention (repeat buyers)', 'account', status='won', date='closed'),
        rp('rt_campaign', 'Campaign conversion', 'campaign', src='lead'),
        rp('rt_corporate', 'Corporate sales pipeline', 'stage', status='open', date='none'),
    ],
}

# ================================================================== 11 Agriculture and farming (new)
AGRICULTURE = {
    'label': 'Agriculture and farming',
    'description': 'Farmers, dealers and distributors: farm profile, crop and season, seeds, fertilizer, machinery and farm services, seasonal campaigns.',
    'pipeline': wf('AGRI', 'Farm sales', [('ENQUIRY', 'Farmer enquiry', 10), ('PROFILE', 'Farm profile', 20), ('REQUIREMENT', 'Crop and seasonal requirement', 35),
                                          ('RECOMMEND', 'Product recommendation', 50), ('QUOTE', 'Quotation', 70), ('WON', 'Order confirmed – delivery handover', 100),
                                          ('LOST', 'Lost', 0)]),
    'sources': [('DEALER_AG', 'Dealer / distributor'), ('FIELD_DAY', 'Field day / farmer meeting'), ('COOP', 'Cooperative')],
    'lost': [('SEASON', 'Missed the season'), ('CREDIT_AG', 'Credit terms')],
    'fields': [
        f('ag_location', 'Farm location', on='lops', lst=True, sec='Farm'),
        f('ag_area', 'Farm area', 'number', unit='hectares', on='lop', sec='Farm'),
        f('ag_crop', 'Crop type', on='lop', lst=True, sec='Crop'),
        f('ag_season', 'Season', 'choice', ['Winter', 'Summer', 'Spring', 'Autumn', 'Rabi', 'Kharif', 'All year'], on='lop', flt=True, sec='Crop'),
        f('ag_soil', 'Soil requirements', on='lo', sec='Farm'),
        f('ag_category', 'Product category', 'choice', ['Seeds', 'Fertilizer', 'Crop protection', 'Irrigation', 'Machinery', 'Animal feed', 'Farm services',
                                                       'Crop advisory'], on='lops', flt=True, sec='Requirement'),
        f('ag_fertilizer', 'Fertilizer requirement', on='lop', sec='Requirement'),
        f('ag_seed', 'Seed variety', on='lop', sec='Requirement'),
        f('ag_qty', 'Quantity', 'number', on='lops', sec='Requirement'),
        f('ag_irrigation', 'Irrigation', 'choice', ['Drip', 'Sprinkler', 'Centre pivot', 'Flood', 'None'], on='lop', sec='Farm'),
        f('ag_machinery', 'Machinery requirement', on='lop', sec='Requirement'),
        f('ag_delivery', 'Expected delivery date', 'date', on='lops', sec='Requirement'),
    ],
    'reports': [
        rp('ag_crop_demand', 'Crop-wise demand', 'field:ag_crop', measure='field:ag_qty', dash=True),
        rp('ag_acquisition', 'Farmer acquisition by month', 'month', src='lead'),
        rp('ag_dealer_pipeline', 'Dealer pipeline', 'stage', status='open', date='none'),
        rp('ag_seasonal', 'Seasonal enquiry forecast', 'field:ag_season', src='lead'),
        rp('ag_products', 'Product-wise opportunities', 'field:ag_category'),
        rp('ag_repeat', 'Repeat customer analysis', 'account', status='won', date='closed'),
    ],
}

# ================================================================== 12 Food processing and food manufacturing (new)
FOOD = {
    'label': 'Food processing and food manufacturing',
    'description': 'Food products, private label and OEM, distributors and retail chains: specification, samples, quality feasibility, certification, packaging.',
    'pipeline': wf('FOOD', 'Food supply and private label', [('ENQUIRY', 'Enquiry', 10), ('SPEC', 'Product specification', 20), ('SAMPLE', 'Sample request', 30),
                                                             ('FEASIBILITY', 'Technical / quality feasibility', 40), ('COSTING', 'Cost estimation', 50),
                                                             ('QUOTE', 'Quotation', 60), ('SAMPLE_OK', 'Sample approval', 80), ('WON', 'Contract – order handover', 100),
                                                             ('LOST', 'Lost', 0)]),
    'sources': [('RETAIL_CHAIN', 'Retail chain'), ('DISTRIBUTOR', 'Distributor'), ('TRADE_SHOW', 'Food trade show (Gulfood …)')],
    'lost': [('SHELF_LIFE', 'Shelf life'), ('CERT', 'Certification missing'), ('TASTE', 'Sample / taste rejected')],
    'fields': [
        f('fd_category', 'Food product category', 'choice', ['Bakery', 'Dairy', 'Beverages', 'Snacks', 'Frozen', 'Meat and poultry', 'Confectionery', 'Spices',
                                                            'Ready meals', 'Oils', 'Other'], on='lops', lst=True, flt=True, sec='Product'),
        f('fd_spec', 'Ingredients / specification', 'textarea', on='lop', sec='Product'),
        f('fd_packaging', 'Packaging type', on='lops', sec='Product'),
        f('fd_shelf_life', 'Shelf life', 'number', unit='days', on='lop', sec='Product'),
        f('fd_qty', 'Quantity', 'number', on='lops', lst=True, sec='Supply'),
        f('fd_moq', 'MOQ', 'number', on='lop', sec='Supply'),
        f('fd_storage', 'Storage requirements', 'choice', ['Ambient', 'Chilled', 'Frozen'], on='lops', sec='Supply'),
        f('fd_cert', 'Certification requirements', on='lop', sec='Quality', help='e.g. Halal, ISO 22000, HACCP, FSSC 22000, FDA'),
        f('fd_private_label', 'Private label', 'yesno', on='lop', flt=True, sec='Product'),
        f('fd_sample', 'Sample approval', 'choice', ['Not requested', 'Requested', 'Sent', 'Approved', 'Rejected'], on='lo', flt=True, rf='FOOD_SAMPLE_OK', sec='Quality'),
        f('fd_frequency', 'Delivery frequency', 'choice', ['One-off', 'Weekly', 'Fortnightly', 'Monthly'], on='lops', sec='Supply'),
        f('fd_territory', 'Distribution territory', on='lop', sec='Supply'),
    ],
    'reports': [
        rp('fd_private_label', 'Private label pipeline', 'field:fd_private_label', status='open', date='none', dash=True),
        rp('fd_distributors', 'Distributor enquiries', 'source', src='lead'),
        rp('fd_sample', 'Sample approval', 'field:fd_sample', status='open', date='none'),
        rp('fd_demand', 'Product demand', 'field:fd_category', measure='field:fd_qty'),
        rp('fd_customer_volume', 'Customer-wise volume', 'account', measure='field:fd_qty'),
        rp('fd_forecast', 'Repeat order forecast', 'month', status='open', date='expected_close'),
    ],
}

# ================================================================== 13 Marine, shipping and port operations (new)
MARINE = {
    'label': 'Marine, shipping and port operations',
    'description': 'Shipping, vessel and port services, ship repair, marine equipment, cargo handling, crew management, chartering and vessel maintenance.',
    'pipeline': wf('MARS', 'Marine services', [('ENQUIRY', 'Enquiry', 10), ('IDENTIFY', 'Vessel / service identification', 20), ('TECH', 'Technical assessment', 35),
                                               ('AVAIL', 'Port and resource availability', 45), ('COSTING', 'Cost estimation', 55), ('PROPOSAL', 'Proposal', 65),
                                               ('NEGOTIATION', 'Negotiation', 80), ('WON', 'Service confirmed – operations handover', 100), ('LOST', 'Lost', 0)]),
    'sources': [('SHIP_AGENT', 'Shipping agent'), ('OWNER_MGR', 'Ship owner / manager'), ('PORT_AUTH', 'Port authority')],
    'lost': [('VESSEL_LEFT', 'Vessel sailed / schedule'), ('NO_BERTH', 'No berth / yard space')],
    'fields': [
        f('mr_vessel', 'Vessel name', on='lops', lst=True, sec='Vessel'),
        f('mr_vessel_type', 'Vessel type', 'choice', ['Tanker', 'Bulk carrier', 'Container', 'Offshore support', 'Tug', 'Barge', 'Passenger', 'Dhow', 'Yacht', 'Other'],
          on='lop', flt=True, sec='Vessel'),
        f('mr_imo', 'IMO number', on='lops', lst=True, sec='Vessel'),
        f('mr_size', 'Vessel size', 'number', unit='DWT', on='lop', sec='Vessel'),
        f('mr_port', 'Port location', on='lops', lst=True, sec='Port call'),
        f('mr_port_call', 'Port call date', 'date', on='lops', sec='Port call'),
        f('mr_service', 'Service requirement', 'choice', ['Ship repair', 'Spares / equipment supply', 'Port services', 'Cargo handling', 'Crew management', 'Chartering',
                                                         'Vessel maintenance contract', 'Survey / inspection', 'Marine insurance referral'],
          on='lops', flt=True, sec='Service'),
        f('mr_cargo', 'Cargo category', on='lop', sec='Service'),
        f('mr_repair', 'Repair requirement', 'textarea', on='lop', sec='Service'),
        f('mr_duration', 'Estimated service duration', 'number', unit='days', on='lop', sec='Service'),
        f('mr_spec', 'Technical specification', 'textarea', on='lop', sec='Service'),
        f('mr_charter_days', 'Chartering period', 'number', unit='days', on='lop', sec='Service'),
    ],
    'reports': [
        rp('mr_pipeline', 'Vessel enquiry pipeline', 'stage', status='open', date='none', dash=True),
        rp('mr_ports', 'Port-wise opportunities', 'field:mr_port'),
        rp('mr_repair_quotes', 'Ship repair quotations', 'field:mr_service', src='quotation'),
        rp('mr_contracts', 'Marine service contracts', 'field:mr_service', status='won', date='closed'),
        rp('mr_forecast', 'Customer revenue forecast', 'account', status='open', date='expected_close'),
    ],
}

# ================================================================== 14 Aviation and airport operations (new)
AVIATION = {
    'label': 'Aviation and airport operations',
    'description': 'Aircraft maintenance, ground handling, airport service contracts, aircraft parts, charter and training; regulatory capability checks.',
    'pipeline': wf('AVN', 'Aviation services', [('ENQUIRY', 'Enquiry', 10), ('IDENTIFY', 'Aircraft / service identification', 20), ('TECH', 'Technical review', 35),
                                                ('CERT', 'Certification and capability check', 45), ('COSTING', 'Cost estimation', 55), ('PROPOSAL', 'Proposal', 65),
                                                ('NEGOTIATION', 'Negotiation', 80), ('WON', 'Contract – operations handover', 100), ('LOST', 'Lost', 0)]),
    'sources': [('AIRLINE', 'Airline / operator'), ('MRO_REF', 'MRO partner'), ('AIRPORT', 'Airport authority')],
    'lost': [('NO_APPROVAL', 'Approval / certification missing'), ('AOG_TIME', 'Turnaround time')],
    'fields': [
        f('av_category', 'Service category', 'choice', ['Aircraft maintenance', 'Ground handling', 'Airport services', 'Aircraft parts', 'Charter', 'Aviation training'],
          on='lops', lst=True, flt=True, sec='Aircraft'),
        f('av_operator', 'Airline / operator', on='lops', lst=True, sec='Aircraft'),
        f('av_model', 'Aircraft model', on='lops', sec='Aircraft'),
        f('av_registration', 'Registration number', on='lops', sec='Aircraft'),
        f('av_airport', 'Airport', on='lops', sec='Aircraft'),
        f('av_maint_type', 'Maintenance type', 'choice', ['Line', 'A-check', 'C-check', 'Heavy', 'Component', 'AOG support', 'Not applicable'], on='lop', flt=True,
          sec='Service'),
        f('av_scope', 'Service scope', 'textarea', on='lop', sec='Service'),
        f('av_parts', 'Aircraft parts requirement', 'textarea', on='lop', sec='Service'),
        f('av_spec', 'Technical specification', 'textarea', on='lop', sec='Service'),
        f('av_cert', 'Regulatory certification requirement', on='lop', rf='AVN_COSTING', sec='Compliance', help='e.g. GCAA Part-145, EASA, FAA'),
        f('av_ground', 'Ground handling requirements', 'textarea', on='lop', sec='Service'),
        f('av_schedule', 'Service schedule', on='lops', sec='Service'),
    ],
    'reports': [
        rp('av_airlines', 'Airline pipeline', 'field:av_operator', status='open', date='none', dash=True),
        rp('av_maintenance', 'Aircraft maintenance opportunities', 'field:av_maint_type'),
        rp('av_ground', 'Ground handling contracts', 'field:av_category', status='won', date='closed'),
        rp('av_forecast', 'Aviation service forecast', 'month', status='open', date='expected_close'),
        rp('av_renewals', 'Customer contracts won by month', 'month', status='won', date='closed'),
    ],
}

# ================================================================== 15 Education, schools and universities
EDUCATION = {
    'label': 'Education, schools and universities',
    'description': 'Student and admission enquiries, courses and programmes, parents, agents, corporate training, applications and intakes.',
    'sources': [('OPEN_DAY', 'Open day'), ('CORP_TRAINING', 'Corporate training request')],
    'lost': [('ELIGIBILITY', 'Not eligible'), ('OTHER_SCHOOL', 'Chose another school / university')],
    'fields': [
        f('ed_student', 'Student name', on='lops', lst=True, sec='Student'),
        f('ed_parent', 'Parent / guardian', on='lops', sec='Student'),
        f('ed_course', 'Course / programme', on='lops', lst=True, sec='Programme'),
        f('ed_year', 'Academic year', on='lops', sec='Programme'),
        f('ed_grade', 'Grade or qualification', on='lops', sec='Programme'),
        f('ed_campus', 'Campus', on='lops', flt=False, sec='Programme'),
        f('ed_country', 'Country of origin', on='lo', sec='Student'),
        f('ed_intake', 'Admission intake', 'choice', ['September', 'January', 'April', 'Rolling'], on='lops', flt=True, sec='Programme'),
        f('ed_status', 'Application status', 'choice', ['Not started', 'Submitted', 'Documents pending', 'Under review', 'Offer made', 'Accepted', 'Rejected'],
          on='o', lst=True, flt=True, sec='Application'),
        f('ed_documents', 'Documents collected', 'yesno', on='o', rf='ADMISSION_OFFER', sec='Application'),
        f('ed_agent', 'Education agent', on='lo', sec='Application'),
        f('ed_scholarship', 'Scholarship enquiry', 'yesno', on='lo', flt=True, sec='Fees'),
        f('ed_fee_category', 'Fee category', 'choice', ['Standard', 'Sibling discount', 'Staff', 'Scholarship', 'Corporate sponsored'], on='lops', sec='Fees'),
    ],
    'reports': [
        rp('ed_courses', 'Course-wise enquiries', 'field:ed_course', src='lead', dash=True),
        rp('ed_conversion', 'Admission conversion', 'stage'),
        rp('ed_campus', 'Campus performance', 'field:ed_campus'),
        rp('ed_agents', 'Agent performance', 'field:ed_agent'),
        rp('ed_campaigns', 'Student recruitment campaigns', 'campaign', src='lead'),
        rp('ed_intake', 'Intake forecasting', 'field:ed_intake', status='open', date='none'),
        rp('ed_applications', 'Applications by status', 'field:ed_status', status='open', date='none'),
    ],
}

# ================================================================== 16 Telecommunications and network infrastructure (service pack "telecom")
TELECOM = {
    'label': 'Telecommunications and network infrastructure',
    'description': 'Enterprise connectivity, internet and bandwidth, fibre, towers, managed networks and equipment; coverage and feasibility checks, SLA renewals.',
    'fields': [
        f('tc_service', 'Service type', 'choice', ['Internet / bandwidth', 'Enterprise connectivity', 'Network infrastructure project', 'Tower leasing', 'Fibre installation',
                                                  'Managed network services', 'Equipment supply'], on='lops', lst=True, flt=True, sec='Service'),
        f('tc_site', 'Customer site', on='lops', lst=True, sec='Site'),
        f('tc_bandwidth', 'Bandwidth requirement', 'number', unit='Mbps', on='lops', sec='Service'),
        f('tc_network', 'Network type', 'choice', ['Fibre', 'Microwave', 'Satellite', '4G / 5G', 'MPLS', 'SD-WAN', 'Leased line'], on='lops', flt=True, sec='Service'),
        f('tc_route', 'Fibre route', on='lop', sec='Site'),
        f('tc_tower', 'Tower requirement', 'yesno', on='lop', sec='Site'),
        f('tc_coverage', 'Coverage location', on='lop', sec='Site'),
        f('tc_provider', 'Connectivity provider', on='lo', sec='Service'),
        f('tc_redundancy', 'Redundancy needed', 'yesno', on='lop', sec='Service'),
        f('tc_availability', 'Service availability', 'choice', ['99.5%', '99.9%', '99.95%', '99.99%'], on='lop', sec='Service'),
        f('tc_feasible', 'Coverage / feasibility', 'choice', ['Pending', 'Feasible', 'Feasible with build', 'Not feasible'], on='o', flt=True, rf='TEL_QUOTE', sec='Site'),
        f('tc_install', 'Installation timeline', on='lops', sec='Service'),
        f('tc_sla', 'SLA requirement', 'textarea', on='op', sec='Service'),
        f('tc_mrc', 'Monthly recurring charge', 'money', on='lop', sec='Commercial'),
    ],
    'reports': [
        rp('tc_pipeline', 'Connectivity pipeline', 'stage', status='open', date='none', dash=True),
        rp('tc_fibre', 'Fibre opportunities', 'field:tc_network', measure='field:tc_bandwidth'),
        rp('tc_forecast', 'Enterprise customer forecast', 'month', status='open', measure='field:tc_mrc', date='expected_close'),
        rp('tc_towers', 'Tower leasing pipeline', 'field:tc_service', status='open', date='none'),
        rp('tc_renewals', 'Contracts won by month', 'month', status='won', date='closed', measure='field:tc_mrc'),
        rp('tc_upsell', 'Network service upselling', 'account', status='won', date='closed'),
    ],
}

# ================================================================== 17 Electrical, power and utilities (new)
POWER = {
    'label': 'Electrical, power and utilities',
    'description': 'Electrical, substation, transformer, power equipment, solar and renewable projects; engineering feasibility, EPC tenders, maintenance contracts.',
    'pipeline': wf('PWR', 'Power and electrical projects', [('ENQUIRY', 'Enquiry', 10), ('TECH', 'Technical assessment', 20), ('SURVEY', 'Site survey', 30),
                                                            ('FEASIBILITY', 'Engineering feasibility', 40), ('COSTING', 'Cost estimation', 50), ('PROPOSAL', 'Proposal', 60),
                                                            ('TENDER', 'Tender submission', 70), ('NEGOTIATION', 'Negotiation', 80), ('WON', 'Contract – handover', 100),
                                                            ('LOST', 'Lost', 0)]),
    'sources': [('UTILITY', 'Utility (DEWA / ADDC / SEWA …)'), ('EPC', 'EPC contractor'), ('CONSULTANT_PWR', 'Electrical consultant')],
    'lost': [('AUTHORITY', 'Authority approval'), ('TECH_SPEC', 'Technical specification')],
    'fields': [
        f('pw_category', 'Project category', 'choice', ['Substation', 'Transformer supply', 'Switchgear / panels', 'Cabling', 'Solar', 'Other renewable', 'Power equipment',
                                                       'Electrical maintenance', 'Energy consulting', 'Utility service'], on='lops', lst=True, flt=True, sec='Project'),
        f('pw_location', 'Project location', on='lops', sec='Project'),
        f('pw_voltage', 'Voltage level', 'choice', ['LV (400 V)', '11 kV', '22 kV', '33 kV', '132 kV', '220 kV', '400 kV'], on='lops', flt=True, sec='Technical'),
        f('pw_load_kw', 'Load requirement', 'number', unit='kW', on='lop', sec='Technical'),
        f('pw_kva', 'Transformer capacity', 'number', unit='kVA', on='lop', sec='Technical'),
        f('pw_solar_kwp', 'Solar capacity', 'number', unit='kWp', on='lop', sec='Technical'),
        f('pw_equipment', 'Equipment specifications', 'textarea', on='lop', sec='Technical'),
        f('pw_grid', 'Grid connection needed', 'yesno', on='lop', sec='Technical'),
        f('pw_installation', 'Installation requirement', 'textarea', on='lop', sec='Technical'),
        f('pw_cert', 'Technical certification', on='lop', sec='Technical', help='e.g. authority approval, IEC type test'),
        f('pw_feasibility', 'Technical feasibility status', 'choice', ['Pending', 'In progress', 'Feasible', 'Not feasible'], on='o', flt=True, rf='PWR_COSTING',
          sec='Technical'),
        f('pw_timeline', 'Project timeline', 'number', unit=MONTHS, on='lops', sec='Project'),
        f('pw_value', 'Estimated project value', 'money', on='lo', sec='Project'),
    ],
    'reports': [
        rp('pw_pipeline', 'Electrical project pipeline', 'stage', status='open', measure='field:pw_value', date='none', dash=True),
        rp('pw_solar', 'Solar project opportunities', 'field:pw_category', measure='field:pw_solar_kwp'),
        rp('pw_equipment', 'Equipment enquiries', 'field:pw_category', src='lead'),
        rp('pw_win_loss', 'Tender win / loss', 'status', date='closed'),
        rp('pw_feasibility', 'Technical feasibility status', 'field:pw_feasibility', status='open', date='none'),
        rp('pw_forecast', 'Contract forecast', 'month', status='open', measure='field:pw_value', date='expected_close'),
    ],
}

# ================================================================== 18 Waste management and recycling (new)
WASTE = {
    'label': 'Waste management and recycling',
    'description': 'Waste collection, disposal, recycling, hazardous and industrial waste, transport and environmental contracts; recurring collections.',
    'pipeline': wf('WST', 'Waste services', [('ENQUIRY', 'Enquiry', 10), ('CLASSIFY', 'Waste classification', 25), ('SITE', 'Site assessment', 35),
                                             ('FEASIBILITY', 'Collection / disposal feasibility', 45), ('PRICING', 'Pricing', 60), ('PROPOSAL', 'Proposal', 75),
                                             ('WON', 'Contract – operations handover', 100), ('LOST', 'Lost', 0)]),
    'sources': [('MUNICIPALITY', 'Municipality referral'), ('ENV_CONSULT', 'Environmental consultant')],
    'lost': [('PERMIT', 'Permit / licence'), ('LANDFILL', 'Disposal route not available')],
    'fields': [
        f('ws_category', 'Waste category', 'choice', ['General', 'Recyclable', 'Hazardous', 'Medical', 'Construction and demolition', 'Organic / food', 'E-waste', 'Liquid / oily'],
          on='lops', lst=True, flt=True, sec='Waste'),
        f('ws_qty', 'Waste quantity', 'number', unit='tonnes / month', on='lops', lst=True, sec='Waste'),
        f('ws_frequency', 'Collection frequency', 'choice', ['Daily', 'Weekly', 'Bi-weekly', 'Monthly', 'On call'], on='lops', sec='Collection'),
        f('ws_location', 'Collection location', on='lops', sec='Collection'),
        f('ws_disposal', 'Disposal requirements', 'textarea', on='lop', sec='Disposal'),
        f('ws_hazard', 'Hazardous classification', on='lop', rf='WST_PRICING', sec='Waste', help='Needed from pricing for hazardous waste'),
        f('ws_recycling', 'Recycling requirements', 'textarea', on='lop', sec='Disposal'),
        f('ws_transport', 'Transportation requirements', 'textarea', on='lop', sec='Collection'),
        f('ws_certificate', 'Disposal certificate needed', 'yesno', on='lops', sec='Disposal'),
        f('ws_duration', 'Contract duration', 'number', unit=MONTHS, on='lops', sec='Collection'),
    ],
    'reports': [
        rp('ws_pipeline', 'Waste collection pipeline', 'stage', status='open', date='none', dash=True),
        rp('ws_industrial', 'Industrial customer enquiries', 'field:ws_category', src='lead'),
        rp('ws_demand', 'Waste category demand', 'field:ws_category', measure='field:ws_qty'),
        rp('ws_forecast', 'Contract revenue forecast', 'month', status='open', date='expected_close'),
        rp('ws_renewals', 'Recurring service contracts won', 'month', status='won', date='closed'),
    ],
}

# ================================================================== 19 Cleaning, janitorial and pest control (service packs "cleaning" and "pest")
CLEANING = {
    'label': 'Cleaning and janitorial',
    'description': 'Commercial, residential and deep cleaning, sanitisation, manpower; site survey, area assessment, frequency quotation, recurring contracts.',
    'fields': [
        f('cl_site', 'Customer site', on='lops', lst=True, sec='Site'),
        f('cl_property', 'Property type', 'choice', ['Office', 'Villa', 'Apartment', 'Retail', 'Warehouse', 'Hospital / clinic', 'School', 'Hotel', 'Restaurant', 'Other'],
          on='lops', flt=True, sec='Site'),
        f('cl_area', 'Area', 'number', unit='sq ft', on='lops', lst=True, sec='Site'),
        f('cl_floors', 'Number of floors', 'number', on='lop', sec='Site'),
        f('cl_service', 'Service type', 'choice', ['Regular cleaning', 'Deep cleaning', 'Sanitisation', 'Facade / window', 'Carpet and upholstery', 'Kitchen',
                                                  'Post-construction', 'Manpower supply'], on='lops', flt=True, sec='Service'),
        f('cl_frequency', 'Cleaning frequency', 'choice', ['One-off', 'Daily', 'Weekly', 'Monthly', 'Quarterly'], on='lops', sec='Service'),
        f('cl_manpower', 'Manpower requirement', 'number', unit='cleaners', on='lop', sec='Service'),
        f('cl_chemicals', 'Chemical / material requirements', 'textarea', on='lop', sec='Service'),
        f('cl_survey_done', 'Site survey done', 'yesno', on='o', flt=True, rf='CLN_QUOTE', sec='Site'),
        f('cl_duration', 'Contract duration', 'number', unit=MONTHS, on='lops', sec='Contract'),
        f('cl_sla', 'SLA requirements', 'textarea', on='op', sec='Contract'),
    ],
    'reports': [
        rp('cl_survey_pipeline', 'Site survey pipeline', 'field:cl_survey_done', status='open', date='none', dash=True),
        rp('cl_contracts', 'Cleaning contract opportunities', 'field:cl_service', measure='field:cl_area'),
        rp('cl_renewals', 'Contracts won by month', 'month', status='won', date='closed'),
        rp('cl_retention', 'Customer retention', 'account', status='won', date='closed'),
        rp('cl_quote_conversion', 'Service quotation conversion', 'status', src='quotation'),
    ],
}
PEST = {
    'label': 'Pest control',
    'description': 'Pest control enquiries and contracts: property, pest category, treatment, frequency, chemicals and AMC renewals.',
    'fields': [
        f('pc_site', 'Customer site', on='lops', lst=True, sec='Site'),
        f('pc_property', 'Property type', 'choice', ['Office', 'Villa', 'Apartment', 'Restaurant / kitchen', 'Warehouse', 'Hospital / clinic', 'School', 'Hotel', 'Other'],
          on='lops', flt=True, sec='Site'),
        f('pc_area', 'Area', 'number', unit='sq ft', on='lops', sec='Site'),
        f('pc_pest', 'Pest category', 'choice', ['Cockroaches', 'Rodents', 'Termites', 'Bed bugs', 'Mosquitoes / flies', 'Birds', 'Snakes / reptiles', 'General'],
          on='lops', lst=True, flt=True, sec='Treatment'),
        f('pc_treatment', 'Treatment requirement', 'choice', ['Spray', 'Gel', 'Fumigation', 'Baiting', 'Termite barrier', 'Bird netting / spikes'], on='lops', sec='Treatment'),
        f('pc_frequency', 'Service frequency', 'choice', ['One-off', 'Monthly', 'Bi-monthly', 'Quarterly'], on='lops', sec='Treatment'),
        f('pc_chemicals', 'Chemical treatment requirements', 'textarea', on='lop', sec='Treatment', help='Municipality-approved chemicals, food areas'),
        f('pc_duration', 'Contract duration', 'number', unit=MONTHS, on='lops', sec='Contract'),
        f('pc_sla', 'SLA requirements', 'textarea', on='op', sec='Contract'),
    ],
    'reports': [
        rp('pc_enquiries', 'Pest control enquiries', 'field:pc_pest', src='lead', dash=True),
        rp('pc_pipeline', 'Pest control pipeline', 'stage', status='open', date='none'),
        rp('pc_renewals', 'Contracts won by month', 'month', status='won', date='closed'),
        rp('pc_retention', 'Customer retention', 'account', status='won', date='closed'),
    ],
}

# ================================================================== 20 Laboratories and pharmaceuticals (new)
LAB = {
    'label': 'Laboratories and pharmaceuticals',
    'description': 'Testing and analysis, contract research, clinical research business, laboratory equipment, testing packages, pharma products, contract manufacturing.',
    'pipeline': wf('LAB', 'Laboratory and pharma services', [('ENQUIRY', 'Enquiry', 10), ('REQUIREMENT', 'Testing / service requirement', 20),
                                                             ('FEASIBILITY', 'Technical feasibility', 35), ('SAMPLE', 'Sample requirement', 45), ('PRICING', 'Pricing', 55),
                                                             ('PROPOSAL', 'Proposal', 70), ('WON', 'Service confirmed – laboratory handover', 100), ('LOST', 'Lost', 0)]),
    'sources': [('REGULATOR', 'Regulator / authority'), ('RESEARCH', 'University / research partner')],
    'lost': [('ACCREDITATION', 'Accreditation not covered'), ('TAT', 'Turnaround time')],
    'fields': [
        f('lb_service', 'Laboratory service', 'choice', ['Testing and analysis', 'Calibration', 'Contract research', 'Clinical research', 'Laboratory equipment',
                                                        'Testing package', 'Pharmaceutical products', 'Contract manufacturing'], on='lops', lst=True, flt=True, sec='Service'),
        f('lb_category', 'Testing category', on='lop', sec='Testing'),
        f('lb_sample_type', 'Sample type', on='lop', sec='Testing'),
        f('lb_method', 'Testing method', on='lop', sec='Testing'),
        f('lb_standard', 'Required standard', on='lop', sec='Testing', help='e.g. ISO, ASTM, USP, BP'),
        f('lb_accreditation', 'Accreditation requirement', on='lop', rf='LAB_PRICING', sec='Testing', help='e.g. ISO/IEC 17025, GLP, GMP'),
        f('lb_sample_qty', 'Sample quantity', 'number', on='lops', lst=True, sec='Testing'),
        f('lb_tat', 'Turnaround time', 'number', unit='days', on='lops', sec='Testing'),
        f('lb_research', 'Research requirement', 'textarea', on='lop', sec='Research and products'),
        f('lb_product_spec', 'Product specification', 'textarea', on='lop', sec='Research and products'),
        f('lb_regulatory', 'Regulatory documentation', 'textarea', on='op', sec='Research and products'),
        f('lb_duration', 'Contract duration', 'number', unit=MONTHS, on='lops', sec='Research and products'),
    ],
    'reports': [
        rp('lb_pipeline', 'Testing service pipeline', 'stage', status='open', date='none', dash=True),
        rp('lb_enquiries', 'Laboratory customer enquiries', 'source', src='lead'),
        rp('lb_equipment', 'Equipment sales', 'field:lb_service', status='won', date='closed'),
        rp('lb_research', 'Research and service opportunities', 'field:lb_service', measure='field:lb_sample_qty'),
        rp('lb_cmo', 'Contract manufacturing pipeline by stage', 'stage', status='open', date='none'),
        rp('lb_retention', 'Customer retention', 'account', status='won', date='closed'),
    ],
}

# ================================================================== 21 Marine construction and shipyards (new)
SHIPYARD = {
    'label': 'Marine construction and shipyards',
    'description': 'Shipyard projects, vessel repair, shipbuilding, dry dock, marine fabrication, welding and offshore construction; drawings, BOQ and tenders.',
    'pipeline': wf('SHY', 'Shipyard projects', [('ENQUIRY', 'Enquiry', 10), ('REGISTER', 'Vessel / project registration', 20), ('TECH', 'Technical assessment', 30),
                                                ('INSPECTION', 'Site / vessel inspection', 40), ('BOQ', 'BOQ and estimation', 55), ('PROPOSAL', 'Proposal', 65),
                                                ('NEGOTIATION', 'Negotiation', 80), ('WON', 'Contract award – project handover', 100), ('LOST', 'Lost', 0)]),
    'sources': [('CLASS_SOC', 'Classification society'), ('OWNER_MGR', 'Ship owner / manager'), ('OFFSHORE', 'Offshore contractor')],
    'lost': [('DOCK_SLOT', 'No dock slot'), ('STEEL_PRICE', 'Steel price')],
    'fields': [
        f('sy_type', 'Project type', 'choice', ['Ship repair', 'Shipbuilding', 'Dry dock', 'Marine fabrication', 'Welding and engineering', 'Marine equipment supply',
                                               'Offshore construction'], on='lops', lst=True, flt=True, sec='Project'),
        f('sy_vessel', 'Vessel identification', on='lops', lst=True, sec='Vessel'),
        f('sy_vessel_type', 'Vessel type', on='lop', sec='Vessel'),
        f('sy_location', 'Shipyard location', on='lops', sec='Project'),
        f('sy_project_ref', 'Project reference', on='lop', sec='Project'),
        f('sy_scope', 'Repair scope', 'textarea', on='lop', sec='Scope'),
        f('sy_drydock', 'Dry dock needed', 'yesno', on='lops', flt=True, sec='Scope'),
        f('sy_fabrication', 'Fabrication requirement', 'textarea', on='lop', sec='Scope'),
        f('sy_steel', 'Steel quantity', 'number', unit='MT', on='lop', rf='SHY_PROPOSAL', sec='Scope'),
        f('sy_drawings', 'Engineering drawings', on='lop', sec='Scope'),
        f('sy_welding', 'Welding specification', on='lop', sec='Scope'),
        f('sy_duration', 'Project duration', 'number', unit='days', on='lops', sec='Project'),
        f('sy_cert', 'Technical certification requirements', on='lop', sec='Project', help='Class society: ABS, DNV, LR, BV …'),
    ],
    'reports': [
        rp('sy_repair_pipeline', 'Ship repair pipeline', 'field:sy_type', status='open', date='none', dash=True),
        rp('sy_shipbuilding', 'Shipbuilding opportunities', 'stage', status='open', measure='field:sy_steel', date='none'),
        rp('sy_drydock', 'Dry dock enquiries', 'field:sy_drydock', src='lead'),
        rp('sy_forecast', 'Marine project forecast', 'month', status='open', date='expected_close'),
        rp('sy_win_loss', 'Tender win / loss', 'status', date='closed'),
        rp('sy_customers', 'Customer contract performance', 'account', status='won', date='closed'),
    ],
}

# ================================================================== 22 Real estate and property brokerage (pack "property_management")
REAL_ESTATE = {
    'label': 'Real estate and property brokerage',
    'description': 'Buyer, seller, tenant and landlord leads, property matching, viewings, offers, reservations and commissions.',
    'fields': [
        f('re_party', 'Lead type', 'choice', ['Buyer', 'Seller', 'Tenant', 'Landlord', 'Investor'], on='lo', lst=True, flt=True, sec='Requirement'),
        f('re_type', 'Property type', 'choice', ['Apartment', 'Villa', 'Townhouse', 'Penthouse', 'Office', 'Retail', 'Warehouse', 'Land', 'Building'], on='lop', flt=True,
          sec='Requirement'),
        f('re_builtup', 'Built-up area', 'number', unit='sq ft', on='lop', sec='Requirement'),
        f('re_furnished', 'Furnished', 'choice', ['Furnished', 'Unfurnished', 'Semi-furnished'], on='lop', sec='Requirement'),
        f('re_ready', 'Ready / off-plan', 'choice', ['Ready', 'Off-plan'], on='lop', flt=True, sec='Requirement'),
        f('re_payment_plan', 'Payment plan', on='lop', sec='Commercial'),
        f('re_move_in', 'Preferred move-in date', 'date', on='lo', sec='Requirement'),
        f('re_owner', 'Property owner', on='o', sec='Listing'),
        f('re_listing_ref', 'Listing reference', on='op', sec='Listing'),
        f('re_finance', 'Mortgage / finance needed', 'yesno', on='lo', sec='Commercial'),
    ],
    'reports': [
        rp('re_agent_pipeline', 'Agent-wise pipeline', 'agent', status='open', date='none', dash=True),
        rp('re_viewing_conversion', 'Viewing conversion by stage', 'stage'),
        rp('re_buyer_seller', 'Buyer and seller enquiries', 'field:re_party', src='lead'),
        rp('re_listing', 'Opportunities by property type', 'field:re_type'),
        rp('re_rent_sale', 'Rental and sales pipeline', 'pipeline', status='open', date='none'),
        rp('re_commission', 'Pipeline value by expected close', 'month', status='open', date='expected_close'),
    ],
}

# ================================================================== 23 Advertising, marketing and signage (new)
ADVERTISING = {
    'label': 'Advertising, marketing and signage',
    'description': 'Campaigns, branding and design, outdoor advertising, billboards and signage: creative brief, media availability, artwork approval, installation.',
    'pipeline': wf('ADV', 'Campaigns and signage', [('ENQUIRY', 'Enquiry', 10), ('BRIEF', 'Creative brief', 20), ('AVAIL', 'Site / media availability', 30),
                                                    ('DESIGN', 'Design proposal', 45), ('COSTING', 'Costing', 55), ('QUOTE', 'Quotation', 65),
                                                    ('ARTWORK', 'Artwork approval', 85), ('WON', 'Contract – production / installation', 100), ('LOST', 'Lost', 0)]),
    'sources': [('MEDIA_AGENCY', 'Media agency'), ('BRAND_DIRECT', 'Brand direct')],
    'lost': [('SITE_TAKEN', 'Site / media taken'), ('PERMIT_ADV', 'Municipality permit')],
    'fields': [
        f('ad_campaign', 'Campaign name', on='lops', lst=True, sec='Campaign'),
        f('ad_medium', 'Advertising medium', 'choice', ['Billboard', 'Unipole', 'Mega com', 'Digital screen', 'Mall media', 'Transit', 'Signage', 'Vehicle branding', 'Print',
                                                       'Online / social', 'Branding and design'], on='lops', lst=True, flt=True, sec='Campaign'),
        f('ad_location', 'Billboard / location', on='lops', sec='Media'),
        f('ad_dimensions', 'Dimensions', on='lops', sec='Media'),
        f('ad_weeks', 'Advertising duration', 'number', unit='weeks', on='lops', sec='Media'),
        f('ad_start', 'Campaign start date', 'date', on='lops', sec='Campaign'),
        f('ad_end', 'Campaign end date', 'date', on='lops', sec='Campaign'),
        f('ad_artwork', 'Artwork requirement', 'choice', ['Client provides', 'We design', 'Adapt existing'], on='lop', sec='Production'),
        f('ad_material', 'Production material', on='lop', sec='Production'),
        f('ad_install_location', 'Installation location', on='lops', sec='Production'),
        f('ad_permit', 'Municipality approval needed', 'yesno', on='lop', sec='Production'),
        f('ad_budget', 'Media budget', 'money', on='lo', sec='Campaign'),
        f('ad_approval', 'Client approval status', 'choice', ['Pending', 'Changes requested', 'Approved'], on='o', flt=True, rf='ADV_ARTWORK', sec='Production'),
    ],
    'reports': [
        rp('ad_pipeline', 'Campaign pipeline', 'stage', status='open', date='none', dash=True),
        rp('ad_billboards', 'Billboard space enquiries', 'field:ad_medium', src='lead'),
        rp('ad_forecast', 'Advertising revenue forecast', 'month', status='open', date='expected_close'),
        rp('ad_medium_value', 'Value by advertising medium', 'field:ad_medium', status='won', date='closed'),
        rp('ad_clients', 'Client-wise campaigns', 'account'),
        rp('ad_starts', 'Campaigns starting by month', 'field:ad_start', status='won', date='none'),
    ],
}

# ================================================================== 24 Professional services and consultancy (new)
PROFESSIONAL = {
    'label': 'Professional services and consultancy',
    'description': 'Management, engineering, legal, accounting and advisory services: discovery, scope of work, resource estimation, proposals, retainers.',
    'pipeline': wf('PRO', 'Consulting engagements', [('ENQUIRY', 'Enquiry', 10), ('DISCOVERY', 'Discovery meeting', 20), ('REQUIREMENT', 'Business requirement', 30),
                                                     ('SCOPE', 'Scope of work', 40), ('ESTIMATE', 'Resource estimation', 50), ('PROPOSAL', 'Proposal', 65),
                                                     ('NEGOTIATION', 'Negotiation', 80), ('WON', 'Engagement agreement – project handover', 100), ('LOST', 'Lost', 0)]),
    'sources': [('REFERRAL_PRO', 'Client / partner referral'), ('SEMINAR', 'Seminar / webinar')],
    'lost': [('FEES', 'Professional fees'), ('INHOUSE_PRO', 'Done in-house')],
    'fields': [
        f('ps_category', 'Consulting service category', 'choice', ['Management consulting', 'Engineering consulting', 'Legal', 'Accounting and audit', 'Tax and VAT',
                                                                  'Business advisory', 'IT advisory', 'HR advisory'], on='lops', lst=True, flt=True, sec='Engagement'),
        f('ps_sector', 'Client business sector', on='lop', sec='Engagement'),
        f('ps_scope', 'Project scope', 'textarea', on='lop', rf='PRO_ESTIMATE', sec='Scope'),
        f('ps_expertise', 'Required expertise', on='lop', sec='Scope'),
        f('ps_consultants', 'Consultants needed', 'number', on='lop', sec='Resources'),
        f('ps_days', 'Estimated consulting days', 'number', unit='days', on='lop', lst=True, rf='PRO_PROPOSAL', sec='Resources'),
        f('ps_deliverables', 'Deliverables', 'textarea', on='lop', sec='Scope'),
        f('ps_duration', 'Engagement duration', 'number', unit=MONTHS, on='lops', sec='Engagement'),
        f('ps_retainer', 'Retainer', 'yesno', on='lop', flt=True, sec='Commercial'),
        f('ps_retainer_value', 'Retainer value (per month)', 'money', on='lop', sec='Commercial'),
        f('ps_completion', 'Expected completion date', 'date', on='lops', sec='Engagement'),
    ],
    'reports': [
        rp('ps_pipeline', 'Consulting pipeline', 'stage', status='open', measure='field:ps_days', date='none', dash=True),
        rp('ps_services', 'Service-wise opportunities', 'field:ps_category'),
        rp('ps_proposal_conversion', 'Proposal conversion', 'status', src='quotation'),
        rp('ps_utilization', 'Consulting days in the pipeline by service', 'field:ps_category', status='open', measure='field:ps_days', date='none'),
        rp('ps_retainers', 'Retainers won', 'field:ps_retainer', status='won', date='closed', measure='field:ps_retainer_value'),
        rp('ps_forecast', 'Client revenue forecast', 'account', status='open', date='expected_close'),
    ],
}


# pack code → content. Existing pack codes keep their pipeline; the new ones get the workflow above.
INDUSTRY_CONTENT = {
    'it': IT, 'construction': CONSTRUCTION, 'manufacturing': MANUFACTURING, 'oil_gas': OIL_GAS, 'data_center': DATA_CENTER, 'logistics': LOGISTICS,
    'healthcare': HEALTHCARE, 'hospitality': HOSPITALITY, 'fm': FM, 'retail': RETAIL, 'agriculture': AGRICULTURE, 'food': FOOD, 'marine': MARINE,
    'aviation': AVIATION, 'education': EDUCATION, 'telecom': TELECOM, 'power': POWER, 'waste': WASTE, 'cleaning': CLEANING, 'pest': PEST,
    'lab_pharma': LAB, 'shipyard': SHIPYARD, 'property_management': REAL_ESTATE, 'advertising': ADVERTISING, 'professional': PROFESSIONAL,
}

# The 24 industries of the scope → pack code(s) (for the README and the checks)
SCOPE_INDUSTRIES = [
    ('01', 'ERP, IT software and technology', ['it']), ('02', 'Construction and civil engineering', ['construction']),
    ('03', 'Manufacturing and industrial products', ['manufacturing']), ('04', 'Oil, gas and petrochemical', ['oil_gas']),
    ('05', 'Data centres and critical infrastructure', ['data_center']), ('06', 'Warehousing, logistics and transportation', ['logistics']),
    ('07', 'Healthcare, hospitals and clinics', ['healthcare']), ('08', 'Hospitality, hotels and restaurants', ['hospitality']),
    ('09', 'Facilities management and maintenance', ['fm']), ('10', 'Retail, shopping malls and supermarkets', ['retail']),
    ('11', 'Agriculture and farming', ['agriculture']), ('12', 'Food processing and food manufacturing', ['food']),
    ('13', 'Marine, shipping and port operations', ['marine']), ('14', 'Aviation and airport operations', ['aviation']),
    ('15', 'Education, schools and universities', ['education']), ('16', 'Telecommunications and network infrastructure', ['telecom']),
    ('17', 'Electrical, power and utilities', ['power']), ('18', 'Waste management and recycling', ['waste']),
    ('19', 'Cleaning, janitorial and pest control', ['cleaning', 'pest']), ('20', 'Laboratories and pharmaceuticals', ['lab_pharma']),
    ('21', 'Marine construction and shipyards', ['shipyard']), ('22', 'Real estate and property brokerage', ['property_management']),
    ('23', 'Advertising, marketing and signage', ['advertising']), ('24', 'Professional services and consultancy', ['professional']),
]
