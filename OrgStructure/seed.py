"""Standard GCC labour-law rule sets (v1.12.0). Values are the statutory minimums as published; check them against the
current law before relying on them for payroll – every rule set can be edited on the Country policies screen."""
from decimal import Decimal

MON, TUE, WED, THU, FRI, SAT, SUN = range(7)

STANDARD = [
    {
        'code': 'AE-FDL33-2021', 'name': 'UAE private sector (Federal Decree-Law 33/2021)', 'country_code': 'AE', 'country_name': 'United Arab Emirates',
        'law_reference': 'Federal Decree-Law No. 33 of 2021 on the Regulation of Labour Relations and Cabinet Resolution No. 1 of 2022',
        'weekend_days': [SAT, SUN], 'daily_hours': Decimal('8'), 'weekly_hours': Decimal('48'), 'ramadan_daily_hours': Decimal('6'),
        'annual_leave': {'first_year_after_months': 6, 'first_year_days_per_month': 2, 'days': 30, 'steps': [], 'basis': 'calendar',
                         'note': 'Art. 29: 2 days a month after 6 months up to one year, then 30 days a year.'},
        'sick_leave': [[15, 100], [30, 50], [45, 0]],
        'maternity': {'days': 60, 'full_pay_days': 45, 'half_pay_days': 15, 'note': 'Art. 30: 45 days full pay + 15 days half pay; up to 45 more days unpaid.'},
        'paternity_days': 5,
        'gratuity': {'basis': 'basic', 'min_service_years': 1, 'slabs': [{'from_year': 1, 'to_year': 5, 'days': 21}, {'from_year': 6, 'to_year': None, 'days': 30}],
                     'cap_years_of_pay': 2, 'by_contract': {'full_time': 'full', 'limited': 'full', 'part_time': 'pro rata to hours', 'temporary': 'pro rata', 'intern': 'none'},
                     'note': 'Art. 51: 21 days’ basic a year for the first 5 years, 30 days after; total capped at 2 years’ wage. No reduction on resignation.'},
        'overtime': {'normal': 125, 'night': 150, 'night_window': '22:00-04:00', 'rest_day': 150, 'holiday': 150, 'max_hours_per_day': 2,
                     'note': 'Art. 19: +25% (night +50%); rest day or public holiday: a substitute day or +50%.'},
        'notice_period': {'min_days': 30, 'max_days': 90}, 'probation_max_months': 6,
        'public_holiday_source': 'UAE Cabinet resolution on official holidays (published each year by MoHRE)',
        'wps_notes': 'Salaries through the Wage Protection System (MoHRE) with a SIF file; pay within 15 days of the due date (Ministerial Resolution 43/2022).',
        'notes': 'Private sector weekly rest is at least one paid day (Art. 21); many companies use Saturday–Sunday. Bereavement: 5 days (spouse), 3 days (close family).',
    },
    {
        'code': 'SA-LL-2005', 'name': 'Saudi Arabia (Labour Law, Royal Decree M/51)', 'country_code': 'SA', 'country_name': 'Saudi Arabia',
        'law_reference': 'Saudi Labour Law, Royal Decree No. M/51 of 1426H (2005) as amended (amendments in force 19 Feb 2025)',
        'weekend_days': [FRI, SAT], 'daily_hours': Decimal('8'), 'weekly_hours': Decimal('48'), 'ramadan_daily_hours': Decimal('6'),
        'annual_leave': {'first_year_after_months': 12, 'first_year_days_per_month': 0, 'days': 21, 'steps': [{'after_years': 5, 'days': 30}], 'basis': 'calendar',
                         'note': 'Art. 109: 21 days a year, 30 days after 5 years of continuous service.'},
        'sick_leave': [[30, 100], [60, 75], [30, 0]],
        'maternity': {'days': 84, 'full_pay_days': 84, 'half_pay_days': 0, 'note': 'Art. 151 (2025): 12 weeks fully paid.'},
        'paternity_days': 3,
        'gratuity': {'basis': 'last_wage', 'min_service_years': 2, 'slabs': [{'from_year': 1, 'to_year': 5, 'days': 15}, {'from_year': 6, 'to_year': None, 'days': 30}],
                     'cap_years_of_pay': None, 'resignation_share': [{'from_year': 2, 'to_year': 5, 'share': 0.3333}, {'from_year': 5, 'to_year': 10, 'share': 0.6667}, {'from_year': 10, 'to_year': None, 'share': 1}],
                     'note': 'Art. 84: half a month’s wage a year for the first 5 years, a full month after. Art. 85: on resignation 1/3 (2–5 years), 2/3 (5–10 years), full after 10.'},
        'overtime': {'normal': 150, 'night': 150, 'rest_day': 150, 'holiday': 150, 'max_hours_per_day': 3,
                     'note': 'Art. 107: hourly wage + 50% of the basic wage.'},
        'notice_period': {'min_days': 30, 'max_days': 60, 'note': 'Indefinite contracts: employer 60 days, employee 30 days (2025).'},
        'probation_max_months': 6, 'public_holiday_source': 'HRSD decisions: Eid al-Fitr, Eid al-Adha, Founding Day (22 Feb), National Day (23 Sep)',
        'wps_notes': 'Wage Protection Program (Mudad) with the Ministry of Human Resources; GOSI contributions.',
        'notes': 'Probation up to 90 days, extendable to 180 days by written agreement.',
    },
    {
        'code': 'QA-LL14-2004', 'name': 'Qatar (Labour Law No. 14 of 2004)', 'country_code': 'QA', 'country_name': 'Qatar',
        'law_reference': 'Qatar Labour Law No. 14 of 2004 as amended',
        'weekend_days': [FRI], 'daily_hours': Decimal('8'), 'weekly_hours': Decimal('48'), 'ramadan_daily_hours': Decimal('6'),
        'annual_leave': {'first_year_after_months': 12, 'first_year_days_per_month': 0, 'days': 21, 'steps': [{'after_years': 5, 'days': 28}], 'basis': 'calendar',
                         'note': 'Art. 79: 3 weeks a year, 4 weeks after 5 years.'},
        'sick_leave': [[14, 100], [28, 50], [42, 0]],
        'maternity': {'days': 50, 'full_pay_days': 50, 'half_pay_days': 0, 'note': 'Art. 96: 50 days full pay after one year of service.'},
        'paternity_days': 0,
        'gratuity': {'basis': 'basic', 'min_service_years': 1, 'slabs': [{'from_year': 1, 'to_year': None, 'days': 21}], 'cap_years_of_pay': None,
                     'note': 'Art. 54: at least 3 weeks’ basic wage for each year of service.'},
        'overtime': {'normal': 125, 'night': 150, 'night_window': '21:00-06:00', 'rest_day': 150, 'holiday': 150, 'max_hours_per_day': 2},
        'notice_period': {'min_days': 30, 'max_days': 60, 'note': 'Up to 5 years of service: 1 month; more: 2 months.'},
        'probation_max_months': 6, 'public_holiday_source': 'Emiri decision / Ministry of Labour announcements',
        'wps_notes': 'Wage Protection System of the Ministry of Labour (SIF file through the bank).', 'notes': '',
    },
    {
        'code': 'OM-RD53-2023', 'name': 'Oman (Labour Law, Royal Decree 53/2023)', 'country_code': 'OM', 'country_name': 'Oman',
        'law_reference': 'Omani Labour Law issued by Royal Decree No. 53/2023',
        'weekend_days': [FRI, SAT], 'daily_hours': Decimal('9'), 'weekly_hours': Decimal('45'), 'ramadan_daily_hours': Decimal('6'),
        'annual_leave': {'first_year_after_months': 6, 'first_year_days_per_month': 2.5, 'days': 30, 'steps': [], 'basis': 'calendar',
                         'note': '30 days a year after 6 months of service.'},
        'sick_leave': [[21, 100], [14, 75], [35, 50], [112, 35]],
        'maternity': {'days': 98, 'full_pay_days': 98, 'half_pay_days': 0}, 'paternity_days': 7,
        'gratuity': {'basis': 'basic', 'min_service_years': 1, 'slabs': [{'from_year': 1, 'to_year': None, 'days': 30}], 'cap_years_of_pay': None,
                     'note': 'Expatriates: one month’s basic wage a year (Omanis are covered by social insurance).'},
        'overtime': {'normal': 125, 'night': 150, 'rest_day': 150, 'holiday': 150, 'max_hours_per_day': 3},
        'notice_period': {'min_days': 30, 'max_days': 90}, 'probation_max_months': 6,
        'public_holiday_source': 'Royal decrees / Ministry of Labour announcements',
        'wps_notes': 'Wage Protection System of the Ministry of Labour through the bank.', 'notes': '',
    },
    {
        'code': 'BH-LL36-2012', 'name': 'Bahrain (Labour Law No. 36 of 2012)', 'country_code': 'BH', 'country_name': 'Bahrain',
        'law_reference': 'Bahrain Labour Law for the Private Sector, Law No. 36 of 2012 as amended',
        'weekend_days': [FRI, SAT], 'daily_hours': Decimal('8'), 'weekly_hours': Decimal('48'), 'ramadan_daily_hours': Decimal('6'),
        'annual_leave': {'first_year_after_months': 12, 'first_year_days_per_month': 2.5, 'days': 30, 'steps': [], 'basis': 'calendar'},
        'sick_leave': [[15, 100], [20, 50], [20, 0]],
        'maternity': {'days': 60, 'full_pay_days': 60, 'half_pay_days': 0, 'note': 'Plus up to 15 days unpaid.'}, 'paternity_days': 1,
        'gratuity': {'basis': 'last_wage', 'min_service_years': 1, 'slabs': [{'from_year': 1, 'to_year': 3, 'days': 15}, {'from_year': 4, 'to_year': None, 'days': 30}],
                     'cap_years_of_pay': None, 'note': 'Since March 2024 the end-of-service benefit of expatriates is paid through the SIO.'},
        'overtime': {'normal': 125, 'night': 150, 'rest_day': 150, 'holiday': 200, 'max_hours_per_day': 2},
        'notice_period': {'min_days': 30, 'max_days': 90}, 'probation_max_months': 3,
        'public_holiday_source': 'Prime Minister’s Office announcements', 'wps_notes': 'LMRA Wage Protection System.', 'notes': '',
    },
    {
        'code': 'KW-LL6-2010', 'name': 'Kuwait (Labour Law No. 6 of 2010)', 'country_code': 'KW', 'country_name': 'Kuwait',
        'law_reference': 'Kuwait Private Sector Labour Law No. 6 of 2010 as amended',
        'weekend_days': [FRI], 'daily_hours': Decimal('8'), 'weekly_hours': Decimal('48'), 'ramadan_daily_hours': Decimal('6'),
        'annual_leave': {'first_year_after_months': 9, 'first_year_days_per_month': 0, 'days': 30, 'steps': [], 'basis': 'working'},
        'sick_leave': [[15, 100], [10, 75], [10, 50], [10, 25], [30, 0]],
        'maternity': {'days': 70, 'full_pay_days': 70, 'half_pay_days': 0}, 'paternity_days': 0,
        'gratuity': {'basis': 'last_wage', 'min_service_years': 1, 'slabs': [{'from_year': 1, 'to_year': 5, 'days': 15}, {'from_year': 6, 'to_year': None, 'days': 30}],
                     'cap_years_of_pay': 1.5},
        'overtime': {'normal': 125, 'night': 125, 'rest_day': 150, 'holiday': 200, 'max_hours_per_day': 2},
        'notice_period': {'min_days': 90, 'max_days': 90}, 'probation_max_months': 3,
        'public_holiday_source': 'Civil Service Commission / Council of Ministers announcements', 'wps_notes': 'PAM salary transfer through the bank.',
        'notes': 'Probation up to 100 working days.',
    },
]


def load_standard(overwrite=False):
    """Adds the standard rule sets that are missing (or resets them with overwrite). Returns (created, updated) codes."""
    from .models import CountryPolicy
    created, updated = [], []
    for d in STANDARD:
        row = CountryPolicy.objects.filter(code=d['code']).first()
        if row is None:
            CountryPolicy.objects.create(is_standard=True, **d)
            created.append(d['code'])
        elif overwrite:
            for k, v in d.items():
                setattr(row, k, v)
            row.is_standard = True
            row.save()
            updated.append(d['code'])
    return created, updated


EMPLOYMENT_TYPES = [
    ('FT', 'Full time', True, 180, False), ('PT', 'Part time', True, 180, False), ('CT', 'Contract (fixed term)', True, 90, True),
    ('TMP', 'Temporary', False, 0, True), ('INT', 'Intern', False, 0, True),
]


def load_employment_types():
    from .models import EmploymentType
    out = []
    for code, name, grat, prob, end in EMPLOYMENT_TYPES:
        if not EmploymentType.objects.filter(code=code).exists() and not EmploymentType.objects.filter(name__iexact=name).exists():
            EmploymentType.objects.create(code=code, name=name, counts_for_gratuity=grat, probation_days=prob, has_end_date=end)
            out.append(code)
    return out
