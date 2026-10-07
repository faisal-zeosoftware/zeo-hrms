from django.contrib import admin

from .models import (KPI, AppraisalCycle, AppraisalOutcome, AppraisalTemplate, CalibrationLog, CheckIn, Goal,
                     GoalSheet, IncrementBand, PerformanceImprovementPlan, PIPReview)


class GoalInline(admin.TabularInline):
    model = Goal
    extra = 0


@admin.register(GoalSheet)
class GoalSheetAdmin(admin.ModelAdmin):
    list_display = ('cycle', 'employee', 'status', 'self_score', 'manager_score', 'final_rating')
    list_filter = ('cycle', 'status')
    inlines = [GoalInline]


for m in (KPI, AppraisalTemplate, AppraisalCycle, IncrementBand, CheckIn, CalibrationLog, AppraisalOutcome,
          PerformanceImprovementPlan, PIPReview):
    admin.site.register(m)
