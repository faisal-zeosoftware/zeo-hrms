"""
UAE standard leave setup (v1.10.0): the leave types of the UAE Labour Law and two policies, Office staff and Labour.
Loading adds what is missing and never changes or removes existing records (types are matched by name).
"""
from django.apps import apps
from django.db import transaction

M = apps.get_model
LAW = 'Federal Decree-Law 33/2021, Art. 29–32; Cabinet Resolution 1/2022'

# name, code, leave_category, paid/unpaid, half day, description
TYPES = [
    ('Annual Leave', 'AL', 'annual', 'paid', True, '30 days a year; 2 days a month between 6 and 12 months of service'),
    ('Sick Leave', 'SL', 'sick', 'paid', True, '90 days a year after probation: 15 full pay, 30 half pay, 45 unpaid'),
    ('Maternity Leave', 'ML', 'maternity', 'paid', False, '60 days: 45 full pay, 15 half pay'),
    ('Maternity Extension (unpaid)', 'MLX', 'maternity', 'unpaid', False, 'Up to 45 more days without pay for illness after the birth'),
    ('Parental Leave', 'PRL', 'paternity', 'paid', False, '5 working days within 6 months of the birth, father or mother'),
    ('Bereavement – Spouse', 'BRS', 'bereavement', 'paid', False, '5 days on the death of a spouse'),
    ('Bereavement – Family', 'BRF', 'bereavement', 'paid', False, '3 days: parent, child, sibling, grandchild, grandparent'),
    ('Study Leave', 'STL', 'casual', 'paid', False, '10 working days a year to sit exams, after 2 years of service'),
    ('Hajj Leave', 'HJ', 'unpaid', 'unpaid', False, 'Up to 30 days without pay, once in service'),
    ('Unpaid Leave', 'UL', 'unpaid', 'unpaid', True, 'By agreement with the employer'),
    ('Casual Leave', 'CL', 'casual', 'paid', True, 'Company benefit (not in the law) – office staff'),
    ('Compensatory Leave', 'CO', 'compensatory', 'paid', True, 'Days off earned by working on a weekend or public holiday (company practice)'),
    # v1.26.0 – the Falcon Demo workbook uses it; UAE law: full pay up to 6 months of treatment, half pay the next 6 months
    ('Work Injury Leave', 'WIL', 'sick', 'paid', False, 'Treatment after a work injury: full pay up to 6 months, then half pay up to 6 months'),
    # v1.32.0 – the rest of the UAE law leaves and the leaves companies of every business type give in practice
    ('National Service Leave', 'NSL', 'casual', 'paid', False, 'Emirati employees called for national / reserve service: full pay for the service period (Art. 32(1)(a); Federal Law 6/2014)'),
    ('Marriage Leave', 'MRL', 'casual', 'paid', False, 'Company benefit (not in the law): usually 3–5 days once in service, on the marriage certificate'),
    ('Emergency Leave', 'EML', 'casual', 'paid', True, 'Company benefit: a few paid days a year for a sudden family / personal emergency, no notice'),
    ('Umrah Leave', 'UMR', 'unpaid', 'unpaid', False, 'Company practice: up to 10 days without pay for Umrah, once a year, with the visa / ticket'),
    ('Medical Appointment', 'MED', 'casual', 'paid', True, 'Half day with pay for a medical / dental appointment, on the appointment proof'),
    ('Short Leave (hours)', 'SLV', 'casual', 'paid', False, 'Permission for a few hours in the day (bank, government office, school); counted in hours'),
    ('Sabbatical / Extended Unpaid Leave', 'SAB', 'unpaid', 'unpaid', False, 'Long leave without pay by agreement (up to 90 days); gratuity service stops for the period'),
    ('Academic Break', 'ACB', 'annual', 'paid', False, 'Education: the term / summer breaks of the academic calendar as the annual leave of teaching staff'),
    ('Quarantine / Isolation Leave', 'QIL', 'sick', 'paid', False, 'Paid isolation ordered by a health authority, with the official notice (not counted against sick leave)'),
]

COMMON = {   # line settings per leave-type name, shared by both policies
    'Sick Leave': dict(days_per_year=90, accrual='yearly', count_days='calendar', after_probation=True, requires_document=True,
                       pay_slabs=[[15, 100], [30, 50], [45, 0]], excess_action='lapse', law_reference='Art. 31'),
    'Maternity Leave': dict(days_per_year=60, accrual='per_event', count_days='calendar', gender='F', max_per_request=60, requires_document=True,
                            pay_slabs=[[45, 100], [15, 50]], law_reference='Art. 30'),
    'Maternity Extension (unpaid)': dict(days_per_year=45, accrual='none', count_days='calendar', gender='F', max_per_request=45, requires_document=True,
                                         law_reference='Art. 30(3)'),
    'Parental Leave': dict(days_per_year=5, accrual='per_event', count_days='working', max_per_request=5, max_per_year=5, requires_document=True,
                           law_reference='Art. 32(1)(c)'),
    'Bereavement – Spouse': dict(days_per_year=5, accrual='per_event', count_days='calendar', max_per_request=5, requires_document=True, law_reference='Art. 32(1)(b)'),
    'Bereavement – Family': dict(days_per_year=3, accrual='per_event', count_days='calendar', max_per_request=3, requires_document=True, law_reference='Art. 32(1)(b)'),
    'Study Leave': dict(days_per_year=10, accrual='yearly', count_days='working', min_service_months=24, requires_document=True, excess_action='lapse', law_reference='Art. 32(1)(d)'),
    'Hajj Leave': dict(days_per_year=30, accrual='none', count_days='calendar', max_per_request=30, max_times_in_service=1, notice_days=30,
                       notes='Practice under the former law; unpaid unless the company decides otherwise'),
    'Unpaid Leave': dict(days_per_year=0, accrual='none', count_days='calendar', notes='Needs approval; reduces pay and gratuity service'),
    'Work Injury Leave': dict(days_per_year=360, accrual='per_event', count_days='calendar', requires_document=True,
                              pay_slabs=[[180, 100], [180, 50]], notes='Medical report of the treating doctor; reported to MoHRE',
                              law_reference='Art. 37; Cabinet Resolution 1/2022'),
    'Compensatory Leave': dict(days_per_year=0, accrual='earned', count_days='calendar', comp_full_day_hours=8, comp_half_day_hours=4,
                               comp_expiry_days=90, notes='Earned by weekend / holiday work: 8 h = 1 day, 4 h = ½ day; use within 90 days'),
    # v1.32.0
    'National Service Leave': dict(days_per_year=0, accrual='none', count_days='calendar', requires_document=True, law_reference='Art. 32(1)(a); Federal Law 6/2014',
                                   notes='Emirati employees only; full pay for the call-up period on the official notice'),
    'Marriage Leave': dict(days_per_year=3, accrual='per_event', count_days='working', max_per_request=3, max_times_in_service=1, requires_document=True,
                           notes='Company benefit – marriage certificate'),
    'Emergency Leave': dict(days_per_year=3, accrual='yearly', count_days='working', max_per_request=2, notice_days=0, excess_action='lapse', notes='Company benefit'),
    'Umrah Leave': dict(days_per_year=10, accrual='none', count_days='calendar', max_per_request=10, max_per_year=10, notice_days=14, requires_document=True,
                        notes='Company practice; unpaid'),
    'Medical Appointment': dict(days_per_year=6, accrual='yearly', count_days='working', max_per_request=0.5, requires_document=True, excess_action='lapse',
                                notes='Half days only'),
    'Short Leave (hours)': dict(days_per_year=24, accrual='yearly', count_days='working', excess_action='lapse',
                                notes='Hours: 24 hours a year (3 working days); 1 unit = 1 hour'),
    'Sabbatical / Extended Unpaid Leave': dict(days_per_year=90, accrual='none', count_days='calendar', max_per_request=90, min_service_months=12, notice_days=30,
                                               notes='Needs management approval; no pay, no gratuity accrual for the period'),
    'Quarantine / Isolation Leave': dict(days_per_year=14, accrual='per_event', count_days='calendar', max_per_request=14, requires_document=True,
                                         notes='Health-authority notice; paid, not deducted from sick leave'),
}

# which of the practice leaves each business type gives (law leaves come from COMMON for everybody)
PRACTICE = ('Marriage Leave', 'Emergency Leave', 'Umrah Leave', 'Medical Appointment', 'Short Leave (hours)', 'Sabbatical / Extended Unpaid Leave',
            'Quarantine / Isolation Leave', 'National Service Leave')

POLICIES = {
    'office': dict(name='Office staff', code='OFFICE', kind='office', categories=['Office Staff', 'Management', 'Technical', 'Non-Technical'], is_default=True,
                   description='Office, technical and management staff. UAE law minimums, half days allowed, 14 days notice for annual leave, '
                               '15 days carried forward (the rest goes for encashment), 6 days casual leave.',
                   lines={'Annual Leave': dict(days_per_year=30, accrual='monthly', first_year_uae=True, count_days='calendar', notice_days=14,
                                               carry_forward_max=15, excess_action='encash', encashable=True, encash_max_per_year=15, law_reference='Art. 29'),
                          'Casual Leave': dict(days_per_year=6, accrual='yearly', count_days='working', max_per_request=2, notice_days=0, excess_action='lapse',
                                               notes='Company benefit; does not carry forward')},
                   skip=('Academic Break',)),
    'labour': dict(name='Labour', code='LABOUR', kind='labour', categories=['Skilled Worker', 'Unskilled Worker', 'Field / Site Staff'], is_default=False,
                   description='Site and labour staff. UAE law minimums, annual leave usually taken in one block with the home trip: '
                               '30 days notice, up to 60 days kept for the 2-year vacation, encashment only above that.',
                   lines={'Annual Leave': dict(days_per_year=30, accrual='monthly', first_year_uae=True, count_days='calendar', notice_days=30,
                                               carry_forward_max=60, excess_action='keep', encashable=True, encash_max_per_year=30, prorate='fixed', law_reference='Art. 29')},
                   skip=('Marriage Leave', 'Medical Appointment', 'Short Leave (hours)', 'Sabbatical / Extended Unpaid Leave', 'Academic Break')),
    # v1.32.0 – policies for the other business types (all on the UAE law minimums; practice leaves differ)
    'retail': dict(name='Retail & hospitality (shift staff)', code='RETAIL', kind='other', categories=['Shift Staff', 'Sales Staff', 'Hospitality Staff', 'Store Staff'], is_default=False,
                   description='Shops, restaurants, hotels and other shift businesses: 30 days annual leave in calendar days with 30 days notice, '
                               'up to 30 days carried forward, no casual leave; emergency leave and compensatory off for holiday work.',
                   lines={'Annual Leave': dict(days_per_year=30, accrual='monthly', first_year_uae=True, count_days='calendar', notice_days=30,
                                               carry_forward_max=30, excess_action='encash', encashable=True, encash_max_per_year=15, law_reference='Art. 29')},
                   skip=('Marriage Leave', 'Sabbatical / Extended Unpaid Leave')),
    'health': dict(name='Healthcare & clinics', code='HEALTH', kind='office', categories=['Medical Staff', 'Nursing Staff', 'Clinical Staff', 'Paramedical Staff'], is_default=False,
                   description='Hospitals, clinics and labs: office terms plus CME / exam study leave, quarantine leave and medical appointments; '
                               'annual leave 30 days with 30 days notice because of the duty roster.',
                   lines={'Annual Leave': dict(days_per_year=30, accrual='monthly', first_year_uae=True, count_days='calendar', notice_days=30,
                                               carry_forward_max=15, excess_action='encash', encashable=True, encash_max_per_year=15, law_reference='Art. 29'),
                          'Casual Leave': dict(days_per_year=4, accrual='yearly', count_days='working', max_per_request=2, excess_action='lapse', notes='Company benefit'),
                          'Study Leave': dict(days_per_year=10, accrual='yearly', count_days='working', min_service_months=12, requires_document=True, excess_action='lapse',
                                              law_reference='Art. 32(1)(d)', notes='Exams and CME / licence renewal')}),
    'education': dict(name='Education (academic staff)', code='EDU', kind='other', categories=['Teaching Staff', 'Academic Staff', 'Faculty'], is_default=False,
                      description='Schools, nurseries and institutes: teaching staff take their annual leave in the term and summer breaks (Academic Break), '
                                  'no carry forward; administrative staff of a school use the Office staff policy.',
                      lines={'Annual Leave': dict(days_per_year=30, accrual='monthly', first_year_uae=True, count_days='calendar', notice_days=30,
                                                  carry_forward_max=0, excess_action='lapse', law_reference='Art. 29',
                                                  notes='Taken in the academic breaks; book through Academic Break'),
                             'Academic Break': dict(days_per_year=0, accrual='none', count_days='calendar', notes='Term / summer breaks per the academic calendar – no balance')},
                      skip=('Casual Leave', 'Umrah Leave')),
    'difc': dict(name='DIFC employees', code='DIFC', kind='other', categories=['DIFC Staff'], is_default=False,
                 description='Companies registered in the Dubai International Financial Centre (DIFC Employment Law No. 2 of 2019): 20 working days annual leave, '
                             '60 working days sick leave (10 full, 20 half, 30 unpaid), 65 working days maternity (33 full, 32 half), 5 working days paternity.',
                 lines={'Annual Leave': dict(days_per_year=20, accrual='monthly', count_days='working', notice_days=14, carry_forward_max=5, carry_forward_expiry_months=3,
                                             excess_action='lapse', encashable=True, encash_max_per_year=5, law_reference='DIFC Employment Law 2019, Art. 27–29'),
                        'Sick Leave': dict(days_per_year=60, accrual='yearly', count_days='working', requires_document=True, pay_slabs=[[10, 100], [20, 50], [30, 0]],
                                           excess_action='lapse', law_reference='DIFC Employment Law 2019, Art. 34'),
                        'Maternity Leave': dict(days_per_year=65, accrual='per_event', count_days='working', gender='F', max_per_request=65, requires_document=True,
                                                pay_slabs=[[33, 100], [32, 50]], min_service_months=12, law_reference='DIFC Employment Law 2019, Art. 37–38'),
                        'Parental Leave': dict(days_per_year=5, accrual='per_event', count_days='working', max_per_request=5, requires_document=True,
                                               law_reference='DIFC Employment Law 2019, Art. 40 (paternity)')},
                 skip=('Casual Leave', 'Hajj Leave', 'Maternity Extension (unpaid)', 'Study Leave')),
    'adgm': dict(name='ADGM employees', code='ADGM', kind='other', categories=['ADGM Staff'], is_default=False,
                 description='Companies registered in Abu Dhabi Global Market (ADGM Employment Regulations 2019 / 2024): 20 working days annual leave, '
                             '60 working days sick leave (10 full, 20 half, 30 unpaid), 65 working days maternity, 5 working days paternity.',
                 lines={'Annual Leave': dict(days_per_year=20, accrual='monthly', count_days='working', notice_days=14, carry_forward_max=5, carry_forward_expiry_months=3,
                                             excess_action='lapse', encashable=True, encash_max_per_year=5, law_reference='ADGM Employment Regulations, s. 23–25'),
                        'Sick Leave': dict(days_per_year=60, accrual='yearly', count_days='working', requires_document=True, pay_slabs=[[10, 100], [20, 50], [30, 0]],
                                           excess_action='lapse', law_reference='ADGM Employment Regulations, s. 30'),
                        'Maternity Leave': dict(days_per_year=65, accrual='per_event', count_days='working', gender='F', max_per_request=65, requires_document=True,
                                                pay_slabs=[[33, 100], [32, 50]], min_service_months=12, law_reference='ADGM Employment Regulations, s. 33–35'),
                        'Parental Leave': dict(days_per_year=5, accrual='per_event', count_days='working', max_per_request=5, requires_document=True,
                                               law_reference='ADGM Employment Regulations, s. 36 (paternity)')},
                 skip=('Casual Leave', 'Hajj Leave', 'Maternity Extension (unpaid)', 'Study Leave')),
}


def _types():
    LT = M('calendars', 'leave_type')
    by_name = {t.name.strip().lower(): t for t in LT.objects.all()}
    return LT, by_name


def preview():
    _, by_name = _types()
    Policy = M('LeavePolicy', 'LeavePolicy')
    return {'leave_types': [{'name': n, 'description': d, 'exists': n.lower() in by_name} for n, _, _, _, _, d in TYPES],
            'policies': [{'name': p['name'], 'exists': Policy.objects.filter(code=p['code']).exists(), 'categories': p['categories'], 'description': p['description']}
                         for p in POLICIES.values()],
            'law': LAW}


@transaction.atomic
def load(user=None):
    LT, by_name = _types()
    Policy, Line = M('LeavePolicy', 'LeavePolicy'), M('LeavePolicy', 'LeavePolicyLine')
    WF = M('calendars', 'LVApprovalWorkflow')
    Branch = M('OrganisationManager', 'brnch_mstr')
    Cat = M('OrganisationManager', 'ctgry_master')
    created_types, created_policies, workflows = [], [], 0
    for name, code, cat, typ, half, desc in TYPES:
        t = by_name.get(name.lower())
        if t is None and cat == 'compensatory':        # an existing compensatory type under another name
            t = LT.objects.filter(is_compensatory=True).first()
            if t is not None:
                by_name[name.lower()] = t
        if t is None and cat == 'paternity':
            # v1.18.0: a new company starts with a default "Paternity Leave"; UAE law has parental leave (Art. 32) – use that type
            # (renamed) instead of adding a second one that has no workflow or policy line. Only while nobody has used it.
            LR = M('calendars', 'employee_leave_request')
            t = LT.objects.filter(leave_category='paternity', name__iexact='Paternity Leave').first()
            if t is not None and not LR.objects.filter(leave_type=t).exists():
                t.name, t.description = name, desc[:200]
                t.include_weekend, t.include_holiday, t.use_common_workflow = True, True, False
                t.save()
                by_name.pop('paternity leave', None)
                by_name[name.lower()] = t
            else:
                t = None
        if t is None:
            c, n = code, 2
            while LT.objects.filter(code=c, branch__isnull=True).exists():
                c, n = f'{code}{n}', n + 1
            t = LT.objects.create(name=name, code=c, type=typ, unit='hours' if 'hours' in name.lower() else 'days', negative=False, description=desc[:200], allow_half_day=half,
                                  include_weekend=True, include_holiday=True, leave_category=cat, use_common_workflow=False,
                                  include_dashboard=name in ('Annual Leave', 'Sick Leave'), is_compensatory=(cat == 'compensatory'), created_by=user)
            by_name[name.lower()] = t
            created_types.append(name)
        if not WF.objects.filter(request_type=t).exists():     # requests need an approval workflow
            wf = WF.objects.create(request_type=t, approval_type='reporting_manager', created_by=user)
            wf.branch.set(Branch.objects.all())
            workflows += 1
    # the standard employee categories (v1.7.2 list) the policies use
    try:
        from OrganisationManager.defaults import load as load_masters
        load_masters('category', list(Branch.objects.all()), user)
    except Exception:
        pass
    cats = {c.ctgry_title.strip().lower(): c.pk for c in Cat.objects.all()}
    for key, p in POLICIES.items():
        pol = Policy.objects.filter(code=p['code']).first()
        if pol is None:
            used = set()
            for other in Policy.objects.all():
                used.update(other.category_ids or [])
            ids = [cats[c.lower()] for c in p['categories'] if c.lower() in cats and cats[c.lower()] not in used]
            pol = Policy.objects.create(name=p['name'], code=p['code'], kind=p['kind'], description=p['description'], category_ids=ids,
                                        is_default=p['is_default'] and not Policy.objects.filter(is_default=True).exists())
            created_policies.append(pol.name)
        lines = {k: v for k, v in COMMON.items() if k not in (p.get('skip') or ())}
        if 'Academic Break' not in p['lines']:
            lines.pop('Academic Break', None)
        lines.update(p['lines'])
        for tname, settings in lines.items():
            t = by_name.get(tname.lower())
            if t and not Line.objects.filter(policy=pol, leave_type_id=t.pk).exists():
                Line.objects.create(policy=pol, leave_type_id=t.pk, **settings)
    mapped = map_categories()
    return {'created_leave_types': created_types, 'created_policies': created_policies, 'workflows_added': workflows,
            'categories_mapped': mapped}


LABOUR_WORDS = ('labour', 'labor', 'worker', 'skilled', 'unskilled', 'semi-skilled', 'site', 'field', 'helper', 'driver', 'operator',
                'technician', 'blue collar', 'crew', 'cleaner', 'mason', 'carpenter', 'welder', 'loader')


def map_categories():
    """v1.26.0 – every employee category that no policy names goes to Labour (labour / worker / skilled / site / driver …)
    or Office staff (everything else). Before, the setup only knew its own category names (Office Staff, Skilled Worker …),
    so a company with Staff / Skilled / Labour / Supervisor had 12 of 314 employees under a policy by category and
    site labour silently fell under the Office default policy. Existing assignments are never changed."""
    Policy = M('LeavePolicy', 'LeavePolicy')
    Cat = M('OrganisationManager', 'ctgry_master')
    office = Policy.objects.filter(code='OFFICE').first()
    labour = Policy.objects.filter(code='LABOUR').first()
    if office is None and labour is None:
        return []
    used = set()
    for p in Policy.objects.all():
        used.update(p.category_ids or [])
    out = []
    for c in Cat.objects.all().order_by('ctgry_title'):
        if c.pk in used:
            continue
        t = (c.ctgry_title or '').lower()
        target = labour if (labour is not None and any(w in t for w in LABOUR_WORDS)) else (office or labour)
        target.category_ids = list(target.category_ids or []) + [c.pk]
        target.save(update_fields=['category_ids'])
        used.add(c.pk)
        out.append({'category': c.ctgry_title, 'policy': target.name})
    return out
