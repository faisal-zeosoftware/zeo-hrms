"""ZKTeco ADMS push protocol – include at the site root: path('iclock/', include('AttendancePlus.adms_urls')).
The device is configured with the server address only (no ?schema=); the company is found from the
serial number (UserManagement.middleware.SchemaMiddleware → AttendancePlus.services.find_schema_for_serial)."""
from django.urls import path, re_path

from . import views as V

urlpatterns = [
    re_path(r'^cdata(?:\.aspx)?/?$', V.ADMSCData.as_view()),
    re_path(r'^getrequest(?:\.aspx)?/?$', V.ADMSGetRequest.as_view()),
    re_path(r'^devicecmd(?:\.aspx)?/?$', V.ADMSDeviceCmd.as_view()),
]
