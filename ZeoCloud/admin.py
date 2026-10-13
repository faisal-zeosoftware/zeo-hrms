from django.contrib import admin

from .models import Account, AccountCompany, AppLicence, Invoice, Plan, PlatformAdmin

for m in (Plan, Account, AccountCompany, AppLicence, Invoice, PlatformAdmin):
    admin.site.register(m)
