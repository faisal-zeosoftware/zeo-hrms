"""
v1.53.2 – "Update the employee record" from a document that was read (AISettings.docread).

The reading's `record` (kind, number, issue, expiry, birth, name, surname, given, nationality, sex, emirates_id, uid) is
compared with the employee's identity details (EmployeeIdentity: passport, visa, Emirates ID, labour card, driving
licence, insurance) and the employee (date of birth, nationality, gender, name). Each line says whether the value is new,
the same or different; HR ticks the lines to save. The change history (Chatter RecordLog) records every changed field and
a note on the employee says which document the values came from.
"""
import re
from datetime import date

from django.apps import apps
from django.db import transaction
from rest_framework.exceptions import ValidationError

from Translations.i18n import tr

from . import services as S

M = apps.get_model

# MRZ nationality / country codes (ICAO 9303) → country name; the country name is matched to the nationality list
ISO3 = {
    'ARE': 'UNITED ARAB EMIRATES', 'SAU': 'SAUDI ARABIA', 'KWT': 'KUWAIT', 'QAT': 'QATAR', 'OMN': 'OMAN', 'BHR': 'BAHRAIN',
    'IND': 'INDIA', 'PAK': 'PAKISTAN', 'BGD': 'BANGLADESH', 'LKA': 'SRI LANKA', 'NPL': 'NEPAL', 'PHL': 'PHILIPPINES',
    'IDN': 'INDONESIA', 'MYS': 'MALAYSIA', 'THA': 'THAILAND', 'VNM': 'VIETNAM', 'CHN': 'CHINA', 'JPN': 'JAPAN', 'KOR': 'SOUTH KOREA',
    'SGP': 'SINGAPORE', 'AFG': 'AFGHANISTAN', 'IRN': 'IRAN', 'IRQ': 'IRAQ', 'JOR': 'JORDAN', 'LBN': 'LEBANON', 'SYR': 'SYRIA',
    'PSE': 'PALESTINE', 'YEM': 'YEMEN', 'EGY': 'EGYPT', 'SDN': 'SUDAN', 'LBY': 'LIBYA', 'TUN': 'TUNISIA', 'DZA': 'ALGERIA',
    'MAR': 'MOROCCO', 'ETH': 'ETHIOPIA', 'ERI': 'ERITREA', 'SOM': 'SOMALIA', 'KEN': 'KENYA', 'UGA': 'UGANDA', 'TZA': 'TANZANIA',
    'NGA': 'NIGERIA', 'GHA': 'GHANA', 'CMR': 'CAMEROON', 'ZAF': 'SOUTH AFRICA', 'ZWE': 'ZIMBABWE', 'TUR': 'TURKEY', 'RUS': 'RUSSIA',
    'UKR': 'UKRAINE', 'GBR': 'UNITED KINGDOM', 'GBD': 'UNITED KINGDOM', 'IRL': 'IRELAND', 'FRA': 'FRANCE', 'DEU': 'GERMANY',
    'D': 'GERMANY', 'ITA': 'ITALY', 'ESP': 'SPAIN', 'PRT': 'PORTUGAL', 'NLD': 'NETHERLANDS', 'BEL': 'BELGIUM', 'CHE': 'SWITZERLAND',
    'AUT': 'AUSTRIA', 'SWE': 'SWEDEN', 'NOR': 'NORWAY', 'DNK': 'DENMARK', 'FIN': 'FINLAND', 'POL': 'POLAND', 'ROU': 'ROMANIA',
    'GRC': 'GREECE', 'USA': 'UNITED STATES', 'CAN': 'CANADA', 'MEX': 'MEXICO', 'BRA': 'BRAZIL', 'ARG': 'ARGENTINA',
    'AUS': 'AUSTRALIA', 'NZL': 'NEW ZEALAND', 'UZB': 'UZBEKISTAN', 'KAZ': 'KAZAKHSTAN', 'KGZ': 'KYRGYZSTAN', 'TJK': 'TAJIKISTAN',
    'TKM': 'TURKMENISTAN', 'AZE': 'AZERBAIJAN', 'ARM': 'ARMENIA', 'GEO': 'GEORGIA', 'MMR': 'MYANMAR', 'KHM': 'CAMBODIA',
    'MDV': 'MALDIVES', 'BTN': 'BHUTAN', 'MUS': 'MAURITIUS', 'SEN': 'SENEGAL', 'CIV': "COTE D'IVOIRE", 'RWA': 'RWANDA',
}
ADJ = {'UNITED ARAB EMIRATES': 'EMIRATI', 'SAUDI ARABIA': 'SAUDI', 'KUWAIT': 'KUWAITI', 'QATAR': 'QATARI', 'OMAN': 'OMANI',
       'BAHRAIN': 'BAHRAINI', 'INDIA': 'INDIAN', 'PAKISTAN': 'PAKISTANI', 'BANGLADESH': 'BANGLADESHI', 'SRI LANKA': 'SRI LANKAN',
       'NEPAL': 'NEPALESE', 'PHILIPPINES': 'FILIPINO', 'INDONESIA': 'INDONESIAN', 'MALAYSIA': 'MALAYSIAN', 'THAILAND': 'THAI',
       'VIETNAM': 'VIETNAMESE', 'CHINA': 'CHINESE', 'JAPAN': 'JAPANESE', 'SOUTH KOREA': 'KOREAN', 'SINGAPORE': 'SINGAPOREAN',
       'AFGHANISTAN': 'AFGHAN', 'IRAN': 'IRANIAN', 'IRAQ': 'IRAQI', 'JORDAN': 'JORDANIAN', 'LEBANON': 'LEBANESE', 'SYRIA': 'SYRIAN',
       'PALESTINE': 'PALESTINIAN', 'YEMEN': 'YEMENI', 'EGYPT': 'EGYPTIAN', 'SUDAN': 'SUDANESE', 'LIBYA': 'LIBYAN', 'TUNISIA': 'TUNISIAN',
       'ALGERIA': 'ALGERIAN', 'MOROCCO': 'MOROCCAN', 'ETHIOPIA': 'ETHIOPIAN', 'ERITREA': 'ERITREAN', 'SOMALIA': 'SOMALI',
       'KENYA': 'KENYAN', 'UGANDA': 'UGANDAN', 'TANZANIA': 'TANZANIAN', 'NIGERIA': 'NIGERIAN', 'GHANA': 'GHANAIAN',
       'CAMEROON': 'CAMEROONIAN', 'SOUTH AFRICA': 'SOUTH AFRICAN', 'ZIMBABWE': 'ZIMBABWEAN', 'TURKEY': 'TURKISH', 'RUSSIA': 'RUSSIAN',
       'UKRAINE': 'UKRAINIAN', 'UNITED KINGDOM': 'BRITISH', 'IRELAND': 'IRISH', 'FRANCE': 'FRENCH', 'GERMANY': 'GERMAN',
       'ITALY': 'ITALIAN', 'SPAIN': 'SPANISH', 'PORTUGAL': 'PORTUGUESE', 'NETHERLANDS': 'DUTCH', 'BELGIUM': 'BELGIAN',
       'SWITZERLAND': 'SWISS', 'AUSTRIA': 'AUSTRIAN', 'SWEDEN': 'SWEDISH', 'NORWAY': 'NORWEGIAN', 'DENMARK': 'DANISH',
       'FINLAND': 'FINNISH', 'POLAND': 'POLISH', 'ROMANIA': 'ROMANIAN', 'GREECE': 'GREEK', 'UNITED STATES': 'AMERICAN',
       'CANADA': 'CANADIAN', 'MEXICO': 'MEXICAN', 'BRAZIL': 'BRAZILIAN', 'ARGENTINA': 'ARGENTINE', 'AUSTRALIA': 'AUSTRALIAN',
       'NEW ZEALAND': 'NEW ZEALANDER', 'UZBEKISTAN': 'UZBEK', 'KAZAKHSTAN': 'KAZAKH', 'KYRGYZSTAN': 'KYRGYZ', 'TAJIKISTAN': 'TAJIK',
       'TURKMENISTAN': 'TURKMEN', 'AZERBAIJAN': 'AZERBAIJANI', 'ARMENIA': 'ARMENIAN', 'GEORGIA': 'GEORGIAN', 'MYANMAR': 'BURMESE',
       'CAMBODIA': 'CAMBODIAN', 'MALDIVES': 'MALDIVIAN', 'BHUTAN': 'BHUTANESE', 'MAURITIUS': 'MAURITIAN', 'SENEGAL': 'SENEGALESE',
       "COTE D'IVOIRE": 'IVORIAN', 'RWANDA': 'RWANDAN'}

KIND_WORDS = {'passport': 'passport', 'visa': 'residence visa', 'emirates_id': 'Emirates ID', 'labour_card': 'labour card',
              'driving_licence': 'driving licence', 'insurance': 'insurance card'}
GENDER = {'M': 'M', 'F': 'F'}


def kind_entry(kind):
    return next((k for k in S.DOC_KINDS if k[0] == kind), None)


def nationality_for(code):
    """Core.Nationality of an MRZ code (IND → Indian), or None."""
    code = (code or '').strip().upper().replace('<', '')
    if not code:
        return None
    country = ISO3.get(code)
    if not country:
        return None
    N = M('Core', 'Nationality')
    for word in (ADJ.get(country), country):
        if word:
            n = N.objects.filter(N_name__iexact=word).first()
            if n is not None:
                return n
    return None


def _clean_num(kind_field, v):
    s = str(v or '').strip().upper()
    if kind_field == 'emirates_id':
        d = re.sub(r'\D', '', s)
        return f'{d[:3]}-{d[3:7]}-{d[7:14]}-{d[14]}' if len(d) == 15 else s
    return re.sub(r'\s+', '', s)


def _same_num(a, b):
    return re.sub(r'[^A-Z0-9]', '', str(a or '').upper()) == re.sub(r'[^A-Z0-9]', '', str(b or '').upper())


def _iso(v):
    if not v:
        return None
    if isinstance(v, date):
        return v.isoformat()
    try:
        return date.fromisoformat(str(v)[:10]).isoformat()
    except ValueError:
        return None


def split_name(rec):
    """(first, middle, last) from the document's name: MRZ given names + surname, else the words of the name."""
    given, sur = (rec.get('given') or '').strip(), (rec.get('surname') or '').strip()
    if given or sur:
        g = given.split()
        return (g[0] if g else ''), ' '.join(g[1:]), sur
    w = (rec.get('name') or '').split()
    if not w:
        return '', '', ''
    if len(w) == 1:
        return w[0], '', ''
    return w[0], ' '.join(w[1:-1]), w[-1]


def lines(emp, rec):
    """What the document would change. Each line: key, label, group, current, found (raw values), current_show, found_show,
    status (new | same | different | unknown), pick (ticked by default), note."""
    ident = S.identity_for(emp.pk)
    kind = rec.get('kind')
    verified = bool(rec.get('verified'))
    out = []

    def add(key, label, group, cur, found, cur_show=None, found_show=None, same=None, pick_diff=True, note=''):
        if found in (None, ''):
            return
        if cur in (None, ''):
            st = 'new'
        else:
            st = 'same' if (same if same is not None else str(cur) == str(found)) else 'different'
        out.append({'key': key, 'label': label, 'group': group, 'current': cur, 'found': found,
                    'current_show': cur_show if cur_show is not None else (cur or ''), 'found_show': found_show if found_show is not None else found,
                    'status': st, 'pick': st == 'new' or (st == 'different' and pick_diff), 'note': note})

    e = kind_entry(kind)
    if e:
        _, tname, nf, isf, exf, _n = e
        grp = tr('Identity details – {0}', tr(tname))
        cur_num = getattr(ident, nf, '') if ident else ''
        num = _clean_num(nf, rec.get('number')) if rec.get('number') else None
        add(f'identity.{nf}', tr('{0} number', tr(tname)), grp, cur_num, num, same=_same_num(cur_num, num) if num else None)
        newer = None
        cur_exp = _iso(getattr(ident, exf, None)) if ident else None
        exp = _iso(rec.get('expiry'))
        if cur_exp and exp:
            newer = exp >= cur_exp
        # the dates of an older document are not ticked: the record already has a later one
        add(f'identity.{isf}', tr('Issue date'), grp, _iso(getattr(ident, isf, None)) if ident else None, _iso(rec.get('issue')),
            pick_diff=newer is not False)
        add(f'identity.{exf}', tr('Expiry date'), grp, cur_exp, exp, pick_diff=newer is not False,
            note=tr('The record has a later expiry date than this document.') if newer is False else '')
    if rec.get('emirates_id') and kind != 'emirates_id':
        cur = ident.emirates_id if ident else ''
        v = _clean_num('emirates_id', rec['emirates_id'])
        add('identity.emirates_id', tr('Emirates ID number'), tr('Identity details – {0}', tr('Emirates ID')), cur, v, same=_same_num(cur, v))
    if rec.get('uid'):
        cur = ident.visa_uid if ident else ''
        add('identity.visa_uid', tr('UID number'), tr('Identity details – {0}', tr('Visa')), cur, re.sub(r'\D', '', str(rec['uid'])))

    egrp = tr('Employee')
    b = _iso(rec.get('birth'))
    if b:
        add('employee.emp_date_of_birth', tr('Date of birth'), egrp, _iso(emp.emp_date_of_birth), b, pick_diff=verified,
            note='' if verified else tr('Check the date: it was not verified by the machine-readable zone.'))
    if rec.get('nationality'):
        n = nationality_for(rec['nationality'])
        cur = emp.emp_nationality_id
        if n is not None:
            add('employee.emp_nationality', tr('Nationality'), egrp, cur, n.pk, cur_show=str(emp.emp_nationality or ''), found_show=n.N_name,
                pick_diff=verified)
        else:
            out.append({'key': 'employee.emp_nationality', 'label': tr('Nationality'), 'group': egrp, 'current': cur, 'found': None,
                        'current_show': str(emp.emp_nationality or ''), 'found_show': rec['nationality'], 'status': 'unknown', 'pick': False,
                        'note': tr('"{0}" is not in the nationality list.', rec['nationality'])})
    sx = GENDER.get((rec.get('sex') or '').upper())
    if sx:
        add('employee.emp_gender', tr('Gender'), egrp, emp.emp_gender, sx, cur_show=emp.get_emp_gender_display() if emp.emp_gender else '',
            found_show=tr('Male') if sx == 'M' else tr('Female'))
    first, middle, last = split_name(rec)
    if first or last:
        cur = ' '.join(x for x in [emp.emp_first_name, emp.emp_middle_name, emp.emp_last_name] if x)
        found = ' '.join(x for x in [first, middle, last] if x)
        same = re.sub(r'[^a-z]', '', cur.lower()) == re.sub(r'[^a-z]', '', found.lower())
        # a different spelling is never ticked by itself: HR decides
        add('employee.name', tr('Name (first, middle, last)'), egrp, cur, found, same=same, pick_diff=False,
            found_show=' / '.join(x or '–' for x in (first, middle, last)),
            note=tr('The name is split as first / middle / last. Untick it to keep the name on the record.'))
    return out


def preview(emp, rec):
    ls = lines(emp, rec)
    return {'kind': rec.get('kind'), 'lines': ls, 'changes': sum(1 for x in ls if x['status'] in ('new', 'different')),
            'mismatches': sum(1 for x in ls if x['status'] == 'different')}


def apply(emp, rec, keys, user=None, file_name='', sync=True, values=None):
    """Save the chosen lines. Returns {saved: [labels], document_sync, preview}. Raises ValidationError."""
    values = values or {}
    chosen = [x for x in lines(emp, rec) if x['key'] in set(keys or []) and x['status'] in ('new', 'different')]
    if not chosen:
        raise ValidationError({'detail': tr('Tick the values to save on the employee record.')})
    ident_data, emp_fields, saved = {}, {}, []
    for x in chosen:
        v = values.get(x['key'], x['found'])
        part, field = x['key'].split('.', 1)
        if part == 'identity':
            ident_data[field] = v
        elif field == 'name':
            first, middle, last = split_name(rec)
            if isinstance(v, dict):
                first, middle, last = v.get('first', first), v.get('middle', middle), v.get('last', last)
            if not first:
                raise ValidationError({'name': tr('The first name cannot be empty.')})
            emp_fields.update(emp_first_name=first[:50], emp_middle_name=(middle or '')[:50] or None, emp_last_name=(last or '')[:50] or None)
        elif field == 'emp_nationality':
            n = M('Core', 'Nationality').objects.filter(pk=v).first()
            if n is None:
                raise ValidationError({'nationality': tr('Choose a nationality from the list.')})
            emp_fields['emp_nationality'] = n
        elif field == 'emp_date_of_birth':
            d = _iso(v)
            if not d or d >= date.today().isoformat():
                raise ValidationError({'emp_date_of_birth': tr('Enter a valid date (YYYY-MM-DD).')})
            emp_fields['emp_date_of_birth'] = date.fromisoformat(d)
        elif field == 'emp_gender':
            emp_fields['emp_gender'] = v if v in ('M', 'F', 'O') else None
        saved.append(x['label'])
    sync_res = None
    from .serializers import IdentitySerializer
    with transaction.atomic():
        if ident_data:
            inst = S.identity_for(emp.pk)
            ser = IdentitySerializer(inst, data=ident_data, partial=True, context={'employee_id': emp.pk})
            ser.is_valid(raise_exception=True)
            obj = ser.save(employee_id=emp.pk, updated_by_id=getattr(user, 'pk', None))
        if emp_fields:
            for k, v in emp_fields.items():
                setattr(emp, k, v)
            emp.updated_by_id = getattr(user, 'pk', None)
            emp.save(update_fields=list(emp_fields) + ['updated_by', 'updated_at'])
    if ident_data and sync:
        sync_res = S.sync_documents(obj, emp, user)
    what = KIND_WORDS.get(rec.get('kind') or '', 'document')
    S.note(emp, tr('Updated from the {0}', tr(what)) + (f' ({file_name})' if file_name else '') + ': ' + ', '.join(saved) + '.', user)
    return {'saved': saved, 'document_sync': sync_res, 'preview': preview(emp, rec)}
