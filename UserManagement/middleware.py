
from zeo.tz import today as _zt_today   # v1.73.16 the user's / company's day, not the server's UTC day
from django.utils.deprecation import MiddlewareMixin
from django_tenants.utils import schema_context
from oauth2_provider.models import AccessToken
from django.utils import timezone
import logging
from django.http import JsonResponse
from django_tenants.utils import schema_context
from django.http import JsonResponse
from django.utils.deprecation import MiddlewareMixin


logger = logging.getLogger(__name__)

class TenantSwitchingMiddleware(MiddlewareMixin):
    def process_request(self, request):
        logger.debug('TenantSwitchingMiddleware: process_request started')
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        if auth_header.startswith('Bearer '):
            token = auth_header.split(' ')[1]
            try:
                access_token = AccessToken.objects.get(token=token)
                if access_token.expires > timezone.now():
                    user = access_token.user
                    if user and user.tenants.exists():
                        tenant_id = request.GET.get('tenant_id')
                        if tenant_id:
                            requested_tenant = user.tenants.filter(id=tenant_id).first()
                            if requested_tenant:
                                with schema_context(requested_tenant.schema_name):
                                    request.tenant = requested_tenant
                                    logger.debug(f'User {user} is associated with requested tenant {requested_tenant}')
                            else:
                                logger.debug(f'User {user} is not associated with the requested tenant')
                        else:
                            default_tenant = user.tenants.first()
                            with schema_context(default_tenant.schema_name):
                                request.tenant = default_tenant
                                logger.debug(f'User {user} is associated with default tenant {default_tenant}')
                    else:
                        logger.debug('User not found or no associated tenants')
            except AccessToken.DoesNotExist:
                logger.debug('Token does not exist or is invalid')
        else:
            logger.debug('No Bearer token found')
        logger.debug('TenantSwitchingMiddleware: process_request ended')

from django.utils import timezone
import pytz
from django.db import connection  # Use connection to get schema
from django.conf import settings  # Import settings from django.conf

import pytz
from django.utils import timezone
from django_tenants.utils import get_tenant_model

def _zone(name):
    from zoneinfo import ZoneInfo
    return ZoneInfo(str(name or 'UTC'))


class TenantTimezoneMiddleware:
    """v1.73.16: the request runs in the person's zone – the user's own time zone, else (self service) the branch of their
    employee record, else the company's (Company › time zone, else its country), else Asia/Dubai (zeo/tz.py). Before, the
    company's country only. _zt_today() / zeo.tz.today() then give the person's day, not the server's UTC day."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from zeo import tz as ZT
        tenant = getattr(request, 'tenant', None)
        tok = None
        try:
            if tenant is not None and getattr(tenant, 'schema_name', 'public') != 'public':
                uid = None
                try:
                    from LanguagePack.middleware import jwt_user_id
                    uid = jwt_user_id(request)
                except Exception:   # noqa: BLE001
                    uid = None
                tok = ZT.activate(ZT.user_zone_name(uid) if uid else ZT.company_zone_name(tenant.schema_name))
            else:
                from django.conf import settings
                tok = ZT.activate(getattr(settings, 'TIME_ZONE', 'UTC'))
        except Exception:   # noqa: BLE001
            logging.getLogger(__name__).exception('time zone of the request')
            timezone.activate(_zone('Asia/Dubai'))
        try:
            return self.get_response(request)
        finally:
            ZT.deactivate(tok)


class SchemaMiddleware(MiddlewareMixin):
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Skip schema activation for public apps
        public_schema_apps = ["users", "core", "cloud", "mobile", "group", "licence", "scim"]   # v1.23.0 cloud, v1.25.0 mobile: public schema; v1.54.0 scim
        app_name = request.path.split('/')[1]

        if app_name in public_schema_apps:
            return self.get_response(request)

        # Handle media file requests
        if request.path.startswith(settings.MEDIA_URL):
            schema_name = connection.schema_name
            request.path = request.path.replace(settings.MEDIA_URL, f"{settings.MEDIA_URL}{schema_name}/")
            return self.get_response(request)
        
        # Get schema name from the query parameter
        schema_name = request.GET.get('schema')
        if not schema_name and app_name == 'iclock':
            # v1.12.0: ZKTeco ADMS devices cannot send ?schema= – find the company by the device serial number
            try:
                from AttendancePlus.services import find_schema_for_serial
                schema_name = find_schema_for_serial(request.GET.get('SN') or request.GET.get('sn') or '')
            except Exception:
                schema_name = None
            if not schema_name:
                from django.http import HttpResponse
                return HttpResponse('Device not registered', content_type='text/plain', status=401)
        if not schema_name and app_name == 'language':
            # v1.15.0: language list / login-page pack before a company is chosen – public schema
            return self.get_response(request)
        if not schema_name:
            return JsonResponse({"error": "Schema name is required"}, status=400)

        # Validate and activate the tenant schema
        try:
            TenantModel = get_tenant_model()
            tenant = TenantModel.objects.get(schema_name=schema_name)
            refused = _company_closed(schema_name)   # v1.67.1 (companies) an inactive or deleted company cannot be used
            if refused is not None:
                return refused
            request.tenant = tenant  # Attach the tenant to the request
            with schema_context(schema_name):
                response = self.get_response(request)
            return response
        except TenantModel.DoesNotExist:
            return JsonResponse({"error": "Invalid schema name"}, status=400)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=400)

def _company_closed(schema_name):
    """v1.67.1 (companies): 403 for every request of an inactive or deleted company (sign-in, screens, devices, the API)."""
    try:
        from ZeoCloud.lifecycle import state_of
        st = state_of(schema_name)
    except Exception:
        return None
    if st == 'active':
        return None
    try:
        from Translations.i18n import tr
    except Exception:
        def tr(t, *a):
            return t
    msg = tr('This company is inactive. Its owner or a company administrator can make it active again on Choose a company.') \
        if st == 'inactive' else tr('This company is not available.')
    return JsonResponse({'detail': msg, 'error': msg, 'company_state': 'inactive' if st == 'inactive' else 'closed'}, status=403)
