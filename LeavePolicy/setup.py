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
    'Compensatory Leave': dict(days_per_year=0, accrual='earned', count_days='calendar', comp_full_day_hours=8, comp_half_day_hours=4,
                               comp_expiry_days=90, notes='Earned by weekend / holiday work: 8 h = 1 day, 4 h = ½ day; use within 90 days'),
}

POLICIES = {
    'office': dict(name='Office staff', code='OFFICE', kind='office', categories=['Office Staff', 'Management', 'Technical', 'Non-Technical'], is_default=True,
                   description='Office, technical and management staff. UAE law minimums, half days allowed, 14 days notice for annual leave, '
                               '15 days carried forward (the rest goes for encashment), 6 days casual leave.',
                   lines={'Annual Leave': dict(days_per_year=30, accrual='monthly', first_year_uae=True, count_days='calendar', notice_days=14,
                                               carry_forward_max=15, excess_action='encash', encashable=True, encash_max_per_year=15, law_reference='Art. 29'),
                          'Casual Leave': dict(days_per_year=6, accrual='yearly', count_days='working', max_per_request=2, notice_days=0, excess_action='lapse',
                                               notes='Company benefit; does not carry forward')}),
    'labour': dict(name='Labour', code='LABOUR', kind='labour', categories=['Skilled Worker', 'Unskilled Worker', 'Field / Site Staff'], is_default=False,
                   description='Site and labour staff. UAE law minimums, annual leave usually taken in one block with the home trip: '
                               '30 days notice, up to 60 days kept for the 2-year vacation, encashment only above that.',
                   lines={'Annual Leave': dict(days_per_year=30, accrual='monthly', first_year_uae=True, count_days='calendar', notice_days=30,
                                               carry_forward_max=60, excess_action='keep', encashable=True, encash_max_per_year=30, prorate='fixed', law_reference='Art. 29')}),
}


def _types():
    LT = M('calendars', 'leave_type')
    by_name = {t.name.strip().lower(): t for t in LT.objects.all()}
    return LT, by_name


def preview():
    _, by_name = _types()
    Policy = M('LeavePolicy', 'LeavePolicy')
    return {'leave_types': [{'name': n, 'description': d, 'exists': n.lower() in by_name} for n, _, _, _, _, d in TYPES],
            'policies': [{'name': p['name'], 'exists': Policy.objects.filter(code=p['code']).exists(), 'categories': p['categories']} for p in POLICIES.values()],
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
        if t is None:
            c, n = code, 2
            while LT.objects.filter(code=c, branch__isnull=True).exists():
                c, n = f'{code}{n}', n + 1
            t = LT.objects.create(name=name, code=c, type=typ, unit='days', negative=False, description=desc[:200], allow_half_day=half,
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
        lines = dict(COMMON)
        lines.update(p['lines'])
        for tname, settings in lines.items():
            t = by_name.get(tname.lower())
            if t and not Line.objects.filter(policy=pol, leave_type_id=t.pk).exists():
                Line.objects.create(policy=pol, leave_type_id=t.pk, **settings)
    return {'created_leave_types': created_types, 'created_policies': created_policies, 'workflows_added': workflows}
