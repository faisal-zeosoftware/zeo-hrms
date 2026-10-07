from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (ApplicationViewSet, CandidateDocumentViewSet, CandidateViewSet, InterviewScoreViewSet,
                    InterviewViewSet, JobOpeningViewSet, ManpowerRequisitionViewSet, OfferViewSet,
                    OnboardingTaskViewSet, RequisitionApprovalLevelViewSet, VisaStepViewSet)

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

urlpatterns = [
    path('api/', include(router.urls)),
]
