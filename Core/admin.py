from django.contrib import admin
from .models import cntry_mstr
from .resource import CountryResource
from import_export.admin import ImportExportModelAdmin

@admin.register(cntry_mstr)
class CountryAdmin(ImportExportModelAdmin):
    resource_class = CountryResource

