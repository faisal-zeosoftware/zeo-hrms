
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

class TenantTimezoneMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tenant = getattr(request, 'tenant', None)  # Get the current tenant

        if tenant and hasattr(tenant, 'country') and tenant.country:
            # Activate timezone based on the tenant's country
            tenant_timezone = tenant.country.timezone
            try:
                timezone.activate(pytz.timezone(tenant_timezone))
            except pytz.UnknownTimeZoneError:
                # Fallback to UTC if the tenant's timezone is invalid
                timezone.activate(pytz.utc)
        else:
            # Use the server's local timezone for public tenants
            from django.conf import settings
            timezone.activate(pytz.timezone(getattr(settings, 'TIME_ZONE', 'UTC')))

        response = self.get_response(request)

        # Deactivate timezone after the request is processed
        timezone.deactivate()
        return response


class SchemaMiddleware(MiddlewareMixin):
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Skip schema activation for public apps
        public_schema_apps = ["users", "core"]
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
        if not schema_name:
            return JsonResponse({"error": "Schema name is required"}, status=400)

        # Validate and activate the tenant schema
        try:
            TenantModel = get_tenant_model()
            tenant = TenantModel.objects.get(schema_name=schema_name)
            request.tenant = tenant  # Attach the tenant to the request
            with schema_context(schema_name):
                response = self.get_response(request)
            return response
        except TenantModel.DoesNotExist:
            return JsonResponse({"error": "Invalid schema name"}, status=400)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=400)