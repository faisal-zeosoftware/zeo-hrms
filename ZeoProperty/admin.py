from django.contrib import admin

from .models import Bill, Building, Lease, Owner, Party, PropertySettings, Unit

for m in (PropertySettings, Building, Unit, Owner, Party, Lease, Bill):
    admin.site.register(m)
