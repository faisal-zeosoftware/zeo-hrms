"""v1.73.23 (pending item 154) – the standard leave setup of Saudi Arabia, Qatar, Oman, Bahrain, Kuwait and India: the leave
types of each country's labour law and one policy, "Standard staff (<country>)", with the law minimums. Before, these
countries got three neutral types (Annual / Sick / Unpaid) with no days, pay slabs or rules.

Loading adds what is missing and never changes or removes existing records (types are matched by name, like the UAE setup in
setup.py). Every value can be changed on Leave › Setup. Each value is from the law text as summarised by at least two published
sources (see the law_reference / notes of each line); what could not be confirmed twice is not loaded (see LEFT_OUT).

Religion is not stored on the employee, so the leaves of Muslim employees (Hajj, the Muslim widow's iddah) are loaded for
everybody with the condition in the description – HR approves them.
"""
from django.apps import apps
from django.db import transaction

M = apps.get_model

# name, code, leave_category, paid/unpaid, half day, description, policy line settings
#   (the line keys are LeavePolicyLine fields; 'nationals_only' goes to the leave type rule)
_AL = 'Annual Leave'
_SL = 'Sick Leave'
_UL = ('Unpaid Leave', 'UL', 'unpaid', 'unpaid', True, 'By agreement with the employer',
       dict(days_per_year=0, accrual='none', count_days='calendar', notes='Needs approval; reduces pay and end-of-service service'))

COUNTRIES = {
    'SA': dict(
        country='Saudi Arabia', law='Saudi Labour Law (Royal Decree M/51) as amended in force 19 Feb 2025',
        types=[
            (_AL, 'AL', 'annual', 'paid', True, '21 days a year; 30 days after 5 years of service (Art. 109)',
             dict(days_per_year=21, accrual='monthly', count_days='calendar', notice_days=14, excess_action='keep', encashable=True,
                  service_steps=[{'from_year': 6, 'days': 30}], law_reference='Art. 109')),
            (_SL, 'SL', 'sick', 'paid', True, '120 days a year: 30 full pay, 60 at 75 %, 30 unpaid (Art. 117)',
             dict(days_per_year=120, accrual='yearly', count_days='calendar', requires_document=True, pay_slabs=[[30, 100], [60, 75], [30, 0]],
                  excess_action='lapse', law_reference='Art. 117')),
            ('Maternity Leave', 'ML', 'maternity', 'paid', False, '12 weeks (84 days) fully paid; 6 weeks must be after the birth (Art. 151, 2025)',
             dict(days_per_year=84, accrual='per_event', count_days='calendar', gender='F', max_per_request=84, requires_document=True,
                  law_reference='Art. 151 (2025)')),
            ('Paternity Leave', 'PTL', 'paternity', 'paid', False, '3 days fully paid, within 7 days of the birth (Art. 113)',
             dict(days_per_year=3, accrual='per_event', count_days='calendar', gender='M', max_per_request=3, requires_document=True,
                  law_reference='Art. 113')),
            ('Marriage Leave', 'MRL', 'casual', 'paid', False, '5 days fully paid on the employee’s marriage (Art. 113)',
             dict(days_per_year=5, accrual='per_event', count_days='calendar', max_per_request=5, requires_document=True, law_reference='Art. 113')),
            ('Bereavement – Spouse, Parent, Child', 'BRS', 'bereavement', 'paid', False, '5 days fully paid: death of a spouse, parent or child (Art. 113)',
             dict(days_per_year=5, accrual='per_event', count_days='calendar', max_per_request=5, requires_document=True, law_reference='Art. 113')),
            ('Bereavement – Brother or Sister', 'BRF', 'bereavement', 'paid', False, '3 days fully paid: death of a brother or sister (Art. 113, 2025)',
             dict(days_per_year=3, accrual='per_event', count_days='calendar', max_per_request=3, requires_document=True, law_reference='Art. 113 (2025)')),
            ('Hajj Leave', 'HJ', 'casual', 'paid', False, 'Muslim employees: 10–15 days with pay incl. the Eid al-Adha holiday, once, after 2 years (Art. 114)',
             dict(days_per_year=15, accrual='none', count_days='calendar', max_per_request=15, max_times_in_service=1, min_service_months=24,
                  requires_document=True, law_reference='Art. 114', notes='Muslim employees who have not done Hajj before; 10 to 15 days including Eid al-Adha')),
            ('Iddah Leave – Muslim Widow', 'IDM', 'bereavement', 'paid', False, 'Muslim widow: at least 4 months and 10 days fully paid (Art. 160)',
             dict(days_per_year=130, accrual='per_event', count_days='calendar', gender='F', max_per_request=130, requires_document=True,
                  law_reference='Art. 160', notes='From the date of the husband’s death')),
            ('Iddah Leave – Non-Muslim Widow', 'IDN', 'bereavement', 'paid', False, 'Non-Muslim widow: 15 days (Art. 160)',
             dict(days_per_year=15, accrual='per_event', count_days='calendar', gender='F', max_per_request=15, requires_document=True,
                  law_reference='Art. 160')),
            _UL,
        ]),
    'QA': dict(
        country='Qatar', law='Qatar Labour Law No. 14 of 2004 as amended',
        types=[
            (_AL, 'AL', 'annual', 'paid', True, '3 weeks a year after one year of service; 4 weeks after 5 years (Art. 79)',
             dict(days_per_year=21, accrual='monthly', count_days='calendar', min_service_months=12, notice_days=14, excess_action='keep',
                  encashable=True, service_steps=[{'from_year': 6, 'days': 28}], law_reference='Art. 79')),
            (_SL, 'SL', 'sick', 'paid', True, 'After 3 months of service: 2 weeks full pay, 4 weeks half pay, then unpaid (Art. 82)',
             dict(days_per_year=84, accrual='yearly', count_days='calendar', min_service_months=3, requires_document=True,
                  pay_slabs=[[14, 100], [28, 50], [42, 0]], excess_action='lapse', law_reference='Art. 82',
                  notes='Medical certificate of a doctor approved by the employer')),
            ('Maternity Leave', 'ML', 'maternity', 'paid', False, '50 days fully paid after one year of service; at least 35 days after the birth (Art. 96)',
             dict(days_per_year=50, accrual='per_event', count_days='calendar', gender='F', max_per_request=50, min_service_months=12,
                  requires_document=True, law_reference='Art. 96')),
            ('Hajj Leave', 'HJ', 'unpaid', 'unpaid', False, 'Muslim employees: up to 20 days without pay, once in service (Art. 83)',
             dict(days_per_year=20, accrual='none', count_days='calendar', max_per_request=20, max_times_in_service=1, requires_document=True,
                  law_reference='Art. 83', notes='The employer sets how many may go each year, longest service first')),
            _UL,
        ]),
    'OM': dict(
        country='Oman', law='Omani Labour Law, Royal Decree 53/2023',
        types=[
            (_AL, 'AL', 'annual', 'paid', True, '30 days a year, after 6 months of service (Art. 78)',
             dict(days_per_year=30, accrual='monthly', count_days='calendar', min_service_months=6, notice_days=14, carry_forward_max=30,
                  excess_action='keep', encashable=True, law_reference='Art. 78, 81')),
            (_SL, 'SL', 'sick', 'paid', True, 'Up to 182 days a year: 21 full pay, 14 at 75 %, 35 at 50 %, 112 at 35 % (Art. 82)',
             dict(days_per_year=182, accrual='yearly', count_days='calendar', requires_document=True,
                  pay_slabs=[[21, 100], [14, 75], [35, 50], [112, 35]], excess_action='lapse', law_reference='Art. 82')),
            ('Maternity Leave', 'ML', 'maternity', 'paid', False, '98 days; up to 14 days before the birth on a medical recommendation (Art. 84)',
             dict(days_per_year=98, accrual='per_event', count_days='calendar', gender='F', max_per_request=98, requires_document=True, law_reference='Art. 84')),
            ('Paternity Leave', 'PTL', 'paternity', 'paid', False, '7 days, before the child is 98 days old (Art. 84)',
             dict(days_per_year=7, accrual='per_event', count_days='calendar', gender='M', max_per_request=7, requires_document=True, law_reference='Art. 84')),
            ('Marriage Leave', 'MRL', 'casual', 'paid', False, '3 days on the employee’s marriage (Art. 84)',
             dict(days_per_year=3, accrual='per_event', count_days='calendar', max_per_request=3, requires_document=True, law_reference='Art. 84')),
            ('Bereavement – Spouse or Child', 'BRS', 'bereavement', 'paid', False, '10 days: death of a wife / husband, son or daughter (Art. 84)',
             dict(days_per_year=10, accrual='per_event', count_days='calendar', max_per_request=10, requires_document=True, law_reference='Art. 84')),
            ('Bereavement – Family', 'BRF', 'bereavement', 'paid', False, '3 days: death of a parent, grandparent, brother or sister (Art. 84)',
             dict(days_per_year=3, accrual='per_event', count_days='calendar', max_per_request=3, requires_document=True, law_reference='Art. 84')),
            ('Bereavement – Uncle or Aunt', 'BRU', 'bereavement', 'paid', False, '2 days: death of a paternal or maternal uncle or aunt (Art. 84)',
             dict(days_per_year=2, accrual='per_event', count_days='calendar', max_per_request=2, requires_document=True, law_reference='Art. 84')),
            ('Hajj Leave', 'HJ', 'casual', 'paid', False, 'Muslim employees: 15 days once in service, after one year (Art. 84)',
             dict(days_per_year=15, accrual='none', count_days='calendar', max_per_request=15, max_times_in_service=1, min_service_months=12,
                  requires_document=True, law_reference='Art. 84')),
            ('Iddah Leave – Muslim Widow', 'IDM', 'bereavement', 'paid', False, 'Muslim widow: 130 days from the husband’s death (Art. 84)',
             dict(days_per_year=130, accrual='per_event', count_days='calendar', gender='F', max_per_request=130, requires_document=True, law_reference='Art. 84')),
            ('Iddah Leave – Non-Muslim Widow', 'IDN', 'bereavement', 'paid', False, 'Non-Muslim widow: 14 days from the husband’s death (Art. 84)',
             dict(days_per_year=14, accrual='per_event', count_days='calendar', gender='F', max_per_request=14, requires_document=True, law_reference='Art. 84')),
            ('Exam Leave', 'EXL', 'casual', 'paid', False, 'Omani employees studying: up to 15 days a year for exams (Art. 84)',
             dict(days_per_year=15, accrual='yearly', count_days='calendar', requires_document=True, excess_action='lapse', law_reference='Art. 84',
                  nationals_only=True)),
            ('Patient Escort Leave', 'PEL', 'casual', 'paid', False, 'Omani employees: up to 15 days a year to accompany a sick spouse or relative (2nd degree) (Art. 84)',
             dict(days_per_year=15, accrual='yearly', count_days='calendar', requires_document=True, excess_action='lapse', law_reference='Art. 84',
                  nationals_only=True)),
            _UL,
        ]),
    'BH': dict(
        country='Bahrain', law='Bahrain Labour Law for the Private Sector, Law No. 36 of 2012 as amended',
        types=[
            (_AL, 'AL', 'annual', 'paid', True, '30 days a year (2.5 days a month) after one year of service (Art. 58)',
             dict(days_per_year=30, accrual='monthly', count_days='calendar', min_service_months=12, notice_days=14, excess_action='keep',
                  encashable=True, law_reference='Art. 58')),
            (_SL, 'SL', 'sick', 'paid', True, '55 days a year after probation: 15 full pay, 20 half pay, 20 unpaid (Art. 65)',
             dict(days_per_year=55, accrual='yearly', count_days='calendar', after_probation=True, requires_document=True,
                  pay_slabs=[[15, 100], [20, 50], [20, 0]], excess_action='lapse', law_reference='Art. 65')),
            ('Maternity Leave', 'ML', 'maternity', 'paid', False, '60 days fully paid (Art. 32)',
             dict(days_per_year=60, accrual='per_event', count_days='calendar', gender='F', max_per_request=60, requires_document=True, law_reference='Art. 32')),
            ('Maternity Extension (unpaid)', 'MLX', 'maternity', 'unpaid', False, 'Up to 15 more days without pay (Art. 32)',
             dict(days_per_year=15, accrual='none', count_days='calendar', gender='F', max_per_request=15, law_reference='Art. 32')),
            ('Paternity Leave', 'PTL', 'paternity', 'paid', False, '1 day with pay on the birth of a child (Art. 63)',
             dict(days_per_year=1, accrual='per_event', count_days='calendar', gender='M', max_per_request=1, requires_document=True, law_reference='Art. 63')),
            ('Marriage Leave', 'MRL', 'casual', 'paid', False, '3 days with pay, once in service (Art. 63)',
             dict(days_per_year=3, accrual='per_event', count_days='calendar', max_per_request=3, max_times_in_service=1, requires_document=True,
                  law_reference='Art. 63')),
            ('Bereavement Leave', 'BRF', 'bereavement', 'paid', False, '3 days with pay: death of a spouse or a relative to the 4th degree, or a spouse’s relative to the 2nd (Art. 63)',
             dict(days_per_year=3, accrual='per_event', count_days='calendar', max_per_request=3, requires_document=True, law_reference='Art. 63')),
            ('Hajj Leave', 'HJ', 'casual', 'paid', False, 'Muslim employees: 14 days with pay once, after 5 years with the employer (Art. 67)',
             dict(days_per_year=14, accrual='none', count_days='calendar', max_per_request=14, max_times_in_service=1, min_service_months=60,
                  requires_document=True, law_reference='Art. 67', notes='Not when it was taken with an earlier employer')),
            _UL,
        ]),
    'KW': dict(
        country='Kuwait', law='Kuwait Private Sector Labour Law No. 6 of 2010 as amended',
        types=[
            (_AL, 'AL', 'annual', 'paid', True, '30 days a year with pay (Art. 70); weekends, holidays and sick leave are not counted',
             dict(days_per_year=30, accrual='monthly', count_days='working', min_service_months=9, notice_days=14, excess_action='keep',
                  encashable=True, law_reference='Art. 70–74', notes='Unused days can be carried for up to 2 years')),
            (_SL, 'SL', 'sick', 'paid', True, '75 days a year: 15 full pay, 10 at 75 %, 10 at 50 %, 10 at 25 %, 30 unpaid (Art. 69)',
             dict(days_per_year=75, accrual='yearly', count_days='calendar', requires_document=True,
                  pay_slabs=[[15, 100], [10, 75], [10, 50], [10, 25], [30, 0]], excess_action='lapse', law_reference='Art. 69')),
            ('Maternity Leave', 'ML', 'maternity', 'paid', False, '70 days fully paid (Art. 24)',
             dict(days_per_year=70, accrual='per_event', count_days='calendar', gender='F', max_per_request=70, requires_document=True, law_reference='Art. 24')),
            ('Maternity Extension (unpaid)', 'MLX', 'maternity', 'unpaid', False, 'Up to 4 more months without pay on request (Art. 24)',
             dict(days_per_year=120, accrual='none', count_days='calendar', gender='F', max_per_request=120, law_reference='Art. 24')),
            ('Bereavement Leave', 'BRF', 'bereavement', 'paid', False, '3 days fully paid: death of a relative of the 1st or 2nd degree (Art. 77)',
             dict(days_per_year=3, accrual='per_event', count_days='calendar', max_per_request=3, requires_document=True, law_reference='Art. 77')),
            ('Hajj Leave', 'HJ', 'casual', 'paid', False, 'Muslim employees: 21 days with pay once, after 2 years with the employer (Art. 76)',
             dict(days_per_year=21, accrual='none', count_days='calendar', max_per_request=21, max_times_in_service=1, min_service_months=24,
                  requires_document=True, law_reference='Art. 76', notes='Employees who have not done Hajj before')),
            ('Iddah Leave – Muslim Widow', 'IDM', 'bereavement', 'paid', False, 'Muslim widow: 4 months and 10 days fully paid (Art. 77)',
             dict(days_per_year=130, accrual='per_event', count_days='calendar', gender='F', max_per_request=130, requires_document=True,
                  law_reference='Art. 77', notes='She may not work for another employer during the leave')),
            ('Iddah Leave – Non-Muslim Widow', 'IDN', 'bereavement', 'paid', False, 'Non-Muslim widow: 21 days with pay (Art. 77)',
             dict(days_per_year=21, accrual='per_event', count_days='calendar', gender='F', max_per_request=21, requires_document=True, law_reference='Art. 77')),
            _UL,
        ]),
    'IN': dict(
        country='India', law='Factories Act 1948; Maternity Benefit Act 1961 (amended 2017); state Shops & Establishments Acts',
        types=[
            ('Earned Leave', 'EL', 'annual', 'paid', True, '1 day for every 20 days worked after 240 days in the year (Factories Act s. 79) – about 18 days',
             dict(days_per_year=18, accrual='monthly', count_days='working', min_service_months=12, notice_days=7, excess_action='keep', encashable=True,
                  law_reference='Factories Act 1948, s. 79', notes='Shops & Establishments Acts differ by state (15–21 days) – change the days for your state')),
            (_SL, 'SL', 'sick', 'paid', True, 'Sick leave of the state Shops & Establishments Act – 12 days by default',
             dict(days_per_year=12, accrual='yearly', count_days='working', requires_document=True, excess_action='lapse',
                  law_reference='State Shops & Establishments Act', notes='Change the days for your state')),
            ('Casual Leave', 'CL', 'casual', 'paid', True, 'Casual leave of the state Shops & Establishments Act – enter the days of your state',
             dict(days_per_year=0, accrual='none', count_days='working', excess_action='lapse', law_reference='State Shops & Establishments Act',
                  notes='Most states give 7–12 days – enter the days and choose "earned yearly"')),
            ('Maternity Leave', 'ML', 'maternity', 'paid', False, '26 weeks (182 days) for the first two children; 12 weeks from the third; after 80 days worked in the 12 months before',
             dict(days_per_year=182, accrual='per_event', count_days='calendar', gender='F', max_per_request=182, requires_document=True,
                  law_reference='Maternity Benefit Act 1961, s. 5 (2017)', notes='Third child onwards: 84 days (12 weeks) – up to 8 weeks before the birth')),
            ('Adoption / Commissioning Mother Leave', 'AML', 'maternity', 'paid', False, '12 weeks: adopting a child under 3 months, or a commissioning mother from the hand-over',
             dict(days_per_year=84, accrual='per_event', count_days='calendar', gender='F', max_per_request=84, requires_document=True,
                  law_reference='Maternity Benefit Act 1961, s. 5(4) (2017)')),
            _UL,
        ]),
}

# what the setup does not load because it could not be confirmed by two sources (listed in the release notes / open points)
LEFT_OUT = {
    'SA': ['Exam leave (Art. 115) – the days and pay'],
    'BH': ['Iddah of a Muslim widow (1 month paid + the rest from annual / unpaid leave)', 'Contingency leave (6 days a year, from annual leave)'],
    'OM': ['Unpaid childcare leave up to one year (Art. 83)'],
    'KW': ['Annual leave in the first year: after 6 or 9 months of service'],
    'IN': ['Miscarriage leave (6 weeks)', 'State sick / casual leave days'],
}


def country_of(pack):
    return COUNTRIES.get((pack or '').upper())


def _pol_code(cc):
    return f'STD-{cc}'


def preview(pack):
    c = country_of(pack)
    if not c:
        return None
    from .setup import _types
    _, by_name = _types()
    Policy = M('LeavePolicy', 'LeavePolicy')
    name = f'Standard staff ({c["country"]})'
    return {'country': pack.upper(), 'country_name': c['country'], 'law': c['law'],
            'leave_types': [{'name': t[0], 'description': t[5], 'exists': t[0].lower() in by_name} for t in c['types']],
            'policies': [{'name': name, 'exists': Policy.objects.filter(code=_pol_code(pack.upper())).exists(), 'categories': [],
                          'description': f'{c["country"]} labour-law minimums for every employee'}],
            'left_out': LEFT_OUT.get(pack.upper(), [])}


@transaction.atomic
def load(pack, user=None):
    """Adds the missing leave types of the country, their approval workflows and the policy "Standard staff (<country>)"
    with a line per type. Returns {created_leave_types, created_policies, workflows_added, lines_added}."""
    cc = (pack or '').upper()
    c = country_of(cc)
    if not c:
        return {'created_leave_types': [], 'created_policies': [], 'workflows_added': 0, 'lines_added': 0}
    from .setup import _types
    LT, by_name = _types()
    Policy, Line = M('LeavePolicy', 'LeavePolicy'), M('LeavePolicy', 'LeavePolicyLine')
    WF = M('calendars', 'LVApprovalWorkflow')
    Branch = M('OrganisationManager', 'brnch_mstr')
    created_types, created_policies, workflows, lines = [], [], 0, 0
    for name, code, cat, typ, half, desc, _line in c['types']:
        t = by_name.get(name.lower())
        if t is None:
            cd, n = code, 2
            while LT.objects.filter(code=cd, branch__isnull=True).exists():
                cd, n = f'{code}{n}', n + 1
            t = LT.objects.create(name=name, code=cd, type=typ, unit='days', negative=False, description=desc[:200], allow_half_day=half,
                                  include_weekend=True, include_holiday=True, leave_category=cat, use_common_workflow=False,
                                  include_dashboard=cat in ('annual', 'sick'), created_by=user)
            by_name[name.lower()] = t
            created_types.append(name)
        if _line.get('nationals_only'):
            try:
                from .models import LeaveTypeRule
                LeaveTypeRule.objects.update_or_create(leave_type_id=t.pk, defaults={'nationals_only': True})
            except Exception:   # noqa: BLE001
                pass
        if not WF.objects.filter(request_type=t).exists():
            wf = WF.objects.create(request_type=t, approval_type='reporting_manager', created_by=user)
            wf.branch.set(Branch.objects.all())
            workflows += 1
    pol = Policy.objects.filter(code=_pol_code(cc)).first()
    if pol is None:
        nm = f'Standard staff ({c["country"]})'
        if Policy.objects.filter(name=nm).exists():
            nm = f'{nm} – {cc}'
        pol = Policy.objects.create(name=nm, code=_pol_code(cc), kind='office', category_ids=[],
                                    description=f'{c["country"]} labour-law minimums ({c["law"]}). Change the days for your own terms on Leave › Setup.',
                                    is_default=not Policy.objects.filter(is_default=True).exists())
        created_policies.append(pol.name)
    for name, *_rest in c['types']:
        settings = {k: v for k, v in _rest[-1].items() if k != 'nationals_only'}
        t = by_name.get(name.lower())
        if t and not Line.objects.filter(policy=pol, leave_type_id=t.pk).exists():
            Line.objects.create(policy=pol, leave_type_id=t.pk, **settings)
            lines += 1
    return {'created_leave_types': created_types, 'created_policies': created_policies, 'workflows_added': workflows, 'lines_added': lines}
