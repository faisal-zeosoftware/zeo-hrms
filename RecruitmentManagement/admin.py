from django.contrib import admin

from .models import (Application, ApplicationStageHistory, Candidate, CandidateDocument, Interview, InterviewScore,
                     JobOpening, ManpowerRequisition, OnboardingTask, Offer, RequisitionApproval,
                     RequisitionApprovalLevel, VisaStep)


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ('candidate', 'job', 'stage', 'rating', 'applied_on')
    list_filter = ('stage', 'job')


for m in (RequisitionApprovalLevel, ManpowerRequisition, RequisitionApproval, JobOpening, Candidate, CandidateDocument,
          ApplicationStageHistory, Interview, InterviewScore, Offer, VisaStep, OnboardingTask):
    admin.site.register(m)
