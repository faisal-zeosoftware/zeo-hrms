"""v1.15.1 – Public holidays per country pack.

Used for the default holiday calendar of a new company (OrganisationManager.signals), by "Apply country defaults"
(CountryPack.services.apply_defaults) and by the `fix_holidays` management command.

Only dates we are sure of are listed. Islamic holidays follow the moon sighting: their Gregorian dates here are the
expected ones and carry the note EXPECTED_NOTE, so HR can confirm them when the government announces the dates.
Left out on purpose (not sure enough – add them by hand when announced):
  * AE: Commemoration Day (30 Nov) – not in the government's current list of official holidays (u.ae lists
    New Year, Eid Al Fitr, Arafah Day, Eid Al Adha, Hijri New Year, Prophet's birthday and National Day 2–3 Dec);
    in some years it is announced as an extra day around National Day.
  * SA / QA: Hijri New Year and the Prophet's birthday are not public holidays there; Saudi Arabia and Qatar give
    longer Eid breaks by announcement – only 1–3 Shawwal / Arafah + 10–12 Dhu al-Hijjah are listed.
  * QA: Sports Day (2nd Tuesday of February) – a moving date. OM: National Day (date changed recently).
  * KW / OM: Isra and Mi'raj. BH: Ashura (2 days).
  * Every year after 2027 (Islamic dates not listed yet).
"""
from datetime import date, timedelta

EXPECTED_NOTE = 'Expected - confirm on moon-sighting announcement'   # holiday.note is 50 characters max
EXPECTED_SUFFIX = ' (expected)'
YEARS = (2026, 2027)

# expected Gregorian dates (UAE moon sighting; 2026 Eid Al Fitr 20 Mar and Arafah / Eid Al Adha 26–29 May match the
# UAE announcements; the rest are astronomical expectations)
ISLAMIC = {
    2026: {'eid_fitr': (date(2026, 3, 20), 3), 'arafah': (date(2026, 5, 26), 1), 'eid_adha': (date(2026, 5, 27), 3),
           'hijri_new_year': (date(2026, 6, 16), 1), 'prophet_birthday': (date(2026, 8, 25), 1)},
    2027: {'eid_fitr': (date(2027, 3, 9), 3), 'arafah': (date(2027, 5, 15), 1), 'eid_adha': (date(2027, 5, 16), 3),
           'hijri_new_year': (date(2027, 6, 6), 1), 'prophet_birthday': (date(2027, 8, 14), 1)},
}
ISLAMIC_NAMES = {'eid_fitr': 'Eid al-Fitr', 'arafah': 'Arafat Day', 'eid_adha': 'Eid al-Adha',
                 'hijri_new_year': 'Islamic New Year', 'prophet_birthday': "Prophet's Birthday"}

# fixed (Gregorian) public holidays: (name, month, day, days)
FIXED = {
    'AE': [("New Year's Day", 1, 1, 1), ('National Day', 12, 2, 2)],
    'SA': [('Founding Day', 2, 22, 1), ('Saudi National Day', 9, 23, 1)],
    'QA': [('Qatar National Day', 12, 18, 1)],
    'OM': [],
    'BH': [("New Year's Day", 1, 1, 1), ('Labour Day', 5, 1, 1), ('National Day', 12, 16, 2)],
    'KW': [("New Year's Day", 1, 1, 1), ('National Day', 2, 25, 1), ('Liberation Day', 2, 26, 1)],
    'GEN': [("New Year's Day", 1, 1, 1)],
}
ALL_ISLAMIC = ['eid_fitr', 'arafah', 'eid_adha', 'hijri_new_year', 'prophet_birthday']
ISLAMIC_BY_PACK = {
    'AE': ALL_ISLAMIC, 'KW': ALL_ISLAMIC, 'BH': ALL_ISLAMIC, 'OM': ALL_ISLAMIC,
    'SA': ['eid_fitr', 'arafah', 'eid_adha'], 'QA': ['eid_fitr', 'arafah', 'eid_adha'],
    'GEN': [],
}

# holidays the old default seed (before v1.15.1) put into every company; `fix_holidays` removes them where the
# country pack does not have them. Lower-case names.
OLD_SEEDED_NON_COUNTRY = {'labor day', 'labour day', 'christmas day', 'christmas'}


def holidays_for(pack, years=None):
    """Sorted list of {key, name, description, start_date, end_date, expected, note} for the pack and years."""
    pack = pack if pack in FIXED else 'GEN'
    years = [y for y in (years or YEARS)]
    out = []
    for y in years:
        for name, m, d, days in FIXED[pack]:
            s = date(y, m, d)
            out.append({'key': f'{name}|{y}', 'name': name, 'description': f'{name} {y}', 'start_date': s,
                        'end_date': s + timedelta(days=days - 1), 'expected': False, 'note': ''})
        for k in ISLAMIC_BY_PACK[pack]:
            if y not in ISLAMIC:
                continue
            s, days = ISLAMIC[y][k]
            n = ISLAMIC_NAMES[k]
            out.append({'key': f'{n}|{y}', 'name': n, 'description': f'{n} {y}{EXPECTED_SUFFIX}', 'start_date': s,
                        'end_date': s + timedelta(days=days - 1), 'expected': True, 'note': EXPECTED_NOTE})
    out.sort(key=lambda h: (h['start_date'], h['name']))
    return out


def pack_holiday_names(pack):
    pack = pack if pack in FIXED else 'GEN'
    return {n.lower() for n, *_ in FIXED[pack]} | {ISLAMIC_NAMES[k].lower() for k in ISLAMIC_BY_PACK[pack]}


def seed_calendar(calendar, pack, from_year=None, user=None):
    """Adds the pack's holidays of from_year and the year after to a holiday calendar (Islamic holidays only for the
    years listed in ISLAMIC). A holiday that already exists in the
    calendar on the same start date is kept as it is. Returns (added, skipped) lists of dicts. Run inside the schema."""
    from django.apps import apps
    H = apps.get_model('calendars', 'holiday')
    from_year = from_year or calendar.year
    have = set(H.objects.filter(calendar=calendar).values_list('start_date', flat=True))
    added, skipped = [], []
    for h in holidays_for(pack, [from_year, from_year + 1]):
        if h['start_date'] in have:
            skipped.append(dict(h, reason='already in the calendar on this date'))
            continue
        desc = h['description']
        if H.objects.filter(description=desc).exists():      # description is unique in the company: name the calendar
            desc = f"{desc} – calendar {calendar.pk}"[:50]
            if H.objects.filter(description=desc).exists():
                skipped.append(dict(h, reason='a holiday with this name exists already'))
                continue
        h = dict(h, description=desc)
        H.objects.create(calendar=calendar, description=desc, start_date=h['start_date'],
                         end_date=h['end_date'], note=(h['note'] or None), created_by=user)
        added.append(h)
    return added, skipped


def _base_name(description):
    """'Christmas Day 2026 (expected)' -> 'christmas day'"""
    import re
    n = (description or '').lower().replace(EXPECTED_SUFFIX.strip(), '')
    n = re.sub(r'\b(19|20)\d\d\b', '', n)
    return re.sub(r'\s+', ' ', n).strip(' -–')


def fix_plan(pack, apply=False, today=None, user=None):
    """v1.15.1 – `fix_holidays`: in the current schema, for every holiday calendar in use (assigned to a branch,
    department, category or employee; all calendars when none is assigned):
      * remove: holidays the old default seed added that are not public holidays of the company's country
        (Christmas Day, Labor / Labour Day) – only these names, company-made holidays are never removed;
      * add: the country's holidays of the calendar year and the year after that are missing (same start date = present);
      * other: holidays that are not in the country list – listed only, kept.
    Dry run unless apply=True. Returns a dict per calendar."""
    from django.apps import apps
    from django.db import transaction
    H = apps.get_model('calendars', 'holiday')
    HC = apps.get_model('calendars', 'holiday_calendar')
    AH = apps.get_model('calendars', 'assign_holiday')
    today = today or date.today()
    names = pack_holiday_names(pack)
    used = set(AH.objects.values_list('holiday_model_id', flat=True))
    cals = HC.objects.filter(id__in=used) if used else HC.objects.all()
    out = []
    for cal in cals.order_by('year', 'id'):
        from_year = max(cal.year or today.year, today.year)
        wanted = holidays_for(pack, [from_year, from_year + 1])
        wanted_dates = {h['start_date'] for h in wanted}
        rows = list(H.objects.filter(calendar=cal).order_by('start_date', 'id'))
        remove = [r for r in rows if _base_name(r.description) in OLD_SEEDED_NON_COUNTRY and _base_name(r.description) not in names]
        rm_ids = {r.id for r in remove}
        have_dates = {r.start_date for r in rows if r.id not in rm_ids}
        add = [h for h in wanted if h['start_date'] not in have_dates]
        other = [r for r in rows if r.id not in rm_ids and r.start_date not in wanted_dates and _base_name(r.description) not in names]
        res = {'calendar_id': cal.id, 'calendar': cal.calendar_title, 'year': cal.year,
               'remove': [{'id': r.id, 'description': r.description, 'start_date': r.start_date} for r in remove],
               'add': [{'description': h['description'], 'start_date': h['start_date'], 'end_date': h['end_date'], 'expected': h['expected']} for h in add],
               'other': [{'id': r.id, 'description': r.description, 'start_date': r.start_date} for r in other],
               'added': [], 'skipped': [], 'removed': []}
        if apply:
            with transaction.atomic():
                for r in remove:
                    res['removed'].append(r.description)
                    r.delete()
                added, skipped = seed_calendar(cal, pack, from_year, user)
                res['added'] = [h['description'] for h in added]
                res['skipped'] = [f"{h['description']}: {h['reason']}" for h in skipped if h['start_date'] not in have_dates]
        out.append(res)
    return out
