"""v1.67.7 (admin) – sign-in security e-mails (and the payslip e-mail) in the language of each recipient.

`lang_of(user, schema)` – the recipient's own language (My account › Language), else the default language of the company
(the sign-in's company, else the first of the user's companies), else English. Every text is an English template translated
with Translations.i18n.tr (the Arabic terms are in i18n/packs/ar.json); a term that is missing stays English.
`message(...)` – the e-mail with a plain-text part and an HTML part; a right-to-left language (Arabic …) gets
`<div dir="rtl" lang="ar">` in the HTML part so mail programs show it right-to-left.
"""
import html as _html
import logging

log = logging.getLogger(__name__)


def lang_of(user, schema=''):
    from Translations.i18n import norm, SOURCE_LANG
    try:
        from LanguagePack.services import active_codes, company_default, user_pref
        uid = getattr(user, 'pk', user)
        p = norm(user_pref(uid) or '')
        if p and p in [norm(c) for c in active_codes()]:
            return p
        sch = schema or ''
        if not sch and getattr(user, 'pk', None):
            try:
                sch = user.tenants.exclude(schema_name='public').order_by('id').values_list('schema_name', flat=True).first() or ''
            except Exception:
                sch = ''
        if sch:
            from django_tenants.utils import schema_context
            with schema_context(sch):
                return norm(company_default(sch)) or SOURCE_LANG
    except Exception:
        log.debug('mail language', exc_info=True)
    return SOURCE_LANG


def T(text, lang, *args, **kw):
    """`text` (English template) in `lang`, placeholders filled; English when no term exists."""
    from Translations.i18n import tr
    try:
        return tr(text, *args, lang=lang, **kw)
    except Exception:
        try:
            return text.format(*args, **kw)
        except Exception:
            return text


def is_rtl(lang):
    from Translations.i18n import is_rtl as _r
    return _r(lang)


def html_of(body, lang):
    rtl = is_rtl(lang)
    paras = [p for p in (body or '').split('\n\n')]
    inner = ''.join('<p style="margin:0 0 12px">' + _html.escape(p).replace('\n', '<br>') + '</p>' for p in paras)
    return (f'<!doctype html><html lang="{_html.escape(lang or "en")}" dir="{"rtl" if rtl else "ltr"}"><head><meta charset="utf-8"></head>'
            f'<body><div dir="{"rtl" if rtl else "ltr"}" lang="{_html.escape(lang or "en")}" '
            f'style="font-family:Tahoma,Arial,sans-serif;font-size:14px;line-height:1.6;text-align:{"right" if rtl else "left"}">'
            f'{inner}</div></body></html>')


def message(subject, body, lang, sender, to, connection=None):
    from django.core.mail import EmailMultiAlternatives
    m = EmailMultiAlternatives(subject=subject, body=body, from_email=sender, to=list(to), connection=connection)
    m.attach_alternative(html_of(body, lang), 'text/html')
    return m


def device_text(dev, lang):
    """"Chrome on Windows" in the reader's language (the app and system names stay as they are)."""
    if not dev or lang == 'en':
        return dev or ''
    app, sep, os_ = str(dev).partition(' on ')
    if not sep:
        return T(dev, lang)
    on = T('on (device)', lang)
    return f'{T(app, lang)} {"on" if on == "on (device)" else on} {T(os_, lang)}'
