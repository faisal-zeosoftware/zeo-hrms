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

    # Call default DRF exception handler
    response = exception_handler(exc, context)

    # If DRF couldn't handle it
    if response is None:
        if isinstance(exc, DRFValidationError):
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    return response
