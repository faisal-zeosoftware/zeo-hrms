from django.contrib import admin
from .models import CompanyTransfer, GroupRole, SettingsCopy

admin.site.register(GroupRole)
admin.site.register(CompanyTransfer)
admin.site.register(SettingsCopy)
