from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (AppraisalCycleViewSet, AppraisalOutcomeViewSet, AppraisalTemplateViewSet, CalibrationLogViewSet,
                    CheckInViewSet, GoalSheetViewSet, GoalViewSet, IncrementBandViewSet, KPIViewSet,
                    PerformanceImprovementPlanViewSet, PIPReviewViewSet)

router = DefaultRouter()
router.register(r'kpis', KPIViewSet)
router.register(r'templates', AppraisalTemplateViewSet)
router.register(r'cycles', AppraisalCycleViewSet)
router.register(r'increment-bands', IncrementBandViewSet)
router.register(r'goal-sheets', GoalSheetViewSet)
router.register(r'goals', GoalViewSet)
router.register(r'checkins', CheckInViewSet)
router.register(r'calibration-logs', CalibrationLogViewSet)
router.register(r'outcomes', AppraisalOutcomeViewSet)
router.register(r'pips', PerformanceImprovementPlanViewSet)
router.register(r'pip-reviews', PIPReviewViewSet)

urlpatterns = [
    path('api/', include(router.urls)),
]
