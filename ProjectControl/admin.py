from django.contrib import admin

from .models import CostRateSetting, MemberRate, ProjectFinance, TaskPlan, TimesheetExtra

for m in (ProjectFinance, MemberRate, TaskPlan, TimesheetExtra, CostRateSetting):
    admin.site.register(m)
