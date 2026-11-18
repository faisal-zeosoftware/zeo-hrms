"""v1.73.15 – demo data of the apps beyond HR and payroll: CRM, Projects, Property, Maintenance, Assets, HSE, People,
HR services, Recruitment and Expenses – each where it fits the company's industry (INDUSTRIES[...]['apps'] in demo_data).

Rows are added to the demo workbook (demo_data.Book) as API sheets: setup rows (masters) first, then monthly rows dated on
their business day (@date) so dashboards show activity across the demo months. Every record is invented.
"""
import datetime

from .demo_data import COUNTRIES, INDUSTRIES, LEVELS, _n, _pick, _q, emp, branch, A, MA

D = datetime.date

# what each industry talks about: customers, projects, equipment, sites, incidents, observations, toolbox talks, deals
CONTENT = {
    'CON': dict(customers=['Marina Heights Developments', 'Desert Rose Real Estate', 'Gulf Logistics Park', 'Al Noor Schools Group'],
                projects=['Marina Heights – tower B structure', 'Logistics park – warehouse 3 fit-out', 'School campus – MEP works'],
                equipment=['Tower crane TC-1', 'Concrete pump', 'Diesel generator 250 kVA', 'Boom lift 20 m'],
                sites=['Marina Heights site', 'Logistics park site'],
                incidents=['Worker slipped on wet formwork', 'Near miss – unsecured load from crane', 'Minor cut while cutting rebar'],
                observations=['Missing edge protection on level 7', 'Scaffold tag out of date', 'Good housekeeping in the rebar yard', 'Blocked fire exit at store'],
                toolbox=['Working at height', 'Lifting operations', 'Heat stress in summer'],
                deals=['Villa compound – civil works', 'Retail mall – shell and core', 'Warehouse extension']),
    'FM': dict(customers=['Skyline Business Tower', 'Corniche Residences', 'Pearl Mall Management', 'Harbour Hotel'],
               projects=['Skyline Tower – annual FM contract', 'Corniche Residences – deep cleaning', 'Hotel chiller overhaul'],
               equipment=['Chiller CH-01', 'Fire pump set', 'Lift L-03', 'AHU level 4'],
               sites=['Skyline Business Tower', 'Corniche Residences'],
               incidents=['Technician burnt hand on hot pipe', 'Near miss – ladder slipped', 'Cleaner allergic reaction to chemical'],
               observations=['Chemical store not labelled', 'Panel room door left open', 'PPE worn correctly by all staff', 'Trip hazard – loose cable'],
               toolbox=['Electrical safety and LOTO', 'Chemical handling (COSHH)', 'Manual handling'],
               deals=['Office tower – soft services', 'School – MEP maintenance', 'Clinic – deep cleaning contract']),
    'PM': dict(customers=['Al Waha Owners Association', 'Crescent Holdings', 'Sunrise Investments', 'Blue Lagoon Owners'],
               projects=['Building handover – Palm Crest A', 'Annual service charge budget'],
               equipment=['Lift A-1', 'Fire alarm panel', 'Swimming pool pump', 'Water tank pump'],
               sites=['Palm Crest A', 'Palm Crest B'],
               incidents=['Resident slipped in lobby', 'Near miss – pool gate left open'],
               observations=['Lobby floor wet without sign', 'Gym equipment in good order', 'Parking lights not working'],
               toolbox=['Fire evacuation drill', 'Working on roofs'],
               deals=['2-bed apartment – yearly lease', 'Retail shop – ground floor', 'Office unit – 5th floor']),
    'OG': dict(customers=['Eastern Petrochem Co.', 'Northern Gas Processing', 'Coastal Refinery Co.', 'Gulf Pipeline Services'],
               projects=['Well W-14 workover', 'Pipeline inspection – section 3', 'Compressor station shutdown'],
               equipment=['Mud pump MP-2', 'Drilling rig R-7', 'Air compressor', 'Mobile crane 50 t'],
               sites=['Rig R-7 location', 'Compressor station 2'],
               incidents=['H2S alarm – area evacuated', 'Hand injury during pipe handling', 'Near miss – dropped object from derrick'],
               observations=['Gas detector not bump-tested', 'Permit board up to date', 'Hose whip checks missing', 'Good pre-job briefing'],
               toolbox=['H2S awareness', 'Dropped objects', 'Permit to work'],
               deals=['Drilling services – 12 months', 'Rig maintenance contract', 'Well testing services']),
    'RET': dict(customers=['Corporate gifting – Al Faisal Group', 'Staff uniforms – Desert Hotels', 'Bulk order – City Schools', 'Loyalty partner – Gulf Air Miles'],
                projects=['New store opening – Jeddah mall', 'Store refit – Riyadh park'],
                equipment=['POS terminal 1', 'Cold room compressor', 'CCTV recorder', 'Forklift'],
                sites=['Riyadh store', 'Jeddah store'],
                incidents=['Customer slipped near entrance', 'Box fell from top shelf'],
                observations=['Fire extinguisher blocked by stock', 'Clean and tidy stockroom', 'Exit sign light off'],
                toolbox=['Safe stacking of stock', 'Fire safety in the store'],
                deals=['Corporate gift cards – Q1', 'Wholesale order – school uniforms', 'Franchise enquiry – Dammam']),
    'HC': dict(customers=['Gulf Health Insurance', 'Al Rayyan Corporate Clinic', 'City Schools – health checks', 'Doha Sports Club'],
               projects=['Paediatrics wing opening', 'Laboratory accreditation'],
               equipment=['X-ray machine', 'Ultrasound scanner', 'Autoclave', 'Patient monitor PM-4'],
               sites=['Main clinic'],
               incidents=['Needle-stick injury – nurse', 'Patient fall in waiting area'],
               observations=['Sharps bin over-filled', 'Hand hygiene audit passed', 'Oxygen cylinder not chained'],
               toolbox=['Infection control', 'Sharps handling'],
               deals=['Corporate health checks – 200 staff', 'School vaccination drive', 'Insurance network agreement']),
    'MFG': dict(customers=['Muscat Building Materials', 'Sohar Steel Trading', 'Gulf Packaging Co.', 'Al Batinah Contractors'],
                projects=['Line 2 capacity upgrade', 'ISO 9001 recertification'],
                equipment=['CNC machine M-01', 'Injection moulding press', 'Air compressor', 'Overhead crane 10 t'],
                sites=['Sohar plant'],
                incidents=['Finger caught in press guard', 'Near miss – forklift and pedestrian', 'Oil spill near line 1'],
                observations=['Machine guard removed', 'Good 5S on line 2', 'Forklift seat belt not used'],
                toolbox=['Machine guarding', 'Forklift safety', 'Hearing protection'],
                deals=['Annual supply – precast parts', 'Export order – Qatar', 'Packaging supply contract']),
    'FIN': dict(customers=['Seef Trading Co.', 'Manama Hospitality Group', 'Gulf Tech Ventures', 'Al Muharraq Contractors'],
                projects=['Core banking upgrade', 'Branch digital onboarding'],
                equipment=['Server rack 1', 'Cash counting machine', 'UPS 20 kVA'],
                sites=['Head office'],
                incidents=['Visitor tripped on carpet edge'],
                observations=['Fire drill completed', 'Server room door propped open'],
                toolbox=['Office ergonomics', 'Fire evacuation'],
                deals=['Working capital facility – trading', 'Equipment finance – hotel group', 'SME credit line']),
    'EDU': dict(customers=['Parents – Grade 1 admissions', 'Corporate partner – Salmiya Mall', 'Sports academy partnership', 'Summer camp families'],
                projects=['New science lab', 'Accreditation visit preparation'],
                equipment=['School bus 1', 'School bus 2', 'Science lab fume hood', 'Projector – hall'],
                sites=['Main campus'],
                incidents=['Student fell in playground', 'Bus minor collision in car park'],
                observations=['Fire exit clear', 'Playground surface damaged'],
                toolbox=['Child safeguarding', 'Bus safety'],
                deals=['Grade 1 admission – sibling', 'Corporate scholarship', 'After-school sports programme']),
    'IT': dict(customers=['Deccan Retail Ltd', 'Konkan Logistics', 'Sahyadri Hospitals', 'Malabar Finance'],
               projects=['Retail ERP roll-out', 'Logistics mobile app', 'Hospital cloud migration'],
               equipment=['Build server', 'Firewall', 'Conference room display'],
               sites=['Bengaluru office'],
               incidents=['Employee tripped on cable in office'],
               observations=['Cable management needed in bay 3', 'Fire drill completed'],
               toolbox=['Office ergonomics'],
               deals=['ERP implementation – retail chain', 'Annual support – logistics app', 'Cloud migration – hospital']),
}


def _d(y, m, day):
    return D(y, m, min(day, 28)).isoformat()


def _past(months):
    return [(y, m, last) for (y, m, last) in months]


def add(B, spec, level, today, ctx, rows, months, br, br_of):
    key, cc, ind, name, code, cities = spec
    C, I, T = COUNTRIES[cc], INDUSTRIES[ind], CONTENT[ind]
    apps = set(I['apps']) & set(ctx.get('apps') or I['apps'])
    big = LEVELS[level]['size'] == 'full'
    b0 = list(br)[0]
    gm = rows[0]['code']
    heads = [r['code'] for r in rows if 'head' in r['flags']]
    staff = [r['code'] for r in rows if 'gm' not in r['flags']]
    fx = C['fx']
    money = lambda v: int(round(v * fx / 10.0)) * 10 or 10   # noqa: E731
    mon = [m for m in months]

    def at(k, day=10):   # the business day of the k-th record, spread over the demo months
        y, m, last = mon[k % len(mon)]
        return _d(y, m, min(day, last))

    # ------------------------------------------------------------------ People: awards, recognitions, objectives
    people_rows = [{'@ref': 'AWD1', '@endpoint': '/zeo-people/api/r/award/', '@body': {'code': f'{code}-STAR', 'name': 'Star performer', 'points': 20,
                                                                                         'description': 'Went beyond the job', 'who_can_give': 'managers'}}]
    rec = []
    for k, c in enumerate(staff[:6 if big else 3]):
        rec.append({'@month': f'{mon[k % len(mon)][0]}-{mon[k % len(mon)][1]:02d}', '@date': at(k, 18), '@as': next((r['mgr'] for r in rows if r['code'] == c), gm),
                    '@endpoint': '/zeo-people/api/r/recognition/', '@body': {'employee': emp(c), 'award': '{ref:AWD1}',
                                                                              'message': 'Thank you for the extra effort this month.'}})
    p0 = D(mon[0][0], mon[0][1], 1)
    okr = [{'@ref': 'OKR1', '@endpoint': '/zeo-people/api/r/okr/', '@body': {'title': f'Grow {I["title"].lower()} revenue by 15 %', 'level': 'company',
                                                                            'owner': emp(gm), 'period_start': p0.isoformat(), 'period_end': D(p0.year, 12, 31).isoformat() if p0.month < 12 else D(p0.year + 1, 12, 31).isoformat()}},
           {'@endpoint': '/zeo-people/api/r/key_result/', '@body': {'objective': '{ref:OKR1}', 'title': 'New customers signed', 'kind': 'number',
                                                                     'start_value': 0, 'target_value': 12, 'owner': emp(heads[0] if heads else gm)}},
           {'@endpoint': '/zeo-people/api/r/key_result/', '@body': {'objective': '{ref:OKR1}', 'title': 'Customer satisfaction', 'kind': 'percent',
                                                                     'start_value': 70, 'target_value': 90, 'owner': emp(gm)}}]
    B.nsheet('People – setup', 'action', '', 'setup', A, people_rows + okr, 'People › Recognition awards and company objectives', 'People', 'People')
    B.nsheet('People – recognitions', 'action', '', 'monthly', MA, rec, 'People › Recognitions given by the managers', 'People', 'People')

    # ------------------------------------------------------------------ HR services: knowledge base, document renewals, a case
    hr = [{'@endpoint': '/zeo-hr/api/r/kb/', '@body': {'title': 'How to apply for annual leave', 'body': 'Open Self service › Leave › New request, '
                                                       'choose the dates and send it to your manager.', 'audience': 'all'}},
          {'@endpoint': '/zeo-hr/api/r/kb/', '@body': {'title': 'Salary certificate requests', 'body': 'Ask for a salary certificate from Self service › My letters.',
                                                       'audience': 'all'}}]
    exp_soon = (today + datetime.timedelta(days=25)).isoformat()
    hr.append({'@endpoint': '/zeo-hr/api/r/renewal/', '@body': {'employee': emp(rows[min(3, len(rows) - 1)]['code']), 'document': 'Passport',
                                                                 'expiry': exp_soon, 'notes': 'Renewal started – demo'}})
    B.nsheet('HR services', 'action', '', 'setup', A, hr, 'HR › Knowledge base and document renewals', 'HR services', 'HR')
    if big and len(staff) > 6:
        case = [{'@month': f'{mon[-2][0]}-{mon[-2][1]:02d}' if len(mon) > 1 else f'{mon[0][0]}-{mon[0][1]:02d}', '@date': at(len(mon) - 2, 12),
                 '@endpoint': '/zeo-hr/api/r/case/', '@body': {'employee': emp(staff[6]), 'category': 'attendance', 'title': 'Repeated late arrival',
                                                              'description': 'Late on several days this month – first warning (demo).'}}]
        B.nsheet('HR cases', 'action', '', 'monthly', MA, case, 'HR › Disciplinary cases', 'HR cases', 'HR')

    # ------------------------------------------------------------------ Recruitment: an opening and its candidates
    job = rows[-1]['desig']
    rc = [{'@ref': 'JOB1', '@endpoint': '/recruitment/api/job-openings/', '@body': {'title': job, 'description': f'We are hiring a {job.lower()} for our {cities[0]} team.'}}]
    for k in range(4 if big else 2):
        males, females, lasts = (['Adam', 'Yusuf', 'Kiran', 'Tariq'], ['Leila', 'Asha', 'Nadia', 'Rita'], ['Rahman', 'Thomas', 'Saleh', 'Fernandes'])
        fn = (males + females)[k % 8]
        ln = lasts[k % 4]
        rc.append({'@endpoint': '/recruitment/api/candidates/', '@body': {'first_name': fn, 'last_name': ln,
                                                                          'email': f'{fn.lower()}.{ln.lower()}.{code.lower()}@mail.example'}})
    B.nsheet('Recruitment', 'action', '', 'setup', A, rc, 'Recruitment › Job openings and candidates', 'Recruitment', 'Recruitment')

    # ------------------------------------------------------------------ Expenses: claims of the month
    ex = []
    from .demo_data import managers
    claimers = [c for c in managers(rows) if c != gm] or [gm]   # people with a login claim their own expenses
    for k, (y, m, last) in enumerate(mon):
        for t in range(2 if big else 1):
            c = claimers[(k * 3 + t) % len(claimers)]
            ex.append({'@month': f'{y}-{m:02d}', '@date': _d(y, m, min(last, 6 + t * 7)), '@as': c, '@endpoint': '/expense/api/expenses/',
                       '@body': {'category': _q('ExpenseManagement.ExpenseCategory', code='TAXI'), 'amount': money(120 + 35 * t + 10 * k),
                                 'expense_date': _d(y, m, min(last, 5 + t * 7)), 'description': _pick(c + str(k), ['Taxi to customer site', 'Fuel – site visit', 'Team lunch with customer', 'Parking at client office'])}})
    B.nsheet('Expenses', 'action', '', 'monthly', MA, ex, 'Expenses › Claims by employees', 'Expenses', 'Expenses')

    # ------------------------------------------------------------------ CRM: accounts, contacts, leads, opportunities, activities
    if 'crm' in apps:
        crm = []
        for k, cu in enumerate(T['customers']):
            crm.append({'@ref': f'ACC{k}', '@endpoint': '/zeo-crm/api/r/account/', '@body': {'name': cu, 'kind': 'customer', 'industry': I['title'],
                        'city': cities[k % len(cities)], 'country': C['country'], 'phone': C['phone'] + _n(cu, 7), 'email': f'info@{_n(cu, 5)}.example',
                        'owner': emp(heads[0] if heads else gm)}})
            males, females, lasts = (['Sameer', 'Hassan', 'Ravi', 'Daniel'], ['Huda', 'Sonia', 'Mira', 'Laura'], ['Kapoor', 'Saeed', 'Mathew', 'Ali'])
            crm.append({'@ref': f'CON{k}', '@endpoint': '/zeo-crm/api/r/contact/', '@body': {'name': f'{(males + females)[k]} {lasts[k]}', 'account': '{ref:ACC' + str(k) + '}',
                        'contact_type': 'customer', 'job_title': 'Procurement manager', 'email': f'buyer{k}@{_n(cu, 5)}.example', 'phone': C['mobile'] + _n(cu + 'm', 7)}})
        B.nsheet('CRM – customers', 'action', '', 'setup', A, crm, 'CRM › Accounts and contacts', 'CRM', 'CRM')
        deals, acts = [], []
        for k, title in enumerate(T['deals'] * (2 if big else 1)):
            y, m, last = mon[k % len(mon)]
            mo = f'{y}-{m:02d}'
            ref = f'OPP{k}'
            deals.append({'@month': mo, '@ref': ref, '@date': _d(y, m, 4 + k), '@endpoint': '/zeo-crm/api/r/opportunity/',
                          '@body': {'title': title, 'contact': '{ref:CON' + str(k % len(T['customers'])) + '}', 'account': '{ref:ACC' + str(k % len(T['customers'])) + '}',
                                    'pipeline': _q('ZeoCRM.Pipeline', is_active='true'), 'value': money(40000 + 25000 * (k % 4)),
                                    'expected_close': (D(y, m, 1) + datetime.timedelta(days=60)).isoformat(), 'owner': emp(heads[0] if heads else gm),
                                    **{f'x_{k}': v for k, v in (ctx.get('opp_x') or {}).items()}}})   # industry fields a won deal needs
            acts.append({'@month': mo, '@date': _d(y, m, 6 + k), '@endpoint': '/zeo-crm/api/r/activity/',
                         '@body': {'kind': _pick(title, ['call', 'meeting', 'email', 'site_visit']), 'subject': f'Follow-up – {title}', 'opportunity': '{ref:' + ref + '}',
                                   'status': 'done' if (y, m) != (today.year, today.month) else 'open', 'owner': emp(heads[0] if heads else gm)}})
            if k % 3 == 0 and (y, m) != (today.year, today.month):
                acts.append({'@month': mo, '@date': _d(y, m, min(last, 20 + k % 5)), '@endpoint': '/zeo-crm/api/r/opportunity/{ref:' + ref + '}/do/won/', '@body': {}})
        leads = []
        for k in range(6 if big else 3):
            y, m, last = mon[k % len(mon)]
            nm = ['Faris Al Amri', 'Neha Joshi', 'Omar Khalil', 'Sara Ibrahim', 'Vivek Menon', 'Lina Haddad'][(k + len(code)) % 6]
            leads.append({'@month': f'{y}-{m:02d}', '@date': _d(y, m, 3 + k * 3), '@endpoint': '/zeo-crm/api/r/lead/',
                          '@body': {'name': nm, 'company_name': f'{nm.split()[-1]} Trading', 'email': f'lead{k}.{code.lower()}@mail.example',
                                    'phone': C['mobile'] + _n(nm + code, 7), 'city': cities[0], 'channel': _pick(nm, ['web', 'phone', 'referral', 'walkin', 'linkedin']),
                                    'interest': T['deals'][k % len(T['deals'])], 'value': money(15000 + 5000 * k), 'priority': _pick(nm + 'p', ['low', 'medium', 'high'])}})
        B.nsheet('CRM – leads', 'action', '', 'monthly', MA, leads, 'CRM › Leads from the website, phone and referrals', 'CRM', 'CRM')
        B.nsheet('CRM – deals', 'action', '', 'monthly', MA, deals, 'CRM › Opportunities', 'CRM', 'CRM')
        B.nsheet('CRM – activities', 'action', '', 'monthly', MA, acts, 'CRM › Calls, meetings and won deals', 'CRM', 'CRM')

    # ------------------------------------------------------------------ Assets: categories, locations, assets handed to staff
    if 'assets' in apps:
        asx = [{'@ref': 'ACAT1', '@endpoint': '/zeo-assets/api/r/category/', '@body': {'code': f'{code}-IT', 'name': 'IT equipment', 'life_months': 36}},
               {'@ref': 'ACAT2', '@endpoint': '/zeo-assets/api/r/category/', '@body': {'code': f'{code}-EQ', 'name': 'Equipment and vehicles', 'life_months': 60}},
               {'@ref': 'ALOC1', '@endpoint': '/zeo-assets/api/r/location/', '@body': {'code': f'{code}-HO', 'name': f'{cities[0]} office', 'kind': 'building',
                                                                                     'custodian': emp(gm)}}]
        items = [('Laptop – Dell Latitude', 'ACAT1', 4200), ('Laptop – Lenovo ThinkPad', 'ACAT1', 3900), ('Mobile phone', 'ACAT1', 2500)] + \
                [(e, 'ACAT2', 45000) for e in T['equipment'][:3]]
        for k, (nm, cat, cost) in enumerate(items):
            asx.append({'@ref': f'AST{k}', '@endpoint': '/zeo-assets/api/r/asset/', '@body': {'name': nm, 'category': '{ref:' + cat + '}', 'location': '{ref:ALOC1}',
                        'serial_number': f'SN{_n(code + nm, 8)}', 'purchase_date': D(today.year - 1, 1 + k, 10).isoformat(), 'purchase_cost': money(cost),
                        'manufacturer': nm.split('–')[-1].strip().split()[0] if '–' in nm else 'Demo'}})
        B.nsheet('Assets', 'action', '', 'setup', A, asx, 'Assets › Categories, locations and the asset register', 'Assets', 'Assets')

    # ------------------------------------------------------------------ Maintenance: locations, assets, plans, work orders
    if 'maintenance' in apps:
        mx = [{'@ref': 'MLOC1', '@endpoint': '/zeo-maintenance/api/r/location/', '@body': {'code': f'{code}-L1', 'name': T['sites'][0]}},
              {'@ref': 'MVEN1', '@endpoint': '/zeo-maintenance/api/r/vendor/', '@body': {'code': f'{code}-V1', 'name': 'Rapid Fix Technical Services', 'trade': 'MEP',
                                                                                       'phone': C['phone'] + _n(code + 'v', 7)}}]
        for k, e in enumerate(T['equipment']):
            mx.append({'@ref': f'MAS{k}', '@endpoint': '/zeo-maintenance/api/r/asset/', '@body': {'code': f'{code}-EQ{k + 1}', 'name': e, 'location': '{ref:MLOC1}',
                       'make': 'Demo', 'serial': f'S{_n(code + e, 7)}', 'installed_on': D(today.year - 2, 3, 1).isoformat(), 'status': 'working'}})
        nxt = (today + datetime.timedelta(days=12)).isoformat()
        mx.append({'@endpoint': '/zeo-maintenance/api/r/plan/', '@body': {'name': f'Quarterly service – {T["equipment"][0]}', 'asset': '{ref:MAS0}', 'location': '{ref:MLOC1}',
                                                                          'every': 3, 'unit': 'months', 'next_due': nxt}})
        B.nsheet('Maintenance – setup', 'action', '', 'setup', A, mx, 'Maintenance › Locations, vendors, assets and preventive plans', 'Maintenance', 'Maintenance')
        wo = []
        techs = [r['code'] for r in rows if 'ot' in r['flags']] or staff
        WO = '/zeo-maintenance/api/r/work_order/'
        for k, (y, m, last) in enumerate(mon):
            current = (y, m) == (today.year, today.month)
            for t in range(2 if big else 1):
                e = T['equipment'][(k + t) % len(T['equipment'])]
                ref = f'WO{k}{t}'
                d0 = min(last, 8 + t * 9)
                mo = f'{y}-{m:02d}'
                wo.append({'@month': mo, '@ref': ref, '@date': _d(y, m, d0), '@endpoint': WO,
                           '@body': {'title': _pick(e + str(k) + str(t), ['Not working – inspect', 'Strange noise', 'Leak found', 'Service due', 'Alarm keeps tripping']) + f' – {e}',
                                     'asset': '{ref:MAS' + str((k + t) % len(T['equipment'])) + '}', 'location': '{ref:MLOC1}',
                                     'category': _q('ZeoMaintenance.Category', id__gt='0'),
                                     'priority': _pick(e + str(k), ['normal', 'high', 'low', 'urgent']), 'source': 'staff', 'requester': 'Demo requester'}})
                # the life of the work order: assigned the same day, started, finished and closed – this month's stay open
                steps = [('assign', {'technician': emp(techs[(k + t) % len(techs)])}, 0)]
                if not current or t == 0:
                    steps.append(('start', {}, 1))
                if not current:
                    steps += [('done', {'work_note': 'Checked and fixed; tested with the user (demo).'}, 2), ('close', {'rating': 4 + (k + t) % 2}, 3)]
                for act, body, plus in steps:
                    wo.append({'@month': mo, '@date': _d(y, m, min(last, d0 + plus)), '@endpoint': WO + '{ref:' + ref + '}/do/' + act + '/', '@body': body})
        B.nsheet('Maintenance – work orders', 'action', '', 'monthly', MA, wo, 'Maintenance › Work orders', 'Maintenance', 'Maintenance')

    # ------------------------------------------------------------------ HSE: sites, observations, toolbox talks, incidents, inspections
    if 'hse' in apps:
        hs = []
        for k, s in enumerate(T['sites']):
            hs.append({'@ref': f'SITE{k}', '@endpoint': '/zeo-hse/api/masters/sites/', '@body': {'code': f'{code}-S{k + 1}', 'name': s, 'branch': branch(list(br)[k % len(br)]),
                                                                                            'manager': emp(heads[0] if heads else gm), 'risk_class': 'high' if ind in ('OG', 'CON') else 'medium'}})
        B.nsheet('HSE – sites', 'action', '', 'setup', A, hs, 'HSE › Sites', 'HSE', 'HSE')
        ob, tb, inc = [], [], []
        hse_off = next((r['code'] for r in rows if 'HSE' in r['dept'] or 'QHSE' in r['dept']), heads[0] if heads else gm)
        for k, (y, m, last) in enumerate(mon):
            mo = f'{y}-{m:02d}'
            for t in range(2 if big else 1):
                txt = T['observations'][(k * 2 + t) % len(T['observations'])]
                ob.append({'@month': mo, '@date': _d(y, m, min(last, 4 + t * 10)), '@as': hse_off, '@endpoint': '/zeo-hse/api/observations/',
                           '@body': {'description': txt, 'kind': 'safe_act' if 'good' in txt.lower() or 'passed' in txt.lower() or 'completed' in txt.lower() or 'order' in txt.lower() else 'unsafe_condition',
                                     'site': '{ref:SITE' + str(t % len(T['sites'])) + '}'}})
            tb.append({'@month': mo, '@date': _d(y, m, min(last, 2)), '@as': hse_off, '@endpoint': '/zeo-hse/api/toolbox/',
                       '@body': {'topic': T['toolbox'][k % len(T['toolbox'])], 'site': '{ref:SITE0}', 'date': _d(y, m, min(last, 2)),
                                 'attendee_count': 12, 'key_points': 'Talk given to the morning shift (demo).'}})
            if k % 2 == 0 or big:
                ttl = T['incidents'][k % len(T['incidents'])]
                inc.append({'@month': mo, '@date': _d(y, m, min(last, 15)), '@endpoint': '/zeo-hse/api/incidents/',
                            '@body': {'title': ttl, 'description': f'{ttl}. First aid given, area made safe (demo).', 'occurred_at': f'{_d(y, m, min(last, 15))}T10:30:00',
                                      'site': '{ref:SITE0}', 'treatment': 'none' if 'near miss' in ttl.lower() else 'first_aid', 'potential_severity': 2 + k % 3}})
        B.nsheet('HSE – observations', 'action', '', 'monthly', MA, ob, 'HSE › Observations', 'HSE', 'HSE')
        B.nsheet('HSE – toolbox talks', 'action', '', 'monthly', MA, tb, 'HSE › Toolbox talks', 'HSE', 'HSE')
        B.nsheet('HSE – incidents', 'action', '', 'monthly', MA, inc, 'HSE › Incidents and near misses', 'HSE', 'HSE')

    # ------------------------------------------------------------------ Projects: customers and projects
    if 'projects' in apps:
        pj = []
        for k, cu in enumerate(T['customers'][:3]):
            pj.append({'@ref': f'PCU{k}', '@endpoint': '/zeo-projects/api/customers/', '@body': {'name': cu, 'email': f'projects@{_n(cu, 5)}.example',
                                                                                               'phone': C['phone'] + _n(cu + 'p', 7)}})
        for k, title in enumerate(T['projects']):
            st = D(mon[0][0], mon[0][1], 5)
            pj.append({'@ref': f'PRJ{k}', '@date': st.isoformat(), '@endpoint': '/zeo-projects/api/projects/', '@body': {'title': title, 'customer': '{ref:PCU' + str(k % 3) + '}',
                       'start_date': st.isoformat(), 'end_date': (st + datetime.timedelta(days=150 + 30 * k)).isoformat(), 'manager': emp(heads[0] if heads else gm)}})
        B.nsheet('Projects', 'action', '', 'setup', A, pj, 'Projects › Customers and projects', 'Projects', 'Projects')

    # ------------------------------------------------------------------ Property (property management only): buildings, units, tenants, leases
    if 'property' in apps:
        px = [{'@ref': 'OWN1', '@endpoint': '/zeo-property/api/r/owner/', '@body': {'kind': 'company', 'name': T['customers'][1], 'email': 'owner@crescent.example',
                                                                                  'phone': C['phone'] + _n(code + 'own', 7)}}]
        for bk, bname in enumerate(T['sites'][:2]):
            px.append({'@ref': f'BLD{bk}', '@endpoint': '/zeo-property/api/r/building/', '@body': {'name': bname, 'kind': 'residential', 'subtype': 'tower',
                       'city': cities[0], 'owner': '{ref:OWN1}', 'floors': 12, 'year_built': 2018 + bk, 'manager': emp(heads[0] if heads else gm)}})
            for u in range(5 if big else 3):
                px.append({'@ref': f'UNT{bk}{u}', '@endpoint': '/zeo-property/api/r/unit/', '@body': {'building': '{ref:BLD' + str(bk) + '}', 'unit_no': f'{bk + 1}{u + 1:02d}',
                           'floor': str(u + 1), 'bedrooms': 1 + u % 3, 'bathrooms': 1 + u % 2, 'area': 750 + 250 * (u % 3), 'rent': money(55000 + 15000 * (u % 3)),
                           'owner': '{ref:OWN1}', 'status': 'available'}})
        B.nsheet('Property – portfolio', 'action', '', 'setup', A, px, 'Property › Owners, buildings and units', 'Property', 'Property')
        ls = []
        tenants = ['Rohan Kapoor', 'Mariam Saleh', 'James Carter', 'Aisha Rahman', 'Chen Wei', 'Fatima Noor']
        for k, tn in enumerate(tenants[:4 if big else 2]):
            y, m, last = mon[k % len(mon)]
            st = D(y, m, 1)
            unit = '{ref:UNT' + str(k % 2) + str(k // 2) + '}'
            ls.append({'@month': f'{y}-{m:02d}', '@ref': f'TEN{k}', '@date': _d(y, m, 1), '@endpoint': '/zeo-property/api/r/party/',
                       '@body': {'kind': 'tenant', 'name': tn, 'email': f'tenant{k}.{code.lower()}@mail.example', 'phone': C['mobile'] + _n(tn, 7)}})
            ls.append({'@month': f'{y}-{m:02d}', '@ref': f'LSE{k}', '@date': _d(y, m, 1), '@endpoint': '/zeo-property/api/r/lease/',
                       '@body': {'unit': unit, 'party': '{ref:TEN' + str(k) + '}', 'start': st.isoformat(), 'end': (D(st.year + 1, st.month, 1) - datetime.timedelta(days=1)).isoformat(),
                                 'annual_rent': money(60000 + 10000 * k), 'deposit': money(3000), 'payments': 4, 'method': 'cheque'}})
            ls.append({'@month': f'{y}-{m:02d}', '@date': _d(y, m, 2), '@endpoint': '/zeo-property/api/r/lease/{ref:LSE' + str(k) + '}/do/activate/', '@body': {}})
        B.nsheet('Property – leases', 'action', '', 'monthly', MA, ls, 'Property › Tenants and leases', 'Property', 'Property')
