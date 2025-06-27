from django.contrib import admin

# from django.contrib import admin
from django.contrib import admin
from import_export.admin import ImportExportModelAdmin
from .models import (emp_master,emp_family,Emp_Documents,EmpJobHistory,EmpQualification,)
from .resource import EmployeeResource,DocumentResource,LanguageSkillResource,MarketingSkillResource, ProLangSkillResource

@admin.register(emp_master)
class EmployeeAdmin(ImportExportModelAdmin):
    resource_class = EmployeeResource
@admin.register(Emp_Documents)
class EmployeeAdmin(ImportExportModelAdmin):
    resource_class = DocumentResource

# @admin.register(emp_family)
# class FamilyAdmin(admin.ModelAdmin):
#     pass
# @admin.register(EmpJobHistory)
# class EmphistoryAdmin(admin.ModelAdmin):
#     pass
