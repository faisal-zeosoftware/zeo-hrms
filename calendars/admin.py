from django.contrib import admin
from .models import weekend_calendar,assign_weekend,holiday,holiday_calendar,assign_holiday,Attendance, leave_type,leave_entitlement
from import_export.admin import ImportExportModelAdmin
from .resource import AttendanceResource

@admin.register(weekend_calendar)
class CalendarAdmin(admin.ModelAdmin):
    pass
@admin.register(assign_weekend)
class Assign_weekendAdmin(admin.ModelAdmin):
    pass
@admin.register(holiday)
class HolidayAdmin(admin.ModelAdmin):
    pass
@admin.register(holiday_calendar)
class Holiday_calendarAdmin(admin.ModelAdmin):
    pass
@admin.register(assign_holiday)
class Assign_holidayAdmin(admin.ModelAdmin):
    pass

#leave
@admin.register(Attendance)
class LeaveAdmin(ImportExportModelAdmin):
    resource_class = AttendanceResource

@admin.register(leave_type)
class LeavetypeAdmin(admin.ModelAdmin):
    pass
@admin.register(leave_entitlement)
class LeaveentitlementAdmin(admin.ModelAdmin):
    pass