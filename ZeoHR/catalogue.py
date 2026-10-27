"""v1.48.0 – PRO (government relations) services per country: code, name, government body, usual steps, the document it
renews (so a completed service renews the employee's document), days it usually takes. The company picks a service; the
steps become the checklist of the request. Fees differ by emirate / region and change often – they are typed per request.
"""

def S(code, name, body, steps, renews='', days=10, company=False):
    return {'code': code, 'name': name, 'authority': body, 'steps': steps, 'renews': renews, 'days': days, 'company': company}


COMMON = [
    S('passport_renewal', 'Passport renewal (embassy)', 'Embassy / consulate', ['Appointment', 'Application and photos', 'Fees paid', 'Passport collected'], 'Passport', 30),
    S('medical_insurance', 'Medical insurance card', 'Insurer', ['Member added', 'Card issued'], 'Medical insurance', 5),
    S('driving_licence', 'Driving licence renewal', 'Traffic department', ['Eye test', 'Application', 'Fees paid', 'Licence issued'], 'Driving licence', 3),
    S('attestation', 'Certificate attestation', 'Ministry of Foreign Affairs', ['Home-country attestation', 'Embassy attestation', 'MOFA attestation'], '', 15),
    S('other', 'Other service', '', ['Submitted', 'Done'], '', 7),
]

BY_COUNTRY = {
    'AE': [
        S('ae_quota', 'Work permit quota / offer letter', 'MOHRE', ['Offer letter signed (MOHRE)', 'Quota approved'], '', 3),
        S('ae_entry_permit', 'Entry permit (employment)', 'ICP / GDRFA', ['Work permit approved (MOHRE)', 'Entry permit issued'], '', 5),
        S('ae_status_change', 'Change of status', 'ICP / GDRFA', ['Entry permit', 'Status changed'], '', 3),
        S('ae_medical', 'Medical fitness test', 'DHA / MOHAP / SEHA', ['Appointment', 'Test done', 'Result received'], '', 3),
        S('ae_eid', 'Emirates ID (new / renewal)', 'ICP', ['Application typed', 'Biometrics', 'Card collected'], 'Emirates ID', 10),
        S('ae_visa_stamping', 'Residence visa (new / renewal)', 'ICP / GDRFA', ['Medical done', 'Emirates ID applied', 'Insurance valid', 'Residence issued'], 'Visa', 7),
        S('ae_labour_card', 'Labour card / work permit (new / renewal)', 'MOHRE', ['Contract signed', 'Work permit issued'], 'Labour card', 5),
        S('ae_contract', 'MOHRE employment contract', 'MOHRE', ['Contract typed', 'Signed by the employee', 'Approved'], 'Labour contract', 3),
        S('ae_cancel', 'Visa and work permit cancellation', 'MOHRE / ICP', ['Final settlement signed', 'Work permit cancelled', 'Residence cancelled'], '', 5),
        S('ae_dependant', 'Dependant visa', 'ICP / GDRFA', ['Entry permit', 'Medical', 'Emirates ID', 'Residence issued'], '', 15),
        S('ae_trade_licence', 'Trade licence renewal', 'DED / free zone', ['Lease (Ejari) valid', 'Fees paid', 'Licence renewed'], '', 10, True),
        S('ae_establishment_card', 'Establishment card (immigration)', 'ICP / GDRFA', ['Licence valid', 'Card renewed'], '', 5, True),
        S('ae_wps_mol', 'MOHRE establishment file update', 'MOHRE', ['Request', 'Approved'], '', 5, True),
    ],
    'SA': [
        S('sa_work_visa', 'Work visa (block / issue)', 'Qiwa / MHRSD', ['Visa block approved', 'Visa issued (Musaned / Qiwa)'], '', 10),
        S('sa_iqama', 'Iqama (new / renewal)', 'Jawazat (Muqeem)', ['Work permit fee paid (Qiwa)', 'Medical insurance valid', 'Iqama issued'], 'Iqama', 5),
        S('sa_work_permit', 'Work permit (Qiwa)', 'Qiwa / MHRSD', ['Fees paid', 'Permit issued'], 'Work permit', 3),
        S('sa_exit_reentry', 'Exit / re-entry visa', 'Jawazat (Muqeem)', ['Application', 'Fees paid', 'Visa issued'], '', 1),
        S('sa_final_exit', 'Final exit', 'Jawazat (Muqeem)', ['GOSI closed', 'Final exit issued'], '', 3),
        S('sa_transfer', 'Sponsorship (service) transfer', 'Qiwa', ['Request', 'Employee approved', 'Transferred'], '', 7),
        S('sa_gosi', 'GOSI registration', 'GOSI', ['Registered'], '', 2),
        S('sa_cr', 'Commercial registration renewal', 'Ministry of Commerce', ['Fees paid', 'CR renewed'], '', 5, True),
    ],
    'OM': [
        S('om_labour_clearance', 'Labour clearance', 'Ministry of Labour', ['Application', 'Approved'], '', 7),
        S('om_visa', 'Employment visa', 'Royal Oman Police', ['Labour clearance', 'Visa issued'], '', 7),
        S('om_resident_card', 'Resident card (new / renewal)', 'Royal Oman Police', ['Medical', 'Fingerprints', 'Card issued'], 'Resident card', 7),
        S('om_labour_card', 'Labour card renewal', 'Ministry of Labour', ['Fees paid', 'Card renewed'], 'Labour card', 5),
        S('om_cancel', 'Visa cancellation', 'Royal Oman Police', ['Settlement', 'Cancelled'], '', 5),
        S('om_cr', 'Commercial registration renewal', 'Ministry of Commerce', ['Fees paid', 'Renewed'], '', 5, True),
    ],
    'QA': [
        S('qa_work_visa', 'Work visa', 'Ministry of Labour / MOI', ['Visa approval', 'Visa issued'], '', 10),
        S('qa_rp', 'Residence permit / QID (new / renewal)', 'Ministry of Interior', ['Medical', 'Fingerprints', 'QID issued'], 'QID', 7),
        S('qa_contract', 'Employment contract attestation', 'Ministry of Labour', ['Contract typed', 'Attested'], 'Labour contract', 3),
        S('qa_exit', 'Exit / cancellation', 'Ministry of Interior', ['Settlement', 'RP cancelled'], '', 3),
        S('qa_cr', 'Commercial registration renewal', 'MOCI', ['Fees paid', 'Renewed'], '', 5, True),
    ],
    'KW': [
        S('kw_work_permit', 'Work permit', 'Public Authority for Manpower', ['Application', 'Permit issued'], 'Work permit', 10),
        S('kw_residence', 'Residence (new / renewal)', 'Ministry of Interior', ['Medical', 'Insurance', 'Residence issued'], 'Residence', 7),
        S('kw_civil_id', 'Civil ID', 'PACI', ['Application', 'Card collected'], 'Civil ID', 10),
        S('kw_cancel', 'Residence cancellation', 'Ministry of Interior', ['Settlement', 'Cancelled'], '', 5),
        S('kw_licence', 'Commercial licence renewal', 'Ministry of Commerce', ['Fees paid', 'Renewed'], '', 5, True),
    ],
    'IN': [
        S('in_pf', 'PF / UAN registration', 'EPFO', ['UAN generated', 'KYC linked'], '', 3),
        S('in_esi', 'ESI registration', 'ESIC', ['Registered', 'Card issued'], '', 3),
        S('in_fssai', 'Shop and establishment licence renewal', 'Labour department', ['Application', 'Renewed'], '', 10, True),
        S('in_frro', 'FRRO registration (foreign national)', 'FRRO', ['Application', 'Registration issued'], 'Residence permit', 15),
    ],
}


def services(country):
    return BY_COUNTRY.get((country or 'AE').upper(), []) + COMMON


def find(country, code):
    for s in services(country) + [x for v in BY_COUNTRY.values() for x in v]:
        if s['code'] == code:
            return s
    return None


def choices(country):
    return [(s['code'], s['name']) for s in services(country)]


def renewal_service(country, document):
    """The usual PRO service that renews this document type (Visa → residence visa …)."""
    d = (document or '').strip().lower()
    for s in services(country):
        if s['renews'] and s['renews'].lower() == d:
            return s
    return None


# the exit interview questions when the company has no exit survey of its own (People → Surveys, kind Exit)
EXIT_QUESTIONS = [
    {'text': 'What is the main reason you are leaving?', 'type': 'choice', 'options': 'Better pay / benefits|Career growth|Manager / leadership|Workload / hours|Culture / team|Relocation|Personal / family|Studies|Other', 'required': True},
    {'text': 'How satisfied were you with your job overall?', 'type': 'rating', 'required': True},
    {'text': 'How satisfied were you with your manager?', 'type': 'rating', 'required': True},
    {'text': 'Did you get the training and tools you needed?', 'type': 'rating', 'required': False},
    {'text': 'Were you paid fairly for your work?', 'type': 'rating', 'required': False},
    {'text': 'Would you work for us again?', 'type': 'yesno', 'required': False},
    {'text': 'How likely are you to recommend us as an employer (0–10)?', 'type': 'nps', 'required': False},
    {'text': 'What could we have done to keep you?', 'type': 'text', 'required': False},
    {'text': 'Anything else you want to tell us?', 'type': 'text', 'required': False},
]

REASON_OF = {'Better pay / benefits': 'pay', 'Career growth': 'career', 'Manager / leadership': 'manager', 'Workload / hours': 'workload',
             'Culture / team': 'culture', 'Relocation': 'relocation', 'Personal / family': 'family', 'Studies': 'studies', 'Other': 'other'}


def company_country():
    try:
        from CountryPack.services import pack_code
        c = (pack_code() or 'AE').upper()
        return c if c in BY_COUNTRY else 'AE'
    except Exception:
        return 'AE'


def service_choices():
    """Choices of ProService.service: the services of the company's country and the common ones (evaluated per request)."""
    return choices(company_country())
