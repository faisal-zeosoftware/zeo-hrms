from django.contrib import admin

from .models import (Action, Contractor, FormTemplate, HSESettings, Incident, IncidentCategory, Permit, PermitType, RiskAssessment, Site)

admin.site.register(HSESettings)


@admin.register(Site)
class SiteAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'kind', 'is_active')


@admin.register(Contractor)
class ContractorAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'status', 'licence_expiry', 'insurance_expiry')


@admin.register(PermitType)
class PermitTypeAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'pack', 'review_status', 'is_active')
    list_filter = ('pack', 'review_status')


@admin.register(FormTemplate)
class FormTemplateAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'kind', 'pack', 'review_status', 'is_active')
    list_filter = ('kind', 'pack', 'review_status')


@admin.register(IncidentCategory)
class IncidentCategoryAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'kind', 'restricted', 'pack', 'review_status')


@admin.register(Permit)
class PermitAdmin(admin.ModelAdmin):
    list_display = ('number', 'permit_type', 'site', 'status', 'valid_from', 'valid_to')
    list_filter = ('status',)


@admin.register(Incident)
class IncidentAdmin(admin.ModelAdmin):
    list_display = ('number', 'title', 'occurred_at', 'status', 'high_potential')


@admin.register(RiskAssessment)
class RiskAdmin(admin.ModelAdmin):
    list_display = ('number', 'title', 'status')


@admin.register(Action)
class ActionAdmin(admin.ModelAdmin):
    list_display = ('number', 'title', 'owner', 'due_date', 'status')


from .models import Handover  # noqa: E402  v1.17.0


@admin.register(Handover)
class HandoverAdmin(admin.ModelAdmin):
    list_display = ('employee', 'reason', 'effective_date', 'status')
    list_filter = ('status', 'reason')
