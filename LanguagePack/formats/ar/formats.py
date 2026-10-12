# v1.15.0 – Arabic number / date formats for server-side output (PDFs, e-mails, Django templates).
# Digits stay Western (0-9) and the separators stay as in English, so amounts read the same in every language
# ("1,250.50", never "1.250,50"). Enabled with FORMAT_MODULE_PATH = ['LanguagePack.formats'].
DATE_FORMAT = 'j F Y'
TIME_FORMAT = 'g:i A'
DATETIME_FORMAT = 'j F Y g:i A'
YEAR_MONTH_FORMAT = 'F Y'
MONTH_DAY_FORMAT = 'j F'
SHORT_DATE_FORMAT = 'd/m/Y'
SHORT_DATETIME_FORMAT = 'd/m/Y H:i'
FIRST_DAY_OF_WEEK = 1
DECIMAL_SEPARATOR = '.'
THOUSAND_SEPARATOR = ','
NUMBER_GROUPING = 3
