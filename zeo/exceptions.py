# zeo/exceptions.py

from rest_framework.views import exception_handler
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response
from rest_framework import status

def custom_exception_handler(exc, context):
    """
    Converts Django ValidationError to DRF ValidationError (JSON instead of HTML).
    """
    # If it's a Django ValidationError, convert it
    if isinstance(exc, DjangoValidationError):
        if hasattr(exc, "message_dict"):
            exc = DRFValidationError(exc.message_dict)
        else:
            exc = DRFValidationError(exc.messages)

    # A record still used elsewhere (protected link): a clear 400 instead of a server error (v1.7.2)
    from django.db.models.deletion import ProtectedError, RestrictedError
    if isinstance(exc, (ProtectedError, RestrictedError)):
        objs = list(getattr(exc, 'protected_objects', None) or getattr(exc, 'restricted_objects', None) or [])
        kinds = sorted({o._meta.verbose_name.title() for o in objs})
        sample = ', '.join(str(o) for o in objs[:3])
        return Response({"detail": f"This record cannot be deleted: it is still used by {len(objs)} "
                                   f"{' / '.join(kinds) or 'record'}{'s' if len(objs) != 1 else ''}"
                                   f"{' (' + sample + (', …' if len(objs) > 3 else '') + ')' if sample else ''}. "
                                   f"Change those records first, or mark this one inactive."},
                        status=status.HTTP_400_BAD_REQUEST)

    # v1.56.1 (release checklist 10 "Server reason shown"): a value the database needs but the form did not send, a duplicate or a
    # value out of range answered 500 "Server error" – now a 400 with the field named
    from django.db import IntegrityError
    if isinstance(exc, IntegrityError):
        from rest_framework.views import set_rollback
        set_rollback()
        return Response(integrity_reason(exc), status=status.HTTP_400_BAD_REQUEST)

    # Call default DRF exception handler
    response = exception_handler(exc, context)

    # If DRF couldn't handle it
    if response is None:
        if isinstance(exc, DRFValidationError):
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    # v1.62.0 the reason code next to the text (session_ended, bank_change_pending, legal_hold …) – the app acts on it
    try:
        d = response.data if response is not None else None
        if isinstance(d, dict) and 'code' not in d and getattr(d.get('detail'), 'code', None):
            d['code'] = d['detail'].code
        if isinstance(d, dict) and getattr(exc, 'field', None):   # v1.62.0 a refused upload names its field
            d.setdefault('field', exc.field)
            d.setdefault(exc.field, [str(d.get('detail', ''))])
    except Exception:
        pass
    return response


def _label(column):
    import re as _re
    c = _re.sub(r'_id$', '', column or '')
    return c.replace('_', ' ').strip().capitalize() or 'A value'


def integrity_reason(exc):
    """A database refusal in plain words, with the field when the database names it (PostgreSQL)."""
    import re as _re
    try:
        from Translations.i18n import tr
    except Exception:   # noqa: BLE001
        def tr(s, *a):
            return s.format(*a) if a else s
    msg = str(exc)
    m = _re.search(r'null value in column "([^"]+)"', msg)
    if m:
        f = _re.sub(r'_id$', '', m.group(1))
        return {'detail': tr('{0} is required.', _label(m.group(1))), f: [tr('This field is required.')]}
    m = _re.search(r'duplicate key value violates unique constraint "[^"]*"\s*DETAIL:\s*Key \(([^)]+)\)=\(([^)]*)\)', msg, _re.S)
    if m:
        cols = [x.strip() for x in m.group(1).split(',')]
        return {'detail': tr('{0} "{1}" is already used – choose another.', ' / '.join(_label(c) for c in cols), m.group(2)),
                _re.sub(r'_id$', '', cols[-1]): [tr('Already used.')]}
    if 'duplicate key value' in msg:
        return {'detail': tr('This record already exists.')}
    m = _re.search(r'violates check constraint "([^"]+)"', msg)
    if m:
        return {'detail': tr('A value is out of range ({0}) – check the numbers and dates.', m.group(1).replace('_', ' '))}
    m = _re.search(r'violates foreign key constraint .*Key \(([^)]+)\)=\(([^)]*)\) is not present', msg, _re.S)
    if m:
        return {'detail': tr('{0} {1} does not exist (any more).', _label(m.group(1)), m.group(2))}
    return {'detail': tr('The record could not be saved: a value is missing or not allowed.')}
