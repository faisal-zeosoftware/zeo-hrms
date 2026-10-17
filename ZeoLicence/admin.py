from django.contrib import admin
from .models import EnterpriseRequest, Installation, IssuedLicence, LicenceFile

admin.site.register(Installation)
admin.site.register(LicenceFile)
admin.site.register(EnterpriseRequest)
admin.site.register(IssuedLicence)
