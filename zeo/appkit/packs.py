"""Industry packs. A pack is data:

    {'label': 'Property management', 'description': '…',
     'terms': {'deal': 'Enquiry'},                      # wording; only keys the company has not set
     'rows': {'pipeline': [{'code': 'LEASE', 'name': 'Leasing'}],
              'stage': [{'pipeline': '@pipeline:LEASE', 'code': 'NEW', 'name': 'New', 'order': 1}]}}

app.pack_models maps a row key to (model, natural key field). '@other:CODE' refers to a row of another key.
Packs only ADD: a row whose natural key exists is left as the company has it. New rows are marked "to review"
when the app setting says so (General rows too: the manager confirms what the app starts with).
"""
from django.db import transaction

from .engine import AppError, company_industry, now, user_name


def suggested(app):
    code, label = company_industry()
    return code, label, app.industry_from_company.get(code, 'general')


def summary(app, code):
    p = app.packs[code]
    return {'code': code, 'label': p['label'], 'description': p.get('description', ''),
            'counts': {k: len(v) for k, v in p.get('rows', {}).items()},
            'highlights': p.get('highlights', [])}


def _resolve(app, v):
    if isinstance(v, str) and v.startswith('@') and ':' in v:
        key, _, nat = v[1:].partition(':')
        model, field = app.pack_models[key]
        return model.objects.filter(**{field: nat}).first()
    return v


def install(app, code, user=None, primary=False):
    if code not in app.packs:
        raise AppError('Unknown industry pack.', 'industry')
    p = app.packs[code]
    s = app.settings()
    review = s.require_review
    added = {}
    with transaction.atomic():
        for key, rows in p.get('rows', {}).items():
            model, nat = app.pack_models[key]
            fields = {f.name for f in model._meta.get_fields()}
            n = 0
            for row in rows:
                if model.objects.filter(**{nat: row[nat]}).exists():
                    continue
                m2m = {}
                data = {}
                for k, v in row.items():
                    if isinstance(v, list) and k in fields and model._meta.get_field(k).many_to_many:
                        m2m[k] = [_resolve(app, x) for x in v]
                    else:
                        data[k] = _resolve(app, v)
                if 'pack' in fields:
                    data['pack'] = code
                if 'review_status' in fields:
                    data['review_status'] = 'to_review' if review else 'reviewed'
                obj = model.objects.create(**data)
                for k, xs in m2m.items():
                    getattr(obj, k).set([x for x in xs if x is not None])
                n += 1
            if n:
                added[key] = n
        s = app.settings()
        terms = dict(s.terms or {})
        for k, v in p.get('terms', {}).items():
            terms.setdefault(k, v)
        s.terms = terms
        if code not in (s.packs_installed or []):
            s.packs_installed = list(s.packs_installed or []) + [code]
        if primary:
            s.industry = code
        s.save()
        hook = p.get('after')
        if hook:
            hook(app, user)
    app.event('settings', 0, f'Industry pack "{p["label"]}" installed by {user_name(user) or "system"}: '
              + (', '.join(f'{v} {k}' for k, v in added.items()) or 'nothing new'), user)
    return added


def activate(app, code, user):
    s = app.settings()
    if s.activated_at:
        raise AppError(f'{app.title} is already activated. Add another industry pack in Setup › Industry pack.')
    if code not in app.packs:
        raise AppError(f'Choose the industry for {app.title}.', 'industry')
    with transaction.atomic():
        added = install(app, code, user, primary=True)
        s = app.settings()
        _, _, sug = suggested(app)
        s.industry = code
        s.activated_at, s.activated_by = now(), (user if user and user.is_authenticated else None)
        s.industry_source = 'company' if code == sug else 'chosen'
        s.save()
    app.event('settings', 0, f'{app.title} activated by {user_name(user) or "system"} – industry {app.packs[code]["label"]}'
              + ('' if s.industry_source == 'company' else ' (different from the company master)'), user)
    try:
        from .engine import forget_tables
        forget_tables()
    except Exception:
        pass
    return added


def to_review(app):
    out = []
    for key, (model, nat) in app.pack_models.items():
        if not hasattr(model, 'review_status'):
            continue
        for x in model.objects.filter(review_status='to_review'):
            out.append({'key': key, 'id': x.pk, 'name': str(getattr(x, 'name', '') or getattr(x, nat)), 'code': getattr(x, nat),
                        'pack': getattr(x, 'pack', ''), 'label': model._meta.verbose_name.title()})
    return out
