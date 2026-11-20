"""v1.73.23 (pending item 157) – the nationalisation quota of every country pack and the law notes on the return ticket.

Before, the quota tab (Payroll extras › Emiratisation and Nafis) and its report used the UAE rule for every company: Emiratis
as a share of the skilled workforce (MOHRE skill levels 1 – 3) and MOHRE's text. A Saudi, Qatari, Omani, Bahraini or Kuwaiti
company now counts its own nationals against its whole workforce (their programmes set the share by activity and company
size – the company enters the figure from its authority's notice), with its own programme name and authority.

The return-ticket notes are what each labour law says about the ticket home at the end of employment, each from the law text as
summarised by at least two published sources (the sources are listed in the release notes). No GCC labour law requires an
annual ticket – that is a contract benefit.
"""

PROGRAMMES = {
    'AE': dict(programme='Emiratisation', authority='MOHRE', basis='skilled', national='UAE national', nationals='Emiratis',
               help='MOHRE (2026): companies of 50+ employees raise Emiratis in skilled jobs by 2 % a year (1 % each half year, deadlines '
                    '30 June and 31 December); from 1 July 2026 AED 10,000 a month (AED 120,000 a year) for each Emirati not hired. Enter '
                    'the target and the amount from MOHRE\'s notice – they change every year.'),
    'SA': dict(programme='Saudisation (Nitaqat)', authority='HRSD', basis='all', national='Saudi national', nationals='Saudis',
               help='Nitaqat (Ministry of Human Resources and Social Development, in Qiwa): the Saudi share a company needs depends on its '
                    'activity and size. Enter the share of your Nitaqat band from Qiwa as the target – the workforce counted is every '
                    'active employee.'),
    'QA': dict(programme='Qatarisation', authority='the Ministry of Labour', basis='all', national='Qatari national', nationals='Qataris',
               help='Qatarisation of private-sector jobs: the share is set for the company\'s sector by the Ministry of Labour. Enter the '
                    'target from the Ministry\'s notice – the workforce counted is every active employee.'),
    'OM': dict(programme='Omanisation', authority='the Ministry of Labour', basis='all', national='Omani national', nationals='Omanis',
               help='Omanisation: the Ministry of Labour sets the Omani share per sector and profession. Enter the target that applies to '
                    'your company – the workforce counted is every active employee.'),
    'BH': dict(programme='Bahrainisation', authority='LMRA', basis='all', national='Bahraini national', nationals='Bahrainis',
               help='Bahrainisation: the Bahraini share is set per activity (LMRA). Enter the share of your activity – the workforce counted '
                    'is every active employee.'),
    'KW': dict(programme='Kuwaitisation', authority='PAM', basis='all', national='Kuwaiti national', nationals='Kuwaitis',
               help='Kuwaitisation: the share of Kuwaiti nationals is set per sector by Council of Ministers decisions (PAM). Enter the '
                    'share of your sector – the workforce counted is every active employee.'),
}

NONE = dict(programme='Nationalisation', authority='', basis='all', national='National', nationals='Nationals',
            help='There is no nationalisation quota for this country in the country pack. You can still enter a company target of your own.')

AIR_TICKET = {
    'AE': dict(law='Federal Decree-Law 33/2021, Art. 13(12)',
               text='At the end of the contract the employer pays the worker\'s return ticket to the place of hire (or another place '
                    'agreed). Not when the worker joins another employer, or when the contract ended for a reason due to the worker – the '
                    'worker then pays. The law has no annual ticket: an annual or two-yearly ticket is a contract benefit.'),
    'SA': dict(law='Saudi Labour Law, Art. 40',
               text='The employer pays the return ticket to the worker\'s home country at the end of the work relationship. The worker pays '
                    'when unfit for work or when leaving without a legitimate reason. No annual ticket in the law – a contract benefit.'),
    'QA': dict(law='Qatar Labour Law No. 14 of 2004, Art. 57',
               text='When the contract ends the employer returns the worker, at its cost, to the place of recruitment or the worker\'s home '
                    'country. When the worker moves to a new employer, the new employer takes this over. No annual ticket in the law.'),
    'OM': dict(law='Omani Labour Law (Royal Decree 53/2023), Art. 14',
               text='The employer arranges and pays the worker\'s return to the home country within 60 days of the end of the employment. No '
                    'annual ticket in the law – a contract benefit.'),
    'BH': dict(law='Labour Market Regulation Law No. 19 of 2006, Art. 27',
               text='The last employer the work permit was issued for pays the worker\'s return to the place in the contract (or the home '
                    'country) when the employment ends or the permit expires or is cancelled; an employer who employed the worker without a '
                    'permit pays instead. A 2025 amendment that moves the cost of deporting absconding workers to the LMRA was passed by '
                    'Parliament – check whether it is in force. No annual ticket in the law.'),
    'KW': dict(law='Kuwait Labour Law No. 6 of 2010, Art. 10 (amended 2013)',
               text='The employer pays the worker\'s repatriation at the end of the employment. When the worker joins another employer, the new '
                    'employer pays it. Not owed when the worker caused the dismissal and can pay, or has another sponsor in Kuwait. No annual '
                    'ticket in the law.'),
}


def pack():
    try:
        from CountryPack import services as cp
        return cp.pack_code()
    except Exception:   # noqa: BLE001
        return 'AE'


def programme(p=None):
    p = (p or pack() or '').upper()
    d = dict(PROGRAMMES.get(p) or NONE)
    d['country'] = p
    d['applies'] = p in PROGRAMMES
    d['air_ticket'] = AIR_TICKET.get(p)
    return d
