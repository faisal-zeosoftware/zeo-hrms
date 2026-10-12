import re


def Q(section, text, type='yesno', critical=False, required=True, hint=''):
    key = re.sub(r'[^a-z0-9]+', '_', text.lower()).strip('_')[:40]
    return {'key': key, 'section': section, 'text': text, 'type': type, 'critical': critical, 'required': required, 'hint': hint}


def steps(*pairs):
    return [{'role': r, 'label': l} for r, l in pairs]
