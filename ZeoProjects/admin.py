from django.contrib import admin

from .models import AMCContract, Contact, Customer, DevRequest, SLAPolicy, SLATarget, SupportTeam, Ticket


class SLATargetInline(admin.TabularInline):
    model = SLATarget
    extra = 0


@admin.register(SLAPolicy)
class SLAPolicyAdmin(admin.ModelAdmin):
    inlines = [SLATargetInline]
    list_display = ('name', 'is_default', 'is_active')


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'email', 'is_active')
    search_fields = ('code', 'name')


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ('number', 'subject', 'customer', 'priority', 'stage', 'assigned_to', 'created_at')
    list_filter = ('stage', 'priority', 'channel')
    search_fields = ('number', 'subject')


admin.site.register([Contact, SupportTeam, AMCContract, DevRequest])
