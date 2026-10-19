"""v1.33.0 – lead capture from a website form (public, keyed) and the quotation / sales order PDFs."""
import logging

from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

log = logging.getLogger(__name__)


@method_decorator(csrf_exempt, name='dispatch')
class CaptureView(APIView):
    """POST /zeo-crm/api/capture/<key>/  {name, email, phone, company, message, source, campaign, city}
    The key is "Web form key" in CRM setup. Honeypot: a filled `website` field is ignored silently (bots)."""
    permission_classes = [AllowAny]
    authentication_classes = []
    zeo_access = False
    zeo_scope = False

    def post(self, request, key):
        from .app import SALES, app
        s = app.settings()
        if not s.capture_key or key != s.capture_key:
            return Response({'detail': 'Unknown key.'}, status=status.HTTP_403_FORBIDDEN)
        d = request.data
        if str(d.get('website') or '').strip():
            return Response({'ok': True})
        name = str(d.get('name') or '').strip()
        email = str(d.get('email') or '').strip()
        phone = str(d.get('phone') or '').strip()
        if not (name or email or phone):
            return Response({'detail': 'Give a name, an e-mail or a phone.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            out = SALES['lead_capture'](name=name, email=email, phone=phone, company=str(d.get('company') or ''), message=str(d.get('message') or ''),
                                        source=str(d.get('source') or 'WEB'), channel='web', city=str(d.get('city') or ''), campaign=str(d.get('campaign') or ''),
                                        area=str(d.get('area') or ''), interest=str(d.get('interest') or ''), budget=d.get('budget'), bedrooms=d.get('bedrooms'))
        except Exception:
            log.exception('lead capture')
            return Response({'detail': 'Could not save the enquiry.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response({'ok': True, 'number': out['number']}, status=status.HTTP_201_CREATED)
