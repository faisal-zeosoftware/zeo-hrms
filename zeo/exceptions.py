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

    # Call default DRF exception handler
    response = exception_handler(exc, context)

    # If DRF couldn't handle it
    if response is None:
        if isinstance(exc, DRFValidationError):
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    return response
