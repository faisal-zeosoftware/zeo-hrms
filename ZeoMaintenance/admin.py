from django.contrib import admin

from .models import Category, Inspection, MaintSettings, Vendor, WorkOrder

for m in (MaintSettings, Category, Vendor, WorkOrder, Inspection):
    admin.site.register(m)
