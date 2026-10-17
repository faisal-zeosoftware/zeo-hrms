from django.contrib import admin

from .models import MobileDevice, PushLog


@admin.register(MobileDevice)
class MobileDeviceAdmin(admin.ModelAdmin):
    list_display = ('user', 'platform', 'model', 'app_version', 'biometric', 'active', 'last_seen')
    list_filter = ('platform', 'active', 'biometric')
    search_fields = ('user__username', 'user__email', 'model')
    exclude = ('key_hash',)


@admin.register(PushLog)
class PushLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'user', 'kind', 'title', 'devices', 'sent', 'result')
    list_filter = ('kind', 'result')
