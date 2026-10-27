from django.urls import include, path, re_path
from rest_framework.routers import DefaultRouter

from .views import (ApplicationViewSet, CandidateDocumentViewSet, CandidateViewSet, CareersApplyView, CareersFeedView, CareersJobView,
                    CareersLogoView, CareersPageView, CareersSettingsView, InterviewScoreViewSet, InterviewViewSet, JobOpeningViewSet,
                    ManpowerRequisitionViewSet, OfferViewSet, OnboardingTaskViewSet, RequisitionApprovalLevelViewSet, TalentCampaignViewSet,
                    TalentPoolMemberViewSet, TalentPoolViewSet, VisaStepViewSet)

router = DefaultRouter()
router.register(r'approval-levels', RequisitionApprovalLevelViewSet)
router.register(r'requisitions', ManpowerRequisitionViewSet)
router.register(r'job-openings', JobOpeningViewSet)
router.register(r'candidates', CandidateViewSet)
router.register(r'candidate-documents', CandidateDocumentViewSet)
router.register(r'applications', ApplicationViewSet)
router.register(r'interviews', InterviewViewSet)
router.register(r'interview-scores', InterviewScoreViewSet)
router.register(r'offers', OfferViewSet)
router.register(r'visa-steps', VisaStepViewSet)
router.register(r'onboarding-tasks', OnboardingTaskViewSet)
# v1.57.0 talent pools
router.register(r'talent-pools', TalentPoolViewSet)
router.register(r'talent-pool-members', TalentPoolMemberViewSet)
router.register(r'talent-campaigns', TalentCampaignViewSet)

urlpatterns = [
    # v1.57.0 public careers page (no login; ?schema=<company>) and its settings
    path('api/careers/', CareersPageView.as_view()),
    path('api/careers/apply/', CareersApplyView.as_view()),
    path('api/careers/logo/', CareersLogoView.as_view()),
    re_path(r'^api/careers/feed\.(?P<fmt>xml|json)$', CareersFeedView.as_view()),
    path('api/careers/job/<str:code>/', CareersJobView.as_view()),
    path('api/careers-settings/', CareersSettingsView.as_view()),
    path('api/', include(router.urls)),
]
