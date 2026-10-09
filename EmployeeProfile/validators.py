"""v1.13.0 – UAE identity / bank checks shared by the HR screens, the import and self-service.

Every checker returns the cleaned value or raises ValueError with a message that says how to fix it.
"""
import re
from datetime import date

EID_RE = re.compile(r'^784-(\d{4})-(\d{7})-(\d)$')


def luhn_ok(digits):
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def luhn_digit(first14):
    """Check digit that makes first14 + digit pass the Luhn test (used by tests and sample data)."""
    for d in range(10):
        if luhn_ok(first14 + str(d)):
            return str(d)
    return '0'


def clean_emirates_id(v):
    """784-YYYY-NNNNNNN-N (15 digits, dashes optional on input) with a valid Luhn check digit."""
    s = re.sub(r'[\s\-]', '', str(v or ''))
    if not s:
        return ''
    if not s.isdigit() or len(s) != 15:
        raise ValueError('Enter the Emirates ID as 784-YYYY-NNNNNNN-N (15 digits).')
    if not s.startswith('784'):
        raise ValueError('An Emirates ID starts with 784.')
    year = int(s[3:7])
    if year < 1900 or year > date.today().year:
        raise ValueError('The 4 digits after 784 are the year of birth – check them.')
    if not luhn_ok(s):
        raise ValueError('This Emirates ID number is not valid (the last digit does not match). Check the number on the card.')
    return f'{s[:3]}-{s[3:7]}-{s[7:14]}-{s[14]}'


def iban_mod97_ok(iban):
    moved = iban[4:] + iban[:4]
    num = ''.join(str(int(ch, 36)) for ch in moved)
    return int(num) % 97 == 1


def clean_iban(v):
    """UAE IBAN: AE + 2 check digits + 3-digit bank code + 16-digit account = 23 characters, mod-97 check."""
    s = re.sub(r'\s+', '', str(v or '')).upper()
    if not s:
        return ''
    if not s.startswith('AE'):
        raise ValueError('Enter a UAE IBAN – it starts with AE (salaries are paid through WPS in the UAE).')
    if len(s) != 23:
        raise ValueError(f'A UAE IBAN has 23 characters (AE + 21 digits); this one has {len(s)}.')
    if not s[2:].isdigit():
        raise ValueError('After AE a UAE IBAN has only digits.')
    if not iban_mod97_ok(s):
        raise ValueError('This IBAN is not valid (the check digits do not match). Copy it again from the bank letter.')
    return s


def iban_check_digits(bban, country='AE'):
    """Check digits for country + bban (for tests / sample data)."""
    num = ''.join(str(int(ch, 36)) for ch in bban + country + '00')
    return str(98 - int(num) % 97).zfill(2)


def clean_passport(v):
    s = re.sub(r'\s+', '', str(v or '')).upper()
    if not s:
        return ''
    if not re.fullmatch(r'[A-Z0-9]{6,12}', s):
        raise ValueError('A passport number has 6 to 12 letters and digits (no spaces or symbols).')
    return s


def clean_digits(v, label, lo, hi):
    s = re.sub(r'\s+', '', str(v or ''))
    if not s:
        return ''
    if not s.isdigit() or not (lo <= len(s) <= hi):
        raise ValueError(f'{label} has {lo} to {hi} digits only.')
    return s


def clean_code(v, label, lo=3, hi=30, extra=''):
    s = re.sub(r'\s+', '', str(v or '')).upper()
    if not s:
        return ''
    if not re.fullmatch(rf'[A-Z0-9{re.escape(extra)}]{{{lo},{hi}}}', s):
        allowed = 'letters, digits' + (f' and {" ".join(extra)}' if extra else '')
        raise ValueError(f'{label}: use {lo} to {hi} {allowed}.')
    return s


def clean_phone(v, label='Phone'):
    s = str(v or '').strip()
    if not s:
        return ''
    if not re.fullmatch(r'\+?[0-9 ()\-]{6,24}', s) or len(re.sub(r'\D', '', s)) < 7:
        raise ValueError(f'Enter a valid {label.lower()} (digits, spaces, + and -).')
    return s


MAX_VALIDITY_YEARS = 15


def check_dates(issue, expiry, label, errors, issue_key, expiry_key, today=None):
    """issue not in the future / not absurd; expiry after issue and within a sensible validity."""
    today = today or date.today()
    if issue and issue > today:
        errors[issue_key] = f'The {label} issue date cannot be in the future.'
    if issue and issue.year < 1950:
        errors[issue_key] = f'Check the {label} issue date.'
    if issue and expiry:
        if expiry <= issue:
            errors[expiry_key] = f'The {label} expiry date must be after the issue date.'
        elif (expiry - issue).days > MAX_VALIDITY_YEARS * 366:
            errors[expiry_key] = f'The {label} is valid for more than {MAX_VALIDITY_YEARS} years – check the dates.'
    if expiry and expiry.year < 1950:
        errors[expiry_key] = f'Check the {label} expiry date.'
