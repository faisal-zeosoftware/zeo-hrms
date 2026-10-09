"""v1.13.1 – shared master data for a new installation (public schema): countries, emirates / regions, currencies,
nationalities, religions, languages, VAT. Safe to run again: existing rows are kept, missing ones are added.

    python manage.py load_master_data
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from Core.models import (LanguageMaster, LanguageSkill, Nationality, ReligionMaster, TaxSystem, cntry_mstr, crncy_mstr,
                         state_mstr)

# (country, ISO code, IANA time zone, currency name, currency code, symbol, nationality); euro countries share the euro (France)
COUNTRIES = [
    ('United Arab Emirates', 'AE', 'Asia/Dubai', 'UAE Dirham', 'AED', 'AED', 'EMIRATI'),
    ('Saudi Arabia', 'SA', 'Asia/Riyadh', 'Saudi Riyal', 'SAR', 'SAR', 'SAUDI'),
    ('Qatar', 'QA', 'Asia/Qatar', 'Qatari Riyal', 'QAR', 'QAR', 'QATARI'),
    ('Oman', 'OM', 'Asia/Muscat', 'Omani Rial', 'OMR', 'OMR', 'OMANI'),
    ('Bahrain', 'BH', 'Asia/Bahrain', 'Bahraini Dinar', 'BHD', 'BHD', 'BAHRAINI'),
    ('Kuwait', 'KW', 'Asia/Kuwait', 'Kuwaiti Dinar', 'KWD', 'KWD', 'KUWAITI'),
    ('India', 'IN', 'Asia/Kolkata', 'Indian Rupee', 'INR', '₹', 'INDIAN'),
    ('Pakistan', 'PK', 'Asia/Karachi', 'Pakistani Rupee', 'PKR', 'Rs', 'PAKISTANI'),
    ('Bangladesh', 'BD', 'Asia/Dhaka', 'Bangladeshi Taka', 'BDT', '৳', 'BANGLADESHI'),
    ('Sri Lanka', 'LK', 'Asia/Colombo', 'Sri Lankan Rupee', 'LKR', 'Rs', 'SRI LANKAN'),
    ('Nepal', 'NP', 'Asia/Kathmandu', 'Nepalese Rupee', 'NPR', 'Rs', 'NEPALESE'),
    ('Philippines', 'PH', 'Asia/Manila', 'Philippine Peso', 'PHP', '₱', 'FILIPINO'),
    ('Indonesia', 'ID', 'Asia/Jakarta', 'Indonesian Rupiah', 'IDR', 'Rp', 'INDONESIAN'),
    ('Egypt', 'EG', 'Africa/Cairo', 'Egyptian Pound', 'EGP', 'E£', 'EGYPTIAN'),
    ('Jordan', 'JO', 'Asia/Amman', 'Jordanian Dinar', 'JOD', 'JOD', 'JORDANIAN'),
    ('Lebanon', 'LB', 'Asia/Beirut', 'Lebanese Pound', 'LBP', 'L£', 'LEBANESE'),
    ('Syria', 'SY', 'Asia/Damascus', 'Syrian Pound', 'SYP', '£S', 'SYRIAN'),
    ('Iraq', 'IQ', 'Asia/Baghdad', 'Iraqi Dinar', 'IQD', 'IQD', 'IRAQI'),
    ('Yemen', 'YE', 'Asia/Aden', 'Yemeni Rial', 'YER', 'YER', 'YEMENI'),
    ('Sudan', 'SD', 'Africa/Khartoum', 'Sudanese Pound', 'SDG', 'SDG', 'SUDANESE'),
    ('Morocco', 'MA', 'Africa/Casablanca', 'Moroccan Dirham', 'MAD', 'MAD', 'MOROCCAN'),
    ('Tunisia', 'TN', 'Africa/Tunis', 'Tunisian Dinar', 'TND', 'TND', 'TUNISIAN'),
    ('Algeria', 'DZ', 'Africa/Algiers', 'Algerian Dinar', 'DZD', 'DZD', 'ALGERIAN'),
    ('Iran', 'IR', 'Asia/Tehran', 'Iranian Rial', 'IRR', 'IRR', 'IRANIAN'),
    ('Afghanistan', 'AF', 'Asia/Kabul', 'Afghan Afghani', 'AFN', 'AFN', 'AFGHAN'),
    ('Kenya', 'KE', 'Africa/Nairobi', 'Kenyan Shilling', 'KES', 'KSh', 'KENYAN'),
    ('Uganda', 'UG', 'Africa/Kampala', 'Ugandan Shilling', 'UGX', 'USh', 'UGANDAN'),
    ('Ethiopia', 'ET', 'Africa/Addis_Ababa', 'Ethiopian Birr', 'ETB', 'Br', 'ETHIOPIAN'),
    ('Nigeria', 'NG', 'Africa/Lagos', 'Nigerian Naira', 'NGN', '₦', 'NIGERIAN'),
    ('Ghana', 'GH', 'Africa/Accra', 'Ghanaian Cedi', 'GHS', 'GH₵', 'GHANAIAN'),
    ('South Africa', 'ZA', 'Africa/Johannesburg', 'South African Rand', 'ZAR', 'R', 'SOUTH AFRICAN'),
    ('China', 'CN', 'Asia/Shanghai', 'Chinese Yuan', 'CNY', '¥', 'CHINESE'),
    ('Japan', 'JP', 'Asia/Tokyo', 'Japanese Yen', 'JPY', '¥', 'JAPANESE'),
    ('South Korea', 'KR', 'Asia/Seoul', 'South Korean Won', 'KRW', '₩', 'SOUTH KOREAN'),
    ('Malaysia', 'MY', 'Asia/Kuala_Lumpur', 'Malaysian Ringgit', 'MYR', 'RM', 'MALAYSIAN'),
    ('Singapore', 'SG', 'Asia/Singapore', 'Singapore Dollar', 'SGD', 'S$', 'SINGAPOREAN'),
    ('Thailand', 'TH', 'Asia/Bangkok', 'Thai Baht', 'THB', '฿', 'THAI'),
    ('Vietnam', 'VN', 'Asia/Ho_Chi_Minh', 'Vietnamese Dong', 'VND', '₫', 'VIETNAMESE'),
    ('Turkey', 'TR', 'Europe/Istanbul', 'Turkish Lira', 'TRY', '₺', 'TURKISH'),
    ('Russia', 'RU', 'Europe/Moscow', 'Russian Ruble', 'RUB', '₽', 'RUSSIAN'),
    ('Ukraine', 'UA', 'Europe/Kyiv', 'Ukrainian Hryvnia', 'UAH', '₴', 'UKRAINIAN'),
    ('United Kingdom', 'GB', 'Europe/London', 'Pound Sterling', 'GBP', '£', 'BRITISH'),
    ('Ireland', 'IE', 'Europe/Dublin', None, None, None, 'IRISH'),
    ('France', 'FR', 'Europe/Paris', 'Euro', 'EUR', '€', 'FRENCH'),
    ('Germany', 'DE', 'Europe/Berlin', None, None, None, 'GERMAN'),
    ('Italy', 'IT', 'Europe/Rome', None, None, None, 'ITALIAN'),
    ('Spain', 'ES', 'Europe/Madrid', None, None, None, 'SPANISH'),
    ('Netherlands', 'NL', 'Europe/Amsterdam', None, None, None, 'DUTCH'),
    ('Switzerland', 'CH', 'Europe/Zurich', 'Swiss Franc', 'CHF', 'CHF', 'SWISS'),
    ('United States', 'US', 'America/New_York', 'US Dollar', 'USD', '$', 'AMERICAN'),
    ('Canada', 'CA', 'America/Toronto', 'Canadian Dollar', 'CAD', 'C$', 'CANADIAN'),
    ('Australia', 'AU', 'Australia/Sydney', 'Australian Dollar', 'AUD', 'A$', 'AUSTRALIAN'),
    ('New Zealand', 'NZ', 'Pacific/Auckland', 'New Zealand Dollar', 'NZD', 'NZ$', 'NEW ZEALANDER'),
    ('Brazil', 'BR', 'America/Sao_Paulo', 'Brazilian Real', 'BRL', 'R$', 'BRAZILIAN'),
]
STATES = {
    'United Arab Emirates': ['Abu Dhabi', 'Dubai', 'Sharjah', 'Ajman', 'Umm Al Quwain', 'Ras Al Khaimah', 'Fujairah'],
    'Saudi Arabia': ['Riyadh', 'Makkah', 'Madinah', 'Eastern Province', 'Qassim', 'Asir', 'Tabuk', 'Hail', 'Northern Borders',
                     'Jazan', 'Najran', 'Al Bahah', 'Al Jawf'],
    'Qatar': ['Doha', 'Al Rayyan', 'Al Wakrah', 'Al Khor', 'Umm Salal', 'Al Daayen', 'Al Shamal', 'Al Shahaniya'],
    'Oman': ['Muscat', 'Dhofar', 'Musandam', 'Al Buraimi', 'Ad Dakhiliyah', 'North Al Batinah', 'South Al Batinah',
             'North Ash Sharqiyah', 'South Ash Sharqiyah', 'Ad Dhahirah', 'Al Wusta'],
    'Bahrain': ['Capital', 'Muharraq', 'Northern', 'Southern'],
    'Kuwait': ['Al Asimah', 'Hawalli', 'Farwaniya', 'Mubarak Al-Kabeer', 'Ahmadi', 'Jahra'],
}
RELIGIONS = ['Islam', 'Christianity', 'Hinduism', 'Buddhism', 'Sikhism', 'Judaism', 'Other', 'Prefer not to say']
LANGUAGES = ['Arabic', 'English', 'Hindi', 'Urdu', 'Malayalam', 'Tamil', 'Telugu', 'Bengali', 'Tagalog', 'Nepali', 'Sinhala',
             'French', 'German', 'Spanish', 'Russian', 'Chinese', 'Persian', 'Turkish']
VAT = {'United Arab Emirates': ('VAT', 5), 'Saudi Arabia': ('VAT', 15), 'Oman': ('VAT', 5), 'Bahrain': ('VAT', 10),
       'India': ('GST', 18)}


class Command(BaseCommand):
    help = 'Load countries, emirates / regions, currencies, nationalities, religions, languages and VAT for a new installation.'

    @transaction.atomic
    def handle(self, *args, **opts):
        added = {'countries': 0, 'states': 0, 'currencies': 0, 'nationalities': 0, 'religions': 0, 'languages': 0, 'tax': 0}
        for name, code, tz, cur, ccode, sym, nat in COUNTRIES:
            c = cntry_mstr.objects.filter(country_name__iexact=name).first() or cntry_mstr.objects.filter(country_code=code).first()
            if c is None:
                c = cntry_mstr.objects.create(country_name=name, country_code=code, timezone=tz)
                added['countries'] += 1
            elif not c.timezone or not c.country_code:
                c.timezone = c.timezone or tz
                c.country_code = c.country_code or code
                c.save(update_fields=['timezone', 'country_code'])
            if ccode and not crncy_mstr.objects.filter(currency_code=ccode).exists() and not crncy_mstr.objects.filter(currency_name=cur).exists():
                crncy_mstr.objects.create(currency_name=cur, currency_code=ccode, symbol=(sym or '')[:5], country=c)
                added['currencies'] += 1
            if not Nationality.objects.filter(N_name__iexact=nat).exists():
                Nationality.objects.create(N_name=nat)
                added['nationalities'] += 1
            for st in STATES.get(name, []):
                if not state_mstr.objects.filter(country=c, state_name__iexact=st).exists():
                    state_mstr.objects.create(country=c, state_name=st)
                    added['states'] += 1
            if name in VAT and not TaxSystem.objects.filter(country=c).exists():
                TaxSystem.objects.create(country=c, tax_name=VAT[name][0], tax_percentage=VAT[name][1])
                added['tax'] += 1
        for r in RELIGIONS:
            if not ReligionMaster.objects.filter(religion__iexact=r).exists():
                ReligionMaster.objects.create(religion=r)
                added['religions'] += 1
        for lang in LANGUAGES:
            if not LanguageMaster.objects.filter(language__iexact=lang).exists():
                LanguageMaster.objects.create(language=lang)
                added['languages'] += 1
            if not LanguageSkill.objects.filter(language__iexact=lang).exists():
                LanguageSkill.objects.create(language=lang)
        self.stdout.write(self.style.SUCCESS('Master data loaded: ' + ', '.join(f'{v} {k}' for k, v in added.items())))
